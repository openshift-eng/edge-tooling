"""Tests for payload_monitor.collectors.prow."""

from unittest.mock import MagicMock, patch

import pytest

from payload_monitor.collectors import prow
from payload_monitor.models import FailingTest, JobResult, JobRun, JobType, PreviousAttempt


class TestProwUrlToGcsPath:
    def test_standard_url(self):
        url = "https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-test/12345"
        result = prow._prow_url_to_gcs_path(url)
        assert result == "gs://test-platform-results/logs/periodic-test/12345"

    def test_no_match(self):
        assert prow._prow_url_to_gcs_path("https://example.com/other") is None

    def test_empty_string(self):
        assert prow._prow_url_to_gcs_path("") is None


class TestTruncate:
    def test_short_string(self):
        assert prow._truncate("short", 100) == "short"

    def test_long_string(self):
        result = prow._truncate("a" * 600)
        assert result.endswith("... [truncated]")
        assert len(result) == prow.MAX_ERROR_LENGTH + len("... [truncated]")

    def test_exact_length(self):
        s = "x" * prow.MAX_ERROR_LENGTH
        assert prow._truncate(s) == s


class TestParseJunit:
    def test_basic_failure(self):
        xml = """<?xml version="1.0"?>
        <testsuite>
          <testcase name="test1" time="1.5">
            <failure message="expected true">details</failure>
          </testcase>
          <testcase name="test2" time="2.0" />
        </testsuite>"""
        tests = prow._parse_junit(xml)
        assert len(tests) == 1
        assert tests[0].name == "test1"
        assert tests[0].error_message == "expected true"
        assert tests[0].duration_seconds == 1.5

    def test_failure_with_text_body(self):
        xml = """<?xml version="1.0"?>
        <testsuite>
          <testcase name="test1" time="0">
            <failure>error text here</failure>
          </testcase>
        </testsuite>"""
        tests = prow._parse_junit(xml)
        assert tests[0].error_message == "error text here"

    def test_no_failures(self):
        xml = """<?xml version="1.0"?>
        <testsuite>
          <testcase name="test1" time="1.0" />
        </testsuite>"""
        assert prow._parse_junit(xml) == []

    def test_invalid_xml(self):
        assert prow._parse_junit("not xml") == []

    def test_nested_testsuites(self):
        xml = """<?xml version="1.0"?>
        <testsuites>
          <testsuite>
            <testcase name="inner" time="0.5">
              <failure message="fail" />
            </testcase>
          </testsuite>
        </testsuites>"""
        tests = prow._parse_junit(xml)
        assert len(tests) == 1
        assert tests[0].name == "inner"


class TestExtractErrorSummary:
    def test_empty_list(self):
        assert prow._extract_error_summary([]) == ""

    def test_single_test(self):
        tests = [FailingTest(name="my-step container test", error_message="something failed")]
        result = prow._extract_error_summary(tests)
        assert "my-step" in result
        assert "something failed" in result

    def test_filters_gather_tests(self):
        tests = [
            FailingTest(name="gather-must-gather container test", error_message="gather error"),
            FailingTest(name="real-test container test", error_message="real error"),
        ]
        result = prow._extract_error_summary(tests)
        assert "real-test" in result
        assert "gather" not in result

    def test_falls_back_to_gather_if_only(self):
        tests = [
            FailingTest(name="gather-must-gather container test", error_message="gather error"),
        ]
        result = prow._extract_error_summary(tests)
        assert "gather" in result

    def test_max_three_summaries(self):
        tests = [
            FailingTest(name=f"test-{i} container test", error_message=f"err {i}")
            for i in range(5)
        ]
        result = prow._extract_error_summary(tests)
        assert result.count(";") == 2  # 3 items separated by 2 semicolons


class TestEnrichJob:
    @patch.object(prow, "_fetch_gcs_file")
    def test_enriches_failing_job(self, mock_fetch):
        mock_fetch.return_value = """<?xml version="1.0"?>
        <testsuite>
          <testcase name="test1" time="1.0">
            <failure message="failed" />
          </testcase>
        </testsuite>"""

        job = JobRun(
            name="test-job",
            prow_url="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/test-job/123",
            result=JobResult.FAILURE,
            job_type=JobType.BLOCKING,
            topology="SNO",
        )
        prow.enrich_job(job)
        assert len(job.failing_tests) == 1
        assert job.failing_tests[0].name == "test1"

    def test_skips_no_url(self):
        job = JobRun("test", "", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        prow.enrich_job(job)
        assert job.failing_tests == []

    @patch.object(prow, "_fetch_gcs_file")
    def test_skips_no_xml(self, mock_fetch):
        mock_fetch.return_value = None
        job = JobRun(
            name="test-job",
            prow_url="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/test-job/123",
            result=JobResult.FAILURE,
            job_type=JobType.BLOCKING,
        )
        prow.enrich_job(job)
        assert job.failing_tests == []


class TestEnrichFailingJobs:
    @patch.object(prow, "enrich_job")
    def test_only_enriches_failures_with_topology(self, mock_enrich):
        jobs = [
            JobRun("fail1", "url1", JobResult.FAILURE, JobType.BLOCKING, "SNO"),
            JobRun("pass1", "url2", JobResult.SUCCESS, JobType.BLOCKING, "SNO"),
            JobRun("fail2", "url3", JobResult.FAILURE, JobType.INFORMING, "TNA"),
        ]
        prow.enrich_failing_jobs(jobs)
        assert mock_enrich.call_count == 2

    @patch.object(prow, "enrich_job")
    def test_no_failing_jobs(self, mock_enrich):
        jobs = [
            JobRun("pass1", "url", JobResult.SUCCESS, JobType.BLOCKING, "SNO"),
        ]
        prow.enrich_failing_jobs(jobs)
        mock_enrich.assert_not_called()

    @patch.object(prow, "enrich_job")
    def test_handles_enrich_exception(self, mock_enrich):
        mock_enrich.side_effect = RuntimeError("boom")
        jobs = [
            JobRun("fail1", "url1", JobResult.FAILURE, JobType.BLOCKING, "SNO"),
            JobRun("fail2", "url2", JobResult.FAILURE, JobType.BLOCKING, "TNA"),
        ]
        # Should not raise — errors are caught per job
        prow.enrich_failing_jobs(jobs)

    @patch.object(prow, "enrich_previous_attempt")
    @patch.object(prow, "enrich_job")
    def test_enriches_previous_attempts(self, mock_enrich_job, mock_enrich_pa):
        pa = PreviousAttempt(prow_url="https://prow/1", result=JobResult.FAILURE)
        jobs = [
            JobRun("j1", "url2", JobResult.SUCCESS, JobType.BLOCKING, "SNO",
                   retries=1, previous_attempts=[pa]),
        ]
        prow.enrich_failing_jobs(jobs)
        mock_enrich_job.assert_not_called()
        mock_enrich_pa.assert_called_once_with(pa)

    @patch.object(prow, "enrich_previous_attempt")
    @patch.object(prow, "enrich_job")
    def test_enriches_both_failed_job_and_previous_attempts(self, mock_enrich_job, mock_enrich_pa):
        pa = PreviousAttempt(prow_url="https://prow/1", result=JobResult.FAILURE)
        jobs = [
            JobRun("j1", "url2", JobResult.FAILURE, JobType.BLOCKING, "SNO",
                   retries=1, previous_attempts=[pa]),
        ]
        prow.enrich_failing_jobs(jobs)
        mock_enrich_job.assert_called_once()
        mock_enrich_pa.assert_called_once_with(pa)


    @patch.object(prow, "enrich_previous_attempt")
    @patch.object(prow, "enrich_job")
    def test_skips_previous_attempts_when_no_topology(self, mock_enrich_job, mock_enrich_pa):
        pa = PreviousAttempt(prow_url="https://prow/1", result=JobResult.FAILURE)
        jobs = [
            JobRun("j1", "url2", JobResult.FAILURE, JobType.BLOCKING, "",
                   retries=1, previous_attempts=[pa]),
        ]
        prow.enrich_failing_jobs(jobs)
        mock_enrich_job.assert_not_called()
        mock_enrich_pa.assert_not_called()


class TestEnrichPreviousAttempt:
    @patch.object(prow, "_fetch_gcs_file")
    def test_enriches_previous_attempt(self, mock_fetch):
        mock_fetch.return_value = """<?xml version="1.0"?>
        <testsuite>
          <testcase name="test1" time="1.0">
            <failure message="failed" />
          </testcase>
        </testsuite>"""

        pa = PreviousAttempt(
            prow_url="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/test-job/123",
            result=JobResult.FAILURE,
        )
        prow.enrich_previous_attempt(pa)
        assert len(pa.failing_tests) == 1
        assert pa.failing_tests[0].name == "test1"
        assert pa.error_summary != ""

    def test_skips_no_url(self):
        pa = PreviousAttempt(prow_url="", result=JobResult.FAILURE)
        prow.enrich_previous_attempt(pa)
        assert pa.failing_tests == []

    @patch.object(prow, "_fetch_gcs_file")
    def test_skips_no_xml(self, mock_fetch):
        mock_fetch.return_value = None
        pa = PreviousAttempt(
            prow_url="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/test-job/123",
            result=JobResult.FAILURE,
        )
        prow.enrich_previous_attempt(pa)
        assert pa.failing_tests == []
