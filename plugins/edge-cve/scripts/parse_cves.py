#!/usr/bin/env python3
"""Parse raw Jira CVE tickets into scan-ready records.

Categorizes tickets by component and version, extracts CVE IDs, and resolves
repository targets from ticket text or component mapping.

Usage:
    parse_cves.py --workdir DIR [--input FILE] [--config FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib.cve_extract import (  # noqa: E402
    extract_cve_ids,
    extract_go_modules,
    extract_repo_urls,
    is_private_ticket,
    load_component_config,
    primary_component,
    resolve_component_repo,
    resolve_git_refs,
    ticket_versions,
)


def parse_issue(issue: dict, config: dict) -> dict:
    cve_ids = extract_cve_ids(issue.get("summary", ""), issue.get("description", ""))
    component = primary_component(issue.get("components", []))
    versions = ticket_versions(issue)

    patterns = config.get("repo_url_patterns", [])
    text = f"{issue.get('summary', '')}\n{issue.get('description', '')}"
    repos = extract_repo_urls(text, patterns)

    component_repo = resolve_component_repo(component, config)
    if component_repo and not repos:
        repos = [component_repo]
    elif component_repo:
        slugs = {r["slug"] for r in repos}
        if component_repo["slug"] not in slugs:
            repos.append(component_repo)

    scan_targets = []
    for repo in repos:
        refs = resolve_git_refs(versions, repo)
        if not refs:
            # Ticket has a repo but no resolvable release version - do NOT
            # invent a tip-of-tree branch (main/master); that would scan far
            # more than the ticket is asking about. Surface via parse_warnings
            # instead so the ticket still shows up as needing a version.
            continue
        scan_targets.append(
            {
                "repo": repo,
                "git_refs": refs,
            }
        )

    return {
        "key": issue["key"],
        "url": issue.get("url", ""),
        "summary": issue.get("summary", ""),
        "status": issue.get("status", ""),
        "priority": issue.get("priority", ""),
        "issue_type": issue.get("issue_type", ""),
        "component": component,
        "components": issue.get("components", []),
        "versions": versions,
        "cve_ids": cve_ids,
        "go_modules": extract_go_modules(text),
        "repos": repos,
        "scan_targets": scan_targets,
        "labels": issue.get("labels", []),
        "security_level": issue.get("security_level", ""),
        "is_private": is_private_ticket(issue),
        "assignee": issue.get("assignee", ""),
        "updated": issue.get("updated", ""),
        "parse_warnings": _warnings(issue, cve_ids, repos, versions, scan_targets),
    }


def _warnings(issue, cve_ids, repos, versions, scan_targets) -> list[str]:
    warnings = []
    if not cve_ids:
        warnings.append("no_cve_id_found")
    if not repos:
        warnings.append("no_repo_resolved")
    if not versions:
        warnings.append("no_version_resolved")
    if repos and not scan_targets:
        # Repo known but no release-branch ref could be derived - we refuse
        # to invent main/master, so this ticket can't be scanned as-is.
        warnings.append("no_git_ref_resolved")
    if not issue.get("components"):
        warnings.append("no_component")
    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse Jira CVE tickets")
    parser.add_argument("--workdir", required=True)
    parser.add_argument(
        "--input",
        default="",
        help="Input JSON (default: <workdir>/jira/cves-raw.json)",
    )
    parser.add_argument(
        "--config",
        default="",
        help="Component mapping JSON (default: plugin config/component-repos.json)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON (default: <workdir>/jira/cves-parsed.json)",
    )
    args = parser.parse_args()

    workdir = Path(args.workdir)
    plugin_dir = SCRIPT_DIR.parent
    input_path = Path(args.input) if args.input else workdir / "jira" / "cves-raw.json"
    config_path = (
        Path(args.config)
        if args.config
        else plugin_dir / "config" / "component-repos.json"
    )
    output_path = Path(args.output) if args.output else workdir / "jira" / "cves-parsed.json"

    if not input_path.is_file():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    if not config_path.is_file():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding="utf-8") as fh:
        raw = json.load(fh)

    config = load_component_config(config_path)
    parsed = [parse_issue(issue, config) for issue in raw.get("issues", [])]

    by_component: dict[str, int] = {}
    for item in parsed:
        comp = item["component"]
        by_component[comp] = by_component.get(comp, 0) + 1

    result = {
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "source": str(input_path),
        "count": len(parsed),
        "by_component": dict(sorted(by_component.items())),
        "tickets": parsed,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    warnings = sum(1 for t in parsed if t["parse_warnings"])
    print(f"Parsed {len(parsed)} tickets ({warnings} with warnings)", file=sys.stderr)
    print(f"Written: {output_path}", file=sys.stderr)
    print(
        json.dumps(
            {
                "count": len(parsed),
                "by_component": result["by_component"],
                "output": str(output_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
