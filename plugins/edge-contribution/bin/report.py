#!/usr/bin/env python3
"""Aggregate collected activity into the report structures the renderers consume.

The collectors emit flat activity records (``{member, workstream, kind, ...}``)
wrapped in a ``{"activities": [...], "collector_meta": {...}}`` envelope. This
module turns those records into a binary contribution matrix, computes the team
metrics, and assembles the manager and IC report objects. It also tallies
unattributed items (``workstream is None``) so they can be reported separately
rather than silently dropped.

``collector_meta`` carries the data-quality counters that no record can express
because the underlying item was dropped before any record existed (personal-repo
PRs). Everything else is derived from the records themselves.

All functions are pure transforms over already-loaded data — no file or network
I/O — so the aggregation logic is fully unit-testable. The thin file-loading and
rendering wiring lives in ``main``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple
from hashlib import sha256

import _common
from _common import ACTIVITY_PROJECTS, UNATTRIBUTED_PARENT_EMPTY
from metrics import ContributionMatrix, compute_allocation_signals, compute_team_metrics
from render import (
    ACTIVITY_KINDS,
    DataQuality,
    ExecutiveSummary,
    ICReport,
    MemberLine,
    ManagerReport,
    WorkstreamActivity,
    WorkstreamLine,
    render_ic,
    render_manager,
    render_summary,
)
from workstream_map import _ALLOWED_ORGS, display_columns, workstream_acronyms

# Managers on the Eng/QE roster by role title who are not IC contributors.
# Filtering them from the team median prevents distortion of allocation signals.
# Stored as the hash256 of the member's jira username
EXCLUDED_MEMBERS = ("82e5282cc07f498ff0daf8352e7403e2f16b017dd17c3e589276e406480bb5be"
                    , "423987e3ab15b31a79a5ebcdecc50f4394886bc47ff70c69385f70adfc4f03a7")


def classify_source(kind: str) -> str:
    """Classify an activity kind as 'jira', 'github', or 'other'.

    Derives classification from membership in ``ACTIVITY_KINDS`` and the naming
    convention: GitHub activity kinds start with ``pr_``, Jira kinds do not.
    Unknown kinds (not in ``ACTIVITY_KINDS``) return ``"other"``.

    This avoids duplicating the kind literals into a new list in this module.
    """
    if kind.startswith("pr_"):
        return "github"
    elif kind in ACTIVITY_KINDS:
        return "jira"
    else:
        return "other"


def resolve_period_window(period_label: str) -> Optional[Tuple[str, str]]:
    """Resolve a period label into a date window tuple.

    - ``YYYYQn`` quarter labels (e.g., ``2026Q3``) are resolved through
      ``_common.quarter_to_window`` to get the actual calendar quarter dates.
    - ``<from>_<to>`` explicit date ranges (e.g., ``2026-01-15_2026-03-31``) are
      split on the underscore and returned as a tuple.
    - Any other format returns ``None`` rather than raising, so the header can
      omit the window line instead of guessing.
    """
    # Try to parse as a quarter label
    try:
        window = _common.quarter_to_window(period_label)
        return (window.start.isoformat(), window.end.isoformat())
    except ValueError:
        pass

    # Try to parse as an explicit date range
    if "_" in period_label:
        parts = period_label.split("_", 1)
        if len(parts) == 2:
            return (parts[0], parts[1])

    # Unrecognized format
    return None


def count_out_of_window_records(activities: List[dict], window: Optional[Tuple[str, str]]) -> int:
    """Count records whose timestamp falls outside the resolved window.

    Only meaningful when ``window`` is not None. When window is None, returns 0.
    Compares dates, not strings-with-times — a timestamp like "2026-07-01T00:00:00Z"
    is inside a window ending 2026-09-30. Records with a missing or unparseable
    timestamp must not raise and must not be counted as out-of-window.

    Args:
        activities: List of activity records, each potentially having a "ts" field.
        window: A tuple of (start_date, end_date) in ISO format (e.g., "2026-07-01").

    Returns:
        Count of records whose timestamp is before window start or after window end.
    """
    if window is None:
        return 0

    from datetime import datetime

    start_date, end_date = window
    count = 0

    for activity in activities:
        ts = activity.get("ts")
        if not ts:
            continue

        try:
            # Parse the ISO timestamp and extract the date part
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            record_date = dt.date().isoformat()

            # Compare dates: record is out-of-window if before start or after end
            if record_date < start_date or record_date > end_date:
                count += 1
        except (ValueError, AttributeError):
            # Malformed timestamp or not a string: don't count, don't raise
            continue

    return count


def prepare_roster(
    roster_entries: List[dict],
) -> Tuple[List[str], Dict[str, str], List[str]]:
    """Filter excluded members and build name translation mappings.

    Factors out the roster preparation logic so both the manager and summary
    views share one copy. Returns ``(excluded, jira_to_display, display_names)``.
    """

    excluded = [
        entry["jira_username"]
        for entry in roster_entries
        if sha256(entry["jira_username"].encode()).hexdigest() in EXCLUDED_MEMBERS
    ]
    roster_entries = [
        entry for entry in roster_entries
        if sha256(entry["jira_username"].encode()).hexdigest() not in EXCLUDED_MEMBERS
    ]

    # Build jira_username -> display_name mapping
    jira_to_display = {
        entry["jira_username"]: entry.get("name", entry["jira_username"])
        for entry in roster_entries
    }

    # Extract display names for the matrix
    display_names = [jira_to_display[entry["jira_username"]] for entry in roster_entries]

    return excluded, jira_to_display, display_names


def translate_activities_to_display_names(
    activities: List[dict], jira_to_display: Dict[str, str]
) -> List[dict]:
    """Translate activity member fields from jira_username to display name.

    This is used for the manager view to show real names instead of jira usernames.
    Activities are shallow-copied so the originals are not mutated.
    """
    result = []
    for activity in activities:
        translated = dict(activity)
        member = activity.get("member")
        if member:
            translated["member"] = jira_to_display.get(member, member)
        result.append(translated)
    return result


def build_contribution_matrix(
    activities: List[dict], members: List[str], workstreams: List[str]
) -> ContributionMatrix:
    """Build the binary matrix: a member touches a workstream with ≥1 activity.

    Also populates the counts matrix (member -> workstream -> count) for allocation
    signals. The workstreams list should be display_columns() (7 columns including
    SHARED) for the manager view, so allocation signals include cross-cutting work.
    The workstreams-touched count internally filters out SHARED to stay "N of 6".
    """
    workstream_set = set(workstreams)
    member_set = set(members)
    touched: Dict[str, set] = {member: set() for member in members}
    counts: Dict[str, Dict[str, int]] = {member: {} for member in members}
    for activity in activities:
        member = activity.get("member")
        workstream = activity.get("workstream")
        if member in member_set and workstream in workstream_set:
            touched[member].add(workstream)
            if workstream not in counts[member]:
                counts[member][workstream] = 0
            counts[member][workstream] += 1
    return ContributionMatrix(
        members=list(members), workstreams=list(workstreams), touched=touched, counts=counts
    )


def count_unattributed(activities: List[dict], member: Optional[str] = None) -> int:
    """Count activities that could not be mapped to a workstream.

    When ``member`` is given, count only that member's unattributed items.
    """
    return sum(
        1
        for activity in activities
        if activity.get("workstream") is None
        and (member is None or activity.get("member") == member)
    )


def build_workstream_activities(
    activities: List[dict], member: str, workstreams: List[str]
) -> List[WorkstreamActivity]:
    """Summarize one member's activity per touched workstream, counted by kind."""
    workstream_set = set(workstreams)
    counts_by_workstream: Dict[str, Dict[str, int]] = {}
    for activity in activities:
        if activity.get("member") != member:
            continue
        workstream = activity.get("workstream")
        if workstream not in workstream_set:
            continue
        kind = activity.get("kind", "")
        kind_counts = counts_by_workstream.setdefault(workstream, {})
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    return [
        WorkstreamActivity(workstream, counts_by_workstream[workstream])
        for workstream in workstreams
        if workstream in counts_by_workstream
    ]


def build_data_quality(
    activities: List[dict],
    excluded_members: List[str],
    collector_meta: Optional[Dict[str, int]] = None,
) -> DataQuality:
    """Tally data quality signals from activity records.

    Counts unattributed items (workstream is None) by category:
    - GitHub PRs: tallied per repo
    - Jira tickets: split by ``unattributed_reason`` into "no component and no
      parent epic" versus "the parent epic is component-less too"

    A Jira record written before ``unattributed_reason`` existed has no reason
    field; those fall into the no-component-no-parent bucket, which is where they
    were already being counted.

    ``collector_meta`` carries counters the records cannot express because the
    underlying item was dropped: ``excluded_personal_repo_prs`` (the total) and
    ``excluded_personal_repos`` (the same PRs broken down by repo).
    """
    meta = collector_meta or {}
    unattributed_by_repo: Dict[str, int] = {}
    tickets_no_component_no_parent = 0
    tickets_parent_also_empty = 0

    for activity in activities:
        if activity.get("workstream") is not None:
            continue  # Only count unattributed items

        repo = activity.get("repo")
        if repo:
            # GitHub PR with no workstream attribution
            unattributed_by_repo[repo] = unattributed_by_repo.get(repo, 0) + 1
        elif activity.get("unattributed_reason") == UNATTRIBUTED_PARENT_EMPTY:
            tickets_parent_also_empty += 1
        else:
            # Jira ticket with no component and nothing above it to fall back to
            tickets_no_component_no_parent += 1

    return DataQuality(
        unattributed_by_repo=unattributed_by_repo,
        tickets_no_component_no_parent=tickets_no_component_no_parent,
        tickets_parent_also_empty=tickets_parent_also_empty,
        excluded_personal_repo_prs=meta.get("excluded_personal_repo_prs", 0),
        excluded_personal_repos=dict(meta.get("excluded_personal_repos") or {}),
        excluded_members=list(excluded_members),
    )


def build_workstream_lines(
    matrix: ContributionMatrix, workstreams: List[str]
) -> List[WorkstreamLine]:
    """Build workstream lines sorted by volume descending.

    Args:
        matrix: The contribution matrix with counts.
        workstreams: List of workstreams to include.

    Returns:
        List of WorkstreamLine objects sorted by volume descending.
    """
    lines = []
    for ws in workstreams:
        volume = sum(matrix.counts.get(member, {}).get(ws, 0) for member in matrix.members)
        contributors = sum(
            1 for member in matrix.members if ws in matrix.touched.get(member, set())
        )

        # Find top contributor and their share
        if volume > 0:
            member_counts = [
                (member, matrix.counts.get(member, {}).get(ws, 0)) for member in matrix.members
            ]
            top_member, top_count = max(member_counts, key=lambda x: x[1])
            if top_count > 0:
                top_share = round(100 * top_count / volume)
                top_contributor = top_member
            else:
                top_share = 0
                top_contributor = None
        else:
            top_share = 0
            top_contributor = None

        lines.append(
            WorkstreamLine(
                workstream=ws,
                volume=volume,
                contributors=contributors,
                top_contributor=top_contributor,
                top_share=top_share,
            )
        )

    # Sort by volume descending
    lines.sort(key=lambda line: line.volume, reverse=True)
    return lines


def build_manager_report(
    activities: List[dict],
    members: List[str],
    workstreams: List[str],
    period_label: str,
    excluded_members: Optional[List[str]] = None,
    collector_meta: Optional[Dict[str, int]] = None,
) -> ManagerReport:
    """Assemble the manager heatmap + scores report."""
    matrix = build_contribution_matrix(activities, members, workstreams)
    signals = compute_allocation_signals(matrix)
    data_quality = build_data_quality(activities, excluded_members or [], collector_meta)
    return ManagerReport(
        period_label=period_label,
        metrics=compute_team_metrics(matrix),
        matrix=matrix,
        unattributed_count=count_unattributed(activities),
        signals=signals,
        data_quality=data_quality,
    )


def build_executive_summary(
    activities: List[dict],
    members: List[str],
    workstreams: List[str],
    period_label: str,
    excluded_members: Optional[List[str]] = None,
    collector_meta: Optional[Dict[str, int]] = None,
) -> ExecutiveSummary:
    """Assemble the executive summary report.

    Reuses ``build_manager_report`` for the matrix, metrics, signals, and data
    quality rather than recomputing them. Pulls static config from the actual
    constants so the summary describes real behavior, not a stale copy.
    """
    # Reuse the manager report to get matrix, metrics, signals, and data_quality
    manager_report = build_manager_report(
        activities, members, workstreams, period_label, excluded_members, collector_meta
    )

    # Count by source and kind
    counts_by_source_kind: Dict[str, Dict[str, int]] = {"jira": {}, "github": {}}
    for activity in activities:
        kind = activity.get("kind", "")
        source = classify_source(kind)
        if source in ("jira", "github"):
            if kind not in counts_by_source_kind[source]:
                counts_by_source_kind[source][kind] = 0
            counts_by_source_kind[source][kind] += 1

    # Count by attribution source, using empty string for None
    counts_by_attribution: Dict[str, int] = {}
    for activity in activities:
        attr_source = activity.get("attribution_source")
        key = "" if attr_source is None else attr_source
        counts_by_attribution[key] = counts_by_attribution.get(key, 0) + 1

    # Build per-member lines with top workstream and share
    per_member = []
    for member in members:
        total = sum(manager_report.matrix.counts.get(member, {}).values())
        touched = manager_report.metrics.workstreams_touched_by_member.get(member, 0)

        # Find top workstream and share
        member_counts = manager_report.matrix.counts.get(member, {})
        if member_counts and total > 0:
            top_ws, top_count = max(member_counts.items(), key=lambda x: x[1])
            top_share = round(100 * top_count / total)
            top_workstream = top_ws
        else:
            top_workstream = None
            top_share = 0

        per_member.append(
            MemberLine(
                member=member,
                total=total,
                workstreams_touched=touched,
                top_workstream=top_workstream,
                top_share=top_share,
            )
        )

    # Sort by total descending
    per_member.sort(key=lambda ml: ml.total, reverse=True)

    # Build per-workstream lines
    per_workstream = build_workstream_lines(manager_report.matrix, workstreams)

    # Resolve the period window
    window = resolve_period_window(period_label)

    # Count records whose timestamp falls outside the window
    out_of_window_count = count_out_of_window_records(activities, window)

    return ExecutiveSummary(
        period_label=period_label,
        window=window,
        metrics=manager_report.metrics,
        signals=manager_report.signals,
        data_quality=manager_report.data_quality,
        counts_by_source_kind=counts_by_source_kind,
        counts_by_attribution=counts_by_attribution,
        per_member=per_member,
        per_workstream=per_workstream,
        total_records=len(activities),
        jira_projects=ACTIVITY_PROJECTS,
        ocpstrat_project="OCPSTRAT",
        allowed_org_count=len(_ALLOWED_ORGS),
        out_of_window_records=out_of_window_count,
    )


def build_ic_report(
    activities: List[dict], member: str, workstreams: List[str], period_label: str
) -> ICReport:
    """Assemble the single-member workstream-coverage report."""
    matrix = build_contribution_matrix(activities, [member], workstreams)
    touched = len(matrix.touched.get(member, set()))
    return ICReport(
        period_label=period_label,
        member=member,
        workstreams_touched=touched,
        total_workstreams=len(workstreams),
        activity=build_workstream_activities(activities, member, workstreams),
        unattributed_count=count_unattributed(activities, member),
    )


# --- CLI --------------------------------------------------------------------


def should_use_color(
    fmt: str, isatty: bool, env_no_color: Optional[str], no_color_flag: bool
) -> bool:
    """Determine whether to use ANSI color codes.

    Color is enabled only when ALL of the following are true:
    - format is "text"
    - NO_COLOR environment variable is unset or empty
    - stdout is a TTY
    - --no-color flag is not set

    The --no-color flag always wins (forces color off).
    """
    if no_color_flag:
        return False
    if fmt != "text":
        return False
    if env_no_color:
        return False
    if not isatty:
        return False
    return True


def parse_activity_file(payload) -> tuple[List[dict], Dict[str, int]]:
    """Split one loaded activity file into its records and its collector counters.

    Collectors write ``{"activities": [...], "collector_meta": {...}}``. A bare
    list is the pre-envelope format and still loads, just with no counters.
    """
    if isinstance(payload, list):
        return payload, {}
    return payload.get("activities", []), payload.get("collector_meta", {}) or {}


def _merge_counter(accumulated, value):
    """Combine one collector counter across two activity files.

    A counter is either a plain total or a ``{label: count}`` breakdown such as
    ``excluded_personal_repos``. Totals add; breakdowns merge label by label.
    Treating a breakdown like a total would raise, and skipping it would throw
    away the repo names the data-quality block needs.
    """
    if isinstance(value, dict):
        merged = dict(accumulated or {})
        for label, count in value.items():
            merged[label] = merged.get(label, 0) + count
        return merged
    return (accumulated or 0) + value


def _load_activities(paths: List[str]) -> tuple[List[dict], Dict[str, object]]:
    """Load every activity file, concatenating records and merging counters."""
    activities: List[dict] = []
    collector_meta: Dict[str, object] = {}
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            records, meta = parse_activity_file(json.load(handle))
        activities.extend(records)
        for key, value in meta.items():
            collector_meta[key] = _merge_counter(collector_meta.get(key), value)
    return activities, collector_meta


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Render a contribution report.")
    parser.add_argument("--view", required=True, choices=("manager", "ic", "summary"))
    parser.add_argument(
        "--activity",
        action="append",
        required=True,
        help="an activity JSON file (repeat for jira + github)",
    )
    parser.add_argument("--members-file", help="roster.json (required for the manager view)")
    parser.add_argument("--member", help="Jira username (required for the ic view)")
    parser.add_argument("--period", required=True, help="reporting period label, e.g. 2026Q2")
    parser.add_argument("--format", default="text", choices=("text", "markdown"))
    parser.add_argument("--include-all", action="store_true", help="include excluded members")
    parser.add_argument("--ascii", action="store_true", help="use ASCII-only glyphs")
    parser.add_argument("--no-color", action="store_true", help="disable color output")
    parser.add_argument(
        "--show-sources",
        action="store_true",
        help="add the sections describing what was counted, how each item was "
        "attributed, and what was excluded (summary view only)",
    )
    args = parser.parse_args(argv)

    # Guard: markdown format only works with summary view
    if args.format == "markdown" and args.view in ("manager", "ic"):
        parser.error("--format markdown is only supported with --view summary")

    activities, collector_meta = _load_activities(args.activity)

    use_color = should_use_color(
        args.format,
        sys.stdout.isatty(),
        os.environ.get("NO_COLOR"),
        args.no_color,
    )

    if args.view == "manager":
        if not args.members_file:
            parser.error("--members-file is required for --view manager")
        with open(args.members_file, encoding="utf-8") as handle:
            roster_entries = json.load(handle)

        excluded, jira_to_display, display_names = prepare_roster(roster_entries)

        # Translate activities to use display names
        translated_activities = translate_activities_to_display_names(activities, jira_to_display)

        # Use display_columns() to include SHARED for allocation signals
        workstreams = display_columns()

        rendered = render_manager(
            build_manager_report(
                translated_activities,
                display_names,
                workstreams,
                args.period,
                excluded,
                collector_meta,
            ),
            args.format,
            color=use_color,
            ascii_only=args.ascii,
        )
    elif args.view == "summary":
        if not args.members_file:
            parser.error("--members-file is required for --view summary")
        with open(args.members_file, encoding="utf-8") as handle:
            roster_entries = json.load(handle)

        excluded, jira_to_display, display_names = prepare_roster(roster_entries)

        # Translate activities to use display names
        translated_activities = translate_activities_to_display_names(activities, jira_to_display)

        # Use display_columns() to include SHARED for allocation signals
        workstreams = display_columns()

        rendered = render_summary(
            build_executive_summary(
                translated_activities,
                display_names,
                workstreams,
                args.period,
                excluded,
                collector_meta,
            ),
            args.format,
            show_sources=args.show_sources,
        )
    else:
        if not args.member:
            parser.error("--member is required for --view ic")
        # IC view uses the 6 canonical workstreams, not display_columns()
        workstreams = workstream_acronyms()
        rendered = render_ic(
            build_ic_report(activities, args.member, workstreams, args.period),
            args.format,
        )

    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
