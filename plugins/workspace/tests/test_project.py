#!/usr/bin/env python3
"""Tests for scripts/project.py and scripts/resume-project.py.

Standalone: python3 tests/test_project.py
Requires PyYAML and git. Each test builds a throwaway workspace with a bare
"origin" and a clone under repos/, and drives the scripts as subprocesses
with WORKSPACE_ROOT set.
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

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import workspace_lib  # noqa: E402


def git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def make_origin_and_clone(base: Path, dest: Path) -> None:
    src = base / f"src-{dest.name}"
    src.mkdir(parents=True)
    git(src, "init", "-q", "-b", "main")
    git(src, "config", "user.email", "t@t")
    git(src, "config", "user.name", "t")
    git(src, "config", "commit.gpgsign", "false")
    (src / "README.md").write_text("x\n")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "init")
    subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True)
    git(dest, "config", "user.email", "t@t")
    git(dest, "config", "user.name", "t")
    git(dest, "config", "commit.gpgsign", "false")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="project-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = self.tmp / "ws"
        (self.ws / "repos").mkdir(parents=True)
        (self.ws / "projects").mkdir()
        (self.ws / "dev-env.yaml").write_text(
            "domain:\n  name: demo\n  source: bundled\nrepos:\n  - name: r1\n")
        make_origin_and_clone(self.tmp, self.ws / "repos" / "r1")

    def run_script(self, script: str, *args: str) -> dict:
        r = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                           capture_output=True, text=True,
                           env={**os.environ, "WORKSPACE_ROOT": str(self.ws)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def project(self, *args: str) -> dict:
        return self.run_script("project.py", *args)


class TestNew(Fixture):
    def test_name_from_jira_and_frontmatter(self):
        out = self.project("new", "--type", "bug", "--title", "Port race",
                           "--jira", "https://issues.example.com/browse/OCPBUGS-123",
                           "--repos", "r1")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["name"], "OCPBUGS-123")
        fm = workspace_lib.parse_frontmatter(Path(out["claude_md"]))
        self.assertEqual(fm["type"], "bug")
        self.assertEqual(fm["status"], "active")
        self.assertEqual(fm["domain"], "demo")
        self.assertEqual(fm["repos"], ["r1"])
        self.assertNotIn("branch", fm)
        text = Path(out["claude_md"]).read_text()
        for heading in ("## Summary", "## Reference Files", "## Progress"):
            self.assertIn(heading, text)
        self.assertIn("| File | Content |", text)
        self.assertTrue((Path(out["dir"]) / ".gitignore").is_file())

    def test_name_from_title_slug(self):
        out = self.project("new", "--type", "analysis", "--dry-run",
                           "--title", "Review the etcd quorum guard!")
        self.assertEqual(out["name"], "review-the-etcd-quorum-guard")
        self.assertFalse(Path(out["dir"]).exists())

    def test_collision_suggests_new_name(self):
        (self.ws / "projects" / "demo").mkdir()
        out = self.project("new", "--type", "docs", "--title", "Demo")
        self.assertEqual(out["status"], "exists")
        self.assertEqual(out["suggestion"], "demo-2")
        self.assertIn("error_message", out)

    def test_worktree_created_and_recorded(self):
        out = self.project("new", "--type", "feature", "--title", "Feat",
                           "--repos", "r1", "--worktrees", "r1", "--branch", "feat-x")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["worktrees"], ["r1"])
        wt = self.ws / "repos" / "r1" / ".worktrees" / "feat-x"
        self.assertTrue(wt.is_dir())
        exclude = (self.ws / "repos" / "r1" / ".git" / "info" / "exclude").read_text()
        self.assertIn(".worktrees/", exclude)
        fm = workspace_lib.parse_frontmatter(Path(out["claude_md"]))
        self.assertEqual(fm["branch"], "feat-x")
        self.assertEqual(fm["worktrees"], ["r1"])

    def test_failed_worktree_is_a_warning(self):
        out = self.project("new", "--type", "feature", "--title", "Feat",
                           "--worktrees", "nope", "--branch", "b")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["worktrees"], [])
        self.assertTrue(out["warnings"])
        fm = workspace_lib.parse_frontmatter(Path(out["claude_md"]))
        self.assertNotIn("worktrees", fm)

    def test_bad_type(self):
        out = self.project("new", "--type", "chore", "--title", "x")
        self.assertEqual(out["status"], "error")


class TestWorktree(Fixture):
    def test_create_is_idempotent_and_reuses_branch(self):
        a = self.project("worktree", "create", "--repo", "r1", "--branch", "b1")
        self.assertEqual(a["status"], "ok", a)
        self.assertFalse(a["existed"])
        b = self.project("worktree", "create", "--repo", "r1", "--branch", "b1")
        self.assertTrue(b["existed"])
        git(self.ws / "repos" / "r1", "worktree", "remove", a["path"])
        c = self.project("worktree", "create", "--repo", "r1", "--branch", "b1")
        self.assertEqual(c["status"], "ok", c)

    def test_self_worktree(self):
        shutil.rmtree(self.ws / "repos")
        selfrepo = self.tmp / "selfws"
        make_origin_and_clone(self.tmp, selfrepo)
        (selfrepo / "dev-env.yaml").write_text("self:\n  name: s\nrepos: []\n")
        self.ws = selfrepo
        out = self.project("worktree", "create", "--self", "--branch", "fix/x")
        self.assertEqual(out["status"], "ok", out)
        self.assertTrue((selfrepo / ".claude" / "worktrees" / "fix" / "x").is_dir())

    def test_status_reports_dirty(self):
        out = self.project("new", "--type", "feature", "--title", "F",
                           "--worktrees", "r1", "--branch", "f")
        wt = self.ws / "repos" / "r1" / ".worktrees" / "f"
        (wt / "new.txt").write_text("x")
        st = self.project("worktree", "status", out["name"])
        self.assertEqual(len(st["worktrees"]), 1)
        self.assertTrue(st["worktrees"][0]["needs_attention"])
        self.assertIn("dirty (1 files)", st["worktrees"][0]["status_text"])


class TestClose(Fixture):
    def new_with_worktree(self) -> dict:
        return self.project("new", "--type", "feature", "--title", "F",
                            "--repos", "r1", "--worktrees", "r1", "--branch", "f")

    def test_clean_close_removes_worktree_and_updates_frontmatter(self):
        p = self.new_with_worktree()
        dry = self.project("close", p["name"], "--dry-run")
        self.assertEqual(dry["needs_attention"], [])
        out = self.project("close", p["name"], "--notes", "Merged.")
        self.assertEqual(out["status"], "ok", out)
        self.assertFalse((self.ws / "repos" / "r1" / ".worktrees" / "f").exists())
        fm = workspace_lib.parse_frontmatter(Path(p["claude_md"]))
        self.assertEqual(fm["status"], "done")
        self.assertIn("closed", fm)
        self.assertEqual(fm["worktrees"], [])
        self.assertIn("## Closing Notes", Path(p["claude_md"]).read_text())

    def test_dirty_worktree_refused_without_flag(self):
        p = self.new_with_worktree()
        (self.ws / "repos" / "r1" / ".worktrees" / "f" / "x.txt").write_text("x")
        out = self.project("close", p["name"])
        self.assertEqual(out["status"], "needs_attention")
        self.assertTrue((self.ws / "repos" / "r1" / ".worktrees" / "f").is_dir())
        fm = workspace_lib.parse_frontmatter(Path(p["claude_md"]))
        self.assertEqual(fm["status"], "active")

    def test_keep_worktrees(self):
        p = self.new_with_worktree()
        (self.ws / "repos" / "r1" / ".worktrees" / "f" / "x.txt").write_text("x")
        out = self.project("close", p["name"], "--keep-worktrees")
        self.assertEqual(out["status"], "ok")
        self.assertTrue((self.ws / "repos" / "r1" / ".worktrees" / "f").is_dir())
        fm = workspace_lib.parse_frontmatter(Path(p["claude_md"]))
        self.assertEqual(fm["status"], "done")
        self.assertEqual(fm["worktrees"], ["r1"])

    def test_force_discards(self):
        p = self.new_with_worktree()
        (self.ws / "repos" / "r1" / ".worktrees" / "f" / "x.txt").write_text("x")
        out = self.project("close", p["name"], "--force")
        self.assertEqual(out["status"], "ok", out)
        self.assertFalse((self.ws / "repos" / "r1" / ".worktrees" / "f").exists())


class TestTouch(Fixture):
    def test_stamps_last_active_and_reports_lines(self):
        p = self.project("new", "--type", "docs", "--title", "D")
        text = Path(p["claude_md"]).read_text()
        Path(p["claude_md"]).write_text(
            workspace_lib.set_frontmatter_field(text, "last-active", "2000-01-01T00:00"))
        out = self.project("touch", p["name"])
        self.assertEqual(out["status"], "ok")
        self.assertFalse(out["over_cap"])
        fm = workspace_lib.parse_frontmatter(Path(p["claude_md"]))
        self.assertNotEqual(fm["last-active"], "2000-01-01T00:00")

    def test_over_cap_runs_consolidation(self):
        p = self.project("new", "--type", "docs", "--title", "D")
        claude_md = Path(p["claude_md"])
        items = "\n".join(f"- [x] item {i}" for i in range(120))
        claude_md.write_text(claude_md.read_text().replace(
            "- [x] Project created", items))
        out = self.project("touch", p["name"])
        self.assertEqual(out["consolidation"]["status"], "consolidated")
        self.assertFalse(out["over_cap"])
        self.assertTrue((Path(p["dir"]) / "progress-archive.md").is_file())


class TestResumeProject(Fixture):
    def test_ok_has_worktree_status_and_no_skill_suggestions(self):
        p = self.project("new", "--type", "feature", "--title", "F",
                         "--repos", "r1", "--worktrees", "r1", "--branch", "f")
        out = self.run_script("resume-project.py", p["name"])
        self.assertEqual(out["status"], "ok")
        proj = out["project"]
        self.assertNotIn("skill_suggestions", proj)
        self.assertEqual(proj["worktree_status"][0]["status_text"], "clean")

    def test_no_projects_numeric(self):
        out = self.run_script("resume-project.py", "1")
        self.assertEqual(out["status"], "no_projects")
        self.assertIn("new-project", out["error_message"])

    def test_not_found_has_instructions(self):
        self.project("new", "--type", "docs", "--title", "D")
        out = self.run_script("resume-project.py", "nope")
        self.assertEqual(out["status"], "not_found")
        self.assertIn("alternatives", out["error_message"])
        self.assertEqual(out["alternatives"], ["d"])

    def test_out_of_range(self):
        self.project("new", "--type", "docs", "--title", "D")
        out = self.run_script("resume-project.py", "5")
        self.assertEqual(out["status"], "out_of_range")

    def test_no_argument(self):
        self.project("new", "--type", "docs", "--title", "D")
        out = self.run_script("resume-project.py")
        self.assertEqual(out["status"], "no_argument")
        self.assertEqual(out["alternatives"], ["d"])


class TestFrontmatterHelpers(unittest.TestCase):
    TEXT = "---\nproject: x\nstatus: active\nworktrees:\n  - a\n---\n# body\n"

    def test_replace_block_list(self):
        out = workspace_lib.set_frontmatter_field(self.TEXT, "worktrees", "[]")
        self.assertIn("worktrees: []\n---", out)

    def test_insert_after(self):
        out = workspace_lib.set_frontmatter_field(self.TEXT, "closed", "2026-01-01",
                                                  after="status")
        self.assertIn("status: active\nclosed: 2026-01-01\n", out)

    def test_remove(self):
        out = workspace_lib.set_frontmatter_field(self.TEXT, "worktrees", None)
        self.assertNotIn("worktrees", out)
        self.assertTrue(out.endswith("---\n# body\n"))

    def test_no_frontmatter_unchanged(self):
        self.assertEqual(workspace_lib.set_frontmatter_field("# x\n", "a", "b"), "# x\n")


if __name__ == "__main__":
    unittest.main()
