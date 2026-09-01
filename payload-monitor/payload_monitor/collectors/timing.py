"""Collect install/upgrade timing data from Sippy APIs and GCS artifacts."""

from __future__ import annotations

from typing import Optional
import json
import logging
import os
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
GCS_LIST_URL = "https://storage.googleapis.com/storage/v1/b/test-platform-results/o"

CACHE_ARTIFACT_RELPATH = (
    "artifacts/ocp-ci-monitor/"
    "openshift-edge-tooling-ci-monitor/artifacts/timing_cache.json"
)

_session = create_session()


# ---------------------------------------------------------------------------
# GCS cache seeding (cross-run persistence)
# ---------------------------------------------------------------------------

# Safety bound on pagination — at ~1000 build folders per page, this covers
# decades of a daily job's history before it would ever bite.
_MAX_LIST_PAGES = 50


def _find_previous_build_ids(job_name: str, current_build: str, limit: int = 1) -> list[str]:
    """Find the most recent completed build IDs for *job_name* in GCS.

    ``latest-build.txt`` can't be used for this — Prow writes it to point at
    the currently-running build as soon as the job starts, so by the time this
    code runs mid-job it always equals *current_build*. Instead, list the
    job's build-ID subdirectories directly and pick the highest ones that
    aren't the current build.

    GCS returns listings in ascending lexicographic order with no
    server-side reverse-sort, so the builds we want — the most recent ones —
    are on the *last* page, not the first. Pagination is followed to the end
    rather than reading a single page, to avoid silently picking an old
    build once a job accumulates more history than fits on one page.

    Returns up to *limit* build IDs, most-recent-first. Prow creates a
    build's directory as soon as it starts, so a rerun/retest can leave a
    newer, still-in-progress build ahead of the last completed one —
    callers should be ready to fall back to an older candidate.
    """
    build_ids: list[str] = []
    page_token = None

    for _ in range(_MAX_LIST_PAGES):
        params = {"prefix": f"logs/{job_name}/", "delimiter": "/"}
        if page_token:
            params["pageToken"] = page_token

        try:
            resp = _session.get(GCS_LIST_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests_lib.RequestException as e:
            logger.warning("Could not list GCS builds for %s (%s)", job_name, type(e).__name__)
            return []

        if not isinstance(data, dict):
            return []

        prefixes = data.get("prefixes")
        if isinstance(prefixes, list):
            for prefix in prefixes:
                build_id = prefix.strip("/").rsplit("/", 1)[-1]
                if build_id.isdigit() and build_id != current_build:
                    build_ids.append(build_id)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    if not build_ids:
        return []
    return sorted(build_ids, key=int, reverse=True)[:limit]


def seed_cache_from_previous_run(cache_path: Path) -> None:
    """Download timing_cache.json from the previous Prow run's GCS artifacts.

    Lists the job's GCS build directories to find the most recent completed
    builds, then fetches ``timing_cache.json`` from the first one that has it.
    A rerun/retest can leave a newer, still-in-progress build directory ahead
    of the last completed one — its cache artifact 404s since the job hasn't
    finished writing it yet — so a few candidates are tried in descending
    order rather than giving up after the single most-recent build.
    Skips gracefully on any failure (logging a warning) — this must never be
    fatal, because a cold start is the natural fallback.
    """
    if cache_path.exists():
        return

    job_name = os.environ.get("JOB_NAME", "")
    if not job_name:
        return

    current_build = os.environ.get("BUILD_ID", "")

    previous_builds = _find_previous_build_ids(job_name, current_build, limit=3)
    if not previous_builds:
        logger.warning(f"No previous completed build found for {job_name}")
        return

    for previous_build in previous_builds:
        cache_url = f"{GCS_BASE}/{job_name}/{previous_build}/{CACHE_ARTIFACT_RELPATH}"
        try:
            resp = _session.get(cache_url, timeout=30)
        except requests_lib.RequestException as e:
            logger.warning(
                "Could not fetch previous cache artifact (build %s) (%s)",
                previous_build, type(e).__name__,
            )
            continue
        if not resp.ok:
            continue
        break
    else:
        logger.warning(f"No previous build with a timing cache found for {job_name}")
        return

    try:
        payload = json.loads(resp.text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            f"Previous cache artifact is not valid JSON (build {previous_build}): {e}"
        )
        return

    if not _is_valid_cache_payload(payload):
        logger.warning("Previous cache artifact has unexpected structure, ignoring")
        return

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(resp.text)
    except OSError as e:
        logger.warning(f"Could not write seeded cache to {cache_path}: {e}")
        return
    logger.info(
        f"Seeded timing cache from build {previous_build} "
        f"({len(payload.get('runs', {}))} runs)"
    )


# Fields load_cache() indexes directly (run_data["..."]) with no default —
# a missing or wrong-typed value there raises KeyError/TypeError.
_REQUIRED_RUN_STRING_FIELDS = (
    "job_name", "topology", "release", "start_time", "result", "run_type",
)


def _is_valid_cache_payload(data) -> bool:
    """Validate a downloaded cache payload matches the shape load_cache() expects.

    This is an untrusted, externally-fetched artifact (GCS), so we allow-list
    the exact structure rather than trusting arbitrary valid JSON.
    """
    if not isinstance(data, dict):
        return False
    runs = data.get("runs")
    if not isinstance(runs, dict):
        return False
    for run_data in runs.values():
        if not isinstance(run_data, dict):
            return False
        if not all(
            isinstance(run_data.get(field), str)
            for field in _REQUIRED_RUN_STRING_FIELDS
        ):
            return False
        duration = run_data.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            return False
        for optional_field in ("variant", "step_durations"):
            if optional_field in run_data and not isinstance(run_data[optional_field], dict):
                return False
        for v in run_data.get("step_durations", {}).values():
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                return False
        for v in run_data.get("variant", {}).values():
            if not isinstance(v, str):
                return False
    return True


def _parse_sippy_timestamp(value) -> Optional[datetime]:
    """Parse a Sippy ``timestamp`` field into a UTC datetime.

    ``/api/jobs/runs`` returns ISO-8601 strings (e.g. "2026-08-30T22:36:19Z"),
    not epoch milliseconds — despite the ``_ms``-style naming used historically
    in this module. Epoch-millisecond numerics are still accepted for
    robustness against other callers/formats. Returns None for anything
    missing or unparseable.
    """
    if not value or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    return None


def _within_retention_window(timestamp, days: int) -> bool:
    """Return True if *timestamp* falls within the last *days* days.

    Returns True for missing/unparseable timestamps (can't judge age).
    """
    run_time = _parse_sippy_timestamp(timestamp)
    if run_time is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return run_time >= cutoff


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
    """Fetch all jobs from Sippy for a release, filter for edge topologies.

    Delegates to collectors.sippy.fetch_edge_jobs so topology filtering stays
    config-driven and isn't duplicated across collectors.
    """
    from . import sippy as _sippy

    return _sippy.fetch_edge_jobs(release, config, session=_session)


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


def _build_timing_run(
    job_name: str,
    run_data: dict,
    version: str,
    topology: str,
    run_type: str,
    variant: dict,
    summary: Optional[dict],
    step_durations: dict[str, float],
) -> TimingRun:
    """Build a TimingRun from raw Sippy job-run + run-summary data."""
    start_dt = _parse_sippy_timestamp(run_data.get("timestamp"))
    start_time = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if start_dt else ""
    duration = summary.get("durationSeconds", 0) if summary else 0

    return TimingRun(
        job_name=job_name,
        topology=topology,
        release=version,
        start_time=start_time,
        duration_seconds=duration,
        result=run_data.get("overall_result", "U"),
        run_type=run_type,
        variant=variant,
        step_durations=step_durations,
    )


def collect(
    config: Config,
    versions: list[str],
    cache_path: Path,
    days: int = 7,
) -> TimingReport:
    """Collect timing data for SNO/TNA/TNF jobs across versions.

    Pipeline (parallelized at each stage):
    1. Seed the local cache from the previous Prow run's GCS artifacts, then
       load the (possibly just-seeded) existing cache
    2. Fetch SNO/TNA/TNF jobs for all versions in parallel
    3. Fetch job runs for all jobs in parallel
    4. Fetch summaries + step durations for all new runs in parallel
    5. Fetch per-phase durations in parallel
    6. Prune old data, save cache
    """
    seed_cache_from_previous_run(cache_path)
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
                if not rid or rid in cached_ids:
                    continue
                if not _within_retention_window(r.get("timestamp", 0), days):
                    continue
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
            report.runs[rid] = _build_timing_run(
                job_name, run_data, version, topology, run_type, variant,
                summaries.get(rid), step_results.get(rid, {}),
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
