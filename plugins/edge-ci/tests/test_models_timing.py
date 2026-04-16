"""Tests for TimingRun and TimingReport models."""

import pytest

from payload_monitor.models import TimingRun, TimingReport, MonitorReport


class TestTimingRun:
    def test_is_success(self):
        run = TimingRun(
            job_name="test-job", topology="TNA", release="4.22",
            start_time="2026-04-01T12:00:00Z", duration_seconds=3600,
            result="S", run_type="install",
        )
        assert run.is_success is True

    def test_is_not_success(self):
        run = TimingRun(
            job_name="test-job", topology="TNA", release="4.22",
            start_time="2026-04-01T12:00:00Z", duration_seconds=3600,
            result="F", run_type="install",
        )
        assert run.is_success is False

    def test_duration_minutes(self):
        run = TimingRun(
            job_name="test-job", topology="TNA", release="4.22",
            start_time="2026-04-01T12:00:00Z", duration_seconds=5400,
            result="S", run_type="install",
        )
        assert run.duration_minutes == 90.0

    def test_default_variant(self):
        run = TimingRun(
            job_name="test-job", topology="TNA", release="4.22",
            start_time="2026-04-01T12:00:00Z", duration_seconds=3600,
            result="S", run_type="install",
        )
        assert run.variant == {}

    def test_install_duration_from_setup(self):
        run = TimingRun(
            job_name="test-job", topology="TNA", release="4.22",
            start_time="", duration_seconds=12000,
            result="S", run_type="install",
            step_durations={"install": 4752.0, "test": 7680.0},
        )
        assert run.install_duration_seconds == 4752.0

    def test_install_duration_from_setup_key(self):
        run = TimingRun(
            job_name="test-job", topology="TNA", release="4.22",
            start_time="", duration_seconds=12000,
            result="S", run_type="install",
            step_durations={"setup": 3000.0},
        )
        assert run.install_duration_seconds == 3000.0

    def test_install_duration_missing(self):
        run = TimingRun(
            job_name="test-job", topology="TNA", release="4.22",
            start_time="", duration_seconds=12000,
            result="S", run_type="install",
        )
        assert run.install_duration_seconds == 0.0


class TestTimingReport:
    def test_empty_report(self):
        report = TimingReport()
        assert report.last_updated == ""
        assert report.runs == {}
        assert report.phase_durations == {}
        assert report.successful_runs == []

    def test_successful_runs_filters(self):
        report = TimingReport(
            runs={
                "1": TimingRun("j1", "TNA", "4.22", "2026-04-01T12:00:00Z", 3600, "S", "install"),
                "2": TimingRun("j2", "TNA", "4.22", "2026-04-01T13:00:00Z", 3600, "F", "install"),
                "3": TimingRun("j3", "TNF", "4.22", "2026-04-01T14:00:00Z", 3600, "S", "install"),
            }
        )
        successful = report.successful_runs
        assert len(successful) == 2
        assert all(r.is_success for r in successful)


class TestMonitorReportTimingFields:
    def test_skip_timing_default(self):
        report = MonitorReport()
        assert report.skip_timing is False
        assert report.timing_report is None

    def test_skip_timing_set(self):
        report = MonitorReport(skip_timing=True)
        assert report.skip_timing is True

    def test_timing_report_attached(self):
        tr = TimingReport(last_updated="2026-04-01T12:00:00Z")
        report = MonitorReport(timing_report=tr)
        assert report.timing_report is tr
