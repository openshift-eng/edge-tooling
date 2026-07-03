#!/usr/bin/env python3
"""
Deterministic analysis planning for CI doctor workflows.

Shared across components (MicroShift, LVMS, etc.) via symlinks in each
plugin's scripts/ directory.

plan mode
    Groups failed jobs by the deterministic failure fingerprint from their
    evidence packs.  Pure-infrastructure and no-failure groups get template
    reports written directly (no LLM).  Every other group gets a fully
    rendered agent prompt file.  Writes <workdir>/analysis-plan.json and
    prints a compact JSON summary for the orchestrator.

fanout mode
    After agents have written (and validation has passed) the per-group
    reports, explodes each group report into the per-job report files that
    aggregate.py, search-bugs.py and create-report.py consume, patching
    job-specific fields and injecting the fingerprint key.

Usage:
    plan-analysis.py plan   --workdir DIR --template FILE
    plan-analysis.py fanout --workdir DIR
"""

import glob as glob_mod
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _normalize_release(value, job_name=""):
    """Map 'release-4.22' / '4.22' / 'main' → '4.22' / 'main'; else ''."""
    for candidate in (value or "", ""):
        m = re.match(r"^(?:release-)?(\d+\.\d+|main)$", candidate)
        if m:
            return m.group(1)
    m = re.search(r"release-(\d+\.\d+)", job_name)
    if m:
        return m.group(1)
    if "main" in job_name:
        return "main"
    return ""


def _load_jobs(workdir):
    """Load all failed jobs from the prepare phase's JSON files.

    Returns a list of job dicts with source bookkeeping attached:
    release (source release or '' for PRs), pr_number (or None), and
    index (1-based ordinal within its source, used for fan-out filenames).
    """
    jobs_dir = os.path.join(workdir, "jobs")
    jobs = []

    for jf in sorted(glob_mod.glob(os.path.join(jobs_dir, "release-*-jobs.json"))):
        m = re.match(r"release-(.+)-jobs\.json$", os.path.basename(jf))
        release = m.group(1) if m else ""
        entries = _read_json(jf) or []
        for i, entry in enumerate(entries, 1):
            if not isinstance(entry, dict) or not entry.get("build_id"):
                continue
            jobs.append({
                "job_name": entry.get("job", ""),
                "job_url": entry.get("url", ""),
                "build_id": entry["build_id"],
                "artifacts_dir": entry.get("artifacts_dir", ""),
                "release": release,
                "pr_number": None,
                "index": i,
            })

    pr_entries = _read_json(os.path.join(jobs_dir, "prs-jobs.json")) or []
    pr_index = 0
    for entry in pr_entries:
        if not isinstance(entry, dict) or not entry.get("build_id"):
            continue
        if entry.get("status", "").upper() != "FAILURE":
            continue
        pr_index += 1
        jobs.append({
            "job_name": entry.get("job", ""),
            "job_url": entry.get("url", ""),
            "build_id": entry["build_id"],
            "artifacts_dir": entry.get("artifacts_dir", ""),
            "release": "",
            "pr_number": entry.get("pr_number"),
            "index": pr_index,
        })

    return jobs


def _evidence_path(workdir, build_id):
    return os.path.join(workdir, "evidence", f"evidence-{build_id}.json")


# ---------------------------------------------------------------------------
# plan: grouping
# ---------------------------------------------------------------------------

def _classify_group(evidence):
    """Decide whether a group needs an agent.

    Returns 'infra', 'no-failure', or 'analysis'.  Deterministic verdicts
    are only issued when there is NO test-level evidence at all — any
    scenario/test/journal signal sends the group to an agent.
    """
    if evidence is None:
        return "analysis"

    fp = evidence.get("fingerprint", {}).get("inputs", {})
    has_test_evidence = (
        fp.get("failing_tests")
        or fp.get("phase_failures")
        or fp.get("journal_categories")
        or fp.get("timeout_cascade")
        or fp.get("greenboot_failure")
    )
    if has_test_evidence:
        return "analysis"

    if evidence.get("infrastructure_indicators", {}).get("is_infra_failure"):
        return "infra"

    if not evidence.get("failed_step", {}).get("name") and not fp.get("build_error"):
        return "no-failure"

    return "analysis"


def _group_jobs(workdir, jobs):
    """Group jobs by fingerprint key.  Jobs without evidence get singleton
    groups so the agent can fall back to raw artifacts."""
    groups = {}
    for job in jobs:
        ep = _evidence_path(workdir, job["build_id"])
        evidence = _read_json(ep)
        job["evidence_pack"] = ep
        job["finished"] = (evidence or {}).get("finished", "")[:10]
        if evidence and evidence.get("fingerprint", {}).get("key"):
            key = evidence["fingerprint"]["key"]
        else:
            key = f"noevidence-{job['build_id']}"
        group = groups.setdefault(key, {"key": key, "jobs": [], "evidence": None})
        group["jobs"].append(job)
        if group["evidence"] is None and evidence is not None:
            group["evidence"] = evidence
    return sorted(groups.values(), key=lambda g: (-len(g["jobs"]), g["key"]))


# ---------------------------------------------------------------------------
# plan: deterministic reports
# ---------------------------------------------------------------------------

def _job_lines(jobs):
    lines = []
    for job in jobs:
        label = job["job_name"]
        if job.get("pr_number"):
            label += f" (PR #{job['pr_number']})"
        lines.append(f"- {label} — {job['job_url']}")
        lines.append(f"  evidence pack: {job['evidence_pack']}")
        lines.append(f"  artifacts: {job['artifacts_dir']}")
    return "\n".join(lines)


def _deterministic_entry(reason, group):
    """Build the structured-summary entry for a no-LLM group verdict."""
    rep = group["jobs"][0]
    evidence = group["evidence"] or {}
    failed_step = evidence.get("failed_step", {})
    matched = evidence.get("infrastructure_indicators", {}).get("matched_patterns", [])

    entry = {
        "severity": 1,
        "step_name": failed_step.get("name", ""),
        "job_url": rep["job_url"],
        "job_name": rep["job_name"],
        "release": rep["release"] or _normalize_release(
            evidence.get("release", ""), rep["job_name"]),
        "finished": rep["finished"],
        "fingerprint": f"{group['key']}#1",
        "confidence": "high",
        "analysis_gaps": [],
        "scenarios": [],
        "causal_chain": [],
    }

    if reason == "infra":
        labels = sorted({m["label"] for m in matched})
        first = matched[0] if matched else {}
        entry.update({
            "stack_layer": "AWS Infra" if any(l.startswith("aws_") for l in labels)
            else "External Infrastructure",
            "error_signature": "CI infrastructure failure: " + ", ".join(labels),
            "root_cause": "CI/cloud infrastructure failure before tests ran: " + ", ".join(labels),
            "raw_error": first.get("text", "")[:150],
            "infrastructure_failure": True,
            "remediation": "No product action; re-run the job and monitor CI infrastructure",
            "causal_chain": [
                {
                    "cause": f"infrastructure indicator '{m['label']}' matched",
                    "evidence": f"{m['file']}:{m.get('line', 1)}",
                    "quote": m.get("text", ""),
                }
                for m in matched[:3]
            ],
        })
    else:  # no-failure
        entry.update({
            "stack_layer": "test",
            "error_signature": "no failure evidence found in artifacts",
            "root_cause": "no failure indicators in artifacts; job may have self-healed",
            "raw_error": "",
            "infrastructure_failure": False,
            "remediation": "No action; verify job status in Prow if it reported failure",
            "confidence": "low",
            "analysis_gaps": [
                "no failed step, infra indicator, test failure, or build error was extracted"
            ],
        })

    return entry


def _write_deterministic_report(reason, group, report_file):
    entry = _deterministic_entry(reason, group)
    chain_text = "\n".join(
        f"  {i}. {link['cause']} — {link['evidence']}: \"{link['quote']}\""
        for i, link in enumerate(entry["causal_chain"], 1)
    ) or "  (none)"

    body = f"""Deterministic CI Doctor verdict (no LLM analysis needed)
Fingerprint group: {group['key']} ({len(group['jobs'])} job(s))

Jobs:
{_job_lines(group['jobs'])}

Error Severity: {entry['severity']}
Stack Layer: {entry['stack_layer']}
Step Name: {entry['step_name']}
Error: {entry['raw_error'] or entry['error_signature']}
Causal Chain:
{chain_text}
Confidence: {entry['confidence']}
Suggested Remediation: {entry['remediation']}

--- STRUCTURED SUMMARY ---
{json.dumps([entry], indent=2)}
--- END STRUCTURED SUMMARY ---
"""
    with open(report_file, "w") as f:
        f.write(body)


# ---------------------------------------------------------------------------
# plan: prompt rendering
# ---------------------------------------------------------------------------

def _render_prompt(template_text, group, report_file):
    text = template_text
    text = text.replace("{GROUP_JOBS}", _job_lines(group["jobs"]))
    text = text.replace("{OUTPUT_FILE}", report_file)
    return text


def cmd_plan(workdir, template):
    template_text = None
    if template:
        try:
            with open(template) as f:
                template_text = f.read()
        except OSError as e:
            print(f"Error: cannot read template: {e}", file=sys.stderr)
            return 1
    if not template_text:
        print("Error: --template is required for plan", file=sys.stderr)
        return 1

    jobs = _load_jobs(workdir)
    if not jobs:
        print("No failed jobs found — nothing to plan", file=sys.stderr)
        print(json.dumps({"workdir": workdir, "total_jobs": 0, "groups": []}))
        return 0

    groups = _group_jobs(workdir, jobs)
    prompts_dir = os.path.join(workdir, "prompts")
    jobs_dir = os.path.join(workdir, "jobs")
    os.makedirs(prompts_dir, exist_ok=True)
    os.makedirs(jobs_dir, exist_ok=True)

    plan = []
    for group in groups:
        report_file = os.path.join(jobs_dir, f"analysis-group-{group['key']}.txt")
        reason = _classify_group(group["evidence"])
        entry = {
            "key": group["key"],
            "reason": reason,
            "deterministic": reason != "analysis",
            "report_file": report_file,
            "jobs": [
                {k: v for k, v in job.items()}
                for job in group["jobs"]
            ],
        }
        if reason == "analysis":
            prompt_file = os.path.join(prompts_dir, f"group-{group['key']}.md")
            with open(prompt_file, "w") as f:
                f.write(_render_prompt(template_text, group, report_file))
            entry["prompt_file"] = prompt_file
        else:
            _write_deterministic_report(reason, group, report_file)
        plan.append(entry)

    plan_path = os.path.join(workdir, "analysis-plan.json")
    with open(plan_path, "w") as f:
        json.dump(plan, f, indent=2)

    agent_groups = [p for p in plan if not p["deterministic"]]
    summary = {
        "workdir": workdir,
        "plan_file": plan_path,
        "total_jobs": len(jobs),
        "total_groups": len(plan),
        "deterministic_groups": len(plan) - len(agent_groups),
        "agent_groups": [
            {
                "key": p["key"],
                "jobs": len(p["jobs"]),
                "prompt_file": p["prompt_file"],
                "report_file": p["report_file"],
            }
            for p in agent_groups
        ],
    }
    print(
        f"Planned {len(plan)} groups for {len(jobs)} jobs: "
        f"{len(agent_groups)} need agents, "
        f"{len(plan) - len(agent_groups)} resolved deterministically",
        file=sys.stderr,
    )
    print(json.dumps(summary, indent=2))
    return 0


# ---------------------------------------------------------------------------
# fanout
# ---------------------------------------------------------------------------

_SUMMARY_RE = re.compile(
    r"--- STRUCTURED SUMMARY ---\n(.+?)(?:\n--- END STRUCTURED SUMMARY ---|\Z)",
    re.DOTALL,
)


def _split_report(content):
    """Split a report into (prose, entries).  entries is None if unparseable."""
    m = _SUMMARY_RE.search(content)
    if not m:
        return content, None
    prose = content[:m.start()].rstrip("\n")
    json_text = m.group(1).replace("\t", "\\t").replace("\r", "\\r")
    json_text = re.sub(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]", "", json_text)
    try:
        entries = json.loads(json_text)
    except json.JSONDecodeError:
        return prose, None
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return prose, None
    return prose, entries


def _member_filename(job):
    if job.get("pr_number"):
        return f"prs-job-{job['index']}-pr{job['pr_number']}-{job['build_id']}.txt"
    return f"release-{job['release']}-job-{job['index']}-{job['build_id']}.txt"


def _patch_entries(entries, job, key, workdir):
    evidence = _read_json(_evidence_path(workdir, job["build_id"])) or {}
    release = job["release"] or _normalize_release(
        evidence.get("release", ""), job["job_name"])
    patched = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            continue
        e = dict(entry)
        e["job_name"] = job["job_name"]
        e["job_url"] = job["job_url"]
        e["release"] = release
        e["finished"] = job.get("finished", "") or e.get("finished", "")
        # Independent failures within one group must stay separate issues
        # downstream, so the grouping key is fingerprint + entry ordinal
        # (the entries array is identical across the group's members).
        e["fingerprint"] = f"{key}#{i}"
        patched.append(e)
    return patched


def cmd_fanout(workdir):
    plan = _read_json(os.path.join(workdir, "analysis-plan.json"))
    if not isinstance(plan, list):
        print(f"Error: no analysis-plan.json in {workdir} — run plan first", file=sys.stderr)
        return 1

    jobs_dir = os.path.join(workdir, "jobs")
    written = 0
    missing = []
    unparseable = []

    for group in plan:
        report_file = group.get("report_file", "")
        if not os.path.isfile(report_file):
            missing.append(group["key"])
            continue
        with open(report_file, errors="replace") as f:
            content = f.read()
        prose, entries = _split_report(content)
        if entries is None:
            unparseable.append(group["key"])
            continue

        note = (
            f"NOTE: analyzed as fingerprint group {group['key']} "
            f"covering {len(group['jobs'])} job(s); shared analysis fanned out per job.\n\n"
        )
        for job in group["jobs"]:
            patched = _patch_entries(entries, job, group["key"], workdir)
            target = os.path.join(jobs_dir, _member_filename(job))
            with open(target, "w") as f:
                f.write(note + prose + "\n\n--- STRUCTURED SUMMARY ---\n")
                json.dump(patched, f, indent=2)
                f.write("\n--- END STRUCTURED SUMMARY ---\n")
            written += 1

    result = {
        "written": written,
        "missing_reports": missing,
        "unparseable_reports": unparseable,
    }
    print(
        f"Fanned out {written} per-job reports"
        + (f"; MISSING group reports: {', '.join(missing)}" if missing else "")
        + (f"; UNPARSEABLE: {', '.join(unparseable)}" if unparseable else ""),
        file=sys.stderr,
    )
    print(json.dumps(result, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("plan", "fanout"):
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    mode = sys.argv[1]
    workdir = None
    template = None
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--workdir":
            workdir = args[i + 1]; i += 2
        elif args[i] == "--template":
            template = args[i + 1]; i += 2
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(2)

    if not workdir or not os.path.isdir(workdir):
        print(f"Error: --workdir missing or not a directory: {workdir}", file=sys.stderr)
        sys.exit(2)
    workdir = os.path.abspath(workdir)

    if mode == "plan":
        sys.exit(cmd_plan(workdir, template))
    sys.exit(cmd_fanout(workdir))


if __name__ == "__main__":
    main()
