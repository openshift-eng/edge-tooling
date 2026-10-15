#!/usr/bin/env python3
"""Tests for scripts/projects.py. Standalone: python3 tests/test_projects.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "projects.py"


def run(ws: Path | None) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("WORKSPACE_ROOT", "CLAUDE_PROJECT_DIR")}
    if ws is not None:
        env["WORKSPACE_ROOT"] = str(ws)
    else:
        env["CLAUDE_PROJECT_DIR"] = ""
    r = subprocess.run([sys.executable, str(SCRIPT), "list"], capture_output=True,
                       text=True, env=env, cwd=tempfile.gettempdir())
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name).resolve()

    def proj(self, name, content):
        d = self.ws / "projects" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "CLAUDE.md").write_text(content)
        return d / "CLAUDE.md"

    def by_name(self, res):
        return {p["name"]: p for p in res["projects"]}

    def test_no_projects_dir(self):
        res = run(self.ws)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["projects"], [])
        self.assertFalse(res["managed"])
        self.assertEqual(res["root"], str(self.ws))

    def test_managed_flag(self):
        (self.ws / "dev-env.yaml").write_text("x: 1\n")
        self.assertTrue(run(self.ws)["managed"])

    def test_full_entry_and_tasks(self):
        self.proj("a", "---\nproject: Alpha\ntype: bug\nstatus: active\n"
                       "last-active: 2026-01-02T10:00\njira: ABC-1\nbranch: fix\n"
                       "domain: tnf\n---\n## Todo\n- [x] one\n- [ ] two\n- [X] three\n")
        p = run(self.ws)["projects"][0]
        self.assertEqual(p, {
            "name": "a", "project": "Alpha", "type": "bug", "status": "active",
            "last_active": "2026-01-02T10:00", "jira": "ABC-1", "branch": "fix",
            "domain": "tnf", "finished": False, "tasks": {"checked": 2, "total": 3}})

    def test_legacy_preset(self):
        self.proj("a", "---\npreset: old\n---\n")
        self.assertEqual(run(self.ws)["projects"][0]["domain"], "old")

    def test_finished_statuses(self):
        for i, s in enumerate(["done", "Complete", "closed", "active"]):
            self.proj(f"p{i}", f"---\nstatus: {s}\n---\n")
        by = self.by_name(run(self.ws))
        self.assertEqual([by[f"p{i}"]["finished"] for i in range(4)],
                         [True, True, True, False])

    def test_bad_frontmatter_does_not_throw(self):
        self.proj("none", "# no frontmatter\n")
        self.proj("unterminated", "---\nstatus: done\nproject: x\n")
        self.proj("empty", "")
        by = self.by_name(run(self.ws))
        self.assertEqual(set(by), {"none", "unterminated", "empty"})
        self.assertEqual(by["unterminated"]["project"], "unterminated")
        self.assertFalse(by["unterminated"]["finished"])

    def test_skips_folder_without_claude_md_and_files(self):
        (self.ws / "projects" / "nodoc").mkdir(parents=True)
        (self.ws / "projects" / "file.txt").write_text("x")
        self.proj("ok", "---\n---\n")
        self.assertEqual([p["name"] for p in run(self.ws)["projects"]], ["ok"])

    def test_sort_order(self):
        self.proj("old", "---\nlast-active: 2026-01-01T09:00\n---\n")
        self.proj("new", "---\nlast-active: 2026-03-01T09:00\n---\n")
        self.proj("zmissing", "---\n---\n")
        self.proj("amissing", "---\n---\n")
        self.assertEqual([p["name"] for p in run(self.ws)["projects"]],
                         ["new", "old", "amissing", "zmissing"])

    def test_read_only(self):
        path = self.proj("a", "---\nlast-active: 2020-01-01\n---\n- [ ] x\n")
        os.utime(path, (1000, 1000))
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        run(self.ws)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_no_workspace(self):
        res = run(None)
        self.assertEqual(res, {"status": "no_workspace", "managed": False, "projects": []})


if __name__ == "__main__":
    unittest.main()
