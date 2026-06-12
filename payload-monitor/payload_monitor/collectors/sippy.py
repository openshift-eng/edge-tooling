"""Fetch test and job health data from Sippy."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ..config import Config
from .http import create_session
from ..models import Regression

logger = logging.getLogger(__name__)

BASE_URL = "https://sippy.dptools.openshift.org"
JOBS_URL = f"{BASE_URL}/api/jobs"


def fetch_edge_jobs(
    release: str, config: Config, session: requests.Session | None = None
) -> list[dict]:
    """Fetch jobs from Sippy and filter for edge topologies."""
    logger.info(f"Fetching Sippy jobs for release {release}")

    s = session or create_session()
    try:
        resp = s.get(JOBS_URL, params={"release": release}, timeout=30)
        resp.raise_for_status()
        all_jobs = resp.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch Sippy jobs for {release}: {e}")
        return []

    if not isinstance(all_jobs, list):
        logger.error(f"Unexpected Sippy jobs response format: {type(all_jobs)}")
        return []

    edge_jobs = []
    for job in all_jobs:
        name = job.get("name", "")
        topology = config.classify_topology(name)
        if topology:
            job["_topology"] = topology
            edge_jobs.append(job)

    logger.info(f"  Found {len(edge_jobs)} edge jobs for {release}")
    return edge_jobs


def identify_regressions(
    edge_jobs: list[dict],
    min_runs: int = 3,
) -> list[Regression]:
    """Identify jobs that are getting worse over time.

    Uses Sippy's pre-computed net_improvement field which handles edge cases
    (e.g. zero runs in either period). A negative net_improvement means the
    job's pass rate dropped compared to the previous period.
    Jobs with fewer than min_runs are excluded (insufficient data).
    """
    regressions = []

    for job in edge_jobs:
        current_pass = job.get("current_pass_percentage", 0)
        previous_pass = job.get("previous_pass_percentage", 0)
        net_improvement = job.get("net_improvement", 0)
        current_runs = job.get("current_runs", 0)
        name = job.get("name", "")
        topology = job.get("_topology", "")
        jira_component = job.get("jira_component", "")
        triage_url = f"{BASE_URL}/sippy-ng/jobs/{name}"

        # Skip jobs with too few runs — insufficient data to confirm regression
        if current_runs < min_runs:
            continue

        # Flag as regression if the job is getting worse over time
        if net_improvement >= 0:
            continue

        regressions.append(Regression(
            test_name=name,
            test_id=str(job.get("id", "")),
            component=jira_component,
            capability="",
            basis_pass_rate=previous_pass,
            sample_pass_rate=current_pass,
            topology=topology,
            triage_url=triage_url,
            current_runs=current_runs,
        ))

    return regressions


def _collect_version(version: str, config: Config) -> tuple[str, list[Regression]]:
    """Collect regressions for a single version."""
    session = create_session()
    try:
        edge_jobs = fetch_edge_jobs(version, config, session=session)
        regressions = identify_regressions(edge_jobs)
        if regressions:
            logger.info(
                f"  {version}: {len(regressions)} edge job regressions detected"
            )
        return version, regressions
    finally:
        session.close()


def collect(config: Config, versions: list[str]) -> dict[str, list[Regression]]:
    """Collect Sippy regressions for all configured versions in parallel.

    Returns a dict mapping version -> list of regressions.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=min(len(versions) or 1, 10)) as pool:
        futures = {
            pool.submit(_collect_version, v, config): v
            for v in versions
        }
        for future in as_completed(futures):
            v = futures[future]
            try:
                version, regressions = future.result()
                results[version] = regressions
            except Exception as e:  # broad catch: isolate per-version failures
                logger.error(f"Sippy collection failed for {v}: {e}")
                results[v] = []
    return results
