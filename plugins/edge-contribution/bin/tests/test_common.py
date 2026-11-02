"""Tests for _common.py — GitHub command helpers and date/window parsing.

Covers rate-limit backoff for the ``gh`` runner and boundary cases (quarter/date
parsing, activity payload counters).
"""

import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import _common  # noqa: E402


class TestDateHelpers(unittest.TestCase):
    def test_parse_date(self):
        assert _common.parse_date("2026-04-01") == date(2026, 4, 1)

    def test_quarter_to_window_q2(self):
        window = _common.quarter_to_window("2026Q2")
        assert window.start == date(2026, 4, 1)
        assert window.end == date(2026, 6, 30)

    def test_quarter_to_window_q4(self):
        window = _common.quarter_to_window("2026Q4")
        assert window.start == date(2026, 10, 1)
        assert window.end == date(2026, 12, 31)

    def test_invalid_quarter_raises(self):
        with self.assertRaises(ValueError):
            _common.quarter_to_window("2026Q9")

    def test_window_contains_jira_timestamp(self):
        window = _common.Window(date(2026, 4, 1), date(2026, 6, 30))
        assert window.contains_timestamp("2026-05-01T12:00:00.000+0000") is True
        assert window.contains_timestamp("2026-03-31T12:00:00.000+0000") is False


class TestCommandRateLimitRetry(unittest.TestCase):
    def test_retries_only_rate_limit_failures(self):
        results = [
            _common.CommandResult(1, "", "HTTP 403: secondary rate limit"),
            _common.CommandResult(0, "[]", ""),
        ]
        calls = []
        delays = []

        def runner(command):
            calls.append(command)
            return results.pop(0)

        result = _common.run_with_rate_limit_retry(runner, ["gh", "api"], delays.append)
        assert result.returncode == 0
        assert len(calls) == 2
        assert delays == [5.0]

    def test_honors_retry_after_and_caps_delay(self):
        result = _common.CommandResult(1, "", "rate limit; Retry-After: 120")
        delays = []

        def runner(command):
            return result

        final = _common.run_with_rate_limit_retry(
            runner, ["gh", "api"], delays.append, max_retries=1
        )
        assert final is result
        assert delays == [60.0]


class TestResolveWindow(unittest.TestCase):
    def test_quarter_takes_precedence(self):
        window = _common.resolve_window(quarter="2026Q2")
        assert window.start == date(2026, 4, 1)

    def test_explicit_date_pair(self):
        window = _common.resolve_window(from_date="2026-01-15", to_date="2026-02-20")
        assert window.start == date(2026, 1, 15)
        assert window.end == date(2026, 2, 20)

    def test_missing_everything_raises(self):
        with self.assertRaises(ValueError):
            _common.resolve_window()

    def test_partial_date_range_raises(self):
        with self.assertRaises(ValueError):
            _common.resolve_window(from_date="2026-01-15")

    def test_past_quarter_returned_as_is(self):
        # Inject today as 2026-09-28 (current date per plan)
        # 2026Q1 is fully in the past
        today = date(2026, 9, 28)
        window = _common.resolve_window(quarter="2026Q1", today=today)
        assert window.start == date(2026, 1, 1)
        assert window.end == date(2026, 3, 31)

    def test_in_progress_quarter_capped_at_today(self):
        # Inject today as 2026-09-28
        # 2026Q3 is in progress (July 1 - Sept 30)
        today = date(2026, 9, 28)
        window = _common.resolve_window(quarter="2026Q3", today=today)
        assert window.start == date(2026, 7, 1)
        assert window.end == date(2026, 9, 28)  # Capped at today

    def test_future_quarter_raises(self):
        # Inject today as 2026-09-28
        # 2026Q4 starts Oct 1, which is in the future
        today = date(2026, 9, 28)
        with self.assertRaises(ValueError) as ctx:
            _common.resolve_window(quarter="2026Q4", today=today)
        assert "has not started yet" in str(ctx.exception)


class TestActivityPayload(unittest.TestCase):
    def test_records_are_wrapped_with_empty_meta_by_default(self):
        assert _common.activity_payload([{"member": "a"}]) == {
            "activities": [{"member": "a"}],
            "collector_meta": {},
        }

    def test_counters_land_in_collector_meta(self):
        payload = _common.activity_payload([], excluded_personal_repo_prs=101)
        assert payload["collector_meta"] == {"excluded_personal_repo_prs": 101}

    def test_a_zero_counter_is_still_emitted(self):
        # Reporting "0 excluded" is a real finding; omitting the key would make it
        # indistinguishable from a collector that never counted at all.
        payload = _common.activity_payload([], excluded_personal_repo_prs=0)
        assert payload["collector_meta"] == {"excluded_personal_repo_prs": 0}

    def test_payload_is_json_serializable(self):
        json.dumps(_common.activity_payload([{"member": "a"}], excluded_personal_repo_prs=1))


if __name__ == "__main__":
    unittest.main()
