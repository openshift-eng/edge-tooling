"""Offline tests for selected Sippy AI targets and enrichment."""

import json
import html as html_lib
import re

from payload_monitor.models import (
    JobResult, JobRun, JobType, MonitorReport, Payload, PayloadStatus,
    PreviousAttempt, Regression, StreamReport,
)
from payload_monitor.report.generator import (
    _build_template_context, generate_html, generate_json, load_json,
    merge_analysis, patch_analysis_html,
)


def report_with_findings(count=7, name_prefix="job"):
    return MonitorReport(streams=[StreamReport("s", "4.19", regressions=[
        Regression(f"{name_prefix}-{n}", str(n), "", "", 80, n * 5,
                   topology="SNO", current_runs=3, triage_url="https://sippy.example/job")
        for n in range(count)
    ])])


def write_analysis(tmp_path, **data):
    path = tmp_path / "analysis.json"
    path.write_text(json.dumps(data))
    return path


def analysis(root="Known test failure", **overrides):
    return {"root_cause": root, "failure_type": "Test regression",
            "impact": "Historical instability", "recommendation": "Investigate failing test",
            "suspect_prs": [], **overrides}


def sippy_rows(html):
    return {
        html_lib.unescape(m.group(2)): (m.group(1), m.group(0))
        for m in re.finditer(
            r'<(div|details) id="sippy-row-\d+"[^>]*data-sippy-key="([^"]*)"[^>]*>'
            r'.*?</(?:div|details)><!-- /sippy-job -->', html, re.DOTALL,
        )
    }


def test_manifest_matches_exactly_the_five_visible_jobs_without_blockers(tmp_path):
    report = report_with_findings()
    path = tmp_path / "actual-report-123.html"
    html = generate_html(report, path)
    targets = json.loads(path.with_suffix(".ai-targets.json").read_text())["sippy_jobs"]
    assert [t["analysis_key"] for t in targets] == [
        r.analysis_key for r in _build_template_context(report)["priority_sippy"]
    ]
    assert len(targets) == 5
    assert html.count('class="sippy-analysis"') == 5
    assert 'data-enrichment-status="data-only"' in html
    assert all(t["known_failing_runs"] == [] for t in targets)
    assert targets[0]["version"] == "4.19"
    assert targets[0]["relative_change"] == -100
    assert "AI Analysis - Selected Sippy Jobs" not in html
    assert len(sippy_rows(html)) == 7
    assert all(tag == "div" for tag, _ in sippy_rows(html).values())


def test_blocking_analysis_does_not_make_unanalyzed_sippy_rows_expandable(tmp_path):
    report = report_with_findings(1)
    report.streams[0].payloads = [Payload("tag", "s", "4.19", PayloadStatus.REJECTED, jobs=[
        JobRun("job-0", "https://prow/1", JobResult.FAILURE, JobType.BLOCKING, "SNO")])]
    path = tmp_path / "report.html"
    generate_html(report, path)
    patch_analysis_html(path, write_analysis(tmp_path,
        by_prow_url={"https://prow/1": analysis()},
    ))
    assert sippy_rows(path.read_text())["4.19/job-0"][0] == "div"


def test_manifest_contains_reusable_blocking_and_informing_runs(tmp_path):
    report = report_with_findings(1)
    job = JobRun("job-0", "https://prow/blocking", JobResult.FAILURE, JobType.BLOCKING, "SNO",
                 previous_attempts=[PreviousAttempt("https://prow/previous", JobResult.FAILURE)])
    informing = JobRun("job-0", "https://prow/informing", JobResult.FAILURE, JobType.INFORMING, "SNO")
    report.streams[0].payloads = [Payload("tag", "s", "4.19", PayloadStatus.REJECTED,
                                        jobs=[job, job, informing])]
    # The same name on another release must not supply evidence for this target.
    report.streams.append(StreamReport("s2", "4.20", payloads=[
        Payload("other", "s2", "4.20", PayloadStatus.REJECTED, jobs=[
            JobRun("job-0", "https://prow/other-release", JobResult.FAILURE, JobType.BLOCKING, "SNO")])]))
    path = tmp_path / "report.html"
    generate_html(report, path)
    targets = json.loads(path.with_suffix(".ai-targets.json").read_text())["sippy_jobs"]
    assert targets[0]["known_failing_runs"] == [
        {"prow_url": "https://prow/blocking", "job_type": "blocking",
         "previous_attempt_urls": ["https://prow/previous"]},
        {"prow_url": "https://prow/informing", "job_type": "informing", "previous_attempt_urls": []},
    ]


def test_empty_manifest_when_no_jobs_qualify(tmp_path):
    path = tmp_path / "report.html"
    generate_html(MonitorReport(), path)
    assert json.loads(path.with_suffix(".ai-targets.json").read_text()) == {"sippy_jobs": []}


def test_summary_links_target_the_same_job_after_health_row_sorting():
    report = report_with_findings(2)
    # Zero-baseline jobs sort last in health status, but first in the priority list.
    report.streams[0].regressions[0].sample_pass_rate = 10
    report.streams[0].regressions[1].basis_pass_rate = 0
    report.streams[0].regressions[1].sample_pass_rate = 0
    html = generate_html(report)
    rows = sippy_rows(html)
    links = re.findall(r'<li data-analysis-id="([^"]*)">(.*?)</li>', html, re.DOTALL)
    selected = _build_template_context(report)["priority_sippy"]
    assert selected[0].test_name == "job-1"
    for (target, summary), job in zip(links, selected, strict=True):
        assert f'>{job.test_name}</a>' in summary
        assert f'id="{target}"' in rows[job.analysis_key][1]


def test_sippy_only_analysis_patches_all_five_and_can_be_repeated(tmp_path):
    path = tmp_path / "report.html"
    generate_html(report_with_findings(), path)
    entries = {f"4.19/job-{n}": analysis(f"Evidence for job {n}") for n in range(5)}
    data = write_analysis(tmp_path, by_sippy_job=entries)
    patch_analysis_html(path, data)
    first = path.read_text()
    assert first.count('<div class="deep-analysis-card">') == 5
    assert first.count('<span class="badge ai-analyzed">AI Analyzed</span>') == 5
    summary = first.split('<!-- Findings Summary -->')[1].split('<!-- Failure Analysis -->')[0]
    sippy_details = first.split('<!-- Sippy Regressions -->')[1]
    assert '<div class="deep-analysis-card">' not in summary
    assert summary.count('<li data-analysis-id=') == 5
    assert sippy_details.count('<div class="deep-analysis-card">') == 5
    assert "Not yet analyzed" not in first
    assert "AI-enriched via Claude" in first
    assert "AI Analysis - Selected Sippy Jobs" not in first
    rows = sippy_rows(first)
    assert len(rows) == 7
    assert sum(tag == "details" for tag, _ in rows.values()) == 5
    for n in range(5):
        assert f"Evidence for job {n}" in rows[f"4.19/job-{n}"][1]
        assert '<div class="content sippy-job-analysis">' in rows[f"4.19/job-{n}"][1]
    for n in range(5, 7):
        assert rows[f"4.19/job-{n}"][0] == "div"
    patch_analysis_html(path, data)
    assert path.read_text() == first


def test_blocking_and_sippy_analyses_are_both_patched(tmp_path):
    report = report_with_findings(1)
    report.failure_counts = {"job-0": 3}
    report.streams[0].payloads = [Payload("tag", "s", "4.19", PayloadStatus.REJECTED, jobs=[
        JobRun("job-0", "https://prow/1", JobResult.FAILURE, JobType.BLOCKING, "SNO")])]
    path = tmp_path / "report.html"
    generate_html(report, path)
    patch_analysis_html(path, write_analysis(tmp_path,
        by_prow_url={"https://prow/1": analysis("Blocking run evidence")},
        by_sippy_job={"4.19/job-0": analysis("Historical job evidence")},
    ))
    html = path.read_text()
    assert "Blocking run evidence" in html
    assert "Historical job evidence" in html
    assert html.count('<div class="deep-analysis-card">') == 2
    assert html.count('<span class="badge ai-analyzed">AI Analyzed</span>') == 2
    assert 'data-status="persistent ai-analyzed"' in html


def test_error_results_visible_and_missing_keys_remain_unanalyzed(tmp_path):
    path = tmp_path / "report.html"
    generate_html(report_with_findings(2), path)
    patch_analysis_html(path, write_analysis(tmp_path, by_sippy_job={
        "4.19/job-0": analysis("Sippy evidence unavailable", failure_type="Analysis error"),
        "5.0/job-1": analysis("Wrong release must not be used"),
    }))
    html = path.read_text()
    assert "Analysis error" in html and "Sippy evidence unavailable" in html
    assert "Wrong release must not be used" not in html
    assert sippy_rows(html)["4.19/job-0"][0] == "details"
    assert sippy_rows(html)["4.19/job-1"][0] == "div"


def test_escaped_identity_and_analysis_text_are_safe(tmp_path):
    report = report_with_findings(1, name_prefix='job"<&')
    path = tmp_path / "report.html"
    generate_html(report, path)
    key = report.streams[0].regressions[0].analysis_key
    patch_analysis_html(path, write_analysis(tmp_path, by_sippy_job={
        key: analysis("<script>alert(1)</script>", suspect_prs=["javascript:alert(1)"]),
    }))
    html = path.read_text()
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in html
    assert '<script>alert(1)</script>' not in html
    assert 'javascript:alert(1)' not in html
    assert 'Not yet analyzed' not in html


def test_sippy_analysis_merges_and_round_trips_in_report_json(tmp_path):
    report = report_with_findings(1)
    merge_analysis(report, write_analysis(tmp_path, by_sippy_job={
        "4.19/job-0": analysis(same_root_cause=False, attempt_analyses=[{
            "prow_url": "https://prow/1", "root_cause": "Run evidence", "failure_type": "Test regression",
        }]),
    }))
    path = tmp_path / "report.json"
    generate_json(report, path)
    restored = load_json(path)
    assert restored.streams[0].regressions[0].deep_analysis.root_cause == "Known test failure"
    assert restored.streams[0].regressions[0].deep_analysis.attempt_analyses[0].prow_url == "https://prow/1"
    html = generate_html(restored)
    assert "Known test failure" in html
    assert "Run evidence" in html
    assert "AI-enriched via Claude" in html
    assert "Not yet analyzed" not in html
    assert '<span class="badge ai-analyzed">AI Analyzed</span>' in html
    assert "AI Analysis - Selected Sippy Jobs" not in html
    assert sippy_rows(html)["4.19/job-0"][0] == "details"
