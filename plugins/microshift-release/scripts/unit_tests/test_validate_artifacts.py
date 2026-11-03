"""Regression tests for RPM variants, independent of bootc image variants."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import artifacts  # noqa: E402
from lib import brew  # noqa: E402
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

    def test_rpm_variants_by_stream(self):
        for minor, expected in (("4.22", [9]), ("5.0", [9]), ("5.1", [9]),
                                ("5.2", [10])):
            with self.subTest(minor=minor):
                self.assertEqual(_rpm_rhel_versions_for(minor), expected)
                result = check_rpm_rhel_version(
                    classify_version(f"{minor}.0"),
                    {"found": True, "el9": True, "el10": False},
                )
                self.assertEqual(result["status"], "FAIL" if 10 in expected else "PASS")

    def test_52_requires_el10_without_el9(self):
        result = check_rpm_rhel_version(
            classify_version("5.2.0"),
            {"found": True, "el9": False, "el10": True},
        )
        self.assertEqual(result["status"], "PASS")
        self.assertIn("el9 RPMs not required", result["reason"])

        result = check_rpm_rhel_version(
            classify_version("5.2.0"),
            {"found": True, "el9": True, "el10": False},
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "Missing RHEL variant: el10")

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

    def test_52_ec_rc_mirrors_only_check_el10(self):
        for release_type in ("EC", "RC"):
            version = f"5.2.0-{release_type.lower()}.1"
            with self.subTest(release_type=release_type), \
                    patch.object(artifacts, "_http_get") as get:
                get.return_value.status_code = 200
                result = check_rpm_mirror(classify_version(version), release_type)
                self.assertEqual(result["status"], "PASS")
                urls = [call.args[0] for call in get.call_args_list]
                self.assertEqual(len(urls), 2)
                self.assertTrue(all("/el10/" in url for url in urls))

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


class TestNightlyVariantDetection(unittest.TestCase):
    """fetch_latest_nightly_builds must report real RHEL variants so that
    check_rpm_rhel_version can validate the el10-from-5.2 requirement."""

    @staticmethod
    def _nvr(stream, ts, el):
        return (f"microshift-{stream}.0~0.nightly_{ts}"
                f".p0.gabc1234.assembly.{stream}.0.{el}")

    def test_detects_both_variants(self):
        html = " ".join([
            self._nvr("5.2", "2026_11_01_120000", "el9"),
            self._nvr("5.2", "2026_11_01_120000", "el10"),
        ])
        with patch.object(brew, "_fetch_brew_page", return_value=html):
            builds = brew.fetch_latest_nightly_builds()
        self.assertIn("5.2", builds)
        self.assertTrue(builds["5.2"]["el9"])
        self.assertTrue(builds["5.2"]["el10"])
        self.assertEqual(builds["5.2"]["nvr"],
                         "microshift-5.2.0~0.nightly_2026_11_01_120000")

    def test_el9_only_build_reports_no_el10(self):
        html = self._nvr("5.1", "2026_11_01_120000", "el9")
        with patch.object(brew, "_fetch_brew_page", return_value=html):
            builds = brew.fetch_latest_nightly_builds()
        self.assertTrue(builds["5.1"]["el9"])
        self.assertFalse(builds["5.1"]["el10"])

    def test_latest_timestamp_supersedes_older_variants(self):
        html = " ".join([
            self._nvr("5.2", "2026_10_01_120000", "el9"),
            self._nvr("5.2", "2026_10_01_120000", "el10"),
            self._nvr("5.2", "2026_11_02_130000", "el10"),
        ])
        with patch.object(brew, "_fetch_brew_page", return_value=html):
            builds = brew.fetch_latest_nightly_builds()
        self.assertEqual(builds["5.2"]["timestamp"], "2026-11-02T13:00:00")
        self.assertFalse(builds["5.2"]["el9"])
        self.assertTrue(builds["5.2"]["el10"])

    def test_52_nightly_with_el10_passes_rhel_check(self):
        html = self._nvr("5.2", "2026_11_01_120000", "el10")
        with patch.object(brew, "_fetch_brew_page", return_value=html):
            nbuild = brew.fetch_latest_nightly_builds()["5.2"]
        build_info = {"found": True, "el9": nbuild["el9"], "el10": nbuild["el10"]}
        result = check_rpm_rhel_version(classify_version("5.2.0"), build_info)
        self.assertEqual(result["status"], "PASS")

    def test_52_nightly_missing_el10_fails_rhel_check(self):
        html = self._nvr("5.2", "2026_11_01_120000", "el9")
        with patch.object(brew, "_fetch_brew_page", return_value=html):
            nbuild = brew.fetch_latest_nightly_builds()["5.2"]
        build_info = {"found": True, "el9": nbuild["el9"], "el10": nbuild["el10"]}
        result = check_rpm_rhel_version(classify_version("5.2.0"), build_info)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["reason"], "Missing RHEL variant: el10")


if __name__ == "__main__":
    unittest.main()
