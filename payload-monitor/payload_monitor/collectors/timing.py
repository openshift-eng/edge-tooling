"""Collect install/upgrade timing data from Sippy APIs and GCS artifacts."""

from __future__ import annotations

from typing import Optional
import json
import logging
import statistics as stats_mod
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests as requests_lib

from ..config import Config, INSTALL_PHASES
from ..models import TimingRun, TimingReport
from .http import create_session

logger = logging.getLogger(__name__)

BASE_URL = "https://sippy.dptools.openshift.org"
JOBS_URL = f"{BASE_URL}/api/jobs"
JOB_RUNS_URL = f"{BASE_URL}/api/jobs/runs"
RUN_SUMMARY_URL = f"{BASE_URL}/api/job/run/summary"
DURATIONS_URL = f"{BASE_URL}/api/tests/durations"

GCS_BASE = "https://storage.googleapis.com/test-platform-results/logs"

_session = create_session()


# ---------------------------------------------------------------------------
# Variant extraction & job classification
# ---------------------------------------------------------------------------

def extract_variant(job_name: str) -> dict:
    """Extract infrastructure variant dimensions from a job name."""
    name = job_name.lower()

    if "dualstack" in name:
        network = "dualstack"
    elif "ipv6" in name:
        network = "ipv6"
    else:
        network = "ipv4"

    feature = "techpreview" if "techpreview" in name else "standard"

    if "assisted" in name:
        install_method = "assisted"
    elif "agent" in name:
        install_method = "agent"
    else:
        install_method = "metal"

    if "recovery" in name:
        scenario = "recovery"
    elif "degraded" in name:
        scenario = "degraded"
    else:
        scenario = "standard"

    return {
        "network": network,
        "feature": feature,
        "install_method": install_method,
        "scenario": scenario,
    }


def classify_job_type(job_name: str) -> str:
    """Classify a job as 'install' or 'upgrade'."""
    return "upgrade" if "upgrade" in job_name.lower() else "install"


# ---------------------------------------------------------------------------
# JSON cache management
# ---------------------------------------------------------------------------

def load_cache(cache_path: Path) -> TimingReport:
    """Load timing data from JSON cache file."""
    if not cache_path.exists():
        return TimingReport()

    try:
        data = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning(f"Corrupt or unreadable cache at {cache_path}, starting fresh")
        return TimingReport()

    runs = {}
    for run_id, run_data in data.get("runs", {}).items():
        runs[run_id] = TimingRun(
            job_name=run_data["job_name"],
            topology=run_data["topology"],
            release=run_data["release"],
            start_time=run_data["start_time"],
            duration_seconds=run_data["duration_seconds"],
            result=run_data["result"],
            run_type=run_data["run_type"],
            variant=run_data.get("variant", {}),
            step_durations=run_data.get("step_durations", {}),
        )

    return TimingReport(
        last_updated=data.get("last_updated", ""),
        runs=runs,
        phase_durations=data.get("phase_durations", {}),
    )


def save_cache(report: TimingReport, cache_path: Path) -> None:
    """Save timing data to JSON cache file."""
    data = {
        "last_updated": report.last_updated,
        "runs": {run_id: asdict(run) for run_id, run in report.runs.items()},
        "phase_durations": report.phase_durations,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(data, indent=2))


def prune_cache(report: TimingReport, max_age_days: int = 30) -> None:
    """Remove runs older than max_age_days from the report."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    def _is_recent(run: TimingRun) -> bool:
        try:
            run_time = datetime.fromisoformat(run.start_time.replace("Z", "+00:00"))
            return run_time >= cutoff
        except ValueError:
            return False

    report.runs = {
        run_id: run for run_id, run in report.runs.items()
        if _is_recent(run)
    }


# ---------------------------------------------------------------------------
# Statistics computation
# ---------------------------------------------------------------------------

def compute_stats(runs: list[TimingRun]) -> dict:
    """Compute duration statistics over a list of TimingRun objects.

    Returns dict with: count, avg, median, min, max, p90, p95, p99, cv, stddev.
    All duration values are in seconds.
    """
    if not runs:
        return {
            "count": 0, "avg": 0, "median": 0, "min": 0, "max": 0,
            "p90": 0, "p95": 0, "p99": 0, "cv": 0, "stddev": 0,
        }

    durations = sorted(r.duration_seconds for r in runs)
    n = len(durations)
    avg = stats_mod.mean(durations)
    median = stats_mod.median(durations)
    stddev = stats_mod.stdev(durations) if n > 1 else 0.0
    cv = (stddev / avg * 100) if avg > 0 else 0

    # quantiles(n=100) requires at least 2 data points
    if n >= 2:
        cuts = stats_mod.quantiles(durations, n=100, method="inclusive")
        p90, p95, p99 = cuts[89], cuts[94], cuts[98]
    else:
        p90 = p95 = p99 = durations[0]

    return {
        "count": n,
        "avg": avg,
        "median": median,
        "min": durations[0],
        "max": durations[-1],
        "p90": p90,
        "p95": p95,
        "p99": p99,
        "cv": round(cv, 1),
        "stddev": round(stddev, 1),
    }


# ---------------------------------------------------------------------------
# Sippy API fetching
# ---------------------------------------------------------------------------

def fetch_edge_jobs(release: str, config: Config) -> list[dict]:
    """Fetch all jobs from Sippy for a release, filter for edge topologies."""
    logger.info(f"Fetching edge topology jobs for release {release}")
    try:
        resp = _session.get(JOBS_URL, params={"release": release}, timeout=30)
        resp.raise_for_status()
        all_jobs = resp.json()
    except requests_lib.RequestException as e:
        logger.error(f"Failed to fetch Sippy jobs for {release}: {e}")
        return []

    if not isinstance(all_jobs, list):
        return []

    edge_jobs = []
    for job in all_jobs:
        name = job.get("name", "")
        topology = config.classify_topology(name)
        if topology in ("SNO", "TNA", "TNF"):
            job["_topology"] = topology
            edge_jobs.append(job)

    logger.info(f"  Found {len(edge_jobs)} SNO/TNA/TNF jobs for {release}")
    return edge_jobs


def fetch_job_runs(job_name: str, release: str) -> list[dict]:
    """Fetch recent runs for a job from Sippy /api/jobs/runs endpoint.

    Returns a list of run dicts with keys: prow_id, overall_result,
    timestamp (ms), url, etc.  Returns [] on error.
    """
    filter_json = json.dumps({
        "items": [{
            "columnField": "job",
            "operatorValue": "equals",
            "value": job_name,
        }],
    })
    try:
        resp = _session.get(
            JOB_RUNS_URL,
            params={
                "release": release,
                "filter": filter_json,
                "perPage": "200",
                "sortField": "timestamp",
                "sort": "desc",
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests_lib.RequestException as e:
        logger.debug(f"Failed to fetch runs for {job_name}: {e}")
        return []

    if not isinstance(data, dict):
        return []

    rows = data.get("rows")
    return rows if isinstance(rows, list) else []


def fetch_run_summary(run_id: str) -> Optional[dict]:
    """Fetch job run summary from Sippy. Returns None on error."""
    try:
        resp = _session.get(
            RUN_SUMMARY_URL,
            params={"prow_job_run_id": run_id},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests_lib.RequestException as e:
        logger.debug(f"Failed to fetch run summary for {run_id}: {e}")
        return None


def fetch_phase_durations(release: str, test_name: str) -> dict[str, float]:
    """Fetch daily average duration for a test from Sippy."""
    try:
        resp = _session.get(
            DURATIONS_URL,
            params={"release": release, "test": test_name},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except requests_lib.RequestException as e:
        logger.debug(f"Failed to fetch durations for '{test_name}': {e}")
        return {}


# ---------------------------------------------------------------------------
# GCS artifact fetching (per-step durations)
# ---------------------------------------------------------------------------

# Patterns to classify CI step names into logical phases.
# Order matters: first match wins.
_STEP_PATTERNS = [
    ("install", ["devscripts-setup", "ipi-install-install"]),
    ("test", ["e2e-test"]),
]


def _classify_step(step_name: str) -> Optional[str]:
    """Map a junit_operator testcase name to a logical step key."""
    name_lower = step_name.lower()
    # Phase totals (e.g. "Run multi-stage test pre phase")
    for phase in ("pre phase", "test phase", "post phase"):
        if phase in name_lower:
            return phase
    # Individual steps
    for key, patterns in _STEP_PATTERNS:
        if any(p in name_lower for p in patterns):
            return key
    return None


def fetch_step_durations(job_name: str, run_id: str) -> dict[str, float]:
    """Fetch per-step durations from GCS junit_operator.xml.

    Returns dict mapping logical step names to duration in seconds.
    E.g. {"install": 4752.0, "test": 7680.0, "pre phase": 4890.0, ...}
    """
    url = f"{GCS_BASE}/{job_name}/{run_id}/artifacts/junit_operator.xml"
    try:
        resp = _session.get(url, timeout=15)
        resp.raise_for_status()
    except requests_lib.RequestException as e:
        logger.debug(f"Failed to fetch junit_operator.xml for {run_id}: {e}")
        return {}

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        logger.debug(f"Failed to parse junit_operator.xml for {run_id}")
        return {}

    steps: dict[str, float] = {}
    for tc in root.iter("testcase"):
        name = tc.get("name", "")
        time_s = float(tc.get("time", 0))
        key = _classify_step(name)
        if key and time_s > 0:
            steps[key] = time_s

    return steps


def collect(
    config: Config,
    versions: list[str],
    cache_path: Path,
    days: int = 7,
) -> TimingReport:
    """Collect timing data for SNO/TNA/TNF jobs across versions.

    Pipeline (parallelized at each stage):
    1. Load existing cache
    2. Fetch SNO/TNA/TNF jobs for all versions in parallel
    3. Fetch job runs for all jobs in parallel
    4. Fetch summaries + step durations for all new runs in parallel
    5. Fetch per-phase durations in parallel
    6. Prune old data, save cache
    """
    report = load_cache(cache_path)
    cached_ids = set(report.runs.keys())
    logger.info(f"Timing: loaded {len(cached_ids)} cached runs")

    # Stage 1: Fetch jobs for all versions in parallel
    logger.info("Timing: fetching jobs for all versions...")
    version_jobs: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=len(versions)) as pool:
        futures = {
            pool.submit(fetch_edge_jobs, version, config): version
            for version in versions
        }
        for future in as_completed(futures):
            version = futures[future]
            try:
                version_jobs[version] = future.result()
            except Exception as e:
                logger.error(f"Failed to fetch jobs for {version}: {e}")
                version_jobs[version] = []

    total_jobs = sum(len(jobs) for jobs in version_jobs.values())
    logger.info(f"Timing: found {total_jobs} SNO/TNA/TNF jobs across {len(versions)} versions")

    # Stage 2: Fetch runs for all jobs in parallel
    # Build task list: (version, job_name, topology, run_type, variant)
    job_tasks = []
    for version, jobs in version_jobs.items():
        for job in jobs:
            job_tasks.append((
                version,
                job["name"],
                job["_topology"],
                classify_job_type(job["name"]),
                extract_variant(job["name"]),
            ))

    logger.info(f"Timing: fetching runs for {len(job_tasks)} jobs...")
    # (run_id, job_name, run_data, version, topology, run_type, variant)
    new_run_tasks = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(fetch_job_runs, job_name, version): (version, job_name, topology, run_type, variant)
            for version, job_name, topology, run_type, variant in job_tasks
        }
        for future in as_completed(futures):
            version, job_name, topology, run_type, variant = futures[future]
            try:
                runs = future.result()
            except Exception:
                continue
            for r in runs:
                rid = str(r.get("prow_id", ""))
                if rid and rid not in cached_ids:
                    new_run_tasks.append((rid, job_name, r, version, topology, run_type, variant))

    logger.info(f"Timing: {len(new_run_tasks)} new runs to fetch details for")

    # Stage 3: Fetch summaries + step durations for all new runs in parallel
    if new_run_tasks:
        logger.info("Timing: fetching summaries + step durations...")
        summaries: dict[str, Optional[dict]] = {}
        step_results: dict[str, dict[str, float]] = {}

        with ThreadPoolExecutor(max_workers=8) as pool:
            summary_futures = {
                pool.submit(fetch_run_summary, rid): rid
                for rid, job_name, *_ in new_run_tasks
            }
            step_futures = {
                pool.submit(fetch_step_durations, job_name, rid): rid
                for rid, job_name, *_ in new_run_tasks
            }

            for future in as_completed(summary_futures):
                rid = summary_futures[future]
                try:
                    summaries[rid] = future.result()
                except Exception:
                    summaries[rid] = None

            for future in as_completed(step_futures):
                rid = step_futures[future]
                try:
                    step_results[rid] = future.result()
                except Exception:
                    step_results[rid] = {}

        added = 0
        for rid, job_name, run_data, version, topology, run_type, variant in new_run_tasks:
            summary = summaries.get(rid)

            ts_ms = run_data.get("timestamp", 0)
            if ts_ms:
                start_time = datetime.fromtimestamp(
                    ts_ms / 1000, tz=timezone.utc,
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                start_time = ""

            duration = summary.get("durationSeconds", 0) if summary else 0

            report.runs[rid] = TimingRun(
                job_name=job_name,
                topology=topology,
                release=version,
                start_time=start_time,
                duration_seconds=duration,
                result=run_data.get("overall_result", "U"),
                run_type=run_type,
                variant=variant,
                step_durations=step_results.get(rid, {}),
            )
            cached_ids.add(rid)
            added += 1

        steps_ok = sum(1 for s in step_results.values() if s)
        logger.info(f"Timing: added {added} runs ({steps_ok} with step durations)")

    # Stage 4: Fetch per-phase durations in parallel
    logger.info("Timing: fetching per-phase durations from Sippy...")
    phase_tasks = [
        (version, phase)
        for version in versions
        for phase in INSTALL_PHASES
    ]
    phase_durations = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(fetch_phase_durations, version, phase): (version, phase)
            for version, phase in phase_tasks
        }
        for future in as_completed(futures):
            version, phase = futures[future]
            try:
                durations = future.result()
                if durations:
                    phase_durations[f"{version}:{phase}"] = durations
            except Exception:
                pass
    report.phase_durations = phase_durations

    # Update metadata, prune, save
    report.last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prune_cache(report, max_age_days=days)
    save_cache(report, cache_path)

    successful = report.successful_runs
    logger.info(
        f"Timing: done — {len(report.runs)} total runs, "
        f"{len(successful)} successful, saved to {cache_path}"
    )
    return report
