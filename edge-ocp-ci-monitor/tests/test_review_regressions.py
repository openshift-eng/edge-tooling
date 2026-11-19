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


def test_drafts_follow_producing_targets_through_rendering_and_drawer(report, tmp_path):
    from payload_monitor.report.generator import load_json

    from edge_ocp_ci_monitor.analysis import build_targets
    from edge_ocp_ci_monitor.report import generate_json, merge_analysis_data

    other = deepcopy(report.streams[0])
    other.version = other.regressions[0].version = "4.20"
    other.payloads[0].jobs[0].prow_url = "https://prow.example/another-run"
    report.streams.append(other)
    data = {"by_prow_url": {}, "by_sippy_job": {}}
    expected = set()
    for category, group, field, prefix in (
        ("blocking_jobs", "by_prow_url", "prow_url", "prow"),
        ("sippy_jobs", "by_sippy_job", "analysis_key", "sippy"),
    ):
        for target in build_targets(report)[category]:
            key = f"{prefix}:{target[field]}"
            expected.add(key)
            versions = target.get("versions", [target["version"]])
            data[group][target[field]] = {
                **asdict(diagnosis()),
                "jira": {
                    "search_status": "checked", "search_summary": "No duplicate found", "existing_bugs": [],
                    "new_bug": {"title": key, "description": "Evidence for " + key,
                                "target_versions": [{"name": version + ".0", "id": "123"}
                                                    for version in versions]},
                },
            }
    merge_analysis_data(report, data)
    assert len(report.suggested_bugs) == len(expected) == 4
    assert {key for bug in report.suggested_bugs for key in bug.analysis_keys} == expected
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
            assert draft.title == key

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
  source.querySelector = selector => selector === '.sippy-cell-action a' && source.dataset.sippyKey
    ? {getAttribute() { return 'https://sippy.example/' + attrs.id; }}
    : null;
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
let external;
const actions = document.getElementById('finding-drawer-actions');
actions.querySelector = () => external;
actions.appendChild = link => {
  external = link;
  link.remove = () => { external = undefined; };
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document, window: {location: {hash: ''}}
});
callbacks.find(handler => handler.name !== 'setupTabScrolling')();
rows.forEach((row, index) => {
  row.events.click({target: {closest() { return null; }}});
  const key = sources[index].dataset.analysisKey;
  const isSippy = key.startsWith('sippy:');
  assert.equal(external.className, 'dashboard-button');
  assert.equal(external.textContent, isSippy ? 'View in Sippy' : 'View in Prow');
  assert.equal(external.href, isSippy ? 'https://sippy.example/' + input.sources[index].id : sources[index].dataset.prowUrl);
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


@pytest.mark.parametrize("destination", ["failed-row", "previous-attempt"])
def test_retry_success_card_opens_reachable_evidence_for_click_and_keyboard(report, destination):
    retry = deepcopy(report.streams[0].payloads[0].jobs[0])
    retry.result = JobResult.SUCCESS
    retry.prow_url = "https://prow.example/passed?a=1&b=2"
    if destination != "failed-row":
        report.streams[0].payloads[0].jobs = []
    report.streams[0].payloads[0].jobs.append(retry)
    action = next(attrs for _, attrs in Links(generate_html(report)).interactions
                  if "fs-blocking-row" in attrs.get("class", ""))
    expected = retry.name if destination == "failed-row" else retry.previous_attempts[0].prow_url
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


def test_payload_tooltip_tracks_hover_and_focus_for_its_active_dot():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed to exercise payload tooltips")
    script = Path(__file__).parents[1] / "edge_ocp_ci_monitor/templates/dashboard.js"
    result = subprocess.run([node, "-e", r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function target() {
  const events = {};
  return {
    addEventListener(type, handler) { events[type] = handler; },
    emit(type, event) { events[type](event); },
    setAttribute() {}
  };
}
function dot(name) {
  return Object.assign(target(), {
    classList: {add() {}},
    removeAttribute() {},
    querySelector() { return {cloneNode() { return {childNodes: [name]}; }}; },
    getBoundingClientRect() { return {left: 100, top: 100, bottom: 120, width: 20}; }
  });
}
const first = dot('first'), second = dot('second');
const tip = Object.assign(target(), {
  style: {}, offsetWidth: 100, offsetHeight: 30,
  replaceChildren(name) { this.content = name; }
});
const setup = [], timers = new Map();
const document = target();
const addDocumentListener = document.addEventListener;
Object.assign(document, {
  addEventListener(type, handler) {
    if (type === 'DOMContentLoaded') setup.push(handler);
    else addDocumentListener(type, handler);
  },
  querySelectorAll() { return [first, second]; },
  createElement() { return tip; },
  body: {appendChild() {}}
});
let timerId = 0;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  document, window: Object.assign(target(), {innerWidth: 500, innerHeight: 500}),
  setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
  clearTimeout(id) { timers.delete(id); }
});
setup.find(handler => handler.name === 'setupPayloadTooltips')();
function runTimers() {
  const callbacks = [...timers.values()];
  timers.clear();
  callbacks.forEach(callback => callback());
}

first.emit('focus');
first.emit('mouseenter');
first.emit('mouseleave');
runTimers();
assert.equal(tip.hidden, false, 'leaving a focused dot keeps its tooltip');

first.emit('mouseenter');
first.emit('blur');
runTimers();
assert.equal(tip.hidden, false, 'blurring a hovered dot keeps its tooltip');
first.emit('mouseleave');
runTimers();
assert.equal(tip.hidden, true);

first.emit('focus');
second.emit('mouseenter');
first.emit('blur');
runTimers();
assert.equal(tip.hidden, false, 'blurring an inactive dot leaves the active tooltip alone');
assert.equal(tip.content, 'second');

second.emit('mouseleave');
tip.emit('mouseenter');
runTimers();
assert.equal(tip.hidden, false, 'the pointer can move into the tooltip');
tip.emit('mouseleave');
runTimers();
assert.equal(tip.hidden, true);

second.emit('focus');
tip.emit('mouseenter');
tip.emit('mouseleave');
runTimers();
assert.equal(tip.hidden, false, 'leaving the tooltip keeps it open for a focused dot');
second.emit('mouseenter');
document.emit('keydown', {key: 'Escape'});
runTimers();
assert.equal(tip.hidden, true, 'Escape dismisses even a focused and hovered tooltip');
""", str(script)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_sippy_import_replaces_manual_question_with_analysis(report, tmp_path):
    finding = report.streams[0].regressions[0]
    html_path = tmp_path / "report.html"
    generate_html(report, html_path)
    row_pattern = r'<(?:div|details) id="sippy-row-1".*?<!-- /sippy-job -->'
    row = re.search(row_pattern, html_path.read_text(), re.DOTALL)[0]
    assert f"Why is {finding.test_name} unhealthy on {finding.version}?" in row
    assert "View AI analysis" not in row

    source = tmp_path / "analysis.json"
    source.write_text(json.dumps({"by_sippy_job": {finding.analysis_key: asdict(diagnosis())}}))
    patch_analysis_html(html_path, source)
    patch_analysis_html(html_path, source)
    row = re.search(row_pattern, html_path.read_text(), re.DOTALL)[0]
    assert finding.triage_url in Links(row).hrefs
    assert row.count("View AI analysis") == 1
    assert 'aria-controls="sippy-analysis-1"' in row
    assert 'id="sippy-analysis-1"' in row
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


def test_findings_queue_links_each_job_to_its_own_record(report):
    report.escalation_risks = []
    other = deepcopy(report.streams[0])
    other.version = "4.20"
    other.payloads[0].jobs[0].prow_url = "https://prow.example/other-version"
    other.regressions[0].version = "4.20"
    report.streams.append(other)

    html = generate_html(report)
    references = re.findall(r'data-finding-source="([^"]+)"', html)
    # Every summary action must lead to its own full record, including data-only Sippy rows.
    assert len(references) == len(set(references)) == 4
    assert all(f'id="{reference}"' in html for reference in references)
    assert {"sippy-row-1", "sippy-row-2"} <= set(references)
    assert sum(reference.startswith("detail-") for reference in references) == 2
    assert ">informing-job</button>" not in html


def test_overview_limits_by_severity_then_orders_latest_failures_first(report):
    stream = report.streams[0]
    stream.payloads = []
    for day in range(1, 8):
        name = "recurring" if day <= 2 else f"blocking-{day}"
        job = JobRun(name, f"https://prow.example/{day}", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        tag = f"4.19.0-0.nightly-2026-11-{day:02d}-070000"
        stream.payloads.append(Payload(tag, stream.stream, stream.version, PayloadStatus.REJECTED, jobs=[job]))
    stream.regressions = [
        Regression(f"sippy-{index}", "", "", "", 80, (index + 1) * 5, topology="SNO", current_runs=5,
                   version=stream.version, latest_failure_ms=timestamp)
        for index, timestamp in enumerate((200, 500, 400, 0, 600, 1000))
    ]

    html = generate_html(report)
    for section, expected in (
        ("payload", ["blocking-7", "blocking-6", "blocking-5", "blocking-4", "recurring"]),
        ("sippy", ["sippy-4", "sippy-1", "sippy-2", "sippy-0", "sippy-3"]),
    ):
        markup = re.search(rf'<section[^>]*data-findings-section="{section}"[^>]*>(.*?)</section>', html, re.DOTALL)[1]
        assert re.findall(r'<button[^>]*class="finding-job"[^>]*>([^<]+)</button>', markup) == expected
        if section == "payload":
            assert re.findall(r'data-finding-source="([^"]+)"', markup)[-1] == "detail-2"


@pytest.mark.parametrize("scheme", ["hTtP", "hTtPs"])
def test_progress_redacts_credentials_and_signatures_in_mixed_case_urls(caplog, scheme):
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


def test_import_replaces_only_matching_blocking_card_and_remains_repeatable(report, tmp_path):
    job, other = report.streams[0].payloads[0].jobs
    other.deep_analysis = diagnosis("Unrelated diagnosis must stay unchanged")
    job.deep_analysis = diagnosis()
    html_path = tmp_path / "report.html"
    source = tmp_path / "analysis.json"
    generate_html(report, html_path)
    for cause in ("Corrected diagnosis", "Second correction"):
        source.write_text(json.dumps({"by_prow_url": {job.prow_url: asdict(diagnosis(cause))}}))
        patch_analysis_html(html_path, source)
        html = html_path.read_text()
        assert cause in html
        assert "Original diagnosis" not in html
        assert "Analysis covers all 2 attempts" in html
        assert html.count('class="deep-analysis-card"') == 2
        assert "Unrelated diagnosis must stay unchanged" in html
    assert "Corrected diagnosis" not in html


SAFE_ATTEMPT = {
    "prow_url": "hTtPs://prow.example/valid?job=a&attempt=2",
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


def test_report_analysis_links_jira_references_and_escapes_markup(report):
    analysis = diagnosis('See OCPBUGS-123; <img src=x onerror="alert(1)"> & ETCD-9.')
    analysis.impact = "Tracks https://redhat.atlassian.net/browse/OCPBUGS-222."
    analysis.recommendation = (
        "Follow [the PHC bug](https://redhat.atlassian.net/browse/OCPBUGS-456) "
        "and https://issues.redhat.com/browse/CNF-7."
    )
    analysis.same_root_cause = False
    analysis.attempt_analyses = [AttemptAnalysis(**{**SAFE_ATTEMPT, "root_cause": "Related to OCPBUGS-789."})]
    report.streams[0].payloads[0].jobs[0].deep_analysis = analysis
    html = generate_html(report)
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


def test_renderer_keeps_safe_attempts_and_drops_unsafe_or_malformed_records():
    raw = asdict(diagnosis())
    raw.update(same_root_cause=False, attempt_analyses=[
        SAFE_ATTEMPT,
        *[{"prow_url": url, "root_cause": f"Unsafe attempt {i}", "failure_type": "Platform issue"}
          for i, url in enumerate(UNSAFE_URLS)],
        None, {}, {"prow_url": 123},
    ])
    html = _render_analysis_card(raw)
    assert Links(html).hrefs == [SAFE_ATTEMPT["prow_url"]]
    assert SAFE_ATTEMPT["root_cause"] in html
    assert "Unsafe attempt" not in html


def test_retry_evidence_prevents_all_healthy_banner(report):
    stream = report.streams[0]
    stream.regressions = []
    report.escalation_risks = []
    job = stream.payloads[0].jobs[0]
    stream.payloads[0].jobs = [job]
    job.result = JobResult.SUCCESS
    html = generate_html(report)
    assert "All edge jobs are passing across all monitored payloads" not in html
    analysis_tab = html.split('id="tab-failure-analysis"', 1)[1].split('<!-- /tab-failure-analysis -->', 1)[0]
    assert "Warning - Passed After Retry" in analysis_tab
    assert {job.prow_url, job.previous_attempts[0].prow_url} <= set(Links(analysis_tab).hrefs)
