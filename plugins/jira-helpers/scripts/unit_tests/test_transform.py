"""Unit tests for transform-my-issues.py parsing and transformation logic."""

import sys
import os
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "bin"))

from importlib import import_module

transform = import_module("transform-my-issues")
unwrap_mcp_response = transform.unwrap_mcp_response
transform_issue = transform.transform_issue
extract_blocked_by = transform.extract_blocked_by
extract_sprint_name = transform.extract_sprint_name

TODAY = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _issue(key="TEST-1", summary="Test", status="To Do", issue_type="Story",
           sp=3, epic_key="EPIC-1", updated="2025-06-10T10:00:00.000+0000",
           links=None, sprint=None, flagged=None):
    """Build a minimal Jira issue dict for test fixtures."""
    fields = {
        "summary": summary,
        "status": {"name": status},
        "issuetype": {"name": issue_type},
        "customfield_10028": sp,
        "customfield_10014": epic_key,
        "customfield_10021": flagged,
        "updated": updated,
        "issuelinks": links or [],
        "sprint": sprint,
        "assignee": None,
    }
    return {"key": key, "fields": fields}


class TestUnwrapMcpResponse(unittest.TestCase):

    def test_bare_list(self):
        issues = [{"key": "A-1"}, {"key": "A-2"}]
        self.assertEqual(unwrap_mcp_response(issues), issues)

    def test_dict_with_issues_key(self):
        raw = {"issues": [{"key": "B-1"}]}
        self.assertEqual(unwrap_mcp_response(raw), [{"key": "B-1"}])

    def test_stringified_result_valid_json(self):
        import json
        inner = json.dumps({"issues": [{"key": "C-1"}]})
        raw = {"result": inner}
        self.assertEqual(unwrap_mcp_response(raw), [{"key": "C-1"}])

    def test_stringified_result_invalid_json(self):
        raw = {"result": "not valid json {{{"}
        result = unwrap_mcp_response(raw)
        self.assertEqual(result, [])

    def test_empty_dict(self):
        self.assertEqual(unwrap_mcp_response({}), [])

    def test_none_value(self):
        self.assertEqual(unwrap_mcp_response(None), [])

    def test_empty_list(self):
        self.assertEqual(unwrap_mcp_response([]), [])


class TestTransformIssueStoryPoints(unittest.TestCase):

    def test_sp_as_integer(self):
        result = transform_issue(_issue(sp=5), TODAY)
        self.assertEqual(result["sp"], 5)

    def test_sp_as_numeric_string(self):
        result = transform_issue(_issue(sp="3"), TODAY)
        self.assertEqual(result["sp"], 3)

    def test_sp_as_none(self):
        result = transform_issue(_issue(sp=None), TODAY)
        self.assertEqual(result["sp"], 0)

    def test_sp_as_non_numeric_string(self):
        result = transform_issue(_issue(sp="abc"), TODAY)
        self.assertEqual(result["sp"], 0)

    def test_sp_as_empty_string(self):
        result = transform_issue(_issue(sp=""), TODAY)
        self.assertEqual(result["sp"], 0)

    def test_bug_forces_sp_zero(self):
        result = transform_issue(_issue(issue_type="Bug", sp=5), TODAY)
        self.assertEqual(result["sp"], 0)

    def test_bug_with_none_sp(self):
        result = transform_issue(_issue(issue_type="Bug", sp=None), TODAY)
        self.assertEqual(result["sp"], 0)

    def test_sp_as_float(self):
        result = transform_issue(_issue(sp=3.5), TODAY)
        self.assertEqual(result["sp"], 3)


class TestTransformIssueTimestamp(unittest.TestCase):

    def test_valid_iso_z_timestamp(self):
        result = transform_issue(_issue(updated="2025-06-10T10:00:00.000Z"), TODAY)
        self.assertEqual(result["last_updated"], "2025-06-10")
        self.assertEqual(result["days_since_update"], 5)

    def test_valid_iso_offset_timestamp(self):
        result = transform_issue(_issue(updated="2025-06-10T10:00:00.000+0000"), TODAY)
        self.assertEqual(result["last_updated"], "2025-06-10")
        self.assertEqual(result["days_since_update"], 5)

    def test_missing_timestamp(self):
        result = transform_issue(_issue(updated=""), TODAY)
        self.assertIsNone(result["last_updated"])
        self.assertEqual(result["days_since_update"], 0)

    def test_none_timestamp(self):
        issue = _issue()
        issue["fields"]["updated"] = None
        result = transform_issue(issue, TODAY)
        self.assertIsNone(result["last_updated"])
        self.assertEqual(result["days_since_update"], 0)

    def test_invalid_timestamp(self):
        result = transform_issue(_issue(updated="not-a-date"), TODAY)
        self.assertIsNone(result["last_updated"])
        self.assertEqual(result["days_since_update"], 0)


class TestExtractBlockedBy(unittest.TestCase):

    def test_blocked_by_non_done_issue(self):
        links = [{
            "type": {"inward": "is blocked by"},
            "inwardIssue": {
                "key": "BLOCK-1",
                "fields": {"status": {"name": "In Progress"}},
            },
        }]
        result = extract_blocked_by(_issue(links=links))
        self.assertEqual(result, ["BLOCK-1"])

    def test_blocked_by_done_issue_excluded(self):
        links = [{
            "type": {"inward": "is blocked by"},
            "inwardIssue": {
                "key": "BLOCK-2",
                "fields": {"status": {"name": "Done"}},
            },
        }]
        result = extract_blocked_by(_issue(links=links))
        self.assertEqual(result, [])

    def test_non_blocking_link_type(self):
        links = [{
            "type": {"inward": "relates to"},
            "inwardIssue": {
                "key": "REL-1",
                "fields": {"status": {"name": "To Do"}},
            },
        }]
        result = extract_blocked_by(_issue(links=links))
        self.assertEqual(result, [])

    def test_no_links(self):
        result = extract_blocked_by(_issue(links=[]))
        self.assertEqual(result, [])

    def test_none_links(self):
        issue = _issue()
        issue["fields"]["issuelinks"] = None
        result = extract_blocked_by(issue)
        self.assertEqual(result, [])


class TestExtractSprintName(unittest.TestCase):

    def test_dict_sprint(self):
        self.assertEqual(extract_sprint_name({"name": "Sprint 5"}), "Sprint 5")

    def test_list_of_dicts(self):
        sprints = [{"name": "Sprint 4"}, {"name": "Sprint 5"}]
        self.assertEqual(extract_sprint_name(sprints), "Sprint 5")

    def test_string_sprint(self):
        self.assertEqual(extract_sprint_name("Sprint 5"), "Sprint 5")

    def test_none(self):
        self.assertIsNone(extract_sprint_name(None))

    def test_empty_list(self):
        self.assertIsNone(extract_sprint_name([]))


if __name__ == "__main__":
    unittest.main()
