"""Tests for payload_monitor.collectors.pr_payload."""

import json
from unittest.mock import patch

import pytest

from payload_monitor.collectors import pr_payload
from payload_monitor.collectors.pr_payload import (
    PRTriageResult,
    _extract_test_description,
    _job_name_from_url,
    _parse_prow_urls,
    build_triage_result,
    classify_failure,
    classify_failures,
    format_triage_json,
)
from payload_monitor.models import (
    FailingTest,
    FailureVerdict,
    JobResult,
    PRPayloadJob,
)


class TestParseProwUrls:
    def test_extracts_urls(self):
        html = """
        <a href="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/job-a/111">job-a</a>
        <a href="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/job-b/222">job-b</a>
        """
        result = _parse_prow_urls(html)
        assert len(result) == 2
        assert "job-a/111" in result[0]
        assert "job-b/222" in result[1]

    def test_deduplicates(self):
        html = """
        <a href="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/job-a/111">1</a>
        <a href="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/job-a/111">2</a>
        """
        result = _parse_prow_urls(html)
        assert len(result) == 1

    def test_no_links(self):
        assert _parse_prow_urls("<html><body>no links</body></html>") == []

    def test_non_prow_links_ignored(self):
        html = '<a href="https://example.com/foo">not prow</a>'
        assert _parse_prow_urls(html) == []


class TestJobNameFromUrl:
    def test_standard_url(self):
        url = "https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-ci-test-job/12345"
        assert _job_name_from_url(url) == "periodic-ci-test-job"

    def test_non_matching_url(self):
        url = "https://example.com/other"
        assert _job_name_from_url(url) == url


class TestFetchJobResult:
    @patch.object(pr_payload, "_fetch_gcs_file")
    @patch.object(pr_payload, "_prow_url_to_gcs_path")
    def test_success(self, mock_gcs_path, mock_fetch):
        mock_gcs_path.return_value = "gs://bucket/logs/job/123"
        mock_fetch.return_value = json.dumps({"result": "SUCCESS", "passed": True})

        result = pr_payload._fetch_job_result("https://prow/view/gs/bucket/logs/job/123")
        assert result == JobResult.SUCCESS

    @patch.object(pr_payload, "_fetch_gcs_file")
    @patch.object(pr_payload, "_prow_url_to_gcs_path")
    def test_failure(self, mock_gcs_path, mock_fetch):
        mock_gcs_path.return_value = "gs://bucket/logs/job/123"
        mock_fetch.return_value = json.dumps({"result": "FAILURE"})

        result = pr_payload._fetch_job_result("https://prow/view/gs/bucket/logs/job/123")
        assert result == JobResult.FAILURE

    @patch.object(pr_payload, "_fetch_gcs_file")
    @patch.object(pr_payload, "_prow_url_to_gcs_path")
    def test_pending(self, mock_gcs_path, mock_fetch):
        mock_gcs_path.return_value = "gs://bucket/logs/job/123"
        mock_fetch.return_value = json.dumps({"result": "PENDING"})

        result = pr_payload._fetch_job_result("https://prow/view/gs/bucket/logs/job/123")
        assert result == JobResult.PENDING

    @patch.object(pr_payload, "_prow_url_to_gcs_path")
    def test_no_gcs_path(self, mock_gcs_path):
        mock_gcs_path.return_value = None
        result = pr_payload._fetch_job_result("https://example.com")
        assert result == JobResult.UNKNOWN

    @patch.object(pr_payload, "_fetch_gcs_file")
    @patch.object(pr_payload, "_prow_url_to_gcs_path")
    def test_invalid_json(self, mock_gcs_path, mock_fetch):
        mock_gcs_path.return_value = "gs://bucket/logs/job/123"
        mock_fetch.return_value = "not json"

        result = pr_payload._fetch_job_result("https://prow/view/gs/bucket/logs/job/123")
        assert result == JobResult.UNKNOWN

    @patch.object(pr_payload, "_fetch_gcs_file")
    @patch.object(pr_payload, "_prow_url_to_gcs_path")
    def test_no_finished_file(self, mock_gcs_path, mock_fetch):
        mock_gcs_path.return_value = "gs://bucket/logs/job/123"
        mock_fetch.return_value = None

        result = pr_payload._fetch_job_result("https://prow/view/gs/bucket/logs/job/123")
        assert result == JobResult.UNKNOWN


class TestExtractTestDescription:
    def test_strips_sig_tags(self):
        name = "[sig-etcd][Feature:TwoNode] Two Node with Fencing should start normally"
        result = _extract_test_description(name)
        assert result == "Two Node with Fencing should start normally"

    def test_multiple_tags(self):
        name = "[sig-auth][Feature:OAuth][Serial] OAuth server should handle token"
        result = _extract_test_description(name)
        assert result == "OAuth server should handle token"

    def test_no_tags(self):
        name = "TestSomething"
        assert _extract_test_description(name) == "TestSomething"

    def test_empty_string(self):
        assert _extract_test_description("") == ""


class TestClassifyFailure:
    def _make_job(self, test_name, error="error"):
        return PRPayloadJob(
            name="test-job",
            prow_url="https://prow/1",
            result=JobResult.FAILURE,
            failing_tests=[FailingTest(name=test_name, error_message=error)],
        )

    def test_test_in_diff_pr_caused(self):
        job = self._make_job(
            "[sig-etcd] Two Node with Fencing should recover after etcd kill"
        )
        diff = """
+   g.It("should recover after etcd kill", func() {
+       // test code
+   })
"""
        verdict = classify_failure(job, diff, ["test/two_node_test.go"])
        assert verdict.verdict == "pr-caused"

    def test_test_not_in_diff_flaky(self):
        job = self._make_job(
            "[sig-etcd] Two Node with Fencing should recover after etcd kill"
        )
        diff = """
+func helperFunction() {
+    return nil
+}
"""
        verdict = classify_failure(job, diff, ["pkg/helper.go"])
        assert verdict.verdict == "flaky"

    def test_func_identifier_match(self):
        job = self._make_job(
            "[sig-etcd] test", error="helperFunction failed"
        )
        diff = """+func helperFunction() error {
+    return nil
+}
"""
        verdict = classify_failure(job, diff, ["pkg/helper.go"])
        assert verdict.verdict == "pr-caused"
        assert "helperFunction" in verdict.reason

    def test_infra_failure_skipped(self):
        job = self._make_job(
            "[sig-cluster] cluster precondition check failed"
        )
        diff = "+something unrelated in the diff that is long enough to match"
        verdict = classify_failure(job, diff, ["file.go"])
        assert verdict.verdict == "flaky"

    def test_short_description_not_matched(self):
        job = self._make_job("[sig-x] ab")
        diff = "+ab is in the diff"
        verdict = classify_failure(job, diff, ["f.go"])
        assert verdict.verdict == "flaky"

    def test_no_failing_tests(self):
        job = PRPayloadJob(
            name="test-job",
            prow_url="https://prow/1",
            result=JobResult.FAILURE,
        )
        verdict = classify_failure(job, "+diff content", ["file.go"])
        assert verdict.verdict == "flaky"


class TestClassifyFailures:
    def test_only_failures(self):
        jobs = [
            PRPayloadJob("pass-job", "url1", JobResult.SUCCESS),
            PRPayloadJob("fail-job", "url2", JobResult.FAILURE,
                         failing_tests=[FailingTest(name="[sig] test x", error_message="err")]),
        ]
        verdicts = classify_failures(jobs, ["f.go"], "+unrelated diff content")
        assert "fail-job" in verdicts
        assert "pass-job" not in verdicts

    def test_empty_jobs(self):
        assert classify_failures([], [], "") == {}


class TestBuildTriageResult:
    def test_counts(self):
        jobs = [
            PRPayloadJob("j1", "url1", JobResult.SUCCESS),
            PRPayloadJob("j2", "url2", JobResult.FAILURE),
            PRPayloadJob("j3", "url3", JobResult.SUCCESS),
            PRPayloadJob("j4", "url4", JobResult.FAILURE),
        ]
        result = build_triage_result("https://payload/run/1", "openshift/origin#1", jobs)
        assert result.total_jobs == 4
        assert result.passed == 2
        assert result.failed == 2
        assert result.payload_url == "https://payload/run/1"

    def test_all_pass(self):
        jobs = [PRPayloadJob("j1", "url1", JobResult.SUCCESS)]
        result = build_triage_result("url", "ref", jobs)
        assert result.passed == 1
        assert result.failed == 0

    def test_empty(self):
        result = build_triage_result("url", "ref", [])
        assert result.total_jobs == 0


class TestFormatTriageJson:
    def test_all_passed(self):
        result = PRTriageResult(
            payload_url="url", pr_ref="ref", total_jobs=2, passed=2, failed=0,
            jobs=[
                PRPayloadJob("j1", "url1", JobResult.SUCCESS),
                PRPayloadJob("j2", "url2", JobResult.SUCCESS),
            ],
        )
        output = json.loads(format_triage_json(result))
        assert output["complete"] is True
        assert "No triage needed" in output["recommendation"]

    def test_has_failures_flaky(self):
        result = PRTriageResult(
            payload_url="url", pr_ref="ref", total_jobs=2, passed=1, failed=1,
            jobs=[
                PRPayloadJob("j1", "url1", JobResult.SUCCESS),
                PRPayloadJob("j2", "url2", JobResult.FAILURE,
                             failing_tests=[FailingTest(name="test1", error_message="err")]),
            ],
            verdicts={"j2": FailureVerdict(verdict="flaky", reason="not in diff")},
        )
        output = json.loads(format_triage_json(result))
        assert output["failed"] == 1
        assert "unrelated" in output["recommendation"].lower()

    def test_has_unknown_jobs(self):
        result = PRTriageResult(
            payload_url="url", pr_ref="ref", total_jobs=2, passed=1, failed=0,
            jobs=[
                PRPayloadJob("j1", "url1", JobResult.SUCCESS),
                PRPayloadJob("j2", "url2", JobResult.UNKNOWN),
            ],
        )
        output = json.loads(format_triage_json(result))
        assert output["complete"] is False
        assert "running" in output["recommendation"].lower()

    def test_pr_caused_failures(self):
        result = PRTriageResult(
            payload_url="url", pr_ref="ref", total_jobs=1, passed=0, failed=1,
            jobs=[
                PRPayloadJob("j1", "url1", JobResult.FAILURE,
                             failing_tests=[FailingTest(name="t", error_message="e")]),
            ],
            verdicts={"j1": FailureVerdict(verdict="pr-caused", reason="in diff")},
        )
        output = json.loads(format_triage_json(result))
        assert "Investigate" in output["recommendation"]


class TestParsePrRefWrapper:
    def test_valid_ref(self):
        result = pr_payload._parse_pr_ref("openshift/origin#31276")
        assert result == ("openshift/origin", "31276")

    def test_github_url(self):
        result = pr_payload._parse_pr_ref("https://github.com/openshift/installer/pull/10546")
        assert result == ("openshift/installer", "10546")

    def test_invalid(self):
        assert pr_payload._parse_pr_ref("invalid") is None
