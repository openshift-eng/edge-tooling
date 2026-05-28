#!/usr/bin/env python3
"""
Aggregate per-job analysis reports into a release or PR summary JSON file.

Shared across components (MicroShift, LVMS, etc.) via symlinks in each
plugin's scripts/ directory.

Usage:
    aggregate.py --release 4.22 [--workdir DIR]
    aggregate.py --prs [--workdir DIR]

Output files:
    analyze-ci-release-<version>-summary.json
    analyze-ci-prs-summary.json
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

SIMILARITY_THRESHOLD = 0.50


# ---------------------------------------------------------------------------
# Parsing per-job report files
# ---------------------------------------------------------------------------

def parse_structured_summary(filepath):
    """Extract the STRUCTURED SUMMARY block from a per-job report file."""
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
    }


def parse_prose_fields(filepath):
    """Extract Error: and Suggested Remediation: from report prose."""
    with open(filepath, "r") as f:
        content = f.read()

    prose = content.split("--- STRUCTURED SUMMARY ---")[0]

    error = ""
    m = re.search(
        r"^Error:\s*(.+?)(?=\nSuggested Remediation:|\nError Severity:|\nStack Layer:|\nStep Name:|\n\n|\n---|\Z)",
        prose, re.MULTILINE | re.DOTALL,
    )
    if m:
        error = " ".join(m.group(1).split())

    remediation = ""
    m = re.search(
        r"^Suggested Remediation:\s*(.+?)(?=\n\n|\n---|\nError Severity:|\nStack Layer:|\nStep Name:|\Z)",
        prose, re.MULTILINE | re.DOTALL,
    )
    if m:
        remediation = " ".join(m.group(1).split())

    return error, remediation


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _normalize_step_name(step_name):
    """Extract the step ref from a fully-qualified Prow step name.

    Prow step names follow the pattern ``<test-variant>-<step-ref>``
    where the step ref typically starts with a known prefix such as
    ``openshift-microshift-``.  The LLM sometimes includes the
    test-variant prefix, sometimes not, which would cause identical
    steps to land in different buckets during two-pass grouping.

    The regex harmlessly falls through for components that don't match
    the MicroShift pattern — the original step_name is returned as-is.
    """
    m = re.search(r"(openshift-microshift-\S+)", step_name)
    return m.group(1) if m else step_name


def _tokenize(text):
    words = re.findall(r"[a-z0-9][a-z0-9_.-]*[a-z0-9]|[a-z0-9]", text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) >= 2}


def signature_similarity(sig_a, sig_b):
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


def _group_by_similarity(jobs):
    """Group jobs by similarity of their grouping text.

    Uses RAW_ERROR when available (deterministic log text),
    falling back to ERROR_SIGNATURE for older reports.

    A new job is compared against ALL existing members of each group,
    not just the first.  If any member exceeds the similarity threshold
    the job joins that group.  This makes grouping less sensitive to
    insertion order and to phrasing variation — each member added to
    a group acts as an additional reference point for future matches.
    """
    groups = []
    for job in jobs:
        sig = _grouping_text(job)
        placed = False
        for group in groups:
            if any(
                signature_similarity(sig, _grouping_text(member)) >= SIMILARITY_THRESHOLD
                for member in group
            ):
                group.append(job)
                placed = True
                break
        if not placed:
            groups.append([job])
    return groups


def group_by_signature(jobs):
    """Two-pass grouping: first by step_name, then by signature similarity.

    Grouping by step_name first prevents jobs from different CI steps
    (e.g. conformance vs metal-tests) from being merged together even
    when their error signatures share enough tokens to exceed the
    similarity threshold.  This makes the issue count deterministic
    across runs where only the signature wording varies.
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


def classify_severity(group):
    count = len(group)
    if count >= 5:
        return "CRITICAL"
    if count >= 3:
        return "HIGH"
    if count >= 2:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# JSON generation
# ---------------------------------------------------------------------------

def build_release_json(release, jobs, timestamp):
    """Build the release summary as a dict (ready for json.dump)."""
    issues, breakdown = _build_issues_from_jobs(jobs)

    return {
        "release": release,
        "total_failed": len(jobs),
        "date": timestamp.strftime("%Y-%m-%d"),
        "breakdown": breakdown,
        "issues": issues,
    }


def _build_issues_from_jobs(jobs):
    """Group jobs by error signature and return (issues list, breakdown dict).

    Shared by both release and PR builders.
    """
    groups = group_by_signature(jobs)
    groups.sort(key=lambda g: (-max(j["severity"] for j in g), -len(g), g[0].get("error_signature", "")))

    breakdown = {"build": 0, "test": 0, "infrastructure": 0}
    for job in jobs:
        breakdown[classify_breakdown(
            job["stack_layer"],
            job.get("step_name", ""),
            job.get("error_signature", ""),
            job.get("infrastructure_failure", False),
        )] += 1

    issues = []
    for i, group in enumerate(groups, 1):
        rep = max(group, key=lambda j: (j["severity"], j.get("job_name", "")))
        failure_type = classify_breakdown(
            rep["stack_layer"],
            rep.get("step_name", ""),
            rep.get("error_signature", ""),
            any(j.get("infrastructure_failure") for j in group),
        )
        issues.append({
            "number": i,
            "title": rep["error_signature"],
            "job_count": len(group),
            "severity": classify_severity(group),
            "failure_type": failure_type,
            "root_cause": rep.get("root_cause") or rep.get("error_text", ""),
            "next_steps": rep.get("remediation_text", ""),
            "affected_jobs": [
                {"name": j["job_name"], "date": j["finished"], "url": j["job_url"]}
                for j in group
            ],
        })

    return issues, breakdown


def build_pr_json(pr_jobs, timestamp):
    """Build the PR summary as a dict (ready for json.dump).

    pr_jobs: dict mapping pr_number to list of job dicts.
    """
    total_failed = sum(len(jobs) for jobs in pr_jobs.values())

    prs = []
    for pr_number, jobs in sorted(pr_jobs.items()):
        if not jobs:
            continue
        first = jobs[0]
        issues, breakdown = _build_issues_from_jobs(jobs)
        prs.append({
            "number": pr_number,
            "title": first.get("pr_title", ""),
            "url": first.get("pr_url", ""),
            "failed": len(jobs),
            "breakdown": breakdown,
            "issues": issues,
        })

    return {
        "total_prs": len(pr_jobs),
        "prs_with_failures": len(prs),
        "total_failed": total_failed,
        "date": timestamp.strftime("%Y-%m-%d"),
        "has_content": total_failed > 0,
        "prs": prs,
    }


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_release_job_files(workdir, release):
    pattern = os.path.join(workdir, f"analyze-ci-release-{release}-job-*.txt")
    return sorted(glob_mod.glob(pattern))


def find_pr_job_files(workdir):
    pattern = os.path.join(workdir, "analyze-ci-prs-job-*.txt")
    return sorted(glob_mod.glob(pattern))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    workdir = None
    release = None
    mode = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--workdir":
            if i + 1 >= len(args):
                print("Error: --workdir requires an argument", file=sys.stderr)
                sys.exit(1)
            workdir = args[i + 1]
            i += 2
        elif args[i] == "--release":
            if i + 1 >= len(args):
                print("Error: --release requires a version", file=sys.stderr)
                sys.exit(1)
            mode = "release"
            release = args[i + 1]
            i += 2
        elif args[i] == "--prs":
            mode = "prs"
            i += 1
        elif args[i].startswith("-"):
            print(f"Unknown option: {args[i]}", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if mode is not None and args.count("--release") + args.count("--prs") > 1:
        print("Error: --release and --prs are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    if mode is None:
        print(
            "Usage:\n"
            "  aggregate.py --release <version> --workdir DIR\n"
            "  aggregate.py --prs --workdir DIR",
            file=sys.stderr,
        )
        sys.exit(1)

    if workdir is None:
        print("Error: --workdir is required", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(workdir):
        print(f"Error: work directory does not exist: {workdir}", file=sys.stderr)
        sys.exit(1)

    timestamp = datetime.now(timezone.utc)

    if mode == "release":
        files = find_release_job_files(workdir, release)
        if not files:
            print(f"No job files found for release {release}", file=sys.stderr)
            sys.exit(1)

        print(f"Found {len(files)} job files for release {release}", file=sys.stderr)
        jobs = []
        for filepath in files:
            summary = parse_structured_summary(filepath)
            if summary is None:
                print(f"  WARNING: no STRUCTURED SUMMARY in {os.path.basename(filepath)}", file=sys.stderr)
                continue
            error_text, remediation_text = parse_prose_fields(filepath)
            summary["error_text"] = error_text
            summary["remediation_text"] = remediation_text
            jobs.append(summary)

        if not jobs:
            print("No valid job reports found", file=sys.stderr)
            sys.exit(1)

        result = build_release_json(release, jobs, timestamp)
        output_path = os.path.join(workdir, f"analyze-ci-release-{release}-summary.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Written: {output_path}", file=sys.stderr)
        print(json.dumps(result, indent=2))

    elif mode == "prs":
        files = find_pr_job_files(workdir)
        if not files:
            print("No PR job files found", file=sys.stderr)
            result = build_pr_json({}, timestamp)
        else:
            print(f"Found {len(files)} PR job files", file=sys.stderr)
            pr_jobs = {}
            for filepath in files:
                summary = parse_structured_summary(filepath)
                if summary is None:
                    print(f"  WARNING: no STRUCTURED SUMMARY in {os.path.basename(filepath)}", file=sys.stderr)
                    continue
                error_text, remediation_text = parse_prose_fields(filepath)
                summary["error_text"] = error_text
                summary["remediation_text"] = remediation_text
                summary["pr_title"] = ""
                summary["pr_url"] = ""

                m = re.search(r"-pr(\d+)-", os.path.basename(filepath))
                pr_number = int(m.group(1)) if m else 0
                pr_jobs.setdefault(pr_number, []).append(summary)

            result = build_pr_json(pr_jobs, timestamp)

        output_path = os.path.join(workdir, "analyze-ci-prs-summary.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Written: {output_path}", file=sys.stderr)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
