#!/usr/bin/env python3
"""Unit tests for collect_govulncheck_results job completion helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from collect_govulncheck_results import job_is_terminal, summarize_jobs  # noqa: E402


def _job(*, active=None, failed=None, succeeded=None, conditions=None):
    status = {}
    if active is not None:
        status["active"] = active
    if failed is not None:
        status["failed"] = failed
    if succeeded is not None:
        status["succeeded"] = succeeded
    if conditions is not None:
        status["conditions"] = conditions
    return {"status": status}


class JobTerminalTests(unittest.TestCase):
    def test_complete_condition_terminal(self):
        self.assertTrue(
            job_is_terminal(
                _job(conditions=[{"type": "Complete", "status": "True"}])
            )
        )

    def test_failed_condition_terminal(self):
        self.assertTrue(
            job_is_terminal(_job(conditions=[{"type": "Failed", "status": "True"}]))
        )

    def test_false_condition_not_terminal(self):
        self.assertFalse(
            job_is_terminal(
                _job(conditions=[{"type": "Complete", "status": "False"}])
            )
        )

    def test_new_job_active_zero_not_terminal(self):
        # Positive case for the fix: newly created jobs often have no active
        # pods yet and must not be treated as finished.
        self.assertFalse(job_is_terminal(_job()))
        self.assertFalse(job_is_terminal(_job(active=0)))


class SummarizeJobsTests(unittest.TestCase):
    def test_all_terminal_complete(self):
        summary = summarize_jobs(
            [
                _job(
                    succeeded=1,
                    conditions=[{"type": "Complete", "status": "True"}],
                ),
                _job(
                    failed=1,
                    conditions=[{"type": "Failed", "status": "True"}],
                ),
            ]
        )
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 1)

    def test_active_zero_without_conditions_keeps_polling(self):
        # Negative: old active==0 heuristic would have returned complete.
        summary = summarize_jobs([_job(active=0), _job()])
        self.assertFalse(summary["complete"])

    def test_mixed_terminal_and_running_not_complete(self):
        summary = summarize_jobs(
            [
                _job(
                    succeeded=1,
                    conditions=[{"type": "Complete", "status": "True"}],
                ),
                _job(active=1),
            ]
        )
        self.assertFalse(summary["complete"])
        self.assertEqual(summary["active"], 1)


if __name__ == "__main__":
    unittest.main()
