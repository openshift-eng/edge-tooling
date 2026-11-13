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
from tests.test_chai_jira_adapter import ROSTER_MD  # noqa: E402

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
        self.assertEqual(sprint["missing_ac"], [])  # bugs are exempt from AC
        self.assertEqual(sprint["by_type"]["Story"], {"issues": 2, "sp": 26})
        self.assertIn("8.4 SP over", sprint["risks"][0]["text"])

    def test_report_has_no_per_person_data(self):
        _, release, report = self._report()
        text = "\n".join(report["top_post"] + report["replies"])
        self.assertNotIn("ABOVE_GUIDELINE", text)
        self.assertNotIn("Robin Outsider", text)  # roster drift is a count
        self.assertNotIn("70", text)  # a person's assigned SP
        self.assertNotIn("Load by Person", text)
        for name in ("Alex Example", "Quinn Quality", "Morgan Manager"):
            for line in text.splitlines():
                if name in line:
                    self.assertRegex(line, r"^\d+\. .*\(owner: " + name + r"\)$")
        self.assertNotIn("capacity", release)  # no per-person rows carried through
        self.assertEqual(release["roster_drift"], {"people": 1, "sp": 12})

    def test_top_post_is_three_lines_with_action_count(self):
        _, release, report = self._report()
        top = report["top_post"]
        self.assertEqual(len(top), 3)
        self.assertTrue(top[0].startswith("🔴 Edge weekly health: Test Sprint 11 (week 1, capacity)"))
        self.assertIn("9.1 pencils-down sprint", top[0])
        self.assertTrue(top[1].startswith("Biggest risk: Sprint committed 30 SP vs capacity 21.6 SP"))
        self.assertEqual(top[2], f"{report['action_count']} items need action")
        self.assertEqual(release["below_cut_line"], ["FEAT-2", "FEAT-3"])
        self.assertEqual(release["features_at_risk"], ["FEAT-1", "FEAT-2"])

    def test_owner_mentions_and_fallbacks(self):
        _, _, report = self._report(mentions={"Alex Example": "<@U000TEST1>"})
        actions = next(r for r in report["replies"] if r.startswith("*Action items*"))
        self.assertIn("Point: TEST-3 (owner: Quinn Quality)", actions)
        self.assertIn("Link to an epic: TEST-2 (owner: <@U000TEST1>)", actions)
        self.assertIn("decide scope for FEAT-3 (below the cut line) (owner: no owner set)", actions)
        self.assertIn("review plan for FEAT-1 (at risk) (owner: Morgan Manager)", actions)

    def test_left_out_lists_missing_sections_and_signals(self):
        _, _, report = self._report()
        left = next(r for r in report["replies"] if r.startswith("*Left out*"))
        self.assertIn("9.2 release section: no Product Pages schedule yet", left)
        self.assertIn("flagged signal: query_jira rejected customfield_10021", left)
        self.assertIn("capacity from 1 assignee(s) not on the roster", left)

    def test_rank_unavailable_leaves_cut_line_out(self):
        release = cr.release_health(CHECKS, {"release": "9.1", "phase": "development"}, rank_available=False)
        self.assertEqual(release["below_cut_line"], [])
        self.assertIn("cut line", release["left_out"][0]["what"])

    def test_roster_fallback_capacity_is_not_reported(self):
        checks = dict(CHECKS, meta=dict(CHECKS["meta"], capacity_basis="roster_fallback"))
        release = cr.release_health(checks, {"release": "9.1", "phase": "development"})
        self.assertIsNone(release["capacity_sp"])
        self.assertEqual((release["below_cut_line"], release["features_at_risk"], release["risks"]), ([], [], []))
        self.assertEqual(release["left_out"][0]["why"], "velocity not available")
        lines = "\n".join(cr._section_lines(dict(release, kind="release-health")))
        self.assertIn("capacity not available", lines)

    def test_nothing_to_flag(self):
        clean = {"total_sp": 20, "total_issues": 1, "issues": [
            {"key": "TEST-1", "type": "Story", "sp": 20, "assignee_display": "Alex Example",
             "epic_key": "TEST-100", "has_ac": True}]}
        sprint = cr.sprint_health(clean, SPRINT, "2030-01-29", "capacity", velocity=24, availability=1.0)
        report = cr.build_report(sprint, [])
        self.assertEqual(report["top_post"][1],
                         "No risks this week: committed 20 SP in 1 issues; capacity 24 SP "
                         "(velocity 24 × availability 1.00)")
        self.assertEqual(report["top_post"][2], "0 items need action")
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
        self.assertEqual([x["key"] for x in out["blockers"]], ["TEST-1", "TEST-2"])
        self.assertNotIn("owner", out["blockers"][0])

    def test_done_issues_are_not_listed_as_blocked(self):
        issues = dict(ISSUES, issues=[dict(i, status="Closed") if i["key"] == "TEST-1" else i
                                      for i in ISSUES["issues"]])
        out = cr.sprint_health(issues, dict(SPRINT, week=2), "2030-02-07", "mid-sprint")
        self.assertEqual([x["key"] for x in out["blockers"]], ["TEST-2"])
        self.assertNotIn("Unblock TEST-1", " ".join(a["text"] for a in out["actions"]))

    def test_incomplete_links_leave_blocked_signal_out(self):
        out = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint",
                               links_complete=False)
        self.assertEqual([x["key"] for x in out["blockers"]], ["TEST-2"])  # stale only
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
        self.assertEqual(out["missing_refinement_spike"], ["FEAT-1", "FEAT-2"])
        self.assertEqual(out["missing_test_plan_spike"], ["FEAT-1"])
        self.assertEqual(out["spike_not_in_refinement_sprint"], ["FEAT-3"])
        owners = {a["text"]: a["owner"] for a in out["actions"]}
        self.assertEqual(owners["9.2: create a refinement spike that blocks FEAT-1 in sprint 16"], "Alex Example")
        self.assertIsNone(owners["9.2: create a refinement spike that blocks FEAT-2 in sprint 16"])
        self.assertEqual(owners["9.2: create a test plan spike that blocks FEAT-1"], "Quinn Quality")
        self.assertEqual(owners["9.2: move refinement spike TEST-60 (FEAT-3) to sprint 16"], "Alex Example")


class NoEstimateTest(unittest.TestCase):
    """Decision 0018: the report counts unpointed work and never estimates its SP."""

    def test_report_has_unpointed_count_and_no_sp_range(self):
        release = cr.release_health(CHECKS, {"release": "9.1", "phase": "development"},
                                    cr.release_capacity(45, [{"number": 14, "availability": 1}]))
        sprint = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=24, availability=0.9)
        report = cr.build_report(sprint, [release])
        text = "\n".join(report["top_post"] + report["replies"])
        self.assertIn("6 unpointed items, not counted in scope", text)
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
    def _risk(self, today, done):
        issues = dict(ISSUES, total_done_sp=done, total_remaining_sp=30 - done)
        out = cr.sprint_health(issues, dict(SPRINT, week=3), today, "mid-sprint")
        return out["risks"][0]["text"] if out["risks"] else None

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

    def test_select_sections_accepts_full_scrum_context_results(self):
        sections, missing = cr.select_sections({}, [{"today": "2030-02-01", "release": _window("9.1", "development")}])
        self.assertEqual([s["release"] for s in sections], ["9.1"])
        self.assertEqual(missing, [])

    def test_assigned_is_active_work_per_law_07(self):
        base = {"type": "Bug", "sp": 1, "assignee_display": "Alex Example", "epic_key": "TEST-100",
                "blocked_by": [], "labels": [], "flagged": False, "stale": False, "updated": "2030-01-29"}
        issues = {"total_sp": 3, "total_done_sp": 0, "total_remaining_sp": 3, "issues": [
            dict(base, key="BUG-1", status="ASSIGNED"),
            dict(base, key="BUG-2", status="Code Review"),   # not a law 07 state: not counted
            dict(base, key="BUG-3", status="ASSIGNED", updated="2030-02-06")]}
        out = cr.sprint_health(issues, dict(SPRINT, week=2), "2030-02-07", "mid-sprint")
        self.assertEqual(out["blockers"], [{"key": "BUG-1", "reasons": ["no update for more than 5 business days"]}])

    def test_goal_rating_is_carried_into_the_reply(self):
        sprint = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint",
                                  goal={"rating": "🟡", "note": "goal work stalled on TEST-2"})
        reply = cr.build_report(sprint, [])["replies"][0]
        self.assertIn("• Sprint goal: 🟡 goal work stalled on TEST-2", reply)
        none = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint",
                                goal={"rating": None, "note": "no sprint goal set"})
        self.assertIn("• Sprint goal: no sprint goal set", cr.build_report(none, [])["replies"][0])

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
        self.assertIsNone(no_flag["goal"]["rating"])
        self.assertIn("Flagged not available", no_flag["goal"]["note"])
        yellow = cr.sprint_health(ISSUES, dict(SPRINT, week=2), "2030-02-07", "mid-sprint",
                                  goal={"rating": "🟡", "note": "stalled"}, links_complete=False)
        self.assertEqual(yellow["goal"]["rating"], "🟡")  # only green is withheld

    def test_build_report_rejects_section_descriptors(self):
        sections, _ = cr.select_sections({}, [_window("9.1", "development")])
        with self.assertRaises(ValueError):
            cr.build_report(None, sections)


class UnknownSprintModeTest(unittest.TestCase):
    def test_unknown_week_leaves_sprint_health_out(self):
        for week in (None, "three"):
            mode = cr.sprint_mode(week)
            self.assertIsNone(mode)
            out = cr.sprint_health(ISSUES, dict(SPRINT, week=week), "2030-02-07", mode)
            self.assertNotIn("burndown", out)
            self.assertNotIn("capacity", out)
            self.assertEqual((out["risks"], out["actions"]), ([], []))
            report = cr.build_report(out, [])
            text = "\n".join(report["top_post"] + report["replies"])
            self.assertIn("• Sprint health: sprint week unknown", text)
            self.assertNotIn("None mode", text)
            self.assertNotIn("Sprint health: Test Sprint", text)
            self.assertTrue(report["top_post"][0].endswith("sprint data not available"))


class TeamOwnerTest(unittest.TestCase):
    ROLE_DOC = "# Scrum Master Responsibilities\n\n## Assignment\n\nAssigned by management.\n"

    def test_team_actions_go_to_the_scrum_master(self):
        sprint = cr.sprint_health(ISSUES, SPRINT, "2030-01-29", "capacity", velocity=24, availability=0.9)
        with_owner = cr.build_report(sprint, [], team_owner="Sam Scrum")
        actions = next(r for r in with_owner["replies"] if r.startswith("*Action items*"))
        self.assertIn("Re-plan the sprint: 8.4 SP over capacity (owner: Sam Scrum)", actions)
        self.assertIn("Point: TEST-3 (owner: Quinn Quality)", actions)  # issue owners unchanged
        self.assertIn("Link to an epic: TEST-2 (owner: Alex Example)", actions)
        without = next(r for r in cr.build_report(sprint, [])["replies"] if r.startswith("*Action items*"))
        self.assertIn("Re-plan the sprint: 8.4 SP over capacity (owner: no owner set)", without)

    def test_find_scrum_master(self):
        self.assertIsNone(ad.find_scrum_master(ROSTER_MD, self.ROLE_DOC))  # edge-context names no one today
        roster = ROSTER_MD.replace("| Senior Software Engineer |", "| Senior Software Engineer, Scrum Master |")
        self.assertEqual(ad.find_scrum_master(roster, self.ROLE_DOC), "Alex Example")
        doc = self.ROLE_DOC + "\n**Current Scrum Master**: [Quinn Quality](https://example.invalid/q)\n"
        self.assertEqual(ad.find_scrum_master(ROSTER_MD, doc), "Quinn Quality")


if __name__ == "__main__":
    unittest.main()
