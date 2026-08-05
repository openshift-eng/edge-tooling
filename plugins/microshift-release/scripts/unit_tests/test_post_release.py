"""Unit tests for post-release verification checks (Phase 4)."""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from post_release import (  # noqa: E402
    find_errata_for_version,
    check_errata_found,
    check_errata_shipped,
    check_bootc_catalog,
    check_rpms_customer_portal,
    check_rpms_cdn,
    check_docs_published,
    check_lifecycle_listed,
    check_lifecycle_active,
    format_text_short,
    format_text_full,
    _all_check_ids,
    _rhel_versions,
)
from validate_artifacts import classify_version  # noqa: E402


def _version(v="4.21.7"):
    return classify_version(v)


def _errata_info(advisory_name="RHBA-2026:12345",
                 portal_url="https://access.redhat.com/errata/RHBA-2026:12345",
                 publication_date="2026-07-29",
                 synopsis="Red Hat build of MicroShift 4.21.7"):
    return {
        "advisory_name": advisory_name,
        "portal_url": portal_url,
        "publication_date": publication_date,
        "synopsis": synopsis,
    }


# ── Version filtering ──────────────────────────────────────────


class TestVersionFiltering(unittest.TestCase):
    def test_reject_ec(self):
        vi = classify_version("4.22.0-ec.3")
        self.assertEqual(vi["type"], "EC")

    def test_reject_rc(self):
        vi = classify_version("4.22.0-rc.2")
        self.assertEqual(vi["type"], "RC")

    def test_accept_zstream(self):
        vi = classify_version("4.21.7")
        self.assertEqual(vi["type"], "Z")

    def test_accept_ga(self):
        vi = classify_version("4.22.0")
        self.assertEqual(vi["type"], "XY")


# ── RHEL versions ──────────────────────────────────────────────


class TestRhelVersions(unittest.TestCase):
    def test_el9_only(self):
        self.assertEqual(_rhel_versions(_version("4.21.7")), [9])

    def test_el9_and_el10(self):
        self.assertEqual(_rhel_versions(_version("4.22.2")), [9, 10])

    def test_el9_only_422_0(self):
        self.assertEqual(_rhel_versions(_version("4.22.0")), [9])

    def test_el9_and_el10_423(self):
        self.assertEqual(_rhel_versions(_version("4.23.0")), [9, 10])


# ── Check IDs ──────────────────────────────────────────────────


class TestAllCheckIds(unittest.TestCase):
    def test_zstream_no_lifecycle(self):
        ids = _all_check_ids(_version("4.21.7"))
        self.assertNotIn("pr_lifecycle_listed", ids)
        self.assertNotIn("pr_lifecycle_active", ids)

    def test_ga_includes_lifecycle(self):
        ids = _all_check_ids(_version("4.22.0"))
        self.assertIn("pr_lifecycle_listed", ids)
        self.assertIn("pr_lifecycle_active", ids)

    def test_no_el10_for_old_version(self):
        ids = _all_check_ids(_version("4.21.7"))
        self.assertNotIn("pr_bootc_catalog_el10", ids)

    def test_el10_for_new_version(self):
        ids = _all_check_ids(_version("4.22.2"))
        self.assertIn("pr_bootc_catalog_el10", ids)


# ── Errata found ───────────────────────────────────────────────


class TestErrataFound(unittest.TestCase):
    def test_found(self):
        r = check_errata_found(_errata_info())
        self.assertEqual(r["status"], "PASS")
        self.assertIn("RHBA-2026:12345", r["reason"])

    def test_not_found(self):
        r = check_errata_found(None)
        self.assertEqual(r["status"], "FAIL")

    def test_search_failed(self):
        r = check_errata_found({"error": "timeout"})
        self.assertEqual(r["status"], "WARN")
        self.assertIn("Hydra search failed", r["reason"])


# ── Errata shipped ─────────────────────────────────────────────


def _advisory(status="SHIPPED_LIVE", advisory_id=170194,
              fulladvisory="RHBA-2026:45112-02", **kwargs):
    adv = {
        "id": advisory_id,
        "fulladvisory": fulladvisory,
        "status": status,
    }
    adv.update(kwargs)
    return adv


class TestErrataShipped(unittest.TestCase):
    def test_no_vpn_with_errata(self):
        r = check_errata_shipped(None, _errata_info(), vpn_ok=False)
        self.assertEqual(r["status"], "WARN")
        self.assertIn("VPN required", r["reason"])

    def test_no_vpn_no_errata(self):
        r = check_errata_shipped(None, None, vpn_ok=False)
        self.assertEqual(r["status"], "WARN")

    def test_shipped_live(self):
        r = check_errata_shipped(_advisory(status="SHIPPED_LIVE"),
                                 _errata_info(), vpn_ok=True)
        self.assertEqual(r["status"], "PASS")
        self.assertIn("SHIPPED_LIVE", r["reason"])

    def test_not_shipped(self):
        r = check_errata_shipped(_advisory(status="REL_PREP"),
                                 _errata_info(), vpn_ok=True)
        self.assertEqual(r["status"], "FAIL")

    def test_in_push(self):
        r = check_errata_shipped(_advisory(status="IN_PUSH"),
                                 _errata_info(), vpn_ok=True)
        self.assertEqual(r["status"], "WARN")

    def test_fetch_failed(self):
        r = check_errata_shipped(None, _errata_info(), vpn_ok=True)
        self.assertEqual(r["status"], "WARN")

    def test_no_errata_info(self):
        r = check_errata_shipped(None, None, vpn_ok=True)
        self.assertEqual(r["status"], "WARN")


# ── Bootc catalog ──────────────────────────────────────────────


class TestBootcCatalog(unittest.TestCase):
    @patch("post_release.pyxis.check_catalog_image_graphql")
    def test_both_arches_found(self, mock_cat):
        mock_cat.return_value = {"valid": True}
        r = check_bootc_catalog(9, _version())
        self.assertEqual(r["status"], "PASS")
        self.assertIn("amd64 and arm64", r["reason"])

    @patch("post_release.pyxis.check_catalog_image_graphql")
    def test_one_arch_missing(self, mock_cat):
        mock_cat.side_effect = [
            {"valid": True},
            {"valid": False, "reason": "Not found"},
        ]
        r = check_bootc_catalog(9, _version())
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("Missing", r["reason"])

    @patch("post_release.pyxis.check_catalog_image_graphql")
    def test_both_arches_missing(self, mock_cat):
        mock_cat.return_value = {"valid": False, "reason": "Not found"}
        r = check_bootc_catalog(9, _version())
        self.assertEqual(r["status"], "FAIL")

    @patch("post_release.pyxis.check_catalog_image_graphql")
    def test_pyxis_exception(self, mock_cat):
        mock_cat.side_effect = Exception("GraphQL error")
        r = check_bootc_catalog(9, _version())
        self.assertEqual(r["status"], "FAIL")
        self.assertTrue(any("error" in d for d in r["details"]))

    def test_skip_old_version(self):
        r = check_bootc_catalog(9, _version("4.17.0"))
        self.assertEqual(r["status"], "SKIP")


# ── RPMs customer portal ──────────────────────────────────────


class TestRpmsCustomerPortal(unittest.TestCase):
    @patch("post_release.artifacts.get_expected_packages")
    @patch("post_release.errata.fetch_builds")
    @patch("post_release.requests.get")
    def test_all_packages_present(self, mock_get, mock_builds, mock_expected):
        mock_get.return_value = MagicMock(status_code=200)
        mock_builds.return_value = {
            "OSE-4.21-RHEL-9": {
                "builds": [{"microshift-4.21.7-1.el9": {
                    "variant_arch": {"x86_64": {"SRPMS": [
                        "microshift-4.21.7-1.el9.src.rpm",
                        "microshift-selinux-4.21.7-1.el9.noarch.rpm",
                    ]}}
                }}]
            }
        }
        mock_expected.return_value = ["microshift", "microshift-selinux"]
        r = check_rpms_customer_portal(_advisory(), _errata_info(), _version(), vpn_ok=True)
        self.assertEqual(r["status"], "PASS")
        self.assertIn("All 2 expected", r["reason"])

    @patch("post_release.artifacts.get_expected_packages")
    @patch("post_release.errata.fetch_builds")
    @patch("post_release.requests.get")
    def test_missing_packages(self, mock_get, mock_builds, mock_expected):
        mock_get.return_value = MagicMock(status_code=200)
        mock_builds.return_value = {
            "OSE-4.21-RHEL-9": {
                "builds": [{"microshift-4.21.7-1.el9": {
                    "variant_arch": {"x86_64": {"SRPMS": [
                        "microshift-4.21.7-1.el9.src.rpm",
                    ]}}
                }}]
            }
        }
        mock_expected.return_value = ["microshift", "microshift-selinux"]
        r = check_rpms_customer_portal(_advisory(), _errata_info(), _version(), vpn_ok=True)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("missing", r["reason"])

    @patch("post_release.requests.get")
    def test_page_not_found(self, mock_get):
        mock_get.return_value = MagicMock(status_code=404)
        r = check_rpms_customer_portal(_advisory(), _errata_info(), _version(), vpn_ok=True)
        self.assertEqual(r["status"], "FAIL")

    @patch("post_release.requests.get")
    def test_no_vpn_page_accessible(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        r = check_rpms_customer_portal(None, _errata_info(), _version(), vpn_ok=False)
        self.assertEqual(r["status"], "WARN")
        self.assertIn("VPN", r["reason"])

    def test_no_data(self):
        r = check_rpms_customer_portal(None, None, _version(), vpn_ok=False)
        self.assertEqual(r["status"], "WARN")

    @patch("post_release.requests.get")
    def test_network_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("timeout")
        r = check_rpms_customer_portal(_advisory(), _errata_info(), _version(), vpn_ok=True)
        self.assertEqual(r["status"], "WARN")


# ── RPMs CDN ───────────────────────────────────────────────────


class TestRpmsCdn(unittest.TestCase):
    def test_no_vpn(self):
        r = check_rpms_cdn(_advisory(), vpn_ok=False)
        self.assertEqual(r["status"], "WARN")

    @patch("post_release.errata.fetch_push_status")
    def test_cdn_complete(self, mock_push):
        mock_push.return_value = [
            {"target": {"name": "cdn"}, "status": "COMPLETE"},
            {"target": {"name": "cdn"}, "status": "COMPLETE"},
        ]
        r = check_rpms_cdn(_advisory(), vpn_ok=True)
        self.assertEqual(r["status"], "PASS")
        self.assertIn("CDN push complete", r["reason"])

    @patch("post_release.errata.fetch_push_status")
    def test_cdn_not_complete(self, mock_push):
        mock_push.return_value = [
            {"target": {"name": "cdn"}, "status": "IN_PROGRESS"},
        ]
        r = check_rpms_cdn(_advisory(), vpn_ok=True)
        self.assertEqual(r["status"], "FAIL")

    @patch("post_release.errata.fetch_push_status")
    def test_no_cdn_push(self, mock_push):
        mock_push.return_value = [
            {"target": {"name": "ftp"}, "status": "COMPLETE"},
        ]
        r = check_rpms_cdn(_advisory(), vpn_ok=True)
        self.assertEqual(r["status"], "FAIL")

    @patch("post_release.errata.fetch_push_status")
    def test_fetch_failed(self, mock_push):
        mock_push.return_value = None
        r = check_rpms_cdn(_advisory(), vpn_ok=True)
        self.assertEqual(r["status"], "WARN")

    def test_no_advisory(self):
        r = check_rpms_cdn(None, vpn_ok=True)
        self.assertEqual(r["status"], "WARN")


# ── Documentation ──────────────────────────────────────────────


class TestDocsPublished(unittest.TestCase):
    @patch("post_release.requests.get")
    def test_version_found(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200,
                                          text="<html>...4.21.7...</html>")
        r = check_docs_published(_version())
        self.assertEqual(r["status"], "PASS")
        self.assertIn("4.21.7", r["reason"])

    @patch("post_release.requests.get")
    def test_page_exists_version_missing(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200,
                                          text="<html>...4.21.5...</html>")
        r = check_docs_published(_version())
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("not mentioned", r["reason"])

    @patch("post_release.requests.get")
    def test_page_not_found(self, mock_get):
        mock_get.return_value = MagicMock(status_code=404)
        r = check_docs_published(_version())
        self.assertEqual(r["status"], "FAIL")

    @patch("post_release.requests.get")
    def test_network_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("DNS failure")
        r = check_docs_published(_version())
        self.assertEqual(r["status"], "WARN")


# ── Lifecycle ──────────────────────────────────────────────────


class TestLifecycleListed(unittest.TestCase):
    def test_skip_zstream(self):
        r = check_lifecycle_listed(_version("4.21.7"), None)
        self.assertEqual(r["status"], "SKIP")

    def test_found(self):
        data = [{"version": "4.22", "phase": "Full Support",
                 "end_date": "2027-06-01", "active": True}]
        r = check_lifecycle_listed(_version("4.22.0"), data)
        self.assertEqual(r["status"], "PASS")

    def test_not_found(self):
        r = check_lifecycle_listed(_version("4.22.0"), [])
        self.assertEqual(r["status"], "FAIL")

    def test_no_data(self):
        r = check_lifecycle_listed(_version("4.22.0"), None)
        self.assertEqual(r["status"], "WARN")


class TestLifecycleActive(unittest.TestCase):
    def test_skip_zstream(self):
        r = check_lifecycle_active(_version("4.21.7"), None)
        self.assertEqual(r["status"], "SKIP")

    def test_full_support(self):
        data = [{"version": "4.22", "phase": "Full Support",
                 "end_date": "2027-06-01", "active": True}]
        r = check_lifecycle_active(_version("4.22.0"), data)
        self.assertEqual(r["status"], "PASS")

    def test_maintenance(self):
        data = [{"version": "4.22", "phase": "Maintenance Support",
                 "end_date": "2027-06-01", "active": True}]
        r = check_lifecycle_active(_version("4.22.0"), data)
        self.assertEqual(r["status"], "FAIL")

    def test_not_found(self):
        r = check_lifecycle_active(_version("4.22.0"), [])
        self.assertEqual(r["status"], "WARN")


# ── Hydra search ───────────────────────────────────────────────


class TestFindErrataForVersion(unittest.TestCase):
    @patch("post_release.requests.get")
    def test_found(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "response": {
                    "docs": [{
                        "portal_synopsis": (
                            "RHBA-2026:12345 - Red Hat build of MicroShift "
                            "4.21 bug fix and enhancement update for "
                            "MicroShift 4.21.7"
                        ),
                        "uri": "https://access.redhat.com/errata/RHBA-2026:12345",
                        "portal_publication_date": "2026-07-29T10:00:00Z",
                    }]
                }
            },
        )
        result = find_errata_for_version("4.21.7", "4.21")
        self.assertIsNotNone(result)
        self.assertEqual(result["advisory_name"], "RHBA-2026:12345")

    @patch("post_release.requests.get")
    def test_not_found(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"response": {"docs": []}},
        )
        result = find_errata_for_version("4.21.99", "4.21")
        self.assertIsNone(result)

    @patch("post_release.requests.get")
    def test_network_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("timeout")
        result = find_errata_for_version("4.21.7", "4.21")
        self.assertIn("error", result)
        self.assertIn("timeout", result["error"])


# ── Formatting ─────────────────────────────────────────────────


class TestFormatting(unittest.TestCase):
    def _sample_results(self):
        from validate_artifacts import _pass, _fail, _warn
        return [
            _pass("pr_errata_found", "RHBA-2026:12345 (published 2026-07-29)"),
            _pass("pr_errata_shipped", "Status: SHIPPED_LIVE"),
            _pass("pr_bootc_catalog_el9", "Found in prod catalog (rhel9)"),
            _fail("pr_rpms_customer_portal", "HTTP 404",
                  ["URL: https://access.redhat.com/errata/..."]),
            _warn("pr_rpms_cdn", "VPN required for CDN repo check"),
            _pass("pr_docs_published", "Release notes page accessible (4.21)"),
        ]

    def test_short_format(self):
        output = format_text_short("4.21.7", self._sample_results())
        self.assertIn("Post-Release Verification: 4.21.7", output)
        self.assertIn("Errata", output)
        self.assertIn("Bootc Images", output)
        self.assertIn("RPMs", output)
        self.assertIn("Documentation", output)

    def test_full_format(self):
        output = format_text_full("4.21.7", self._sample_results(),
                                  _version())
        self.assertIn("# Post-Release Verification", output)
        self.assertIn("## Errata", output)
        self.assertIn("**Summary:**", output)


if __name__ == "__main__":
    unittest.main()
