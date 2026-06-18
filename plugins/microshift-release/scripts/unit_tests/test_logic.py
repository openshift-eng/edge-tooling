"""Unit tests for pure logic functions in precheck scripts."""

import sys
import os
import unittest

# Add parent directory to path so we can import the modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from precheck_xyz import (  # noqa: E402
    compute_recommendation, interpret_cves, format_text_short, _build_reason,
)
from lib.ocpbugs import query_resolved_bugs  # noqa: E402
from precheck_ecrc import (  # noqa: E402
    parse_ecrc_version, format_text as ecrc_format_text,
)
from precheck_nightly import (  # noqa: E402
    classify_gap, format_gap, format_text as nightly_format_text,
)


def _ocpbugs(count=0, required=0, not_required=0, review=0):
    """Build an ocpbugs dict for test fixtures."""
    return {
        "count": count, "bugs": [], "skipped": False,
        "release_required": required,
        "release_not_required": not_required,
        "needs_review": review,
    }


class TestClassifyGap(unittest.TestCase):
    def test_ok_zero(self):
        self.assertEqual(classify_gap(0), "OK")

    def test_ok_boundary(self):
        self.assertEqual(classify_gap(24), "OK")

    def test_ask_art_just_over(self):
        self.assertEqual(classify_gap(24.1), "ASK ART")

    def test_ask_art_large(self):
        self.assertEqual(classify_gap(200), "ASK ART")

    def test_negative(self):
        self.assertEqual(classify_gap(-1), "OK")


class TestFormatGap(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_gap(0), "0h")

    def test_hours_only(self):
        self.assertEqual(format_gap(13), "13h")

    def test_days_and_hours(self):
        self.assertEqual(format_gap(75), "3d 3h")

    def test_negative(self):
        self.assertEqual(format_gap(-5), "0h")

    def test_exactly_one_day(self):
        self.assertEqual(format_gap(24), "1d 0h")


class TestParseEcrcVersion(unittest.TestCase):
    def test_ec(self):
        result = parse_ecrc_version("4.22.0-ec.5")
        self.assertEqual(result["type"], "EC")
        self.assertEqual(result["base"], "4.22.0")
        self.assertEqual(result["num"], 5)
        self.assertEqual(result["minor"], "4.22")

    def test_rc(self):
        result = parse_ecrc_version("4.22.0-rc.1")
        self.assertEqual(result["type"], "RC")
        self.assertEqual(result["num"], 1)

    def test_invalid(self):
        self.assertIsNone(parse_ecrc_version("4.22.0"))

    def test_invalid_format(self):
        self.assertIsNone(parse_ecrc_version("not-a-version"))


class TestInterpretCves(unittest.TestCase):
    def test_no_report(self):
        result = interpret_cves(None)
        self.assertEqual(result["impact"], "unknown")

    def test_skipped_report(self):
        result = interpret_cves({"skipped": True, "error": "no VPN"})
        self.assertEqual(result["impact"], "unknown")

    def test_no_cves(self):
        report = {
            "RHBA-2026:12345": {"type": "extras", "cves": {}},
            "RHBA-2026:12346": {"type": "image", "cves": {}},
        }
        result = interpret_cves(report)
        self.assertEqual(result["impact"], "none")

    def test_cve_not_affected(self):
        report = {
            "RHSA-2026:12345": {
                "type": "image",
                "cves": {"CVE-2026-1234": {}},
            }
        }
        result = interpret_cves(report)
        self.assertEqual(result["impact"], "none")

    def test_cve_done_errata_skipped(self):
        report = {
            "RHSA-2026:12345": {
                "type": "image",
                "cves": {
                    "CVE-2026-1234": {
                        "jira_ticket": {
                            "id": "USHIFT-999",
                            "resolution": "Done-Errata",
                            "status": "Closed",
                        }
                    }
                },
            }
        }
        result = interpret_cves(report)
        self.assertEqual(result["impact"], "none")

    def test_cve_needs_review(self):
        report = {
            "RHSA-2026:12345": {
                "type": "image",
                "cves": {
                    "CVE-2026-5678": {
                        "jira_ticket": {
                            "id": "USHIFT-100",
                            "resolution": "",
                            "status": "In Progress",
                        }
                    }
                },
            }
        }
        result = interpret_cves(report)
        self.assertEqual(result["impact"], "needs_review")

    def test_cve_must_release_resolution_done(self):
        report = {
            "RHSA-2026:12345": {
                "type": "image",
                "cves": {
                    "CVE-2026-9999": {
                        "jira_ticket": {
                            "id": "OCPBUGS-80721",
                            "resolution": "Done",
                            "status": "Closed",
                        }
                    }
                },
            }
        }
        result = interpret_cves(report)
        self.assertEqual(result["impact"], "must_release")

    def test_cve_needs_review_status_verified(self):
        report = {
            "RHSA-2026:12345": {
                "type": "extras",
                "cves": {
                    "CVE-2026-8888": {
                        "jira_ticket": {
                            "id": "OCPBUGS-12345",
                            "resolution": "",
                            "status": "Verified",
                        }
                    }
                },
            }
        }
        result = interpret_cves(report)
        self.assertEqual(result["impact"], "needs_review")

    def test_metadata_skipped(self):
        report = {
            "RHBA-2026:99999": {
                "type": "metadata",
                "cves": {
                    "CVE-2026-0000": {
                        "jira_ticket": {
                            "id": "USHIFT-1",
                            "resolution": "Done-Errata",
                            "status": "Closed",
                        }
                    }
                },
            }
        }
        result = interpret_cves(report)
        self.assertEqual(result["impact"], "none")


class TestComputeRecommendation(unittest.TestCase):
    def test_must_release_cve(self):
        evaluation = {
            "cve_impact": {"impact": "must_release", "details": [{"cve": "CVE-2026-1"}]},
            "commits": 5,
            "days_since": 10,
            "ocp_status": "available",
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "ASK ART TO CREATE ARTIFACTS")
        self.assertIn("CVE fix", reason)

    def test_90_day_rule(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 3,
            "days_since": 95,
            "ocp_status": "available",
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "ASK ART TO CREATE ARTIFACTS")
        self.assertIn("90-day", reason)

    def test_skip_no_commits(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 0,
            "days_since": 10,
        }
        rec, _ = compute_recommendation(evaluation)
        self.assertEqual(rec, "SKIP")

    def test_skip_commits_no_cves(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 10,
            "days_since": 30,
        }
        rec, _ = compute_recommendation(evaluation)
        self.assertEqual(rec, "SKIP")

    def test_needs_review_cve_in_progress(self):
        evaluation = {
            "cve_impact": {"impact": "needs_review"},
            "commits": 5,
            "days_since": 10,
        }
        rec, _ = compute_recommendation(evaluation)
        self.assertEqual(rec, "NEEDS REVIEW")

    def test_needs_review_unknown_advisory(self):
        evaluation = {
            "cve_impact": {"impact": "unknown"},
            "commits": 5,
            "days_since": 10,
        }
        rec, _ = compute_recommendation(evaluation)
        self.assertEqual(rec, "NEEDS REVIEW")

    def test_skip_unknown_no_commits(self):
        evaluation = {
            "cve_impact": {"impact": "unknown"},
            "commits": 0,
            "days_since": 10,
        }
        rec, _ = compute_recommendation(evaluation)
        self.assertEqual(rec, "SKIP")

    def test_ocpbugs_triggers_needs_review(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 5,
            "days_since": 30,
            "ocp_status": "available",
            "ocpbugs": _ocpbugs(count=3, review=3),
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "NEEDS REVIEW")
        self.assertIn("OCPBUGS", reason)

    def test_ocpbugs_needs_review_no_ocp(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 5,
            "days_since": 30,
            "ocp_status": "not_available",
            "ocpbugs": _ocpbugs(count=2, review=2),
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "NEEDS REVIEW")
        self.assertIn("OCPBUGS", reason)

    def test_no_ocpbugs_still_skip(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 5,
            "days_since": 30,
            "ocp_status": "available",
            "ocpbugs": {"count": 0, "bugs": [], "skipped": False},
        }
        rec, _ = compute_recommendation(evaluation)
        self.assertEqual(rec, "SKIP")

    def test_90_day_overrides_release_not_required(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 5,
            "days_since": 95,
            "ocp_status": "available",
            "ocpbugs": _ocpbugs(count=1, not_required=1),
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "ASK ART TO CREATE ARTIFACTS")
        self.assertIn("90-day", reason)

    def test_release_required_label(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 5,
            "days_since": 30,
            "ocp_status": "available",
            "ocpbugs": _ocpbugs(count=2, required=1, not_required=1),
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "ASK ART TO CREATE ARTIFACTS")
        self.assertIn("release-required", reason)

    def test_release_not_required_label_skips(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 5,
            "days_since": 30,
            "ocp_status": "available",
            "ocpbugs": _ocpbugs(count=1, not_required=1),
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "SKIP")
        self.assertIn("release-not-required", reason)

    def test_release_required_no_ocp(self):
        evaluation = {
            "cve_impact": {"impact": "none"},
            "commits": 5,
            "days_since": 30,
            "ocp_status": "not_available",
            "ocpbugs": _ocpbugs(count=1, required=1),
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "NEEDS REVIEW")
        self.assertIn("OCP payload not yet available", reason)

    def test_cve_takes_priority_over_ocpbugs(self):
        evaluation = {
            "cve_impact": {"impact": "must_release", "details": [{"cve": "CVE-2026-1"}]},
            "commits": 5,
            "days_since": 10,
            "ocp_status": "available",
            "ocpbugs": {"count": 3, "bugs": [], "skipped": False},
        }
        rec, reason = compute_recommendation(evaluation)
        self.assertEqual(rec, "ASK ART TO CREATE ARTIFACTS")
        self.assertIn("CVE fix", reason)


class TestNightlyFormatText(unittest.TestCase):
    def test_empty_branches(self):
        self.assertEqual(nightly_format_text([]), "No branches to check.")

    def test_ok_branch(self):
        branches = [{
            "stream": "4.21",
            "branch": "release-4.21",
            "status": "OK",
            "ocp_timestamp": "2026-04-10T14:30:00",
            "brew_timestamp": "2026-04-10T12:00:00",
            "gap_display": "2h",
        }]
        result = nightly_format_text(branches)
        self.assertIn("OK", result)
        self.assertIn("release-4.21", result)
        self.assertIn("2h", result)

    def test_eol_branch(self):
        branches = [{
            "stream": "4.14",
            "branch": "release-4.14",
            "status": "EOL",
            "lifecycle_phase": "End of life",
            "end_date": "2025-10-31",
        }]
        result = nightly_format_text(branches)
        self.assertIn("EOL", result)
        self.assertIn("End of life", result)

    def test_error_branch(self):
        branches = [{
            "stream": "4.21",
            "branch": "release-4.21",
            "status": "ERROR",
            "ocp_error": "timeout",
        }]
        result = nightly_format_text(branches)
        self.assertIn("ERROR", result)
        self.assertIn("timeout", result)

    def test_verbose_shows_nvr(self):
        branches = [{
            "stream": "4.21",
            "branch": "release-4.21",
            "status": "OK",
            "ocp_timestamp": "2026-04-10T14:30:00",
            "brew_timestamp": "2026-04-10T12:00:00",
            "ocp_nightly": "4.21.0-0.nightly-2026-04-10-143000",
            "brew_build": "microshift-4.21.0~0.nightly_2026_04_10_120000",
            "gap_display": "2h",
        }]
        result = nightly_format_text(branches, verbose=True)
        self.assertIn("OCP: 4.21.0-0.nightly", result)
        self.assertIn("Brew: microshift-4.21", result)


class TestEcrcFormatText(unittest.TestCase):
    def test_ready(self):
        data = {
            "version": "4.22.0-ec.5",
            "status": "READY",
            "ocp_phase": "Accepted",
            "brew": {"found": True, "build_date": "2026-04-09"},
        }
        result = ecrc_format_text(data)
        self.assertIn("OK", result)
        self.assertIn("4.22.0-ec.5", result)
        self.assertIn("2026-04-09", result)

    def test_ocp_pending(self):
        data = {
            "version": "4.22.0-ec.6",
            "status": "OCP_PENDING",
            "ocp_phase": "Pending",
        }
        result = ecrc_format_text(data)
        self.assertIn("ASK ART", result)
        self.assertIn("Pending", result)

    def test_not_found(self):
        data = {
            "version": "4.22.0-ec.99",
            "status": "NOT_FOUND",
        }
        result = ecrc_format_text(data)
        self.assertIn("not on release controller", result)

    def test_type_mismatch(self):
        data = {
            "type": "EC",
            "actual_type": "RC",
            "type_mismatch": True,
            "version": "4.22.0-rc.1",
        }
        result = ecrc_format_text(data)
        self.assertIn("OK", result)
        self.assertIn("No active EC", result)
        self.assertIn("latest is RC", result)

    def test_verbose_next_versions(self):
        data = {
            "version": "4.22.0-ec.5",
            "status": "READY",
            "ocp_phase": "Accepted",
            "brew": {"found": True, "build_date": "2026-04-09"},
            "next_versions": [
                {"version": "4.22.0-ec.6", "exists": False},
                {"version": "4.22.0-rc.1", "exists": True, "phase": "Accepted"},
            ],
        }
        result = ecrc_format_text(data, verbose=True)
        self.assertIn("Next:", result)
        self.assertIn("4.22.0-rc.1 (Accepted)", result)
        self.assertIn("4.22.0-ec.6 (not found)", result)

    def test_brew_error(self):
        data = {
            "version": "4.22.0-ec.5",
            "status": "RPMS_NOT_BUILT",
            "ocp_phase": "Accepted",
            "brew": {"found": False, "error": "VPN not connected"},
        }
        result = ecrc_format_text(data)
        self.assertIn("VPN not connected", result)

    def test_brew_not_found(self):
        data = {
            "version": "4.22.0-ec.5",
            "status": "RPMS_NOT_BUILT",
            "ocp_phase": "Accepted",
            "brew": {"found": False},
        }
        result = ecrc_format_text(data)
        self.assertIn("not found", result)


class TestXyzFormatTextShort(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(format_text_short([]), "No versions to evaluate.")

    def test_already_released(self):
        evals = [{"version": "4.21.7", "recommendation": "ALREADY RELEASED"}]
        result = format_text_short(evals)
        self.assertIn("ALREADY RELEASED", result)
        self.assertIn("4.21.7", result)
        self.assertNotIn("OCP:", result)

    def test_skip_with_ocp(self):
        evals = [{
            "version": "4.18.37",
            "recommendation": "SKIP",
            "ocp_status": "available",
            "cve_impact": {"impact": "none"},
            "last_released": "4.18.36",
            "days_since": 24,
        }]
        result = format_text_short(evals)
        self.assertIn("SKIP", result)
        self.assertIn("[OCP: available]", result)
        self.assertIn("no CVEs", result)

    def test_ask_art(self):
        evals = [{
            "version": "4.21.9",
            "recommendation": "ASK ART TO CREATE ARTIFACTS",
            "ocp_status": "not_available",
            "cve_impact": {"impact": "must_release", "details": [{"cve": "CVE-2026-1"}]},
        }]
        result = format_text_short(evals)
        self.assertIn("ASK ART TO CREATE ARTIFACTS", result)
        self.assertIn("[OCP: NOT available]", result)


class TestBuildReason(unittest.TestCase):
    def test_no_data(self):
        self.assertEqual(_build_reason({}), "advisory unknown")

    def test_no_cves_with_last(self):
        result = _build_reason({
            "cve_impact": {"impact": "none"},
            "last_released": "4.21.6",
            "days_since": 30,
        })
        self.assertIn("no CVEs", result)
        self.assertIn("last: 4.21.6 (30d ago)", result)

    def test_advisory_skipped(self):
        result = _build_reason({
            "cve_impact": {"impact": "unknown"},
            "advisory_report": {"skipped": True, "error": "no VPN"},
        })
        self.assertIn("advisory report unavailable", result)

    def test_ocpbugs_in_reason(self):
        result = _build_reason({
            "cve_impact": {"impact": "none"},
            "ocpbugs": _ocpbugs(count=3, required=1, not_required=2),
            "last_released": "4.21.7",
            "days_since": 30,
        })
        self.assertIn("3 OCPBUGS", result)
        self.assertIn("1 release-required", result)
        self.assertIn("2 release-not-required", result)

    def test_no_ocpbugs_in_reason(self):
        result = _build_reason({
            "cve_impact": {"impact": "none"},
            "ocpbugs": {"count": 0, "bugs": [], "skipped": False},
            "last_released": "4.21.7",
            "days_since": 30,
        })
        self.assertIn("no OCPBUGS", result)

    def test_ocpbugs_skipped_not_shown(self):
        result = _build_reason({
            "cve_impact": {"impact": "none"},
            "ocpbugs": {"count": 0, "bugs": [], "skipped": True},
            "last_released": "4.21.7",
            "days_since": 30,
        })
        self.assertNotIn("OCPBUGS", result)


class TestCommitBugScanning(unittest.TestCase):
    """Commit-referenced bugs: always unenriched (MCP enriches at skill level)."""

    def test_bugs_unenriched(self):
        from unittest.mock import patch

        with patch("lib.ocpbugs.extract_bugs_from_commits",
                   return_value={"OCPBUGS-80721", "OCPBUGS-12345"}):
            result = query_resolved_bugs(
                "4.21.16", branch="release-4.21",
                since_version="4.21.11",
            )

        self.assertEqual(result["count"], 2)
        self.assertEqual(result["needs_review"], 2)
        self.assertEqual(result["release_required"], 0)
        self.assertEqual(result["release_not_required"], 0)
        keys = {b["key"] for b in result["bugs"]}
        self.assertIn("OCPBUGS-80721", keys)
        self.assertIn("OCPBUGS-12345", keys)
        for bug in result["bugs"]:
            self.assertEqual(bug["summary"], "Pending Jira lookup")
            self.assertEqual(bug["release_action"], "needs_review")
            self.assertEqual(bug["source"], "commit")

    def test_no_bugs_found(self):
        from unittest.mock import patch

        with patch("lib.ocpbugs.extract_bugs_from_commits",
                   return_value=set()):
            result = query_resolved_bugs(
                "4.21.16", branch="release-4.21",
                since_version="4.21.11",
            )

        self.assertEqual(result["count"], 0)
        self.assertFalse(result["skipped"])


class TestExtractCommitFromNvr(unittest.TestCase):
    """Tests for brew.extract_commit_from_nvr NVR parsing."""

    def _call_with_mock(self, rpms_result):
        """Call extract_commit_from_nvr with a mocked find_zstream_rpms."""
        from unittest.mock import patch
        with patch("lib.brew.find_zstream_rpms", return_value=rpms_result):
            from lib.brew import extract_commit_from_nvr
            return extract_commit_from_nvr("4.21.11")

    def test_valid_nvr_with_commit(self):
        """NVR with g<sha> suffix returns the commit hash."""
        rpms = {
            "found": True,
            "nvr": "microshift-4.21.11-202604201054.p0.g7f7539e.assembly.4.21.11.el9",
            "build_date": "2026-04-20",
        }
        self.assertEqual(self._call_with_mock(rpms), "7f7539e")

    def test_valid_nvr_long_commit(self):
        """NVR with a longer commit hash is also extracted."""
        rpms = {
            "found": True,
            "nvr": "microshift-4.18.36-202603150930.p0.gabcdef0123.assembly.4.18.36.el9",
            "build_date": "2026-03-15",
        }
        self.assertEqual(self._call_with_mock(rpms), "abcdef0123")

    def test_nvr_without_commit_suffix(self):
        """NVR without g<sha> suffix returns None."""
        rpms = {
            "found": True,
            "nvr": "microshift-4.21.11-202604201054.p0.assembly.4.21.11.el9",
            "build_date": "2026-04-20",
        }
        self.assertIsNone(self._call_with_mock(rpms))

    def test_rpm_not_found(self):
        """No matching RPM in Brew returns None."""
        rpms = {"found": False}
        self.assertIsNone(self._call_with_mock(rpms))


class TestFindLatestFromErrata(unittest.TestCase):
    """Tests for pyxis._find_latest_from_errata Hydra API parsing."""

    def _call_with_mock(self, json_response=None, exc=None):
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        if exc:
            with patch("lib.pyxis.requests.get", side_effect=exc):
                from lib.pyxis import _find_latest_from_errata
                return _find_latest_from_errata("4.16")
        mock_resp.json.return_value = json_response
        mock_resp.raise_for_status.return_value = None
        with patch("lib.pyxis.requests.get", return_value=mock_resp):
            from lib.pyxis import _find_latest_from_errata
            return _find_latest_from_errata("4.16")

    def test_happy_path(self):
        data = {"response": {"docs": [
            {"portal_synopsis": "Red Hat build of MicroShift 4.16.58 security update",
             "portal_publication_date": "2026-03-19T00:00:00Z"},
        ]}}
        result = self._call_with_mock(data)
        self.assertEqual(result["version"], "4.16.58")
        self.assertEqual(result["z"], 58)
        self.assertEqual(result["date"], "2026-03-19")

    def test_no_matching_synopsis(self):
        data = {"response": {"docs": [
            {"portal_synopsis": "OpenShift Container Platform update",
             "portal_publication_date": "2026-03-19T00:00:00Z"},
        ]}}
        self.assertIsNone(self._call_with_mock(data))

    def test_empty_docs(self):
        data = {"response": {"docs": []}}
        self.assertIsNone(self._call_with_mock(data))

    def test_network_failure(self):
        import requests
        self.assertIsNone(self._call_with_mock(
            exc=requests.RequestException("timeout")))


class TestIsVersionPublishedErrataFallback(unittest.TestCase):
    """Tests for the errata fallback in is_version_published."""

    def _call(self, version, errata_result):
        from unittest.mock import patch
        # Pyxis returns nothing (all pages empty)
        with patch("lib.pyxis._fetch_page", return_value="{}"):
            with patch("lib.pyxis._find_latest_from_errata",
                       return_value=errata_result):
                from lib.pyxis import is_version_published
                return is_version_published(version, pages=1)

    def test_version_before_latest(self):
        errata = {"version": "4.16.58", "z": 58, "date": "2026-03-19"}
        self.assertTrue(self._call("4.16.57", errata))

    def test_version_equals_latest(self):
        errata = {"version": "4.16.58", "z": 58, "date": "2026-03-19"}
        self.assertTrue(self._call("4.16.58", errata))

    def test_version_after_latest(self):
        errata = {"version": "4.16.58", "z": 58, "date": "2026-03-19"}
        self.assertFalse(self._call("4.16.60", errata))

    def test_no_errata(self):
        self.assertFalse(self._call("4.16.60", None))


class TestEolSkipFormatting(unittest.TestCase):
    """Tests for EOL version display in format_text_short."""

    def test_eol_version(self):
        evals = [{
            "version": "4.14.30",
            "recommendation": "SKIP",
            "lifecycle_status": "End of life",
        }]
        result = format_text_short(evals)
        self.assertIn("End of life", result)
        self.assertIn("SKIP", result)
        # Should not contain OCP status or advisory info
        self.assertNotIn("OCP:", result)

    def test_eol_mixed_with_active(self):
        evals = [
            {"version": "4.15.63", "recommendation": "SKIP",
             "lifecycle_status": "End of life"},
            {"version": "4.21.12", "recommendation": "SKIP",
             "ocp_status": "available",
             "cve_impact": {"impact": "none"},
             "days_since": 2, "last_released": "4.21.11"},
        ]
        result = format_text_short(evals)
        lines = result.strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("End of life", lines[0])
        self.assertIn("OCP:", lines[1])


class TestBuildRevisionRange(unittest.TestCase):
    """Tests for git_ops.build_revision_range priority ordering."""

    def test_tag_wins_over_commit(self):
        """When a tag exists, since_commit is ignored."""
        from unittest.mock import patch
        with patch("lib.git_ops.find_version_tag",
                   return_value="4.21.7-202603230928.p0"):
            from lib.git_ops import build_revision_range
            rev = build_revision_range(
                "release-4.21", "4.21.7", "abc1234")
            self.assertIn("4.21.7-202603230928.p0", rev)
            self.assertNotIn("abc1234", rev)

    def test_commit_used_when_no_tag(self):
        from unittest.mock import patch
        with patch("lib.git_ops.find_version_tag", return_value=None):
            from lib.git_ops import build_revision_range
            rev = build_revision_range(
                "release-4.21", "4.21.11", "7f7539e")
            self.assertEqual(rev, "7f7539e..origin/release-4.21")

    def test_full_branch_when_nothing(self):
        from unittest.mock import patch
        with patch("lib.git_ops.find_version_tag", return_value=None):
            from lib.git_ops import build_revision_range
            rev = build_revision_range("release-4.21", None, None)
            self.assertEqual(rev, "origin/release-4.21")


if __name__ == "__main__":
    unittest.main()
