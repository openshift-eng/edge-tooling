#!/usr/bin/env python3
"""Enrich OCPBUGS with Jira data from Atlassian MCP.

Reads a JSON array on stdin with bug details fetched via MCP, processes them
(determines release_action, flags unresolved bugs, identifies CVE trackers),
and outputs an enriched markdown table with updated recommendations.

Input JSON format (array of objects):
[
  {
    "key": "OCPBUGS-12345",
    "version": "4.21",
    "summary": "...",
    "status": "Closed",
    "labels": ["release-required", ...],
    "issuetype": "Bug"
  }
]

Usage: echo '<json>' | python3 enrich_ocpbugs.py
"""

import json
import sys

RESOLVED_STATUSES = {"MODIFIED", "ON_QA", "Verified", "Closed"}


def classify_bug(bug):
    """Determine release_action and resolution state for a bug."""
    labels = bug.get("labels", [])
    status = bug.get("status", "unknown")
    issuetype = bug.get("issuetype", "Bug")

    has_required = "release-required" in labels
    has_not_required = "release-not-required" in labels

    is_cve_tracker = issuetype == "Vulnerability"
    is_resolved = status in RESOLVED_STATUSES

    if not is_resolved:
        release_action = "release_not_required"
    elif has_required and not has_not_required:
        release_action = "release_required"
    elif has_not_required and not has_required:
        release_action = "release_not_required"
    else:
        release_action = "needs_review"

    return {
        **bug,
        "release_action": release_action,
        "is_cve_tracker": is_cve_tracker,
        "is_resolved": is_resolved,
    }


def render_table(bugs):
    """Render enriched OCPBUGS markdown table."""
    lines = []
    lines.append("## OCPBUGS (via Atlassian MCP)")
    lines.append("")
    lines.append("| Bug | Version | Type | Status | Release Action | Summary |")
    lines.append("|-----|---------|------|--------|----------------|---------|")

    for b in bugs:
        typ = "Vulnerability" if b["is_cve_tracker"] else "Bug"
        priority = b.get("priority", "")
        if priority and priority not in ("Undefined", "") and typ == "Bug":
            typ = f"Bug ({priority})"
        note = ""
        if not b["is_resolved"]:
            note = " (unresolved)"
        if b["is_cve_tracker"]:
            note = " (CVE tracker)"
        lines.append(
            f"| {b['key']} | {b['version']} | {typ} | "
            f"{b['status']}{note} | {b['release_action']} | {b['summary'][:80].replace('|', '\\|').replace(chr(10), ' ')} |"
        )

    return "\n".join(lines)


def render_recommendations(bugs):
    """Render updated recommendations based on enriched bug data."""
    by_version = {}
    for b in bugs:
        v = b["version"]
        if v not in by_version:
            by_version[v] = []
        by_version[v].append(b)

    lines = []
    lines.append("")
    lines.append("## Updated Recommendations (post-enrichment)")
    lines.append("")

    for version in sorted(by_version.keys()):
        vbugs = by_version[version]

        resolved_bugs = [b for b in vbugs if b["is_resolved"]]
        unresolved_bugs = [b for b in vbugs if not b["is_resolved"]]
        cve_trackers = [b for b in vbugs if b["is_cve_tracker"]]
        real_bugs = [b for b in resolved_bugs if not b["is_cve_tracker"]]

        release_required = [b for b in real_bugs if b["release_action"] == "release_required"]
        release_not_required = [b for b in real_bugs if b["release_action"] == "release_not_required"]
        needs_review = [b for b in real_bugs if b["release_action"] == "needs_review"]

        parts = []
        if release_required:
            parts.append(f"{len(release_required)} release-required")
        if release_not_required:
            parts.append(f"{len(release_not_required)} release-not-required")
        if needs_review:
            parts.append(f"{len(needs_review)} needs-review")
        if cve_trackers:
            parts.append(f"{len(cve_trackers)} CVE tracker(s) covered by advisory")
        if unresolved_bugs:
            keys = ", ".join(b["key"] for b in unresolved_bugs)
            parts.append(f"{len(unresolved_bugs)} unresolved ({keys})")

        summary = ", ".join(parts) if parts else "no actionable bugs"
        lines.append(f"- **{version}**: {summary}")

    return "\n".join(lines)


def main():
    try:
        bugs = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(bugs, list):
        print(f"Error: expected JSON array, got {type(bugs).__name__}", file=sys.stderr)
        sys.exit(1)

    classified = [classify_bug(b) for b in bugs]
    print(render_table(classified))
    print(render_recommendations(classified))


if __name__ == "__main__":
    main()
