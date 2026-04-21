#!/usr/bin/env python3
"""Helper script for /two-node:ticket-tracker command.

Handles deterministic operations: argument parsing, stream detection,
z-stream gap analysis, OCPBUGS state validation, PR grouping,
report formatting, and report diffing.

Usage:
    python ticket_tracker_helper.py parse-args <arguments>
    python ticket_tracker_helper.py detect-streams <tickets_json>
    python ticket_tracker_helper.py check-zstream-gaps <streams_json>
    python ticket_tracker_helper.py validate-state <pr_data_json>
    python ticket_tracker_helper.py group-prs <prs_json>
    python ticket_tracker_helper.py format-report <report_data_json>
    python ticket_tracker_helper.py diff-data <previous.json> <current.json>
    python ticket_tracker_helper.py history-filename <history_dir>
    python ticket_tracker_helper.py latest-data-file <history_dir>
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone


# Expected z-stream coverage for a fully-cloned RHEL ticket.
# Update this list when new streams are added.
EXPECTED_STREAMS = [
    "(parent)",
    "rhel-9.9",
    "rhel-9.6.z",
    "rhel-9.7.z",
    "rhel-9.8",
    "rhel-9.8.z",
    "rhel-10.2.z",
]

OCPBUGS_RE = re.compile(r"OCPBUGS-\d+")
TICKET_REF_RE = re.compile(r"(?:OCPBUGS|OCPEDGE)-\d+")
VERSION_SUFFIX_RE = re.compile(r"\s*\[rhel-[\d.]+z?\]\s*$", re.IGNORECASE)

TOPOLOGY_CONFIG = {
    "tnf": {
        "label": "TNF (Two-Node Fencing)",
        "repo": "ClusterLabs/resource-agents",
        "pr_keyword": "podman-etcd",
        "ci_job_pattern": "two-node-fencing",
        "has_rhel_tickets": True,
    },
    "tna": {
        "label": "TNA (Two-Node Arbiter)",
        "repo": "openshift/cluster-etcd-operator",
        "pr_keyword": "arbiter",
        "ci_job_pattern": "two-node-arbiter",
        "has_rhel_tickets": False,
    },
}

OCPEDGE_RULES = [
    # (min_days, max_days, flagged_states, expected_states, severity, label)
    (3,   7,   ["To Do", "Backlog"],
     ["In Progress", "Code Review", "Done"],   "low",    "should move soon"),
    (7,  14,   ["To Do", "Backlog"],
     ["In Progress", "Done"],                   "medium", "stale — action needed"),
    (7,  14,   ["In Progress"],
     ["Code Review", "Done"],                   "low",    "should move to review/done"),
    (14, 9999, ["To Do", "Backlog"],
     ["Done"],                                  "high",   "significantly stale — escalate"),
    (14, 9999, ["In Progress"],
     ["Code Review", "Done"],                   "medium", "stale — action needed"),
]


# ---------------------------------------------------------------------------
# parse-args
# ---------------------------------------------------------------------------

def parse_args(arguments: str) -> dict:
    """Parse command arguments into structured format.

    Returns:
        {
            "mode": "specific" | "all",
            "topologies": ["tnf", "tna"],
            "pr_numbers": [2130, 2134],
            "ticket_keys": ["OCPBUGS-76538"],
            "output_file": "report.md" | null,
            "diff": bool
        }
    """
    args = arguments.strip()

    diff = "--diff" in args
    args = args.replace("--diff", "").strip()

    output_file = None
    if "--output" in args:
        parts = args.split("--output", 1)
        args = parts[0].strip()
        output_part = parts[1].strip()
        if output_part:
            tokens = output_part.split()
            output_file = tokens[0]
            args = (args + " " + " ".join(tokens[1:])).strip()

    topologies = ["tnf", "tna"]
    tokens = args.split()
    if tokens and tokens[0].lower() in ("tnf", "tna"):
        topologies = [tokens[0].lower()]
        args = " ".join(tokens[1:])

    ticket_keys = TICKET_REF_RE.findall(args)
    if ticket_keys and "all" not in args.lower():
        return {
            "mode": "specific",
            "topologies": topologies,
            "pr_numbers": [],
            "ticket_keys": ticket_keys,
            "output_file": output_file,
            "diff": diff,
        }

    numbers = re.findall(r"\b\d+\b", args)
    if numbers and "all" not in args.lower():
        return {
            "mode": "specific",
            "topologies": topologies,
            "pr_numbers": [int(n) for n in numbers],
            "ticket_keys": [],
            "output_file": output_file,
            "diff": diff,
        }

    return {
        "mode": "all",
        "topologies": topologies,
        "pr_numbers": [],
        "ticket_keys": [],
        "output_file": output_file,
        "diff": diff,
    }


# ---------------------------------------------------------------------------
# detect-streams
# ---------------------------------------------------------------------------

def detect_stream(ticket: dict) -> str:
    """Determine which stream a RHEL ticket targets.

    Priority:
      1. fixVersions field (authoritative)
      2. Summary suffix tag (fallback)
      3. No stream → parent tracker
    """
    fix_versions = ticket.get("fixVersions") or ticket.get("fix_versions") or []
    if isinstance(fix_versions, list):
        for fv in fix_versions:
            name = fv.get("name", "") if isinstance(fv, dict) else str(fv)
            if name:
                return name

    summary = ticket.get("summary", "")
    match = VERSION_SUFFIX_RE.search(summary)
    if match:
        return match.group().strip().strip("[]")

    return "(parent)"


def detect_streams(tickets: list[dict]) -> list[dict]:
    """Add a ``stream`` field to each RHEL ticket dict."""
    result = []
    for t in tickets:
        out = dict(t)
        out["stream"] = detect_stream(t)
        result.append(out)
    return result


# ---------------------------------------------------------------------------
# check-zstream-gaps
# ---------------------------------------------------------------------------

def check_zstream_gaps(streams: list[str]) -> dict:
    """Compare found streams against EXPECTED_STREAMS.

    Returns: {"present": [...], "missing": [...], "full_coverage": bool}
    """
    normalized = {s.lower().strip() for s in streams}
    present = [s for s in EXPECTED_STREAMS if s.lower() in normalized]
    missing = [s for s in EXPECTED_STREAMS if s.lower() not in normalized]
    return {"present": present, "missing": missing, "full_coverage": len(missing) == 0}


# ---------------------------------------------------------------------------
# validate-state
# ---------------------------------------------------------------------------

def validate_state(pr_data: dict) -> dict | None:
    """Validate ticket state against PR merge status.

    Input:
        {
            "pr_status": "Merged" | "Open" | "Draft",
            "merge_date": "YYYY-MM-DD" | null,
            "ticket_status": "ASSIGNED",           (or legacy "ocpbugs_status")
            "ticket_project": "OCPBUGS" | "OCPEDGE" (optional, defaults to OCPBUGS)
            "today": "YYYY-MM-DD"                   (optional, defaults to UTC today)
        }

    Returns None if valid, or a dict with severity/message.
    """
    pr_status = pr_data.get("pr_status", "").lower()
    ticket_status = pr_data.get("ticket_status") or pr_data.get("ocpbugs_status", "")
    merge_date_str = pr_data.get("merge_date")
    ticket_project = pr_data.get("ticket_project", "OCPBUGS")

    if pr_status != "merged" or not merge_date_str:
        return None

    merge_date = datetime.strptime(merge_date_str, "%Y-%m-%d").date()
    today_str = pr_data.get("today")
    today = (
        datetime.strptime(today_str, "%Y-%m-%d").date()
        if today_str
        else datetime.now(timezone.utc).date()
    )
    days = (today - merge_date).days

    ocpbugs_rules = [
        # (min_days, max_days, flagged_states, expected_states, severity, label)
        (3,   7,   ["NEW", "ASSIGNED"],
         ["POST", "MODIFIED", "ON_QA"],             "low",    "should move soon"),
        (7,  14,   ["NEW", "ASSIGNED"],
         ["MODIFIED", "ON_QA", "VERIFIED"],          "medium", "stale — action needed"),
        (7,  14,   ["POST"],
         ["MODIFIED", "ON_QA", "VERIFIED"],          "low",    "should move to ON_QA"),
        (14, 9999, ["NEW", "ASSIGNED"],
         ["ON_QA", "VERIFIED", "CLOSED"],            "high",   "significantly stale — escalate"),
        (14, 9999, ["POST"],
         ["ON_QA", "VERIFIED", "CLOSED"],            "medium", "stale POST — action needed"),
        (14, 9999, ["MODIFIED"],
         ["ON_QA", "VERIFIED", "CLOSED"],            "low",    "should move to ON_QA"),
    ]

    rules = OCPEDGE_RULES if ticket_project == "OCPEDGE" else ocpbugs_rules

    for min_d, max_d, flagged, expected, severity, label in rules:
        if min_d <= days < max_d and ticket_status in flagged:
            return {
                "severity": severity,
                "label": label,
                "message": (
                    f"Ticket is {ticket_status} but PR was merged "
                    f"{days} days ago. Expected: {', '.join(expected)}."
                ),
                "expected_states": expected,
                "days_since_merge": days,
            }

    return None


# ---------------------------------------------------------------------------
# group-prs
# ---------------------------------------------------------------------------

def group_prs(prs: list[dict]) -> list[dict]:
    """Group PRs that share a ticket reference within the same topology.

    Input:  [{"number": 2130, "ticket_refs": ["OCPBUGS-76538"], "topology": "tnf"}, ...]
            Also accepts legacy "ocpbugs_refs" field name.
    Output: [{"prs": [2130, 2131], "ticket_refs": ["OCPBUGS-76538"], "topology": "tnf"}, ...]
    """
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

    pr_map = {}
    ticket_to_prs: dict[str, list[int]] = defaultdict(list)

    for pr in prs:
        n = pr["number"]
        parent[n] = n
        pr_map[n] = pr
        refs = pr.get("ticket_refs") or pr.get("ocpbugs_refs", [])
        for ref in refs:
            ticket_to_prs[ref].append(n)

    # Only union PRs that share the same topology
    for pr_nums in ticket_to_prs.values():
        same_topo = defaultdict(list)
        for n in pr_nums:
            topo = pr_map[n].get("topology", "tnf")
            same_topo[topo].append(n)
        for nums in same_topo.values():
            for n in nums[1:]:
                union(nums[0], n)

    groups = defaultdict(list)
    for n in pr_map:
        groups[find(n)].append(n)

    result = []
    for members in groups.values():
        members.sort()
        all_refs = set()
        for n in members:
            all_refs.update(
                pr_map[n].get("ticket_refs") or pr_map[n].get("ocpbugs_refs", [])
            )
        topology = pr_map[members[0]].get("topology", "tnf")
        result.append({
            "prs": members,
            "ticket_refs": sorted(all_refs),
            "topology": topology,
        })

    result.sort(key=lambda g: g["prs"][0])
    return result


# ---------------------------------------------------------------------------
# format-report  (and helpers)
# ---------------------------------------------------------------------------

def _prelim_str(value) -> str:
    """Normalize Preliminary Testing to a display string."""
    if value is None:
        return "Not set"
    if isinstance(value, dict):
        return value.get("value", "Not set") or "Not set"
    return str(value) if value else "Not set"


def _get_ticket(pr: dict) -> dict:
    """Get ticket data from PR, handling both old and new schema."""
    return pr.get("ticket") or pr.get("ocpbugs") or {}


def _format_tnf_summary_table(prs: list[dict]) -> str:
    lines = [
        "| PR | Title | Status | Ticket | Ticket Status "
        "| RHEL Z-streams | Prelim Testing | Build |",
        "|----|-------|--------|--------|-------------- "
        "|----------------|----------------|-------|",
    ]

    for pr in prs:
        ticket = _get_ticket(pr)
        rhel = pr.get("rhel_tickets", [])
        active = [t for t in rhel if t.get("status", "").lower() != "closed"]

        filed = len(active)
        total = len(EXPECTED_STREAMS)

        counts: dict[str, int] = defaultdict(int)
        for t in active:
            counts[_prelim_str(t.get("preliminary_testing"))] += 1
        abbrev = {
            "Pass": "Pass", "Fail": "Fail", "Requested": "Req",
            "Not Started": "NS", "Not set": "—",
        }
        parts = [
            f"{counts[s]} {abbrev.get(s, s)}"
            for s in ("Pass", "Fail", "Requested", "Not Started", "Not set")
            if counts.get(s)
        ]
        prelim = " / ".join(parts) or "—"

        build = "None"
        for t in active:
            b = t.get("fixed_in_build") or t.get("customfield_10578")
            if b:
                build = b if isinstance(b, str) else str(b)
                break

        title = pr.get("title", "")
        if len(title) > 40:
            title = title[:37] + "..."

        lines.append(
            f"| #{pr['number']} | {title} | {pr.get('status', '?')} "
            f"| {ticket.get('key', '—')} | {ticket.get('status', '—')} "
            f"| {filed}/{total} filed | {prelim} | {build} |"
        )

    return "\n".join(lines)


def _format_tna_summary_table(prs: list[dict]) -> str:
    lines = [
        "| PR | Title | Status | Ticket | Ticket Status |",
        "|----|-------|--------|--------|---------------|",
    ]

    for pr in prs:
        ticket = _get_ticket(pr)

        title = pr.get("title", "")
        if len(title) > 50:
            title = title[:47] + "..."

        lines.append(
            f"| #{pr['number']} | {title} | {pr.get('status', '?')} "
            f"| {ticket.get('key', '—')} | {ticket.get('status', '—')} |"
        )

    return "\n".join(lines)


def _format_detailed_pr(pr: dict) -> str:
    parts: list[str] = []
    ticket = _get_ticket(pr)
    topology = pr.get("topology", "tnf")

    parts.append(f"### PR #{pr['number']} — {pr.get('title', '?')}")
    parts.append(f"- **Author**: {pr.get('author', '?')}")
    parts.append(f"- **PR Status**: {pr.get('status', '?')} ({pr.get('date', '?')})")

    if ticket:
        parts.append(
            f"- **Ticket**: {ticket.get('key', '?')} — "
            f"{ticket.get('summary', '?')}"
        )
        parts.append(
            f"- **Ticket Status**: {ticket.get('status', '?')} | "
            f"Priority: {ticket.get('priority', '?')} | "
            f"Assignee: {ticket.get('assignee', '?')}"
        )
        if ticket.get("qa_contact"):
            parts.append(f"- **QA Contact**: {ticket['qa_contact']}")

    linked = ticket.get("linked_tickets") or ticket.get("linked_ocpbugs", [])
    if linked:
        parts.append("")
        parts.append("**Linked Tickets**:")
        for lnk in linked:
            if isinstance(lnk, str):
                parts.append(f"- {lnk}")
            else:
                parts.append(
                    f"- {lnk.get('key', '?')} — "
                    f"{lnk.get('summary', '?')} ({lnk.get('status', '?')})"
                )

    # RHEL tickets table (TNF only)
    rhel = pr.get("rhel_tickets", [])
    if rhel and topology == "tnf":
        parts.append("")
        parts.append("**RHEL Tickets**:")
        parts.append("")
        parts.append(
            "| Key | Fix Version | Status | Preliminary Testing "
            "| Fixed in Build | QA Contact |"
        )
        parts.append(
            "|-----|------------|--------|------------------- "
            "|----------------|------------|"
        )

        def _sort_key(t):
            s = t.get("stream", t.get("fix_version", ""))
            return "0" if s == "(parent)" else s

        for t in sorted(rhel, key=_sort_key):
            prelim = _prelim_str(t.get("preliminary_testing"))
            build = t.get("fixed_in_build") or t.get("customfield_10578") or "—"
            if isinstance(build, dict):
                build = str(build)
            qa = t.get("qa_contact", "—")
            if isinstance(qa, dict):
                qa = qa.get("displayName", "—")
            parts.append(
                f"| {t.get('key', '?')} "
                f"| {t.get('stream', t.get('fix_version', '?'))} "
                f"| {t.get('status', '?')} | {prelim} | {build} | {qa} |"
            )

        active_streams = [
            t.get("stream", t.get("fix_version", ""))
            for t in rhel
            if t.get("status", "").lower() != "closed"
        ]
        gaps = check_zstream_gaps(active_streams)
        parts.append("")
        if gaps["full_coverage"]:
            parts.append("**Z-stream gaps**: Full coverage")
        else:
            parts.append(
                f"**Z-stream gaps**: Missing: {', '.join(gaps['missing'])}"
            )

    sv = pr.get("state_validation")
    if sv:
        parts.append("")
        parts.append(
            f"**State Validation**: {ticket.get('key', '?')} is "
            f"**{ticket.get('status', '?')}** but PR was merged "
            f"**{sv['days_since_merge']} days ago**. "
            f"Expected: {', '.join(sv['expected_states'])}. "
            f"Action needed: move ticket to "
            f"{'/'.join(sv['expected_states'][:2])}."
        )

    ci_statuses = pr.get("ci_status", [])
    if ci_statuses:
         parts.append("")
         parts.append("**CI Status**:")
         for ci in ci_statuses:
             link_str = f" — {ci['link']}" if ci.get("link") else ""
             parts.append(f"- {ci.get('job', '?')}: {ci.get('result', '?')}{link_str}")
    return "\n".join(parts)


def _format_action_items_for_prs(prs: list[dict], topology: str) -> list[str]:
    items: list[str] = []

    for pr in prs:
        ticket = _get_ticket(pr)
        rhel = pr.get("rhel_tickets", [])

        # RHEL-specific checks (TNF only)
        if topology == "tnf":
            if not rhel and pr.get("status", "").lower() == "merged":
                items.append(f"- **PR #{pr['number']}**: No RHEL tickets filed")

            active_streams = [
                t.get("stream", t.get("fix_version", ""))
                for t in rhel
                if t.get("status", "").lower() != "closed"
            ]
            gaps = check_zstream_gaps(active_streams)
            if not gaps["full_coverage"] and active_streams:
                items.append(
                    f"- **PR #{pr['number']} ({ticket.get('key', '?')})**: "
                    f"Missing z-stream clones: {', '.join(gaps['missing'])}"
                )

            for t in rhel:
                if _prelim_str(t.get("preliminary_testing")) == "Fail":
                    items.append(
                        f"- **{t['key']}**: Preliminary Testing is **Fail**"
                    )

            for t in rhel:
                if t.get("status", "").lower() == "closed":
                    continue
                build = t.get("fixed_in_build") or t.get("customfield_10578")
                stream = t.get("stream", t.get("fix_version", "?"))
                if not build and stream != "(parent)":
                    items.append(
                        f"- **{t['key']}** ({stream}): No Fixed in Build set"
                    )

        # Common checks (both topologies)
        sv = pr.get("state_validation")
        if sv:
            items.append(
                f"- **PR #{pr['number']} ({ticket.get('key', '?')})**: "
                f"Ticket state **{ticket.get('status', '?')}** is "
                f"{sv['label']} — PR merged {sv['days_since_merge']} days ago. "
                f"Expected {'/'.join(sv['expected_states'][:2])} by now."
            )

        for ci in pr.get("ci_status", []):
            if ci.get("result", "").lower() == "fail":
                items.append(f"- **CI**: {ci['job']} is **failing**")

    return items


def _format_prelim_summary(prs: list[dict]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for pr in prs:
        for t in pr.get("rhel_tickets", []):
            if t.get("status", "").lower() == "closed":
                continue
            counts[_prelim_str(t.get("preliminary_testing"))] += 1

    lines = ["| Status | Count |", "|--------|-------|"]
    for status in ("Pass", "Requested", "Not Started", "Fail", "Not set"):
        if counts.get(status, 0) > 0:
            lines.append(f"| {status} | {counts[status]} |")
    return "\n".join(lines)


def _format_build_summary(prs: list[dict]) -> str:
    stream_data: dict[str, dict] = defaultdict(
        lambda: {"nvrs": set(), "tickets": []}
    )

    for pr in prs:
        for t in pr.get("rhel_tickets", []):
            if t.get("status", "").lower() == "closed":
                continue
            stream = t.get("stream", t.get("fix_version", "?"))
            build = t.get("fixed_in_build") or t.get("customfield_10578")
            if build and isinstance(build, str):
                stream_data[stream]["nvrs"].add(build)
            stream_data[stream]["tickets"].append(t.get("key", "?"))

    lines = ["| Stream | Build NVR | Tickets |", "|--------|-----------|---------|"]
    for stream in sorted(stream_data):
        d = stream_data[stream]
        nvrs = ", ".join(sorted(d["nvrs"])) if d["nvrs"] else "—"
        tickets = ", ".join(d["tickets"])
        lines.append(f"| {stream} | {nvrs} | {tickets} |")
    return "\n".join(lines)


def _format_ci_table(ci_jobs: list[dict]) -> str:
    lines = [
        "| Job | Last Result | Date | Link |",
        "|-----|------------|------|------|",
    ]
    for ci in ci_jobs:
        link = f"[Prow link]({ci['link']})" if ci.get("link") else "—"
        lines.append(
            f"| {ci.get('job', '?')} | {ci.get('result', '?')} "
            f"| {ci.get('date', '?')} | {link} |"
        )
    return "\n".join(lines)


def format_report(data: dict) -> str:
    """Format the complete markdown report from structured data.

    Supports both unified (TNF + TNA) and single-topology reports.
    Backward compatible with old schema (ocpbugs → ticket, flat ci_jobs).
    """
    all_prs = data.get("prs", [])
    tnf_prs = [p for p in all_prs if p.get("topology", "tnf") == "tnf"]
    tna_prs = [p for p in all_prs if p.get("topology") == "tna"]
    topologies = data.get("topologies", [])
    if not topologies:
        topologies = []
        if tnf_prs:
            topologies.append("tnf")
        if tna_prs:
            topologies.append("tna")
        if not topologies:
            topologies = ["tnf"]

    sections: list[str] = []

    sections.append("# Two-Node Ticket Tracker Report\n")
    sections.append(f"**Generated**: {data.get('generated_at', '?')}")
    topo_labels = [TOPOLOGY_CONFIG[t]["label"] for t in topologies
                   if t in TOPOLOGY_CONFIG]
    sections.append(f"**Topologies**: {', '.join(topo_labels)}\n")
    sections.append("---\n")

    # --- Summary tables ---
    if tnf_prs:
        sections.append("## TNF Summary (Two-Node Fencing)\n")
        sections.append("**Repository**: ClusterLabs/resource-agents\n")
        sections.append(_format_tnf_summary_table(tnf_prs))
        sections.append("\n")

    if tna_prs:
        sections.append("## TNA Summary (Two-Node Arbiter)\n")
        sections.append("**Repository**: openshift/cluster-etcd-operator\n")
        sections.append(_format_tna_summary_table(tna_prs))
        sections.append("\n")

    sections.append("---\n")

    # --- Detailed PR Status ---
    sections.append("## Detailed PR Status\n")

    if tnf_prs and tna_prs:
        sections.append("### TNF\n")
    if tnf_prs:
        for pr in tnf_prs:
            sections.append(_format_detailed_pr(pr))
            sections.append("\n---\n")

    if tnf_prs and tna_prs:
        sections.append("### TNA\n")
    if tna_prs:
        for pr in tna_prs:
            sections.append(_format_detailed_pr(pr))
            sections.append("\n---\n")

    # --- Action Items ---
    sections.append("## Action Items\n")
    if tnf_prs and tna_prs:
        tnf_items = _format_action_items_for_prs(tnf_prs, "tnf")
        tna_items = _format_action_items_for_prs(tna_prs, "tna")
        if tnf_items:
            sections.append("### TNF\n")
            sections.append("\n".join(tnf_items))
            sections.append("")
        if tna_items:
            sections.append("### TNA\n")
            sections.append("\n".join(tna_items))
            sections.append("")
        if not tnf_items and not tna_items:
            sections.append("- No action items — all clear!")
    else:
        topo = "tnf" if tnf_prs else "tna"
        items = _format_action_items_for_prs(tnf_prs or tna_prs, topo)
        sections.append("\n".join(items) if items else "- No action items — all clear!")
    sections.append("\n---\n")

    # --- Preliminary Testing + Build Summary (TNF only) ---
    if tnf_prs:
        sections.append("## Preliminary Testing Summary\n")
        sections.append(_format_prelim_summary(tnf_prs))
        sections.append("\n---\n")

        sections.append("## Build Summary\n")
        sections.append(_format_build_summary(tnf_prs))
        sections.append("\n---\n")

    # --- CI Status ---
    ci_jobs = data.get("ci_jobs", [])
    if isinstance(ci_jobs, dict):
        tnf_ci = ci_jobs.get("tnf", [])
        tna_ci = ci_jobs.get("tna", [])
        if tnf_ci or tna_ci:
            sections.append("## CI Status\n")
            if tnf_ci:
                if tna_ci:
                    sections.append("### TNF Jobs\n")
                sections.append(_format_ci_table(tnf_ci))
                sections.append("")
            if tna_ci:
                if tnf_ci:
                    sections.append("\n### TNA Jobs\n")
                sections.append(_format_ci_table(tna_ci))
                sections.append("")
            sections.append("\n---\n")
    elif ci_jobs:
        sections.append("## CI Status\n")
        sections.append(_format_ci_table(ci_jobs))
        sections.append("\n---\n")

    # --- Diff ---
    diff_result = data.get("diff")
    if diff_result:
        sections.append("## Changes Since Last Report\n")
        changes = diff_result.get("changes", [])
        if changes:
            sections.append("### Status Changes")
            sections.append("| Ticket | Field | Previous | Current |")
            sections.append("|--------|-------|----------|---------|")
            for c in changes:
                sections.append(
                    f"| {c['ticket']} | {c['field']} "
                    f"| {c['previous']} | {c['current']} |"
                )

        new_tickets = diff_result.get("new_tickets", [])
        if new_tickets:
            sections.append("\n### New Tickets")
            for t in new_tickets:
                sections.append(f"- {t['key']} — {t.get('summary', '?')}")

        resolved = diff_result.get("resolved_tickets", [])
        if resolved:
            sections.append("\n### Resolved Tickets")
            for t in resolved:
                sections.append(
                    f"- {t['key']} — {t.get('summary', '?')} (Closed)"
                )

        if not changes and not new_tickets and not resolved:
            sections.append("No changes since last report.")
        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# diff-data
# ---------------------------------------------------------------------------

def diff_data(previous: dict, current: dict) -> dict:
    """Compare two report data structures and return changes.

    Backward compatible: reads both old schema (ocpbugs) and new (ticket).

    Returns:
        {
            "changes":          [{"ticket", "field", "previous", "current"}],
            "new_tickets":      [{"key", "summary"}],
            "resolved_tickets": [{"key", "summary"}]
        }
    """
    def _extract_ticket_statuses(data: dict) -> dict[str, str]:
        result = {}
        for pr in data.get("prs", []):
            tk = pr.get("ticket") or pr.get("ocpbugs") or {}
            if tk.get("key"):
                result[tk["key"]] = tk.get("status", "?")
        return result

    def _extract_rhel_tickets(data: dict) -> dict[str, dict]:
        result = {}
        for pr in data.get("prs", []):
            for t in pr.get("rhel_tickets", []):
                result[t["key"]] = t
        return result

    prev_tickets = _extract_ticket_statuses(previous)
    curr_tickets = _extract_ticket_statuses(current)
    prev_rhel = _extract_rhel_tickets(previous)
    curr_rhel = _extract_rhel_tickets(current)

    changes: list[dict] = []
    new_tickets: list[dict] = []
    resolved: list[dict] = []

    # Ticket status changes (OCPBUGS + OCPEDGE)
    for key in curr_tickets:
        if key in prev_tickets and prev_tickets[key] != curr_tickets[key]:
            changes.append({
                "ticket": key, "field": "Status",
                "previous": prev_tickets[key], "current": curr_tickets[key],
            })

    # RHEL ticket changes
    tracked = [
        ("status", "Status"),
        ("preliminary_testing", "Preliminary Testing"),
        ("fixed_in_build", "Fixed in Build"),
    ]
    for key, curr_t in curr_rhel.items():
        if key not in prev_rhel:
            new_tickets.append({"key": key, "summary": curr_t.get("summary", "?")})
            continue
        prev_t = prev_rhel[key]
        for field, label in tracked:
            pv = (
                _prelim_str(prev_t.get(field))
                if field == "preliminary_testing"
                else str(prev_t.get(field, "?"))
            )
            cv = (
                _prelim_str(curr_t.get(field))
                if field == "preliminary_testing"
                else str(curr_t.get(field, "?"))
            )
            if pv != cv:
                changes.append({
                    "ticket": key, "field": label,
                    "previous": pv, "current": cv,
                })

    # resolved
    for key, prev_t in prev_rhel.items():
        if key not in curr_rhel:
            resolved.append({"key": key, "summary": prev_t.get("summary", "?")})
        elif (
            curr_rhel[key].get("status", "").lower() == "closed"
            and prev_t.get("status", "").lower() != "closed"
        ):
            resolved.append({
                "key": key, "summary": curr_rhel[key].get("summary", "?"),
            })

    return {
        "changes": changes,
        "new_tickets": new_tickets,
        "resolved_tickets": resolved,
    }


# ---------------------------------------------------------------------------
# history-filename / latest-data-file
# ---------------------------------------------------------------------------

def history_filename(history_dir: str) -> str:
    """Return the full path for the next report file."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = f"report-{today}"

    if not os.path.isdir(history_dir):
        return os.path.join(history_dir, f"{base}.md")

    existing = [f for f in os.listdir(history_dir) if f.startswith(base)]
    if not existing:
        return os.path.join(history_dir, f"{base}.md")

    max_suffix = 1
    for f in existing:
        if f == f"{base}.md":
            max_suffix = max(max_suffix, 2)
        else:
            m = re.match(rf"{re.escape(base)}-(\d+)\.", f)
            if m:
                max_suffix = max(max_suffix, int(m.group(1)) + 1)

    return os.path.join(history_dir, f"{base}-{max_suffix}.md")


def latest_data_file(history_dir: str) -> str | None:
    """Find the most recent .json data file in the history directory."""
    if not os.path.isdir(history_dir):
        return None
    candidates = [
        os.path.join(history_dir, f)
        for f in os.listdir(history_dir)
        if f.endswith(".json")
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_json_input() -> str:
    """Read JSON from CLI arg or stdin (pass '-' for stdin)."""
    if len(sys.argv) > 2 and sys.argv[2] != "-":
        return sys.argv[2]
    return sys.stdin.read()


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: ticket_tracker_helper.py <command> [args | -]\n"
            "Commands: parse-args, detect-streams, check-zstream-gaps,\n"
            "          validate-state, group-prs, format-report,\n"
            "          diff-data, history-filename, latest-data-file",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "parse-args":
        print(json.dumps(parse_args(" ".join(sys.argv[2:])), indent=2))

    elif cmd == "detect-streams":
        print(json.dumps(detect_streams(json.loads(_read_json_input())), indent=2))

    elif cmd == "check-zstream-gaps":
        print(json.dumps(check_zstream_gaps(json.loads(_read_json_input())), indent=2))

    elif cmd == "validate-state":
        result = validate_state(json.loads(_read_json_input()))
        print(json.dumps(result, indent=2))

    elif cmd == "group-prs":
        print(json.dumps(group_prs(json.loads(_read_json_input())), indent=2))

    elif cmd == "format-report":
        print(format_report(json.loads(_read_json_input())))

    elif cmd == "diff-data":
        if len(sys.argv) < 4:
            print("Usage: diff-data <previous.json> <current.json>", file=sys.stderr)
            sys.exit(1)
        with open(sys.argv[2], encoding="utf-8") as f:
            prev = json.load(f)
        with open(sys.argv[3], encoding="utf-8") as f:
            curr = json.load(f)
        print(json.dumps(diff_data(prev, curr), indent=2))

    elif cmd == "history-filename":
        if len(sys.argv) < 3:
            print("Usage: history-filename <history_dir>", file=sys.stderr)
            sys.exit(1)
        print(history_filename(sys.argv[2]))

    elif cmd == "latest-data-file":
        if len(sys.argv) < 3:
            print("Usage: latest-data-file <history_dir>", file=sys.stderr)
            sys.exit(1)
        result = latest_data_file(sys.argv[2])
        print(result if result else "null")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
