"""Regressions for log redaction, local imports, safe links and dashboard access."""

import asyncio
import json
import logging
import re
import shutil
import subprocess
from copy import deepcopy
from dataclasses import asdict
from html.parser import HTMLParser

import pytest
from edge_ocp_ci_monitor.analysis import Settings, _BatchProgress
from edge_ocp_ci_monitor.analysis_cards import _render_analysis_card
from edge_ocp_ci_monitor.report import generate_html, patch_analysis_html
from payload_monitor.models import (
    AttemptAnalysis,
    DeepAnalysis,
    EscalationRisk,
    JobResult,
    JobRun,
    JobType,
    MonitorReport,
    Payload,
    PayloadStatus,
    PreviousAttempt,
    Regression,
    StreamReport,
)


@pytest.fixture
def report():
    job = JobRun("blocking-job", "https://prow.example/current?a=1&b=2", JobResult.FAILURE,
                 JobType.BLOCKING, "SNO", retries=1, previous_attempts=[
                     PreviousAttempt("https://prow.example/previous", JobResult.FAILURE),
                 ])
    other = JobRun("informing-job", "https://prow.example/other", JobResult.FAILURE, JobType.INFORMING, "TNF")
    finding = Regression("blocking-job", "id", "", "", 80, 20, topology="SNO", current_runs=5,
                         version="4.19", triage_url="https://sippy.example/job")
    return MonitorReport(streams=[StreamReport("nightly", "4.19", payloads=[
        Payload("4.19.0-0.nightly-2026-11-13-070000", "nightly", "4.19", PayloadStatus.REJECTED,
                url="https://release.example/payload", jobs=[job, other]),
    ], regressions=[finding])], escalation_risks=[
        EscalationRisk("informing-job", "TNF", "4.19", 3, "https://prow.example/other",
                       triage_url="https://sippy.example/informing"),
    ])


def diagnosis(cause="Original diagnosis"):
    return DeepAnalysis(root_cause=cause, failure_type="Platform issue", impact="SNO", recommendation="Investigate")


@pytest.mark.parametrize("scheme", ["http", "HTTP", "hTtP", "https", "HTTPS", "hTtPs"])
def test_progress_redacts_credentials_and_signatures_for_all_scheme_cases(caplog, scheme):
    settings = Settings("https://chai.example/mcp", "bot-secret")
    progress = _BatchProgress("blocking-jobs", settings)
    message = (f"Reading {scheme}://private-user:private-password@prow.example/log"
               "?X-Amz-Credential=private-credential&X-Amz-Signature=private-signature#private-fragment "
               "Bearer private-bearer bot-secret")
    with caplog.at_level(logging.INFO, logger="edge_ocp_ci_monitor.analysis"):
        asyncio.run(progress.on_progress(1, None, message))
    assert "prow.example/log" in caplog.text
    for secret in ("private-user", "private-password", "private-credential", "private-signature",
                   "private-fragment", "private-bearer", "bot-secret"):
        assert secret not in caplog.text


@pytest.mark.parametrize("initial", ["inline", "placeholder", "standalone"])
def test_import_replaces_only_matching_blocking_card_and_remains_repeatable(report, tmp_path, initial):
    job, other = report.streams[0].payloads[0].jobs
    other.deep_analysis = diagnosis("Unrelated diagnosis must stay unchanged")
    if initial == "inline":
        job.deep_analysis = diagnosis()
    html_path = tmp_path / "report.html"
    source = tmp_path / "analysis.json"
    generate_html(report, html_path)
    if initial == "standalone":
        # A card inserted outside a details row retains its own import key.
        html_path.write_text(_render_analysis_card(asdict(diagnosis()), attempt_count=2,
                                                   job_name=job.name, prow_url=job.prow_url))
    elif initial == "placeholder":
        source.write_text(json.dumps({"by_prow_url": {job.prow_url: asdict(diagnosis())}}))
        patch_analysis_html(html_path, source)
    before = html_path.read_text()
    if initial != "standalone":
        unrelated = re.search(r'<details id="detail-2".*?</details>', before, re.DOTALL)[0]
    for cause in ("Corrected diagnosis", "Second correction"):
        source.write_text(json.dumps({"by_prow_url": {job.prow_url: asdict(diagnosis(cause))}}))
        patch_analysis_html(html_path, source)
        html = html_path.read_text()
        assert cause in html
        assert "Original diagnosis" not in html
        assert "Analysis covers all 2 attempts" in html
        assert html.count('class="deep-analysis-card"') == (1 if initial == "standalone" else 2)
        if initial != "standalone":
            assert unrelated in html
            assert "AI-enriched via Chai" in html
            assert html.count('class="badge ai-analyzed"') == 2
    assert "Corrected diagnosis" not in html


SAFE_ATTEMPT = {
    "prow_url": "https://prow.example/valid?job=a&attempt=2",
    "root_cause": "Keep this valid attempt unchanged",
    "failure_type": "Platform issue",
}
UNSAFE_URLS = ["javascript:alert(1)", "data:text/html,unsafe", "http://prow.example/insecure",
               "//prow.example/relative", "https://", "https://[broken", "https://user:secret@prow.example/log",
               "https://prow.example/with\nnewline", "https://prow.example\\@other/log"]


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.hrefs = []
        self.interactions = []
        self.anchors = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.anchors.append(attrs)
            self.hrefs.append(attrs.get("href"))
        if "onclick" in attrs and tag in ("div", "a"):
            self.interactions.append((tag, attrs))


@pytest.mark.parametrize("caller", ["renderer", "blocking-inline", "sippy-inline",
                                     "blocking-import", "sippy-import"])
def test_safe_attempt_links_in_all_edge_rendering_paths(report, tmp_path, caller):
    raw = asdict(diagnosis())
    raw.update(same_root_cause=False, attempt_analyses=[
        SAFE_ATTEMPT,
        *[{"prow_url": url, "root_cause": f"Unsafe attempt {i}", "failure_type": "Platform issue"}
          for i, url in enumerate(UNSAFE_URLS)],
    ])
    if caller == "renderer":
        html = _render_analysis_card(raw)
    else:
        job = report.streams[0].payloads[0].jobs[0]
        finding = report.streams[0].regressions[0]
        key = finding.analysis_key if caller.startswith("sippy") else job.prow_url
        if caller.endswith("inline"):
            analysis = deepcopy(diagnosis())
            analysis.same_root_cause = False
            analysis.attempt_analyses = [AttemptAnalysis(**entry) for entry in raw["attempt_analyses"]]
            if caller.startswith("sippy"):
                finding.deep_analysis = analysis
            else:
                job.deep_analysis = analysis
            html = generate_html(report)
        else:
            html_path = tmp_path / "report.html"
            source = tmp_path / "analysis.json"
            generate_html(report, html_path)
            group = "by_sippy_job" if caller.startswith("sippy") else "by_prow_url"
            source.write_text(json.dumps({group: {key: raw}}))
            patch_analysis_html(html_path, source)
            html = html_path.read_text()
    assert SAFE_ATTEMPT["prow_url"] in Links(html).hrefs
    assert SAFE_ATTEMPT["root_cause"] in html
    for url in UNSAFE_URLS:
        assert url not in Links(html).hrefs
    assert "Unsafe attempt" not in html


def test_renderer_skips_malformed_attempt_records():
    raw = asdict(diagnosis())
    raw.update(same_root_cause=False, attempt_analyses=[None, {}, {"prow_url": 123}, SAFE_ATTEMPT])
    html = _render_analysis_card(raw)
    assert Links(html).hrefs == [SAFE_ATTEMPT["prow_url"]]


@pytest.mark.parametrize("scheme", ["https", "HTTPS", "hTtPs"])
def test_valid_https_attempt_is_preserved_without_normalizing_its_url(scheme):
    raw = asdict(diagnosis())
    attempt = {**SAFE_ATTEMPT, "prow_url": f"{scheme}://prow.example/valid?a=1&b=2"}
    raw.update(same_root_cause=False, attempt_analyses=[attempt])
    assert Links(_render_analysis_card(raw)).hrefs == [attempt["prow_url"]]


@pytest.mark.parametrize("retried", [False, True])
def test_retry_evidence_prevents_all_healthy_banner(report, retried):
    stream = report.streams[0]
    stream.regressions = []
    report.escalation_risks = []
    job = stream.payloads[0].jobs[0]
    stream.payloads[0].jobs = [job]
    job.result = JobResult.SUCCESS
    if not retried:
        job.previous_attempts = []
        job.retries = 0
    html = generate_html(report)
    assert ("All edge jobs are passing across all monitored payloads" in html) is not retried
    assert ("Warning - Passed After Retry" in html) is retried
    if retried:
        assert job.prow_url in Links(html).hrefs
        assert job.previous_attempts[0].prow_url in Links(html).hrefs


def test_dashboard_actions_have_keyboard_semantics_and_destinations(report):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise keyboard handlers")
    retry = JobRun("retry-job", "https://prow.example/retry", JobResult.SUCCESS, JobType.BLOCKING, "SNO", retries=1,
                   previous_attempts=[PreviousAttempt("https://prow.example/first", JobResult.FAILURE)])
    report.streams[0].payloads[0].jobs.append(retry)
    parsed = Links(generate_html(report))
    assert all(anchor.get("href") for anchor in parsed.anchors)
    actions = []
    classes = set()
    for tag, attrs in parsed.interactions:
        if tag == "a" and attrs.get("role") != "button":
            continue  # Real external links retain native link behavior.
        assert attrs["role"] == "button"
        assert attrs["tabindex"] == "0"
        assert attrs.get("onkeydown")
        classes.update(attrs.get("class", "").split())
        actions.append(attrs)
    assert {"health-status", "health-count", "fs-blocking-row", "fs-escalation-item", "fs-situation-item"} <= classes
    result = subprocess.run([node, "-e", r"""
const assert = require('node:assert/strict');
const actions = JSON.parse(process.argv[1]);
for (const action of actions) {
  let clicks = 0;
  const control = {click() { clicks++; }};
  const activate = new Function('event', action.onkeydown);
  for (const key of ['Enter', ' ']) {
    let prevented = false;
    const before = clicks;
    activate.call(control, {target: control, key, preventDefault() { prevented = true; }});
    assert.equal(clicks, before + 1);
    assert.equal(prevented, true);
  }
  activate.call(control, {target: {}, key: 'Enter', preventDefault() { throw Error('nested link intercepted'); }});
  activate.call(control, {target: control, key: 'Tab', preventDefault() { throw Error('Tab intercepted'); }});
  assert.equal(clicks, 2);
}
""", json.dumps(actions)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
