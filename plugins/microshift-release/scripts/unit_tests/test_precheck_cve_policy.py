"""Release-policy regressions across advisory, Jira, and report paths."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import jira_client
from lib.ocpbugs import query_resolved_bugs
from precheck_xyz import (
    _build_reason,
    compute_recommendation,
    enrich_advisory_cves,
    format_text_full,
    format_text_short,
    interpret_cves,
)


def _report(*tickets):
    """Build advisory CVEs from (CVE ID, tracker fields) pairs."""
    return {"RHSA-2026:12345": {
        "type": "image",
        "cves": {cve: {"jira_ticket": ticket} for cve, ticket in tickets},
    }}


def _evaluation(report, **overrides):
    """Interpret an advisory and compute its final release recommendation."""
    result = {
        "version": "4.22.1", "commits": 0, "days_since": 10,
        "ocp_status": "available", "advisory_report": report,
        "cve_impact": interpret_cves(report),
    }
    result.update(overrides)
    result["recommendation"], result["reason"] = compute_recommendation(result)
    return result


class TestAdvisoryCvePolicy(unittest.TestCase):
    def test_verified_without_resolution_needs_review_even_without_commits(self):
        for commits in (0, 5):
            for ocp_status in ("available", "not_available"):
                with self.subTest(commits=commits, ocp_status=ocp_status):
                    result = _evaluation(_report(("CVE-2026-1", {
                        "id": "OCPBUGS-1", "status": "Verified", "resolution": "",
                    })), commits=commits, ocp_status=ocp_status)
                    self.assertEqual(result["recommendation"], "NEEDS REVIEW")
                    self.assertIn("CVE-2026-1", result["reason"])
                    self.assertIn("OCPBUGS-1", result["reason"])
                    self.assertIn("resolution is empty", result["reason"])
                    self.assertNotIn("not yet verified", result["reason"])

    def test_explicit_labels_override_verified_without_resolution(self):
        for labels, expected in (
                (["release-required"], "ASK ART TO CREATE ARTIFACTS"),
                (["release-not-required"], "SKIP"),
                (["release-required", "release-not-required"],
                 "ASK ART TO CREATE ARTIFACTS")):
            with self.subTest(labels=labels):
                result = _evaluation(_report(("CVE-2026-1", {
                    "id": "OCPBUGS-1", "status": "Verified", "resolution": "",
                    "labels": labels,
                })))
                self.assertEqual(result["recommendation"], expected)
                self.assertEqual(result["cve_impact"]["needs_review_cves"], [])

    def test_release_not_required_overrides_done(self):
        result = _evaluation(_report(("CVE-2026-1", {
            "id": "OCPBUGS-1", "status": "Closed", "resolution": "Done",
            "labels": ["release-not-required"],
        })))
        self.assertEqual(result["recommendation"], "SKIP")
        self.assertEqual(result["cve_impact"]["impact"], "none")

    def test_already_shipped_or_non_applicable_cves_remain_excluded(self):
        for resolution in ("Done-Errata", "Not a Bug", "Won't Do", "Duplicate"):
            with self.subTest(resolution=resolution):
                result = _evaluation(_report(("CVE-2026-1", {
                    "id": "OCPBUGS-1", "status": "Verified", "resolution": resolution,
                    "labels": ["release-required"],
                })))
                self.assertEqual(result["recommendation"], "SKIP")
                self.assertEqual(result["cve_impact"]["impact"], "none")
                output = format_text_full({"evaluations": [result]})
                self.assertNotIn("must release", output)

    def test_review_and_pending_enrichment_are_both_reported(self):
        report = _report(("CVE-2026-1", {
            "id": "OCPBUGS-1", "status": "Verified", "resolution": "",
        }))
        report["RHSA-2026:12345"]["cves"]["CVE-2026-2"] = {"pending": True}
        result = _evaluation(report)
        self.assertEqual(result["recommendation"], "NEEDS REVIEW")
        self.assertIn("resolution is empty", result["reason"])
        self.assertIn("1 advisory CVEs pending enrichment", result["reason"])

    def test_review_is_deduplicated_across_advisories(self):
        report = _report(("CVE-2026-1", {
            "id": "OCPBUGS-1", "status": "Verified", "resolution": "",
        }))
        report["RHSA-2026:54321"] = report["RHSA-2026:12345"]
        result = _evaluation(report)
        self.assertEqual(len(result["cve_impact"]["needs_review_cves"]), 1)
        self.assertEqual(result["reason"].count("resolution is empty"), 1)

    def test_done_fix_wins_over_review_and_retains_warning(self):
        for ocp_status, expected in (
                ("available", "ASK ART TO CREATE ARTIFACTS"),
                ("not_available", "BLOCKED")):
            with self.subTest(ocp_status=ocp_status):
                result = _evaluation(_report(
                    ("CVE-2026-1", {"id": "OCPBUGS-1", "status": "Closed",
                                    "resolution": "Done"}),
                    ("CVE-2026-2", {"id": "OCPBUGS-2", "status": "Verified",
                                    "resolution": ""}),
                ), ocp_status=ocp_status)
                self.assertEqual(result["recommendation"], expected)
                self.assertEqual(result["cve_impact"]["impact"], "must_release")
                self.assertEqual(len(result["cve_impact"]["details"]), 1)
                self.assertIn("1 CVE fix", result["reason"])
                self.assertIn("CVE-2026-2", result["reason"])
                self.assertIn("resolution is empty", result["reason"])

    def test_uncertain_cves_need_review_regardless_of_source_commits(self):
        for impact in (
                {"impact": "unknown"},
                {"impact": "pending_enrichment",
                 "pending_cve_enrichment": ["CVE-2026-1"]}):
            for commits in (0, 1):
                for days in (10, 95):
                    for ocp_status in ("available", "not_available"):
                        with self.subTest(impact=impact, commits=commits,
                                          days=days, ocp_status=ocp_status):
                            # The 90-day policy still requires a release when
                            # there are changes, regardless of advisory data.
                            wanted = "NEEDS REVIEW"
                            if days >= 90 and commits:
                                wanted = ("ASK ART TO CREATE ARTIFACTS"
                                          if ocp_status == "available" else "BLOCKED")
                            recommendation, _ = compute_recommendation({
                                "cve_impact": impact, "commits": commits,
                                "days_since": days, "ocp_status": ocp_status,
                            })
                            self.assertEqual(recommendation, wanted)

    def test_release_not_required_bugs_do_not_clear_uncertain_advisory_cves(self):
        for impact in (
                {"impact": "unknown"},
                {"impact": "pending_enrichment",
                 "pending_cve_enrichment": ["CVE-2026-1"]}):
            with self.subTest(impact=impact):
                recommendation, _ = compute_recommendation({
                    "cve_impact": impact, "commits": 0,
                    "ocpbugs": {"count": 1, "release_required": 0,
                                "release_not_required": 1, "needs_review": 0,
                                "bugs": []},
                })
                self.assertEqual(recommendation, "NEEDS REVIEW")

    def test_compiler_or_bootc_dependency_fix_requires_release_without_source_commits(self):
        for component in ("Go compiler", "bootc image dependency"):
            for ocp_status, expected in (
                    ("available", "ASK ART TO CREATE ARTIFACTS"),
                    ("not_available", "BLOCKED")):
                with self.subTest(component=component, ocp_status=ocp_status):
                    result = _evaluation(_report(("CVE-2026-1", {
                        "id": "OCPBUGS-1", "status": "Closed", "resolution": "Done",
                        "summary": f"CVE-2026-1: fixed in {component}",
                    })), commits=0, ocp_status=ocp_status)
                    self.assertEqual(result["recommendation"], expected)
                    self.assertEqual(result["commits"], 0)
                    self.assertEqual(result["cve_impact"]["impact"], "must_release")

    def test_skip_requires_completed_cve_assessment(self):
        result = _evaluation({"RHBA-2026:12345": {"type": "image", "cves": {}}})
        self.assertEqual(result["commits"], 0)
        self.assertEqual(result["cve_impact"]["impact"], "none")
        self.assertEqual(result["recommendation"], "SKIP")


class TestCveReport(unittest.TestCase):
    def test_counts_and_identifiers_for_one_and_multiple_fixes(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                tickets = [(f"CVE-2026-{index}", {
                    "id": f"OCPBUGS-{index}", "status": "Closed", "resolution": "Done",
                }) for index in range(1, count + 1)]
                result = _evaluation(_report(*tickets))
                label = f"{count} CVE fix{'es' if count != 1 else ''}"
                self.assertEqual(result["reason"], label)
                self.assertEqual(_build_reason(result), label)
                self.assertIn(label, format_text_short([result]))
                output = format_text_full({"evaluations": [result]})
                self.assertIn("## Recommendations\n", output)
                self.assertIn("## CVEs Requiring Release\n", output)
                summary = output.split("## Recommendations\n", 1)[1].split(
                    "## CVEs Requiring Release", 1)[0]
                self.assertIn(f"| {label} |", summary)
                detail = output.split("## CVEs Requiring Release\n", 1)[1]
                for cve, ticket in tickets:
                    self.assertIn(f"| 4.22.1 | {cve} | {ticket['id']} |", detail)

    def test_mixed_report_does_not_call_unresolved_cve_a_release_fix(self):
        result = _evaluation(_report(
            ("CVE-2026-1", {"id": "OCPBUGS-1", "status": "Closed", "resolution": "Done"}),
            ("CVE-2026-2", {"id": "OCPBUGS-2", "status": "Verified", "resolution": ""}),
        ))
        output = format_text_full({"evaluations": [result]})
        self.assertIn("| 1 CVE fix, 1 need review |", output)
        self.assertIn("## CVEs Requiring Release\n", output)
        self.assertIn("## CVEs Requiring Review\n", output)
        release = output.split("## CVEs Requiring Release\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("| 4.22.1 | CVE-2026-1 | OCPBUGS-1 |", release)
        self.assertNotIn("CVE-2026-2", release)
        review = output.split("## CVEs Requiring Review\n", 1)[1]
        self.assertIn("| 4.22.1 | CVE-2026-2 | OCPBUGS-2 |", review)
        self.assertIn("resolution is empty", review)
        advisory_row = next(line for line in output.splitlines()
                            if "RHSA-2026:12345 | image | CVE-2026-2" in line)
        self.assertIn("needs review", advisory_row)
        self.assertIn("resolution is empty", advisory_row)
        self.assertNotIn("must release", advisory_row)
        self.assertIn("resolution is empty", format_text_short([result]))

    def test_multiple_review_cves_keep_their_count_and_identifiers(self):
        tickets = [(f"CVE-2026-{index}", {
            "id": f"OCPBUGS-{index}", "status": "Verified", "resolution": "",
        }) for index in (1, 2)]
        result = _evaluation(_report(*tickets))
        self.assertEqual(result["recommendation"], "NEEDS REVIEW")
        self.assertIn("2 CVEs need review", _build_reason(result))
        output = format_text_full({"evaluations": [result]})
        self.assertIn("| 2 need review |", output)
        self.assertIn("## CVEs Requiring Review\n", output)
        detail = output.split("## CVEs Requiring Review\n", 1)[1]
        for cve, ticket in tickets:
            self.assertIn(f"| 4.22.1 | {cve} | {ticket['id']} |", detail)

    def test_review_without_done_fix_is_visible_in_both_reports(self):
        result = _evaluation(_report(("CVE-2026-1", {
            "id": "OCPBUGS-1", "status": "Verified", "resolution": "",
        })))
        for output in (format_text_short([result]),
                       format_text_full({"evaluations": [result]})):
            with self.subTest(output=output):
                self.assertIn("NEEDS REVIEW", output)
                self.assertIn("resolution is empty", output)
                self.assertIn("CVE-2026-1", output)
                self.assertIn("OCPBUGS-1", output)


class TestCveJiraIntegration(unittest.TestCase):
    def _run(self, route, labels=(), extra_done=False, ocp_status="available",
             advisory=True):
        """Use raw Jira responses to exercise discovery, enrichment, and policy."""
        issue = {
            "key": "OCPBUGS-1",
            "fields": {
                "summary": "CVE-2026-1 microshift: vulnerability",
                "status": {"name": "Verified"}, "resolution": None,
                "issuetype": {"name": "Vulnerability"},
                "labels": ["SecurityTracking", "pscomponent:microshift",
                           "CVE-2026-1", *labels],
            },
        }
        issues = [issue]
        cves = {"CVE-2026-1": {"pending": True}}
        if extra_done:
            issues.append({
                "key": "OCPBUGS-2",
                "fields": {
                    "summary": "CVE-2026-2 microshift: fixed vulnerability",
                    "status": {"name": "Closed"}, "resolution": {"name": "Done"},
                    "issuetype": {"name": "Vulnerability"},
                    "labels": ["SecurityTracking", "pscomponent:microshift", "CVE-2026-2"],
                },
            })
            cves["CVE-2026-2"] = {"pending": True}
        report = {"RHSA-2026:12345": {"type": "image", "cves": cves}}
        if not advisory:
            report = {"skipped": True, "error": "Advisory unavailable"}

        def search(jql, **kwargs):
            if jql.startswith("key in"):
                return [item for item in issues if item["key"] in jql]
            if jql.startswith("summary ~"):
                cve = jql.split('"')[1]
                return [item for item in issues if cve in item["fields"]["labels"]]
            return issues

        keys = {"OCPBUGS-1"} if route == "commit" else set()
        with patch.dict(os.environ, {"JIRA_API_TOKEN": "test-token", "JIRA_USERNAME": "test-user"}), \
                patch("lib.jira_client._jira_search", side_effect=search), \
                patch("lib.ocpbugs.extract_bugs_from_commits", return_value=keys):
            if advisory:
                enrich_advisory_cves(report, minor="4.22")
            bugs = query_resolved_bugs("4.22.1", branch="release-4.22")
        return _evaluation(report, ocpbugs=bugs, ocp_status=ocp_status,
                           commits=1 if keys else 0)

    def test_verified_without_resolution_needs_review_across_sources(self):
        for route in ("commit", "component"):
            for advisory in (False, True):
                with self.subTest(route=route, advisory=advisory):
                    result = self._run(route, advisory=advisory)
                    self.assertEqual(result["recommendation"], "NEEDS REVIEW")
                    self.assertEqual(result["ocpbugs"]["release_required"], 0)
                    self.assertEqual(result["ocpbugs"]["needs_review"], 1)
                    self.assertIn("OCPBUGS-1", result["reason"])
                    self.assertEqual(result["reason"].count("resolution is empty"), 1)
                    self.assertNotIn("not yet verified", result["reason"])

    def test_labels_decide_consistently_across_sources(self):
        for route in ("commit", "component"):
            for labels, expected in (
                    (["release-required"], "ASK ART TO CREATE ARTIFACTS"),
                    (["release-not-required"], "SKIP"),
                    (["release-required", "release-not-required"],
                     "ASK ART TO CREATE ARTIFACTS")):
                with self.subTest(route=route, labels=labels):
                    result = self._run(route, labels=labels)
                    self.assertEqual(result["recommendation"], expected)
                    self.assertNotEqual(result["cve_impact"]["impact"], "needs_review")

    def test_mixed_fixes_release_without_losing_review_warning(self):
        for route in ("commit", "component"):
            for advisory in (False, True):
                for ocp_status, expected in (
                        ("available", "ASK ART TO CREATE ARTIFACTS"),
                        ("not_available", "BLOCKED")):
                    with self.subTest(route=route, advisory=advisory, ocp_status=ocp_status):
                        result = self._run(route, extra_done=True,
                                           ocp_status=ocp_status, advisory=advisory)
                        self.assertEqual(result["recommendation"], expected)
                        self.assertEqual(result["ocpbugs"]["release_required"], 1)
                        self.assertEqual(result["reason"].count("resolution is empty"), 1)

    @patch("lib.jira_client._jira_search")
    @patch.dict(os.environ, {"JIRA_API_TOKEN": "test-token", "JIRA_USERNAME": "test-user"})
    def test_commit_cve_detection_does_not_change_regular_bug_status_rules(self, search):
        for issue_type, labels, expected in (
                ("Bug", [], "release_required"),
                ("Bug", ["CVE-2026-1"], "needs_review"),
                ("Vulnerability", [], "needs_review")):
            with self.subTest(issue_type=issue_type, labels=labels):
                search.return_value = [{
                    "key": "OCPBUGS-1", "fields": {
                        "summary": "Example issue", "status": {"name": "Verified"},
                        "resolution": None, "issuetype": {"name": issue_type}, "labels": labels,
                    },
                }]
                result = jira_client.enrich_ocpbugs(["OCPBUGS-1"])
                self.assertEqual(result["OCPBUGS-1"]["release_action"], expected)


if __name__ == "__main__":
    unittest.main()
