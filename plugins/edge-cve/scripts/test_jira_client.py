#!/usr/bin/env python3
"""Unit tests for jira_client.search_jql pagination guards."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Allow importing jira_client when requests isn't installed in the test env.
if "requests" not in sys.modules:
    _requests = types.ModuleType("requests")
    _requests.Session = MagicMock  # type: ignore[attr-defined]
    _requests.RequestException = Exception  # type: ignore[attr-defined]
    sys.modules["requests"] = _requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib import jira_client  # noqa: E402


def _resp(payload: dict, status: int = 200) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.raise_for_status = MagicMock()
    r.json.return_value = payload
    return r


class SearchJqlPaginationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "base_url": "https://example.atlassian.net",
            "email": "u@example.com",
            "token": "t",
        }

    @patch.object(jira_client, "load_config")
    def test_explicit_is_last_terminates(self, load_config):
        load_config.return_value = self.cfg
        sess = MagicMock()
        sess.post.side_effect = [
            _resp({"issues": [{"key": "A"}], "isLast": False, "nextPageToken": "p2"}),
            _resp({"issues": [{"key": "B"}], "isLast": True}),
        ]
        issues = jira_client.search_jql("project = X", session=sess, max_results=1)
        self.assertEqual([i["key"] for i in issues], ["A", "B"])
        self.assertEqual(sess.post.call_count, 2)

    @patch.object(jira_client, "load_config")
    def test_repeated_token_raises(self, load_config):
        load_config.return_value = self.cfg
        sess = MagicMock()
        sess.post.side_effect = [
            _resp({"issues": [{"key": "A"}], "isLast": False, "nextPageToken": "same"}),
            _resp({"issues": [{"key": "B"}], "isLast": False, "nextPageToken": "same"}),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            jira_client.search_jql("project = X", session=sess, max_results=1)
        self.assertIn("failed to advance", str(ctx.exception))

    @patch.object(jira_client, "load_config")
    def test_is_last_false_without_token_raises(self, load_config):
        load_config.return_value = self.cfg
        sess = MagicMock()
        sess.post.return_value = _resp({"issues": [{"key": "A"}], "isLast": False})
        with self.assertRaises(RuntimeError) as ctx:
            jira_client.search_jql("project = X", session=sess, max_results=1)
        self.assertIn("isLast=false but no nextPageToken", str(ctx.exception))

    @patch.object(jira_client, "load_config")
    def test_missing_metadata_on_full_page_raises(self, load_config):
        load_config.return_value = self.cfg
        sess = MagicMock()
        # Full page, neither isLast nor nextPageToken - must not silently truncate.
        sess.post.return_value = _resp({"issues": [{"key": "A"}, {"key": "B"}]})
        with self.assertRaises(RuntimeError) as ctx:
            jira_client.search_jql("project = X", session=sess, max_results=2)
        self.assertIn("refusing to truncate", str(ctx.exception))

    @patch.object(jira_client, "load_config")
    def test_missing_metadata_on_short_page_ok(self, load_config):
        load_config.return_value = self.cfg
        sess = MagicMock()
        sess.post.return_value = _resp({"issues": [{"key": "A"}]})
        issues = jira_client.search_jql("project = X", session=sess, max_results=100)
        self.assertEqual([i["key"] for i in issues], ["A"])


if __name__ == "__main__":
    unittest.main()
