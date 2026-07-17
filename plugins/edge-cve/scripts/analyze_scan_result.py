#!/usr/bin/env python3
"""Deterministically analyze a single govulncheck result.json and decide what
action, if any, is needed - no LLM call required for this base determination.

Given the result.json written by process_govulncheck_result.go (local mode;
see run_single_repo_scan.sh / scan_target.sh), this:

1. Computes a verdict ("affected", "not_affected", or "inconclusive") using
   the same signal-kill-aware logic as generate_report.py's
   verdict_for_ticket, so a scan that was OOM-killed (scan_incomplete) is
   never mistaken for a clean "not affected" result.
2. Builds a ready-to-use `suggested_agent_prompt` string from a fixed
   template filled in with the scan's own matched findings - a deterministic
   remediation prompt, not an LLM-generated one. Callers who want the LLM to
   refine/verify this prompt (e.g. edge-cve:investigate Step 3) can still do
   so as a separate step.

Usage:
    analyze_scan_result.py --result RESULT_JSON [--out OUT_JSON]
        [--jira-url URL] [--summary TEXT] [--component NAME]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def determine_verdict(result: dict) -> tuple[str, bool]:
    """Return (verdict, action_required)."""
    if result.get("scan_incomplete"):
        return "inconclusive", False
    if result.get("affected"):
        return "affected", True
    # govulncheck: 0 = clean, 3 = vulnerabilities found. Any other exit is
    # abnormal (tool/build error, etc.) - treat as inconclusive even when
    # finding_count is 0, so a failed scan is never mistaken for "not affected".
    if result.get("scan_exit_code", 0) not in (0, 3):
        return "inconclusive", False
    return "not_affected", False


def finding_label(finding: dict) -> str:
    inner = finding.get("finding", finding)
    if not isinstance(inner, dict):
        inner = finding if isinstance(finding, dict) else {}
    # govulncheck may emit finding.osv as a string ID or an embedded OSV object.
    osv = inner.get("osv")
    if not osv:
        osv = inner.get("vulnerability")
    vuln_id = "?"
    if isinstance(osv, str):
        vuln_id = osv or "?"
    elif isinstance(osv, dict):
        raw_id = osv.get("id", "?")
        vuln_id = raw_id if isinstance(raw_id, str) and raw_id else "?"
    module = ""
    trace = inner.get("trace") or []
    if trace and isinstance(trace, list) and isinstance(trace[0], dict):
        module = trace[0].get("module", "") or trace[0].get("package", "") or ""
    return f"{vuln_id}" + (f" in {module}" if module else "")


def build_prompt(
    result: dict,
    verdict: str,
    *,
    jira_url: str = "",
    summary: str = "",
    component: str = "",
) -> str | None:
    if verdict == "not_affected":
        return None

    repo_url = result.get("repo_url", "")
    repo_slug = result.get("repo_slug", "")
    git_ref = result.get("git_ref", "")
    commit = result.get("commit", "") or ""
    cve_ids = result.get("cve_ids") or []
    findings = result.get("matched_findings") or []

    lines = [
        "You are fixing a CVE in an OpenShift edge component repository.",
        "",
    ]
    if jira_url:
        lines.append(f"Jira: {jira_url}")
    if summary:
        lines.append(f"Summary: {summary}")
    if cve_ids:
        lines.append(f"CVEs: {', '.join(cve_ids)}")
    if component:
        lines.append(f"Component: {component}")
    lines.append(f"Repository: {repo_slug} ({repo_url})")
    lines.append(f"Target ref: {git_ref} (commit {commit[:12] if commit else 'unknown'})")
    lines.append("")

    if verdict == "inconclusive":
        lines.extend(
            [
                "govulncheck did not produce a conclusive result for this ref",
                "(scan_incomplete or a non-zero exit with ambiguous findings), so this",
                "is NOT yet confirmed as affected. Before writing any fix:",
                "1. Re-run with more memory/CPU (see run_single_repo_scan.sh/"
                "run_govulncheck_podman.sh --memory) or check the scan's stderr_tail"
                " for the real cause.",
                "2. Only proceed with a fix once govulncheck confirms an affected finding.",
            ]
        )
        return "\n".join(lines)

    # verdict == "affected"
    lines.append("govulncheck confirmed this repository/ref is affected:")
    for finding in findings:
        lines.append(f"- {finding_label(finding)}")
    lines.extend(
        [
            "",
            "Steps:",
            f"1. Clone the repository and checkout {git_ref}.",
            "2. Confirm the vulnerable module/path above against govulncheck's "
            "call-graph findings (matched_findings in the scan result).",
            "3. Bump the dependency (or apply the upstream fix) to a version "
            "that resolves the vulnerability.",
            "4. Run `go mod tidy && go test ./...` and `govulncheck ./...` to "
            "verify the fix and check for regressions.",
            "5. Open a PR"
            + (f" referencing {jira_url}" if jira_url else " describing the fix")
            + ".",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, help="Path to a result.json from a govulncheck scan")
    parser.add_argument("--out", help="Write the augmented JSON here (always also printed to stdout)")
    parser.add_argument("--jira-url", default="", help="Jira ticket URL for context in the prompt")
    parser.add_argument("--summary", default="", help="Ticket/issue summary for context in the prompt")
    parser.add_argument("--component", default="", help="Component name for context in the prompt")
    args = parser.parse_args()

    result_path = Path(args.result)
    if not result_path.is_file():
        print(f"Error: {result_path} not found", file=sys.stderr)
        sys.exit(1)

    result = json.loads(result_path.read_text(encoding="utf-8"))
    verdict, action_required = determine_verdict(result)
    prompt = build_prompt(
        result,
        verdict,
        jira_url=args.jira_url,
        summary=args.summary,
        component=args.component,
    )

    output = dict(result)
    output["verdict"] = verdict
    output["action_required"] = action_required
    output["suggested_agent_prompt"] = prompt
    # Persist the context args as their own fields (not just baked into the
    # prompt text) so downstream consumers (generate_html_report.py) can
    # render a proper Jira link/summary without re-parsing prose.
    if args.jira_url:
        output["jira_url"] = args.jira_url
    if args.summary:
        output["summary"] = args.summary
    if args.component:
        output["component"] = args.component

    text = json.dumps(output, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"Written: {args.out}", file=sys.stderr)
    print(text)


if __name__ == "__main__":
    main()
