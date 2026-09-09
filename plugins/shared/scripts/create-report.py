#!/usr/bin/env python3
"""
Generate an HTML report from CI analysis JSON files.

Shared across components (MicroShift, LVMS, etc.) via symlinks in each
plugin's scripts/ directory.

Usage:
    create-report.py --component <component> [--workdir DIR] <release1,release2,...>
"""

import json
import sys
import os
import re
import glob as glob_mod
from datetime import datetime, timezone
from filter_images import tag_matches_release


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Threshold for fuzzy matching issue titles to bug candidate signatures.
# Uses asymmetric formula: overlap / len(sig_tokens) — measures what fraction
# of the bug candidate's signature is covered by the issue title. This differs
# from the symmetric min-based formula in aggregate.py/search-bugs.py because
# issue titles are short summaries while signatures are detailed.
MATCH_THRESHOLD = 0.50

STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by",
    "is", "was", "are", "were", "be", "been", "and", "or", "not", "no",
    "but", "from", "that", "this", "all", "has", "have", "had", "do",
    "does", "did", "will", "would", "could", "should", "may", "might",
})

COMPONENT_TITLES = {
    "microshift": "MicroShift",
    "lvm-operator": "LVMS",
}

JIRA_BASE = "https://issues.redhat.com"

# Per-component settings for the "Create Bug in JIRA" button shown next to
# every issue for quick manual bug filing. Fields mirror what the component's
# create-bugs skill would use, so manually filed bugs stay trackable by the
# existing tooling (Bugs tab query, close-stale-bugs). Components without an
# entry get no button.
COMPONENT_JIRA_CREATE = {
    "microshift": {
        "pid": "10417",
        "issuetype": "10016",
        "component": "83330",
        "labels": "microshift-ci-ai-generated",
        "summary_prefix": "MicroShift CI: ",
        "reporter": "712020:dc2a5866-d3bd-4f61-a413-4daef5b032b7",
    },
}


_GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "F": 4}
_GRADE_CSS = {"A": "grade-a", "B": "grade-b", "C": "grade-c", "D": "grade-d", "F": "grade-f"}

CSS = """\
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; color: #333; }
        .container { max-width: 1200px; margin: 0 auto; transition: max-width 0.2s; }
        .container.wide { max-width: 1800px; }
        h1 { color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 8px; font-size: 1.4em; margin: 10px 0; }
        h2 { font-size: 1.15em; margin: 0; }
        h3 { font-size: 1.05em; margin: 0 0 8px 0; }
        .release-section h3 { margin: 18px 0 4px 0; }
        .release-section { background: white; border-radius: 8px; padding: 15px; margin: 15px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .release-header { display: flex; justify-content: space-between; align-items: center; }
        .release-header h2 { color: #16213e; margin: 0; }
        .diagnostics-banner { background: #fff3cd; border: 1px solid #ffc107; border-radius: 8px; padding: 12px 16px; margin: 10px 0 15px 0; }
        .diagnostics-banner summary { font-weight: 600; color: #856404; cursor: pointer; font-size: 0.95em; }
        .diagnostics-banner pre { margin: 8px 0 0 0; white-space: pre-wrap; font-size: 0.85em; color: #533f03; }
        .badge { padding: 4px 12px; border-radius: 12px; font-size: 0.85em; font-weight: 600; }
        .badge-ok { background: #d4edda; color: #155724; }
        .badge-issues { background: #fff3cd; color: #856404; }
        .badge-critical { background: #f8d7da; color: #721c24; }
        .badge-nodata { background: #e2e3e5; color: #383d41; }
        .root-cause { background: #fff8e1; border-left: 3px solid #ffc107; padding: 8px 12px; margin: 8px 0; font-size: 0.9em; }
        .status-pass { color: #28a745; }
        .status-fail { color: #dc3545; }
        .overview-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; margin: 15px 0; }
        .overview-card { background: white; border-radius: 8px; padding: 12px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .overview-card .number { font-size: 1.6em; font-weight: 700; }
        .overview-card .label { color: #6c757d; font-size: 0.9em; }
        .job-date { font-weight: 400; color: #6c757d; font-size: 0.85em; }
        .issues-table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        .issues-table td { padding: 5px 6px; vertical-align: middle; }
        .issues-table .col-link { width: 24px; text-align: center; }
        .issues-table .col-sev { width: 78px; }
        .issues-table .col-ftype { width: 58px; }
        .issues-table .col-title { cursor: pointer; user-select: none; }
        .issues-table .col-title::before { content: '\\25B6  '; font-size: 0.7em; color: #6c757d; }
        .issues-table .col-title.active::before { content: '\\25BC  '; }
        .issues-table .col-jobs { width: 70px; text-align: center; color: #6c757d; font-size: 0.85em; white-space: nowrap; }
        .issues-table .detail-row td { padding: 0 6px 12px 40px; }
        .issues-table .detail-row { display: none; }
        .issues-table .detail-row.show { display: table-row; }
        .issues-table tr.issue-row { border-top: 1px solid #eee; }
        .issues-table tr.issue-row:first-child { border-top: none; }
        .bug-links { margin: 8px 0; padding: 8px 12px; background: #f0f4ff; border-left: 3px solid #0366d6; font-size: 0.9em; }
        .bug-links .bug-tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; font-weight: 600; margin: 2px 4px 2px 0; text-decoration: none; }
        .bug-tag-open { background: #fff3cd; color: #856404; border: 1px solid #ffc107; }
        .bug-tag-regression { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
        .create-bug-btn { background: #198754; color: #fff; border: 1px solid #157347; }
        .no-bugs { color: #6c757d; font-style: italic; font-size: 0.85em; }
        .toc { background: white; border-radius: 8px; padding: 15px; margin: 15px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .toc ul { list-style: none; padding-left: 0; }
        .toc li { padding: 5px 0; }
        .toc a { color: #0366d6; text-decoration: none; }
        .toc a:hover { text-decoration: underline; }
        .toc-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
        .filter-toggle { cursor: pointer; user-select: none; font-size: 0.9em; color: #6c757d; font-weight: 400; }
        .filter-toggle input[type="checkbox"] { margin-right: 5px; vertical-align: middle; }
        .timestamp { color: #6c757d; font-size: 0.9em; }
        a { color: #0366d6; }
        .tab-bar { display: flex; gap: 0; margin: 20px 0 0 0; border-bottom: 2px solid #dee2e6; }
        .tab-btn { padding: 12px 24px; border: none; background: transparent; font-size: 1em; font-weight: 600;
            color: #6c757d; cursor: pointer; border-bottom: 3px solid transparent;
            margin-bottom: -2px; transition: color 0.2s, border-color 0.2s; }
        .tab-btn:hover { color: #333; }
        .tab-btn.active { color: #e94560; border-bottom-color: #e94560; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .breakdown { display: flex; gap: 15px; margin: 10px 0; flex-wrap: wrap; }
        .breakdown-item { font-size: 0.9em; color: #495057; }
        .breakdown-item strong { color: #333; }
        .severity-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 700; text-transform: uppercase; }
        .severity-high { background: #f8d7da; color: #721c24; }
        .severity-medium { background: #fff3cd; color: #856404; }
        .severity-low { background: #d4edda; color: #155724; }
        .severity-critical { background: #721c24; color: #fff; }
        .ftype-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 700; text-transform: uppercase; }
        .ftype-test { background: #cce5ff; color: #004085; }
        .ftype-build { background: #e2d5f1; color: #4a235a; }
        .ftype-infra { background: #fde2cc; color: #7d4e24; }
        .confidence-badge { display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 0.7em; font-weight: 700; text-transform: uppercase; margin-left: 6px; vertical-align: middle; }
        .confidence-high { background: #d4edda; color: #155724; }
        .confidence-medium { background: #fff3cd; color: #856404; }
        .confidence-low { background: #f8d7da; color: #721c24; }
        .causal-chain { margin: 6px 0; }
        .causal-chain ol { margin: 4px 0 4px 20px; padding: 0; }
        .causal-chain li { margin: 2px 0; font-size: 0.9em; }
        .causal-chain .evidence { color: #6c757d; font-family: monospace; font-size: 0.9em; }
        .causal-chain code { background: #f8f9fa; padding: 1px 4px; border-radius: 3px; font-size: 0.85em; }
        .analysis-gaps { color: #6c757d; font-style: italic; font-size: 0.85em; margin: 4px 0; }
        .scenario-chip { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 0.78em; background: #e9ecef; color: #495057; margin: 0 3px 2px 0; font-family: monospace; }
        .graph-source { font-size: 0.8em; color: #6c757d; font-style: italic; margin-bottom: 4px; }
        .graph-toggle { cursor: pointer; text-decoration: none; font-size: 1em; margin-left: 4px; }
        .graph-toggle:hover { opacity: 0.7; }
        .perf-graphs { margin: 6px 0 6px 0; padding: 8px 12px; background: #f8f9fa; border-left: 3px solid #6c757d; }
        .pcp-chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 6px; }
        .pcp-chart-card { background: #fff; border-radius: 8px; padding: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .pcp-chart-card h4 { font-size: 0.9em; color: #1a1a2e; margin: 0 0 6px 0; }
        .pcp-chart-card canvas { width: 100% !important; height: 240px !important; }
        .pcp-chart-card:fullscreen { display: flex; flex-direction: column; justify-content: center; padding: 32px; }
        .pcp-chart-card:fullscreen canvas { height: 70vh !important; }
        .pcp-fs-btn { background: none; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 1.1em; color: #6c757d; padding: 2px 6px; line-height: 1; }
        .pcp-fs-btn:hover { background: #f0f0f0; color: #333; }
        .pcp-stats-row { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 6px; font-size: 0.75em; color: #6c757d; }
        .pcp-stats-row .val { font-weight: 600; color: #333; }
        @media (max-width: 900px) { .pcp-chart-grid { grid-template-columns: 1fr; } }
        .anchor-link, .section-anchor { color: #adb5bd; text-decoration: none; cursor: pointer; }
        .anchor-link:hover, .section-anchor:hover { color: #0366d6; }
        .anchor-link { font-size: 0.85em; }
        .section-anchor { font-size: 0.75em; margin-left: 8px; vertical-align: middle; }
        .copy-toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #333; color: #fff; padding: 8px 16px; border-radius: 6px; font-size: 0.85em; z-index: 1000; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
        .copy-toast.show { opacity: 1; }
        .data-table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        .data-table th { text-align: left; padding: 8px 6px; border-bottom: 2px solid #dee2e6; font-size: 0.85em; color: #6c757d; text-transform: uppercase; cursor: pointer; user-select: none; white-space: nowrap; }
        .data-table th:hover { color: #333; }
        .data-table th:after { content: ' \\25B2\\25BC'; font-size: 0.7em; opacity: 0.35; letter-spacing: -2px; }
        .data-table th.sort-asc:after { content: ' \\25B2'; font-size: 0.8em; opacity: 1; color: #0d6efd; letter-spacing: normal; }
        .data-table th.sort-desc:after { content: ' \\25BC'; font-size: 0.8em; opacity: 1; color: #0d6efd; letter-spacing: normal; }
        .data-table td { padding: 6px; border-bottom: 1px solid #eee; font-size: 0.9em; vertical-align: middle; }
        .data-table tr:hover { background: #f8f9fa; }
        .link-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 700; text-transform: uppercase; }
        .link-badge-unlinked { background: #fff3cd; color: #856404; }
        .grade-badge { display: inline-block; padding: 2px 10px; border-radius: 4px; font-size: 0.85em; font-weight: 700; min-width: 24px; text-align: center; }
        .grade-a { background: #d4edda; color: #155724; }
        .grade-b { background: #cce5ff; color: #004085; }
        .grade-c { background: #fff3cd; color: #856404; }
        .grade-d { background: #f8d7da; color: #721c24; }
        .grade-f { background: #721c24; color: #fff; }
        .grade-na { background: #e2e3e5; color: #383d41; }
        .index-image-info { background: #e8f4fd; border-left: 3px solid #0366d6; padding: 8px 12px; margin: 8px 0; font-size: 0.9em; }
        .index-image-info code { background: #f1f1f1; padding: 2px 4px; border-radius: 3px; font-size: 0.9em; }
        .section-toggle { margin: 12px 0; }
        .section-toggle summary { font-size: 1.05em; font-weight: 600; cursor: pointer; padding: 6px 0; user-select: none; list-style: none; }
        .section-toggle summary::before { content: '\\25B6  '; font-size: 0.8em; color: #6c757d; }
        .section-toggle[open] summary::before { content: '\\25BC  '; }
        .section-toggle summary::-webkit-details-marker { display: none; }
        .filter-bar { display: flex; align-items: center; gap: 12px; margin: 12px 0 0 0; }
        .filter-bar input[type="text"] { flex: 1; max-width: 360px; padding: 6px 12px; border: 1px solid #dee2e6; border-radius: 6px; font-size: 0.9em; outline: none; }
        .filter-bar input[type="text"]:focus { border-color: #e94560; box-shadow: 0 0 0 2px rgba(233,69,96,0.15); }
        .filter-bar .filter-count { font-size: 0.85em; color: #6c757d; white-space: nowrap; }
        .export-bar { display: inline-flex; gap: 6px; margin-left: auto; }
        .export-btn { padding: 5px 12px; border: 1px solid #dee2e6; border-radius: 6px; background: #fff; font-size: 0.85em; cursor: pointer; color: #495057; font-weight: 600; }
        .export-btn:hover { background: #f8f9fa; border-color: #adb5bd; }
        .release-section.side-by-side .section-panels { display: flex; gap: 20px; }
        .release-section.side-by-side .section-panels > .section-toggle { flex: 1; min-width: 0; }
        @media (max-width: 1200px) { .release-section.side-by-side .section-panels { flex-direction: column; } }"""

# ---------------------------------------------------------------------------
# Client-side JS renderer — builds all DOM from window.REPORT_DATA
# ---------------------------------------------------------------------------

JS = """\
(function() {
var D = window.REPORT_DATA;
if (!D) return;
var JIRA_BASE = 'https://issues.redhat.com';
var _gc = 0;

// === DOM helpers ===
function h(tag, cls, children) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (children == null) return e;
    if (typeof children === 'string') { e.textContent = children; return e; }
    if (children.nodeType) { e.appendChild(children); return e; }
    if (Array.isArray(children)) children.forEach(function(c) {
        if (c == null) return;
        e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return e;
}
function setAttr(el, obj) { for (var k in obj) if (obj[k] != null) el.setAttribute(k, obj[k]); return el; }
function makeLink(href, text, cls, target) {
    var a = h('a', cls || '', text);
    a.href = href;
    if (target) a.target = target;
    return a;
}
function anchorLink(id) {
    var a = makeLink('#' + id, '\\u{1F517}', 'anchor-link');
    a.title = 'Copy link';
    return a;
}
function sectionAnchor(id) {
    var a = makeLink('#' + id, '\\u{1F517}', 'section-anchor');
    a.title = 'Copy link to this section';
    return a;
}

// === Formatters ===
function fmtEpoch(v) {
    try { var d = new Date(parseInt(v, 10) * 1000); return d.toISOString().replace('T', ' ').substring(0, 16); }
    catch(e) { return String(v || ''); }
}
function fmtDuration(v) {
    try {
        var s = Math.floor(parseFloat(v));
        if (s >= 3600) return Math.floor(s/3600) + 'h ' + Math.floor((s%3600)/60) + 'm';
        return Math.floor(s/60) + 'm ' + (s%60) + 's';
    } catch(e) { return String(v || ''); }
}
function badgeClass(total, hasCritical) {
    if (total === 0) return 'badge-ok';
    if (total >= 5 || hasCritical) return 'badge-critical';
    return 'badge-issues';
}

// === JIRA bug URL builder ===
function jiraEscape(text) {
    return (text || '').replace(/[\\\\{}\\[\\]|*^~_]/g, '\\\\$&');
}
function createBugUrl(issue, sourceLabel) {
    var cfg = D.jira_cfg;
    if (!cfg) return null;
    var summary = ((cfg.summary_prefix || '') + (issue.title || '')).substring(0, 100);
    var rc = jiraEscape(issue.root_cause || '');
    var ns = jiraEscape(issue.next_steps || '');
    var sev = issue.severity || 'UNKNOWN';
    var ft = issue.failure_type || 'test';
    var conf = issue.confidence || '';
    var scenarios = issue.scenarios || [];
    var chain = issue.causal_chain || [];
    var jobs = (issue.affected_jobs || []).slice(0, 5);
    var lines = ['h2. Description of problem', '', 'CI job failures detected: ' + sourceLabel, '', rc || '', '',
        'h2. How reproducible', '', 'N/A', '', 'h2. Steps to Reproduce', '',
        '# Run the CI job(s) listed below', '# Observe failure in step: ' + ft, '',
        'h2. Expected results', '', 'CI job should pass successfully.', '',
        'h2. Additional info', '', '*Error Severity:* ' + sev];
    if (conf) lines.push('*Analysis confidence:* ' + conf);
    if (scenarios.length) lines.push('*Affected scenarios:* ' + scenarios.join(', '));
    lines.push('*Number of affected jobs:* ' + (issue.job_count || jobs.length));
    if (jobs.length) {
        var dates = jobs.map(function(j) { return j.date || ''; }).filter(Boolean).sort();
        if (dates.length) lines.push('*Last observed:* ' + dates[dates.length - 1]);
    }
    if (chain.length) {
        lines.push(''); lines.push('*Root cause chain:*');
        chain.forEach(function(link) { if (link && link.cause) lines.push('# ' + jiraEscape(link.cause)); });
    }
    if (ns) { lines.push(''); lines.push('*Remediation:* ' + ns); }
    if (jobs.length) {
        lines.push(''); lines.push('*Affected Jobs:*');
        jobs.forEach(function(j) {
            var n = j.name || 'unknown';
            lines.push(j.url ? '- [' + n + '|' + j.url + ']' : '- ' + n);
        });
    }
    lines.push(''); lines.push('Prefilled by the CI Doctor report.');
    var params = {pid: cfg.pid, issuetype: cfg.issuetype, components: cfg.component,
        labels: cfg.labels, reporter: cfg.reporter || '', summary: summary, description: lines.join('\\n')};
    var qs = Object.keys(params).filter(function(k) { return params[k]; }).map(function(k) {
        return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
    }).join('&');
    var url = JIRA_BASE + '/secure/CreateIssueDetails!init.jspa?' + qs;
    if (url.length > 3800 && params.description) {
        var over = url.length - 3800;
        var desc = params.description;
        var suffix = '\\n\\n(truncated \\u2014 open the bug to add more detail)';
        params.description = desc.substring(0, Math.max(0, desc.length - over - suffix.length)) + suffix;
        qs = Object.keys(params).filter(function(k) { return params[k]; }).map(function(k) {
            return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]);
        }).join('&');
        url = JIRA_BASE + '/secure/CreateIssueDetails!init.jspa?' + qs;
    }
    return url;
}

// === Bug links rendering ===
function renderBugLinks(parent, bugMatch, issue, sourceLabel) {
    var div = h('div', 'bug-links');
    var url = createBugUrl(issue, sourceLabel);
    if (url) {
        var btn = makeLink(url, '+ Create Bug in JIRA', 'bug-tag create-bug-btn', '_blank');
        div.appendChild(btn);
    }
    var hasDups = bugMatch && bugMatch.duplicates && bugMatch.duplicates.length;
    var hasRegs = bugMatch && bugMatch.regressions && bugMatch.regressions.length;
    if (!hasDups && !hasRegs) {
        if (url) div.appendChild(document.createElement('br'));
        var nb = h('span', 'no-bugs', 'No tracked bugs');
        div.appendChild(nb);
        parent.appendChild(div); return;
    }
    if (url) div.appendChild(document.createElement('br'));
    if (hasDups) {
        div.appendChild(h('strong', '', 'Bugs:')); div.appendChild(document.createElement('br'));
        bugMatch.duplicates.forEach(function(d) {
            var a = makeLink(JIRA_BASE + '/browse/' + d.key, d.key, 'bug-tag bug-tag-open', '_blank');
            div.appendChild(a);
            var info = ' ' + (d.summary || '') + ' (' + (d.status || '');
            if (d.assignee) info += ', ' + d.assignee;
            info += ')';
            var sp = h('span', 'job-date', info); div.appendChild(sp);
            div.appendChild(document.createElement('br'));
        });
    }
    if (hasRegs) {
        div.appendChild(h('strong', '', 'Regressions:')); div.appendChild(document.createElement('br'));
        bugMatch.regressions.forEach(function(r) {
            var a = makeLink(JIRA_BASE + '/browse/' + r.key, r.key + ' \\u27F2', 'bug-tag bug-tag-regression', '_blank');
            div.appendChild(a);
            var info = ' ' + (r.summary || '') + ' (' + (r.status || '');
            if (r.assignee) info += ', ' + r.assignee;
            info += ')';
            div.appendChild(h('span', 'job-date', info));
            div.appendChild(document.createElement('br'));
        });
    }
    parent.appendChild(div);
}

// === Investigation rendering ===
function renderInvestigation(parent, issue) {
    var scenarios = issue.scenarios || [];
    if (scenarios.length) {
        var sd = h('div', 'scenarios');
        sd.appendChild(h('strong', '', 'Scenarios: '));
        scenarios.forEach(function(s) { sd.appendChild(h('span', 'scenario-chip', s)); });
        parent.appendChild(sd);
    }
    var chain = (issue.causal_chain || []).filter(function(l) { return l && l.cause; });
    if (chain.length) {
        var cd = h('div', 'causal-chain');
        cd.appendChild(h('strong', '', 'Causal chain:'));
        var ol = document.createElement('ol');
        chain.forEach(function(link) {
            var li = document.createElement('li');
            li.appendChild(document.createTextNode(link.cause));
            if (link.evidence) { li.appendChild(document.createTextNode(' \\u2014 ')); var ev = h('span', 'evidence', link.evidence); li.appendChild(ev); }
            if (link.quote) { li.appendChild(document.createTextNode(' ')); li.appendChild(h('code', '', link.quote)); }
            ol.appendChild(li);
        });
        cd.appendChild(ol); parent.appendChild(cd);
    }
    var gaps = (issue.analysis_gaps || []).filter(Boolean);
    if (gaps.length) parent.appendChild(h('div', 'analysis-gaps', 'Evidence gaps: ' + gaps.join(', ')));
}

// === Affected jobs list with PCP charts ===
function renderAffectedJobs(parent, jobs) {
    if (!jobs || !jobs.length) return;
    parent.appendChild(h('p', '', h('strong', '', 'Affected Jobs:')));
    var ul = document.createElement('ul');
    jobs.forEach(function(job) {
        var li = document.createElement('li');
        var dateSpan = h('span', 'job-date', '[' + (job.date || '') + ']');
        li.appendChild(dateSpan); li.appendChild(document.createTextNode(' '));
        if (job.url) { li.appendChild(makeLink(job.url, job.name || '', '', '_blank')); }
        else { li.appendChild(document.createTextNode(job.name || '')); }
        // PCP chart toggle
        if (job.metrics && typeof pcpCharts !== 'undefined') {
            _gc++;
            var gid = 'gp' + _gc;
            var toggle = document.createElement('a');
            toggle.className = 'graph-toggle'; toggle.textContent = '\\u{1F4CA}';
            toggle.title = 'Host performance graphs'; toggle.href = 'javascript:void(0)';
            var panel = h('div', 'perf-graphs');
            panel.id = gid; panel.style.display = 'none';
            panel.appendChild(h('div', 'graph-source', 'Host metrics (PCP)'));
            var grid = h('div', 'pcp-chart-grid');
            panel.appendChild(grid);
            (function(pid, m, g) {
                toggle.addEventListener('click', function() {
                    var el = document.getElementById(pid);
                    if (!el) return;
                    var show = el.style.display === 'none';
                    el.style.display = show ? 'block' : 'none';
                    if (show && !el.dataset.rendered) {
                        el.dataset.rendered = '1';
                        pcpCharts.init({cardClass:'pcp-chart-card',headingTag:'h4',statsClass:'pcp-stats-row'});
                        if (m.cpu) pcpCharts.renderCpu(g, m.cpu);
                        if (m.mem) pcpCharts.renderMem(g, m.mem);
                        if (m.io) pcpCharts.renderIo(g, m.io);
                        if (m.disk) pcpCharts.renderDisk(g, m.disk);
                    }
                });
            })(gid, job.metrics, grid);
            li.appendChild(document.createTextNode(' ')); li.appendChild(toggle);
            li.appendChild(panel);
        }
        ul.appendChild(li);
    });
    parent.appendChild(ul);
}

// === Issue table rendering (shared by periodics + PRs) ===
function renderIssueTable(parent, issues, anchorPrefix, sourceLabel) {
    if (!issues || !issues.length) return;
    var table = h('table', 'issues-table');
    issues.forEach(function(issue) {
        var jc = issue.job_count || 0;
        var sev = (issue.severity || 'UNKNOWN').toUpperCase();
        var sevCss = ({HIGH:1,MEDIUM:1,LOW:1,CRITICAL:1})[sev] ? 'severity-' + sev.toLowerCase() : '';
        var ftype = issue.failure_type || 'test';
        var ftypeLabel = ftype === 'infrastructure' ? 'INFRA' : ftype.toUpperCase();
        var ftypeCss = ftype === 'infrastructure' ? 'ftype-infra' : 'ftype-' + ftype;
        var jobDates = [];
        (issue.affected_jobs || []).forEach(function(j) { if (j.date) jobDates.push(j.date.substring(0, 10)); });
        jobDates = jobDates.filter(function(v, i, a) { return a.indexOf(v) === i; }).sort();
        var anchorId = anchorPrefix + '-' + issue.number;
        // Issue row
        var tr = h('tr', 'issue-row');
        tr.id = anchorId;
        if (jobDates.length) tr.setAttribute('data-dates', jobDates.join(' '));
        var tdSev = h('td', 'col-sev', h('span', 'severity-badge ' + sevCss, sev));
        var tdFtype = h('td', 'col-ftype', h('span', 'ftype-badge ' + ftypeCss, ftypeLabel));
        var tdTitle = h('td', 'col-title', issue.title || '');
        var tdJobs = h('td', 'col-jobs', jc + ' ' + (jc === 1 ? 'job' : 'jobs'));
        var tdLink = h('td', 'col-link', anchorLink(anchorId));
        tr.appendChild(tdSev); tr.appendChild(tdFtype); tr.appendChild(tdTitle);
        tr.appendChild(tdJobs); tr.appendChild(tdLink);
        table.appendChild(tr);
        // Expand/collapse
        tdTitle.addEventListener('click', function() {
            this.classList.toggle('active');
            var next = this.closest('tr').nextElementSibling;
            if (next && next.classList.contains('detail-row')) next.classList.toggle('show');
        });
        // Detail row
        var dtr = h('tr', 'detail-row');
        var dtd = document.createElement('td'); dtd.colSpan = 5;
        if (issue.root_cause) {
            var rc = h('div', 'root-cause');
            rc.appendChild(h('strong', '', 'Root Cause: '));
            var conf = (issue.confidence || '').toLowerCase();
            if (['high','medium','low'].indexOf(conf) !== -1) {
                var cb = h('span', 'confidence-badge confidence-' + conf, conf);
                cb.title = 'Root cause analysis confidence';
                rc.appendChild(cb); rc.appendChild(document.createTextNode(' '));
            }
            rc.appendChild(document.createTextNode(issue.root_cause));
            dtd.appendChild(rc);
        }
        renderInvestigation(dtd, issue);
        renderBugLinks(dtd, issue.bug_match, issue, sourceLabel);
        renderAffectedJobs(dtd, issue.affected_jobs);
        if (issue.next_steps) {
            var p = document.createElement('p');
            p.appendChild(h('em', '', 'Next Steps: ')); p.appendChild(document.createTextNode(issue.next_steps));
            dtd.appendChild(p);
        }
        dtr.appendChild(dtd); table.appendChild(dtr);
    });
    parent.appendChild(table);
}

// === Index image info (LVMS-specific) ===
function renderIndexImage(parent, info) {
    if (!info) return;
    var div = h('div', 'index-image-info');
    if (info.image) { div.appendChild(h('strong', '', 'Catalog Index Image: ')); div.appendChild(h('code', '', info.image)); div.appendChild(document.createElement('br')); }
    if (info.digest) { div.appendChild(h('strong', '', 'Digest: ')); div.appendChild(h('code', '', info.digest)); div.appendChild(document.createElement('br')); }
    if (info.built) { div.appendChild(h('strong', '', 'Built: ')); div.appendChild(document.createTextNode(info.built)); div.appendChild(document.createElement('br')); }
    if (info.commit) {
        var short = info.commit.length >= 12 ? info.commit.substring(0, 12) : info.commit;
        div.appendChild(h('strong', '', 'Source Commit: '));
        div.appendChild(makeLink('https://github.com/openshift/lvm-operator/commit/' + info.commit, short, '', '_blank'));
    }
    if (info.error) {
        div.appendChild(document.createElement('br'));
        var em = h('em', '', 'Inspect failed: ' + info.error);
        em.style.color = '#856404'; div.appendChild(em);
    }
    parent.appendChild(div);
}

// === Build job-issue map for cross-referencing status table → issues ===
function buildJobIssueMap() {
    var result = {};
    var rd = D.releases_data || {};
    for (var ver in rd) {
        if (!rd[ver] || !rd[ver].issues) continue;
        rd[ver].issues.forEach(function(issue) {
            var anchor = 'release-' + ver + '-' + issue.number;
            (issue.affected_jobs || []).forEach(function(j) {
                if (j.name) {
                    if (!result[j.name]) result[j.name] = [];
                    result[j.name].push({anchor: anchor, title: issue.title || ''});
                }
            });
        });
    }
    return result;
}

// === Diagnostics banner ===
function renderDiagnostics(parent) {
    var text = D.diagnostics_text;
    if (!text || !text.trim()) return;
    var details = document.createElement('details');
    details.className = 'diagnostics-banner';
    var summary = document.createElement('summary');
    summary.textContent = 'Pipeline Diagnostics';
    details.appendChild(summary);
    var pre = document.createElement('pre');
    pre.textContent = text.trim();
    details.appendChild(pre);
    parent.appendChild(details);
}

// === Overview cards ===
function renderOverview(parent) {
    var rd = D.releases_data || {};
    var sd = D.status_data || {};
    var versions = Object.keys(rd);
    versions.forEach(function(ver) {
        var rdata = rd[ver]; var status = sd[ver];
        var card = h('div', 'overview-card');
        var numDiv = h('div', 'number');
        var sub = '';
        if (rdata && rdata.collection_error) { numDiv.textContent = '!'; numDiv.className = 'number status-fail'; }
        else if (rdata) {
            var failed = rdata.total_failed;
            numDiv.className = 'number ' + (failed > 0 ? 'status-fail' : 'status-pass');
            if (status) {
                var total = status.length, passed = status.filter(function(j){return j.status==='success';}).length;
                var rate = total > 0 ? Math.round(passed/total*100) : 0;
                numDiv.innerHTML = failed + '<span style="font-size:0.5em;font-weight:400;color:#6c757d">/' + total + '</span>';
                sub = rate + '% pass rate';
            } else { numDiv.textContent = String(failed); }
        } else { numDiv.textContent = '?'; }
        card.appendChild(numDiv);
        card.appendChild(h('div', 'label', 'Release ' + ver));
        if (sub) { var sd2 = document.createElement('div'); sd2.style.cssText = 'font-size:0.8em;color:#6c757d'; sd2.textContent = sub; card.appendChild(sd2); }
        parent.appendChild(card);
    });
    // PR overview card
    var prCard = h('div', 'overview-card');
    var prNum = h('div', 'number');
    if (D.pr_error) { prNum.textContent = '!'; prNum.className = 'number status-fail'; }
    else if (D.pr_status) {
        var f = D.pr_status.reduce(function(a,p){return a+(p.failed||0);},0);
        prNum.textContent = String(f); prNum.className = 'number ' + (f > 0 ? 'status-fail' : 'status-pass');
    } else if (D.pr_data) {
        var f2 = D.pr_data.total_failed || 0;
        prNum.textContent = String(f2); prNum.className = 'number ' + (f2 > 0 ? 'status-fail' : 'status-pass');
    } else { prNum.textContent = '0'; prNum.className = 'number status-pass'; }
    prCard.appendChild(prNum); prCard.appendChild(h('div', 'label', 'Pull Requests'));
    parent.appendChild(prCard);
}

// === Periodics tab ===
function renderPeriodics(parent) {
    var rd = D.releases_data || {};
    var sd = D.status_data || {};
    var versions = Object.keys(rd);
    var jim = buildJobIssueMap();
    // TOC
    var toc = h('div', 'toc');
    var tocHeader = h('div', 'toc-header');
    tocHeader.appendChild(h('h3', '', 'Table of Contents'));
    var todayLabel = document.createElement('label'); todayLabel.className = 'filter-toggle';
    var todayCb = document.createElement('input'); todayCb.type = 'checkbox'; todayCb.id = 'filter-today';
    todayLabel.appendChild(todayCb); todayLabel.appendChild(document.createTextNode(' Today only'));
    tocHeader.appendChild(todayLabel);
    var sbsLabel = document.createElement('label'); sbsLabel.className = 'filter-toggle';
    var sbsCb = document.createElement('input'); sbsCb.type = 'checkbox'; sbsCb.id = 'toggle-side-by-side';
    sbsLabel.appendChild(sbsCb); sbsLabel.appendChild(document.createTextNode(' Side by side'));
    tocHeader.appendChild(sbsLabel);
    toc.appendChild(tocHeader);
    var tocUl = document.createElement('ul');
    versions.forEach(function(ver) {
        var rdata = rd[ver]; var status = sd[ver];
        var li = document.createElement('li');
        li.appendChild(makeLink('#release-' + ver, 'Release ' + ver));
        if (rdata && rdata.collection_error) {
            li.appendChild(document.createTextNode(' \\u2014 collection error'));
        } else if (rdata) {
            var b = rdata.breakdown || {};
            var passInfo = '';
            if (status) {
                var total = status.length, passed = status.filter(function(j){return j.status==='success';}).length;
                passInfo = ' \\u2014 ' + passed + '/' + total + ' passed (' + Math.round(passed/total*100) + '%)';
            }
            var countsSpan = h('span', '', rdata.total_failed + ' failures (' + (b.build||0) + ' build, ' + (b.test||0) + ' test, ' + (b.infrastructure||0) + ' infra)' + passInfo);
            countsSpan.className = 'toc-counts';
            countsSpan.setAttribute('data-release', ver);
            li.appendChild(document.createTextNode(' \\u2014 ')); li.appendChild(countsSpan);
        } else { li.appendChild(document.createTextNode(' \\u2014 no data')); }
        tocUl.appendChild(li);
    });
    toc.appendChild(tocUl);
    parent.appendChild(toc);
    // Release sections
    versions.forEach(function(ver) {
        renderReleaseSection(parent, ver, rd[ver], sd[ver], jim);
    });
    // Event handlers
    todayCb.addEventListener('change', function() { filterToday(this.checked); });
    sbsCb.addEventListener('change', function() { toggleSideBySide(this.checked); });
}

function renderReleaseSection(parent, version, rdata, status, jim) {
    var sec = h('div', 'release-section');
    sec.id = 'release-' + version;
    if (!rdata) {
        var hdr = h('div', 'release-header');
        hdr.appendChild(h('h2', '', 'Release ' + version));
        hdr.appendChild(h('span', 'badge badge-nodata', 'no data'));
        sec.appendChild(hdr);
        sec.appendChild(h('p', '', 'Analysis failed to produce results.'));
        parent.appendChild(sec); return;
    }
    if (rdata.collection_error) {
        var hdr2 = h('div', 'release-header');
        hdr2.appendChild(h('h2', '', 'Release ' + version));
        hdr2.appendChild(h('span', 'badge badge-nodata', 'collection error'));
        sec.appendChild(hdr2);
        var pre = document.createElement('pre');
        pre.textContent = 'Data collection failed: ' + rdata.collection_error;
        sec.appendChild(pre); parent.appendChild(sec); return;
    }
    var total = rdata.total_failed;
    var hasCrit = (rdata.issues || []).some(function(i) { return (i.severity || '').toUpperCase() === 'CRITICAL'; });
    var label = total === 1 ? 'failure' : 'failures';
    var hdr3 = h('div', 'release-header');
    var heading = h('h2', '', ['Release ' + version]);
    heading.appendChild(sectionAnchor('release-' + version));
    hdr3.appendChild(heading);
    var badge = h('span', 'badge ' + badgeClass(total, hasCrit) + ' release-badge', total + ' ' + label);
    badge.setAttribute('data-release', version);
    hdr3.appendChild(badge);
    sec.appendChild(hdr3);
    // Index image
    var idxData = (D.index_data || {})[version];
    renderIndexImage(sec, idxData);
    // Breakdown
    var b = rdata.breakdown || {};
    var bd = h('div', 'breakdown');
    bd.appendChild(h('span', 'breakdown-item', [h('strong', 'bd-build', String(b.build || 0)), ' Build']));
    bd.appendChild(h('span', 'breakdown-item', [h('strong', 'bd-test', String(b.test || 0)), ' Test']));
    bd.appendChild(h('span', 'breakdown-item', [h('strong', 'bd-infra', String(b.infrastructure || 0)), ' Infrastructure']));
    sec.appendChild(bd);
    var panels = h('div', 'section-panels');
    // All Jobs table
    if (status && status.length) {
        var totalS = status.length, passedS = status.filter(function(j){return j.status==='success';}).length;
        var rateS = totalS > 0 ? Math.round(passedS/totalS*100) : 0;
        var rateCss = rateS >= 90 ? 'status-pass' : (rateS < 70 ? 'status-fail' : '');
        var jobsDet = document.createElement('details'); jobsDet.className = 'section-toggle';
        var jobsSum = document.createElement('summary');
        jobsSum.appendChild(document.createTextNode('All Jobs \\u2014 '));
        var rateSpan = h('span', rateCss, passedS + '/' + totalS + ' passed (' + rateS + '%)');
        jobsSum.appendChild(rateSpan);
        jobsDet.appendChild(jobsSum);
        var jTable = h('table', 'data-table');
        jTable.setAttribute('data-default-sort', '2,asc');
        var thead = document.createElement('thead');
        var thRow = document.createElement('tr');
        ['Status','Job Name','Finished','Duration','Issues'].forEach(function(t) {
            var th2 = document.createElement('th'); th2.textContent = t; thRow.appendChild(th2);
        });
        thead.appendChild(thRow); jTable.appendChild(thead);
        var tbody = document.createElement('tbody');
        var sorted = status.slice().sort(function(a,b) { return (a.finished||'').toString().localeCompare((b.finished||'').toString()); });
        sorted.forEach(function(sj) {
            var tr = document.createElement('tr');
            var st = sj.status || 'unknown';
            var stBadge = h('span', 'severity-badge', '');
            if (st === 'success') { stBadge.className = 'severity-badge severity-low'; stBadge.style.background = '#d4edda'; stBadge.style.color = '#155724'; stBadge.textContent = 'PASS'; }
            else if (st === 'failure') { stBadge.className = 'severity-badge severity-high'; stBadge.textContent = 'FAIL'; }
            else if (st === 'pending') { stBadge.className = 'severity-badge'; stBadge.style.background = '#cce5ff'; stBadge.style.color = '#004085'; stBadge.textContent = 'RUNNING'; }
            else { stBadge.className = 'severity-badge'; stBadge.style.background = '#e2e3e5'; stBadge.style.color = '#383d41'; stBadge.textContent = st.toUpperCase(); }
            tr.appendChild(h('td', '', stBadge));
            var nameTd = document.createElement('td');
            if (sj.url) { nameTd.appendChild(makeLink(sj.url, sj.job || '', '', '_blank')); }
            else { nameTd.textContent = sj.job || ''; }
            tr.appendChild(nameTd);
            tr.appendChild(h('td', '', fmtEpoch(sj.finished)));
            tr.appendChild(h('td', '', fmtDuration(sj.duration)));
            // Issues cross-reference
            var issTd = document.createElement('td'); issTd.style.fontSize = '0.85em';
            var refs = jim[sj.job || ''];
            if (refs && refs.length) {
                var refUl = document.createElement('ul');
                refUl.style.cssText = 'margin:0;padding-left:1.2em;display:flex;flex-direction:column;gap:4px';
                refs.forEach(function(ref) {
                    var rli = document.createElement('li');
                    var short = ref.title.length > 60 ? ref.title.substring(0, 60) + '...' : ref.title;
                    var ra = makeLink('#' + ref.anchor, short, 'issue-ref');
                    ra.title = ref.title;
                    rli.appendChild(ra); refUl.appendChild(rli);
                });
                issTd.appendChild(refUl);
            }
            tr.appendChild(issTd);
            tbody.appendChild(tr);
        });
        jTable.appendChild(tbody); jobsDet.appendChild(jTable);
        panels.appendChild(jobsDet);
    }
    // Failure analysis
    if (rdata.issues && rdata.issues.length) {
        var faDet = document.createElement('details'); faDet.className = 'section-toggle';
        var faSum = document.createElement('summary');
        faSum.textContent = 'Failure Analysis \\u2014 ' + total + ' ' + label;
        faDet.appendChild(faSum);
        renderIssueTable(faDet, rdata.issues, 'release-' + version, 'Release ' + version);
        panels.appendChild(faDet);
    }
    sec.appendChild(panels);
    parent.appendChild(sec);
}

// === PRs tab ===
function renderPRs(parent) {
    if (D.pr_error) {
        var sec = h('div', 'release-section');
        sec.appendChild(h('div', 'release-header', [h('h2', '', 'Pull Requests'), h('span', 'badge badge-nodata', 'collection error')]));
        var pre = document.createElement('pre'); pre.textContent = 'Data collection failed: ' + D.pr_error;
        sec.appendChild(pre); parent.appendChild(sec); return;
    }
    var analyzed = {};
    if (D.pr_data && D.pr_data.has_content) {
        (D.pr_data.prs || []).forEach(function(pr) { analyzed[pr.number] = pr; });
    }
    var allPrs = [];
    if (D.pr_status) {
        D.pr_status.forEach(function(s) {
            var entry = {number: s.pr_number, title: s.title || '', url: s.url || '', passed: s.passed || 0, failed: s.failed || 0, pending: s.pending || 0, total: s.total || 0};
            if (analyzed[s.pr_number]) entry.analysis = analyzed[s.pr_number];
            allPrs.push(entry);
        });
    } else if (Object.keys(analyzed).length) {
        (D.pr_data.prs || []).forEach(function(pr) {
            allPrs.push({number: pr.number, title: pr.title || '', url: pr.url || '', passed: 0, failed: pr.failed || 0, pending: 0, total: pr.failed || 0, analysis: pr});
        });
    }
    if (!allPrs.length) {
        var sec2 = h('div', 'release-section');
        sec2.appendChild(h('div', 'release-header', [h('h2', '', 'Pull Requests'), h('span', 'badge badge-ok', '0 failures')]));
        sec2.appendChild(h('p', '', 'No open pull requests found.'));
        parent.appendChild(sec2); return;
    }
    // TOC
    var toc = h('div', 'toc');
    toc.appendChild(h('h3', '', 'Table of Contents'));
    var ul = document.createElement('ul');
    allPrs.forEach(function(pr) {
        var b = (pr.analysis && pr.analysis.breakdown) || {build:0,test:0,infrastructure:0};
        var li = document.createElement('li');
        li.appendChild(makeLink('#pr-' + pr.number, 'PR# ' + pr.number));
        var info = ' \\u2014 ' + pr.failed + ' failures (' + (b.build||0) + ' build, ' + (b.test||0) + ' test, ' + (b.infrastructure||0) + ' infra)';
        if (pr.pending) info += ' \\u2014 ' + pr.pending + ' running';
        li.appendChild(document.createTextNode(info));
        ul.appendChild(li);
    });
    toc.appendChild(ul); parent.appendChild(toc);
    // PR sections
    allPrs.forEach(function(pr) {
        var sec3 = h('div', 'release-section');
        sec3.id = 'pr-' + pr.number;
        var hdr = h('div', 'release-header');
        var heading = document.createElement('h2');
        if (pr.url) { heading.appendChild(setAttr(makeLink(pr.url, 'PR# ' + pr.number, '', '_blank'), {title: pr.title})); }
        else { var sp = h('span', '', 'PR# ' + pr.number); sp.title = pr.title; heading.appendChild(sp); }
        var prRelM = (pr.title || '').match(/rebase-(release-[0-9.]+|main)/);
        if (prRelM) heading.appendChild(document.createTextNode(' (rebase ' + prRelM[1] + ')'));
        else if (pr.title) heading.appendChild(document.createTextNode(': ' + pr.title));
        heading.appendChild(sectionAnchor('pr-' + pr.number));
        hdr.appendChild(heading);
        var totalFailed = pr.failed;
        var lbl = totalFailed === 1 ? 'failure' : 'failures';
        hdr.appendChild(h('span', 'badge ' + badgeClass(totalFailed, false), totalFailed + ' ' + lbl));
        sec3.appendChild(hdr);
        var b2 = (pr.analysis && pr.analysis.breakdown) || {build:0,test:0,infrastructure:0};
        var bd = h('div', 'breakdown');
        bd.appendChild(h('span', 'breakdown-item', [h('strong', '', String(b2.build||0)), ' Build']));
        bd.appendChild(h('span', 'breakdown-item', [h('strong', '', String(b2.test||0)), ' Test']));
        bd.appendChild(h('span', 'breakdown-item', [h('strong', '', String(b2.infrastructure||0)), ' Infrastructure']));
        if (pr.passed) bd.appendChild(h('span', 'breakdown-item', [h('strong', '', String(pr.passed)), ' Passed']));
        if (pr.pending) bd.appendChild(h('span', 'breakdown-item', [h('strong', '', String(pr.pending)), ' Running']));
        sec3.appendChild(bd);
        if (pr.analysis && pr.analysis.issues && pr.analysis.issues.length) {
            renderIssueTable(sec3, pr.analysis.issues, 'pr-' + pr.number, 'PR #' + pr.number);
        }
        parent.appendChild(sec3);
    });
}

// === Bugs tab ===
function renderBugs(parent) {
    var bd = D.bugs_tab_data;
    if (!bd || (!bd.linked.length && !bd.unlinked.length)) {
        var sec = h('div', 'release-section');
        sec.appendChild(h('p', '', 'No bug data available. Run the full doctor workflow to populate bug information.'));
        parent.appendChild(sec); return;
    }
    var sec2 = h('div', 'release-section');
    sec2.appendChild(h('div', 'release-header', h('h2', '', 'AI-Generated Bugs')));
    var grid = h('div', 'overview-grid');
    var totalLinked = bd.linked.length, totalUnlinked = bd.unlinked.length;
    var total = bd.jira_query_available ? bd.total_open : totalLinked;
    grid.appendChild(h('div', 'overview-card', [h('div', 'number', String(total)), h('div', 'label', 'Total Open')]));
    var lnCss = totalLinked > 0 ? 'status-pass' : '';
    grid.appendChild(h('div', 'overview-card', [h('div', 'number ' + lnCss, String(totalLinked)), h('div', 'label', 'Linked to Failures')]));
    if (bd.jira_query_available) {
        var ulCss = totalUnlinked > 0 ? 'status-fail' : '';
        grid.appendChild(h('div', 'overview-card', [h('div', 'number ' + ulCss, String(totalUnlinked)), h('div', 'label', 'Not Linked')]));
    }
    sec2.appendChild(grid);
    if (!bd.jira_query_available) {
        sec2.appendChild(h('p', 'job-date', 'Only bugs linked to current failures are shown. Run the full doctor workflow to include all open AI-generated bugs.'));
    }
    var PRIO = {blocker:0,critical:1,major:2,normal:3,minor:4,trivial:5};
    function sortBugs(a,b) { var pa = PRIO[(a.priority||'').toLowerCase()]||99, pb = PRIO[(b.priority||'').toLowerCase()]||99; return pa-pb || (a.key||'').localeCompare(b.key||''); }
    function renderBugTable(bugs, showReleases) {
        var table = h('table', 'data-table');
        var thead = document.createElement('thead'); var tr = document.createElement('tr');
        var cols = ['JIRA','Status','Assignee','Summary'];
        if (showReleases) cols.push('Releases');
        cols.push('Updated','');
        cols.forEach(function(c) { var th2 = document.createElement('th'); th2.textContent = c; tr.appendChild(th2); });
        thead.appendChild(tr); table.appendChild(thead);
        var tbody = document.createElement('tbody');
        bugs.sort(sortBugs).forEach(function(bug) {
            var btr = document.createElement('tr');
            btr.id = 'bug-' + bug.key;
            var tdKey = document.createElement('td');
            tdKey.appendChild(makeLink(JIRA_BASE + '/browse/' + bug.key, bug.key, '', '_blank'));
            btr.appendChild(tdKey);
            btr.appendChild(h('td', '', bug.status || ''));
            btr.appendChild(h('td', '', bug.assignee || ''));
            btr.appendChild(h('td', '', bug.summary || ''));
            if (showReleases) {
                var rlTd = document.createElement('td');
                if (bug.links) {
                    var byRel = {};
                    bug.links.forEach(function(l) { byRel[l.release] = (byRel[l.release]||0) + (l.affected_jobs||0); });
                    var parts = [];
                    Object.keys(byRel).sort().forEach(function(r) {
                        var anchor = r === 'PRs' ? 'tab-pull-requests' : 'release-' + r;
                        var a2 = makeLink('#' + anchor, r);
                        rlTd.appendChild(a2);
                        rlTd.appendChild(document.createTextNode(' (' + byRel[r] + ') '));
                    });
                }
                btr.appendChild(rlTd);
            }
            btr.appendChild(h('td', '', bug.updated || ''));
            var linkTd = h('td', '', anchorLink('bug-' + bug.key));
            btr.appendChild(linkTd);
            tbody.appendChild(btr);
        });
        table.appendChild(tbody);
        return table;
    }
    if (bd.linked.length) {
        sec2.appendChild(h('h3', '', 'Linked to Failures'));
        sec2.appendChild(renderBugTable(bd.linked.slice(), true));
    }
    if (bd.unlinked.length && bd.jira_query_available) {
        sec2.appendChild(h('h3', '', 'Not Linked'));
        sec2.appendChild(renderBugTable(bd.unlinked.slice(), false));
    }
    parent.appendChild(sec2);
}

// === Images tab ===
function renderImages(parent) {
    var id = D.images_tab_data;
    if (!id || !id.has_data) {
        var sec = h('div', 'release-section');
        sec.appendChild(h('p', '', 'No container image data available. Run the full doctor workflow to populate image health data.'));
        parent.appendChild(sec); return;
    }
    var releases = Object.keys(id.releases).sort().reverse();
    // TOC
    var toc = h('div', 'toc');
    var tocHdr = h('div', 'toc-header');
    tocHdr.appendChild(h('h3', '', 'Table of Contents'));
    var latestLabel = document.createElement('label'); latestLabel.className = 'filter-toggle';
    var latestCb = document.createElement('input'); latestCb.type = 'checkbox'; latestCb.id = 'filter-latest-images';
    latestLabel.appendChild(latestCb); latestLabel.appendChild(document.createTextNode(' Latest only'));
    tocHdr.appendChild(latestLabel);
    toc.appendChild(tocHdr);
    var tocUl = document.createElement('ul');
    releases.forEach(function(rel) {
        var relData = id.releases[rel];
        var li = document.createElement('li');
        li.appendChild(makeLink('#images-' + rel, 'Release ' + rel));
        var repoInfo = relData.repos.map(function(r) {
            var name = r.display_name;
            if (r.latest_grade) return name + ' [' + r.latest_grade + ']';
            return name;
        }).join(', ');
        li.appendChild(document.createTextNode(' (' + repoInfo + ')'));
        tocUl.appendChild(li);
    });
    toc.appendChild(tocUl); parent.appendChild(toc);
    // Sections
    releases.forEach(function(rel) {
        var relData = id.releases[rel];
        var sec2 = h('div', 'release-section');
        sec2.id = 'images-' + rel;
        var hdr = h('div', 'release-header');
        var heading = h('h2', '', 'Release ' + rel);
        heading.appendChild(sectionAnchor('images-' + rel));
        hdr.appendChild(heading); sec2.appendChild(hdr);
        relData.repos.forEach(function(repo) {
            var repoUrl = repo.catalog_id ? 'https://catalog.redhat.com/en/software/containers/' + repo.name + '/' + repo.catalog_id : '';
            if (repoUrl) { sec2.appendChild(h('h3', '', makeLink(repoUrl, repo.display_name, '', '_blank'))); }
            else { sec2.appendChild(h('h3', '', repo.display_name)); }
            var table = h('table', 'data-table');
            var thead = document.createElement('thead'); var thRow = document.createElement('tr');
            ['Version','Architectures','Image Created','Grade Updated'].forEach(function(t) { var th2 = document.createElement('th'); th2.textContent = t; thRow.appendChild(th2); });
            thead.appendChild(thRow); table.appendChild(thead);
            var tbody = document.createElement('tbody');
            repo.versions.forEach(function(ver, vi) {
                var tr = document.createElement('tr');
                if (vi === 0) tr.setAttribute('data-latest', '1');
                tr.appendChild(h('td', '', ver.tag));
                var archTd = document.createElement('td');
                ver.archs.forEach(function(a, ai) {
                    if (ai > 0) archTd.appendChild(document.createTextNode(' '));
                    if (repoUrl && a.image_id) {
                        archTd.appendChild(makeLink(repoUrl + '?image=' + a.image_id + '&architecture=' + a.arch, a.arch, '', '_blank'));
                    } else { archTd.appendChild(document.createTextNode(a.arch)); }
                    archTd.appendChild(document.createTextNode('\\u00A0'));
                    archTd.appendChild(h('span', 'grade-badge ' + (a.grade_css || 'grade-na'), a.grade || 'N/A'));
                });
                tr.appendChild(archTd);
                tr.appendChild(h('td', '', ver.creation_date || ''));
                tr.appendChild(h('td', '', ver.last_update_date || ''));
                tbody.appendChild(tr);
            });
            table.appendChild(tbody); sec2.appendChild(table);
        });
        parent.appendChild(sec2);
    });
    latestCb.addEventListener('change', function() {
        var on = this.checked;
        document.querySelectorAll('#tab-images .data-table tbody tr').forEach(function(row) {
            row.style.display = (!on || row.hasAttribute('data-latest')) ? '' : 'none';
        });
    });
}

// === Tab switching ===
document.querySelectorAll('.tab-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
        var name = this.getAttribute('data-tab');
        document.querySelectorAll('.tab-content').forEach(function(el) { el.classList.remove('active'); });
        document.querySelectorAll('.tab-btn').forEach(function(el) { el.classList.remove('active'); });
        document.getElementById('tab-' + name).classList.add('active');
        this.classList.add('active');
    });
});

// === Today filter ===
function filterToday(on) {
    var today = new Date().toISOString().split('T')[0];
    document.querySelectorAll('#tab-periodics .issue-row').forEach(function(row) {
        var dates = (row.getAttribute('data-dates') || '').split(' ');
        var show = !on || dates.indexOf(today) !== -1;
        row.style.display = show ? '' : 'none';
        var detail = row.nextElementSibling;
        if (detail && detail.classList.contains('detail-row')) {
            if (!show) detail.classList.remove('show');
            detail.style.display = show ? '' : 'none';
        }
    });
    document.querySelectorAll('#tab-periodics .release-section').forEach(function(sec) {
        var id = sec.id.replace('release-', '');
        var rows = sec.querySelectorAll('.issue-row');
        var total = 0, bd = {build: 0, test: 0, infra: 0};
        rows.forEach(function(r) {
            if (r.style.display !== 'none') {
                total++;
                var ft = r.querySelector('.col-ftype .ftype-badge');
                if (ft) { var t = ft.textContent.trim().toLowerCase(); if (t==='build') bd.build++; else if (t==='infra') bd.infra++; else bd.test++; }
            }
        });
        var lbl = total === 1 ? 'failure' : 'failures';
        var summary = total + ' ' + lbl + ' (' + bd.build + ' build, ' + bd.test + ' test, ' + bd.infra + ' infra)';
        var tocEl = document.querySelector('.toc-counts[data-release="' + id + '"]');
        if (tocEl) tocEl.textContent = summary;
        var badge = sec.querySelector('.release-badge');
        if (badge) { badge.textContent = total + ' ' + lbl; badge.className = 'badge release-badge ' + (total===0?'badge-ok':total>=5?'badge-critical':'badge-issues'); }
        var bdb = sec.querySelector('.bd-build'), bdt = sec.querySelector('.bd-test'), bdi = sec.querySelector('.bd-infra');
        if (bdb) bdb.textContent = bd.build;
        if (bdt) bdt.textContent = bd.test;
        if (bdi) bdi.textContent = bd.infra;
    });
}

// === Side-by-side toggle ===
function toggleSideBySide(on) {
    document.querySelector('.container').classList.toggle('wide', on);
    document.querySelectorAll('#tab-periodics .release-section').forEach(function(sec) {
        sec.classList.toggle('side-by-side', on);
        if (on) sec.querySelectorAll('.section-toggle').forEach(function(d) { d.open = true; });
    });
}

// === Issue-ref click handler (side-by-side cross-reference) ===
document.addEventListener('click', function(e) {
    var link = e.target.closest('a.issue-ref');
    if (!link) return;
    var sec = link.closest('.release-section');
    if (!sec || !sec.classList.contains('side-by-side')) return;
    e.preventDefault();
    var id = link.getAttribute('href').substring(1);
    var row = document.getElementById(id);
    if (!row) return;
    var title = row.querySelector('.col-title');
    var detail = row.nextElementSibling;
    if (!detail || !detail.classList.contains('detail-row')) return;
    if (!detail.classList.contains('show')) {
        if (title) title.classList.add('active');
        detail.classList.add('show');
    } else {
        if (title) title.classList.remove('active');
        detail.classList.remove('show');
    }
});

// === Render everything ===
document.getElementById('report-title').textContent = D.component_title + ' CI Doctor Report';
document.getElementById('report-timestamp').textContent = 'Generated: ' + D.timestamp + ' UTC';
renderDiagnostics(document.getElementById('diagnostics-slot'));
renderOverview(document.getElementById('overview-slot'));
renderPeriodics(document.getElementById('tab-periodics'));
renderPRs(document.getElementById('tab-pull-requests'));
renderBugs(document.getElementById('tab-bugs'));
renderImages(document.getElementById('tab-images'));

// Show container, hide loading
document.getElementById('loading').style.display = 'none';
document.querySelector('.container').style.display = '';

// === Table sorting ===
document.querySelectorAll('.data-table').forEach(function(table) {
    var headers = table.querySelectorAll('th');
    function sortBy(colIdx, asc) {
        headers.forEach(function(h2) { h2.classList.remove('sort-asc', 'sort-desc'); });
        headers[colIdx].classList.add(asc ? 'sort-asc' : 'sort-desc');
        var tbody = table.querySelector('tbody');
        var rows = Array.from(tbody.querySelectorAll('tr'));
        rows.sort(function(a, b) {
            var av = a.cells[colIdx].textContent.trim().toLowerCase();
            var bv = b.cells[colIdx].textContent.trim().toLowerCase();
            return asc ? av.localeCompare(bv) : bv.localeCompare(av);
        });
        rows.forEach(function(r) { tbody.appendChild(r); });
    }
    headers.forEach(function(th, colIdx) {
        if (!th.textContent.trim()) return;
        th.addEventListener('click', function() { sortBy(colIdx, !th.classList.contains('sort-asc')); });
    });
    var ds = table.getAttribute('data-default-sort');
    if (ds) { var parts = ds.split(','); sortBy(parseInt(parts[0], 10), parts[1] === 'asc'); }
    else if (headers.length >= 2) { sortBy(headers.length - 2, false); }
});

// === Anchor link copy-to-clipboard ===
(function() {
    var toast = h('div', 'copy-toast', 'Link copied');
    document.body.appendChild(toast);
    var timer;
    function copyAnchor(e) {
        e.preventDefault(); e.stopPropagation();
        var href = e.currentTarget.getAttribute('href');
        var url = location.href.split('#')[0] + href;
        if (!navigator.clipboard || !navigator.clipboard.writeText) { location.hash = href.slice(1); return; }
        navigator.clipboard.writeText(url).then(function() {
            toast.classList.add('show'); clearTimeout(timer);
            timer = setTimeout(function() { toast.classList.remove('show'); }, 1500);
        }).catch(function() { location.hash = href.slice(1); });
    }
    document.querySelectorAll('.anchor-link, .section-anchor').forEach(function(el) { el.addEventListener('click', copyAnchor); });
})();

// === Hash-based deep linking ===
(function() {
    function openAnchor() {
        var hash = location.hash;
        if (!hash) return;
        var target = document.getElementById(hash.substring(1));
        if (!target) return;
        if (target.classList.contains('issue-row')) {
            var title = target.querySelector('.col-title');
            if (title && !title.classList.contains('active')) {
                title.classList.add('active');
                var detail = target.nextElementSibling;
                if (detail && detail.classList.contains('detail-row')) detail.classList.add('show');
            }
        }
        var section = target.closest('.tab-content');
        if (section && !section.classList.contains('active')) {
            document.querySelectorAll('.tab-content').forEach(function(el) { el.classList.remove('active'); });
            document.querySelectorAll('.tab-btn').forEach(function(el) { el.classList.remove('active'); });
            section.classList.add('active');
            var tabId = section.id.replace('tab-', '');
            document.querySelectorAll('.tab-btn').forEach(function(el) {
                if (el.getAttribute('data-tab') === tabId) el.classList.add('active');
            });
        }
        requestAnimationFrame(function() { target.scrollIntoView({behavior: 'smooth'}); });
    }
    openAnchor();
    window.addEventListener('hashchange', openAnchor);
})();

// === Text filter ===
(function() {
    var input = document.getElementById('report-filter');
    var countEl = document.getElementById('filter-count');
    if (!input || !countEl) return;
    var debounceTimer;
    function matchIssue(iss, q) {
        var fields = [iss.title||'', iss.root_cause||'', iss.failure_type||'', iss.severity||'', iss.next_steps||''];
        (iss.affected_jobs||[]).forEach(function(j){fields.push(j.name||'');});
        (iss.scenarios||[]).forEach(function(s){fields.push(s);});
        return fields.join(' ').toLowerCase().indexOf(q) !== -1;
    }
    function applyFilter() {
        var q = input.value.trim().toLowerCase();
        var shown = 0, total = 0;
        document.querySelectorAll('#tab-periodics .issue-row').forEach(function(row) {
            total++;
            if (!q) { row.style.display = ''; shown++; return; }
            var id = row.id || '';
            var parts = id.match(/^release-(.+)-(\\d+)$/);
            if (!parts) { row.style.display = ''; shown++; return; }
            var ver = parts[1], num = parseInt(parts[2], 10);
            var rd = (D.releases_data || {})[ver];
            if (!rd || !rd.issues) { row.style.display = ''; shown++; return; }
            var iss = rd.issues.find(function(i) { return i.number === num; });
            var vis = iss ? matchIssue(iss, q) : true;
            row.style.display = vis ? '' : 'none';
            if (vis) shown++;
            var detail = row.nextElementSibling;
            if (detail && detail.classList.contains('detail-row')) {
                if (!vis) { detail.classList.remove('show'); detail.style.display = 'none'; }
                else { detail.style.display = ''; }
            }
        });
        document.querySelectorAll('#tab-pull-requests .issue-row').forEach(function(row) {
            total++;
            if (!q) { row.style.display = ''; shown++; return; }
            var id = row.id || '';
            var parts = id.match(/^pr-(\\d+)-(\\d+)$/);
            if (!parts) { row.style.display = ''; shown++; return; }
            var prNum = parseInt(parts[1], 10), issNum = parseInt(parts[2], 10);
            var pr = D.pr_data && D.pr_data.prs ? D.pr_data.prs.find(function(p){return p.number===prNum;}) : null;
            var iss = pr && pr.issues ? pr.issues.find(function(i){return i.number===issNum;}) : null;
            var vis = iss ? matchIssue(iss, q) : true;
            row.style.display = vis ? '' : 'none';
            if (vis) shown++;
            var detail = row.nextElementSibling;
            if (detail && detail.classList.contains('detail-row')) {
                if (!vis) { detail.classList.remove('show'); detail.style.display = 'none'; }
                else { detail.style.display = ''; }
            }
        });
        document.querySelectorAll('#tab-bugs .data-table tbody tr').forEach(function(row) {
            total++; if (!q) { row.style.display = ''; shown++; return; }
            var vis = row.textContent.toLowerCase().indexOf(q) !== -1;
            row.style.display = vis ? '' : 'none'; if (vis) shown++;
        });
        countEl.textContent = q ? (shown + ' / ' + total + ' matches') : '';
    }
    input.addEventListener('input', function() { clearTimeout(debounceTimer); debounceTimer = setTimeout(applyFilter, 300); });
})();
})();"""


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_files(workdir, releases):
    result = {"releases": {}, "prs": {"summary": None, "status": None, "bugs": [], "error": None}}

    jobs_dir = os.path.join(workdir, "jobs")
    bugs_dir = os.path.join(workdir, "bugs")

    for version in releases:
        entry = {"summary": None, "bugs": None, "jobs": None, "status": None, "error": None}
        path = os.path.join(jobs_dir, f"release-{version}-summary.json")
        if os.path.exists(path):
            entry["summary"] = path
        path = os.path.join(bugs_dir, f"bug-matches-{version}.json")
        if os.path.exists(path):
            entry["bugs"] = path
        path = os.path.join(jobs_dir, f"release-{version}-jobs.json")
        if os.path.exists(path):
            entry["jobs"] = path
        path = os.path.join(jobs_dir, f"release-{version}-status.json")
        if os.path.exists(path):
            entry["status"] = path
        path = os.path.join(jobs_dir, f"release-{version}-error.txt")
        if os.path.exists(path):
            with open(path) as f:
                entry["error"] = f.read().strip()
        result["releases"][version] = entry

    path = os.path.join(jobs_dir, "prs-summary.json")
    if os.path.exists(path):
        result["prs"]["summary"] = path

    path = os.path.join(jobs_dir, "prs-status.json")
    if os.path.exists(path):
        result["prs"]["status"] = path

    for path in glob_mod.glob(os.path.join(bugs_dir, "bug-matches-rebase-release-*.json")):
        result["prs"]["bugs"].append(path)

    path = os.path.join(jobs_dir, "prs-error.txt")
    if os.path.exists(path):
        with open(path) as f:
            result["prs"]["error"] = f.read().strip()

    return result


# ---------------------------------------------------------------------------
# JSON loading (replaces all text parsers)
# ---------------------------------------------------------------------------

def load_json(filepath):
    if not filepath or not os.path.exists(filepath):
        return None
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        print(f"WARNING: failed to load {filepath}: {exc}", file=sys.stderr)
        return None


def load_bug_candidates(filepath):
    data = load_json(filepath)
    if not data:
        return []
    return data.get("candidates", [])


def load_open_bugs(filepath):
    data = load_json(filepath)
    if not data:
        return []
    return data.get("open_bugs", [])


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

def _tokenize(text):
    words = re.findall(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|[a-z0-9]", text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) >= 2}


def match_issue_to_bugs(issue_title, bug_candidates):
    if not bug_candidates:
        return None
    issue_tokens = _tokenize(issue_title)
    if not issue_tokens:
        return None
    matches = []
    for cand in bug_candidates:
        sig_tokens = _tokenize(cand["error_signature"])
        if not sig_tokens:
            continue
        score = len(issue_tokens & sig_tokens) / len(sig_tokens)
        if score >= MATCH_THRESHOLD:
            matches.append((score, cand))
    if not matches:
        return None
    matches.sort(key=lambda x: x[0], reverse=True)
    # Merge duplicates/regressions from all matching candidates (de-duped by key)
    merged = dict(matches[0][1])
    seen_dup_keys = {d["key"] for d in merged.get("duplicates", [])}
    seen_reg_keys = {r["key"] for r in merged.get("regressions", [])}
    all_dups = list(merged.get("duplicates", []))
    all_regs = list(merged.get("regressions", []))
    for _, cand in matches[1:]:
        for d in cand.get("duplicates", []):
            if d["key"] not in seen_dup_keys:
                seen_dup_keys.add(d["key"])
                all_dups.append(d)
        for r in cand.get("regressions", []):
            if r["key"] not in seen_reg_keys:
                seen_reg_keys.add(r["key"])
                all_regs.append(r)
    merged["duplicates"] = all_dups
    merged["regressions"] = all_regs
    return merged


# ---------------------------------------------------------------------------
# Bugs tab data
# ---------------------------------------------------------------------------

def _collect_linked_bugs(bug_data, pr_bug_paths, ignore_keys=None):
    """Extract all JIRA keys from bug mapping duplicates, with release associations.

    Returns (linked, details) where:
    - linked: dict mapping JIRA key to list of {release, error_signature, affected_jobs}
    - details: dict mapping JIRA key to {summary, status, updated} from the mapping file
    """
    linked = {}
    details = {}

    def _add(cand, release_label):
        for dup in cand.get("duplicates", []):
            key = dup.get("key", "")
            if not key or (ignore_keys and key in ignore_keys):
                continue
            existing = linked.get(key, [])
            if any(link["release"] == release_label for link in existing):
                continue
            linked.setdefault(key, []).append({
                "release": release_label,
                "error_signature": cand.get("error_signature", ""),
                "affected_jobs": cand.get("affected_jobs", 0),
            })
            if key not in details:
                details[key] = {"summary": dup.get("summary", ""), "status": dup.get("status", ""), "assignee": dup.get("assignee", ""), "updated": dup.get("updated", "")}

    for version, candidates in bug_data.items():
        for cand in candidates:
            _add(cand, version)

    for path in pr_bug_paths:
        for cand in load_bug_candidates(path):
            _add(cand, "PRs")

    return linked, details


def _pick_bug_fields(issue, links=None):
    entry = {
        "key": issue.get("key", ""),
        "summary": issue.get("summary", ""),
        "status": issue.get("status", ""),
        "assignee": issue.get("assignee", ""),
        "updated": issue.get("updated", ""),
    }
    if links is not None:
        entry["links"] = links
    return entry


def _add_matched_links(linked_map, linked_details, releases_data, pr_data, all_bug_candidates, ignore_keys=None):
    """Add release/PR associations discovered by pooled candidate matching.

    Walks each release's and PR's issues, runs match_issue_to_bugs against the
    pooled candidates, and records new (key, release) associations in linked_map.
    Only processes duplicates (open bugs), not regressions (closed bugs).
    """
    def _scan_issues(issues, release_label):
        for issue in issues:
            match = match_issue_to_bugs(issue.get("title", ""), all_bug_candidates)
            if not match:
                continue
            for entry in match.get("duplicates", []):
                key = entry.get("key", "")
                if not key or (ignore_keys and key in ignore_keys):
                    continue
                existing = linked_map.get(key, [])
                if any(link["release"] == release_label for link in existing):
                    continue
                linked_map.setdefault(key, []).append({
                    "release": release_label,
                    "error_signature": match.get("error_signature", ""),
                    "affected_jobs": issue.get("job_count", 0),
                })
                if key not in linked_details:
                    linked_details[key] = {"summary": entry.get("summary", ""), "status": entry.get("status", ""), "assignee": entry.get("assignee", ""), "updated": entry.get("updated", "")}

    for version, rdata in (releases_data or {}).items():
        if rdata and rdata.get("issues"):
            _scan_issues(rdata["issues"], version)

    if pr_data and pr_data.get("prs"):
        for pr in pr_data["prs"]:
            if pr.get("issues"):
                _scan_issues(pr["issues"], "PRs")


def build_bugs_tab_data(open_bugs_data, bug_data, pr_bug_paths, releases_data=None, pr_data=None, all_bug_candidates=None, ignore_keys=None):
    """Cross-reference open bugs query with bug mapping files."""
    linked_map, linked_details = _collect_linked_bugs(bug_data, pr_bug_paths, ignore_keys)

    if all_bug_candidates and (releases_data or pr_data):
        _add_matched_links(linked_map, linked_details, releases_data, pr_data, all_bug_candidates, ignore_keys)

    if open_bugs_data and open_bugs_data.get("issues"):
        linked = []
        unlinked = []
        seen_keys = set()

        for issue in open_bugs_data["issues"]:
            key = issue["key"]
            seen_keys.add(key)
            if key in linked_map:
                linked.append(_pick_bug_fields(issue, linked_map[key]))
            else:
                unlinked.append(_pick_bug_fields(issue))

        # Keys in mapping files but not in open bugs query
        for key, links in linked_map.items():
            if key not in seen_keys:
                det = dict(linked_details.get(key, {}), key=key)
                linked.append(_pick_bug_fields(det, links))

        return {
            "total_open": len(linked) + len(unlinked),
            "linked": linked,
            "unlinked": unlinked,
            "jira_query_available": True,
        }

    # Graceful degradation: no open bugs file, use mapping files only
    linked = []
    for key, links in linked_map.items():
        det = dict(linked_details.get(key, {}), key=key)
        linked.append(_pick_bug_fields(det, links))
    return {
        "total_open": 0,
        "linked": linked,
        "unlinked": [],
        "jira_query_available": False,
    }


# ---------------------------------------------------------------------------
# Images (Image Health) tab
# ---------------------------------------------------------------------------

def _load_catalog_id(images_dir, repo_slug):
    """Load cached catalog repository ID from a text file."""
    path = os.path.join(images_dir, f"{repo_slug}-id.txt")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    return None


def load_images_data(workdir, releases):
    """Load cached image JSON files from ${WORKDIR}/images/.

    Each repo has a single {repo_slug}.json containing all images.
    Images are filtered into per-release buckets here in Python.
    """
    images_dir = os.path.join(workdir, "images")
    if not os.path.isdir(images_dir):
        return None

    result = {}
    for fname in sorted(os.listdir(images_dir)):
        if not fname.endswith(".json") or fname.endswith("-id.txt"):
            continue
        repo_slug = fname[:-len(".json")]
        path = os.path.join(images_dir, fname)
        all_images = load_json(path)
        if not all_images:
            continue
        repo = repo_slug.replace("@", "/", 1)
        repo_data = {}
        for release in releases:
            filtered = [img for img in all_images
                        if any(tag_matches_release(t, release) for t in img.get("tags", []))]
            if filtered:
                repo_data[release] = filtered
        if repo_data:
            catalog_id = _load_catalog_id(images_dir, repo_slug)
            result[repo] = {"releases": repo_data, "catalog_id": catalog_id}

    return result if result else None


_ZSTREAM_RE = re.compile(r'^v?\d+\.\d+\.\d+$')
_ASSEMBLY_RE = re.compile(r'assembly\.(\d+\.\d+\.\d+)(?:\.|$)')


def _pick_zstream_tag(tags, release):
    """Pick the z-stream version tag (e.g. v4.18.42) for display."""
    if not tags:
        return ""
    matching = [t for t in tags if tag_matches_release(t, release) and _ZSTREAM_RE.match(t)]
    if matching:
        return max(matching, key=len).lstrip("v")
    # Extract z-stream from assembly tag (e.g. "assembly.4.19.7.el9" → "4.19.7").
    # Filter by release to avoid picking an assembly tag from a different release
    # when an image carries tags for multiple versions.
    for t in tags:
        if not tag_matches_release(t, release):
            continue
        m = _ASSEMBLY_RE.search(t)
        if m:
            return m.group(1)
    matching = [t for t in tags if tag_matches_release(t, release)]
    tag = min(matching, key=len) if matching else min(tags, key=len)
    return tag.lstrip("v")


def _worst_grade(grades):
    """Return the worst freshness grade from a list of grade strings."""
    worst = -1
    worst_label = None
    for grade in grades:
        if grade and _GRADE_ORDER.get(grade, -1) > worst:
            worst = _GRADE_ORDER[grade]
            worst_label = grade
    return worst_label


def _group_repo_images(images, release):
    """Group raw images by z-stream version, merging architectures."""
    groups = {}
    for img in images:
        tag = _pick_zstream_tag(img.get("tags", []), release)
        if tag not in groups:
            groups[tag] = {
                "tag": tag,
                "archs": [],
                "_seen_archs": set(),
                "creation_date": (img.get("creation_date") or "")[:10],
                "last_update_date": (img.get("last_update_date") or "")[:10],
                "_grades": [],
            }
        arch = img.get("architecture", "")
        image_id = img.get("_id", "")
        grade = img.get("freshness_grade")
        if arch not in groups[tag]["_seen_archs"]:
            groups[tag]["archs"].append({
                "arch": arch,
                "image_id": image_id,
                "grade": grade or "N/A",
                "grade_css": _GRADE_CSS.get(grade, "grade-na"),
            })
            groups[tag]["_seen_archs"].add(arch)
        groups[tag]["_grades"].append(grade)

    versions = []
    for g in groups.values():
        worst = _worst_grade(g["_grades"])
        versions.append({
            "tag": g["tag"],
            "archs": sorted(g["archs"], key=lambda a: a["arch"]),
            "freshness_grade": worst or "N/A",
            "creation_date": g["creation_date"],
            "last_update_date": g["last_update_date"],
        })
    versions.sort(key=lambda v: tuple(int(p) for p in v["tag"].split(".") if p.isdigit()), reverse=True)
    return versions


def build_images_tab_data(images_data, releases):
    """Structure raw image data for rendering.

    Top-level grouping is by release (like the Periodics tab), with each
    release containing per-repository tables of z-stream versions.
    """
    if not images_data:
        return {"has_data": False, "releases": {}}

    releases_out = {}
    for release in releases:
        repos = []
        for repo, repo_info in images_data.items():
            release_map = repo_info["releases"]
            catalog_id = repo_info.get("catalog_id") or ""
            images = release_map.get(release, [])
            if not images:
                continue
            versions = _group_repo_images(images, release)
            latest_grade = versions[0]["freshness_grade"] if versions else None
            if latest_grade == "N/A":
                latest_grade = None
            repos.append({
                "name": repo,
                "display_name": repo.split("/")[-1],
                "catalog_id": catalog_id,
                "versions": versions,
                "latest_grade": latest_grade,
            })
        if repos:
            all_grades = [r["latest_grade"] for r in repos if r["latest_grade"]]
            worst = _worst_grade(all_grades)
            releases_out[release] = {"repos": repos, "latest_grade": worst}

    return {"has_data": bool(releases_out), "releases": releases_out}


# ---------------------------------------------------------------------------
# Index image extraction (LVMS-specific)
# ---------------------------------------------------------------------------

def extract_index_image(workdir, version):
    """Load index image info from the index-image subdirectory.

    Reads ${WORKDIR}/index-image/release-<version>.json produced by
    extract-index-image.sh. Returns None when the file does not exist
    (non-LVMS component or script has not run).
    """
    path = os.path.join(workdir, "index-image", f"release-{version}.json")
    return load_json(path)


# ---------------------------------------------------------------------------
# Graph / metrics helpers (pre-compute PCP data for JSON model)
# ---------------------------------------------------------------------------

# Graph workdir and Chart.js source — set by main() before rendering
_GRAPHS_DIR = None
_CHARTJS_SRC = ""
_PCP_CHARTS_SRC = ""


def _extract_build_id(url):
    """Extract build_id (last numeric path component) from a Prow job URL."""
    if not url:
        return None
    m = re.search(r"/(\d+)/?$", url)
    return m.group(1) if m else None


_graph_cache = {}

_METRIC_FILES = [("cpu.json", "cpu"), ("mem.json", "mem"),
                 ("io.json", "io"), ("disk.json", "disk")]


def _load_job_metrics(build_id):
    """Load and cache parsed PCP metric JSON files for a build_id."""
    if build_id in _graph_cache:
        return _graph_cache[build_id]
    metrics = {}
    if _GRAPHS_DIR:
        graph_dir = os.path.join(_GRAPHS_DIR, build_id)
        if os.path.isdir(graph_dir):
            for fname, key in _METRIC_FILES:
                fpath = os.path.join(graph_dir, fname)
                if os.path.isfile(fpath):
                    try:
                        with open(fpath) as f:
                            metrics[key] = json.load(f)
                    except (json.JSONDecodeError, IOError) as e:
                        print(f"WARNING: skipping {fpath}: {e}", file=sys.stderr)
    _graph_cache[build_id] = metrics
    return metrics


# ---------------------------------------------------------------------------
# HTML generation — emits skeleton + embedded data + JS renderer
# ---------------------------------------------------------------------------

def generate_html(component_title, releases_data, all_bug_candidates, pr_data, pr_status, timestamp, pr_error=None, bugs_tab_data=None, images_tab_data=None, index_data=None, jira_cfg=None, status_data=None, diagnostics_text=None):
    date_str = timestamp.strftime("%Y-%m-%d")
    time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

    # --- Pre-compute bug matches and metrics for JSON data model ---
    for _ver, _rd in releases_data.items():
        if _rd and _rd.get("issues"):
            for iss in _rd["issues"]:
                if "bug_match" not in iss:
                    iss["bug_match"] = match_issue_to_bugs(
                        iss.get("title", ""), all_bug_candidates)
                for ajob in iss.get("affected_jobs", []):
                    if "metrics" not in ajob:
                        bid = _extract_build_id(ajob.get("url", ""))
                        if bid:
                            met = _load_job_metrics(bid)
                            if met:
                                ajob["metrics"] = met

    if pr_data and pr_data.get("prs"):
        for pr_item in pr_data["prs"]:
            for iss in pr_item.get("issues", []):
                if "bug_match" not in iss:
                    iss["bug_match"] = match_issue_to_bugs(
                        iss.get("title", ""), all_bug_candidates)
                for ajob in iss.get("affected_jobs", []):
                    if "metrics" not in ajob:
                        bid = _extract_build_id(ajob.get("url", ""))
                        if bid:
                            met = _load_job_metrics(bid)
                            if met:
                                ajob["metrics"] = met

    # --- Build unified data model for client-side rendering ---
    report_data = {
        "component_title": component_title,
        "timestamp": time_str,
        "date": date_str,
        "releases_data": releases_data,
        "pr_data": pr_data,
        "pr_status": pr_status,
        "pr_error": pr_error,
        "bugs_tab_data": bugs_tab_data,
        "images_tab_data": images_tab_data,
        "index_data": index_data,
        "jira_cfg": jira_cfg,
        "status_data": status_data,
        "diagnostics_text": diagnostics_text,
        "has_chartjs": bool(_CHARTJS_SRC),
    }
    report_json_str = json.dumps(report_data, default=str, separators=(",", ":"))
    safe_report_json = report_json_str.replace("</", "<\\/").replace("<!--", "<\\!--")

    chartjs_tags = f'<script>{_CHARTJS_SRC}</script><script>{_PCP_CHARTS_SRC}</script>' if _CHARTJS_SRC else ''

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{component_title} CI Doctor Report - {date_str}</title>
    <style>
{CSS}
    </style>
    <script>window.REPORT_DATA = {safe_report_json};</script>
</head>
<body>
<div id="loading" style="display:flex;align-items:center;justify-content:center;height:80vh;font-family:sans-serif;color:#6c757d;font-size:1.2em;">Loading report&hellip;</div>
<div class="container" style="display:none">
    <h1 id="report-title"></h1>
    <p class="timestamp" id="report-timestamp"></p>
    <div id="diagnostics-slot"></div>
    <div id="overview-slot" class="overview-grid"></div>

    <div class="tab-bar">
        <button class="tab-btn active" data-tab="periodics">Periodics</button>
        <button class="tab-btn" data-tab="pull-requests">Pull Requests</button>
        <button class="tab-btn" data-tab="bugs">Bugs</button>
        <button class="tab-btn" data-tab="images">Image Health</button>
    </div>
    <div class="filter-bar">
        <input type="text" id="report-filter" placeholder="Filter issues\\u2026 (title, root cause, job name, severity)">
        <span class="filter-count" id="filter-count"></span>
    </div>

    <div id="tab-periodics" class="tab-content active"></div>
    <div id="tab-pull-requests" class="tab-content"></div>
    <div id="tab-bugs" class="tab-content"></div>
    <div id="tab-images" class="tab-content"></div>

    <p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p>
</div>
{chartjs_tags}
<script>
{JS}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    workdir = None
    releases_arg = None
    component = None
    ignore_keys = set()

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--workdir":
            if i + 1 >= len(args):
                print("Error: --workdir requires an argument", file=sys.stderr)
                sys.exit(1)
            workdir = args[i + 1]
            i += 2
        elif args[i] == "--component":
            if i + 1 >= len(args):
                print("Error: --component requires an argument", file=sys.stderr)
                sys.exit(1)
            component = args[i + 1]
            i += 2
        elif args[i] == "--ignore":
            if i + 1 >= len(args):
                print("Error: --ignore requires an argument", file=sys.stderr)
                sys.exit(1)
            ignore_keys = {k.strip() for k in args[i + 1].split(",") if k.strip()}
            i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            sys.exit(1)
        else:
            releases_arg = args[i]
            i += 1

    if not releases_arg:
        print("Usage: create-report.py --component <component> [--workdir DIR] <release1,release2,...>", file=sys.stderr)
        sys.exit(1)

    if not component:
        print("Error: --component is required", file=sys.stderr)
        sys.exit(1)

    if component not in COMPONENT_TITLES:
        print(f"Error: unsupported component '{component}'. Supported: {', '.join(COMPONENT_TITLES)}", file=sys.stderr)
        sys.exit(1)

    component_title = COMPONENT_TITLES[component]

    releases = [v.strip() for v in releases_arg.split(",") if v.strip()]
    if not releases:
        print("Error: at least one release version is required", file=sys.stderr)
        sys.exit(1)

    if workdir is None:
        workdir = f"/tmp/{component}-ci-claude-workdir.{datetime.now().strftime('%y%m%d')}"

    if not os.path.isdir(workdir):
        print(f"Error: work directory does not exist: {workdir}", file=sys.stderr)
        sys.exit(1)

    files = discover_files(workdir, releases)

    # Report discovery
    print("Files discovered:")
    found_any = False
    for version in releases:
        entry = files["releases"][version]
        parts = []
        if entry["summary"]:
            parts.append("summary found")
            found_any = True
        else:
            parts.append("summary MISSING")
        parts.append("bug mapping found" if entry["bugs"] else "no bug mapping")
        print(f"  Release {version}: {', '.join(parts)}")

    pr_entry = files["prs"]
    if pr_entry["summary"] or pr_entry["status"]:
        found_any = True
        parts = []
        if pr_entry["summary"]:
            parts.append("summary found")
        if pr_entry["status"]:
            parts.append("status found")
        parts.append(f'{len(pr_entry["bugs"])} bug mapping files')
        print(f"  PRs: {', '.join(parts)}")
    else:
        print("  PRs: no data")

    if not found_any:
        print(f"\nError: no analysis files found in {workdir}", file=sys.stderr)
        sys.exit(1)

    # Load everything via json.load
    releases_data = {}
    bug_data = {}
    _EMPTY_BREAKDOWN = {"build": 0, "test": 0, "infrastructure": 0}
    for version in releases:
        entry = files["releases"][version]
        rdata = load_json(entry["summary"])
        if rdata is None:
            if entry.get("error"):
                rdata = {
                    "total_failed": 0,
                    "issues": [],
                    "breakdown": _EMPTY_BREAKDOWN,
                    "collection_error": entry["error"],
                }
            else:
                # Distinguish "no failures" from "analysis failed" by checking the jobs file
                jobs = load_json(entry["jobs"])
                if jobs is not None and len(jobs) == 0:
                    rdata = {
                        "total_failed": 0,
                        "issues": [],
                        "breakdown": _EMPTY_BREAKDOWN,
                    }
        releases_data[version] = rdata
        bug_data[version] = load_bug_candidates(entry["bugs"])

    status_data = {}
    for version in releases:
        entry = files["releases"][version]
        status_data[version] = load_json(entry.get("status"))

    index_data = {}
    for version in releases:
        index_data[version] = extract_index_image(workdir, version)

    pr_data = load_json(pr_entry["summary"])
    pr_status = load_json(pr_entry["status"])
    pr_error = pr_entry.get("error")

    # Pool all bug candidates from every source for cross-release correlation
    all_bug_candidates = []
    for version in releases:
        all_bug_candidates.extend(bug_data[version])
    for path in pr_entry["bugs"]:
        all_bug_candidates.extend(load_bug_candidates(path))

    # Collect open bugs from mapping files (deduplicated)
    all_open_bugs = []
    seen_open_keys = set()
    bug_file_paths = [files["releases"][v]["bugs"] for v in releases if files["releases"].get(v, {}).get("bugs")]
    bug_file_paths.extend(pr_entry["bugs"])
    for path in bug_file_paths:
        for bug in load_open_bugs(path):
            if bug.get("key") and bug["key"] not in seen_open_keys:
                seen_open_keys.add(bug["key"])
                all_open_bugs.append(bug)

    open_bugs_data = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "total": len(all_open_bugs), "issues": all_open_bugs} if all_open_bugs else None

    if ignore_keys:
        print(f"  Ignoring {len(ignore_keys)} closed bug(s): {', '.join(sorted(ignore_keys))}")
        if open_bugs_data and open_bugs_data.get("issues"):
            open_bugs_data["issues"] = [b for b in open_bugs_data["issues"] if b.get("key") not in ignore_keys]
            open_bugs_data["total"] = len(open_bugs_data["issues"])
        for version in bug_data:
            for cand in bug_data[version]:
                cand["duplicates"] = [d for d in cand.get("duplicates", []) if d.get("key") not in ignore_keys]

    bugs_tab_data = build_bugs_tab_data(open_bugs_data, bug_data, pr_entry["bugs"], releases_data, pr_data, all_bug_candidates, ignore_keys)

    bugs_dir = os.path.join(workdir, "bugs")
    os.makedirs(bugs_dir, exist_ok=True)
    bugs_summary_path = os.path.join(bugs_dir, "bug-matches-summary.json")
    with open(bugs_summary_path, "w") as f:
        json.dump(bugs_tab_data, f, indent=2)

    # Load container image health data
    images_data = load_images_data(workdir, releases)
    images_tab_data = build_images_tab_data(images_data, releases) if images_data else None

    # Set graphs directory and load Chart.js for rendering
    global _GRAPHS_DIR, _CHARTJS_SRC, _PCP_CHARTS_SRC
    graphs_dir = os.path.join(workdir, "graphs")
    if os.path.isdir(graphs_dir):
        search_dirs = [
            os.path.dirname(os.path.abspath(sys.argv[0])),
            os.path.dirname(os.path.abspath(__file__)),
        ]
        for sdir in search_dirs:
            chartjs_path = os.path.join(sdir, "pcp-graphs", "vendor", "chart.umd.min.js")
            charts_path = os.path.join(sdir, "pcp-graphs", "pcp-charts.js")
            if os.path.isfile(chartjs_path) and os.path.isfile(charts_path):
                with open(chartjs_path) as f:
                    _CHARTJS_SRC = f.read()
                with open(charts_path) as f:
                    _PCP_CHARTS_SRC = f.read()
                _GRAPHS_DIR = graphs_dir
                break
        else:
            print("WARNING: Chart.js or pcp-charts.js not found, "
                  "interactive PCP charts will not render", file=sys.stderr)

    # Read diagnostics
    diagnostics_path = os.path.join(workdir, "diagnostics.txt")
    diagnostics_text = None
    if os.path.isfile(diagnostics_path):
        try:
            with open(diagnostics_path, encoding="utf-8", errors="replace") as f:
                diagnostics_text = f.read()
        except OSError as e:
            print(f"WARNING: could not read {diagnostics_path}: {e}", file=sys.stderr)

    # Generate HTML
    timestamp = datetime.now(timezone.utc)
    html_content = generate_html(component_title, releases_data, all_bug_candidates, pr_data, pr_status, timestamp, pr_error, bugs_tab_data, images_tab_data, index_data, COMPONENT_JIRA_CREATE.get(component), status_data=status_data, diagnostics_text=diagnostics_text)

    output_path = os.path.join(workdir, f"report-{component}-ci-doctor.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Summary
    print("\nSummary:")
    print("  Periodics:")
    for version in releases:
        rdata = releases_data[version]
        status = status_data.get(version)
        if rdata and rdata.get("collection_error"):
            print(f"    Release {version}: ERROR - data collection failed")
        elif rdata:
            extra = ""
            if status:
                total = len(status)
                passed = sum(1 for j in status if j.get("status") == "success")
                rate = round(passed / total * 100) if total > 0 else 0
                extra = f" ({passed}/{total} passed, {rate}% pass rate)"
            print(f"    Release {version}: {rdata['total_failed']} failed periodic jobs{extra}")
        else:
            print(f"    Release {version}: no data")
    print("  Pull Requests:")
    if pr_error:
        print("    ERROR - data collection failed")
    elif pr_status:
        pr_total_failed = sum(p.get("failed", 0) for p in pr_status)
        pr_total_pending = sum(p.get("pending", 0) for p in pr_status)
        parts = [f"{len(pr_status)} PRs", f"{pr_total_failed} failed jobs"]
        if pr_total_pending:
            parts.append(f"{pr_total_pending} running")
        print(f"    {', '.join(parts)}")
    elif pr_data and pr_data.get("has_content"):
        print(f"    {len(pr_data['prs'])} PRs with {pr_data['total_failed']} total failed jobs")
    else:
        print("    No PR data")
    print("  Bugs:")
    if bugs_tab_data["jira_query_available"]:
        print(f"    {bugs_tab_data['total_open']} open AI-generated bugs"
              f" ({len(bugs_tab_data['linked'])} linked, {len(bugs_tab_data['unlinked'])} not linked)")
    elif bugs_tab_data["linked"]:
        print(f"    {len(bugs_tab_data['linked'])} linked bugs (JIRA query not available)")
    else:
        print("    No bug data")
    print("  Image Health:")
    if images_tab_data and images_tab_data.get("has_data"):
        for release, rel in sorted(images_tab_data["releases"].items()):
            worst = rel["latest_grade"]
            grade_str = f" (grade {worst})" if worst else ""
            repo_names = ", ".join(r["display_name"] for r in rel["repos"])
            print(f"    Release {release}: {repo_names}{grade_str}")
    else:
        print("    No container image data")
    print(f"\nHTML report generated: {output_path}")


if __name__ == "__main__":
    main()
