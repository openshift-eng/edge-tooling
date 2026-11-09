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
import subprocess
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


# ─── Project frontmatter ────────────────────────────────────────────────────
# read_frontmatter_lines() and set_frontmatter_field() are yaml-free (the
# SessionStart hook uses them). parse_frontmatter() imports PyYAML lazily, so
# importing this module never needs it.

PYYAML_MISSING = (
    "PyYAML is required to parse project frontmatter and dev-env.yaml. "
    "Tell the user to install it with: pip3 install pyyaml"
)


def read_frontmatter_lines(path: Path) -> list[str] | None:
    """Lines between the opening and closing '---', or None if absent."""
    try:
        lines = path.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return lines[1:i]
    return None


def parse_frontmatter_flat(path: Path) -> dict[str, str]:
    """Top-level ``key: value`` pairs as strings. yaml-free (hook-safe)."""
    fm = read_frontmatter_lines(path)
    if fm is None:
        return {}
    result: dict[str, str] = {}
    for line in fm:
        if line[:1] not in (" ", "\t", "-", "#") and ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def parse_frontmatter(path: Path) -> dict:
    """YAML frontmatter as a dict ({} if absent or invalid).

    Dates are coerced to strings so the result is JSON-serializable.
    Raises ImportError when PyYAML is missing.
    """
    import datetime

    import yaml

    fm = read_frontmatter_lines(path)
    if fm is None:
        return {}
    try:
        result = yaml.safe_load("\n".join(fm))
    except yaml.YAMLError:
        return {}
    if not isinstance(result, dict):
        return {}
    for k, v in result.items():
        if isinstance(v, (datetime.date, datetime.datetime)):
            result[k] = str(v)
    return result


def set_frontmatter_field(text: str, key: str, value: str | None,
                          after: str | None = None) -> str:
    """Set, add, or remove a top-level frontmatter field in ``text``.

    ``value`` is the raw YAML text after ``key:`` (e.g. ``done`` or ``[]``);
    ``None`` removes the field. A field's indented continuation lines (block
    lists, maps) are replaced with it. A new field goes after the ``after``
    field when present, else at the end of the frontmatter. Text without
    frontmatter is returned unchanged.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"), None)
    if end is None:
        return text

    def field_span(name: str) -> tuple[int, int] | None:
        for i in range(1, end):
            if lines[i].startswith(f"{name}:"):
                j = i + 1
                while j < end and (lines[j][:1] in (" ", "\t") or
                                   lines[j].startswith("- ")):
                    j += 1
                return i, j
        return None

    new = [] if value is None else [f"{key}: {value}\n" if value != "" else f"{key}:\n"]
    span = field_span(key)
    if span is not None:
        lines[span[0]:span[1]] = new
    elif new:
        anchor = field_span(after) if after else None
        lines[anchor[1] if anchor else end:anchor[1] if anchor else end] = new
    return "".join(lines)


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file and os.replace."""
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ─── Worktrees ──────────────────────────────────────────────────────────────


def project_worktrees(fm: dict, root: Path) -> list[dict[str, str]]:
    """Worktrees a project declares, as [{repo, branch, path}] (absolute paths).

    Multi-repo projects list repos under ``worktrees:`` (strings, dicts, or a
    repo->branch map); self-repo projects set ``worktree_path:``.
    """
    branch = fm.get("branch") or ""
    if isinstance(branch, list):
        branch = branch[0] if branch else ""
    branch = str(branch)
    raw = fm.get("worktrees") or []
    if isinstance(raw, str):
        raw = [raw]
    items: list[dict[str, str]] = []
    if isinstance(raw, dict):
        raw = [{"repo": r, "branch": b} for r, b in raw.items()]
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                repo = str(item.get("repo", ""))
                b = str(item.get("branch", branch))
                path = item.get("path") or f"repos/{repo}/.worktrees/{b}"
            else:
                repo, b = str(item), branch
                path = f"repos/{repo}/.worktrees/{b}"
            items.append({"repo": repo, "branch": b, "path": str(root / path)})
    wt_path = fm.get("worktree_path")
    if wt_path:
        items.append({"repo": "(self)", "branch": branch,
                      "path": str(root / str(wt_path))})
    return items


def _git(path: str, *args: str) -> subprocess.CompletedProcess:

    return subprocess.run(["git", "-C", path, *args],
                          capture_output=True, text=True)


def worktree_status(wt: dict[str, str]) -> dict:
    """Git state of one worktree, with a ``needs_attention`` flag and text.

    ``unpushed`` counts commits on HEAD that no remote branch contains, so a
    branch with no upstream but no new commits does not need attention.
    """
    entry: dict = {**wt, "exists": False, "dirty_count": 0, "ahead": 0,
                   "no_upstream": False, "unpushed": 0, "error": None}
    path = wt["path"]
    if not Path(path).is_dir():
        entry.update(needs_attention=False, status_text="MISSING")
        return entry
    entry["exists"] = True
    st = _git(path, "status", "--porcelain")
    if st.returncode != 0:
        entry["error"] = st.stderr.strip() or "git status failed"
    else:
        entry["dirty_count"] = len([ln for ln in st.stdout.splitlines() if ln.strip()])
    if _git(path, "rev-parse", "--verify", "@{upstream}").returncode != 0:
        entry["no_upstream"] = True
    else:
        ahead = _git(path, "rev-list", "--count", "@{upstream}..HEAD")
        if ahead.returncode == 0 and ahead.stdout.strip().isdigit():
            entry["ahead"] = int(ahead.stdout.strip())
    unpushed = _git(path, "rev-list", "--count", "HEAD", "--not", "--remotes")
    if unpushed.returncode == 0 and unpushed.stdout.strip().isdigit():
        entry["unpushed"] = int(unpushed.stdout.strip())

    parts = []
    if entry["error"]:
        parts.append(f"ERROR: {entry['error']}")
    if entry["dirty_count"]:
        parts.append(f"dirty ({entry['dirty_count']} files)")
    if entry["ahead"]:
        parts.append(f"ahead by {entry['ahead']}")
    elif entry["no_upstream"] and entry["unpushed"]:
        parts.append(f"no upstream, {entry['unpushed']} unpushed commits")
    entry["needs_attention"] = bool(parts)
    entry["status_text"] = ", ".join(parts) or "clean"
    return entry
