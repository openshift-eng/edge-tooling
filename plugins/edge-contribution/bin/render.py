#!/usr/bin/env python3
"""Render cross-workstream contribution reports.

Three report shapes are supported:

* **Manager** — a people x workstream heatmap showing contribution intensity via
  a grey-to-green gradient, plus allocation signals (over/under/narrow flags).
  Rendered as plain text for the terminal: the grid, scale legend, and flag
  legend. The team-level means, data quality notes, and other analytics are in
  the Summary view.
* **IC** — one member's workstreams-touched count ("N of M") and a per-workstream
  activity breakdown, rendered as plain text.
* **Summary** — an executive summary reporting what was counted, how each item
  was attributed, team-level means, per-member totals and workstreams touched,
  and data quality notes. Formats: ``text`` (default) and ``markdown``
  (for Slack/docs).

Rendering is pure: it turns already-computed data structures into a string, with
no I/O, so every format is unit-testable.

The gradient uses log-spaced bands on a single global maximum so brightness is
comparable across the entire grid. Allocation signals are relative to the team
median and highlight imbalance, not capacity targets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil, log1p
from typing import Dict, List, Optional, Tuple

from metrics import AllocationSignals, ContributionMatrix, TeamMetrics, compute_allocation_signals

# Canonical order of activity kinds for IC output columns.
# No "comment" kind: collect_jira does not gather comments, because Jira Cloud
# omits comment author emails and the query could never match a member.
ACTIVITY_KINDS: List[str] = [
    "assignee",
    "qa",
    "ocpstrat_role",
    "pr_authored",
    "pr_reviewed",
]

_VALID_FORMATS = ("text",)
_SUMMARY_FORMATS = ("text", "markdown")

# Gradient glyphs and colors for contribution intensity heatmap.
# Band 0 = no activity, bands 1..4 = log-spaced buckets of increasing volume.
_BAND_GLYPHS = ("·", "░", "▒", "▓", "█")
_BAND_ASCII = (".", ":", "*", "+", "#")
_BAND_COLORS = (59, 65, 71, 77, 40)  # xterm-256: grey #5f5f5f -> green #00d700


_TOP_REPO_LIMIT = 8
_TWO_NODE_TOOLBOX_NOTE = "(mixed arbiter/fencing; not mapped by design)"


def _top_repos(
    tally: Dict[str, int], limit: int = _TOP_REPO_LIMIT
) -> Tuple[List[Tuple[str, int]], int, int]:
    """Split a repo tally into the biggest ``limit`` entries and a remainder.

    Returns ``(top, remaining_repo_count, remaining_pr_count)``. Every repo list
    in the data-quality block truncates the same way, so they share this.
    """
    items = sorted(tally.items(), key=lambda pair: pair[1], reverse=True)
    rest = items[limit:]
    return items[:limit], len(rest), sum(count for _repo, count in rest)


def _repo_note(repo: str) -> str:
    """Return the trailing explanation for a repo that is unmapped on purpose."""
    if repo == "openshift-eng/two-node-toolbox":
        return f"  {_TWO_NODE_TOOLBOX_NOTE}"
    return ""


@dataclass(frozen=True)
class DataQuality:
    """Data quality notes for a manager report."""

    unattributed_by_repo: Dict[str, int] = field(default_factory=dict)
    tickets_no_component_no_parent: int = 0
    tickets_parent_also_empty: int = 0
    excluded_personal_repo_prs: int = 0
    # Which repos those excluded PRs lived in. The org allowlist behind the
    # exclusion is hand-kept, so naming the repos is how a legitimate org that
    # is missing from it becomes visible instead of vanishing into the total.
    excluded_personal_repos: Dict[str, int] = field(default_factory=dict)
    excluded_members: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorkstreamLine:
    """Per-workstream volume and top contributor for an executive summary."""

    workstream: str
    volume: int
    contributors: int
    top_contributor: Optional[str]
    top_share: int  # percent, rounded int


@dataclass(frozen=True)
class MemberLine:
    """Per-member totals and workstreams-touched count for an executive summary."""

    member: str
    total: int
    workstreams_touched: int
    top_workstream: Optional[str]
    top_share: int  # percent, rounded int


@dataclass(frozen=True)
class ExecutiveSummary:
    """Executive summary of cross-workstream contribution for a reporting period.

    Reports what was counted, how each item was attributed, team-level means,
    per-member totals and workstreams touched, and data quality notes. This is
    the manager-facing narrative view that complements the at-a-glance heatmap
    grid.
    """

    period_label: str
    window: Optional[Tuple[str, str]]  # ("2026-07-01", "2026-09-30")
    metrics: TeamMetrics
    signals: AllocationSignals
    data_quality: DataQuality
    counts_by_source_kind: Dict[str, Dict[str, int]]  # "jira"/"github" -> kind -> n
    counts_by_attribution: Dict[str, int]  # source -> n, "" for None
    per_member: List[MemberLine]  # sorted total desc
    per_workstream: List[WorkstreamLine]  # sorted volume desc
    total_records: int
    jira_projects: Tuple[str, ...]
    ocpstrat_project: str
    allowed_org_count: int
    out_of_window_records: int = 0


@dataclass(frozen=True)
class WorkstreamActivity:
    """One workstream's activity for a single member, counted by kind."""

    workstream: str
    counts: Dict[str, int] = field(default_factory=dict)

    def total(self) -> int:
        return sum(self.counts.values())


@dataclass(frozen=True)
class ICReport:
    """Personal workstream-coverage report for a single member."""

    period_label: str
    member: str
    workstreams_touched: int
    total_workstreams: int
    activity: List[WorkstreamActivity] = field(default_factory=list)
    unattributed_count: int = 0


@dataclass(frozen=True)
class ManagerReport:
    """Team heatmap report across the full roster."""

    period_label: str
    metrics: TeamMetrics
    matrix: ContributionMatrix
    unattributed_count: int = 0
    signals: Optional[AllocationSignals] = None
    data_quality: Optional[DataQuality] = None


def _require_known_format(output_format: str) -> None:
    if output_format not in _VALID_FORMATS:
        raise ValueError(
            f"unknown output format {output_format!r}; expected one of {_VALID_FORMATS}"
        )


def _require_known_summary_format(output_format: str) -> None:
    if output_format not in _SUMMARY_FORMATS:
        raise ValueError(
            f"unknown summary format {output_format!r}; expected one of {_SUMMARY_FORMATS}"
        )


def shade_band(count: int, grid_max: int) -> int:
    """Return a 0..4 shade band index for a contribution count on a log scale.

    Band 0 = no activity (count == 0); bands 1..4 = log-spaced buckets of
    increasing volume relative to ``grid_max``. The distribution is log-based
    so the full range (1 to grid_max) is split into four perceptually-equal
    steps rather than collapsing low counts into a single band.

    Args:
        count: Number of contributions in a single cell.
        grid_max: Maximum count across the entire grid (for normalization).

    Returns:
        Shade band index 0..4 inclusive. Returns 0 when count <= 0 or grid_max <= 0,
        and clamps to 4 when count exceeds grid_max.
    """
    if count <= 0 or grid_max <= 0:
        return 0
    # log1p(count) / log1p(grid_max) ranges from ~0 to 1, scaling by 4 spreads
    # it across bands 1..4. Ceil ensures count=1 lands in band 1, not 0.
    # Max(1, ...) guards against count < 1 after the log transform, and
    # min(..., 4) clamps counts above grid_max to the top band.
    return max(1, min(4, ceil(log1p(count) / log1p(grid_max) * 4)))


# --- Manager report ---------------------------------------------------------


def render_manager(
    report: ManagerReport, output_format: str, *, color: bool = True, ascii_only: bool = False
) -> str:
    """Render the manager heatmap in text format.

    Args:
        report: The manager report to render.
        output_format: Must be "text".
        color: Whether to include ANSI color codes.
        ascii_only: Whether to use strict ASCII glyphs instead of Unicode.

    Returns:
        Rendered report as a string.
    """
    _require_known_format(output_format)
    return _manager_text(report, color=color, ascii_only=ascii_only)


def _cell_is_filled(report: ManagerReport, member: str, workstream: str) -> bool:
    return workstream in report.matrix.touched.get(member, set())


def _cell_count(report: ManagerReport, member: str, workstream: str) -> int:
    """Return the contribution count for a member/workstream cell."""
    return report.matrix.counts.get(member, {}).get(workstream, 0)


def _colorize_glyph(glyph: str, band: int, color: bool) -> str:
    """Wrap a glyph in ANSI color codes if color is enabled."""
    if not color:
        return glyph
    color_code = _BAND_COLORS[band]
    return f"\x1b[38;5;{color_code}m{glyph}\x1b[0m"


def _compute_scale_legend(grid_max: int) -> List[tuple[int, str]]:
    """Compute the scale legend by inverting shade_band for each band 1..4.

    Returns a list of (band_index, label) tuples describing the count range for
    each non-zero band. The label is "N" for a single-value band or "N-M" for
    a range. Band 0 (none) is always "none" and is not returned here.
    """
    if grid_max <= 0:
        return []

    # Find the boundary counts for each band by testing shade_band.
    # We want the first count that lands in each band, and the last count
    # before transitioning to the next band.
    legend: List[tuple[int, str]] = []
    for band in range(1, 5):
        # Find the first count that produces this band.
        first = None
        for count in range(1, grid_max + 1):
            if shade_band(count, grid_max) == band:
                first = count
                break
        if first is None:
            continue

        # Find the last count that produces this band.
        last = first
        for count in range(first + 1, grid_max + 1):
            if shade_band(count, grid_max) == band:
                last = count
            else:
                break

        if first == last:
            legend.append((band, str(first)))
        else:
            legend.append((band, f"{first}-{last}"))

    return legend


def _manager_text(report: ManagerReport, *, color: bool = True, ascii_only: bool = False) -> str:
    from workstream_map import SHARED_COLUMN, display_columns

    metrics_data = report.metrics
    signals = (
        report.signals if report.signals is not None else compute_allocation_signals(report.matrix)
    )

    # Choose glyph set based on ascii_only.
    glyphs = _BAND_ASCII if ascii_only else _BAND_GLYPHS
    em_dash = "-" if ascii_only else "—"
    box_vert = "|" if ascii_only else "│"
    flag_over = "^" if ascii_only else "▲"
    flag_under = "v" if ascii_only else "▼"

    # Determine column order: six workstreams + SHARED.
    display_cols = display_columns()
    six_workstreams = [ws for ws in display_cols if ws != SHARED_COLUMN]
    shared_present = SHARED_COLUMN in display_cols

    # Sort members by total descending.
    members_sorted = sorted(
        metrics_data.members, key=lambda m: signals.total_by_member.get(m, 0), reverse=True
    )

    # Compute column widths.
    name_width = max((len(m) for m in members_sorted), default=10)
    ws_widths = {ws: max(len(ws), 2) for ws in display_cols}
    shared_width = ws_widths.get(SHARED_COLUMN, 6)
    total_width = max(5, len(str(max(signals.total_by_member.values(), default=0))))

    # Build title line.
    active_count = metrics_data.active_member_count
    total_count = metrics_data.total_member_count
    lines = [
        f"Cross-Workstream Allocation {em_dash} {report.period_label}            "
        f"{active_count} of {total_count} members active",
        "",
    ]

    # Build header row.
    header_parts = [f"{'':<{name_width}}"]
    for ws in six_workstreams:
        header_parts.append(ws.center(ws_widths[ws]))
    if shared_present:
        header_parts.append(box_vert)
        header_parts.append(SHARED_COLUMN.center(shared_width))
    header_parts.append(box_vert)
    header_parts.append("TOTAL".center(total_width))
    lines.append("  ".join(header_parts))

    # Build member rows.
    for member in members_sorted:
        row_parts = [f"{member:<{name_width}}"]
        for ws in six_workstreams:
            count = _cell_count(report, member, ws)
            band = shade_band(count, signals.grid_max)
            glyph = glyphs[band]
            # Double the glyph to make it more visible.
            cell = (glyph * 2).center(ws_widths[ws])
            row_parts.append(_colorize_glyph(cell, band, color))

        if shared_present:
            shared_count = _cell_count(report, member, SHARED_COLUMN)
            shared_band = shade_band(shared_count, signals.grid_max)
            shared_glyph = glyphs[shared_band]
            shared_cell = (shared_glyph * 2).center(shared_width)
            row_parts.append(box_vert)
            row_parts.append(_colorize_glyph(shared_cell, shared_band, color))

        total = signals.total_by_member.get(member, 0)
        row_parts.append(box_vert)
        row_parts.append(str(total).rjust(total_width))

        # Add flags.
        flags = []
        if member in signals.over_allocated:
            flags.append(flag_over)
        if member in signals.under_allocated:
            flags.append(flag_under)
        if member in signals.narrow:
            flags.append("narrow")
        if flags:
            row_parts.append(" " + " ".join(flags))

        lines.append("  ".join(row_parts))

    # Build footer separator.
    sep_parts = [f"{'─' * name_width if not ascii_only else '-' * name_width}"]
    for ws in six_workstreams:
        sep_parts.append("─" * ws_widths[ws] if not ascii_only else "-" * ws_widths[ws])
    if shared_present:
        sep_parts.append("┼" if not ascii_only else "+")
        sep_parts.append("─" * shared_width if not ascii_only else "-" * shared_width)
    sep_parts.append("┼" if not ascii_only else "+")
    sep_parts.append("─" * total_width if not ascii_only else "-" * total_width)
    lines.append("  ".join(sep_parts))

    # Build TOTAL footer row.
    total_parts = [f"{'TOTAL':<{name_width}}"]
    for ws in six_workstreams:
        ws_total = signals.total_by_workstream.get(ws, 0)
        total_parts.append(str(ws_total).center(ws_widths[ws]))
    if shared_present:
        shared_total = signals.total_by_workstream.get(SHARED_COLUMN, 0)
        total_parts.append(box_vert)
        total_parts.append(str(shared_total).center(shared_width))
    total_parts.append(box_vert)
    lines.append("  ".join(total_parts))

    # Build PEOPLE footer row.
    people_parts = [f"{'PEOPLE':<{name_width}}"]
    for ws in six_workstreams:
        people_count = signals.contributors_by_workstream.get(ws, 0)
        people_parts.append(str(people_count).center(ws_widths[ws]))
    if shared_present:
        shared_people = signals.contributors_by_workstream.get(SHARED_COLUMN, 0)
        people_parts.append(box_vert)
        people_parts.append(str(shared_people).center(shared_width))
    people_parts.append(box_vert)
    lines.append("  ".join(people_parts))

    # Build scale legend.
    lines.append("")
    scale_legend = _compute_scale_legend(signals.grid_max)
    legend_parts = [f"  scale (log)  {glyphs[0]}  none"]
    for band, label in scale_legend:
        legend_parts.append(f"{glyphs[band]} {label}")
    lines.append("   ".join(legend_parts))

    # Build flags legend.
    flags_legend = (
        f"  flags        {flag_over} >2x team median   "
        f"{flag_under} <0.5x team median   "
        f"narrow = >70% in one workstream"
    )
    lines.append(flags_legend)

    # Text output stops at the flags legend, on purpose.
    #
    # The team-level means and the data-quality block used to be
    # appended here. They restated what the grid's TOTAL and PEOPLE rows
    # already show, and pushed the grid off the top of the terminal. The text
    # view is the at-a-glance one; for analytics, use --view summary --show-sources.
    # Pinned by TestManagerTextIsGridOnly in test_render.py.
    return "\n".join(lines) + "\n"


# --- IC report --------------------------------------------------------------


def render_ic(report: ICReport, output_format: str) -> str:
    """Render a single member's workstream-coverage report in text format.

    Args:
        report: The IC report to render.
        output_format: Must be "text".

    Returns:
        Rendered report as a string.
    """
    _require_known_format(output_format)
    return _ic_text(report)


def _ic_text(report: ICReport) -> str:
    lines = [
        f"Personal Workstream Coverage — {report.period_label}",
        f"Member: {report.member}",
        f"Workstreams touched: {report.workstreams_touched} of {report.total_workstreams}",
        "",
    ]
    if not report.activity:
        lines.append("No attributed activity in this period.")
    for activity in report.activity:
        detail = ", ".join(
            f"{kind}={activity.counts[kind]}"
            for kind in ACTIVITY_KINDS
            if activity.counts.get(kind)
        )
        lines.append(f"  {activity.workstream}: {detail or 'activity present'}")
    if report.unattributed_count:
        lines.append("")
        lines.append(f"Note: {report.unattributed_count} unattributed items not shown.")
    return "\n".join(lines) + "\n"


# --- Summary report ---------------------------------------------------------


def _compute_attribution_percentages(summary: ExecutiveSummary) -> Dict[str, int]:
    """Compute percentages for attribution sources, shared by text and markdown.

    Returns a dict of source -> percentage (as integer 0-100). Percentages are
    computed against total_records to avoid disagreement between formats. An
    empty summary returns an empty dict without raising ZeroDivisionError.
    """
    if summary.total_records == 0:
        return {}
    percentages = {}
    for source, count in summary.counts_by_attribution.items():
        percentages[source] = round(100 * count / summary.total_records)
    return percentages


def _summary_header_text(summary: ExecutiveSummary) -> List[str]:
    """Render the title and window lines."""
    lines = []
    lines.append(f"OCP-Edge Cross-Workstream Contribution — {summary.period_label}")
    if summary.window:
        from_date, to_date = summary.window
        included = summary.metrics.total_member_count
        excluded = len(summary.data_quality.excluded_members)
        full = included + excluded
        if excluded > 0:
            roster_line = f"Window {from_date} .. {to_date}        Roster {included} of {full} ({excluded} excluded)"
        else:
            roster_line = f"Window {from_date} .. {to_date}        Roster {included} of {full}"
        lines.append(roster_line)
    return lines


def _summary_team_text(summary: ExecutiveSummary) -> List[str]:
    """Render the TEAM section."""
    from workstream_map import workstream_acronyms

    lines = []
    lines.append("TEAM")

    # Total activity records across the roster (not the same as total_touches,
    # which counts member x workstream pairs rather than records).
    total_activity = sum(ml.total for ml in summary.per_member)
    median = int(summary.signals.team_median_total)
    lines.append(f"  {total_activity} contributions        median {median} per person")

    people_per_ws = summary.metrics.mean_people_per_workstream
    num_workstreams = len(workstream_acronyms())
    lines.append(f"  {people_per_ws:.1f} people per workstream (mean of {num_workstreams})")

    ws_per_person = summary.metrics.mean_workstreams_per_person
    active_count = summary.metrics.active_member_count
    lines.append(f"   {ws_per_person:.1f} workstreams per person ({active_count} active)")

    return lines


def _summary_workstreams_text(summary: ExecutiveSummary) -> List[str]:
    """Render the WORKSTREAMS table."""
    lines = []
    lines.append("")
    lines.append("WORKSTREAMS                     volume   people   largest contributor")

    for ws_line in summary.per_workstream:
        volume_str = f"{ws_line.volume:>6d}"
        people_str = f"{ws_line.contributors:>6d}"

        if ws_line.top_contributor:
            contrib_str = f"{ws_line.top_contributor:28s}  {ws_line.top_share:3d}%"
        else:
            contrib_str = ""

        lines.append(
            f"  {ws_line.workstream:30s}  {volume_str:>6s}  {people_str:>6s}   {contrib_str}"
        )

    return lines


def _summary_people_text(summary: ExecutiveSummary) -> List[str]:
    """Render the PEOPLE table."""
    lines = []
    lines.append("")
    lines.append("PEOPLE                           total  touched   largest workstream")

    for member_line in summary.per_member:
        total_str = f"{member_line.total:>5d}"
        touched_str = f"{member_line.workstreams_touched} of 6"

        if member_line.top_workstream:
            ws_str = f"{member_line.top_workstream:8s}  {member_line.top_share:3d}%"
        else:
            ws_str = ""

        lines.append(f"  {member_line.member:32s}  {total_str:>5s}   {touched_str:7s}   {ws_str}")

    return lines


def _summary_data_health_text(summary: ExecutiveSummary) -> List[str]:
    """Render the DATA HEALTH section."""
    lines = []
    lines.append("")
    lines.append("DATA HEALTH")

    # Count unattributed items
    unattributed_total = summary.counts_by_attribution.get("", 0)
    total_records = summary.total_records
    if total_records > 0:
        pct = round(100 * unattributed_total / total_records)
    else:
        pct = 0

    lines.append(
        f"  {unattributed_total} of {total_records} records ({pct}%) could not be placed in a workstream."
    )

    # Break down by category
    jira_no_comp = summary.data_quality.tickets_no_component_no_parent
    jira_parent_empty = summary.data_quality.tickets_parent_also_empty
    pr_count = sum(summary.data_quality.unattributed_by_repo.values())

    lines.append(
        f"    {jira_no_comp + jira_parent_empty} Jira tickets   {jira_no_comp} missing a component, {jira_parent_empty} whose epic is also empty"
    )
    lines.append(f"    {pr_count} PRs            repos not mapped to a workstream")

    return lines


def _summary_what_counted_text(summary: ExecutiveSummary) -> List[str]:
    """Render the WHAT WAS COUNTED section (--show-sources only)."""
    lines = []
    lines.append("")
    lines.append(f"WHAT WAS COUNTED  {summary.total_records} records")

    jira_counts = summary.counts_by_source_kind.get("jira", {})
    github_counts = summary.counts_by_source_kind.get("github", {})

    if jira_counts:
        jira_total = sum(jira_counts.values())
        jira_detail = " · ".join(
            f"{kind} {jira_counts[kind]}" for kind in ACTIVITY_KINDS if kind in jira_counts
        )
        lines.append(f"  Jira     {jira_total:4d}   {jira_detail}")
        jira_projects_str = ", ".join(summary.jira_projects)
        lines.append(f"                 projects {jira_projects_str} (assignee + QA contact)")
        if summary.ocpstrat_project:
            lines.append(f"                 plus {summary.ocpstrat_project} (SME / assignee roles)")
        lines.append("                 comments are not collected — Jira Cloud hides author emails")

    if github_counts:
        github_total = sum(github_counts.values())
        github_detail = " · ".join(
            f"{kind} {github_counts[kind]}" for kind in ACTIVITY_KINDS if kind in github_counts
        )
        lines.append(f"  GitHub  {github_total:5d}   {github_detail}")
        lines.append(
            f"                 any repo whose owner is in the org allowlist ({summary.allowed_org_count} orgs)"
        )
        lines.append("                 authored = PR created in the window")
        lines.append(
            "                 reviewed = PR updated in the window (GitHub cannot search by review date)"
        )
        lines.append("                 every record is timestamped with the PR's creation date")
        if summary.out_of_window_records > 0:
            lines.append(
                f"                 {summary.out_of_window_records} records carry a date before the window but are still counted"
            )
            lines.append("                 → do not filter activities.csv by the ts column")

    return lines


def _summary_how_attributed_text(summary: ExecutiveSummary) -> List[str]:
    """Render the HOW EACH ITEM WAS ATTRIBUTED section (--show-sources only)."""
    lines = []
    lines.append("")
    lines.append("HOW EACH ITEM WAS ATTRIBUTED   first hit wins")

    percentages = _compute_attribution_percentages(summary)
    named_sources = [
        (source, count) for source, count in summary.counts_by_attribution.items() if source
    ]
    named_sources.sort(key=lambda pair: pair[1], reverse=True)
    unattributed_count = summary.counts_by_attribution.get("", 0)

    for source, count in named_sources:
        pct = percentages.get(source, 0)
        desc = _attribution_description(source)
        if source in ("component", "repo", "project"):
            article = "the "
        elif source == "shared":
            article = "a "
        else:
            article = ""
        lines.append(f"  {source:12s} {count:4d} {pct:3d}%   {article}{desc}")

    if unattributed_count > 0:
        pct = percentages.get("", 0)
        lines.append(
            f"  unattributed {unattributed_count:4d} {pct:3d}%   none of the above matched — see EXCLUDED"
        )

    lines.append("")
    lines.append("  A PR that names a Jira key is resolved through the Jira chain, so it is")
    lines.append('  reported as component / parent / project, not as a separate "jira key" rule.')

    return lines


def _summary_what_excluded_text(summary: ExecutiveSummary) -> List[str]:
    """Render the WHAT WAS EXCLUDED section (--show-sources only)."""
    lines = []
    lines.append("")
    lines.append("WHAT WAS EXCLUDED")

    # Excluded members
    if summary.data_quality.excluded_members:
        count = len(summary.data_quality.excluded_members)
        members_str = ", ".join(summary.data_quality.excluded_members)
        lines.append(f"  {count} roster members    {members_str}")
        lines.append("                      managers by role title, not IC contributors.")
        lines.append("                      --include-all puts them back.")

    # Excluded PRs
    if summary.data_quality.excluded_personal_repo_prs > 0:
        lines.append(
            f"  {summary.data_quality.excluded_personal_repo_prs} PRs              personal-namespace repos, dropped before counting"
        )
        if summary.data_quality.excluded_personal_repos:
            top_repos, more_repos, more_prs = _top_repos(
                summary.data_quality.excluded_personal_repos
            )
            repo_lines = []
            for repo, count in top_repos:
                repo_lines.append(f"{repo} {count}")
            lines.append(f"                      {' · '.join(repo_lines)}")
            if more_repos:
                lines.append(f"                      and {more_repos} more repos ({more_prs} PRs)")
        lines.append(
            "                      The org allowlist is hand-kept — a real org missing from"
        )
        lines.append("                      it lands in this list looking like a side project.")

    # Unattributed items
    unattributed_total = summary.counts_by_attribution.get("", 0)
    if unattributed_total > 0:
        lines.append(
            f"  {unattributed_total} items           could not be attributed to any workstream"
        )

        # Unattributed PRs by repo
        if summary.data_quality.unattributed_by_repo:
            pr_count = sum(summary.data_quality.unattributed_by_repo.values())
            lines.append(f"                      {pr_count:3d}  PRs, by repo:")
            top_repos, more_repos, more_prs = _top_repos(summary.data_quality.unattributed_by_repo)
            for repo, count in top_repos:
                note = _repo_note(repo)
                lines.append(f"                             {repo} {count}{note}")
            if more_repos:
                lines.append(
                    f"                             and {more_repos} more repos ({more_prs} PRs)"
                )

        # Jira tickets with no component and no parent
        if summary.data_quality.tickets_no_component_no_parent > 0:
            count = summary.data_quality.tickets_no_component_no_parent
            lines.append(
                f"                      {count:3d}  Jira tickets with no component and no parent epic"
            )
            lines.append("                             → fix on the ticket")

        # Jira tickets whose parent epic is also empty
        if summary.data_quality.tickets_parent_also_empty > 0:
            count = summary.data_quality.tickets_parent_also_empty
            lines.append(
                f"                       {count:2d}  Jira tickets whose parent epic is also empty"
            )
            lines.append("                             → fix on the epic")

    return lines


def _summary_text(summary: ExecutiveSummary, show_sources: bool = False) -> str:
    """Render executive summary in plain text format."""
    lines = []

    # Header
    lines.extend(_summary_header_text(summary))
    lines.append("")

    # TEAM section
    lines.extend(_summary_team_text(summary))

    # WORKSTREAMS table
    lines.extend(_summary_workstreams_text(summary))

    # PEOPLE table
    lines.extend(_summary_people_text(summary))

    # DATA HEALTH section
    lines.extend(_summary_data_health_text(summary))

    # Source-disclosure sections (only if requested)
    if show_sources:
        lines.extend(_summary_what_counted_text(summary))
        lines.extend(_summary_how_attributed_text(summary))
        lines.extend(_summary_what_excluded_text(summary))

    return "\n".join(lines) + "\n"


def _attribution_description(source: str) -> str:
    """Return the human-readable description for an attribution source."""
    descriptions = {
        "component": "issue's own components field",
        "repo": "PR's repo maps to exactly one workstream",
        "project": "Jira project key (USHIFT → MicroShift)",
        "shared": "cross-cutting CI / tooling / docs repo",
        "parent": "inherited from the parent epic",
    }
    return descriptions.get(source, source)


def _summary_header_markdown(summary: ExecutiveSummary) -> List[str]:
    """Render the title and window lines in markdown."""
    lines = []
    lines.append(f"# OCP-Edge Cross-Workstream Contribution — {summary.period_label}")
    lines.append("")

    if summary.window:
        from_date, to_date = summary.window
        included = summary.metrics.total_member_count
        excluded = len(summary.data_quality.excluded_members)
        full = included + excluded
        lines.append(f"**Window:** {from_date} .. {to_date}")
        if excluded > 0:
            lines.append(f"**Roster:** {included} of {full} ({excluded} excluded)")
        else:
            lines.append(f"**Roster:** {included} of {full}")
        lines.append("")

    return lines


def _summary_team_markdown(summary: ExecutiveSummary) -> List[str]:
    """Render the TEAM section in markdown."""
    from workstream_map import workstream_acronyms

    lines = []
    lines.append("## Team")
    lines.append("")

    total_activity = sum(ml.total for ml in summary.per_member)
    median = int(summary.signals.team_median_total)
    people_per_ws = summary.metrics.mean_people_per_workstream
    num_workstreams = len(workstream_acronyms())
    ws_per_person = summary.metrics.mean_workstreams_per_person
    active_count = summary.metrics.active_member_count

    lines.append(f"- **{total_activity} contributions** — median {median} per person")
    lines.append(f"- **{people_per_ws:.1f} people per workstream** (mean of {num_workstreams})")
    lines.append(f"- **{ws_per_person:.1f} workstreams per person** ({active_count} active)")
    lines.append("")

    return lines


def _summary_workstreams_markdown(summary: ExecutiveSummary) -> List[str]:
    """Render the WORKSTREAMS table in markdown."""
    lines = []
    lines.append("## Workstreams")
    lines.append("")
    lines.append("| Workstream | Volume | People | Largest Contributor | Share |")
    lines.append("|------------|--------|--------|---------------------|-------|")

    for ws_line in summary.per_workstream:
        if ws_line.top_contributor:
            lines.append(
                f"| {ws_line.workstream} | {ws_line.volume} | {ws_line.contributors} | "
                f"{ws_line.top_contributor} | {ws_line.top_share}% |"
            )
        else:
            lines.append(
                f"| {ws_line.workstream} | {ws_line.volume} | {ws_line.contributors} | | |"
            )

    lines.append("")
    return lines


def _summary_people_markdown(summary: ExecutiveSummary) -> List[str]:
    """Render the PEOPLE table in markdown."""
    lines = []
    lines.append("## People")
    lines.append("")
    lines.append("| Member | Total | Workstreams Touched | Largest Workstream | Share |")
    lines.append("|--------|-------|---------------------|-------------------|-------|")

    for member_line in summary.per_member:
        touched_str = f"{member_line.workstreams_touched} of 6"
        if member_line.top_workstream:
            lines.append(
                f"| {member_line.member} | {member_line.total} | {touched_str} | "
                f"{member_line.top_workstream} | {member_line.top_share}% |"
            )
        else:
            lines.append(f"| {member_line.member} | {member_line.total} | {touched_str} | | |")

    lines.append("")
    return lines


def _summary_data_health_markdown(summary: ExecutiveSummary) -> List[str]:
    """Render the DATA HEALTH section in markdown."""
    lines = []
    lines.append("## Data Health")
    lines.append("")

    unattributed_total = summary.counts_by_attribution.get("", 0)
    total_records = summary.total_records
    if total_records > 0:
        pct = round(100 * unattributed_total / total_records)
    else:
        pct = 0

    lines.append(
        f"**{unattributed_total} of {total_records} records ({pct}%)** could not be placed in a workstream."
    )
    lines.append("")

    jira_no_comp = summary.data_quality.tickets_no_component_no_parent
    jira_parent_empty = summary.data_quality.tickets_parent_also_empty
    pr_count = sum(summary.data_quality.unattributed_by_repo.values())

    lines.append(
        f"- **{jira_no_comp + jira_parent_empty} Jira tickets** — {jira_no_comp} missing a component, {jira_parent_empty} whose epic is also empty"
    )
    lines.append(f"- **{pr_count} PRs** — repos not mapped to a workstream")
    lines.append("")

    return lines


def _summary_what_counted_markdown(summary: ExecutiveSummary) -> List[str]:
    """Render the WHAT WAS COUNTED section in markdown (--show-sources only)."""
    lines = []
    lines.append("## What Was Counted")
    lines.append("")

    jira_counts = summary.counts_by_source_kind.get("jira", {})
    github_counts = summary.counts_by_source_kind.get("github", {})

    if jira_counts or github_counts:
        lines.append("| Source | Total | Details |")
        lines.append("|--------|-------|---------|")

        if jira_counts:
            jira_total = sum(jira_counts.values())
            jira_detail = " · ".join(
                f"{kind} {jira_counts[kind]}" for kind in ACTIVITY_KINDS if kind in jira_counts
            )
            lines.append(f"| Jira | {jira_total} | {jira_detail} |")

        if github_counts:
            github_total = sum(github_counts.values())
            github_detail = " · ".join(
                f"{kind} {github_counts[kind]}" for kind in ACTIVITY_KINDS if kind in github_counts
            )
            lines.append(f"| GitHub | {github_total} | {github_detail} |")
        lines.append("")

    if jira_counts:
        jira_projects_str = ", ".join(summary.jira_projects)
        lines.append(f"**Jira projects:** {jira_projects_str} (assignee + QA contact)")
        if summary.ocpstrat_project:
            lines.append(f" plus {summary.ocpstrat_project} (SME / assignee roles)")
        lines.append("")
        lines.append("*Comments are not collected — Jira Cloud hides author emails*")
        lines.append("")

    if github_counts:
        lines.append(
            f"**GitHub:** any repo whose owner is in the org allowlist ({summary.allowed_org_count} orgs)"
        )
        lines.append("")
        lines.append("- **authored** = PR created in the window")
        lines.append(
            "- **reviewed** = PR updated in the window (GitHub cannot search by review date)"
        )
        lines.append("- every record is timestamped with the PR's creation date")
        if summary.out_of_window_records > 0:
            lines.append(
                f"- **{summary.out_of_window_records} records** carry a date before the window but are still counted"
            )
            lines.append("- → do not filter activities.csv by the ts column")
        lines.append("")

    return lines


def _summary_how_attributed_markdown(summary: ExecutiveSummary) -> List[str]:
    """Render the HOW EACH ITEM WAS ATTRIBUTED section in markdown (--show-sources only)."""
    lines = []
    lines.append("## How Each Item Was Attributed")
    lines.append("")
    lines.append("**First hit wins**")
    lines.append("")
    lines.append("| Source | Count | % | Description |")
    lines.append("|--------|-------|---|-------------|")

    percentages = _compute_attribution_percentages(summary)
    named_sources = [
        (source, count) for source, count in summary.counts_by_attribution.items() if source
    ]
    named_sources.sort(key=lambda pair: pair[1], reverse=True)
    unattributed_count = summary.counts_by_attribution.get("", 0)

    for source, count in named_sources:
        pct = percentages.get(source, 0)
        desc = _attribution_description(source)
        lines.append(f"| {source} | {count} | {pct}% | {desc} |")

    if unattributed_count > 0:
        pct = percentages.get("", 0)
        lines.append(
            f"| unattributed | {unattributed_count} | {pct}% | none of the above matched — see EXCLUDED |"
        )

    lines.append("")
    lines.append(
        '*A PR that names a Jira key is resolved through the Jira chain, so it is reported as component / parent / project, not as a separate "jira key" rule.*'
    )
    lines.append("")

    return lines


def _summary_what_excluded_markdown(summary: ExecutiveSummary) -> List[str]:
    """Render the WHAT WAS EXCLUDED section in markdown (--show-sources only)."""
    lines = []
    lines.append("## What Was Excluded")
    lines.append("")

    # Excluded members
    if summary.data_quality.excluded_members:
        count = len(summary.data_quality.excluded_members)
        members_str = ", ".join(summary.data_quality.excluded_members)
        lines.append(f"**{count} roster members:** {members_str}")
        lines.append("")
        lines.append("*Managers by role title, not IC contributors. --include-all puts them back.*")
        lines.append("")

    # Excluded PRs
    if summary.data_quality.excluded_personal_repo_prs > 0:
        lines.append(
            f"**{summary.data_quality.excluded_personal_repo_prs} PRs** — personal-namespace repos, dropped before counting"
        )
        lines.append("")
        if summary.data_quality.excluded_personal_repos:
            top_repos, more_repos, more_prs = _top_repos(
                summary.data_quality.excluded_personal_repos
            )
            for repo, count in top_repos:
                lines.append(f"- {repo}: {count}")
            if more_repos:
                lines.append(f"- and {more_repos} more repos ({more_prs} PRs)")
            lines.append("")
        lines.append(
            "*The org allowlist is hand-kept — a real org missing from it lands in this list looking like a side project.*"
        )
        lines.append("")

    # Unattributed items
    unattributed_total = summary.counts_by_attribution.get("", 0)
    if unattributed_total > 0:
        lines.append(f"**{unattributed_total} items** — could not be attributed to any workstream")
        lines.append("")

        # Unattributed PRs by repo
        if summary.data_quality.unattributed_by_repo:
            pr_count = sum(summary.data_quality.unattributed_by_repo.values())
            lines.append(f"**{pr_count} PRs by repo:**")
            lines.append("")
            top_repos, more_repos, more_prs = _top_repos(summary.data_quality.unattributed_by_repo)
            for repo, count in top_repos:
                note = _repo_note(repo)
                if note:
                    lines.append(f"- {repo}: {count} {note}")
                else:
                    lines.append(f"- {repo}: {count}")
            if more_repos:
                lines.append(f"- and {more_repos} more repos ({more_prs} PRs)")
            lines.append("")

        # Jira tickets with no component and no parent
        if summary.data_quality.tickets_no_component_no_parent > 0:
            count = summary.data_quality.tickets_no_component_no_parent
            lines.append(
                f"**{count} Jira tickets** with no component and no parent epic → fix on the ticket"
            )
            lines.append("")

        # Jira tickets whose parent epic is also empty
        if summary.data_quality.tickets_parent_also_empty > 0:
            count = summary.data_quality.tickets_parent_also_empty
            lines.append(
                f"**{count} Jira tickets** whose parent epic is also empty → fix on the epic"
            )
            lines.append("")

    return lines


def _summary_markdown(summary: ExecutiveSummary, show_sources: bool = False) -> str:
    """Render executive summary in markdown format with pipe tables."""
    lines = []

    # Header
    lines.extend(_summary_header_markdown(summary))

    # TEAM section
    lines.extend(_summary_team_markdown(summary))

    # WORKSTREAMS table
    lines.extend(_summary_workstreams_markdown(summary))

    # PEOPLE table
    lines.extend(_summary_people_markdown(summary))

    # DATA HEALTH section
    lines.extend(_summary_data_health_markdown(summary))

    # Source-disclosure sections (only if requested)
    if show_sources:
        lines.extend(_summary_what_counted_markdown(summary))
        lines.extend(_summary_how_attributed_markdown(summary))
        lines.extend(_summary_what_excluded_markdown(summary))

    return "\n".join(lines) + "\n"


def render_summary(
    summary: ExecutiveSummary, output_format: str, show_sources: bool = False
) -> str:
    """Render the executive summary in the requested format.

    Args:
        summary: The executive summary to render.
        output_format: One of "text" or "markdown".
        show_sources: Whether to append the WHAT WAS COUNTED, HOW EACH ITEM WAS
            ATTRIBUTED, and WHAT WAS EXCLUDED sections.

    Returns:
        Rendered summary as a string.
    """
    _require_known_summary_format(output_format)
    if output_format == "markdown":
        return _summary_markdown(summary, show_sources)
    return _summary_text(summary, show_sources)
