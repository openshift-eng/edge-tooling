#!/usr/bin/env python3
"""Fetch open Black CVE tickets from Jira.

Uses the intersection of the "All Open CVEs" and "All Open Black CVEs" saved
filters — the same query as:
  https://redhat.atlassian.net/issues/?filter=92079&jql=filter%20%3D%20%22All%20Open%20CVEs%22%20and%20filter%20%3D%20%22All%20Open%20Black%20CVEs%22

Usage:
    fetch_cves.py --workdir DIR [--jql QUERY] [--output FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib.jira_client import (  # noqa: E402
    DEFAULT_JQL,
    JiraConfigError,
    load_config,
    normalize_issue,
    search_jql,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Black CVE tickets from Jira")
    parser.add_argument("--workdir", required=True, help="Working directory for outputs")
    parser.add_argument("--jql", default=DEFAULT_JQL, help="Jira JQL query")
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path (default: <workdir>/jira/cves-raw.json)",
    )
    args = parser.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    jira_dir = workdir / "jira"
    jira_dir.mkdir(parents=True, exist_ok=True)

    output_path = Path(args.output) if args.output else jira_dir / "cves-raw.json"

    try:
        raw_issues = search_jql(args.jql)
    except JiraConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config()
    issues = [normalize_issue(item, base_url=cfg["base_url"]) for item in raw_issues]
    result = {
        "jql": args.jql,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "count": len(issues),
        "issues": issues,
    }

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print(f"Fetched {len(issues)} issues", file=sys.stderr)
    print(f"Written: {output_path}", file=sys.stderr)
    print(json.dumps({"count": len(issues), "output": str(output_path)}, indent=2))


if __name__ == "__main__":
    main()
