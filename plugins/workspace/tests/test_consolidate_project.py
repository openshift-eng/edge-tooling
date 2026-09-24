#!/usr/bin/env python3
"""Tests for scripts/consolidate-project.py.

Standalone: python3 tests/test_consolidate_project.py

Mirrors tests/test_domain_info.py: builds a throwaway "plugin" dir
(scripts/ only — consolidate-project.py needs no domains/) and throwaway
workspace dirs, then drives consolidate-project.py as a subprocess with
WORKSPACE_ROOT set.
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

REPO_ROOT = Path(__file__).resolve().parent.parent


def new_plugin(tmp: Path) -> Path:
    """Build a throwaway plugin dir: scripts/{consolidate-project.py,workspace_lib.py}."""
    plugin = tmp / "plugin"
    (plugin / "scripts").mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "consolidate-project.py", plugin / "scripts")
    shutil.copy(REPO_ROOT / "scripts" / "workspace_lib.py", plugin / "scripts")
    return plugin


class ConsolidateProjectFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="consolidate-project-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.plugin = new_plugin(self.tmp)
        self.ws = self.tmp / "ws"
        (self.ws / "projects" / "demo").mkdir(parents=True)

    def write_claude_md(self, text: str) -> None:
        (self.ws / "projects" / "demo" / "CLAUDE.md").write_text(text)

    def read_claude_md(self) -> str:
        return (self.ws / "projects" / "demo" / "CLAUDE.md").read_text()

    def read_archive(self) -> str:
        return (self.ws / "projects" / "demo" / "progress-archive.md").read_text()

    def run_consolidate(self, *args: str) -> dict:
        result = subprocess.run(
            [sys.executable, str(self.plugin / "scripts" / "consolidate-project.py"),
             *args, "demo"],
            capture_output=True, text=True,
            env={**os.environ, "WORKSPACE_ROOT": str(self.ws)},
        )
        assert result.returncode == 0, (
            f"consolidate-project.py exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        return json.loads(result.stdout)


class TestCheckedItemThreshold(ConsolidateProjectFixture):
    """Regression: 10+ checked items in a non-Progress section still qualify."""

    def test_dry_run_qualifies_and_reports_archive_counts(self):
        lines = ["# Title", "", "## Fix Plan", ""]
        lines += [f"- [x] item {i}" for i in range(12)]
        self.write_claude_md("\n".join(lines) + "\n")

        result = self.run_consolidate("--dry-run")

        self.assertEqual(result["status"], "needs_consolidation")
        self.assertFalse(result["over_line_threshold"])
        section = result["sections"][0]
        self.assertEqual(section["name"], "Fix Plan")
        self.assertEqual(section["to_archive"], 9)
        self.assertEqual(section["to_keep"], 3)


class TestProgressNarrativeThreshold(ConsolidateProjectFixture):
    """Plain bullets and paragraphs under ## Progress count toward the threshold."""

    def test_plain_bullets_trigger_and_archive(self):
        lines = ["# Title", "", "## Progress", "- [x] Project created", "- [x] Design documented"]
        lines += [f"- milestone note {i} happened" for i in range(9)]
        self.write_claude_md("\n".join(lines) + "\n")

        dry = self.run_consolidate("--dry-run")
        self.assertEqual(dry["status"], "needs_consolidation")
        section = dry["sections"][0]
        self.assertEqual(section["name"], "Progress")
        self.assertEqual(section["checked"], 2)
        self.assertEqual(section["to_archive"], 9)
        self.assertEqual(section["to_keep"], 2)

        applied = self.run_consolidate()
        self.assertEqual(applied["status"], "consolidated")
        self.assertEqual(applied["sections"][0]["archived"], 9)

        claude_md = self.read_claude_md()
        self.assertNotIn("milestone note 0 happened", claude_md)
        self.assertIn("Project created", claude_md)
        self.assertIn("milestone note 0 happened", self.read_archive())

    def test_prose_paragraphs_also_count(self):
        lines = ["# Title", "", "## Progress", "- [x] Project created"]
        lines += [f"Paragraph narrative line {i} describing what happened." for i in range(9)]
        self.write_claude_md("\n".join(lines) + "\n")

        dry = self.run_consolidate("--dry-run")
        self.assertEqual(dry["status"], "needs_consolidation")
        self.assertEqual(dry["sections"][0]["to_archive"], 9)

    def test_narrative_in_non_progress_section_does_not_qualify(self):
        lines = ["# Title", "", "## Fix Plan"]
        lines += [f"- [x] item {i}" for i in range(3)]
        lines += [f"- narrative note {i}" for i in range(9)]
        self.write_claude_md("\n".join(lines) + "\n")

        result = self.run_consolidate("--dry-run")

        self.assertEqual(result["status"], "already_lean")


class TestFileLineThreshold(ConsolidateProjectFixture):
    """A file over the line threshold is flagged even with no qualifying section."""

    def test_over_threshold_no_qualifying_sections(self):
        lines = ["# Title", ""]
        for i in range(40):
            lines += [f"## Section {i}", f"- [x] one checked item {i}", ""]
        self.write_claude_md("\n".join(lines) + "\n")

        result = self.run_consolidate("--dry-run")

        self.assertEqual(result["status"], "over_threshold_no_sections")
        self.assertEqual(result["line_threshold"], 100)
        self.assertGreater(result["claude_md_lines"], 100)

    def test_small_file_stays_already_lean(self):
        self.write_claude_md("# Title\n\n## Progress\n- [x] Project created\n")

        result = self.run_consolidate("--dry-run")

        self.assertEqual(result["status"], "already_lean")


if __name__ == "__main__":
    unittest.main()
