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
    def test_redacts_private_keeps_public(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            jira = workdir / "jira"
            jira.mkdir()
            (jira / "cves-parsed.json").write_text(
                json.dumps(
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
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--workdir", str(workdir)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            out = jira / "cves-parsed-for-analysis.json"
            payload = json.loads(out.read_text(encoding="utf-8"))
            by_key = {t["key"]: t for t in payload["tickets"]}
            self.assertEqual(by_key["OCPBUGS-1"]["summary"], "public summary")
            self.assertNotIn("summary", by_key["OCPBUGS-2"])
            self.assertNotIn("cve_ids", by_key["OCPBUGS-2"])
            self.assertTrue(by_key["OCPBUGS-2"]["redacted"])
            self.assertEqual(payload["private_redacted_count"], 1)


if __name__ == "__main__":
    unittest.main()
