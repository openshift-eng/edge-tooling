"""Tests for payload_monitor.models."""

import pytest

from payload_monitor.models import (
    ComponentRegression,
    DeepAnalysis,
    FailingTest,
    JobResult,
    JobRun,
    JobType,
    MonitorReport,
    Payload,
    PayloadStatus,
    Regression,
    StreamReport,
    SuggestedBug,
    Topology,
)


class TestTopology:
    def test_matches_exact_pattern(self):
        topo = Topology("SNO", ["sno", "single-node"], [], "SNO")
        assert topo.matches("periodic-ci-e2e-metal-sno-test")
        assert topo.matches("periodic-ci-e2e-single-node-test")

    def test_no_match(self):
        topo = Topology("SNO", ["sno", "single-node"], [], "SNO")
        assert not topo.matches("periodic-ci-e2e-metal-arbiter-test")

    def test_exclude_patterns(self):
        topo = Topology("SNO", ["sno"], ["telco"], "SNO")
        assert topo.matches("periodic-ci-e2e-metal-sno-test")
        assert not topo.matches("periodic-ci-e2e-telco-sno-test")

    def test_case_insensitive(self):
        topo = Topology("SNO", ["sno"], [], "SNO")
        assert topo.matches("periodic-ci-e2e-metal-SNO-test")

    def test_pattern_at_boundaries(self):
        topo = Topology("SNO", ["sno"], [], "SNO")
        # Pattern should match at word boundaries (separated by - or _)
        assert topo.matches("sno-test")
        assert topo.matches("test-sno")
        assert topo.matches("test_sno_live")
        # Should not match as substring within a word
        assert not topo.matches("snooze-test")

    def test_tna_topology(self):
        topo = Topology("TNA", ["tna", "arbiter"], [], "TNA")
        assert topo.matches("periodic-ci-e2e-metal-ipi-arbiter-test")
        assert topo.matches("periodic-ci-e2e-metal-tna-test")

    def test_tnf_topology(self):
        topo = Topology("TNF", ["tnf", "fencing"], [], "TNF")
        assert topo.matches("periodic-ci-e2e-metal-fencing-test")
        assert topo.matches("periodic-ci-e2e-metal-tnf-test")


class TestPayload:
    def test_failing_edge_jobs_filters_failures(self):
        job1 = JobRun("j1", "", JobResult.SUCCESS, JobType.BLOCKING, "SNO")
        job2 = JobRun("j2", "", JobResult.FAILURE, JobType.INFORMING, "TNA")
        job3 = JobRun("j3", "", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        payload = Payload("tag", "stream", "4.19", PayloadStatus.REJECTED, jobs=[job1, job2, job3])
        assert payload.failing_edge_jobs == [job2, job3]

    def test_failing_edge_jobs_empty_when_all_pass(self):
        job1 = JobRun("j1", "", JobResult.SUCCESS, JobType.BLOCKING, "SNO")
        payload = Payload("tag", "stream", "4.19", PayloadStatus.ACCEPTED, jobs=[job1])
        assert payload.failing_edge_jobs == []


class TestStreamReport:
    def test_latest_payload(self, sample_stream):
        assert sample_stream.latest_payload is not None
        assert sample_stream.latest_payload.tag == "4.19.0-0.nightly-2026-03-25-085944"

    def test_latest_payload_empty(self):
        stream = StreamReport("s", "4.19", payloads=[])
        assert stream.latest_payload is None

    def test_total_edge_failures(self, sample_stream):
        assert sample_stream.total_edge_failures == 1

    def test_total_edge_failures_multiple_payloads(self):
        job_f = JobRun("j1", "", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        job_s = JobRun("j2", "", JobResult.SUCCESS, JobType.BLOCKING, "SNO")
        p1 = Payload("t1", "s", "4.19", PayloadStatus.REJECTED, jobs=[job_f, job_s])
        p2 = Payload("t2", "s", "4.19", PayloadStatus.REJECTED, jobs=[job_f])
        stream = StreamReport("s", "4.19", payloads=[p1, p2])
        assert stream.total_edge_failures == 2


class TestEnums:
    def test_payload_status_values(self):
        assert PayloadStatus.ACCEPTED.value == "Accepted"
        assert PayloadStatus.REJECTED.value == "Rejected"
        assert PayloadStatus.PENDING.value == "Pending"

    def test_job_result_values(self):
        assert JobResult.SUCCESS.value == "S"
        assert JobResult.FAILURE.value == "F"
        assert JobResult.PENDING.value == "P"
        assert JobResult.UNKNOWN.value == "U"

    def test_job_type_values(self):
        assert JobType.BLOCKING.value == "blocking"
        assert JobType.INFORMING.value == "informing"


class TestPayloadEdgeFailureTypes:
    def test_blocking_edge_failures(self):
        blocking_fail = JobRun("j1", "", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        informing_fail = JobRun("j2", "", JobResult.FAILURE, JobType.INFORMING, "TNA")
        blocking_pass = JobRun("j3", "", JobResult.SUCCESS, JobType.BLOCKING, "SNO")
        payload = Payload("tag", "stream", "4.19", PayloadStatus.REJECTED,
                          jobs=[blocking_fail, informing_fail, blocking_pass])
        assert payload.blocking_edge_failures == [blocking_fail]

    def test_informing_edge_failures(self):
        blocking_fail = JobRun("j1", "", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        informing_fail = JobRun("j2", "", JobResult.FAILURE, JobType.INFORMING, "TNA")
        payload = Payload("tag", "stream", "4.19", PayloadStatus.REJECTED,
                          jobs=[blocking_fail, informing_fail])
        assert payload.informing_edge_failures == [informing_fail]

    def test_no_failures(self):
        job = JobRun("j1", "", JobResult.SUCCESS, JobType.BLOCKING, "SNO")
        payload = Payload("tag", "stream", "4.19", PayloadStatus.ACCEPTED, jobs=[job])
        assert payload.blocking_edge_failures == []
        assert payload.informing_edge_failures == []


class TestStreamReportEdgeFailureTypes:
    def test_total_blocking_and_informing(self):
        b_fail = JobRun("j1", "", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        i_fail = JobRun("j2", "", JobResult.FAILURE, JobType.INFORMING, "TNA")
        p1 = Payload("t1", "s", "4.19", PayloadStatus.REJECTED, jobs=[b_fail, i_fail])
        p2 = Payload("t2", "s", "4.19", PayloadStatus.REJECTED, jobs=[b_fail])
        stream = StreamReport("s", "4.19", payloads=[p1, p2])
        assert stream.total_blocking_edge_failures == 2
        assert stream.total_informing_edge_failures == 1

    def test_no_failures(self):
        job = JobRun("j1", "", JobResult.SUCCESS, JobType.BLOCKING, "SNO")
        payload = Payload("t1", "s", "4.19", PayloadStatus.ACCEPTED, jobs=[job])
        stream = StreamReport("s", "4.19", payloads=[payload])
        assert stream.total_blocking_edge_failures == 0
        assert stream.total_informing_edge_failures == 0

    def test_informing_only(self):
        i_fail = JobRun("j1", "", JobResult.FAILURE, JobType.INFORMING, "SNO")
        payload = Payload("t1", "s", "4.19", PayloadStatus.ACCEPTED, jobs=[i_fail])
        stream = StreamReport("s", "4.19", payloads=[payload])
        assert stream.total_blocking_edge_failures == 0
        assert stream.total_informing_edge_failures == 1


class TestDataclassDefaults:
    def test_job_run_defaults(self):
        job = JobRun("name", "url", JobResult.SUCCESS, JobType.BLOCKING)
        assert job.topology is None
        assert job.failing_tests == []
        assert job.error_summary == ""
        assert job.deep_analysis is None

    def test_monitor_report_defaults(self):
        report = MonitorReport()
        assert report.generated_at == ""
        assert report.streams == []
        assert report.jira_bugs == []
        assert report.suggested_bugs == []
        assert report.component_regressions == []
        assert report.skip_prow is False
        assert report.skip_sippy is False
        assert report.skip_jira is False
        assert report.data_errors == []

    def test_suggested_bug_defaults(self):
        bug = SuggestedBug(title="t", description="d", job_name="j", topology="SNO")
        assert bug.versions == []
        assert bug.failing_tests == []
        assert bug.create_url == ""
        assert bug.prow_url == ""
        assert bug.full_description == ""
