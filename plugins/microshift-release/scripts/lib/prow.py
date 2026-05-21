"""Prow CI and GCS operations for MicroShift release testing.

Provides version parsing, PR lookup, GCS job listing, and parallel
build status fetching for the 6 release testing CI jobs.
"""

import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logger = logging.getLogger(__name__)

GH_REPO = "openshift/microshift"
GCS_API = "https://storage.googleapis.com/storage/v1/b/test-platform-results/o"
GCS_BASE = "https://storage.googleapis.com/test-platform-results"
GCS_PR_PREFIX = "pr-logs/pull/openshift_microshift"
PROW_VIEW = "https://prow.ci.openshift.org/view/gs/test-platform-results"
S3_BUCKET = "s3://release-testing-results/microshift"
S3_BUILD_CACHE = "s3://microshift-build-cache-us-west-2"

_CI_JOBS_421 = [
    "e2e-aws-tests-bootc-release",
    "e2e-aws-tests-bootc-release-arm",
    "e2e-aws-tests-release",
    "e2e-aws-tests-release-arm",
]

_CI_JOBS_422 = [
    "e2e-aws-tests-release",
    "e2e-aws-tests-release-arm",
    "e2e-aws-tests-bootc-release-el9",
    "e2e-aws-tests-bootc-release-el10",
    "e2e-aws-tests-bootc-release-arm-el9",
    "e2e-aws-tests-bootc-release-arm-el10",
]


def ci_jobs(minor):
    """Return the CI job list for a given minor version string (e.g. '4.21')."""
    minor_num = int(minor.split(".")[1])
    if minor_num <= 21:
        return _CI_JOBS_421
    return _CI_JOBS_422


_VERSION_RE = re.compile(r"^(4)\.(\d+)\.(\d+)(?:-(ec|rc)\.(\d+))?$")
_HTTP_TIMEOUT = 60
_HTTP_RETRIES = 3
_GH_ACTIVE_STATES = frozenset({"PENDING", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"})


def parse_version(version_str):
    """Validate and parse a MicroShift version string.

    Args:
        version_str: e.g. "4.21.3", "4.22.0-rc.1", "4.22.0-ec.5".

    Returns:
        dict with keys: version, minor, branch, pr_title, release_type, ecrc_num.

    Raises:
        ValueError: If the version is invalid, <4.21, or nightly.
    """
    version = version_str.replace("~", "-")

    if "nightly" in version.lower():
        raise ValueError("Phase 2 does not apply to nightly versions.")

    match = _VERSION_RE.match(version)
    if not match:
        raise ValueError(
            f"Invalid version format: {version}. "
            "Expected X.Y.Z, X.Y.Z-rc.N, or X.Y.Z-ec.N."
        )

    minor_num = int(match.group(2))
    if minor_num < 21:
        raise ValueError(
            f"Version 4.{minor_num} uses Jenkins pipelines (USHIFT-6805), "
            "not Prow CI. This skill only supports 4.21+."
        )

    minor = f"4.{minor_num}"
    branch = f"release-{minor}"
    pr_title = f"[{branch}] Release Testing {version}"

    release_type_tag = match.group(4)
    ecrc_num = int(match.group(5)) if match.group(5) else None
    if release_type_tag == "ec":
        release_type = "EC"
    elif release_type_tag == "rc":
        release_type = "RC"
    else:
        release_type = "Z"

    return {
        "version": version,
        "minor": minor,
        "branch": branch,
        "pr_title": pr_title,
        "release_type": release_type,
        "ecrc_num": ecrc_num,
    }


def find_release_testing_pr(pr_title):
    """Find an open release testing PR by title.

    Args:
        pr_title: Exact PR title to match.

    Returns:
        dict with number, title, url, headRefName — or None if not found.
        If multiple matches, returns a list.
    """
    result = subprocess.run(
        [
            "gh", "pr", "list",
            "--repo", GH_REPO,
            "--state", "open",
            "--limit", "100",
            "--json", "number,title,url,headRefName",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {result.stderr.strip()}")

    prs = json.loads(result.stdout)
    matches = [pr for pr in prs if pr["title"] == pr_title]

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return matches


def find_pr_any_state(pr_title):
    """Find a release testing PR by title in any state (open, merged, closed).

    Args:
        pr_title: Exact PR title to match.

    Returns:
        dict with number, title, url, state, mergedAt — or None if not found.
    """
    result = subprocess.run(
        [
            "gh", "pr", "list",
            "--repo", GH_REPO,
            "--state", "all",
            "--limit", "100",
            "--json", "number,title,url,state,mergedAt",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {result.stderr.strip()}")

    prs = json.loads(result.stdout)
    matches = [pr for pr in prs if pr["title"] == pr_title]

    if not matches:
        return None
    return matches[0]


def _http_get(url):
    """GET with retries and timeout."""
    for attempt in range(_HTTP_RETRIES):
        try:
            return requests.get(url, timeout=_HTTP_TIMEOUT)
        except requests.RequestException:
            if attempt < _HTTP_RETRIES - 1:
                logger.debug("HTTP GET %s attempt %d failed, retrying", url, attempt + 1)
    return None


def list_pr_jobs(pr_number):
    """List GCS job directories for a PR.

    Args:
        pr_number: PR number (int or str).

    Returns:
        list[str]: Full GCS job names (without trailing slash).
    """
    url = f"{GCS_API}?prefix={GCS_PR_PREFIX}/{pr_number}/&delimiter=/"
    resp = _http_get(url)
    if resp is None or resp.status_code != 200:
        logger.warning("GCS API returned %s for PR %s", resp.status_code if resp else "None", pr_number)
        return []

    data = resp.json()
    prefixes = data.get("prefixes", [])
    prefix_to_strip = f"{GCS_PR_PREFIX}/{pr_number}/"
    return [p.replace(prefix_to_strip, "").rstrip("/") for p in prefixes]


def match_ci_jobs(gcs_jobs, minor):
    """Match GCS job names to the expected CI job short names.

    Args:
        gcs_jobs: List of full GCS job names.
        minor: Minor version string (e.g. '4.21').

    Returns:
        dict: Maps short CI job name to full GCS job name, or None if unmatched.
    """
    matched = {}
    for short in ci_jobs(minor):
        full = next((j for j in gcs_jobs if j.endswith(short)), None)
        matched[short] = full
    return matched


def get_latest_build(pr_number, full_job_name):
    """Fetch the latest build status for a job from GCS.

    Args:
        pr_number: PR number.
        full_job_name: Full GCS job name.

    Returns:
        dict with keys: job, status, url, build_id.
    """
    base = f"{GCS_BASE}/{GCS_PR_PREFIX}/{pr_number}/{full_job_name}"

    try:
        resp = _http_get(f"{base}/latest-build.txt")
    except requests.RequestException as e:
        logger.warning("Failed to fetch latest-build.txt for %s: %s", full_job_name, e)
        return {"job": full_job_name, "status": "ERROR", "url": None, "build_id": None}

    if resp is None or resp.status_code != 200:
        return {"job": full_job_name, "status": "ERROR", "url": None, "build_id": None}

    build_id = resp.text.strip()
    if not build_id or "<" in build_id:
        return {"job": full_job_name, "status": "ERROR", "url": None, "build_id": None}

    prow_url = f"{PROW_VIEW}/{GCS_PR_PREFIX}/{pr_number}/{full_job_name}/{build_id}"

    try:
        finished_resp = _http_get(f"{base}/{build_id}/finished.json")
    except requests.RequestException:
        return {"job": full_job_name, "status": "PENDING", "url": prow_url, "build_id": build_id}

    if (
        finished_resp is None
        or finished_resp.status_code != 200
        or "NoSuchKey" in finished_resp.text
        or "<" in finished_resp.text
    ):
        return {"job": full_job_name, "status": "PENDING", "url": prow_url, "build_id": build_id}

    try:
        result = finished_resp.json().get("result", "UNKNOWN")
    except (json.JSONDecodeError, AttributeError):
        result = "UNKNOWN"

    return {"job": full_job_name, "status": result, "url": prow_url, "build_id": build_id}


def get_pr_check_statuses(pr_number, minor):
    """Get current check run statuses from GitHub for a PR.

    Uses ``gh pr checks`` to get real-time CI status, which is
    authoritative for whether a job is currently running — GCS
    artifacts may lag behind when a new build has just started.

    Args:
        pr_number: PR number (int or str).
        minor: Minor version string (e.g. '4.21').

    Returns:
        dict: Maps short CI job name to GitHub check state string
              (e.g. "IN_PROGRESS", "COMPLETED").
    """
    result = subprocess.run(
        [
            "gh", "pr", "checks", str(pr_number),
            "--repo", GH_REPO,
            "--json", "name,state",
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning("gh pr checks failed: %s", result.stderr.strip())
        return {}

    try:
        checks = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("Failed to parse gh pr checks output")
        return {}

    status_map = {}
    for check in checks:
        name = check.get("name", "")
        state = check.get("state", "")
        for short in ci_jobs(minor):
            if name.endswith(short):
                status_map[short] = state
                break

    return status_map


def fetch_all_job_statuses(pr_number, matched_jobs, minor):
    """Fetch build statuses for all matched jobs in parallel.

    Args:
        pr_number: PR number.
        matched_jobs: dict from match_ci_jobs (short name -> full name or None).
        minor: Minor version string (e.g. '4.21').

    Returns:
        list[dict]: One entry per job, in order. Unmatched jobs get status "--".
    """
    results = {}

    jobs_to_fetch = {short: full for short, full in matched_jobs.items() if full}

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            pool.submit(get_latest_build, pr_number, full): short
            for short, full in jobs_to_fetch.items()
        }
        for future in as_completed(futures):
            short = futures[future]
            try:
                results[short] = future.result()
            except Exception as e:
                logger.warning("Error fetching %s: %s", short, e)
                results[short] = {
                    "job": matched_jobs[short],
                    "status": "ERROR",
                    "url": None,
                    "build_id": None,
                }

    check_statuses = get_pr_check_statuses(pr_number, minor)

    ordered = []
    for short in ci_jobs(minor):
        if short in results:
            entry = results[short]
            entry["short_name"] = short
        else:
            entry = {
                "short_name": short,
                "job": None,
                "status": "--",
                "url": None,
                "build_id": None,
            }

        gh_state = check_statuses.get(short)
        if gh_state in _GH_ACTIVE_STATES:
            entry["status"] = "PENDING"

        ordered.append(entry)

    return ordered
