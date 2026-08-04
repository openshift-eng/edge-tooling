"""Unit tests for Errata Tool promotion checks (Phase 3)."""

import sys
import os
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from errata_promotion import (  # noqa: E402
    check_advisory_exists,
    check_advisory_type,
    check_qa_owner,
    check_bugs_verified,
    check_rpms_present,
    check_rpms_product_listed,
    check_cdn_staging,
    check_cat_tests,
    check_status_rel_prep,
    format_text_short,
    format_text_full,
    _expected_types,
)
from lib.errata import extract_microshift_nvrs, extract_package_names, extract_bug_keys  # noqa: E402
from validate_artifacts import classify_version  # noqa: E402


def _version(v="4.20.26"):
    return classify_version(v)


def _advisory(status="QE", errata_type="RHBA", qa_name="MicroShift QE",
              fulladvisory="RHBA-2026:12345-05", advisory_id=12345,
              **kwargs):
    adv = {
        "id": advisory_id,
        "fulladvisory": fulladvisory,
        "status": status,
        "errata_type": errata_type,
        "quality_responsibility_name": qa_name,
    }
    adv.update(kwargs)
    return adv


# ── Advisory exists ─────────────────────────────────────────────


class TestAdvisoryExists(unittest.TestCase):
    def test_found(self):
        r = check_advisory_exists(_advisory())
        self.assertEqual(r["status"], "PASS")
        self.assertIn("RHBA-2026:12345", r["reason"])

    def test_not_found(self):
        r = check_advisory_exists(None)
        self.assertEqual(r["status"], "FAIL")


# ── Advisory type ───────────────────────────────────────────────


class TestAdvisoryType(unittest.TestCase):
    def test_zstream_rhba(self):
        r = check_advisory_type(_advisory(errata_type="RHBA"), _version("4.20.26"))
        self.assertEqual(r["status"], "PASS")

    def test_zstream_rhsa(self):
        r = check_advisory_type(_advisory(errata_type="RHSA"), _version("4.20.26"))
        self.assertEqual(r["status"], "PASS")

    def test_zstream_wrong_type(self):
        r = check_advisory_type(_advisory(errata_type="RHEA"), _version("4.20.26"))
        self.assertEqual(r["status"], "FAIL")

    def test_ga_rhea(self):
        r = check_advisory_type(_advisory(errata_type="RHEA"), _version("4.22.0"))
        self.assertEqual(r["status"], "PASS")

    def test_ga_wrong_type(self):
        r = check_advisory_type(_advisory(errata_type="RHBA"), _version("4.22.0"))
        self.assertEqual(r["status"], "FAIL")

    def test_none_advisory(self):
        r = check_advisory_type(None, _version())
        self.assertEqual(r["status"], "WARN")


class TestExpectedTypes(unittest.TestCase):
    def test_xy(self):
        self.assertEqual(_expected_types(_version("4.22.0")), ["RHEA"])

    def test_z(self):
        self.assertEqual(_expected_types(_version("4.20.26")), ["RHBA", "RHSA"])

    def test_ec(self):
        self.assertEqual(_expected_types(_version("5.0.0-ec.3")), ["RHBA", "RHSA"])

    def test_rc(self):
        self.assertEqual(_expected_types(_version("4.22.0-rc.2")), ["RHBA", "RHSA"])


# ── QA owner ────────────────────────────────────────────────────


class TestQAOwner(unittest.TestCase):
    def test_changed(self):
        r = check_qa_owner(_advisory(qa_name="MicroShift QE"))
        self.assertEqual(r["status"], "PASS")
        self.assertIn("MicroShift QE", r["reason"])

    def test_default(self):
        r = check_qa_owner(_advisory(qa_name="Default"))
        self.assertEqual(r["status"], "FAIL")

    def test_empty(self):
        r = check_qa_owner(_advisory(qa_name=""))
        self.assertEqual(r["status"], "FAIL")

    def test_numeric_id_set(self):
        r = check_qa_owner(_advisory(qa_name="", quality_responsibility_id=139))
        self.assertEqual(r["status"], "PASS")
        self.assertIn("139", r["reason"])

    def test_numeric_id_zero(self):
        r = check_qa_owner(_advisory(qa_name="", quality_responsibility_id=0))
        self.assertEqual(r["status"], "FAIL")

    def test_none_advisory(self):
        r = check_qa_owner(None)
        self.assertEqual(r["status"], "WARN")


# ── Bugs verified ──────────────────────────────────────────────


class TestBugsVerified(unittest.TestCase):
    def test_all_verified(self):
        bugs = [
            {"key": "OCPBUGS-1", "status": "Verified"},
            {"key": "OCPBUGS-2", "status": "Verified"},
        ]
        r = check_bugs_verified(bugs)
        self.assertEqual(r["status"], "PASS")
        self.assertIn("2/2", r["reason"])

    def test_all_closed(self):
        bugs = [
            {"key": "OCPBUGS-1", "status": "Closed"},
            {"key": "OCPBUGS-2", "status": "Closed"},
        ]
        r = check_bugs_verified(bugs)
        self.assertEqual(r["status"], "PASS")

    def test_mixed_verified_closed(self):
        bugs = [
            {"key": "OCPBUGS-1", "status": "Verified"},
            {"key": "OCPBUGS-2", "status": "Closed"},
        ]
        r = check_bugs_verified(bugs)
        self.assertEqual(r["status"], "PASS")

    def test_some_not_verified(self):
        bugs = [
            {"key": "OCPBUGS-1", "status": "Verified"},
            {"key": "OCPBUGS-2", "status": "Modified"},
            {"key": "OCPBUGS-3", "status": "ON_QA"},
        ]
        r = check_bugs_verified(bugs)
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("1/3", r["reason"])

    def test_no_bugs(self):
        r = check_bugs_verified([])
        self.assertEqual(r["status"], "PASS")

    def test_none_data(self):
        r = check_bugs_verified(None)
        self.assertEqual(r["status"], "WARN")


# ── RPMs present ───────────────────────────────────────────────


class TestRPMsPresent(unittest.TestCase):
    @patch("errata_promotion.artifacts.get_expected_packages",
           return_value=["microshift", "microshift-selinux", "microshift-networking"])
    def test_all_found(self, _mock):
        nvrs = [
            "microshift-4.20.26-202607010000.p0.gabc1234.assembly.4.20.26.el9",
            "microshift-selinux-4.20.26-202607010000.p0.gabc1234.assembly.4.20.26.el9",
            "microshift-networking-4.20.26-202607010000.p0.gabc1234.assembly.4.20.26.el9",
        ]
        r = check_rpms_present(nvrs, _version("4.20.26"))
        self.assertEqual(r["status"], "PASS")
        self.assertIn("3/3", r["reason"])

    @patch("errata_promotion.artifacts.get_expected_packages",
           return_value=["microshift", "microshift-selinux", "microshift-networking"])
    def test_missing_rpms(self, _mock):
        nvrs = [
            "microshift-4.20.26-202607010000.p0.gabc1234.assembly.4.20.26.el9",
        ]
        r = check_rpms_present(nvrs, _version("4.20.26"))
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("2", r["reason"])

    @patch("errata_promotion.artifacts.get_expected_packages", return_value=None)
    def test_no_expected_packages(self, _mock):
        nvrs = ["microshift-4.20.26-el9"]
        r = check_rpms_present(nvrs, _version("4.20.26"))
        self.assertEqual(r["status"], "WARN")

    def test_no_nvrs(self):
        r = check_rpms_present([], _version())
        self.assertEqual(r["status"], "FAIL")


# ── RPMs product listed ─────────────────────────────────────────


class TestRPMsProductListed(unittest.TestCase):
    def test_listed_nested(self):
        builds_data = {
            "OSE-4.22-RHEL-9": {
                "name": "OSE-4.22-RHEL-9",
                "builds": [
                    {"microshift-4.22.7-el9": {"nvr": "microshift-4.22.7-el9"}}
                ]
            }
        }
        nvrs = ["microshift-4.22.7-el9"]
        r = check_rpms_product_listed(builds_data, nvrs)
        self.assertEqual(r["status"], "PASS")
        self.assertIn("OSE-4.22-RHEL-9", r["details"])

    def test_listed_flat(self):
        builds_data = {
            "RHEL-9-OSE-4.20": [
                {"microshift-4.20.26-el9": {"nvr": "microshift-4.20.26-el9"}}
            ]
        }
        nvrs = ["microshift-4.20.26-el9"]
        r = check_rpms_product_listed(builds_data, nvrs)
        self.assertEqual(r["status"], "PASS")

    def test_not_listed(self):
        builds_data = {
            "OSE-4.22-RHEL-9": {
                "name": "OSE-4.22-RHEL-9",
                "builds": [
                    {"other-package-4.20.26-el9": {"nvr": "other-package-el9"}}
                ]
            }
        }
        nvrs = ["microshift-4.20.26-el9"]
        r = check_rpms_product_listed(builds_data, nvrs)
        self.assertEqual(r["status"], "FAIL")

    def test_no_data(self):
        r = check_rpms_product_listed(None, [])
        self.assertEqual(r["status"], "WARN")


# ── CDN staging ─────────────────────────────────────────────────


class TestCDNStaging(unittest.TestCase):
    def test_rel_prep(self):
        r = check_cdn_staging(_advisory(status="REL_PREP"))
        self.assertEqual(r["status"], "PASS")

    def test_qe_status(self):
        r = check_cdn_staging(_advisory(status="QE"))
        self.assertEqual(r["status"], "WARN")

    def test_text_only(self):
        r = check_cdn_staging(_advisory(text_only=True))
        self.assertEqual(r["status"], "SKIP")

    def test_none(self):
        r = check_cdn_staging(None)
        self.assertEqual(r["status"], "WARN")


# ── CAT tests ──────────────────────────────────────────────────


class TestCATTests(unittest.TestCase):
    def test_rhnqa_passed(self):
        r = check_cat_tests(_advisory(rhnqa=1))
        self.assertEqual(r["status"], "PASS")

    def test_qa_complete(self):
        r = check_cat_tests(_advisory(rhnqa=0, qa_complete=1))
        self.assertEqual(r["status"], "PASS")

    def test_rhnqa_not_passed_qe(self):
        r = check_cat_tests(_advisory(status="QE", rhnqa=0, qa_complete=0))
        self.assertEqual(r["status"], "FAIL")

    def test_rel_prep_fallback(self):
        r = check_cat_tests(_advisory(status="REL_PREP", rhnqa=0))
        self.assertEqual(r["status"], "PASS")

    def test_none_advisory(self):
        r = check_cat_tests(None)
        self.assertEqual(r["status"], "WARN")


# ── Status REL_PREP ─────────────────────────────────────────────


class TestStatusRelPrep(unittest.TestCase):
    def test_rel_prep(self):
        r = check_status_rel_prep(_advisory(status="REL_PREP"))
        self.assertEqual(r["status"], "PASS")

    def test_push_ready(self):
        r = check_status_rel_prep(_advisory(status="PUSH_READY"))
        self.assertEqual(r["status"], "PASS")

    def test_shipped(self):
        r = check_status_rel_prep(_advisory(status="SHIPPED_LIVE"))
        self.assertEqual(r["status"], "PASS")

    def test_qe(self):
        r = check_status_rel_prep(_advisory(status="QE"))
        self.assertEqual(r["status"], "FAIL")

    def test_new_files(self):
        r = check_status_rel_prep(_advisory(status="NEW_FILES"))
        self.assertEqual(r["status"], "FAIL")

    def test_none(self):
        r = check_status_rel_prep(None)
        self.assertEqual(r["status"], "WARN")


# ── lib/errata helpers ──────────────────────────────────────────


class TestExtractMicroshiftNVRs(unittest.TestCase):
    def test_variant_arch_format(self):
        builds = {
            "OSE-4.22-RHEL-9": {
                "name": "OSE-4.22-RHEL-9",
                "builds": [{
                    "microshift-4.22.7-202607.el9": {
                        "nvr": "microshift-4.22.7-202607.el9",
                        "variant_arch": {
                            "9Base-RHOSE-4.22": {
                                "x86_64": [
                                    "microshift-4.22.7-202607.el9.x86_64.rpm",
                                    "microshift-selinux-4.22.7-202607.el9.x86_64.rpm",
                                ],
                                "noarch": [
                                    "microshift-greenboot-4.22.7-202607.el9.noarch.rpm",
                                ],
                            }
                        },
                    },
                    "other-pkg-4.22.7-el9": {"nvr": "other-pkg-4.22.7-el9"},
                }]
            }
        }
        nvrs = extract_microshift_nvrs(builds)
        self.assertIn("microshift-4.22.7-202607.el9.x86_64.rpm", nvrs)
        self.assertIn("microshift-selinux-4.22.7-202607.el9.x86_64.rpm", nvrs)
        self.assertIn("microshift-greenboot-4.22.7-202607.el9.noarch.rpm", nvrs)
        self.assertFalse(any("other-pkg" in n for n in nvrs))

    def test_no_variant_arch_fallback(self):
        builds = {
            "RHEL-9-OSE-4.20": [
                {"microshift-4.20.26-202607.el9": {"nvr": "microshift-4.20.26-202607.el9"}},
            ]
        }
        nvrs = extract_microshift_nvrs(builds)
        self.assertEqual(nvrs, ["microshift-4.20.26-202607.el9"])

    def test_empty(self):
        self.assertEqual(extract_microshift_nvrs(None), [])
        self.assertEqual(extract_microshift_nvrs({}), [])


class TestExtractPackageNames(unittest.TestCase):
    def test_basic(self):
        nvrs = [
            "microshift-4.20.26-202607010000.p0.gabc1234.assembly.4.20.26.el9",
            "microshift-selinux-4.20.26-202607010000.p0.gabc1234.assembly.4.20.26.el9",
            "microshift-networking-4.20.26-202607010000.p0.gabc1234.assembly.4.20.26.el9",
        ]
        names = extract_package_names(nvrs)
        self.assertEqual(names, {"microshift", "microshift-selinux", "microshift-networking"})

    def test_empty(self):
        self.assertEqual(extract_package_names([]), set())


class TestExtractBugKeys(unittest.TestCase):
    def test_list_format(self):
        data = [
            {"key": "OCPBUGS-1", "status": "Verified", "summary": "Bug 1"},
            {"key": "OCPBUGS-2", "status": "Modified", "summary": "Bug 2"},
        ]
        bugs = extract_bug_keys(data)
        self.assertEqual(len(bugs), 2)
        self.assertEqual(bugs[0]["key"], "OCPBUGS-1")
        self.assertEqual(bugs[0]["status"], "Verified")

    def test_nested_status(self):
        data = [
            {"key": "OCPBUGS-1", "status": {"name": "Verified"}, "summary": "Bug 1"},
        ]
        bugs = extract_bug_keys(data)
        self.assertEqual(bugs[0]["status"], "Verified")

    def test_jira_issue_key(self):
        data = [
            {"jira_issue": {"key": "OCPBUGS-1", "summary": "Bug 1"}, "status": "Verified"},
        ]
        bugs = extract_bug_keys(data)
        self.assertEqual(bugs[0]["key"], "OCPBUGS-1")

    def test_none(self):
        self.assertEqual(extract_bug_keys(None), [])

    def test_empty(self):
        self.assertEqual(extract_bug_keys([]), [])


# ── Formatting ──────────────────────────────────────────────────


class TestFormatShort(unittest.TestCase):
    def test_sections(self):
        results = [
            {"check": "et_advisory_exists", "status": "PASS",
             "reason": "Advisory found", "details": []},
            {"check": "et_bugs_verified", "status": "PASS",
             "reason": "3/3 verified", "details": []},
            {"check": "et_rpms_present", "status": "PASS",
             "reason": "8/8 RPMs", "details": []},
            {"check": "et_cdn_staging", "status": "PASS",
             "reason": "CDN push completed", "details": []},
        ]
        out = format_text_short("4.20.26", "RHBA-2026:12345", results)
        self.assertIn("── Advisory", out)
        self.assertIn("── Bugs", out)
        self.assertIn("── Builds", out)
        self.assertIn("── Distribution", out)
        self.assertIn("Errata Tool Promotion", out)

    def test_fail_details_shown(self):
        results = [
            {"check": "et_bugs_verified", "status": "FAIL",
             "reason": "1/3 verified", "details": ["OCPBUGS-2: Modified"]},
        ]
        out = format_text_short("4.20.26", "RHBA-2026:12345", results)
        self.assertIn("OCPBUGS-2: Modified", out)


class TestFormatFull(unittest.TestCase):
    def test_markdown(self):
        results = [
            {"check": "et_advisory_exists", "status": "PASS",
             "reason": "Advisory found", "details": []},
            {"check": "et_rpms_present", "status": "PASS",
             "reason": "8/8 RPMs", "details": []},
        ]
        vi = _version("4.20.26")
        out = format_text_full("4.20.26", "RHBA-2026:12345", results, vi)
        self.assertIn("## Advisory", out)
        self.assertIn("## Builds", out)
        self.assertIn("**Summary:**", out)


if __name__ == "__main__":
    unittest.main()
