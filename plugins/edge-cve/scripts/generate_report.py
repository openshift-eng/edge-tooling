#!/usr/bin/env python3
"""Generate a deterministic CVE investigation report from grouped tickets and scans.

Produces markdown suitable for team notification. Actionable remediation prompts
are written separately for LLM follow-up.

Usage:
    generate_report.py --workdir DIR
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def build_ticket_index(parsed: dict) -> dict[str, dict]:
    return {ticket["key"]: ticket for ticket in parsed.get("tickets", [])}


def scan_by_ticket(scan_results: dict) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for result in scan_results.get("results", []):
        for ticket_key in result.get("ticket_keys", []):
            index.setdefault(ticket_key, []).append(result)
    # Also attach via target metadata from grouped tickets if ticket_keys missing.
    return index


def verdict_for_ticket(ticket: dict, scans: list[dict]) -> str:
    if not ticket.get("cve_ids"):
        return "needs_review"
    if not scans:
        return "not_scanned"
    if any(scan.get("affected") for scan in scans):
        return "affected"
    # A scan that was OOM-killed/signal-terminated (scan_incomplete) never
    # finished, so "no matches" there does NOT mean "not affected" - treat
    # it as inconclusive rather than falsely clearing the ticket.
    if any(scan.get("scan_incomplete") for scan in scans):
        return "inconclusive"
    if all(not scan.get("affected") for scan in scans):
        return "not_affected"
    return "inconclusive"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate CVE investigation report")
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    workdir = Path(args.workdir)
    grouped_path = workdir / "jira" / "cves-grouped.json"
    parsed_path = workdir / "jira" / "cves-parsed.json"
    scan_path = workdir / "scans" / "govulncheck-results.json"

    for path in (grouped_path, parsed_path):
        if not path.is_file():
            print(f"Error: required input missing: {path}", file=sys.stderr)
            sys.exit(1)

    grouped = load_json(grouped_path)
    parsed = load_json(parsed_path)
    scans = load_json(scan_path) if scan_path.is_file() else {"results": []}

    ticket_index = build_ticket_index(parsed)
    scan_index: dict[str, list[dict]] = {}
    for result in scans.get("results", []):
        for key in result.get("ticket_keys", []):
            scan_index.setdefault(key, []).append(result)

    lines = [
        "# Edge CVE Investigation Report",
        "",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Jira groups:** {grouped.get('group_count', 0)}",
        f"**Tickets:** {parsed.get('count', 0)}",
        "",
        "## Summary by component",
        "",
        "| Component | Tickets | Affected | Not Affected | Needs Review |",
        "|-----------|---------|----------|--------------|--------------|",
    ]

    component_stats: dict[str, dict[str, int]] = {}
    ticket_rows = []

    for ticket in parsed.get("tickets", []):
        key = ticket["key"]
        comp = ticket.get("component", "Unknown")
        ticket_scans = scan_index.get(key, [])
        verdict = verdict_for_ticket(ticket, ticket_scans)
        stats = component_stats.setdefault(
            comp,
            {
                "tickets": 0,
                "affected": 0,
                "not_affected": 0,
                "needs_review": 0,
                "not_scanned": 0,
            },
        )
        stats["tickets"] += 1
        stats[verdict] = stats.get(verdict, 0) + 1
        ticket_rows.append((comp, ticket, verdict, ticket_scans))

    for comp, stats in sorted(component_stats.items()):
        lines.append(
            f"| {comp} | {stats['tickets']} | {stats.get('affected', 0)} | "
            f"{stats.get('not_affected', 0)} | "
            f"{stats.get('needs_review', 0) + stats.get('not_scanned', 0) + stats.get('inconclusive', 0)} |"
        )

    lines.extend(["", "## Ticket details", ""])
    for comp, ticket, verdict, ticket_scans in sorted(
        ticket_rows, key=lambda row: (row[0], row[1]["key"])
    ):
        cves = ", ".join(ticket.get("cve_ids", [])) or "(none)"
        versions = ", ".join(ticket.get("versions", [])) or "(unknown)"
        repos = ", ".join(r["slug"] for r in ticket.get("repos", [])) or "(none)"
        lines.append(f"### {ticket['key']} — {verdict}")
        lines.append("")
        lines.append(f"- **Summary:** {ticket.get('summary', '')}")
        lines.append(f"- **Component:** {comp}")
        lines.append(f"- **Versions:** {versions}")
        lines.append(f"- **CVEs:** {cves}")
        lines.append(f"- **Repos:** {repos}")
        lines.append(f"- **Jira:** {ticket.get('url', '')}")
        if ticket.get("parse_warnings"):
            lines.append(f"- **Warnings:** {', '.join(ticket['parse_warnings'])}")
        if ticket_scans:
            lines.append("- **govulncheck:**")
            for scan in ticket_scans:
                if scan.get("scan_incomplete"):
                    status = f"INCOMPLETE (killed, exit {scan.get('scan_exit_code')} - likely OOM, re-run with more memory)"
                elif scan.get("affected"):
                    status = "AFFECTED"
                else:
                    status = "NOT AFFECTED"
                lines.append(
                    f"  - {scan.get('repo_url', '')} @ {scan.get('git_ref', '')} "
                    f"({scan.get('commit', '')[:12]}) → {status}"
                )
        lines.append("")

    report_path = workdir / "report-cve-investigation.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    actionable = [
        row
        for row in ticket_rows
        if row[2] == "affected"
        or (row[2] == "needs_review" and row[1].get("repos"))
    ]
    prompt_lines = [
        "# CVE Remediation Agent Prompts",
        "",
        "Use these prompts only for tickets marked affected or needing review.",
        "",
    ]
    for _, ticket, verdict, ticket_scans in actionable:
        prompt_lines.extend(
            [
                f"## {ticket['key']} ({verdict})",
                "",
                "You are fixing a CVE in an OpenShift edge component repository.",
                f"Jira: {ticket.get('url', '')}",
                f"Summary: {ticket.get('summary', '')}",
                f"CVEs: {', '.join(ticket.get('cve_ids', []))}",
                f"Component: {ticket.get('component', '')}",
                f"Target versions: {', '.join(ticket.get('versions', []))}",
                f"Repositories: {', '.join(r['slug'] for r in ticket.get('repos', []))}",
                "",
                "Steps:",
                "1. Clone the repository and checkout the target release branch.",
                "2. Confirm the vulnerable module/path from govulncheck findings.",
                "3. Bump the dependency or apply the upstream fix.",
                "4. Run `go test ./...` and `govulncheck ./...` to verify.",
                "5. Open a PR referencing the Jira ticket.",
                "",
            ]
        )
        if ticket_scans:
            prompt_lines.append("govulncheck evidence:")
            for scan in ticket_scans:
                if scan.get("matched_findings"):
                    prompt_lines.append(
                        f"- {scan.get('repo_url')}@{scan.get('git_ref')}: "
                        f"{len(scan['matched_findings'])} matched finding(s)"
                    )
            prompt_lines.append("")

    prompts_path = workdir / "remediation-prompts.md"
    prompts_path.write_text("\n".join(prompt_lines) + "\n", encoding="utf-8")

    summary = {
        "report": str(report_path),
        "prompts": str(prompts_path),
        "affected_tickets": sum(1 for _, _, v, _ in ticket_rows if v == "affected"),
        "actionable_prompts": len(actionable),
    }
    with open(workdir / "report-summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)

    print(f"Written: {report_path}", file=sys.stderr)
    print(f"Written: {prompts_path}", file=sys.stderr)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
