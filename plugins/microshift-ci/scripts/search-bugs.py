#!/usr/bin/env python3
"""Prepare bug candidates from per-job analysis reports."""

import argparse
import json
import sys
import os
import re
import glob as glob_mod
from datetime import datetime, timezone

import jira_search
from classify import classify_breakdown
from parse import (
    STOP_WORDS, normalize_step_name, cluster_by_similarity,
    group_by_signature, grouping_text, parse_structured_summary, tokenize,
)


# Additional stop words filtered only during keyword extraction for Jira search,
# not during signature grouping (which uses the shared STOP_WORDS).
KEYWORD_STOP_WORDS = STOP_WORDS | frozenset({
    "ci", "microshift", "failure", "failed", "error", "test", "tests",
    "job", "jobs", "step", "periodic",
})


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def extract_keywords(error_signature):
    """Extract distinctive search keywords from an error signature.

    Returns a list of 2-4 keywords ranked by specificity.
    Uses KEYWORD_STOP_WORDS (broader filtering) so generic CI terms
    like "test", "failed", "microshift" don't pollute Jira searches.
    """
    tokens = tokenize(error_signature, KEYWORD_STOP_WORDS)
    if not tokens:
        return []

    def specificity(token):
        score = len(token)
        if "-" in token or "." in token:
            score += 10
        if any(c.isdigit() for c in token):
            score += 5
        return score

    ranked = sorted(tokens, key=lambda t: (-specificity(t), t))
    return ranked[:4]


def extract_test_ids(error_signature):
    """Extract numeric test case IDs (4-6 digits) from error signature."""
    return re.findall(r"\b(\d{4,6})\b", error_signature)


# ---------------------------------------------------------------------------
# Jira search (JQL query builders and search logic)
# ---------------------------------------------------------------------------

_JIRA_SCOPE = "((project = OCPBUGS AND component = MicroShift) OR project = USHIFT) AND issuetype = Bug"


def build_search_jql_open_match_keyword(keyword):
    """Build JQL for open bugs matching keyword (for duplicate detection)."""
    return f'{_JIRA_SCOPE} AND text ~ "{keyword}" AND status not in (Closed, Verified)'


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


def build_search_jql_open_all():
    """Build JQL for broad open-bugs query (all open bugs in scope)."""
    return f'{_JIRA_SCOPE} AND status not in (Closed, Verified) ORDER BY updated DESC'


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


def find_jira_bugs_for_candidate(cand, search_fn=None):
    """Search Jira for bugs matching one candidate's keywords and test IDs.

    Returns {duplicates: [...], regressions: [...]}.
    Uses injectable search_fn for testability (defaults to jira_search.search).
    """
    if search_fn is None:
        search_fn = jira_search.search

    error_signature = cand.get("error_signature", "")
    keywords = extract_keywords(error_signature)
    test_ids = extract_test_ids(error_signature)

    # Find open bugs by keyword (top 3 keywords) → potential duplicates
    duplicates_dict = {}
    for kw in keywords[:3]:
        jql = build_search_jql_open_match_keyword(kw)
        issues = search_fn(jql, fields="summary,status,assignee,updated", max_results=5)
        if issues:
            for iss in issues:
                key = iss.get("key")
                if key and key not in duplicates_dict:
                    duplicates_dict[key] = _convert_jira_issue_to_entry(iss)

    # Find open bugs by test ID (both bare and OCP-prefixed forms) → potential duplicates
    for tid in test_ids:
        for ocp_prefixed in [False, True]:
            jql = build_search_jql_open_match_test_id(tid, ocp_prefixed=ocp_prefixed)
            issues = search_fn(jql, fields="summary,status,assignee,updated", max_results=5)
            if issues:
                for iss in issues:
                    key = iss.get("key")
                    if key and key not in duplicates_dict:
                        duplicates_dict[key] = _convert_jira_issue_to_entry(iss)

    # Find closed bugs by keyword (top 2 keywords) → potential regressions
    regressions_dict = {}
    for kw in keywords[:2]:
        jql = build_search_jql_closed_match_keyword(kw)
        issues = search_fn(jql, fields="summary,status,assignee,updated", max_results=5)
        if issues:
            for iss in issues:
                key = iss.get("key")
                if key and key not in regressions_dict:
                    regressions_dict[key] = _convert_jira_issue_to_entry(iss)

    return {
        "duplicates": list(duplicates_dict.values()),
        "regressions": list(regressions_dict.values()),
    }


def find_jira_bugs_for_source(candidates_data, search_fn=None, include_open_bugs=False):
    """Search Jira for all candidates in a source.

    Returns bug-matches dict: {source, date, candidates, open_bugs}.
    Each candidate gets {error_signature, severity, failure_type, step_name,
    affected_jobs, duplicates, regressions}.
    """
    if search_fn is None:
        search_fn = jira_search.search

    source = candidates_data.get("source", "")
    candidates = candidates_data.get("candidates", [])

    result_candidates = []
    for cand in candidates:
        search_result = find_jira_bugs_for_candidate(cand, search_fn=search_fn)
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

    # Optional broad open-bugs query (only for first source to avoid redundant queries)
    if include_open_bugs:
        jql = build_search_jql_open_all()
        issues = search_fn(jql, fields="summary,status,assignee,updated,priority,created", max_results=50)
        if issues:
            result["open_bugs"] = [_convert_jira_issue_to_entry(iss, include_priority_created=True) for iss in issues]
        else:
            result["open_bugs"] = []

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


# ---------------------------------------------------------------------------
# Cross-release merge
# ---------------------------------------------------------------------------


def _jira_keys(candidate):
    """Extract all Jira issue keys from a candidate's duplicates and regressions."""
    keys = set()
    for d in candidate.get("duplicates", []):
        if d.get("key"):
            keys.add(d["key"])
    for r in candidate.get("regressions", []):
        if r.get("key"):
            keys.add(r["key"])
    return keys


def _merge_groups_by_jira(groups):
    """Merge groups that share any Jira issue key across duplicates/regressions.

    Uses union-find to transitively merge: if group A shares a key with
    group B, and group B shares a different key with group C, all three
    merge.  Operates across step-name boundaries.
    """
    parent = list(range(len(groups)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[b] = a

    key_to_groups = {}
    for i, group in enumerate(groups):
        for cand in group:
            for key in _jira_keys(cand):
                key_to_groups.setdefault(key, set()).add(i)

    for indices in key_to_groups.values():
        indices = list(indices)
        for j in range(1, len(indices)):
            union(indices[0], indices[j])

    merged = {}
    for i, group in enumerate(groups):
        root = find(i)
        merged.setdefault(root, []).extend(group)

    return list(merged.values())


def _load_jira_lookup(workdir):
    """Load Jira duplicates/regressions from bug mapping files.

    Returns a dict mapping error_signature to {duplicates, regressions}.
    Bug mapping files live under ${workdir}/bugs/.
    """
    lookup = {}
    pattern = os.path.join(workdir, "bugs", "bug-matches-*.json")
    for filepath in sorted(glob_mod.glob(pattern)):
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
        sources.append(source)
        for cand in data.get("candidates", []):
            all_candidates.append({**cand, "_source": source})

    total_candidates = len(all_candidates)

    # Inject Jira data from bug mapping files when available.
    # The lookup is authoritative — always overwrite the candidate's
    # duplicates/regressions since candidate files never carry Jira data.
    jira_injected = 0
    if workdir:
        jira_lookup = _load_jira_lookup(workdir)
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

    # Build merged candidates from groups
    merged_candidates = []
    for group in merged_groups:
        rep = max(group, key=lambda c: (c["severity"], c["affected_jobs"], c["error_signature"]))

        # Build releases list (aggregate affected_jobs per source)
        releases_map = {}
        jobs_by_source = {}
        for cand in group:
            src = cand["_source"]
            releases_map[src] = releases_map.get(src, 0) + cand["affected_jobs"]
            jobs_by_source.setdefault(src, []).extend(cand.get("jobs", []))
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

        step_names = sorted({c.get("step_name", "") for c in group if c.get("step_name")})

        entry = {
            "error_signature": rep["error_signature"],
            "root_cause": rep.get("root_cause", ""),
            "raw_error": rep.get("raw_error", ""),
            "remediation": rep.get("remediation", ""),
            "severity": max(c["severity"] for c in group),
            "failure_type": rep.get("failure_type", "test"),
            "step_name": ", ".join(step_names) if step_names else rep.get("step_name", ""),
            "affected_jobs": sum(c["affected_jobs"] for c in group),
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
        lines.append(f"     MicroShift CI: {cand['error_signature']}")
        lines.append(f"     Severity: {cand['severity']} | Total Jobs: {cand['affected_jobs']} | Step: {cand['step_name']}")
        lines.append(f"     Releases: {_format_releases(cand.get('releases', []))}")

        grouped = _format_grouped_with(cand.get("merged_signatures", []))
        if grouped:
            lines.append(grouped)

        lines.append(f"     {_format_jira_refs('Potential Duplicates', cand.get('duplicates', []))}")
        lines.append(f"     {_format_jira_refs('Potential Regressions', cand.get('regressions', []))}")

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
        f"  Linked to existing: {counters['linked']}",
        f"  Skipped (infrastructure): {counters['skip_infrastructure']}",
        f"  Skipped (stale regression): {counters['skip_stale_regression']}",
    ])

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
# The suggest/linked/skip decision — including whether a failure is a
# regression of a closed Jira bug — is made here in code, NOT by the model.
# Keep the rules and reason strings in sync with the documented policy in
# skills/find-regressions/SKILL.md (Step 3 Decision Policy).

REASON_INFRASTRUCTURE = "Infrastructure failure — not a product bug"
REASON_NO_BUGS = "No existing bugs found — suggest filing a new bug"


def _most_recent_regression(regressions):
    """Return the regression with the most recent 'updated' date.

    'updated' values are ISO 'YYYY-MM-DD' strings, so a lexicographic max is
    chronological. A missing/empty 'updated' sorts lowest, so a regression
    carrying a real date is always preferred when one exists. Returns None
    for an empty list.
    """
    if not regressions:
        return None
    return max(regressions, key=lambda r: r.get("updated") or "")


def categorize_candidate(candidate):
    """Apply the deterministic decision policy to one merged candidate.

    Rules are applied in order (see SKILL.md Step 3):

      1. infrastructure failure                 -> skip / infrastructure
      2. open duplicates                        -> linked (first duplicate)
      3. closed regressions, no open duplicates -> compare job finish dates
         against the most recently fixed regression:
           - any job finished after that fix    -> suggest
           - all jobs on/before that fix        -> skip / stale_regression
           - indeterminate dates                -> suggest (never hide a regression)
      4. no duplicates, no regressions          -> suggest

    Returns {action, jira_key, skip_category, reason}. Missing
    duplicates/regressions keys are treated as empty (they are omitted from
    a merged candidate when empty).
    """
    failure_type = candidate.get("failure_type", "test")
    duplicates = candidate.get("duplicates") or []
    regressions = candidate.get("regressions") or []
    finished = [
        j.get("finished") for j in (candidate.get("jobs") or []) if j.get("finished")
    ]

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

    # Rule 3: a previously-closed bug covered this signature.
    if regressions:
        ref = _most_recent_regression(regressions)
        ref_updated = ref.get("updated") or ""
        ref_key = ref.get("key", "")
        # If we cannot establish that every failure predates the fix, treat it
        # as a potential regression rather than silently skipping.
        indeterminate = not ref_updated or not finished
        if indeterminate or any(f > ref_updated for f in finished):
            return {
                "action": "suggest",
                "jira_key": "",
                "skip_category": "",
                "reason": f"Potential regression of {ref_key} — suggest filing a new bug",
            }
        return {
            "action": "skip",
            "jira_key": "",
            "skip_category": "stale_regression",
            "reason": f"Stale failure predating fix for {ref_key} (updated {ref_updated})",
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
        f"{counters['linked']} linked, {counters['skip_infrastructure']} skip(infra), "
        f"{counters['skip_stale_regression']} skip(stale)",
        file=sys.stderr,
    )
    print(json.dumps(results_data, indent=2))


# ---------------------------------------------------------------------------
# Main entry points (prepare, search, pipeline, and orchestration)
# ---------------------------------------------------------------------------

def main_prepare(source, workdir):
    """Entry point for prepare mode: build bug candidates from job files."""
    if not os.path.isdir(workdir):
        print(f"Error: work directory does not exist: {workdir}", file=sys.stderr)
        sys.exit(1)

    files, source_label = find_job_files(workdir, source)
    if not files:
        print(f"No job files found for {source_label} in {workdir}", file=sys.stderr)
        sys.exit(1)

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

    # Build output
    result = {
        "source": source,
        "source_label": source_label,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "job_files_found": len(files),
        "job_files_parsed": len(jobs),
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

    # Check credentials
    if not jira_search.credentials_available():
        print(
            "WARNING: JIRA_USERNAME and/or JIRA_API_TOKEN not set.\n"
            "Writing bug-matches file with empty duplicates/regressions.\n"
            "Set credentials to enable Jira bug search.",
            file=sys.stderr,
        )
        # Write empty results
        result = find_jira_bugs_for_source(candidates_data, search_fn=lambda *a, **k: None, include_open_bugs=False)
        # Override to ensure all arrays are empty
        for cand in result["candidates"]:
            cand["duplicates"] = []
            cand["regressions"] = []
        result["open_bugs"] = []
    else:
        # Run actual searches (include_open_bugs=True for first source)
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

    # Step 1: Prepare candidates for each source
    candidate_files = []
    for source in sources:
        print(f"\n=== Preparing candidates for {source} ===", file=sys.stderr)
        bugs_dir = os.path.join(workdir, "bugs")
        candidate_path = os.path.join(bugs_dir, f"bug-candidates-{source}.json")

        # Check if already exists (skip prepare if so)
        if os.path.isfile(candidate_path):
            print(f"Using existing candidates: {candidate_path}", file=sys.stderr)
            candidate_files.append(candidate_path)
            continue

        # Run prepare
        try:
            main_prepare(source, workdir)
            candidate_files.append(candidate_path)
        except SystemExit as e:
            if e.code != 0:
                print(f"WARNING: Failed to prepare candidates for {source}, skipping", file=sys.stderr)
                continue
            candidate_files.append(candidate_path)

    if not candidate_files:
        print("Error: No candidates prepared", file=sys.stderr)
        sys.exit(1)

    # Step 2: Search Jira for each source (open_bugs only for first)
    for i, source in enumerate(sources):
        bugs_dir = os.path.join(workdir, "bugs")
        candidate_path = os.path.join(bugs_dir, f"bug-candidates-{source}.json")
        if not os.path.isfile(candidate_path):
            continue

        print(f"\n=== Searching Jira for {source} ===", file=sys.stderr)

        # Read candidates
        with open(candidate_path) as f:
            candidates_data = json.load(f)

        # Search (with credentials check)
        if not jira_search.credentials_available():
            print(
                "WARNING: JIRA_USERNAME and/or JIRA_API_TOKEN not set.\n"
                "Writing bug-matches with empty duplicates/regressions.\n"
                "Set credentials to enable Jira bug search.",
                file=sys.stderr,
            )
            result = find_jira_bugs_for_source(candidates_data, search_fn=lambda *a, **k: None, include_open_bugs=False)
            for cand in result["candidates"]:
                cand["duplicates"] = []
                cand["regressions"] = []
            result["open_bugs"] = []
        else:
            # Only include open_bugs for first source
            result = find_jira_bugs_for_source(candidates_data, include_open_bugs=(i == 0))

        # Write bug-matches
        output_path = os.path.join(bugs_dir, f"bug-matches-{source}.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Written: {output_path}", file=sys.stderr)

    # Step 3: Merge candidates
    print(f"\n=== Merging candidates ===", file=sys.stderr)
    bugs_dir = os.path.join(workdir, "bugs")
    merge_files = [os.path.join(bugs_dir, f"bug-candidates-{s}.json") for s in sources]
    merge_files = [f for f in merge_files if os.path.isfile(f)]

    # Determine tag for merged file
    release_sources = [s for s in sources if not s.startswith("rebase-")]
    if len(release_sources) == 1:
        tag = release_sources[0]
    elif len(release_sources) > 1:
        tag = "merged"
    else:
        tag = sources[0] if len(sources) == 1 else "merged"

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
    main()
