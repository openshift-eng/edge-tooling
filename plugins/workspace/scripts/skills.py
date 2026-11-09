#!/usr/bin/env python3
"""Manage workspace symlinks that surface repo-shipped skills.

Skills living in repos (repos/<repo>/.claude/skills/) are only discovered
by Claude Code inside that repo's directory. Flat-symlinking them into the
workspace's .claude/skills/ makes them visible in autocomplete at the
workspace root. `setup.sh clone` runs `link-all` once; the links are
workspace-wide, not owned by a project.

Usage:
  skills.py scan <repo> [<repo>...]
  skills.py link <name> <repo>
  skills.py link-all

Output: JSON to stdout. Always {"status": "ok" | "error", ...}; fatal
problems report status "error" and still exit 0 so calling skills can
parse the message.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Plugins cannot declare python dependencies. PyYAML is required to parse
# skill and project frontmatter — emit a self-describing JSON error (that
# the calling skill can relay) instead of a raw ImportError traceback.
try:
    import yaml  # noqa: F401  (fail fast with a JSON error)
except ImportError:
    print(json.dumps({
        "status": "error",
        "error": "PyYAML is required by skills.py (skill frontmatter is "
                 "YAML). Install with: pip3 install pyyaml",
    }))
    sys.exit(0)

import workspace_lib


class SkillError(Exception):
    pass


def fail(message: str) -> None:
    print(json.dumps({"status": "error", "error": message}))
    sys.exit(0)


def is_safe_name(name: str) -> bool:
    """True if `name` is safe to use as a single path component under
    .claude/skills/ — i.e. it cannot escape that directory.

    A repo controls its skills' frontmatter `name:`, so this must reject
    anything containing a path separator or a dot-segment ("." or "..")
    before it is ever joined onto a filesystem path.
    """
    if not name or not isinstance(name, str):
        return False
    if name in (".", ".."):
        return False
    return Path(name).name == name


def parse_frontmatter(path: Path) -> dict[str, Any]:
    return workspace_lib.parse_frontmatter(path)


def ws_skills_dir(root: Path) -> Path:
    return root / ".claude" / "skills"


def repo_skills_dir(root: Path, repo: str) -> Path:
    return root / "repos" / repo / ".claude" / "skills"


def iter_repo_skills(root: Path, repo: str):
    """Yield (skill_dir, frontmatter) for every SKILL.md dir in the repo."""
    skills_dir = repo_skills_dir(root, repo)
    if not skills_dir.is_dir():
        return
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.is_file():
            yield child, parse_frontmatter(skill_md)


def find_skill_dir(root: Path, repo: str, name: str) -> Path | None:
    """Repo skill dir whose frontmatter name equals `name`."""
    for skill_dir, fm in iter_repo_skills(root, repo):
        if fm.get("name") == name:
            return skill_dir
    return None


def classify_existing(entry: Path, target: Path) -> str:
    """How does the workspace entry relate to the intended target?

    Returns "absent", "same_source", "dangling", or "collision".
    """
    if not os.path.lexists(entry):
        return "absent"
    if entry.is_symlink():
        if not os.path.exists(entry):
            return "dangling"
        if entry.resolve() == target.resolve():
            return "same_source"
    return "collision"


def cmd_scan(root: Path, repos: list[str]) -> dict:
    skills: list[dict] = []
    errors: list[str] = []

    for repo in repos:
        if not (root / "repos" / repo).is_dir():
            errors.append(f"repo '{repo}' is not cloned under repos/")
            continue
        for skill_dir, fm in iter_repo_skills(root, repo):
            name = fm.get("name")
            if not name or not isinstance(name, str):
                errors.append(
                    f"{repo}: skill dir '{skill_dir.name}' has no usable "
                    f"frontmatter name: — skipped")
                continue
            if not is_safe_name(name):
                errors.append(
                    f"{repo}: skill dir '{skill_dir.name}' has an unsafe "
                    f"frontmatter name '{name}' — skipped")
                continue
            skills.append({
                "name": name,
                "description": str(fm.get("description", "")),
                "repo": repo,
                "dir": skill_dir.name,
                "path": str(skill_dir),
                "conflict": False,
                "already_present": None,
            })

    name_counts: dict[str, int] = {}
    for s in skills:
        name_counts[s["name"]] = name_counts.get(s["name"], 0) + 1
    for s in skills:
        s["conflict"] = name_counts[s["name"]] > 1

    for s in skills:
        entry = ws_skills_dir(root) / s["name"]
        state = classify_existing(entry, Path(s["path"]))
        if state == "same_source":
            s["already_present"] = {"status": "same_source"}
        elif state == "dangling":
            s["already_present"] = {"status": "dangling"}
        elif state == "collision":
            target = str(entry.resolve()) if os.path.exists(entry) else str(entry)
            s["already_present"] = {"status": "collision", "target": target}

    return {"status": "ok", "workspace": str(root),
            "skills": skills, "errors": errors}


def cmd_link(root: Path, name: str, repo: str) -> dict:
    if not is_safe_name(name):
        raise SkillError(f"skill name '{name}' is not a valid skill name")

    skill_dir = find_skill_dir(root, repo, name)
    if skill_dir is None:
        raise SkillError(f"No skill with frontmatter name '{name}' found in "
             f"repos/{repo}/.claude/skills/")

    skills_dir = ws_skills_dir(root)
    if skills_dir.exists() and not skills_dir.is_dir():
        raise SkillError(f"{skills_dir} exists and is not a directory — refusing to link")
    created_dir = not skills_dir.is_dir()
    skills_dir.mkdir(parents=True, exist_ok=True)

    entry = skills_dir / name
    state = classify_existing(entry, skill_dir)
    if state == "collision":
        raise SkillError(f"{entry} already exists and is not a symlink to this skill "
             f"— refusing to touch it")
    existed = state == "same_source"
    if state == "dangling":
        entry.unlink()

    target = os.path.relpath(skill_dir, skills_dir)
    if not existed:
        os.symlink(target, entry)

    return {
        "status": "ok",
        "linked": {"name": name, "repo": repo,
                   "path": str(entry), "target": os.readlink(entry)},
        "existed": existed,
        "created_dir": created_dir,
    }


def cmd_link_all(root: Path) -> dict:
    """Link every repo skill under repos/*/.claude/skills/ into the workspace.

    A name offered by more than one repo, or already taken at the workspace
    root by something else, is skipped and reported.
    """
    repos_dir = root / "repos"
    repos = sorted(d.name for d in repos_dir.iterdir()
                   if d.is_dir()) if repos_dir.is_dir() else []
    scan = cmd_scan(root, repos)
    linked, reused, skipped = [], [], []
    created_dir = False
    for s in scan["skills"]:
        state = (s["already_present"] or {}).get("status")
        if s["conflict"]:
            skipped.append({"name": s["name"], "repo": s["repo"],
                            "reason": "same name in more than one repo"})
        elif state == "collision":
            skipped.append({"name": s["name"], "repo": s["repo"],
                            "reason": "name already in use in .claude/skills/"})
        elif state == "same_source":
            reused.append({"name": s["name"], "repo": s["repo"]})
        else:
            try:
                result = cmd_link(root, s["name"], s["repo"])
            except SkillError as exc:
                skipped.append({"name": s["name"], "repo": s["repo"],
                                "reason": str(exc)})
                continue
            created_dir = created_dir or result["created_dir"]
            linked.append({"name": s["name"], "repo": s["repo"]})
    return {"status": "ok", "linked": linked, "reused": reused,
            "skipped": skipped, "errors": scan["errors"],
            "created_dir": created_dir}


def main():
    root = workspace_lib.resolve_workspace_root()
    if root is None:
        fail("Could not determine the workspace root. Set WORKSPACE_ROOT "
             "or run inside a workspace (a directory containing dev-env.yaml).")

    args = sys.argv[1:]
    if not args:
        fail("Usage: skills.py <scan|link|link-all> ...")
    command, rest = args[0], args[1:]

    if command == "scan":
        if not rest:
            fail("scan requires at least one repo name")
        result = cmd_scan(root, rest)
    elif command == "link":
        if len(rest) != 2:
            fail("Usage: skills.py link <name> <repo>")
        try:
            result = cmd_link(root, rest[0], rest[1])
        except SkillError as exc:
            fail(str(exc))
            return
    elif command == "link-all":
        result = cmd_link_all(root)
    else:
        fail(f"Unknown subcommand: {command}")
        return  # unreachable; keeps type-checkers happy

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
