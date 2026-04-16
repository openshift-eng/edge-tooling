#!/usr/bin/env python3
"""Transform raw Jira search results into epics.json."""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import (
    extract_parent_key,
    extract_display_name,
    has_acceptance_criteria,
    get_nested,
    load_issues,
    write_output,
)


def transform_epic(raw):
    sp_raw = raw.get("customfield_10028")
    if isinstance(sp_raw, dict):
        val = sp_raw.get("value")
        size = str(int(val)) if val is not None else "Unsized"
    elif sp_raw is not None:
        size = str(int(float(sp_raw)))
    else:
        size = "Unsized"

    return {
        "key": raw.get("key", ""),
        "summary": raw.get("summary", ""),
        "status": get_nested(raw, "status", "name") or "",
        "feature_key": extract_parent_key(raw),
        "assignee": extract_display_name(raw.get("assignee"), "Unassigned"),
        "qa_contact": extract_display_name(raw.get("customfield_10470"), "None"),
        "size": size,
        "has_ac": has_acceptance_criteria(raw.get("description")),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transform Jira search results into epics.json"
    )
    parser.add_argument("--input", nargs="+", required=True, help="Raw MCP response file(s)")
    parser.add_argument("--output", required=True, help="Output path")
    args = parser.parse_args()

    raw_issues = load_issues(args.input)
    epics = [transform_epic(raw) for raw in raw_issues]
    epic_keys = [e["key"] for e in epics]

    feature_to_epics = {}
    for epic in epics:
        feature_to_epics.setdefault(epic["feature_key"], []).append(epic["key"])

    output = {
        "epic_keys": epic_keys,
        "epic_keys_csv": ", ".join(epic_keys),
        "feature_to_epics": feature_to_epics,
        "epics": epics,
    }

    write_output(output, args.output)


if __name__ == "__main__":
    main()
