"""Prow artifact enrichment using the storage command selected by the Edge monitor."""

from __future__ import annotations

import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from payload_monitor.collectors.prow import (
    _extract_error_summary,
    _parse_junit,
    _prow_url_to_gcs_path,
)
from payload_monitor.models import JobResult, JobRun, PreviousAttempt

logger = logging.getLogger(__name__)


def _enrich_run(run: JobRun | PreviousAttempt, command: tuple[str, ...]) -> None:
    path = _prow_url_to_gcs_path(run.prow_url)
    if not path:
        return
    artifact = f"{path}/artifacts/junit_operator.xml"
    try:
        result = subprocess.run([*command, artifact], check=False, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.debug("Failed to fetch %s: %s", artifact, exc)
        return
    if result.returncode:
        logger.debug("%s failed for %s: %s", " ".join(command), artifact, result.stderr)
        return
    tests = _parse_junit(result.stdout) if result.stdout else []
    if tests:
        run.failing_tests = tests
        run.error_summary = _extract_error_summary(tests)


def enrich_failing_jobs(jobs: list[JobRun], *, command: tuple[str, ...], max_workers: int) -> None:
    """Enrich failing jobs and previous attempts, isolating failures per run."""
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for job in jobs:
            if job.result == JobResult.FAILURE and job.prow_url:
                futures[pool.submit(_enrich_run, job, command)] = job.prow_url
            for attempt in job.previous_attempts:
                if attempt.prow_url:
                    futures[pool.submit(_enrich_run, attempt, command)] = attempt.prow_url
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                logger.exception("Failed to enrich %s", futures[future])
