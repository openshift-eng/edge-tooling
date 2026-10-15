#!/usr/bin/env python3
"""Unit tests for generate_html_report.group_tickets private version bucketing."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from generate_html_report import (  # noqa: E402
    group_tickets,
    load_known_components,
    render_component,
    render_summary,
    unique_rows_by_key,
)


class GroupTicketsPrivateVersionTests(unittest.TestCase):
    def test_private_ticket_grouped_under_withheld(self) -> None:
        parsed = {
            "tickets": [
                {
                    "key": "OCPBUGS-1",
                    "url": "https://example/OCPBUGS-1",
                    "component": "MicroShift",
                    "versions": ["4.18", "4.19"],
                    "is_private": True,
                    "cve_ids": ["CVE-2024-1"],
                    "summary": "secret",
                }
            ]
        }
        grouped, dropped = group_tickets(parsed, {}, {"MicroShift"})
        self.assertEqual(dropped, 0)
        self.assertIn("Withheld", grouped["MicroShift"])
        self.assertNotIn("4.18", grouped["MicroShift"])
        self.assertNotIn("4.19", grouped["MicroShift"])
        row = grouped["MicroShift"]["Withheld"][0]
        self.assertTrue(row["is_private"])
        self.assertEqual(row["cve_ids"], [])
        self.assertEqual(row["summary"], "")
        self.assertEqual(row["scans"], [])

    def test_public_ticket_keeps_real_versions(self) -> None:
        parsed = {
            "tickets": [
                {
                    "key": "OCPBUGS-2",
                    "url": "https://example/OCPBUGS-2",
                    "component": "MicroShift",
                    "versions": ["4.18"],
                    "is_private": False,
                    "cve_ids": ["CVE-2024-2"],
                    "summary": "public",
                }
            ]
        }
        grouped, _ = group_tickets(parsed, {}, {"MicroShift"})
        self.assertIn("4.18", grouped["MicroShift"])
        self.assertNotIn("Withheld", grouped["MicroShift"])
        row = grouped["MicroShift"]["4.18"][0]
        self.assertEqual(row["summary"], "public")
        self.assertEqual(row["cve_ids"], ["CVE-2024-2"])


class LoadKnownComponentsTests(unittest.TestCase):
    def test_missing_config_returns_empty(self) -> None:
        self.assertEqual(load_known_components(Path("/nonexistent/component-repos.json")), set())

    def test_empty_components_mapping_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "component-repos.json"
            path.write_text(json.dumps({"components": {}}), encoding="utf-8")
            self.assertEqual(load_known_components(path), set())

    def test_main_exits_when_mapping_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "jira").mkdir()
            (workdir / "jira" / "cves-parsed.json").write_text(
                json.dumps({"tickets": []}), encoding="utf-8"
            )
            empty_cfg = workdir / "empty-components.json"
            empty_cfg.write_text(json.dumps({"components": {}}), encoding="utf-8")
            # S603: argv is sys.executable + fixed script path + test-controlled args only.
            result = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    str(SCRIPT_DIR / "generate_html_report.py"),
                    "--workdir",
                    str(workdir),
                    "--config",
                    str(empty_cfg),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("refusing to render all Jira components", result.stderr)
            self.assertFalse((workdir / "report-cve-investigation.html").exists())


class DedupStatsTests(unittest.TestCase):
    def test_multi_version_ticket_counted_once(self) -> None:
        parsed = {
            "tickets": [
                {
                    "key": "OCPBUGS-9",
                    "url": "https://example/OCPBUGS-9",
                    "component": "MicroShift",
                    "versions": ["4.18", "4.19"],
                    "is_private": False,
                    "cve_ids": ["CVE-2024-9"],
                    "summary": "multi",
                }
            ]
        }
        grouped, _ = group_tickets(parsed, {}, {"MicroShift"})
        # Still listed under both version sections for display.
        self.assertEqual(len(grouped["MicroShift"]["4.18"]), 1)
        self.assertEqual(len(grouped["MicroShift"]["4.19"]), 1)
        unique = unique_rows_by_key(grouped["MicroShift"])
        self.assertEqual(len(unique), 1)

        _, counts = render_summary(grouped)
        self.assertEqual(counts["total"], 1)

        html_out = render_component("MicroShift", grouped["MicroShift"], open_by_default=False)
        self.assertIn("(1 ticket)", html_out)
        # Version headings preserved (badge/summary display behavior).
        self.assertIn("<h4>4.18</h4>", html_out)
        self.assertIn("<h4>4.19</h4>", html_out)


if __name__ == "__main__":
    unittest.main()
