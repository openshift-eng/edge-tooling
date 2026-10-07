#!/usr/bin/env python3
"""Pure attribution for Jira issues collected via MCP tools.

Takes raw Jira issues (collected by Claude via mcp__mcp-atlassian__jira_search)
and applies the standard attribution chain:

1. Issue's own components → workstream
2. Parent epic's component (via pre-resolved map)
3. Jira project key (e.g., USHIFT → MicroShift)

This script contains NO network calls — all data is pre-fetched. The skill
orchestrates MCP queries and passes results here for processing.

Input format (raw_jira_issues.json):
{
  "issues": [
    {
      "member": "jcope@redhat.com",
      "kind": "assignee" | "qa" | "ocpstrat_role",
      "fields": {
        "key": "OCPEDGE-1234",
        "components": [...],
        "updated": "2026-08-15T10:30:00Z",
        "summary": "...",
        "parent": {"key": "OCPEDGE-999"}
      }
    }
  ],
  "parent_workstreams": {
    "OCPEDGE-999": "SNO" | null
  },
  "base_url": "https://redhat.atlassian.net"
}

Output format (jira_activity.json):
{
  "activities": [
    {
      "member": "jcope@redhat.com",
      "workstream": "SNO" | null,
      "kind": "assignee",
      "issue_key": "OCPEDGE-1234",
      "url": "https://redhat.atlassian.net/browse/OCPEDGE-1234",
      "ts": "2026-08-15T10:30:00Z",
      "attribution_source": "component" | "parent" | "project" | null,
      "unattributed_reason": "no_component_no_parent" | "parent_also_empty" | null
    }
  ],
  "collector_meta": {}
}
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from _common import (
    UNATTRIBUTED_NO_PARENT,
    UNATTRIBUTED_PARENT_EMPTY,
    activity_payload,
)
from workstream_map import component_to_workstream, project_to_workstream


@dataclass(frozen=True)
class Activity:
    """One attributed contribution by one member.

    ``workstream`` is ``None`` for items that could not be mapped (unattributed).
    ``attribution_source`` is ``None`` exactly when ``workstream`` is ``None``.
    Valid sources: "component", "parent", "project".

    ``unattributed_reason`` is set exactly when ``workstream`` is ``None``, and is
    one of ``UNATTRIBUTED_NO_PARENT`` / ``UNATTRIBUTED_PARENT_EMPTY``.
    """

    member: str
    workstream: Optional[str]
    kind: str
    issue_key: str
    url: str
    ts: Optional[str]
    attribution_source: Optional[str] = None
    unattributed_reason: Optional[str] = None


# --- Attribution (pure) -----------------------------------------------------


def _issue_components(issue: dict) -> List[str]:
    """Extract component names from Jira issue fields."""
    components = issue.get("fields", {}).get("components") or []
    return [component["name"] for component in components if component.get("name")]


def attribute_issue(
    issue: dict, parent_workstreams: Dict[str, Optional[str]]
) -> tuple[List[str], Optional[str]]:
    """Return (workstream acronyms, attribution_source) for one issue.

    Attribution chain (first hit wins):
    1. Issue's own components (can yield multiple workstreams) -> "component"
    2. Parent epic's component (via resolved map) -> "parent"
    3. Jira project key (e.g., USHIFT -> MicroShift) -> "project"

    Returns ``([], None)`` when nothing matches.
    """
    # Step 1: issue's own components
    mapped: List[str] = []
    for component_name in _issue_components(issue):
        workstream = component_to_workstream(component_name)
        if workstream and workstream not in mapped:
            mapped.append(workstream)
    if mapped:
        return (mapped, "component")

    # Step 2: parent epic's component
    parent = issue.get("fields", {}).get("parent")
    if parent:
        parent_key = parent.get("key")
        if parent_key:
            parent_workstream = parent_workstreams.get(parent_key)
            if parent_workstream:
                return ([parent_workstream], "parent")

    # Step 3: Jira project key
    issue_key = issue.get("fields", {}).get("key") or issue.get("key")
    if issue_key:
        project_workstream = project_to_workstream(issue_key)
        if project_workstream:
            return ([project_workstream], "project")

    # Dead end
    return ([], None)


def unattributed_reason(issue: dict) -> str:
    """Return why ``issue`` could not be attributed, for the data-quality report.

    Only meaningful for issues that ``attribute_issue`` returned no workstream for.
    An issue that names a parent epic but still ended up unattributed means the
    epic was itself component-less — a fixable gap one level up. Note that a parent
    Jira refused to return (deleted, or a failed lookup batch) is indistinguishable
    from a component-less one here and is counted the same way.
    """
    parent = issue.get("fields", {}).get("parent") or {}
    if parent.get("key"):
        return UNATTRIBUTED_PARENT_EMPTY
    return UNATTRIBUTED_NO_PARENT


def issue_to_activities(
    issue: dict,
    member: str,
    kind: str,
    base_url: str,
    parent_workstreams: Dict[str, Optional[str]],
) -> List[Activity]:
    """Turn one issue into per-workstream activities (unattributed if unmappable).

    An issue with multiple mappable components fans out to one Activity per
    workstream. Unattributed issues produce one Activity with workstream=None.
    """
    fields = issue.get("fields", {})
    issue_key = fields.get("key") or issue.get("key", "")
    url = f"{base_url}/browse/{issue_key}"
    timestamp = fields.get("updated")

    workstreams, attribution_source = attribute_issue(issue, parent_workstreams)
    if not workstreams:
        return [
            Activity(
                member, None, kind, issue_key, url, timestamp, None, unattributed_reason(issue)
            )
        ]
    return [
        Activity(member, workstream, kind, issue_key, url, timestamp, attribution_source)
        for workstream in workstreams
    ]


# --- Main -------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply workstream attribution to raw Jira issues collected via MCP."
    )
    parser.add_argument("--raw-issues", required=True, help="raw_jira_issues.json from skill")
    parser.add_argument("--output", default="jira_activity.json", help="output activities JSON")
    args = parser.parse_args(argv)

    # Load raw issues
    with open(args.raw_issues, encoding="utf-8") as handle:
        raw_data = json.load(handle)

    issues = raw_data.get("issues", [])
    parent_workstreams = raw_data.get("parent_workstreams", {})
    base_url = raw_data.get("base_url", "https://redhat.atlassian.net")

    # Apply attribution to each issue
    activities: List[Activity] = []
    for issue_record in issues:
        member = issue_record["member"]
        kind = issue_record["kind"]
        issue = issue_record  # Issue data is in the record itself under "fields"

        activities.extend(issue_to_activities(issue, member, kind, base_url, parent_workstreams))

    # Write activities with standard envelope format
    payload = activity_payload([asdict(activity) for activity in activities])
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
