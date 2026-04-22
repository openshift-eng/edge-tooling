#!/usr/bin/env python3
"""Shared transformation library for edge-scrum Jira data processing."""

import json
import os
import re
import sys
from datetime import datetime, date, timedelta


# --- Status Constants ---

DONE_STATUSES_SPRINT = {"Done", "Closed", "Verified"}
DONE_STATUSES_BLOCKER = {"Closed", "Verified", "Done", "Won't Fix"}
IN_PROGRESS_STATUSES = {"In Progress", "Review"}
KNOWN_ISSUE_TYPES = {"Story", "Bug", "Spike", "Task"}


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
    """Extract story points. Bugs always return 0."""
    if issue_type is None:
        issue_type = extract_issue_type(issue)
    if issue_type == "Bug":
        return 0
    raw = issue.get("customfield_10028")
    if raw is None:
        return 0
    if isinstance(raw, dict):
        val = raw.get("value")
        return int(val) if val is not None else 0
    return int(float(raw))  # truncates fractional SP intentionally


def extract_epic_key(issue):
    """Extract epic key from customfield_10014."""
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
    """Extract parent link from customfield_10018 (Epic->Feature)."""
    raw = issue.get("customfield_10018")
    if raw is None:
        return "No Feature"
    if isinstance(raw, str):
        return raw or "No Feature"
    if isinstance(raw, dict):
        val = raw.get("key") or raw.get("value")
        return val if val else "No Feature"
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


def extract_display_name(field_value, fallback="None"):
    """Extract display name from a user picker field."""
    if field_value is None:
        return fallback
    if isinstance(field_value, dict):
        if "errorMessage" in field_value:
            return "Field not configured"
        return (
            field_value.get("displayName")
            or field_value.get("display_name")
            or field_value.get("name")
            or fallback
        )
    return str(field_value) if field_value else fallback


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


def days_between(start, end, inclusive=True):
    """Calendar days between two dates."""
    s = parse_date(start)
    e = parse_date(end)
    delta = (e - s).days
    return delta + 1 if inclusive else delta


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
    """True if updated is > threshold business days before today."""
    return business_days_between(updated_str, today_str) > threshold_days


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
