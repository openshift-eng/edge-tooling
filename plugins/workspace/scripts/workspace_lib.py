#!/usr/bin/env python3
"""Shared helpers for the workspace plugin's python scripts.

Deliberately minimal and **yaml-free** so the SessionStart hook
(recent-projects.py) never needs a third-party dependency. Imported by
resume-project.py, consolidate-project.py, and recent-projects.py via a
plain ``import workspace_lib`` — python3 prepends the running script's own
directory to sys.path, so the sibling import resolves identically whether
the plugin is installed, loaded with ``--plugin-dir``, invoked by the hook,
or run as a subprocess.

Two roots are kept distinct:

* **Plugin root** — where this file (and the rest of the plugin) ships.
  Read-only; derived from this file's own location.
* **Workspace root** — the user-chosen directory holding ``dev-env.yaml``,
  ``repos/``, ``projects/``, and workspace-local ``domains/``. Resolved at
  runtime; never derived from ``__file__`` (that would point into the
  plugin).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

# This library ships inside the plugin, so its own location IS the plugin
# root (scripts/ is one level below the plugin root).
PLUGIN_ROOT = Path(__file__).resolve().parent.parent

DEV_ENV_YAML = "dev-env.yaml"


def _walk_up_for_marker(start: Path, marker: str) -> Path | None:
    """Walk up from ``start`` (inclusive) looking for a dir containing ``marker``."""
    try:
        current = start.resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if (candidate / marker).is_file():
            return candidate
    return None


def resolve_workspace_root() -> Path | None:
    """Resolve the workspace root, or None if it cannot be determined.

    Resolution order (matches setup.sh):
      1. ``WORKSPACE_ROOT`` env var (explicit override).
      2. ``CLAUDE_PROJECT_DIR`` env var (set by Claude Code to the launch dir).
      3. Walk up from the current working directory to the nearest ancestor
         containing ``dev-env.yaml``.
      4. ``None``.

    There is intentionally **no** ``__file__`` fallback: this file lives in
    the plugin, not the workspace.
    """
    env_ws = os.environ.get("WORKSPACE_ROOT")
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        return Path(project_dir).expanduser().resolve()

    found = _walk_up_for_marker(Path.cwd(), DEV_ENV_YAML)
    if found is not None:
        return found

    return None


# ---------------------------------------------------------------------------
# Project CLAUDE.md helpers (frontmatter + checklist). Yaml-free.
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = {"done", "complete", "closed"}


def parse_frontmatter(claude_md: Path) -> dict[str, str]:
    """Extract YAML frontmatter as a flat key-value dict.

    Only parses if line 1 is exactly '---' and a closing '---' exists.
    Never raises: unreadable/odd files yield {}.
    """
    try:
        lines = claude_md.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return {}

    if not lines or lines[0].strip() != "---":
        return {}

    result = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    else:
        return {}

    return result


RE_CHECKLIST_HEADING = re.compile(r"^(#{2,3})\s+(.+)")
RE_CHECKLIST_ITEM = re.compile(r"^(\s*- \[)([ xX])(\] )(.+)$")


def _eol_split(line: str) -> tuple[str, str]:
    """Split a line into (content, line ending)."""
    stripped = line.rstrip("\r\n")
    return stripped, line[len(stripped):]


def parse_checklist(text: str) -> list[dict]:
    """Parse the checklist of a project CLAUDE.md.

    Returns sections in file order. Items before any ``##``/``###`` heading
    land in a leading section with heading "" and level 0. Frontmatter and
    fenced code blocks are ignored. Each section is
    ``{"heading", "level", "line", "items"}`` where ``line`` is the heading's
    line index (-1 for the leading section) and each item is
    ``{"line", "text", "checked", "occurrence"}`` (``occurrence`` = 0-based
    index among same-text items across sections sharing that heading).
    """
    lines = text.splitlines(keepends=True)
    start = 0
    if lines and _eol_split(lines[0])[0].strip() == "---":
        for i in range(1, len(lines)):
            if _eol_split(lines[i])[0].strip() == "---":
                start = i + 1
                break

    sections: list[dict] = [{"heading": "", "level": 0, "line": -1, "items": []}]
    seen: dict[tuple[str, str], int] = {}
    fence: str | None = None
    for idx in range(start, len(lines)):
        content = _eol_split(lines[idx])[0]
        marker = content.lstrip()[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is not None:
            continue
        m = RE_CHECKLIST_HEADING.match(content)
        if m:
            sections.append({"heading": m.group(2).strip(), "level": len(m.group(1)),
                             "line": idx, "items": []})
            continue
        m = RE_CHECKLIST_ITEM.match(content)
        if m:
            item_text = m.group(4).strip()
            # Count by heading text: edits target heading+text+occurrence, and
            # several sections may share a heading.
            key = (sections[-1]["heading"], item_text)
            occ = seen.get(key, 0)
            seen[key] = occ + 1
            sections[-1]["items"].append({
                "line": idx, "text": item_text,
                "checked": m.group(2) in "xX", "occurrence": occ,
            })
    return sections


def extract_checklist(text: str) -> dict:
    """Summarize the checklist: counts plus checked/unchecked items with section."""
    checked_items: list[dict] = []
    unchecked_items: list[dict] = []
    for sec in parse_checklist(text):
        for it in sec["items"]:
            entry = {"text": it["text"], "section": sec["heading"]}
            (checked_items if it["checked"] else unchecked_items).append(entry)
    return {
        "checked": len(checked_items),
        "unchecked": len(unchecked_items),
        "total": len(checked_items) + len(unchecked_items),
        "unchecked_items": unchecked_items,
        "checked_items": checked_items,
    }


def atomic_write_text(path: Path, text: str) -> None:
    """Write text verbatim (no newline translation) via temp file + os.replace."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(text)
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
