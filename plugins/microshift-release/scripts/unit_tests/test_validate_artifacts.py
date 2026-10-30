"""Regression tests for RPM variants, independent of bootc image variants."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import artifacts  # noqa: E402
from validate_artifacts import (  # noqa: E402
    _rhel_versions_for,
    _rpm_rhel_versions_for,
    check_rpm_mirror,
    check_rpm_rhel_version,
    classify_version,
    run_bootc_checks,
)


class TestRpmRhelVariants(unittest.TestCase):

    def test_50_el9_only_build_passes(self):
        for version in ("5.0.0", "5.0.1", "5.0.0-ec.3", "5.0.0-rc.1"):
            with self.subTest(version=version):
                result = check_rpm_rhel_version(
                    classify_version(version),
                    {"found": True, "el9": True, "el10": False},
                )
                self.assertEqual(result["status"], "PASS")
                self.assertIn("el10 RPMs not required", result["reason"])
                self.assertNotIn("before 4.23", result["reason"])

    def test_50_missing_el9_still_fails(self):
        result = check_rpm_rhel_version(
            classify_version("5.0.0"),
            {"found": True, "el9": False, "el10": False},
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "Missing RHEL variant: el9")

    def test_no_build_is_skipped(self):
        result = check_rpm_rhel_version(classify_version("5.0.0"), {"found": False})
        self.assertEqual(result["status"], "SKIP")

    def test_other_streams_keep_existing_requirements(self):
        for minor, expected in (("4.22", [9]), ("4.23", [9, 10]), ("5.1", [9, 10])):
            with self.subTest(minor=minor):
                self.assertEqual(_rpm_rhel_versions_for(minor), expected)
                result = check_rpm_rhel_version(
                    classify_version(f"{minor}.0"),
                    {"found": True, "el9": True, "el10": False},
                )
                self.assertEqual(result["status"], "FAIL" if 10 in expected else "PASS")

    def test_50_ec_rc_mirrors_only_check_el9(self):
        for release_type in ("EC", "RC"):
            version = f"5.0.0-{release_type.lower()}.1"
            with self.subTest(release_type=release_type), \
                    patch.object(artifacts, "_http_get") as get:
                get.return_value.status_code = 200
                result = check_rpm_mirror(classify_version(version), release_type)
                self.assertEqual(result["status"], "PASS")
                urls = [call.args[0] for call in get.call_args_list]
                self.assertEqual(len(urls), 2)
                self.assertTrue(all("/el9/" in url for url in urls))
                self.assertTrue(any("/x86_64/" in url for url in urls))
                self.assertTrue(any("/aarch64/" in url for url in urls))

    def test_50_ec_rc_mirrors_missing_el9_fail(self):
        for release_type in ("EC", "RC"):
            version = f"5.0.0-{release_type.lower()}.1"
            with self.subTest(release_type=release_type), \
                    patch.object(artifacts, "_http_get") as get:
                get.return_value.status_code = 404
                result = check_rpm_mirror(classify_version(version), release_type)
                self.assertEqual(result["status"], "FAIL")
                urls = [call.args[0] for call in get.call_args_list]
                self.assertEqual(len(urls), 2)
                self.assertTrue(all("/el9/" in url for url in urls))

    def test_50_bootc_still_checks_el10(self):
        self.assertEqual(_rhel_versions_for("5.0"), [9, 10])
        shipment = {"found": False, "reason": "test shipment unavailable"}
        with patch.object(artifacts, "fetch_shipment_mr", return_value=shipment), \
                patch.object(artifacts, "validate_bootc_mirror", return_value={
                    "valid": True, "reason": "test mirror available",
                }) as mirror, \
                patch.object(artifacts, "validate_bootc_sha_match", return_value={
                    "valid": None, "reason": "test SHA unavailable",
                }) as sha:
            run_bootc_checks(classify_version("5.0.0-ec.3"))
        mirror.assert_called_once_with("5.0.0-ec.3", "EC", [9, 10])
        sha.assert_called_once_with("5.0.0-ec.3", "EC", shipment, [9, 10])


if __name__ == "__main__":
    unittest.main()
