#!/usr/bin/env python3
"""Unit tests for analyze_scan_result.determine_verdict."""

import unittest
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from analyze_scan_result import determine_verdict  # noqa: E402


class DetermineVerdictTests(unittest.TestCase):
    def test_scan_incomplete_inconclusive(self):
        verdict, action = determine_verdict(
            {"scan_incomplete": True, "affected": False, "scan_exit_code": 137}
        )
        self.assertEqual(verdict, "inconclusive")
        self.assertFalse(action)

    def test_affected_true(self):
        verdict, action = determine_verdict(
            {"scan_incomplete": False, "affected": True, "scan_exit_code": 3, "finding_count": 2}
        )
        self.assertEqual(verdict, "affected")
        self.assertTrue(action)

    def test_abnormal_exit_zero_findings_inconclusive(self):
        # Positive case for the fix: exit 1 with no findings must not clear the repo.
        verdict, action = determine_verdict(
            {"scan_incomplete": False, "affected": False, "scan_exit_code": 1, "finding_count": 0}
        )
        self.assertEqual(verdict, "inconclusive")
        self.assertFalse(action)

    def test_abnormal_exit_with_findings_inconclusive(self):
        verdict, action = determine_verdict(
            {"scan_incomplete": False, "affected": False, "scan_exit_code": 1, "finding_count": 3}
        )
        self.assertEqual(verdict, "inconclusive")
        self.assertFalse(action)

    def test_clean_result_not_affected(self):
        # Negative case: normal clean scan (exit 0, no findings) stays not_affected.
        verdict, action = determine_verdict(
            {"scan_incomplete": False, "affected": False, "scan_exit_code": 0, "finding_count": 0}
        )
        self.assertEqual(verdict, "not_affected")
        self.assertFalse(action)

    def test_exit_3_without_affected_flag_not_affected(self):
        # Exit 3 is a normal govulncheck code; without affected=True we do not
        # invent an affected verdict here (matched_findings drive that flag).
        verdict, action = determine_verdict(
            {"scan_incomplete": False, "affected": False, "scan_exit_code": 3, "finding_count": 0}
        )
        self.assertEqual(verdict, "not_affected")
        self.assertFalse(action)


if __name__ == "__main__":
    unittest.main()
