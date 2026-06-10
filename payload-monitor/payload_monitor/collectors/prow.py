"""Fetch job details and failing tests from Prow CI artifacts."""

from __future__ import annotations

import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from ..models import FailingTest, JobResult, JobRun, PreviousAttempt

logger = logging.getLogger(__name__)

# Max length for error messages in the report
MAX_ERROR_LENGTH = 500


def _prow_url_to_gcs_path(prow_url: str) -> Optional[str]:
    """Convert a Prow view URL to a GCS path.

    Example:
        https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-.../ID
        -> gs://test-platform-results/logs/periodic-.../ID
    """
    match = re.search(r"/view/gs/(.+)", prow_url)
    if not match:
        return None
    return f"gs://{match.group(1)}"


def _fetch_gcs_file(gcs_path: str) -> Optional[str]:
    """Fetch a file from GCS using gsutil."""
    try:
        result = subprocess.run(
            ["gsutil", "cat", gcs_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
        logger.debug(f"gsutil cat failed for {gcs_path}: {result.stderr}")
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug(f"Failed to fetch {gcs_path}: {e}")
        return None


def _truncate(text: str, max_len: int = MAX_ERROR_LENGTH) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "... [truncated]"


def _parse_junit(xml_content: str) -> list[FailingTest]:
    """Parse junit XML and extract failing test cases."""
    tests = []
    try:
        root = ET.fromstring(xml_content)
        for testcase in root.iter("testcase"):
            failure = testcase.find("failure")
            if failure is not None:
                name = testcase.get("name", "")
                error_msg = failure.get("message", "") or (failure.text or "")
                # Clean up XML-escaped content
                error_msg = error_msg.replace("&#xA;", "\n").replace("&#34;", '"')
                duration = float(testcase.get("time", "0") or "0")
                tests.append(FailingTest(
                    name=name,
                    error_message=_truncate(error_msg),
                    duration_seconds=duration,
                ))
    except ET.ParseError as e:
        logger.warning(f"Failed to parse junit XML: {e}")
    return tests


def _extract_error_summary(failing_tests: list[FailingTest]) -> str:
    """Create a brief error summary from failing tests."""
    if not failing_tests:
        return ""

    # Filter out gather/observer steps — focus on the actual test/setup failure
    meaningful = [
        t for t in failing_tests
        if not any(kw in t.name.lower() for kw in ["gather", "observer", "resource-watch"])
    ]
    if not meaningful:
        meaningful = failing_tests[:1]

    summaries = []
    for t in meaningful[:3]:
        # Extract the step name
        step_match = re.search(r"(\S+)\s+container test$", t.name)
        step_name = step_match.group(1) if step_match else t.name
        # First meaningful line of error
        first_line = ""
        for line in t.error_message.split("\n"):
            line = line.strip()
            if line and not line.startswith("E0") and not line.startswith("{"):
                first_line = line[:120]
                break
        if first_line:
            summaries.append(f"{step_name}: {first_line}")
        else:
            summaries.append(step_name)

    return "; ".join(summaries)


def enrich_job(job: JobRun) -> None:
    """Enrich a failing job with test failure details from Prow artifacts."""
    if not job.prow_url:
        return

    gcs_base = _prow_url_to_gcs_path(job.prow_url)
    if not gcs_base:
        logger.debug(f"Could not parse GCS path from {job.prow_url}")
        return

    # Fetch the top-level junit_operator.xml
    junit_path = f"{gcs_base}/artifacts/junit_operator.xml"
    xml_content = _fetch_gcs_file(junit_path)
    if xml_content:
        job.failing_tests = _parse_junit(xml_content)
        job.error_summary = _extract_error_summary(job.failing_tests)
        logger.debug(
            f"  {job.name}: {len(job.failing_tests)} failing tests"
        )


def enrich_previous_attempt(attempt: PreviousAttempt) -> None:
    """Enrich a previous attempt with test failure details from Prow artifacts."""
    if not attempt.prow_url:
        return

    gcs_base = _prow_url_to_gcs_path(attempt.prow_url)
    if not gcs_base:
        logger.debug(f"Could not parse GCS path from {attempt.prow_url}")
        return

    junit_path = f"{gcs_base}/artifacts/junit_operator.xml"
    xml_content = _fetch_gcs_file(junit_path)
    if xml_content:
        attempt.failing_tests = _parse_junit(xml_content)
        attempt.error_summary = _extract_error_summary(attempt.failing_tests)
        logger.debug(
            f"  previous attempt: {len(attempt.failing_tests)} failing tests"
        )


def enrich_failing_jobs(jobs: list[JobRun], max_workers: int = 4) -> None:
    """Enrich all failing edge jobs and previous attempts with test failure details."""
    failing = [j for j in jobs if j.result == JobResult.FAILURE and j.topology]
    prev_attempts = [
        pa for j in jobs if j.topology
        for pa in j.previous_attempts
    ]
    if not failing and not prev_attempts:
        return
    logger.info(
        f"Enriching {len(failing)} failing edge jobs and "
        f"{len(prev_attempts)} previous attempt(s) with Prow data"
    )
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for job in failing:
            futures[pool.submit(enrich_job, job)] = f"job:{job.name}"
        for pa in prev_attempts:
            futures[pool.submit(enrich_previous_attempt, pa)] = f"attempt:{pa.prow_url}"
        for future in as_completed(futures):
            label = futures[future]
            try:
                future.result()
            except Exception as e:  # noqa: BLE001 — isolate per-job failures in thread pool
                logger.error(f"Failed to enrich {label}: {e}")
                continue
