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
    extract_user_username,
    extract_display_name,
    extract_sprints,
    has_acceptance_criteria,
    is_stale,
    safe_format_date,
    get_nested,
    load_issues,
    load_json,
    write_output,
    DONE_STATUSES_SPRINT,
    IN_PROGRESS_STATUSES,
)


def _none_if_null_string(value):
    """Treat the literal strings "null"/"none"/"" (any case) as a missing value.

    Metadata templates should emit a bare JSON null for a missing date, but a
    stray string "null" would otherwise be read downstream as a real date.
    """
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none"):
        return None
    return value


def transform_issue(raw, today):
    issue_type = extract_issue_type(raw)
    status = get_nested(raw, "status", "name") or ""
    # Unreadable or missing dates fall back (to created, then today) instead of raising.
    created = safe_format_date(raw.get("created"), today)
    updated = safe_format_date(raw.get("updated"), created)

    return {
        "key": raw.get("key", ""),
        "summary": raw.get("summary", ""),
        "type": issue_type,
        "status": status,
        "assignee": extract_assignee_username(raw),
        "assignee_display": extract_display_name(raw.get("assignee"), fallback="Unassigned"),
        "qa_contact": extract_user_username(raw.get("customfield_10470")),
        "qa_contact_display": extract_display_name(raw.get("customfield_10470"), fallback="None"),
        "sp": extract_sp(raw, issue_type),
        "epic_key": extract_epic_key(raw),
        "flagged": extract_flagged(raw),
        "blocked_by": extract_blocked_by(raw),
        "stale": status in IN_PROGRESS_STATUSES and is_stale(updated, today),
        "created": created,
        "updated": updated,
        # Sprint IDs from customfield_10020. Jira does not return these in a
        # reliable order. compute-velocity picks the issue's finishing sprint by
        # latest sprint end date (from sprint_details below), falling back to the
        # highest id in this list, then to the resolution date. Drop any
        # unparseable (None) id so it never shadows a real finishing sprint.
        "sprints": [s["id"] for s in extract_sprints(raw.get("customfield_10020")) if s["id"] is not None],
        # Same sprints with their end dates. compute-velocity prefers the sprint
        # with the latest end date here (more reliable than id order when a sprint
        # is renumbered or reopened), falling back to the max id in "sprints" above,
        # then to the resolution date, when the end dates are missing.
        "sprint_details": [{"id": s["id"], "end": s.get("end")}
                           for s in extract_sprints(raw.get("customfield_10020"))
                           if s["id"] is not None],
        # Resolution date (ISO YYYY-MM-DD) or None if unresolved — used by
        # compute-velocity as a fallback when sprint history is unavailable.
        "resolved": safe_format_date(raw.get("resolutiondate"), None),
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
    parser.add_argument("--sprint-id", type=int, default=None)
    parser.add_argument("--sprint-name", default=None)
    parser.add_argument(
        "--sprint-meta",
        default=None,
        help='JSON file with {"sprint_id","sprint_name"[,"sprint_start","sprint_end"]}. '
        "Preferred over --sprint-id/--sprint-name: avoids interpolating the Jira "
        "sprint name into a shell command.",
    )
    parser.add_argument("--today", required=True, help="Today's date YYYY-MM-DD")
    args = parser.parse_args()

    sprint_id = args.sprint_id
    sprint_name = args.sprint_name
    sprint_start = None
    sprint_end = None
    if args.sprint_meta:
        meta = load_json(args.sprint_meta)
        sprint_id = meta.get("sprint_id", sprint_id)
        sprint_name = meta.get("sprint_name", sprint_name)
        # Defensive: a template may emit the string "null" for a missing date
        # instead of a bare JSON null; treat that (and "") as absent so velocity
        # does not read it as a real resolution window.
        sprint_start = _none_if_null_string(meta.get("sprint_start"))
        sprint_end = _none_if_null_string(meta.get("sprint_end"))

    if sprint_id is None:
        parser.error("one of --sprint-id or --sprint-meta (with sprint_id) is required")

    raw_issues = load_issues(args.input)
    issues = [transform_issue(raw, args.today) for raw in raw_issues]
    aggregates = compute_aggregates(issues)

    output = {
        "sprint_id": sprint_id,
        "sprint_name": sprint_name,
        "sprint_start": sprint_start,
        "sprint_end": sprint_end,
        "total_issues": len(issues),
        "issues": issues,
        **aggregates,
    }

    write_output(output, args.output)


if __name__ == "__main__":
    main()
