"""Tests for payload_monitor.collectors.timing."""

import json
import os
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

        jobs = timing.fetch_edge_jobs("4.22", config)
        assert len(jobs) == 3
        topos = {j["_topology"] for j in jobs}
        assert topos == {"SNO", "TNA", "TNF"}

    @patch.object(timing, "_session")
    def test_http_error_returns_empty(self, mock_session, config):
        mock_session.get.side_effect = requests.RequestException("timeout")
        assert timing.fetch_edge_jobs("4.22", config) == []

    @patch.object(timing, "_session")
    def test_non_list_response(self, mock_session, config):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "bad"}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp
        assert timing.fetch_edge_jobs("4.22", config) == []


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


class TestBuildTimingRun:
    """Covers the previously-untested run-building step inside collect().

    Regression coverage for the real Sippy /api/jobs/runs shape, e.g.:
    {"prow_id": "123", "timestamp": "2026-08-30T22:36:19Z", "overall_result": "S"}
    """

    def test_builds_run_from_real_sippy_shapes(self):
        run_data = {
            "prow_id": "2094192518542397440",
            "timestamp": "2026-08-30T22:36:19Z",
            "overall_result": "S",
        }
        summary = {"durationSeconds": 3474, "overallResult": "S"}

        run = timing._build_timing_run(
            "test-job", run_data, "4.20", "TNA", "install",
            {"network": "ipv4"}, summary, {"install": 120.0},
        )

        assert run.start_time == "2026-08-30T22:36:19Z"
        assert run.duration_seconds == 3474
        assert run.result == "S"
        assert run.step_durations == {"install": 120.0}

    def test_missing_timestamp_yields_empty_start_time(self):
        run_data = {"prow_id": "1", "overall_result": "F"}
        run = timing._build_timing_run(
            "test-job", run_data, "4.20", "SNO", "install", {}, None, {},
        )
        assert run.start_time == ""
        assert run.duration_seconds == 0

    def test_missing_summary_defaults_duration_to_zero(self):
        run_data = {"prow_id": "1", "timestamp": "2026-08-30T22:36:19Z"}
        run = timing._build_timing_run(
            "test-job", run_data, "4.20", "SNO", "install", {}, None, {},
        )
        assert run.duration_seconds == 0


# ---------------------------------------------------------------------------
# Retention window helper
# ---------------------------------------------------------------------------

class TestParseSippyTimestamp:
    """Sippy's /api/jobs/runs returns ISO-8601 strings for `timestamp`
    (e.g. "2026-08-30T22:36:19Z"), not epoch milliseconds."""

    def test_iso_string_with_z_suffix(self):
        dt = timing._parse_sippy_timestamp("2026-08-30T22:36:19Z")
        assert dt.year == 2026 and dt.month == 8 and dt.day == 30
        assert dt.tzinfo is not None

    def test_epoch_ms_still_supported(self):
        import time
        now_ms = int(time.time() * 1000)
        dt = timing._parse_sippy_timestamp(now_ms)
        assert abs(dt.timestamp() * 1000 - now_ms) < 1000

    def test_none_returns_none(self):
        assert timing._parse_sippy_timestamp(None) is None

    def test_empty_string_returns_none(self):
        assert timing._parse_sippy_timestamp("") is None

    def test_zero_returns_none(self):
        assert timing._parse_sippy_timestamp(0) is None

    def test_bool_returns_none(self):
        assert timing._parse_sippy_timestamp(True) is None

    def test_malformed_string_returns_none(self):
        assert timing._parse_sippy_timestamp("not-a-timestamp") is None

    def test_unsupported_type_returns_none(self):
        assert timing._parse_sippy_timestamp(["not", "a", "timestamp"]) is None
        assert timing._parse_sippy_timestamp({"ts": 1}) is None

    def test_does_not_raise_typeerror_on_string_division(self):
        # Regression test: previously `_within_retention_window` fed the raw
        # value straight into `value / 1000`, crashing with
        # "unsupported operand type(s) for /: 'str' and 'int'" on every
        # real Sippy response.
        timing._parse_sippy_timestamp("2026-08-30T22:36:19Z")

    def test_offset_aware_string_is_normalized_to_utc(self):
        # A non-UTC offset must be converted, not kept as-is, so downstream
        # comparisons/formatting treat it as the same instant in UTC.
        from datetime import timezone

        dt = timing._parse_sippy_timestamp("2026-08-30T22:36:19+02:00")
        assert dt.tzinfo == timezone.utc
        assert dt.hour == 20 and dt.day == 30

    def test_offset_less_string_is_treated_as_utc(self):
        # Regression test: an offset-naive datetime compared against the
        # tz-aware cutoff in `_within_retention_window` raises
        # "TypeError: can't compare offset-naive and offset-aware datetimes".
        from datetime import timezone

        dt = timing._parse_sippy_timestamp("2026-08-30T22:36:19")
        assert dt.tzinfo == timezone.utc

    def test_offset_less_string_does_not_raise_in_retention_window(self):
        assert timing._within_retention_window("2026-08-30T22:36:19", days=7) in (
            True,
            False,
        )


class TestWithinRetentionWindow:
    def test_recent_iso_timestamp_is_within(self):
        from datetime import datetime, timedelta, timezone
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        assert timing._within_retention_window(recent, days=7) is True

    def test_old_iso_timestamp_is_outside(self):
        from datetime import datetime, timedelta, timezone
        old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        assert timing._within_retention_window(old, days=7) is False

    def test_recent_timestamp_is_within(self):
        # 1 hour ago in milliseconds (epoch-ms still supported for robustness)
        import time
        ts_ms = int((time.time() - 3600) * 1000)
        assert timing._within_retention_window(ts_ms, days=7) is True

    def test_old_timestamp_is_outside(self):
        # 30 days ago in milliseconds
        import time
        ts_ms = int((time.time() - 30 * 86400) * 1000)
        assert timing._within_retention_window(ts_ms, days=7) is False

    def test_zero_timestamp_returns_true(self):
        assert timing._within_retention_window(0, days=7) is True

    def test_none_timestamp_returns_true(self):
        assert timing._within_retention_window(None, days=7) is True

    def test_boundary_exactly_at_cutoff(self):
        import time
        ts_ms = int((time.time() - 7 * 86400) * 1000)
        # At the boundary (within a second of the cutoff) — may be just inside or
        # just outside depending on execution speed, so just verify it doesn't crash.
        result = timing._within_retention_window(ts_ms, days=7)
        assert isinstance(result, bool)

    def test_unparseable_timestamp_returns_true(self):
        for bad_value in ("not-a-timestamp", ["not", "a", "number"], {"ts": 1}):
            assert timing._within_retention_window(bad_value, days=7) is True

    def test_bool_timestamp_returns_true(self):
        assert timing._within_retention_window(True, days=7) is True


VALID_RUN = {
    "job_name": "j1", "topology": "TNA", "release": "4.22",
    "start_time": "2026-07-15T06:00:00Z", "result": "S", "run_type": "install",
    "duration_seconds": 3600, "variant": {"network": "ipv4"},
    "step_durations": {"install": 120.0},
}


class TestIsValidCachePayload:
    def test_valid_payload_passes(self):
        assert timing._is_valid_cache_payload({"runs": {"111": VALID_RUN}}) is True

    def test_non_dict_payload_rejected(self):
        assert timing._is_valid_cache_payload(["not", "a", "dict"]) is False

    def test_non_dict_runs_rejected(self):
        assert timing._is_valid_cache_payload({"runs": "not_a_dict"}) is False

    def test_missing_required_field_rejected(self):
        bad_run = {k: v for k, v in VALID_RUN.items() if k != "job_name"}
        assert timing._is_valid_cache_payload({"runs": {"111": bad_run}}) is False

    def test_non_numeric_duration_rejected(self):
        bad_run = {**VALID_RUN, "duration_seconds": "not_a_number"}
        assert timing._is_valid_cache_payload({"runs": {"111": bad_run}}) is False

    def test_bool_duration_rejected(self):
        bad_run = {**VALID_RUN, "duration_seconds": True}
        assert timing._is_valid_cache_payload({"runs": {"111": bad_run}}) is False

    def test_non_numeric_step_duration_value_rejected(self):
        bad_run = {**VALID_RUN, "step_durations": {"install": "not_a_number"}}
        assert timing._is_valid_cache_payload({"runs": {"111": bad_run}}) is False

    def test_bool_step_duration_value_rejected(self):
        bad_run = {**VALID_RUN, "step_durations": {"install": True}}
        assert timing._is_valid_cache_payload({"runs": {"111": bad_run}}) is False

    def test_non_string_variant_value_rejected(self):
        bad_run = {**VALID_RUN, "variant": {"network": 123}}
        assert timing._is_valid_cache_payload({"runs": {"111": bad_run}}) is False


# ---------------------------------------------------------------------------
# GCS build listing (finding the previous completed build)
# ---------------------------------------------------------------------------

class TestFindPreviousBuildIds:
    @patch.object(timing, "_session")
    def test_returns_highest_build_excluding_current(self, mock_session):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "prefixes": [
                "logs/periodic-ci-test-job/198/",
                "logs/periodic-ci-test-job/199/",
                "logs/periodic-ci-test-job/200/",
            ],
        }
        mock_session.get.return_value = resp

        result = timing._find_previous_build_ids("periodic-ci-test-job", "200")

        assert result == ["199"]

    @patch.object(timing, "_session")
    def test_returns_top_n_candidates_descending(self, mock_session):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "prefixes": [
                "logs/periodic-ci-test-job/197/",
                "logs/periodic-ci-test-job/198/",
                "logs/periodic-ci-test-job/199/",
                "logs/periodic-ci-test-job/200/",
            ],
        }
        mock_session.get.return_value = resp

        result = timing._find_previous_build_ids("periodic-ci-test-job", "200", limit=3)

        assert result == ["199", "198", "197"]

    @patch.object(timing, "_session")
    def test_returns_empty_when_no_prefixes(self, mock_session):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"prefixes": []}
        mock_session.get.return_value = resp

        assert timing._find_previous_build_ids("periodic-ci-test-job", "200") == []

    @patch.object(timing, "_session")
    def test_returns_empty_when_only_current_build_listed(self, mock_session):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "prefixes": ["logs/periodic-ci-test-job/200/"],
        }
        mock_session.get.return_value = resp

        assert timing._find_previous_build_ids("periodic-ci-test-job", "200") == []

    @patch.object(timing, "_session")
    def test_ignores_non_numeric_prefixes(self, mock_session):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "prefixes": [
                "logs/periodic-ci-test-job/latest-build.txt/",
                "logs/periodic-ci-test-job/199/",
            ],
        }
        mock_session.get.return_value = resp

        assert timing._find_previous_build_ids("periodic-ci-test-job", "200") == ["199"]

    @patch.object(timing, "_session")
    def test_returns_empty_on_request_exception(self, mock_session):
        mock_session.get.side_effect = requests.RequestException("network error")

        assert timing._find_previous_build_ids("periodic-ci-test-job", "200") == []

    @patch.object(timing, "_session")
    def test_returns_empty_when_prefixes_missing(self, mock_session):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}
        mock_session.get.return_value = resp

        assert timing._find_previous_build_ids("periodic-ci-test-job", "200") == []

    @patch.object(timing, "_session")
    def test_follows_pagination_to_find_highest_build(self, mock_session):
        # GCS returns prefixes in ascending order, so the highest (most
        # recent) build ID lands on the last page, not the first.
        page1 = MagicMock()
        page1.raise_for_status = MagicMock()
        page1.json.return_value = {
            "prefixes": ["logs/periodic-ci-test-job/198/"],
            "nextPageToken": "token-2",
        }
        page2 = MagicMock()
        page2.raise_for_status = MagicMock()
        page2.json.return_value = {
            "prefixes": ["logs/periodic-ci-test-job/199/", "logs/periodic-ci-test-job/200/"],
        }
        mock_session.get.side_effect = [page1, page2]

        result = timing._find_previous_build_ids("periodic-ci-test-job", "200")

        assert result == ["199"]
        assert mock_session.get.call_count == 2
        assert mock_session.get.call_args_list[1].kwargs["params"]["pageToken"] == "token-2"


# ---------------------------------------------------------------------------
# GCS cache seeding
# ---------------------------------------------------------------------------

class TestSeedCacheFromPreviousRun:
    def test_skips_when_cache_exists(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(b"{}")
            cache_path = Path(f.name)
        try:
            timing.seed_cache_from_previous_run(cache_path)
            # Should not have made any HTTP calls
        finally:
            cache_path.unlink(missing_ok=True)

    @patch.dict(os.environ, {}, clear=True)
    def test_skips_when_job_name_unset(self, tmp_path):
        cache_path = tmp_path / "nonexistent_test_cache.json"
        timing.seed_cache_from_previous_run(cache_path)
        assert not cache_path.exists()

    @patch.object(timing, "_find_previous_build_ids")
    @patch.object(timing, "_session")
    @patch.dict(os.environ, {"JOB_NAME": "periodic-ci-test-job", "BUILD_ID": "200"})
    def test_success_writes_cache(self, mock_session, mock_find_build):
        mock_find_build.return_value = ["199"]

        cache_data = json.dumps({
            "last_updated": "2026-07-15T07:00:00Z",
            "runs": {"111": {
                "job_name": "j1", "topology": "TNA", "release": "4.22",
                "start_time": "2026-07-15T06:00:00Z", "duration_seconds": 3600,
                "result": "S", "run_type": "install", "variant": {},
                "step_durations": {},
            }},
            "phase_durations": {},
        })

        cache_resp = MagicMock()
        cache_resp.text = cache_data
        cache_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = cache_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "timing_cache.json"
            timing.seed_cache_from_previous_run(cache_path)

            assert cache_path.exists()
            loaded = json.loads(cache_path.read_text())
            assert "111" in loaded["runs"]

        mock_find_build.assert_called_once_with("periodic-ci-test-job", "200", limit=3)

    @patch.object(timing, "_find_previous_build_ids")
    @patch.dict(os.environ, {"JOB_NAME": "periodic-ci-test-job", "BUILD_ID": "200"})
    def test_no_previous_build_found_no_crash(self, mock_find_build, tmp_path):
        mock_find_build.return_value = []

        cache_path = tmp_path / "nonexistent_seed_test.json"
        timing.seed_cache_from_previous_run(cache_path)
        assert not cache_path.exists()

    @patch.object(timing, "_find_previous_build_ids")
    @patch.object(timing, "_session")
    @patch.dict(os.environ, {"JOB_NAME": "periodic-ci-test-job", "BUILD_ID": "200"})
    def test_cache_artifact_404_no_crash(self, mock_session, mock_find_build, tmp_path):
        mock_find_build.return_value = ["199"]

        cache_resp = MagicMock()
        cache_resp.ok = False
        cache_resp.status_code = 404
        mock_session.get.return_value = cache_resp

        cache_path = tmp_path / "nonexistent_seed_test.json"
        timing.seed_cache_from_previous_run(cache_path)
        assert not cache_path.exists()

    @patch.object(timing, "_find_previous_build_ids")
    @patch.object(timing, "_session")
    @patch.dict(os.environ, {"JOB_NAME": "periodic-ci-test-job", "BUILD_ID": "200"})
    def test_falls_back_to_older_build_on_404(self, mock_session, mock_find_build, tmp_path):
        """The most recent candidate 404s (e.g. an in-progress rerun) —
        the next-older candidate's cache should be used instead."""
        mock_find_build.return_value = ["200_inprogress", "199"]

        missing_resp = MagicMock()
        missing_resp.ok = False
        missing_resp.status_code = 404

        cache_data = json.dumps({"runs": {"111": VALID_RUN}})
        found_resp = MagicMock()
        found_resp.ok = True
        found_resp.text = cache_data

        mock_session.get.side_effect = [missing_resp, found_resp]

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "timing_cache.json"
            timing.seed_cache_from_previous_run(cache_path)

            assert cache_path.exists()
            loaded = json.loads(cache_path.read_text())
            assert "111" in loaded["runs"]

        assert mock_session.get.call_count == 2

    @patch.object(timing, "_find_previous_build_ids")
    @patch.object(timing, "_session")
    @patch.dict(os.environ, {"JOB_NAME": "periodic-ci-test-job", "BUILD_ID": "200"})
    def test_invalid_json_from_artifact_no_crash(self, mock_session, mock_find_build, tmp_path):
        mock_find_build.return_value = ["199"]

        cache_resp = MagicMock()
        cache_resp.text = "not valid json {"
        cache_resp.ok = True
        mock_session.get.return_value = cache_resp

        cache_path = tmp_path / "nonexistent_seed_test.json"
        timing.seed_cache_from_previous_run(cache_path)
        assert not cache_path.exists()

    @patch.object(timing, "_find_previous_build_ids")
    @patch.object(timing, "_session")
    @patch.dict(os.environ, {"JOB_NAME": "periodic-ci-test-job", "BUILD_ID": "200"})
    def test_structurally_invalid_json_no_crash(self, mock_session, mock_find_build, tmp_path):
        mock_find_build.return_value = ["199"]

        # Valid JSON, but not the shape load_cache() expects (runs entries
        # missing required string fields).
        cache_data = json.dumps({"runs": {"111": {"job_name": "j1"}}})

        cache_resp = MagicMock()
        cache_resp.text = cache_data
        cache_resp.ok = True
        mock_session.get.return_value = cache_resp

        cache_path = tmp_path / "nonexistent_seed_test.json"
        timing.seed_cache_from_previous_run(cache_path)
        assert not cache_path.exists()

    @patch.object(timing, "_find_previous_build_ids")
    @patch.object(timing, "_session")
    @patch.dict(os.environ, {"JOB_NAME": "periodic-ci-test-job", "BUILD_ID": "200"})
    def test_write_failure_no_crash(self, mock_session, mock_find_build, tmp_path):
        mock_find_build.return_value = ["199"]

        cache_data = json.dumps({"runs": {"111": VALID_RUN}})

        cache_resp = MagicMock()
        cache_resp.text = cache_data
        cache_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = cache_resp

        cache_path = tmp_path / "nonexistent_seed_test.json"
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            timing.seed_cache_from_previous_run(cache_path)
        assert not cache_path.exists()
