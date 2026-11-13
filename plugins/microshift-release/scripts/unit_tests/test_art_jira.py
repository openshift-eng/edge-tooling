"""Unit tests for ART Jira version parsing and filtering."""

import json
import os
import sys
import tempfile
import unittest

# Add parent directory to path so we can import the modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import art_jira  # noqa: E402

_extract = art_jira._extract_version_from_summary


class TestExtractVersionFromSummary(unittest.TestCase):
    def test_matches_4x(self):
        self.assertEqual(_extract("Release 4.21.18 [2026-Jun-02]"), "4.21.18")

    def test_matches_5x(self):
        self.assertEqual(_extract("Release 5.0.2 [2026-Nov-17]"), "5.0.2")

    def test_rejects_non_release_story(self):
        self.assertIsNone(_extract("Release RHCOS images in Konflux"))


class TestQueryArtReleasesDue(unittest.TestCase):
    def setUp(self):
        # Reset the module-level cache between tests.
        art_jira._cached_tickets = None

    def tearDown(self):
        art_jira._cached_tickets = None
        os.environ.pop("ART_TICKETS_JSON", None)

    def _load(self, summary):
        tickets = [{"key": "ART-1", "summary": summary}]
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(tickets, f)
        self.addCleanup(os.unlink, path)
        os.environ["ART_TICKETS_JSON"] = path

    def _versions(self):
        return [r["version"] for r in art_jira.query_art_releases_due()]

    def test_keeps_5x_version(self):
        self._load("Release 5.0.2 [2026-Nov-17]")
        self.assertEqual(self._versions(), ["5.0.2"])

    def test_drops_4x_below_414(self):
        self._load("Release 4.13.9 [2026-Jun-02]")
        self.assertEqual(self._versions(), [])

    def test_drops_3x(self):
        # Major versions below 4 predate MicroShift and must be excluded.
        self._load("Release 3.11.0 [2026-Jun-02]")
        self.assertEqual(self._versions(), [])

    def test_keeps_414(self):
        self._load("Release 4.14.0 [2026-Jun-02]")
        self.assertEqual(self._versions(), ["4.14.0"])

    def test_keeps_5x_minor_below_14(self):
        # The 4.14 floor must not apply to major versions beyond 4.
        self._load("Release 5.1.0 [2026-Nov-17]")
        self.assertEqual(self._versions(), ["5.1.0"])

    def test_ignores_non_release_story(self):
        self._load("Release RHCOS images in Konflux")
        self.assertEqual(self._versions(), [])


if __name__ == "__main__":
    unittest.main()
