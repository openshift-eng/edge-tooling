#!/usr/bin/env python3
"""
Generate an HTML report from CI analysis JSON files.

Shared across components (MicroShift, LVMS, etc.) via symlinks in each
plugin's scripts/ directory.

Usage:
    create-report.py --component <component> [--workdir DIR] <release1,release2,...>
"""

import base64
import json
import sys
import os
import re
import html as html_mod
import glob as glob_mod
from datetime import datetime, timezone


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

CSS = """\
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; color: #333; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 8px; font-size: 1.4em; margin: 10px 0; }
        h2 { font-size: 1.15em; margin: 0; }
        h3 { font-size: 1.05em; margin: 0 0 8px 0; }
        .release-section h3 { margin: 18px 0 4px 0; }
        .release-section { background: white; border-radius: 8px; padding: 15px; margin: 15px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .release-header { display: flex; justify-content: space-between; align-items: center; }
        .release-header h2 { color: #16213e; margin: 0; }
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
        .no-bugs { color: #6c757d; font-style: italic; font-size: 0.85em; }
        .toc { background: white; border-radius: 8px; padding: 15px; margin: 15px 0; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .toc ul { list-style: none; padding-left: 0; }
        .toc li { padding: 5px 0; }
        .toc a { color: #0366d6; text-decoration: none; }
        .toc a:hover { text-decoration: underline; }
        .toc-header { display: flex; justify-content: space-between; align-items: center; }
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
        .perf-graphs img { max-width: 100%; height: auto; border: 1px solid #dee2e6; border-radius: 4px; }
        .graph-tabs { display: flex; gap: 0; margin: 4px 0 0 0; border-bottom: 2px solid #dee2e6; }
        .graph-tab-btn { padding: 4px 14px; border: 1px solid #dee2e6; border-bottom: none; border-radius: 4px 4px 0 0; background: #e9ecef; color: #495057; font-size: 0.82em; font-weight: 600; cursor: pointer; margin-bottom: -2px; }
        .graph-tab-btn.active { background: #fff; border-bottom: 2px solid #fff; color: #212529; }
        .graph-pane { display: none; padding: 6px 0; }
        .graph-pane.active { display: block; }
        .anchor-link, .section-anchor { color: #adb5bd; text-decoration: none; cursor: pointer; }
        .anchor-link:hover, .section-anchor:hover { color: #0366d6; }
        .anchor-link { font-size: 0.85em; }
        .section-anchor { font-size: 0.75em; margin-left: 8px; vertical-align: middle; }
        .copy-toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #333; color: #fff; padding: 8px 16px; border-radius: 6px; font-size: 0.85em; z-index: 1000; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
        .copy-toast.show { opacity: 1; }
        .bugs-table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        .bugs-table th { text-align: left; padding: 8px 6px; border-bottom: 2px solid #dee2e6; font-size: 0.85em; color: #6c757d; text-transform: uppercase; cursor: pointer; user-select: none; white-space: nowrap; }
        .bugs-table th:hover { color: #333; }
        .bugs-table th:after { content: ' \\25B2\\25BC'; font-size: 0.7em; opacity: 0.35; letter-spacing: -2px; }
        .bugs-table th.sort-asc:after { content: ' \\25B2'; font-size: 0.8em; opacity: 1; color: #0d6efd; letter-spacing: normal; }
        .bugs-table th.sort-desc:after { content: ' \\25BC'; font-size: 0.8em; opacity: 1; color: #0d6efd; letter-spacing: normal; }
        .bugs-table td { padding: 6px; border-bottom: 1px solid #eee; font-size: 0.9em; vertical-align: middle; }
        .bugs-table tr:hover { background: #f8f9fa; }
        .link-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75em; font-weight: 700; text-transform: uppercase; }
        .link-badge-unlinked { background: #fff3cd; color: #856404; }
        .index-image-info { background: #e8f4fd; border-left: 3px solid #0366d6; padding: 8px 12px; margin: 8px 0; font-size: 0.9em; }
        .index-image-info code { background: #f1f1f1; padding: 2px 4px; border-radius: 3px; font-size: 0.9em; }"""

JS = """\
function showTab(e, name) {
    document.querySelectorAll('.tab-content').forEach(function(el) {
        el.classList.remove('active');
    });
    document.querySelectorAll('.tab-btn').forEach(function(el) {
        el.classList.remove('active');
    });
    document.getElementById('tab-' + name).classList.add('active');
    e.target.classList.add('active');
}
document.querySelectorAll('.col-title').forEach(function(el) {
    el.addEventListener('click', function() {
        this.classList.toggle('active');
        var row = this.closest('tr').nextElementSibling;
        if (row && row.classList.contains('detail-row')) {
            row.classList.toggle('show');
        }
    });
});
function toggleGraph(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}
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
                if (ft) {
                    var t = ft.textContent.trim().toLowerCase();
                    if (t === 'build') bd.build++;
                    else if (t === 'infra') bd.infra++;
                    else bd.test++;
                }
            }
        });
        var lbl = total === 1 ? 'failure' : 'failures';
        var summary = total + ' ' + lbl + ' (' + bd.build + ' build, ' + bd.test + ' test, ' + bd.infra + ' infra)';
        var toc = document.querySelector('.toc-counts[data-release="' + id + '"]');
        if (toc) toc.textContent = summary;
        var badge = sec.querySelector('.release-badge');
        if (badge) {
            badge.textContent = total + ' ' + lbl;
            badge.className = 'badge release-badge ' + (total === 0 ? 'badge-ok' : total >= 5 ? 'badge-critical' : 'badge-issues');
        }
        var bdb = sec.querySelector('.bd-build');
        var bdt = sec.querySelector('.bd-test');
        var bdi = sec.querySelector('.bd-infra');
        if (bdb) bdb.textContent = bd.build;
        if (bdt) bdt.textContent = bd.test;
        if (bdi) bdi.textContent = bd.infra;
    });
}
function showGraphTab(btn, paneId) {
    var container = btn.closest('.perf-graphs');
    container.querySelectorAll('.graph-tab-btn').forEach(function(b) { b.classList.remove('active'); });
    container.querySelectorAll('.graph-pane').forEach(function(p) { p.classList.remove('active'); });
    btn.classList.add('active');
    document.getElementById(paneId).classList.add('active');
}
document.getElementById('loading').style.display='none';
document.querySelector('.container').style.display='';
(function() {
    var toast = document.createElement('div');
    toast.className = 'copy-toast';
    toast.textContent = 'Link copied';
    document.body.appendChild(toast);
    var timer;
    function copyAnchor(e) {
        e.preventDefault();
        e.stopPropagation();
        var href = e.currentTarget.getAttribute('href');
        var url = location.href.split('#')[0] + href;
        if (!navigator.clipboard || !navigator.clipboard.writeText) {
            location.hash = href.slice(1);
            return;
        }
        navigator.clipboard.writeText(url).then(function() {
            toast.classList.add('show');
            clearTimeout(timer);
            timer = setTimeout(function() { toast.classList.remove('show'); }, 1500);
        }).catch(function() {
            location.hash = href.slice(1);
        });
    }
    document.querySelectorAll('.anchor-link, .section-anchor').forEach(function(el) {
        el.addEventListener('click', copyAnchor);
    });
})();
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
                if (detail && detail.classList.contains('detail-row')) {
                    detail.classList.add('show');
                }
            }
        }
        var section = target.closest('.tab-content');
        if (section && !section.classList.contains('active')) {
            document.querySelectorAll('.tab-content').forEach(function(el) { el.classList.remove('active'); });
            document.querySelectorAll('.tab-btn').forEach(function(el) { el.classList.remove('active'); });
            section.classList.add('active');
            document.querySelectorAll('.tab-btn').forEach(function(el) {
                if (el.getAttribute('onclick') && el.getAttribute('onclick').indexOf(section.id.replace('tab-', '')) !== -1) {
                    el.classList.add('active');
                }
            });
        }
        requestAnimationFrame(function() {
            target.scrollIntoView({ behavior: 'smooth' });
        });
    }
    openAnchor();
    window.addEventListener('hashchange', openAnchor);
})();
document.querySelectorAll('.bugs-table').forEach(function(table) {
    var headers = table.querySelectorAll('th');
    function sortBy(colIdx, asc) {
        headers.forEach(function(h) { h.classList.remove('sort-asc', 'sort-desc'); });
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
        th.addEventListener('click', function() {
            sortBy(colIdx, !th.classList.contains('sort-asc'));
        });
    });
    if (headers.length >= 2) sortBy(headers.length - 2, false);
});"""


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_files(workdir, releases):
    result = {"releases": {}, "prs": {"summary": None, "status": None, "bugs": [], "error": None}}

    jobs_dir = os.path.join(workdir, "jobs")
    bugs_dir = os.path.join(workdir, "bugs")

    for version in releases:
        entry = {"summary": None, "bugs": None, "jobs": None, "error": None}
        path = os.path.join(jobs_dir, f"release-{version}-summary.json")
        if os.path.exists(path):
            entry["summary"] = path
        path = os.path.join(bugs_dir, f"bug-matches-{version}.json")
        if os.path.exists(path):
            entry["bugs"] = path
        path = os.path.join(jobs_dir, f"release-{version}-jobs.json")
        if os.path.exists(path):
            entry["jobs"] = path
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


def _format_release_links(links):
    """Format release associations as linked '4.20 (2), 4.22 (1)'."""
    by_release = {}
    for link in links:
        rel = link["release"]
        by_release[rel] = by_release.get(rel, 0) + link["affected_jobs"]
    parts = []
    for r, c in sorted(by_release.items()):
        anchor = "tab-pull-requests" if r == "PRs" else f"release-{_e(r)}"
        parts.append(f'<a href="#{anchor}">{_e(r)}</a> ({c})')
    return ", ".join(parts)


_PRIORITY_ORDER = {"blocker": 0, "critical": 1, "major": 2, "normal": 3, "minor": 4, "trivial": 5}


def _bug_sort_key(bug):
    prio = _PRIORITY_ORDER.get(bug.get("priority", "").lower(), 99)
    return (prio, bug.get("key", ""))


def _render_bugs_table(bugs, show_releases=True):
    lines = []
    lines.append('            <table class="bugs-table">')
    lines.append("            <thead><tr>")
    cols = '<th>JIRA</th><th>Status</th><th>Assignee</th><th>Summary</th>'
    if show_releases:
        cols += '<th>Releases</th>'
    cols += '<th>Updated</th><th></th>'
    lines.append(f'                {cols}')
    lines.append("            </tr></thead>")
    lines.append("            <tbody>")
    for bug in bugs:
        key = _e(bug["key"])
        href = f"https://issues.redhat.com/browse/{key}"
        summary = _e(bug.get("summary", ""))
        status = _e(bug.get("status", ""))
        assignee = _e(bug.get("assignee", ""))
        updated = _e(bug.get("updated", ""))
        anchor_id = f'bug-{key}'
        lines.append(f'            <tr id="{anchor_id}">')
        lines.append(f'                <td><a href="{href}" target="_blank">{key}</a></td>')
        lines.append(f"                <td>{status}</td>")
        lines.append(f"                <td>{assignee}</td>")
        lines.append(f"                <td>{summary}</td>")
        if show_releases:
            releases_cell = _format_release_links(bug["links"]) if bug.get("links") else ""
            lines.append(f"                <td>{releases_cell}</td>")
        lines.append(f"                <td>{updated}</td>")
        lines.append(f'                <td><a href="#{anchor_id}" class="anchor-link" title="Copy link to this bug">&#128279;</a></td>')
        lines.append("            </tr>")
    lines.append("            </tbody>")
    lines.append("            </table>")
    return lines


def render_bugs_section(bugs_data):
    """Render the Bugs tab HTML."""
    linked = bugs_data["linked"]
    unlinked = bugs_data["unlinked"]
    jira_available = bugs_data["jira_query_available"]

    if not linked and not unlinked:
        return (
            '        <div class="release-section">\n'
            "            <p>No bug data available. "
            "Run the full doctor workflow to populate bug information.</p>\n"
            "        </div>"
        )

    lines = []

    # Summary cards
    total_linked = len(linked)
    total_unlinked = len(unlinked)
    total = bugs_data["total_open"] if jira_available else total_linked

    lines.append('        <div class="release-section">')
    lines.append('            <div class="release-header">')
    lines.append('                <h2>AI-Generated Bugs</h2>')
    lines.append('            </div>')
    lines.append('            <div class="overview-grid">')
    lines.append('                <div class="overview-card">')
    lines.append(f'                    <div class="number">{total}</div>')
    lines.append('                    <div class="label">Total Open</div>')
    lines.append('                </div>')
    lines.append('                <div class="overview-card">')
    css = "status-pass" if total_linked > 0 else ""
    lines.append(f'                    <div class="number {css}">{total_linked}</div>')
    lines.append('                    <div class="label">Linked to Failures</div>')
    lines.append('                </div>')
    if jira_available:
        lines.append('                <div class="overview-card">')
        css = "status-fail" if total_unlinked > 0 else ""
        lines.append(f'                    <div class="number {css}">{total_unlinked}</div>')
        lines.append('                    <div class="label">Not Linked</div>')
        lines.append('                </div>')
    lines.append('            </div>')

    if not jira_available:
        lines.append(
            '            <p class="job-date">Only bugs linked to current failures are shown. '
            "Run the full doctor workflow to include all open AI-generated bugs.</p>"
        )

    # Linked table
    if linked:
        lines.append('            <h3>Linked to Failures</h3>')
        lines.extend(_render_bugs_table(sorted(linked, key=_bug_sort_key), show_releases=True))

    # Unlinked table
    if unlinked and jira_available:
        lines.append('            <h3>Not Linked</h3>')
        lines.extend(_render_bugs_table(sorted(unlinked, key=_bug_sort_key), show_releases=False))

    lines.append("        </div>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------

def _e(text):
    return html_mod.escape(str(text)) if text else ""


def _render_confidence_badge(issue):
    """Confidence badge for the issue title; empty string when unset."""
    conf = (issue.get("confidence") or "").lower()
    if conf not in ("high", "medium", "low"):
        return ""
    return (f'<span class="confidence-badge confidence-{conf}"'
            f' title="Root cause analysis confidence">{conf}</span>')


def _render_investigation(issue):
    """Render scenario chips, causal chain, and analysis gaps for an issue.

    Returns a list of HTML lines; empty when the issue (old summary files)
    has none of the investigation fields.
    """
    lines = []
    scenarios = issue.get("scenarios") or []
    if scenarios:
        chips = "".join(f'<span class="scenario-chip">{_e(s)}</span>' for s in scenarios)
        lines.append(f'                <div class="scenarios"><strong>Scenarios:</strong> {chips}</div>')
    chain = [
        link for link in (issue.get("causal_chain") or [])
        if isinstance(link, dict) and link.get("cause")
    ]
    if chain:
        lines.append('                <div class="causal-chain"><strong>Causal chain:</strong><ol>')
        for link in chain:
            item = _e(link.get("cause"))
            if link.get("evidence"):
                item += f' — <span class="evidence">{_e(link["evidence"])}</span>'
            if link.get("quote"):
                item += f' <code>{_e(link["quote"])}</code>'
            lines.append(f'                    <li>{item}</li>')
        lines.append('                </ol></div>')
    gaps = [g for g in (issue.get("analysis_gaps") or []) if g]
    if gaps:
        lines.append(f'                <div class="analysis-gaps">Evidence gaps: {_e(", ".join(gaps))}</div>')
    return lines

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


def _render_index_image(index_info):
    """Render index image info box HTML (LVMS-specific)."""
    if not index_info:
        return ""
    lines = ['            <div class="index-image-info">']
    if index_info.get("image"):
        lines.append(f'                <strong>Catalog Index Image:</strong> <code>{_e(index_info["image"])}</code><br>')
    if index_info.get("digest"):
        lines.append(f'                <strong>Digest:</strong> <code>{_e(index_info["digest"])}</code><br>')
    if index_info.get("built"):
        lines.append(f'                <strong>Built:</strong> {_e(index_info["built"])}<br>')
    if index_info.get("commit"):
        commit = index_info["commit"]
        short = commit[:12] if len(commit) >= 12 else commit
        lines.append(
            f'                <strong>Source Commit:</strong> '
            f'<a href="https://github.com/openshift/lvm-operator/commit/{_e(commit)}" target="_blank">{_e(short)}</a>'
        )
    if index_info.get("error"):
        lines.append(f'                <br><em style="color:#856404;">Inspect failed: {_e(index_info["error"])}</em>')
    lines.append("            </div>")
    return "\n".join(lines)


# Graph workdir — set by main() before rendering
_GRAPHS_DIR = None


def _extract_build_id(url):
    """Extract build_id (last numeric path component) from a Prow job URL."""
    if not url:
        return None
    m = re.search(r"/(\d+)/?$", url)
    return m.group(1) if m else None


_graph_counter = 0

# Cache of loaded graph data: build_id -> list of (label, b64)
_graph_cache = {}


def _load_job_graphs(build_id):
    """Load and cache base64-encoded graphs for a build_id."""
    if build_id in _graph_cache:
        return _graph_cache[build_id]
    graphs = []
    if _GRAPHS_DIR:
        graph_dir = os.path.join(_GRAPHS_DIR, build_id)
        if os.path.isdir(graph_dir):
            for png in sorted(glob_mod.glob(os.path.join(graph_dir, "*.png"))):
                try:
                    label = re.sub(r"^\d+_", "", os.path.splitext(os.path.basename(png))[0]).replace("_", " ").title()
                    with open(png, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("ascii")
                    graphs.append((label, b64))
                except Exception as e:
                    print(f"WARNING: skipping {png}: {e}", file=sys.stderr)
    _graph_cache[build_id] = graphs
    return graphs


def _render_job_with_graphs(job):
    """Render a single job list item with optional graph icon and inline graphs."""
    global _graph_counter
    date_str = f'<span class="job-date">[{_e(job["date"])}]</span>'
    url = job.get("url", "")
    name = _e(job["name"])

    if url:
        job_link = f'{date_str} <a href="{_e(url)}" target="_blank">{name}</a>'
    else:
        job_link = f'{date_str} {name}'

    bid = _extract_build_id(url)
    if not bid:
        return f"<li>{job_link}</li>"

    graphs = _load_job_graphs(bid)
    if not graphs:
        return f"<li>{job_link}</li>"

    _graph_counter += 1
    gid = f"gp{_graph_counter}"

    icon = f' <a class="graph-toggle" onclick="toggleGraph(\'{gid}\')" title="Host performance graphs">&#x1F4CA;</a>'

    # Build tabbed graph panel (hidden by default)
    header = '<div class="graph-source">Host metrics (PCP)</div>'
    if len(graphs) == 1:
        label, b64 = graphs[0]
        panel = (
            f'<div id="{gid}" class="perf-graphs" style="display:none">'
            f'{header}'
            f'<img src="data:image/png;base64,{b64}" alt="{_e(label)}"/>'
            f'</div>'
        )
    else:
        tabs = []
        panes = []
        for i, (label, b64) in enumerate(graphs):
            active = " active" if i == 0 else ""
            tid = f"{gid}-{i}"
            tabs.append(
                f'<button class="graph-tab-btn{active}" onclick="showGraphTab(this,\'{tid}\')">{_e(label)}</button>'
            )
            panes.append(
                f'<div id="{tid}" class="graph-pane{active}">'
                f'<img src="data:image/png;base64,{b64}" alt="{_e(label)}"/>'
                f'</div>'
            )
        panel = (
            f'<div id="{gid}" class="perf-graphs" style="display:none">'
            f'{header}'
            f'<div class="graph-tabs">{"".join(tabs)}</div>'
            + "".join(panes)
            + '</div>'
        )

    return f"<li>{job_link}{icon}{panel}</li>"


def _badge_class(total_failed, has_critical=False):
    if total_failed == 0:
        return "badge-ok"
    if total_failed >= 5 or has_critical:
        return "badge-critical"
    return "badge-issues"


def _render_bug_links(bug_match):
    if not bug_match:
        return '<span class="no-bugs">No tracked bugs</span>'
    has_dups = bool(bug_match.get("duplicates"))
    has_regs = bool(bug_match.get("regressions"))
    if not has_dups and not has_regs:
        return '<span class="no-bugs">No tracked bugs</span>'

    parts = []
    if has_dups:
        parts.append("<strong>Bugs:</strong><br>")
        for d in bug_match["duplicates"]:
            assignee = d.get("assignee", "")
            assignee_part = f", {_e(assignee)}" if assignee else ""
            parts.append(
                f'<a class="bug-tag bug-tag-open" '
                f'href="https://issues.redhat.com/browse/{_e(d["key"])}" '
                f'target="_blank">{_e(d["key"])}</a> '
                f'<span class="job-date">{_e(d["summary"])} ({_e(d["status"])}{assignee_part})</span><br>'
            )
    if has_regs:
        parts.append("<strong>Regressions:</strong><br>")
        for r in bug_match["regressions"]:
            assignee = r.get("assignee", "")
            assignee_part = f", {_e(assignee)}" if assignee else ""
            parts.append(
                f'<a class="bug-tag bug-tag-regression" '
                f'href="https://issues.redhat.com/browse/{_e(r["key"])}" '
                f'target="_blank">{_e(r["key"])} &#x27F2;</a> '
                f'<span class="job-date">{_e(r["summary"])} ({_e(r["status"])}{assignee_part})</span><br>'
            )
    return "".join(parts)


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def render_release_section(version, rdata, bug_candidates, index_info=None):
    if rdata is None:
        return (
            f'        <div class="release-section" id="release-{_e(version)}">\n'
            '            <div class="release-header">\n'
            f'                <h2>Release {_e(version)}</h2>\n'
            '                <span class="badge badge-nodata">no data</span>\n'
            '            </div>\n'
            "            <p>Analysis failed to produce results.</p>\n"
            "        </div>"
        )

    if rdata.get("collection_error"):
        return (
            f'        <div class="release-section" id="release-{_e(version)}">\n'
            '            <div class="release-header">\n'
            f'                <h2>Release {_e(version)}</h2>\n'
            '                <span class="badge badge-nodata">collection error</span>\n'
            '            </div>\n'
            f'            <pre>Data collection failed: {_e(rdata["collection_error"])}</pre>\n'
            "        </div>"
        )

    total = rdata["total_failed"]
    has_critical = any(i.get("severity", "").upper() == "CRITICAL" for i in rdata["issues"])
    badge = _badge_class(total, has_critical)
    b = rdata["breakdown"]

    lines = []
    lines.append(f'        <div class="release-section" id="release-{_e(version)}">')
    lines.append('            <div class="release-header">')
    lines.append(f'                <h2>Release {_e(version)}<a href="#release-{_e(version)}" class="section-anchor" title="Copy link to this section">&#128279;</a></h2>')
    label = "failure" if total == 1 else "failures"
    lines.append(f'                <span class="badge {badge} release-badge" data-release="{_e(version)}">{total} {label}</span>')
    lines.append("            </div>")

    idx_html = _render_index_image(index_info)
    if idx_html:
        lines.append(idx_html)

    lines.append('            <div class="breakdown">')
    lines.append(f'                <span class="breakdown-item"><strong class="bd-build">{b["build"]}</strong> Build</span>')
    lines.append(f'                <span class="breakdown-item"><strong class="bd-test">{b["test"]}</strong> Test</span>')
    lines.append(f'                <span class="breakdown-item"><strong class="bd-infra">{b["infrastructure"]}</strong> Infrastructure</span>')
    lines.append("            </div>")

    lines.append('            <table class="issues-table">')
    for issue in rdata["issues"]:
        bug_match = match_issue_to_bugs(issue["title"], bug_candidates)
        jc = issue["job_count"]
        sev = issue.get("severity", "UNKNOWN").upper()
        sev_css = f"severity-{sev.lower()}" if sev in ("HIGH", "MEDIUM", "LOW", "CRITICAL") else ""
        ftype = issue.get("failure_type", "test")
        ftype_label = "INFRA" if ftype == "infrastructure" else ftype.upper()
        ftype_css = "ftype-infra" if ftype == "infrastructure" else f"ftype-{ftype}"
        jobs_label = f'{jc} {"job" if jc == 1 else "jobs"}'

        job_dates = sorted({j["date"][:10] for j in issue.get("affected_jobs", []) if j.get("date")})
        dates_attr = f' data-dates="{" ".join(job_dates)}"' if job_dates else ""
        anchor_id = f'release-{_e(version)}-{issue["number"]}'
        lines.append(f'            <tr class="issue-row" id="{anchor_id}"{dates_attr}>')
        lines.append(f'                <td class="col-sev"><span class="severity-badge {sev_css}">{sev}</span></td>')
        lines.append(f'                <td class="col-ftype"><span class="ftype-badge {ftype_css}">{ftype_label}</span></td>')
        lines.append(f'                <td class="col-title">{_e(issue["title"])}</td>')
        lines.append(f'                <td class="col-jobs">{jobs_label}</td>')
        lines.append(f'                <td class="col-link"><a href="#{anchor_id}" class="anchor-link" title="Copy link to this issue">&#128279;</a></td>')
        lines.append('            </tr>')
        lines.append('            <tr class="detail-row"><td colspan="5">')
        if issue.get("root_cause"):
            conf_badge = _render_confidence_badge(issue)
            lines.append(f'                <div class="root-cause"><strong>Root Cause:</strong> {conf_badge} {_e(issue["root_cause"])}</div>')
        lines.extend(_render_investigation(issue))
        lines.append(f'                <div class="bug-links">{_render_bug_links(bug_match)}</div>')
        if issue.get("affected_jobs"):
            lines.append("                <p><strong>Affected Jobs:</strong></p><ul>")
            for job in issue["affected_jobs"]:
                lines.append(f"                    {_render_job_with_graphs(job)}")
            lines.append("                </ul>")
        if issue.get("next_steps"):
            lines.append(f"                <p><em>Next Steps:</em> {_e(issue['next_steps'])}</p>")
        lines.append("            </td></tr>")
    lines.append('            </table>')

    lines.append("        </div>")
    return "\n".join(lines)


def render_pr_section(pr_data, bug_candidates, pr_status, pr_error=None):
    """Render the Pull Requests tab.

    pr_data: analyzed PR summary (from aggregate), may be None.
    bug_candidates: flat list of all bug candidates (pooled across all sources).
    pr_status: list of all PR status snapshots (from prepare), may be None.
    pr_error: collection error message string, or None.
    """
    if pr_error:
        return (
            '        <div class="release-section">\n'
            '            <div class="release-header">\n'
            "                <h2>Rebase Pull Requests</h2>\n"
            '                <span class="badge badge-nodata">collection error</span>\n'
            "            </div>\n"
            f'            <pre>Data collection failed: {_e(pr_error)}</pre>\n'
            "        </div>"
        )

    # Build a lookup of analyzed PRs by number
    analyzed = {}
    if pr_data and pr_data.get("has_content"):
        for pr in pr_data["prs"]:
            analyzed[pr["number"]] = pr

    # Build the full PR list: all PRs from status, merged with analysis
    all_prs = []
    if pr_status:
        for s in pr_status:
            num = s["pr_number"]
            entry = {
                "number": num,
                "title": s.get("title", ""),
                "url": s.get("url", ""),
                "passed": s.get("passed", 0),
                "failed": s.get("failed", 0),
                "pending": s.get("pending", 0),
                "total": s.get("total", 0),
            }
            if num in analyzed:
                entry["analysis"] = analyzed[num]
            all_prs.append(entry)
    elif analyzed:
        # No status file — fall back to analyzed data only
        for pr in pr_data["prs"]:
            all_prs.append({
                "number": pr["number"],
                "title": pr.get("title", ""),
                "url": pr.get("url", ""),
                "passed": 0,
                "failed": pr.get("failed", 0),
                "pending": 0,
                "total": pr.get("failed", 0),
                "analysis": pr,
            })

    if not all_prs:
        return (
            '        <div class="release-section">\n'
            '            <div class="release-header">\n'
            "                <h2>Rebase Pull Requests</h2>\n"
            '                <span class="badge badge-ok">0 failures</span>\n'
            "            </div>\n"
            "            <p>No open rebase pull requests found.</p>\n"
            "        </div>"
        )

    # TOC
    toc_lines = []
    toc_lines.append('        <div class="toc">')
    toc_lines.append('            <h3>Table of Contents</h3>')
    toc_lines.append('            <ul>')
    for pr in all_prs:
        analysis = pr.get("analysis")
        if analysis:
            b = analysis.get("breakdown", {})
        else:
            b = {"build": 0, "test": 0, "infrastructure": 0}
        pending = pr.get("pending", 0)
        suffix = f' &mdash; {pending} running' if pending else ''
        toc_lines.append(
            f'                <li><a href="#pr-{pr["number"]}">PR# {pr["number"]}</a>'
            f' &mdash; {pr["failed"]} failures ({b.get("build", 0)} build, {b.get("test", 0)} test, {b.get("infrastructure", 0)} infra){suffix}</li>'
        )
    toc_lines.append('            </ul>')
    toc_lines.append('        </div>')

    # Sections
    lines = []
    for pr in all_prs:
        analysis = pr.get("analysis")
        total_failed = pr["failed"]
        badge = _badge_class(total_failed)

        lines.append(f'        <div class="release-section" id="pr-{pr["number"]}">')
        lines.append('            <div class="release-header">')
        pr_link = f'<a href="{_e(pr["url"])}" target="_blank" title="{_e(pr["title"])}">PR# {pr["number"]}</a>' if pr.get("url") else f'<span title="{_e(pr["title"])}">PR# {pr["number"]}</span>'
        pr_release_m = re.search(r"rebase-(release-\d+\.\d+|main)", pr.get("title", ""))
        pr_release_label = f' (rebase {pr_release_m.group(1)})' if pr_release_m else f': {_e(pr["title"])}' if pr.get("title") else ''
        lines.append(f'                <h2>{pr_link}{pr_release_label}<a href="#pr-{pr["number"]}" class="section-anchor" title="Copy link to this section">&#128279;</a></h2>')
        label = "failure" if total_failed == 1 else "failures"
        lines.append(f'                <span class="badge {badge}">{total_failed} {label}</span>')

        lines.append("            </div>")

        # Breakdown: same format as periodics (Build/Test/Infrastructure)
        # Plus job status (passed/running) when available
        pending = pr.get("pending", 0)
        if analysis and analysis.get("breakdown"):
            b = analysis["breakdown"]
        else:
            b = {"build": 0, "test": 0, "infrastructure": 0}
        lines.append('            <div class="breakdown">')
        lines.append(f'                <span class="breakdown-item"><strong>{b.get("build", 0)}</strong> Build</span>')
        lines.append(f'                <span class="breakdown-item"><strong>{b.get("test", 0)}</strong> Test</span>')
        lines.append(f'                <span class="breakdown-item"><strong>{b.get("infrastructure", 0)}</strong> Infrastructure</span>')
        if pr["passed"]:
            lines.append(f'                <span class="breakdown-item"><strong>{pr["passed"]}</strong> Passed</span>')
        if pending:
            lines.append(f'                <span class="breakdown-item"><strong>{pending}</strong> Running</span>')
        lines.append("            </div>")

        if analysis and analysis.get("issues"):

            lines.append('            <table class="issues-table">')
            for issue in analysis["issues"]:
                bug_match = match_issue_to_bugs(issue.get("title", ""), bug_candidates)
                jc = issue["job_count"]
                sev = issue.get("severity", "UNKNOWN").upper()
                sev_css = f"severity-{sev.lower()}" if sev in ("HIGH", "MEDIUM", "LOW", "CRITICAL") else ""
                ftype = issue.get("failure_type", "test")
                ftype_label = "INFRA" if ftype == "infrastructure" else ftype.upper()
                ftype_css = "ftype-infra" if ftype == "infrastructure" else f"ftype-{ftype}"
                jobs_label = f'{jc} {"job" if jc == 1 else "jobs"}'

                anchor_id = f'pr-{pr["number"]}-{issue["number"]}'
                lines.append(f'            <tr class="issue-row" id="{anchor_id}">')
                lines.append(f'                <td class="col-sev"><span class="severity-badge {sev_css}">{sev}</span></td>')
                lines.append(f'                <td class="col-ftype"><span class="ftype-badge {ftype_css}">{ftype_label}</span></td>')
                lines.append(f'                <td class="col-title">{_e(issue["title"])}</td>')
                lines.append(f'                <td class="col-jobs">{jobs_label}</td>')
                lines.append(f'                <td class="col-link"><a href="#{anchor_id}" class="anchor-link" title="Copy link to this issue">&#128279;</a></td>')
                lines.append('            </tr>')
                lines.append('            <tr class="detail-row"><td colspan="5">')
                if issue.get("root_cause"):
                    conf_badge = _render_confidence_badge(issue)
                    lines.append(f'                <div class="root-cause"><strong>Root Cause:</strong> {conf_badge} {_e(issue["root_cause"])}</div>')
                lines.extend(_render_investigation(issue))
                lines.append(f'                <div class="bug-links">{_render_bug_links(bug_match)}</div>')
                if issue.get("affected_jobs"):
                    lines.append("                <p><strong>Affected Jobs:</strong></p><ul>")
                    for job in issue["affected_jobs"]:
                        lines.append(f"                    {_render_job_with_graphs(job)}")
                    lines.append("                </ul>")
                if issue.get("next_steps"):
                    lines.append(f"                <p><em>Next Steps:</em> {_e(issue['next_steps'])}</p>")
                lines.append("            </td></tr>")
            lines.append('            </table>')

        lines.append("        </div>")
    return "\n".join(toc_lines) + "\n\n" + "\n".join(lines)


def generate_html(component_title, releases_data, all_bug_candidates, pr_data, pr_status, timestamp, pr_error=None, bugs_tab_data=None, index_data=None):
    date_str = timestamp.strftime("%Y-%m-%d")
    time_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

    cards = []
    for version, rdata in releases_data.items():
        if rdata and rdata.get("collection_error"):
            count = "!"
            css = "status-fail"
        elif rdata:
            count = rdata["total_failed"]
            css = "status-fail" if rdata["total_failed"] > 0 else "status-pass"
        else:
            count = "?"
            css = ""
        cards.append(
            '        <div class="overview-card">\n'
            f'            <div class="number {css}">{count}</div>\n'
            f'            <div class="label">Release {_e(version)}</div>\n'
            "        </div>"
        )
    # PR overview: count failures from status (all PRs) or analysis
    if pr_error:
        pr_failed_count = "!"
        pr_css = "status-fail"
    elif pr_status:
        pr_failed_count = sum(p.get("failed", 0) for p in pr_status)
        pr_css = "status-fail" if pr_failed_count > 0 else "status-pass"
    elif pr_data:
        pr_failed_count = pr_data.get("total_failed", 0)
        pr_css = "status-fail" if pr_failed_count > 0 else "status-pass"
    else:
        pr_failed_count = 0
        pr_css = "status-pass"
    cards.append(
        '        <div class="overview-card">\n'
        f'            <div class="number {pr_css}">{pr_failed_count}</div>\n'
        f'            <div class="label">Rebase PRs</div>\n'
        "        </div>"
    )

    toc = []
    for version, rdata in releases_data.items():
        if rdata and rdata.get("collection_error"):
            toc.append(
                f'                <li><a href="#release-{_e(version)}">Release {_e(version)}</a> &mdash; collection error</li>'
            )
        elif rdata:
            b = rdata["breakdown"]
            toc.append(
                f'                <li><a href="#release-{_e(version)}">Release {_e(version)}</a> &mdash; '
                f'<span class="toc-counts" data-release="{_e(version)}">'
                f'{rdata["total_failed"]} failures ({b["build"]} build, {b["test"]} test, {b["infrastructure"]} infra)'
                f'</span></li>'
            )
        else:
            toc.append(f'                <li><a href="#release-{_e(version)}">Release {_e(version)}</a> &mdash; no data</li>')

    sections = []
    _idx = index_data or {}
    for version, rdata in releases_data.items():
        sections.append(render_release_section(version, rdata, all_bug_candidates, _idx.get(version)))

    pr_section = render_pr_section(pr_data, all_bug_candidates, pr_status, pr_error)
    bugs_section = render_bugs_section(bugs_tab_data) if bugs_tab_data else ""

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{component_title} CI Doctor Report - {date_str}</title>
    <style>
{CSS}
    </style>
</head>
<body>
<div id="loading" style="display:flex;align-items:center;justify-content:center;height:80vh;font-family:sans-serif;color:#6c757d;font-size:1.2em;">Loading report&hellip;</div>
<div class="container" style="display:none">
    <h1>{component_title} CI Doctor Report</h1>
    <p class="timestamp">Generated: {time_str} UTC</p>

    <div class="overview-grid">
{chr(10).join(cards)}
    </div>

    <div class="tab-bar">
        <button class="tab-btn active" onclick="showTab(event, 'periodics')">Periodics</button>
        <button class="tab-btn" onclick="showTab(event, 'pull-requests')">Pull Requests</button>
        <button class="tab-btn" onclick="showTab(event, 'bugs')">Bugs</button>
    </div>

    <div id="tab-periodics" class="tab-content active">
        <div class="toc">
            <div class="toc-header">
                <h3>Table of Contents</h3>
                <label class="filter-toggle"><input type="checkbox" id="filter-today" onchange="filterToday(this.checked)"> Today only</label>
            </div>
            <ul>
{chr(10).join(toc)}
            </ul>
        </div>

{chr(10).join(sections)}
    </div>

    <div id="tab-pull-requests" class="tab-content">
{pr_section}
    </div>

    <div id="tab-bugs" class="tab-content">
{bugs_section}
    </div>

    <p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p><p>&nbsp;</p>
</div>
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

    # Set graphs directory for rendering
    global _GRAPHS_DIR
    graphs_dir = os.path.join(workdir, "graphs")
    if os.path.isdir(graphs_dir):
        _GRAPHS_DIR = graphs_dir

    # Generate HTML
    timestamp = datetime.now(timezone.utc)
    html_content = generate_html(component_title, releases_data, all_bug_candidates, pr_data, pr_status, timestamp, pr_error, bugs_tab_data, index_data)

    output_path = os.path.join(workdir, f"report-{component}-ci-doctor.html")
    with open(output_path, "w") as f:
        f.write(html_content)

    # Summary
    print("\nSummary:")
    print("  Periodics:")
    for version in releases:
        rdata = releases_data[version]
        if rdata and rdata.get("collection_error"):
            print(f"    Release {version}: ERROR - data collection failed")
        elif rdata:
            print(f"    Release {version}: {rdata['total_failed']} failed periodic jobs")
        else:
            print(f"    Release {version}: no data")
    print("  Pull Requests:")
    if pr_error:
        print("    ERROR - data collection failed")
    elif pr_status:
        pr_total_failed = sum(p.get("failed", 0) for p in pr_status)
        pr_total_pending = sum(p.get("pending", 0) for p in pr_status)
        parts = [f"{len(pr_status)} rebase PRs", f"{pr_total_failed} failed jobs"]
        if pr_total_pending:
            parts.append(f"{pr_total_pending} running")
        print(f"    {', '.join(parts)}")
    elif pr_data and pr_data.get("has_content"):
        print(f"    {len(pr_data['prs'])} rebase PRs with {pr_data['total_failed']} total failed jobs")
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
    print(f"\nHTML report generated: {output_path}")


if __name__ == "__main__":
    main()
