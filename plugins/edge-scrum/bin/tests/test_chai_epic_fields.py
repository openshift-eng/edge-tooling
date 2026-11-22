"""Tests for the decision 0024 epic flags (chai_report.epic_field_gaps).

The query_jira fixtures follow real chai-bot output for the two epic queries
(layout, wording, omitted lines for empty fields, "| Parent: KEY" on the
Labels line, comma-separated Target Versions) with synthetic keys, names,
summaries, labels, versions and dates. The repository is public: never paste
real chai-bot output, names or Jira keys here. No real sample showed an epic
with a QA Contact; the epic line is assumed to match the parent's
("customfield_10470: <name>").
"""

import os
import re
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import chai_jira_adapter as ad  # noqa: E402
import chai_report as cr  # noqa: E402
from _jira_transforms import normalize_name  # noqa: E402
from tests.test_chai_jira_adapter import ROSTER_MD  # noqa: E402
from tests.test_chai_report import CHECKS, ISSUES, SPRINT  # noqa: E402

WEEKLY_SKILL = os.path.join(os.path.dirname(__file__), "..", "..", "chai", "weekly-health-report", "SKILL.md")

EPICS_JQL = ('project = OCPEDGE AND issuetype = Epic AND status in ("To Do", "In Progress", '
             '"Dev Complete", "Testing", "Release Pending") AND parent is not EMPTY')
PARENTS_JQL = "key in (FEAT-1, FEAT-2, FEAT-3, FEAT-4, FEAT-9)"
ROSTER = {"contacts": [{"display_name": "Alex Example", "email": "aexample@redhat.com"},
                       {"display_name": "Quinn Quality", "email": "qquality@redhat.com"}]}
MENTIONS = {"Alex Example": "<@U000TEST1>"}

# Epics, one per case (ORDER BY key DESC, as fetched). New and Closed would not match the
# SKILL's active-status JQL; they are here to show the checks ignore them anyway.
EPICS_MIX = """Found 12 issues (showing 12):
  [TEST-112] Synthetic epic: to do without a version
    Type: Epic | Status: To Do | Priority: Minor | Assignee: Morgan Manager | Reporter: Pat Product
    Created: 2030-01-12 | Updated: 2030-02-12
    Labels: synthetic-label | Parent: FEAT-1
    URL: https://example.invalid/browse/TEST-112
    customfield_10470: Quinn Quality
  [TEST-111] Synthetic epic: new, still being refined
    Type: Epic | Status: New | Priority: Minor | Assignee: Morgan Manager | Reporter: Pat Product
    Created: 2030-01-11 | Updated: 2030-02-11
    Labels: synthetic-label | Parent: FEAT-3
    URL: https://example.invalid/browse/TEST-111
  [TEST-110] Synthetic epic: dev complete in an earlier release
    Type: Epic | Status: Dev Complete | Priority: Minor | Assignee: Alex Example | Reporter: Pat Product
    Created: 2030-01-10 | Updated: 2030-02-10
    Labels: synthetic-label | Parent: FEAT-1
    URL: https://example.invalid/browse/TEST-110
    customfield_10855: openshift-9.0
    customfield_10470: Quinn Quality
  [TEST-109] Synthetic epic: clean on every check
    Type: Epic | Status: In Progress | Priority: Minor | Assignee: Alex Example | Reporter: Pat Product
    Created: 2030-01-09 | Updated: 2030-02-09
    Labels: synthetic-label | Parent: FEAT-1
    URL: https://example.invalid/browse/TEST-109
    customfield_10855: openshift-9.1
    customfield_10470: Quinn Quality
  [TEST-108] Synthetic epic: parent restricted
    Type: Epic | Status: To Do | Priority: Minor | Assignee: Unassigned | Reporter: Pat Product
    Created: 2030-01-08 | Updated: 2030-02-08
    Labels: synthetic-label | Parent: FEAT-9
    URL: https://example.invalid/browse/TEST-108
    customfield_10855: openshift-9.1
  [TEST-107] Synthetic epic: testing under a backlog parent
    Type: Epic | Status: Testing | Priority: Minor | Assignee: Alex Example | Reporter: Pat Product
    Created: 2030-01-07 | Updated: 2030-02-07
    Components: Component A | Labels: synthetic-label | Parent: FEAT-4
    URL: https://example.invalid/browse/TEST-107
    customfield_10470: Quinn Quality
  [TEST-106] Synthetic epic: release pending, parent Closed, no QA anywhere
    Type: Epic | Status: Release Pending | Priority: Minor | Assignee: Morgan Manager | Reporter: Pat Product
    Created: 2030-01-06 | Updated: 2030-02-06
    Labels: synthetic-label | Parent: FEAT-3
    URL: https://example.invalid/browse/TEST-106
    customfield_10855: openshift-9.0
  [TEST-105] Synthetic epic: closed
    Type: Epic | Status: Closed | Priority: Minor | Assignee: Alex Example | Reporter: Pat Product | Resolution: Done
    Created: 2030-01-05 | Updated: 2030-02-05 | Resolved: 2030-02-05
    Labels: synthetic-label | Parent: FEAT-3
    URL: https://example.invalid/browse/TEST-105
    customfield_10855: openshift-9.0
  [TEST-104] Synthetic epic: dev complete
    Type: Epic | Status: Dev Complete | Priority: Minor | Assignee: Alex Example | Reporter: Pat Product
    Created: 2030-01-04 | Updated: 2030-02-04
    Labels: synthetic-label | Parent: FEAT-3
    URL: https://example.invalid/browse/TEST-104
  [TEST-103] Synthetic epic: started without a version
    Type: Epic | Status: In Progress | Priority: Minor | Assignee: Unassigned | Reporter: Pat Product
    Created: 2030-01-03 | Updated: 2030-02-03
    Components: Component A, Component B | Labels: synthetic-label | Parent: FEAT-2
    URL: https://example.invalid/browse/TEST-103
    customfield_10470: unassigned_jira
  [TEST-102] Synthetic epic: matches one of two parent versions
    Type: Epic | Status: To Do | Priority: Minor | Assignee: Unassigned | Reporter: Pat Product
    Created: 2030-01-02 | Updated: 2030-02-02
    Labels: synthetic-label | Parent: FEAT-2
    URL: https://example.invalid/browse/TEST-102
    customfield_10855: openshift-9.2
  [TEST-101] Synthetic epic: version drifted from its feature
    Type: Epic | Status: In Progress | Priority: Minor | Assignee: Alex Example | Reporter: Pat Product
    Created: 2030-01-01 | Updated: 2030-02-01
    Labels: synthetic-label | Parent: FEAT-1
    URL: https://example.invalid/browse/TEST-101
    customfield_10855: openshift-9.0
    customfield_10470: Quinn Quality
"""

# Their parents; FEAT-9 was requested but not returned (restricted).
PARENTS = """Found 4 issues (showing 4):
  [FEAT-4] Synthetic feature in the backlog
    Type: Feature | Status: Backlog | Priority: Normal | Assignee: Pat Product | Reporter: Pat Product
    Created: 2029-12-01 | Updated: 2030-01-20
    Components: Component A | Labels: synthetic-label
    URL: https://example.invalid/browse/FEAT-4
    customfield_10855: openshift-9.1
    customfield_10470: Quinn Quality
  [FEAT-3] Synthetic initiative, closed
    Type: Initiative | Status: Closed | Priority: Normal | Assignee: Pat Product | Reporter: Pat Product | Resolution: Obsolete
    Created: 2029-12-02 | Updated: 2030-01-21 | Resolved: 2030-01-21
    Labels: synthetic-label
    URL: https://example.invalid/browse/FEAT-3
  [FEAT-2] Synthetic initiative over two releases
    Type: Initiative | Status: In Progress | Priority: Minor | Assignee: Pat Product | Reporter: Pat Product
    Created: 2029-12-03 | Updated: 2030-01-22
    Components: Component A, Component B | Labels: synthetic-label
    URL: https://example.invalid/browse/FEAT-2
    customfield_10855: openshift-9.1, openshift-9.2
    customfield_10470: Quinn Quality
  [FEAT-1] Synthetic feature in progress
    Type: Feature | Status: In Progress | Priority: Normal | Assignee: Pat Product | Reporter: Pat Product
    Created: 2029-12-04 | Updated: 2030-01-23
    Labels: synthetic-label
    URL: https://example.invalid/browse/FEAT-1
    customfield_10855: openshift-9.1
    customfield_10470: Quinn Quality
"""

EPICS_CLEAN = """Found 2 issues (showing 2):
  [TEST-202] Synthetic epic: not started, matches its parent
    Type: Epic | Status: New | Priority: Minor | Assignee: Unassigned | Reporter: Pat Product
    Created: 2030-01-02 | Updated: 2030-02-01
    Labels: synthetic-label | Parent: FEAT-2
    URL: https://example.invalid/browse/TEST-202
    customfield_10855: openshift-9.1
    customfield_10470: Quinn Quality
  [TEST-201] Synthetic epic: clean on every check
    Type: Epic | Status: In Progress | Priority: Major | Assignee: Alex Example | Reporter: Pat Product
    Created: 2030-01-02 | Updated: 2030-02-01
    Labels: synthetic-label | Parent: FEAT-1
    URL: https://example.invalid/browse/TEST-201
    customfield_10855: openshift-9.1
    customfield_10470: Quinn Quality
"""


def _adapt(text, jql, name):
    return ad.adapt_fetch({"name": name, "pages": [{"text": text, "jql": jql}]}, roster=ROSTER)


def _mix(parent_text=PARENTS, keys=("FEAT-1", "FEAT-2", "FEAT-3", "FEAT-4", "FEAT-9")):
    epics = _adapt(EPICS_MIX, EPICS_JQL, "epics")
    parents = [{"keys": list(keys), "result": _adapt(parent_text, PARENTS_JQL, "parents")}]
    return epics, parents, cr.epic_field_gaps(epics, parents)


def _keys(gaps, check):
    return [i["key"] for i in gaps["checks"][check]]


def _epic_block(n, status="To Do", parent="FEAT-1", tv="openshift-9.1", qa=None, assignee="Unassigned"):
    lines = [f"  [TEST-{n}] Synthetic epic {n}",
             f"    Type: Epic | Status: {status} | Priority: Minor | Assignee: {assignee} | Reporter: Pat Product",
             "    Created: 2030-01-02 | Updated: 2030-02-01",
             f"    Labels: synthetic-label | Parent: {parent}",
             f"    URL: https://example.invalid/browse/TEST-{n}"]
    if tv:
        lines.append(f"    customfield_10855: {tv}")
    if qa:
        lines.append(f"    customfield_10470: {qa}")
    return "\n".join(lines)


class ParseSampleFormatTest(unittest.TestCase):
    def test_epic_and_parent_fields(self):
        epics, parents, _ = _mix()
        self.assertTrue(epics["fetch"]["complete"])
        self.assertEqual(epics["warnings"], [])  # URL lines are known, not "unrecognized"
        by_key = {i["key"]: i for i in epics["issues"]}
        self.assertEqual(by_key["TEST-101"]["parent"]["key"], "FEAT-1")
        self.assertEqual(by_key["TEST-103"]["parent"]["key"], "FEAT-2")  # Components | Labels | Parent line
        self.assertIsNone(by_key["TEST-108"]["assignee"])  # "Assignee: Unassigned"
        self.assertNotIn("customfield_10855", by_key["TEST-103"])  # no line = empty
        feat2 = next(i for i in parents[0]["result"]["issues"] if i["key"] == "FEAT-2")
        self.assertEqual(feat2["customfield_10855"], [{"name": "openshift-9.1"}, {"name": "openshift-9.2"}])
        self.assertEqual(feat2["customfield_10470"]["display_name"], "Quinn Quality")


class EpicChecksTest(unittest.TestCase):
    def setUp(self):
        _, _, self.gaps = _mix()

    def test_target_version_differs(self):
        self.assertEqual(_keys(self.gaps, "target_version_differs"), ["TEST-101"])
        self.assertEqual(self.gaps["checks"]["target_version_differs"][0]["detail"],
                         "Target Version ≠ feature")

    def test_parent_with_two_versions_matches_either(self):
        self.assertNotIn("TEST-102", _keys(self.gaps, "target_version_differs"))

    def test_active_epics_without_target_version(self):
        self.assertEqual(_keys(self.gaps, "no_target_version"), ["TEST-103", "TEST-104", "TEST-107", "TEST-112"])
        self.assertEqual({i["detail"] for i in self.gaps["checks"]["no_target_version"]}, {"no Target Version"})

    def test_to_do_without_target_version_is_flagged_new_is_not(self):
        self.assertIn("TEST-112", _keys(self.gaps, "no_target_version"))
        flagged = {k for items in self.gaps["checks"].values() for k in (i["key"] for i in items)}
        self.assertNotIn("TEST-111", flagged)  # New: no Target Version, no QA Contact, Closed parent

    def test_testing_and_release_pending(self):
        self.assertIn("TEST-107", _keys(self.gaps, "no_target_version"))  # Testing
        self.assertIn("TEST-107", _keys(self.gaps, "parent_backlog_or_closed"))
        for check in ("no_qa_contact", "parent_backlog_or_closed"):  # Release Pending
            self.assertIn("TEST-106", _keys(self.gaps, check))
        # TEST-106 has openshift-9.0 under a parent with none: no drift flag from Dev Complete on.
        self.assertNotIn("TEST-106", _keys(self.gaps, "target_version_differs"))

    def test_dev_complete_not_in_check_1_but_in_checks_2_to_4(self):
        self.assertNotIn("TEST-110", _keys(self.gaps, "target_version_differs"))  # 9.0 vs parent 9.1
        for check in ("no_target_version", "no_qa_contact", "parent_backlog_or_closed"):
            self.assertIn("TEST-104", _keys(self.gaps, check))

    def test_no_qa_contact(self):
        self.assertEqual(_keys(self.gaps, "no_qa_contact"),
                         ["TEST-102", "TEST-103", "TEST-104", "TEST-106", "TEST-108"])
        detail = {i["key"]: i["detail"] for i in self.gaps["checks"]["no_qa_contact"]}
        # Decision 0024: note when the feature has none (or could not be read); never the parent's name.
        self.assertEqual(detail, {"TEST-102": "no QA Contact", "TEST-103": "no QA Contact",
                                  "TEST-104": "no QA Contact, feature has none",
                                  "TEST-106": "no QA Contact, feature has none",
                                  "TEST-108": "no QA Contact, feature could not be read"})

    def test_parent_qa_contact_name_is_never_printed(self):
        report = cr.build_report(None, [], epic_gaps=self.gaps)
        self.assertNotIn("Quinn Quality", "\n".join(report["top_post"] + report["replies"]))

    def test_unassigned_jira_is_no_qa_contact(self):
        self.assertIn("TEST-103", _keys(self.gaps, "no_qa_contact"))

    def test_missing_field_line_is_empty(self):
        self.assertIn("TEST-102", _keys(self.gaps, "no_qa_contact"))  # no customfield_10470 line
        self.assertIn("TEST-103", _keys(self.gaps, "no_target_version"))  # no customfield_10855 line

    def test_parent_in_backlog_or_closed(self):
        self.assertEqual(_keys(self.gaps, "parent_backlog_or_closed"), ["TEST-104", "TEST-106", "TEST-107"])
        detail = {i["key"]: i["detail"] for i in self.gaps["checks"]["parent_backlog_or_closed"]}
        self.assertEqual(detail, {"TEST-104": "feature closed", "TEST-106": "feature closed",
                                  "TEST-107": "feature in backlog"})

    def test_closed_and_new_epics_are_ignored(self):
        flagged = {k for items in self.gaps["checks"].values() for k in (i["key"] for i in items)}
        self.assertFalse({"TEST-105", "TEST-111"} & flagged)
        self.assertEqual(self.gaps["epics_checked"], 10)  # 12 minus Closed and New

    def test_clean_epic_and_counts(self):
        flagged = {k for items in self.gaps["checks"].values() for k in (i["key"] for i in items)}
        self.assertNotIn("TEST-109", flagged)
        self.assertEqual(self.gaps["flagged"], 8)
        self.assertEqual((self.gaps["risks"], self.gaps["actions"]), ([], []))  # informational

    def test_owners_are_the_assignees(self):
        owners = {i["key"]: i["owner"] for items in self.gaps["checks"].values() for i in items}
        self.assertEqual(owners["TEST-101"], "Alex Example")
        self.assertEqual(owners["TEST-106"], "Morgan Manager")
        self.assertIsNone(owners["TEST-103"])


class ParentNotReadTest(unittest.TestCase):
    def test_restricted_parent_is_left_out(self):
        _, _, gaps = _mix()
        left = [x for x in gaps["left_out"] if "FEAT-9" in x["what"]]
        self.assertEqual(left, [{"what": "Target Version and parent status checks for 1 epic(s) under FEAT-9",
                                 "why": "parent could not be read: not returned by Jira "
                                        "(restricted, deleted or no permission)"}])

    def test_failed_batch_leaves_its_epics_out(self):
        epics = _adapt(EPICS_MIX, EPICS_JQL, "epics")
        gaps = cr.epic_field_gaps(epics, [{"keys": ["FEAT-1", "FEAT-2", "FEAT-3", "FEAT-4", "FEAT-9"],
                                           "error": "query_jira call failed"}])
        self.assertEqual(_keys(gaps, "target_version_differs"), [])
        self.assertEqual(_keys(gaps, "parent_backlog_or_closed"), [])
        # Checks that do not need the parent still run.
        self.assertEqual(_keys(gaps, "no_target_version"), ["TEST-103", "TEST-104", "TEST-107", "TEST-112"])
        self.assertIn("TEST-106", _keys(gaps, "no_qa_contact"))
        self.assertEqual(len(gaps["left_out"]), 1)
        self.assertIn("query failed (query_jira call failed)", gaps["left_out"][0]["why"])

    def test_cut_off_parent_page_is_not_used(self):
        cut = PARENTS.replace("Found 4 issues (showing 4):", "Found 5 issues (showing 5):")
        _, parents, gaps = _mix(parent_text=cut)
        self.assertFalse(parents[0]["result"]["fetch"]["complete"])
        self.assertEqual(_keys(gaps, "target_version_differs"), [])
        self.assertIn("fetch incomplete (fetch: page 1:", gaps["left_out"][0]["why"])

    def test_one_batch_failing_keeps_the_others(self):
        epics = _adapt(EPICS_MIX, EPICS_JQL, "epics")
        good = _adapt(PARENTS, PARENTS_JQL, "parents")
        gaps = cr.epic_field_gaps(epics, [{"keys": ["FEAT-1", "FEAT-2", "FEAT-3", "FEAT-4"], "result": good},
                                          {"keys": ["FEAT-9"], "error": "timeout"}])
        self.assertEqual(_keys(gaps, "target_version_differs"), ["TEST-101"])
        self.assertIn("query failed (timeout)", gaps["left_out"][0]["why"])

    def test_parent_never_requested(self):
        epics = _adapt(EPICS_MIX, EPICS_JQL, "epics")
        gaps = cr.epic_field_gaps(epics, [])
        self.assertTrue(any("not fetched" in x["why"] for x in gaps["left_out"]))


class EmptyAndCapTest(unittest.TestCase):
    def test_empty_result_has_no_section(self):
        jql = EPICS_JQL + " ORDER BY key DESC"
        epics = _adapt(f"No issues found for JQL: {jql}", jql, "epics")
        gaps = cr.epic_field_gaps(epics, [])
        self.assertEqual((gaps["flagged"], gaps["risks"], gaps["left_out"]), (0, [], []))
        with_gaps = cr.build_report(None, [], epic_gaps=gaps)
        self.assertEqual(with_gaps, cr.build_report(None, []))

    def test_nothing_flagged_has_no_section(self):
        epics = _adapt(EPICS_CLEAN, EPICS_JQL, "epics")
        gaps = cr.epic_field_gaps(epics, [{"keys": ["FEAT-1", "FEAT-2"],
                                           "result": _adapt(PARENTS, PARENTS_JQL, "parents")}])
        self.assertEqual(gaps["flagged"], 0)
        report = cr.build_report(None, [], epic_gaps=gaps)
        self.assertFalse(any(r.startswith("*Epic fields*") for r in report["replies"]))

    def test_more_than_ten_owners_is_capped(self):
        blocks = [_epic_block(n, assignee=f"Owner {n}") for n in range(313, 300, -1)]  # 13 owners, no QA Contact
        text = "Found 13 issues (showing 13):\n" + "\n".join(blocks) + "\n"
        epics = _adapt(text, EPICS_JQL, "epics")
        gaps = cr.epic_field_gaps(epics, [{"keys": ["FEAT-1"], "result": _adapt(PARENTS, PARENTS_JQL, "p")}])
        self.assertEqual((len(gaps["checks"]["no_qa_contact"]), len(gaps["epics"])), (13, 13))
        report = cr.build_report(None, [], epic_gaps=gaps)
        lines = next(r for r in report["replies"] if r.startswith("*Epic fields*")).splitlines()
        # Zero counts and the Key omitted; the cap is in the header; overflow keys only on the Left out line.
        self.assertEqual(lines[0], "*Epic fields* (13 epics: 13 no QA Contact; ✓ 0 complete; 10 of 13 owners shown)")
        self.assertEqual(lines[1:-1], [f"• Owner {n}: TEST-{n} (no QA Contact)" for n in range(301, 311)])
        self.assertEqual(lines[-1], "Left out: 3 more owner(s) in epic fields: TEST-311, TEST-312, TEST-313")


class FailureIsolationTest(unittest.TestCase):
    BAD_EPICS = (None, "text", 7, [], {}, {"issues": "x"}, {"issues": [], "fetch": "x"},
                 {"issues": None, "fetch": {"complete": True}})

    def test_malformed_epic_input_never_raises(self):
        for bad in self.BAD_EPICS:
            with self.subTest(bad=bad):
                gaps = cr.epic_field_gaps(bad, [])
                self.assertEqual(gaps["flagged"], 0)
                self.assertEqual(gaps["left_out"][0]["what"], "Epic Target Version and QA Contact checks")

    def test_malformed_issues_and_fields_never_raise(self):
        epics = {"fetch": {"complete": True}, "issues": [
            None, "TEST-1", {"key": None}, {"key": "TEST-2"},  # no parent
            {"key": "TEST-3", "status": "To Do", "parent": "FEAT-1", "customfield_10855": "openshift-9.0",
             "customfield_10470": 5, "assignee": ["x"], "summary": None},
            {"key": "TEST-4", "status": {"name": 3}, "parent": {"key": 5}},
            {"key": "TEST-5", "status": "In Progress", "parent": {"key": "FEAT-1"},
             "customfield_10855": [None, 3, {"name": None}], "customfield_10470": {"display_name": None}},
        ]}
        bad_parents = [None, "x", {"keys": "FEAT-1"}, {"keys": [1, None]},
                       {"keys": ["FEAT-1"], "result": {"fetch": {"complete": True},
                                                        "issues": [{"key": "FEAT-1", "status": [],
                                                                    "customfield_10855": {"name": 9}}]}}]
        for parents in (bad_parents, "garbage", {"keys": ["FEAT-1"], "error": "x"}, None):
            with self.subTest(parents=parents):
                gaps = cr.epic_field_gaps(epics, parents)
                self.assertIn("TEST-3", _keys(gaps, "no_qa_contact"))
                self.assertIn("TEST-5", _keys(gaps, "no_target_version"))
                cr.build_report(None, [], epic_gaps=gaps)  # renders

    def test_malformed_epic_section_does_not_take_down_the_report(self):
        sprint = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=24, availability=0.9)
        good = cr.build_report(sprint, [])
        for bad in ("garbage", 3, [], {}, {"kind": cr.EPIC_FIELDS}, {"kind": cr.EPIC_FIELDS, "flagged": 1},
                    {"kind": cr.EPIC_FIELDS, "flagged": 1, "checks": {}, "shown": {"no_qa_contact": [{}]},
                     "more": {}, "risks": ["x"], "left_out": []}):
            with self.subTest(bad=bad):
                report = cr.build_report(sprint, [], epic_gaps=bad)
                lines = report["replies"][0].splitlines()
                self.assertEqual(lines[:-1], good["replies"][0].splitlines())  # sprint reply intact
                self.assertEqual(lines[-1], "Left out: Epic Target Version and QA Contact checks: result not readable")


def weekly_report(epic_text, parent_text=PARENTS, parent_keys=("FEAT-1", "FEAT-2", "FEAT-3", "FEAT-4", "FEAT-9"),
                  sprint_issues=None, checks=None):
    """A full weekly report from fixtures: sprint health, one release section, epic flags."""
    if sprint_issues is None:
        sprint_issues = ISSUES
    sprint = cr.sprint_health(sprint_issues, SPRINT, "2030-01-29", "capacity", velocity=24, availability=0.9)
    section = {"release": "9.1", "phase": "development", "pencils_down": False}
    release = cr.release_health(checks or CHECKS, section,
                                cr.release_capacity(45, [{"number": 14, "availability": 1},
                                                         {"number": 15, "availability": 1}]))
    epics = _adapt(epic_text, EPICS_JQL, "epics")
    gaps = cr.epic_field_gaps(epics, [{"keys": list(parent_keys),
                                       "result": _adapt(parent_text, PARENTS_JQL, "parents")}])
    return cr.build_report(sprint, [release], [], MENTIONS, epic_gaps=gaps)


CLEAN_ISSUES = {"total_sp": 20, "total_issues": 1, "issues": [
    {"key": "TEST-1", "type": "Story", "sp": 20, "assignee_display": "Alex Example",
     "epic_key": "TEST-100", "has_ac": True}]}
CLEAN_CHECKS = {"meta": {"total_remaining_sp": 60, "total_capacity_sp": 90, "capacity_basis": "velocity",
                         "team_velocity": 45.0, "remaining_sprints": 2, "gap_sp": 30},
                "cut_line": [{"feature_key": "FEAT-1", "sme": "Morgan Manager", "fits": "fits"}],
                "composite": [], "timeline": [], "process_gaps": {"roster_drift": []}}


class InformationalTest(unittest.TestCase):
    """The epic section never changes the colour, the risk line or the action count; its owners
    are counted on the top post's third line like everyone tagged."""

    def test_flags_and_left_out_keep_the_top_post(self):
        _, _, gaps = _mix()  # 8 flagged, plus a parent that could not be read
        self.assertTrue(gaps["flagged"] and gaps["left_out"])
        for sprint_issues, checks in ((CLEAN_ISSUES, CLEAN_CHECKS), (ISSUES, CHECKS)):
            with self.subTest(clean=sprint_issues is CLEAN_ISSUES):
                sprint = cr.sprint_health(sprint_issues, SPRINT, "2030-01-29", "capacity",
                                          velocity=24, availability=0.9)
                release = cr.release_health(checks, {"release": "9.1", "phase": "development"},
                                            cr.release_capacity(45, [{"number": 14, "availability": 1},
                                                                     {"number": 15, "availability": 1}]))
                without = cr.build_report(sprint, [release], [], MENTIONS)
                with_gaps = cr.build_report(sprint, [release], [], MENTIONS, epic_gaps=gaps)
                self.assertEqual(with_gaps["top_post"][:2], without["top_post"][:2])
                self.assertEqual(with_gaps["action_count"], without["action_count"])
                self.assertIn("Morgan Manager", with_gaps["people"])  # an epic owner
                self.assertTrue(set(without["people"]) <= set(with_gaps["people"]))

    def test_only_epic_flags_is_still_green(self):
        _, _, gaps = _mix()
        report = cr.build_report(None, [], epic_gaps=gaps)
        self.assertTrue(report["top_post"][0].startswith("🟢"))
        self.assertEqual(report["top_post"][1:], ["No risks this week", "2 people have items: Alex Example, Morgan Manager"])
        self.assertTrue(any(r.startswith("*Epic fields*") for r in report["replies"]))

    def test_malformed_epic_section_does_not_change_the_colour(self):
        report = cr.build_report(None, [], epic_gaps="garbage")
        self.assertTrue(report["top_post"][0].startswith("🟢"))
        self.assertIn("result not readable", report["replies"][-1])


def skill_snippet(*needles, path=WEEKLY_SKILL):
    """The first python code block of a SKILL.md (weekly-health-report by default) containing every needle."""
    with open(path, encoding="utf-8") as fh:
        blocks = re.findall(r"```python\n(.*?)```", fh.read(), re.S)
    return textwrap.dedent(next(b for b in blocks if all(n in b for n in needles)))


def skill_owner_snippet():
    """The owner-collection code block from weekly-health-report/SKILL.md, verbatim."""
    return skill_snippet("owners", 'gaps["epics"]')


class PerOwnerLinesTest(unittest.TestCase):
    def setUp(self):
        _, _, self.gaps = _mix()

    def test_problems_merge_into_one_line_sorted_by_count_then_key(self):
        self.assertEqual([(e["key"], len(e["problems"])) for e in self.gaps["epics"]],
                         [("TEST-103", 3), ("TEST-104", 3), ("TEST-102", 2), ("TEST-106", 2),
                          ("TEST-107", 2), ("TEST-108", 2), ("TEST-101", 1), ("TEST-112", 1)])
        e104 = next(e for e in self.gaps["epics"] if e["key"] == "TEST-104")
        self.assertEqual(e104["problems"], ["no Target Version", "no QA Contact, feature has none", "feature closed"])  # check order

    def test_section_is_emitted_once_and_owners_are_not_concatenated(self):
        report = weekly_report(EPICS_MIX)
        text = "\n".join(report["top_post"] + report["replies"])
        self.assertEqual(text.count("*Epic fields*"), 1)
        self.assertNotIn("\r", text)
        epic = next(r for r in report["replies"] if r.startswith("*Epic fields*"))
        lines = epic.splitlines()
        for line in lines[1:-1]:  # one owner per line, then that owner's epics only
            self.assertEqual(line.count(": "), 1, line)
            self.assertRegex(line, r"^• [^:()]+: .*TEST-\d+")
        self.assertEqual(len(lines), 1 + 3 + 1)  # header, three owners, the Left out line
        self.assertTrue(lines[-1].startswith("Left out: "))
        owner_part = "\n".join(lines[:-1])  # header and owner lines (Left out may name an unreadable parent)
        self.assertNotIn("Synthetic epic", owner_part)  # no summaries
        self.assertNotIn("openshift-", owner_part)  # no version numbers
        self.assertNotIn("FEAT-", owner_part)  # no parent keys


class DriftNeedsParentVersionTest(unittest.TestCase):
    def test_parent_without_target_version_is_not_drift(self):
        epics = {"fetch": {"complete": True}, "issues": [
            {"key": f"TEST-{n}", "status": {"name": status}, "parent": {"key": parent},
             "customfield_10855": [{"name": "openshift-9.0"}], "assignee": {"display_name": "Alex Example"},
             "customfield_10470": {"display_name": "Quinn Quality"}}
            for n, status, parent in ((1, "To Do", "FEAT-3"), (2, "In Progress", "FEAT-3"),  # parent: none
                                      (3, "In Progress", "FEAT-1"))]}  # parent: openshift-9.1
        gaps = cr.epic_field_gaps(epics, [{"keys": ["FEAT-1", "FEAT-3"],
                                           "result": _adapt(PARENTS, PARENTS_JQL, "parents")}])
        self.assertEqual(_keys(gaps, "target_version_differs"), ["TEST-3"])


class NoAssigneeTest(unittest.TestCase):
    def _gaps(self):
        clean = {"status": {"name": "In Progress"}, "parent": {"key": "FEAT-1"},
                 "customfield_10855": [{"name": "openshift-9.1"}], "customfield_10470": {"display_name": "Quinn Quality"}}
        epics = {"fetch": {"complete": True}, "issues": [
            dict(clean, key="TEST-1", summary="Unassigned only"),
            dict(clean, key="TEST-2", summary="Assigned and clean", assignee={"display_name": "Alex Example"})]}
        return cr.epic_field_gaps(epics, [{"keys": ["FEAT-1"], "result": _adapt(PARENTS, PARENTS_JQL, "parents")}])

    def test_no_assignee_is_a_problem_and_tags_the_scrum_master(self):
        gaps = self._gaps()
        self.assertEqual([(e["key"], e["problems"]) for e in gaps["epics"]], [("TEST-1", ["no assignee"])])
        report = cr.build_report(None, [], [], {"Pat Product": "<@U000TEST9>"}, team_owner="Pat Product",
                                 epic_gaps=gaps)
        epic = next(r for r in report["replies"] if r.startswith("*Epic fields*"))
        self.assertEqual(epic.splitlines(), ["*Epic fields* (1 epic: 1 no assignee; ✓ 1 complete)",
                                             "• <@U000TEST9>: TEST-1 (no assignee)"])

    def test_scrum_master_line_merges_their_own_epics(self):
        gaps = self._gaps()
        gaps["epics"].append({"key": "TEST-5", "owner": "Pat Product", "problems": ["no QA Contact"], "new": None})
        gaps["checks"]["no_qa_contact"].append({"key": "TEST-5"})
        gaps["flagged"] = 2
        epic = cr.build_report(None, [], [], {}, team_owner="Pat Product", epic_gaps=gaps)["replies"][0]
        self.assertEqual(epic.splitlines()[1], "• Pat Product: TEST-1 (no assignee), TEST-5 (no QA Contact)")

    def test_without_a_scrum_master_it_says_no_owner_set(self):
        report = cr.build_report(None, [], epic_gaps=self._gaps())
        self.assertIn("• no owner set: TEST-1 (no assignee)", report["replies"][0])


class NewMarkerTest(unittest.TestCase):
    """🆕 only when the previous report's flagged set is known (persona memory)."""

    def test_marks_epics_not_flagged_last_time(self):
        epics, parents, _ = _mix()
        gaps = cr.epic_field_gaps(epics, parents, previous_flagged=["TEST-101", "TEST-104", "TEST-999"])
        self.assertEqual({e["key"]: e["new"] for e in gaps["epics"]}["TEST-104"], False)
        self.assertEqual(gaps["flagged_keys"], ["TEST-101", "TEST-102", "TEST-103", "TEST-104",
                                                "TEST-106", "TEST-107", "TEST-108", "TEST-112"])
        epic = next(r for r in cr.build_report(None, [], [], MENTIONS, epic_gaps=gaps)["replies"]
                    if r.startswith("*Epic fields*"))
        self.assertIn("• <@U000TEST1>: no Target Version for TEST-104, 🆕 107; TEST-104 (no QA Contact, feature has none, feature closed); "
                      "🆕 TEST-107 (feature in backlog); TEST-101 (Target Version \u2260 feature)", epic)
        self.assertIn("• Morgan Manager: 🆕 TEST-106 (no QA Contact, feature has none, feature closed), 🆕 TEST-112 (no Target Version)", epic)

    def test_snapshot_is_complete_only_when_every_epic_was_checked(self):
        _, _, mix = _mix()  # FEAT-9 could not be read
        self.assertFalse(mix["complete"])
        epics = _adapt(EPICS_CLEAN, EPICS_JQL, "epics")
        clean = cr.epic_field_gaps(epics, [{"keys": ["FEAT-1", "FEAT-2"],
                                            "result": _adapt(PARENTS, PARENTS_JQL, "parents")}])
        self.assertTrue(clean["complete"])
        self.assertEqual(clean["flagged_keys"], [])  # a valid, empty set
        failed = cr.epic_field_gaps({"issues": [], "fetch": {"complete": False, "problems": ["cut off"]}}, [])
        self.assertFalse(failed["complete"])
        rejected = cr.epic_field_gaps({"fetch": {"complete": True}, "issues": [None]}, [])
        self.assertFalse(rejected["complete"])

    def test_no_marker_without_the_previous_set(self):
        for previous in (None, "TEST-101", 5, {"TEST-101": 1}):
            with self.subTest(previous=previous):
                epics, parents, _ = _mix()
                gaps = cr.epic_field_gaps(epics, parents, previous_flagged=previous)
                self.assertTrue(all(e["new"] is None for e in gaps["epics"]))
                text = "\n".join(cr.build_report(None, [], epic_gaps=gaps)["replies"])
                self.assertNotIn("🆕", text)


class StatusScopeTest(unittest.TestCase):
    """Law 04 status names: which checks apply in which epic status (decision 0024)."""

    PARENTS = [{"keys": ["FEAT-1", "FEAT-4"], "result": _adapt(PARENTS, PARENTS_JQL, "parents")}]

    def _gaps(self, status):
        drift = {"key": "TEST-1", "status": {"name": status}, "parent": {"key": "FEAT-4"},  # Backlog, 9.1
                 "customfield_10855": [{"name": "openshift-9.0"}]}  # drifted, no QA Contact
        no_tv = {"key": "TEST-2", "status": {"name": status}, "parent": {"key": "FEAT-1"},
                 "customfield_10470": {"display_name": "Quinn Quality"}}
        return cr.epic_field_gaps({"fetch": {"complete": True}, "issues": [drift, no_tv]}, self.PARENTS)

    def test_each_status(self):
        for status in ("To Do", "In Progress", "Dev Complete", "Testing", "Release Pending"):
            with self.subTest(status=status):
                gaps = self._gaps(status)
                self.assertEqual(_keys(gaps, "target_version_differs"),
                                 ["TEST-1"] if status in ("To Do", "In Progress") else [])
                self.assertEqual(_keys(gaps, "no_target_version"), ["TEST-2"])
                self.assertEqual(_keys(gaps, "no_qa_contact"), ["TEST-1"])
                self.assertEqual(_keys(gaps, "parent_backlog_or_closed"), ["TEST-1"])
        for status in ("New", "Planning", "Closed"):
            with self.subTest(status=status):
                gaps = self._gaps(status)
                self.assertEqual((gaps["flagged"], gaps["epics_checked"]), (0, 0))

    def test_status_names_are_law_04s(self):
        self.assertEqual(cr.ACTIVE_EPIC_STATUSES,
                         ("To Do", "In Progress", "Dev Complete", "Testing", "Release Pending"))
        self.assertEqual(cr.DRIFT_EPIC_STATUSES, ("To Do", "In Progress"))


class RejectedRecordsAndKeysTest(unittest.TestCase):
    def test_rejected_records_are_reported_and_siblings_processed(self):
        valid = {"key": "TEST-1", "summary": "Synthetic epic", "status": {"name": "To Do"},
                 "parent": {"key": "FEAT-1"}, "customfield_10855": [{"name": "openshift-9.1"}]}
        epics = {"fetch": {"complete": True}, "issues": [None, {"key": None}, valid]}
        parents = [{"keys": ["FEAT-1"], "result": _adapt(PARENTS, PARENTS_JQL, "parents")}]
        gaps = cr.epic_field_gaps(epics, parents)
        self.assertEqual(gaps["epics_checked"], 1)
        self.assertEqual(_keys(gaps, "no_qa_contact"), ["TEST-1"])  # the valid sibling is checked
        self.assertIn({"what": "2 epic record(s) could not be read",
                       "why": "not an issue record or no issue key; not checked"}, gaps["left_out"])
        left = cr.build_report(None, [], epic_gaps=gaps)["replies"][-1].splitlines()[-1]
        self.assertIn("2 epic record(s) could not be read: not an issue record or no issue key; not checked", left)
        self.assertTrue(left.startswith("Left out: "))

    def test_only_rejected_records_is_not_a_clean_result(self):
        gaps = cr.epic_field_gaps({"fetch": {"complete": True}, "issues": [None, {"key": None}]}, [])
        self.assertEqual(gaps["flagged"], 0)
        self.assertEqual(gaps["left_out"][0]["what"], "2 epic record(s) could not be read")

    def test_key_number_is_safe_for_non_decimal_digits(self):
        self.assertEqual(cr._key_number("TEST-2"), ("TEST", 2))
        self.assertEqual(cr._key_number("TEST-10"), ("TEST", 10))
        self.assertEqual(cr._key_number("TEST-²"), ("TEST-²", 0))  # isdigit() but not int()
        self.assertEqual(sorted(["TEST-10", "TEST-²", "TEST-2"], key=cr._key_number),
                         ["TEST-2", "TEST-10", "TEST-²"])
        epics = {"fetch": {"complete": True}, "issues": [
            {"key": k, "status": {"name": "To Do"}, "parent": {"key": "FEAT-1"}} for k in ("TEST-10", "TEST-²", "TEST-2")]}
        gaps = cr.epic_field_gaps(epics, [])
        self.assertEqual(_keys(gaps, "no_qa_contact"), ["TEST-2", "TEST-10", "TEST-²"])
        cr.build_report(None, [], epic_gaps=gaps)  # renders


class MentionsFromSkillTest(unittest.TestCase):
    """Builds the mentions map exactly as the SKILL tells chai-bot to."""

    SLACK = {"aexample@redhat.com": "U000TEST1", "mmanager@redhat.com": "U000TEST2"}

    def _owners(self, sprint, computed, gaps, scrum_master=None):
        scope = {"sprint": sprint, "computed": computed, "gaps": gaps, "scrum_master": scrum_master}
        exec(skill_owner_snippet(), {}, scope)  # noqa: S102 - our own SKILL text
        return scope["owners"]

    def _mentions(self, owners):
        # SKILL step 5, items 1-4: roster row by display name → email → Slack user.
        contacts = {normalize_name(c["display_name"]): c for c in ad.parse_team_roster(ROSTER_MD)["contacts"]}
        out = {}
        for owner in owners:
            row = contacts.get(normalize_name(owner))
            user = self.SLACK.get(row["email"]) if row and row["email"] else None
            if user:
                out[owner] = f"<@{user}>"
        return out

    def _parts(self):
        sprint = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=24, availability=0.9)
        release = cr.release_health(CHECKS, {"release": "9.1", "phase": "development"},
                                    cr.release_capacity(45, [{"number": 14, "availability": 1}]))
        return sprint, [release]

    def test_several_checks_and_a_shared_owner(self):
        sprint, computed = self._parts()
        _, _, gaps = _mix()
        owners = self._owners(sprint, computed, gaps, scrum_master="Pat Product")
        # Alex Example owns epic flags and sprint/release items; Morgan Manager owns two epic flags.
        self.assertEqual(owners, {"Alex Example", "Quinn Quality", "Morgan Manager", "Pat Product"})
        mentions = self._mentions(owners)
        self.assertEqual(mentions, {"Alex Example": "<@U000TEST1>", "Morgan Manager": "<@U000TEST2>"})
        report = cr.build_report(sprint, computed, [], mentions, team_owner="Pat Product", epic_gaps=gaps)
        epic = next(r for r in report["replies"] if r.startswith("*Epic fields*"))
        self.assertIn("• <@U000TEST2>: TEST-106 (no QA Contact, feature has none, feature closed), TEST-112 (no Target Version)", epic)
        self.assertIn("• <@U000TEST1>: no Target Version for TEST-104, 107; TEST-104 (no QA Contact, feature has none, feature closed)", epic)
        self.assertIn("• Pat Product: no assignee for TEST-102, 103, 108; no QA Contact for TEST-102, 103; "
                      "TEST-103 (no Target Version); TEST-108 (no QA Contact, feature could not be read)", epic)
        self.assertIn("• Pat Product: re-plan the sprint (8.4 SP over capacity)", report["replies"][0])

    def test_empty_epic_result_adds_no_owner(self):
        sprint, computed = self._parts()
        jql = EPICS_JQL + " ORDER BY key DESC"
        gaps = cr.epic_field_gaps(_adapt(f"No issues found for JQL: {jql}", jql, "epics"), [])
        with_empty = self._owners(sprint, computed, gaps)
        self.assertEqual(with_empty, {"Alex Example", "Quinn Quality"})

    def test_no_sprint_and_only_epic_flags(self):
        _, _, gaps = _mix()
        self.assertEqual(self._owners(None, [], gaps), {"Alex Example", "Morgan Manager"})


class EndToEndTest(unittest.TestCase):
    def test_nothing_to_flag(self):
        report = weekly_report(EPICS_CLEAN, parent_keys=("FEAT-1", "FEAT-2"),
                               sprint_issues=CLEAN_ISSUES, checks=CLEAN_CHECKS)
        self.assertEqual(report["top_post"], [
            "🟢 Edge weekly health: Test Sprint 11 (week 1, capacity); 9.1 development",
            "No risks this week: committed 20 SP in 1 issue; capacity 21.6 SP (velocity 24 × availability 0.90)",
            "0 people have items"])
        self.assertFalse(any("Epic" in r for r in report["replies"]))
        self.assertFalse(any("Left out" in r for r in report["replies"]))
        self.assertEqual(len(report["replies"]), 2)  # sprint, release

    def test_realistic_mix(self):
        report = weekly_report(EPICS_MIX)
        epic = next(r for r in report["replies"] if r.startswith("*Epic fields*"))
        self.assertEqual(epic.splitlines(), [
            "*Epic fields* (8 epics: 4 no Target Version, 5 no QA Contact, 1 Target Version ≠ feature, "
            "3 feature closed or in backlog, 3 no assignee; ✓ 2 complete). Key: \"Target Version ≠ feature\" = retarget, split or keep?",
            "• <@U000TEST1>: no Target Version for TEST-104, 107; TEST-104 (no QA Contact, feature has none, feature closed); "
            "TEST-107 (feature in backlog); TEST-101 (Target Version ≠ feature)",
            "• no owner set: no assignee for TEST-102, 103, 108; no QA Contact for TEST-102, 103; "
            "TEST-103 (no Target Version); TEST-108 (no QA Contact, feature could not be read)",
            "• Morgan Manager: TEST-106 (no QA Contact, feature has none, feature closed), TEST-112 (no Target Version)",
            "Left out: 9.1: capacity from 1 assignee(s) not on the roster: their 12 SP counts as scope, not capacity; "
            "Target Version and parent status checks for 1 epic(s) under FEAT-9: parent could not be read: "
            "not returned by Jira (restricted, deleted or no permission)",
        ])
        # At most 3 replies: sprint, release, epic fields (ending with the one Left out line).
        heads = [r.splitlines()[0] for r in report["replies"]]
        self.assertEqual([h.split("*")[1] for h in heads],
                         ["Sprint health: Test Sprint 11", "9.1 release health", "Epic fields"])
        self.assertEqual(len(report["top_post"]), 3)


if __name__ == "__main__":
    unittest.main()
