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

import glob as glob_mod
import json
import os
import re
import sys
from datetime import datetime, timezone

from classify import classify_breakdown
from parse import group_by_signature, issue_title, parse_structured_summary


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
    """Group jobs by causal identity and return (issues list, breakdown dict).

    Shared by both release and PR builders.
    """
    groups = group_by_signature(jobs)
    groups.sort(key=lambda g: (-max(j["severity"] for j in g), -len(g), issue_title(g[0])))

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
            "title": issue_title(rep),
            "job_count": len(group),
            "severity": classify_severity(group),
            "failure_type": failure_type,
            "root_cause": rep.get("root_cause", ""),
            "cause_identity": rep.get("cause_identity", ""),
            "failure_signals": sorted({
                j.get("failure_signal", "") or j.get("raw_error", "")
                for j in group
                if j.get("failure_signal", "") or j.get("raw_error", "")
            }),
            "impacts": sorted({impact for j in group for impact in j.get("impacts", [])}),
            "trigger_context": sorted({
                context for j in group for context in j.get("trigger_context", [])
            }),
            "next_steps": rep.get("remediation", ""),
            "confidence": rep.get("confidence", ""),
            "causal_chain": rep.get("causal_chain", []),
            "analysis_gaps": rep.get("analysis_gaps", []),
            "scenarios": sorted({s for j in group for s in j.get("scenarios", [])}),
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
    pattern = os.path.join(workdir, "jobs", f"release-{release}-job-*.json")
    return sorted(glob_mod.glob(pattern))


def find_pr_job_files(workdir):
    pattern = os.path.join(workdir, "jobs", "prs-job-*.json")
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
            summaries = parse_structured_summary(filepath)
            if not summaries:
                print(f"  WARNING: no valid JSON in {os.path.basename(filepath)}", file=sys.stderr)
                continue
            jobs.extend(summaries)

        if not jobs:
            print("No valid job reports found", file=sys.stderr)
            sys.exit(1)

        result = build_release_json(release, jobs, timestamp)
        jobs_dir = os.path.join(workdir, "jobs")
        os.makedirs(jobs_dir, exist_ok=True)
        output_path = os.path.join(jobs_dir, f"release-{release}-summary.json")
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
                summaries = parse_structured_summary(filepath)
                if not summaries:
                    print(f"  WARNING: no valid JSON in {os.path.basename(filepath)}", file=sys.stderr)
                    continue
                for summary in summaries:
                    summary["pr_title"] = ""
                    summary["pr_url"] = ""

                m = re.search(r"-pr(\d+)-", os.path.basename(filepath))
                pr_number = int(m.group(1)) if m else 0
                pr_jobs.setdefault(pr_number, []).extend(summaries)

            result = build_pr_json(pr_jobs, timestamp)

        jobs_dir = os.path.join(workdir, "jobs")
        os.makedirs(jobs_dir, exist_ok=True)
        output_path = os.path.join(jobs_dir, "prs-summary.json")
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Written: {output_path}", file=sys.stderr)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
