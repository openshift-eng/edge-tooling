#!/usr/bin/env python3
"""Tests for scripts/handoff.py.

Standalone: python3 tests/test_handoff.py
Requires no third-party modules (nor does the script under test).

Each test drives handoff.py as a subprocess with HANDOFF_DIR and
CLAUDE_PROJECT_DIR pointed at throwaway temp dirs, so nothing touches the
real ~/.claude.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "handoff.py"

NOTE = "# Handoff: demo\n\n**Goal:** ship it\n\n## Next task\nrun the e2e\n"


class HandoffFixture(unittest.TestCase):

    def setUp(self):
        # resolve(): on macOS mkdtemp returns /var/... which is a symlink to
        # /private/var/... — path-equality assertions need the real path.
        self.tmp = Path(tempfile.mkdtemp(prefix="handoff-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = self.tmp / "store"
        self.project = self.tmp / "repo"
        self.project.mkdir()

    def run_handoff(self, *args: str, project: Path | None = None,
                    **env: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True,
            env={**os.environ,
                 "HANDOFF_DIR": str(self.store),
                 "CLAUDE_PROJECT_DIR": str(project or self.project),
                 **env},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def arm(self, text: str = NOTE, age_minutes: float = 0,
            project: Path | None = None) -> Path:
        """Write a note the way the skill does: to the path `path` reports."""
        out = json.loads(self.run_handoff("path", project=project).stdout)
        path = Path(out["path"])
        path.write_text(text)
        if age_minutes:
            stamp = time.time() - age_minutes * 60
            os.utime(path, (stamp, stamp))
        return path


class TestPath(HandoffFixture):

    def test_path_is_under_store_and_creates_it(self):
        out = json.loads(self.run_handoff("path").stdout)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["project_dir"], str(self.project))
        self.assertEqual(Path(out["path"]).parent, self.store)
        self.assertTrue(self.store.is_dir())
        self.assertFalse(out["exists"])
        self.assertFalse(out["workspace_detected"])
        self.assertEqual(out["ttl_minutes"], 60)

    def test_path_is_stable_and_distinct_per_project(self):
        other = self.tmp / "other"
        other.mkdir()
        first = json.loads(self.run_handoff("path").stdout)["path"]
        again = json.loads(self.run_handoff("path").stdout)["path"]
        elsewhere = json.loads(self.run_handoff("path", project=other).stdout)["path"]
        self.assertEqual(first, again)
        self.assertNotEqual(first, elsewhere)

    def test_path_distinguishes_similarly_sanitized_projects(self):
        """'/tmp/foo-bar' and '/tmp/foo/bar' both sanitize to the same
        string; the trailing hash must still tell them apart."""
        a = self.tmp / "foo-bar"
        b = self.tmp / "foo" / "bar"
        out_a = json.loads(self.run_handoff("path", project=a).stdout)["path"]
        out_b = json.loads(self.run_handoff("path", project=b).stdout)["path"]
        self.assertNotEqual(out_a, out_b)

    def test_path_stays_within_filename_limits_for_deep_projects(self):
        deep = self.tmp
        for part in ["nested"] * 30:
            deep = deep / part
        out = json.loads(self.run_handoff("path", project=deep).stdout)
        self.assertLessEqual(len(Path(out["path"]).name), 100)

    def test_path_reports_existing_note_and_workspace(self):
        self.arm()
        (self.project / "dev-env.yaml").write_text("repos: []\n")
        out = json.loads(self.run_handoff("path").stdout)
        self.assertTrue(out["exists"])
        self.assertTrue(out["workspace_detected"])

    def test_ttl_override(self):
        out = json.loads(self.run_handoff(
            "path", HANDOFF_TTL_MINUTES="5").stdout)
        self.assertEqual(out["ttl_minutes"], 5)

    def test_project_dir_flag_overrides_env_var(self):
        """The Bash tool never exports CLAUDE_PROJECT_DIR; only the skill's
        explicit --project-dir can be trusted to match what the hook sees."""
        other = self.tmp / "elsewhere"
        other.mkdir()
        out = json.loads(self.run_handoff("path", "--project-dir", str(other)).stdout)
        self.assertEqual(out["project_dir"], str(other))


class TestRead(HandoffFixture):

    def test_fresh_note_is_injected_and_retired(self):
        path = self.arm()
        out = json.loads(self.run_handoff("read").stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("run the e2e", ctx)
        self.assertIn("BEGIN HANDOFF NOTE", ctx)
        self.assertIn("handoff", out["systemMessage"].lower())
        self.assertFalse(path.exists())
        self.assertEqual(path.with_name(path.stem + ".consumed.md").read_text(), NOTE)

    def test_fires_only_once(self):
        self.arm()
        self.assertTrue(self.run_handoff("read").stdout)
        self.assertEqual(self.run_handoff("read").stdout, "")

    def test_no_note_is_silent(self):
        self.assertEqual(self.run_handoff("read").stdout, "")

    def test_stale_note_is_silent_and_retired(self):
        path = self.arm(age_minutes=61)
        self.assertEqual(self.run_handoff("read").stdout, "")
        self.assertFalse(path.exists())

    def test_empty_note_is_silent(self):
        self.arm(text="  \n")
        self.assertEqual(self.run_handoff("read").stdout, "")

    def test_note_for_other_project_does_not_fire(self):
        other = self.tmp / "other"
        other.mkdir()
        path = self.arm(project=other)
        self.assertEqual(self.run_handoff("read").stdout, "")
        self.assertTrue(path.exists())

    def test_note_not_leaked_across_similarly_sanitized_projects(self):
        """Regression: '/tmp/foo-bar' and '/tmp/foo/bar' used to flatten to
        the same note_key, so B's read would consume A's note."""
        a = self.tmp / "foo-bar"
        b = self.tmp / "foo" / "bar"
        path_a = self.arm(project=a)
        self.assertEqual(self.run_handoff("read", project=b).stdout, "")
        self.assertTrue(path_a.exists())

    def test_oversized_unarmed_note_is_visibly_truncated(self):
        """An unarmed note (arm already rejects this for the armed path)
        must stay under Claude Code's 10,000-char additionalContext limit,
        past which it drops to a bare file-path preview — and must say so
        rather than silently dropping content."""
        path = self.arm(text="x" * 100_000)
        ctx = json.loads(self.run_handoff("read").stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertLess(len(ctx), 10_000)
        self.assertIn("note truncated", ctx)
        self.assertIn(str(path.with_name(path.stem + ".consumed.md")), ctx)

    def test_unwritable_store_never_fails(self):
        self.store.write_text("not a directory")
        self.assertEqual(self.run_handoff("read").stdout, "")


class TestArm(HandoffFixture):

    def arm_with(self, *flags: str) -> dict:
        return json.loads(self.run_handoff("arm", *flags).stdout)

    def expiry(self, path: Path) -> datetime:
        header = path.read_text().splitlines()[1]
        self.assertTrue(header.startswith("expires_at: "), header)
        return datetime.fromisoformat(header.split(": ", 1)[1])

    def test_default_ttl_stamps_header_and_keeps_body(self):
        path = self.arm()
        out = self.arm_with()
        self.assertEqual(out["status"], "ok")
        delta = self.expiry(path) - datetime.now().astimezone()
        self.assertAlmostEqual(delta.total_seconds(), 3600, delta=120)
        self.assertTrue(path.read_text().endswith(NOTE))

    def test_ttl_minutes(self):
        path = self.arm()
        out = self.arm_with("--ttl-minutes", str(14 * 60))
        self.assertEqual(out["expires_in"], "14 hours")
        delta = self.expiry(path) - datetime.now().astimezone()
        self.assertAlmostEqual(delta.total_seconds(), 14 * 3600, delta=120)

    def test_until_clock_time_is_next_occurrence(self):
        path = self.arm()
        target = datetime.now().astimezone() - timedelta(hours=1)
        self.arm_with("--until", target.strftime("%H:%M"))
        delta = self.expiry(path) - datetime.now().astimezone()
        self.assertGreater(delta.total_seconds(), 22 * 3600)
        self.assertLess(delta.total_seconds(), 24 * 3600)

    def test_until_iso_timestamp(self):
        path = self.arm()
        target = (datetime.now().astimezone() + timedelta(days=3)).replace(
            second=0, microsecond=0)
        self.arm_with("--until", target.isoformat())
        self.assertEqual(self.expiry(path), target)

    def test_rearm_replaces_header_instead_of_stacking(self):
        path = self.arm()
        self.arm_with("--ttl-minutes", "10")
        self.arm_with("--ttl-minutes", "20")
        self.assertEqual(path.read_text().count("expires_at"), 1)

    def test_rejects_past_too_far_and_garbage(self):
        path = self.arm()
        past = (datetime.now().astimezone() - timedelta(hours=1)).isoformat()
        for flags in (["--until", past],
                      ["--ttl-minutes", str(8 * 24 * 60)],
                      ["--until", "tomorrow-ish"]):
            self.assertEqual(self.arm_with(*flags)["status"], "error", flags)
        self.assertEqual(path.read_text(), NOTE)

    def test_arm_without_note_errors(self):
        self.assertEqual(self.arm_with()["status"], "error")

    def test_arm_rejects_note_too_big_to_inject(self):
        path = self.arm(text="x" * 100_000)
        out = self.arm_with()
        self.assertEqual(out["status"], "error")
        self.assertIn("injection limit", out["message"])
        self.assertNotIn("expires_at", path.read_text())

    def test_armed_expiry_outlives_default_ttl(self):
        path = self.arm()
        self.arm_with("--ttl-minutes", str(14 * 60))
        stamp = time.time() - 3 * 3600
        os.utime(path, (stamp, stamp))
        ctx = json.loads(self.run_handoff("read").stdout)[
            "hookSpecificOutput"]["additionalContext"]
        self.assertIn("run the e2e", ctx)
        self.assertNotIn("expires_at", ctx)

    def test_armed_expiry_in_past_does_not_fire(self):
        path = self.arm()
        past = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat()
        path.write_text(f"---\nexpires_at: {past}\n---\n\n{NOTE}")
        self.assertEqual(self.run_handoff("read").stdout, "")
        self.assertFalse(path.exists())


class TestClear(HandoffFixture):

    def test_clear_removes_armed_note(self):
        path = self.arm()
        out = json.loads(self.run_handoff("clear").stdout)
        self.assertEqual(out, {"status": "ok", "deleted": True})
        self.assertFalse(path.exists())
        self.assertEqual(self.run_handoff("read").stdout, "")

    def test_clear_without_note(self):
        out = json.loads(self.run_handoff("clear").stdout)
        self.assertEqual(out, {"status": "ok", "deleted": False})


class TestHookManifest(unittest.TestCase):

    def test_hook_runs_read_on_startup_and_clear_only(self):
        hooks = json.loads((SCRIPT.parent.parent / "hooks" / "hooks.json").read_text())
        entries = hooks["hooks"]["SessionStart"]
        self.assertEqual([e["matcher"] for e in entries], ["startup|clear"])
        self.assertIn("handoff.py\" read", entries[0]["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
