#!/usr/bin/env python3
"""Jira REST API client for edge-cve scripts."""

from __future__ import annotations

import os
import sys
from typing import Any

import requests

DEFAULT_BASE_URL = "https://redhat.atlassian.net"
DEFAULT_JQL = 'filter = "All Open CVEs" AND filter = "All Open Black CVEs"'

DEFAULT_FIELDS = [
    "summary",
    "status",
    "components",
    "versions",
    "fixVersions",
    "labels",
    "description",
    "issuetype",
    "priority",
    "assignee",
    "created",
    "updated",
    "security",
]


class JiraConfigError(RuntimeError):
    """Raised when required Jira environment variables are missing."""


def load_config() -> dict[str, str]:
    """Load Jira credentials from environment."""
    base_url = os.environ.get("JIRA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    email = os.environ.get("JIRA_EMAIL") or os.environ.get("JIRA_USERNAME", "")
    token = os.environ.get("JIRA_API_TOKEN", "")

    missing = []
    if not email:
        missing.append("JIRA_EMAIL or JIRA_USERNAME")
    if not token:
        missing.append("JIRA_API_TOKEN")

    if missing:
        raise JiraConfigError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + "\nSet JIRA_BASE_URL, JIRA_EMAIL (or JIRA_USERNAME), and JIRA_API_TOKEN."
        )

    return {"base_url": base_url, "email": email, "token": token}


def search_jql(
    jql: str,
    *,
    fields: list[str] | None = None,
    max_results: int = 100,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Execute a JQL query with pagination via /rest/api/3/search/jql."""
    cfg = load_config()
    fields = fields or DEFAULT_FIELDS
    sess = session or requests.Session()
    sess.auth = (cfg["email"], cfg["token"])
    sess.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    url = f"{cfg['base_url']}/rest/api/3/search/jql"
    issues: list[dict[str, Any]] = []
    next_page_token: str | None = None

    while True:
        payload: dict[str, Any] = {
            "jql": jql,
            "maxResults": max_results,
            "fields": fields,
        }
        if next_page_token:
            payload["nextPageToken"] = next_page_token

        try:
            resp = sess.post(url, json=payload, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Jira search failed: {exc}") from exc

        data = resp.json()
        batch = data.get("issues", [])
        issues.extend(batch)

        if data.get("isLast", True):
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    return issues


def normalize_issue(raw: dict[str, Any], *, base_url: str | None = None) -> dict[str, Any]:
    """Flatten a Jira API issue into a stable dict for downstream scripts."""
    if base_url is None:
        base_url = load_config()["base_url"]
    fields = raw.get("fields", {})

    def names(items: list | None) -> list[str]:
        if not items:
            return []
        out = []
        for item in items:
            if isinstance(item, dict):
                out.append(item.get("name", ""))
            else:
                out.append(str(item))
        return [n for n in out if n]

    status = fields.get("status", {})
    priority = fields.get("priority", {})
    assignee = fields.get("assignee") or {}
    issue_type = fields.get("issuetype", {})
    security = fields.get("security") or {}

    description = fields.get("description")
    if isinstance(description, dict):
        description = _adf_to_text(description)
    elif description is None:
        description = ""

    return {
        "key": raw.get("key", ""),
        "summary": fields.get("summary", ""),
        "status": status.get("name", ""),
        "priority": priority.get("name", ""),
        "issue_type": issue_type.get("name", ""),
        "components": names(fields.get("components")),
        "affected_versions": names(fields.get("versions")),
        "fix_versions": names(fields.get("fixVersions")),
        "labels": fields.get("labels", []) or [],
        "security_level": security.get("name", ""),
        "description": description,
        "assignee": assignee.get("displayName", "Unassigned"),
        "created": (fields.get("created") or "")[:10],
        "updated": (fields.get("updated") or "")[:10],
        "url": f"{base_url}/browse/{raw.get('key', '')}",
    }


def _adf_to_text(node: dict[str, Any]) -> str:
    """Convert Atlassian Document Format to plain text (best effort)."""
    parts: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            for child in item.get("content", []):
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(node)
    return "\n".join("".join(parts).splitlines())


def die_config_error() -> None:
    """Print configuration help and exit."""
    try:
        load_config()
    except JiraConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
