"""OCPBUGS commit scanning and Jira enrichment for MicroShift bugs.

Extracts OCPBUGS references from git commit messages and enriches them
with Jira data (status, labels, release action) when ATLASSIAN_EMAIL
and ATLASSIAN_API_TOKEN are set. Falls back to unenriched output when
credentials are unavailable.
"""

import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from lib.git_ops import ensure_microshift_repo, build_revision_range

logger = logging.getLogger(__name__)

_OCPBUGS_RE = re.compile(r"OCPBUGS-\d+")
_JIRA_BASE = "https://issues.redhat.com"
_RESOLVED_STATUSES = {"MODIFIED", "ON_QA", "Verified", "Closed"}


def _jira_auth():
    """Return (email, token) for Jira basic auth, or None."""
    email = os.environ.get("ATLASSIAN_EMAIL", "").strip()
    token = os.environ.get("ATLASSIAN_API_TOKEN", "").strip()
    if email and token:
        return (email, token)
    return None


def _fetch_one_bug(key, auth):
    """Fetch a single OCPBUGS issue from Jira REST API.

    Returns:
        dict with key/summary/status/labels/issuetype/priority, or None on failure.
    """
    url = f"{_JIRA_BASE}/rest/api/2/issue/{key}"
    try:
        resp = requests.get(
            url,
            params={"fields": "summary,status,labels,issuetype,priority"},
            auth=auth,
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("Jira fetch failed for %s: %s", key, e)
        return None

    data = resp.json()
    fields = data.get("fields", {})
    status_obj = fields.get("status") or {}
    issuetype_obj = fields.get("issuetype") or {}
    priority_obj = fields.get("priority") or {}

    return {
        "key": key,
        "summary": fields.get("summary", ""),
        "status": status_obj.get("name", "unknown"),
        "labels": fields.get("labels", []),
        "issuetype": issuetype_obj.get("name", "Bug"),
        "priority": priority_obj.get("name", ""),
    }


def _fetch_bugs_from_jira(keys):
    """Fetch multiple OCPBUGS issues from Jira in parallel.

    Returns:
        dict[str, dict]: key → bug data for successfully fetched bugs.
    """
    auth = _jira_auth()
    if not auth:
        return {}

    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_fetch_one_bug, key, auth): key
            for key in keys
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                bug = future.result()
                if bug:
                    results[key] = bug
            except Exception as e:
                logger.warning("Jira fetch failed for %s: %s", key, e)

    return results


def _classify_bug(bug):
    """Determine release_action based on Jira labels.

    Returns:
        str: "release_required", "release_not_required", or "needs_review".
    """
    labels = bug.get("labels", [])
    has_required = "release-required" in labels
    has_not_required = "release-not-required" in labels

    if has_required and not has_not_required:
        return "release_required"
    if has_not_required and not has_required:
        return "release_not_required"
    return "needs_review"


def extract_bugs_from_commits(branch, since_version, since_commit=None):
    """Extract OCPBUGS references from commit messages since a version.

    Args:
        branch: Branch name, e.g., "release-4.21".
        since_version: Version string, e.g., "4.18.36", or None.
        since_commit: Git commit hash to use as range base when the
            version tag is unavailable.

    Returns:
        set[str]: Unique OCPBUGS keys found in commits, e.g., {"OCPBUGS-12345"}.
    """
    revision = build_revision_range(branch, since_version, since_commit)

    repo = ensure_microshift_repo()
    try:
        result = subprocess.run(
            ["git", "log", revision, "--format=%B"],
            cwd=repo, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.warning("git log for OCPBUGS extraction timed out")
        return set()
    if result.returncode != 0:
        logger.warning("git log for OCPBUGS extraction failed: %s", result.stderr.strip())
        return set()

    return set(_OCPBUGS_RE.findall(result.stdout))


def query_resolved_bugs(version, branch=None, since_version=None,
                        since_commit=None):
    """Scan commits for OCPBUGS references and enrich with Jira data.

    When ATLASSIAN_EMAIL and ATLASSIAN_API_TOKEN are set, fetches bug
    details from Jira and classifies release action based on labels.
    Otherwise, bugs are returned as unenriched with "needs_review".

    Args:
        version: Full version, e.g., "4.21.8".
        branch: Branch name for commit scanning, e.g., "release-4.21".
        since_version: Last released version for commit range, or None.
        since_commit: Git commit hash to use as range base when the
            version tag is unavailable.

    Returns:
        dict: {"count": int, "bugs": list[dict], "skipped": bool, "error": str|None}
    """
    commit_bug_keys = set()
    if branch:
        commit_bug_keys = extract_bugs_from_commits(
            branch, since_version, since_commit=since_commit,
        )
        if commit_bug_keys:
            logger.info("Found %d OCPBUGS references in commits: %s",
                        len(commit_bug_keys), ", ".join(sorted(commit_bug_keys)))

    jira_bugs = {}
    if commit_bug_keys and _jira_auth():
        logger.info("Enriching OCPBUGS from Jira...")
        jira_bugs = _fetch_bugs_from_jira(commit_bug_keys)
        if jira_bugs:
            logger.info("Enriched %d/%d OCPBUGS from Jira",
                        len(jira_bugs), len(commit_bug_keys))

    release_required = 0
    release_not_required = 0
    needs_review = 0

    all_bugs = []
    for key in sorted(commit_bug_keys):
        jira_data = jira_bugs.get(key)
        if jira_data:
            action = _classify_bug(jira_data)
            bug = {
                "key": key,
                "summary": jira_data["summary"],
                "status": jira_data["status"],
                "source": "commit",
                "release_note": "",
                "release_note_type": "",
                "release_note_status": "",
                "labels": jira_data["labels"],
                "release_action": action,
            }
        else:
            action = "needs_review"
            bug = {
                "key": key,
                "summary": "Pending Jira lookup",
                "status": "unknown",
                "source": "commit",
                "release_note": "",
                "release_note_type": "",
                "release_note_status": "",
                "labels": [],
                "release_action": action,
            }

        if action == "release_required":
            release_required += 1
        elif action == "release_not_required":
            release_not_required += 1
        else:
            needs_review += 1

        all_bugs.append(bug)

    return {
        "count": len(all_bugs),
        "bugs": all_bugs,
        "release_required": release_required,
        "release_not_required": release_not_required,
        "needs_review": needs_review,
        "skipped": False,
        "error": None,
    }
