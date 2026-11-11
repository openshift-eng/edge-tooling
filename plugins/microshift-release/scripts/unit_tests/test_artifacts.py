"""Unit tests for GitLab URL helpers in lib.artifacts."""

import sys
import os
import unittest
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import artifacts  # noqa: E402
from lib.artifacts import (  # noqa: E402
    _gitlab_raw_to_api_path,
    _redact_url,
)


_RAW = "https://gitlab.cee.redhat.com/group/project/-/raw"


class TestGitlabRawToApiPath(unittest.TestCase):
    def test_valid_raw_url(self):
        url = ("https://gitlab.cee.redhat.com/group/sub/project"
               "/-/raw/main/path/to/advisory.yaml")
        self.assertEqual(
            _gitlab_raw_to_api_path(url),
            "projects/group%2Fsub%2Fproject/repository/files/"
            "path%2Fto%2Fadvisory.yaml/raw?ref=main",
        )

    def test_query_and_fragment_are_ignored(self):
        for suffix in ("?inline=false", "#L5"):
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    _gitlab_raw_to_api_path(_RAW + "/main/data/a.yaml" + suffix),
                    "projects/group%2Fproject/repository/files/"
                    "data%2Fa.yaml/raw?ref=main",
                )

    def test_percent_encoded_file_name_is_not_double_encoded(self):
        self.assertEqual(
            _gitlab_raw_to_api_path(_RAW + "/main/data/release%20notes.yaml"),
            "projects/group%2Fproject/repository/files/"
            "data%2Frelease%20notes.yaml/raw?ref=main",
        )

    def test_ref_is_encoded_as_query_value(self):
        self.assertEqual(
            _gitlab_raw_to_api_path(_RAW + "/release+candidate/data/a.yaml"),
            "projects/group%2Fproject/repository/files/"
            "data%2Fa.yaml/raw?ref=release%2Bcandidate",
        )

    def test_other_origins_are_rejected(self):
        for origin in ("https://other.example",
                       "http://gitlab.cee.redhat.com",
                       "https://gitlab.cee.redhat.com:8443"):
            with self.subTest(origin=origin):
                self.assertIsNone(_gitlab_raw_to_api_path(
                    origin + "/group/project/-/raw/main/data/a.yaml"))

    def test_invalid_urls(self):
        for url in (
            None,
            "",
            "https://gitlab.cee.redhat.com/group/project/blob/main/file.yaml",
            "https://gitlab.cee.redhat.com/-/raw/main/file.yaml",
            "https://gitlab.cee.redhat.com/group/project/-/raw/main",
            "https://gitlab.cee.redhat.com/group/project/-/raw/",
            "https://[invalid",
        ):
            with self.subTest(url=url):
                self.assertIsNone(_gitlab_raw_to_api_path(url))


class TestRedactUrl(unittest.TestCase):
    def test_strips_host_and_query(self):
        self.assertEqual(
            _redact_url("https://gitlab.cee.redhat.com/group/project"
                        "/-/raw/main/x.yaml?inline=false"),
            "/group/project/-/raw/main/x.yaml",
        )

    def test_none_passthrough(self):
        self.assertIsNone(_redact_url(None))

    def test_pathless_url_is_not_returned(self):
        self.assertEqual(
            _redact_url("https://user@gitlab.cee.redhat.com?inline=false"),
            "<redacted-url>",
        )

    def test_malformed_url_does_not_raise(self):
        self.assertEqual(_redact_url("https://[invalid"), "<invalid-url>")


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
