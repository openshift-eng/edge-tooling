#!/usr/bin/env python3
"""Shared transformation library for edge-scrum Jira data processing."""

import json
import os
import re
import sys
from datetime import datetime, date, timedelta


# --- Status Constants ---

# "Done" is defensive: the OCPEDGE and USHIFT workflows have no Done status (law 07: Closed).
# OCPBUGS "Release Pending" is done: the fix is verified and waits for its release (team decision, 2026-11-26).
DONE_STATUSES_SPRINT = {"Done", "Closed", "Verified", "Release Pending"}
DONE_STATUSES_OCPBUGS = {"Closed", "Verified", "Release Pending"}
DONE_STATUSES_BLOCKER = {"Closed", "Verified", "Done", "Won't Fix"}
IN_PROGRESS_STATUSES = {"In Progress", "Review"}
KNOWN_ISSUE_TYPES = {"Story", "Bug", "Spike", "Task"}


def is_sprint_done(issue):
    """Done-state of a transformed sprint issue by law 07, per issue type/project.

    OCPBUGS bugs are done at Verified or Closed; every other issue type (OCPEDGE /
    USHIFT stories, tasks, spikes and bugs) is done only at Closed. This mirrors
    ``run-checks.is_story_done`` so velocity and planning agree on what "done"
    means — notably, a bare "Done" status does not count."""
    status = issue.get("status") or get_nested(issue, "status", "name") or ""
    key = issue.get("key") or ""
    if key.startswith("OCPBUGS-"):
        return status in DONE_STATUSES_OCPBUGS
    return status == "Closed"


# --- Field Extraction ---


def get_nested(obj, *keys):
    """Safely traverse nested dicts."""
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def normalize_issue_type(raw_name):
    """Map issue type to Story|Bug|Spike|Task."""
    if raw_name in KNOWN_ISSUE_TYPES:
        return raw_name
    return "Task"


def extract_issue_type(issue):
    """Get normalized issue type from an issue dict."""
    name = (
        get_nested(issue, "issue_type", "name")
        or get_nested(issue, "issuetype", "name")
        or "Task"
    )
    return normalize_issue_type(name)


def extract_sp(issue, issue_type=None):
    """Extract story points for any issue type, including Bugs (edge-context
    decision 0003: all work is pointed). Returns 0 only when the field is unset
    or unparseable. The ``issue_type`` argument is accepted for call-site
    symmetry but no longer changes the result."""
    raw = issue.get("customfield_10028")
    if raw is None:
        return 0
    try:
        if isinstance(raw, bool):
            return 0
        if isinstance(raw, dict):
            val = raw.get("value")
            if val is None:
                return 0
            val = float(val)
        else:
            val = float(raw)
        result = max(0, round(val))
        return result
    except (ValueError, TypeError):
        return 0


def _parent_issue_type(parent):
    """Issue-type name of a native 'parent' field, across MCP/REST shapes."""
    return (
        get_nested(parent, "issue_type", "name")
        or get_nested(parent, "issuetype", "name")
        or get_nested(parent, "fields", "issuetype", "name")
    )


def extract_epic_key(issue):
    """Extract the epic key for a child issue.

    Jira Cloud links issues to their epic through the native ``parent`` field
    (laws 02 and 06). Prefer it when the parent is an Epic; fall back to the
    legacy Epic Link (``customfield_10014``) otherwise, so a non-Epic parent
    (e.g. a sub-task's Story parent) does not shadow a real Epic Link and older
    data without a native parent still resolves."""
    parent = issue.get("parent")
    if isinstance(parent, dict) and _parent_issue_type(parent) == "Epic":
        key = parent.get("key")
        if key:
            return key
    raw = issue.get("customfield_10014")
    if raw is None:
        return "No Epic"
    if isinstance(raw, str):
        return raw or "No Epic"
    if isinstance(raw, dict):
        val = raw.get("key") or raw.get("value")
        return val if val else "No Epic"
    return "No Epic"


def extract_parent_key(issue):
    """Extract parent key from the native 'parent' field or customfield_10018."""
    for field in ("parent", "customfield_10018"):
        raw = issue.get(field)
        if raw is None:
            continue
        if isinstance(raw, str):
            return raw or "No Feature"
        if isinstance(raw, dict):
            val = raw.get("key") or raw.get("value")
            while isinstance(val, dict):
                val = val.get("key") or val.get("value") or None
            if val:
                return val
    return "No Feature"


def extract_flagged(issue):
    """Check if issue is flagged (impediment)."""
    raw = issue.get("customfield_10021")
    return isinstance(raw, list) and len(raw) > 0


def extract_assignee_username(issue):
    """Extract assignee identifier matching roster username format (email)."""
    assignee = issue.get("assignee")
    if assignee is None:
        return None
    if isinstance(assignee, dict):
        return assignee.get("email") or assignee.get("name")
    return None


def extract_user_username(field_value):
    """Extract a user-picker field's identifier in roster username format (email).

    Mirrors :func:`extract_assignee_username` for arbitrary user fields such as
    QA Contact (``customfield_10470``). Returns ``None`` when unset or when the
    identifier cannot be determined."""
    if field_value is None:
        return None
    if isinstance(field_value, dict):
        inner = field_value.get("value")
        if isinstance(inner, dict):
            field_value = inner
        return field_value.get("email") or field_value.get("name")
    return None


def extract_display_name(field_value, fallback="None"):
    """Extract display name from a user picker field."""
    if field_value is None:
        return fallback
    if isinstance(field_value, dict):
        if "errorMessage" in field_value:
            return "Field not configured"
        inner = field_value.get("value")
        if isinstance(inner, dict):
            field_value = inner
        return (
            field_value.get("displayName")
            or field_value.get("display_name")
            or field_value.get("name")
            or fallback
        )
    return str(field_value) if field_value else fallback


# --- Roster matching ---


def normalize_name(value):
    """Lowercase, collapse internal whitespace and strip — for case- and
    whitespace-insensitive matching of emails and display names."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def build_roster_index(roster):
    """Index roster members by normalized username/email and display name.

    Returns ``{"by_username": {...}, "by_display": {...}}`` mapping each
    normalized key to the member dict, for use by :func:`roster_match`."""
    by_username = {}
    by_display = {}
    for m in (roster or {}).get("members", []):
        u = normalize_name(m.get("username"))
        d = normalize_name(m.get("display_name"))
        if u:
            by_username[u] = m
        if d:
            by_display[d] = m
    return {"by_username": by_username, "by_display": by_display}


def roster_match(user_email, user_display_name, roster_or_index):
    """Return the roster member matching by email/username or display name, else None.

    Live Jira frequently returns users with ``email: null`` (display name only),
    so matching on the email/username alone misses them. We match on either the
    email/username **or** the display name, each compared case- and
    whitespace-insensitively (:func:`normalize_name`). ``roster_or_index`` may be a
    raw roster dict or a prebuilt :func:`build_roster_index` result (prefer the
    latter in loops)."""
    if (isinstance(roster_or_index, dict)
            and "by_username" in roster_or_index and "by_display" in roster_or_index):
        index = roster_or_index
    else:
        index = build_roster_index(roster_or_index)
    e = normalize_name(user_email)
    if e and e in index["by_username"]:
        return index["by_username"][e]
    d = normalize_name(user_display_name)
    if d and d in index["by_display"]:
        return index["by_display"][d]
    return None


def extract_blocked_by(issue, done_statuses=None):
    """Get keys of unresolved blocking issues."""
    if done_statuses is None:
        done_statuses = DONE_STATUSES_BLOCKER
    links = issue.get("issuelinks") or []
    blocked_by = []
    for link in links:
        link_type = link.get("type", {})
        if link_type.get("inward") == "is blocked by":
            inward = link.get("inward_issue") or link.get("inwardIssue")
            if inward:
                status_name = get_nested(inward, "status", "name") or ""
                if status_name not in done_statuses:
                    key = inward.get("key")
                    if key:
                        blocked_by.append(key)
    return blocked_by


def extract_tshirt_size(issue, field="customfield_10795"):
    """Extract T-shirt size from a custom field."""
    raw = issue.get(field)
    if isinstance(raw, dict):
        return raw.get("value") or "Unsized"
    if isinstance(raw, str):
        return raw or "Unsized"
    return "Unsized"


def extract_version_names(raw):
    """Normalize a version-like field (fixVersions, Target Version) to a list of names."""
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, dict):
        val = raw.get("name") or raw.get("value")
        if isinstance(val, list):
            return extract_version_names(val)
        return [val] if val else []
    if isinstance(raw, list):
        out = []
        for item in raw:
            out.extend(extract_version_names(item))
        return out
    return []


_SPRINT_STR_RE = re.compile(r"id=(\d+).*?name=([^,\]]+)")


def extract_sprints(raw):
    """Normalize the Sprint field (customfield_10020) to [{id, name, end}].

    ``end`` is the sprint's endDate (or completeDate when endDate is absent),
    normalized to ``YYYY-MM-DD`` or ``None``. compute-velocity uses it to pick the
    sprint an issue finished in by latest end date, falling back to the highest
    sprint id when the dates are unavailable.
    """
    if raw is None:
        return []
    # This Jira/MCP wraps the sprint field as {"value": [<sprint>, ...]} (same as
    # other custom fields). Unwrap it so each sprint dict is parsed individually,
    # not treated as a single id-less sprint.
    if isinstance(raw, dict) and "value" in raw:
        raw = raw.get("value")
        if raw is None:
            return []
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for item in raw:
        if isinstance(item, dict):
            sid = item.get("id")
            name = item.get("name") or ""
            end = safe_format_date(item.get("endDate") or item.get("completeDate"), None)
            out.append({"id": int(sid) if str(sid).isdigit() else sid,
                        "name": name, "end": end})
        elif isinstance(item, str):
            m = _SPRINT_STR_RE.search(item)
            if m:
                out.append({"id": int(m.group(1)), "name": m.group(2).strip(), "end": None})
            elif item:
                out.append({"id": None, "name": item, "end": None})
    return out


# --- Date Helpers ---


def parse_date(date_str):
    """Parse date string to date object."""
    if isinstance(date_str, date) and not isinstance(date_str, datetime):
        return date_str
    if isinstance(date_str, datetime):
        return date_str.date()
    s = str(date_str)
    if "T" in s:
        s = s.split("T")[0]
    return datetime.strptime(s, "%Y-%m-%d").date()


def format_date(d):
    """Format date object as YYYY-MM-DD string."""
    if isinstance(d, str):
        return parse_date(d).isoformat()
    return d.isoformat()


def safe_format_date(d, fallback):
    """Format date, returning fallback on None or parse failure."""
    if d is None:
        return fallback
    try:
        return format_date(d)
    except (ValueError, TypeError, AttributeError):
        return fallback


def days_between(start, end, inclusive=True):
    """Calendar days between two dates."""
    s = parse_date(start)
    e = parse_date(end)
    delta = (e - s).days
    return delta + 1 if inclusive else delta


def sprint_day_counts(start, end, today):
    """``(days elapsed, days remaining, total days)`` of a sprint, in calendar days.

    The single definition shared by ``transform-sprints.py`` (Claude Code) and
    ``chai_report.py`` (chai-bot). ``total`` counts the start and end days;
    ``elapsed`` counts today, clamped to ``[0, total]``; ``remaining`` is
    ``total - elapsed``, so ``elapsed + remaining == total``.
    """
    total = days_between(start, end)
    elapsed = max(0, min(days_between(start, today), total))
    return elapsed, total - elapsed, total


def business_days_between(start, end):
    """Business days (Mon-Fri) between start (exclusive) and end (inclusive)."""
    s = parse_date(start)
    e = parse_date(end)
    count = 0
    current = s
    while current < e:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count


def is_stale(updated_str, today_str, threshold_days=5):
    """True if updated is > threshold business days before today.

    An unreadable date is not stale (False), never an error: one bad issue
    must not stop a report.
    """
    try:
        return business_days_between(updated_str, today_str) > threshold_days
    except (ValueError, TypeError, AttributeError):
        return False


# --- ADF / Description Helpers ---


def extract_text_from_adf(node):
    """Recursively extract all text from ADF node."""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    parts = []
    if node.get("type") == "text":
        parts.append(node.get("text", ""))
    for child in node.get("content", []):
        parts.append(extract_text_from_adf(child))
    return "".join(parts)


def has_acceptance_criteria(description):
    """Check if description contains acceptance criteria markers."""
    if description is None:
        return False
    if isinstance(description, str):
        return _check_ac_text(description)
    if isinstance(description, dict):
        text = extract_text_from_adf(description)
        if _check_ac_text(text):
            return True
        return _check_adf_headings(description)
    return False


def _check_ac_text(text):
    lower = text.lower()
    return "acceptance criteria" in lower or "ac:" in lower


def _check_adf_headings(node):
    if isinstance(node, dict):
        if node.get("type") == "heading":
            text = extract_text_from_adf(node)
            if "acceptance" in text.lower():
                return True
        for child in node.get("content", []):
            if _check_adf_headings(child):
                return True
    return False


# --- Sprint Helpers ---


def extract_sprint_number(sprint_name):
    """Extract sprint number from name like 'OCPEDGE Sprint 287'."""
    match = re.search(r"(\d+)\s*$", sprint_name.strip())
    if match:
        return int(match.group(1))
    return None


# --- I/O Helpers ---


def load_json(path):
    """Load JSON from file path or stdin (-)."""
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r") as f:
        return json.load(f)


def unwrap_mcp_response(data):
    """Unwrap MCP tool response wrapper if present."""
    if isinstance(data, dict) and "result" in data:
        result = data["result"]
        if isinstance(result, str):
            return json.loads(result)
        return result
    return data


def load_issues(paths):
    """Load and merge issues from one or more raw MCP jira_search response files."""
    all_issues = []
    for path in paths:
        data = unwrap_mcp_response(load_json(path))
        if isinstance(data, list):
            all_issues.extend(data)
        elif isinstance(data, dict):
            all_issues.extend(data.get("issues", []))
    return all_issues


def load_sprints(paths):
    """Load and merge sprints from raw MCP jira_get_sprints_from_board response files."""
    all_sprints = []
    for path in paths:
        data = unwrap_mcp_response(load_json(path))
        if isinstance(data, list):
            all_sprints.extend(data)
        elif isinstance(data, dict):
            all_sprints.extend(data.get("values", []))
    return all_sprints


def write_output(data, path):
    """Write JSON to file, creating parent dirs."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Wrote {path}", file=sys.stderr)
