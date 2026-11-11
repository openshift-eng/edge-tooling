"""Unit tests for GitLab helpers in lib.artifacts."""

import sys
import os
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import artifacts  # noqa: E402


class TestGitlabTlsFailure(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(artifacts, "_gitlab_tls_failed", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("lib.artifacts._http_get")
    def test_tls_error_reported_instead_of_token_hint(self, mock_get):
        mock_get.side_effect = requests.exceptions.SSLError("verify failed")
        with self.assertLogs("lib.artifacts", level="ERROR"):
            result = artifacts.fetch_shipment_mr("5.0.0")
        self.assertFalse(result["found"])
        self.assertIn("TLS verification failed", result["reason"])
        self.assertNotIn("GITLAB_API_TOKEN", result["reason"])

    @patch("lib.artifacts._http_get")
    def test_other_failure_keeps_token_hint(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")
        with self.assertLogs("lib.artifacts", level="WARNING"):
            result = artifacts.fetch_shipment_mr("5.0.0")
        self.assertIn("GITLAB_API_TOKEN", result["reason"])


if __name__ == "__main__":
    unittest.main()
