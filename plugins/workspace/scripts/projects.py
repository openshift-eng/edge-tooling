#!/usr/bin/env python3
"""List the workspace's projects as JSON (read-only).

Usage: projects.py list
Output: {"status": "ok"|"no_workspace", "root", "managed", "projects": [...]}

Never writes anything (unlike resume-project.py, it does not stamp
last-active). Yaml-free: uses workspace_lib's flat frontmatter reader.
"""

from __future__ import annotations

import json
import sys

import workspace_lib


def _clean(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def list_projects(root) -> list[dict]:
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return []
    entries = []
    for d in sorted(projects_dir.iterdir()):
        claude_md = d / "CLAUDE.md"
        try:
            if not d.is_dir() or not claude_md.is_file():
                continue
        except OSError:
            continue
        fm = {k: _clean(v) for k, v in workspace_lib.parse_frontmatter(claude_md).items()}
        try:
            text = claude_md.read_text()
        except (OSError, UnicodeDecodeError):
            text = ""
        items = [i for s in workspace_lib.parse_checklist(text) for i in s["items"]]
        status = fm.get("status", "")
        entries.append({
            "name": d.name,
            "project": fm.get("project") or d.name,
            "type": fm.get("type", ""),
            "status": status,
            "last_active": fm.get("last-active", ""),
            "jira": fm.get("jira", ""),
            "branch": fm.get("branch", ""),
            "domain": fm.get("domain") or fm.get("preset", ""),
            "finished": status.lower() in workspace_lib.TERMINAL_STATUSES,
            "tasks": {"checked": sum(1 for i in items if i["checked"]),
                      "total": len(items)},
        })
    # Entries are already name-ascending; a stable reverse sort keeps that
    # order for ties and pushes missing last_active last.
    entries.sort(key=lambda e: e["last_active"], reverse=True)
    return entries


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "list":
        print(json.dumps({"status": "error", "error_message": "usage: projects.py list"}))
        return
    root = workspace_lib.resolve_workspace_root()
    if root is None:
        print(json.dumps({"status": "no_workspace", "managed": False, "projects": []}))
        return
    print(json.dumps({
        "status": "ok",
        "root": str(root),
        "managed": (root / workspace_lib.DEV_ENV_YAML).exists(),
        "projects": list_projects(root),
    }))


if __name__ == "__main__":
    main()
