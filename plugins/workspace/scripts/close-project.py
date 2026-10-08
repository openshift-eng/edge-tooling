#!/usr/bin/env python3
"""Inspect and close a project workspace (shared by /workspace:close-project
and the Claude Code mod UI).

Usage:
  close-project.py check <name>
  close-project.py apply <name> [--notes TEXT] [--worktrees remove|keep|discard]

``check`` reports the project's worktrees (dirty / ahead / no-upstream) and
linked skills so a caller can decide what to ask. ``apply`` performs the
close: removes worktrees and branches according to ``--worktrees``, unlinks
repo skills no other active project uses, edits the project CLAUDE.md
frontmatter, appends a ``## Closing Notes`` section, and clears any handoff
marker for the project. The project directory itself is never deleted, and a
re-run is safe.

Yaml-free: frontmatter is edited line by line so unrelated keys, ordering and
comments are left untouched. Output is JSON on stdout; handled errors still
exit 0 with a ``status`` field.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import workspace_lib

SELF_REPO = "(self)"
NOTES_SKIP = {"", "no", "none", "n", "skip"}
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:\s+(.*))?$")


def emit(payload: dict) -> int:
    print(json.dumps(payload, indent=2))
    return 0


def fail(message: str, status: str = "error") -> int:
    return emit({"status": status, "error_message": message})


def git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["git"] + (["-C", str(cwd)] if cwd else []) + args
    return subprocess.run(cmd, capture_output=True, text=True)


# --------------------------------------------------------------------------
# Frontmatter (line-oriented, yaml-free)
# --------------------------------------------------------------------------

def split_frontmatter(lines: list[str]) -> tuple[int, int] | None:
    """Return (start, end) indexes of the frontmatter body (between the ---)."""
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return 1, i
    return None


def key_spans(lines: list[str], start: int, end: int) -> dict[str, tuple[int, int]]:
    """Map top-level key -> (first_line, last_line_exclusive) within [start, end).

    A key's block is its line plus following lines that are not themselves a
    top-level key (indented values, ``- item`` lines). Trailing blank/comment
    lines belong to nobody so removing a block never eats them.
    """
    spans: dict[str, tuple[int, int]] = {}
    i = start
    while i < end:
        m = KEY_RE.match(lines[i].rstrip("\n"))
        if not m:
            i += 1
            continue
        j = i + 1
        while j < end and not KEY_RE.match(lines[j].rstrip("\n")):
            j += 1
        k = j
        while k > i + 1 and (not lines[k - 1].strip()
                             or lines[k - 1].lstrip().startswith("#")):
            k -= 1
        spans[m.group(1)] = (i, k)
        i = j
    return spans


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def scalar(lines: list[str], spans: dict, key: str) -> str:
    if key not in spans:
        return ""
    m = KEY_RE.match(lines[spans[key][0]].rstrip("\n"))
    return unquote(m.group(2) or "") if m else ""


def parse_items(block: list[str]) -> list[list[str]]:
    """Group a key's continuation lines into ``- item`` groups."""
    items: list[list[str]] = []
    for line in block:
        if line.lstrip().startswith("- "):
            items.append([line])
        elif items and line.strip():
            items[-1].append(line)
    return items


def item_fields(item: list[str]) -> dict[str, str]:
    """Fields of one list item: scalar ``- x`` -> {"_": x}; dict -> key/values."""
    first = item[0].lstrip()[2:].strip()
    fields: dict[str, str] = {}
    if first and not re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:(\s|$)", first):
        fields["_"] = unquote(first)
        return fields
    for raw in [first] + [ln.strip() for ln in item[1:]]:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", raw)
        if m:
            fields[m.group(1)] = unquote(m.group(2))
    return fields


def block_lines(lines: list[str], spans: dict, key: str) -> list[str]:
    s, e = spans[key]
    return lines[s + 1:e]


def inline_list(value: str) -> list[str] | None:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [unquote(p) for p in inner.split(",") if p.strip()] if inner else []
    return None


def remove_key(lines: list[str], key: str) -> list[str]:
    fm = split_frontmatter(lines)
    if not fm:
        return lines
    spans = key_spans(lines, *fm)
    if key not in spans:
        return lines
    s, e = spans[key]
    return lines[:s] + lines[e:]


def set_key(lines: list[str], key: str, rendered: list[str],
            after: str | None = None) -> list[str]:
    """Replace a key's block with ``rendered`` lines, or insert it."""
    fm = split_frontmatter(lines)
    if not fm:
        return lines
    spans = key_spans(lines, *fm)
    if key in spans:
        s, e = spans[key]
        return lines[:s] + rendered + lines[e:]
    if after and after in spans:
        at = spans[after][1]
    else:
        at = fm[1]
    return lines[:at] + rendered + lines[at:]


# --------------------------------------------------------------------------
# Project model
# --------------------------------------------------------------------------

def valid_name(name: str) -> bool:
    return bool(name) and Path(name).name == name and name not in (".", "..") \
        and not name.startswith("-")


class Project:
    def __init__(self, root: Path, name: str):
        self.root = root
        self.name = name
        self.dir = root / "projects" / name
        self.claude_md = self.dir / "CLAUDE.md"
        self.text = self.claude_md.read_text()
        self.lines = self.text.splitlines(keepends=True)
        fm = split_frontmatter(self.lines)
        self.spans = key_spans(self.lines, *fm) if fm else {}

    def get(self, key: str) -> str:
        return scalar(self.lines, self.spans, key)

    @property
    def status(self) -> str:
        return self.get("status")

    @property
    def branch(self) -> str:
        return self.get("branch")

    def worktrees(self) -> list[dict]:
        """Declared worktrees: [{repo, branch, path (abs), checkout (abs)}]."""
        result: list[dict] = []
        wt_path = self.get("worktree_path")
        if wt_path:
            path = Path(wt_path)
            if not path.is_absolute():
                path = self.root / path
            result.append({"repo": SELF_REPO, "branch": self.branch,
                           "path": path, "checkout": self.root})
        if "worktrees" not in self.spans:
            return result

        raw = self.get("worktrees")
        block = block_lines(self.lines, self.spans, "worktrees")
        entries: list[dict[str, str]] = []
        flow = inline_list(raw)
        if flow is not None:
            entries = [{"_": r} for r in flow]
        elif raw:
            entries = [{"_": raw}]
        for item in parse_items(block):
            entries.append(item_fields(item))
        if not entries:
            # map form: "  repo: branch"
            for line in block:
                m = re.match(r"^\s+([^\s:#][^:]*):\s*(\S.*)$", line)
                if m:
                    entries.append({"repo": m.group(1).strip(),
                                    "branch": unquote(m.group(2))})

        for e in entries:
            repo = e.get("repo") or e.get("_", "")
            if not valid_name(repo):
                continue
            branch = e.get("branch") or self.branch
            path = Path(e["path"]) if e.get("path") else \
                Path("repos") / repo / ".worktrees" / branch
            if not path.is_absolute():
                path = self.root / path
            result.append({"repo": repo, "branch": branch, "path": path,
                           "checkout": self.root / "repos" / repo})
        return result

    def skills(self) -> list[dict]:
        if "skills" not in self.spans:
            return []
        out = []
        for item in parse_items(block_lines(self.lines, self.spans, "skills")):
            f = item_fields(item)
            name = f.get("name") or f.get("_")
            if name:
                out.append({"name": name, "source": f.get("source", "")})
        return out


# --------------------------------------------------------------------------
# Worktree inspection
# --------------------------------------------------------------------------

def is_pr_branch(branch: str) -> bool:
    return bool(re.match(r"^pr/\d+$", branch))


def inspect_worktree(wt: dict) -> dict:
    path: Path = wt["path"]
    info = {"repo": wt["repo"], "path": str(path), "branch": wt["branch"],
            "exists": path.is_dir(), "dirty": False, "dirty_files": 0,
            "ahead": 0, "no_upstream": False}
    if not info["exists"]:
        return info
    st = git(["status", "--porcelain"], path)
    if st.returncode == 0:
        files = [ln for ln in st.stdout.splitlines() if ln.strip()]
        info["dirty"] = bool(files)
        info["dirty_files"] = len(files)
    up = git(["rev-parse", "--verify", "@{upstream}"], path)
    if up.returncode != 0:
        info["no_upstream"] = True
        if is_pr_branch(wt["branch"]):
            # pr/<n> has no upstream; count commits that exist on no remote.
            local = git(["rev-list", "--count", "HEAD", "--not", "--remotes"], path)
            if local.returncode == 0 and local.stdout.strip().isdigit():
                info["ahead"] = int(local.stdout.strip())
    else:
        ahead = git(["rev-list", "--count", "@{upstream}..HEAD"], path)
        if ahead.returncode == 0 and ahead.stdout.strip().isdigit():
            info["ahead"] = int(ahead.stdout.strip())
    return info


def needs_attention(info: dict) -> bool:
    """Would removing this worktree risk losing work?

    ``pr/<n>`` checkouts have no upstream by construction (local refs fetched
    from the PR head), so a missing upstream alone does not count for them.
    """
    if info["dirty"] or info["ahead"] > 0:
        return True
    return info["no_upstream"] and not is_pr_branch(info["branch"])


def skill_usage(root: Path, name: str) -> tuple[list[dict] | None, str]:
    """Run skills.py unlink-check; return (entries, error)."""
    script = Path(__file__).resolve().parent / "skills.py"
    res = subprocess.run(
        [sys.executable, str(script), "unlink-check", name],
        capture_output=True, text=True,
        env={**os.environ, "WORKSPACE_ROOT": str(root)})
    try:
        data = json.loads(res.stdout)
    except ValueError:
        return None, f"skills.py unlink-check failed: {res.stderr.strip()[:200]}"
    if data.get("status") != "ok":
        return None, str(data.get("error") or data.get("message") or "unknown error")
    return data.get("skills", []), ""


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def load(root: Path | None, name: str):
    if root is None:
        return None, fail("Could not determine the workspace root. Set "
                          "WORKSPACE_ROOT or run inside a workspace.")
    if not valid_name(name) or not (root / "projects" / name / "CLAUDE.md").is_file():
        return None, fail(f"Project '{name}' not found", status="not_found")
    try:
        return Project(root, name), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, fail(f"Cannot read project '{name}': {exc}")


def cmd_check(args: argparse.Namespace) -> int:
    project, err = load(workspace_lib.resolve_workspace_root(), args.name)
    if err is not None:
        return err
    errors: list[str] = []
    worktrees = [inspect_worktree(w) for w in project.worktrees()]

    skills = []
    declared = project.skills()
    if declared:
        usage, problem = skill_usage(project.root, project.name)
        if usage is None:
            errors.append(problem)
            usage = []
        by_name = {u["name"]: u for u in usage}
        for s in declared:
            used_by = by_name.get(s["name"], {}).get("used_by", [])
            skills.append({"name": s["name"], "source": s["source"],
                           "shared": bool(used_by), "used_by": used_by})

    payload = {
        "status": "ok",
        "project": {"name": project.name, "status": project.status,
                    "branch": project.branch},
        "already_done": project.status == "done",
        "worktrees": worktrees,
        "skills": skills,
    }
    if errors:
        payload["errors"] = errors
    return emit(payload)


def remove_worktree(info: dict, wt: dict, force: bool, errors: list[str],
                    removed: list[dict], kept: list[dict]) -> bool:
    """Remove one worktree and its branch. True when the worktree is gone."""
    path: Path = wt["path"]
    checkout: Path = wt["checkout"]
    repo, branch = wt["repo"], wt["branch"]

    if info["exists"]:
        if not checkout.is_dir():
            errors.append(f"{repo}: repo checkout {checkout} not found; "
                          f"worktree {path} left in place")
            kept.append({"kind": "worktree", "name": repo, "path": str(path),
                         "reason": "repo checkout missing"})
            return False
        cmd = ["worktree", "remove"] + (["--force"] if force else []) \
            + ["--", str(path)]
        res = git(cmd, checkout)
        if res.returncode != 0:
            errors.append(f"{repo}: git worktree remove failed: "
                          f"{res.stderr.strip()}")
            kept.append({"kind": "worktree", "name": repo, "path": str(path),
                         "reason": "git worktree remove failed"})
            return False
        removed.append({"kind": "worktree", "name": repo, "path": str(path)})
    elif checkout.is_dir():
        git(["worktree", "prune"], checkout)

    if not branch or not checkout.is_dir():
        return True
    if git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
           checkout).returncode != 0:
        return True
    # pr/<n> are throwaway local refs; discarding also drops unmerged commits.
    flag = "-D" if (is_pr_branch(branch) or force) else "-d"
    res = git(["branch", flag, "--", branch], checkout)
    if res.returncode != 0:
        errors.append(f"{repo}: could not delete branch {branch}: "
                      f"{res.stderr.strip()}")
        kept.append({"kind": "branch", "name": branch, "path": str(checkout),
                     "reason": "branch delete failed"})
    else:
        removed.append({"kind": "branch", "name": branch, "path": str(checkout)})
    return True


def unlink_skills(project: Project, errors: list[str], removed: list[dict],
                  kept: list[dict]) -> bool:
    """Unlink unshared skill symlinks. True when the skills list can be cleared."""
    declared = project.skills()
    if not declared:
        return False
    usage, problem = skill_usage(project.root, project.name)
    if usage is None:
        errors.append(f"skills not unlinked: {problem}")
        for s in declared:
            kept.append({"kind": "skill", "name": s["name"],
                         "reason": "unlink-check failed"})
        return False
    for u in usage:
        name = u["name"]
        entry = project.root / ".claude" / "skills" / name
        if u.get("missing"):
            continue
        if not valid_name(name):
            continue
        if u.get("used_by"):
            kept.append({"kind": "skill", "name": name,
                         "reason": f"still used by {', '.join(u['used_by'])}"})
        elif not u.get("is_symlink"):
            kept.append({"kind": "skill", "name": name,
                         "reason": ".claude/skills entry is not a symlink"})
        elif u.get("removable"):
            try:
                entry.unlink()
                removed.append({"kind": "skill", "name": name,
                                "path": str(entry)})
            except OSError as exc:
                errors.append(f"could not unlink skill {name}: {exc}")
                kept.append({"kind": "skill", "name": name,
                             "reason": "unlink failed"})
    return True


def closing_notes_section(notes: str, today: str) -> list[str]:
    return ["## Closing Notes\n", "\n", f"_Closed {today}_\n", "\n",
            notes.rstrip() + "\n"]


def apply_notes(lines: list[str], notes: str, today: str) -> list[str]:
    section = closing_notes_section(notes, today)
    start = next((i for i, ln in enumerate(lines)
                  if ln.rstrip() == "## Closing Notes"), None)
    if start is None:
        if lines and not lines[-1].endswith("\n"):
            lines = lines[:-1] + [lines[-1] + "\n"]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        return lines + ["\n"] + section
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    tail = lines[end:]
    return lines[:start] + section + (["\n"] + tail if tail else [])


def rewrite_worktrees(project: Project, lines: list[str],
                      gone: set[str]) -> list[str]:
    """Drop removed repos from ``worktrees:``; ``[]`` when none remain."""
    spans = key_spans(lines, *split_frontmatter(lines))
    if "worktrees" not in spans:
        return lines
    s, e = spans["worktrees"]
    block = lines[s + 1:e]
    items = parse_items(block)
    keep_items = [it for it in items
                  if (item_fields(it).get("repo") or item_fields(it).get("_"))
                  not in gone]
    if items and len(keep_items) == len(items):
        return lines
    if not items:
        # flow / map / scalar forms: only rewrite when everything is gone
        remaining = [w for w in project.worktrees()
                     if w["repo"] != SELF_REPO and w["repo"] not in gone]
        if remaining:
            return lines
        return lines[:s] + ["worktrees: []\n"] + lines[e:]
    if not keep_items:
        return lines[:s] + ["worktrees: []\n"] + lines[e:]
    flat = [ln for it in keep_items for ln in it]
    return lines[:s + 1] + flat + lines[e:]


def cmd_apply(args: argparse.Namespace) -> int:
    project, err = load(workspace_lib.resolve_workspace_root(), args.name)
    if err is not None:
        return err
    mode = args.worktrees
    notes = (args.notes or "").strip()
    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    was_done = project.status == "done"

    errors: list[str] = []
    removed: list[dict] = []
    kept: list[dict] = []

    gone_repos: set[str] = set()
    self_gone = False
    for wt in project.worktrees():
        info = inspect_worktree(wt)
        is_self = wt["repo"] == SELF_REPO
        if mode == "keep":
            if info["exists"]:
                kept.append({"kind": "worktree", "name": wt["repo"],
                             "path": str(wt["path"]), "reason": "keep requested"})
            else:
                # already gone on disk: nothing to keep; drop the stale
                # reference but leave the branch alone
                if wt["checkout"].is_dir():
                    git(["worktree", "prune"], wt["checkout"])
                gone_repos.add(wt["repo"])
                self_gone = self_gone or is_self
            continue
        if info["exists"] and needs_attention(info) and mode != "discard":
            reason = "dirty" if info["dirty"] else \
                "unpushed commits" if info["ahead"] else "no upstream"
            kept.append({"kind": "worktree", "name": wt["repo"],
                         "path": str(wt["path"]),
                         "reason": f"{reason}; use discard to remove"})
            continue
        if remove_worktree(info, wt, mode == "discard", errors, removed, kept):
            gone_repos.add(wt["repo"])
            self_gone = self_gone or is_self

    skills_clear = unlink_skills(project, errors, removed, kept)

    lines = list(project.lines)
    lines = set_key(lines, "status", ["status: done\n"], after="project")
    if not was_done or notes.lower() not in NOTES_SKIP:
        lines = set_key(lines, "closed", [f"closed: {today}\n"], after="status")
    lines = set_key(lines, "last-active",
                    [f"last-active: {now.strftime('%Y-%m-%dT%H:%M')}\n"],
                    after="closed")
    if gone_repos - {SELF_REPO}:
        lines = rewrite_worktrees(project, lines, gone_repos)
    if self_gone:
        lines = remove_key(lines, "worktree_path")
    if skills_clear:
        lines = set_key(lines, "skills", ["skills: []\n"])
    if notes.lower() not in NOTES_SKIP:
        lines = apply_notes(lines, notes, today)

    try:
        project.claude_md.write_text("".join(lines))
    except OSError as exc:
        return fail(f"Could not write {project.claude_md}: {exc}")

    handoff = Path(__file__).resolve().parent / "handoff.py"
    res = subprocess.run(
        [sys.executable, str(handoff), "clear", "--project", project.name],
        capture_output=True, text=True,
        env={**os.environ, "WORKSPACE_ROOT": str(project.root)})
    if res.returncode != 0:
        errors.append(f"handoff clear failed: {res.stderr.strip()[:200]}")

    return emit({"status": "ok", "closed": True, "project": project.name,
                 "removed": removed, "kept": kept, "errors": errors})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="Report worktrees and skills")
    check.add_argument("name")
    apply = sub.add_parser("apply", help="Close the project")
    apply.add_argument("name")
    apply.add_argument("--notes", default="")
    apply.add_argument("--worktrees", choices=["remove", "keep", "discard"],
                       default="keep")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return cmd_check(args) if args.command == "check" else cmd_apply(args)


if __name__ == "__main__":
    sys.exit(main())
