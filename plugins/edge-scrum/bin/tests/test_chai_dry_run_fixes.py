"""Fixes from the first chai-bot dry run on real data.

1. query_jira printed ``Due:`` on the dates line; the adapter did not know the
   label, kept the whole line as "Created", and a transform crashed on the
   resulting date. Unknown labels on a pipe line are now split off and ignored
   with a warning, and no unreadable date can stop the report.
2. On a sprint-change Monday the ending sprint is still "active" on the board
   until someone starts the next one; the report now uses the sprint that
   starts that day.

All keys, names, sprints, releases and dates are synthetic (public repository).
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import chai_jira_adapter as ad  # noqa: E402
import chai_report as cr  # noqa: E402
import scrum_context as sc  # noqa: E402
from _jira_transforms import is_stale  # noqa: E402
from tests.test_chai_jira_adapter import transform_sprint_issues  # noqa: E402

DATES_WITH_DUE = "Created: 2026-11-10 | Updated: 2026-11-17 | Due: 2026-11-17 | Resolved: 2026-11-17"


def _page(*date_lines, status="In Progress"):
    """One issue whose dates line(s) are given, in query_jira's layout."""
    lines = ["Found 1 issues (showing 1):",
             "  [TEST-1] Synthetic item",
             f"    Type: Story | Status: {status} | Priority: Major | Assignee: Alex Example | Reporter: Sam Sample"]
    lines += [f"    {d}" for d in date_lines]
    lines += ["    Components: Component A | Parent: TEST-100", "    customfield_10028: 3.0"]
    return "\n".join(lines) + "\n"


def _adapt(text):
    return ad.adapt_fetch({"name": "sprint_issues", "pages": [{"text": text}]})


class DueLabelTest(unittest.TestCase):
    def test_dates_line_with_due_is_split(self):
        out = _adapt(_page(DATES_WITH_DUE))
        issue = out["issues"][0]
        self.assertEqual((issue["created"], issue["updated"], issue["resolutiondate"], issue["duedate"]),
                         ("2026-11-10", "2026-11-17", "2026-11-17", "2026-11-17"))
        self.assertEqual((out["warnings"], out["left_out"]), ([], []))
        self.assertTrue(out["fetch"]["complete"])
        row = transform_sprint_issues.transform_issue(issue, "2026-11-23")
        self.assertEqual((row["created"], row["updated"]), ("2026-11-10", "2026-11-17"))

    def test_unknown_label_is_split_off_and_warned(self):
        out = _adapt(_page("Created: 2026-11-10 | Foo: bar | Updated: 2026-11-17"))
        issue = out["issues"][0]
        self.assertEqual((issue["created"], issue["updated"]), ("2026-11-10", "2026-11-17"))
        self.assertIn("ignored 1 unrecognized line(s); labels: Foo", out["warnings"][0])
        self.assertTrue(out["fetch"]["complete"])

    def test_free_text_lines_are_still_not_cut(self):
        out = _adapt(_page("Created: 2026-11-10", "Description: step one | Note: see docs | Due: never"))
        self.assertEqual(out["issues"][0]["description"], "step one | Note: see docs | Due: never")


class UnreadableDateTest(unittest.TestCase):
    def test_garbage_date_is_dropped_and_listed(self):
        out = _adapt(_page("Created: 2026-11-10 | Updated: garbage | Due: 31/11/2026"))
        issue = out["issues"][0]
        self.assertEqual(issue["created"], "2026-11-10")
        self.assertNotIn("updated", issue)
        self.assertNotIn("duedate", issue)
        self.assertEqual(out["left_out"], [{
            "what": "Unreadable dates in the sprint_issues fetch (1 issue(s))",
            "why": "TEST-1 updated 'garbage'; TEST-1 duedate '31/11/2026'; those dates are ignored"}])
        self.assertTrue(out["fetch"]["complete"])

    def test_one_bad_issue_does_not_stop_the_report(self):
        good = _page(DATES_WITH_DUE).replace("TEST-1", "TEST-2")
        bad = _page("Created: garbage | Updated: also garbage")
        text = "Found 2 issues (showing 2):\n" + "\n".join(
            t.split("\n", 1)[1].rstrip("\n") for t in (good, bad)) + "\n"
        out = _adapt(text)
        self.assertEqual(len(out["issues"]), 2)
        rows = [transform_sprint_issues.transform_issue(i, "2026-11-23") for i in out["issues"]]
        sprint_issues = {"issues": rows, "total_sp": 6, "total_done_sp": 0, "total_remaining_sp": 6}
        sprint = {"name": "Test Sprint 11", "start": "2026-11-09", "end": "2026-11-30", "week": 3}
        health = cr.sprint_health(sprint_issues, sprint, "2026-11-23", "mid-sprint")
        report = cr.build_report(health, [], out["left_out"])
        self.assertEqual(len(report["top_post"]), 3)
        left = report["replies"][-1].splitlines()[-1]
        self.assertTrue(left.startswith("Left out: "))
        self.assertIn("Unreadable dates in the sprint_issues fetch (1 issue(s)): TEST-1 created 'garbage'", left)

    def test_transform_and_stale_tolerate_garbage(self):
        raw = {"key": "TEST-3", "status": {"name": "In Progress"}, "issue_type": {"name": "Story"},
               "created": "not a date", "updated": "2026-13-45"}
        row = transform_sprint_issues.transform_issue(raw, "2026-11-23")
        self.assertEqual((row["created"], row["updated"], row["stale"]), ("2026-11-23", "2026-11-23", False))
        self.assertFalse(is_stale("garbage", "2026-11-23"))
        self.assertFalse(is_stale(None, "2026-11-23"))
        self.assertTrue(is_stale("2026-11-02", "2026-11-23"))  # readable dates unchanged


BOARD = 1000
START = dt.date(2030, 1, 7)  # Monday; Test Sprint 10 onwards, 3-week sprints


def _board(states):
    """Board sprints Test Sprint 10.. with the given states (one per sprint)."""
    out = []
    for i, state in enumerate(states):
        start = START + dt.timedelta(weeks=3 * i)
        out.append({"id": str(500 + i), "name": f"Test Sprint {10 + i}", "state": state, "originBoardId": BOARD,
                    "start_date": start.isoformat(), "end_date": (start + dt.timedelta(weeks=3)).isoformat()})
    return out


def _day(sprint, weeks=0):
    return (START + dt.timedelta(weeks=3 * (sprint - 10) + weeks)).isoformat()


# Release 9.2's development branch opens with Test Sprint 12: its refinement sprint.
SCHEDULES = {"9.2": [{"name": "9.2 Development Branch opens", "date": _day(12)}]}


def _report_context(today, states):
    base = {"today": today, "board_id": BOARD, "sprints": _board(states), "schedules": SCHEDULES}
    ctx = sc.build_context(base)
    window = sc.build_context(dict(base, release="9.2"))["release"]
    sections, _ = cr.select_sections(ctx, [window])
    return ctx["current_sprint"], window, sections


class SprintChangeMondayTest(unittest.TestCase):
    """Like Sprint 297 ending and Sprint 298 starting on the same Monday."""

    def test_next_sprint_starting_today_is_used(self):
        # Test Sprint 11 is still "active" on its end day; Test Sprint 12 starts that day.
        current, window, sections = _report_context(_day(12), ["closed", "active", "future", "future"])
        self.assertEqual((current["name"], current["week"]), ("Test Sprint 12", 1))
        self.assertEqual(cr.sprint_mode(current["week"]), "capacity")
        self.assertEqual(window["phase"], "refinement")
        self.assertEqual([(s["release"], s["kind"]) for s in sections], [("9.2", "refinement")])

    def test_normal_monday_is_unchanged(self):
        current, window, sections = _report_context(_day(11, weeks=2), ["closed", "active", "future", "future"])
        self.assertEqual((current["name"], current["week"]), ("Test Sprint 11", 3))
        self.assertEqual(cr.sprint_mode(current["week"]), "mid-sprint")
        self.assertEqual((window["phase"], sections), ("not started", []))

    def test_no_next_sprint_keeps_current_behaviour(self):
        current, window, _ = _report_context(_day(12), ["closed", "active"])
        self.assertEqual((current["name"], current["week"]), ("Test Sprint 11", 3))
        self.assertEqual(window["phase"], "not started")

    def test_next_sprint_already_started_is_unchanged(self):
        current, window, _ = _report_context(_day(12), ["closed", "closed", "active", "future"])
        self.assertEqual((current["name"], current["week"]), ("Test Sprint 12", 1))
        self.assertEqual(window["phase"], "refinement")

    def test_next_sprint_starting_later_is_not_used(self):
        board = _board(["closed", "active", "future"])
        later = dt.date.fromisoformat(board[2]["start_date"]) + dt.timedelta(days=7)
        board[2]["start_date"] = later.isoformat()
        board[2]["end_date"] = (later + dt.timedelta(weeks=3)).isoformat()
        ctx = sc.build_context({"today": _day(12), "board_id": BOARD, "sprints": board, "schedules": {}})
        self.assertEqual(ctx["current_sprint"]["name"], "Test Sprint 11")


if __name__ == "__main__":
    unittest.main()
