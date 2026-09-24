#!/usr/bin/env python3
"""Consolidate bloated project CLAUDE.md by archiving completed checklist items.

Usage: consolidate-project.py [--dry-run] <project-name>
Output: JSON to stdout.

Sections with 10+ completed items get their checked items (except the
last 3) archived to progress-archive.md. A pointer line replaces the
archived items in CLAUDE.md.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import workspace_lib

THRESHOLD = 10
KEEP_RECENT = 3
ARCHIVE_FILENAME = "progress-archive.md"
SENTINEL = "consolidated in `progress-archive.md`"
FILE_LINE_THRESHOLD = 100
NARRATIVE_SECTION = "Progress"

RE_HEADING = re.compile(r"^## (.+)$")
RE_CHECKED = re.compile(r"^\s*- \[x\] .+$")
RE_UNCHECKED = re.compile(r"^\s*- \[ \] .+$")
RE_STRIKETHROUGH = re.compile(r"^\s*- ~~?.+~~?\s*$")
RE_PLAIN_BULLET = re.compile(r"^\s*-\s+(?!\[[ x]\])(?!~).+$")


@dataclass
class Item:
    line_idx: int
    text: str
    kind: str  # "checked", "unchecked", "strikethrough", "plain_bullet", "paragraph", "other"


@dataclass
class Section:
    name: str
    start_idx: int
    end_idx: int  # exclusive
    items: list[Item] = field(default_factory=list)
    has_sentinel: bool = False

    @property
    def checked(self) -> list[Item]:
        return [i for i in self.items if i.kind == "checked"]

    @property
    def unchecked(self) -> list[Item]:
        return [i for i in self.items if i.kind == "unchecked"]

    @property
    def strikethrough(self) -> list[Item]:
        return [i for i in self.items if i.kind == "strikethrough"]

    @property
    def narrative(self) -> list[Item]:
        """Plain bullets and prose paragraphs — narrative bloat outside the checklist model."""
        return [i for i in self.items if i.kind in ("plain_bullet", "paragraph")]

    @property
    def qualifies(self) -> bool:
        if self.has_sentinel:
            return False
        if len(self.checked) >= THRESHOLD:
            return True
        if self.name == NARRATIVE_SECTION and len(self.checked) + len(self.narrative) >= THRESHOLD:
            return True
        return False


def classify_line(line: str) -> str:
    if RE_CHECKED.match(line):
        return "checked"
    if RE_UNCHECKED.match(line):
        return "unchecked"
    if RE_STRIKETHROUGH.match(line):
        return "strikethrough"
    if RE_PLAIN_BULLET.match(line):
        return "plain_bullet"
    stripped = line.strip()
    if stripped and not stripped.startswith("|") and not stripped.startswith("#"):
        return "paragraph"
    return "other"


def parse_sections(lines: list[str]) -> list[Section]:
    """Split CLAUDE.md lines into sections by ## headings."""
    sections: list[Section] = []
    current: Section | None = None

    for idx, line in enumerate(lines):
        m = RE_HEADING.match(line)
        if m:
            if current is not None:
                current.end_idx = idx
                sections.append(current)
            current = Section(name=m.group(1).strip(), start_idx=idx, end_idx=len(lines))
            continue

        if current is not None:
            kind = classify_line(line)
            current.items.append(Item(line_idx=idx, text=line, kind=kind))
            if SENTINEL in line:
                current.has_sentinel = True

    if current is not None:
        current.end_idx = len(lines)
        sections.append(current)

    return sections


def parse_reference_table(text: str) -> tuple[bool, bool, int]:
    """Check if Reference Files table exists and if archive is listed.

    Returns (has_table, archive_listed, last_row_line_idx).
    last_row_line_idx is the line index of the last table row (for insertion).
    """
    in_section = False
    found_header = False
    skipped_separator = False
    archive_listed = False
    last_row_idx = -1

    for idx, line in enumerate(text.splitlines()):
        if re.match(r"^##\s+Reference Files", line, re.IGNORECASE):
            in_section = True
            continue

        if in_section and not found_header:
            if "|" in line and "File" in line:
                found_header = True
            elif line.startswith("## "):
                return False, False, -1
            continue

        if found_header and not skipped_separator:
            if re.match(r"^\|[-\s|]+\|$", line):
                skipped_separator = True
            continue

        if skipped_separator:
            if not line.strip() or line.startswith("## "):
                break
            if ARCHIVE_FILENAME in line:
                archive_listed = True
            last_row_idx = idx

    return (found_header and skipped_separator), archive_listed, last_row_idx


def build_archive_block(section: Section, today: str) -> str:
    """Build the archive markdown block for a section."""
    checked = section.checked
    to_archive = checked[:-KEEP_RECENT]
    strikethroughs = section.strikethrough
    narrative = section.narrative if section.name == NARRATIVE_SECTION else []

    total_archived = len(to_archive) + len(strikethroughs) + len(narrative)
    lines = [
        f"## {section.name} (archived {today})",
        "",
        f"{total_archived} items archived from CLAUDE.md.",
        "",
    ]
    for item in to_archive:
        lines.append(item.text)
    if strikethroughs:
        for item in strikethroughs:
            lines.append(item.text)
    if narrative:
        for item in narrative:
            lines.append(item.text)
    lines.append("")
    return "\n".join(lines)


def build_replacement(section: Section, today: str) -> list[str]:
    """Build replacement lines for a consolidated section (excluding the ## heading)."""
    checked = section.checked
    to_archive_set = set()
    if len(checked) > KEEP_RECENT:
        for item in checked[:-KEEP_RECENT]:
            to_archive_set.add(item.line_idx)
    for item in section.strikethrough:
        to_archive_set.add(item.line_idx)
    if section.name == NARRATIVE_SECTION:
        for item in section.narrative:
            to_archive_set.add(item.line_idx)

    archived_count = len(to_archive_set)
    pointer = f"_Earlier items consolidated in `{ARCHIVE_FILENAME}` ({archived_count} items, {today})._"

    result = ["", pointer, ""]

    kept_items = [item for item in section.items if item.line_idx not in to_archive_set]
    # Strip leading blank lines from kept items (the pointer line already adds spacing)
    while kept_items and kept_items[0].kind == "other" and not kept_items[0].text.strip():
        kept_items.pop(0)
    for item in kept_items:
        result.append(item.text)

    # Trim trailing blank lines, keep one
    while len(result) > 1 and result[-1] == "":
        result.pop()
    result.append("")

    return result


def consolidate(project_dir: Path, dry_run: bool = False) -> dict[str, Any]:
    claude_md = project_dir / "CLAUDE.md"
    if not claude_md.is_file():
        return {
            "status": "error",
            "error": f"No CLAUDE.md found in {project_dir}",
        }

    text = claude_md.read_text()
    lines = text.splitlines()
    sections = parse_sections(lines)
    qualifying = [s for s in sections if s.qualifies]

    project_name = project_dir.name
    over_threshold = len(lines) > FILE_LINE_THRESHOLD

    if not qualifying:
        if over_threshold:
            return {
                "status": "over_threshold_no_sections",
                "project": project_name,
                "claude_md_lines": len(lines),
                "line_threshold": FILE_LINE_THRESHOLD,
                "error": (
                    f"CLAUDE.md is {len(lines)} lines (over the {FILE_LINE_THRESHOLD}-line "
                    "threshold) but no section has enough archivable items to consolidate "
                    "automatically. Trim narrative by hand or move it to a detail file."
                ),
                "sections": [],
            }
        total_checked = sum(len(s.checked) for s in sections)
        return {
            "status": "already_lean",
            "project": project_name,
            "claude_md_lines": len(lines),
            "error": f"Nothing to consolidate ({total_checked} completed items across all sections, none exceeding threshold of {THRESHOLD}).",
            "sections": [],
        }

    section_info = []
    for s in qualifying:
        narrative_count = len(s.narrative) if s.name == NARRATIVE_SECTION else 0
        to_archive = max(0, len(s.checked) - KEEP_RECENT) + len(s.strikethrough) + narrative_count
        section_info.append({
            "name": s.name,
            "checked": len(s.checked),
            "to_archive": to_archive,
            "to_keep": min(len(s.checked), KEEP_RECENT),
            "unchecked": len(s.unchecked),
            "strikethrough": len(s.strikethrough),
        })

    if dry_run:
        # Cosmetic display path — fall back to the absolute path if the project
        # dir isn't nested under a workspace root the way we expect.
        try:
            display_path = str(claude_md.relative_to(project_dir.parent.parent))
        except ValueError:
            display_path = str(claude_md)
        return {
            "status": "needs_consolidation",
            "project": project_name,
            "claude_md_path": display_path,
            "claude_md_lines": len(lines),
            "over_line_threshold": over_threshold,
            "line_threshold": FILE_LINE_THRESHOLD,
            "sections": section_info,
        }

    today = date.today().isoformat()

    # Build archive content
    archive_blocks = []
    for s in qualifying:
        archive_blocks.append(build_archive_block(s, today))

    # Write or append archive file
    archive_path = project_dir / ARCHIVE_FILENAME
    if archive_path.is_file():
        existing = archive_path.read_text()
        # Check for duplicate sections (same name + same date)
        new_blocks = []
        for block in archive_blocks:
            header_line = block.splitlines()[0]
            if header_line in existing:
                continue
            new_blocks.append(block)
        if new_blocks:
            with archive_path.open("a") as f:
                f.write("\n" + "\n".join(new_blocks))
        archive_action = "appended"
    else:
        header = (
            "# Progress Archive\n"
            "\n"
            "_Completed checklist items archived from CLAUDE.md by\n"
            "`/workspace:consolidate-project`. Items grouped by source section,\n"
            "ordered chronologically (oldest first)._\n"
            "\n"
        )
        archive_path.write_text(header + "\n".join(archive_blocks))
        archive_action = "created"

    # Rebuild CLAUDE.md with consolidated sections
    new_lines: list[str] = []
    qualifying_by_start = {s.start_idx: s for s in qualifying}
    skip_until: int | None = None

    for idx, line in enumerate(lines):
        if skip_until is not None and idx < skip_until:
            continue
        skip_until = None

        if idx in qualifying_by_start:
            section = qualifying_by_start[idx]
            new_lines.append(line)  # keep the ## heading
            replacement = build_replacement(section, today)
            new_lines.extend(replacement)
            skip_until = section.end_idx
        else:
            new_lines.append(line)

    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    # Update Reference Files table
    has_table, archive_listed, last_row_idx = parse_reference_table(new_text)
    ref_updated = False
    if has_table and not archive_listed and last_row_idx >= 0:
        ref_line = f"| `{ARCHIVE_FILENAME}` | Archived completed checklist items |"
        ref_lines = new_text.splitlines()
        ref_lines.insert(last_row_idx + 1, ref_line)
        new_text = "\n".join(ref_lines)
        if not new_text.endswith("\n"):
            new_text += "\n"
        ref_updated = True

    claude_md.write_text(new_text)

    return {
        "status": "consolidated",
        "project": project_name,
        "sections": [
            {"name": si["name"], "archived": si["to_archive"], "kept": si["to_keep"]}
            for si in section_info
        ],
        "claude_md_before": len(lines),
        "claude_md_after": len(new_text.splitlines()),
        "over_line_threshold": over_threshold,
        "line_threshold": FILE_LINE_THRESHOLD,
        "archive_file": ARCHIVE_FILENAME,
        "archive_action": archive_action,
        "reference_table_updated": ref_updated,
    }


def main():
    args = sys.argv[1:]
    dry_run = False
    if "--dry-run" in args:
        dry_run = True
        args.remove("--dry-run")

    if not args:
        print(json.dumps({
            "status": "error",
            "error": "Usage: consolidate-project.py [--dry-run] <project-name>",
        }))
        sys.exit(0)

    project_name = args[0]
    root = workspace_lib.resolve_workspace_root()
    if root is None:
        print(json.dumps({
            "status": "error",
            "error": "Could not determine the workspace root. Set WORKSPACE_ROOT "
                       "or run inside a workspace (a directory containing dev-env.yaml).",
        }))
        sys.exit(0)
    project_dir = root / "projects" / project_name

    if not project_dir.is_dir():
        print(json.dumps({
            "status": "error",
            "error": f"Project directory not found: {project_dir}",
        }))
        sys.exit(0)

    result = consolidate(project_dir, dry_run=dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
