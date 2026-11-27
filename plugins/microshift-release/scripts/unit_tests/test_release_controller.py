"""Unit tests for OCP Release Controller client."""

import os
import sys
import unittest
from unittest.mock import patch

# Add parent directory to path so we can import the modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import release_controller  # noqa: E402


class TestCheckOcpPayloadAccepted(unittest.TestCase):
    @patch.object(release_controller, "get_specific_release")
    def test_4x_uses_4_stable(self, mock_get):
        mock_get.side_effect = [None, {"name": "4.21.44", "phase": "Accepted"}]
        self.assertEqual(
            release_controller.check_ocp_payload_accepted("4.21.44"),
            "available",
        )
        self.assertEqual(
            [c.args for c in mock_get.call_args_list],
            [("4.21.0-0.nightly", "4.21.44"), ("4-stable", "4.21.44")],
        )

    @patch.object(release_controller, "get_specific_release")
    def test_5x_uses_5_stable(self, mock_get):
        mock_get.side_effect = [None, {"name": "5.0.3", "phase": "Accepted"}]
        self.assertEqual(
            release_controller.check_ocp_payload_accepted("5.0.3"),
            "available",
        )
        self.assertEqual(
            [c.args for c in mock_get.call_args_list],
            [("5.0.0-0.nightly", "5.0.3"), ("5-stable", "5.0.3")],
        )

    @patch.object(release_controller, "get_specific_release")
    def test_rejected_returns_failed(self, mock_get):
        mock_get.side_effect = [None, {"name": "5.0.3", "phase": "Rejected"}]
        self.assertEqual(
            release_controller.check_ocp_payload_accepted("5.0.3"),
            "failed",
        )

    @patch.object(release_controller, "get_specific_release")
    def test_not_found_returns_not_available(self, mock_get):
        mock_get.return_value = None
        self.assertEqual(
            release_controller.check_ocp_payload_accepted("5.0.3"),
            "not_available",
        )

    @patch.object(release_controller, "get_specific_release")
    def test_invalid_version_returns_not_available(self, mock_get):
        for bad in ("", "5", ".0.3", "v5.0.3"):
            self.assertEqual(
                release_controller.check_ocp_payload_accepted(bad),
                "not_available",
            )
        mock_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
