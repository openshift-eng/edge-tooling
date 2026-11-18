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
from pathlib import Path

import pytest
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

from edge_ocp_ci_monitor.analysis import Settings, _BatchProgress
from edge_ocp_ci_monitor.analysis_cards import _render_analysis_card
from edge_ocp_ci_monitor.report import generate_html, patch_analysis_html


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


@pytest.mark.parametrize("merged", [False, True])
def test_drafts_follow_producing_targets_through_rendering_and_drawer(report, tmp_path, merged):
    from payload_monitor.report.generator import load_json

    from edge_ocp_ci_monitor.analysis import build_targets
    from edge_ocp_ci_monitor.report import generate_json, merge_analysis_data

    other = deepcopy(report.streams[0])
    other.version = other.regressions[0].version = "4.20"
    other.payloads[0].jobs[0].prow_url = "https://prow.example/another-run"
    report.streams.append(other)
    data = {"by_prow_url": {}, "by_sippy_job": {}}
    expected = {}
    for category, group, field, prefix in (
        ("blocking_jobs", "by_prow_url", "prow_url", "prow"),
        ("sippy_jobs", "by_sippy_job", "analysis_key", "sippy"),
    ):
        for target in build_targets(report)[category]:
            key = f"{prefix}:{target[field]}"
            title = "Shared diagnosis" if merged else key
            expected[key] = title
            versions = target.get("versions", [target["version"]])
            data[group][target[field]] = {
                **asdict(diagnosis()),
                "jira": {
                    "search_status": "checked", "search_summary": "No duplicate found", "existing_bugs": [],
                    "new_bug": {"title": title, "description": "Evidence for " + key,
                                "target_versions": [{"name": version + ".0", "id": "123"}
                                                    for version in versions]},
                },
            }
    merge_analysis_data(report, data)
    assert len(report.suggested_bugs) == (1 if merged else 4)
    assert {key for bug in report.suggested_bugs for key in bug.analysis_keys} == set(expected)
    path = tmp_path / "report.json"
    generate_json(report, path)
    restored = load_json(path)
    assert [bug.analysis_keys for bug in restored.suggested_bugs] == [
        bug.analysis_keys for bug in report.suggested_bugs
    ]
    html = generate_html(restored)

    class Cards(HTMLParser):
        def __init__(self):
            super().__init__()
            self.sources = []
            self.drafts = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if "data-analysis-key" in attrs:
                self.sources.append(attrs)
            if "jira-draft" in attrs.get("class", "").split():
                self.drafts.append(attrs)

    cards = Cards()
    cards.feed(html)
    for source in cards.sources:
        key = source["data-analysis-key"]
        row = html.split(f'id="{source["id"]}"', 1)[1].split("</details>", 1)[0]
        actual = [url for url in Links(row).hrefs if "CreateIssue!default.jspa" in url]
        draft = next((bug for bug in restored.suggested_bugs if key in bug.analysis_keys), None)
        assert actual == ([draft.create_url] if draft else [])
        if draft:
            assert draft.title == expected[key]

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise the finding drawer")
    script = Path(__file__).parents[1] / "edge_ocp_ci_monitor/templates/dashboard.js"
    result = subprocess.run([node, "-e", r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(process.argv[2]);
function element() {
  return {
    dataset: {}, events: {}, hidden: false,
    classList: {contains() { return false; }, toggle() {}},
    querySelector() { return null; }, querySelectorAll() { return []; }, closest() { return null; },
    addEventListener(type, handler) { this.events[type] = handler; },
    replaceChildren() {}, appendChild() {}, append() {}, focus() {}
  };
}
function dataset(attrs) {
  return Object.fromEntries(Object.entries(attrs).filter(([key]) => key.startsWith('data-'))
    .map(([key, value]) => [key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), value]));
}
const elements = {}, callbacks = [];
const sources = input.sources.map(attrs => {
  const source = element();
  source.dataset = dataset(attrs);
  source.classList.contains = name => attrs.class.split(' ').includes(name);
  elements[attrs.id] = source;
  return source;
});
let opened;
const drafts = input.drafts.map((attrs, index) => ({
  dataset: dataset(attrs), querySelector() { return {click() { opened = index; }}; }
}));
const rows = input.sources.map(attrs => {
  const row = element();
  row.dataset.findingSource = attrs.id;
  row.querySelector = () => element();
  return row;
});
const document = {
  addEventListener(type, handler) { callbacks.push(handler); },
  getElementById(id) { return elements[id] ||= element(); },
  createElement: element, createTextNode: element,
  querySelectorAll(selector) {
    if (selector === '[data-finding-source]') return rows;
    if (selector === '.jira-draft') return drafts;
    return [];
  }
};
const drawer = document.getElementById('finding-drawer');
drawer.showModal = () => {};
drawer.close = () => drawer.events.close();
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document, window: {location: {hash: ''}}
});
callbacks.find(handler => handler.name !== 'setupTabScrolling')();
rows.forEach((row, index) => {
  row.events.click({target: {closest() { return null; }}});
  const key = sources[index].dataset.analysisKey;
  const expected = drafts.findIndex(draft => JSON.parse(draft.dataset.analysisKeys).includes(key));
  const action = document.getElementById('finding-drawer-bug');
  assert.equal(action.hidden, expected === -1);
  if (expected !== -1) {
    action.events.click();
    assert.equal(opened, expected, key);
  }
});
""", str(script), json.dumps({"sources": cards.sources, "drafts": cards.drafts})],
        capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("destination", ["failed-row", "previous-attempt", "current-run"])
def test_retry_success_card_opens_reachable_evidence_for_click_and_keyboard(report, destination):
    retry = deepcopy(report.streams[0].payloads[0].jobs[0])
    retry.result = JobResult.SUCCESS
    retry.prow_url = "https://prow.example/passed?a=1&b=2"
    if destination != "failed-row":
        report.streams[0].payloads[0].jobs = []
    if destination == "current-run":
        retry.previous_attempts = []
    report.streams[0].payloads[0].jobs.append(retry)
    action = next(attrs for _, attrs in Links(generate_html(report)).interactions
                  if "fs-blocking-row" in attrs.get("class", ""))
    expected = retry.name if destination == "failed-row" else (
        retry.previous_attempts[0].prow_url if retry.previous_attempts else retry.prow_url
    )
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise retry card navigation")
    result = subprocess.run([node, "-e", r"""
const assert = require('node:assert/strict');
const action = JSON.parse(process.argv[1]), expected = process.argv[2], mode = process.argv[3];
const destinations = [];
const click = new Function('event', 'window', 'scrollToJob', action.onclick);
const control = {closest() { return null; }, click() {
  click({target: control}, {open(url, target, features) {
    assert.notEqual(mode, 'failed-row');
    assert.equal(target, '_blank');
    assert.equal(features, 'noopener,noreferrer');
    destinations.push(url);
  }}, name => { assert.equal(mode, 'failed-row'); destinations.push(name); });
}};
control.click();
const keydown = new Function('event', action.onkeydown);
for (const key of ['Enter', ' ']) {
  keydown.call(control, {target: control, key, preventDefault() {}});
}
assert.deepEqual(destinations, [expected, expected, expected]);
click({target: {closest() { return {}; }}}, {}, () => { throw Error('nested link intercepted'); });
""", json.dumps(action), expected, destination], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("blocking", [True, False])
def test_tab_counts_use_neutral_badges_regardless_of_blocking_state(report, blocking):
    if not blocking:
        report.streams[0].payloads[0].jobs[0].job_type = JobType.INFORMING
    html = generate_html(report)
    nav = re.search(r'<nav[^>]*id="dashboard-tabs-nav"[^>]*>(.*?)</nav>', html, re.DOTALL)[1]
    failures = re.search(r'<button[^>]*id="view-failure-analysis"[^>]*>(.*?)</button>', nav, re.DOTALL)[1]
    assert 'class="dashboard-count"' in failures
    assert set(re.findall(r'<span class="([^"]+)">\d+</span>', nav)) == {"dashboard-count"}
    assert re.search(r'<span[^>]*>2</span>', failures)
    assert html.count('aria-controls="dashboard-tabs-nav"') == 2
    assert 'id="view-sippy-health"' in nav
    assert 'id="view-component-readiness"' not in nav
    assert 'id="view-timing-insights"' not in nav


@pytest.mark.parametrize("reduced_motion", [True, False])
def test_tab_scroll_controls_follow_overflow_and_motion_preference(reduced_motion):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise tab scrolling")
    script = Path(__file__).parents[1] / "edge_ocp_ci_monitor/templates/dashboard.js"
    result = subprocess.run([node, "-e", r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const reduced = JSON.parse(process.argv[2]);
const callbacks = [], classes = new Set(), clicks = {}, events = {}, calls = [];
const nav = {
  scrollWidth: 1000, clientWidth: 400, scrollLeft: 0,
  addEventListener(type, handler) { events[type] = handler; },
  scrollBy(options) { calls.push(options); }
};
const wrap = {
  classList: {toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); }},
  querySelector(selector) { return {addEventListener(type, handler) { clicks[selector] = handler; }}; }
};
const document = {
  addEventListener(type, handler) { callbacks.push(handler); },
  getElementById(id) { return id === 'dashboard-tabs-wrap' ? wrap : nav; }
};
const window = {
  matchMedia() { return {matches: reduced}; },
  addEventListener(type, handler) { events[type] = handler; }
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {document, window});
callbacks.find(handler => handler.name === 'setupTabScrolling')();
assert.equal(classes.has('can-scroll-left'), false);
assert.equal(classes.has('can-scroll-right'), true);
nav.scrollLeft = 300;
events.scroll();
assert.equal(classes.has('can-scroll-left'), true);
assert.equal(classes.has('can-scroll-right'), true);
clicks['.tab-scroll-right']();
clicks['.tab-scroll-left']();
assert.equal(calls[0].left, 240);
assert.equal(calls[1].left, -240);
assert.equal(calls[0].behavior, reduced ? 'auto' : 'smooth');
assert.equal(calls[1].behavior, reduced ? 'auto' : 'smooth');
nav.scrollLeft = 600;
events.scroll();
assert.equal(classes.has('can-scroll-left'), true);
assert.equal(classes.has('can-scroll-right'), false);
nav.clientWidth = 1000;
nav.scrollLeft = 0;
events.resize();
assert.equal(classes.size, 0);
""", str(script), json.dumps(reduced_motion)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["no-analysis", "inline", "import", "legacy-import"])
def test_sippy_actions_offer_analysis_or_copyable_question(report, tmp_path, mode):
    finding = report.streams[0].regressions[0]
    if mode == "inline":
        finding.deep_analysis = diagnosis()
    html_path = tmp_path / "report.html"
    generate_html(report, html_path)
    if mode.endswith("import"):
        if mode == "legacy-import":
            html = html_path.read_text().replace('<span class="sippy-ask-label">Ask chai-bot: </span>', '')
            html_path.write_text(html.replace(' id="sippy-analysis-1"', ''))
        source = tmp_path / "analysis.json"
        source.write_text(json.dumps({"by_sippy_job": {finding.analysis_key: asdict(diagnosis())}}))
        patch_analysis_html(html_path, source)
        patch_analysis_html(html_path, source)
    html = html_path.read_text()
    row = re.search(r'<(?:div|details) id="sippy-row-1".*?<!-- /sippy-job -->', html, re.DOTALL)[0]
    assert finding.triage_url in Links(row).hrefs
    if mode == "no-analysis":
        assert 'class="sippy-ask-label">Ask chai-bot: </span>' in row
        code = re.search(r'<code>(.*?)</code>', row, re.DOTALL)[1]
        assert code == f"Why is {finding.test_name} unhealthy on {finding.version}? What tests are failing?"
        assert "Ask chai-bot:" not in code
        assert "View AI analysis" not in row
    else:
        assert row.startswith('<details ')
        assert row.count("View AI analysis") == 1
        assert 'aria-controls="sippy-analysis-1"' in row
        assert 'id="sippy-analysis-1"' in row
        assert "Ask chai-bot:" not in row
        assert "cmd-copy-row" not in row
        assert "What tests are failing?" not in row
        assert "Original diagnosis" in row


def test_sippy_analysis_button_opens_card_without_closing_an_open_card(report):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise the analysis button")
    report.streams[0].regressions[0].deep_analysis = diagnosis()
    html = generate_html(report)
    button = re.search(r'<button[^>]*class="dashboard-button sippy-view-analysis"[^>]*>', html)[0]
    handler = re.search(r'onclick="([^"]+)"', button)[1]
    result = subprocess.run([node, "-e", r"""
const assert = require('node:assert/strict');
const activate = new Function('event', process.argv[1]);
for (const initiallyOpen of [false, true]) {
  const card = {open: initiallyOpen};
  let prevented = false, stopped = false;
  const button = {closest(selector) { assert.equal(selector, 'details'); return card; }};
  activate.call(button, {
    preventDefault() { prevented = true; },
    stopPropagation() { stopped = true; }
  });
  assert.equal(card.open, true);
  assert.equal(prevented, true);
  assert.equal(stopped, true);
}
""", handler], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["mixed", "sippy-only", "empty"])
def test_findings_queue_preserves_targets_and_supports_data_only_reports(report, mode):
    report.escalation_risks = []
    stream = report.streams[0]
    if mode == "mixed":
        other = deepcopy(stream)
        other.version = "4.20"
        other.payloads[0].jobs[0].prow_url = "https://prow.example/other-version"
        other.regressions[0].version = "4.20"
        report.streams.append(other)
    elif mode == "sippy-only":
        stream.payloads = []
    else:
        report.streams = []

    html = generate_html(report)
    references = re.findall(r'data-finding-source="([^"]+)"', html)
    # Every summary action must lead to its own full record, including data-only Sippy rows.
    assert len(references) == len(set(references))
    assert all(f'id="{reference}"' in html for reference in references)
    sections = {
        name: re.search(rf'<section[^>]*data-findings-section="{name}"[^>]*>(.*?)</section>', html, re.DOTALL)[1]
        for name in ("payload", "sippy")
    }
    payload_targets = re.findall(r'data-finding-source="([^"]+)"', sections["payload"])
    sippy_targets = re.findall(r'data-finding-source="([^"]+)"', sections["sippy"])
    assert all(target.startswith("detail-") for target in payload_targets)
    assert all(target.startswith("sippy-row-") for target in sippy_targets)
    assert "Nightly Payload - Failure Analysis" in sections["payload"]
    assert "Sippy Job Health Status" in sections["sippy"]
    assert "consecutive" not in sections["sippy"]
    assert "Selection:" not in sections["sippy"]
    assert '<details class="queue-selection"' not in html
    if mode == "mixed":
        assert sections["payload"].count('<table class="finding-table"') == 1
        assert sections["sippy"].count('<table class="finding-table"') == 1
        assert "Current Pass Rate" not in sections["payload"]
        assert "Recurrence" in sections["payload"]
        assert "Current Pass Rate" in sections["sippy"]
        assert len(payload_targets) == 4  # Blocking and informing jobs in both releases.
        assert len(sippy_targets) == 2
        assert {"sippy-row-1", "sippy-row-2"} <= set(references)
        assert html.count('class="finding-job"') == 6
        assert 'data-finding-kind="informing"' in sections["payload"]
        assert 'data-finding-kind="informing"' not in sections["sippy"]
        assert 'id="tab-failure-analysis"' in html
        assert "https://prow.example/other-version" in html
    elif mode == "sippy-only":
        assert references == ["sippy-row-1"]
        assert 'id="tab-sippy-health"' in html
    else:
        assert references == []
        assert "All edge jobs are passing across all monitored payloads" in html
        assert 'id="tab-sippy-health"' not in html


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
def test_jira_references_are_links_in_every_analysis_rendering_path(report, tmp_path, caller):
    raw = asdict(diagnosis('See OCPBUGS-123; <img src=x onerror="alert(1)"> & ETCD-9.'))
    raw.update(
        impact="Tracks https://redhat.atlassian.net/browse/OCPBUGS-222.",
        recommendation="Follow [the PHC bug](https://redhat.atlassian.net/browse/OCPBUGS-456) "
                       "and https://issues.redhat.com/browse/CNF-7.",
        same_root_cause=False,
        attempt_analyses=[{**SAFE_ATTEMPT, "root_cause": "Related to OCPBUGS-789."}],
    )
    if caller == "renderer":
        html = _render_analysis_card(raw)
    else:
        finding = report.streams[0].regressions[0]
        job = report.streams[0].payloads[0].jobs[0]
        if caller.endswith("inline"):
            analysis = DeepAnalysis(**{**raw, "attempt_analyses": [AttemptAnalysis(**raw["attempt_analyses"][0])]})
            if caller.startswith("sippy"):
                finding.deep_analysis = analysis
            else:
                job.deep_analysis = analysis
            html = generate_html(report)
        else:
            html_path, source = tmp_path / "report.html", tmp_path / "analysis.json"
            generate_html(report, html_path)
            group, key = (("by_sippy_job", finding.analysis_key) if caller.startswith("sippy")
                          else ("by_prow_url", job.prow_url))
            source.write_text(json.dumps({group: {key: raw}}))
            patch_analysis_html(html_path, source)
            html = html_path.read_text()
    links = Links(html)
    for key in ("OCPBUGS-123", "ETCD-9", "OCPBUGS-222", "OCPBUGS-456", "CNF-7", "OCPBUGS-789"):
        assert links.hrefs.count(f"https://redhat.atlassian.net/browse/{key}") == 1
    assert "<img " not in html
    assert "&lt;img " in html
    assert ">the PHC bug</a>" in html
    assert "](https://" not in html
    assert all(anchor.get("rel") == "noopener noreferrer" for anchor in links.anchors
               if anchor.get("href", "").startswith("https://redhat.atlassian.net/browse/"))


def test_jira_links_do_not_trust_html_or_markdown_destinations():
    raw = asdict(diagnosis('[OCPBUGS-123](javascript:alert(1)) <script>alert(1)</script>'))
    raw["recommendation"] = '[<img src=x onerror="alert(1)">](https://redhat.atlassian.net/browse/OCPBUGS-456)'
    html = _render_analysis_card(raw)
    assert Links(html).hrefs == [
        "https://redhat.atlassian.net/browse/OCPBUGS-123", "https://redhat.atlassian.net/browse/OCPBUGS-456",
    ]
    assert "<script>" not in html
    assert "<img " not in html


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
