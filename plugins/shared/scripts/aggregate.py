#!/usr/bin/env python3
"""
Aggregate per-job analysis reports into a release or PR summary JSON file.

Shared across components (MicroShift, LVMS, etc.) via symlinks in each
plugin's scripts/ directory.

Usage:
    aggregate.py --release 4.22 [--workdir DIR]
    aggregate.py --prs [--workdir DIR]

Output files (under ${WORKDIR}/jobs/):
    jobs/release-<version>-summary.json
    jobs/prs-summary.json
"""

import json
import sys
import os
import re
import glob as glob_mod
from datetime import datetime, timezone

from classify import classify_breakdown
from parse import parse_structured_summary, group_by_signature


# ---------------------------------------------------------------------------
# Detected-job ground truth (deterministic overlay)
#
# The prepare phase's jobs/release-<v>-jobs.json and jobs/prs-jobs.json list
# every detected failed job.  Aggregation starts from that list and joins the
# parsed analyses onto it; detected jobs with no parsed analysis become
# explicit placeholders in a separate missing_analysis array, so a job can
# never silently vanish between detection and the HTML report.
# ---------------------------------------------------------------------------

def _detected_job(workdir, entry):
    build_id = entry.get("build_id", "")
    finished = ""
    if build_id:
        evidence_path = os.path.join(workdir, "evidence", f"evidence-{build_id}.json")
        try:
            with open(evidence_path) as f:
                finished = (json.load(f).get("finished") or "")[:10]
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "job_name": entry.get("job", ""),
        "job_url": entry.get("url", ""),
        "build_id": build_id,
        "download_error": entry.get("download_error", ""),
        "finished": finished,
    }


def load_detected_release_jobs(workdir, release):
    """Read jobs/release-<release>-jobs.json into detected-job dicts.

    Returns None when the file is missing or unreadable (distinct from []),
    so callers can fall back to legacy file-glob behavior.
    """
    path = os.path.join(workdir, "jobs", f"release-{release}-jobs.json")
    try:
        with open(path) as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(entries, list):
        return None
    jobs = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Release job lists only contain failures; tolerate a status field.
        status = entry.get("status", "")
        if status and status.upper() != "FAILURE":
            continue
        jobs.append(_detected_job(workdir, entry))
    return jobs


def load_detected_pr_jobs(workdir):
    """Read jobs/prs-jobs.json into {pr_number: [detected-job dicts]}.

    prs-jobs.json includes SUCCESS/pending jobs of PRs that had at least one
    failure; only FAILURE entries count as detected (same filter as
    plan-analysis.py).  Returns None when the file is missing or unreadable.
    """
    path = os.path.join(workdir, "jobs", "prs-jobs.json")
    try:
        with open(path) as f:
            entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(entries, list):
        return None
    prs = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("status", "").upper() != "FAILURE":
            continue
        pr_number = entry.get("pr_number") or 0
        prs.setdefault(pr_number, []).append(_detected_job(workdir, entry))
    return prs


def _file_build_id(filepath):
    """Extract the build id from a per-job report filename.

    Both shapes end in -<build_id>.txt:
    release-<v>-job-<i>-<build_id>.txt and prs-job-<i>-pr<N>-<build_id>.txt.
    """
    m = re.search(r"-(\d+)\.txt$", os.path.basename(filepath))
    return m.group(1) if m else ""


def _placeholder(job):
    """Missing-analysis entry for a detected job with no parsed report.

    Deliberately carries no fingerprint/severity/stack_layer: placeholders
    live outside issues[] and must never look like analyzed failures to
    downstream consumers.
    """
    if job.get("download_error"):
        reason = "artifact download failed"
    elif not job.get("build_id"):
        reason = "no build id"
    else:
        reason = "analysis report missing or unparseable"
    return {
        "job_name": job.get("job_name", ""),
        "job_url": job.get("job_url", ""),
        "build_id": job.get("build_id", ""),
        "finished": job.get("finished", ""),
        "reason": reason,
    }


def _missing_placeholders(detected, parsed):
    """Placeholders for detected jobs not covered by any parsed entry.

    Join primarily on build_id (attached to parsed entries from their
    filename), with job_url as a defensive fallback.
    """
    covered_ids = {e.get("build_id") for e in parsed if e.get("build_id")}
    covered_urls = {e.get("job_url") for e in parsed if e.get("job_url")}
    missing = []
    for job in detected:
        if job.get("build_id") and job["build_id"] in covered_ids:
            continue
        if job.get("job_url") and job["job_url"] in covered_urls:
            continue
        missing.append(_placeholder(job))
    return missing


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

def build_release_json(release, jobs, timestamp, detected=None):
    """Build the release summary as a dict (ready for json.dump).

    When the detected-job list is available, total_failed is the detected
    count (not the parsed-entry count, which can under- and over-count) and
    detected jobs with no parsed analysis are listed in missing_analysis.
    """
    issues, breakdown = _build_issues_from_jobs(jobs)
    missing = _missing_placeholders(detected, jobs) if detected is not None else []

    return {
        "release": release,
        "total_failed": len(detected) if detected is not None else len(jobs),
        "date": timestamp.strftime("%Y-%m-%d"),
        "breakdown": breakdown,
        "issues": issues,
        "missing_analysis": missing,
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
            "root_cause": rep.get("root_cause", ""),
            "next_steps": rep.get("remediation", ""),
            "confidence": rep.get("confidence", ""),
            "causal_chain": rep.get("causal_chain", []),
            "analysis_gaps": rep.get("analysis_gaps", []),
            "scenarios": sorted({s for j in group for s in j.get("scenarios", [])}),
            "affected_jobs": [
                {"name": j["job_name"], "date": j["finished"], "url": j["job_url"],
                 "build_id": j.get("build_id", "")}
                for j in group
            ],
        })

    return issues, breakdown


def build_pr_json(pr_jobs, timestamp, detected_prs=None):
    """Build the PR summary as a dict (ready for json.dump).

    pr_jobs: dict mapping pr_number to list of parsed job dicts.
    detected_prs: dict mapping pr_number to detected-job dicts (ground
    truth), or None when prs-jobs.json is unavailable.  When available,
    per-PR failed counts come from the detected list and unanalyzed jobs
    are listed per PR in missing_analysis — including PRs where no report
    parsed at all.
    """
    pr_numbers = sorted(set(pr_jobs) | set(detected_prs or {}))

    total_failed = 0
    prs = []
    for pr_number in pr_numbers:
        jobs = pr_jobs.get(pr_number, [])
        detected = (detected_prs or {}).get(pr_number)
        missing = _missing_placeholders(detected, jobs) if detected is not None else []
        if not jobs and not missing:
            continue
        failed = len(detected) if detected is not None else len(jobs)
        total_failed += failed
        first = jobs[0] if jobs else {}
        issues, breakdown = _build_issues_from_jobs(jobs)
        prs.append({
            "number": pr_number,
            "title": first.get("pr_title", ""),
            "url": first.get("pr_url", ""),
            "failed": failed,
            "breakdown": breakdown,
            "issues": issues,
            "missing_analysis": missing,
        })

    return {
        "total_prs": len(pr_numbers) if detected_prs is not None else len(pr_jobs),
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
    pattern = os.path.join(workdir, "jobs", f"release-{release}-job-*.txt")
    return sorted(glob_mod.glob(pattern))


def find_pr_job_files(workdir):
    pattern = os.path.join(workdir, "jobs", "prs-job-*.txt")
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
        # A recorded collection error must keep rendering as such —
        # create-report.py falls back to release-<v>-error.txt only when
        # the summary file is absent, so do not write one.
        error_path = os.path.join(workdir, "jobs", f"release-{release}-error.txt")
        if os.path.exists(error_path):
            print(f"Collection error recorded for release {release}; "
                  f"not writing a summary", file=sys.stderr)
            sys.exit(0)

        detected = load_detected_release_jobs(workdir, release)
        files = find_release_job_files(workdir, release)
        if detected is None and not files:
            print(f"No job files found for release {release}", file=sys.stderr)
            sys.exit(1)

        print(f"Found {len(files)} job files for release {release}"
              + (f" ({len(detected)} detected failed jobs)" if detected is not None else ""),
              file=sys.stderr)
        jobs = []
        for filepath in files:
            summaries = parse_structured_summary(filepath)
            if not summaries:
                print(f"  WARNING: no STRUCTURED SUMMARY in {os.path.basename(filepath)}", file=sys.stderr)
                continue
            build_id = _file_build_id(filepath)
            for summary in summaries:
                summary["build_id"] = build_id
            jobs.extend(summaries)

        if detected is None and not jobs:
            print("No valid job reports found", file=sys.stderr)
            sys.exit(1)

        result = build_release_json(release, jobs, timestamp, detected=detected)
        jobs_dir = os.path.join(workdir, "jobs")
        os.makedirs(jobs_dir, exist_ok=True)
        output_path = os.path.join(jobs_dir, f"release-{release}-summary.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Written: {output_path}", file=sys.stderr)
        print(json.dumps(result, indent=2))

    elif mode == "prs":
        detected_prs = load_detected_pr_jobs(workdir)
        files = find_pr_job_files(workdir)
        if not files:
            print("No PR job files found", file=sys.stderr)
        else:
            print(f"Found {len(files)} PR job files", file=sys.stderr)
        pr_jobs = {}
        for filepath in files:
            summaries = parse_structured_summary(filepath)
            if not summaries:
                print(f"  WARNING: no STRUCTURED SUMMARY in {os.path.basename(filepath)}", file=sys.stderr)
                continue
            build_id = _file_build_id(filepath)
            for summary in summaries:
                summary["pr_title"] = ""
                summary["pr_url"] = ""
                summary["build_id"] = build_id

            m = re.search(r"-pr(\d+)-", os.path.basename(filepath))
            pr_number = int(m.group(1)) if m else 0
            pr_jobs.setdefault(pr_number, []).extend(summaries)

        result = build_pr_json(pr_jobs, timestamp, detected_prs)

        jobs_dir = os.path.join(workdir, "jobs")
        os.makedirs(jobs_dir, exist_ok=True)
        output_path = os.path.join(jobs_dir, "prs-summary.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Written: {output_path}", file=sys.stderr)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
