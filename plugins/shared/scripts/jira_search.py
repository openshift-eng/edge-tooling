#!/usr/bin/env python3
"""
Jira REST API v3 client using stdlib only (no requests dependency).

Provides Basic auth search against https://redhat.atlassian.net using
JIRA_USERNAME and JIRA_API_TOKEN environment variables.
"""

import base64
import errno
import http.client
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request

_JIRA_BASE = "https://redhat.atlassian.net"
_JIRA_API = f"{_JIRA_BASE}/rest/api/3"
_SEARCH_ATTEMPTS = 3
_SEARCH_RETRY_DELAYS = (1, 2)


class JiraSearchError(RuntimeError):
    """A Jira query failed or returned an incomplete response."""


def credentials_available() -> bool:
    """Return True if both JIRA_USERNAME and JIRA_API_TOKEN are set."""
    username = os.getenv("JIRA_USERNAME", "").strip()
    token = os.getenv("JIRA_API_TOKEN", "").strip()
    return bool(username and token)


def _retryable_transport_error(error) -> bool:
    """Recognize temporary transport failures, not HTTP or payload errors."""
    if isinstance(error, urllib.error.URLError):
        error = error.reason
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    return isinstance(error, (TimeoutError, http.client.IncompleteRead,
                              http.client.RemoteDisconnected)) or (
        isinstance(error, OSError) and error.errno in {
            errno.ECONNRESET, errno.ECONNABORTED, errno.ECONNREFUSED,
            errno.ETIMEDOUT, errno.EPIPE,
        }
    )


def _request(jql: str, fields: str, max_results: int, next_page_token=None) -> dict:
    """Retry only this read-only search POST, with three attempts per page."""
    username = os.getenv("JIRA_USERNAME", "").strip()
    token = os.getenv("JIRA_API_TOKEN", "").strip()
    if not username or not token:
        raise JiraSearchError("JIRA_USERNAME and JIRA_API_TOKEN are required")

    credentials = f"{username}:{token}"
    b64_credentials = base64.b64encode(credentials.encode()).decode()
    payload = {
        "jql": jql,
        "fields": fields.split(","),
        "maxResults": max_results,
    }
    if next_page_token:
        payload["nextPageToken"] = next_page_token

    headers = {
        "Authorization": f"Basic {b64_credentials}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    req = urllib.request.Request(
        f"{_JIRA_API}/search/jql",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    for attempt in range(1, _SEARCH_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read()
            break
        except urllib.error.HTTPError as exc:
            exc.close()
            raise JiraSearchError(f"Jira HTTP {exc.code}") from exc
        except (OSError, http.client.IncompleteRead) as exc:
            if not _retryable_transport_error(exc):
                raise JiraSearchError("Jira non-retryable transport error") from exc
            if attempt == _SEARCH_ATTEMPTS:
                raise JiraSearchError(
                    f"Jira search transport failure after {attempt} attempts") from exc
            delay = _SEARCH_RETRY_DELAYS[attempt - 1]
            print(f"Jira search transport failure (attempt {attempt}/{_SEARCH_ATTEMPTS}); "
                  f"retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)

    try:
        data = json.loads(body.decode())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise JiraSearchError("Invalid Jira response encoding or JSON") from exc

    if not isinstance(data, dict) or not isinstance(data.get("issues"), list):
        raise JiraSearchError("Jira response missing issues array")
    return data


def search(jql: str, fields: str = "summary,status,assignee,updated", max_results: int = 5) -> list[dict]:
    """
    Execute a JQL search and return the issues list.

    Args:
        jql: JQL query string
        fields: Comma-separated field names to retrieve
        max_results: Maximum number of results to return

    Returns one bounded page. Raises JiraSearchError on any failure.
    """
    return _request(jql, fields, max_results)["issues"]


def search_all(jql: str, fields: str = "summary,status,assignee,updated",
               page_size: int = 50, max_pages: int = 200,
               max_issues: int = 10000) -> list[dict]:
    """Return a complete enhanced-search result using nextPageToken."""
    issues = []
    token = None
    seen_tokens = set()
    for _ in range(max_pages):
        data = _request(jql, fields, page_size, token)
        issues.extend(data["issues"])
        if len(issues) > max_issues:
            raise JiraSearchError(f"Jira result exceeded {max_issues} issues")
        if data.get("isLast") is True:
            return issues
        next_token = data.get("nextPageToken")
        if not next_token:
            raise JiraSearchError("Jira paginated response missing nextPageToken")
        if next_token in seen_tokens:
            raise JiraSearchError("Jira pagination repeated nextPageToken")
        seen_tokens.add(next_token)
        token = next_token
    raise JiraSearchError(f"Jira result exceeded {max_pages} pages")
