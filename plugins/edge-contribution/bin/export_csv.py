#!/usr/bin/env python3
"""Export cross-workstream contribution data as CSV files for external analysis.

Produces four CSV files from collected activity records:

* ``activities.csv`` — one row per activity record, with normalized fields
* ``matrix.csv`` — people x workstream counts with allocation flags
* ``metrics.csv`` — team scalars and per-workstream totals
* ``data-quality.csv`` — excluded members/repos and unattributed items

All builders are pure functions returning CSV strings. The ``main`` function
wires them together with file I/O.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from typing import List, Optional

import report
from render import ManagerReport
from workstream_map import display_columns


EXPORT_FILES = ("activities.csv", "matrix.csv", "metrics.csv", "data-quality.csv")


def build_activities_csv(activities: List[dict]) -> str:
    """Build the activities CSV with normalized field names.

    Jira and GitHub records use different field names for the same concepts:
    - url: Jira uses ``url``, GitHub uses ``pr_url``
    - issue_key: Jira uses ``issue_key``, GitHub uses ``source_key``
    - repo: only present on GitHub records

    Missing fields write empty strings, never the literal "None".
    Rows are sorted by (member, ts) for stable diff output.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([
        "member",
        "source",
        "kind",
        "workstream",
        "attribution_source",
        "repo",
        "issue_key",
        "url",
        "ts",
        "unattributed_reason",
    ])

    # Sort activities by member, then timestamp for stable output
    sorted_activities = sorted(
        activities,
        key=lambda a: (a.get("member", ""), a.get("ts", "")),
    )

    for activity in sorted_activities:
        kind = activity.get("kind", "")
        source = report.classify_source(kind)

        # Normalize field names between Jira and GitHub
        url = activity.get("url") or activity.get("pr_url") or ""
        issue_key = activity.get("issue_key") or activity.get("source_key") or ""
        repo = activity.get("repo", "")

        writer.writerow([
            activity.get("member", ""),
            source,
            kind,
            activity.get("workstream", ""),
            activity.get("attribution_source", ""),
            repo,
            issue_key,
            url,
            activity.get("ts", ""),
            activity.get("unattributed_reason", ""),
        ])

    return buffer.getvalue()


def build_matrix_csv(report: ManagerReport) -> str:
    """Build the matrix CSV with per-workstream counts and allocation flags.

    Columns: member, <workstreams>, TOTAL, workstreams_touched, over, under, narrow.
    The workstream columns come from display_columns() (6 canonical + SHARED),
    not metrics.workstreams (which excludes SHARED so workstreams_touched stays
    "N of 6").
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    workstreams = display_columns()
    header = ["member"] + workstreams + ["TOTAL", "workstreams_touched", "over", "under", "narrow"]
    writer.writerow(header)

    for member in report.metrics.members:
        # Get counts for each workstream
        row = [member]
        for ws in workstreams:
            count = report.matrix.counts.get(member, {}).get(ws, 0)
            row.append(count)

        # Add total
        total = report.signals.total_by_member.get(member, 0)
        row.append(total)

        # Add how many of the six workstreams this member touched
        row.append(report.metrics.workstreams_touched_by_member.get(member, 0))

        # Add flags
        row.append("true" if member in report.signals.over_allocated else "false")
        row.append("true" if member in report.signals.under_allocated else "false")
        row.append("true" if member in report.signals.narrow else "false")

        writer.writerow(row)

    return buffer.getvalue()


def build_metrics_csv(report: ManagerReport) -> str:
    """Build the metrics CSV with team scalars and per-workstream totals.

    Every row label says what it counts. Team scalars first
    (mean_people_per_workstream, mean_workstreams_per_person, active_members,
    total_members, team_median_total, grid_max, unattributed), then
    per-workstream total and contributors counts.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    writer.writerow(["metric", "value"])

    # Team scalars
    writer.writerow(
        ["mean_people_per_workstream", round(report.metrics.mean_people_per_workstream, 4)]
    )
    writer.writerow(
        ["mean_workstreams_per_person", round(report.metrics.mean_workstreams_per_person, 4)]
    )
    writer.writerow(["active_members", report.metrics.active_member_count])
    writer.writerow(["total_members", report.metrics.total_member_count])
    writer.writerow(["team_median_total", round(report.signals.team_median_total, 4)])
    writer.writerow(["grid_max", report.signals.grid_max])
    writer.writerow(["unattributed", report.unattributed_count])

    # Per-workstream metrics. There is deliberately no second per-workstream
    # contributor count here: contributors:<ws> is the only one, and it also
    # covers SHARED.
    for workstream, count in report.signals.total_by_workstream.items():
        writer.writerow([f"total:{workstream}", count])

    for workstream, count in report.signals.contributors_by_workstream.items():
        writer.writerow([f"contributors:{workstream}", count])

    return buffer.getvalue()


def build_data_quality_csv(report: ManagerReport) -> str:
    """Build the data quality CSV with all exclusion and unattributed categories.

    Categories:
    - excluded_member
    - excluded_personal_repo
    - unattributed_repo
    - unattributed_jira_no_parent
    - unattributed_jira_parent_empty

    Unlike the text renderer, this does NOT truncate repo lists — every repo
    gets a row since this is the raw export.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    writer.writerow(["category", "label", "count"])

    if not report.data_quality:
        return buffer.getvalue()

    dq = report.data_quality

    # Excluded members
    for member in dq.excluded_members:
        writer.writerow(["excluded_member", member, ""])

    # Excluded personal repos
    for repo, count in sorted(dq.excluded_personal_repos.items(), key=lambda x: x[1], reverse=True):
        writer.writerow(["excluded_personal_repo", repo, count])

    # Unattributed repos
    for repo, count in sorted(dq.unattributed_by_repo.items(), key=lambda x: x[1], reverse=True):
        writer.writerow(["unattributed_repo", repo, count])

    # Unattributed Jira tickets
    if dq.tickets_no_component_no_parent > 0:
        writer.writerow([
            "unattributed_jira_no_parent",
            "tickets_no_component_no_parent",
            dq.tickets_no_component_no_parent,
        ])

    if dq.tickets_parent_also_empty > 0:
        writer.writerow([
            "unattributed_jira_parent_empty",
            "tickets_parent_also_empty",
            dq.tickets_parent_also_empty,
        ])

    return buffer.getvalue()


def main(argv: Optional[List[str]] = None) -> int:
    """Export contribution data as four CSV files."""
    parser = argparse.ArgumentParser(
        description="Export cross-workstream contribution data as CSV files."
    )
    parser.add_argument(
        "--activity",
        action="append",
        required=True,
        dest="activity_files",
        metavar="PATH",
        help="Activity JSON file (repeatable)",
    )
    parser.add_argument(
        "--members-file",
        required=True,
        metavar="PATH",
        help="Roster JSON file",
    )
    parser.add_argument(
        "--period",
        required=True,
        metavar="LABEL",
        help="Period label (e.g., 2026Q3)",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        metavar="PATH",
        help="Output directory for CSV files",
    )

    args = parser.parse_args(argv)

    # Load activities
    activities, collector_meta = report._load_activities(args.activity_files)

    # Load roster and prepare member lists
    import json
    with open(args.members_file, encoding="utf-8") as handle:
        roster_entries = json.load(handle)

    excluded, jira_to_display, display_names = report.prepare_roster(
        roster_entries
    )

    # Translate activities to display names
    activities = report.translate_activities_to_display_names(activities, jira_to_display)

    # Build manager report
    workstreams = display_columns()
    manager_report = report.build_manager_report(
        activities, display_names, workstreams, args.period, excluded, collector_meta
    )

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Build and write each CSV file
    files_written = []

    # activities.csv
    activities_csv = build_activities_csv(activities)
    activities_path = os.path.join(args.output_dir, "activities.csv")
    with open(activities_path, "w", encoding="utf-8") as handle:
        handle.write(activities_csv)
    files_written.append(activities_path)
    print(activities_path)

    # matrix.csv
    matrix_csv = build_matrix_csv(manager_report)
    matrix_path = os.path.join(args.output_dir, "matrix.csv")
    with open(matrix_path, "w", encoding="utf-8") as handle:
        handle.write(matrix_csv)
    files_written.append(matrix_path)
    print(matrix_path)

    # metrics.csv
    metrics_csv = build_metrics_csv(manager_report)
    metrics_path = os.path.join(args.output_dir, "metrics.csv")
    with open(metrics_path, "w", encoding="utf-8") as handle:
        handle.write(metrics_csv)
    files_written.append(metrics_path)
    print(metrics_path)

    # data-quality.csv
    data_quality_csv = build_data_quality_csv(manager_report)
    data_quality_path = os.path.join(args.output_dir, "data-quality.csv")
    with open(data_quality_path, "w", encoding="utf-8") as handle:
        handle.write(data_quality_csv)
    files_written.append(data_quality_path)
    print(data_quality_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
