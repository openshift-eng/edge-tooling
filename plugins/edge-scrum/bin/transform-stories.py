#!/usr/bin/env python3
"""Transform raw Jira search results into stories.json for release planning."""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import (
    extract_issue_type,
    extract_sp,
    extract_parent_key,
    extract_flagged,
    extract_blocked_by,
    extract_assignee_username,
    extract_display_name,
    is_stale,
    safe_format_date,
    get_nested,
    load_issues,
    write_output,
    IN_PROGRESS_STATUSES,
    extract_sprints,
)

DONE_STATUSES_STORIES = {"Closed"}
DONE_STATUSES_OCPBUGS = {"Closed", "Verified"}


def transform_story(raw, today):
    issue_type = extract_issue_type(raw)
    status = get_nested(raw, "status", "name") or ""
    updated = safe_format_date(raw.get("updated") or raw.get("created") or today, today)
    priority = get_nested(raw, "priority", "name") or "Major"
    assignee_raw = raw.get("assignee")

    return {
        "key": raw.get("key", ""),
        "summary": raw.get("summary", ""),
        "type": issue_type,
        "status": status,
        "assignee": extract_assignee_username(raw),
        "assignee_display": extract_display_name(assignee_raw, fallback="Unassigned"),
        "sp": extract_sp(raw, issue_type),
        "epic_key": extract_parent_key(raw),
        "flagged": extract_flagged(raw),
        "blocked_by": extract_blocked_by(raw),
        "stale": status in IN_PROGRESS_STATUSES and is_stale(updated, today),
        "labels": raw.get("labels", []),
        "priority": priority,
        # Raw updated date (None if Jira omitted it) — used as activity evidence.
        # `updated` above falls back to today for the staleness test only.
        "updated": safe_format_date(raw.get("updated"), None),
        "sprints": extract_sprints(raw.get("customfield_10020")),
    }


def compute_aggregates(stories):
    stories_by_epic = {}
    stories_by_assignee = {}
    sp_by_assignee = {}
    sp_by_assignee_by_epic = {}
    sp_by_epic = {}
    done_sp_by_epic = {}
    remaining_sp_by_epic = {}
    unassigned_stories = []
    unpointed_stories = []

    for story in stories:
        key = story["key"]
        epic = story["epic_key"]
        assignee = story["assignee"]
        sp = story["sp"]
        done_set = DONE_STATUSES_OCPBUGS if story["key"].startswith("OCPBUGS-") else DONE_STATUSES_STORIES
        is_done = story["status"] in done_set
        is_bug = story["type"] == "Bug"

        stories_by_epic.setdefault(epic, []).append(key)
        sp_by_epic[epic] = sp_by_epic.get(epic, 0) + sp
        done_sp_by_epic[epic] = done_sp_by_epic.get(epic, 0) + (sp if is_done else 0)

        if assignee:
            stories_by_assignee.setdefault(assignee, []).append(key)
            if not is_done and not is_bug and sp > 0:
                sp_by_assignee[assignee] = sp_by_assignee.get(assignee, 0) + sp
                sp_by_assignee_by_epic.setdefault(epic, {})
                sp_by_assignee_by_epic[epic][assignee] = (
                    sp_by_assignee_by_epic[epic].get(assignee, 0) + sp
                )
        else:
            if not is_done and not is_bug:
                unassigned_stories.append(key)

        if sp == 0 and not is_bug and not is_done:
            unpointed_stories.append(key)

    for epic in sp_by_epic:
        remaining_sp_by_epic[epic] = sp_by_epic[epic] - done_sp_by_epic.get(epic, 0)

    return {
        "stories_by_epic": stories_by_epic,
        "stories_by_assignee": stories_by_assignee,
        "sp_by_assignee": sp_by_assignee,
        "sp_by_assignee_by_epic": sp_by_assignee_by_epic,
        "sp_by_epic": sp_by_epic,
        "done_sp_by_epic": done_sp_by_epic,
        "remaining_sp_by_epic": remaining_sp_by_epic,
        "unassigned_stories": unassigned_stories,
        "unpointed_stories": unpointed_stories,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transform Jira search results into stories.json"
    )
    parser.add_argument("--input", nargs="+", required=True, help="Raw MCP response file(s)")
    parser.add_argument("--output", required=True, help="Output path for stories.json")
    parser.add_argument("--today", required=True, help="Today's date YYYY-MM-DD")
    args = parser.parse_args()

    raw_issues = load_issues(args.input)
    stories = []
    skipped_issues = []
    for raw in raw_issues:
        try:
            stories.append(transform_story(raw, args.today))
        except Exception as e:
            key = raw.get("key", "unknown")
            skipped_issues.append({"key": key, "error": str(e)})
            print(f"WARNING: skipped {key}: {e}", file=sys.stderr)

    aggregates = compute_aggregates(stories)

    output = {
        "total_stories": len(stories),
        "skipped_issues": skipped_issues,
        "stories": stories,
        **aggregates,
    }

    write_output(output, args.output)


if __name__ == "__main__":
    main()
