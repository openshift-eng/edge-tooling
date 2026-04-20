"""Search JIRA for existing bugs related to edge topology failures."""

from __future__ import annotations

import logging
import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

from ..config import Config
from .http import create_session
from ..models import JiraBug, JobRun, SuggestedBug

logger = logging.getLogger(__name__)

JIRA_BASE = "https://redhat.atlassian.net"
SEARCH_URL = f"{JIRA_BASE}/rest/api/3/search"

_session = create_session()


def _get_auth() -> Optional[tuple[str, str]]:
    """Return (username, token) for Basic auth, or None if Bearer should be used."""
    username = os.environ.get("JIRA_USERNAME")
    token = os.environ.get("JIRA_TOKEN")
    if username and token:
        return (username, token)
    return None


def _get_headers() -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get("JIRA_TOKEN")
    if token and not os.environ.get("JIRA_USERNAME"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def has_auth() -> bool:
    return bool(os.environ.get("JIRA_TOKEN"))


def search_bugs(
    job_name: str,
    config: Config,
    topology: Optional[str] = None,
    max_results: int = 5,
) -> list[JiraBug]:
    """Search JIRA for bugs matching a failing job name."""
    if not has_auth():
        logger.debug("JIRA_TOKEN not set, skipping JIRA search")
        return []

    # Search by job name in summary or description
    # Escape special JQL characters
    escaped = job_name.replace('\\', '\\\\').replace('"', '\\"')
    component = config.jira_component_for(topology) if topology else ""
    component_clause = f'AND component = "{component}" ' if component else ""
    jql = (
        f'project = {config.jira_project} '
        f'{component_clause}'
        f'AND (summary ~ "{escaped}" OR description ~ "{escaped}") '
        f'AND status not in (Closed, "Release Pending") '
        f'ORDER BY updated DESC'
    )

    resp = _session.get(
        SEARCH_URL,
        params={"jql": jql, "maxResults": max_results, "fields": "summary,status,assignee,priority,components"},
        headers=_get_headers(),
        auth=_get_auth(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    bugs = []
    for issue in data.get("issues", []):
        key = issue.get("key")
        if not key:
            continue
        fields = issue.get("fields", {})
        assignee = fields.get("assignee") or {}
        status = fields.get("status") or {}
        priority = fields.get("priority") or {}
        components = fields.get("components", [])

        bugs.append(JiraBug(
            key=key,
            summary=fields.get("summary", ""),
            status=status.get("name", ""),
            assignee=assignee.get("displayName", "Unassigned"),
            priority=priority.get("name", ""),
            url=f"{JIRA_BASE}/browse/{key}",
            component=components[0].get("name", "") if components else "",
        ))

    return bugs


def search_bugs_for_jobs(
    failing_jobs: list[JobRun],
    config: Config,
    max_workers: int = 4,
) -> tuple[dict[str, list[JiraBug]], list[str]]:
    """Search JIRA for bugs matching each failing job.

    Returns (results, errors) where results maps job name -> list of
    matching bugs and errors is a list of human-readable error strings
    for jobs whose JIRA searches failed.
    """
    if not has_auth():
        logger.info("JIRA_TOKEN not set — JIRA bug matching disabled")
        return {}, []

    results = {}
    errors: list[str] = []

    def _search(job: JobRun) -> tuple[str, list[JiraBug]]:
        return job.name, search_bugs(job.name, config, topology=job.topology)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_search, job): job for job in failing_jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                name, bugs = future.result()
            except (requests.RequestException, ValueError) as e:
                msg = f"JIRA search failed for {job.name}: {e}"
                logger.warning(msg)
                errors.append(msg)
                continue
            if bugs:
                logger.info(f"  Found {len(bugs)} JIRA bugs for {name}")
                results[name] = bugs

    return results, errors


def create_bug_url(
    title: str,
    description: str,
    config: Config,
    component: str = "",
) -> str:
    """Generate a JIRA create-issue URL with pre-populated fields."""
    params = {
        "project.key": config.jira_project,
        "issuetype.name": "Bug",
        "summary": title,
        "description": description,
    }
    if component:
        params["component"] = component
    return f"{JIRA_BASE}/secure/CreateIssue!default.jspa?{urllib.parse.urlencode(params)}"


def suggest_bug(
    job: JobRun,
    versions: list[str],
    config: Config,
    component: str = "",
) -> SuggestedBug:
    """Generate a suggested JIRA bug for an unmatched failing job."""
    failing_test_names = [t.name for t in job.failing_tests[:5]]

    versions_str = ", ".join(versions)
    title = f"[{job.topology}] {job.name} failing in {versions_str} nightly"

    # Short description for URL (avoids browser URL length limits)
    short_lines = [
        f"*Job*: [{job.name}|{job.prow_url}]",
        f"*Topology*: {job.topology}",
        f"*Versions*: {versions_str}",
        f"*Job Type*: {job.job_type.value}",
    ]
    short_description = "\n".join(short_lines)

    # Full description for clipboard copy
    full_lines = list(short_lines)
    full_lines.append("")
    full_lines.append("*Failing Tests*:")
    for t in job.failing_tests[:5]:
        full_lines.append(f"- {t.name}")
        if t.error_message:
            err = t.error_message[:200].replace("\n", " ")
            full_lines.append(f"  {{noformat}}{err}{{noformat}}")

    if job.error_summary:
        full_lines.extend(["", f"*Error Summary*: {job.error_summary}"])

    full_description = "\n".join(full_lines)

    return SuggestedBug(
        title=title,
        description=short_description,
        job_name=job.name,
        topology=job.topology or "",
        versions=versions,
        failing_tests=failing_test_names,
        create_url=create_bug_url(title, short_description, config, component=component),
        prow_url=job.prow_url,
        full_description=full_description,
    )
