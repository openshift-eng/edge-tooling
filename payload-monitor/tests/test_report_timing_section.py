"""Tests for payload_monitor.report.timing_section."""

import pytest

from payload_monitor.models import TimingRun, TimingReport
from payload_monitor.report.timing_section import (
    _fmt_duration,
    _group_runs,
    _variant_key,
    render_summary_table,
    render_variant_table,
    render_phase_table,
    render_trend_svg,

    render_dow_heatmap,
    render_version_comparison,
    render_anomaly_flags,
    render_timing_section,
)


def _make_run(
    topology="TNA", run_type="install", release="4.22",
    duration=3600, result="S", day=1, variant=None,
    job_name="test-job",
):
    return TimingRun(
        job_name=job_name,
        topology=topology,
        release=release,
        start_time=f"2026-04-0{day}T12:00:00Z",
        duration_seconds=duration,
        result=result,
        run_type=run_type,
        variant=variant or {"network": "ipv4", "install_method": "metal",
                            "feature": "standard", "scenario": "standard"},
    )


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

class TestFmtDuration:
    def test_zero(self):
        assert _fmt_duration(0) == "N/A"

    def test_negative(self):
        assert _fmt_duration(-100) == "N/A"

    def test_minutes(self):
        assert _fmt_duration(2700) == "45m"

    def test_hours(self):
        assert _fmt_duration(5400) == "1h 30m"

    def test_exact_hour(self):
        assert _fmt_duration(3600) == "1h 0m"


class TestGroupRuns:
    def test_groups_by_topology_and_type(self):
        runs = [
            _make_run(topology="TNA", run_type="install"),
            _make_run(topology="TNA", run_type="upgrade"),
            _make_run(topology="TNF", run_type="install"),
        ]
        groups = _group_runs(runs)
        assert len(groups) == 3
        assert ("TNA", "install") in groups
        assert ("TNA", "upgrade") in groups
        assert ("TNF", "install") in groups


class TestVariantKey:
    def test_default_variant(self):
        run = _make_run()
        assert _variant_key(run) == "ipv4 / metal"

    def test_techpreview(self):
        run = _make_run(variant={
            "network": "ipv6", "install_method": "agent",
            "feature": "techpreview", "scenario": "standard",
        })
        assert _variant_key(run) == "ipv6 / agent / techpreview"

    def test_non_standard_scenario(self):
        run = _make_run(variant={
            "network": "ipv4", "install_method": "metal",
            "feature": "standard", "scenario": "degraded",
        })
        assert _variant_key(run) == "ipv4 / metal / degraded"


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

class TestRenderSummaryTable:
    def test_empty_report(self):
        report = TimingReport()
        html = render_summary_table(report)
        assert "No timing runs collected" in html

    def test_renders_table(self):
        report = TimingReport(runs={
            "1": _make_run(duration=2400),
            "2": _make_run(duration=3600),
        })
        html = render_summary_table(report)
        assert "timing-summary" in html
        assert "TNA" in html
        assert "Install" in html

    def test_aggregate_rows_have_no_data_tversion(self):
        report = TimingReport(runs={
            "1": _make_run(duration=2400, release="4.21"),
            "2": _make_run(duration=3600, release="4.22"),
        })
        result = render_summary_table(report)
        assert "timing-aggregate" in result
        assert 'data-tversion' not in result.split("timing-aggregate")[1].split("</tr>")[0]

    def test_version_detail_rows_hidden_by_default(self):
        report = TimingReport(runs={
            "1": _make_run(duration=2400, release="4.21"),
            "2": _make_run(duration=3600, release="4.22"),
        })
        result = render_summary_table(report)
        assert "timing-version-detail" in result
        assert 'data-tversion="4.21"' in result
        assert 'data-tversion="4.22"' in result
        for line in result.split("\n"):
            if "timing-version-detail" in line:
                assert 'display:none' in line

    def test_version_column_present(self):
        report = TimingReport(runs={
            "1": _make_run(duration=2400),
        })
        result = render_summary_table(report)
        assert "<th>Version</th>" in result

    def test_zero_successes_shows_note(self):
        report = TimingReport(runs={
            "1": _make_run(duration=2400, result="F", topology="TNF"),
            "2": _make_run(duration=3600, result="F", topology="TNF", day=2),
        })
        html = render_summary_table(report)
        assert "none successful" in html
        assert "TNF" in html
        assert "0 / 2" in html


# ---------------------------------------------------------------------------
# Variant table
# ---------------------------------------------------------------------------

class TestRenderVariantTable:
    def test_empty_report(self):
        assert render_variant_table(TimingReport()) == ""

    def test_single_variant_hidden(self):
        report = TimingReport(runs={
            "1": _make_run(duration=2400),
            "2": _make_run(duration=3600),
        })
        # Only one variant group, so no table
        assert render_variant_table(report) == ""

    def test_multiple_variants(self):
        report = TimingReport(runs={
            "1": _make_run(duration=2400, variant={
                "network": "ipv4", "install_method": "metal",
                "feature": "standard", "scenario": "standard",
            }),
            "2": _make_run(duration=3600, variant={
                "network": "ipv6", "install_method": "metal",
                "feature": "standard", "scenario": "standard",
            }),
        })
        html = render_variant_table(report)
        assert "timing-variants" in html
        assert "ipv4" in html
        assert "ipv6" in html


# ---------------------------------------------------------------------------
# Phase table
# ---------------------------------------------------------------------------

class TestRenderPhaseTable:
    def test_empty(self):
        assert render_phase_table(TimingReport()) == ""

    def test_all_zeros_shows_na(self):
        report = TimingReport(phase_durations={
            "4.22:install should succeed: cluster creation": {"2026-04-01": 0},
        })
        html = render_phase_table(report)
        assert "N/A*" in html
        assert "Sippy" in html

    def test_real_data(self):
        report = TimingReport(phase_durations={
            "4.22:install should succeed: cluster creation": {"2026-04-01": 15.5},
        })
        html = render_phase_table(report)
        assert "15.5m" in html
        assert "cluster creation" in html


# ---------------------------------------------------------------------------
# SVG charts
# ---------------------------------------------------------------------------

class TestRenderTrendSvg:
    def test_empty(self):
        assert render_trend_svg(TimingReport()) == ""

    def test_single_run_no_chart(self):
        report = TimingReport(runs={"1": _make_run()})
        assert render_trend_svg(report) == ""

    def test_renders_svg(self):
        report = TimingReport(runs={
            "1": _make_run(day=1, duration=2400),
            "2": _make_run(day=2, duration=3000),
        })
        html = render_trend_svg(report)
        assert "<svg" in html
        assert "timing-trend-chart" in html
        assert "<polyline" in html



# ---------------------------------------------------------------------------
# Day-of-week heatmap
# ---------------------------------------------------------------------------

class TestRenderDowHeatmap:
    def test_too_few_runs(self):
        report = TimingReport(runs={"1": _make_run(), "2": _make_run(day=2)})
        assert render_dow_heatmap(report) == ""

    def test_renders_days(self):
        report = TimingReport(runs={
            str(i): _make_run(day=min(i, 7), duration=2400 + i * 100)
            for i in range(1, 6)
        })
        html = render_dow_heatmap(report)
        assert "Day-of-Week Heatmap" in html
        assert "Mon" in html or "Tue" in html  # At least one day name


# ---------------------------------------------------------------------------
# Version-over-version
# ---------------------------------------------------------------------------

class TestRenderVersionComparison:
    def test_single_version_hidden(self):
        report = TimingReport(runs={
            "1": _make_run(release="4.22"),
            "2": _make_run(release="4.22", day=2),
        })
        assert render_version_comparison(report) == ""

    def test_multiple_versions(self):
        report = TimingReport(runs={
            "1": _make_run(release="4.21", duration=2400),
            "2": _make_run(release="4.22", duration=3000),
        })
        html = render_version_comparison(report)
        assert "Version-over-Version" in html
        assert "4.21" in html
        assert "4.22" in html


# ---------------------------------------------------------------------------
# Anomaly flags
# ---------------------------------------------------------------------------

class TestRenderAnomalyFlags:
    def test_too_few_runs(self):
        report = TimingReport(runs={
            "1": _make_run(duration=3600),
            "2": _make_run(duration=3700, day=2),
        })
        assert render_anomaly_flags(report) == ""

    def test_detects_anomaly(self):
        # 9 normal runs + 1 outlier
        runs = {}
        for i in range(1, 10):
            runs[str(i)] = _make_run(duration=3600, day=min(i, 7))
        runs["10"] = _make_run(duration=10000, day=7, job_name="slow-job")
        report = TimingReport(runs=runs)
        html = render_anomaly_flags(report)
        assert "Anomaly Flags" in html
        assert "slow-job" in html

    def test_no_anomalies(self):
        # All identical durations
        runs = {
            str(i): _make_run(duration=3600, day=min(i, 7))
            for i in range(1, 8)
        }
        report = TimingReport(runs=runs)
        html = render_anomaly_flags(report)
        assert html == ""


# ---------------------------------------------------------------------------
# Full section assembly
# ---------------------------------------------------------------------------

class TestRenderTimingSection:
    def test_empty_report(self):
        assert render_timing_section(TimingReport()) == ""

    def test_none_report(self):
        assert render_timing_section(None) == ""

    def test_renders_full_section(self):
        runs = {}
        for i in range(1, 8):
            runs[str(i)] = _make_run(day=min(i, 7), duration=2400 + i * 100)
        report = TimingReport(
            last_updated="2026-04-02T14:00:00Z",
            runs=runs,
        )
        html = render_timing_section(report)
        assert "timing-section" in html
        assert "Install &amp; Upgrade Timing" in html
        assert "timing-summary" in html
