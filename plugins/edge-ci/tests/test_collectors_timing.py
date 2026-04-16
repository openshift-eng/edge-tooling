"""Tests for payload_monitor.collectors.timing."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from payload_monitor.collectors import timing
from payload_monitor.config import Config
from payload_monitor.models import TimingRun, TimingReport


# ---------------------------------------------------------------------------
# Variant extraction & classification
# ---------------------------------------------------------------------------

class TestExtractVariant:
    def test_default_variant(self):
        v = timing.extract_variant("periodic-ci-e2e-metal-ovn-two-node-arbiter")
        assert v == {
            "network": "ipv4",
            "feature": "standard",
            "install_method": "metal",
            "scenario": "standard",
        }

    def test_ipv6(self):
        v = timing.extract_variant("e2e-metal-ovn-two-node-arbiter-ipv6")
        assert v["network"] == "ipv6"

    def test_dualstack(self):
        v = timing.extract_variant("e2e-metal-ovn-two-node-fencing-dualstack")
        assert v["network"] == "dualstack"

    def test_techpreview(self):
        v = timing.extract_variant("e2e-metal-ovn-two-node-arbiter-techpreview")
        assert v["feature"] == "techpreview"

    def test_agent_install(self):
        v = timing.extract_variant("e2e-agent-ovn-two-node-arbiter")
        assert v["install_method"] == "agent"

    def test_assisted_install(self):
        v = timing.extract_variant("e2e-assisted-ovn-two-node-arbiter")
        assert v["install_method"] == "assisted"

    def test_degraded_scenario(self):
        v = timing.extract_variant("e2e-metal-ovn-two-node-fencing-degraded")
        assert v["scenario"] == "degraded"

    def test_recovery_scenario(self):
        v = timing.extract_variant("e2e-metal-ovn-two-node-arbiter-recovery")
        assert v["scenario"] == "recovery"

    def test_combined(self):
        v = timing.extract_variant(
            "e2e-metal-ovn-two-node-fencing-dualstack-techpreview-degraded"
        )
        assert v == {
            "network": "dualstack",
            "feature": "techpreview",
            "install_method": "metal",
            "scenario": "degraded",
        }


class TestClassifyJobType:
    def test_install(self):
        assert timing.classify_job_type("e2e-metal-ovn-two-node-arbiter") == "install"

    def test_upgrade(self):
        assert timing.classify_job_type("e2e-metal-ovn-two-node-arbiter-upgrade") == "upgrade"

    def test_upgrade_case_insensitive(self):
        assert timing.classify_job_type("e2e-metal-Upgrade-test") == "upgrade"


# ---------------------------------------------------------------------------
# JSON cache
# ---------------------------------------------------------------------------

class TestCache:
    def test_load_missing_file(self):
        report = timing.load_cache(Path("/nonexistent/path.json"))
        assert report.runs == {}
        assert report.last_updated == ""

    def test_save_and_load_roundtrip(self):
        report = TimingReport(
            last_updated="2026-04-01T12:00:00Z",
            runs={
                "123": TimingRun(
                    "job1", "TNA", "4.22", "2026-04-01T12:00:00Z",
                    3600, "S", "install", {"network": "ipv4"},
                    step_durations={"install": 4752.0, "test": 7680.0},
                ),
            },
            phase_durations={"4.22:test phase": {"2026-04-01": 10.5}},
        )
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            cache_path = Path(f.name)

        try:
            timing.save_cache(report, cache_path)
            loaded = timing.load_cache(cache_path)

            assert loaded.last_updated == "2026-04-01T12:00:00Z"
            assert "123" in loaded.runs
            run = loaded.runs["123"]
            assert run.job_name == "job1"
            assert run.topology == "TNA"
            assert run.duration_seconds == 3600
            assert run.variant == {"network": "ipv4"}
            assert run.step_durations == {"install": 4752.0, "test": 7680.0}
            assert loaded.phase_durations["4.22:test phase"]["2026-04-01"] == 10.5
        finally:
            cache_path.unlink(missing_ok=True)

    def test_load_corrupt_file(self):
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", delete=False
        ) as f:
            f.write("not json")
            cache_path = Path(f.name)

        try:
            report = timing.load_cache(cache_path)
            assert report.runs == {}
        finally:
            cache_path.unlink(missing_ok=True)


class TestPruneCache:
    def test_prune_old_runs(self):
        report = TimingReport(
            runs={
                "old": TimingRun(
                    "j1", "TNA", "4.22", "2020-01-01T00:00:00Z",
                    3600, "S", "install",
                ),
                "new": TimingRun(
                    "j2", "TNA", "4.22", "2099-01-01T00:00:00Z",
                    3600, "S", "install",
                ),
            }
        )
        timing.prune_cache(report, max_age_days=7)
        assert "old" not in report.runs
        assert "new" in report.runs

    def test_prune_invalid_date(self):
        report = TimingReport(
            runs={
                "bad": TimingRun(
                    "j1", "TNA", "4.22", "not-a-date",
                    3600, "S", "install",
                ),
            }
        )
        timing.prune_cache(report, max_age_days=7)
        assert "bad" not in report.runs


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestComputeStats:
    def test_empty_list(self):
        stats = timing.compute_stats([])
        assert stats["count"] == 0
        assert stats["avg"] == 0

    def test_single_run(self):
        runs = [TimingRun("j", "T", "4.22", "", 3600, "S", "install")]
        stats = timing.compute_stats(runs)
        assert stats["count"] == 1
        assert stats["avg"] == 3600
        assert stats["median"] == 3600
        assert stats["min"] == 3600
        assert stats["max"] == 3600
        assert stats["stddev"] == 0

    def test_multiple_runs(self):
        runs = [
            TimingRun("j", "T", "4.22", "", d, "S", "install")
            for d in [1000, 2000, 3000, 4000, 5000]
        ]
        stats = timing.compute_stats(runs)
        assert stats["count"] == 5
        assert stats["avg"] == 3000
        assert stats["median"] == 3000
        assert stats["min"] == 1000
        assert stats["max"] == 5000
        assert stats["p90"] > stats["median"]
        assert stats["p95"] > stats["p90"]
        assert stats["cv"] > 0

    def test_cv_calculation(self):
        # Identical runs should have CV=0
        runs = [
            TimingRun("j", "T", "4.22", "", 3600, "S", "install")
            for _ in range(5)
        ]
        stats = timing.compute_stats(runs)
        assert stats["cv"] == 0
        assert stats["stddev"] == 0


# ---------------------------------------------------------------------------
# API fetching
# ---------------------------------------------------------------------------

class TestFetchTnaTnfJobs:
    @patch.object(timing, "_session")
    def test_filters_sno_tna_tnf(self, mock_session, config):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"name": "periodic-e2e-metal-ovn-two-node-arbiter"},
            {"name": "periodic-e2e-metal-ovn-sno"},
            {"name": "periodic-e2e-metal-ovn-two-node-fencing"},
            {"name": "periodic-e2e-metal-ovn-some-other-topology"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        jobs = timing.fetch_tna_tnf_jobs("4.22", config)
        assert len(jobs) == 3
        topos = {j["_topology"] for j in jobs}
        assert topos == {"SNO", "TNA", "TNF"}

    @patch.object(timing, "_session")
    def test_http_error_returns_empty(self, mock_session, config):
        mock_session.get.side_effect = requests.RequestException("timeout")
        assert timing.fetch_tna_tnf_jobs("4.22", config) == []

    @patch.object(timing, "_session")
    def test_non_list_response(self, mock_session, config):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "bad"}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        assert timing.fetch_tna_tnf_jobs("4.22", config) == []


class TestFetchJobRuns:
    @patch.object(timing, "_session")
    def test_success(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "rows": [
                {
                    "prow_id": 12345,
                    "overall_result": "S",
                    "timestamp": 1711972800000,
                    "duration": 3600,
                    "url": "https://prow.ci.openshift.org/view/gs/logs/job/12345",
                },
                {
                    "prow_id": 67890,
                    "overall_result": "F",
                    "timestamp": 1711886400000,
                    "duration": 1800,
                    "url": "https://prow.ci.openshift.org/view/gs/logs/job/67890",
                },
            ],
            "total_rows": 2,
        }
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        runs = timing.fetch_job_runs("test-job", "4.22")
        assert len(runs) == 2
        assert runs[0]["prow_id"] == 12345
        assert runs[1]["overall_result"] == "F"

    @patch.object(timing, "_session")
    def test_error_returns_empty(self, mock_session):
        mock_session.get.side_effect = requests.RequestException("500")
        assert timing.fetch_job_runs("test-job", "4.22") == []

    @patch.object(timing, "_session")
    def test_non_dict_response(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = "unexpected"
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        assert timing.fetch_job_runs("test-job", "4.22") == []

    @patch.object(timing, "_session")
    def test_missing_rows(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"total_rows": 0}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        assert timing.fetch_job_runs("test-job", "4.22") == []


class TestFetchRunSummary:
    @patch.object(timing, "_session")
    def test_success(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "durationSeconds": 3600,
            "startTime": "2026-04-01T12:00:00Z",
            "overallResult": "S",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        summary = timing.fetch_run_summary("12345")
        assert summary["durationSeconds"] == 3600

    @patch.object(timing, "_session")
    def test_error_returns_none(self, mock_session):
        mock_session.get.side_effect = requests.RequestException("500")
        assert timing.fetch_run_summary("12345") is None


class TestFetchPhaseDurations:
    @patch.object(timing, "_session")
    def test_success(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"2026-04-01": 10.5, "2026-04-02": 11.0}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        result = timing.fetch_phase_durations("4.22", "install should succeed: overall")
        assert result["2026-04-01"] == 10.5

    @patch.object(timing, "_session")
    def test_error_returns_empty(self, mock_session):
        mock_session.get.side_effect = requests.RequestException("timeout")
        assert timing.fetch_phase_durations("4.22", "test") == {}


# ---------------------------------------------------------------------------
# Step classification & GCS fetching
# ---------------------------------------------------------------------------

class TestClassifyStep:
    def test_devscripts_setup(self):
        name = "Run multi-stage test foo - foo-baremetalds-devscripts-setup container test"
        assert timing._classify_step(name) == "install"

    def test_ipi_install(self):
        name = "Run multi-stage test foo - foo-ipi-install-install container test"
        assert timing._classify_step(name) == "install"

    def test_e2e_test(self):
        name = "Run multi-stage test foo - foo-baremetalds-e2e-test container test"
        assert timing._classify_step(name) == "test"

    def test_pre_phase(self):
        assert timing._classify_step("Run multi-stage test pre phase") == "pre phase"

    def test_test_phase(self):
        assert timing._classify_step("Run multi-stage test test phase") == "test phase"

    def test_post_phase(self):
        assert timing._classify_step("Run multi-stage test post phase") == "post phase"

    def test_unrecognized(self):
        assert timing._classify_step("Run multi-stage test foo - foo-ofcir-acquire container test") is None

    def test_import_payload(self):
        assert timing._classify_step("Import the release payload \"latest\"") is None


class TestFetchStepDurations:
    @patch.object(timing, "_session")
    def test_parses_junit_xml(self, mock_session):
        xml_content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <testsuites>
            <testcase name="Run multi-stage test foo - foo-baremetalds-devscripts-setup container test" time="4752"/>
            <testcase name="Run multi-stage test foo - foo-baremetalds-e2e-test container test" time="7680"/>
            <testcase name="Run multi-stage test pre phase" time="4890"/>
            <testcase name="Run multi-stage test test phase" time="7700"/>
            <testcase name="Run multi-stage test post phase" time="600"/>
            <testcase name="Run multi-stage test foo - foo-ofcir-acquire container test" time="30"/>
        </testsuites>"""
        mock_resp = MagicMock()
        mock_resp.content = xml_content
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        steps = timing.fetch_step_durations("test-job", "12345")
        assert steps["install"] == 4752.0
        assert steps["test"] == 7680.0
        assert steps["pre phase"] == 4890.0
        assert steps["test phase"] == 7700.0
        assert steps["post phase"] == 600.0
        assert "ofcir-acquire" not in steps

    @patch.object(timing, "_session")
    def test_http_error_returns_empty(self, mock_session):
        mock_session.get.side_effect = requests.RequestException("404")
        assert timing.fetch_step_durations("test-job", "12345") == {}

    @patch.object(timing, "_session")
    def test_invalid_xml_returns_empty(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.content = b"not xml"
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        assert timing.fetch_step_durations("test-job", "12345") == {}

    @patch.object(timing, "_session")
    def test_zero_duration_skipped(self, mock_session):
        xml_content = b"""<testsuites>
            <testcase name="Run multi-stage test foo - foo-baremetalds-devscripts-setup container test" time="0"/>
            <testcase name="Run multi-stage test pre phase" time="100"/>
        </testsuites>"""
        mock_resp = MagicMock()
        mock_resp.content = xml_content
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        steps = timing.fetch_step_durations("test-job", "12345")
        assert "install" not in steps
        assert steps["pre phase"] == 100.0
