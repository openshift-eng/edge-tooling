"""Tests for scrum_context.py — sprint/release-cycle context for chai-bot.

All dates, versions, and people here are synthetic. Do not add real Product Pages
dates or real names: the repository is public and the schedule is internal.
"""

import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import scrum_context as sc  # noqa: E402

D = dt.date
BOARD = 1000
# Synthetic 3-week sprints starting Monday 2030-01-07: Sprint 10 .. Sprint 21.
FIRST_START = D(2030, 1, 7)


def _raw_sprints(first=10, count=12, board=BOARD):
    """Synthetic board sprints, 3 weeks each, as the Jira tool returns them."""
    out = []
    for i in range(count):
        start = FIRST_START + dt.timedelta(weeks=3 * i)
        end = start + dt.timedelta(weeks=3)
        out.append({"id": str(500 + i), "name": f"Test Sprint {first + i}",
                    "state": "closed", "originBoardId": board,
                    "start_date": f"{start.isoformat()}T12:00:00.000Z",
                    "end_date": f"{end.isoformat()}T12:00:00.000Z"})
    return out


def _start(number):
    """Start date of synthetic sprint ``number``."""
    return FIRST_START + dt.timedelta(weeks=3 * (number - 10))


def _end(number):
    """End date of synthetic sprint ``number``."""
    return _start(number) + dt.timedelta(weeks=3)


SPRINTS = sc.board_sprints(_raw_sprints(), BOARD)

# Release 9.1: refinement Sprint 11, development 12-13, pencils-down 14, branch cut 15.
SCHEDULES = {
    "9.1": [
        {"name": "9.1 Development Branch opens", "date": _start(11).isoformat()},
        {"name": "Pencils Down / Stabilization", "date": (_end(14) + dt.timedelta(days=4)).isoformat()},
        {"name": "Post-Branching / Feature Freeze", "date": _start(15).isoformat()},
        {"name": "Master Branch Open for 9.2", "date": (_start(15) + dt.timedelta(days=11)).isoformat()},
    ],
}


def _num(sprint):
    """Sprint number, or None for a missing sprint."""
    return sprint["number"] if sprint else None


class TestBoardSprints(unittest.TestCase):
    def test_skips_sprints_from_other_origin_boards(self):
        """Sprints marked with another origin board are dropped."""
        raw = _raw_sprints(count=2) + [
            {"id": "9", "name": "Other Sprint 99", "originBoardId": 2000,
             "start_date": "2030-01-07", "end_date": "2030-01-28"},
            {"id": "8", "name": "Marked Sprint 98", "origin_board": True,
             "start_date": "2030-01-07", "end_date": "2030-01-28"},
        ]
        names = [s["name"] for s in sc.board_sprints(raw, BOARD)]
        self.assertEqual(names, ["Test Sprint 10", "Test Sprint 11"])

    def test_skips_sprints_without_dates(self):
        """Sprints missing a start or end date are dropped."""
        raw = _raw_sprints(count=1) + [
            {"id": "7", "name": "Test Sprint 97", "originBoardId": BOARD,
             "start_date": "2030-01-28T12:00:00.000Z"},
            {"id": "6", "name": "Test Sprint 96", "originBoardId": BOARD, "end_date": None,
             "start_date": "2030-02-18T12:00:00.000Z"},
        ]
        self.assertEqual([s["name"] for s in sc.board_sprints(raw, BOARD)], ["Test Sprint 10"])

    def test_dates_use_calendar_day(self):
        self.assertEqual(SPRINTS[0]["start"], D(2030, 1, 7))
        self.assertEqual(SPRINTS[0]["end"], D(2030, 1, 28))


class TestSprintForDate(unittest.TestCase):
    def test_mid_sprint(self):
        self.assertEqual(_num(sc.sprint_for_date(SPRINTS, D(2030, 1, 15))), 10)

    def test_boundary_belongs_to_sprint_ending_that_day(self):
        self.assertEqual(_num(sc.sprint_for_date(SPRINTS, _end(10))), 10)
        self.assertEqual(_num(sc.sprint_for_date(SPRINTS, _end(10) + dt.timedelta(days=1))), 11)

    def test_first_sprint_start_day(self):
        self.assertEqual(_num(sc.sprint_for_date(SPRINTS, FIRST_START)), 10)

    def test_week(self):
        s = SPRINTS[0]
        self.assertEqual(sc.sprint_week(s, D(2030, 1, 7)), 1)
        self.assertEqual(sc.sprint_week(s, D(2030, 1, 14)), 2)
        self.assertEqual(sc.sprint_week(s, D(2030, 1, 27)), 3)
        self.assertEqual(sc.sprint_week(s, D(2030, 1, 28)), 3)


class TestCurrentSprint(unittest.TestCase):
    def _sprints(self, states):
        """Normalized synthetic sprints with the given states applied."""
        raw = _raw_sprints()
        for number, state in states.items():
            raw[number - 10]["state"] = state
        return sc.board_sprints(raw, BOARD)

    def test_active_sprint_wins_on_boundary_day(self):
        # Boundary day between 13 and 14: 13 is closed (or just ending), 14 is active.
        """On a boundary day the active sprint is today's sprint, in week 1."""
        for previous_state in ("closed", None):
            with self.subTest(previous_state=previous_state):
                sprints = self._sprints({13: previous_state, 14: "active", 15: "future"})
                current = sc.current_sprint(sprints, _end(13))
                self.assertEqual(current["number"], 14)
                self.assertEqual(sc.sprint_week(current, _end(13)), 1)

    def test_active_sprint_wins_over_date(self):
        # A sprint left active past its end date is still today's sprint.
        sprints = self._sprints({13: "active", 14: "future"})
        self.assertEqual(sc.current_sprint(sprints, _start(14) + dt.timedelta(days=1))["number"], 13)

    def test_first_day_without_state_is_new_sprint(self):
        """Without state info, a sprint's first day belongs to the new sprint."""
        raw = _raw_sprints()
        for r in raw:
            r.pop("state")
        sprints = sc.board_sprints(raw, BOARD)
        self.assertEqual(sc.current_sprint(sprints, _start(14))["number"], 14)
        self.assertEqual(sc.sprint_week(sc.current_sprint(sprints, _start(14)), _start(14)), 1)

    def test_date_past_last_board_sprint_is_projected(self):
        sprints = SPRINTS[:6]  # Sprints 10-15, all closed
        current = sc.current_sprint(sprints, _start(16) + dt.timedelta(days=2))
        self.assertEqual(current["number"], 16)
        self.assertTrue(current["projected"])

    def test_build_context_uses_active_sprint(self):
        raw = _raw_sprints()
        raw[3]["state"], raw[4]["state"] = "closed", "active"  # 13 closed, 14 active
        out = sc.build_context({"today": _end(13).isoformat(), "board_id": BOARD,
                                "sprints": raw, "schedules": SCHEDULES})
        self.assertEqual(out["current_sprint"]["number"], 14)
        self.assertEqual(out["current_sprint"]["week"], 1)
        self.assertEqual(out["next_sprint"]["number"], 15)
        self.assertEqual(out["release_in_progress"][0]["phase"], sc.PHASE_PENCILS_DOWN)


class TestMilestoneMapping(unittest.TestCase):
    def test_mapping(self):
        """Release 9.1 maps to refinement 11, development 12-13, pencils-down 14, branch cut 15."""
        m = sc.map_release("9.1", SCHEDULES, SPRINTS)
        self.assertEqual(_num(m["refinement"]), 11)
        self.assertEqual([s["number"] for s in m["development"]], [12, 13])
        self.assertEqual(_num(m["pencils_down"]), 14)
        self.assertEqual(_num(m["branch_cut"]), 15)
        self.assertEqual(m["milestones"]["next_version"], "9.2")

    def test_pencils_down_on_sprint_end_is_that_sprint(self):
        sched = {"9.1": [
            {"name": "Development Branch opens", "date": _start(11).isoformat()},
            {"name": "Pencils Down", "date": _end(14).isoformat()},
            {"name": "Master Branch Open for 9.2", "date": _end(15).isoformat()}]}
        m = sc.map_release("9.1", sched, SPRINTS)
        self.assertEqual(_num(m["pencils_down"]), 14)

    def test_branch_cut_on_sprint_boundary_is_sprint_ending_that_day(self):
        sched = {"9.1": [
            {"name": "Development Branch opens", "date": _start(11).isoformat()},
            {"name": "Pencils Down", "date": _end(14).isoformat()},
            {"name": "Master Branch Open for 9.2", "date": _end(15).isoformat()}]}
        m = sc.map_release("9.1", sched, SPRINTS)
        self.assertEqual(_num(m["branch_cut"]), 15)

    def test_differently_worded_pencils_down(self):
        """Pencils-down milestones match by prefix however they are worded."""
        for name in ("Pencils Down / Stabilization", "Pencils Down Stabilization Gate",
                     "9.1 pencils down - stabilization gate", "OpenShift 9.1 Pencils Down"):
            sched = {"9.1": [dict(SCHEDULES["9.1"][0]),
                             {"name": name, "date": (_end(14) + dt.timedelta(days=4)).isoformat()},
                             dict(SCHEDULES["9.1"][3])]}
            with self.subTest(name=name):
                self.assertEqual(_num(sc.map_release("9.1", sched, SPRINTS)["pencils_down"]), 14)

    def test_refinement_on_boundary_is_sprint_starting_that_day(self):
        # Unlike pencils down / branch cut, refinement matches the sprint's start day.
        m = sc.map_release("9.1", SCHEDULES, SPRINTS)
        self.assertEqual(SCHEDULES["9.1"][0]["date"], _end(10).isoformat())
        self.assertEqual(_num(m["refinement"]), 11)

    def test_refinement_sprint_starting_late_is_chosen(self):
        """A sprint starting 1-7 days after Development Branch opens is the refinement sprint."""
        for late in (1, 7):
            sched = {"9.1": [{"name": "Development Branch opens",
                              "date": (_start(11) - dt.timedelta(days=late)).isoformat()},
                             dict(SCHEDULES["9.1"][1]), dict(SCHEDULES["9.1"][3])]}
            with self.subTest(late=late):
                self.assertEqual(_num(sc.map_release("9.1", sched, SPRINTS)["refinement"]), 11)

    def test_refinement_sprint_more_than_a_week_late_is_not_scheduled(self):
        sched = {"9.1": [{"name": "Development Branch opens",
                          "date": (_start(11) - dt.timedelta(days=8)).isoformat()}]}
        self.assertIsNone(sc.map_release("9.1", sched, SPRINTS)["refinement"])

    def test_near_miss_milestone_names_do_not_match(self):
        sched = {"9.1": [dict(SCHEDULES["9.1"][0]),
                         {"name": "Pre-Pencils Down checkpoint", "date": _end(14).isoformat()},
                         {"name": "Feature Freeze before Master Branch Open for 9.2",
                          "date": _end(15).isoformat()},
                         {"name": "Development Branch opens review", "date": _start(12).isoformat()}]}
        m = sc.map_release("9.1", sched, SPRINTS)
        self.assertIsNone(m["pencils_down"])
        self.assertIsNone(m["branch_cut"])
        self.assertEqual(_num(m["refinement"]), 11)  # the earliest matching prefix wins

    def test_pencils_down_range_uses_its_start(self):
        sched = {"9.1": [dict(SCHEDULES["9.1"][0]),
                         {"name": "Pencils Down / Stabilization",
                          "start": (_end(14) + dt.timedelta(days=4)).isoformat(),
                          "end": _end(16).isoformat()},
                         dict(SCHEDULES["9.1"][3])]}
        self.assertEqual(_num(sc.map_release("9.1", sched, SPRINTS)["pencils_down"]), 14)

    def test_release_without_feature_freeze_milestone(self):
        """A release without Post-Branching / Feature Freeze still maps fully."""
        sched = {"9.1": [m for m in SCHEDULES["9.1"] if not m["name"].startswith("Post-Branching")]}
        m = sc.map_release("9.1", sched, SPRINTS)
        self.assertEqual((_num(m["refinement"]), _num(m["pencils_down"]), _num(m["branch_cut"])),
                         (11, 14, 15))

    def test_release_with_no_schedule_is_not_scheduled_yet(self):
        """A release with no schedule is 'not scheduled yet' everywhere."""
        w = sc.release_window("9.5", SCHEDULES, SPRINTS, D(2030, 3, 1))
        self.assertEqual(w["phase"], sc.NOT_SCHEDULED)
        for key in ("refinement_sprint", "development_sprints", "pencils_down_sprint",
                    "branch_cut_sprint", "development_sprints_remaining"):
            self.assertEqual(w[key], sc.NOT_SCHEDULED, key)

    def test_next_release_refinement_from_previous_master_branch_open(self):
        # 9.2 has no schedule of its own; 9.1's "Master Branch Open for 9.2" falls
        # mid-sprint 15, so 9.2's refinement sprint is the first starting after it.
        w = sc.release_window("9.2", SCHEDULES, SPRINTS, D(2030, 3, 1))
        self.assertEqual(w["refinement_sprint"]["number"], 16)
        self.assertEqual(w["pencils_down_sprint"], sc.NOT_SCHEDULED)
        self.assertEqual(w["branch_cut_sprint"], sc.NOT_SCHEDULED)
        self.assertEqual(w["development_sprints_remaining"], sc.NOT_SCHEDULED)
        self.assertEqual(w["phase"], sc.PHASE_NOT_STARTED)

    def test_milestone_past_last_board_sprint_is_projected(self):
        few = SPRINTS[:6]  # Sprints 10-15
        w = sc.release_window("9.2", SCHEDULES, few, D(2030, 3, 1))
        self.assertEqual(w["refinement_sprint"]["number"], 16)
        self.assertTrue(w["refinement_sprint"]["projected"])
        self.assertIsNone(w["refinement_sprint"]["id"])
        self.assertEqual(w["refinement_sprint"]["name"], "Test Sprint 16")
        self.assertEqual(w["refinement_sprint"]["start"], _start(16).isoformat())


class TestReleaseValidation(unittest.TestCase):
    def test_valid_releases(self):
        """'X.Y' strings (with surrounding spaces) are valid releases."""
        for v in ("9.1", "10.0", " 9.1 "):
            with self.subTest(v=v):
                self.assertTrue(sc.is_valid_release(v))

    def test_invalid_releases(self):
        """Anything but an 'X.Y' string is an invalid release."""
        for v in ("latest", "1.x", "9", "9.1.2", "v9.1", "", None, 9.1):
            with self.subTest(v=v):
                self.assertFalse(sc.is_valid_release(v))
                w = sc.release_window(v, SCHEDULES, SPRINTS, D(2030, 3, 1))
                self.assertEqual(w["phase"], sc.INVALID_RELEASE)

    def test_build_context_invalid_release_does_not_raise(self):
        """build_context returns the invalid-release value instead of raising."""
        for v in ("latest", "1.x", 9.1):
            with self.subTest(v=v):
                out = sc.build_context({"today": "2030-03-01", "board_id": BOARD,
                                        "sprints": _raw_sprints(), "schedules": SCHEDULES,
                                        "release": v})
                self.assertEqual(out["release"]["phase"], sc.INVALID_RELEASE)
                self.assertEqual(out["current_sprint"]["number"], 12)

    def test_invalid_schedule_keys_are_ignored(self):
        """Schedule keys that are not 'X.Y' are ignored."""
        out = sc.build_context({"today": (_start(13) + dt.timedelta(days=2)).isoformat(),
                                "board_id": BOARD, "sprints": _raw_sprints(),
                                "schedules": {**SCHEDULES, "latest": [], "1.x": []}})
        self.assertEqual([r["release"] for r in out["release_in_progress"]], ["9.1"])


class TestProjectionLimit(unittest.TestCase):
    def test_projects_up_to_the_limit(self):
        last = SPRINTS[-1]  # Sprint 21
        inside = last["end"] + dt.timedelta(weeks=3 * (sc.MAX_PROJECTED_SPRINTS - 1) + 1)
        current = sc.current_sprint(SPRINTS, inside)
        self.assertTrue(current["projected"])
        self.assertEqual(current["number"], 21 + sc.MAX_PROJECTED_SPRINTS)

    def test_beyond_the_limit_is_not_available(self):
        last = SPRINTS[-1]
        beyond = last["end"] + dt.timedelta(weeks=3 * sc.MAX_PROJECTED_SPRINTS + 1)
        self.assertIsNone(sc.current_sprint(SPRINTS, beyond))
        out = sc.build_context({"today": beyond.isoformat(), "board_id": BOARD,
                                "sprints": _raw_sprints()})
        self.assertEqual(out["current_sprint"], sc.NOT_AVAILABLE)
        sched = {"9.9": [{"name": "Development Branch opens", "date": beyond.isoformat()}]}
        w = sc.release_window("9.9", sched, SPRINTS, D(2030, 3, 1))
        self.assertEqual(w["refinement_sprint"], sc.NOT_SCHEDULED)


class TestMalformedDates(unittest.TestCase):
    BAD = "2030-13-45"

    def _data(self, **extra):
        """Default build_context input, overridden by ``extra``."""
        data = {"today": (_start(13) + dt.timedelta(days=2)).isoformat(), "board_id": BOARD,
                "sprints": _raw_sprints(), "schedules": SCHEDULES, "events": [],
                "absences": [], "engineer_count": 2}
        data.update(extra)
        return data

    def _assert_warning(self, out, expected):
        """Assert the warning is present and the raw bad value is not in the output."""
        self.assertIn(expected, out["warnings"])
        self.assertNotIn(self.BAD, json.dumps(out))  # never the raw value

    def test_malformed_sprint_date_is_skipped_with_warning(self):
        raw = _raw_sprints()
        raw[11]["end_date"] = self.BAD  # Sprint 21
        out = sc.build_context(self._data(sprints=raw))
        self._assert_warning(out, "skipped sprint 'Test Sprint 21': bad end date")
        self.assertEqual(out["current_sprint"]["number"], 13)

    def test_malformed_milestone_date_is_skipped_with_warning(self):
        sched = {"9.1": [dict(SCHEDULES["9.1"][0]),
                         {"name": "Pencils Down", "date": self.BAD},
                         dict(SCHEDULES["9.1"][3])]}
        out = sc.build_context(self._data(schedules=sched, release="9.1"))
        self._assert_warning(out, "skipped milestone 'Pencils Down' (9.1): bad date")
        self.assertEqual(out["release"]["pencils_down_sprint"], sc.NOT_SCHEDULED)
        self.assertEqual(out["release"]["refinement_sprint"]["number"], 11)

    def test_malformed_event_date_is_skipped_with_warning(self):
        events = [{"ceremony": "Sprint Demos", "start": self.BAD},
                  {"title": "Retrospective with Jane Doe <jane@example.com>", "start": "not-a-date"},
                  {"ceremony": "Sprint Planning", "start": _end(13).isoformat()}]
        out = sc.build_context(self._data(events=events))
        self._assert_warning(out, "skipped calendar event (Sprint Demos): bad date")
        self.assertIn("skipped calendar event (Retrospective): bad date", out["warnings"])
        self.assertNotIn("Jane", json.dumps(out))
        self.assertEqual(out["next_ceremonies"]["Sprint Planning"], _end(13).isoformat())

    def test_malformed_absence_date_is_skipped_with_warning(self):
        absences = [{"person_id": "p1", "start": _start(14).isoformat(), "end": self.BAD},
                    {"person_id": "p2", "start": _start(14).isoformat(),
                     "end": _start(14).isoformat()}]
        out = sc.build_context(self._data(absences=absences))
        self._assert_warning(out, "skipped absence entry 1: bad date")
        self.assertEqual(out["next_sprint_availability"], 0.97)  # 1 of 30 person-days

    def test_no_warnings_on_clean_input(self):
        self.assertEqual(sc.build_context(self._data())["warnings"], [])

    def test_malformed_today_raises(self):
        """A provided malformed today raises ValueError."""
        for bad in ("2030-02-30", "tomorrow", 20300301):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sc.build_context(self._data(today=bad))

    def test_malformed_today_cli_fails(self):
        """The CLI exits non-zero on a malformed today."""
        script = os.path.join(os.path.dirname(__file__), "..", "scrum_context.py")
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "in.json"), os.path.join(tmp, "out.json")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(self._data(today="tomorrow"), fh)
            proc = subprocess.run([sys.executable, script, "--input", src, "--output", dst],
                                  capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)


class TestCeremonyLabels(unittest.TestCase):
    def test_ceremony_field_is_used(self):
        s = SPRINTS[1]
        events = [{"ceremony": "Sprint Demos", "start": (s["end"] - dt.timedelta(days=2)).isoformat()},
                  {"ceremony": "Retrospective", "start": (s["end"] - dt.timedelta(days=2)).isoformat(),
                   "status": "cancelled"}]
        out = sc.ceremony_dates(s, events)
        self.assertEqual(out["Sprint Demos"], (s["end"] - dt.timedelta(days=2)).isoformat())
        self.assertIsNone(out["Retrospective"])

    def test_legacy_title_prefix_still_cancels(self):
        """A cancelled prefix on a legacy title still cancels the event."""
        s = SPRINTS[1]
        day = (s["end"] - dt.timedelta(days=2)).isoformat()
        for title in ("Cancelled: Retrospective", "Canceled: Retrospective", "CANCELLED Retrospective"):
            with self.subTest(title=title):
                self.assertIsNone(sc.ceremony_dates(s, [{"title": title, "start": day}])["Retrospective"])
        # With a ceremony label but no cancelled status, the event counts.
        self.assertEqual(sc.ceremony_dates(s, [{"ceremony": "Retrospective", "start": day,
                                                "status": "confirmed"}])["Retrospective"], day)

    def test_extra_title_text_never_appears_in_output(self):
        """Text from event titles never reaches the output."""
        event_detail = "1:1 Jane Doe jane.doe@example.com"
        s = SPRINTS[3]  # Sprint 13
        events = [{"title": f"Sprint Demos + {event_detail}", "start": (s["end"] - dt.timedelta(days=2)).isoformat()},
                  {"title": f"Retrospective ({event_detail})", "start": "garbage"},
                  {"ceremony": "Sprint Planning", "title": event_detail, "start": s["start"].isoformat()}]
        out = sc.build_context({"today": (s["start"] + dt.timedelta(days=2)).isoformat(),
                                "board_id": BOARD, "sprints": _raw_sprints(), "events": events})
        dumped = json.dumps(out)
        for fragment in ("Jane", "jane.doe", "example.com", "1:1"):
            self.assertNotIn(fragment, dumped)
        self.assertEqual(out["current_sprint_ceremonies"]["Sprint Demos"],
                         (s["end"] - dt.timedelta(days=2)).isoformat())
        self.assertEqual(out["current_sprint_ceremonies"]["Sprint Planning"], s["start"].isoformat())


class TestPhase(unittest.TestCase):
    def setUp(self):
        """Build the shared fixtures for this test case."""
        self.m = sc.map_release("9.1", SCHEDULES, SPRINTS)
        self.master = sc.release_milestones("9.1", SCHEDULES)["master_branch_open"]

    def _phase(self, day):
        """Phase of release 9.1 on ``day``."""
        return sc.phase_on(self.m, day)

    # "Today" has no boundary rule: a sprint's first day belongs to that sprint.
    def test_before_refinement(self):
        self.assertEqual(self._phase(_start(10) + dt.timedelta(days=3)), sc.PHASE_NOT_STARTED)
        self.assertEqual(self._phase(_start(11) - dt.timedelta(days=1)), sc.PHASE_NOT_STARTED)

    def test_refinement(self):
        self.assertEqual(self._phase(_start(11)), sc.PHASE_REFINEMENT)
        self.assertEqual(self._phase(_end(11) - dt.timedelta(days=1)), sc.PHASE_REFINEMENT)

    def test_development(self):
        self.assertEqual(self._phase(_start(12)), sc.PHASE_DEVELOPMENT)
        self.assertEqual(self._phase(_end(13) - dt.timedelta(days=1)), sc.PHASE_DEVELOPMENT)

    def test_pencils_down_sprint(self):
        self.assertEqual(self._phase(_start(14)), sc.PHASE_PENCILS_DOWN)
        self.assertEqual(self._phase(_end(14) - dt.timedelta(days=1)), sc.PHASE_PENCILS_DOWN)

    def test_pre_branch_cut(self):
        self.assertEqual(self._phase(_end(14)), sc.PHASE_PRE_BRANCH_CUT)
        self.assertEqual(self._phase(self.master - dt.timedelta(days=1)), sc.PHASE_PRE_BRANCH_CUT)

    def test_explicit_current_sprint_overrides_date(self):
        # Sprint 14 still active one day past its end: still the pencils-down sprint.
        self.assertEqual(sc.phase_on(self.m, _end(14) + dt.timedelta(days=1), SPRINTS[4]),
                         sc.PHASE_PENCILS_DOWN)

    def test_finished(self):
        self.assertEqual(self._phase(self.master), sc.PHASE_FINISHED)
        self.assertEqual(self._phase(self.master + dt.timedelta(days=1)), sc.PHASE_FINISHED)
        self.assertEqual(self._phase(_end(18)), sc.PHASE_FINISHED)

    def test_no_schedule(self):
        m = sc.map_release("9.7", SCHEDULES, SPRINTS)
        self.assertEqual(sc.phase_on(m, D(2030, 3, 1)), sc.NOT_SCHEDULED)


class TestDevSprintsRemaining(unittest.TestCase):
    def setUp(self):
        """Build the shared fixtures for this test case."""
        self.m = sc.map_release("9.1", SCHEDULES, SPRINTS)

    def test_counts(self):
        """Development sprints left on dates before, during and after each sprint."""
        cases = [
            (_start(10) + dt.timedelta(days=2), 3),  # before refinement: 12, 13, 14
            (_start(11) + dt.timedelta(days=2), 3),  # in refinement
            (_start(12) + dt.timedelta(days=2), 3),  # in 12: 12, 13, 14
            (_end(12), 2),                           # first day of 13
            (_start(13) + dt.timedelta(days=2), 2),
            (_start(14) + dt.timedelta(days=2), 1),  # pencils-down sprint
            (_end(14) - dt.timedelta(days=1), 1),
            (_end(14), 0),
            (_end(17), 0),
        ]
        for day, expected in cases:
            with self.subTest(day=day):
                self.assertEqual(sc.dev_sprints_remaining(self.m, day), expected)

    def test_without_pencils_down(self):
        sched = {"9.1": [SCHEDULES["9.1"][0]]}
        m = sc.map_release("9.1", sched, SPRINTS)
        self.assertEqual(sc.dev_sprints_remaining(m, D(2030, 3, 1)), sc.NOT_SCHEDULED)


class TestCeremonies(unittest.TestCase):
    def setUp(self):
        """Build the shared fixtures for this test case."""
        s = SPRINTS[1]  # Sprint 11
        self.sprint = s
        self.events = [
            {"title": "Sprint Planning", "start": f"{s['start']}T14:00:00Z"},
            {"title": "Edge: Mid-Sprint Feature Readback", "start": (s["start"] + dt.timedelta(days=10)).isoformat()},
            {"title": "Sprint Demos", "start": (s["end"] - dt.timedelta(days=3)).isoformat()},
            {"title": "Retrospective", "start": (s["end"] - dt.timedelta(days=3)).isoformat()},
            # Next sprint's planning on the boundary day; previous sprint's retro on our start day.
            {"title": "Sprint Planning", "start": s["end"].isoformat()},
            {"title": "Retrospective", "start": s["start"].isoformat()},
            {"title": "Team lunch", "start": (s["start"] + dt.timedelta(days=2)).isoformat()},
        ]

    def test_detects_all_ceremonies(self):
        s = self.sprint
        self.assertEqual(sc.ceremony_dates(s, self.events), {
            "Sprint Planning": s["start"].isoformat(),
            "Mid-Sprint Feature Readback": (s["start"] + dt.timedelta(days=10)).isoformat(),
            "Sprint Demos": (s["end"] - dt.timedelta(days=3)).isoformat(),
            "Retrospective": (s["end"] - dt.timedelta(days=3)).isoformat(),
        })

    def test_cancelled_ceremony_is_none(self):
        """A cancelled ceremony comes back as None."""
        s = self.sprint
        events = [e for e in self.events if "Readback" not in e["title"]] + [
            {"title": "Mid-Sprint Feature Readback", "status": "cancelled",
             "start": (s["start"] + dt.timedelta(days=10)).isoformat()},
            {"title": "Canceled: Mid-Sprint Feature Readback",
             "start": (s["start"] + dt.timedelta(days=11)).isoformat()},
        ]
        self.assertIsNone(sc.ceremony_dates(s, events)["Mid-Sprint Feature Readback"])

    def test_neighbouring_sprints_boundary_ceremonies_are_not_borrowed(self):
        """Neighbouring sprints' ceremonies on a boundary day are not borrowed."""
        s = self.sprint
        own = (s["end"] - dt.timedelta(days=3)).isoformat()
        events = [e for e in self.events if e["title"] not in ("Retrospective", "Sprint Planning")]
        events += [
            {"title": "Retrospective", "start": own, "status": "cancelled"},
            {"title": "Retrospective", "start": s["start"].isoformat()},  # previous sprint's
            {"title": "Sprint Planning", "start": s["end"].isoformat()},  # next sprint's
        ]
        out = sc.ceremony_dates(s, events)
        self.assertIsNone(out["Retrospective"])
        self.assertIsNone(out["Sprint Planning"])

    def test_missing_ceremony_is_none(self):
        """A ceremony with no event comes back as None."""
        events = [e for e in self.events if e["title"] != "Sprint Demos"]
        self.assertIsNone(sc.ceremony_dates(self.sprint, events)["Sprint Demos"])

    def test_next_ceremonies(self):
        """Next ceremonies are the first non-cancelled ones on or after today."""
        s = self.sprint
        nxt = sc.next_ceremonies(self.events, s["start"] + dt.timedelta(days=5))
        self.assertEqual(nxt["Sprint Planning"], s["end"].isoformat())
        self.assertEqual(nxt["Sprint Demos"], (s["end"] - dt.timedelta(days=3)).isoformat())
        nxt = sc.next_ceremonies(self.events, s["end"] + dt.timedelta(days=1))
        self.assertEqual(nxt, {c: None for c in sc.CEREMONIES})


class TestAvailability(unittest.TestCase):
    # Sprint window Mon 2030-01-07 .. Mon 2030-01-28: 15 working days.
    START, END = D(2030, 1, 7), D(2030, 1, 28)

    def test_working_days_exclude_weekends_and_end_day(self):
        self.assertEqual(len(sc.working_days(self.START, self.END)), 15)

    def test_no_absences(self):
        self.assertEqual(sc.availability([], 4, self.START, self.END), 1.0)

    def test_overlapping_absences_count_once_per_person_day(self):
        absences = [
            {"person_id": "p1", "start": "2030-01-07", "end": "2030-01-11"},  # 5 days
            {"person_id": "p1", "start": "2030-01-09", "end": "2030-01-10"},  # overlap
            {"person_id": "p1", "start": "2030-01-10", "end": "2030-01-10"},  # holiday overlap
        ]
        # 5 person-days off of 4 x 15 = 60 -> 1 - 5/60 = 0.9167
        self.assertEqual(sc.availability(absences, 4, self.START, self.END), 0.92)

    def test_weekends_excluded(self):
        absences = [{"person_id": "p2", "start": "2030-01-11", "end": "2030-01-14"}]  # Fri-Mon
        # Fri + Mon = 2 of 30 person-days
        self.assertEqual(sc.availability(absences, 2, self.START, self.END), 0.93)

    def test_clipped_to_sprint_window(self):
        absences = [{"person_id": "p3", "start": "2030-01-01", "end": "2030-01-08"},
                    {"person_id": "p3", "start": "2030-01-28", "end": "2030-02-01"}]
        # Only Jan 7-8 fall in the window (the end day belongs to the next sprint).
        self.assertEqual(sc.availability(absences, 1, self.START, self.END), 0.87)

    def test_different_people_same_day_both_count(self):
        absences = [{"person_id": "p1", "start": "2030-01-07", "end": "2030-01-07"},
                    {"person_id": "p2", "start": "2030-01-07", "end": "2030-01-07"}]
        self.assertEqual(sc.availability(absences, 2, self.START, self.END), 0.93)

    def test_roster_not_configured(self):
        self.assertEqual(sc.availability([], None, self.START, self.END), sc.ROSTER_NOT_CONFIGURED)
        self.assertEqual(sc.availability([], 0, self.START, self.END), sc.ROSTER_NOT_CONFIGURED)


class TestBuildContext(unittest.TestCase):
    def _data(self, **extra):
        """Default build_context input, overridden by ``extra``."""
        data = {"today": (_start(13) + dt.timedelta(days=9)).isoformat(), "board_id": BOARD,
                "sprints": _raw_sprints(), "schedules": SCHEDULES, "events": [],
                "absences": [], "engineer_count": 5}
        data.update(extra)
        return data

    def test_default_mode(self):
        """No-argument mode returns current/next sprint, release in progress, availability."""
        out = sc.build_context(self._data())
        self.assertEqual(out["current_sprint"]["number"], 13)
        self.assertEqual(out["current_sprint"]["week"], 2)
        self.assertEqual(out["next_sprint"]["number"], 14)
        self.assertEqual(out["release_in_progress"],
                         [{"release": "9.1", "phase": sc.PHASE_DEVELOPMENT,
                           "development_sprints_remaining": 2}])
        self.assertEqual(out["next_sprint_availability"], 1.0)
        self.assertEqual(out["next_ceremonies"], {c: None for c in sc.CEREMONIES})

    def test_release_mode(self):
        """Release mode returns the release's full window and phase."""
        out = sc.build_context(self._data(release="9.1"))
        r = out["release"]
        self.assertEqual(r["refinement_sprint"]["number"], 11)
        self.assertEqual([s["number"] for s in r["development_sprints"]], [12, 13])
        self.assertEqual(r["pencils_down_sprint"]["number"], 14)
        self.assertEqual(r["branch_cut_sprint"]["number"], 15)
        self.assertEqual(r["phase"], sc.PHASE_DEVELOPMENT)
        self.assertEqual(r["development_sprints_remaining"], 2)

    def test_inferred_release_is_in_progress_during_its_refinement(self):
        # 9.2 has no schedule; its refinement sprint (16) comes from 9.1's
        # "Master Branch Open for 9.2". Empty and missing schedules both count.
        """A release inferred from the previous Master Branch Open is in progress in refinement."""
        for schedules in (SCHEDULES, {**SCHEDULES, "9.2": []}):
            with self.subTest(has_empty=("9.2" in schedules)):
                out = sc.build_context(self._data(schedules=schedules,
                                                  today=(_start(16) + dt.timedelta(days=2)).isoformat()))
                self.assertIn({"release": "9.2", "phase": sc.PHASE_REFINEMENT,
                               "development_sprints_remaining": sc.NOT_SCHEDULED},
                              out["release_in_progress"])

    def test_on_master_branch_open_only_next_release_is_in_progress(self):
        # 9.1's "Master Branch Open for 9.2" falls on the day sprint 16 starts, which
        # is also 9.2's inferred refinement sprint: 9.1 is finished, 9.2 refining.
        sched = {"9.1": [dict(SCHEDULES["9.1"][0]), dict(SCHEDULES["9.1"][1]),
                         {"name": "Master Branch Open for 9.2", "date": _start(16).isoformat()}]}
        out = sc.build_context(self._data(schedules=sched, today=_start(16).isoformat()))
        self.assertEqual(out["release_in_progress"],
                         [{"release": "9.2", "phase": sc.PHASE_REFINEMENT,
                           "development_sprints_remaining": sc.NOT_SCHEDULED}])
        self.assertEqual(sc.release_window("9.1", sched, SPRINTS, _start(16))["phase"],
                         sc.PHASE_FINISHED)

    def test_missing_sources_are_not_available(self):
        out = sc.build_context({"today": "2030-03-01", "board_id": BOARD})
        self.assertEqual(out["current_sprint"], sc.NOT_AVAILABLE)
        self.assertEqual(out["release_in_progress"], sc.NOT_AVAILABLE)
        self.assertEqual(out["next_ceremonies"], sc.NOT_AVAILABLE)
        self.assertEqual(out["next_sprint_availability"], sc.NOT_AVAILABLE)

    def test_roster_not_configured(self):
        out = sc.build_context(self._data(engineer_count=None))
        self.assertEqual(out["next_sprint_availability"], sc.ROSTER_NOT_CONFIGURED)
        self.assertEqual(out["current_sprint"]["number"], 13)  # everything else still works

    def test_cli_round_trip(self):
        """The CLI reads --input JSON and writes the same result to --output."""
        script = os.path.join(os.path.dirname(__file__), "..", "scrum_context.py")
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = os.path.join(tmp, "in.json"), os.path.join(tmp, "out.json")
            with open(src, "w", encoding="utf-8") as fh:
                json.dump(self._data(release="9.1"), fh)
            subprocess.run([sys.executable, script, "--input", src, "--output", dst], check=True)
            with open(dst, encoding="utf-8") as fh:
                out = json.load(fh)
        self.assertEqual(out["release"]["pencils_down_sprint"]["number"], 14)


if __name__ == "__main__":
    unittest.main()
