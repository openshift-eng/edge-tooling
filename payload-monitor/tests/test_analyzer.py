"""Tests for payload_monitor.analyzer."""

from unittest.mock import patch

from payload_monitor.analyzer import (
    _correlate_cross_topology,
    _find_escalation_risks,
    _find_recurring_failures,
    _find_unique_failing_jobs,
    _normalize_job_name,
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


class TestFindUniqueFailingJobs:
    def test_finds_jobs(self):
        j1 = _make_job("job-a")
        j2 = _make_job("job-b")
        p = _make_payload("t1", [j1, j2])
        stream = StreamReport("s", "4.19", payloads=[p])

        result = _find_unique_failing_jobs([stream])
        assert len(result) == 2
        names = {r[0].name for r in result}
        assert names == {"job-a", "job-b"}

    def test_deduplicates_across_versions(self):
        j1 = _make_job("same-job")
        j2 = _make_job("same-job")
        p1 = _make_payload("t1", [j1])
        p2 = _make_payload("t2", [j2])
        s1 = StreamReport("s1", "4.18", payloads=[p1])
        s2 = StreamReport("s2", "4.19", payloads=[p2])

        result = _find_unique_failing_jobs([s1, s2])
        assert len(result) == 1
        assert sorted(result[0][1]) == ["4.18", "4.19"]

    def test_empty_streams(self):
        assert _find_unique_failing_jobs([]) == []


class TestFailureCountsOnReport:
    def test_failure_counts_stored_on_report(self):
        j1 = _make_job("job-a")
        j2 = _make_job("job-a")
        j3 = _make_job("job-b")
        p1 = _make_payload("t1", [j1])
        p2 = _make_payload("t2", [j2, j3])
        stream = StreamReport("s", "4.19", payloads=[p1, p2])
        report = MonitorReport(generated_at="now", streams=[stream])

        analyze(report, Config())

        assert report.failure_counts["job-a"] == 2
        assert report.failure_counts["job-b"] == 1

    def test_failure_counts_empty_when_no_failures(self):
        job = _make_job("j1", result=JobResult.SUCCESS)
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[
                _make_payload("t1", [job]),
            ])],
        )
        analyze(report, Config())
        assert report.failure_counts == {}


class TestAnalyze:
    def test_suggests_bugs_for_failing_jobs(self):
        j1 = _make_job("j1")
        j2 = _make_job("j2")
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[
                _make_payload("t1", [j1, j2]),
            ])],
        )
        analyze(report, Config())

        assert len(report.suggested_bugs) == 2
        assert {b.job_name for b in report.suggested_bugs} == {"j1", "j2"}

    def test_no_failures(self):
        job = _make_job("j1", result=JobResult.SUCCESS)
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[
                _make_payload("t1", [job]),
            ])],
        )
        analyze(report, Config())
        assert report.suggested_bugs == []


def _make_informing_job(name, result=JobResult.FAILURE, topology="SNO"):
    return JobRun(name=name, prow_url=f"https://prow/{name}",
                  result=result, job_type=JobType.INFORMING, topology=topology)


class TestFindEscalationRisks:
    def test_consecutive(self):
        """Informing job failing in last 3 consecutive payloads -> 1 EscalationRisk."""
        def j(r):
            return _make_informing_job("j1", result=r)
        # 5 payloads, oldest first; j1 passes in first 2, fails in last 3
        payloads = [
            _make_payload("t1", [j(JobResult.SUCCESS)]),
            _make_payload("t2", [j(JobResult.SUCCESS)]),
            _make_payload("t3", [j(JobResult.FAILURE)]),
            _make_payload("t4", [j(JobResult.FAILURE)]),
            _make_payload("t5", [j(JobResult.FAILURE)]),
        ]
        stream = StreamReport("s", "4.19", payloads=payloads)

        risks = _find_escalation_risks([stream], Config())
        assert len(risks) == 1
        assert risks[0].job_name == "j1"
        assert risks[0].consecutive_failures == 3
        assert risks[0].topology == "SNO"
        assert risks[0].version == "4.19"
        assert risks[0].prow_url == "https://prow/j1"
        assert "sippy.dptools.openshift.org" in risks[0].triage_url
        assert "4.19" in risks[0].triage_url
        assert len(risks[0].failing_runs) == 3
        assert risks[0].failing_runs[0]["payload_tag"] == "t5"
        assert risks[0].failing_runs[1]["payload_tag"] == "t4"
        assert risks[0].failing_runs[2]["payload_tag"] == "t3"
        assert risks[0].failing_runs[0]["prow_url"] == "https://prow/j1"

    def test_non_consecutive(self):
        """Informing job fails in payloads 1, 3, 5 (gaps) -> no EscalationRisk."""
        # Oldest first: F, S, F, S, F -> newest first: F, S, F, S, F
        # Consecutive from newest: only 1 (fails, then passes)
        payloads = [
            _make_payload("t1", [_make_informing_job("j1", result=JobResult.FAILURE)]),
            _make_payload("t2", [_make_informing_job("j1", result=JobResult.SUCCESS)]),
            _make_payload("t3", [_make_informing_job("j1", result=JobResult.FAILURE)]),
            _make_payload("t4", [_make_informing_job("j1", result=JobResult.SUCCESS)]),
            _make_payload("t5", [_make_informing_job("j1", result=JobResult.FAILURE)]),
        ]
        stream = StreamReport("s", "4.19", payloads=payloads)

        risks = _find_escalation_risks([stream], Config())
        assert len(risks) == 0

    def test_blocking_excluded(self):
        """Blocking job failing in all payloads -> no EscalationRisk (only informing)."""
        payloads = [
            _make_payload(f"t{i}", [_make_job("j1", result=JobResult.FAILURE)])
            for i in range(5)
        ]
        stream = StreamReport("s", "4.19", payloads=payloads)

        risks = _find_escalation_risks([stream], Config())
        assert len(risks) == 0

    def test_below_threshold(self):
        """Informing job fails in 2 consecutive -> no EscalationRisk (threshold is 3)."""
        payloads = [
            _make_payload("t1", [_make_informing_job("j1", result=JobResult.SUCCESS)]),
            _make_payload("t2", [_make_informing_job("j1", result=JobResult.SUCCESS)]),
            _make_payload("t3", [_make_informing_job("j1", result=JobResult.SUCCESS)]),
            _make_payload("t4", [_make_informing_job("j1", result=JobResult.FAILURE)]),
            _make_payload("t5", [_make_informing_job("j1", result=JobResult.FAILURE)]),
        ]
        stream = StreamReport("s", "4.19", payloads=payloads)

        risks = _find_escalation_risks([stream], Config())
        assert len(risks) == 0

    def test_empty_streams(self):
        """No streams -> empty list."""
        assert _find_escalation_risks([], Config()) == []

    def test_latest_failure_empty_url_not_replaced_by_older(self):
        """Newest failure has an empty prow_url -> it must not be replaced by
        an older job's URL."""
        def informing_job(url):
            return JobRun(name="j1", prow_url=url, result=JobResult.FAILURE,
                          job_type=JobType.INFORMING, topology="SNO")

        payloads = [
            _make_payload("t1", [informing_job("https://prow/old")]),
            _make_payload("t2", [informing_job("https://prow/mid")]),
            _make_payload("t3", [informing_job("")]),
        ]
        stream = StreamReport("s", "4.19", payloads=payloads)

        risks = _find_escalation_risks([stream], Config())
        assert len(risks) == 1
        assert risks[0].prow_url == ""

    def test_all_passing(self):
        """All jobs pass -> empty list."""
        payloads = [
            _make_payload(f"t{i}", [_make_informing_job("j1", result=JobResult.SUCCESS)])
            for i in range(5)
        ]
        stream = StreamReport("s", "4.19", payloads=payloads)

        risks = _find_escalation_risks([stream], Config())
        assert len(risks) == 0


class TestNormalizeJobName:
    def test_sno(self):
        """SNO job name -> topology marker replaced."""
        config = Config()
        result = _normalize_job_name(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-single-node-live-iso",
            config,
        )
        assert "__TOPO__" in result
        assert "single-node" not in result

    def test_tna(self):
        """TNA job name with 'arbiter' -> marker replaced."""
        config = Config()
        result = _normalize_job_name(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-arbiter-live-iso",
            config,
        )
        assert "__TOPO__" in result
        assert "arbiter" not in result

    def test_no_topology(self):
        """Non-edge job -> returned unchanged."""
        config = Config()
        name = "periodic-ci-openshift-release-master-nightly-4.19-e2e-aws-ovn"
        result = _normalize_job_name(name, config)
        assert result == name

    def test_exclude_patterns_respected(self):
        """Job matching exclude_patterns (e.g. 'telco') should not be normalized."""
        config = Config()
        name = "periodic-ci-openshift-release-master-nightly-4.19-e2e-telco-single-node-live-iso"
        result = _normalize_job_name(name, config)
        # 'telco' is in SNO exclude_patterns, so 'single-node' should NOT be replaced
        assert "__TOPO__" not in result
        assert result == name


class TestCorrelateCrossTopology:
    def test_groups(self):
        """SNO and TNA jobs share base -> cross_topology maps each to the other."""
        config = Config()
        # Two jobs that differ only in topology marker
        sno_job = _make_job(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-single-node-live-iso",
            topology="SNO",
        )
        tna_job = _make_job(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-arbiter-live-iso",
            topology="TNA",
        )
        payloads = [_make_payload("t1", [sno_job, tna_job])]
        stream = StreamReport("s", "4.19", payloads=payloads)

        cross = _correlate_cross_topology([stream], config)
        assert sno_job.name in cross
        assert "TNA" in cross[sno_job.name]
        assert tna_job.name in cross
        assert "SNO" in cross[tna_job.name]

    def test_different_base_names_not_grouped(self):
        """Different base names -> cross_topology empty."""
        config = Config()
        sno_job = _make_job(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-single-node-live-iso",
            topology="SNO",
        )
        tna_job = _make_job(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-arbiter-upgrade",
            topology="TNA",
        )
        payloads = [_make_payload("t1", [sno_job, tna_job])]
        stream = StreamReport("s", "4.19", payloads=payloads)

        cross = _correlate_cross_topology([stream], config)
        assert cross == {}


class TestAbsentJobBreaksStreak:
    def test_absent_job_breaks_streak(self):
        """Job absent from a payload should break the consecutive failure streak."""
        # 5 payloads, oldest first; j1 present in t1-t3 (fail), absent in t4, present in t5 (fail)
        payloads = [
            _make_payload("t1", [_make_informing_job("j1", result=JobResult.FAILURE)]),
            _make_payload("t2", [_make_informing_job("j1", result=JobResult.FAILURE)]),
            _make_payload("t3", [_make_informing_job("j1", result=JobResult.FAILURE)]),
            _make_payload("t4", []),  # j1 absent
            _make_payload("t5", [_make_informing_job("j1", result=JobResult.FAILURE)]),
        ]
        stream = StreamReport("s", "4.19", payloads=payloads)

        risks = _find_escalation_risks([stream], Config())
        # Newest-first: t5(fail), t4(absent) -> streak breaks at 1 -> below threshold
        assert len(risks) == 0


class TestAnalyzeEndToEnd:
    def test_escalation_risks_populated(self):
        """analyze() should populate escalation_risks on the report."""
        # 3 consecutive informing failures (threshold=3)
        payloads = [
            _make_payload("t1", [_make_informing_job("j1", result=JobResult.FAILURE)]),
            _make_payload("t2", [_make_informing_job("j1", result=JobResult.FAILURE)]),
            _make_payload("t3", [_make_informing_job("j1", result=JobResult.FAILURE)]),
        ]
        stream = StreamReport("s", "4.19", payloads=payloads)
        report = MonitorReport(generated_at="now", streams=[stream])

        analyze(report, Config())

        assert len(report.escalation_risks) == 1
        assert report.escalation_risks[0].job_name == "j1"

    def test_cross_topology_populated(self):
        """analyze() should populate cross_topology on the report."""
        sno_job = _make_job(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-single-node-live-iso",
            topology="SNO",
        )
        tna_job = _make_job(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-arbiter-live-iso",
            topology="TNA",
        )
        payloads = [_make_payload("t1", [sno_job, tna_job])]
        stream = StreamReport("s", "4.19", payloads=payloads)
        report = MonitorReport(generated_at="now", streams=[stream])

        analyze(report, Config())

        assert sno_job.name in report.cross_topology
        assert "TNA" in report.cross_topology[sno_job.name]


class TestAnalyzerErrorRecovery:
    @patch("payload_monitor.analyzer._find_escalation_risks", side_effect=RuntimeError("boom"))
    def test_escalation_risk_failure_appends_data_error(self, mock_esc):
        """Escalation risk failure should append to data_errors and set safe default."""
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[
                _make_payload("t1", [_make_job("j1")]),
            ])],
        )
        analyze(report, Config())
        assert report.escalation_risks == []
        assert any("Escalation risk" in e for e in report.data_errors)

    @patch("payload_monitor.analyzer._correlate_cross_topology", side_effect=RuntimeError("boom"))
    def test_cross_topology_failure_appends_data_error(self, mock_cross):
        """Cross-topology failure should append to data_errors and set safe default."""
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[
                _make_payload("t1", [_make_job("j1")]),
            ])],
        )
        analyze(report, Config())
        assert report.cross_topology == {}
        assert any("Cross-topology" in e for e in report.data_errors)


class TestTnfNormalization:
    def test_tnf_fencing_pattern(self):
        """TNF job with 'fencing' pattern should be normalized."""
        config = Config()
        result = _normalize_job_name(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-fencing-live-iso",
            config,
        )
        assert "__TOPO__" in result
        assert "fencing" not in result

    def test_case_insensitive_normalization(self):
        """Normalization should be case-insensitive."""
        config = Config()
        result = _normalize_job_name(
            "periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-SNO-live-iso",
            config,
        )
        assert "__TOPO__" in result
