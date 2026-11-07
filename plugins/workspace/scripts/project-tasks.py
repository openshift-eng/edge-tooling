#!/usr/bin/env python3
"""Read and edit a project's checklist in <workspace>/projects/<project>/CLAUDE.md.

Usage:
  project-tasks.py list   <project>
  project-tasks.py toggle <project> --section HEADING --text TEXT [--occurrence N]
  project-tasks.py add    <project> --section HEADING --text TEXT
  project-tasks.py remove <project> --section HEADING --text TEXT [--occurrence N]

Targets are matched by section heading + item text + occurrence, never by
line number. Statuses: ok, not_found (no project), stale (section/item gone;
nothing written), error. Frontmatter is never touched. Exit code is 0 for
handled outcomes.
"""

from __future__ import annotations

import argparse
import json
import sys

import workspace_lib


def out(payload: dict) -> None:
    print(json.dumps(payload))


def find_section_item(sections, heading, text, occurrence):
    """Return (section, item) for the match; item is None if absent. section None if no heading."""
    found_section = None
    for sec in sections:
        if sec["heading"] != heading or sec["level"] == 0:
            continue
        found_section = found_section or sec
        for it in sec["items"]:
            if it["text"] == text and it["occurrence"] == occurrence:
                return sec, it
    return found_section, None


def main() -> None:
    ap = argparse.ArgumentParser(prog="project-tasks.py")
    ap.add_argument("action", choices=["list", "toggle", "add", "remove"])
    ap.add_argument("project")
    ap.add_argument("--section")
    ap.add_argument("--text")
    ap.add_argument("--occurrence", type=int, default=0)
    try:
        args = ap.parse_args()
    except SystemExit:
        out({"status": "error", "error_message": "invalid arguments"})
        return

    root = workspace_lib.resolve_workspace_root()
    if root is None:
        out({"status": "no_workspace"})
        return
    name = args.project
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        out({"status": "not_found"})
        return
    path = root / "projects" / name / "CLAUDE.md"
    if not path.is_file():
        out({"status": "not_found"})
        return

    def read() -> str:
        with open(path, newline="", encoding="utf-8") as fh:
            return fh.read()

    try:
        text = read()
    except (OSError, UnicodeDecodeError) as exc:
        out({"status": "error", "error_message": str(exc)})
        return
    sections = workspace_lib.parse_checklist(text)

    if args.action == "list":
        out({"status": "ok", "project": name, "sections": [
            {"heading": s["heading"], "level": s["level"],
             "items": [{"text": i["text"], "checked": i["checked"],
                        "occurrence": i["occurrence"]} for i in s["items"]]}
            for s in sections if s["items"] and s["level"] != 0]})
        return

    if args.section is None or args.text is None:
        out({"status": "error", "error_message": "--section and --text are required"})
        return
    heading = args.section.strip()
    item_text = args.text.strip()

    lines = text.splitlines(keepends=True)

    if args.action == "add":
        if not item_text or "\n" in args.text or "\r" in args.text:
            out({"status": "error", "error_message": "text must be a single non-empty line"})
            return
        matches = [s for s in sections
                   if s["heading"] == heading and s["level"] != 0]
        if not matches:
            out({"status": "stale"})
            return
        if len(matches) > 1:
            out({"status": "error", "error_message":
                 "Cannot add a task under a repeated heading; use a unique heading."})
            return

        sec = matches[0]
        after = sec["items"][-1]["line"] if sec["items"] else sec["line"]
        anchor = lines[after]
        eol = anchor[len(anchor.rstrip("\r\n")):]
        if not eol:
            # Anchor is the file's last line without a newline: borrow the
            # file's dominant ending for the join and the new line.
            eol = "\r\n" if "\r\n" in text else "\n"
            lines[after] = anchor + eol
            new_line = f"- [ ] {item_text}"
        else:
            new_line = f"- [ ] {item_text}{eol}"
        lines.insert(after + 1, new_line)
        workspace_lib.atomic_write_text(path, "".join(lines))
        out({"status": "ok"})
        return

    sec, item = find_section_item(sections, heading, item_text, args.occurrence)
    if item is None:
        out({"status": "stale"})
        return
    idx = item["line"]
    if args.action == "remove":
        del lines[idx]
        workspace_lib.atomic_write_text(path, "".join(lines))
        out({"status": "ok"})
        return

    # toggle
    m = workspace_lib.RE_CHECKLIST_ITEM.match(lines[idx].rstrip("\r\n"))
    new_checked = not item["checked"]
    eol = lines[idx][len(lines[idx].rstrip("\r\n")):]
    lines[idx] = f"{m.group(1)}{'x' if new_checked else ' '}{m.group(3)}{m.group(4)}{eol}"
    workspace_lib.atomic_write_text(path, "".join(lines))
    out({"status": "ok", "checked": new_checked})


if __name__ == "__main__":
    main()
