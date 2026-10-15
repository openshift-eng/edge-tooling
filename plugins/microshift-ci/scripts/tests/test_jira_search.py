#!/usr/bin/env python3
"""Unit tests for the deterministic Jira REST client."""

import importlib.util
import json
import os
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "jira_search.py"
SPEC = importlib.util.spec_from_file_location("jira_search_under_test", SCRIPT)
jira_search = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jira_search)


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.data).encode()


class JiraSearchClientTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(
            os.environ, {"JIRA_USERNAME": "user", "JIRA_API_TOKEN": "token"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_missing_credentials_fails(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(jira_search.JiraSearchError):
                jira_search.search("project = USHIFT")

    def test_empty_success_is_empty_list(self):
        with mock.patch.object(jira_search.urllib.request, "urlopen",
                               return_value=Response({"issues": []})):
            self.assertEqual(jira_search.search("project = USHIFT"), [])

    def test_http_failure_is_not_empty_success(self):
        error = urllib.error.HTTPError("url", 503, "unavailable", {}, None)
        with mock.patch.object(jira_search.urllib.request, "urlopen",
                               side_effect=error):
            with self.assertRaises(jira_search.JiraSearchError):
                jira_search.search("project = USHIFT")

    def test_search_all_paginates_with_next_page_token(self):
        pages = [
            Response({"issues": [{"key": "USHIFT-1"}], "isLast": False,
                      "nextPageToken": "next"}),
            Response({"issues": [{"key": "USHIFT-2"}], "isLast": True}),
        ]
        requests = []

        def urlopen(request, timeout):
            requests.append(json.loads(request.data))
            return pages.pop(0)

        with mock.patch.object(jira_search.urllib.request, "urlopen",
                               side_effect=urlopen):
            issues = jira_search.search_all("project = USHIFT")
        self.assertEqual([issue["key"] for issue in issues],
                         ["USHIFT-1", "USHIFT-2"])
        self.assertNotIn("nextPageToken", requests[0])
        self.assertEqual(requests[1]["nextPageToken"], "next")

    def test_search_all_rejects_repeated_token(self):
        response = Response({"issues": [], "isLast": False,
                             "nextPageToken": "same"})
        with mock.patch.object(jira_search.urllib.request, "urlopen",
                               return_value=response):
            with self.assertRaisesRegex(jira_search.JiraSearchError, "repeated"):
                jira_search.search_all("project = USHIFT")

    def test_search_all_rejects_incomplete_page(self):
        response = Response({"issues": [], "isLast": False})
        with mock.patch.object(jira_search.urllib.request, "urlopen",
                               return_value=response):
            with self.assertRaisesRegex(jira_search.JiraSearchError,
                                        "missing nextPageToken"):
                jira_search.search_all("project = USHIFT")


if __name__ == "__main__":
    unittest.main()
