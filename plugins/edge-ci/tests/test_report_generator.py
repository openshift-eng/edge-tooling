"""Tests for payload_monitor.report.generator."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from payload_monitor.models import (
    ComponentRegression,
    DeepAnalysis,
    FailingTest,
    JobResult,
    JobRun,
    JobType,
    JiraBug,
    MonitorReport,
    Payload,
    PayloadStatus,
    Regression,
    StreamReport,
    SuggestedBug,
)
from payload_monitor.report.generator import (
    _build_template_context,
    _extract_date,
    _render_analysis_card,
    _safe_urls,
    generate_html,
    generate_json,
    load_json,
)


class TestExtractDate:
    def test_standard_tag(self):
        assert _extract_date("4.19.0-0.nightly-2026-03-25-085944") == "2026-03-25 08:59:44"

    def test_no_match(self):
        assert _extract_date("random-string") == ""

    def test_partial_match(self):
        assert _extract_date("2026-03-25") == ""


class TestSafeUrls:
    def test_allows_https(self):
        urls = ["https://github.com/org/repo/pull/1"]
        assert _safe_urls(urls) == urls

    def test_blocks_javascript(self):
        urls = ["javascript:alert(1)"]
        assert _safe_urls(urls) == []

    def test_blocks_http(self):
        urls = ["http://example.com"]
        assert _safe_urls(urls) == []

    def test_blocks_empty_netloc(self):
        urls = ["https://"]
        assert _safe_urls(urls) == []

    def test_mixed(self):
        urls = [
            "https://github.com/pr/1",
            "javascript:void(0)",
            "https://bugzilla.redhat.com/123",
        ]
        assert _safe_urls(urls) == [
            "https://github.com/pr/1",
            "https://bugzilla.redhat.com/123",
        ]


class TestBuildTemplateContext:
    def test_basic_context(self, sample_report):
        ctx = _build_template_context(sample_report)
        assert ctx["report"] is sample_report
        assert len(ctx["all_failing"]) == 1
        assert "4.19" in ctx["versions"]
        assert "SNO" in ctx["topologies"]

    def test_blocking_sorted_first(self):
        blocking = JobRun("b", "url", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        informing = JobRun("i", "url", JobResult.FAILURE, JobType.INFORMING, "SNO")
        payload = Payload("t", "s", "4.19", PayloadStatus.REJECTED, jobs=[informing, blocking])
        stream = StreamReport("s", "4.19", payloads=[payload])
        report = MonitorReport(generated_at="now", streams=[stream])

        ctx = _build_template_context(report)
        assert ctx["all_failing"][0]["job"].job_type == JobType.BLOCKING

    def test_empty_report(self):
        report = MonitorReport(generated_at="now")
        ctx = _build_template_context(report)
        assert ctx["all_failing"] == []
        assert ctx["versions"] == []
        assert ctx["topologies"] == []

    def test_regressions_sorted_by_delta(self):
        r1 = Regression("t1", "id1", "c1", "", basis_pass_rate=90, sample_pass_rate=50, version="4.19")
        r2 = Regression("t2", "id2", "c2", "", basis_pass_rate=80, sample_pass_rate=70, version="4.19")
        stream = StreamReport("s", "4.19", regressions=[r1, r2])
        report = MonitorReport(generated_at="now", streams=[stream])

        ctx = _build_template_context(report)
        # r1 has worse delta (-40) than r2 (-10), should come first
        assert ctx["all_regressions"][0].test_name == "t1"


class TestRenderAnalysisCard:
    def test_basic_card(self):
        da = {
            "root_cause": "DNS failure",
            "failure_type": "Infrastructure",
            "impact": "All SNO jobs affected",
            "suspect_prs": ["https://github.com/org/repo/pull/123"],
            "recommendation": "Check DNS config",
        }
        html = _render_analysis_card(da)
        assert "DNS failure" in html
        assert "Infrastructure" in html
        assert "https://github.com/org/repo/pull/123" in html
        assert "Check DNS config" in html
        assert "deep-analysis-card" in html

    def test_no_suspect_prs(self):
        da = {"root_cause": "x", "failure_type": "y", "impact": "z",
              "suspect_prs": [], "recommendation": "r"}
        html = _render_analysis_card(da)
        assert "Suspect PRs" not in html

    def test_html_escapes_content(self):
        da = {"root_cause": '<script>alert("xss")</script>',
              "failure_type": "", "impact": "", "suspect_prs": [],
              "recommendation": ""}
        html = _render_analysis_card(da)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_unsafe_urls_filtered(self):
        da = {"root_cause": "x", "failure_type": "y", "impact": "z",
              "suspect_prs": ["javascript:alert(1)", "https://github.com/pr/1"],
              "recommendation": "r"}
        html = _render_analysis_card(da)
        assert "javascript:" not in html
        assert "https://github.com/pr/1" in html


class TestGenerateHtml:
    def test_generates_html(self, sample_report):
        html = generate_html(sample_report)
        assert "Edge Enablement Payload Monitor" in html
        assert "4.19" in html

    def test_writes_to_file(self, sample_report, tmp_path):
        out = tmp_path / "report.html"
        generate_html(sample_report, out)
        assert out.exists()
        content = out.read_text()
        assert "Edge Enablement Payload Monitor" in content

    def test_creates_parent_dirs(self, sample_report, tmp_path):
        out = tmp_path / "sub" / "dir" / "report.html"
        generate_html(sample_report, out)
        assert out.exists()


class TestGenerateJson:
    def test_generates_json(self, sample_report, tmp_path):
        out = tmp_path / "report.json"
        generate_json(sample_report, out)
        assert out.exists()

        data = json.loads(out.read_text())
        assert data["generated_at"] == "2026-03-25 12:00 UTC"
        assert len(data["streams"]) == 1
        assert data["streams"][0]["version"] == "4.19"

    def test_json_contains_jobs(self, sample_report, tmp_path):
        out = tmp_path / "report.json"
        generate_json(sample_report, out)
        data = json.loads(out.read_text())

        payloads = data["streams"][0]["payloads"]
        assert len(payloads) == 1
        jobs = payloads[0]["edge_jobs"]
        assert len(jobs) == 2

    def test_json_with_deep_analysis(self, tmp_path):
        da = DeepAnalysis(root_cause="DNS", failure_type="Infra",
                          impact="All", recommendation="Fix DNS",
                          suspect_prs=["https://github.com/pr/1"])
        job = JobRun("j", "url", JobResult.FAILURE, JobType.BLOCKING,
                     "SNO", deep_analysis=da)
        payload = Payload("t", "s", "4.19", PayloadStatus.REJECTED, jobs=[job])
        stream = StreamReport("s", "4.19", payloads=[payload])
        report = MonitorReport(generated_at="now", streams=[stream])

        out = tmp_path / "report.json"
        generate_json(report, out)
        data = json.loads(out.read_text())

        job_data = data["streams"][0]["payloads"][0]["edge_jobs"][0]
        assert job_data["deep_analysis"]["root_cause"] == "DNS"

    def test_json_with_suggested_bugs(self, tmp_path):
        bug = SuggestedBug(title="Bug", description="desc",
                           job_name="j", topology="SNO",
                           versions=["4.19"], failing_tests=["t1"])
        report = MonitorReport(generated_at="now", suggested_bugs=[bug])

        out = tmp_path / "report.json"
        generate_json(report, out)
        data = json.loads(out.read_text())

        assert len(data["suggested_bugs"]) == 1
        assert data["suggested_bugs"][0]["title"] == "Bug"


class TestLoadJson:
    def test_round_trip(self, sample_report, tmp_path):
        out = tmp_path / "report.json"
        generate_json(sample_report, out)
        loaded = load_json(out)

        assert loaded.generated_at == sample_report.generated_at
        assert len(loaded.streams) == 1
        assert loaded.streams[0].version == "4.19"
        assert len(loaded.streams[0].payloads) == 1

        payload = loaded.streams[0].payloads[0]
        assert payload.tag == sample_report.streams[0].payloads[0].tag
        assert payload.status == PayloadStatus.REJECTED

    def test_loads_deep_analysis(self, tmp_path):
        da = DeepAnalysis(root_cause="DNS", failure_type="Infra",
                          impact="All", recommendation="Fix",
                          suspect_prs=["https://github.com/pr/1"])
        job = JobRun("j", "url", JobResult.FAILURE, JobType.BLOCKING,
                     "SNO", deep_analysis=da)
        payload = Payload("t", "s", "4.19", PayloadStatus.REJECTED, jobs=[job])
        report = MonitorReport(
            generated_at="now",
            streams=[StreamReport("s", "4.19", payloads=[payload])],
        )

        out = tmp_path / "report.json"
        generate_json(report, out)
        loaded = load_json(out)

        loaded_da = loaded.streams[0].payloads[0].jobs[0].deep_analysis
        assert loaded_da is not None
        assert loaded_da.root_cause == "DNS"
        assert loaded_da.suspect_prs == ["https://github.com/pr/1"]

    def test_loads_regressions(self, tmp_path):
        reg = Regression(
            test_name="test1", test_id="id1", component="c1",
            capability="cap1", basis_pass_rate=90.0, sample_pass_rate=50.0,
            version="4.19", topology="SNO", current_runs=10,
        )
        stream = StreamReport("s", "4.19", regressions=[reg])
        report = MonitorReport(generated_at="now", streams=[stream])

        out = tmp_path / "report.json"
        generate_json(report, out)
        loaded = load_json(out)

        loaded_reg = loaded.streams[0].regressions[0]
        assert loaded_reg.test_name == "test1"
        assert loaded_reg.basis_pass_rate == 90.0
        assert loaded_reg.sample_pass_rate == 50.0

    def test_loads_suggested_bugs(self, tmp_path):
        bug = SuggestedBug(title="Bug", description="desc",
                           job_name="j", topology="SNO",
                           versions=["4.19"], failing_tests=["t1"])
        report = MonitorReport(generated_at="now", suggested_bugs=[bug])

        out = tmp_path / "report.json"
        generate_json(report, out)
        loaded = load_json(out)

        assert len(loaded.suggested_bugs) == 1
        assert loaded.suggested_bugs[0].title == "Bug"

    def test_loads_component_regressions(self, tmp_path):
        cr = ComponentRegression(
            component="c1", test_name="t1", test_suite="s1",
            test_id="id1", capability="cap", version="4.19",
            sample_pass_rate=25.0, base_pass_rate=95.0,
        )
        report = MonitorReport(generated_at="now", component_regressions=[cr])

        out = tmp_path / "report.json"
        generate_json(report, out)
        loaded = load_json(out)

        assert len(loaded.component_regressions) == 1
        assert loaded.component_regressions[0].component == "c1"

    def test_filters_unsafe_urls_on_load(self, tmp_path):
        da_data = {
            "root_cause": "x", "failure_type": "y", "impact": "z",
            "suspect_prs": ["javascript:alert(1)", "https://github.com/pr/1"],
            "recommendation": "r",
        }
        data = {
            "generated_at": "now",
            "streams": [{
                "stream": "s", "version": "4.19",
                "payloads": [{
                    "tag": "t", "status": "Rejected", "url": "",
                    "edge_jobs": [{
                        "name": "j", "result": "F", "job_type": "blocking",
                        "topology": "SNO", "prow_url": "url",
                        "error_summary": "", "failing_tests": [],
                        "deep_analysis": da_data,
                    }],
                }],
                "regressions": [],
            }],
            "jira_bugs": [],
            "suggested_bugs": [],
            "component_regressions": [],
        }
        json_path = tmp_path / "report.json"
        json_path.write_text(json.dumps(data))

        loaded = load_json(json_path)
        prs = loaded.streams[0].payloads[0].jobs[0].deep_analysis.suspect_prs
        assert prs == ["https://github.com/pr/1"]
