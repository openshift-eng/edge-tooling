#!/usr/bin/env python3
"""Shared infrastructure for the Jira/GitHub collectors.

This module isolates the impure edges of the plugin — the ``gh`` command
transport with pagination and retry, and date/quarter parsing — so the domain
modules (workstream_map, load_context, metrics, render) stay pure and trivially
testable. The command runner is injected, which keeps unit tests hermetic: no
live network, no third-party mocking library.

Jira data is collected by the skill via the ``mcp-atlassian`` MCP tools, not by
a client in this module — see ``attribute_jira_activities.py`` and
``attribute_github_activities.py`` for the pure attribution logic that consumes it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple, Union

# --- Jira collection constants ----------------------------------------------

# Custom field IDs
QA_CONTACT_FIELD = "customfield_10470"
SME_FIELD = "customfield_10475"

# Projects searched for assignee and QA-contact activity
ACTIVITY_PROJECTS: Tuple[str, ...] = ("OCPEDGE", "USHIFT", "OCPBUGS")

# Unattributed reasons (different problems require different fixes)
UNATTRIBUTED_NO_PARENT = "no_component_no_parent"
UNATTRIBUTED_PARENT_EMPTY = "parent_also_empty"

_DEFAULT_COMMAND_MAX_RETRIES = 4
_DEFAULT_COMMAND_BACKOFF_SECONDS = 5.0
_DEFAULT_COMMAND_MAX_BACKOFF_SECONDS = 60.0
_QUARTER_PATTERN = re.compile(r"^(?P<year>\d{4})Q(?P<quarter>[1-4])$")
_QUARTER_MONTHS = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
_LAST_DAY_OF_MONTH = {1: 31, 3: 31, 6: 30, 9: 30, 10: 31, 12: 31}


def activity_payload(
    activities: List[dict], **counters: Union[int, Dict[str, int]]
) -> Dict[str, object]:
    """Wrap collected activity records with any collector-level counters.

    Some data-quality signals cannot live on a record, because the thing being
    counted never produced one — a PR dropped for being in a personal namespace,
    for example. ``counters`` carries those out of the collector so ``report.py``
    can show a real number instead of assuming zero.

    A counter is either a plain total or a ``{label: count}`` breakdown. Both
    merge across files in ``report._load_activities``: totals add, breakdowns
    merge key by key.
    """
    return {"activities": activities, "collector_meta": dict(counters)}


class CollectorError(Exception):
    """A collector could not complete because of an external failure."""


@dataclass(frozen=True)
class Window:
    """An inclusive date range for a reporting period."""

    start: date
    end: date

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def contains_timestamp(self, timestamp: str) -> bool:
        """Return whether a Jira ISO timestamp falls within the window."""
        return self.contains(_date_from_timestamp(timestamp))


def parse_date(text: str) -> date:
    """Parse an ISO ``YYYY-MM-DD`` date string."""
    return date.fromisoformat(text)


def _date_from_timestamp(timestamp: str) -> date:
    """Parse the date portion of a Jira timestamp (``2026-05-01T12:00:...``)."""
    return date.fromisoformat(timestamp[:10])


def quarter_to_window(quarter: str) -> Window:
    """Convert a ``YYYYQn`` label into its inclusive calendar-quarter window.

    Returns the full calendar quarter without date capping. Use ``resolve_window``
    for policy-aware windowing (e.g., capping in-progress quarters at today).

    Raises ``ValueError`` for anything that is not a well-formed quarter.
    """
    match = _QUARTER_PATTERN.match(quarter)
    if not match:
        raise ValueError(f"not a valid quarter label: {quarter!r} (expected e.g. 2026Q2)")
    year = int(match.group("year"))
    first_month, last_month = _QUARTER_MONTHS[int(match.group("quarter"))]
    return Window(
        start=date(year, first_month, 1),
        end=date(year, last_month, _LAST_DAY_OF_MONTH[last_month]),
    )


def resolve_window(
    quarter: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    today: Optional[date] = None,
) -> Window:
    """Resolve a reporting window from a quarter label or an explicit date pair.

    For quarter-based windows, applies policy:
    - Fully past quarters are returned as-is.
    - In-progress quarters have the end date capped at today (to avoid future
      dates that break GitHub search).
    - Future quarters raise ``ValueError`` (meaningless for reporting).

    Raises ``ValueError`` when neither a quarter nor a complete date range is
    supplied, or when the quarter has not yet started.
    """
    if quarter:
        window = quarter_to_window(quarter)
        if today is None:
            today = date.today()
        if window.start > today:
            raise ValueError(
                f"quarter {quarter} has not started yet (begins {window.start.isoformat()})"
            )
        if window.end > today:
            # In-progress quarter: cap at today
            return Window(window.start, today)
        # Fully past quarter: return as-is
        return window
    if from_date and to_date:
        return Window(parse_date(from_date), parse_date(to_date))
    raise ValueError("provide a quarter, or both a start and end date")


@dataclass(frozen=True)
class CommandResult:
    """The outcome of running an external command (e.g. the ``gh`` CLI)."""

    returncode: int
    stdout: str
    stderr: str


# A runner takes an argv list and returns its CommandResult.
CommandRunner = Callable[[List[str]], CommandResult]


def is_rate_limit_failure(result: CommandResult) -> bool:
    """Return whether a command failed because GitHub rate-limited it."""
    if result.returncode == 0:
        return False
    message = f"{result.stdout}\n{result.stderr}".lower()
    return any(
        marker in message
        for marker in (
            "rate limit",
            "rate_limit",
            "abuse detection",
            "secondary rate",
        )
    )


def _retry_after_seconds(message: str) -> Optional[float]:
    """Extract a Retry-After value when the command reports one."""
    match = re.search(r"retry-after\s*[:=]\s*(\d+(?:\.\d+)?)", message, re.IGNORECASE)
    return float(match.group(1)) if match else None


def run_with_rate_limit_retry(
    runner: CommandRunner,
    command: List[str],
    sleep: Callable[[float], None] = time.sleep,
    max_retries: int = _DEFAULT_COMMAND_MAX_RETRIES,
    backoff_seconds: float = _DEFAULT_COMMAND_BACKOFF_SECONDS,
    max_backoff_seconds: float = _DEFAULT_COMMAND_MAX_BACKOFF_SECONDS,
) -> CommandResult:
    """Run a command, retrying rate-limit failures with bounded backoff.

    ``gh`` does not consistently expose GitHub's reset headers, so the default
    is exponential backoff (5, 10, 20, 40 seconds), capped at 60 seconds.
    When a ``Retry-After`` value is present, it is honored up to the cap.
    Other command failures are returned immediately to preserve their original
    error handling.
    """
    retries = 0
    while True:
        result = runner(command)
        if not is_rate_limit_failure(result) or retries >= max_retries:
            return result
        message = f"{result.stdout}\n{result.stderr}"
        retry_after = _retry_after_seconds(message)
        delay = retry_after if retry_after is not None else backoff_seconds * (2**retries)
        sleep(min(delay, max_backoff_seconds))
        retries += 1


def build_default_runner() -> CommandRunner:
    """Build the production command runner backed by ``subprocess``.

    ``subprocess`` is imported lazily so unit tests, which inject a runner,
    never spawn a real process.
    """
    import subprocess

    def runner(command: List[str]) -> CommandResult:
        completed = subprocess.run(command, capture_output=True, text=True)
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)

    return runner
