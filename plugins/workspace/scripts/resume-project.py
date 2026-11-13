#!/usr/bin/env python3
"""Resolve a project and return structured context for /workspace:resume-project and /workspace:close-project.

Usage: resume-project.py [project-name-or-number]
Output: JSON to stdout (see resolve_project for schema).
"""

from __future__ import annotations

import datetime
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Plugins cannot declare python dependencies. PyYAML is required to parse
# project frontmatter — emit a self-describing JSON error (that the calling
# skill can relay) instead of a raw ImportError traceback.
try:
    import yaml  # noqa: F401  (fail fast with a JSON error)
except ImportError:
    print(json.dumps({
        "status": "error",
        "error_message": "PyYAML is required (project frontmatter is YAML). "
                         "Tell the user to install it with: pip3 install pyyaml. "
                         "Do not retry until it is installed.",
    }))
    sys.exit(0)

import workspace_lib

ALWAYS_PRESENT = {"CLAUDE.md", "README.md", ".gitignore"}


def stamp_last_active(claude_md: Path) -> None:
    """Update or insert last-active in CLAUDE.md frontmatter (YYYY-MM-DDTHH:MM)."""
    try:
        text = claude_md.read_text()
    except OSError:
        return

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return

    today = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    found = False
    end = len(lines)

    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = i
            break
        if line.startswith("last-active:"):
            lines[i] = f"last-active: {today}\n"
            found = True
            break

    if not found:
        insert_at = end
        for i, line in enumerate(lines[1:end], 1):
            if line.startswith("status:"):
                insert_at = i + 1
                break
        lines.insert(insert_at, f"last-active: {today}\n")

    try:
        claude_md.write_text("".join(lines))
    except OSError:
        pass


def parse_reference_files(text: str) -> list[dict[str, str]]:
    """Parse the Reference Files markdown table into [{filename, description}]."""
    in_section = False
    found_header = False
    skipped_separator = False
    results = []

    for line in text.splitlines():
        if re.match(r"^##\s+Reference Files", line, re.IGNORECASE):
            in_section = True
            continue

        if in_section and not found_header:
            if "|" in line and "File" in line:
                found_header = True
            elif line.startswith("## "):
                break
            continue

        if found_header and not skipped_separator:
            if re.match(r"^\|[-\s|]+\|$", line):
                skipped_separator = True
            continue

        if skipped_separator:
            if not line.strip() or line.startswith("## "):
                break
            cells = [c.strip() for c in line.split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 2:
                filename = cells[0].strip("`")
                description = cells[1]
                results.append({"filename": filename, "description": description})

    return results


def list_project_files(project_dir: Path) -> list[str]:
    """Recursively list files relative to project_dir, sorted."""
    files = []
    for root, dirs, filenames in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in filenames:
            rel = os.path.relpath(os.path.join(root, f), project_dir)
            files.append(rel)
    files.sort()
    return files


def find_unregistered_files(
    all_files: list[str], manifest_files: list[dict[str, str]]
) -> list[str]:
    """Files on disk not in the Reference Files manifest or ALWAYS_PRESENT."""
    known = ALWAYS_PRESENT | {m["filename"] for m in manifest_files}
    return [f for f in all_files if f not in known and not f.startswith(".")]


def extract_checklist(text: str) -> dict:
    """Extract checked/unchecked items with their section headings."""
    current_section = ""
    checked_items = []
    unchecked_items = []

    for line in text.splitlines():
        heading_match = re.match(r"^#{2,3}\s+(.+)", line)
        if heading_match:
            current_section = heading_match.group(1).strip()
            continue

        item_match = re.match(r"^\s*- \[([ x])\] (.+)$", line)
        if item_match:
            done = item_match.group(1) == "x"
            entry = {"text": item_match.group(2).strip(), "section": current_section}
            if done:
                checked_items.append(entry)
            else:
                unchecked_items.append(entry)

    return {
        "checked": len(checked_items),
        "unchecked": len(unchecked_items),
        "total": len(checked_items) + len(unchecked_items),
        "unchecked_items": unchecked_items,
        "checked_items": checked_items,
    }


def domain_search_roots(root: Path) -> list[Path]:
    """Directories that may hold domains: workspace domains/ first, then bundled."""
    return [root / "domains", workspace_lib.PLUGIN_ROOT / "domains"]


def find_domain_base(domain: str, root: Path) -> Path | None:
    """Locate a domain directory by name: workspace first, then plugin-bundled."""
    if not domain:
        return None
    for base in domain_search_roots(root):
        candidate = base / domain
        if candidate.is_dir():
            return candidate
    return None


def resolve_domain_context(domain: str, root: Path) -> dict:
    """Resolve the domain's context file and docs directory (absolute paths)."""
    domain_dir = find_domain_base(domain, root)
    if domain_dir is None:
        return {"context_file": None, "docs": []}
    ctx_file = domain_dir / "context.md"
    context_file = str(ctx_file) if ctx_file.is_file() else None
    docs_dir = domain_dir / "docs"
    docs = []
    if docs_dir.is_dir():
        docs = [
            {"name": p.stem, "path": str(p)}
            for p in sorted(docs_dir.glob("*.md"))
        ]
    return {"context_file": context_file, "docs": docs}


def resolve_repo_context(repos: list[str], root: Path) -> list[dict[str, str]]:
    """For each repo, find context files (repo CLAUDE.md and/or domain context).

    Paths are absolute — bundled-domain files live outside the workspace.
    """
    results = []
    for repo in repos:
        repo_claude = root / "repos" / repo / "CLAUDE.md"
        if repo_claude.is_file():
            results.append({
                "repo": repo,
                "path": str(repo_claude),
                "source": "repo",
            })

        for base in domain_search_roots(root):
            matches = glob.glob(str(base / "*" / "context" / f"{repo}.md"))
            if matches:
                results.append({
                    "repo": repo,
                    "path": str(Path(matches[0])),
                    "source": "domain",
                })
                break

    return results


def get_recent_names(root: Path) -> list[str]:
    """Get recent non-done project names via recent-projects.py --names."""
    script = Path(__file__).resolve().parent / "recent-projects.py"
    if not script.is_file():
        return _fallback_project_names(root)
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--names"],
            capture_output=True, text=True,
            env={**os.environ, "WORKSPACE_ROOT": str(root)},
        )
        if result.returncode != 0:
            return _fallback_project_names(root)
        return [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    except OSError:
        return _fallback_project_names(root)


def _fallback_project_names(root: Path) -> list[str]:
    """Fallback: list project directories sorted alphabetically."""
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return []
    return sorted(d.name for d in projects_dir.iterdir() if d.is_dir())


PICK = ("Ask the user to pick one of `alternatives` (offer the first 3 "
        "plus \"See all projects\"), then re-run with the chosen name.")
NO_PROJECTS = ("There are no active projects in this workspace. Tell the user "
               "and suggest /workspace:new-project. Stop.")


def resolve_project(arg: str | None, root: Path) -> dict:
    """Resolve a project argument and return full structured context.

    Returns a dict with:
      status: "ok" | "not_found" | "no_projects" | "out_of_range" | "no_argument"
      error_message: what the calling skill should do (when status != "ok")
      alternatives: list[str] (when status != "ok")
      project: dict (only when status == "ok")
    """
    projects_dir = root / "projects"

    if not projects_dir.is_dir():
        return {"status": "no_projects", "error_message": NO_PROJECTS, "alternatives": []}

    # Resolve argument to a project name
    if arg is None:
        names = get_recent_names(root)
        if not names:
            return {"status": "no_projects", "error_message": NO_PROJECTS, "alternatives": []}
        return {
            "status": "no_argument",
            "error_message": ("No project was given. If a project is already "
                              "loaded in this conversation, re-run with its "
                              "name. Otherwise: " + PICK),
            "alternatives": names,
        }

    if arg.isdigit():
        names = get_recent_names(root)
        if not names:
            return {"status": "no_projects", "error_message": NO_PROJECTS, "alternatives": []}
        idx = int(arg) - 1
        if idx < 0 or idx >= len(names):
            return {
                "status": "out_of_range",
                "error_message": f"Only {len(names)} active projects exist. " + PICK,
                "alternatives": names,
            }
        project_name = names[idx]
    else:
        project_name = arg

    project_dir = projects_dir / project_name
    if not project_dir.is_dir():
        all_names = sorted(d.name for d in projects_dir.iterdir() if d.is_dir())
        return {
            "status": "not_found",
            "error_message": f"Project '{project_name}' not found. " + PICK,
            "alternatives": all_names,
        }

    # Build full project context
    claude_md = project_dir / "CLAUDE.md"
    readme = project_dir / "README.md"

    if claude_md.is_file():
        context_file = str(claude_md)
        context_type = "claude_md"
        try:
            text = claude_md.read_text()
        except OSError:
            text = ""
    elif readme.is_file():
        context_file = str(readme)
        context_type = "readme"
        try:
            text = readme.read_text()
        except OSError:
            text = ""
    else:
        context_file = None
        context_type = "none"
        text = ""

    fm = workspace_lib.parse_frontmatter(claude_md) if claude_md.is_file() else {}
    has_frontmatter = bool(fm)

    repos_list = fm.get("repos", [])
    if isinstance(repos_list, str):
        repos_list = [repos_list] if repos_list else []

    ref_files = parse_reference_files(text) if text else []
    all_files = list_project_files(project_dir)
    unregistered = find_unregistered_files(all_files, ref_files) if ref_files else []
    checklist = extract_checklist(text) if text else {
        "checked": 0, "unchecked": 0, "total": 0,
        "unchecked_items": [], "checked_items": [],
    }
    repo_context = resolve_repo_context(repos_list, root)

    # Prefer the new `domain:` field; fall back to legacy `preset:` for
    # projects created before the preset→domain rename.
    domain = fm.get("domain") or fm.get("preset", "")
    if isinstance(domain, list):
        domain = domain[0] if domain else ""
    domain_ctx = resolve_domain_context(domain, root)

    frontmatter = dict(fm)
    frontmatter["repos"] = repos_list

    branch = fm.get("branch", "")
    if isinstance(branch, list):
        branch = branch[0] if branch else ""
    worktrees = workspace_lib.project_worktrees(fm, root)
    worktree_status = [workspace_lib.worktree_status(wt) for wt in worktrees]

    if claude_md.is_file():
        stamp_last_active(claude_md)

    return {
        "status": "ok",
        "root": str(root),
        "project": {
            "name": project_name,
            "dir": str(project_dir),
            "context_file": context_file,
            "context_type": context_type,
            "has_frontmatter": has_frontmatter,
            "frontmatter": frontmatter,
            "reference_files": ref_files,
            "has_reference_files": bool(ref_files),
            "all_files": all_files,
            "unregistered_files": unregistered,
            "checklist": checklist,
            "repo_context_files": repo_context,
            "domain_context": domain_ctx["context_file"],
            "domain_docs": domain_ctx["docs"],
            "branch": branch,
            "worktree_status": worktree_status,
        },
    }


def main():
    root = workspace_lib.resolve_workspace_root()
    if root is None:
        print(json.dumps({
            "status": "error",
            "error_message": "Could not determine the workspace root. Tell the "
                             "user to launch claude inside a workspace (a "
                             "directory with dev-env.yaml) or to run "
                             "/workspace:setup-environment. Stop.",
        }))
        sys.exit(0)
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    result = resolve_project(arg, root)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
