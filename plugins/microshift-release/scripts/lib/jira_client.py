"""Jira Cloud REST API client for MicroShift CVE discovery.

Authenticates via JIRA_API_TOKEN + JIRA_USERNAME env vars (PAT).
Degrades gracefully if credentials are not set.
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

_JIRA_BASE = "https://redhat.atlassian.net"
_JIRA_API = f"{_JIRA_BASE}/rest/api/3"

_CVE_LABEL_RE = re.compile(r"^CVE-\d{4}-\d+$")
_PSCOMP_MICROSHIFT_RE = re.compile(r"pscomponent:.*microshift", re.IGNORECASE)


def _get_auth():
    """Return (email, token) tuple or None if not configured."""
    token = os.environ.get("JIRA_API_TOKEN", "").strip()
    user = os.environ.get("JIRA_USERNAME", "").strip()
    if not token or not user:
        return None
    return (user, token)


def _jira_search(jql, fields="summary,status,resolution,labels", max_results=50):
    """Run a JQL search against Jira Cloud.

    Returns:
        list[dict] of issues, or None on failure.
    """
    auth = _get_auth()
    if auth is None:
        return None

    try:
        resp = requests.post(
            f"{_JIRA_API}/search/jql",
            json={
                "jql": jql,
                "fields": fields.split(","),
                "maxResults": max_results,
            },
            auth=auth,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code == 401:
            logger.warning("Jira auth failed (HTTP 401) — check JIRA_API_TOKEN")
            return None
        resp.raise_for_status()
        data = resp.json()
        issues = data.get("issues", [])
        total = data.get("total", len(issues))
        if total > max_results:
            logger.warning("Jira search returned %d of %d results — "
                           "some may be missing", len(issues), total)
        return issues
    except requests.RequestException as exc:
        logger.warning("Jira search failed: %s", exc)
        return None


def enrich_ocpbugs(keys):
    """Fetch Jira details for OCPBUGS keys and return enrichment data.

    Args:
        keys: list of OCPBUGS key strings, e.g., ["OCPBUGS-12345"].

    Returns:
        dict mapping key to fields dict, or None if Jira unavailable.
    """
    if not keys:
        return {}
    if _get_auth() is None:
        return None

    jql = f'key in ({",".join(keys)})'
    issues = _jira_search(
        jql, fields="summary,status,resolution,labels,issuetype,priority"
    )
    if issues is None:
        return None

    result = {}
    for issue in issues:
        key = issue.get("key", "")
        fields = issue.get("fields", {})
        labels = [lbl for lbl in fields.get("labels", [])
                  if isinstance(lbl, str)]

        if "release-required" in labels:
            release_action = "release_required"
        elif "release-not-required" in labels:
            release_action = "release_not_required"
        else:
            release_action = "needs_review"

        result[key] = {
            "summary": fields.get("summary", ""),
            "status": fields.get("status", {}).get("name", ""),
            "resolution": (fields.get("resolution") or {}).get("name", ""),
            "labels": labels,
            "release_action": release_action,
        }
    return result


def search_cve_tickets(cve_ids, minor=None):
    """Search Jira for OCPBUGS tickets matching CVE IDs.

    Args:
        cve_ids: list of CVE ID strings, e.g., ["CVE-2026-34986"].
        minor: minor version to filter by, e.g., "4.22". If set,
            only returns tickets whose summary contains the matching
            openshift version bracket (e.g., "[openshift-4.22]").

    Returns:
        dict mapping CVE ID to Jira ticket info, or None if unavailable.
        Each value is {"key": str, "resolution": str, "status": str}
        or None if no ticket found for that CVE.
    """
    if not cve_ids:
        return {}
    if _get_auth() is None:
        return None

    version_filter = ""
    if minor:
        version_filter = f' AND summary ~ "openshift-{minor}"'

    result = {}

    def _search_one(cve_id):
        jql = (f'summary ~ "{cve_id}" AND project = OCPBUGS'
               f'{version_filter}')
        issues = _jira_search(jql, fields="summary,status,resolution,labels",
                              max_results=10)
        return cve_id, issues

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_search_one, cve): cve
                   for cve in cve_ids}
        for future in futures:
            cve_id, issues = future.result()
            if issues is None:
                result[cve_id] = {"error": "Jira search failed"}
                continue
            if not issues:
                result[cve_id] = None
                continue
            # Only match tickets for MicroShift components
            match = None
            for issue in issues:
                fields_i = issue.get("fields", {})
                labels = fields_i.get("labels", [])
                summary = fields_i.get("summary", "")
                if (any(_PSCOMP_MICROSHIFT_RE.match(lbl) for lbl in labels)
                        and cve_id in summary):
                    match = issue
                    break
            if match is None:
                result[cve_id] = None
                continue
            fields = match.get("fields", {})
            result[cve_id] = {
                "key": match.get("key", ""),
                "resolution": (fields.get("resolution") or {}).get("name", ""),
                "status": fields.get("status", {}).get("name", ""),
            }
    return result


def find_microshift_component_cves(minor, resolved_after=None):
    """Find unshipped CVE bugs targeting MicroShift components for a minor version.

    Searches for Vulnerability issues with SecurityTracking label whose
    labels contain any pscomponent with "microshift" in the name (matched
    client-side). Returns bugs where the fix has landed (Verified, or
    Closed+Done) but not yet shipped (not Done-Errata).

    Args:
        minor: Minor version string, e.g., "4.21".
        resolved_after: ISO date string (YYYY-MM-DD). If set, only include
            bugs resolved after this date (excludes bugs shipped in prior
            MicroShift releases).

    Returns:
        list[dict]: Bug entries compatible with ocpbugs format, or None on failure.
    """
    auth = _get_auth()
    if auth is None:
        logger.info("Jira credentials not set — skipping component CVE discovery")
        return None

    date_filter = ""
    if resolved_after:
        date_filter = (f' AND (resolutiondate >= "{resolved_after}"'
                       f' OR status = Verified)')

    jql = (
        f'project = OCPBUGS AND issuetype = Vulnerability'
        f' AND labels = SecurityTracking'
        f' AND text ~ "microshift"'
        f' AND versions = "{minor}"'
        f'{date_filter}'
        f' ORDER BY created DESC'
    )
    logger.info("Searching Jira for %s MicroShift component CVEs...", minor)
    issues = _jira_search(jql)
    if issues is None:
        return None

    all_bugs = []
    seen_keys = set()

    for issue in issues:
        key = issue.get("key", "")
        if key in seen_keys:
            continue
        seen_keys.add(key)

        fields = issue.get("fields", {})
        status_obj = fields.get("status", {})
        status = status_obj.get("name", "")
        resolution_obj = fields.get("resolution") or {}
        resolution = resolution_obj.get("name", "")
        labels = fields.get("labels", [])

        # Verify at least one pscomponent label contains "microshift"
        if not any(_PSCOMP_MICROSHIFT_RE.match(lbl) for lbl in labels):
            continue

        if resolution in ("Not a Bug", "Duplicate", "Won't Do", "Done-Errata"):
            continue

        cve_id = ""
        for label in labels:
            if _CVE_LABEL_RE.match(label):
                cve_id = label
                break

        if not cve_id:
            continue

        # Cross-check: CVE ID from label must appear in summary.
        # Guards against Jira search index returning stale/wrong fields.
        summary = fields.get("summary", "")
        if cve_id not in summary:
            logger.warning("Bug %s: CVE label %s not in summary, skipping: %s",
                           key, cve_id, summary[:80])
            continue

        if resolution == "Done" or status == "Verified":
            release_action = "release_required"
        elif status in ("New", "Backlog", "In Progress", "ON_QA"):
            continue
        else:
            release_action = "needs_review"

        all_bugs.append({
            "key": key,
            "summary": fields.get("summary", ""),
            "status": status,
            "resolution": resolution,
            "source": "component-cve",
            "cve_id": cve_id,
            "release_note": "",
            "release_note_type": "",
            "release_note_status": "",
            "labels": labels,
            "release_action": release_action,
        })

    return all_bugs
