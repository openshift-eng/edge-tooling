"""Job-health selection, relative thresholds, and the Slack data contract."""

import json
from unittest.mock import patch

import pytest

from payload_monitor.__main__ import _write_sippy_summary
from payload_monitor.collectors import sippy
from payload_monitor.models import (
    JobResult, JobRun, JobType, MonitorReport, Payload, PayloadStatus,
    Regression, StreamReport,
)
from payload_monitor.report.generator import _build_template_context, generate_html


def finding(name, previous, current, runs=3):
    return Regression(name, name, "", "", previous, current,
                      topology="SNO", current_runs=runs,
                      triage_url=f"https://sippy.example/{name}")


@pytest.mark.parametrize("previous,current,change,severe,low", [
    (80, 30, -62.5, True, True),  # Relative, not percentage-point change.
    (100, 50, -50, False, False),  # Both boundaries are strict.
    (100, 49.9, -50.1, True, True),
    (40, 40, 0, False, True),
    (20, 40, 100, False, True),  # Improving but still unhealthy.
    (0, 0, None, False, True),
    (0, 30, None, False, True),  # No division by zero or invented drop.
    (80, 60, -25, False, False),
])
def test_thresholds(previous, current, change, severe, low):
    r = finding("job", previous, current)
    if change is None:
        assert r.relative_change is None
    else:
        assert r.relative_change == pytest.approx(change)
    assert r.severe_drop is severe
    assert r.low_pass_rate is low
    assert r.needs_attention is (severe or low)


@pytest.mark.parametrize("previous,current,net", [(40, 40, 0), (20, 40, 20), (0, 0, 0)])
def test_collector_keeps_low_rates_regardless_of_change(previous, current, net):
    jobs = [{"name": "sno-job", "previous_pass_percentage": previous,
             "current_pass_percentage": current, "net_improvement": net,
             "current_runs": 3}]
    assert len(sippy.identify_regressions(jobs)) == 1
    jobs[0]["current_runs"] = 2
    assert sippy.identify_regressions(jobs) == []


def test_collection_distinguishes_failed_version_from_no_findings(config):
    with patch.object(sippy, "fetch_edge_jobs", side_effect=lambda v, *a, **kw: [] if v == "4.19" else None):
        assert sippy.collect(config, ["4.19", "4.20"]) == {"4.19": []}


def test_worst_five_distinct_job_release_pairs():
    jobs = [finding(f"job-{n}", 80, n * 5) for n in range(7)]
    jobs += [finding("minor", 100, 80), finding("too-few", 100, 0, runs=2)]
    jobs += [finding("improving", 0, 5)]
    # Duplicate stream observations must not use up summary slots.
    report = MonitorReport(streams=[StreamReport("s", "4.19", regressions=jobs),
                                    StreamReport("duplicate", "4.19", regressions=jobs)])
    context = _build_template_context(report)
    assert context["priority_sippy_count"] == 8
    assert [r.test_name for r in context["priority_sippy"]] == [
        "job-0", "job-1", "improving", "job-2", "job-3",
    ]
    summary = generate_html(report).split('<!-- Findings Summary -->')[1].split('<!-- Failure Analysis -->')[0]
    assert summary.count('<li data-analysis-id=') == 5
    assert "Worst 5 of 8" in summary
    assert "job-4" not in summary
    assert "Selection:" in summary
    assert "even if flat or improving" in summary
    assert "lowest current rate first" in summary
    assert 'class="badge sippy-drop"' not in summary
    assert 'class="badge sippy-low"' not in summary
    assert summary.count('relative pass-rate drop &gt;50%') == 1
    assert summary.count('current pass rate &lt;50%') == 1


def test_low_rate_alone_is_not_healthy_and_zero_baseline_is_explicit():
    report = MonitorReport(streams=[StreamReport("s", "4.19", regressions=[finding("improving", 0, 40)])])
    html = generate_html(report)
    assert "fs-healthy-banner\">" not in html
    assert 'Current pass rate <strong>40.0%</strong>' in html
    assert "current pass rate &lt;50%" in html
    assert 'Previous pass rate is zero' in html


def test_blocking_overlap_is_a_cross_reference_not_an_extra_blocker():
    job = JobRun("same-job", "https://prow/1", JobResult.FAILURE, JobType.BLOCKING, "SNO")
    informing = JobRun("informing", "https://prow/2", JobResult.FAILURE, JobType.INFORMING, "SNO")
    retried = JobRun("retried", "https://prow/3", JobResult.SUCCESS, JobType.BLOCKING, "SNO", retries=1)
    payload = Payload("tag", "s", "4.19", PayloadStatus.REJECTED, jobs=[job, informing, retried])
    report = MonitorReport(streams=[StreamReport("s", "4.19", payloads=[payload],
                                                regressions=[finding("same-job", 80, 30)])])
    context = _build_template_context(report)
    assert context["blocking_failures"] == 1
    html = generate_html(report)
    assert "Also in blocking failures above" in html
    assert html.index("1 Blocking Failure") < html.index("Worst 1 of 1")
    assert html.index("Worst 1 of 1") < html.index("Succeeded on Retry") < html.index("1 Informing Job Failure")
    summary = html.split('<!-- Findings Summary -->')[1].split('<!-- Failure Analysis -->')[0]
    assert summary.count('class="fs-section fs-section-critical"') == 1
    assert 'class="fs-critical-group" id="findings-blocking"' in summary
    assert 'class="fs-critical-group" id="findings-job-health"' in summary
    assert 'Sippy Job regression' in summary
    assert '<ul class="fs-sippy-list" role="list">' in summary
    assert '<ol' not in summary
    assert 'class="fs-section fs-section-info"' not in html
    assert '<span class="fs-sippy-current">Current pass rate <strong>30.0%</strong></span>' in summary
    assert '<strong>Previous ' not in summary
    assert "-62.5%" in html
    assert "these findings do not determine payload blocking status" in html


def test_slack_counts_all_severe_drops_deduplicated_and_keeps_unavailable(tmp_path):
    jobs = [finding(f"job-{n}", 80, 30) for n in range(7)]
    jobs += [jobs[0], finding("boundary", 100, 50), finding("flat", 40, 40),
             finding("improving", 20, 40), finding("few-runs", 100, 0, 2)]
    html_path = tmp_path / "report.html"
    _write_sippy_summary(html_path, ["5.0", "4.20", "4.19"], {"4.19": jobs, "4.20": []})
    assert json.loads(html_path.with_suffix(".sippy-summary.json").read_text()) == {
        "4.19": 7, "4.20": 0, "5.0": None,
    }


def test_same_job_in_two_releases_is_two_findings():
    report = MonitorReport(streams=[
        StreamReport("s1", "4.19", regressions=[finding("same-job", 80, 30)]),
        StreamReport("s2", "4.20", regressions=[finding("same-job", 80, 30)]),
    ])
    context = _build_template_context(report)
    assert context["priority_sippy_count"] == 2
    assert {r.version for r in context["priority_sippy"]} == {"4.19", "4.20"}


def test_skipped_sippy_has_unavailable_counts(tmp_path):
    html_path = tmp_path / "report.html"
    _write_sippy_summary(html_path, ["4.19", "4.20"], {})
    assert json.loads(html_path.with_suffix(".sippy-summary.json").read_text()) == {
        "4.19": None, "4.20": None,
    }
