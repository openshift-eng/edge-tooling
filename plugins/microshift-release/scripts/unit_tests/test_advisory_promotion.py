"""Unit tests for advisory promotion checks (Phase 3)."""

import sys
import os
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from advisory_promotion import (  # noqa: E402
    check_in_advisory,
    check_in_catalog,
    check_repository,
    check_image_sha,
    check_tag_commit_id,
    check_tag_build_date,
    check_no_xy0_tag,
    check_chi_freshness,
    check_image_sha_distinct,
    check_shipment_mr_approved,
    format_text_short,
    format_text_full,
    _expected_repo,
    _all_check_ids,
    _rhel_versions,
    _variants,
)
from validate_artifacts import classify_version  # noqa: E402


def _advisory(images=None, spec_type=None):
    return {"spec_type": spec_type, "images": images}


def _image(arch="amd64", rhel=9, sha="abc123"):
    repo = _expected_repo(rhel)
    comp = f"microshift-bootc-rhel{rhel}"
    return {
        "component": comp,
        "architecture": arch,
        "arch_key": f"{arch}/el{rhel}",
        "containerImage": f"{repo}@sha256:{sha}",
        "sha": sha,
        "repo": repo,
    }


def _catalog(tags=None, commit_id=None, valid=True, freshness_grade=None):
    image = {
        "tags": tags or [],
        "commit_id": commit_id,
        "commit_short": commit_id[:7] if commit_id else None,
        "image_id": "test-id",
        "freshness_grade": freshness_grade,
    }
    return {"valid": valid, "image": image, "catalog": "stage"}


def _version(v="4.18.3"):
    return classify_version(v)


# ── Version-based variant selection ──────────────────────────────


class TestRhelVersions(unittest.TestCase):
    def test_el9_only_old(self):
        self.assertEqual(_rhel_versions(_version("4.18.3")), [9])

    def test_el9_only_422_1(self):
        self.assertEqual(_rhel_versions(_version("4.22.1")), [9])

    def test_el9_el10_422_2(self):
        self.assertEqual(_rhel_versions(_version("4.22.2")), [9, 10])

    def test_el9_el10_423(self):
        self.assertEqual(_rhel_versions(_version("4.23.0")), [9, 10])

    def test_el9_el10_422_10(self):
        self.assertEqual(_rhel_versions(_version("4.22.10")), [9, 10])


class TestVariants(unittest.TestCase):
    def test_el9_only(self):
        v = _variants(_version("4.18.3"))
        self.assertEqual(v, [("amd64", 9), ("arm64", 9)])

    def test_el9_el10(self):
        v = _variants(_version("4.22.2"))
        self.assertEqual(v, [("amd64", 9), ("amd64", 10),
                              ("arm64", 9), ("arm64", 10)])


class TestCheckIds(unittest.TestCase):
    def test_el9_only(self):
        ids = _all_check_ids(_version("4.18.3"))
        self.assertTrue(all("el10" not in i for i in ids))
        el9_ids = [i for i in ids if i.startswith("amd64_el9_")]
        self.assertEqual(len(el9_ids), 13)

    def test_el9_el10(self):
        ids = _all_check_ids(_version("4.22.2"))
        el9_variant = [i for i in ids if i.startswith(("amd64_el9_", "arm64_el9_"))]
        el10_variant = [i for i in ids if i.startswith(("amd64_el10_", "arm64_el10_"))]
        self.assertEqual(len(el9_variant), 26)  # 13 per arch * 2 arches
        self.assertEqual(len(el10_variant), 26)
        self.assertIn("advisory_sha_distinct_el9", ids)
        self.assertIn("advisory_sha_distinct_el10", ids)
        self.assertIn("advisory_sha_distinct_el9", ids)
        self.assertIn("advisory_sha_distinct_el10", ids)


# ── Per-variant: in_advisory ─────────────────────────────────────


class TestInAdvisory(unittest.TestCase):
    def test_present(self):
        adv = _advisory([_image("amd64", 9), _image("arm64", 9)])
        r = check_in_advisory("amd64_el9", "amd64", 9, adv)
        self.assertEqual(r["status"], "PASS")

    def test_missing(self):
        adv = _advisory([_image("amd64", 9)])
        r = check_in_advisory("arm64_el9", "arm64", 9, adv)
        self.assertEqual(r["status"], "FAIL")

    def test_el10_present(self):
        adv = _advisory([_image("amd64", 10)])
        r = check_in_advisory("amd64_el10", "amd64", 10, adv)
        self.assertEqual(r["status"], "PASS")

    def test_no_advisory(self):
        r = check_in_advisory("amd64_el9", "amd64", 9, None)
        self.assertEqual(r["status"], "FAIL")


# ── Per-variant: repository ──────────────────────────────────────


class TestRepository(unittest.TestCase):
    def test_correct_el9(self):
        adv = _advisory([_image("amd64", 9)])
        r = check_repository("amd64_el9", "amd64", 9, 4, adv)
        self.assertEqual(r["status"], "PASS")

    def test_correct_el10(self):
        adv = _advisory([_image("amd64", 10)])
        r = check_repository("amd64_el10", "amd64", 10, 4, adv)
        self.assertEqual(r["status"], "PASS")

    def test_wrong_rhel(self):
        adv = _advisory([_image("amd64", 9)])
        # Checking el10 but image is el9 — won't find it
        r = check_repository("amd64_el10", "amd64", 10, 4, adv)
        self.assertEqual(r["status"], "WARN")

    def test_wrong_repo(self):
        img = _image("amd64", 9)
        img["repo"] = "registry.redhat.io/wrong"
        adv = _advisory([img])
        r = check_repository("amd64_el9", "amd64", 9, 4, adv)
        self.assertEqual(r["status"], "FAIL")


# ── Per-variant: image_sha ───────────────────────────────────────


class TestImageSha(unittest.TestCase):
    def test_present(self):
        adv = _advisory([_image("amd64", 9, sha="deadbeef")])
        r = check_image_sha("amd64_el9", "amd64", 9, adv)
        self.assertEqual(r["status"], "PASS")

    def test_empty(self):
        img = _image("amd64", 9)
        img["sha"] = ""
        adv = _advisory([img])
        r = check_image_sha("amd64_el9", "amd64", 9, adv)
        self.assertEqual(r["status"], "FAIL")


# ── Per-variant: tag_commit_id ───────────────────────────────────


class TestTagCommitId(unittest.TestCase):
    def test_matches(self):
        tags = [{"name": "v4.18-202606151054.p2.g7f7539e.assembly.4.18.3.el9"}]
        r = check_tag_commit_id("amd64_el9", "stage", _catalog(tags=tags, commit_id="7f7539e123456"))
        self.assertEqual(r["status"], "PASS")

    def test_mismatch(self):
        tags = [{"name": "v4.18-202606151054.p2.g7f7539e.assembly.4.18.3.el9"}]
        r = check_tag_commit_id("amd64_el9", "stage", _catalog(tags=tags, commit_id="abc1234567890"))
        self.assertEqual(r["status"], "FAIL")

    def test_no_catalog(self):
        r = check_tag_commit_id("amd64_el9", "stage", None)
        self.assertEqual(r["status"], "SKIP")


# ── Per-variant: tag_build_date ──────────────────────────────────


class TestTagBuildDate(unittest.TestCase):
    def test_valid(self):
        tags = [{"name": "v4.18-202606151054.p2.g7f7539e.assembly.4.18.3.el9"}]
        r = check_tag_build_date("amd64_el9", "stage", _catalog(tags=tags))
        self.assertEqual(r["status"], "PASS")
        self.assertIn("2026-06-15 10:54", r["reason"])

    def test_no_catalog(self):
        r = check_tag_build_date("amd64_el9", "stage", None)
        self.assertEqual(r["status"], "SKIP")


# ── Per-variant: no_xy0_tag ──────────────────────────────────────


class TestNoXY0Tag(unittest.TestCase):
    def test_zstream_clean(self):
        tags = [{"name": "v4.18-202606151054.p2.g7f7539e.assembly.4.18.3.el9"}]
        r = check_no_xy0_tag("amd64_el9", "stage", _catalog(tags=tags), _version("4.18.3"))
        self.assertEqual(r["status"], "PASS")

    def test_zstream_has_xy0(self):
        tags = [
            {"name": "v4.18-202606151054.p2.g7f7539e.assembly.4.18.3.el9"},
            {"name": "v4.18-202601011200.p2.gabc1234.assembly.4.18.0.el9"},
        ]
        r = check_no_xy0_tag("amd64_el9", "stage", _catalog(tags=tags), _version("4.18.3"))
        self.assertEqual(r["status"], "FAIL")

    def test_xy0_skipped(self):
        r = check_no_xy0_tag("amd64_el9", "stage", _catalog(), _version("4.18.0"))
        self.assertEqual(r["status"], "SKIP")


# ── Per-variant: chi_freshness ────────────────────────────────────


class TestCHIFreshness(unittest.TestCase):
    def test_grade_a(self):
        r = check_chi_freshness("amd64_el9", "stage", _catalog(freshness_grade="A"))
        self.assertEqual(r["status"], "PASS")
        self.assertIn("A", r["reason"])

    def test_grade_d(self):
        r = check_chi_freshness("amd64_el9", "stage", _catalog(freshness_grade="D"))
        self.assertEqual(r["status"], "FAIL")
        self.assertIn("D", r["reason"])

    def test_no_grade(self):
        r = check_chi_freshness("amd64_el9", "stage", _catalog())
        self.assertEqual(r["status"], "WARN")

    def test_not_fetched(self):
        r = check_chi_freshness("amd64_el9", "prod", None)
        self.assertEqual(r["status"], "SKIP")


# ── Per-variant: check_in_catalog (stage/prod phase) ──────────────


class TestCheckInCatalog(unittest.TestCase):
    def test_stage_mode_skips_prod(self):
        r = check_in_catalog("amd64_el9", "prod", {}, _version("4.18.3"), phase="stage")
        self.assertEqual(r["status"], "SKIP")
        self.assertIn("stage mode", r["reason"])

    def test_prod_mode_checks_prod(self):
        r = check_in_catalog("amd64_el9", "prod", {"valid": True}, _version("4.18.3"), phase="prod")
        self.assertEqual(r["status"], "PASS")

    def test_ec_skips_prod_regardless(self):
        r = check_in_catalog("amd64_el9", "prod", {}, _version("5.0.0-ec.3"), phase="prod")
        self.assertEqual(r["status"], "SKIP")

    def test_stage_always_checked(self):
        r = check_in_catalog("amd64_el9", "stage", {"valid": True}, _version("4.18.3"), phase="stage")
        self.assertEqual(r["status"], "PASS")


# ── Global: image_sha_distinct ───────────────────────────────────


class TestImageShaDistinct(unittest.TestCase):
    def test_distinct(self):
        adv = _advisory([_image("amd64", 9, "aaa"), _image("arm64", 9, "bbb")])
        r = check_image_sha_distinct(9, adv)
        self.assertEqual(r["status"], "PASS")

    def test_identical(self):
        adv = _advisory([_image("amd64", 9, "same"), _image("arm64", 9, "same")])
        r = check_image_sha_distinct(9, adv)
        self.assertEqual(r["status"], "FAIL")

    def test_el10(self):
        adv = _advisory([_image("amd64", 10, "aaa"), _image("arm64", 10, "bbb")])
        r = check_image_sha_distinct(10, adv)
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["check"], "advisory_sha_distinct_el10")


# ── Global: shipment_mr_approved ─────────────────────────────────


class TestShipmentMRApproved(unittest.TestCase):
    def test_skipped(self):
        r = check_shipment_mr_approved({"skipped": True, "reason": "No token"})
        self.assertEqual(r["status"], "WARN")

    def test_not_found(self):
        r = check_shipment_mr_approved({"found": False, "reason": "Not found"})
        self.assertEqual(r["status"], "FAIL")


# ── Formatting ───────────────────────────────────────────────────


class TestFormatShort(unittest.TestCase):
    def test_grouped_by_variant(self):
        results = [
            {"check": "amd64_el9_advisory_image_present", "status": "PASS",
             "reason": "amd64/el9 present", "details": []},
            {"check": "arm64_el9_advisory_image_present", "status": "PASS",
             "reason": "arm64/el9 present", "details": []},
            {"check": "shipment_mr_approved", "status": "PASS",
             "reason": "MR approved", "details": []},
        ]
        vi = _version("4.18.3")
        out = format_text_short("4.18.3", results, vi)
        self.assertIn("── amd64_el9", out)
        self.assertIn("── arm64_el9", out)
        self.assertIn("── Global", out)

    def test_skips_shown(self):
        results = [
            {"check": "amd64_el9_catalog_prod_present", "status": "SKIP",
             "reason": "N/A (EC not shipped to prod)", "details": []},
        ]
        vi = _version("4.18.3")
        out = format_text_short("4.18.3", results, vi)
        self.assertIn("amd64_el9_catalog_prod_present", out)
        self.assertIn("N/A", out)

    def test_el10_sections(self):
        results = [
            {"check": "amd64_el9_advisory_image_present", "status": "PASS",
             "reason": "present", "details": []},
            {"check": "amd64_el10_advisory_image_present", "status": "PASS",
             "reason": "present", "details": []},
        ]
        vi = _version("4.22.2")
        out = format_text_short("4.22.2", results, vi)
        self.assertIn("── amd64_el9", out)
        self.assertIn("── amd64_el10", out)


class TestFormatFull(unittest.TestCase):
    def test_sections(self):
        results = [
            {"check": "amd64_el9_advisory_image_present", "status": "PASS",
             "reason": "present", "details": []},
            {"check": "shipment_mr_approved", "status": "PASS",
             "reason": "approved", "details": []},
        ]
        vi = _version("4.18.3")
        out = format_text_full("4.18.3", results, vi)
        self.assertIn("## amd64_el9", out)
        self.assertIn("## Global", out)


if __name__ == "__main__":
    unittest.main()
