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
from typing import Any

# Plugins cannot declare python dependencies. PyYAML is required to parse
# project frontmatter — emit a self-describing JSON error (that the calling
# skill can relay) instead of a raw ImportError traceback.
try:
    import yaml
except ImportError:
    print(json.dumps({
        "status": "error",
        "error": "PyYAML is required by resume-project.py (project frontmatter "
                 "is YAML). Install with: pip3 install pyyaml",
    }))
    sys.exit(0)

import workspace_lib

SKILL_SUGGESTIONS: dict[str, list[str]] = {
    "bug": [
        "/prow-job:analyze-test-failure",
        "/prow-job:analyze-install-failure",
        "/prow-job:extract-must-gather",
        "/feature-dev:feature-dev",
    ],
    "feature": [
        "/feature-dev:feature-dev",
        "/pr-review-toolkit:review-pr",
    ],
    "ci-testing": [
        "/prow-job:analyze-test-failure",
        "/prow-job:analyze-install-failure",
        "/prow-job:analyze-resource",
        "/prow-job:extract-must-gather",
    ],
    "docs": [
        "/feature-dev:feature-dev",
    ],
    "analysis": [
        "/pr-review-toolkit:review-pr",
        "/prow-job:analyze-test-failure",
        "/feature-dev:feature-dev",
    ],
}
DEFAULT_SKILLS = ["/feature-dev:feature-dev"]

ALWAYS_PRESENT = {"CLAUDE.md", "README.md", ".gitignore"}


def parse_frontmatter(path: Path) -> dict[str, Any]:
    """Extract YAML frontmatter between --- delimiters."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return {}

    if not lines or lines[0].strip() != "---":
        return {}

    fm_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        fm_lines.append(line)
    else:
        return {}

    try:
        result = yaml.safe_load("\n".join(fm_lines))
    except yaml.YAMLError:
        return {}
    if not isinstance(result, dict):
        return {}
    for k, v in result.items():
        if isinstance(v, (datetime.date, datetime.datetime)):
            result[k] = str(v)
    return result


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


def normalize_worktrees(raw: Any, fallback_branch: str) -> list[dict[str, str]]:
    """Normalize any worktree frontmatter format to list[dict] with repo, branch, path."""
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, dict):
        return [
            {"repo": str(repo), "branch": str(branch),
             "path": f"repos/{repo}/.worktrees/{branch}"}
            for repo, branch in raw.items()
        ]
    if isinstance(raw, list):
        result: list[dict[str, str]] = []
        for item in raw:
            if isinstance(item, dict):
                repo = str(item.get("repo", ""))
                branch = str(item.get("branch", fallback_branch))
                path = str(item.get("path", f"repos/{repo}/.worktrees/{branch}"))
                result.append({"repo": repo, "branch": branch, "path": path})
            else:
                item = str(item)
                result.append(
                    {"repo": item, "branch": fallback_branch,
                     "path": f"repos/{item}/.worktrees/{fallback_branch}"})
        return result
    return []


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


extract_checklist = workspace_lib.extract_checklist


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


def resolve_worktree_status(
    worktrees: list[dict[str, str]], root: Path
) -> list[dict]:
    """Check status of declared worktrees."""
    if not worktrees:
        return []

    results = []
    for wt in worktrees:
        wt_path = root / wt["path"]
        entry: dict = {
            "repo": wt["repo"],
            "branch": wt["branch"],
            "path": str(wt_path),
            "exists": False,
            "dirty": False,
            "dirty_count": 0,
            "ahead": 0,
            "no_upstream": False,
            "error": None,
        }
        if not wt_path.is_dir():
            results.append(entry)
            continue

        entry["exists"] = True

        try:
            status_result = subprocess.run(
                ["git", "-C", str(wt_path), "status", "--porcelain"],
                capture_output=True, text=True,
            )
            if status_result.returncode != 0:
                entry["error"] = f"git status failed: {status_result.stderr.strip()}"
            elif status_result.stdout.strip():
                lines = [l for l in status_result.stdout.splitlines() if l.strip()]
                entry["dirty"] = True
                entry["dirty_count"] = len(lines)

            upstream_check = subprocess.run(
                ["git", "-C", str(wt_path), "rev-parse", "--verify", "@{upstream}"],
                capture_output=True, text=True,
            )
            if upstream_check.returncode != 0:
                entry["no_upstream"] = True
            else:
                ahead_result = subprocess.run(
                    ["git", "-C", str(wt_path), "rev-list", "--count", "@{upstream}..HEAD"],
                    capture_output=True, text=True,
                )
                if ahead_result.returncode == 0 and ahead_result.stdout.strip():
                    entry["ahead"] = int(ahead_result.stdout.strip())
        except (OSError, ValueError) as exc:
            entry["error"] = str(exc)

        results.append(entry)
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
        return [l.strip() for l in result.stdout.splitlines() if l.strip()]
    except OSError:
        return _fallback_project_names(root)


def _fallback_project_names(root: Path) -> list[str]:
    """Fallback: list project directories sorted alphabetically."""
    projects_dir = root / "projects"
    if not projects_dir.is_dir():
        return []
    return sorted(d.name for d in projects_dir.iterdir() if d.is_dir())


def resolve_project(arg: str | None, root: Path) -> dict:
    """Resolve a project argument and return full structured context.

    Returns a dict with:
      status: "ok" | "not_found" | "no_projects" | "out_of_range" | "no_argument"
      error_message: str (when status != "ok")
      alternatives: list[str] (when status != "ok")
      project: dict (only when status == "ok")
    """
    projects_dir = root / "projects"

    if not projects_dir.is_dir():
        return {"status": "no_projects", "error_message": "No projects/ directory found.", "alternatives": []}

    # Resolve argument to a project name
    if arg is None:
        names = get_recent_names(root)
        if not names:
            return {"status": "no_projects", "error_message": "No active projects found.", "alternatives": []}
        return {"status": "no_argument", "alternatives": names}

    if arg.isdigit():
        names = get_recent_names(root)
        idx = int(arg) - 1
        if idx < 0 or idx >= len(names):
            return {
                "status": "out_of_range",
                "error_message": f"Only {len(names)} projects exist.",
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
            "error_message": f"Project '{project_name}' not found.",
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

    fm = parse_frontmatter(claude_md) if claude_md.is_file() else {}
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

    project_type = fm.get("type", "")
    if isinstance(project_type, list):
        project_type = project_type[0] if project_type else ""
    suggestions = SKILL_SUGGESTIONS.get(project_type, DEFAULT_SKILLS)

    frontmatter = dict(fm)
    frontmatter["repos"] = repos_list

    branch = fm.get("branch", "")
    if isinstance(branch, list):
        branch = branch[0] if branch else ""
    worktrees = normalize_worktrees(fm.get("worktrees", []), branch)
    worktree_repos = [wt["repo"] for wt in worktrees]
    worktree_status = resolve_worktree_status(worktrees, root)

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
            "skill_suggestions": suggestions,
            "branch": branch,
            "worktree_repos": worktree_repos,
            "worktree_status": worktree_status,
        },
    }


def main():
    root = workspace_lib.resolve_workspace_root()
    if root is None:
        print(json.dumps({
            "status": "error",
            "error": "Could not determine the workspace root. Set WORKSPACE_ROOT "
                     "or run inside a workspace (a directory containing dev-env.yaml).",
        }))
        sys.exit(0)
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    result = resolve_project(arg, root)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
