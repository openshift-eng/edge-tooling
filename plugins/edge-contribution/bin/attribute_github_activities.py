#!/usr/bin/env python3
"""Pure workstream attribution for GitHub PRs, using Jira data collected via MCP.

``collect_github.py`` gathers PR data and the Jira keys referenced in PR titles/
bodies, but performs no Jira lookups itself. The skill resolves those keys (and
their parent epics' components) via ``mcp__mcp-atlassian__jira_search``, then
this script applies the same attribution chain as ``attribute_jira_activities.py``
(component -> parent epic -> project fallback) with no network calls.

Input format (github_prs_raw.json, from collect_github.py):
{
  "pr_pairs": [{"member": "...", "kind": "pr_authored" | "pr_reviewed", "pr": {...}}],
  "jira_keys": ["OCPEDGE-1234", ...],
  "excluded_personal_repo_prs": 3,
  "excluded_personal_repos": {"copejon/edge-tooling": 3}
}

Input format (raw_jira_issues.json, from the skill's MCP queries):
{
  "issues": [{"fields": {"key": "OCPEDGE-1234", "components": [...], "parent": {"key": "OCPEDGE-999"}}}],
  "parent_workstreams": {"OCPEDGE-999": "SNO" | null}
}

Output format (github_activity.json): same envelope as the legacy collector.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Dict, List, Optional

from _common import activity_payload
from collect_github import (
    GithubActivity,
    make_prefetched_resolver,
    pr_to_activities,
    resolve_workstreams,
)


def _issues_by_key(raw_jira: dict) -> Dict[str, dict]:
    issues_by_key: Dict[str, dict] = {}
    for issue in raw_jira.get("issues", []):
        key = issue.get("fields", {}).get("key") or issue.get("key")
        if key:
            issues_by_key[key] = issue
    return issues_by_key


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply workstream attribution to raw GitHub PRs using MCP-fetched Jira data."
    )
    parser.add_argument("--raw-prs", required=True, help="github_prs_raw.json from collect_github.py")
    parser.add_argument("--raw-jira-issues", required=True, help="raw_jira_issues.json from the skill")
    parser.add_argument("--output", default="github_activity.json", help="output activities JSON")
    args = parser.parse_args(argv)

    with open(args.raw_prs, encoding="utf-8") as handle:
        raw_prs = json.load(handle)
    with open(args.raw_jira_issues, encoding="utf-8") as handle:
        raw_jira = json.load(handle)

    workstream_map = resolve_workstreams(
        raw_prs.get("jira_keys", []),
        _issues_by_key(raw_jira),
        raw_jira.get("parent_workstreams", {}),
    )
    resolve_workstream = make_prefetched_resolver(workstream_map)

    activities: List[GithubActivity] = []
    for pair in raw_prs.get("pr_pairs", []):
        activities.extend(
            pr_to_activities(pair["pr"], pair["member"], pair["kind"], resolve_workstream)
        )

    payload = activity_payload(
        [asdict(activity) for activity in activities],
        excluded_personal_repo_prs=raw_prs.get("excluded_personal_repo_prs", 0),
        excluded_personal_repos=raw_prs.get("excluded_personal_repos", {}),
    )
    with open(args.output, "w", encoding="utf-8") as output:
        json.dump(payload, output, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
