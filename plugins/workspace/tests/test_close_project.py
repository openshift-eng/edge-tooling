#!/usr/bin/env python3
"""Tests for scripts/close-project.py.

Standalone: python3 tests/test_close_project.py

Builds throwaway workspaces with real git repos (bare origin + clone under
repos/) and project CLAUDE.md files, then drives close-project.py as a
subprocess with WORKSPACE_ROOT set. Skill-linking tests need PyYAML (as does
skills.py, which close-project.py delegates to) and are skipped without it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SCRIPT = SCRIPTS / "close-project.py"

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


def git_ok(*args: str, cwd: Path) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          env=GIT_ENV).returncode == 0


def run_script(script: Path, ws: Path, *args: str) -> dict:
    res = subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True,
        env={**GIT_ENV, "WORKSPACE_ROOT": str(ws)})
    assert res.returncode == 0, (
        f"{script.name} exit {res.returncode}\n{res.stdout}\n{res.stderr}")
    return json.loads(res.stdout)


def check(ws: Path, name: str) -> dict:
    return run_script(SCRIPT, ws, "check", name)


def apply(ws: Path, name: str, *args: str) -> dict:
    return run_script(SCRIPT, ws, "apply", name, *args)


class Fixture(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="closeproj-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        (self.ws / "repos").mkdir(parents=True)
        (self.ws / "projects").mkdir()
        (self.ws / "dev-env.yaml").write_text("repos: []\n")

    def make_origin(self, name: str) -> Path:
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
        dest = self.ws / "repos" / name
        git("clone", str(self.make_origin(name)), str(dest))
        return dest

    def add_worktree(self, checkout: Path, branch: str) -> Path:
        path = checkout / ".worktrees" / branch
        git("worktree", "add", "-b", branch, "--", str(path), "origin/main",
            cwd=checkout)
        return path

    def write_project(self, name: str, fm_lines: list[str],
                      body: str = "# Demo\n\n## Progress\n\n- [x] Project created\n"
                      ) -> Path:
        pdir = self.ws / "projects" / name
        pdir.mkdir(parents=True, exist_ok=True)
        lines = ["---", f"project: {name}", "type: feature", "created: 2026-01-01",
                 "last-active: 2026-01-02T10:00", "status: active",
                 "jira: OCPBUGS-1", *fm_lines, "related_links:",
                 "  - https://example.com/x", "---", ""]
        (pdir / "CLAUDE.md").write_text("\n".join(lines) + body)
        return pdir

    def multi_project(self, name: str = "demo", repo: str = "alpha",
                      branch: str = "feat/x") -> tuple[Path, Path, Path]:
        """Project with one real worktree. Returns (project_dir, checkout, wt)."""
        checkout = self.make_repo(repo)
        wt = self.add_worktree(checkout, branch)
        pdir = self.write_project(name, [
            f"repos:\n  - {repo}", f"branch: {branch}",
            f"worktrees:\n  - {repo}"])
        return pdir, checkout, wt

    def fm(self, name: str) -> str:
        text = (self.ws / "projects" / name / "CLAUDE.md").read_text()
        return text.split("---\n")[1]

    def text(self, name: str) -> str:
        return (self.ws / "projects" / name / "CLAUDE.md").read_text()


class TestCheck(Fixture):

    def test_not_found(self):
        self.assertEqual(check(self.ws, "nope")["status"], "not_found")

    def test_path_traversal_name_is_not_found(self):
        self.assertEqual(check(self.ws, "../x")["status"], "not_found")

    def test_clean_worktree(self):
        _, _, wt = self.multi_project()
        out = check(self.ws, "demo")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["project"], {"name": "demo", "status": "active",
                                          "branch": "feat/x"})
        self.assertFalse(out["already_done"])
        self.assertEqual(len(out["worktrees"]), 1)
        w = out["worktrees"][0]
        self.assertEqual((w["repo"], w["path"], w["branch"]),
                         ("alpha", str(wt), "feat/x"))
        self.assertFalse(w["dirty"])
        self.assertEqual(w["dirty_files"], 0)
        self.assertEqual(w["ahead"], 0)
        self.assertFalse(w["no_upstream"])

    def test_dirty_files_counted(self):
        _, _, wt = self.multi_project()
        (wt / "a.txt").write_text("x")
        (wt / "b.txt").write_text("y")
        w = check(self.ws, "demo")["worktrees"][0]
        self.assertTrue(w["dirty"])
        self.assertEqual(w["dirty_files"], 2)

    def test_ahead_reported(self):
        _, _, wt = self.multi_project()
        (wt / "c.txt").write_text("c")
        git("add", ".", cwd=wt)
        git("commit", "-m", "c", cwd=wt)
        w = check(self.ws, "demo")["worktrees"][0]
        self.assertFalse(w["dirty"])
        self.assertEqual(w["ahead"], 1)
        self.assertFalse(w["no_upstream"])

    def test_no_upstream_reported(self):
        _, _, wt = self.multi_project()
        git("branch", "--unset-upstream", cwd=wt)
        w = check(self.ws, "demo")["worktrees"][0]
        self.assertTrue(w["no_upstream"])
        self.assertEqual(w["ahead"], 0)

    def test_pr_without_upstream_reports_local_commits(self):
        _, _, wt = self.multi_project(branch="pr/5")
        git("branch", "--unset-upstream", cwd=wt)
        (wt / "local.txt").write_text("local work\n")
        git("add", ".", cwd=wt)
        git("commit", "-m", "local work", cwd=wt)

        w = check(self.ws, "demo")["worktrees"][0]

        self.assertTrue(w["no_upstream"])
        self.assertFalse(w["dirty"])
        self.assertEqual(w["ahead"], 1)

    def test_missing_worktree_dir(self):
        _, _, wt = self.multi_project()
        shutil.rmtree(wt)
        w = check(self.ws, "demo")["worktrees"][0]
        self.assertFalse(w["exists"])
        self.assertFalse(w["dirty"])

    def test_self_workspace_layout(self):
        shutil.rmtree(self.ws)
        git("clone", str(self.make_origin("selfrepo")), str(self.ws))
        wt = self.ws / ".claude" / "worktrees" / "feat-s"
        git("worktree", "add", "-b", "feat-s", "--", str(wt), "origin/main",
            cwd=self.ws)
        (self.ws / "projects").mkdir()
        self.write_project("selfproj", [
            "repos: []", "branch: feat-s", f"worktree_path: {wt}"])
        w = check(self.ws, "selfproj")["worktrees"][0]
        self.assertEqual((w["repo"], w["path"], w["branch"]),
                         ("(self)", str(wt), "feat-s"))
        self.assertFalse(w["dirty"])

    def test_dict_style_worktrees(self):
        checkout = self.make_repo("alpha")
        wt = self.add_worktree(checkout, "pr/4")
        self.write_project("prproj", [
            "repos:\n  - alpha",
            "worktrees:\n  - repo: alpha\n    branch: pr/4"])
        w = check(self.ws, "prproj")["worktrees"][0]
        self.assertEqual((w["repo"], w["path"], w["branch"]),
                         ("alpha", str(wt), "pr/4"))

    def test_already_done(self):
        self.write_project("old", [])
        text = self.text("old").replace("status: active", "status: done")
        (self.ws / "projects/old/CLAUDE.md").write_text(text)
        self.assertTrue(check(self.ws, "old")["already_done"])

    @unittest.skipUnless(HAVE_YAML, "PyYAML required by skills.py")
    def test_skills_shared_vs_unshared(self):
        self.skill_setup()
        out = check(self.ws, "p1")
        by = {s["name"]: s for s in out["skills"]}
        self.assertTrue(by["common"]["shared"])
        self.assertEqual(by["common"]["used_by"], ["p2"])
        self.assertFalse(by["solo"]["shared"])

    def skill_setup(self):
        """Two projects; both reference `common`, only p1 references `solo`."""
        self.make_repo("alpha")
        for name in ("common", "solo"):
            sd = self.ws / "repos/alpha/.claude/skills" / name
            sd.mkdir(parents=True)
            (sd / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: d\n---\n")
            run_script(SCRIPTS / "skills.py", self.ws, "link", name, "alpha")
        both = ["skills:\n  - name: common\n    source: alpha\n"
                "  - name: solo\n    source: alpha"]
        self.write_project("p1", both)
        self.write_project("p2", ["skills:\n  - name: common\n    source: alpha"])


class TestApplyWorktrees(Fixture):

    def test_keep_is_default_and_removes_nothing(self):
        _, checkout, wt = self.multi_project()
        out = apply(self.ws, "demo")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["closed"])
        self.assertTrue(wt.is_dir())
        self.assertTrue(git_ok("show-ref", "--verify", "--quiet",
                               "refs/heads/feat/x", cwd=checkout))
        self.assertEqual([k["kind"] for k in out["kept"]], ["worktree"])
        self.assertEqual(out["removed"], [])
        fm = self.fm("demo")
        self.assertIn("status: done", fm)
        self.assertIn("worktrees:\n  - alpha", fm)
        self.assertIn("branch: feat/x", fm)

    def test_remove_clean_worktree_and_branch(self):
        _, checkout, wt = self.multi_project()
        out = apply(self.ws, "demo", "--worktrees", "remove")
        self.assertEqual(out["errors"], [])
        self.assertFalse(wt.exists())
        self.assertFalse(git_ok("show-ref", "--verify", "--quiet",
                                "refs/heads/feat/x", cwd=checkout))
        kinds = sorted(r["kind"] for r in out["removed"])
        self.assertEqual(kinds, ["branch", "worktree"])
        fm = self.fm("demo")
        self.assertIn("worktrees: []", fm)
        self.assertIn("branch: feat/x", fm)  # kept for history
        self.assertNotIn("  - alpha\n", fm.split("worktrees:")[1])

    def test_dirty_kept_under_remove(self):
        _, _, wt = self.multi_project()
        (wt / "dirty.txt").write_text("x")
        out = apply(self.ws, "demo", "--worktrees", "remove")
        self.assertTrue(wt.is_dir())
        self.assertEqual(out["removed"], [])
        self.assertIn("dirty", out["kept"][0]["reason"])
        self.assertIn("worktrees:\n  - alpha", self.fm("demo"))
        self.assertIn("status: done", self.fm("demo"))

    def test_ahead_kept_under_remove(self):
        _, _, wt = self.multi_project()
        (wt / "c.txt").write_text("c")
        git("add", ".", cwd=wt)
        git("commit", "-m", "c", cwd=wt)
        out = apply(self.ws, "demo", "--worktrees", "remove")
        self.assertTrue(wt.is_dir())
        self.assertIn("unpushed", out["kept"][0]["reason"])

    def test_no_upstream_kept_under_remove(self):
        _, _, wt = self.multi_project()
        git("branch", "--unset-upstream", cwd=wt)
        out = apply(self.ws, "demo", "--worktrees", "remove")
        self.assertTrue(wt.is_dir())
        self.assertIn("no upstream", out["kept"][0]["reason"])

    def test_dirty_removed_under_discard(self):
        _, checkout, wt = self.multi_project()
        (wt / "dirty.txt").write_text("x")
        (wt / "c.txt").write_text("c")
        git("add", "c.txt", cwd=wt)
        git("commit", "-m", "c", cwd=wt)
        out = apply(self.ws, "demo", "--worktrees", "discard")
        self.assertEqual(out["errors"], [])
        self.assertFalse(wt.exists())
        self.assertFalse(git_ok("show-ref", "--verify", "--quiet",
                                "refs/heads/feat/x", cwd=checkout))
        self.assertIn("worktrees: []", self.fm("demo"))

    def test_pr_branch_removed_with_force_delete(self):
        checkout = self.make_repo("alpha")
        git("branch", "pr/5", "origin/main", cwd=checkout)
        git("branch", "--unset-upstream", "pr/5", cwd=checkout)
        wt = checkout / ".worktrees" / "pr" / "5"
        git("worktree", "add", "--", str(wt), "pr/5", cwd=checkout)
        (wt / "x.txt").write_text("x")
        git("add", ".", cwd=wt)
        git("commit", "-m", "unmerged", cwd=wt)  # -d would refuse this
        self.write_project("prproj", [
            "repos:\n  - alpha", "branch: pr/5", "worktrees:\n  - alpha"])
        self.assertFalse(git_ok("rev-parse", "--verify", "@{upstream}", cwd=wt))

        out = apply(self.ws, "prproj", "--worktrees", "discard")

        self.assertEqual(out["errors"], [])
        self.assertFalse(wt.exists())
        self.assertFalse(git_ok("show-ref", "--verify", "--quiet",
                                "refs/heads/pr/5", cwd=checkout))

    def test_pr_branch_without_upstream_counts_as_clean(self):
        checkout = self.make_repo("alpha")
        git("branch", "pr/6", "origin/main", cwd=checkout)
        git("branch", "--unset-upstream", "pr/6", cwd=checkout)
        wt = checkout / ".worktrees" / "pr" / "6"
        git("worktree", "add", "--", str(wt), "pr/6", cwd=checkout)
        self.write_project("prproj", [
            "repos:\n  - alpha", "branch: pr/6", "worktrees:\n  - alpha"])
        out = apply(self.ws, "prproj", "--worktrees", "remove")
        self.assertFalse(wt.exists())
        self.assertEqual(out["errors"], [])

    def test_pr_local_commit_without_upstream_is_kept_under_remove(self):
        _, checkout, wt = self.multi_project(branch="pr/5")
        git("branch", "--unset-upstream", cwd=wt)
        (wt / "local.txt").write_text("local work\n")
        git("add", ".", cwd=wt)
        git("commit", "-m", "local work", cwd=wt)
        head = git("rev-parse", "HEAD", cwd=wt)

        out = apply(self.ws, "demo", "--worktrees", "remove")

        self.assertTrue(wt.is_dir())
        self.assertEqual(git("rev-parse", "refs/heads/pr/5", cwd=checkout), head)
        self.assertEqual(out["removed"], [])
        self.assertIn("unpushed", out["kept"][0]["reason"])
        self.assertIn("worktrees:\n  - alpha", self.fm("demo"))

    def test_worktree_already_gone_is_cleared(self):
        _, _, wt = self.multi_project()
        git("worktree", "remove", "--force", str(wt), cwd=wt.parents[1])
        out = apply(self.ws, "demo", "--worktrees", "remove")
        self.assertEqual(out["errors"], [])
        self.assertIn("worktrees: []", self.fm("demo"))

    def test_keep_with_worktree_already_gone_clears_reference(self):
        _, checkout, wt = self.multi_project()
        git("worktree", "remove", "--force", str(wt), cwd=checkout)
        apply(self.ws, "demo")
        self.assertIn("worktrees: []", self.fm("demo"))
        # branch untouched under keep
        self.assertTrue(git_ok("show-ref", "--verify", "--quiet",
                               "refs/heads/feat/x", cwd=checkout))

    def test_partial_removal_keeps_blocked_repo_listed(self):
        a = self.make_repo("alpha")
        b = self.make_repo("beta")
        wa = self.add_worktree(a, "feat/x")
        wb = self.add_worktree(b, "feat/x")
        (wb / "dirty.txt").write_text("x")
        self.write_project("two", [
            "repos:\n  - alpha\n  - beta", "branch: feat/x",
            "worktrees:\n  - alpha\n  - beta"])
        apply(self.ws, "two", "--worktrees", "remove")
        self.assertFalse(wa.exists())
        self.assertTrue(wb.exists())
        fm = self.fm("two")
        self.assertIn("worktrees:\n  - beta", fm)
        self.assertNotIn("worktrees: []", fm)

    def test_self_workspace_remove(self):
        shutil.rmtree(self.ws)
        git("clone", str(self.make_origin("selfrepo")), str(self.ws))
        wt = self.ws / ".claude" / "worktrees" / "feat-s"
        git("worktree", "add", "-b", "feat-s", "--", str(wt), "origin/main",
            cwd=self.ws)
        (self.ws / "projects").mkdir()
        self.write_project("selfproj", [
            "repos: []", "branch: feat-s", f"worktree_path: {wt}"])
        out = apply(self.ws, "selfproj", "--worktrees", "remove")
        self.assertEqual(out["errors"], [])
        self.assertFalse(wt.exists())
        self.assertFalse(git_ok("show-ref", "--verify", "--quiet",
                                "refs/heads/feat-s", cwd=self.ws))
        fm = self.fm("selfproj")
        self.assertNotIn("worktree_path", fm)
        self.assertIn("branch: feat-s", fm)

    def test_self_workspace_keep_preserves_worktree_path(self):
        shutil.rmtree(self.ws)
        git("clone", str(self.make_origin("selfrepo")), str(self.ws))
        wt = self.ws / ".claude" / "worktrees" / "feat-s"
        git("worktree", "add", "-b", "feat-s", "--", str(wt), "origin/main",
            cwd=self.ws)
        (self.ws / "projects").mkdir()
        self.write_project("selfproj", [
            "repos: []", "branch: feat-s", f"worktree_path: {wt}"])
        apply(self.ws, "selfproj", "--worktrees", "keep")
        self.assertTrue(wt.is_dir())
        self.assertIn(f"worktree_path: {wt}", self.fm("selfproj"))


class TestApplyFrontmatter(Fixture):

    def test_frontmatter_edits_and_untouched_keys(self):
        self.write_project("p", ["repos:\n  - alpha", "custom-key: keepme"])
        before = self.fm("p")
        out = apply(self.ws, "p")
        self.assertEqual(out["status"], "ok")
        fm = self.fm("p")
        self.assertIn("status: done", fm)
        self.assertRegex(fm, r"closed: \d{4}-\d{2}-\d{2}\n")
        self.assertRegex(fm, r"last-active: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}\n")
        self.assertNotIn("2026-01-02T10:00", fm)
        for kept in ("project: p", "type: feature", "created: 2026-01-01",
                     "jira: OCPBUGS-1", "repos:\n  - alpha",
                     "custom-key: keepme",
                     "related_links:\n  - https://example.com/x"):
            self.assertIn(kept, fm)
        # closed sits right after status
        self.assertRegex(fm, r"status: done\nclosed: ")
        # only status/closed/last-active changed
        removed = set(before.splitlines()) - set(fm.splitlines())
        self.assertEqual(removed, {"status: active", "last-active: 2026-01-02T10:00"})

    def test_body_untouched_without_notes(self):
        self.write_project("p", [], body="# T\n\nBody text\n")
        apply(self.ws, "p")
        self.assertTrue(self.text("p").endswith("---\n# T\n\nBody text\n"))
        self.assertNotIn("Closing Notes", self.text("p"))

    def test_notes_appended(self):
        self.write_project("p", [])
        apply(self.ws, "p", "--notes", "Fixed in PR 12.")
        text = self.text("p")
        self.assertRegex(text, r"\n## Closing Notes\n\n_Closed \d{4}-\d{2}-\d{2}_"
                               r"\n\nFixed in PR 12\.\n$")

    def test_no_notes_skip_words(self):
        self.write_project("p", [])
        apply(self.ws, "p", "--notes", "no")
        self.assertNotIn("Closing Notes", self.text("p"))

    def test_notes_replaced_when_already_done(self):
        self.write_project("p", [])
        apply(self.ws, "p", "--notes", "first")
        out = apply(self.ws, "p", "--notes", "second")
        text = self.text("p")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(text.count("## Closing Notes"), 1)
        self.assertIn("second", text)
        self.assertNotIn("first", text)

    def test_notes_replacement_preserves_following_sections(self):
        self.write_project(
            "p", [],
            body="# T\n\n## Closing Notes\n\n_Closed 2026-01-01_\n\nold\n\n"
                 "## Appendix\n\nkeep me\n")
        apply(self.ws, "p", "--notes", "new")
        text = self.text("p")
        self.assertIn("new", text)
        self.assertNotIn("old", text)
        self.assertIn("## Appendix\n\nkeep me\n", text)

    def test_idempotent_rerun(self):
        _, checkout, wt = self.multi_project()
        apply(self.ws, "demo", "--worktrees", "remove", "--notes", "done")
        first = self.fm("demo")
        first_body = self.text("demo").split("---\n", 2)[2]
        out = apply(self.ws, "demo", "--worktrees", "remove", "--notes", "done")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["removed"], [])
        norm = lambda s: re.sub(r"last-active: \S+", "", s)  # noqa: E731
        self.assertEqual(norm(first), norm(self.fm("demo")))
        self.assertEqual(first_body, self.text("demo").split("---\n", 2)[2])
        self.assertTrue((self.ws / "projects/demo").is_dir())

    def test_rerun_without_notes_keeps_original_closed_date(self):
        self.write_project("p", [])
        apply(self.ws, "p")
        text = self.text("p").replace(
            re.search(r"closed: \S+", self.text("p")).group(0),
            "closed: 2026-02-02")
        (self.ws / "projects/p/CLAUDE.md").write_text(text)
        apply(self.ws, "p")
        self.assertIn("closed: 2026-02-02", self.fm("p"))

    def test_project_dir_never_deleted(self):
        pdir = self.write_project("p", [])
        (pdir / "notes.md").write_text("n")
        apply(self.ws, "p", "--worktrees", "discard")
        self.assertTrue((pdir / "notes.md").is_file())

    def test_not_found(self):
        self.assertEqual(apply(self.ws, "nope")["status"], "not_found")

    def test_handoff_marker_cleared_for_project_only(self):
        self.write_project("p", [])
        self.write_project("other", [])
        marker = self.ws / ".claude" / "handoff.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"version": 1, "project": "other",
                                      "written_at": "2026-01-01T00:00:00",
                                      "next_task": "x", "load_files": []}))
        apply(self.ws, "p")
        self.assertTrue(marker.exists())
        marker.write_text(json.dumps({"version": 1, "project": "p",
                                      "written_at": "2026-01-01T00:00:00",
                                      "next_task": "x", "load_files": []}))
        apply(self.ws, "p")
        self.assertFalse(marker.exists())

    def test_skills_empty_list_untouched_when_none(self):
        self.write_project("p", [])
        apply(self.ws, "p")
        self.assertNotIn("skills:", self.fm("p"))

    def multi_project(self):
        checkout = self.make_repo("alpha")
        wt = self.add_worktree(checkout, "feat/x")
        self.write_project("demo", ["repos:\n  - alpha", "branch: feat/x",
                                    "worktrees:\n  - alpha"])
        return None, checkout, wt


@unittest.skipUnless(HAVE_YAML, "PyYAML required by skills.py")
class TestApplySkills(Fixture):

    skill_setup = TestCheck.skill_setup

    def test_unshared_removed_shared_kept(self):
        self.skill_setup()
        out = apply(self.ws, "p1")
        self.assertEqual(out["errors"], [])
        skills_dir = self.ws / ".claude" / "skills"
        self.assertFalse(os.path.lexists(skills_dir / "solo"))
        self.assertTrue((skills_dir / "common").is_symlink())
        self.assertEqual([r["name"] for r in out["removed"]
                          if r["kind"] == "skill"], ["solo"])
        kept = [k for k in out["kept"] if k["kind"] == "skill"]
        self.assertEqual([k["name"] for k in kept], ["common"])
        self.assertIn("p2", kept[0]["reason"])
        self.assertIn("skills: []", self.fm("p1"))

    def test_last_user_removes_shared_skill(self):
        self.skill_setup()
        apply(self.ws, "p1")
        out = apply(self.ws, "p2")
        self.assertFalse(os.path.lexists(self.ws / ".claude/skills/common"))
        self.assertEqual([r["name"] for r in out["removed"]], ["common"])

    def test_skills_unlinked_even_when_worktrees_kept(self):
        self.skill_setup()
        apply(self.ws, "p1", "--worktrees", "keep")
        self.assertFalse(os.path.lexists(self.ws / ".claude/skills/solo"))

    def test_non_symlink_entry_left_alone(self):
        self.skill_setup()
        entry = self.ws / ".claude/skills/solo"
        entry.unlink()
        entry.mkdir()
        out = apply(self.ws, "p1")
        self.assertTrue(entry.is_dir())
        self.assertTrue(any(k["name"] == "solo" for k in out["kept"]))


if __name__ == "__main__":
    unittest.main()
