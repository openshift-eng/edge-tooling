#!/usr/bin/env python3
"""Tests for scripts/project-tasks.py. Standalone: python3 tests/test_project_tasks.py"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "project-tasks.py"

FM = "---\nproject: p\nstatus: active\nlast-active: 2026-01-01T00:00\n---\n"


class TasksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.ws = Path(self._tmp.name).resolve()
        self.path = self.ws / "projects" / "p" / "CLAUDE.md"
        self.path.parent.mkdir(parents=True)

    def write(self, text: str):
        self.path.write_bytes(text.encode())

    def read(self) -> str:
        return self.path.read_bytes().decode()

    def run_cmd(self, *args: str, project="p") -> dict:
        env = {**os.environ, "WORKSPACE_ROOT": str(self.ws)}
        r = subprocess.run([sys.executable, str(SCRIPT), args[0], project, *args[1:]],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_list_sections_and_occurrence(self):
        self.write(FM + "# Title\n- [ ] ignored (h1)\n## A\n- [ ] x\n- [x] x\n- [ ] y\n"
                        "## Empty\ntext\n### B\n  - [X] z\n")
        res = self.run_cmd("list")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["project"], "p")
        self.assertEqual(res["sections"], [
            {"heading": "A", "level": 2, "items": [
                {"text": "x", "checked": False, "occurrence": 0},
                {"text": "x", "checked": True, "occurrence": 1},
                {"text": "y", "checked": False, "occurrence": 0}]},
            {"heading": "B", "level": 3, "items": [
                {"text": "z", "checked": True, "occurrence": 0}]},
        ])

    def test_list_ignores_frontmatter_and_fences(self):
        self.write("---\nnote: - [ ] fm\n---\n## A\n```\n- [ ] fenced\n## Fake\n```\n- [ ] real\n")
        res = self.run_cmd("list")
        self.assertEqual([(s["heading"], [i["text"] for i in s["items"]])
                          for s in res["sections"]], [("A", ["real"])])

    def test_list_does_not_modify(self):
        self.write(FM + "## A\n- [ ] x\n")
        os.utime(self.path, (1000, 1000))
        before = (self.read(), self.path.stat().st_mtime_ns)
        self.run_cmd("list")
        self.assertEqual((self.read(), self.path.stat().st_mtime_ns), before)

    def test_toggle_both_ways_and_occurrence(self):
        self.write(FM + "## A\n- [ ] x\n- [ ] x\n")
        res = self.run_cmd("toggle", "--section", "A", "--text", "x", "--occurrence", "1")
        self.assertEqual(res, {"status": "ok", "checked": True})
        self.assertEqual(self.read(), FM + "## A\n- [ ] x\n- [x] x\n")
        res = self.run_cmd("toggle", "--section", "A", "--text", "x", "--occurrence", "1")
        self.assertEqual(res, {"status": "ok", "checked": False})
        self.assertEqual(self.read(), FM + "## A\n- [ ] x\n- [ ] x\n")

    def test_toggle_uppercase_x(self):
        self.write("## A\n- [X] x\n")
        res = self.run_cmd("toggle", "--section", "A", "--text", "x")
        self.assertEqual(res["checked"], False)
        self.assertEqual(self.read(), "## A\n- [ ] x\n")

    def test_toggle_skips_fenced_item(self):
        self.write("## A\n```\n- [ ] x\n```\n- [ ] x\n")
        self.run_cmd("toggle", "--section", "A", "--text", "x")
        self.assertEqual(self.read(), "## A\n```\n- [ ] x\n```\n- [x] x\n")

    def test_toggle_matches_by_section(self):
        self.write("## A\n- [ ] x\n## B\n- [ ] x\n")
        self.run_cmd("toggle", "--section", "B", "--text", "x")
        self.assertEqual(self.read(), "## A\n- [ ] x\n## B\n- [x] x\n")

    def test_repeated_headings_share_occurrences_and_toggle_the_selected_item(self):
        for heading in ("## A", "### A"):
            with self.subTest(heading=heading):
                first = f"{heading}\n- [ ] x\n- [ ] x\n"
                second = f"{heading}\n- [ ] x\n"
                self.write(FM + first + "## Other\n- [ ] x\n" + second)

                sections = self.run_cmd("list")["sections"]
                item = sections[2]["items"][0]
                self.assertEqual([i["occurrence"] for s in sections
                                  if s["heading"] == "A" for i in s["items"]],
                                 [0, 1, 2])
                res = self.run_cmd("toggle", "--section", "A", "--text", "x",
                                   "--occurrence", str(item["occurrence"]))

                self.assertEqual(res, {"status": "ok", "checked": True})
                self.assertEqual(self.read(), FM + first + "## Other\n- [ ] x\n"
                                 + f"{heading}\n- [x] x\n")

    def test_remove_targets_the_item_under_a_repeated_heading(self):
        for heading in ("## A", "### A"):
            with self.subTest(heading=heading):
                first = f"{heading}\n- [ ] x\n"
                self.write(FM + first + f"{heading}\n- [ ] x\n")
                item = self.run_cmd("list")["sections"][1]["items"][0]

                res = self.run_cmd("remove", "--section", "A", "--text", "x",
                                   "--occurrence", str(item["occurrence"]))

                self.assertEqual(res, {"status": "ok"})
                self.assertEqual(self.read(), FM + first + f"{heading}\n")

    def test_add_under_a_repeated_heading_errors_without_writing(self):
        content = FM + "## A\n- [ ] x\n### A\n- [ ] y\n"
        self.write(content)
        os.utime(self.path, (1000, 1000))
        before = (self.read(), self.path.stat().st_mtime_ns)

        res = self.run_cmd("add", "--section", "A", "--text", "new")

        self.assertEqual(res["status"], "error")
        self.assertIn("heading", res["error_message"])
        self.assertEqual((self.read(), self.path.stat().st_mtime_ns), before)

    def test_add_after_last_item(self):
        self.write(FM + "## A\n- [ ] a\n- [x] b\n\nprose\n## B\n- [ ] c\n")
        self.assertEqual(self.run_cmd("add", "--section", "A", "--text", "new"), {"status": "ok"})
        self.assertEqual(self.read(),
                         FM + "## A\n- [ ] a\n- [x] b\n- [ ] new\n\nprose\n## B\n- [ ] c\n")

    def test_add_empty_section(self):
        self.write("## A\n\n## B\n")
        self.run_cmd("add", "--section", "A", "--text", "new")
        self.assertEqual(self.read(), "## A\n- [ ] new\n\n## B\n")

    def test_add_at_eof_without_newline(self):
        self.write("## A\n- [ ] a")
        self.run_cmd("add", "--section", "A", "--text", "b")
        self.assertEqual(self.read(), "## A\n- [ ] a\n- [ ] b")

    def test_add_rejects_newline(self):
        self.write("## A\n")
        res = self.run_cmd("add", "--section", "A", "--text", "a\nb")
        self.assertEqual(res["status"], "error")
        self.assertIn("error_message", res)
        self.assertEqual(self.read(), "## A\n")

    def test_remove_with_occurrence(self):
        self.write("## A\n- [ ] x\n- [x] x\n- [ ] y\n")
        self.assertEqual(self.run_cmd("remove", "--section", "A", "--text", "x",
                                      "--occurrence", "1"), {"status": "ok"})
        self.assertEqual(self.read(), "## A\n- [ ] x\n- [ ] y\n")

    def test_crlf_preserved(self):
        self.write("---\r\nproject: p\r\n---\r\n## A\r\n- [ ] a\r\n- [ ] b\r\n")
        self.run_cmd("toggle", "--section", "A", "--text", "a")
        self.run_cmd("add", "--section", "A", "--text", "c")
        self.run_cmd("remove", "--section", "A", "--text", "b")
        self.assertEqual(self.read(),
                         "---\r\nproject: p\r\n---\r\n## A\r\n- [x] a\r\n- [ ] c\r\n")

    def test_frontmatter_untouched(self):
        self.write(FM + "## A\n- [ ] a\n")
        self.run_cmd("toggle", "--section", "A", "--text", "a")
        self.assertTrue(self.read().startswith(FM))

    def test_stale_writes_nothing(self):
        content = FM + "## A\n- [ ] a\n"
        self.write(content)
        os.utime(self.path, (1000, 1000))
        for cmd in (["toggle", "--section", "A", "--text", "gone"],
                    ["toggle", "--section", "Nope", "--text", "a"],
                    ["remove", "--section", "A", "--text", "a", "--occurrence", "3"],
                    ["remove", "--section", "Nope", "--text", "a"],
                    ["add", "--section", "Nope", "--text", "a"]):
            self.assertEqual(self.run_cmd(*cmd), {"status": "stale"}, cmd)
        self.assertEqual(self.read(), content)
        self.assertEqual(self.path.stat().st_mtime_ns, 1000 * 10**9)

    def test_not_found(self):
        self.assertEqual(self.run_cmd("list", project="missing"), {"status": "not_found"})
        self.assertEqual(self.run_cmd("toggle", "--section", "A", "--text", "a",
                                      project="missing"), {"status": "not_found"})
        self.assertEqual(self.run_cmd("list", project="../p"), {"status": "not_found"})
        (self.ws / "projects" / "nodoc").mkdir()
        self.assertEqual(self.run_cmd("list", project="nodoc"), {"status": "not_found"})

    def test_no_temp_files_left(self):
        self.write("## A\n- [ ] a\n")
        self.run_cmd("toggle", "--section", "A", "--text", "a")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["CLAUDE.md"])


if __name__ == "__main__":
    unittest.main()
