#!/usr/bin/env python3
"""Transform raw Jira search results into features.json."""

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import (
    extract_display_name,
    extract_tshirt_size,
    has_acceptance_criteria,
    get_nested,
    load_issues,
    write_output,
)


def transform_feature(raw):
    # Extract spike candidates from issuelinks
    spike_candidates = []
    for link in raw.get("issuelinks", []):
        link_type = link.get("type", {})
        if link_type.get("inward") == "is blocked by":
            inward = link.get("inward_issue") or link.get("inwardIssue")
            if inward:
                itype = (
                    get_nested(inward, "issue_type", "name")
                    or get_nested(inward, "issuetype", "name")
                    or ""
                )
                if itype == "Spike":
                    spike_candidates.append(
                        {
                            "key": inward.get("key"),
                            "status": get_nested(inward, "status", "name") or "",
                            "issuelinks": inward.get("issuelinks", []),
                        }
                    )

    return {
        "key": raw.get("key", ""),
        "summary": raw.get("summary", ""),
        "type": (
            get_nested(raw, "issue_type", "name")
            or get_nested(raw, "issuetype", "name")
            or ""
        ),
        "status": get_nested(raw, "status", "name") or "",
        "sme": extract_display_name(raw.get("customfield_10475"), "None"),
        "qa_contact": extract_display_name(raw.get("customfield_10470"), "None"),
        "docs_approver": extract_display_name(raw.get("customfield_10473"), "None"),
        "has_ac": has_acceptance_criteria(raw.get("description")),
        "size": extract_tshirt_size(raw),
        "spike_candidates": spike_candidates,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transform Jira search results into features.json"
    )
    parser.add_argument("--input", nargs="+", required=True, help="Raw MCP response file(s)")
    parser.add_argument("--output", required=True, help="Output path")
    parser.add_argument(
        "--fallback-used", action="store_true", help="Set if fallback JQL was used"
    )
    args = parser.parse_args()

    raw_issues = load_issues(args.input)
    features = [transform_feature(raw) for raw in raw_issues]
    feature_keys = [f["key"] for f in features]

    output = {
        "fallback_used": args.fallback_used,
        "feature_keys": feature_keys,
        "feature_keys_csv": ", ".join(feature_keys),
        "features": features,
    }

    write_output(output, args.output)


if __name__ == "__main__":
    main()
