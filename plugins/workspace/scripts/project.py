#!/usr/bin/env python3
"""Deterministic project operations for the workspace skills.

Usage:
  project.py new --type T --title "..." [--name N] [--jira J] [--repos a,b]
                 [--branch B] [--worktrees a,b | --self-worktree] [--pr N]
                 [--links u1,u2] [--dry-run]
  project.py worktree create (--repo R | --self) --branch B [--pr N]
  project.py worktree status <project>
  project.py close <project> [--dry-run] [--force | --keep-worktrees] [--notes "..."]
  project.py touch <project>

Output: JSON to stdout, always exit 0. Every non-ok result has an
``error_message`` that says what the calling skill must do next.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import yaml  # noqa: F401  (fail fast with a JSON error)
except ImportError:
    print(json.dumps({"status": "error",
                      "error_message": "PyYAML is required. Tell the user to "
                                       "install it with: pip3 install pyyaml. Stop."}))
    sys.exit(0)

import workspace_lib as wl

SCRIPTS = Path(__file__).resolve().parent
TYPES = ("bug", "feature", "ci-testing", "docs", "analysis")
LINE_CAP = 100
JIRA_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
GITIGNORE = "# Large files that shouldn't be committed\n*.log\n*.txt.gz\n*.tar.gz\n"


class ProjectError(Exception):
    def __init__(self, status: str, message: str, **extra):
        super().__init__(message)
        self.payload = {"status": status, "error_message": message, **extra}


def now_minute() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True)


def project_dir(root: Path, name: str) -> Path:
    d = root / "projects" / name
    if not (d / "CLAUDE.md").is_file():
        raise ProjectError("not_found", f"Project '{name}' has no CLAUDE.md under "
                           f"{root / 'projects'}. Run resume-project.py with no "
                           "argument to list projects, and ask the user.")
    return d


# ─── Worktrees ──────────────────────────────────────────────────────────────


def default_branch(repo: Path) -> str:
    head = git(repo, "symbolic-ref", "refs/remotes/origin/HEAD")
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip().rsplit("refs/remotes/origin/", 1)[-1]
    for candidate in ("main", "master"):
        if git(repo, "rev-parse", "--verify", f"origin/{candidate}").returncode == 0:
            return candidate
    raise ProjectError("error", f"Cannot find the default branch of origin in {repo}. "
                       f"Tell the user to run: git -C {repo} remote set-head "
                       "origin --auto")


def add_exclude(repo: Path, pattern: str) -> None:
    common = git(repo, "rev-parse", "--git-common-dir")
    if common.returncode != 0:
        return
    git_dir = Path(common.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text() if exclude.is_file() else ""
    if pattern not in text.splitlines():
        exclude.write_text(text + ("" if text.endswith("\n") or not text else "\n")
                           + pattern + "\n")


def create_worktree(root: Path, repo: str | None, branch: str,
                    pr: int | None = None) -> dict:
    """Create (or find) a worktree. ``repo=None`` means the self repo."""
    if repo is None:
        repo_dir, rel, pattern = root, Path(".claude/worktrees"), ".claude/worktrees/"
    else:
        repo_dir, rel, pattern = root / "repos" / repo, Path(".worktrees"), ".worktrees/"
    if git(repo_dir, "rev-parse", "--git-dir").returncode != 0:
        raise ProjectError("error", f"{repo_dir} is not a git checkout. Check the "
                           "repo name against dev-env.yaml.")
    if pr is not None:
        branch = f"pr/{pr}"
    path = repo_dir / rel / branch
    result = {"status": "ok", "repo": repo or "(self)", "branch": branch,
              "path": str(path), "existed": False}
    add_exclude(repo_dir, pattern)
    if path.is_dir():
        result["existed"] = True
        return result

    if pr is not None:
        fetch = git(repo_dir, "fetch", "origin", f"pull/{pr}/head:pr/{pr}")
        if fetch.returncode != 0 and git(repo_dir, "rev-parse", "--verify",
                                         f"refs/heads/pr/{pr}").returncode != 0:
            raise ProjectError("error", f"Cannot fetch PR {pr}: {fetch.stderr.strip()}")
        add = git(repo_dir, "worktree", "add", str(path), branch)
    elif git(repo_dir, "rev-parse", "--verify", f"refs/heads/{branch}").returncode == 0:
        add = git(repo_dir, "worktree", "add", str(path), branch)
    else:
        base = default_branch(repo_dir)
        add = git(repo_dir, "worktree", "add", str(path), "-b", branch, f"origin/{base}")
    if add.returncode != 0:
        raise ProjectError("error", f"git worktree add failed: {add.stderr.strip()}. "
                           "Tell the user; work continues in the main checkout.")
    return result


def cmd_worktree_create(root: Path, a: argparse.Namespace) -> dict:
    if not a.self_repo and not a.repo:
        raise ProjectError("error", "Give --repo <name> or --self.")
    if not a.branch and a.pr is None:
        raise ProjectError("error", "Give --branch <name> or --pr <number>.")
    return create_worktree(root, None if a.self_repo else a.repo, a.branch or "", a.pr)


def cmd_worktree_status(root: Path, a: argparse.Namespace) -> dict:
    fm = wl.parse_frontmatter(project_dir(root, a.project) / "CLAUDE.md")
    return {"status": "ok",
            "worktrees": [wl.worktree_status(w) for w in wl.project_worktrees(fm, root)]}


# ─── new ────────────────────────────────────────────────────────────────────


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > limit:
        slug = slug[:limit].rsplit("-", 1)[0] or slug[:limit]
    return slug


def split_csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def workspace_info(root: Path) -> tuple[str, bool]:
    """(domain name, is self-workspace) from dev-env.yaml."""
    import yaml

    try:
        data = yaml.safe_load((root / "dev-env.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return "", False
    if not isinstance(data, dict):
        return "", False
    dom = data.get("domain") or ""
    if isinstance(dom, dict):
        dom = dom.get("name", "")
    return str(dom), "self" in data


def yaml_list(items: list[str]) -> str:
    return "[]" if not items else "\n" + "\n".join(f"  - {i}" for i in items)


def cmd_new(root: Path, a: argparse.Namespace) -> dict:
    if a.type not in TYPES:
        raise ProjectError("error", f"--type must be one of {', '.join(TYPES)}.")
    jira_url = a.jira or ""
    jira_id = ""
    if jira_url:
        m = JIRA_RE.search(jira_url.upper())
        jira_id = m.group(1) if m else ""
    name = a.name or jira_id or slugify(a.title)
    if not name:
        raise ProjectError("error", "Cannot derive a project name. Give --name.")
    pdir = root / "projects" / name
    if pdir.exists():
        n = 2
        while (root / "projects" / f"{name}-{n}").exists():
            n += 1
        raise ProjectError("exists", f"projects/{name} already exists. Ask the user "
                           f"to use '{name}-{n}' or to resume the existing project "
                           "with /workspace:resume-project.",
                           name=name, suggestion=f"{name}-{n}")
    if a.dry_run:
        return {"status": "ok", "dry_run": True, "name": name, "dir": str(pdir),
                "jira": jira_url or "none"}

    domain, is_self = workspace_info(root)
    repos = split_csv(a.repos)
    links = split_csv(a.links)
    warnings: list[str] = []

    worktrees: list[str] = []
    worktree_path = ""
    branch = a.branch or ""
    targets: list[str | None] = [None] if a.self_worktree else split_csv(a.worktrees)
    if targets and not branch and a.pr is None:
        raise ProjectError("error", "Worktrees need --branch (or --pr).")
    for repo in targets:
        try:
            wt = create_worktree(root, repo, branch, a.pr)
        except ProjectError as exc:
            warnings.append(f"{repo or 'self'}: {exc}")
            continue
        branch = wt["branch"]
        if repo is None:
            worktree_path = wt["path"]
        else:
            worktrees.append(repo)
    if a.pr is not None and not worktrees and not worktree_path:
        branch = ""

    fm = ["---", f"project: {name}", f"type: {a.type}",
          f"created: {datetime.date.today().isoformat()}",
          "status: active", f"last-active: {now_minute()}",
          f"jira: {jira_url or 'none'}"]
    if domain:
        fm.append(f"domain: {domain}")
    fm.append(f"repos: {yaml_list(repos)}")
    if branch:
        fm.append(f"branch: {branch}")
    if worktrees:
        fm.append(f"worktrees: {yaml_list(worktrees)}")
    if worktree_path:
        fm.append(f"worktree_path: {worktree_path}")
    fm.append(f"related_links: {yaml_list(links)}")
    fm.append("---")
    body = [
        "", f"# {a.title}", "",
        "## Summary", "", "<!-- 2-3 sentences: the task and its goal. -->", "",
        "## Reference Files", "", "| File | Content |", "|------|---------|", "",
        "## Plan", "", "<!-- Checklist (- [ ]) of steps specific to this task. -->", "",
        "## Progress", "", "- [x] Project created", "",
    ]
    pdir.mkdir(parents=True)
    (pdir / ".gitignore").write_text(GITIGNORE)
    claude_md = pdir / "CLAUDE.md"
    claude_md.write_text("\n".join(fm + body))
    return {"status": "ok", "name": name, "dir": str(pdir),
            "claude_md": str(claude_md), "self_workspace": is_self,
            "branch": branch, "worktrees": worktrees,
            "worktree_path": worktree_path, "warnings": warnings}


# ─── close ──────────────────────────────────────────────────────────────────


def repo_of(root: Path, wt: dict) -> Path:
    return root if wt["repo"] == "(self)" else root / "repos" / wt["repo"]


def set_section(text: str, heading: str, content: str) -> str:
    """Replace the body of '## heading' or append the section at the end."""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"## {heading}"), None)
    if start is None:
        return text.rstrip("\n") + f"\n\n## {heading}\n\n{content}\n"
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("## ")), len(lines))
    new = lines[:start + 1] + ["", content, ""] + lines[end:]
    return "\n".join(new).rstrip("\n") + "\n"


def cmd_close(root: Path, a: argparse.Namespace) -> dict:
    pdir = project_dir(root, a.project)
    claude_md = pdir / "CLAUDE.md"
    fm = wl.parse_frontmatter(claude_md)
    statuses = [wl.worktree_status(w) for w in wl.project_worktrees(fm, root)]
    attention = [s for s in statuses if s["needs_attention"]]
    already_done = str(fm.get("status", "")).lower() == "done"

    if a.dry_run:
        return {"status": "ok", "dry_run": True, "project": a.project,
                "already_done": already_done, "worktrees": statuses,
                "needs_attention": attention}
    if attention and not (a.force or a.keep_worktrees):
        raise ProjectError(
            "needs_attention",
            "Some worktrees have uncommitted or unpushed work (see "
            "needs_attention). Ask the user to commit and push first, to "
            "discard it (re-run with --force), or to keep the worktrees "
            "(re-run with --keep-worktrees).", needs_attention=attention)

    removed, kept, branches_kept = [], [], []
    if not a.keep_worktrees:
        for s in statuses:
            repo = repo_of(root, s)
            if s["exists"]:
                args = ["worktree", "remove", s["path"]]
                if a.force:
                    args.insert(2, "--force")
                rm = git(repo, *args)
                if rm.returncode != 0:
                    kept.append({**s, "error": rm.stderr.strip()})
                    continue
            git(repo, "worktree", "prune")
            removed.append(s["path"])
            b = s["branch"]
            if b:
                flag = "-D" if b.startswith("pr/") or a.force else "-d"
                if git(repo, "rev-parse", "--verify", f"refs/heads/{b}").returncode == 0 \
                        and git(repo, "branch", flag, b).returncode != 0:
                    branches_kept.append(b)
    else:
        kept = statuses

    subprocess.run([sys.executable, str(SCRIPTS / "handoff.py"), "clear",
                    "--project", a.project], capture_output=True, text=True,
                   env={**os.environ, "WORKSPACE_ROOT": str(root)})

    text = claude_md.read_text()
    text = wl.set_frontmatter_field(text, "status", "done")
    text = wl.set_frontmatter_field(text, "closed",
                                    datetime.date.today().isoformat(), after="status")
    text = wl.set_frontmatter_field(text, "last-active", now_minute(), after="closed")
    if not kept:
        if "worktrees" in fm:
            text = wl.set_frontmatter_field(text, "worktrees", "[]")
        if "worktree_path" in fm:
            text = wl.set_frontmatter_field(text, "worktree_path", None)
    if "skills" in fm:
        text = wl.set_frontmatter_field(text, "skills", None)
    if a.notes:
        text = set_section(text, "Closing Notes",
                           f"_Closed {datetime.date.today().isoformat()}_\n\n{a.notes}")
    wl.write_atomic(claude_md, text)
    return {"status": "ok", "project": a.project, "was_done": already_done,
            "removed_worktrees": removed, "kept_worktrees": [k["path"] for k in kept],
            "remove_errors": [k for k in kept if k.get("error")],
            "branches_kept": branches_kept}


# ─── touch ──────────────────────────────────────────────────────────────────


def load_consolidate():
    spec = importlib.util.spec_from_file_location(
        "consolidate_project", SCRIPTS / "consolidate-project.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses look the module up here
    spec.loader.exec_module(mod)
    return mod


def cmd_touch(root: Path, a: argparse.Namespace) -> dict:
    pdir = project_dir(root, a.project)
    claude_md = pdir / "CLAUDE.md"
    text = claude_md.read_text()
    after = "status" if re.search(r"^status:", text, re.M) else None
    wl.write_atomic(claude_md, wl.set_frontmatter_field(
        text, "last-active", now_minute(), after=after))
    before = len(claude_md.read_text().splitlines())
    result: dict = {"status": "ok", "project": a.project, "lines": before,
                    "line_cap": LINE_CAP, "consolidation": None}
    if before > LINE_CAP:
        result["consolidation"] = load_consolidate().consolidate(pdir)
        result["lines"] = len(claude_md.read_text().splitlines())
    result["over_cap"] = result["lines"] > LINE_CAP
    if result["over_cap"]:
        result["error_message"] = (
            f"CLAUDE.md is {result['lines']} lines (cap {LINE_CAP}). Move narrative "
            "into a detail file, add its Reference Files row, and leave a one-line "
            "pointer in CLAUDE.md.")
    return result


# ─── CLI ────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="project.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new")
    n.add_argument("--type", required=True)
    n.add_argument("--title", required=True)
    n.add_argument("--name")
    n.add_argument("--jira")
    n.add_argument("--repos")
    n.add_argument("--branch")
    g = n.add_mutually_exclusive_group()
    g.add_argument("--worktrees")
    g.add_argument("--self-worktree", action="store_true")
    n.add_argument("--pr", type=int)
    n.add_argument("--links")
    n.add_argument("--dry-run", action="store_true")

    w = sub.add_parser("worktree")
    wsub = w.add_subparsers(dest="wcmd", required=True)
    wc = wsub.add_parser("create")
    wc.add_argument("--repo")
    wc.add_argument("--self", dest="self_repo", action="store_true")
    wc.add_argument("--branch")
    wc.add_argument("--pr", type=int)
    ws = wsub.add_parser("status")
    ws.add_argument("project")

    c = sub.add_parser("close")
    c.add_argument("project")
    c.add_argument("--dry-run", action="store_true")
    cg = c.add_mutually_exclusive_group()
    cg.add_argument("--force", action="store_true")
    cg.add_argument("--keep-worktrees", action="store_true")
    c.add_argument("--notes", default="")

    t = sub.add_parser("touch")
    t.add_argument("project")
    return p


def main() -> None:
    root = wl.resolve_workspace_root()
    if root is None:
        print(json.dumps({"status": "error", "error_message":
                          "Could not determine the workspace root. Tell the user "
                          "to launch claude inside a workspace (a directory with "
                          "dev-env.yaml). Stop."}))
        return
    try:
        a = build_parser().parse_args()
    except SystemExit:
        print(json.dumps({"status": "error", "error_message":
                          "Bad arguments; see the usage in project.py."}))
        return
    handlers = {"new": cmd_new, "close": cmd_close, "touch": cmd_touch,
                "worktree": (lambda r, x: cmd_worktree_create(r, x)
                             if x.wcmd == "create" else cmd_worktree_status(r, x))}
    try:
        result = handlers[a.cmd](root, a)
    except ProjectError as exc:
        result = exc.payload
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
