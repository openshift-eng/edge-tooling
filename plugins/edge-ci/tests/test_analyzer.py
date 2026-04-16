"""Tests for payload_monitor.analyzer."""

import os
from unittest.mock import patch, MagicMock

import pytest

from payload_monitor.analyzer import (
    _find_recurring_failures,
    _find_unmatched_jobs,
    analyze,
)
from payload_monitor.config import Config
from payload_monitor.models import (
    FailingTest,
    JobResult,
    JobRun,
    JobType,
    MonitorReport,
    Payload,
    PayloadStatus,
    StreamReport,
)


def _make_job(name, result=JobResult.FAILURE, topology="SNO"):
    return JobRun(name=name, prow_url=f"https://prow/{name}",
                  result=result, job_type=JobType.BLOCKING, topology=topology)


def _make_payload(tag, jobs):
    return Payload(tag=tag, stream="s", version="4.19",
                   status=PayloadStatus.REJECTED, jobs=jobs)


class TestFindRecurringFailures:
    def test_counts_across_payloads(self):
        j1 = _make_job("job-a")
        j2 = _make_job("job-a")
        j3 = _make_job("job-b")
        p1 = _make_payload("t1", [j1])
        p2 = _make_payload("t2", [j2, j3])
        stream = StreamReport("s", "4.19", payloads=[p1, p2])

        counts = _find_recurring_failures([stream])
        assert counts["job-a"] == 2
        assert counts["job-b"] == 1

    def test_counts_across_streams(self):
        j1 = _make_job("job-a")
        j2 = _make_job("job-a")
        p1 = _make_payload("t1", [j1])
        p2 = _make_payload("t2", [j2])
        s1 = StreamReport("s1", "4.18", payloads=[p1])
        s2 = StreamReport("s2", "4.19", payloads=[p2])

        counts = _find_recurring_failures([s1, s2])
        assert counts["job-a"] == 2

    def test_empty_streams(self):
        assert _find_recurring_failures([]) == {}


class TestFindUnmatchedJobs:
    def test_finds_unmatched(self):
        j1 = _make_job("matched")
        j2 = _make_job("unmatched")
        p = _make_payload("t1", [j1, j2])
        stream = StreamReport("s", "4.19", payloads=[p])

        result = _find_unmatched_jobs([stream], {"matched"})
        assert len(result) == 1
        assert result[0][0].name == "unmatched"
        assert "4.19" in result[0][1]

    def test_deduplicates_across_versions(self):
        j1 = _make_job("same-job")
        j2 = _make_job("same-job")
        p1 = _make_payload("t1", [j1])
        p2 = _make_payload("t2", [j2])
        s1 = StreamReport("s1", "4.18", payloads=[p1])
        s2 = StreamReport("s2", "4.19", payloads=[p2])

        result = _find_unmatched_jobs([s1, s2], set())
        assert len(result) == 1
        assert sorted(result[0][1]) == ["4.18", "4.19"]

    def test_all_matched(self):
        j1 = _make_job("matched")
        p = _make_payload("t1", [j1])
        stream = StreamReport("s", "4.19", payloads=[p])

        assert _find_unmatched_jobs([stream], {"matched"}) == []


class TestAnalyze:
    @patch("payload_monitor.analyzer.jira_has_auth", return_value=False)
    @patch("payload_monitor.analyzer.jira_collector")
    def test_no_jira_auth(self, mock_jira, mock_auth):
        mock_jira.search_bugs_for_jobs.return_value = {}
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[
                _make_payload("t1", [_make_job("j1")]),
            ])],
        )
        analyze(report, Config())
        assert report.skip_jira is True

    @patch("payload_monitor.analyzer.jira_has_auth", return_value=True)
    @patch("payload_monitor.analyzer.jira_collector")
    def test_analyze_with_jira(self, mock_jira, mock_auth):
        from payload_monitor.models import JiraBug, SuggestedBug

        mock_jira.search_bugs_for_jobs.return_value = {
            "j1": [JiraBug(key="OCPBUGS-1", summary="bug", status="New")]
        }
        mock_jira.suggest_bug.return_value = SuggestedBug(
            title="t", description="d", job_name="j2", topology="SNO",
        )

        j1 = _make_job("j1")
        j2 = _make_job("j2")
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[
                _make_payload("t1", [j1, j2]),
            ])],
        )
        analyze(report, Config())

        assert len(report.jira_bugs) == 1
        assert report.jira_bugs[0].key == "OCPBUGS-1"
        assert len(report.suggested_bugs) == 1
        assert report.suggested_bugs[0].job_name == "j2"

    @patch("payload_monitor.analyzer.jira_has_auth", return_value=True)
    @patch("payload_monitor.analyzer.jira_collector")
    def test_no_failures(self, mock_jira, mock_auth):
        mock_jira.search_bugs_for_jobs.return_value = {}
        job = _make_job("j1", result=JobResult.SUCCESS)
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[
                _make_payload("t1", [job]),
            ])],
        )
        analyze(report, Config())
        assert report.jira_bugs == []
        assert report.suggested_bugs == []
        mock_jira.search_bugs_for_jobs.assert_not_called()
