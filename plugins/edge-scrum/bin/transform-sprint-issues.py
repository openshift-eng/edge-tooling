#!/usr/bin/env python3
"""Transform raw Jira search results into sprint_issues.json."""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import (
    extract_issue_type,
    extract_sp,
    extract_epic_key,
    extract_flagged,
    extract_blocked_by,
    extract_assignee_username,
    has_acceptance_criteria,
    is_stale,
    format_date,
    get_nested,
    load_issues,
    write_output,
    DONE_STATUSES_SPRINT,
    IN_PROGRESS_STATUSES,
)


def transform_issue(raw, today):
    issue_type = extract_issue_type(raw)
    status = get_nested(raw, "status", "name") or ""
    updated = format_date(raw.get("updated", raw.get("created", today)))
    created = format_date(raw.get("created", today))

    return {
        "key": raw.get("key", ""),
        "summary": raw.get("summary", ""),
        "type": issue_type,
        "status": status,
        "assignee": extract_assignee_username(raw),
        "sp": extract_sp(raw, issue_type),
        "epic_key": extract_epic_key(raw),
        "flagged": extract_flagged(raw),
        "blocked_by": extract_blocked_by(raw),
        "stale": status in IN_PROGRESS_STATUSES and is_stale(updated, today),
        "created": created,
        "updated": updated,
        "has_ac": has_acceptance_criteria(raw.get("description")),
        "labels": raw.get("labels", []),
    }


def compute_aggregates(issues):
    sp_by_assignee = {}
    issues_by_type = {"Story": [], "Bug": [], "Spike": [], "Task": []}
    issues_by_epic = {}
    total_sp = 0
    total_done_sp = 0

    for issue in issues:
        key = issue["key"]

        if issue["assignee"] and issue["sp"] > 0:
            sp_by_assignee[issue["assignee"]] = (
                sp_by_assignee.get(issue["assignee"], 0) + issue["sp"]
            )

        issues_by_type.setdefault(issue["type"], []).append(key)

        issues_by_epic.setdefault(issue["epic_key"], []).append(key)

        total_sp += issue["sp"]
        if issue["status"] in DONE_STATUSES_SPRINT:
            total_done_sp += issue["sp"]

    issues_by_epic.setdefault("No Epic", [])

    return {
        "sp_by_assignee": sp_by_assignee,
        "issues_by_type": issues_by_type,
        "issues_by_epic": issues_by_epic,
        "total_sp": total_sp,
        "total_done_sp": total_done_sp,
        "total_remaining_sp": total_sp - total_done_sp,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transform Jira search results into sprint_issues.json"
    )
    parser.add_argument("--input", nargs="+", required=True, help="Raw MCP response file(s)")
    parser.add_argument("--output", required=True, help="Output path for sprint_issues.json")
    parser.add_argument("--sprint-id", type=int, required=True)
    parser.add_argument("--sprint-name", required=True)
    parser.add_argument("--today", required=True, help="Today's date YYYY-MM-DD")
    args = parser.parse_args()

    raw_issues = load_issues(args.input)
    issues = [transform_issue(raw, args.today) for raw in raw_issues]
    aggregates = compute_aggregates(issues)

    output = {
        "sprint_id": args.sprint_id,
        "sprint_name": args.sprint_name,
        "total_issues": len(issues),
        "issues": issues,
        **aggregates,
    }

    write_output(output, args.output)


if __name__ == "__main__":
    main()
