#!/usr/bin/env python3
"""
Deterministic evidence extraction for CI job artifacts.

Extracts structured evidence from Prow job artifacts into a JSON "evidence
pack" that gives the LLM agent a head-start on analysis.  The agent still
has access to raw artifacts for deeper investigation.

Shared across components (MicroShift, LVMS, etc.) via symlinks in each
plugin's scripts/ directory.

Usage:
    extract-evidence.py --artifacts-dir <DIR> --workdir <WORKDIR>
    extract-evidence.py --batch --workdir <WORKDIR>
"""

import glob as glob_mod
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EVIDENCE_VERSION = 1


# ---------------------------------------------------------------------------
# CONFIGURABLE PATTERN LISTS
# Add new patterns here.  Each is (label, regex).
# ---------------------------------------------------------------------------

JOURNAL_PATTERNS = [
    ("oom_kills",           r"oom-kill|oom_kill|Out of memory|invoked oom-killer"),
    ("panics",              r"panic|BUG:|RIP:|kernel BUG"),
    ("segfaults",           r"segfault|SIGSEGV"),
    ("container_restarts",  r"Created container"),
    ("greenboot_verdicts",  r"greenboot.*Script.*SUCCESS|greenboot.*Script.*FAILURE"),
    ("etcd_pressure",       r"apply request took too long"),
    ("probe_failures",      r"Probe failed|probe failed"),
    ("leader_election",     r"leader election lost|became leader|leaderelection"),
    ("ovn_binding",         r"timed out waiting for OVS port binding"),
    ("service_failures",    r"microshift\.service.*Failed|microshift\.service.*canceled"),
    ("x509_errors",         r"x509: certificate"),
    ("disk_pressure",       r"eviction manager|DiskPressure|nodefs.available"),
    ("access_denied",       r"denied|forbidden|DENY"),
    ("fatal_errors",        r"FATAL|fatal error"),
    ("apiserver_issues",    r"etcdserver: request timed out|watch chan error|TLS handshake error"),
]

BOOT_AND_RUN_PATTERNS = [
    ("failures",        r"\bFAIL\b|FAILED"),
    ("errors",          r"\berror\b|\bError\b"),
    ("cancels",         r"canceled|cancelled"),
    ("timeouts",        r"timeout|timed out|Timing out"),
    ("signals",         r"Execution terminated by signal|Test execution stopped"),
    ("service_issues",  r"Job for .* canceled|Job for .* failed"),
]

INFRA_INDICATORS = [
    ("scheduling_failure",  r"pod pending for more than|pod has not been scheduled"),
    ("aws_credential",      r"InvalidClientTokenId|security token.*invalid"),
    ("aws_quota",           r"RequestLimitExceeded|InstanceLimitExceeded"),
    ("aws_provision",       r"ProvisioningFailed|CREATE_FAILED"),
    ("ci_cluster_capacity", r"nodes are available:.*didn't match Pod's node affinity"),
    ("prow_timeout",        r"Process did not finish before.*timeout"),
    ("pod_pending_reason",  r"pod_pending|importing_release:pod_pending"),
]

BUILD_LOG_ERROR_PATTERNS = [
    ("fatal_errors",    r"^ERRO|^ERROR|^Fatal|^FATAL|panic:"),
    ("step_failures",   r"Container.*exited with code [^0]|step.*failed"),
    ("exceptions",      r"Exception:|Traceback \(most recent"),
]

# Steps whose presence in the artifact tree signals a specific job type.
JOB_TYPE_STEP_MARKERS = {
    "openshift-microshift-infra-iso-build": "build",
    "openshift-microshift-rebase": "rebase",
    "openshift-microshift-manage-versions-releases": "config",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_lines(path, limit=None):
    try:
        with open(path, errors="replace") as f:
            if limit:
                return [f.readline() for _ in range(limit)]
            return f.readlines()
    except OSError:
        return []


def _grep_file(path, pattern, max_matches=200):
    """Run grep via subprocess — fast on large files (100MB+ journals)."""
    try:
        result = subprocess.run(
            ["grep", "-n", "-E", pattern, path],
            capture_output=True, text=True, timeout=30,
        )
        lines = result.stdout.strip().split("\n") if result.stdout.strip() else []
        return lines[:max_matches]
    except (subprocess.TimeoutExpired, OSError):
        return []


def _parse_grep_line(line):
    """Parse 'linenum:text' from grep -n output."""
    m = re.match(r"^(\d+):(.*)", line)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 0, line.strip()


def _find_glob(base, pattern):
    return sorted(glob_mod.glob(os.path.join(base, pattern)))


_JOURNAL_TS_RE = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})")


def _parse_journal_timestamp(text):
    m = _JOURNAL_TS_RE.match(text)
    return m.group(1) if m else ""


def _timestamp_from_epoch(epoch):
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError, OSError):
        return ""


def _date_from_epoch(epoch):
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return ""


# ---------------------------------------------------------------------------
# Phase A: Metadata
# ---------------------------------------------------------------------------

def extract_metadata(artifacts_dir):
    build_id = os.path.basename(artifacts_dir.rstrip("/"))

    finished = _read_json(os.path.join(artifacts_dir, "finished.json")) or {}
    started = _read_json(os.path.join(artifacts_dir, "started.json")) or {}
    prowjob = _read_json(os.path.join(artifacts_dir, "prowjob.json")) or {}

    ts_finished = finished.get("timestamp", 0)
    ts_started = started.get("timestamp", 0)
    duration_minutes = round((ts_finished - ts_started) / 60) if ts_finished and ts_started else 0

    release = finished.get("revision", "")
    if not release:
        repos = (finished.get("metadata") or {}).get("repos") or {}
        for repo_ref in repos.values():
            if isinstance(repo_ref, str):
                release = repo_ref
                break

    job_name = prowjob.get("spec", {}).get("job", "")
    job_url = ""
    refs = prowjob.get("spec", {}).get("refs") or prowjob.get("spec", {}).get("extra_refs", [{}])[0] if prowjob else {}
    if isinstance(refs, dict):
        org = refs.get("org", "")
        repo = refs.get("repo", "")
        if job_name and build_id:
            pulls = refs.get("pulls", [])
            if pulls:
                pr_num = pulls[0].get("number", "")
                job_url = f"https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/{org}_{repo}/{pr_num}/{job_name}/{build_id}"
            else:
                job_url = f"https://prow.ci.openshift.org/view/gs/test-platform-results/logs/{job_name}/{build_id}"

    build_cluster = prowjob.get("spec", {}).get("cluster", "")

    return {
        "build_id": build_id,
        "job_name": job_name,
        "job_url": job_url,
        "release": release,
        "started": _timestamp_from_epoch(ts_started),
        "finished": _timestamp_from_epoch(ts_finished),
        "finished_date": _date_from_epoch(ts_finished),
        "finished_epoch": ts_finished,
        "duration_minutes": duration_minutes,
        "result": finished.get("result", "UNKNOWN"),
        "build_cluster": build_cluster,
    }


# ---------------------------------------------------------------------------
# Phase B: Job type classification
# ---------------------------------------------------------------------------

def classify_job_type(artifacts_dir):
    inner = os.path.join(artifacts_dir, "artifacts")
    if not os.path.isdir(inner):
        return "other", None, None

    test_dirs = [d for d in os.listdir(inner) if os.path.isdir(os.path.join(inner, d))
                 and d not in ("build-resources", "release")]
    if not test_dirs:
        return "other", None, None

    # Check each test variant directory and its step subdirectories.
    # The failed step determines job type.  We scan all test dirs (not just
    # the first) because some jobs have multiple test variants.
    best_type = "other"
    best_test = test_dirs[0]
    best_step = None

    for test_name in test_dirs:
        test_dir = os.path.join(inner, test_name)
        for step_dir_name in sorted(os.listdir(test_dir)):
            step_path = os.path.join(test_dir, step_dir_name)
            if not os.path.isdir(step_path):
                continue

            scenario_info = os.path.join(step_path, "artifacts", "scenario-info")
            if os.path.isdir(scenario_info):
                return "scenario-e2e", test_name, step_dir_name

            junit_glob = _find_glob(os.path.join(step_path, "artifacts"), "junit/junit_e2e__*.xml")
            if junit_glob:
                return "conformance", test_name, step_dir_name

            for marker_step, jtype in JOB_TYPE_STEP_MARKERS.items():
                if marker_step in step_dir_name:
                    best_type = jtype
                    best_test = test_name
                    best_step = step_dir_name

    return best_type, best_test, best_step


# ---------------------------------------------------------------------------
# Phase C: Failed step identification
# ---------------------------------------------------------------------------

def _find_failed_step_from_finished_json(artifacts_dir):
    """Find the step that failed by checking each step's finished.json.

    Each step directory under artifacts/<test-name>/<step>/ has a
    finished.json with {"passed": true/false}.  The step with passed=false
    is the one that actually failed (not the deprovision/teardown steps
    that run after the failure).
    """
    inner = os.path.join(artifacts_dir, "artifacts")
    if not os.path.isdir(inner):
        return None

    # Teardown/infra steps to deprioritize — they often show passed=false
    # because the test step failed first and the teardown is just cleanup.
    teardown_keywords = ("deprovision", "pmlogs", "sos-aws", "includes")

    for test_dir_name in os.listdir(inner):
        test_dir = os.path.join(inner, test_dir_name)
        if not os.path.isdir(test_dir) or test_dir_name in ("build-resources", "release"):
            continue
        candidates = []
        for step_name in os.listdir(test_dir):
            step_path = os.path.join(test_dir, step_name)
            if not os.path.isdir(step_path):
                continue
            fj = _read_json(os.path.join(step_path, "finished.json"))
            if fj and fj.get("passed") is False:
                is_teardown = any(kw in step_name for kw in teardown_keywords)
                candidates.append((step_name, is_teardown))

        if candidates:
            non_teardown = [c for c in candidates if not c[1]]
            if non_teardown:
                return non_teardown[0][0]
            return candidates[0][0]

    return None


def extract_failed_step(artifacts_dir):
    build_log = os.path.join(artifacts_dir, "build-log.txt")
    lines = _read_lines(build_log)

    step_diagram_url = ""
    anchor_error = None
    anchor_line_idx = -1

    for i, line in enumerate(lines):
        stripped = line.strip()

        if re.search(r"steps\.ci\.openshift\.org", stripped):
            url_m = re.search(r"(https://steps\.ci\.openshift\.org\S+)", stripped)
            if url_m:
                step_diagram_url = url_m.group(1)

        if anchor_error is None:
            if re.search(r"Container.*exited with code [^0]", stripped):
                anchor_error = {"text": stripped[:300], "file": "build-log.txt", "line": i + 1}
                anchor_line_idx = i
            elif re.search(r"Process did not finish before.*timeout", stripped):
                anchor_error = {"text": stripped[:300], "file": "build-log.txt", "line": i + 1}
                anchor_line_idx = i
            elif re.search(r"pod pending for more than", stripped):
                anchor_error = {"text": stripped[:300], "file": "build-log.txt", "line": i + 1}
                anchor_line_idx = i

    context_lines = []
    if anchor_line_idx >= 0:
        start = max(0, anchor_line_idx - 5)
        end = min(len(lines), anchor_line_idx + 6)
        context_lines = [l.rstrip() for l in lines[start:end]]

    # Primary: find the failed step from per-step finished.json (most reliable)
    failed_step_name = _find_failed_step_from_finished_json(artifacts_dir) or ""

    ci_operator_reason = ""
    ci_op_log = os.path.join(artifacts_dir, "artifacts", "ci-operator.log")
    for line in _read_lines(ci_op_log):
        m = re.search(r"Reporting job state '(\w+)' with reason '([^']+)'", line)
        if m:
            ci_operator_reason = m.group(2)
            break

    # Fallback: extract step name from ci-operator reason when no step
    # directory had finished.json (e.g. infra failure before any step ran)
    if not failed_step_name and ci_operator_reason:
        m = re.search(r"step_failed:(\w[\w-]*)", ci_operator_reason)
        if m:
            failed_step_name = m.group(1)
        elif "importing_release" in ci_operator_reason:
            failed_step_name = "[release:latest]"

    exit_code = -1
    if anchor_error:
        m = re.search(r"exited with code (\d+)", anchor_error["text"])
        if m:
            exit_code = int(m.group(1))

    return {
        "name": failed_step_name,
        "exit_code": exit_code,
        "step_diagram_url": step_diagram_url,
        "ci_operator_reason": ci_operator_reason,
        "anchor_error": anchor_error,
        "context_lines": context_lines,
    }


# ---------------------------------------------------------------------------
# Phase D: Infrastructure indicator scan
# ---------------------------------------------------------------------------

def scan_infra_indicators(artifacts_dir):
    files_to_scan = [
        os.path.join(artifacts_dir, "build-log.txt"),
        os.path.join(artifacts_dir, "artifacts", "ci-operator.log"),
    ]

    matched = []
    for fpath in files_to_scan:
        if not os.path.isfile(fpath):
            continue
        for label, pattern in INFRA_INDICATORS:
            hits = _grep_file(fpath, pattern, max_matches=3)
            if hits:
                _, text = _parse_grep_line(hits[0])
                matched.append({"label": label, "file": os.path.basename(fpath), "text": text[:200]})

    return {
        "is_infra_failure": len(matched) > 0,
        "matched_patterns": matched,
    }


# ---------------------------------------------------------------------------
# Phase E: Scenario extraction
# ---------------------------------------------------------------------------

def _parse_junit(path):
    """Parse junit.xml → list of {name, classname, status, message, time_s}, counts, suite_timestamp."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return [], 0, 0, ""

    root = tree.getroot()
    suite_timestamp = root.get("timestamp", "")
    tests = int(root.get("tests", 0))
    failures = int(root.get("failures", 0))

    results = []
    for tc in root.iter("testcase"):
        entry = {
            "name": tc.get("name", ""),
            "classname": tc.get("classname", ""),
            "time_s": tc.get("time", ""),
        }
        fail = tc.find("failure")
        if fail is not None:
            entry["status"] = "FAILED"
            entry["message"] = (fail.get("message") or fail.text or "")[:300]
        else:
            entry["status"] = "PASSED"
            entry["message"] = ""
        results.append(entry)

    return results, tests, failures, suite_timestamp


def _extract_rf_failures(path):
    hits = _grep_file(path, r"\| FAIL \|", max_matches=50)
    results = []
    for h in hits:
        line_num, text = _parse_grep_line(h)
        results.append({"line": line_num, "text": text[:300]})
    return results


def _extract_boot_and_run_alerts(path):
    alerts = []
    for label, pattern in BOOT_AND_RUN_PATTERNS:
        hits = _grep_file(path, pattern, max_matches=20)
        for h in hits:
            line_num, text = _parse_grep_line(h)
            if not text or re.match(r"^\d+:\s*#", text):
                continue
            alerts.append({"pattern": label, "line": line_num, "text": text[:300]})
    return alerts


def _detect_timeout_cascade(path):
    content_check = _grep_file(path, r"Execution terminated by signal", max_matches=1)
    if content_check:
        stopped = _grep_file(path, r"Test execution stopped", max_matches=1)
        return bool(stopped)
    return False


def _extract_journal_alerts(journal_path):
    alerts = {}
    for label, pattern in JOURNAL_PATTERNS:
        hits = _grep_file(journal_path, pattern, max_matches=50)
        entries = []
        for h in hits:
            line_num, text = _parse_grep_line(h)
            entries.append({
                "line": line_num,
                "text": text[:300],
                "timestamp": _parse_journal_timestamp(text),
            })
        alerts[label] = entries
    return alerts


_CREATED_CONTAINER_RE = re.compile(r"Created container [0-9a-f]+:\s*(\S+/\S+)/\S+")


def _group_container_restarts(entries):
    """Group 'Created container' journal entries by pod, flag pods with >1 creation."""
    pod_counts = {}
    for entry in entries:
        m = _CREATED_CONTAINER_RE.search(entry.get("text", ""))
        if m:
            pod = m.group(1)
            pod_counts[pod] = pod_counts.get(pod, 0) + 1
    return sorted(
        [{"pod": pod, "count": count} for pod, count in pod_counts.items() if count > 1],
        key=lambda x: -x["count"],
    )


def _find_sosreports(scenario_dir):
    tarballs = _find_glob(os.path.join(scenario_dir, "vms", "*", "sos"), "sosreport-*.tar.xz")
    results = []
    for t in tarballs:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(t))
        capture_date = m.group(1) if m else ""
        results.append({"path": t, "capture_date": capture_date})
    return results


def extract_scenarios(artifacts_dir, test_name, step_name, workdir):
    if not test_name or not step_name:
        return []

    scenario_info_dir = os.path.join(
        artifacts_dir, "artifacts", test_name, step_name, "artifacts", "scenario-info"
    )
    if not os.path.isdir(scenario_info_dir):
        return []

    scenarios = []
    for scenario_name in sorted(os.listdir(scenario_info_dir)):
        sdir = os.path.join(scenario_info_dir, scenario_name)
        if not os.path.isdir(sdir):
            continue

        junit_path = os.path.join(sdir, "junit.xml")
        phase_junit_path = os.path.join(sdir, "phase_create-and-run", "junit.xml")
        rf_debug_path = os.path.join(sdir, "rf-debug.log")
        boot_run_path = os.path.join(sdir, "boot_and_run.log")
        journal_paths = _find_glob(os.path.join(sdir, "vms", "*", "sos"), "journal_*.log")

        has_junit = os.path.isfile(junit_path)
        has_rf_debug = os.path.isfile(rf_debug_path)
        has_journal = len(journal_paths) > 0
        has_boot_run = os.path.isfile(boot_run_path)
        sosreports = _find_sosreports(sdir)

        test_failures, test_count, failure_count, suite_timestamp = _parse_junit(junit_path) if has_junit else ([], 0, 0, "")
        test_failures = [t for t in test_failures if t["status"] == "FAILED"]

        infra_results, _, infra_fail_count, _ = _parse_junit(phase_junit_path) if os.path.isfile(phase_junit_path) else ([], 0, 0, "")
        infra_failures = [t["name"] for t in infra_results if t["status"] == "FAILED"]

        rf_failures = _extract_rf_failures(rf_debug_path) if has_rf_debug else []
        boot_alerts = _extract_boot_and_run_alerts(boot_run_path) if has_boot_run else []
        timeout_cascade = _detect_timeout_cascade(boot_run_path) if has_boot_run else False

        journal_alerts = {}
        for jp in journal_paths:
            alerts = _extract_journal_alerts(jp)
            for label, entries in alerts.items():
                journal_alerts.setdefault(label, []).extend(entries)

        container_restarts = _group_container_restarts(
            journal_alerts.get("container_restarts", [])
        )

        greenboot_status = None
        for entry in journal_alerts.get("greenboot_verdicts", []):
            if "SUCCESS" in entry["text"]:
                greenboot_status = "SUCCESS"
            elif "FAILURE" in entry["text"]:
                greenboot_status = "FAILURE"

        # Pre-extract sosreports when journal shows container restarts or crashes
        should_extract = (
            len(journal_alerts.get("container_restarts", [])) >= 2
            or len(journal_alerts.get("panics", [])) > 0
            or len(journal_alerts.get("oom_kills", [])) > 0
        )
        extracted_dirs = []
        if should_extract and sosreports:
            extract_script = os.path.join(SCRIPT_DIR, "extract-sosreport.sh")
            if os.path.isfile(extract_script):
                for sos in sosreports:
                    try:
                        result = subprocess.run(
                            ["bash", extract_script, sos["path"]],
                            capture_output=True, text=True, timeout=120,
                        )
                        extracted_path = result.stdout.strip().split("\n")[-1]
                        if extracted_path and os.path.isdir(extracted_path):
                            extracted_dirs.append(extracted_path)
                    except (subprocess.TimeoutExpired, OSError):
                        pass

        gaps = []
        if not has_journal:
            gaps.append("no journal")
        if not sosreports:
            gaps.append("no sosreport")
        if not has_rf_debug and has_junit and failure_count > 0:
            gaps.append("no rf-debug.log")

        scenarios.append({
            "name": scenario_name,
            "evidence_available": {
                "junit": has_junit,
                "rf_debug": has_rf_debug,
                "journal": has_journal,
                "boot_and_run": has_boot_run,
                "sosreport": len(sosreports) > 0,
            },
            "infra_phase": {
                "result": "FAILED" if infra_failures else "PASSED",
                "failures": infra_failures,
            },
            "junit_suite_timestamp": suite_timestamp,
            "test_count": test_count,
            "failure_count": failure_count,
            "test_failures": test_failures,
            "rf_failures": rf_failures,
            "boot_and_run_alerts": boot_alerts,
            "journal_alerts": journal_alerts,
            "container_restarts": container_restarts,
            "timeout_cascade": timeout_cascade,
            "greenboot_status": greenboot_status,
            "sosreport_paths": sosreports,
            "extracted_sosreport_dirs": extracted_dirs,
            "analysis_gaps": gaps,
        })

    return scenarios


# ---------------------------------------------------------------------------
# Phase F: Conformance extraction
# ---------------------------------------------------------------------------

def extract_conformance_failures(artifacts_dir, test_name, step_name):
    if not test_name or not step_name:
        return []

    step_dir = os.path.join(artifacts_dir, "artifacts", test_name, step_name)
    junit_files = _find_glob(os.path.join(step_dir, "artifacts"), "junit/junit_e2e__*.xml")

    failures = []
    for jf in junit_files:
        results, _, _, _ = _parse_junit(jf)
        for tc in results:
            if tc["status"] == "FAILED":
                failures.append({
                    "test_name": tc["name"],
                    "classname": tc["classname"],
                    "message": tc["message"],
                    "file": os.path.relpath(jf, artifacts_dir),
                })

    step_log = os.path.join(step_dir, "build-log.txt")
    if os.path.isfile(step_log):
        monitor_hits = _grep_file(step_log, r"MonitorTest|Suite run returned error", max_matches=10)
        for h in monitor_hits:
            line_num, text = _parse_grep_line(h)
            failures.append({
                "test_name": "",
                "classname": "build-log",
                "message": text[:300],
                "file": os.path.relpath(step_log, artifacts_dir),
                "line": line_num,
            })

    return failures


# ---------------------------------------------------------------------------
# Phase G: Build/config/rebase extraction
# ---------------------------------------------------------------------------

def extract_build_errors(artifacts_dir, test_name, step_name):
    if not test_name or not step_name:
        return _extract_errors_from_main_log(artifacts_dir)

    step_log = os.path.join(artifacts_dir, "artifacts", test_name, step_name, "build-log.txt")
    if not os.path.isfile(step_log):
        return _extract_errors_from_main_log(artifacts_dir)

    errors = []
    lines = _read_lines(step_log)
    for i, line in enumerate(lines):
        stripped = line.strip()
        for label, pattern in BUILD_LOG_ERROR_PATTERNS:
            if re.search(pattern, stripped):
                start = max(0, i - 3)
                end = min(len(lines), i + 4)
                context = [l.rstrip() for l in lines[start:end]]
                errors.append({
                    "pattern": label,
                    "line": i + 1,
                    "text": stripped[:300],
                    "file": os.path.relpath(step_log, artifacts_dir),
                    "context": context,
                })
                break
    return errors[:30]


def _extract_errors_from_main_log(artifacts_dir):
    main_log = os.path.join(artifacts_dir, "build-log.txt")
    if not os.path.isfile(main_log):
        return []

    errors = []
    for label, pattern in BUILD_LOG_ERROR_PATTERNS:
        hits = _grep_file(main_log, pattern, max_matches=10)
        for h in hits:
            line_num, text = _parse_grep_line(h)
            errors.append({
                "pattern": label,
                "line": line_num,
                "text": text[:300],
                "file": "build-log.txt",
            })
    return errors[:30]


# ---------------------------------------------------------------------------
# Phase H: Source context
# ---------------------------------------------------------------------------

def extract_source_context(workdir, release, finished_epoch):
    if not workdir or not finished_epoch:
        return {"available": False, "path": "", "recent_commits": [], "gap": "no workdir"}

    if release and release != "main":
        src_dir = os.path.join(workdir, "src", f"microshift-release-{release}")
        if not os.path.isdir(src_dir):
            src_dir = os.path.join(workdir, "src", "microshift")
    else:
        src_dir = os.path.join(workdir, "src", "microshift")

    if not os.path.isdir(src_dir):
        return {"available": False, "path": "", "recent_commits": [], "gap": "source checkout not available"}

    finished_dt = datetime.fromtimestamp(int(finished_epoch), tz=timezone.utc)
    since_dt = finished_dt - timedelta(days=30)
    since_str = since_dt.strftime("%Y-%m-%d")
    until_str = finished_dt.strftime("%Y-%m-%d")

    repo_log_script = os.path.join(SCRIPT_DIR, "repo-log.sh")
    if os.path.isfile(repo_log_script):
        try:
            result = subprocess.run(
                ["bash", repo_log_script, src_dir,
                 "--since", since_str, "--until", until_str, "--limit", "30"],
                capture_output=True, text=True, timeout=30,
            )
            commits = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split(None, 2)
                if len(parts) >= 3:
                    commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
                elif len(parts) == 2:
                    commits.append({"sha": parts[0], "date": parts[1], "subject": ""})
            return {"available": True, "path": src_dir, "recent_commits": commits}
        except (subprocess.TimeoutExpired, OSError):
            pass

    return {"available": True, "path": src_dir, "recent_commits": [], "gap": "repo-log.sh failed"}


# ---------------------------------------------------------------------------
# Phase I: PCP graphs
# ---------------------------------------------------------------------------

def find_pcp_graphs(workdir, build_id):
    graphs_dir = os.path.join(workdir, "graphs", build_id)
    if not os.path.isdir(graphs_dir):
        return []
    return sorted([os.path.basename(f) for f in _find_glob(graphs_dir, "*.png")])


# ---------------------------------------------------------------------------
# Phase J: Failure timeline
# ---------------------------------------------------------------------------

_FAILURE_JOURNAL_CATEGORIES = {
    "oom_kills", "panics", "segfaults", "service_failures", "fatal_errors",
    "etcd_pressure", "ovn_binding", "x509_errors", "disk_pressure",
    "apiserver_issues",
}


def _resolve_journal_ts(ts_str, year):
    """Convert 'Jun 30 03:58:39' + year → '2026-06-30T03:58:39Z'."""
    try:
        dt = datetime.strptime(f"{year} {ts_str}", "%Y %b %d %H:%M:%S")
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return ""


def _build_failure_timeline(scenarios, meta):
    started = meta.get("started", "")
    year = ""
    if started:
        try:
            year = started[:4]
        except (IndexError, TypeError):
            pass
    if not year:
        finished = meta.get("finished", "")
        if finished:
            year = finished[:4]
    if not year:
        return []

    timeline = []
    for scenario in scenarios:
        name = scenario.get("name", "")
        has_test_failures = scenario.get("failure_count", 0) > 0
        infra_failed = scenario.get("infra_phase", {}).get("result") == "FAILED"

        journal_alerts = scenario.get("journal_alerts", {})
        has_problem_alerts = any(
            journal_alerts.get(cat)
            for cat in _FAILURE_JOURNAL_CATEGORIES
        )

        if not has_test_failures and not infra_failed and not has_problem_alerts:
            continue

        earliest_ts = ""
        earliest_source = ""
        earliest_detail = ""

        for cat in _FAILURE_JOURNAL_CATEGORIES:
            for entry in journal_alerts.get(cat, []):
                raw_ts = entry.get("timestamp", "")
                if not raw_ts:
                    continue
                resolved = _resolve_journal_ts(raw_ts, year)
                if resolved and (not earliest_ts or resolved < earliest_ts):
                    earliest_ts = resolved
                    earliest_source = "journal"
                    earliest_detail = f"{cat}: {entry.get('text', '')[:80]}"

        suite_ts = scenario.get("junit_suite_timestamp", "")
        if suite_ts:
            normalized = suite_ts
            if len(normalized) >= 19 and "T" not in normalized:
                normalized = normalized[:10] + "T" + normalized[11:19] + "Z"
            elif not normalized.endswith("Z") and "+" not in normalized:
                normalized = normalized.rstrip() + "Z"
            if not earliest_ts or normalized < earliest_ts:
                earliest_ts = normalized
                earliest_source = "junit"
                detail_parts = [t.get("name", "") for t in scenario.get("test_failures", [])[:2]]
                earliest_detail = "; ".join(detail_parts)[:80] if detail_parts else "test suite"

        for alert in scenario.get("boot_and_run_alerts", []):
            raw_ts = _parse_journal_timestamp(alert.get("text", ""))
            if not raw_ts:
                continue
            resolved = _resolve_journal_ts(raw_ts, year)
            if resolved and (not earliest_ts or resolved < earliest_ts):
                earliest_ts = resolved
                earliest_source = "boot_and_run"
                earliest_detail = alert.get("text", "")[:80]

        if earliest_ts:
            timeline.append({
                "scenario": name,
                "earliest_failure": earliest_ts,
                "source": earliest_source,
                "detail": earliest_detail,
            })

    timeline.sort(key=lambda e: e["earliest_failure"])
    return timeline


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_evidence(artifacts_dir, workdir):
    artifacts_dir = os.path.abspath(artifacts_dir)
    workdir = os.path.abspath(workdir)

    meta = extract_metadata(artifacts_dir)
    job_type, test_name, step_name = classify_job_type(artifacts_dir)
    failed_step = extract_failed_step(artifacts_dir)
    infra = scan_infra_indicators(artifacts_dir)

    scenarios = []
    conformance_failures = []
    build_errors = []

    if job_type == "scenario-e2e":
        scenarios = extract_scenarios(artifacts_dir, test_name, step_name, workdir)
    elif job_type == "conformance":
        conformance_failures = extract_conformance_failures(artifacts_dir, test_name, step_name)
    elif job_type in ("build", "config", "rebase", "other"):
        build_errors = extract_build_errors(artifacts_dir, test_name, step_name)

    # When no step was identified from the test artifacts but we detected infra
    # failure patterns, still extract build errors from main log.
    if not failed_step["name"] and infra["is_infra_failure"]:
        build_errors = _extract_errors_from_main_log(artifacts_dir)

    failure_timeline = _build_failure_timeline(scenarios, meta)

    source = extract_source_context(workdir, meta["release"], meta["finished_epoch"])
    pcp_graphs = find_pcp_graphs(workdir, meta["build_id"])

    top_level_gaps = []
    if source.get("gap"):
        top_level_gaps.append(source["gap"])
    if not pcp_graphs:
        top_level_gaps.append("no PCP graphs")

    evidence = {
        "version": EVIDENCE_VERSION,
        **{k: v for k, v in meta.items() if k != "finished_epoch"},
        "artifacts_dir": artifacts_dir,
        "job_type": job_type,
        "failed_step": failed_step,
        "infrastructure_indicators": infra,
        "scenarios": scenarios,
        "failure_timeline": failure_timeline,
        "conformance_failures": conformance_failures,
        "build_errors": build_errors,
        "pcp_graphs": pcp_graphs,
        "source_checkout": {k: v for k, v in source.items() if k != "gap"},
        "analysis_gaps": top_level_gaps,
    }

    return evidence


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

def _rebase_artifacts_dir(artifacts_dir, workdir):
    """Rebase an artifacts_dir path onto the current workdir.

    Job JSON files record the artifacts_dir from the original machine (e.g.
    /tmp/microshift-ci-claude-workdir.260629/artifacts/BUILD_ID).  When the
    workdir has been moved or mounted elsewhere, we rebase the path.
    """
    build_id = os.path.basename(artifacts_dir.rstrip("/"))
    rebased = os.path.join(workdir, "artifacts", build_id)
    if os.path.isdir(rebased):
        return rebased
    if os.path.isdir(artifacts_dir):
        return artifacts_dir
    return None


def run_batch(workdir):
    workdir = os.path.abspath(workdir)
    jobs_dir = os.path.join(workdir, "jobs")
    evidence_dir = os.path.join(workdir, "evidence")
    os.makedirs(evidence_dir, exist_ok=True)

    job_files = sorted(
        _find_glob(jobs_dir, "release-*-jobs.json")
        + _find_glob(jobs_dir, "prs-jobs.json")
    )

    total = 0
    errors = 0
    skipped = 0

    for jf in job_files:
        jobs = _read_json(jf)
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            raw_dir = job.get("artifacts_dir", "")
            if not raw_dir:
                continue
            artifacts_dir = _rebase_artifacts_dir(raw_dir, workdir)
            if not artifacts_dir:
                skipped += 1
                continue
            build_id = job.get("build_id", os.path.basename(artifacts_dir))
            output_path = os.path.join(evidence_dir, f"evidence-{build_id}.json")
            try:
                evidence = extract_evidence(artifacts_dir, workdir)
                with open(output_path, "w") as f:
                    json.dump(evidence, f, indent=2)
                total += 1
                print(f"  {build_id}: {evidence['job_type']}, step={evidence['failed_step']['name']}", file=sys.stderr)
            except Exception as e:
                errors += 1
                print(f"  ERROR {build_id}: {e}", file=sys.stderr)

    if skipped:
        print(f"  ({skipped} jobs skipped — artifacts_dir not found)", file=sys.stderr)
    print(f"\nExtracted evidence for {total} jobs ({errors} errors) → {evidence_dir}", file=sys.stderr)
    return total, errors


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    artifacts_dir = None
    workdir = None
    batch = False
    output = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--artifacts-dir":
            artifacts_dir = args[i + 1]; i += 2
        elif args[i] == "--workdir":
            workdir = args[i + 1]; i += 2
        elif args[i] == "--batch":
            batch = True; i += 1
        elif args[i] == "--output":
            output = args[i + 1]; i += 2
        elif args[i] in ("-h", "--help"):
            print(__doc__); sys.exit(0)
        else:
            print(f"Unknown argument: {args[i]}", file=sys.stderr)
            sys.exit(1)

    if not workdir:
        print("Error: --workdir is required", file=sys.stderr)
        sys.exit(1)

    if batch:
        print("=== Extracting evidence (batch) ===", file=sys.stderr)
        total, errors = run_batch(workdir)
        sys.exit(1 if errors > 0 and total == 0 else 0)

    if not artifacts_dir:
        print("Error: --artifacts-dir required (or use --batch)", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(artifacts_dir):
        print(f"Error: artifacts directory not found: {artifacts_dir}", file=sys.stderr)
        sys.exit(1)

    evidence = extract_evidence(artifacts_dir, workdir)
    build_id = evidence["build_id"]

    if not output:
        evidence_dir = os.path.join(workdir, "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        output = os.path.join(evidence_dir, f"evidence-{build_id}.json")

    with open(output, "w") as f:
        json.dump(evidence, f, indent=2)
    print(f"Written: {output}", file=sys.stderr)
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
