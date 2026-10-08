#!/usr/bin/env python3
"""
Jira REST API v3 client using stdlib only (no requests dependency).

Provides Basic auth search against https://redhat.atlassian.net using
JIRA_USERNAME and JIRA_API_TOKEN environment variables.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional


_JIRA_BASE = "https://redhat.atlassian.net"
_JIRA_API = f"{_JIRA_BASE}/rest/api/3"


def credentials_available() -> bool:
    """Return True if both JIRA_USERNAME and JIRA_API_TOKEN are set."""
    username = os.getenv("JIRA_USERNAME", "").strip()
    token = os.getenv("JIRA_API_TOKEN", "").strip()
    return bool(username and token)


def search(jql: str, fields: str = "summary,status,assignee,updated", max_results: int = 5) -> Optional[list[dict]]:
    """
    Execute a JQL search and return the issues list.

    Args:
        jql: JQL query string
        fields: Comma-separated field names to retrieve
        max_results: Maximum number of results to return

    Returns:
        List of issue dicts from the 'issues' response key, or None on any error
        (missing credentials, HTTP 401, network failure, etc.)

    Uses HTTP Basic auth with JIRA_USERNAME:JIRA_API_TOKEN.
    Gracefully returns None on any failure for deterministic degradation.
    """
    username = os.getenv("JIRA_USERNAME", "").strip()
    token = os.getenv("JIRA_API_TOKEN", "").strip()

    if not username or not token:
        return None

    # Build Basic auth header
    credentials = f"{username}:{token}"
    b64_credentials = base64.b64encode(credentials.encode()).decode()

    # Build request payload
    payload = {
        "jql": jql,
        "fields": fields.split(","),
        "maxResults": max_results,
    }

    headers = {
        "Authorization": f"Basic {b64_credentials}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        req = urllib.request.Request(
            f"{_JIRA_API}/search/jql",
            data=json.dumps(payload).encode(),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            return data.get("issues", [])

    except urllib.error.HTTPError as e:
        print(f"[jira_search] HTTP {e.code} error: {e.reason}", file=sys.stderr)
        if e.code == 401:
            # Authentication failed
            return None
        # Other HTTP errors
        return None
    except urllib.error.URLError as e:
        print(f"[jira_search] Network error: {e.reason}", file=sys.stderr)
        return None
    except (OSError, json.JSONDecodeError, KeyError) as e:
        print(f"[jira_search] Error: {e}", file=sys.stderr)
        return None
