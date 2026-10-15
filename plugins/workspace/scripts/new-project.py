#!/usr/bin/env python3
"""Scaffold a new project workspace (shared by /workspace:new-project and the
Claude Code mod UI).

Usage:
  new-project.py create [--dry-run] < payload.json

Payload (JSON on stdin):
  description  str, required  what the task is
  type         str, required  bug | feature | ci-testing | docs | analysis
  jira         str            ticket URL/key, default "none"
  repos        [str]          related repos under repos/ (multi-repo)
  worktree_repos [str]        subset of repos to give a worktree (default:
                              all repos for bug/feature/docs, none otherwise)
  branch       str            branch for new worktrees (default: derived)
  pr           {repo: number} analysis PR checkouts (pr/<n> worktrees)
  title        str            H1 of the project CLAUDE.md (default: derived)
  folder       str            exact folder name (default: derived; an
                              existing folder is then an error, not renamed)
  links        [str]          related_links entries
  skills       [{name, source}] already-linked repo skills to record
  no_worktree  bool           skip worktree creation

Creates projects/<folder>/ with CLAUDE.md (lean index), type-specific detail
files, and .gitignore, plus git worktrees. Worktree failures never block
project creation; they are reported in "errors". With --dry-run nothing is
written and git is not run. Yaml-free; JSON on stdout, exit 0 on handled
errors.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

import workspace_lib

SELF_REPO = "(self)"
TYPES = ("bug", "feature", "ci-testing", "docs", "analysis")
WORKTREE_TYPES = ("bug", "feature", "docs")
FOLDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
JIRA_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*-\d+")
SLUG_MAX = 39  # "keep it under 40 characters"

GITIGNORE = """# Large files that shouldn't be committed
*.log
*.txt.gz
*.tar.gz
"""

# type -> scaffold definition (mirrors the new-project skill's templates)
SPECS: dict[str, dict] = {
    "bug": {
        "dirs": ["logs", "docs"],
        "summary": "Bug Summary",
        "meta": [("Assignee", "TBD")],
        "files": [
            ("investigation.md", "Failure analysis, root cause, proposed fixes"),
            ("ci-runs.md", "CI runs analyzed and their artifacts"),
            ("source-code-map.md", "Key source paths per repo"),
        ],
        "plan": "Fix Plan",
        "plan_items": ["Identify root cause", "Determine fix approach",
                       "Implement fix", "Test on cluster", "Submit PR"],
        "progress": ["Bug details captured", "Logs collected and analyzed",
                     "Root cause identified", "Fix implemented", "PR submitted"],
    },
    "feature": {
        "dirs": ["docs", "patches"],
        "summary": "Feature Summary",
        "meta": [("Target Version", "TBD")],
        "files": [
            ("design.md", "Architecture, API changes, related PRs"),
            ("source-code-map.md", "Key source paths per repo"),
        ],
        "plan": "Implementation Plan",
        "plan_items": ["Review enhancement doc", "Design approach",
                       "Implement changes", "Write tests", "Submit PRs"],
        "progress": ["Design documented", "Implementation started",
                     "Tests written", "PR(s) submitted", "PR(s) merged"],
    },
    "ci-testing": {
        "dirs": ["results", "scripts"],
        "summary": "Test Summary",
        "meta": [("CI Job(s)", "TBD")],
        "files": [
            ("ci-runs.md", "CI runs analyzed and their artifacts"),
            ("test-failures.md", "Failing tests, causes, fixes, status"),
        ],
        "plan": "Test Plan",
        "plan_items": ["Identify failing jobs", "Analyze failures",
                       "Implement fixes", "Validate CI passing"],
        "progress": ["CI jobs identified", "Failures analyzed",
                     "Fixes implemented", "CI passing"],
    },
    "docs": {
        "dirs": ["drafts"],
        "summary": "Doc Summary",
        "meta": [("Target", "TBD")],
        "files": [("drafts.md", "Target documents, outline, review notes")],
        "plan": "Outline",
        "plan_items": ["Research and outline", "Write draft",
                       "Technical review", "Editorial review", "Submit PR"],
        "progress": ["Draft written", "Technical review", "Editorial review",
                     "PR submitted"],
    },
    "analysis": {
        "dirs": ["docs"],
        "summary": "Analysis Summary",
        "meta": [("Scope", "TBD")],
        "files": [("findings.md", "Scope, findings, recommendations")],
        "plan": "Analysis Plan",
        "plan_items": ["Define scope", "Gather data", "Analyze findings",
                       "Write recommendations"],
        "progress": ["Analysis started", "Findings documented",
                     "Recommendations made", "Actions taken"],
    },
}

DETAIL_TEMPLATES: dict[str, str] = {
    "investigation.md": """# Investigation

## Failure Analysis

_Describe the observed failure and symptoms._

## Root Cause

_Root cause goes here once identified._

## Proposed Fix

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
""",
    "ci-runs.md": """# CI Runs

<!-- Add a section per CI run analyzed. Template: -->
<!-- ## Run <ID> (<short description>)              -->
<!--                                                -->
<!-- **Job:** `<job name>`                           -->
<!-- **Date:** <YYYY-MM-DD>                          -->
<!--                                                -->
<!-- | Artifact | Description |                     -->
<!-- |----------|-------------|                      -->
<!--                                                -->
<!-- **Timeline:**                                   -->
<!-- ```                                             -->
<!-- <chronological events>                          -->
<!-- ```                                             -->
""",
    "design.md": """# Design

## Architecture

_High-level design and component interactions._

## API Changes

_New or modified APIs._

## Related PRs

| PR | Repo | Status | Description |
|----|------|--------|-------------|
""",
    "test-failures.md": """# Test Failures

| Test | Error | Root Cause | Fix | Status |
|------|-------|------------|-----|--------|
""",
    "drafts.md": """# Drafts

## Target Documents

| Document | Path | Status |
|----------|------|--------|

## Outline

_Document outline goes here._

## Review Notes

_Technical and editorial review feedback._
""",
    "findings.md": """# Findings

## Scope

_What is being analyzed and why._

## Findings

_Analysis results._

## Recommendations

| # | Recommendation | Priority | Status |
|---|----------------|----------|--------|
""",
}


class Invalid(Exception):
    """Bad payload; reported as status=error before anything is written."""


def emit(payload: dict) -> int:
    print(json.dumps(payload, indent=2))
    return 0


def git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = ["git"] + (["-C", str(cwd)] if cwd else []) + args
    return subprocess.run(cmd, capture_output=True, text=True)


# --------------------------------------------------------------------------
# Validation and naming
# --------------------------------------------------------------------------

def validate_branch(branch: str) -> None:
    if not branch:
        raise Invalid("branch name is empty")
    if branch.startswith("-"):
        raise Invalid(f"invalid branch '{branch}': must not start with '-'")
    if ".." in branch:
        raise Invalid(f"invalid branch '{branch}': must not contain '..'")
    if re.search(r"\s", branch):
        raise Invalid(f"invalid branch '{branch}': must not contain whitespace")
    if branch.endswith(".lock") or branch.endswith("/") or branch.endswith("."):
        raise Invalid(f"invalid branch '{branch}': bad suffix")
    if branch.startswith("/"):
        raise Invalid(f"invalid branch '{branch}': must not start with '/'")
    if re.search(r"[\x00-\x1f\x7f~^:?*\[\\]", branch) or "@{" in branch \
            or "//" in branch or branch == "@" \
            or any(c.startswith(".") for c in branch.split("/")):
        raise Invalid(f"invalid branch '{branch}': not a valid git ref name")


def jira_id(jira: str) -> str:
    m = JIRA_RE.search(jira or "")
    return m.group(0) if m else ""


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > SLUG_MAX:
        cut = slug[:SLUG_MAX + 1]
        slug = cut[:cut.rfind("-")] if "-" in cut[1:] else slug[:SLUG_MAX]
        slug = slug.strip("-")
    return slug or "project"


def suggest_folder(description: str, jira: str) -> str:
    return jira_id(jira) or slugify(description)


def derive_title(description: str) -> str:
    first = description.strip().splitlines()[0] if description.strip() else ""
    first = re.split(r"(?<=[.!?])\s", first)[0].strip()
    return (first[:97] + "...") if len(first) > 100 else first or "New Project"


def yaml_scalar(value: str) -> str:
    if re.match(r"^[A-Za-z0-9_./][A-Za-z0-9_./:@+=?&%~-]*$", value) \
            and value.lower() not in ("true", "false", "null", "yes", "no"):
        return value
    return json.dumps(value)


def read_dev_env(root: Path) -> tuple[bool, str]:
    """(is_self_workspace, domain) from dev-env.yaml, without a yaml parser."""
    path = root / "dev-env.yaml"
    try:
        text = path.read_text()
    except OSError:
        return False, ""
    is_self = bool(re.search(r"^self:", text, re.MULTILINE))
    m = re.search(r"^domain:\s*(\S+)\s*$", text, re.MULTILINE)
    return is_self, (m.group(1).strip("\"'") if m else "")


def parse_payload(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise Invalid("payload must be a JSON object")
    p: dict = {}
    p["description"] = str(raw.get("description") or "").strip()
    if not p["description"]:
        raise Invalid("description is required")
    p["type"] = raw.get("type")
    if p["type"] not in TYPES:
        raise Invalid(f"type must be one of {', '.join(TYPES)}")
    p["jira"] = str(raw.get("jira") or "none").strip() or "none"
    if p["jira"].lower() in ("no", "none"):
        p["jira"] = "none"

    def str_list(key: str) -> list[str]:
        val = raw.get(key) or []
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            raise Invalid(f"{key} must be a list of strings")
        return val

    p["repos"] = str_list("repos")
    p["links"] = [v for v in str_list("links") if v.strip()]
    wr = raw.get("worktree_repos")
    p["worktree_repos"] = str_list("worktree_repos") if wr is not None else None
    for repo in p["repos"] + (p["worktree_repos"] or []):
        if not REPO_RE.match(repo) or ".." in repo:
            raise Invalid(f"invalid repo name '{repo}'")
    for repo in p["worktree_repos"] or []:
        if repo not in p["repos"]:
            raise Invalid(f"worktree repo '{repo}' is not in repos")

    pr = raw.get("pr") or {}
    if not isinstance(pr, dict):
        raise Invalid("pr must be an object of repo -> number")
    p["pr"] = {}
    for repo, num in pr.items():
        if not REPO_RE.match(repo) or ".." in repo:
            raise Invalid(f"invalid repo name '{repo}' in pr")
        if isinstance(num, bool) or not isinstance(num, (int, str)) \
                or not str(num).isdigit() or int(num) < 1:
            raise Invalid(f"pr number for '{repo}' must be a positive integer")
        p["pr"][repo] = int(num)

    p["branch"] = str(raw.get("branch") or "").strip()
    if p["branch"]:
        validate_branch(p["branch"])
    p["title"] = " ".join(str(raw.get("title") or "").split())
    p["folder"] = str(raw.get("folder") or "").strip()
    if p["folder"] and (not FOLDER_RE.match(p["folder"]) or ".." in p["folder"]):
        raise Invalid(f"invalid folder name '{p['folder']}'")

    skills = raw.get("skills") or []
    if not isinstance(skills, list):
        raise Invalid("skills must be a list of {name, source}")
    p["skills"] = []
    for s in skills:
        if not isinstance(s, dict) or not s.get("name"):
            raise Invalid("each skill needs a name")
        p["skills"].append({"name": str(s["name"]),
                            "source": str(s.get("source", ""))})
    p["no_worktree"] = bool(raw.get("no_worktree"))
    return p


def pick_folder(projects: Path, p: dict, dry_run: bool) -> str:
    """Resolve the folder name, applying collision handling."""
    if p["folder"]:
        if (projects / p["folder"]).exists():
            raise Invalid(f"project folder '{p['folder']}' already exists "
                          f"(resume it with /workspace:resume-project)")
        return p["folder"]
    base = suggest_folder(p["description"], p["jira"])
    candidate, n = base, 1
    while (projects / candidate).exists():
        n += 1
        candidate = f"{base}-{n}"
    return candidate


# --------------------------------------------------------------------------
# Worktrees
# --------------------------------------------------------------------------

def plan_worktrees(p: dict, root: Path, folder: str, is_self: bool) -> list[dict]:
    """Planned worktrees: {repo, branch, kind: new|pr, number, path}."""
    if p["no_worktree"]:
        return []
    default_branch = p["branch"]
    if not default_branch:
        base = folder.lower() if jira_id(p["jira"]) else folder
        default_branch = f"fix/{base}" if p["type"] == "bug" else base
        validate_branch(default_branch)

    if is_self:
        if p["type"] not in WORKTREE_TYPES and not p["branch"]:
            return []
        return [{"repo": SELF_REPO, "branch": default_branch, "kind": "new",
                 "number": 0,
                 "path": root / ".claude" / "worktrees" / default_branch}]

    plan: list[dict] = []
    for repo, num in p["pr"].items():
        plan.append({"repo": repo, "branch": f"pr/{num}", "kind": "pr",
                     "number": num,
                     "path": root / "repos" / repo / ".worktrees" / "pr" / str(num)})
    if p["worktree_repos"] is not None:
        wanted = p["worktree_repos"]
    elif p["type"] in WORKTREE_TYPES:
        wanted = p["repos"]
    else:
        wanted = []
    for repo in wanted:
        if repo in p["pr"]:
            continue
        plan.append({"repo": repo, "branch": default_branch, "kind": "new",
                     "number": 0,
                     "path": root / "repos" / repo / ".worktrees" / default_branch})
    return plan


def ensure_exclude(checkout: Path, entry: str) -> None:
    res = git(["rev-parse", "--git-path", "info/exclude"], checkout)
    if res.returncode != 0:
        raise OSError(res.stderr.strip() or "cannot locate info/exclude")
    path = Path(res.stdout.strip())
    if not path.is_absolute():
        path = checkout / path
    existing = path.read_text() if path.is_file() else ""
    bare = entry.rstrip("/")
    if any(ln.strip() in (entry, bare, "/" + entry, "/" + bare)
           for ln in existing.splitlines()):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(entry + "\n")


def default_branch_of(checkout: Path) -> str:
    res = git(["symbolic-ref", "refs/remotes/origin/HEAD"], checkout)
    if res.returncode == 0:
        name = res.stdout.strip()
        prefix = "refs/remotes/origin/"
        if name.startswith(prefix):
            return name[len(prefix):]
    for cand in ("main", "master"):
        if git(["rev-parse", "--verify", "--quiet", f"origin/{cand}"],
               checkout).returncode == 0:
            return cand
    return ""


def create_worktree(wt: dict, root: Path) -> str:
    """Create one worktree; return an error message or ''."""
    is_self = wt["repo"] == SELF_REPO
    checkout = root if is_self else root / "repos" / wt["repo"]
    label = "workspace repo" if is_self else wt["repo"]
    if not checkout.is_dir():
        return f"{label}: repo checkout {checkout} not found"
    exclude = ".claude/worktrees/" if is_self else ".worktrees/"
    try:
        ensure_exclude(checkout, exclude)
    except OSError as exc:
        return f"{label}: could not update .git/info/exclude: {exc}"

    path = str(wt["path"])
    if wt["kind"] == "pr":
        n = wt["number"]
        res = git(["fetch", "--", "origin", f"+pull/{n}/head:pr/{n}",
                   f"+pull/{n}/head:refs/remotes/origin/pr/{n}"], checkout)
        if res.returncode != 0:
            return f"{label}: fetch of PR {n} failed: {res.stderr.strip()}"
        res = git(["worktree", "add", "--", path, f"pr/{n}"], checkout)
    else:
        default = default_branch_of(checkout)
        if not default:
            return f"{label}: cannot determine default branch from origin"
        res = git(["worktree", "add", "-b", wt["branch"], "--", path,
                   f"origin/{default}"], checkout)
    if res.returncode != 0:
        return f"{label}: git worktree add failed: {res.stderr.strip()}"
    return ""


# --------------------------------------------------------------------------
# Scaffold rendering
# --------------------------------------------------------------------------

def render_frontmatter(p: dict, folder: str, domain: str, repos: list[str],
                       worktrees: list[dict], now: datetime.datetime) -> str:
    out = ["---", f"project: {folder}", f"type: {p['type']}",
           f"created: {now.strftime('%Y-%m-%d')}",
           f"last-active: {now.strftime('%Y-%m-%dT%H:%M')}",
           "status: active", f"jira: {yaml_scalar(p['jira'])}"]
    if domain:
        out.append(f"domain: {yaml_scalar(domain)}")
    out += ["repos: []"] if not repos else \
        ["repos:"] + [f"  - {r}" for r in repos]

    self_wt = [w for w in worktrees if w["repo"] == SELF_REPO]
    multi = [w for w in worktrees if w["repo"] != SELF_REPO]
    if self_wt:
        out.append(f"branch: {yaml_scalar(self_wt[0]['branch'])}")
        out.append(f"worktree_path: {yaml_scalar(str(self_wt[0]['path']))}")
    if multi:
        branches = {w["branch"] for w in multi}
        if len(branches) == 1:
            out.append(f"branch: {yaml_scalar(branches.pop())}")
            out.append("worktrees:")
            out += [f"  - {w['repo']}" for w in multi]
        else:
            out.append("worktrees:")
            for w in multi:
                out.append(f"  - repo: {w['repo']}")
                out.append(f"    branch: {yaml_scalar(w['branch'])}")
    if p["skills"]:
        out.append("skills:")
        for s in p["skills"]:
            out.append(f"  - name: {yaml_scalar(s['name'])}")
            out.append(f"    source: {yaml_scalar(s['source'])}")
    out += ["related_links: []"] if not p["links"] else \
        ["related_links:"] + [f"  - {yaml_scalar(u)}" for u in p["links"]]
    out.append("---")
    return "\n".join(out) + "\n"


def render_claude_md(p: dict, folder: str, domain: str, repos: list[str],
                     worktrees: list[dict], now: datetime.datetime) -> str:
    spec = SPECS[p["type"]]
    parts = [render_frontmatter(p, folder, domain, repos, worktrees, now)]
    title = p["title"] or derive_title(p["description"])
    parts.append(f"\n# {title}\n")
    meta = [("Jira", p["jira"])] + spec["meta"]
    parts.append(f"\n## {spec['summary']}\n\n{p['description']}\n\n"
                 + "\n".join(f"- **{k}:** {v}" for k, v in meta) + "\n")
    rows = "\n".join(f"| `{name}` | {desc} |" for name, desc in spec["files"])
    parts.append("\n## Reference Files\n\n| File | Content |\n"
                 f"|------|---------|\n{rows}\n")
    parts.append(f"\n## {spec['plan']}\n\n"
                 + "\n".join(f"- [ ] {i}" for i in spec["plan_items"]) + "\n")
    parts.append("\n## Progress\n\n- [x] Project created\n"
                 + "\n".join(f"- [ ] {i}" for i in spec["progress"]) + "\n")
    return "".join(parts)


def render_detail(name: str, repos: list[str]) -> str:
    if name == "source-code-map.md":
        rows = "".join(f"| {r} |  | TODO: fill in relevant paths |\n"
                       for r in repos)
        return ("# Source Code Map\n\n| Repo | Key Path | Purpose |\n"
                f"|------|----------|---------|\n{rows}")
    return DETAIL_TEMPLATES[name]


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------

def cmd_create(args: argparse.Namespace) -> int:
    root = workspace_lib.resolve_workspace_root()
    if root is None:
        return emit({"status": "error", "error_message":
                     "Could not determine the workspace root. Set "
                     "WORKSPACE_ROOT or run inside a workspace."})
    try:
        p = parse_payload(json.loads(sys.stdin.read() or "null"))
    except ValueError as exc:
        return emit({"status": "error",
                     "error_message": f"invalid JSON payload: {exc}"})
    except Invalid as exc:
        return emit({"status": "error", "error_message": str(exc)})

    is_self, domain = read_dev_env(root)
    if is_self:
        domain = ""
        # Self-workspaces never get repo worktrees; PRs are links only.
        p["repos"], p["worktree_repos"], p["pr"] = [], None, {}
    repos = list(p["repos"]) + [r for r in p["pr"] if r not in p["repos"]]
    projects = root / "projects"

    try:
        folder = pick_folder(projects, p, args.dry_run)
        plan = plan_worktrees(p, root, folder, is_self)
    except Invalid as exc:
        return emit({"status": "error", "error_message": str(exc)})

    errors: list[str] = []
    now = datetime.datetime.now()
    project_dir = projects / folder
    created: list[dict] = []

    if args.dry_run:
        for wt in plan:
            if wt["repo"] != SELF_REPO and not (root / "repos" / wt["repo"]).is_dir():
                errors.append(f"{wt['repo']}: repo checkout not found under repos/")
            else:
                created.append(wt)
    else:
        try:
            projects.mkdir(parents=True, exist_ok=True)
            project_dir.mkdir()
        except OSError as exc:
            return emit({"status": "error",
                         "error_message": f"cannot create {project_dir}: {exc}"})
        for wt in plan:
            msg = create_worktree(wt, root)
            if msg:
                errors.append(msg)
            else:
                created.append(wt)

        spec = SPECS[p["type"]]
        try:
            for sub in spec["dirs"]:
                (project_dir / sub).mkdir(exist_ok=True)
            (project_dir / ".gitignore").write_text(GITIGNORE)
            for name, _ in spec["files"]:
                (project_dir / name).write_text(render_detail(name, repos))
            (project_dir / "CLAUDE.md").write_text(
                render_claude_md(p, folder, domain, repos, created, now))
        except OSError as exc:
            return emit({"status": "error",
                         "error_message": f"cannot write project files: {exc}"})

    payload = {
        "status": "ok",
        "folder": folder,
        "path": str(project_dir),
        "worktrees": [{"repo": w["repo"], "path": str(w["path"]),
                       "branch": w["branch"]} for w in created],
        "errors": errors,
    }
    if args.dry_run:
        payload["dry_run"] = True
    return emit(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create a project from a JSON payload on stdin")
    create.add_argument("--dry-run", action="store_true",
                        help="Report what would be created without writing or running git")
    return parser


def main() -> int:
    return cmd_create(build_parser().parse_args())


if __name__ == "__main__":
    sys.exit(main())
