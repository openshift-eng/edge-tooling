#!/usr/bin/env python3
"""Validate a grouped-CVE JSON file against the cves-grouped.json schema.

Usage:
    validate_grouped_cves.py --input FILE [--schema-ref FILE]
    validate_grouped_cves.py --workdir DIR
      # checks jira/cves-grouped-reviewed.json vs jira/cves-grouped.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Top-level keys always present on group_cves.py output.
TOP_LEVEL_REQUIRED = (
    "group_count",
    "ticket_count",
    "groups",
)

GROUP_REQUIRED = (
    "group_id",
    "cve_id",
    "component",
    "summary_stem",
    "ticket_count",
    "ticket_keys",
    "versions",
    "repos",
    "tickets",
    "needs_llm_review",
    "llm_review_reasons",
)

TICKET_REQUIRED = (
    "key",
)


def _err(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def validate_grouped(data: Any, *, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{label}: root must be a JSON object"]

    for key in TOP_LEVEL_REQUIRED:
        if key not in data:
            errors.append(f"{label}: missing top-level key '{key}'")

    groups = data.get("groups")
    if "groups" in data and not isinstance(groups, list):
        errors.append(f"{label}: 'groups' must be a list")
        return errors

    if isinstance(groups, list):
        if "group_count" in data and data["group_count"] != len(groups):
            errors.append(
                f"{label}: group_count ({data['group_count']}) != len(groups) ({len(groups)})"
            )
        for i, group in enumerate(groups):
            g_label = f"{label}.groups[{i}]"
            if not isinstance(group, dict):
                errors.append(f"{g_label}: must be an object")
                continue
            for key in GROUP_REQUIRED:
                if key not in group:
                    errors.append(f"{g_label}: missing key '{key}'")
            tickets = group.get("tickets")
            if "tickets" in group and not isinstance(tickets, list):
                errors.append(f"{g_label}: 'tickets' must be a list")
            elif isinstance(tickets, list):
                if "ticket_count" in group and group["ticket_count"] != len(tickets):
                    errors.append(
                        f"{g_label}: ticket_count ({group['ticket_count']}) "
                        f"!= len(tickets) ({len(tickets)})"
                    )
                for j, ticket in enumerate(tickets):
                    t_label = f"{g_label}.tickets[{j}]"
                    if not isinstance(ticket, dict):
                        errors.append(f"{t_label}: must be an object")
                        continue
                    for key in TICKET_REQUIRED:
                        if key not in ticket:
                            errors.append(f"{t_label}: missing key '{key}'")
    return errors


def validate_against_schema_ref(candidate: Any, schema_ref: Any) -> list[str]:
    """Ensure candidate uses the same top-level and group key sets as schema_ref."""
    errors: list[str] = []
    if not isinstance(schema_ref, dict) or not isinstance(candidate, dict):
        return errors

    ref_top = set(schema_ref.keys())
    cand_top = set(candidate.keys())
    missing_top = ref_top - cand_top
    if missing_top:
        errors.append(
            "missing top-level keys present in schema ref: "
            + ", ".join(sorted(missing_top))
        )

    ref_groups = schema_ref.get("groups") or []
    cand_groups = candidate.get("groups") or []
    if ref_groups and isinstance(ref_groups[0], dict) and cand_groups:
        ref_group_keys = set(ref_groups[0].keys())
        for i, group in enumerate(cand_groups):
            if not isinstance(group, dict):
                continue
            missing = ref_group_keys - set(group.keys())
            if missing:
                errors.append(
                    f"groups[{i}]: missing keys present in schema ref: "
                    + ", ".join(sorted(missing))
                )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate grouped CVE JSON against cves-grouped schema"
    )
    parser.add_argument("--workdir", default="")
    parser.add_argument("--input", default="")
    parser.add_argument(
        "--schema-ref",
        default="",
        help="Reference cves-grouped.json (default: sibling or workdir/jira/cves-grouped.json)",
    )
    args = parser.parse_args()

    if args.workdir:
        workdir = Path(args.workdir)
        input_path = Path(args.input) if args.input else workdir / "jira" / "cves-grouped-reviewed.json"
        schema_path = (
            Path(args.schema_ref)
            if args.schema_ref
            else workdir / "jira" / "cves-grouped.json"
        )
    elif args.input:
        input_path = Path(args.input)
        schema_path = Path(args.schema_ref) if args.schema_ref else input_path.parent / "cves-grouped.json"
    else:
        _err("--workdir or --input required")
        sys.exit(2)

    if not input_path.is_file():
        _err(f"reviewed grouping not found: {input_path}")
        _err("stop without rebuilding scan targets (avoid stale prepare outputs)")
        sys.exit(1)

    try:
        candidate = load_json(input_path)
    except json.JSONDecodeError as exc:
        _err(f"invalid JSON in {input_path}: {exc}")
        sys.exit(1)

    errors = validate_grouped(candidate, label=str(input_path))

    if schema_path.is_file():
        try:
            schema_ref = load_json(schema_path)
        except json.JSONDecodeError as exc:
            _err(f"invalid schema ref JSON in {schema_path}: {exc}")
            sys.exit(1)
        errors.extend(validate_against_schema_ref(candidate, schema_ref))
    else:
        _err(f"schema ref not found: {schema_path} (structural checks only)")

    if errors:
        for msg in errors:
            _err(msg)
        _err("stop without rebuilding scan targets")
        sys.exit(1)

    print(
        json.dumps(
            {
                "ok": True,
                "input": str(input_path),
                "schema_ref": str(schema_path) if schema_path.is_file() else None,
                "group_count": candidate.get("group_count"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
