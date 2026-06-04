"""ART Jira queries for release schedule.

All Jira lookups are done via MCP Atlassian OAuth. This module reads
pre-fetched ART ticket data from a JSON file (path set via ART_TICKETS_JSON
env var). When the env var is not set, functions return empty results and
callers degrade gracefully.

JSON file format — array of objects:
[
  {"key": "ART-XXXXX", "summary": "Release 4.21.18 [2026-Jun-03]",
   "status": "In Progress", "due_date": "2026-06-03"}
]
"""

import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"Release\s+(4\.\d+\.\d+)")
_DATE_RE = re.compile(r"\[(\d{4}-\w{3}-\d{2})\]")

_cached_tickets = None


def _load_tickets():
    """Load ART tickets from the JSON file specified by ART_TICKETS_JSON."""
    global _cached_tickets
    if _cached_tickets is not None:
        return _cached_tickets

    path = os.environ.get("ART_TICKETS_JSON", "").strip()
    if not path:
        _cached_tickets = []
        return _cached_tickets

    try:
        with open(path) as f:
            _cached_tickets = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to load ART tickets from %s: %s", path, e)
        _cached_tickets = []

    return _cached_tickets


def _extract_version_from_summary(summary):
    """Extract version from ART ticket summary (e.g., 'Release 4.21.8 [...]')."""
    match = _VERSION_RE.search(summary)
    return match.group(1) if match else None


def _extract_date_from_summary(summary):
    """Extract date from ART ticket summary (e.g., '[2026-Jun-03]')."""
    match = _DATE_RE.search(summary)
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1), "%Y-%b-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def _enrich_ticket(ticket):
    """Add version and normalized due_date to a raw ticket dict."""
    summary = ticket.get("summary", "")
    version = _extract_version_from_summary(summary)
    due_date = ticket.get("due_date") or _extract_date_from_summary(summary)
    return {
        "key": ticket.get("key", ""),
        "summary": summary,
        "status": ticket.get("status", ""),
        "due_date": due_date,
        "version": version,
    }


def query_art_releases_due(days_ahead=7, minor_version=None, specific_version=None):
    """Find ART release tickets from pre-fetched MCP data.

    Args:
        days_ahead: Unused (filtering done by MCP JQL query).
        minor_version: Filter to a specific minor, e.g., "4.21".
        specific_version: Filter to exact version, e.g., "4.21.8".

    Returns:
        list[dict]: Matching tickets with keys: key, summary, status, due_date, version.
    """
    tickets = _load_tickets()
    if not tickets:
        return []

    results = []
    for ticket in tickets:
        enriched = _enrich_ticket(ticket)
        version = enriched.get("version")
        if not version:
            continue

        parts = version.split(".")
        if len(parts) >= 2 and int(parts[1]) < 14:
            continue

        if specific_version and version != specific_version:
            continue
        if minor_version and not version.startswith(minor_version + "."):
            continue

        results.append(enriched)

    return results


def query_art_ecrc(version_pattern):
    """Find an ART ticket for an EC/RC version from pre-fetched data.

    Args:
        version_pattern: e.g., "4.22.0-ec.5".

    Returns:
        dict or None: {"key": "ART-14768", "status": "In Progress"} or None.
    """
    tickets = _load_tickets()
    if not tickets:
        return None

    for ticket in tickets:
        if version_pattern in ticket.get("summary", ""):
            return {
                "key": ticket.get("key", ""),
                "status": ticket.get("status", ""),
            }

    return None
