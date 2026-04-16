#!/usr/bin/env python3
"""Transform raw Jira sprint board results into sprints.json."""

import argparse
from datetime import datetime
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import (
    extract_sprint_number,
    format_date,
    days_between,
    load_sprints,
    write_output,
)


def parse_sprint(raw):
    name = raw.get("name") or ""
    start = raw.get("startDate") or raw.get("start_date") or raw.get("start")
    end = raw.get("endDate") or raw.get("end_date") or raw.get("end")

    return {
        "num": extract_sprint_number(name),
        "id": raw.get("id"),
        "name": name,
        "goal": raw.get("goal") or None,
        "start": format_date(start) if start else None,
        "end": format_date(end) if end else None,
        "state": raw.get("state", ""),
    }


def build_target_sprint(sprint, today):
    if sprint["start"] is None or sprint["end"] is None:
        return None

    total = days_between(sprint["start"], sprint["end"])
    elapsed = max(0, min(days_between(sprint["start"], today), total))
    remaining = max(0, min(days_between(today, sprint["end"]), total))

    return {
        "id": sprint["id"],
        "name": sprint["name"],
        "goal": sprint["goal"],
        "start": sprint["start"],
        "end": sprint["end"],
        "state": sprint["state"],
        "days_elapsed": elapsed,
        "days_remaining": remaining,
        "total_days": total,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transform sprint board data into sprints.json"
    )
    parser.add_argument("--input", nargs="+", required=True, help="Raw MCP response file(s)")
    parser.add_argument("--output", required=True, help="Output path")
    parser.add_argument("--today", required=True, help="Today's date YYYY-MM-DD")
    parser.add_argument("--target-sprint", default=None, help="Sprint number or 'active'")
    parser.add_argument("--first-sprint", type=int, default=None)
    parser.add_argument("--last-sprint", type=int, default=None)
    parser.add_argument("--total-dev-sprints", type=int, default=None)
    args = parser.parse_args()

    try:
        datetime.strptime(args.today, "%Y-%m-%d")
    except ValueError:
        parser.error(f"--today must be YYYY-MM-DD, got: {args.today}")

    if (args.first_sprint is None) != (args.last_sprint is None):
        parser.error("--first-sprint and --last-sprint must be provided together")

    if args.first_sprint is not None and args.first_sprint > args.last_sprint:
        args.first_sprint, args.last_sprint = args.last_sprint, args.first_sprint

    raw_sprints = load_sprints(args.input)
    sprints = [parse_sprint(s) for s in raw_sprints]
    sprints = [s for s in sprints if s["num"] is not None]

    # Build sprint map
    use_range = args.first_sprint is not None
    if use_range:
        map_sprints = [
            s for s in sprints if args.first_sprint <= s["num"] <= args.last_sprint
        ]
    else:
        map_sprints = sprints

    sprint_map = {}
    for s in map_sprints:
        sprint_map[str(s["num"])] = {
            "id": s["id"],
            "name": s["name"],
            "start": s["start"],
            "end": s["end"],
            "state": s["state"],
        }

    # Release-health derived values
    rh = {
        "refinement_sprint_id": None,
        "refinement_sprint_closed": None,
        "current_sprint_num": None,
        "completed_sprint_nums": None,
        "completed_dev_sprint_count": None,
        "total_dev_sprints": None,
        "remaining_sprint_count": None,
        "sprints_until_branch_cut": None,
        "expected_dev_completion_pct": None,
    }

    if use_range:
        range_sprints = [
            s for s in sprints if args.first_sprint <= s["num"] <= args.last_sprint
        ]

        if not range_sprints:
            rh["error"] = (
                f"No sprints found for range {args.first_sprint}\u2013{args.last_sprint}"
            )
        else:
            ref = next(
                (s for s in range_sprints if s["num"] == args.first_sprint), None
            )
            if ref:
                rh["refinement_sprint_id"] = ref["id"]
                rh["refinement_sprint_closed"] = ref["state"] == "closed"

            active_in_range = [s for s in range_sprints if s["state"] == "active"]
            closed_in_range = [s for s in range_sprints if s["state"] == "closed"]

            if active_in_range:
                rh["current_sprint_num"] = max(s["num"] for s in active_in_range)
            elif closed_in_range:
                rh["current_sprint_num"] = max(s["num"] for s in closed_in_range)

            rh["completed_sprint_nums"] = sorted(
                s["num"] for s in closed_in_range if s["num"] != args.first_sprint
            )
            rh["completed_dev_sprint_count"] = len(rh["completed_sprint_nums"])
            rh["total_dev_sprints"] = args.total_dev_sprints

            remaining = [
                s for s in range_sprints if s["state"] in ("active", "future")
            ]
            rh["remaining_sprint_count"] = len(remaining)
            rh["sprints_until_branch_cut"] = len(remaining)  # alias for release-health consumers

            if args.total_dev_sprints and args.total_dev_sprints > 0:
                rh["expected_dev_completion_pct"] = round(
                    rh["completed_dev_sprint_count"] / args.total_dev_sprints * 100, 1
                )
            else:
                rh["expected_dev_completion_pct"] = 0

    # Target sprint
    target_sprint = None
    if args.target_sprint:
        if args.target_sprint == "active":
            active = [s for s in sprints if s["state"] == "active"]
            if active:
                target = max(active, key=lambda s: s["num"])
            else:
                closed = [
                    s
                    for s in sprints
                    if s["state"] == "closed" and s["num"] is not None
                ]
                target = max(closed, key=lambda s: s["num"]) if closed else None
        else:
            try:
                target_num = int(args.target_sprint)
            except ValueError:
                parser.error(f"--target-sprint must be 'active' or an integer, got: {args.target_sprint}")
            target = next((s for s in sprints if s["num"] == target_num), None)

        if target:
            target_sprint = build_target_sprint(target, args.today)

    output = {
        "sprint_map": sprint_map,
        **rh,
        "target_sprint": target_sprint,
    }

    write_output(output, args.output)


if __name__ == "__main__":
    main()
