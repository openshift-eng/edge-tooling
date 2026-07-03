"""Fetch and triage PR payload test runs from pr-payload-tests.ci.openshift.org."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

from ..models import (
    FailingTest,
    FailureVerdict,
    JobResult,
    PRPayloadJob,
    PRTriageResult,
)
from .http import create_session
from .payload_job_discovery import _parse_pr_ref as _parse_pr_ref_3
from .prow import _fetch_gcs_file, _prow_url_to_gcs_path

logger = logging.getLogger(__name__)

_session = create_session()

# Matches Prow job links embedded in the pr-payload-tests page
_PROW_HREF_RE = re.compile(
    r'href="(https://prow\.ci\.openshift\.org/view/gs/[^"]+)"'
)
# Extracts job name and build ID from a Prow URL
_PROW_JOB_RE = re.compile(r'/logs/([^/]+)/(\d+)')


def _parse_prow_urls(html: str) -> list[str]:
    return list(dict.fromkeys(_PROW_HREF_RE.findall(html)))  # deduplicated, order preserved


def _job_name_from_url(prow_url: str) -> str:
    m = _PROW_JOB_RE.search(prow_url)
    return m.group(1) if m else prow_url


def _fetch_job_result(prow_url: str) -> JobResult:
    gcs_base = _prow_url_to_gcs_path(prow_url)
    if not gcs_base:
        return JobResult.UNKNOWN
    content = _fetch_gcs_file(f"{gcs_base}/finished.json")
    if not content:
        return JobResult.UNKNOWN
    try:
        data = json.loads(content)
        result = data.get("result", "").upper()
        if result == "SUCCESS" or data.get("passed") is True:
            return JobResult.SUCCESS
        if result in ("FAILURE", "FAILED", "ERROR"):
            return JobResult.FAILURE
        if result in ("PENDING", "RUNNING"):
            return JobResult.PENDING
        return JobResult.UNKNOWN
    except json.JSONDecodeError:
        return JobResult.UNKNOWN


# --- Deep junit: fetch individual test results from step artifacts ---

def _list_gcs_dir(gcs_path: str) -> list[str]:
    """List files in a GCS directory."""
    try:
        result = subprocess.run(
            ["gsutil", "ls", gcs_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
        return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def _find_e2e_step_name(gcs_base: str) -> Optional[str]:
    """Discover the e2e test step directory name from the artifacts."""
    artifacts_path = f"{gcs_base}/artifacts/"
    dirs = _list_gcs_dir(artifacts_path)
    for d in dirs:
        # The top-level step directory (e.g. e2e-metal-ovn-two-node-fencing-recovery/)
        if d.endswith("/") and "gather" not in d and "release" not in d and "build" not in d:
            step_name = d.rstrip("/").rsplit("/", 1)[-1]
            return step_name
    return None


def _fetch_deep_junit_failures(prow_url: str) -> list[FailingTest]:
    """Fetch individual test-level failures from the deep junit artifacts.

    Looks for junit_e2e__*.xml inside the e2e test step's artifacts/junit/ directory.
    These contain the actual Go test names (g.It descriptions), not just step-level results.
    """
    gcs_base = _prow_url_to_gcs_path(prow_url)
    if not gcs_base:
        return []

    step_name = _find_e2e_step_name(gcs_base)
    if not step_name:
        return []

    junit_dir = f"{gcs_base}/artifacts/{step_name}/baremetalds-e2e-test/artifacts/junit/"
    files = _list_gcs_dir(junit_dir)

    junit_files = [f for f in files if f.endswith(".xml") and "junit_e2e" in f]
    if not junit_files:
        # Fall back to any junit XML
        junit_files = [f for f in files if f.endswith(".xml")]

    tests: list[FailingTest] = []
    for jf in junit_files:
        content = _fetch_gcs_file(jf)
        if not content:
            continue
        try:
            root = ET.fromstring(content)
            for tc in root.iter("testcase"):
                failure = tc.find("failure")
                if failure is not None:
                    name = tc.get("name", "")
                    error_msg = failure.get("message", "") or (failure.text or "")
                    error_msg = error_msg.replace("&#xA;", "\n").replace("&#34;", '"')
                    duration = float(tc.get("time", "0") or "0")
                    tests.append(FailingTest(
                        name=name, error_message=error_msg[:500], duration_seconds=duration,
                    ))
        except ET.ParseError as e:
            logger.debug(f"Failed to parse {jf}: {e}")

    return tests


def _fetch_single_job(prow_url: str) -> PRPayloadJob:
    name = _job_name_from_url(prow_url)
    result = _fetch_job_result(prow_url)
    job = PRPayloadJob(name=name, prow_url=prow_url, result=result)

    if result == JobResult.FAILURE:
        # Fetch deep junit for individual test names
        deep_tests = _fetch_deep_junit_failures(prow_url)
        if deep_tests:
            job.failing_tests = deep_tests
            job.error_summary = "; ".join(t.name[:80] for t in deep_tests[:3])
        else:
            # Fall back to step-level junit
            from .prow import _fetch_junit_failures
            tests, summary = _fetch_junit_failures(prow_url)
            job.failing_tests = tests
            job.error_summary = summary

    return job


def fetch_pr_payload_run(url: str, max_workers: int = 4) -> list[PRPayloadJob]:
    """Fetch a pr-payload-tests run URL and return all jobs with results."""
    logger.info(f"Fetching PR payload run: {url}")
    try:
        resp = _session.get(url)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Payload page fetch failed: %s", exc)
        return []

    prow_urls = _parse_prow_urls(resp.text)
    if not prow_urls:
        logger.warning("No Prow job links found on the page")
        return []

    logger.info(f"Found {len(prow_urls)} job(s), fetching results...")
    jobs: list[PRPayloadJob] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_single_job, u): u for u in prow_urls}
        for future in as_completed(futures):
            try:
                jobs.append(future.result())
            except Exception as e:
                logger.error(f"Failed to fetch job {futures[future]}: {e}")

    jobs.sort(key=lambda j: j.name)
    return jobs


def build_triage_result(
    payload_url: str,
    pr_ref: str,
    jobs: list[PRPayloadJob],
) -> PRTriageResult:
    passed = sum(1 for j in jobs if j.result == JobResult.SUCCESS)
    failed = sum(1 for j in jobs if j.result == JobResult.FAILURE)
    return PRTriageResult(
        payload_url=payload_url,
        pr_ref=pr_ref,
        total_jobs=len(jobs),
        passed=passed,
        failed=failed,
        jobs=jobs,
    )


# --- PR diff fetching ---

def _parse_pr_ref(pr_ref: str) -> Optional[tuple[str, str]]:
    """Parse a PR reference into (repo, number). Returns None if unparsable."""
    parsed = _parse_pr_ref_3(pr_ref)
    if not parsed:
        return None
    org, repo, number = parsed
    return f"{org}/{repo}", number


def fetch_pr_changed_files(pr_ref: str) -> list[str]:
    """Fetch changed file paths from a GitHub PR."""
    parsed = _parse_pr_ref(pr_ref)
    if not parsed:
        logger.warning(f"Could not parse PR reference: {pr_ref}")
        return []
    repo, number = parsed
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{number}/files",
             "--paginate", "--jq", ".[].filename"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"gh api failed: {result.stderr.strip()}")
            return []
        return [f for f in result.stdout.strip().split("\n") if f]
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error(f"Failed to fetch PR files: {e}")
        return []


def fetch_pr_diff(pr_ref: str) -> str:
    """Fetch the full diff content of a PR."""
    parsed = _parse_pr_ref(pr_ref)
    if not parsed:
        return ""
    repo, number = parsed
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/pulls/{number}",
             "-H", "Accept: application/vnd.github.v3.diff"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error(f"gh api diff failed: {result.stderr.strip()}")
            return ""
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.error(f"Failed to fetch PR diff: {e}")
        return ""


# --- Classifier ---

def _extract_test_description(test_name: str) -> str:
    """Extract the g.It description from a full junit test name.

    Junit names look like:
      [sig-etcd][...] Two Node with Fencing etcd disruption should start normally as standalone voter...
    The meaningful part is after the last ] bracket.
    """
    # Strip leading [sig-...][...] tags
    stripped = re.sub(r'\[[^\]]*\]', '', test_name).strip()
    return stripped


def classify_failure(
    job: PRPayloadJob,
    diff_content: str,
    pr_files: list[str],
) -> FailureVerdict:
    """Classify whether a job failure is PR-caused or flaky.

    Uses diff-aware matching: checks if the failing test's description
    appears in the PR diff, indicating the PR modified or added that test.
    """
    diff_lower = diff_content.lower()

    for t in job.failing_tests:
        # Skip generic infrastructure failures
        if "cluster precondition" in t.name.lower():
            continue

        desc = _extract_test_description(t.name)
        if not desc:
            continue

        # Check if the test description (or a significant substring) appears in the diff
        # Match on a meaningful fragment — at least 20 chars to avoid false positives
        fragments = [desc]
        if len(desc) > 40:
            # Also try the last part (the "should ..." clause)
            should_match = re.search(r'(should .+)', desc, re.IGNORECASE)
            if should_match:
                fragments.append(should_match.group(1))

        for frag in fragments:
            if len(frag) >= 20 and frag.lower() in diff_lower:
                matched = [f for f in pr_files if any(
                    part in f.lower() for part in desc.lower().split()[:3]
                )]
                return FailureVerdict(
                    verdict="pr-caused",
                    reason=f"Failing test found in PR diff: \"{desc}\"",
                    matched_files=matched if matched else pr_files,
                )

    # Fallback: check if any failing test name references functions/identifiers in the diff
    # Extract added/removed Go identifiers from the diff
    diff_identifiers = set(re.findall(r'[+-]func (\w+)', diff_content))
    if diff_identifiers:
        for t in job.failing_tests:
            for ident in diff_identifiers:
                if ident.lower() in t.name.lower() or ident.lower() in t.error_message.lower():
                    return FailureVerdict(
                        verdict="pr-caused",
                        reason=f"Failing test references PR function: {ident}",
                        matched_files=pr_files,
                    )

    return FailureVerdict(
        verdict="flaky",
        reason="Failing test(s) not found in PR diff — likely unrelated to changes",
    )


def classify_failures(
    jobs: list[PRPayloadJob],
    pr_files: list[str],
    diff_content: str,
) -> dict[str, FailureVerdict]:
    """Classify all failing jobs. Returns job_name -> verdict mapping."""
    verdicts: dict[str, FailureVerdict] = {}
    for job in jobs:
        if job.result != JobResult.FAILURE:
            continue
        verdicts[job.name] = classify_failure(job, diff_content, pr_files)
    return verdicts


# --- Markdown output ---

def format_triage_json(result: PRTriageResult) -> str:
    """Format a triage result as structured JSON for machine consumption."""
    jobs_out = []
    for job in result.jobs:
        verdict = result.verdicts.get(job.name)
        job_data = {
            "name": job.name,
            "result": job.result.name,
            "prow_url": job.prow_url,
            "verdict": verdict.verdict if verdict else None,
            "reason": verdict.reason if verdict else None,
            "matched_files": verdict.matched_files if verdict else [],
            "failing_tests": [
                {"name": t.name, "error": t.error_message.split("\n")[0][:200]}
                for t in job.failing_tests
            ],
        }
        jobs_out.append(job_data)

    unknown_count = sum(1 for j in jobs_out if j["result"] == "UNKNOWN")
    complete = unknown_count == 0

    pr_caused = [j for j in jobs_out if j["verdict"] == "pr-caused"]
    if not complete:
        recommendation = "Payload jobs still running. Re-check later."
    elif not result.failed:
        recommendation = "All jobs passed. No triage needed."
    elif not pr_caused:
        recommendation = "All failures are unrelated to this PR. Safe to re-trigger with /payload-job."
    elif len(pr_caused) == len([j for j in jobs_out if j["result"] == "FAILURE"]):
        recommendation = "All failures are in tests this PR modifies. Investigate before re-triggering /payload-job."
    else:
        recommendation = "Some failures are PR-caused. Investigate those before re-triggering /payload-job."

    output = {
        "payload_url": result.payload_url,
        "pr_ref": result.pr_ref,
        "total_jobs": result.total_jobs,
        "passed": result.passed,
        "failed": result.failed,
        "complete": complete,
        "jobs": jobs_out,
        "recommendation": recommendation,
    }
    return json.dumps(output, indent=2)


def format_triage_markdown(result: PRTriageResult) -> str:
    """Format a triage result as a readable markdown summary."""
    lines: list[str] = []

    status = "PASS" if result.failed == 0 else "FAIL"
    lines.append(f"## PR Payload Triage: {status}")
    lines.append("")
    lines.append(f"**PR:** {result.pr_ref}")
    lines.append(f"**Payload run:** {result.payload_url}")
    lines.append(f"**Jobs:** {result.passed}/{result.total_jobs} passed, {result.failed} failed")
    lines.append("")

    if result.failed == 0:
        lines.append("All jobs passed. No triage needed.")
        return "\n".join(lines)

    lines.append("### Failure Classification")
    lines.append("")

    for job in result.jobs:
        if job.result != JobResult.FAILURE:
            continue
        verdict = result.verdicts.get(job.name)
        if not verdict:
            continue

        icon = "🔴" if verdict.verdict == "pr-caused" else "🟡"
        label = verdict.verdict.upper().replace("-", " ")
        lines.append(f"#### {icon} {job.name}")
        lines.append(f"**Verdict:** {label}")
        lines.append(f"**Reason:** {verdict.reason}")
        if verdict.matched_files:
            lines.append(f"**Matched files:** {', '.join(verdict.matched_files)}")
        lines.append(f"**Prow:** {job.prow_url}")

        if job.failing_tests:
            lines.append("")
            lines.append("| Test | Error |")
            lines.append("|------|-------|")
            for t in job.failing_tests:
                name = t.name[:120]
                error = t.error_message.split("\n")[0][:100]
                lines.append(f"| {name} | {error} |")

        lines.append("")

    return "\n".join(lines)
