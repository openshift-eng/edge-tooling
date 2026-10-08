#!/usr/bin/env python3
"""Tests for scripts/new-project.py.

Standalone: python3 tests/test_new_project.py

Each test builds a throwaway workspace in a temp dir, with real git repos
(a bare "origin" plus a clone under repos/) so worktree creation is exercised
for real. new-project.py runs as a subprocess with WORKSPACE_ROOT set.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "new-project.py"

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(*args: str, cwd: Path | None = None) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                         text=True, env=GIT_ENV)
    assert res.returncode == 0, f"git {args} failed: {res.stderr}"
    return res.stdout.strip()


def run_create(ws: Path, payload: dict | str, *flags: str) -> dict:
    data = payload if isinstance(payload, str) else json.dumps(payload)
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "create", *flags], input=data,
        capture_output=True, text=True,
        env={**GIT_ENV, "WORKSPACE_ROOT": str(ws)})
    assert res.returncode == 0, (
        f"exit {res.returncode}\nstdout: {res.stdout}\nstderr: {res.stderr}")
    return json.loads(res.stdout)


def frontmatter(path: Path) -> list[str]:
    lines = path.read_text().splitlines()
    return lines[1:lines.index("---", 1)]


class Fixture(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="newproj-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        (self.ws / "repos").mkdir(parents=True)
        (self.ws / "dev-env.yaml").write_text(
            "domain: demo\nrepos:\n  - name: alpha\n")

    def make_origin(self, name: str) -> Path:
        """Bare origin with one commit on main."""
        bare = self.tmp / "origins" / f"{name}.git"
        bare.mkdir(parents=True)
        git("init", "--bare", "-b", "main", cwd=bare)
        seed = self.tmp / "seeds" / name
        seed.mkdir(parents=True)
        git("init", "-b", "main", cwd=seed)
        (seed / "README.md").write_text("hi\n")
        git("add", ".", cwd=seed)
        git("commit", "-m", "init", cwd=seed)
        git("remote", "add", "origin", str(bare), cwd=seed)
        git("push", "origin", "main", cwd=seed)
        return bare

    def make_repo(self, name: str) -> Path:
        bare = self.make_origin(name)
        dest = self.ws / "repos" / name
        git("clone", str(bare), str(dest))
        return dest

    def add_pr_ref(self, repo_name: str, number: int) -> None:
        seed = self.tmp / "seeds" / repo_name
        git("checkout", "-b", f"prbranch{number}", cwd=seed)
        (seed / f"pr{number}.txt").write_text("pr\n")
        git("add", ".", cwd=seed)
        git("commit", "-m", f"pr {number}", cwd=seed)
        git("push", "origin", f"HEAD:refs/pull/{number}/head", cwd=seed)

    def project(self, folder: str) -> Path:
        return self.ws / "projects" / folder


class TestTypes(Fixture):

    def create(self, type_: str, **extra) -> dict:
        out = run_create(self.ws, {"description": "Fix the thing. More text.",
                                   "type": type_, **extra})
        self.assertEqual(out["status"], "ok", out)
        return out

    def test_each_type_scaffold(self):
        expected = {
            "bug": ("## Bug Summary", "## Fix Plan", ["logs", "docs"],
                    ["investigation.md", "ci-runs.md", "source-code-map.md"]),
            "feature": ("## Feature Summary", "## Implementation Plan",
                        ["docs", "patches"], ["design.md", "source-code-map.md"]),
            "ci-testing": ("## Test Summary", "## Test Plan",
                           ["results", "scripts"],
                           ["ci-runs.md", "test-failures.md"]),
            "docs": ("## Doc Summary", "## Outline", ["drafts"], ["drafts.md"]),
            "analysis": ("## Analysis Summary", "## Analysis Plan", ["docs"],
                         ["findings.md"]),
        }
        for type_, (summary, plan, dirs, files) in expected.items():
            with self.subTest(type=type_):
                out = self.create(type_, folder=f"proj-{type_}")
                pdir = Path(out["path"])
                self.assertEqual(pdir, self.project(f"proj-{type_}"))
                text = (pdir / "CLAUDE.md").read_text()
                self.assertIn(summary, text)
                self.assertIn(plan, text)
                self.assertIn("- [x] Project created", text)
                self.assertIn("## Reference Files", text)
                for d in dirs:
                    self.assertTrue((pdir / d).is_dir(), d)
                for f in files:
                    self.assertTrue((pdir / f).is_file(), f)
                    self.assertIn(f"`{f}`", text)
                self.assertIn("*.tar.gz", (pdir / ".gitignore").read_text())
                fm = frontmatter(pdir / "CLAUDE.md")
                self.assertIn(f"type: {type_}", fm)
                self.assertIn("status: active", fm)
                self.assertIn("jira: none", fm)
                self.assertIn("domain: demo", fm)
                self.assertIn("related_links: []", fm)
                self.assertEqual(out["worktrees"], [])

    def test_folder_from_description_slug(self):
        out = self.create("docs", description="Write the Install Guide!")
        self.assertEqual(out["folder"], "write-the-install-guide")

    def test_long_description_slug_under_40(self):
        out = run_create(self.ws, {
            "description": "Fix kubelet start timeout after fencing happens "
                           "on every single node", "type": "docs"})
        self.assertLess(len(out["folder"]), 40)
        self.assertFalse(out["folder"].endswith("-"))

    def test_folder_from_jira_id(self):
        out = self.create(
            "analysis",
            jira="https://issues.redhat.com/browse/OCPBUGS-12345")
        self.assertEqual(out["folder"], "OCPBUGS-12345")
        fm = frontmatter(self.project("OCPBUGS-12345") / "CLAUDE.md")
        self.assertIn("jira: https://issues.redhat.com/browse/OCPBUGS-12345", fm)

    def test_folder_collision_appends_suffix(self):
        first = self.create("docs", description="Same task")
        second = self.create("docs", description="Same task")
        third = self.create("docs", description="Same task")
        self.assertEqual(first["folder"], "same-task")
        self.assertEqual(second["folder"], "same-task-2")
        self.assertEqual(third["folder"], "same-task-3")

    def test_explicit_folder_collision_is_error(self):
        self.create("docs", folder="mine")
        out = run_create(self.ws, {"description": "x", "type": "docs",
                                   "folder": "mine"})
        self.assertEqual(out["status"], "error")
        self.assertIn("already exists", out["error_message"])

    def test_repos_and_links_and_skills_in_frontmatter(self):
        self.make_repo("alpha")
        out = self.create("ci-testing", repos=["alpha"],
                          links=["https://example.com/a b"],
                          skills=[{"name": "demo", "source": "alpha"}])
        fm = frontmatter(Path(out["path"]) / "CLAUDE.md")
        self.assertIn("  - alpha", fm)
        self.assertIn("skills:", fm)
        self.assertIn("  - name: demo", fm)
        self.assertIn("    source: alpha", fm)
        self.assertIn('  - "https://example.com/a b"', fm)
        # ci-testing: no worktrees by default
        self.assertEqual(out["worktrees"], [])
        self.assertNotIn("worktrees:", fm)

    def test_source_code_map_has_row_per_repo(self):
        self.make_repo("alpha")
        self.make_repo("beta")
        out = self.create("feature", repos=["alpha", "beta"], no_worktree=True)
        text = (Path(out["path"]) / "source-code-map.md").read_text()
        self.assertIn("| alpha |", text)
        self.assertIn("| beta |", text)

    def test_invalid_payloads(self):
        for payload in ({"type": "bug"},
                        {"description": "x", "type": "nope"},
                        {"description": "x", "type": "bug", "repos": ["../x"]},
                        {"description": "x", "type": "bug", "folder": "a/b"},
                        "not json"):
            with self.subTest(payload=payload):
                out = run_create(self.ws, payload)
                self.assertEqual(out["status"], "error")
        self.assertFalse((self.ws / "projects").exists())


class TestWorktrees(Fixture):

    def test_bug_default_branch_and_worktree(self):
        repo = self.make_repo("alpha")
        out = run_create(self.ws, {
            "description": "Port race", "type": "bug", "repos": ["alpha"],
            "jira": "OCPBUGS-84336"})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["errors"], [])
        branch = "fix/ocpbugs-84336"
        wt_path = self.ws / "repos" / "alpha" / ".worktrees" / branch
        self.assertEqual(out["worktrees"], [
            {"repo": "alpha", "path": str(wt_path), "branch": branch}])
        self.assertTrue((wt_path / "README.md").is_file())
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path),
                         branch)
        exclude = (repo / ".git" / "info" / "exclude").read_text()
        self.assertIn(".worktrees/", exclude)
        fm = frontmatter(self.project("OCPBUGS-84336") / "CLAUDE.md")
        self.assertIn(f"branch: {branch}", fm)
        self.assertIn("worktrees:", fm)
        self.assertIn("  - alpha", fm)

    def test_explicit_branch_and_subset(self):
        self.make_repo("alpha")
        self.make_repo("beta")
        out = run_create(self.ws, {
            "description": "Add thing", "type": "feature",
            "repos": ["alpha", "beta"], "worktree_repos": ["beta"],
            "branch": "feat/thing"})
        self.assertEqual([w["repo"] for w in out["worktrees"]], ["beta"])
        self.assertTrue((self.ws / "repos/beta/.worktrees/feat/thing").is_dir())
        self.assertFalse((self.ws / "repos/alpha/.worktrees").exists())
        fm = frontmatter(self.project("add-thing") / "CLAUDE.md")
        self.assertIn("branch: feat/thing", fm)
        self.assertIn("  - alpha", fm)  # still listed under repos:
        self.assertEqual(fm.count("  - beta"), 2)  # repos + worktrees

    def test_no_branch_defaults_to_folder_name(self):
        self.make_repo("alpha")
        out = run_create(self.ws, {"description": "Add thing",
                                   "type": "docs", "repos": ["alpha"]})
        self.assertEqual(out["worktrees"][0]["branch"], "add-thing")

    def test_exclude_entry_not_duplicated(self):
        repo = self.make_repo("alpha")
        for desc in ("one", "two"):
            run_create(self.ws, {"description": desc, "type": "docs",
                                 "repos": ["alpha"]})
        exclude = (repo / ".git" / "info" / "exclude").read_text()
        self.assertEqual(exclude.count(".worktrees/"), 1)

    def test_ci_testing_gets_no_worktree(self):
        self.make_repo("alpha")
        out = run_create(self.ws, {"description": "ci", "type": "ci-testing",
                                   "repos": ["alpha"]})
        self.assertEqual(out["worktrees"], [])
        self.assertFalse((self.ws / "repos/alpha/.worktrees").exists())

    def test_no_worktree_flag(self):
        self.make_repo("alpha")
        out = run_create(self.ws, {"description": "x", "type": "feature",
                                   "repos": ["alpha"], "no_worktree": True})
        self.assertEqual(out["worktrees"], [])

    def test_pr_flow(self):
        checkout = self.make_repo("alpha")
        self.add_pr_ref("alpha", 7)

        out = run_create(self.ws, {
            "description": "Review PR", "type": "analysis",
            "repos": ["alpha"], "pr": {"alpha": 7},
            "links": ["https://github.com/o/alpha/pull/7"]})

        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["errors"], [])
        path = self.ws / "repos/alpha/.worktrees/pr/7"
        self.assertEqual(out["worktrees"], [
            {"repo": "alpha", "path": str(path), "branch": "pr/7"}])
        self.assertTrue((path / "pr7.txt").is_file())
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=path),
                         "pr/7")
        head = git("rev-parse", "HEAD", cwd=path)
        self.assertEqual(git("rev-parse", "refs/remotes/origin/pr/7", cwd=checkout), head)
        self.assertEqual(git("rev-list", "--count", "origin/main..HEAD", cwd=path), "1")

        git("fetch", "origin", "main", cwd=checkout)

        self.assertEqual(git("rev-parse", "refs/remotes/origin/pr/7", cwd=checkout), head)
        fm = frontmatter(self.project("review-pr") / "CLAUDE.md")
        self.assertIn("branch: pr/7", fm)
        self.assertIn("  - https://github.com/o/alpha/pull/7", fm)

    def test_close_removes_a_clean_fetched_unmerged_pr(self):
        checkout = self.make_repo("alpha")
        self.add_pr_ref("alpha", 7)
        out = run_create(self.ws, {"description": "Review PR", "type": "analysis",
                                   "repos": ["alpha"], "pr": {"alpha": 7}})
        self.assertEqual(out["errors"], [])
        path = Path(out["worktrees"][0]["path"])
        upstream = subprocess.run(["git", "rev-parse", "--verify", "@{upstream}"],
                                  cwd=path, capture_output=True, env=GIT_ENV)
        self.assertNotEqual(upstream.returncode, 0)
        self.assertEqual(git("rev-list", "--count", "origin/main..HEAD", cwd=path), "1")
        git("fetch", "origin", "main", cwd=checkout)

        res = subprocess.run(
            [sys.executable, str(SCRIPT.with_name("close-project.py")), "apply",
             out["folder"], "--worktrees", "remove"], capture_output=True, text=True,
            env={**GIT_ENV, "WORKSPACE_ROOT": str(self.ws)})

        self.assertEqual(res.returncode, 0, res.stderr)
        closed = json.loads(res.stdout)
        self.assertEqual(closed["errors"], [])
        self.assertFalse(path.exists())
        self.assertEqual(sorted(r["kind"] for r in closed["removed"]),
                         ["branch", "worktree"])

    def test_pr_flow_adds_repo_not_listed(self):
        self.make_repo("alpha")
        self.add_pr_ref("alpha", 3)
        out = run_create(self.ws, {"description": "Review", "type": "analysis",
                                   "pr": {"alpha": 3}})
        fm = frontmatter(Path(out["path"]) / "CLAUDE.md")
        self.assertIn("repos:", fm)
        self.assertIn("  - alpha", fm)

    def test_pr_failure_does_not_block(self):
        self.make_repo("alpha")  # no refs/pull/9/head upstream
        out = run_create(self.ws, {"description": "Review", "type": "analysis",
                                   "repos": ["alpha"], "pr": {"alpha": 9}})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["worktrees"], [])
        self.assertEqual(len(out["errors"]), 1)
        self.assertIn("PR 9", out["errors"][0])
        fm = frontmatter(Path(out["path"]) / "CLAUDE.md")
        self.assertNotIn("branch:", "\n".join(fm))
        self.assertNotIn("worktrees:", fm)

    def test_worktree_failure_does_not_block_creation(self):
        # repos/ghost does not exist; alpha is fine
        self.make_repo("alpha")
        out = run_create(self.ws, {
            "description": "Partial", "type": "feature",
            "repos": ["alpha", "ghost"], "branch": "feat/p"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual([w["repo"] for w in out["worktrees"]], ["alpha"])
        self.assertEqual(len(out["errors"]), 1)
        self.assertIn("ghost", out["errors"][0])
        pdir = Path(out["path"])
        self.assertTrue((pdir / "CLAUDE.md").is_file())
        fm = frontmatter(pdir / "CLAUDE.md")
        self.assertIn("worktrees:", fm)
        self.assertNotIn("  - ghost", fm[fm.index("worktrees:"):])

    def test_existing_branch_fails_softly(self):
        repo = self.make_repo("alpha")
        git("branch", "feat/dup", cwd=repo)
        out = run_create(self.ws, {"description": "d", "type": "feature",
                                   "repos": ["alpha"], "branch": "feat/dup"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["worktrees"], [])
        self.assertEqual(len(out["errors"]), 1)
        self.assertTrue((Path(out["path"]) / "CLAUDE.md").is_file())

    def test_invalid_branches_rejected_before_writing(self):
        self.make_repo("alpha")
        for bad in ("-evil", "a..b", "has space", "x.lock", "a/b.lock",
                    "/abs", "a//b", "x~1", "tab\tx"):
            with self.subTest(branch=bad):
                out = run_create(self.ws, {
                    "description": "d", "type": "feature",
                    "repos": ["alpha"], "branch": bad})
                self.assertEqual(out["status"], "error")
                self.assertIn("branch", out["error_message"])
        self.assertFalse((self.ws / "projects").exists())
        self.assertFalse((self.ws / "repos/alpha/.worktrees").exists())


class TestSelfWorkspace(Fixture):

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.ws)
        bare = self.make_origin("selfrepo")
        git("clone", str(bare), str(self.ws))
        (self.ws / "dev-env.yaml").write_text(
            "self:\n  name: selfrepo\n  summary: demo\nrepos: []\n")

    def test_self_workspace_worktree(self):
        out = run_create(self.ws, {"description": "Add thing",
                                   "type": "feature", "repos": ["ignored"]})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["errors"], [])
        path = self.ws / ".claude" / "worktrees" / "add-thing"
        self.assertEqual(out["worktrees"], [
            {"repo": "(self)", "path": str(path), "branch": "add-thing"}])
        self.assertTrue((path / "README.md").is_file())
        exclude = (self.ws / ".git/info/exclude").read_text()
        self.assertIn(".claude/worktrees/", exclude)
        fm = frontmatter(self.project("add-thing") / "CLAUDE.md")
        self.assertIn("branch: add-thing", fm)
        self.assertIn(f"worktree_path: {path}", fm)
        self.assertIn("repos: []", fm)
        self.assertNotIn("worktrees:", fm)
        self.assertFalse(any(ln.startswith("domain:") for ln in fm))

    def test_self_workspace_bug_prefix_and_analysis_skip(self):
        out = run_create(self.ws, {"description": "Crash", "type": "bug",
                                   "jira": "OCPBUGS-1"})
        self.assertEqual(out["worktrees"][0]["branch"], "fix/ocpbugs-1")
        out = run_create(self.ws, {"description": "Look around",
                                   "type": "analysis",
                                   "pr": {"x": 5}})
        self.assertEqual(out["worktrees"], [])
        fm = frontmatter(Path(out["path"]) / "CLAUDE.md")
        self.assertNotIn("worktree_path", "\n".join(fm))
        self.assertIn("repos: []", fm)

    def test_self_workspace_failure_does_not_block(self):
        git("worktree", "add", "-b", "taken", "--",
            str(self.tmp / "other"), "origin/main", cwd=self.ws)
        out = run_create(self.ws, {"description": "d", "type": "feature",
                                   "branch": "taken"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["worktrees"], [])
        self.assertEqual(len(out["errors"]), 1)
        fm = "\n".join(frontmatter(Path(out["path"]) / "CLAUDE.md"))
        self.assertNotIn("worktree_path", fm)


class TestDryRun(Fixture):

    def test_dry_run_writes_nothing(self):
        repo = self.make_repo("alpha")
        before = git("branch", "--list", cwd=repo)
        out = run_create(self.ws, {
            "description": "Port race", "type": "bug", "repos": ["alpha"]},
            "--dry-run")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["folder"], "port-race")
        self.assertEqual(out["worktrees"][0]["branch"], "fix/port-race")
        self.assertFalse((self.ws / "projects").exists())
        self.assertFalse((repo / ".worktrees").exists())
        self.assertEqual(git("branch", "--list", cwd=repo), before)
        self.assertNotIn(".worktrees", (repo / ".git/info/exclude").read_text())

    def test_dry_run_reports_collision_folder(self):
        run_create(self.ws, {"description": "Dup", "type": "docs"})
        out = run_create(self.ws, {"description": "Dup", "type": "docs"},
                         "--dry-run")
        self.assertEqual(out["folder"], "dup-2")
        self.assertFalse((self.ws / "projects" / "dup-2").exists())

    def test_dry_run_flags_missing_repo(self):
        out = run_create(self.ws, {"description": "x", "type": "feature",
                                   "repos": ["ghost"]}, "--dry-run")
        self.assertEqual(out["worktrees"], [])
        self.assertEqual(len(out["errors"]), 1)


if __name__ == "__main__":
    unittest.main()
