#!/usr/bin/env python3
"""
Prepare bug candidates from per-job analysis reports.

Parses STRUCTURED SUMMARY blocks, groups by ERROR_SIGNATURE similarity,
extracts Jira search keywords, and writes a candidates JSON file for
the create-bugs skill to search Jira against.

Usage:
    search-bugs.py <source> [--workdir DIR]
    search-bugs.py --merge <bugs-file1.json> <bugs-file2.json> ... [--workdir DIR]

    <source> is one of:
      - Release version: 4.22, main
      - PR number: pr-6396, pr6396
      - Rebase shorthand: rebase-release-4.22

    --merge mode reads multiple analyze-ci-bug-candidates-<source>.json
    files and merges candidates across sources using fuzzy signature
    matching for cross-release dedup.

Output:
    ${WORKDIR}/analyze-ci-bug-candidates-<source>.json       (default mode)
    ${WORKDIR}/analyze-ci-bug-candidates-merged.json          (--merge mode)
"""

import json
import sys
import os
import re
import glob as glob_mod
from datetime import datetime, timezone

from classify import classify_breakdown


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by",
    "is", "was", "are", "were", "be", "been", "and", "or", "not", "no",
    "but", "from", "that", "this", "all", "has", "have", "had", "do",
    "does", "did", "will", "would", "could", "should", "may", "might",
})

# Additional stop words filtered only during keyword extraction for Jira search,
# not during signature grouping (which must match aggregate.py's tokenization).
KEYWORD_STOP_WORDS = STOP_WORDS | frozenset({
    "ci", "microshift", "failure", "failed", "error", "test", "tests",
    "job", "jobs", "step", "periodic",
})

SIMILARITY_THRESHOLD = 0.50


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_structured_summary(filepath):
    """Extract STRUCTURED SUMMARY block from a per-job report file."""
    with open(filepath, "r") as f:
        content = f.read()

    m = re.search(
        r"--- STRUCTURED SUMMARY ---\n(.+?)\n--- END STRUCTURED SUMMARY ---",
        content, re.DOTALL,
    )
    if not m:
        return None

    data = {}
    for line in m.group(1).strip().split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            data[key.strip()] = val.strip()

    try:
        severity = int(data.get("SEVERITY", "3"))
    except ValueError:
        severity = 3

    # Get the analysis text (everything before STRUCTURED SUMMARY)
    analysis_text = content.split("--- STRUCTURED SUMMARY ---")[0].strip()

    return {
        "severity": severity,
        "stack_layer": data.get("STACK_LAYER", ""),
        "step_name": data.get("STEP_NAME", ""),
        "error_signature": data.get("ERROR_SIGNATURE", ""),
        "raw_error": data.get("RAW_ERROR", ""),
        "root_cause": data.get("ROOT_CAUSE", ""),
        "infrastructure_failure": data.get("INFRASTRUCTURE_FAILURE", "false").lower() == "true",
        "job_url": data.get("JOB_URL", ""),
        "job_name": data.get("JOB_NAME", ""),
        "release": data.get("RELEASE", ""),
        "finished": data.get("FINISHED", ""),
        "analysis_text": analysis_text,
        "source_file": filepath,
    }


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _normalize_step_name(step_name):
    """Extract the step ref from a fully-qualified Prow step name.

    Prow step names follow ``<test-variant>-<step-ref>`` where the
    step ref typically starts with ``openshift-microshift-``.
    """
    m = re.search(r"(openshift-microshift-\S+)", step_name)
    return m.group(1) if m else step_name


def _tokenize(text):
    words = re.findall(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|[a-z0-9]", text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) >= 2}


def _signature_similarity(sig_a, sig_b):
    tokens_a = _tokenize(sig_a)
    tokens_b = _tokenize(sig_b)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))


def _grouping_text(job):
    """Return the text used for similarity grouping.

    Prefers RAW_ERROR (verbatim log text, deterministic) over
    ERROR_SIGNATURE (LLM-paraphrased, variable across runs).
    Appends ROOT_CAUSE when present to improve cross-release matching
    for failures that share the same underlying mechanism.
    """
    base = job.get("raw_error") or job.get("error_signature", "")
    root_cause = job.get("root_cause", "")
    if root_cause:
        return base + " " + root_cause
    return base


def _cluster_by_similarity(items, key_fn):
    """Cluster items whose key texts exceed the similarity threshold.

    A new item is compared against ALL existing members of each cluster.
    If any member exceeds the threshold the item joins that cluster.
    """
    groups = []
    for item in items:
        sig = key_fn(item)
        placed = False
        for group in groups:
            if any(
                _signature_similarity(sig, key_fn(member)) >= SIMILARITY_THRESHOLD
                for member in group
            ):
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    return groups


def _group_by_similarity(jobs):
    """Group jobs by similarity of their grouping text.

    Uses RAW_ERROR when available (deterministic log text),
    falling back to ERROR_SIGNATURE for older reports.
    """
    return _cluster_by_similarity(jobs, _grouping_text)


def group_by_signature(jobs):
    """Two-pass grouping: first by step_name, then by signature similarity.

    Grouping by step_name first prevents jobs from different CI steps
    from being merged together even when their error signatures share
    enough tokens to exceed the similarity threshold.
    """
    # Pass 1: bucket by normalized step_name
    by_step = {}
    for job in jobs:
        step = _normalize_step_name(job.get("step_name", ""))
        by_step.setdefault(step, []).append(job)

    # Pass 2: within each step bucket, group by signature similarity
    all_groups = []
    for step_jobs in by_step.values():
        all_groups.extend(_group_by_similarity(step_jobs))
    return all_groups


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

def _tokenize_for_keywords(text):
    """Tokenize with extra stop words filtered for Jira keyword extraction."""
    words = re.findall(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|[a-z0-9]", text.lower())
    return {w for w in words if w not in KEYWORD_STOP_WORDS and len(w) >= 2}


def extract_keywords(error_signature):
    """Extract distinctive search keywords from an error signature.

    Returns a list of 2-4 keywords ranked by specificity.
    Uses KEYWORD_STOP_WORDS (broader filtering) so generic CI terms
    like "test", "failed", "microshift" don't pollute Jira searches.
    """
    tokens = _tokenize_for_keywords(error_signature)
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
            "severity": max(j["severity"] for j in group),
            "failure_type": classify_breakdown(
                rep["stack_layer"],
                rep.get("step_name", ""),
                rep.get("error_signature", ""),
            ),
            "step_name": ", ".join(step_names),
            "affected_jobs": len(group),
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
            "analysis_text": rep["analysis_text"],
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
    """
    # Release version
    if re.match(r"^(\d+\.\d+|main)$", source):
        pattern = os.path.join(workdir, f"analyze-ci-release-{source}-job-*.txt")
        files = sorted(glob_mod.glob(pattern))
        return files, f"release {source}"

    # PR number
    m = re.match(r"^pr-?(\d+)$", source)
    if m:
        pr_num = m.group(1)
        pattern = os.path.join(workdir, f"analyze-ci-prs-job-*-pr{pr_num}-*.txt")
        files = sorted(glob_mod.glob(pattern))
        return files, f"PR #{pr_num}"

    # Rebase PR shorthand — jobs may target a different branch than the
    # rebase source name (e.g. rebase-release-5.0 jobs run on branch main)
    m = re.match(r"^rebase-release-(.+)$", source)
    if m:
        release = m.group(1)

        # Find PR numbers for this rebase source from the status file
        rebase_pr_numbers = set()
        status_file = os.path.join(workdir, "analyze-ci-prs-status.json")
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

        pattern = os.path.join(workdir, "analyze-ci-prs-job-*.txt")
        all_files = sorted(glob_mod.glob(pattern))
        files = []
        for filepath in all_files:
            # Match by PR number extracted from filename
            pr_match = re.search(r"-pr(\d+)-", os.path.basename(filepath))
            if pr_match and int(pr_match.group(1)) in rebase_pr_numbers:
                files.append(filepath)
                continue

            # Fallback: match by structured summary fields
            summary = parse_structured_summary(filepath)
            if summary and (
                f"release-{release}" in summary.get("job_name", "")
                or summary.get("release", "") == release
            ):
                files.append(filepath)

        return files, f"rebase PR for {release}"

    return [], source


# ---------------------------------------------------------------------------
# Cross-release merge
# ---------------------------------------------------------------------------

def _merge_by_similarity(candidates):
    """Group candidates by error_signature + root_cause similarity for cross-release dedup."""
    def _merge_key(c):
        base = c.get("error_signature", "")
        root_cause = c.get("root_cause", "")
        if root_cause:
            return base + " " + root_cause
        return base
    return _cluster_by_similarity(candidates, _merge_key)


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
    """Load Jira duplicates/regressions from bug mapping files in workdir.

    Returns a dict mapping error_signature to {duplicates, regressions}.
    """
    lookup = {}
    pattern = os.path.join(workdir, "analyze-ci-bugs-*.json")
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

    Handles both pre-Jira candidate files (keywords, test_ids, jobs,
    analysis_text) and post-Jira bug mapping files (duplicates, regressions).

    When workdir is provided and contains bug mapping files
    (analyze-ci-bugs-*.json), their Jira data is injected into candidates
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
        step = _normalize_step_name(cand.get("step_name", ""))
        by_step.setdefault(step, []).append(cand)

    merged_groups = []
    for step_cands in by_step.values():
        merged_groups.extend(_merge_by_similarity(step_cands))

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
            "severity": max(c["severity"] for c in group),
            "failure_type": rep.get("failure_type", "test"),
            "step_name": ", ".join(step_names) if step_names else rep.get("step_name", ""),
            "affected_jobs": sum(c["affected_jobs"] for c in group),
            "keywords": sorted(all_keywords),
            "test_ids": sorted(all_test_ids),
            "jobs": all_jobs,
            "analysis_text": rep.get("analysis_text", ""),
            "releases": releases,
        }
        if other_sigs:
            entry["merged_signatures"] = other_sigs
        if all_duplicates:
            entry["duplicates"] = list(all_duplicates.values())
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
# Main
# ---------------------------------------------------------------------------

def main():
    workdir = None
    source = None
    merge_mode = False
    merge_files = []

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--merge":
            merge_mode = True
            i += 1
        elif args[i] == "--workdir":
            if i + 1 >= len(args):
                print("Error: --workdir requires an argument", file=sys.stderr)
                sys.exit(1)
            workdir = args[i + 1]
            i += 2
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            sys.exit(1)
        else:
            if merge_mode:
                merge_files.append(args[i])
            else:
                source = args[i]
            i += 1

    if merge_mode:
        return main_merge(merge_files, workdir)

    if not source:
        print(
            "Usage: search-bugs.py <source> [--workdir DIR]\n"
            "       search-bugs.py --merge <bugs-file1.json> ... [--workdir DIR]\n"
            "  <source>: release version (4.22), PR (pr-6396), or rebase (rebase-release-4.22)",
            file=sys.stderr,
        )
        sys.exit(1)

    if workdir is None:
        workdir = f"/tmp/microshift-ci-claude-workdir.{datetime.now().strftime('%y%m%d')}"

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
        summary = parse_structured_summary(filepath)
        if summary is None:
            print(f"  WARNING: no STRUCTURED SUMMARY in {os.path.basename(filepath)}", file=sys.stderr)
            skipped += 1
            continue
        jobs.append(summary)

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

    output_path = os.path.join(workdir, f"analyze-ci-bug-candidates-{source}.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Written: {output_path}", file=sys.stderr)
    print(json.dumps(result, indent=2))


def main_merge(merge_files, workdir):
    """Entry point for --merge mode."""
    if not merge_files:
        print(
            "Usage: search-bugs.py --merge <candidates1.json> <candidates2.json> ... [--workdir DIR]",
            file=sys.stderr,
        )
        sys.exit(1)

    for filepath in merge_files:
        if not os.path.isfile(filepath):
            print(f"Error: file not found: {filepath}", file=sys.stderr)
            sys.exit(1)

    if workdir is None:
        workdir = f"/tmp/microshift-ci-claude-workdir.{datetime.now().strftime('%y%m%d')}"

    os.makedirs(workdir, exist_ok=True)

    print(f"Merging {len(merge_files)} candidate files", file=sys.stderr)
    result = merge_candidate_files(merge_files, workdir=workdir)

    n_merged = len(result["candidates"])
    n_total = result["total_candidates"]
    n_cross = n_total - n_merged
    print(f"Merged {n_total} candidates into {n_merged} unique failures "
          f"({n_cross} cross-release duplicates)", file=sys.stderr)

    output_path = os.path.join(workdir, "analyze-ci-bug-candidates-merged.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Written: {output_path}", file=sys.stderr)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
