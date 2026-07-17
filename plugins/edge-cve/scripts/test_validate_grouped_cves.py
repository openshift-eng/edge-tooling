#!/usr/bin/env python3
"""Tests for validate_grouped_cves.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "validate_grouped_cves.py"

MINIMAL_GROUPED = {
    "grouped_at": "2026-01-01T00:00:00+00:00",
    "source": "/tmp/cves-parsed.json",
    "group_count": 1,
    "ticket_count": 1,
    "llm_review_count": 0,
    "groups": [
        {
            "group_id": "CVE-2024-1::Comp::stem",
            "cve_id": "CVE-2024-1",
            "component": "Comp",
            "summary_stem": "stem",
            "ticket_count": 1,
            "ticket_keys": ["OCPBUGS-1"],
            "versions": ["4.18"],
            "repos": ["openshift/foo"],
            "tickets": [{"key": "OCPBUGS-1", "cve_ids": ["CVE-2024-1"]}],
            "needs_llm_review": False,
            "llm_review_reasons": [],
        }
    ],
}


class ValidateGroupedCvesTest(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "jira").mkdir()
            schema = workdir / "jira" / "cves-grouped.json"
            schema.write_text(json.dumps(MINIMAL_GROUPED), encoding="utf-8")
            result = self._run("--workdir", str(workdir))
            self.assertEqual(result.returncode, 1)
            self.assertIn("not found", result.stderr)

    def test_valid_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            jira = workdir / "jira"
            jira.mkdir()
            (jira / "cves-grouped.json").write_text(
                json.dumps(MINIMAL_GROUPED), encoding="utf-8"
            )
            (jira / "cves-grouped-reviewed.json").write_text(
                json.dumps(MINIMAL_GROUPED), encoding="utf-8"
            )
            result = self._run("--workdir", str(workdir))
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])

    def test_invalid_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            jira = workdir / "jira"
            jira.mkdir()
            (jira / "cves-grouped.json").write_text(
                json.dumps(MINIMAL_GROUPED), encoding="utf-8"
            )
            bad = {"groups": "not-a-list"}
            (jira / "cves-grouped-reviewed.json").write_text(
                json.dumps(bad), encoding="utf-8"
            )
            result = self._run("--workdir", str(workdir))
            self.assertEqual(result.returncode, 1)
            self.assertIn("stop without rebuilding", result.stderr)


if __name__ == "__main__":
    unittest.main()
