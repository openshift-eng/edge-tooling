#!/usr/bin/env python3
"""Helper script for /create-ocpedge-stories command.

Handles deterministic operations: argument parsing, ticket grouping,
description generation, and link checking.

Usage:
    python ocpedge_rhel_helper.py parse-args <arguments>
    python ocpedge_rhel_helper.py group-tickets <tickets_json>
    python ocpedge_rhel_helper.py generate-description <ticket_keys_json>
    python ocpedge_rhel_helper.py check-links <tickets_json>
"""

import json
import re
import sys
from collections import defaultdict


VERSION_SUFFIX_RE = re.compile(r"\s*\[rhel-[\d.]+z?\]\s*$", re.IGNORECASE)
TICKET_KEY_RE = re.compile(r"[A-Z]+-\d+")
OCPBUGS_RE = re.compile(r"OCPBUGS-\d+")
JIRA_BASE_URL = "https://issues.redhat.com/browse"


def parse_args(arguments: str) -> dict:
    """Parse command arguments into structured format.

    Returns:
        {
            "mode": "jql" | "tickets" | "interactive",
            "dry_run": bool,
            "jql": "<query>" (if mode=jql),
            "tickets": ["RHEL-123", ...] (if mode=tickets)
        }
    """
    arguments = arguments.strip()
    dry_run = "--dry-run" in arguments
    arguments = arguments.replace("--dry-run", "").strip()

    if not arguments:
        return {"mode": "interactive", "dry_run": dry_run}

    if arguments.lower().startswith("jql:"):
        return {"mode": "jql", "jql": arguments[4:].strip(), "dry_run": dry_run}

    keys = TICKET_KEY_RE.findall(arguments)
    if keys:
        return {"mode": "tickets", "tickets": keys, "dry_run": dry_run}

    return {"mode": "interactive", "dry_run": dry_run}


def strip_version_suffix(summary: str) -> str:
    """Strip RHEL version suffixes like [rhel-9.6.z] from a summary."""
    return VERSION_SUFFIX_RE.sub("", summary).strip()


def extract_ocpbugs_refs(ticket: dict) -> list[str]:
    """Extract OCPBUGS-XXXXX references from summary and description."""
    refs = set()
    for field in ["summary", "description"]:
        text = ticket.get(field, "") or ""
        refs.update(OCPBUGS_RE.findall(text))
    return sorted(refs)


def extract_clone_links(ticket: dict) -> list[str]:
    """Extract ticket keys linked via clones/is cloned by."""
    clone_types = {"cloners", "clones", "is cloned by"}
    linked_keys = []
    for link in ticket.get("issuelinks", []):
        link_type = link.get("type", {}).get("name", "").lower()
        if link_type in clone_types:
            for direction in ["inwardIssue", "outwardIssue", "inward_issue", "outward_issue"]:
                issue = link.get(direction)
                if issue:
                    linked_keys.append(issue["key"])
    return linked_keys


def find_missing_clones(tickets: list[dict]) -> list[str]:
    """Find clone-linked ticket keys that are not in the current ticket set.

    Returns a list of ticket keys that are referenced via clone links
    but were not included in the input (i.e. sibling clones that weren't
    matched by the JQL filter).
    """
    known_keys = {t["key"] for t in tickets}
    missing = set()
    for t in tickets:
        for linked_key in extract_clone_links(t):
            if linked_key not in known_keys:
                missing.add(linked_key)
    return sorted(missing)


def extract_ocpedge_links(ticket: dict) -> list[str]:
    """Extract existing OCPEDGE issue keys from a ticket's issuelinks."""
    ocpedge_keys = []
    for link in ticket.get("issuelinks", []):
        for direction in ["inwardIssue", "outwardIssue", "inward_issue", "outward_issue"]:
            issue = link.get(direction)
            if issue:
                key = issue["key"]
                if key.startswith("OCPEDGE-"):
                    ocpedge_keys.append(key)
    return ocpedge_keys


def group_tickets(tickets: list[dict]) -> list[dict]:
    """Group related RHEL tickets by base summary, clone links, and OCPBUGS refs.

    Input: list of ticket dicts with at least "key", "summary", "issuelinks",
           and optionally "description".

    Returns:
        [
            {
                "base_summary": "<stripped summary>",
                "tickets": ["RHEL-123", "RHEL-456"],
                "ocpbugs_refs": ["OCPBUGS-789"],
                "existing_ocpedge_links": ["OCPEDGE-101"]
            },
            ...
        ]
    """
    # Build union-find for merging groups
    parent = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    ticket_map = {}
    for t in tickets:
        key = t["key"]
        parent[key] = key
        ticket_map[key] = t

    # Group by base summary
    summary_groups = defaultdict(list)
    for t in tickets:
        base = strip_version_suffix(t.get("summary", ""))
        summary_groups[base].append(t["key"])

    for keys in summary_groups.values():
        for k in keys[1:]:
            union(keys[0], k)

    # Group by clone links
    for t in tickets:
        for linked_key in extract_clone_links(t):
            if linked_key in ticket_map:
                union(t["key"], linked_key)

    # Group by shared OCPBUGS refs
    ocpbugs_map = defaultdict(list)
    for t in tickets:
        for ref in extract_ocpbugs_refs(t):
            ocpbugs_map[ref].append(t["key"])
    for keys in ocpbugs_map.values():
        for k in keys[1:]:
            union(keys[0], k)

    # Collect groups
    groups = defaultdict(list)
    for key in ticket_map:
        groups[find(key)].append(key)

    result = []
    for root, members in groups.items():
        members.sort()
        # Use the base summary from the first ticket
        base_summary = strip_version_suffix(
            ticket_map[members[0]].get("summary", "")
        )

        # Collect all OCPBUGS refs and existing OCPEDGE links
        all_ocpbugs = set()
        all_ocpedge = set()
        for key in members:
            all_ocpbugs.update(extract_ocpbugs_refs(ticket_map[key]))
            all_ocpedge.update(extract_ocpedge_links(ticket_map[key]))

        result.append(
            {
                "base_summary": base_summary,
                "tickets": members,
                "ocpbugs_refs": sorted(all_ocpbugs),
                "existing_ocpedge_links": sorted(all_ocpedge),
            }
        )

    # Sort groups by first ticket key for consistent output
    result.sort(key=lambda g: g["tickets"][0])
    return result


def generate_description(ticket_keys: list[str]) -> str:
    """Generate the OCPEDGE story description from a list of RHEL ticket keys."""
    lines = ["Ticket verification and automation for this test:", ""]
    for key in sorted(ticket_keys):
        lines.append(f"- [{key}]({JIRA_BASE_URL}/{key})")
    return "\n".join(lines)


def generate_summary(base_summary: str) -> str:
    """Generate the OCPEDGE story summary."""
    return f"RHEL bug fix verification: {base_summary}"


def check_links(tickets: list[dict]) -> dict:
    """Check which tickets already have OCPEDGE links.

    Returns:
        {
            "linked": {"RHEL-123": ["OCPEDGE-456"]},
            "unlinked": ["RHEL-789"]
        }
    """
    linked = {}
    unlinked = []
    for t in tickets:
        ocpedge = extract_ocpedge_links(t)
        if ocpedge:
            linked[t["key"]] = ocpedge
        else:
            unlinked.append(t["key"])
    return {"linked": linked, "unlinked": unlinked}


def read_json_input() -> str:
    """Read JSON input from CLI argument or stdin (use '-' for stdin)."""
    if len(sys.argv) > 2 and sys.argv[2] != "-":
        return sys.argv[2]
    return sys.stdin.read()


def main():
    if len(sys.argv) < 2:
        print("Usage: ocpedge_rhel_helper.py <command> [args | -]", file=sys.stderr)
        print("  Pass '-' to read JSON from stdin instead of CLI args.", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == "parse-args":
        arguments = " ".join(sys.argv[2:])
        print(json.dumps(parse_args(arguments), indent=2))

    elif command == "group-tickets":
        data = json.loads(read_json_input())
        print(json.dumps(group_tickets(data), indent=2))

    elif command == "generate-description":
        keys = json.loads(read_json_input())
        print(generate_description(keys))

    elif command == "generate-summary":
        base_summary = sys.argv[2]
        print(generate_summary(base_summary))

    elif command == "check-links":
        data = json.loads(read_json_input())
        print(json.dumps(check_links(data), indent=2))

    elif command == "find-missing-clones":
        data = json.loads(read_json_input())
        print(json.dumps(find_missing_clones(data), indent=2))

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
