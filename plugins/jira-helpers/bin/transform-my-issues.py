#!/usr/bin/env python3
"""Transform raw MCP jira_search response JSON into structured issue data."""

import argparse
import json
import sys
from datetime import datetime, timezone

DONE_STATUSES = {"Done", "Closed", "Verified"}
IN_PROGRESS_STATUSES = {"In Progress", "Review"}
FIBONACCI = {0, 1, 2, 3, 5, 8, 13}

FIELD_STORY_POINTS = "customfield_10028"
FIELD_EPIC_LINK = "customfield_10014"
FIELD_FLAGGED = "customfield_10021"


def unwrap_mcp_response(raw):
    """Unwrap MCP response into a list of issue dicts.

    Handles three formats:
    1. {"result": "{\"issues\": [...]}"} — stringified JSON inside result
    2. {"issues": [...]} — direct object
    3. [{...}, {...}] — bare list of issues
    """
    if isinstance(raw, list):
        return raw

    if isinstance(raw, dict):
        if "result" in raw and isinstance(raw["result"], str):
            inner = json.loads(raw["result"])
            return unwrap_mcp_response(inner)
        if "issues" in raw:
            return raw["issues"]

    return []


def extract_sprint_name(sprint_field):
    """Extract the most recent sprint name from the sprint field."""
    if not sprint_field:
        return None

    if isinstance(sprint_field, list):
        if not sprint_field:
            return None
        last = sprint_field[-1]
        if isinstance(last, dict):
            return last.get("name")
        if isinstance(last, str):
            return last
        return None

    if isinstance(sprint_field, dict):
        return sprint_field.get("name")

    if isinstance(sprint_field, str):
        return sprint_field

    return None


def extract_blocked_by(issue):
    """Extract keys of blocking issues that are not in a done status."""
    links = issue.get("fields", {}).get("issuelinks", []) or []
    blocked_by = []
    for link in links:
        link_type = link.get("type", {})
        inward = link_type.get("inward", "")
        if "is blocked by" in inward and "inwardIssue" in link:
            inward_issue = link["inwardIssue"]
            inward_status = inward_issue.get("fields", {}).get("status", {}).get("name", "")
            if inward_status not in DONE_STATUSES:
                blocked_by.append(inward_issue.get("key"))
    return blocked_by


def transform_issue(issue, today):
    """Transform a single Jira issue into the output format."""
    fields = issue.get("fields", {})

    key = issue.get("key", "")
    summary = fields.get("summary", "")
    status = fields.get("status", {}).get("name", "")
    issue_type = fields.get("issuetype", {}).get("name", "")

    sp_raw = fields.get(FIELD_STORY_POINTS)
    sp = int(sp_raw) if sp_raw is not None else 0
    if issue_type == "Bug":
        sp = 0

    epic_key = fields.get(FIELD_EPIC_LINK)

    sprint_name = extract_sprint_name(fields.get("sprint"))

    assignee_field = fields.get("assignee")
    assignee = None
    if assignee_field:
        assignee = assignee_field.get("emailAddress") or assignee_field.get("displayName")

    updated_raw = fields.get("updated", "")
    last_updated = None
    days_since_update = 0
    if updated_raw:
        updated_dt = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        last_updated = updated_dt.strftime("%Y-%m-%d")
        days_since_update = (today - updated_dt.replace(tzinfo=timezone.utc)).days

    flagged_field = fields.get(FIELD_FLAGGED)
    flagged = bool(flagged_field)

    blocked_by = extract_blocked_by(issue)

    return {
        "key": key,
        "summary": summary,
        "status": status,
        "issue_type": issue_type,
        "sp": sp,
        "epic_key": epic_key,
        "sprint": sprint_name,
        "assignee": assignee,
        "last_updated": last_updated,
        "days_since_update": days_since_update,
        "flagged": flagged,
        "blocked_by": blocked_by,
    }


def load_input(path):
    """Load JSON from a file path or stdin."""
    if path == "-":
        return json.load(sys.stdin)
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(
        description="Transform raw MCP jira_search response JSON into structured issue data."
    )
    parser.add_argument("inputs", nargs="+", metavar="input_file", help="Input JSON files (use - for stdin)")
    parser.add_argument("-o", "--output", required=True, help="Output JSON file path")
    args = parser.parse_args()

    today = datetime.now(timezone.utc)
    all_issues = []

    for path in args.inputs:
        raw = load_input(path)
        issues = unwrap_mcp_response(raw)
        all_issues.extend(issues)

    transformed = [transform_issue(issue, today) for issue in all_issues]

    by_status = {}
    by_project = {}
    by_type = {}
    total_sp = 0

    for t in transformed:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1

        project = t["key"].split("-")[0] if "-" in t["key"] else t["key"]
        by_project[project] = by_project.get(project, 0) + 1

        by_type[t["issue_type"]] = by_type.get(t["issue_type"], 0) + 1

        total_sp += t["sp"]

    output = {
        "generated_at": today.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_issues": len(transformed),
        "total_sp": total_sp,
        "by_status": by_status,
        "by_project": by_project,
        "by_type": by_type,
        "issues": transformed,
    }

    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Processed {len(transformed)} issues", file=sys.stderr)


if __name__ == "__main__":
    main()
