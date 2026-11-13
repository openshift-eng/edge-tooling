#!/usr/bin/env python3
"""Prepare bug candidates from per-job analysis reports."""

import argparse
import json
import sys
import os
import re
import glob as glob_mod
from datetime import datetime, timezone
from itertools import combinations

import jira_search
from classify import classify_breakdown
from parse import (
    STOP_WORDS, normalize_step_name, cluster_by_similarity,
    group_by_signature, grouping_text, parse_structured_summary, tokenize,
)


# Additional stop words filtered only during keyword extraction for Jira search,
# not during signature grouping (which uses the shared STOP_WORDS).
# Component-specific terms (e.g. "microshift", "lvms") are added per component
# by configure_component() so a component's own name never pollutes searches.
_BASE_KEYWORD_STOP_WORDS = STOP_WORDS | frozenset({
    "ci", "failure", "failed", "error", "test", "tests",
    "job", "jobs", "step", "periodic",
})


# ---------------------------------------------------------------------------
# Per-component Jira search configuration
# ---------------------------------------------------------------------------
# search-bugs.py is shared across CI doctor plugins. Each component fixes the
# Jira project/component scope, the AI-generated bug inventory query, the human
# report prefix, and any component-name stop words. Selected via --component
# (default: microshift, preserving historical behavior for direct callers).

SEARCH_COMPONENTS = {
    "microshift": {
        "jira_scope": (
            "((project = OCPBUGS AND component = MicroShift) OR project = USHIFT) "
            "AND issuetype = Bug"
        ),
        "inventory_jql": (
            'project = USHIFT AND issuetype = Bug '
            'AND labels = "microshift-ci-ai-generated" '
            'AND status not in (Closed, Verified) ORDER BY updated DESC'
        ),
        "report_prefix": "MicroShift CI",
        "extra_stop_words": frozenset({"microshift"}),
    },
    "lvm-operator": {
        # LVMS bugs live in OCPBUGS (AI-generated) and OCPEDGE (team-created),
        # both under the "Logical Volume Manager Storage" component, issuetype Bug.
        "jira_scope": (
            'project in (OCPBUGS, OCPEDGE) '
            'AND component = "Logical Volume Manager Storage" '
            'AND issuetype = Bug'
        ),
        # Inventory = AI-generated bugs filed via the Create Bug button (OCPBUGS,
        # lvms-ci-ai-generated label). Team-created OCPEDGE tickets carry no such
        # label and surface via the duplicate/regression scope above instead.
        "inventory_jql": (
            'project = OCPBUGS AND issuetype = Bug '
            'AND component = "Logical Volume Manager Storage" '
            'AND labels = "lvms-ci-ai-generated" '
            'AND status not in (Closed, Verified) ORDER BY updated DESC'
        ),
        "report_prefix": "LVMS CI",
        "extra_stop_words": frozenset({"lvms", "lvm", "lvmcluster"}),
    },
}

DEFAULT_COMPONENT = "microshift"

# Module-level state configured by configure_component() before any search runs.
_COMPONENT = DEFAULT_COMPONENT
_JIRA_SCOPE = SEARCH_COMPONENTS[DEFAULT_COMPONENT]["jira_scope"]
_INVENTORY_JQL = SEARCH_COMPONENTS[DEFAULT_COMPONENT]["inventory_jql"]
_REPORT_PREFIX = SEARCH_COMPONENTS[DEFAULT_COMPONENT]["report_prefix"]
KEYWORD_STOP_WORDS = (
    _BASE_KEYWORD_STOP_WORDS | SEARCH_COMPONENTS[DEFAULT_COMPONENT]["extra_stop_words"]
)


def configure_component(component):
    """Select the component whose Jira scope/inventory/prefix the search uses.

    Must be called before any JQL builder or report function runs. Raises
    ValueError for an unknown component.
    """
    global _COMPONENT, _JIRA_SCOPE, _INVENTORY_JQL, _REPORT_PREFIX, KEYWORD_STOP_WORDS
    if component not in SEARCH_COMPONENTS:
        raise ValueError(
            f"unknown component {component!r}; expected one of "
            f"{', '.join(sorted(SEARCH_COMPONENTS))}"
        )
    cfg = SEARCH_COMPONENTS[component]
    _COMPONENT = component
    _JIRA_SCOPE = cfg["jira_scope"]
    _INVENTORY_JQL = cfg["inventory_jql"]
    _REPORT_PREFIX = cfg["report_prefix"]
    KEYWORD_STOP_WORDS = _BASE_KEYWORD_STOP_WORDS | cfg["extra_stop_words"]


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def extract_keywords(error_signature):
    """Extract distinctive search keywords from an error signature.

    Returns a list of 2-4 keywords ranked by specificity.
    Uses KEYWORD_STOP_WORDS (broader filtering) so generic CI terms
    like "test", "failed", "microshift" don't pollute Jira searches.
    """
    return _ranked_tokens(error_signature)[:4]


def _keyword_specificity(token):
    score = len(token)
    if "-" in token or "." in token:
        score += 10
    if any(c.isdigit() for c in token):
        score += 5
    return score


def _ranked_tokens(error_signature):
    """Return every keyword-eligible token, most specific first."""
    tokens = tokenize(error_signature, KEYWORD_STOP_WORDS)
    return sorted(tokens, key=lambda t: (-_keyword_specificity(t), t))


# Optional alphabetic prefix and separator before the first digit, as in
# "go1", "v2", "rhel-9" or "ocp-68256".
_VERSION_PREFIX_RE = re.compile(r"^[a-z]*[-_.]?(?=\d)")
_DOTTED_NUMBER_RE = re.compile(r"\d\.\d")


def is_version_like(token):
    """Return True for version strings, numbers and similar identifiers.

    Tokens such as "1.2.3", "go1.2.3", "rhel-9.6", "4.18.0-rc.1", "x86_64" or
    a bare test ID recur across unrelated bugs, so they are never counted as
    failure-context evidence when matching closed bugs. A token qualifies
    when it contains a dotted number anywhere (so versioned names such as
    "builder-1.22-base" also qualify), or when, after an optional alphabetic
    prefix and separator, it starts with a digit and has at least as many
    digits as letters. The second rule also covers short names such as
    "ipv6", "x509" or "k8s": by shape they cannot be told apart from "go1" or
    "el9", and on their own they rarely distinguish one failure from another.
    """
    if _DOTTED_NUMBER_RE.search(token):
        return True
    rest = _VERSION_PREFIX_RE.sub("", token, count=1)
    if not rest or not rest[0].isdigit():
        return False
    digits = sum(c.isdigit() for c in rest)
    letters = sum(c.isalpha() for c in rest)
    return digits >= letters


# Single-letter dotted abbreviations ("e.g", "i.e") are prose, not context.
_ABBREVIATION_RE = re.compile(r"^(?:[a-z]\.)+[a-z]$")


def extract_context_terms(error_signature):
    """Split keyword-eligible tokens into (context_terms, ignored_terms).

    context_terms are the words and structured identifiers that describe the
    failure, most specific first; ignored_terms are the version-like tokens
    that never count as context evidence (see is_version_like).
    """
    context, ignored = [], []
    for token in _ranked_tokens(error_signature):
        if _ABBREVIATION_RE.match(token):
            continue
        (ignored if is_version_like(token) else context).append(token)
    return context, ignored


def extract_test_ids(error_signature):
    """Extract numeric test case IDs (4-6 digits) from error signature."""
    return re.findall(r"\b(\d{4,6})\b", error_signature)


# ---------------------------------------------------------------------------
# Jira search (JQL query builders and search logic)
# ---------------------------------------------------------------------------
# All builders read the module-level _JIRA_SCOPE / _INVENTORY_JQL set by
# configure_component(). They default to microshift at import time.


def build_search_jql_open_match_keyword(keyword):
    """Build JQL for open bugs matching keyword (for duplicate detection)."""
    return f'{_JIRA_SCOPE} AND text ~ "{keyword}" AND status not in (Closed, Verified)'


def build_search_jql_open_match_keywords(keywords):
    """Build JQL for open bugs matching ALL keywords (compound evidence)."""
    terms = " AND ".join(f'text ~ "{kw}"' for kw in keywords)
    return f'{_JIRA_SCOPE} AND {terms} AND status not in (Closed, Verified)'


def build_search_jql_open_match_test_id(test_id, ocp_prefixed=False):
    """Build JQL for open bugs matching test ID (bare or OCP-prefixed, for duplicate detection)."""
    if ocp_prefixed:
        term = f"OCP-{test_id}"
    else:
        term = test_id
    return f'{_JIRA_SCOPE} AND text ~ "{term}" AND status not in (Closed, Verified)'


def build_search_jql_closed_match_keyword(keyword):
    """Build JQL for closed/verified bugs matching keyword (for regression detection)."""
    return f'{_JIRA_SCOPE} AND text ~ "{keyword}" AND status in (Closed, Verified) ORDER BY updated DESC'


def build_search_jql_closed_match_keywords(keywords):
    """Build JQL for closed/verified bugs matching ALL keywords (compound evidence)."""
    terms = " AND ".join(f'text ~ "{kw}"' for kw in keywords)
    return (f'{_JIRA_SCOPE} AND {terms} '
            f'AND status in (Closed, Verified) ORDER BY updated DESC')


def build_search_jql_open_all():
    """Build the open AI-generated bug inventory query for the active component."""
    return _INVENTORY_JQL


def _convert_jira_issue_to_entry(issue, include_priority_created=False):
    """Convert a Jira API issue dict to the bug-matches entry format.

    Truncates 'updated' and 'created' to YYYY-MM-DD so categorize_candidate's
    lexicographic date compare works correctly.
    """
    fields = issue.get("fields", {})
    assignee_obj = fields.get("assignee")
    assignee = assignee_obj.get("displayName", "") if assignee_obj else ""

    entry = {
        "key": issue.get("key", ""),
        "summary": fields.get("summary", ""),
        "status": fields.get("status", {}).get("name", ""),
        "assignee": assignee,
        "updated": (fields.get("updated") or "")[:10],
    }

    if include_priority_created:
        priority_obj = fields.get("priority")
        priority = priority_obj.get("name", "") if priority_obj else ""
        entry["priority"] = priority
        entry["created"] = (fields.get("created") or "")[:10]

    return entry


def _jira_text(value):
    """Flatten a Jira text field (plain string or ADF document) to text.

    Inline leaves (text runs split by formatting marks, mentions, links) are
    joined without a separator so a token split across marks stays whole;
    block nodes are joined by newlines.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_jira_text(item) for item in value)
    if not isinstance(value, dict):
        return ""
    if value.get("type") == "hardBreak":
        return "\n"
    children = value.get("content")
    if not isinstance(children, list) or not children:
        if isinstance(value.get("text"), str):
            return value["text"]
        attrs = value.get("attrs") if isinstance(value.get("attrs"), dict) else {}
        leaf = attrs.get("text") or attrs.get("url")
        return leaf if isinstance(leaf, str) else ""
    inline = all(isinstance(child, dict) and not child.get("content")
                 for child in children)
    return ("" if inline else "\n").join(_jira_text(child) for child in children)


_STRUCTURED_TERM_RE = re.compile(r"[-_.]")


def required_context_terms(context_terms):
    """Return the context terms a closed bug must contain.

    These are the two most specific context terms. When neither of them is a
    structured identifier (a file, script, target, image or component name
    joined by '-', '_' or '.') but the failure names one, the most specific
    identifier is required too, so the same symptom on a different build path
    does not match. Retrieval and local verification use the same terms, so
    an acceptable hit cannot fall off the search result page. Returns [] when
    fewer than two context terms exist.
    """
    if len(context_terms) < 2:
        return []
    required = list(context_terms[:2])
    if not any(_STRUCTURED_TERM_RE.search(term) for term in required):
        structured = [t for t in context_terms if _STRUCTURED_TERM_RE.search(t)]
        required.extend(structured[:1])
    return required


def match_closed_issue_context(issue, context_terms, ignored_terms=()):
    """Return match evidence when a closed hit shares the failure context.

    Jira text search matches stemmed fragments anywhere in an issue,
    including comments, so a retrieved closed bug is not evidence on its own.
    The hit is re-checked against its summary and description: every
    required_context_terms() term must occur there as a whole token.
    Version-like tokens are never context terms (see extract_context_terms).
    Returns None when the hit fails the check or lacks the fields to make it;
    otherwise {matched_terms, matched_fields, ignored_terms}.
    """
    required = required_context_terms(context_terms)
    if not required:
        return None
    fields = issue.get("fields") or {}
    field_tokens = {
        "summary": tokenize(_jira_text(fields.get("summary"))),
        "description": tokenize(_jira_text(fields.get("description"))),
    }
    issue_tokens = field_tokens["summary"] | field_tokens["description"]
    if not all(term in issue_tokens for term in required):
        return None
    matched = [term for term in context_terms if term in issue_tokens]
    return {
        "matched_terms": matched,
        "matched_fields": [name for name, tokens in field_tokens.items()
                           if tokens.intersection(matched)],
        "ignored_terms": list(ignored_terms),
    }


def _resolve_search_fns(search_fn=None, search_all_fn=None):
    """Return (search_fn, search_all_fn), defaulting to the Jira client.

    search_fn returns one bounded page (open-bug duplicate queries);
    search_all_fn returns a complete paginated result (closed-bug matches and
    the open-bug inventory). The production default, jira_search.search_all,
    is bounded (page_size 50, max_pages 200, max_issues 10000) and raises
    JiraSearchError when a result is incomplete or exceeds a bound, so an
    incomplete search is never presented as complete. Only a caller that
    injects search_fn alone (a test harness) gets a single-page stand-in.
    """
    if search_all_fn is None:
        if search_fn is None:
            search_all_fn = jira_search.search_all
        else:
            injected = search_fn

            def single_page(jql, fields):
                return injected(jql, fields=fields, max_results=50)

            search_all_fn = single_page
    if search_fn is None:
        search_fn = jira_search.search
    return search_fn, search_all_fn


def find_jira_bugs_for_candidate(cand, search_fn=None, search_all_fn=None):
    """Search Jira for bugs matching one candidate's keywords and test IDs.

    Returns {duplicates: [...], regressions: [...]}.
    Uses injectable search_fn/search_all_fn for testability (see
    _resolve_search_fns for the production defaults).
    """
    search_fn, search_all_fn = _resolve_search_fns(search_fn, search_all_fn)

    error_signature = cand.get("error_signature", "")
    test_ids = extract_test_ids(error_signature)
    id_terms = set(test_ids) | {f"ocp-{test_id}" for test_id in test_ids}
    keywords = [kw for kw in extract_keywords(error_signature)
                if kw.lower() not in id_terms]

    # Retrieval is deliberately broad; acceptance requires compound evidence.
    open_hits = {}

    def record(hits, issue, evidence_type, evidence_value):
        key = issue.get("key")
        if not key:
            return
        item = hits.setdefault(key, {"issue": issue, "keywords": set(),
                                    "bare_ids": set(), "ocp_ids": set()})
        item[evidence_type].add(evidence_value)

    for kw in keywords[:3]:
        jql = build_search_jql_open_match_keyword(kw)
        issues = search_fn(jql, fields="summary,status,assignee,updated", max_results=50)
        for iss in issues:
            record(open_hits, iss, "keywords", kw)

    # Query compound evidence directly: an issue matching any pair of keywords
    # satisfies the >=2-keyword acceptance rule even when it falls outside the
    # first page of a broad single-keyword query.
    for kw_a, kw_b in combinations(keywords[:3], 2):
        jql = build_search_jql_open_match_keywords([kw_a, kw_b])
        issues = search_fn(jql, fields="summary,status,assignee,updated", max_results=50)
        for iss in issues:
            record(open_hits, iss, "keywords", kw_a)
            record(open_hits, iss, "keywords", kw_b)

    # Find open bugs by test ID (both bare and OCP-prefixed forms) → potential duplicates
    for tid in test_ids:
        for ocp_prefixed in [False, True]:
            jql = build_search_jql_open_match_test_id(tid, ocp_prefixed=ocp_prefixed)
            issues = search_fn(jql, fields="summary,status,assignee,updated", max_results=5)
            for iss in issues:
                record(open_hits, iss, "ocp_ids" if ocp_prefixed else "bare_ids", tid)

    # A bare test ID is accepted only together with a keyword, so query that
    # combination directly; it cannot be lost to truncation of either the
    # bare-ID or the single-keyword result page.
    for tid in test_ids:
        for kw in keywords[:3]:
            jql = build_search_jql_open_match_keywords([tid, kw])
            issues = search_fn(jql, fields="summary,status,assignee,updated", max_results=50)
            for iss in issues:
                record(open_hits, iss, "bare_ids", tid)
                record(open_hits, iss, "keywords", kw)

    def accepted(hits):
        result = []
        for key in sorted(hits):
            evidence = hits[key]
            if (evidence["ocp_ids"] or len(evidence["keywords"]) >= 2
                    or (evidence["bare_ids"] and evidence["keywords"])):
                result.append(_convert_jira_issue_to_entry(evidence["issue"]))
        return result

    return {
        "duplicates": accepted(open_hits),
        "regressions": _find_closed_context_matches(error_signature, search_all_fn),
    }


def _find_closed_context_matches(error_signature, search_all_fn):
    """Return closed bugs that share the candidate's failure context.

    Retrieval ANDs the required context terms (see required_context_terms);
    version-like tokens are excluded because they match unrelated bugs that
    merely mention the same version. Every hit is then verified by
    match_closed_issue_context and kept with its match_evidence, which the
    merge, categorize and report stages carry through. Without two context
    terms nothing can be verified, so no query is made.

    Every result page is read (search_all_fn): the local check can reject
    the most recently updated hits and keep a later one. A pagination
    failure or an exceeded bound propagates as JiraSearchError.
    """
    context_terms, ignored_terms = extract_context_terms(error_signature)
    required = required_context_terms(context_terms)
    if not required:
        return []
    jql = build_search_jql_closed_match_keywords(required)
    issues = search_all_fn(jql, fields="summary,status,assignee,updated,description")
    matches = {}
    for issue in issues:
        key = issue.get("key")
        if not key or key in matches:
            continue
        evidence = match_closed_issue_context(issue, context_terms, ignored_terms)
        if evidence is not None:
            entry = _convert_jira_issue_to_entry(issue)
            entry["match_evidence"] = evidence
            matches[key] = entry
    return [matches[key] for key in sorted(matches)]


def find_jira_bugs_for_source(candidates_data, search_fn=None,
                              include_open_bugs=False, search_all_fn=None):
    """Search Jira for all candidates in a source.

    Returns bug-matches dict: {source, date, candidates, open_bugs}.
    Each candidate gets {error_signature, severity, failure_type, step_name,
    affected_jobs, duplicates, regressions}.
    """
    search_fn, search_all_fn = _resolve_search_fns(search_fn, search_all_fn)

    source = candidates_data.get("source", "")
    candidates = candidates_data.get("candidates", [])

    result_candidates = []
    for cand in candidates:
        search_result = find_jira_bugs_for_candidate(
            cand, search_fn=search_fn, search_all_fn=search_all_fn)
        result_candidates.append({
            "error_signature": cand.get("error_signature", ""),
            "severity": cand.get("severity"),
            "failure_type": cand.get("failure_type", "test"),
            "step_name": cand.get("step_name", ""),
            "affected_jobs": cand.get("affected_jobs", 0),
            "duplicates": search_result["duplicates"],
            "regressions": search_result["regressions"],
        })

    result = {
        "source": source,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "candidates": result_candidates,
    }

    # Query the labeled open-bug inventory once, on the first source.
    if include_open_bugs:
        jql = build_search_jql_open_all()
        issues = search_all_fn(
            jql, fields="summary,status,assignee,updated,priority,created")
        result["open_bugs"] = [
            _convert_jira_issue_to_entry(iss, include_priority_created=True)
            for iss in issues
        ]

    return result


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "": 0}


def _best_confidence(group):
    """Return the highest confidence value from any item in the group."""
    return max(
        (c.get("confidence", "") for c in group),
        key=lambda v: _CONFIDENCE_RANK.get(v, 0),
    )


def _best_causal_chain(group):
    """Return the longest causal_chain from a high/medium-confidence item.

    Falls back to the longest chain from any item if none have
    high/medium confidence.
    """
    best = []
    best_fallback = []
    for c in group:
        chain = c.get("causal_chain", [])
        conf = c.get("confidence", "")
        if conf in ("high", "medium") and len(chain) > len(best):
            best = chain
        if len(chain) > len(best_fallback):
            best_fallback = chain
    return best if best else best_fallback


# ---------------------------------------------------------------------------
# Candidate building
# ---------------------------------------------------------------------------

def build_candidates(groups):
    """Build bug candidate list from grouped jobs."""
    candidates = []

    for group in groups:
        rep = max(group, key=lambda j: (j["severity"], j.get("job_name", "")))
        keywords = extract_keywords(rep["error_signature"])
        test_ids = extract_test_ids(rep["error_signature"])

        step_names = sorted({j["step_name"] for j in group if j["step_name"]})

        entry = {
            "error_signature": rep["error_signature"],
            "root_cause": rep.get("root_cause", ""),
            "raw_error": rep.get("raw_error", ""),
            "remediation": rep.get("remediation", ""),
            "severity": max(j["severity"] for j in group),
            "failure_type": classify_breakdown(
                rep["stack_layer"],
                rep.get("step_name", ""),
                rep.get("error_signature", ""),
                any(j.get("infrastructure_failure") for j in group),
            ),
            "step_name": ", ".join(step_names),
            "affected_jobs": len(group),
            "confidence": _best_confidence(group),
            "causal_chain": _best_causal_chain(group),
            "analysis_gaps": rep.get("analysis_gaps", []),
            "scenarios": sorted({s for j in group for s in j.get("scenarios", [])}),
            "keywords": keywords,
            "test_ids": test_ids,
            "jobs": [
                {
                    "job_name": j["job_name"],
                    "job_url": j["job_url"],
                    "finished": j["finished"],
                }
                for j in group
            ],
        }

        other_sigs = sorted({j["error_signature"] for j in group} - {rep["error_signature"]})
        if other_sigs:
            entry["merged_signatures"] = other_sigs

        candidates.append(entry)

    # Sort by severity desc, then job count desc
    candidates.sort(key=lambda c: (-c["severity"], -c["affected_jobs"], c["error_signature"]))
    return candidates


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_job_files(workdir, source):
    """Find per-job report files for a given source.

    Returns (files, source_label) tuple.
    Job reports live under ${workdir}/jobs/.
    """
    jobs_dir = os.path.join(workdir, "jobs")

    # Release version
    if re.match(r"^(\d+\.\d+|main)$", source):
        pattern = os.path.join(jobs_dir, f"release-{source}-job-*.json")
        files = sorted(glob_mod.glob(pattern))
        return files, f"release {source}"

    # PR number
    m = re.match(r"^pr-?(\d+)$", source)
    if m:
        pr_num = m.group(1)
        pattern = os.path.join(jobs_dir, f"prs-job-*-pr{pr_num}-*.json")
        files = sorted(glob_mod.glob(pattern))
        return files, f"PR #{pr_num}"

    # Rebase PR shorthand — jobs may target a different branch than the
    # rebase source name (e.g. rebase-release-5.0 jobs run on branch main)
    m = re.match(r"^rebase-release-(.+)$", source)
    if m:
        release = m.group(1)

        # Find PR numbers for this rebase source from the status file
        rebase_pr_numbers = set()
        status_file = os.path.join(jobs_dir, "prs-status.json")
        if os.path.isfile(status_file):
            with open(status_file, "r") as f:
                try:
                    pr_statuses = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    pr_statuses = []
            for pr in pr_statuses:
                if f"rebase-release-{release}" in pr.get("title", ""):
                    pr_num = pr.get("pr_number")
                    if pr_num is not None:
                        rebase_pr_numbers.add(int(pr_num))

        pattern = os.path.join(jobs_dir, "prs-job-*.json")
        all_files = sorted(glob_mod.glob(pattern))
        files = []
        for filepath in all_files:
            # Match by PR number extracted from filename
            pr_match = re.search(r"-pr(\d+)-", os.path.basename(filepath))
            if pr_match and int(pr_match.group(1)) in rebase_pr_numbers:
                files.append(filepath)
                continue

            # Fallback: match by structured summary fields
            summaries = parse_structured_summary(filepath)
            if summaries and any(
                f"release-{release}" in s.get("job_name", "")
                or s.get("release", "") == release
                for s in summaries
            ):
                files.append(filepath)

        return files, f"rebase PR for {release}"

    return [], source


def _confirmed_no_failures(workdir, source):
    """Return True when the collect stage recorded zero failed jobs for this
    source, distinguishing a healthy source from one with missing reports.

    doctor-helper.sh writes jobs/release-<source>-status.json (every periodic
    job for a release, pass and fail), jobs/release-<source>-jobs.json (the
    failed jobs selected for analysis) and jobs/prs-status.json (per-PR job
    counts). A release with no failed jobs has a failure-free status file and
    no release-<source>-error.txt. A collection that completed without
    matching any periodic job (e.g. a release with no periodic jobs yet, or a
    release name no job matches) writes empty status and jobs lists; it has
    nothing to correlate and is reported with a warning. A failed collection
    writes the same empty lists plus the error file and is NOT treated as
    healthy, nor is a missing or unparsable status file, or an empty status
    list whose jobs file is missing, unparsable or non-empty. PR and rebase
    sources are healthy when every matching entry in prs-status.json has zero
    failed jobs.
    """
    jobs_dir = os.path.join(workdir, "jobs")

    def _load_json(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    if re.match(r"^(\d+\.\d+|main)$", source):
        if os.path.exists(os.path.join(jobs_dir, f"release-{source}-error.txt")):
            return False
        statuses = _load_json(
            os.path.join(jobs_dir, f"release-{source}-status.json"))
        if not isinstance(statuses, list):
            return False
        if not statuses:
            failed_jobs = _load_json(
                os.path.join(jobs_dir, f"release-{source}-jobs.json"))
            if failed_jobs != []:
                return False
            print(f"WARNING: no periodic jobs were collected for release "
                  f"{source}", file=sys.stderr)
            return True
        return all(isinstance(s, dict) and s.get("status") != "failure"
                   for s in statuses)

    pr_num = None
    title_fragment = None
    m = re.match(r"^pr-?(\d+)$", source)
    if m:
        pr_num = int(m.group(1))
    else:
        m = re.match(r"^rebase-release-(.+)$", source)
        if not m:
            return False
        title_fragment = f"rebase-release-{m.group(1)}"

    pr_statuses = _load_json(os.path.join(jobs_dir, "prs-status.json"))
    if not isinstance(pr_statuses, list):
        return False
    matched = [
        p for p in pr_statuses
        if (pr_num is not None and p.get("pr_number") == pr_num)
        or (title_fragment is not None and title_fragment in p.get("title", ""))
    ]
    return bool(matched) and all(p.get("failed", 0) == 0 for p in matched)


# ---------------------------------------------------------------------------
# Cross-release merge
# ---------------------------------------------------------------------------


def _jira_keys(candidate):
    """Extract accepted open Jira keys; regressions never merge failures."""
    keys = set()
    for d in candidate.get("duplicates", []):
        if d.get("key"):
            keys.add(d["key"])
    return keys


def _merge_groups_by_jira(groups):
    """Merge groups only when every member shares one accepted open Jira key."""
    result = []
    for group in groups:
        group_keys = set.intersection(*(_jira_keys(c) for c in group)) if group else set()
        merged = False
        for index, (existing, shared_keys) in enumerate(result):
            common = shared_keys & group_keys
            if common:
                result[index] = (existing + group, common)
                merged = True
                break
        if not merged:
            result.append((list(group), group_keys))
    return [group for group, _ in result]


def _representative_key(candidate):
    """Sort key that picks a merged group's representative candidate."""
    return (candidate["severity"], candidate["affected_jobs"],
            candidate["error_signature"])


def _merge_groups_by_representative_signature(groups):
    """Merge groups whose representatives share a non-empty error_signature.

    Step-name bucketing keeps fuzzy matches inside one CI step, so the same
    signature reported in two steps (or by two sources in different steps)
    ends up as two groups with the same representative signature. Every
    downstream consumer (categorize, _validate_results, format_report and
    create-report) keys merged candidates by that signature, so they must be
    unique. The representative of the union keeps the shared signature,
    hence one pass suffices. Groups whose representatives already differ are
    left untouched, and empty signatures never merge so that
    _validate_results still rejects them.
    """
    result = []
    index_by_sig = {}
    for group in groups:
        sig = max(group, key=_representative_key)["error_signature"]
        if not sig:
            result.append(list(group))
        elif sig in index_by_sig:
            result[index_by_sig[sig]].extend(group)
        else:
            index_by_sig[sig] = len(result)
            result.append(list(group))
    return result


def _load_jira_lookup(workdir, sources=None):
    """Load Jira duplicates/regressions from bug mapping files.

    Returns a dict mapping error_signature to {duplicates, regressions}.
    Bug mapping files live under ${workdir}/bugs/.
    """
    lookup = {}
    if sources is None:
        pattern = os.path.join(workdir, "bugs", "bug-matches-*.json")
        filepaths = sorted(glob_mod.glob(pattern))
    else:
        filepaths = [
            os.path.join(workdir, "bugs", f"bug-matches-{source}.json")
            for source in sources
        ]
    for filepath in filepaths:
        if not os.path.isfile(filepath):
            continue
        with open(filepath, "r") as f:
            data = json.load(f)
        for cand in data.get("candidates", []):
            sig = cand.get("error_signature", "")
            if not sig:
                continue
            if sig not in lookup:
                lookup[sig] = {"duplicates": [], "regressions": []}
            existing_dkeys = {d["key"] for d in lookup[sig]["duplicates"]}
            for d in cand.get("duplicates", []):
                if d.get("key") and d["key"] not in existing_dkeys:
                    lookup[sig]["duplicates"].append(d)
                    existing_dkeys.add(d["key"])
            existing_rkeys = {r["key"] for r in lookup[sig]["regressions"]}
            for r in cand.get("regressions", []):
                if r.get("key") and r["key"] not in existing_rkeys:
                    lookup[sig]["regressions"].append(r)
                    existing_rkeys.add(r["key"])
    return lookup


def merge_candidate_files(filepaths, workdir=None):
    """Merge multiple candidate JSON files with fuzzy dedup and Jira-based dedup.

    Groups left with the same representative error_signature are merged last,
    so every merged candidate has a unique signature.

    Handles both pre-Jira candidate files (keywords, test_ids, jobs)
    and post-Jira bug mapping files (duplicates, regressions).

    When workdir is provided and contains bug mapping files
    (bug-matches-*.json), their Jira data is injected into candidates
    so that _merge_groups_by_jira() can merge groups sharing issue keys.

    Returns a dict with sources, total_candidates, and candidates[] where
    each candidate has a releases[] list showing all sources it appears in.
    """
    all_candidates = []
    sources = []

    for filepath in filepaths:
        with open(filepath, "r") as f:
            data = json.load(f)
        source = data["source"]
        # Several inputs can share a source (e.g. its bug-candidates and
        # bug-matches files); per-source releases and jobs are emitted once.
        if source not in sources:
            sources.append(source)
        for cand in data.get("candidates", []):
            all_candidates.append({**cand, "_source": source})

    total_candidates = len(all_candidates)

    # Inject Jira data from bug mapping files when available.
    # The lookup is authoritative — always overwrite the candidate's
    # duplicates/regressions since candidate files never carry Jira data.
    jira_injected = 0
    if workdir:
        jira_lookup = _load_jira_lookup(workdir, sources)
        for cand in all_candidates:
            sig = cand.get("error_signature", "")
            if sig in jira_lookup:
                jira_data = jira_lookup[sig]
                cand["duplicates"] = jira_data["duplicates"]
                cand["regressions"] = jira_data["regressions"]
                jira_injected += 1
        if jira_injected:
            print(f"Injected Jira data into {jira_injected}/{total_candidates} candidates from bug mapping files", file=sys.stderr)

    # Pass 1: bucket by normalized step_name, then fuzzy-match within each bucket
    by_step = {}
    for cand in all_candidates:
        step = normalize_step_name(cand.get("step_name", ""))
        by_step.setdefault(step, []).append(cand)

    merged_groups = []
    for step_cands in by_step.values():
        merged_groups.extend(cluster_by_similarity(step_cands, grouping_text))

    n_groups_before_jira = len(merged_groups)

    # Pass 2: merge groups that share Jira issue keys (crosses step-name boundaries)
    merged_groups = _merge_groups_by_jira(merged_groups)

    if len(merged_groups) < n_groups_before_jira:
        print(f"Jira-based merge: {n_groups_before_jira} -> {len(merged_groups)} groups", file=sys.stderr)

    # Pass 3: merge groups left with the same representative error_signature
    # (crosses step-name boundaries; downstream consumers key on it)
    n_groups_before_signature = len(merged_groups)
    merged_groups = _merge_groups_by_representative_signature(merged_groups)
    if len(merged_groups) < n_groups_before_signature:
        print(f"Same-signature merge: {n_groups_before_signature} -> {len(merged_groups)} groups", file=sys.stderr)

    # Build merged candidates from groups
    merged_candidates = []
    for group in merged_groups:
        rep = max(group, key=_representative_key)

        # Build releases list (aggregate affected_jobs per source). Only the
        # first occurrence of a job URL within a source is kept and counted,
        # e.g. when one job reports the same signature in two steps.
        releases_map = {}
        jobs_by_source = {}
        seen_jobs = set()
        for cand in group:
            src = cand["_source"]
            source_jobs = jobs_by_source.setdefault(src, [])
            repeated = 0
            for job in cand.get("jobs", []):
                url = job.get("job_url", "")
                if url:
                    if (src, url) in seen_jobs:
                        repeated += 1
                        continue
                    seen_jobs.add((src, url))
                source_jobs.append(job)
            releases_map[src] = releases_map.get(src, 0) + cand["affected_jobs"] - repeated
        releases = [{"source": s, "affected_jobs": releases_map[s]} for s in sources if s in releases_map]

        # Union keywords, test_ids, duplicates, regressions across the group
        all_keywords = set()
        all_test_ids = set()
        all_duplicates = {}
        all_regressions = {}
        for cand in group:
            all_keywords.update(cand.get("keywords", []))
            all_test_ids.update(cand.get("test_ids", []))
            for d in cand.get("duplicates", []):
                if d.get("key"):
                    all_duplicates[d["key"]] = d
            for r in cand.get("regressions", []):
                if r.get("key"):
                    all_regressions[r["key"]] = r

        # Concatenate all jobs across sources
        all_jobs = []
        for s in sources:
            all_jobs.extend(jobs_by_source.get(s, []))

        all_sigs = set()
        for cand in group:
            all_sigs.add(cand["error_signature"])
            all_sigs.update(cand.get("merged_signatures", []))
        other_sigs = sorted(all_sigs - {rep["error_signature"]})

        step_names = sorted({step for c in group
                             for step in (c.get("step_name") or "").split(", ") if step})

        entry = {
            "error_signature": rep["error_signature"],
            "root_cause": rep.get("root_cause", ""),
            "raw_error": rep.get("raw_error", ""),
            "remediation": rep.get("remediation", ""),
            "severity": max(c["severity"] for c in group),
            "failure_type": rep.get("failure_type", "test"),
            "step_name": ", ".join(step_names) if step_names else rep.get("step_name", ""),
            "affected_jobs": sum(releases_map.values()),
            "confidence": _best_confidence(group),
            "causal_chain": _best_causal_chain(group),
            "analysis_gaps": rep.get("analysis_gaps", []),
            "scenarios": sorted({s for c in group for s in c.get("scenarios", [])}),
            "keywords": sorted(all_keywords),
            "test_ids": sorted(all_test_ids),
            "jobs": all_jobs,
            "releases": releases,
        }
        if other_sigs:
            entry["merged_signatures"] = other_sigs
        if all_duplicates:
            entry["duplicates"] = sorted(all_duplicates.values(), key=lambda d: d.get("key", ""))
        if all_regressions:
            entry["regressions"] = list(all_regressions.values())

        merged_candidates.append(entry)

    merged_candidates.sort(key=lambda c: (-c["severity"], -c["affected_jobs"], c["error_signature"]))

    return {
        "sources": sources,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_candidates": total_candidates,
        "candidates": merged_candidates,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

VALID_ACTIONS = {"suggest", "skip", "linked"}
# categorize_candidate no longer emits stale_regression (a closed bug's
# 'updated' date is not proof its fix was in the tested code); it stays valid
# so results files written by earlier versions still render.
VALID_SKIP_CATEGORIES = {"infrastructure", "stale_regression"}
JIRA_URL_BASE = "https://redhat.atlassian.net/browse"
SEPARATOR = "=" * 63


def _validate_results(results_data, candidates_data):
    """Validate results JSON against merged candidates. Exit non-zero on any mismatch."""
    errors = []

    mode = results_data.get("mode", "")
    if not mode:
        errors.append("results JSON missing 'mode' field")
    elif mode != "search":
        errors.append(f"invalid mode: {mode}")

    if "date" not in results_data:
        errors.append("results JSON missing 'date' field")

    if "results" not in results_data:
        errors.append("results JSON missing 'results' field")
        _die_on_errors(errors)

    results = results_data["results"]
    candidates = candidates_data["candidates"]

    cand_sigs = {c["error_signature"] for c in candidates}
    result_sigs = set()

    for i, r in enumerate(results):
        prefix = f"results[{i}]"
        sig = r.get("error_signature", "")
        if not sig:
            errors.append(f"{prefix}: missing error_signature")
        else:
            if sig in result_sigs:
                errors.append(f"{prefix}: duplicate error_signature '{sig}'")
            result_sigs.add(sig)

        action = r.get("action", "")
        if action not in VALID_ACTIONS:
            errors.append(f"{prefix}: invalid action '{action}'")

        if "jira_key" not in r:
            errors.append(f"{prefix}: missing jira_key field")
        elif action == "linked" and not r["jira_key"]:
            errors.append(f"{prefix}: linked action requires non-empty jira_key")
        elif action != "linked" and r["jira_key"] != "":
            errors.append(f"{prefix}: jira_key must be empty for action '{action}'")

        if "skip_category" not in r:
            errors.append(f"{prefix}: missing skip_category field")
        elif action == "skip" and r["skip_category"] not in VALID_SKIP_CATEGORIES:
            errors.append(f"{prefix}: invalid skip_category '{r['skip_category']}' for skip action")
        elif action != "skip" and r["skip_category"]:
            errors.append(f"{prefix}: skip_category must be empty for action '{action}'")

        reason = r.get("reason", "")
        if not reason:
            errors.append(f"{prefix}: missing or empty reason")

    missing = cand_sigs - result_sigs
    extra = result_sigs - cand_sigs

    if missing:
        errors.append(f"candidates without results: {sorted(missing)}")
    if extra:
        errors.append(f"results without candidates: {sorted(extra)}")

    _die_on_errors(errors)


def _die_on_errors(errors):
    if errors:
        for e in errors:
            print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def _format_releases(releases):
    """Format releases list: '4.20 (8 jobs), 4.21 (1 job)'."""
    parts = []
    for r in releases:
        n = r["affected_jobs"]
        parts.append(f"{r['source']} ({n} {'job' if n == 1 else 'jobs'})")
    return ", ".join(parts)


def _format_jira_refs(label, refs):
    """Format 'Potential Duplicates: USHIFT-1234 [Status], ...' line."""
    if not refs:
        return f"{label}: None"
    parts = []
    for ref in refs:
        parts.append(f"{ref['key']} [{ref.get('status', 'Unknown')}]")
    return f"{label}: {', '.join(parts)}"


def _format_closed_refs(refs):
    """Format the closed-bug line with each match's failure-context evidence."""
    label = "Possibly Related Closed Bugs (fix inclusion unverified)"
    if not refs:
        return f"{label}: None"
    parts = []
    for ref in refs:
        terms = (ref.get("match_evidence") or {}).get("matched_terms") or []
        detail = f"matched: {', '.join(terms)}" if terms else "match evidence unavailable"
        parts.append(f"{ref['key']} [{ref.get('status', 'Unknown')}] ({detail})")
    return f"{label}: {'; '.join(parts)}"


def _format_grouped_with(merged_signatures):
    """Format 'Grouped with:' block."""
    if not merged_signatures:
        return ""
    lines = ["     Grouped with:"]
    for sig in merged_signatures:
        lines.append(f"       - {sig}")
    return "\n".join(lines)


def _format_jobs(jobs):
    """Format job URLs list."""
    if not jobs:
        return ""
    lines = ["     Jobs:"]
    for job in jobs:
        lines.append(f"       - {job['job_url']}")
    return "\n".join(lines)


def _compute_summary_counters(results):
    """Compute summary counters from results list."""
    counters = {
        "suggest": 0,
        "linked": 0,
        "skip_infrastructure": 0,
        "skip_stale_regression": 0,
    }
    for r in results:
        action = r["action"]
        if action == "skip":
            cat = r["skip_category"]
            key = f"skip_{cat}"
            if key in counters:
                counters[key] += 1
        elif action in counters:
            counters[action] += 1
    return counters


def format_report(candidates_data, results_data):
    """Produce deterministic bug search report."""
    candidates = candidates_data["candidates"]
    results = results_data["results"]
    sources = candidates_data["sources"]
    date = results_data["date"]

    result_lookup = {r["error_signature"]: r for r in results}
    counters = _compute_summary_counters(results)
    n_possibly_related = sum(
        1 for cand in candidates
        if cand.get("regressions")
        and result_lookup[cand["error_signature"]]["action"] == "suggest")
    n_unique = len(candidates)
    n_total = candidates_data["total_candidates"]
    n_sources = len(sources)

    lines = [
        SEPARATOR,
        "BUG SEARCH REPORT",
        f"Sources: {', '.join(sources)}",
        f"Date: {date}",
        SEPARATOR,
        "",
        f"CANDIDATES ({n_unique} unique failures from {n_total} total across {n_sources} {'source' if n_sources == 1 else 'sources'})",
    ]

    for i, cand in enumerate(candidates, 1):
        r = result_lookup[cand["error_signature"]]
        action = r["action"]
        jira_key = r.get("jira_key", "")

        tag_map = {
            "skip": "[SKIP]",
            "suggest": "[SUGGEST]",
            "linked": f"[LINKED \u2192 {jira_key}]" if jira_key else "[LINKED]",
        }
        tag = tag_map.get(action, f"[{action.upper()}]")

        lines.append("")
        lines.append(f"  {i}. {tag}")
        lines.append(f"     {_REPORT_PREFIX}: {cand['error_signature']}")
        lines.append(f"     Severity: {cand['severity']} | Total Jobs: {cand['affected_jobs']} | Step: {cand['step_name']}")
        lines.append(f"     Releases: {_format_releases(cand.get('releases', []))}")

        grouped = _format_grouped_with(cand.get("merged_signatures", []))
        if grouped:
            lines.append(grouped)

        lines.append(f"     {_format_jira_refs('Potential Duplicates', cand.get('duplicates', []))}")
        lines.append(f"     {_format_closed_refs(cand.get('regressions', []))}")

        jobs_block = _format_jobs(cand.get("jobs", []))
        if jobs_block:
            lines.append(jobs_block)

        if jira_key and action == "linked":
            lines.append(f"     URL: {JIRA_URL_BASE}/{jira_key}")

        lines.append(f"     Decision: {r['reason']}")

    lines.extend([
        "",
        "SUMMARY",
        f"  Sources processed: {n_sources}",
        f"  Unique failures: {n_unique} (from {n_total} total candidates)",
        f"  Suggest filing: {counters['suggest']}",
        f"    Possibly related to closed bugs: {n_possibly_related}",
        f"  Linked to existing: {counters['linked']}",
        f"  Skipped (infrastructure): {counters['skip_infrastructure']}",
    ])
    if counters["skip_stale_regression"]:
        lines.append(
            f"  Skipped (stale regression): {counters['skip_stale_regression']}")

    return "\n".join(lines)


def main_report(report_file, candidates_file, workdir):
    """Entry point for --report mode."""
    if not os.path.isdir(workdir):
        print(f"Error: work directory does not exist: {workdir}", file=sys.stderr)
        sys.exit(1)

    with open(report_file, "r") as f:
        results_data = json.load(f)
    with open(candidates_file, "r") as f:
        candidates_data = json.load(f)

    _validate_results(results_data, candidates_data)

    report = format_report(candidates_data, results_data)

    sources = candidates_data["sources"]
    release_sources = [s for s in sources if not s.startswith("rebase-")]
    if len(release_sources) > 1:
        tag = "merged"
    elif len(release_sources) == 1:
        tag = release_sources[0]
    else:
        tag = "merged" if len(sources) > 1 else sources[0]
    if tag == "merged":
        filename = "report-bug-search.txt"
        output_path = os.path.join(workdir, filename)
    else:
        filename = f"bug-search-{tag}.txt"
        bugs_dir = os.path.join(workdir, "bugs")
        os.makedirs(bugs_dir, exist_ok=True)
        output_path = os.path.join(bugs_dir, filename)
    report_with_footer = report + f"\n\nReport saved: {output_path}\n{SEPARATOR}\n"

    with open(output_path, "w") as f:
        f.write(report_with_footer)

    print(f"Written: {output_path}", file=sys.stderr)
    print(report_with_footer)


# ---------------------------------------------------------------------------
# Categorization (deterministic decision policy)
# ---------------------------------------------------------------------------
#
# The suggest/linked/skip decision — including how a failure that matches a
# closed Jira bug is reported — is deterministic. This module is the source
# of truth for the decision policy: the rules and
# reason strings in categorize_candidate() below are enforced by
# scripts/tests/test_search_bugs.py. The decisions are consumed by the
# --report text stage (bug-search-*.txt), by downstream skills reading
# bug-results-*.json, and by the HTML report (plugins/shared
# create-report.py), which renders each decision next to the failure. The
# report's manual Create Bug button does not depend on the decision.

REASON_INFRASTRUCTURE = "Infrastructure failure — not a product bug"
REASON_NO_BUGS = "No matching existing bugs found — suggest filing a new bug"


def categorize_candidate(candidate):
    """Apply the deterministic decision policy to one merged candidate.

    Rules are applied in order:

      1. infrastructure failure                 -> skip / infrastructure
      2. open duplicates                        -> linked (first duplicate)
      3. closed bugs matching the failure context, no open duplicates
                                                -> suggest, reported as
         possibly related for a human to review. The match is a text
         heuristic, not a root-cause finding, and Jira data cannot show
         whether a closed bug's fix was in the code a job tested ('updated'
         moves on any edit), so the match is neither confirmed as a
         regression nor used to skip the failure as stale, whatever the job
         finish dates.
      4. no duplicates, no closed matches       -> suggest

    Returns {action, jira_key, skip_category, reason}. Missing
    duplicates/regressions keys are treated as empty (they are omitted from
    a merged candidate when empty).
    """
    failure_type = candidate.get("failure_type", "test")
    duplicates = candidate.get("duplicates") or []
    regressions = candidate.get("regressions") or []

    # Rule 1: infrastructure failures are transient CI/cloud issues, not bugs.
    if failure_type == "infrastructure":
        return {
            "action": "skip",
            "jira_key": "",
            "skip_category": "infrastructure",
            "reason": REASON_INFRASTRUCTURE,
        }

    # Rule 2: an open bug already tracks this failure.
    if duplicates:
        key = duplicates[0].get("key", "")
        return {
            "action": "linked",
            "jira_key": key,
            "skip_category": "",
            "reason": f"Linked to existing bug {key}",
        }

    # Rule 3: closed bugs share the failure context (match_evidence), but
    # whether their fixes were in the tested code is unknown. Entries from
    # bug-matches files written before the context check carry no
    # match_evidence; they get the same label and the reports show
    # "match evidence unavailable".
    if regressions:
        keys = sorted({r.get("key", "") for r in regressions} - {""})
        return {
            "action": "suggest",
            "jira_key": "",
            "skip_category": "",
            "reason": (f"Possibly related to closed {', '.join(keys) or 'bug'}; "
                       "fix inclusion in the tested code is not established "
                       "— review before filing a new bug"),
        }

    # Rule 4: nothing found — draft a new-bug suggestion.
    return {
        "action": "suggest",
        "jira_key": "",
        "skip_category": "",
        "reason": REASON_NO_BUGS,
    }


def categorize_candidates(merged_data):
    """Build a results-JSON dict from merged candidates using the decision policy.

    Deterministic: no model involvement. The output matches the schema
    enforced by _validate_results (exactly one result per candidate, keyed by
    error_signature).
    """
    results = []
    for cand in merged_data.get("candidates", []):
        decision = categorize_candidate(cand)
        results.append({
            "error_signature": cand["error_signature"],
            "action": decision["action"],
            "jira_key": decision["jira_key"],
            "skip_category": decision["skip_category"],
            "reason": decision["reason"],
        })
    return {
        "mode": "search",
        "date": merged_data.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "results": results,
    }


def _derive_results_path(candidates_file, workdir):
    """Default bug-results output path derived from the merged candidates path.

    Substitutes 'bug-candidates-merged-' -> 'bug-results-' in the basename so
    the results tag matches the merged file with no recomputation. Returns
    None when that prefix is absent (caller falls back to tag logic).
    """
    basename = os.path.basename(candidates_file)
    if "bug-candidates-merged-" in basename:
        out_name = basename.replace("bug-candidates-merged-", "bug-results-", 1)
        return os.path.join(workdir, "bugs", out_name)
    return None


def main_categorize(candidates_file, output_file, workdir):
    """Entry point for --categorize mode (deterministic decision policy)."""
    if not os.path.isdir(workdir):
        print(f"Error: work directory does not exist: {workdir}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(candidates_file):
        print(f"Error: file not found: {candidates_file}", file=sys.stderr)
        sys.exit(1)

    with open(candidates_file, "r") as f:
        merged_data = json.load(f)

    results_data = categorize_candidates(merged_data)

    # Self-check: the deterministic output must satisfy the same contract the
    # --report step enforces. Exits non-zero on any mismatch.
    _validate_results(results_data, merged_data)

    output_path = output_file or _derive_results_path(candidates_file, workdir)
    if not output_path:
        # Fallback: derive a tag the same way main_report does.
        sources = merged_data.get("sources", [])
        release_sources = [s for s in sources if not s.startswith("rebase-")]
        if len(release_sources) >= 1:
            tag = "merged" if len(release_sources) > 1 else release_sources[0]
        elif sources:
            tag = "merged" if len(sources) > 1 else sources[0]
        else:
            tag = "merged"
        output_path = os.path.join(workdir, "bugs", f"bug-results-{tag}.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(results_data, f, indent=2)

    counters = _compute_summary_counters(results_data["results"])
    n = len(results_data["results"])
    print(f"Written: {output_path}", file=sys.stderr)
    print(
        f"Categorized {n} candidates: {counters['suggest']} suggest, "
        f"{counters['linked']} linked, {counters['skip_infrastructure']} skip(infra)",
        file=sys.stderr,
    )
    print(json.dumps(results_data, indent=2))


# ---------------------------------------------------------------------------
# Main entry points (prepare, search, pipeline, and orchestration)
# ---------------------------------------------------------------------------

def _write_candidates(source, source_label, workdir, found, parsed, skipped,
                      candidates):
    """Write bugs/bug-candidates-<source>.json and print it."""
    result = {
        "source": source,
        "source_label": source_label,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "job_files_found": found,
        "job_files_parsed": parsed,
        "job_files_skipped": skipped,
        "candidates": candidates,
    }

    bugs_dir = os.path.join(workdir, "bugs")
    os.makedirs(bugs_dir, exist_ok=True)
    output_path = os.path.join(bugs_dir, f"bug-candidates-{source}.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Written: {output_path}", file=sys.stderr)
    print(json.dumps(result, indent=2))


def main_prepare(source, workdir):
    """Entry point for prepare mode: build bug candidates from job files."""
    if not os.path.isdir(workdir):
        print(f"Error: work directory does not exist: {workdir}", file=sys.stderr)
        sys.exit(1)

    files, source_label = find_job_files(workdir, source)
    if not files:
        if not _confirmed_no_failures(workdir, source):
            print(f"Error: no analysis reports found for {source_label} in "
                  f"{workdir}", file=sys.stderr)
            sys.exit(1)
        # Healthy source: the collect stage recorded zero failed jobs, so no
        # per-job analysis reports are expected. Publish an empty candidate
        # set so the pipeline completes and overwrites any stale mapping.
        print(f"No failed jobs for {source_label} in {workdir}", file=sys.stderr)
        _write_candidates(source, source_label, workdir,
                          found=0, parsed=0, skipped=0, candidates=[])
        return

    print(f"Found {len(files)} job files for {source_label}", file=sys.stderr)

    # Parse all files
    jobs = []
    skipped = 0
    for filepath in files:
        summaries = parse_structured_summary(filepath)
        if not summaries:
            print(f"  WARNING: no valid JSON in {os.path.basename(filepath)}", file=sys.stderr)
            skipped += 1
            continue
        jobs.extend(summaries)

    if not jobs:
        print("No valid job reports found", file=sys.stderr)
        sys.exit(1)

    print(f"Parsed {len(jobs)} jobs ({skipped} skipped)", file=sys.stderr)

    # Group and build candidates
    groups = group_by_signature(jobs)
    candidates = build_candidates(groups)

    print(f"Deduplicated to {len(candidates)} bug candidates", file=sys.stderr)

    _write_candidates(source, source_label, workdir,
                      found=len(files), parsed=len(jobs), skipped=skipped,
                      candidates=candidates)


def main_search(source, workdir):
    """Entry point for --search mode: search Jira for bugs matching candidates."""
    if not os.path.isdir(workdir):
        print(f"Error: work directory does not exist: {workdir}", file=sys.stderr)
        sys.exit(1)

    bugs_dir = os.path.join(workdir, "bugs")
    candidates_path = os.path.join(bugs_dir, f"bug-candidates-{source}.json")

    if not os.path.isfile(candidates_path):
        print(f"Error: candidates file not found: {candidates_path}", file=sys.stderr)
        sys.exit(1)

    with open(candidates_path) as f:
        candidates_data = json.load(f)

    if not jira_search.credentials_available():
        raise jira_search.JiraSearchError(
            "JIRA_USERNAME and JIRA_API_TOKEN are required")
    result = find_jira_bugs_for_source(candidates_data, include_open_bugs=True)

    output_path = os.path.join(bugs_dir, f"bug-matches-{source}.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Written: {output_path}", file=sys.stderr)
    print(json.dumps(result, indent=2))


def main_pipeline(sources_str, workdir):
    """Entry point for --pipeline mode: prepare→search→merge→categorize→report."""
    if not os.path.isdir(workdir):
        print(f"Error: work directory does not exist: {workdir}", file=sys.stderr)
        sys.exit(1)

    # Parse sources
    sources = [s.strip() for s in sources_str.split(",") if s.strip()]
    if not sources:
        print("Error: --pipeline requires at least one source", file=sys.stderr)
        sys.exit(1)

    # Auto-append rebase sources by scanning prs-status.json
    prs_status_path = os.path.join(workdir, "jobs", "prs-status.json")
    if os.path.isfile(prs_status_path):
        with open(prs_status_path) as f:
            prs_data = json.load(f)
        for pr_entry in prs_data:
            title = pr_entry.get("title", "")
            if "rebase-release-" in title:
                # Extract rebase source from title
                match = re.search(r"rebase-release-([\w\.]+)", title)
                if match:
                    rebase_source = f"rebase-release-{match.group(1)}"
                    if rebase_source not in sources:
                        sources.append(rebase_source)
                        print(f"Auto-appended rebase source: {rebase_source}", file=sys.stderr)

    print(f"Pipeline sources: {', '.join(sources)}", file=sys.stderr)

    # Step 1: Prepare fresh candidates for each source. A source whose
    # analysis reports are missing or invalid is a genuine failure: abort
    # before publishing any mapping so stale bug-matches-<source>.json files
    # are never finalized into the HTML report. Sources with confirmed zero
    # failed jobs succeed with an empty candidate set (see
    # _confirmed_no_failures).
    active_sources = []
    for source in sources:
        print(f"\n=== Preparing candidates for {source} ===", file=sys.stderr)
        main_prepare(source, workdir)
        active_sources.append(source)

    if not jira_search.credentials_available():
        raise jira_search.JiraSearchError(
            "JIRA_USERNAME and JIRA_API_TOKEN are required")

    # Step 2: Complete every Jira search before publishing any mapping.
    match_results = []
    for i, source in enumerate(active_sources):
        bugs_dir = os.path.join(workdir, "bugs")
        candidate_path = os.path.join(bugs_dir, f"bug-candidates-{source}.json")
        if not os.path.isfile(candidate_path):
            continue

        print(f"\n=== Searching Jira for {source} ===", file=sys.stderr)

        # Read candidates
        with open(candidate_path) as f:
            candidates_data = json.load(f)

        result = find_jira_bugs_for_source(
            candidates_data, include_open_bugs=(i == 0))
        match_results.append((source, result))

    for source, result in match_results:
        output_path = os.path.join(bugs_dir, f"bug-matches-{source}.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Written: {output_path}", file=sys.stderr)

    # Step 3: Merge candidates
    print(f"\n=== Merging candidates ===", file=sys.stderr)
    bugs_dir = os.path.join(workdir, "bugs")
    merge_files = [os.path.join(bugs_dir, f"bug-candidates-{s}.json")
                   for s in active_sources]

    # Determine tag for merged file
    release_sources = [s for s in active_sources if not s.startswith("rebase-")]
    tag = release_sources[0] if release_sources else active_sources[0]

    merged_output = os.path.join(bugs_dir, f"bug-candidates-merged-{tag}.json")
    main_merge(merge_files, merged_output, workdir)

    # Step 4: Categorize
    print(f"\n=== Categorizing candidates ===", file=sys.stderr)
    results_output = os.path.join(bugs_dir, f"bug-results-{tag}.json")
    main_categorize(merged_output, results_output, workdir)

    # Step 5: Generate report
    print(f"\n=== Generating report ===", file=sys.stderr)
    main_report(results_output, merged_output, workdir)

    print(f"\n=== Pipeline complete ===", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Prepare bug candidates from per-job analysis reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Mutually exclusive group for modes
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--search",
        metavar="SOURCE",
        help="search Jira for bugs matching candidates (reads bug-candidates-<source>.json, writes bug-matches-<source>.json)",
    )
    mode_group.add_argument(
        "--pipeline",
        metavar="SOURCES",
        help="full pipeline: prepare→search→merge→categorize→report for comma-separated sources; auto-appends rebase sources",
    )
    mode_group.add_argument(
        "--merge",
        action="store_true",
        help="merge multiple candidate files with fuzzy dedup",
    )
    mode_group.add_argument(
        "--categorize",
        metavar="FILE",
        help="merged candidates JSON to categorize with the deterministic decision policy (writes bug-results-*.json)",
    )
    mode_group.add_argument(
        "--report",
        metavar="FILE",
        help="results JSON to generate a report from",
    )

    # Common options
    parser.add_argument(
        "--component",
        choices=sorted(SEARCH_COMPONENTS),
        default=DEFAULT_COMPONENT,
        help="component whose Jira scope/inventory/labels to search (default: %(default)s)",
    )
    parser.add_argument(
        "--workdir",
        metavar="DIR",
        required=True,
        help="working directory (required)",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        help="output path for merged candidates (--merge mode) or results (--categorize mode)",
    )
    parser.add_argument(
        "--candidates",
        metavar="FILE",
        help="merged candidates JSON (required with --report)",
    )

    # Positional arguments (source or files)
    parser.add_argument(
        "sources",
        nargs="*",
        metavar="SOURCE|FILE",
        help="release version (4.22, main), PR number (pr-6396), rebase shorthand (rebase-release-4.22), or candidate files to merge (--merge mode)",
    )

    args = parser.parse_args()

    # Select component scope/inventory/prefix before any search or report runs.
    configure_component(args.component)

    # --search mode
    if args.search:
        return main_search(args.search, args.workdir)

    # --pipeline mode
    if args.pipeline:
        return main_pipeline(args.pipeline, args.workdir)

    # --categorize mode
    if args.categorize:
        return main_categorize(args.categorize, args.output, args.workdir)

    # --report mode
    if args.report:
        if not args.candidates:
            parser.error("--report requires --candidates")
        return main_report(args.report, args.candidates, args.workdir)

    # --merge mode
    if args.merge:
        return main_merge(args.sources, args.output, args.workdir)

    # Default: prepare mode
    if not args.sources or len(args.sources) != 1:
        parser.error("prepare mode requires exactly one SOURCE argument")

    return main_prepare(args.sources[0], args.workdir)


def main_merge(merge_files, output_file, workdir):
    """Entry point for --merge mode."""
    if not merge_files:
        print("Error: --merge requires at least one candidate file", file=sys.stderr)
        sys.exit(1)

    for filepath in merge_files:
        if not os.path.isfile(filepath):
            print(f"Error: file not found: {filepath}", file=sys.stderr)
            sys.exit(1)

    if not output_file:
        print("Error: --output FILE is required for --merge", file=sys.stderr)
        sys.exit(1)

    os.makedirs(workdir, exist_ok=True)
    bugs_dir = os.path.join(workdir, "bugs")
    os.makedirs(bugs_dir, exist_ok=True)

    print(f"Merging {len(merge_files)} candidate files", file=sys.stderr)
    result = merge_candidate_files(merge_files, workdir=workdir)

    n_merged = len(result["candidates"])
    n_total = result["total_candidates"]
    n_cross = n_total - n_merged
    print(f"Merged {n_total} candidates into {n_merged} unique failures "
          f"({n_cross} cross-release duplicates)", file=sys.stderr)

    with open(output_file, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Written: {output_file}", file=sys.stderr)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except jira_search.JiraSearchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
