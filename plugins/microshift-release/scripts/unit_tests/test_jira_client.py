"""Unit tests for lib.jira_client module."""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.jira_client import (  # noqa: E402
    enrich_ocpbugs, search_cve_tickets, find_microshift_component_cves,
)


def _mock_issue(key, summary, status, resolution="", labels=None):
    """Build a Jira issue dict matching the REST API shape."""
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "status": {"name": status},
            "resolution": {"name": resolution} if resolution else None,
            "labels": labels or [],
            "issuetype": {"name": "Bug"},
            "priority": {"name": "Major"},
        },
    }


class TestEnrichOcpbugs(unittest.TestCase):

    @patch.dict(os.environ, {"JIRA_API_TOKEN": "", "JIRA_USERNAME": ""})
    def test_no_auth_returns_none(self):
        result = enrich_ocpbugs(["OCPBUGS-1"])
        self.assertIsNone(result)

    def test_empty_keys_returns_empty(self):
        result = enrich_ocpbugs([])
        self.assertEqual(result, {})

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_release_required_label(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-100", "etcd restart bug", "Closed",
                        resolution="Done", labels=["release-required"]),
        ]
        result = enrich_ocpbugs(["OCPBUGS-100"])
        self.assertEqual(result["OCPBUGS-100"]["release_action"], "release_required")
        self.assertEqual(result["OCPBUGS-100"]["status"], "Closed")

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_release_not_required_label(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-200", "minor cleanup", "Closed",
                        resolution="Done", labels=["release-not-required"]),
        ]
        result = enrich_ocpbugs(["OCPBUGS-200"])
        self.assertEqual(result["OCPBUGS-200"]["release_action"],
                         "release_not_required")

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_no_release_label_needs_review(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-300", "unlabeled bug", "Verified"),
        ]
        result = enrich_ocpbugs(["OCPBUGS-300"])
        self.assertEqual(result["OCPBUGS-300"]["release_action"], "needs_review")

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_jira_search_fails_returns_none(self, mock_search):
        mock_search.return_value = None
        result = enrich_ocpbugs(["OCPBUGS-1"])
        self.assertIsNone(result)


class TestSearchCveTickets(unittest.TestCase):

    @patch.dict(os.environ, {"JIRA_API_TOKEN": "", "JIRA_USERNAME": ""})
    def test_no_auth_returns_none(self):
        result = search_cve_tickets(["CVE-2026-1111"])
        self.assertIsNone(result)

    def test_empty_ids_returns_empty(self):
        result = search_cve_tickets([])
        self.assertEqual(result, {})

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_finds_microshift_tracker(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-500",
                        "CVE-2026-9999 openshift4/microshift-bootc-rhel9: ...",
                        "Closed", resolution="Done",
                        labels=["pscomponent:microshift-bootc", "CVE-2026-9999"]),
        ]
        result = search_cve_tickets(["CVE-2026-9999"], minor="4.22")
        self.assertIsNotNone(result["CVE-2026-9999"])
        self.assertEqual(result["CVE-2026-9999"]["key"], "OCPBUGS-500")
        self.assertEqual(result["CVE-2026-9999"]["resolution"], "Done")

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_no_microshift_component_returns_none(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-600",
                        "CVE-2026-9999 openshift4/ose-console-rhel9: ...",
                        "Closed", resolution="Done",
                        labels=["pscomponent:ose-console"]),
        ]
        result = search_cve_tickets(["CVE-2026-9999"])
        self.assertIsNone(result["CVE-2026-9999"])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_no_results_returns_none_per_cve(self, mock_search):
        mock_search.return_value = []
        result = search_cve_tickets(["CVE-2026-1111"])
        self.assertIsNone(result["CVE-2026-1111"])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_search_failure_returns_error_dict(self, mock_search):
        mock_search.return_value = None
        result = search_cve_tickets(["CVE-2026-1111"])
        self.assertIn("error", result["CVE-2026-1111"])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_cve_not_in_summary_skipped(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-700",
                        "CVE-2026-OTHER some unrelated bug",
                        "Closed", resolution="Done",
                        labels=["pscomponent:microshift", "CVE-2026-9999"]),
        ]
        result = search_cve_tickets(["CVE-2026-9999"])
        self.assertIsNone(result["CVE-2026-9999"])


class TestFindMicroshiftComponentCves(unittest.TestCase):

    @patch.dict(os.environ, {"JIRA_API_TOKEN": "", "JIRA_USERNAME": ""})
    def test_no_auth_returns_none(self):
        result = find_microshift_component_cves("4.22")
        self.assertIsNone(result)

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_filters_non_microshift_pscomponent(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-800",
                        "CVE-2026-1111 openshift4/ose-console: vuln",
                        "Verified",
                        labels=["SecurityTracking", "pscomponent:ose-console",
                                "CVE-2026-1111"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(result, [])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_skips_not_a_bug_resolution(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-810",
                        "CVE-2026-2222 openshift4/microshift: vuln",
                        "Closed", resolution="Not a Bug",
                        labels=["SecurityTracking",
                                "pscomponent:microshift",
                                "CVE-2026-2222"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(result, [])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_skips_done_errata_resolution(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-820",
                        "CVE-2026-3333 openshift4/microshift: vuln",
                        "Closed", resolution="Done-Errata",
                        labels=["SecurityTracking",
                                "pscomponent:microshift",
                                "CVE-2026-3333"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(result, [])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_skips_in_progress_status(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-830",
                        "CVE-2026-4444 openshift4/microshift: vuln",
                        "In Progress",
                        labels=["SecurityTracking",
                                "pscomponent:microshift",
                                "CVE-2026-4444"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(result, [])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_verified_bug_returns_release_required(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-840",
                        "CVE-2026-5555 openshift4/microshift: vuln",
                        "Verified",
                        labels=["SecurityTracking",
                                "pscomponent:microshift",
                                "CVE-2026-5555"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["key"], "OCPBUGS-840")
        self.assertEqual(result[0]["release_action"], "release_required")
        self.assertEqual(result[0]["cve_id"], "CVE-2026-5555")
        self.assertEqual(result[0]["source"], "component-cve")

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_done_resolution_returns_release_required(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-850",
                        "CVE-2026-6666 openshift4/microshift: vuln",
                        "Closed", resolution="Done",
                        labels=["SecurityTracking",
                                "pscomponent:microshift",
                                "CVE-2026-6666"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["release_action"], "release_required")

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_cve_label_summary_mismatch_skipped(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-860",
                        "CVE-2026-OTHER openshift4/microshift: wrong bug",
                        "Verified",
                        labels=["SecurityTracking",
                                "pscomponent:microshift",
                                "CVE-2026-7777"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(result, [])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_no_cve_label_skipped(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-870",
                        "CVE-2026-8888 openshift4/microshift: vuln",
                        "Verified",
                        labels=["SecurityTracking",
                                "pscomponent:microshift"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(result, [])

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_dedup_by_key(self, mock_search):
        issue = _mock_issue("OCPBUGS-880",
                            "CVE-2026-9999 openshift4/microshift: vuln",
                            "Verified",
                            labels=["SecurityTracking",
                                    "pscomponent:microshift",
                                    "CVE-2026-9999"])
        mock_search.return_value = [issue, issue]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(len(result), 1)

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_jira_search_fails_returns_none(self, mock_search):
        mock_search.return_value = None
        result = find_microshift_component_cves("4.22")
        self.assertIsNone(result)

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_unknown_status_returns_needs_review(self, mock_search):
        mock_search.return_value = [
            _mock_issue("OCPBUGS-890",
                        "CVE-2026-1010 openshift4/microshift: vuln",
                        "MODIFIED",
                        labels=["SecurityTracking",
                                "pscomponent:microshift",
                                "CVE-2026-1010"]),
        ]
        result = find_microshift_component_cves("4.22")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["release_action"], "needs_review")


class TestJiraSearch(unittest.TestCase):

    @patch("lib.jira_client.requests.post")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_pagination_warning(self, mock_post):
        from lib.jira_client import _jira_search
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "issues": [_mock_issue("OCPBUGS-1", "bug", "New")] * 50,
            "total": 120,
        }
        mock_post.return_value = mock_resp
        with self.assertLogs("lib.jira_client", level="WARNING") as cm:
            result = _jira_search("project = OCPBUGS")
        self.assertEqual(len(result), 50)
        self.assertTrue(any("120" in msg for msg in cm.output))

    @patch("lib.jira_client.requests.post")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "tok", "JIRA_USERNAME": "user"})
    def test_auth_failure_returns_none(self, mock_post):
        from lib.jira_client import _jira_search
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_post.return_value = mock_resp
        result = _jira_search("project = OCPBUGS")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
