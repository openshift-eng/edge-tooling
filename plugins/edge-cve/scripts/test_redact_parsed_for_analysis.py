#!/usr/bin/env python3
"""Tests for redact_parsed_for_analysis.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "redact_parsed_for_analysis.py"


class RedactParsedForAnalysisTest(unittest.TestCase):
    def _run_with_parsed(self, workdir: Path, parsed: dict) -> subprocess.CompletedProcess[str]:
        jira = workdir / "jira"
        jira.mkdir(parents=True, exist_ok=True)
        (jira / "cves-parsed.json").write_text(json.dumps(parsed), encoding="utf-8")
        # S603: argv is sys.executable + fixed SCRIPT path + test-controlled args only.
        return subprocess.run(  # noqa: S603
            [sys.executable, str(SCRIPT), "--workdir", str(workdir)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_redacts_private_keeps_public(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            result = self._run_with_parsed(
                workdir,
                {
                    "tickets": [
                        {
                            "key": "OCPBUGS-1",
                            "summary": "public summary",
                            "cve_ids": ["CVE-2024-1"],
                            "is_private": False,
                        },
                        {
                            "key": "OCPBUGS-2",
                            "summary": "SECRET EMBARGO DETAILS",
                            "cve_ids": ["CVE-2024-2"],
                            "is_private": True,
                            "url": "https://example/OCPBUGS-2",
                        },
                    ]
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            out = workdir / "jira" / "cves-parsed-for-analysis.json"
            payload = json.loads(out.read_text(encoding="utf-8"))
            by_key = {t["key"]: t for t in payload["tickets"]}
            self.assertEqual(by_key["OCPBUGS-1"]["summary"], "public summary")
            self.assertNotIn("summary", by_key["OCPBUGS-2"])
            self.assertNotIn("cve_ids", by_key["OCPBUGS-2"])
            self.assertTrue(by_key["OCPBUGS-2"]["redacted"])
            self.assertEqual(payload["private_redacted_count"], 1)

    def test_non_list_tickets_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            result = self._run_with_parsed(workdir, {"tickets": {"key": "OCPBUGS-1"}})
            self.assertEqual(result.returncode, 1)
            self.assertIn("'tickets' must be a list", result.stderr)
            self.assertFalse((workdir / "jira" / "cves-parsed-for-analysis.json").exists())

    def test_missing_tickets_key_handled_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            result = self._run_with_parsed(workdir, {"count": 0})
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(
                (workdir / "jira" / "cves-parsed-for-analysis.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["tickets"], [])
            self.assertEqual(payload["count"], 0)
            self.assertEqual(payload["private_redacted_count"], 0)

    def test_non_dict_ticket_entries_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            result = self._run_with_parsed(
                workdir,
                {
                    "tickets": [
                        "not-a-ticket",
                        42,
                        None,
                        {
                            "key": "OCPBUGS-3",
                            "summary": "kept",
                            "is_private": False,
                        },
                    ]
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(
                (workdir / "jira" / "cves-parsed-for-analysis.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(payload["tickets"]), 1)
            self.assertEqual(payload["tickets"][0]["key"], "OCPBUGS-3")
            self.assertEqual(payload["private_redacted_count"], 0)

    def test_empty_tickets_list_zero_private_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            result = self._run_with_parsed(workdir, {"tickets": []})
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(
                (workdir / "jira" / "cves-parsed-for-analysis.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["tickets"], [])
            self.assertEqual(payload["private_redacted_count"], 0)


if __name__ == "__main__":
    unittest.main()
