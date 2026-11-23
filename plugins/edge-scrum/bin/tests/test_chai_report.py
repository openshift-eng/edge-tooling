"""Tests for chai_report.py and the per-sprint availability in scrum_context.py.

All releases, sprints, dates, keys and names are synthetic: the repository is
public. Do not add real Product Pages dates, names or Jira keys.
"""

import datetime as dt
import os
import sys
import unittest
from importlib import import_module

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import chai_jira_adapter as ad  # noqa: E402
import chai_report as cr  # noqa: E402
import scrum_context as sc  # noqa: E402
from tests.test_chai_jira_adapter import ROSTER_MD, block, keys_desc, page  # noqa: E402

run_checks = import_module("run-checks")

D = dt.date
BOARD = 1000
FIRST_START = D(2030, 1, 7)  # Monday; synthetic 3-week sprints, Test Sprint 10 onwards


def _sprints(count=6):
    out = []
    for i in range(count):
        start = FIRST_START + dt.timedelta(weeks=3 * i)
        end = start + dt.timedelta(weeks=3)
        out.append({"id": str(500 + i), "name": f"Test Sprint {10 + i}", "state": "closed",
                    "originBoardId": BOARD, "start_date": start.isoformat(), "end_date": end.isoformat()})
    return out


def _sprint(number):
    start = FIRST_START + dt.timedelta(weeks=3 * (number - 10))
    return {"name": f"Test Sprint {number}", "number": number, "id": str(490 + number),
            "start": start.isoformat(), "end": (start + dt.timedelta(weeks=3)).isoformat(),
            "projected": False}


def _window(release, phase, refinement=True, branch_cut=True):
    return {"release": release, "phase": phase,
            "refinement_sprint": _sprint(10) if refinement else sc.NOT_SCHEDULED,
            "branch_cut_sprint": _sprint(15) if branch_cut else sc.NOT_SCHEDULED,
            "pencils_down_sprint": _sprint(14), "development_sprints_remaining": 2}


class ModeAndCapacityTest(unittest.TestCase):
    def test_mode_by_sprint_week(self):
        self.assertEqual(cr.sprint_mode(1), "capacity")
        self.assertEqual(cr.sprint_mode(2), "mid-sprint")
        self.assertEqual(cr.sprint_mode(3), "mid-sprint")
        self.assertIsNone(cr.sprint_mode(None))

    def test_capacity_with_availability(self):
        cap = cr.sprint_capacity(80, 0.9)
        self.assertEqual(cap["capacity_sp"], 72.0)
        self.assertTrue(cap["pto_adjusted"])
        self.assertIn("availability 0.90", cap["label"])

    def test_capacity_without_availability_is_not_pto_adjusted(self):
        for avail in (None, sc.ROSTER_NOT_CONFIGURED, sc.NOT_AVAILABLE, sc.OUTSIDE_CALENDAR_WINDOW):
            cap = cr.sprint_capacity(80, avail)
            self.assertEqual(cap["capacity_sp"], 80)
            self.assertFalse(cap["pto_adjusted"])
            self.assertEqual(cap["label"], "not PTO-adjusted")

    def test_capacity_without_velocity_is_not_estimated(self):
        cap = cr.sprint_capacity(None, 0.9)
        self.assertIsNone(cap["capacity_sp"])

    def test_release_capacity_mixed_availability(self):
        sprints = [{"number": 13, "availability": 0.8}, {"number": 14, "availability": 1.0},
                   {"number": 15, "availability": sc.OUTSIDE_CALENDAR_WINDOW}]
        cap = cr.release_capacity(50, sprints)
        self.assertEqual(cap["capacity_sp"], 140)  # 50 × (0.8 + 1 + 1)
        self.assertEqual(cap["availability_csv"], "0.8,1,1")
        self.assertEqual(cap["not_adjusted"], [15])
        self.assertIn("not PTO-adjusted for sprint(s) 15", cap["label"])
        full = cr.release_capacity(50, [{"number": 13, "availability": 0.5}])
        self.assertEqual((full["capacity_sp"], full["pto_adjusted"]), (25, True))
        none = cr.release_capacity(50, [{"number": 13, "availability": None}])
        self.assertEqual(none["label"], "not PTO-adjusted")


class ReleaseInputsTest(unittest.TestCase):
    """What release_health may claim from the inputs it was given."""

    CHECKS = {"meta": {"total_remaining_sp": 10, "total_capacity_sp": 90, "capacity_basis": "velocity"},
              "cut_line": [], "process_gaps": {"roster_drift": []}}
    SECTION = {"release": "9.2", "phase": "development",
               "window": {"refinement_sprint": {"number": 12}, "branch_cut_sprint": {"number": 16}}}
    FEATURES = {"features": [{"key": "TEST-1", "type": "Feature", "status": "In Progress", "sme": "Alex Example",
                              "qa_contact": "Quinn Quality", "docs_approver": "Dana Docs"}]}

    def test_rank_order_only_from_a_complete_rank_fetch(self):
        one = [{"text": page(["TEST-1"])}]
        self.assertTrue(cr.rank_order_available(ad.adapt_fetch({"order": "rank", "pages": one})))
        self.assertFalse(cr.rank_order_available(ad.adapt_fetch({"pages": [{"text": page(["TEST-1"])}]})))
        more = page(keys_desc("TEST", 60, 50), final=False)  # the rank page shows only the first 50
        self.assertFalse(cr.rank_order_available(ad.adapt_fetch({"order": "rank", "pages": [{"text": more}]})))
        self.assertFalse(cr.rank_order_available(None))

    def test_rank_order_needs_one_page_in_all(self):
        parts = [{"project": "TEST", "pages": [{"text": page(["TEST-2"])}]},
                 {"project": "OTHER", "pages": [{"text": page(["OTHER-1"])}]}]
        two = ad.adapt_fetch({"order": "rank", "parts": parts})
        self.assertEqual((two["fetch"]["complete"], two["fetch"]["order"], two["fetch"]["pages"]), (True, "rank", 2))
        self.assertFalse(cr.rank_order_available(two))  # one final page per part is no global rank
        out = cr.release_health(self.CHECKS | {"cut_line": [{"feature_key": "TEST-1", "fits": "over"}]}, self.SECTION,
                                rank_available=cr.rank_order_available(two))
        self.assertEqual(out["below_cut_line"], [])
        self.assertIn("9.2 cut line", [x["what"] for x in out["left_out"]])

    def test_unknown_development_window_qualifies_the_header(self):
        out = cr.release_health(self.CHECKS, {"release": "9.2", "phase": "development"}, features=self.FEATURES,
                                epics={"feature_to_epics": {}, "epics": []}, spikes={"spike_map": {}},
                                stories={"stories": []}, links={})
        self.assertEqual(out["not_checked"], ["schedule"])
        header = cr.build_report(None, [out])["replies"][0].splitlines()[0]
        self.assertTrue(header.endswith("; schedule not checked"), header)
        self.assertNotIn("on track", header)  # no on-track count, not even a qualified one
        known = cr.release_health(self.CHECKS, self.SECTION, features=self.FEATURES,
                                  epics={"feature_to_epics": {}, "epics": []}, spikes={"spike_map": {}},
                                  stories={"stories": []}, links={}, current_sprint_number=14)
        self.assertIn("; ✓ 1 of 1 feature(s) on track", cr.build_report(None, [known])["replies"][0])

    def test_checks_whose_input_is_missing_are_named_not_counted_on_track(self):
        out = cr.release_health(self.CHECKS, self.SECTION, features=self.FEATURES, epics=None, spikes=None,
                                stories=None, links={}, current_sprint_number=14)
        self.assertEqual([x["what"] for x in out["left_out"]],
                         ["9.2 epic checks (epic owner, epic blocks, schedule)", "9.2 refinement-overdue check"])
        header = cr.build_report(None, [out])["replies"][0].splitlines()[0]
        self.assertTrue(header.endswith("; schedule not checked (epics, refinement spikes not checked)"), header)
        self.assertNotIn("on track", header)  # no epics: the schedule check could not run
        out = cr.release_health(self.CHECKS, self.SECTION, features=self.FEATURES,
                                epics={"feature_to_epics": {}, "epics": []}, spikes={"spike_map": {}}, stories=None,
                                links={}, current_sprint_number=14)
        self.assertEqual(out["left_out"], [{"what": "9.2 schedule check", "why": "epic issues not fetched"}])
        self.assertEqual(out["not_checked"], ["schedule"])

    def test_missing_epics_name_only_checks_that_would_have_run(self):
        out = cr.release_health(self.CHECKS, {"release": "9.2", "phase": "development"}, features=self.FEATURES,
                                epics=None, spikes={"spike_map": {}}, stories={"stories": []}, links=None)
        self.assertEqual([x["what"] for x in out["left_out"]],
                         ["9.2 schedule check", "9.2 epic checks (epic owner)", "9.2 feature and epic blocks"])

    def test_all_inputs_given_leave_nothing_out(self):
        out = cr.release_health(self.CHECKS, self.SECTION, features=self.FEATURES,
                                epics={"feature_to_epics": {}, "epics": []}, spikes={"spike_map": {}},
                                stories={"stories": []}, links={}, current_sprint_number=14)
        self.assertEqual((out["left_out"], out["not_checked"]), ([], []))


class ReleaseGateTest(unittest.TestCase):
    """Releases are reported only when non-Closed Edge Features target them (complete gate)."""

    @staticmethod
    def _fetch(*issues):
        blocks = [block(key, type_=kind, status=status, sprints=None, sp=None,
                        extra=[f"Labels: {labels}", f"customfield_10855: {versions}"])
                  for key, kind, status, labels, versions in issues]
        text = f"Found {len(blocks)} issues (showing {len(blocks)}):\n" + "\n".join(blocks) + "\n"
        return ad.adapt_fetch({"name": "feature_gate", "pages": [{"text": text}]})

    def test_counts_non_closed_edge_features_per_target_version(self):
        gate = cr.release_feature_gate(self._fetch(
            ("FEAT-1", "Feature", "In Progress", "ocpedge-plan", "openshift-9.2"),
            ("FEAT-2", "Feature", "New", "MicroShift", "openshift-9.2, openshift-9.3"),
            ("FEAT-3", "Initiative", "In Progress", "ocpedge-plan", "openshift-9.4"),
            ("FEAT-4", "Feature", "Closed", "ocpedge-plan", "openshift-9.5"),
            ("FEAT-5", "Feature", "In Progress", "other-label", "openshift-9.6"),
            ("FEAT-6", "Feature", "In Progress", "ocpedge-plan", "9.7")))
        self.assertEqual(gate, {"complete": True, "counts": {"9.2": 2, "9.3": 1}, "releases": ["9.2", "9.3"],
                                "why": None})

    def test_incomplete_gate_is_unknown_never_zero(self):
        failed = cr.release_feature_gate(ad.adapt_fetch({"name": "feature_gate", "pages": [{"error": "x"}]}))
        self.assertEqual((failed["complete"], failed["counts"], failed["releases"]), (False, None, []))
        self.assertEqual(failed["why"], "fetch: page 1: query_jira call failed: x")
        cut = ad.adapt_fetch({"pages": [{"text": page(keys_desc("TEST", 60, 50), final=False)}]})
        self.assertFalse(cr.release_feature_gate(cut)["complete"])  # stopped after a "more match" page
        no_version = ad.adapt_fetch({"pages": [{"text": page(["TEST-1"])}]})  # Target Version not requested
        self.assertEqual(cr.release_feature_gate(no_version)["why"], "TEST-1: Target Version not returned")
        no_labels = ad.adapt_fetch({"pages": [{"text": page(["TEST-2"], extra=["customfield_10855: openshift-9.2"])}]})
        self.assertEqual(cr.release_feature_gate(no_labels)["why"], "TEST-2: labels not returned")
        self.assertFalse(cr.release_feature_gate(None)["complete"])
        sections, missing = cr.select_sections({"release_in_progress": [{"release": "9.2", "phase": "development"}]},
                                               [_window("9.2", "development")], failed)
        why = "release Feature gate incomplete: fetch: page 1: query_jira call failed: x"
        self.assertEqual((sections, missing), ([], [{"what": "Release sections", "why": why}]))

    def test_unknown_type_or_status_is_incomplete_before_filtering(self):
        base = {"key": "TEST-1", "issue_type": {"name": "Feature"}, "status": {"name": "New"},
                "labels": ["ocpedge-plan"], "customfield_10855": [{"name": "openshift-9.2"}]}
        cases = {"no issue type": (dict(base, issue_type=None), "TEST-1: issue type not readable"),
                 "blank issue type": (dict(base, issue_type={"name": "  "}), "TEST-1: issue type not readable"),
                 "no status": (dict(base, status=None), "TEST-1: status not readable"),
                 "blank status": (dict(base, status={"name": ""}), "TEST-1: status not readable"),
                 "type as a string": (dict(base, issue_type="Feature"), "TEST-1: issue type not readable"),
                 "type name as a list": (dict(base, issue_type={"name": ["Feature"]}),
                                         "TEST-1: issue type not readable"),
                 "status name as a number": (dict(base, status={"name": 1}), "TEST-1: status not readable")}
        for name, (issue, why) in cases.items():
            with self.subTest(name):
                gate = cr.release_feature_gate({"fetch": {"complete": True}, "issues": [issue]})
                self.assertEqual((gate["complete"], gate["counts"], gate["why"]), (False, None, why))
        self.assertEqual(cr.release_feature_gate({"fetch": {"complete": True}, "issues": [base]})["counts"], {"9.2": 1})
        custom = dict(base, status={"name": "Custom Review"})  # any non-Closed status name still counts
        counted = cr.release_feature_gate({"fetch": {"complete": True}, "issues": [custom]})
        self.assertEqual(counted["counts"], {"9.2": 1})

    def test_release_without_edge_features_is_omitted_silently(self):
        gate = {"complete": True, "counts": {"9.3": 2}, "releases": ["9.3"], "why": None}
        for phase in ("refinement", "development", sc.NOT_SCHEDULED, sc.PHASE_LOOKUP_FAILED):
            with self.subTest(phase=phase):
                sections, missing = cr.select_sections({"release_in_progress": [{"release": "9.2", "phase": phase}]},
                                                       [_window("9.2", phase), _window("9.3", "not started")], gate)
                self.assertEqual(sections, [])
                self.assertEqual(missing, [{"what": "Release section", "why": "no release in progress"}])

    def test_complete_empty_gate_adds_nothing_to_left_out(self):
        gate = {"complete": True, "counts": {}, "releases": [], "why": None}
        context = {"release_in_progress": [{"release": "9.2", "phase": "development"}]}
        windows = [_window("9.1", "finished"), _window("9.2", "development"),
                   {"release": "9.3", "phase": sc.NOT_SCHEDULED},
                   {"release": "9.4", "phase": sc.PHASE_LOOKUP_FAILED, "milestones": sc.NOT_AVAILABLE}]
        self.assertEqual(cr.select_sections(context, windows, gate), ([], []))
        self.assertEqual(cr.select_sections({}, [], gate), ([], []))
        # Unknown is never zero: an incomplete gate still says why.
        unknown = cr.select_sections(context, windows, {"complete": False, "counts": None, "releases": [], "why": "x"})
        self.assertEqual(unknown, ([], [{"what": "Release sections", "why": "release Feature gate incomplete: x"}]))

    def test_gated_releases_keep_their_schedule_phase(self):
        gate = {"complete": True, "counts": {"9.2": 1, "9.3": 4, "9.4": 1}, "releases": ["9.2", "9.3", "9.4"],
                "why": None}
        failed = {"release": "9.3", "phase": sc.PHASE_LOOKUP_FAILED, "milestones": sc.NOT_AVAILABLE}
        sections, missing = cr.select_sections({}, [_window("9.2", "development"), failed], gate)
        self.assertEqual([s["release"] for s in sections], ["9.2"])
        self.assertEqual(missing, [
            {"what": "9.3 release section", "why": "release phase unknown: Product Pages lookup failed"},
            {"what": "9.4 release section", "why": "phase unknown (not available)"}])  # no window read: not lost

class SectionSelectionTest(unittest.TestCase):
    def test_phase_selects_section_kind(self):
        cases = {"refinement": "refinement", "development": "release-health",
                 "pencils-down sprint": "release-health", "pre-branch-cut": "release-health"}
        for phase, kind in cases.items():
            sections, missing = cr.select_sections({}, [_window("9.1", phase)])
            self.assertEqual([s["kind"] for s in sections], [kind], phase)
            self.assertEqual(missing, [])
            self.assertEqual(sections[0]["pencils_down"], phase == "pencils-down sprint")

    def test_two_releases_in_progress_get_their_own_sections(self):
        context = {"release_in_progress": [{"release": "9.1", "phase": "pre-branch-cut"},
                                           {"release": "9.2", "phase": "refinement"}]}
        sections, missing = cr.select_sections(
            context, [_window("9.2", "refinement"), _window("9.1", "pre-branch-cut")])
        self.assertEqual([(s["release"], s["kind"]) for s in sections],
                         [("9.1", "release-health"), ("9.2", "refinement")])
        self.assertEqual(missing, [])

    def test_no_release_in_progress(self):
        sections, missing = cr.select_sections({"release_in_progress": sc.NOT_AVAILABLE}, [])
        self.assertEqual(sections, [])
        self.assertEqual(missing, [{"what": "Release section", "why": "no release in progress"}])

    def test_not_started_and_finished_releases_are_skipped(self):
        sections, missing = cr.select_sections(
            {}, [_window("9.0", "finished"), _window("9.3", "not started")])
        self.assertEqual(sections, [])
        self.assertEqual(missing[0]["why"], "no release in progress")

    def test_unscheduled_unknown_and_unplaceable_releases_are_missing(self):
        sections, missing = cr.select_sections({}, [
            _window("9.1", "development"),
            {"release": "9.2", "phase": sc.NOT_SCHEDULED},
            {"release": "9.3", "phase": "not available"},
            _window("9.4", "development", branch_cut=False)])
        self.assertEqual([s["release"] for s in sections], ["9.1"])
        self.assertEqual([(m["what"], m["why"]) for m in missing], [
            ("9.2 release section", "no Product Pages schedule yet"),
            ("9.3 release section", "phase unknown (not available)"),
            ("9.4 release section", "a release milestone could not be placed on a sprint")])

    def test_not_scheduled_reason_names_what_is_missing(self):
        base = {"phase": sc.NOT_SCHEDULED}
        no_milestone = {"development_branch_opens": sc.NOT_SCHEDULED, "pencils_down": "2030-05-13",
                        "master_branch_open": "2030-05-16"}
        unplaced = dict(no_milestone, development_branch_opens="2030-02-20")
        _, missing = cr.select_sections({}, [dict(base, release="9.2", milestones={}),
                                             dict(base, release="9.3", milestones=no_milestone),
                                             dict(base, release="9.4", milestones=unplaced)])
        self.assertEqual([m["why"] for m in missing], [
            "no Product Pages schedule yet",
            'Product Pages schedule has no readable "Development Branch opens" milestone '
            '(nor a previous release\'s "Master Branch Open for 9.3")',
            "refinement sprint could not be placed on a sprint"])

    def test_in_progress_entry_without_window_uses_context(self):
        context = {"release_in_progress": [{"release": "9.1", "phase": "development"}]}
        sections, missing = cr.select_sections(context, [])
        self.assertEqual(sections, [])
        self.assertIn("could not be placed", missing[0]["why"])


class ScrumContextAvailabilityTest(unittest.TestCase):
    def _data(self, today, **extra):
        data = {"today": today, "board_id": BOARD, "sprints": _sprints(),
                "absences": [{"person_id": "p1", "start": "2030-01-28", "end": "2030-02-01"}],
                "engineer_count": 2}
        data.update(extra)
        return data

    def test_current_sprint_availability(self):
        # Test Sprint 11 runs 2030-01-28 .. 2030-02-18: 15 working days x 2 people, 5 off.
        out = sc.build_context(self._data("2030-01-29"))
        self.assertEqual(out["current_sprint"]["number"], 11)
        self.assertEqual(out["current_sprint_availability"], round(1 - 5 / 30, 2))
        self.assertEqual(out["sprint_availability"][0]["number"], 11)

    def test_sprints_up_to_branch_cut(self):
        schedules = {"9.1": [{"name": "9.1 Development Branch opens", "date": "2030-01-07"},
                             {"name": "Pencils Down / Stabilization", "date": "2030-03-11"},
                             {"name": "Master Branch Open for 9.2", "date": "2030-03-25"}]}
        out = sc.build_context(self._data("2030-01-29", release="9.1", schedules=schedules,
                                          absence_window={"start": "2030-01-08", "end": "2030-03-10"}))
        self.assertEqual(out["release"]["branch_cut_sprint"]["number"], 13)
        by_number = {s["number"]: s["availability"] for s in out["sprint_availability"]}
        self.assertEqual(sorted(by_number), [11, 12, 13])
        self.assertEqual(by_number[12], 1.0)
        # A sprint running past the calendar window read is not estimated.
        self.assertEqual(by_number[13], sc.OUTSIDE_CALENDAR_WINDOW)

    def test_without_roster_availability_is_not_available(self):
        out = sc.build_context(self._data("2030-01-29", engineer_count=None))
        self.assertEqual(out["current_sprint_availability"], sc.ROSTER_NOT_CONFIGURED)
        self.assertEqual(cr.sprint_capacity(60, out["current_sprint_availability"])["label"],
                         "not PTO-adjusted")

    def test_not_counted_member_is_excluded_from_engineer_count(self):
        roster = ad.parse_team_roster(ROSTER_MD)
        # 3 counted engineers (a "Not counted" principal engineer is excluded), one off 5 days.
        out = sc.build_context(self._data("2030-01-29", engineer_count=roster["engineer_count"]))
        self.assertEqual(out["current_sprint_availability"], round(1 - 5 / 45, 2))


ISSUES = {
    "total_sp": 30, "total_done_sp": 4, "total_remaining_sp": 26, "total_issues": 4,
    "issues": [
        {"key": "TEST-1", "type": "Story", "sp": 13, "assignee_display": "Alex Example", "epic_key": "TEST-100",
         "has_ac": True, "blocked_by": ["TEST-9"], "labels": [], "stale": False, "flagged": False},
        {"key": "TEST-2", "type": "Story", "sp": 13, "assignee_display": "Alex Example", "epic_key": "No Epic",
         "has_ac": True, "blocked_by": [], "labels": [], "stale": True, "flagged": False},
        {"key": "TEST-3", "type": "Bug", "sp": 0, "assignee_display": "Quinn Quality", "epic_key": "TEST-100",
         "has_ac": False, "blocked_by": [], "labels": [], "stale": False, "flagged": False},
        {"key": "TEST-4", "type": "Task", "sp": 4, "assignee_display": "Unassigned", "epic_key": "TEST-100",
         "has_ac": True, "blocked_by": [], "labels": [], "stale": False, "flagged": False},
    ]}
SPRINT = {"name": "Test Sprint 11", "number": 11, "start": "2030-01-28", "end": "2030-02-18", "week": 1}
CHECKS = {
    "meta": {"total_remaining_sp": 120, "total_capacity_sp": 90, "capacity_basis": "velocity",
             "team_velocity": 45.0, "remaining_sprints": 2, "gap_sp": -30,
             "hidden_scope_low": 10, "hidden_scope_high": 25, "total_unpointed_stories": 4, "unpointed_bugs": 2},
    "capacity": [{"person": "aexample@redhat.com", "display_name": "Alex Example", "assigned_sp": 70,
                  "status": "ABOVE_GUIDELINE", "in_roster": True}],
    "cut_line": [{"feature_key": "FEAT-1", "sme": "Morgan Manager", "fits": "fits"},
                 {"feature_key": "FEAT-2", "sme": "Alex Example", "fits": "partial"},
                 {"feature_key": "FEAT-3", "sme": "None", "fits": "over"}],
    "composite": [{"feature_key": "FEAT-1", "sme": "Morgan Manager", "composite_risk": "HIGH"}],
    "timeline": [{"feature_key": "FEAT-2", "sme": "Alex Example", "risk": "HIGH"}],
    "process_gaps": {"roster_drift": [{"person": "Robin Outsider", "display_name": "Robin Outsider",
                                       "assigned_sp": 12}]},
}


class TeamLevelOutputTest(unittest.TestCase):
    def _report(self, mentions=None):
        sprint = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=24, availability=0.9,
                                  skipped_signals=[ad.SKIPPED_FLAGGED])
        section = {"release": "9.1", "phase": "pencils-down sprint", "pencils_down": True}
        release = cr.release_health(CHECKS, section, cr.release_capacity(45, [{"number": 14, "availability": 1},
                                                                              {"number": 15, "availability": 1}]))
        return sprint, release, cr.build_report(sprint, [release], [{"what": "9.2 release section",
                                                                     "why": "no Product Pages schedule yet"}],
                                                mentions)

    def test_capacity_mode_figures(self):
        sprint, _, _ = self._report()
        self.assertEqual(sprint["capacity"]["capacity_sp"], 21.6)
        self.assertEqual(sprint["unpointed"], ["TEST-3"])
        self.assertEqual(sprint["no_epic"], ["TEST-2"])
        self.assertNotIn("missing_ac", sprint)  # acceptance criteria are not checked (descriptions come back cut)
        self.assertEqual(sprint["by_type"]["Story"], {"issues": 2, "sp": 26})
        self.assertIn("8.4 SP over", sprint["risks"][0]["text"])

    def test_report_has_no_per_person_data(self):
        _, release, report = self._report()
        text = "\n".join(report["top_post"] + report["replies"])
        self.assertNotIn("ABOVE_GUIDELINE", text)
        self.assertNotIn("Robin Outsider", text)  # roster drift is a count
        self.assertNotIn("70", text)  # a person's assigned SP
        self.assertNotIn("Load by Person", text)
        for name in ("Alex Example", "Quinn Quality"):  # names only as owners or in the people line
            for line in text.splitlines():
                if name in line:
                    self.assertTrue(line.startswith(f"• {name}: ") or line.endswith("have items: " + ", ".join(report["people"]))
                                    or "people have items" in line, line)
        self.assertNotIn("capacity", release)  # no per-person rows carried through
        self.assertEqual(release["roster_drift"], {"people": 1, "sp": 12})

    def test_top_post_is_three_lines_with_action_count(self):
        _, release, report = self._report()
        top = report["top_post"]
        self.assertEqual(len(top), 3)
        self.assertTrue(top[0].startswith("🔴 Edge weekly health: Test Sprint 11 (week 1, capacity)"))
        self.assertIn("9.1 pencils-down sprint", top[0])
        self.assertTrue(top[1].startswith("Biggest risk: Sprint committed 30 SP vs capacity 21.6 SP"))
        self.assertEqual(top[2], "2 people have items: Alex Example, Quinn Quality")
        self.assertEqual(release["below_cut_line"], ["FEAT-2", "FEAT-3"])

    def test_owner_mentions_and_fallbacks(self):
        _, _, report = self._report(mentions={"Alex Example": "<@U000TEST1>"})
        sprint_reply, release_reply = report["replies"][:2]
        self.assertIn("• Quinn Quality: TEST-3 (unpointed)", sprint_reply)
        self.assertIn("• <@U000TEST1>: TEST-2 (no epic)", sprint_reply)
        self.assertIn("• no owner set: re-plan the sprint (8.4 SP over capacity)", sprint_reply)
        self.assertIn("• <@U000TEST1>: FEAT-2 (below the cut line)", release_reply)
        self.assertIn("• no owner set: FEAT-3 (below the cut line)", release_reply)

    def test_left_out_lists_missing_sections_and_signals(self):
        _, _, report = self._report()
        left = report["replies"][-1].splitlines()[-1]
        self.assertTrue(left.startswith("Left out: "))
        self.assertIn("9.2 release section: no Product Pages schedule yet", left)
        self.assertIn("flagged signal: query_jira rejected customfield_10021", left)
        self.assertIn("capacity from 1 assignee(s) not on the roster", left)
        self.assertEqual(len(report["replies"]), 2)  # sprint, release; no action-items or left-out reply

    def test_rank_unavailable_leaves_cut_line_out(self):
        release = cr.release_health(CHECKS, {"release": "9.1", "phase": "development"}, rank_available=False)
        self.assertEqual(release["below_cut_line"], [])
        self.assertIn("cut line", release["left_out"][0]["what"])

    def test_roster_fallback_capacity_is_not_reported(self):
        checks = dict(CHECKS, meta=dict(CHECKS["meta"], capacity_basis="roster_fallback"))
        release = cr.release_health(checks, {"release": "9.1", "phase": "development"})
        self.assertIsNone(release["capacity_sp"])
        self.assertEqual((release["below_cut_line"], release["items"], release["risks"]), ([], [], []))
        self.assertEqual(release["left_out"][0]["why"], "velocity not available")
        self.assertIn("capacity not available", cr._section_header(release))

    def test_nothing_to_flag(self):
        clean = {"total_sp": 20, "total_issues": 1, "issues": [
            {"key": "TEST-1", "type": "Story", "sp": 20, "assignee_display": "Alex Example",
             "epic_key": "TEST-100", "has_ac": True}]}
        sprint = cr.sprint_health(clean, SPRINT, "2030-01-29", "capacity", velocity=24, availability=1.0)
        report = cr.build_report(sprint, [])
        self.assertEqual(report["top_post"][1],
                         "No risks this week: committed 20 SP in 1 issue; capacity 24 SP "
                         "(velocity 24 × availability 1.00)")
        self.assertEqual(report["top_post"][2], "0 people have items")
        self.assertTrue(report["top_post"][0].startswith("🟢"))


class MidSprintTest(unittest.TestCase):
    def test_burndown_and_blockers(self):
        sprint = dict(SPRINT, week=2)
        out = cr.sprint_health(ISSUES, sprint, "2030-02-07", "mid-sprint",
                               skipped_signals=[ad.SKIPPED_FLAGGED])
        b = out["burndown"]
        self.assertEqual((b["days_elapsed"], b["total_days"]), (11, 22))
        self.assertEqual(b["expected_done_sp"], 15.0)
        self.assertEqual(b["status"], "Behind")
        # Explicit blocks only: TEST-1 is blocked by TEST-9; TEST-2 (stale) is not listed.
        self.assertEqual(out["blockers"], [{"key": "TEST-1", "reasons": ["blocked by TEST-9"]}])

    def test_done_issues_are_not_listed_as_blocked(self):
        issues = dict(ISSUES, issues=[dict(i, status="Closed") if i["key"] == "TEST-1" else i
                                      for i in ISSUES["issues"]])
        out = cr.sprint_health(issues, dict(SPRINT, week=2), "2030-02-07", "mid-sprint")
        self.assertEqual(out["blockers"], [])
        self.assertNotIn("TEST-1", [f["key"] for f in out["items"]])

    def test_incomplete_links_leave_blocked_signal_out(self):
        out = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint",
                               links_complete=False)
        self.assertEqual(out["blockers"], [])  # links unknown, and stale is not a block
        self.assertIn("blocked-by links", [x["what"] for x in out["left_out"]])


class RefinementGapsTest(unittest.TestCase):
    def test_spike_gaps_and_owners(self):
        features = {"features": [
            {"key": "FEAT-1", "sme": "Alex Example", "qa_contact": "Quinn Quality"},
            {"key": "FEAT-2", "sme": "None", "qa_contact": "unassigned_jira"},
            {"key": "FEAT-3", "sme": "Alex Example", "qa_contact": "Quinn Quality"}]}
        base = {"spike_key": None, "spike_status": "Missing", "spike_missing": True, "spike_on_epic": False,
                "epics_appear_refined": False, "spike_in_ref_sprint": False, "spike_overdue": False,
                "test_plan_check_skipped": False, "test_plan_spike_missing": True,
                "test_plan_spike_overdue": False, "test_plan_spike_key": None}
        spikes = {"spike_map": {
            "FEAT-1": base,
            "FEAT-2": dict(base, test_plan_check_skipped=True),
            "FEAT-3": dict(base, spike_key="TEST-60", spike_status="In Progress", spike_missing=False,
                           spike_in_ref_sprint=False, test_plan_spike_missing=False,
                           test_plan_spike_key="TEST-61", test_plan_spike_status="Closed")}}
        section = {"release": "9.2", "phase": "refinement", "window": {"refinement_sprint": _sprint(16)}}
        out = cr.refinement_gaps(features, spikes, section)
        found = {(f["key"], f["label"]): f["owner"] for f in out["items"] if f["check"] != "unsized"}
        self.assertEqual(found[("FEAT-1", "no refinement spike")], "Alex Example")
        self.assertEqual([k for k in found if k[0] == "FEAT-2"], [("FEAT-2", "no SME")])  # only "no SME"
        self.assertEqual(found[("FEAT-1", "test plan spike missing")], "Quinn Quality")  # the QE Lead
        self.assertEqual(found[("FEAT-3", "spike not in the refinement sprint")], "Alex Example")
        self.assertEqual(out["refinement_sprint"], 16)
        self.assertEqual([r["text"] for r in out["risks"]][:2],
                         ["9.2: 1 feature(s) have no SME", "9.2: 1 feature(s) with a missing or overdue refinement spike"])


class NoEstimateTest(unittest.TestCase):
    """Decision 0018: the report counts unpointed work and never estimates its SP."""

    def test_report_has_unpointed_count_and_no_sp_range(self):
        release = cr.release_health(CHECKS, {"release": "9.1", "phase": "development"},
                                    cr.release_capacity(45, [{"number": 14, "availability": 1}]))
        sprint = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=24, availability=0.9)
        report = cr.build_report(sprint, [release])
        text = "\n".join(report["top_post"] + report["replies"])
        self.assertIn("6 unpointed items not counted", text)
        self.assertNotRegex(text, r"\d+(\.\d+)?\s*[–-]\s*\d+(\.\d+)?\s*SP")
        self.assertNotIn("about", text)
        self.assertNotIn("hidden_scope", release)

    def test_run_checks_can_skip_the_estimator(self):
        features = [{"key": "F-1", "all_stories": [
            {"key": "S-1", "type": "Story", "status": "New", "sp": 0, "assignee": "a"},
            {"key": "S-2", "type": "Bug", "status": "New", "sp": 0, "assignee": None},
            {"key": "S-3", "type": "Story", "status": "Closed", "sp": 5, "assignee": "a"}]}]
        hidden = run_checks.unestimated_hidden_scope(features, {"F-1"})
        self.assertEqual((hidden["unpointed_open"], hidden["unpointed_bugs"], hidden["estimate_low"]), (1, 1, None))
        estimated = run_checks.estimate_hidden_scope(features, {"F-1"})
        self.assertEqual(estimated["unpointed_open"], 1)
        self.assertIsNotNone(estimated["estimate_low"])  # Claude Code path unchanged


class ProjectionHorizonTest(unittest.TestCase):
    def test_current_sprint_after_the_branch_cut_sprint_is_still_generated(self):
        # One board sprint ending 2030-01-28; today falls in a projected sprint
        # (2030-03-11 .. 2030-04-01) after a release horizon ending 2030-02-18.
        sprints = sc.board_sprints([{"id": "500", "name": "Test Sprint 10", "state": "closed",
                                     "originBoardId": BOARD, "start_date": "2030-01-07",
                                     "end_date": "2030-01-28"}], BOARD)
        today = D(2030, 3, 12)
        current = sc.current_sprint(sprints, today)
        self.assertEqual((current["number"], current["projected"]), (13, True))
        window = sc.absence_window(None, today, [])
        out = sc.availability_by_sprint(sprints, current, D(2030, 2, 18), [], 2, window)
        self.assertEqual([(s["number"], s["availability"]) for s in out], [(13, 1.0)])


class AbsenceWindowTest(unittest.TestCase):
    def test_non_object_window_falls_back_with_a_warning(self):
        for bad in ("126 days", ["2030-01-01", "2030-03-01"]):
            out = sc.build_context({"today": "2030-01-29", "board_id": BOARD, "sprints": _sprints(),
                                    "absences": [], "engineer_count": 2, "absence_window": bad})
            self.assertIn("ignored absence_window: not an object; using the default window", out["warnings"])
            self.assertEqual(out["current_sprint_availability"], 1.0)


class CurrentSprintCapacityTest(unittest.TestCase):
    """Release capacity counts the current sprint for its remaining working days only."""

    CURRENT = {"number": 11, "start": "2030-01-28", "end": "2030-02-18", "availability": 0.8}  # 15 working days
    NEXT = {"number": 12, "start": "2030-02-18", "end": "2030-03-11", "availability": 1.0}

    def _cap(self, today):
        return cr.release_capacity(60, [self.CURRENT, self.NEXT], today=today)

    def test_week_1_monday_counts_the_whole_sprint(self):
        cap = self._cap("2030-01-28")
        self.assertEqual(cap["availability_csv"], "0.8,1")
        self.assertEqual(cap["capacity_sp"], 108)  # 60 × (0.8 × 15/15 + 1)
        self.assertIsNone(cap["current_sprint"])

    def test_week_3_counts_the_remaining_days(self):
        cap = self._cap("2030-02-13")  # Wednesday of week 3: Wed, Thu, Fri left
        self.assertEqual(cap["current_sprint"], {"number": 11, "share": 0.2, "days_left": 3, "days": 15})
        self.assertEqual(cap["availability_csv"], "0.16,1")
        self.assertEqual(cap["capacity_sp"], 70)  # 60 × (0.8 × 3/15 + 1) = 69.6
        self.assertIn("current sprint 3 of 15 working days left", cap["label"])

    def test_last_day_counts_one_working_day(self):
        cap = self._cap("2030-02-15")  # Friday before the Monday end day
        self.assertEqual((cap["current_sprint"]["days_left"], cap["availability_csv"]), (1, "0.0533,1"))
        self.assertEqual(cap["capacity_sp"], 63)  # 60 × (0.8 × 1/15 + 1) = 63.2

    def test_weekend_before_the_end_day_counts_nothing(self):
        cap = self._cap("2030-02-17")  # Sunday: no working day left in the current sprint
        self.assertEqual((cap["current_sprint"]["days_left"], cap["capacity_sp"]), (0, 60))


class BurndownWordingTest(unittest.TestCase):
    def _risk(self, today, done, week=2):
        issues = dict(ISSUES, total_done_sp=done, total_remaining_sp=30 - done)
        out = cr.sprint_health(issues, dict(SPRINT, week=week), today, "mid-sprint")
        return out["risks"][0]["text"] if out["risks"] else None

    def test_the_final_week_has_no_projection_and_no_re_scope(self):
        self.assertEqual(self._risk("2030-02-07", 4, week=3), "Burndown behind: 4 of 30 SP done, 15 expected")
        final = cr.sprint_health(dict(ISSUES, total_done_sp=4, total_remaining_sp=26), dict(SPRINT, week=3),
                                 "2030-02-07", "mid-sprint")
        self.assertEqual(final["team_items"], [])  # too late to re-scope
        self.assertNotIn("projected_finish", final["burndown"])  # nothing for another renderer to show
        week2 = cr.sprint_health(dict(ISSUES, total_done_sp=4, total_remaining_sp=26), dict(SPRINT, week=2),
                                 "2030-02-07", "mid-sprint")
        self.assertEqual(week2["team_items"], ["re-scope the sprint (26 SP left, burndown behind)"])  # unchanged
        self.assertIn("projected_finish", week2["burndown"])

    def test_exact_biggest_risk_string(self):
        self.assertEqual(self._risk("2030-02-07", 4),
                         "Burndown behind: 4 of 30 SP done, 15 expected; "
                         "projected to finish 61 days after the sprint ends")

    def test_small_overrun_is_rounded_up_and_singular(self):
        # 21 of 22 days elapsed, 1 left; 20 done → 20/21 SP/day, 10 SP left need 10.5 days → 10 late.
        self.assertIn("projected to finish 10 days after", self._risk("2030-02-17", 20))
        # 11 elapsed, 11 left; 14 done → 16 SP left at 14/11 SP/day need 12.6 days → 2 days late.
        self.assertIn("projected to finish 2 days after", self._risk("2030-02-07", 14))
        # 11 elapsed; 13 done → 17 SP left need 14.4 days, 3.4 over → 4 days (never "3.4" or "0 days").
        self.assertIn("projected to finish 4 days after", self._risk("2030-02-07", 13))


class FinalWeekTest(unittest.TestCase):
    """The sprint's final week: what is still open, by stage; no carry-over items."""

    def _issue(self, key, status, sp, owner="Alex Example", blocked=()):
        return {"key": key, "type": "Story", "status": status, "sp": sp, "assignee_display": owner,
                "epic_key": "TEST-100", "blocked_by": list(blocked), "labels": []}

    def _final(self, issues, week=3, sprint=None):
        total = sum(i["sp"] or 0 for i in issues)
        done = sum(i["sp"] or 0 for i in issues if i["status"] in ("Closed", "Done", "Verified"))
        data = {"total_sp": total, "total_done_sp": done, "total_remaining_sp": total - done, "issues": issues}
        return cr.sprint_health(data, dict(sprint or SPRINT, week=week), "2030-02-14", "mid-sprint")

    def test_still_open_by_stage_adds_up(self):
        issues = [self._issue("TEST-1", "Review", 2), self._issue("TEST-2", "ON_QA", 1),
                  self._issue("TEST-3", "MODIFIED", 1), self._issue("TEST-4", "In Progress", 3, blocked=["TEST-9"]),
                  self._issue("TEST-5", "To Do", None), self._issue("TEST-6", "ASSIGNED", 2),
                  self._issue("TEST-7", "NEW", 1), self._issue("TEST-8", "Code Review", 2),  # not a law 07 status
                  self._issue("TEST-9", "", 1), self._issue("TEST-10", "Closed", 5),
                  self._issue("TEST-11", "Verified", 1)]
        out = self._final(issues)
        self.assertEqual(out["still_open"], {"open_issues": 9, "open_sp": 13, "review_issues": 3, "review_sp": 4,
                                             "work_issues": 4, "work_sp": 6, "other_issues": 2, "other_sp": 3})
        self.assertEqual(cr._still_open_text(out["still_open"]),
                         "9 still open (13 SP): 3 in review or testing (4 SP), 4 in progress or to do (6 SP), "
                         "2 other status (3 SP)")
        self.assertEqual([(f["key"], f["label"]) for f in out["items"]],
                         [("TEST-4", "blocked by TEST-9")])  # no carry-over
        header = cr._sprint_header(out, [])
        self.assertTrue(header.startswith("*Sprint health: Test Sprint 11* (final week): "))
        self.assertNotIn("delivered", header)
        self.assertNotIn("carry-over", header)

    def test_nothing_still_open_and_no_other_bucket_when_every_status_is_known(self):
        done = self._final([self._issue("TEST-1", "Closed", 3)])
        self.assertEqual(cr._still_open_text(done["still_open"]), "0 still open (0 SP)")
        known = self._final([self._issue("TEST-1", "Review", 1.5), self._issue("TEST-2", "To Do", 2)])
        self.assertEqual(cr._still_open_text(known["still_open"]),
                         "2 still open (3.5 SP): 1 in review or testing (1.5 SP), 1 in progress or to do (2 SP)")

    def test_the_last_week_comes_from_the_sprint_dates(self):
        two = {"name": "Test Sprint 11", "start": "2030-01-28", "end": "2030-02-11"}
        four = {"name": "Test Sprint 11", "start": "2030-01-28", "end": "2030-02-25"}
        self.assertTrue(cr.final_week(dict(two, week=2)))  # a 2-week sprint's week 2 is its final week
        self.assertFalse(cr.final_week(dict(four, week=3)))  # a 4-week sprint's week 3 is not
        self.assertTrue(cr.final_week(dict(four, week=4)))
        self.assertTrue(cr.final_week(dict(SPRINT, week=3)))
        self.assertFalse(cr.final_week(dict(SPRINT, week=2)))
        self.assertFalse(cr.final_week({"name": "Test Sprint 11", "week": 3}))  # no dates: not known
        self.assertNotIn("still_open", self._final([self._issue("TEST-1", "To Do", 1)], week=3, sprint=four))
        self.assertTrue(cr.final_week(dict(four, week=5)))  # a week past the last is still the end

    def test_a_one_week_sprint_stays_in_capacity_mode(self):
        one = {"name": "Test Sprint 11", "number": 11, "start": "2030-01-28", "end": "2030-02-04"}
        self.assertTrue(cr.final_week(dict(one, week=1)))  # by its dates, week 1 is also its last week
        self.assertEqual(cr.sprint_mode(1), "capacity")  # but week 1 takes capacity mode (the documented rule)
        out = cr.sprint_health(ISSUES, dict(one, week=1), "2030-01-29", "capacity", velocity=24, availability=0.9)
        self.assertNotIn("final_week", out)  # no final-week view, label or still-open line
        self.assertNotIn("still_open", out)
        self.assertIn("capacity", out)  # the capacity view: sprint SP vs capacity
        self.assertTrue(cr.build_report(out, [])["top_post"][0].endswith("Test Sprint 11 (week 1, capacity)"))

    def test_a_two_week_sprint_takes_the_final_week_view_in_week_2(self):
        two = {"name": "Test Sprint 11", "start": "2030-01-28", "end": "2030-02-11"}
        issues = dict(ISSUES, total_done_sp=4, total_remaining_sp=26)
        out = cr.sprint_health(issues, dict(two, week=2), "2030-02-07", "mid-sprint")
        self.assertTrue(out["final_week"])  # final-week suppression takes precedence over week 2
        self.assertEqual(out["team_items"], [])
        self.assertEqual(out["risks"][0]["text"], "Burndown behind: 4 of 30 SP done, 22 expected")
        report = cr.build_report(out, [])
        self.assertTrue(report["top_post"][0].endswith("Test Sprint 11 (final week)"))
        self.assertTrue(report["replies"][0].startswith("*Sprint health: Test Sprint 11* (final week): "))
        self.assertNotIn("mid-sprint", report["top_post_text"] + report["replies"][0])

    def test_status_names_match_in_any_case(self):
        out = self._final([self._issue("TEST-1", "review", 1), self._issue("TEST-2", "in progress", 2)])
        self.assertEqual((out["still_open"]["review_issues"], out["still_open"]["work_issues"]), (1, 1))

    def test_an_on_track_final_week_says_so_without_a_projection(self):
        out = self._final([self._issue("TEST-1", "Closed", 8), self._issue("TEST-2", "Review", 1)])
        report = cr.build_report(out, [])
        self.assertEqual(report["top_post"][0], "🟢 Edge weekly health: Test Sprint 11 (final week)")
        self.assertEqual(report["top_post"][1], "No risks this week: 8 of 9 SP done, 7.4 expected (On Track)")
        self.assertNotIn("projected", report["top_post_text"] + "".join(report["replies"]))


class CodeRabbitFixesTest(unittest.TestCase):
    def test_usable_velocity(self):
        ok = {"team_velocity": 80.3, "sprint_count": 3, "skipped_no_sprint_data": 0}
        self.assertEqual(cr.usable_velocity(ok), (80.3, None))
        self.assertEqual(cr.usable_velocity(dict(ok, skipped_no_sprint_data=2)),
                         (None, "2 done team issue(s) had no sprint or resolution data"))
        self.assertEqual(cr.usable_velocity(dict(ok, sprint_count=2))[0], None)
        self.assertEqual(cr.usable_velocity(dict(ok, team_velocity=0)), (None, "velocity not measured"))
        self.assertEqual(cr.usable_velocity(None), (None, "velocity not measured"))

    def test_velocity_reason_is_carried_into_left_out(self):
        _, reason = cr.usable_velocity({"team_velocity": 70.0, "sprint_count": 2})
        sprint = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=None,
                                  velocity_reason=reason)
        self.assertIn({"what": "sprint capacity", "why": "only 2 closed sprint(s) measured, 3 needed"},
                      sprint["left_out"])
        checks = dict(CHECKS, meta=dict(CHECKS["meta"], capacity_basis="roster_fallback"))
        release = cr.release_health(checks, {"release": "9.1", "phase": "development"}, velocity_reason=reason)
        self.assertEqual(release["left_out"][0]["why"], "only 2 closed sprint(s) measured, 3 needed")
        # Without a reason, the previous text is kept.
        plain = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=None)
        self.assertIn({"what": "sprint capacity", "why": "velocity not measured"}, plain["left_out"])
        self.assertEqual(cr.release_health(checks, {"release": "9.1", "phase": "development"})["left_out"][0]["why"],
                         "velocity not available")

    def test_capacity_line_uses_the_velocity_reason(self):
        checks = dict(CHECKS, meta=dict(CHECKS["meta"], capacity_basis="roster_fallback"))
        release = cr.release_health(checks, {"release": "9.1", "phase": "development"},
                                    velocity_reason="only 2 closed sprint(s) measured, 3 needed")
        header = cr._section_header(release)
        self.assertIn("capacity not available (only 2 closed sprint(s) measured, 3 needed)", header)
        self.assertNotIn("velocity not measured", header)

    def test_mention_matches_case_and_space_insensitively(self):
        mentions = {"Alex Example": "<@U000TEST1>"}
        self.assertEqual(cr.mention("alex   EXAMPLE", mentions), "<@U000TEST1>")
        self.assertEqual(cr.mention("Quinn Quality", mentions), "Quinn Quality")
        self.assertEqual(cr.mention(None, mentions), "no owner set")

    def test_select_sections_accepts_full_scrum_context_results(self):
        sections, missing = cr.select_sections({}, [{"today": "2030-02-01", "release": _window("9.1", "development")}])
        self.assertEqual([s["release"] for s in sections], ["9.1"])
        self.assertEqual(missing, [])

    def test_only_explicit_blocks_count(self):
        base = {"type": "Bug", "sp": 1, "assignee_display": "Alex Example", "epic_key": "TEST-100",
                "blocked_by": [], "labels": [], "flagged": True, "stale": True, "updated": "2030-01-02"}
        issues = {"total_sp": 4, "total_done_sp": 0, "total_remaining_sp": 4, "issues": [
            dict(base, key="BUG-1", status="ASSIGNED"),  # stale and Flagged: not a block
            dict(base, key="BUG-2", status="In Progress", labels=["Blocked"]),
            dict(base, key="BUG-3", status="New", labels=["Parked"]),
            dict(base, key="BUG-4", status="Code Review", blocked_by=["BUG-9"])]}
        out = cr.sprint_health(issues, dict(SPRINT, week=2), "2030-02-07", "mid-sprint")
        self.assertEqual(out["blockers"], [{"key": "BUG-2", "reasons": ["Blocked label"]},
                                           {"key": "BUG-3", "reasons": ["Parked"]},
                                           {"key": "BUG-4", "reasons": ["blocked by BUG-9"]}])
        text = "\n".join(cr.build_report(out, [])["replies"])
        self.assertNotIn("stale", text)
        self.assertNotIn("no update", text)

    def test_goal_rating_is_carried_into_the_reply(self):
        sprint = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint",
                                  goal={"rating": "🟡", "note": "goal work stalled on TEST-2"})
        reply = cr.build_report(sprint, [])["replies"][0]
        self.assertIn("goal: 🟡 goal work stalled on TEST-2", reply.splitlines()[0])
        none = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint",
                                goal={"rating": None, "note": "no sprint goal set"})
        self.assertIn("goal: no sprint goal set", cr.build_report(none, [])["replies"][0].splitlines()[0])

    def test_goal_is_not_green_without_blocked_signals(self):
        green = {"rating": "🟢", "note": "goal work on track"}
        ok = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint", goal=green)
        self.assertEqual(ok["goal"], green)
        no_links = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint", goal=green,
                                    links_complete=False)
        self.assertEqual(no_links["goal"], {"rating": None,
                                            "note": "goal work on track (not rated: blocked-by links not available)"})
        no_flag = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint", goal=green,
                                   skipped_signals=[ad.SKIPPED_FLAGGED])
        self.assertEqual(no_flag["goal"], green)  # Flagged is no longer a block signal
        yellow = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint",
                                  goal={"rating": "🟡", "note": "stalled"}, links_complete=False)
        self.assertEqual(yellow["goal"]["rating"], "🟡")  # only green is withheld

    def test_build_report_rejects_section_descriptors(self):
        sections, _ = cr.select_sections({}, [_window("9.1", "development")])
        with self.assertRaises(ValueError):
            cr.build_report(None, sections)


class LinksLeftOutTest(unittest.TestCase):
    """Blocked-by links that could not be read: left out with the failed fetch and its error."""

    def test_sprint_links_left_out_with_the_error(self):
        sprint = dict(SPRINT, week=2)
        named = cr.sprint_health(ISSUES, sprint, "2030-02-07", "mid-sprint", links_complete=False,
                                 links_problems=["blocked items fetch incomplete: fetch: page 1: x",
                                                 "TEST-2: get_jira_issue failed"])
        self.assertIn({"what": "blocked-by links",
                       "why": "blocked items fetch incomplete: fetch: page 1: x; TEST-2: get_jira_issue failed"},
                      named["left_out"])
        plain = cr.sprint_health(ISSUES, sprint, "2030-02-07", "mid-sprint", links_complete=False)
        self.assertIn({"what": "blocked-by links", "why": "issue link details incomplete (get_jira_issue)"},
                      plain["left_out"])
        # A problem alone makes the links incomplete: the blocked signal is never guessed.
        problem_only = cr.sprint_health(ISSUES, sprint, "2030-02-07", "mid-sprint",
                                        links_problems=["TEST-2: get_jira_issue failed"])
        self.assertEqual((problem_only["links_complete"], problem_only["items"]),
                         (plain["links_complete"], plain["items"]))
        one = cr.sprint_health(ISSUES, sprint, "2030-02-07", "mid-sprint",
                               links_problems="TEST-2: get_jira_issue failed")  # one string, one problem
        self.assertIn({"what": "blocked-by links", "why": "TEST-2: get_jira_issue failed"}, one["left_out"])
        blank = cr.sprint_health(ISSUES, sprint, "2030-02-07", "mid-sprint", links_problems=["", " "])
        self.assertEqual(blank["links_complete"], True)  # no problem named: nothing changes

    def test_release_links_left_out_with_the_error(self):
        section = {"release": "9.1", "phase": "development"}
        kw = {"features": {"features": []}, "epics": {"epics": []}, "spikes": {}, "stories": {"stories": []}}
        not_run = cr.release_health(CHECKS, section, links=None,
                                    links_problems=["block candidates fetch incomplete: fetch: page 1: x"], **kw)
        self.assertIn({"what": "9.1 feature and epic blocks",
                       "why": "block candidates fetch incomplete: fetch: page 1: x"}, not_run["left_out"])
        unread = cr.release_health(CHECKS, section, links={}, links_unread=["FEAT-3"],
                                   links_problems=["FEAT-3: get_jira_issue failed"], **kw)
        self.assertIn({"what": "9.1 blocks of FEAT-3", "why": "FEAT-3: get_jira_issue failed"}, unread["left_out"])
        default = cr.release_health(CHECKS, section, links=None, **kw)
        self.assertIn({"what": "9.1 feature and epic blocks", "why": "issue links not read"}, default["left_out"])
        keyless = cr.release_health(CHECKS, section, links={}, links_problems=["a problem with no key"], **kw)
        self.assertEqual(keyless["blocks_unchecked"], "all")  # never counted as checked
        self.assertIn({"what": "9.1 feature and epic blocks", "why": "a problem with no key"}, keyless["left_out"])


def _fetch(blocks_text, complete=True):
    """An adapt_fetch result of one final page of these issue blocks."""
    text = f"Found {len(blocks_text)} issues (showing {len(blocks_text)}):\n" + "\n".join(blocks_text) + "\n"
    out = ad.adapt_fetch({"name": "shared", "pages": [{"text": text}]})
    if not complete:
        out["fetch"].update(complete=False, problems=["fetch: page 1: query_jira call failed: x"])
    return out


def _issue(key, type_="Feature", status="In Progress", extra=(), resolution=None):
    lines = [f"  [{key}] Synthetic {type_.lower()}",
             f"    Type: {type_} | Status: {status} | Priority: Major | Assignee: Alex Example | Reporter: Sam Sample"
             + (f" | Resolution: {resolution}" if resolution else ""),
             "    Created: 2030-01-02 | Updated: 2030-02-01"]
    return "\n".join(lines + [f"    {e}" for e in extra])


class SharedFetchTest(unittest.TestCase):
    """The shared fetch plan's partitions reproduce each old query exactly."""

    GATE = _fetch([
        _issue("FEAT-1", extra=["Labels: ocpedge-plan", "customfield_10855: openshift-9.2"]),
        _issue("FEAT-2", extra=["Labels: ocpedge-plan", "customfield_10855: openshift-9.2, openshift-9.3"]),
        _issue("FEAT-3", extra=["Labels: microshift", "customfield_10855: openshift-9.3"]),
        _issue("FEAT-4", extra=["Labels: ocpedge-plan", "customfield_10855: openshift-9.2"], resolution="Duplicate"),
    ])

    def test_gate_features_are_the_refinement_features_query(self):
        self.assertEqual([i["key"] for i in cr.gate_features(self.GATE, "9.2")["issues"]], ["FEAT-1", "FEAT-2"])
        self.assertEqual([i["key"] for i in cr.gate_features(self.GATE, "9.3")["issues"]], ["FEAT-2", "FEAT-3"])
        self.assertTrue(cr.gate_features(self.GATE, "9.2")["fetch"]["complete"])

    EPICS = _fetch([
        _issue("TEST-10", "Epic", "Closed", ["customfield_10018: FEAT-1"]),  # a Closed epic of a release feature
        _issue("TEST-11", "Epic", extra=["Parent: FEAT-1", "customfield_10018: FEAT-1"]),
        _issue("TEST-12", "Epic", "To Do", ["Parent: FEAT-2"]),  # Jira Cloud: the feature is the native parent
        _issue("USH-14", "Epic", extra=["Parent: FEAT-99", "customfield_10018: FEAT-2"]),  # matched its Parent Link
        _issue("TEST-15", "Story", extra=["customfield_10018: FEAT-2"]),  # Parent Link query has no type filter
        _issue("TEST-16", "Epic", extra=["Parent: FEAT-5"]),  # a feature of both releases
    ])
    RELEASES = (("9.2", ("FEAT-1", "FEAT-5")), ("9.3", ("FEAT-2", "FEAT-5")))

    def test_epics_partition_by_parent_link(self):
        parts = cr.partition_epics(self.EPICS, dict(self.RELEASES))
        self.assertEqual([i["key"] for i in parts["9.2"]["issues"]], ["TEST-10", "TEST-11", "TEST-16"])
        self.assertEqual([i["key"] for i in parts["9.3"]["issues"]], ["TEST-12", "USH-14", "TEST-15", "TEST-16"])
        self.assertTrue(all(part["fetch"]["complete"] for part in parts.values()))
        failed = cr.partition_epics(_fetch([], complete=False), dict(self.RELEASES))
        self.assertFalse(any(part["fetch"]["complete"] for part in failed.values()))  # fails every part

    def test_an_epic_whose_copies_disagree_goes_to_no_release(self):
        def query(parent):  # one "Parent Link" in (...) query of the shared epics fetch
            return {"pages": [{"text": page(["TEST-11"], type_="Epic", sprints=None, sp=None,
                                            extra=[f"customfield_10018: {parent}"])}]}
        releases = {"9.2": ["FEAT-1"], "9.3": ["FEAT-2"]}
        moved = ad.adapt_fetches({"epics": {"parts": [query("FEAT-1"), query("FEAT-2")]}})["epics"]
        why = "copies of TEST-11 in this fetch differ in customfield_10018 (it changed between reads)"
        for parts in (cr.partition_epics(moved, releases), cr.partition_epics(moved, {"9.2": ["FEAT-1"]})):
            self.assertEqual({r: (p["fetch"]["complete"], p["fetch"]["problems"]) for r, p in parts.items()},
                             {r: (False, [why]) for r in parts})  # neither release takes either copy silently
        same = ad.adapt_fetches({"epics": {"parts": [query("FEAT-1"), query("FEAT-1")]}})["epics"]
        self.assertEqual({r: (p["fetch"]["complete"], [i["key"] for i in p["issues"]])
                          for r, p in cr.partition_epics(same, releases).items()},
                         {"9.2": (True, ["TEST-11"]), "9.3": (True, [])})  # identical copies: kept once

    def test_one_release_takes_every_returned_issue(self):
        odd = _fetch([_issue("TEST-17", "Epic"), _issue("TEST-18", "Epic", extra=["Parent: FEAT-7"])])
        part = cr.partition_epics(odd, {"9.2": ["FEAT-1"]})["9.2"]
        self.assertEqual([i["key"] for i in part["issues"]], ["TEST-17", "TEST-18"])
        self.assertTrue(part["fetch"]["complete"])
        cut = _fetch([_issue("TEST-19", "Epic", extra=["Parent: FEAT-1", "customfield_10018: (value stored)"])])
        self.assertTrue(cr.partition_epics(cut, {"9.2": ["FEAT-1"]})["9.2"]["fetch"]["complete"])  # parent read
        cut = _fetch([_issue("TEST-19", "Epic", extra=["customfield_10018: (value stored)"])])
        self.assertFalse(cr.partition_epics(cut, {"9.2": ["FEAT-1"]})["9.2"]["fetch"]["complete"])  # feature unknown

    def test_stories_are_grouped_per_release(self):
        stories = _fetch([_issue("TESTA-1", "Story", extra=["Parent: TEST-11"]),
                          _issue("TESTB-2", "Story", extra=["Parent: TEST-20"])])
        groups = cr.group_stories(stories, {"9.2": ["TEST-11"], "9.3": ["TEST-11", "TEST-20"]})
        self.assertEqual([i["key"] for i in groups["9.2"]["issues"]], ["TESTA-1"])
        self.assertEqual([i["key"] for i in groups["9.3"]["issues"]], ["TESTA-1", "TESTB-2"])  # in both releases

    def test_parents_are_reused_only_with_full_coverage(self):
        batch, missing = cr.reuse_parents(["FEAT-1", "FEAT-7", "FEAT-2"], [self.GATE])
        self.assertEqual((batch["keys"], missing), (["FEAT-1", "FEAT-2"], ["FEAT-7"]))
        gaps_fetch = _fetch([_issue("TEST-30", "Epic", extra=["Parent: FEAT-1"])])
        gaps = cr.epic_field_gaps(gaps_fetch, [batch])
        self.assertEqual(gaps["checks"]["no_target_version"][0]["key"], "TEST-30")  # parent read from the gate
        cut = dict(self.GATE, fetch=dict(self.GATE["fetch"], complete=False,
                                         problems=["FEAT-1: customfield_10475 value shortened (not read whole)"]))
        self.assertEqual(cr.reuse_parents(["FEAT-1", "FEAT-2"], [cut])[1], ["FEAT-1"])  # its own problem: read again
        paged = dict(self.GATE, fetch=dict(self.GATE["fetch"], complete=False, problems=["fetch: page 2: cut off"]))
        self.assertEqual(cr.reuse_parents(["FEAT-1"], [paged]), (None, ["FEAT-1"]))  # a page problem: nothing reused

    def test_the_newest_whole_parent_copy_wins(self):
        older = _fetch([_issue("FEAT-1", extra=["customfield_10855: openshift-9.2",
                                                "customfield_10470: Quinn Quality"])])
        newer = _fetch([_issue("FEAT-1", extra=["customfield_10855: openshift-9.2"])])  # QA Contact removed since
        batch, _ = cr.reuse_parents(["FEAT-1"], [newer, older])  # the recipe: the latest read first
        self.assertNotIn("customfield_10470", batch["result"]["issues"][0])
        batch, _ = cr.reuse_parents(["FEAT-1"], [older, newer])  # the first whole copy wins
        self.assertIn("customfield_10470", batch["result"]["issues"][0])

    def test_reused_parents_keep_their_own_source_status(self):
        cut = _fetch([_issue("FEAT-1", extra=["customfield_10855: openshift-9.2"]),
                      _issue("FEAT-2", extra=["customfield_10855: openshift-9.2"])])
        cut["fetch"].update(complete=False, problems=["FEAT-2: customfield_10470 value shortened (not read whole)"])
        whole = _fetch([_issue("FEAT-2", extra=["customfield_10855: openshift-9.2"])])
        batch, missing = cr.reuse_parents(["FEAT-1", "FEAT-2"], [cut, whole])
        self.assertEqual((batch["keys"], missing), (["FEAT-1", "FEAT-2"], []))  # FEAT-2 taken whole from the second
        self.assertTrue(batch["result"]["fetch"]["complete"])  # not the first source's FEAT-2 problem
        self.assertEqual(batch["result"]["fetch"]["problems"], [])
        self.assertEqual(cr.reuse_parents(["FEAT-2"], [cut])[1], ["FEAT-2"])  # cut everywhere: read again

    def test_a_release_with_no_features_has_no_epics(self):
        one = _fetch([_issue("TEST-10", "Epic", "Closed", ["customfield_10018: FEAT-1"]),
                      _issue("TEST-17", "Epic")])  # one release with keys: all its query returned
        parts = cr.partition_epics(one, {"9.2": ["FEAT-1"], "9.3": []})
        self.assertEqual(([i["key"] for i in parts["9.3"]["issues"]], parts["9.3"]["fetch"]["complete"]), ([], True))
        self.assertEqual([i["key"] for i in parts["9.2"]["issues"]], ["TEST-10", "TEST-17"])
        self.assertTrue(parts["9.2"]["fetch"]["complete"])
        none_sent = cr.partition_epics(None, {"9.3": []})  # no feature at all: no epics query
        self.assertEqual((none_sent["9.3"]["issues"], none_sent["9.3"]["fetch"]["complete"]), ([], True))
        failed = cr.partition_epics(_fetch([], complete=False), {"9.2": ["FEAT-1"], "9.3": []})
        self.assertFalse(failed["9.2"]["fetch"]["complete"])  # attempted and failed: still incomplete
        self.assertTrue(failed["9.3"]["fetch"]["complete"])  # nothing of 9.3 was in it

    def test_a_release_with_no_epics_needs_no_stories(self):
        failed = _fetch([], complete=False)
        groups = cr.group_stories(failed, {"9.2": [], "9.3": ["TEST-40"]})
        self.assertEqual((groups["9.2"]["issues"], groups["9.2"]["fetch"]["complete"]), ([], True))
        self.assertFalse(groups["9.3"]["fetch"]["complete"])  # its epics were queried: the failure is its own
        self.assertEqual(cr.group_stories(None, {"9.2": []})["9.2"]["fetch"]["complete"], True)  # nothing sent

    def test_empty_epic_check_names_the_failed_fetch(self):
        failed = _fetch([], complete=False)
        counts, why = cr.epic_issue_counts(failed, ["TEST-11"])
        self.assertEqual((counts, why), (None, "epic issues not fetched (fetch: page 1: query_jira call failed: x)"))
        out = cr.refinement_gaps([], [], {"release": "9.3", "phase": "refinement"}, epics={"epic_keys": []},
                                 epic_issue_counts=counts, epic_issue_counts_why=why)
        self.assertIn({"what": "9.3 empty-epic check", "why": why}, out["left_out"])
        stray = _fetch([_issue("TESTA-4", "Story")])
        self.assertEqual(cr.epic_issue_counts(stray, ["TEST-11"]),
                         (None, "epic issues not attributable (TESTA-4: returned with no readable parent)"))
        whole = _fetch([_issue("TESTA-1", "Story", extra=["Parent: TEST-11"])])
        self.assertEqual(cr.epic_issue_counts(whole, ["TEST-11"]), ({"TEST-11": 1}, None))
        self.assertEqual(cr.epic_issue_counts(failed, []), ({}, None))  # no epic: no story needed
        other = _fetch([_issue("TESTA-5", "Story", extra=["Parent: TEST-99"])])
        self.assertEqual(cr.epic_issue_counts(other, ["TEST-11"])[1],
                         "epic issues not attributable (TESTA-5: returned with a parent of no queried epic (TEST-99))")

    def test_unknown_membership_fails_every_part_it_could_be_in(self):
        for title, extra in (("Parent Link shortened", ["Parent: FEAT-1", "customfield_10018: (value stored)"]),
                             ("parent and Parent Link name features of different releases",
                              ["Parent: FEAT-1", "customfield_10018: FEAT-2"]),
                             ("no selected feature named", ["Parent: FEAT-99"]),
                             ("neither printed", [])):
            with self.subTest(title):
                parts = cr.partition_epics(_fetch([_issue("TEST-20", "Epic", extra=extra)]), dict(self.RELEASES))
                for part in parts.values():
                    self.assertFalse(part["fetch"]["complete"])
                    self.assertIn("TEST-20: returned by the epics fetch, release unknown",
                                  part["fetch"]["problems"][-1])
        stories = _fetch([_issue("TESTA-9", "Story", extra=["Parent: TEST-99"])])  # a parent of no selected epic
        self.assertFalse(cr.group_stories(stories, {"9.2": ["TEST-11"]})["9.2"]["fetch"]["complete"])

    def test_epic_issue_counts_need_whole_pages_only(self):
        stories = _fetch([_issue("TESTA-1", "Story", extra=["Parent: TEST-11"]),
                          _issue("TESTA-2", "Story", extra=["Parent: TEST-11"]),
                          _issue("TESTA-3", "Story", extra=["Parent: TEST-12"])])
        self.assertEqual(cr.epic_issue_counts(stories, ["TEST-11", "TEST-12"]), ({"TEST-11": 2, "TEST-12": 1}, None))
        cut = dict(stories, fetch=dict(stories["fetch"], complete=False,
                                       problems=["TESTA-1: customfield_10028 value shortened (not read whole)"]))
        both = ["TEST-11", "TEST-12"]  # the epic keys the query used
        self.assertEqual(cr.epic_issue_counts(cut, ["TEST-11"], both)[0], {"TEST-11": 2})  # the points do not matter
        paged = dict(stories, fetch=dict(stories["fetch"], complete=False, problems=["fetch: page 2: cut off"]))
        self.assertIsNone(cr.epic_issue_counts(paged, ["TEST-11"])[0])

    def test_epic_issue_counts_unknown_when_a_story_cannot_be_attributed(self):
        queried = ["TEST-11", "TEST-12", "TEST-20"]  # every section's epics, as the query sent them
        for title, row in (("no readable parent", _issue("TESTA-4", "Story")),
                           ("a parent of no queried epic", _issue("TESTA-5", "Story", extra=["Parent: TEST-99"]))):
            with self.subTest(title):
                stories = _fetch([_issue("TESTA-1", "Story", extra=["Parent: TEST-11"]), row])
                self.assertIsNone(cr.epic_issue_counts(stories, ["TEST-11", "TEST-12"], queried)[0])  # never "empty"
        other = _fetch([_issue("TESTA-1", "Story", extra=["Parent: TEST-11"]),
                        _issue("TESTA-6", "Story", extra=["Parent: TEST-20"])])  # another section's epic
        self.assertEqual(cr.epic_issue_counts(other, ["TEST-11", "TEST-12"], queried), ({"TEST-11": 1}, None))

    def test_subsets_keep_unexplained_incompleteness_and_their_fields(self):
        odd = dict(self.GATE, fetch=dict(self.GATE["fetch"], complete=False, linked_spike_problems=["FEAT-1: x"],
                                         problems=["FEAT-3: customfield_10475 value shortened (not read whole)"]))
        self.assertFalse(cr.gate_features(odd, "9.2")["fetch"]["complete"])  # not only its problems: stays incomplete
        batch, missing = cr.reuse_parents(["FEAT-1", "FEAT-1", "FEAT-7"], [self.GATE])
        self.assertEqual((batch["keys"], len(batch["result"]["issues"]), missing), (["FEAT-1"], 1, ["FEAT-7"]))

    def test_a_problem_of_one_issue_stays_with_its_part(self):
        cut = dict(self.GATE, fetch=dict(self.GATE["fetch"], complete=False,
                                         problems=["FEAT-3: customfield_10475 value shortened (not read whole)"]))
        self.assertTrue(cr.gate_features(cut, "9.2")["fetch"]["complete"])  # FEAT-3 targets 9.3 only
        self.assertFalse(cr.gate_features(cut, "9.3")["fetch"]["complete"])
        gate = cr.release_feature_gate(cut)  # the gate does not read the SME
        self.assertEqual((gate["complete"], gate["releases"]), (True, ["9.2", "9.3"]))
        tv_cut = dict(cut, fetch=dict(cut["fetch"],
                                      problems=["FEAT-3: customfield_10855 value shortened (not read whole)"]))
        self.assertFalse(cr.release_feature_gate(tv_cut)["complete"])  # it reads the Target Version
        self.assertEqual(cr._scoped_problems(["fetch: page 1: x", "FEAT-1: a", "FEAT-2: b"], keys={"FEAT-2"}),
                         ["fetch: page 1: x", "FEAT-2: b"])


class UnknownSprintModeTest(unittest.TestCase):
    def test_unknown_week_leaves_sprint_health_out(self):
        for week in (None, "three"):
            mode = cr.sprint_mode(week)
            self.assertIsNone(mode)
            out = cr.sprint_health(ISSUES, dict(SPRINT, week=week), "2030-02-07", mode)
            self.assertNotIn("burndown", out)
            self.assertNotIn("capacity", out)
            self.assertEqual((out["risks"], out["items"]), ([], []))
            report = cr.build_report(out, [])
            text = "\n".join(report["top_post"] + report["replies"])
            self.assertIn("Left out: Sprint health: sprint week unknown", text)
            self.assertNotIn("None mode", text)
            self.assertNotIn("Sprint health: Test Sprint", text)
            self.assertTrue(report["top_post"][0].endswith("sprint data not available"))
            # The weekly report posts none of it.
            self.assertEqual(cr.sprint_health_failure(out),
                             "Weekly health report not posted: Sprint health incomplete: sprint week unknown")


class SprintHealthFailureTest(unittest.TestCase):
    """The weekly report's guard: no post without a complete sprint health."""

    def test_complete_sprint_health_is_posted(self):
        for mode, week in (("capacity", 1), ("mid-sprint", 2)):
            sprint = cr.sprint_health(ISSUES, dict(SPRINT, week=week), "2030-02-07", mode, velocity=24)
            self.assertIsNone(cr.sprint_health_failure(sprint, [{"what": "9.2 release section", "why": "x"}]))

    def test_failed_sprint_fetch_names_the_fetch_and_error(self):
        missing = [{"what": "9.2 release section", "why": "features fetch incomplete: fetch: page 1: x"},
                   {"what": "Sprint health",
                    "why": "sprint issue fetch incomplete: fetch: page 1: query_jira call failed: 403 Forbidden"}]
        self.assertEqual(cr.sprint_health_failure(None, missing),
                         "Weekly health report not posted: Sprint health incomplete: sprint issue fetch incomplete: "
                         "fetch: page 1: query_jira call failed: 403 Forbidden")

    def test_sprint_health_not_run_is_not_posted(self):
        for sprint in (None, {}, {"sprint": "Test Sprint 11"}, "not a result"):
            for missing in ([], None):
                with self.subTest(sprint=sprint, missing=missing):
                    self.assertEqual(cr.sprint_health_failure(sprint, missing),
                                     "Weekly health report not posted: Sprint health incomplete: "
                                     "no sprint health result")

    def test_skipped_result_and_missing_reasons_are_named_once(self):
        skipped = cr.sprint_health(ISSUES, dict(SPRINT, week=None), "2030-02-07", None)
        missing = [{"what": "Sprint health", "why": "sprint week unknown"},
                   {"what": "Sprint health", "why": "current sprint is not on the board yet"}]
        self.assertEqual(cr.sprint_health_failure(skipped, missing),
                         "Weekly health report not posted: Sprint health incomplete: sprint week unknown; "
                         "current sprint is not on the board yet")


    def test_every_reason_is_one_line_before_it_is_joined(self):
        missing = [{"what": "Sprint health",
                    "why": "board sprint fetch failed: list_jira_board_sprints failed:\n  timeout"},
                   {"what": "Sprint health",
                    "why": "board sprint fetch failed:  list_jira_board_sprints failed: timeout"},
                   {"what": "Sprint health", "why": "sprint issue fetch incomplete:\tx"},
                   {"what": "Sprint health", "why": "  \n "}]  # blank: not a reason
        self.assertEqual(cr.sprint_health_failure(None, missing),
                         "Weekly health report not posted: Sprint health incomplete: board sprint fetch failed: "
                         "list_jira_board_sprints failed: timeout; sprint issue fetch incomplete: x")
        self.assertEqual(cr.sprint_health_failure(None, [{"what": "Sprint health", "why": " "}]),
                         "Weekly health report not posted: Sprint health incomplete: no sprint health result")


class TeamOwnerTest(unittest.TestCase):
    ROLE_DOC = "# Scrum Master Responsibilities\n\n## Assignment\n\nAssigned by management.\n"

    def test_team_actions_go_to_the_scrum_master(self):
        sprint = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=24, availability=0.9)
        with_owner = cr.build_report(sprint, [], team_owner="Sam Scrum")
        reply = with_owner["replies"][0]
        # The team item goes to the Scrum Master; issues keep their assignees.
        self.assertIn("• Sam Scrum: re-plan the sprint (8.4 SP over capacity)", reply)
        self.assertIn("• Quinn Quality: TEST-3 (unpointed)", reply)  # issue owners unchanged
        self.assertIn("• Alex Example: TEST-2 (no epic)", reply)
        self.assertIn("Sam Scrum", with_owner["people"])
        without = cr.build_report(sprint, [])["replies"][0]
        self.assertIn("• no owner set: re-plan the sprint (8.4 SP over capacity)", without)

    def test_find_scrum_master(self):
        self.assertIsNone(ad.find_scrum_master(ROSTER_MD, self.ROLE_DOC))  # edge-context names no one today
        roster = ROSTER_MD.replace("| Senior Software Engineer |", "| Senior Software Engineer, Scrum Master |")
        self.assertEqual(ad.find_scrum_master(roster, self.ROLE_DOC), "Alex Example")
        doc = self.ROLE_DOC + "\n**Current Scrum Master**: [Quinn Quality](https://example.invalid/q)\n"
        self.assertEqual(ad.find_scrum_master(ROSTER_MD, doc), "Quinn Quality")


if __name__ == "__main__":
    unittest.main()
