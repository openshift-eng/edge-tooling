#!/usr/bin/env python3
"""Show the most recently active projects, excluding done projects.

Used by the SessionStart hook to give Claude and the user quick context.
Output: JSON with systemMessage (user-visible) and additionalContext (model context).

Deliberately yaml-free: this runs on every SessionStart and plugins cannot
declare python dependencies, so it must never import a third-party module.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import workspace_lib


parse_frontmatter = workspace_lib.parse_frontmatter


def newest_mtime(directory: Path) -> float | None:
    """Return the most recent file mtime under directory, or None."""
    newest = None
    for root, _, files in os.walk(directory):
        for f in files:
            try:
                mt = os.path.getmtime(os.path.join(root, f))
            except OSError:
                continue
            if newest is None or mt > newest:
                newest = mt
    return newest


TERMINAL_STATUSES = workspace_lib.TERMINAL_STATUSES


def _parse_last_active(value: str) -> float | None:
    """Parse a last-active value (YYYY-MM-DDTHH:MM or YYYY-MM-DD) into a timestamp."""
    try:
        if "T" in value:
            dt = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        else:
            dt = datetime.strptime(value, "%Y-%m-%d")
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt.timestamp()
    except (ValueError, OverflowError):
        return None


def collect_projects(projects_dir: Path) -> list[dict]:
    """Collect non-done projects with their metadata, sorted by last-active date or mtime."""
    entries = []
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue

        fm = parse_frontmatter(d / "CLAUDE.md")
        if fm.get("status", "").lower() in TERMINAL_STATUSES:
            continue

        last_active_str = fm.get("last-active", "")
        la_ts = _parse_last_active(last_active_str) if last_active_str else None

        if la_ts is not None:
            sort_ts = la_ts
            date_str = datetime.fromtimestamp(la_ts).strftime("%b %d %H:%M")
        else:
            sort_ts = newest_mtime(d)
            if sort_ts is None:
                continue
            date_str = datetime.fromtimestamp(sort_ts).strftime("%b %d %H:%M")

        entries.append({
            "name": d.name,
            "type": fm.get("type", "—"),
            "status": fm.get("status", "—"),
            "sort_ts": sort_ts,
            "date_str": date_str,
        })

    entries.sort(key=lambda e: e["sort_ts"], reverse=True)
    return entries


def main():
    """Entry point: output recent non-done projects as JSON or plain names."""
    # SessionStart hook: must stay silent (exit 0, no output) when not launched
    # inside a workspace, so it never adds noise to unrelated projects.
    project_root = workspace_lib.resolve_workspace_root()
    if project_root is None:
        sys.exit(0)
    projects_dir = project_root / "projects"

    if not projects_dir.is_dir():
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "--names":
        for entry in collect_projects(projects_dir):
            print(entry["name"])
        sys.exit(0)

    entries = collect_projects(projects_dir)
    if not entries:
        sys.exit(0)

    top = entries[:5]

    lines = ["Recent projects:", ""]
    lines.append("  #   NAME                           TYPE           STATUS     LAST ACTIVE")
    lines.append("  -   ----                           ----           ------     -----------")
    for i, e in enumerate(top, 1):
        lines.append(f"  {i:<3} {e['name']:<30} {e['type']:<14} {e['status']:<10} {e['date_str']}")
    lines.append("")
    lines.append("  Tip: /workspace:resume-project <name-or-number>")

    output = "\n".join(lines)
    payload = {
        "systemMessage": output,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": output,
        },
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
