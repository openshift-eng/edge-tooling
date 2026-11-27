"""Regression tests for two weekly report bugs seen on 2026-11-26 (OCPEDGE-3276).

1. A release section was left out with "a release milestone could not be placed on a
   sprint": Product Pages renamed the branch-cut milestone "Master Branch Open for <X.Y>"
   to "Main Branch Open for <X.Y>", and only the old name matched.
2. The final-week split put Code Review and POST issues under "other status".

All keys, names, sprints and dates are synthetic (public repository).
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import chai_report as cr  # noqa: E402
import scrum_context as sc  # noqa: E402
from _jira_transforms import DONE_STATUSES_SPRINT  # noqa: E402

START = dt.date(2030, 1, 7)  # Test Sprint 10 starts; 3-week sprints


def sprint(n, state):
    s = START + dt.timedelta(weeks=3 * (n - 10))
    return {"id": str(500 + n), "name": f"Test Sprint {n}", "state": state, "originBoardId": 1000,
            "start_date": f"{s.isoformat()}T13:00:00.000Z",
            "end_date": f"{(s + dt.timedelta(weeks=3)).isoformat()}T13:00:00.000Z"}


def board(active=14):
    return [sprint(n, "closed" if n < active else "active" if n == active else "future") for n in range(10, 17)]


def day(n, offset=0):
    return (START + dt.timedelta(weeks=3 * (n - 10), days=offset)).isoformat()


# 9.2: development opens with Test Sprint 11, pencils down at the end of 13, branch cut on the
# day Test Sprint 14 ends (a sprint boundary: it belongs to the sprint ending that day).
SCHEDULE_92 = [{"name": "9.2 Development Branch opens", "date_start": day(11), "date_finish": day(11)},
               {"name": "Pencils Down / Stabilization Gate for OCP 9.2", "date_start": day(14, 4),
                "date_finish": day(14, 4)},
               {"name": "Main Branch Open for 9.3", "date_start": day(15), "date_finish": day(15)}]


def window(schedule, today=None, release="9.2", extra=None):
    schedules = {release: schedule, **(extra or {})}
    return sc.build_context({"today": today or day(14, 3), "board_id": 1000, "sprints": board(),
                             "schedules": schedules, "release": release})["release"]


class MainBranchOpenTest(unittest.TestCase):
    def test_the_new_name_places_the_branch_cut(self):
        w = window(SCHEDULE_92)
        self.assertEqual("Test Sprint 14", w["branch_cut_sprint"]["name"])  # the sprint ending that day
        self.assertEqual("9.3", w["milestones"]["next_release"])
        self.assertEqual("pre-branch-cut", w["phase"])

    def test_the_section_is_shown_on_both_sides_of_the_week(self):
        for today in (day(14, 0), day(14, 3)):
            w = window(SCHEDULE_92, today)
            ctx = {"release_in_progress": [{"release": "9.2", "phase": w["phase"]}]}
            sections, missing = cr.select_sections(ctx, [w])
            self.assertEqual(["9.2"], [s["release"] for s in sections], today)
            self.assertEqual([], missing)

    def test_the_old_name_still_matches(self):
        old = [dict(m, name="Master Branch Open for 9.3") if m["name"].startswith("Main") else m for m in SCHEDULE_92]
        self.assertEqual("Test Sprint 14", window(old)["branch_cut_sprint"]["name"])

    def test_the_previous_release_places_the_next_refinement_sprint_by_either_name(self):
        for name in ("Main Branch Open for 9.3", "Master Branch Open for 9.3"):
            prev = [dict(m, name=name) if "Branch Open" in m["name"] else m for m in SCHEDULE_92]
            w = window([], release="9.3", extra={"9.2": prev})
            self.assertEqual("Test Sprint 15", w["refinement_sprint"]["name"], name)

    def test_a_missing_branch_cut_names_the_milestone(self):
        schedule = [m for m in SCHEDULE_92 if "Branch Open" not in m["name"]]
        w = window(schedule)
        ctx = {"release_in_progress": [{"release": "9.2", "phase": w["phase"]}]}
        sections, missing = cr.select_sections(ctx, [w])
        self.assertEqual([], sections)
        self.assertEqual([{"what": "9.2 release section", "why": 'the branch-cut milestone ("Main Branch Open for '
                                                                 '<next release>") is not in the 9.2 Product Pages '
                                                                 'schedule'}], missing)


class StatusStageTest(unittest.TestCase):
    def issue(self, status, sp):
        return {"key": f"TEST-{status}", "status": status, "sp": sp}

    def test_every_workflow_status_has_a_stage(self):
        statuses = {"To Do": 1, "In Progress": 2, "Code Review": 3, "Review": 1, "New": 1, "ASSIGNED": 1,
                    "POST": 2, "MODIFIED": 1, "ON_QA": 0}
        out = cr._still_open([self.issue(s, sp) for s, sp in statuses.items()])
        self.assertEqual((5, 7), (out["review_issues"], out["review_sp"]))  # Code Review, Review, POST, MODIFIED, ON_QA
        self.assertEqual((4, 5), (out["work_issues"], out["work_sp"]))  # To Do, In Progress, New, ASSIGNED
        self.assertEqual((0, 0), (out["other_issues"], out["other_sp"]))

    def test_release_pending_is_done(self):
        self.assertIn("Release Pending", DONE_STATUSES_SPRINT)


if __name__ == "__main__":
    unittest.main()
