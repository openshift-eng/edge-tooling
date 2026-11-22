"""Feature health by release phase (shared _scrum_rules + chai_report formatting).

All keys, names, sprints, releases and dates are synthetic (public repository).
Run this file directly to print the rendered phase cases and the worst case.
"""

import copy
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _scrum_rules as rules  # noqa: E402
import chai_report as cr  # noqa: E402
import scrum_context as sc  # noqa: E402
from tests.test_chai_epic_fields import MENTIONS, _mix  # noqa: E402

SM = "Sam Scrum"
BOARD = 1000
START = dt.date(2030, 1, 7)  # Monday: Test Sprint 10 starts; 3-week sprints


def _board(active):
    out = []
    for i in range(10):
        n = 10 + i
        start = START + dt.timedelta(weeks=3 * i)
        state = "closed" if n < active else "active" if n == active else "future"
        out.append({"id": str(500 + i), "name": f"Test Sprint {n}", "state": state, "originBoardId": BOARD,
                    "start_date": start.isoformat(), "end_date": (start + dt.timedelta(weeks=3)).isoformat()})
    return out


def _day(sprint, week=1):
    return (START + dt.timedelta(weeks=3 * (sprint - 10) + week - 1, days=1)).isoformat()


# Release 9.2: refinement = Test Sprint 12, pencils down = end of 15, branch cut in 16.
SCHEDULES = {"9.2": [
    {"name": "9.2 Development Branch opens", "date": (START + dt.timedelta(weeks=6)).isoformat()},  # Sprint 12 start
    {"name": "Pencils Down / Stabilization", "date": (START + dt.timedelta(weeks=18)).isoformat()},  # end of 15
    {"name": "Master Branch Open for 9.3", "date": (START + dt.timedelta(weeks=18, days=3)).isoformat()}]}  # in 16

FEATURES = {"features": [
    {"type": "Feature", "key": "FEAT-1", "status": "Refinement", "sme": "Alex Example", "qa_contact": "Quinn Quality",
     "docs_approver": "Dana Docs", "size": "M", "labels": []},
    {"type": "Feature", "key": "FEAT-2", "status": "New", "sme": "None", "qa_contact": "None", "docs_approver": "None",
     "size": "Unsized", "labels": []},
    {"type": "Feature", "key": "FEAT-3", "status": "In Progress", "sme": "Morgan Manager", "qa_contact": "Quinn Quality",
     "docs_approver": "Dana Docs", "size": "L", "labels": ["Parked"]},
    {"type": "Feature", "key": "FEAT-4", "status": "New", "sme": "Alex Example", "qa_contact": "unassigned_jira",
     "docs_approver": "Dana Docs", "size": "S", "labels": []},
    {"type": "Feature", "key": "FEAT-5", "status": "Closed", "sme": "Robin Lead", "qa_contact": "None", "docs_approver": "None",
     "size": "Unsized", "labels": []},
    {"type": "Feature", "key": "FEAT-6", "status": "Refinement", "sme": "Morgan Manager", "qa_contact": "Quinn Quality",
     "docs_approver": "Dana Docs", "size": "M", "labels": []},
    # Initiatives are out of scope for the chai report's feature checks.
    {"type": "Initiative", "key": "INIT-7", "status": "New", "sme": "Morgan Manager", "qa_contact": "None",
     "docs_approver": "None", "size": "Unsized", "labels": ["Blocked"]},
]}
EPICS = {"feature_to_epics": {"FEAT-1": ["EPIC-11", "EPIC-12"], "FEAT-2": ["EPIC-21"], "FEAT-3": ["EPIC-31"],
                              "FEAT-6": ["EPIC-61"]},
         "epics": [{"key": "EPIC-11", "assignee": "Alex Example"}, {"key": "EPIC-12", "assignee": "Unassigned"},
                   {"key": "EPIC-21", "assignee": "Unassigned"}, {"key": "EPIC-31", "assignee": "Morgan Manager"},
                   {"key": "EPIC-61", "assignee": "Morgan Manager"}]}
COUNTS = {"EPIC-11": 3, "EPIC-12": 0, "EPIC-21": 2, "EPIC-31": 4, "EPIC-61": 2}
_SPIKE = {"spike_key": None, "spike_status": "Missing", "spike_missing": True, "spike_on_epic": False,
          "epics_appear_refined": False, "spike_in_ref_sprint": False, "spike_overdue": False,
          "test_plan_check_skipped": False, "test_plan_spike_missing": False}


def _spikes(closed):
    open_spike = dict(_SPIKE, spike_key="SPK-2", spike_status="In Progress", spike_missing=False,
                      spike_overdue=closed)
    return {"spike_map": {
        "FEAT-1": dict(_SPIKE, test_plan_spike_missing=True),
        "FEAT-2": dict(open_spike, test_plan_check_skipped=True),
        "FEAT-3": dict(_SPIKE, spike_key="SPK-3", spike_status="Closed", spike_missing=False),
        "FEAT-4": dict(_SPIKE, test_plan_check_skipped=True),
        "FEAT-6": dict(open_spike, spike_key="SPK-6", spike_in_ref_sprint=True, spike_overdue=closed),
    }}


STORIES = {"stories": (
    [{"key": f"ST-11{n}", "epic_key": "EPIC-11", "sp": 5, "status": "Closed" if n == 1 else "In Progress"}
     for n in range(1, 4)]
    + [{"key": f"ST-21{n}", "epic_key": "EPIC-21", "sp": 3, "status": "Closed"} for n in range(1, 3)]
    + [{"key": f"ST-31{n}", "epic_key": "EPIC-31", "sp": 2, "status": "New"} for n in range(1, 5)]
    + [{"key": f"ST-61{n}", "epic_key": "EPIC-61", "sp": 5, "status": "Closed" if n == 1 else "Code Review"}
       for n in range(1, 3)])}
LINKS = {"FEAT-3": ["OCPBUGS-1"], "EPIC-61": ["TEST-900"]}
CHECKS = {"meta": {"total_remaining_sp": 120, "total_capacity_sp": 90, "capacity_basis": "velocity",
                   "team_velocity": 45.0, "remaining_sprints": 3, "total_unpointed_stories": 2},
          "cut_line": [{"feature_key": "FEAT-6", "sme": "Morgan Manager", "fits": "partial"}],
          "process_gaps": {"roster_drift": []}}


def _sprint_issues():
    def issue(key, owner, **kw):
        base = {"key": key, "type": "Story", "sp": 3, "assignee_display": owner, "epic_key": "EPIC-11",
                "status": "In Progress", "blocked_by": [], "labels": []}
        base.update(kw)
        return base
    issues = [issue("TEST-1", "Alex Example", blocked_by=["TEST-9"]),
              issue("TEST-2", "Alex Example", epic_key="No Epic"),
              issue("TEST-3", "Quinn Quality", sp=0, type="Bug"),
              issue("TEST-4", "Unassigned", labels=["Parked"]),
              issue("TEST-5", "Morgan Manager", status="Closed", sp=8),
              issue("TEST-6", "Morgan Manager", labels=["Blocked"])]
    return {"issues": issues, "total_sp": 23, "total_done_sp": 8, "total_remaining_sp": 15}


def render_case(today, active, sprint_issues=None, features=FEATURES, epics=EPICS, counts=COUNTS,
                stories=STORIES, links=LINKS, checks=CHECKS, epic_gaps="mix", team_owner=SM):
    """The full weekly report for a synthetic board on ``today`` (phase from scrum-context)."""
    base = {"today": today, "board_id": BOARD, "sprints": _board(active), "schedules": SCHEDULES}
    ctx = sc.build_context(base)
    window = sc.build_context(dict(base, release="9.2"))["release"]
    sections, missing = cr.select_sections(ctx, [window])
    cur = ctx["current_sprint"]
    mode = cr.sprint_mode(cur["week"])
    sprint = cr.sprint_health(sprint_issues or _sprint_issues(), cur, today, mode, velocity=24, availability=0.9)
    computed = []
    for sec in sections:
        if sec["kind"] == cr.REFINEMENT:
            computed.append(cr.refinement_gaps(features, _spikes(False), sec, False, epics=epics,
                                               epic_issue_counts=counts, week=cur["week"]))
        else:
            computed.append(cr.release_health(checks, sec, None, features=features, epics=epics,
                                              spikes=_spikes(True), stories=stories, links=links,
                                              current_sprint_number=cur["number"]))
    gaps = _mix()[2] if epic_gaps == "mix" else epic_gaps
    return cr.build_report(sprint, computed, missing, MENTIONS, team_owner=team_owner, epic_gaps=gaps)


CASES = {
    "refinement, week 1": (_day(12, 1), 12),
    "refinement, week 2": (_day(12, 2), 12),
    "refinement, week 3 (sprint-end view)": (_day(12, 3), 12),
    "after refinement (first development sprint)": (_day(13, 1), 13),
    "development": (_day(14, 2), 14),
    "pencils down": (_day(15, 2), 15),
    "between releases": (_day(11, 2), 11),
}


def worst_case():
    """Many features with many problems, many owners, many sprint blocks, many epic flags."""
    owners = [f"Owner {n:02d}" for n in range(1, 21)]
    features = {"features": [{"key": f"FEAT-{100 + n}", "status": "New", "sme": owners[n % 20],
                              "qa_contact": "None", "docs_approver": "None", "size": "Unsized",
                              "labels": ["Blocked", "Parked"]} for n in range(40)]}
    epics = {"feature_to_epics": {f["key"]: [f"EPIC-{1000 + n * 3 + k}" for k in range(3)]
                                  for n, f in enumerate(features["features"])},
             "epics": [{"key": f"EPIC-{1000 + n}", "assignee": "Unassigned"} for n in range(120)]}
    counts = {e["key"]: 0 for e in epics["epics"]}
    spikes = {"spike_map": {f["key"]: dict(_SPIKE, test_plan_spike_missing=True) for f in features["features"]}}
    links = {f"EPIC-{1000 + n}": [f"BLOCK-{n}", f"BLOCK-{n + 500}"] for n in range(120)}
    links.update({f["key"]: ["BLOCK-1", "BLOCK-2"] for f in features["features"]})
    issues = [{"key": f"TEST-{n}", "type": "Story", "sp": 0, "assignee_display": owners[n % 20],
               "epic_key": "No Epic", "status": "In Progress", "blocked_by": [f"TEST-{n + 900}"],
               "labels": ["Blocked", "Parked"]} for n in range(80)]
    sprint_issues = {"issues": issues, "total_sp": 400, "total_done_sp": 10, "total_remaining_sp": 390}
    epic_gaps = {"kind": cr.EPIC_FIELDS, "epics_checked": 120, "flagged": 100,
                 "checks": {"no_target_version": [{}] * 100, "no_qa_contact": [{}] * 100,
                            "target_version_differs": [{}] * 50, "parent_backlog_or_closed": [{}] * 50,
                            "no_assignee": []},
                 "epics": [{"key": f"OCPEDGE-{n}", "owner": owners[n % 20], "new": None,
                            "problems": ["no Target Version", "no QA Contact", "Target Version ≠ feature",
                                         "feature closed"]} for n in range(100)],
                 "flagged_keys": [], "risks": [], "actions": [], "left_out": []}
    checks = dict(CHECKS, cut_line=[{"feature_key": f["key"], "sme": f["sme"], "fits": "over"}
                                    for f in features["features"]])
    return {"features": features, "epics": epics, "counts": counts, "spikes": spikes, "links": links,
            "sprint_issues": sprint_issues, "epic_gaps": epic_gaps, "checks": checks}


def render_worst(today, active):
    w = worst_case()
    global _spikes
    saved = _spikes
    _spikes = lambda closed: {"spike_map": {k: dict(v, spike_overdue=closed, spike_missing=not closed,  # noqa: E731
                                                    spike_status="Missing" if not closed else "In Progress")
                                            for k, v in w["spikes"]["spike_map"].items()}}
    try:
        return render_case(today, active, sprint_issues=w["sprint_issues"], features=w["features"],
                           epics=w["epics"], counts=w["counts"], stories={"stories": []}, links=w["links"],
                           checks=w["checks"], epic_gaps=w["epic_gaps"])
    finally:
        _spikes = saved


def _adapt_parents():
    from tests.test_chai_epic_fields import PARENTS, PARENTS_JQL, _adapt
    return _adapt(PARENTS, PARENTS_JQL, "parents")


def budget_problems(report):
    """Noise-budget violations of a rendered report (empty list = within budget)."""
    problems = []
    if len(report["top_post"]) != 3:
        problems.append("top post is not 3 lines")
    if len(report["replies"]) > 3:
        problems.append(f"{len(report['replies'])} replies")
    total = len(report["top_post"])
    for n, reply in enumerate(report["replies"]):
        lines = reply.splitlines()
        total += len(lines)
        owner_lines = [ln for ln in lines if ln.startswith("• ")]
        if len(owner_lines) > cr.OWNER_LINE_LIMIT:
            problems.append(f"reply {n + 1}: {len(owner_lines)} owner lines")
        extra = [ln for ln in lines[1:] if not ln.startswith("• ")]
        if extra and (extra != [lines[-1]] or not lines[-1].startswith("Left out: ")):
            problems.append(f"reply {n + 1}: unexpected line {extra[0]!r}")
        if any(ln.startswith("Left out: ") for ln in lines) and n != len(report["replies"]) - 1:
            problems.append("Left out line not in the last reply")
        problems += [f"reply {n + 1}: line of {len(ln)} chars" for ln in owner_lines if len(ln) > cr.LINE_CHARS]
    if total > 40:
        problems.append(f"{total} lines in all")
    return problems


def _labels(findings, key):
    return [f["label"] for f in findings if f["key"] == key]


class RefinementChecksTest(unittest.TestCase):
    def setUp(self):
        self.out = rules.refinement_findings(FEATURES, EPICS, _spikes(False), COUNTS)
        self.f = self.out["findings"]

    def test_every_refinement_label(self):
        self.assertEqual(_labels(self.f, "FEAT-1"), ["epic EPIC-12 has no owner", "empty epic EPIC-12",
                                                    "no refinement spike", "test plan spike missing"])
        self.assertEqual(_labels(self.f, "FEAT-2"), ["no SME"])  # nothing else until an SME is set
        self.assertEqual(_labels(self.f, "FEAT-3"), [])  # Parked is not a refinement check; spike Closed
        # unassigned_jira counts as no QA Contact (as in the epic section).
        self.assertEqual(_labels(self.f, "FEAT-4"), ["no QA Contact", "no epics", "no refinement spike"])
        self.assertEqual(_labels(self.f, "FEAT-6"), [])  # spike open in the refinement sprint: in progress

    def test_owners(self):
        owners = {(f["key"], f["label"]): f["owner"] for f in self.f}
        self.assertEqual(owners[("FEAT-1", "no refinement spike")], "Alex Example")  # SME
        self.assertEqual(owners[("FEAT-1", "test plan spike missing")], "Quinn Quality")  # QE Lead
        self.assertIsNone(owners[("FEAT-2", "no SME")])  # collected on the leads' line

    def test_counts_and_closed_features(self):
        self.assertEqual((self.out["checked"], self.out["clean"]), (6, 2))  # FEAT-5 Closed: not checked
        self.assertNotIn("FEAT-5", {f["key"] for f in self.f})

    def test_features_only(self):
        f = rules.refinement_findings(FEATURES, EPICS, _spikes(False), COUNTS, feature_types=("Feature",))
        self.assertEqual(f["checked"], 5)
        self.assertNotIn("INIT-7", {x["key"] for x in f["findings"]})
        self.assertIn("INIT-7", {x["key"] for x in self.f})  # without the scope, the shared rules check it

    def test_after_the_refinement_sprint_closes_open_spikes_are_overdue(self):
        f = rules.refinement_findings(FEATURES, EPICS, _spikes(True), COUNTS, refinement_sprint_closed=True)["findings"]
        self.assertEqual(_labels(f, "FEAT-2"), ["no SME"])
        self.assertIn("refinement overdue", _labels(f, "FEAT-6"))

    def test_unavailable_data_is_left_out_not_guessed(self):
        out = rules.refinement_findings(FEATURES, None, _spikes(False), None)
        self.assertNotIn("no epics", [f["label"] for f in out["findings"]])
        sec = {"release": "9.2", "phase": "refinement", "window": {}}
        whats = [x["what"] for x in cr.refinement_gaps(FEATURES, _spikes(False), sec)["left_out"]]
        self.assertEqual(whats, ["9.2 epic checks"])
        whats = [x["what"] for x in cr.refinement_gaps(FEATURES, _spikes(False), sec, epics=EPICS)["left_out"]]
        self.assertEqual(whats, ["9.2 empty-epic check"])

    def test_malformed_feature_is_left_out(self):
        bad = {"features": [None, {"no": "key"}, FEATURES["features"][0]]}
        out = rules.refinement_findings(bad, EPICS, _spikes(False), COUNTS)
        self.assertEqual(out["checked"], 1)  # only the readable feature counts as checked
        self.assertEqual(len(out["left_out"]), 2)
        self.assertIn("no refinement spike", _labels(out["findings"], "FEAT-1"))
        dev = rules.development_findings(bad, EPICS, _spikes(True), STORIES, LINKS, 50)
        self.assertEqual((dev["checked"], len(dev["left_out"])), (1, 2))


class DevelopmentChecksTest(unittest.TestCase):
    def _out(self, expected):
        return rules.development_findings(FEATURES, EPICS, _spikes(True), STORIES, LINKS, expected, ["FEAT-6"])

    def test_every_development_label(self):
        f = self._out(50)["findings"]
        self.assertEqual(_labels(f, "FEAT-1"), ["behind schedule: 33% done vs 50% expected", "epic EPIC-12 has no owner"])
        self.assertEqual(_labels(f, "FEAT-2"), ["no SME"])
        self.assertEqual(_labels(f, "FEAT-3"), ["behind schedule: 0% done vs 50% expected", "blocked by OCPBUGS-1",
                                               "Parked"])
        self.assertEqual(_labels(f, "FEAT-6"), ["refinement overdue", "epic EPIC-61 blocked by TEST-900",
                                               "below the cut line"])

    def test_blocked_label(self):
        features = copy.deepcopy(FEATURES)
        features["features"][0]["labels"] = ["Blocked"]
        f = rules.development_findings(features, EPICS, None, None, None, None)["findings"]
        self.assertIn("Blocked label", _labels(f, "FEAT-1"))

    def test_schedule_formula(self):
        self.assertEqual(rules.expected_pct(12, 16, 13), 0)  # first development sprint: nothing finished
        self.assertEqual(rules.expected_pct(12, 16, 14), 25)
        self.assertEqual(rules.expected_pct(12, 16, 15), 50)
        self.assertEqual(rules.expected_pct(12, 16, 20), 100)
        self.assertIsNone(rules.expected_pct(None, 16, 14))
        self.assertEqual(round(rules.done_pct(["EPIC-11"], STORIES["stories"])), 33)  # 5 of 15 SP closed
        self.assertEqual(rules.done_pct(["EPIC-12"], STORIES["stories"]), None)
        unpointed = [{"epic_key": "E", "sp": 0, "status": "Closed", "key": "A-1"},
                     {"epic_key": "E", "sp": 0, "status": "New", "key": "A-2"}]
        self.assertEqual(rules.done_pct(["E"], unpointed), 50)  # by issue count when nothing is pointed

    def test_expected_pct_counts_board_sprints_not_numbers(self):
        # Board lists 12 (refinement), 14, 15 (branch cut): 14 is the first development sprint.
        self.assertEqual(rules.expected_pct(12, 15, 14, [14, 15]), 0)
        self.assertEqual(rules.expected_pct(12, 15, 15, [14, 15]), 50)
        self.assertEqual(rules.expected_pct(12, 16, 14, [13, 14, 15, 16]), 25)  # consecutive: unchanged
        window = {"refinement_sprint": {"number": 12}, "branch_cut_sprint": {"number": 15},
                  "development_window": [{"number": 14}, {"number": 15}]}  # as scrum-context gives it
        sec = {"release": "9.2", "phase": "development", "window": window}
        out = cr.release_health(CHECKS, sec, None, features={"features": []}, epics=EPICS, spikes=None,
                                stories=STORIES, links={}, current_sprint_number=14)
        self.assertEqual(out["expected_pct"], 0)

    def test_expected_is_compared_unrounded(self):
        exact = rules.expected_pct(12, 15, 15, [13, 14, 15], exact=True)  # 2 of 3 finished
        self.assertAlmostEqual(exact, 200 / 3)
        self.assertEqual(rules.expected_pct(12, 15, 15, [13, 14, 15]), 67)  # display only
        feat = {"features": [FEATURES["features"][0]]}
        at = [{"key": "S-1", "epic_key": "EPIC-11", "sp": 5, "status": "Closed"},
              {"key": "S-2", "epic_key": "EPIC-11", "sp": 5, "status": "New"}]  # 50% = 75% of 66.67%
        f = rules.development_findings(feat, EPICS, None, at, None, exact)["findings"]
        self.assertFalse(any(x.startswith("behind") for x in _labels(f, "FEAT-1")))
        below = [dict(at[0], sp=4), dict(at[1], sp=6)]  # 40%
        f = rules.development_findings(feat, EPICS, None, below, None, exact)["findings"]
        self.assertIn("behind schedule: 40% done vs 67% expected", _labels(f, "FEAT-1"))

    def test_development_window_comes_from_the_board(self):
        def board(numbers):  # board sprints with the given numbers, three weeks apart
            out = []
            for i, n in enumerate(numbers):
                start = START + dt.timedelta(weeks=3 * i)
                out.append({"id": str(700 + i), "name": f"Test Sprint {n}", "state": "active" if i == 0 else "future",
                            "originBoardId": BOARD, "start_date": start.isoformat(),
                            "end_date": (start + dt.timedelta(weeks=3)).isoformat()})
            return out

        def window(numbers, pencils_week=None, cut_week=None):
            sched = [{"name": "9.2 Development Branch opens", "date": START.isoformat()}]
            if pencils_week is not None:
                sched.append({"name": "Pencils Down / Stabilization",
                              "date": (START + dt.timedelta(weeks=pencils_week)).isoformat()})
            sched.append({"name": "Master Branch Open for 9.3",
                          "date": (START + dt.timedelta(weeks=cut_week, days=3)).isoformat()})
            return sc.build_context({"today": START.isoformat(), "board_id": BOARD, "sprints": board(numbers),
                                     "schedules": {"9.2": sched}, "release": "9.2"})["release"]

        # Consecutive board, a sprint between pencils down (end of 14) and branch cut (17).
        w = window([12, 13, 14, 15, 16, 17], pencils_week=9, cut_week=15)
        self.assertEqual([s["number"] for s in w["development_window"]], [13, 14, 15, 16, 17])
        # Skipped number after pencils down: no Sprint 15 is invented.
        w = window([12, 13, 14, 16, 17], pencils_week=9, cut_week=12)
        self.assertEqual([s["number"] for s in w["development_window"]], [13, 14, 16, 17])
        sec = {"release": "9.2", "phase": "development", "window": w}
        self.assertEqual(cr._window_sprint_numbers(sec), [13, 14, 16, 17])
        self.assertEqual(rules.expected_pct(12, 17, 16, cr._window_sprint_numbers(sec)), 50)  # not 60%
        # No pencils-down milestone: the window still runs from refinement to branch cut.
        w = window([12, 13, 14, 15], cut_week=9)
        self.assertEqual([s["number"] for s in w["development_window"]], [13, 14, 15])
        self.assertEqual(rules.expected_pct(12, 15, 14, cr._window_sprint_numbers(
            {"window": w})), 33)

    def test_exact_tie_is_not_behind_despite_floating_point(self):
        exact = rules.expected_pct(12, 18, 18, [13, 14, 15, 16, 17, 18], exact=True)  # 5 of 6
        self.assertGreater(exact * rules.BEHIND_RATIO, 62.5)  # floating point lands above 62.5
        feat = {"features": [FEATURES["features"][0]]}
        tie = [{"key": "S-1", "epic_key": "EPIC-11", "sp": 5, "status": "Closed"},
               {"key": "S-2", "epic_key": "EPIC-11", "sp": 3, "status": "New"}]  # 5 of 8 = 62.5%
        f = rules.development_findings(feat, EPICS, None, tie, None, exact)["findings"]
        self.assertFalse(any(x.startswith("behind") for x in _labels(f, "FEAT-1")))
        below = [dict(tie[0], sp=4), dict(tie[1], sp=4)]  # 50%
        f = rules.development_findings(feat, EPICS, None, below, None, exact)["findings"]
        self.assertIn("behind schedule: 50% done vs 83% expected", _labels(f, "FEAT-1"))

    def test_unreadable_block_row_is_a_link_problem(self):
        import chai_jira_adapter as ad
        text = ("Linked Jira Issues:\n- is blocked by: TEST-9 (Blocks) | status: New | https://x\n"
                "- is blocked by: TES\n- relates to: weird line\n")
        adapted = {"issues": [{"key": "TEST-1", "issuelinks": []}]}
        problems = ad.attach_issue_links(adapted, {"TEST-1": text})
        self.assertEqual(problems, ["TEST-1: 1 block link line(s) could not be read"])
        self.assertEqual(adapted["issues"][0]["issuelinks"][0]["inward_issue"]["key"], "TEST-9")  # kept
        clean = {"issues": [{"key": "TEST-2", "issuelinks": []}]}
        self.assertEqual(ad.attach_issue_links(clean, {"TEST-2": "Linked Jira Issues:\n"}), [])  # empty is fine

    def test_behind_compares_the_exact_percentage(self):
        stories = [{"key": "S-1", "epic_key": "EPIC-11", "sp": 8, "status": "Closed"},
                   {"key": "S-2", "epic_key": "EPIC-11", "sp": 35, "status": "New"}]  # 8/43 = 18.6%
        feat = {"features": [FEATURES["features"][0]]}
        f = rules.development_findings(feat, EPICS, None, stories, None, 25)["findings"]
        self.assertIn("behind schedule: 19% done vs 25% expected", _labels(f, "FEAT-1"))  # 18.6 < 18.75
        tie = [{"key": "S-1", "epic_key": "EPIC-11", "sp": 3, "status": "Closed"},
               {"key": "S-2", "epic_key": "EPIC-11", "sp": 13, "status": "New"}]  # 3/16 = 18.75%
        f = rules.development_findings(feat, EPICS, None, tie, None, 25)["findings"]
        self.assertFalse(any(x.startswith("behind") for x in _labels(f, "FEAT-1")))  # a tie is not behind

    def test_parked_is_a_red_block_like_blocked(self):
        feat = copy.deepcopy({"features": [FEATURES["features"][5]]})
        feat["features"][0]["labels"] = ["Parked"]
        out = cr.release_health(CHECKS, {"release": "9.2", "phase": "development", "window": {}}, None,
                                features=feat, epics=EPICS, spikes=None, stories=None, links={})
        risks = {r["text"]: r["severity"] for r in out["risks"]}
        self.assertEqual(risks.get("9.2: 1 feature(s) blocked"), cr.RED)
        self.assertFalse(any("other gaps" in t for t in risks if "FEAT-6" in t))
        # Behind = below 75% of expected: 33% vs 25% expected is fine; vs 50% it is behind.
        self.assertNotIn("FEAT-1", {f["key"] for f in self._out(25)["findings"] if f["check"] == "behind_schedule"})

    def test_no_stale_check_anywhere(self):
        self.assertFalse(hasattr(rules, "LABEL_STALE"))
        issues = [{"key": "TEST-1", "status": "In Progress", "assignee_display": "A", "sp": 1, "epic_key": "E",
                   "stale": True, "updated": "2000-01-01", "labels": [], "blocked_by": []}]
        self.assertEqual(rules.sprint_findings(issues, "mid-sprint"), [])


class SprintEndTest(unittest.TestCase):
    def test_week_3_adds_delivered_and_carry_over(self):
        start = START + dt.timedelta(weeks=6)
        sprint = {"name": "Test Sprint 12", "start": start.isoformat(),
                  "end": (start + dt.timedelta(weeks=3)).isoformat(), "week": 3}
        out = cr.sprint_health(_sprint_issues(), sprint, _day(12, 3), "mid-sprint")
        self.assertEqual(out["sprint_end"], {"delivered_sp": 8, "total_sp": 23, "delivered_pct": 35,
                                             "carry_over_issues": 5, "carry_over_sp": 12})
        self.assertIn("carry-over", [f["label"] for f in out["items"] if f["key"] == "TEST-2"])
        self.assertNotIn("TEST-5", {f["key"] for f in out["items"]})  # Closed: delivered
        week2 = cr.sprint_health(_sprint_issues(), dict(sprint, week=2), _day(12, 2), "mid-sprint")
        self.assertNotIn("sprint_end", week2)


class PhaseSelectionTest(unittest.TestCase):
    def _report(self, case):
        return render_case(*CASES[case])

    def test_refinement_weeks(self):
        for case, mode in (("refinement, week 1", "capacity"), ("refinement, week 2", "mid-sprint"),
                           ("refinement, week 3 (sprint-end view)", "mid-sprint")):
            with self.subTest(case=case):
                r = self._report(case)
                self.assertIn(f"Test Sprint 12 (week {case[-1] if case[-1].isdigit() else 3}, {mode}); 9.2 refinement",
                              r["top_post"][0])
                self.assertTrue(r["replies"][1].startswith("*9.2 refinement* (refinement sprint 12)"))
        self.assertIn("delivered 8 of 23 SP (35%)", self._report("refinement, week 3 (sprint-end view)")["replies"][0])

    def test_after_refinement_and_development(self):
        r = self._report("after refinement (first development sprint)")
        self.assertIn("9.2 development", r["top_post"][0])
        self.assertIn("refinement overdue", r["replies"][1])
        self.assertNotIn("behind schedule", r["replies"][1])  # 0% expected in the first development sprint
        dev = self._report("development")
        self.assertIn("behind schedule: 0% done vs 25% expected", dev["replies"][1])
        pd = self._report("pencils down")
        self.assertIn("9.2 pencils-down sprint", pd["top_post"][0])
        self.assertIn("behind schedule: 33% done vs 50% expected", pd["replies"][1])

    def test_between_releases(self):
        r = self._report("between releases")
        self.assertEqual(len(r["replies"]), 2)  # sprint and epic fields only
        self.assertFalse(any("release health" in x or "refinement*" in x for x in r["replies"]))
        self.assertIn("Release section: no release in progress", r["replies"][-1].splitlines()[-1])


class FormatTest(unittest.TestCase):
    def test_several_labels_on_one_line(self):
        release = render_case(*CASES["refinement, week 2"])["replies"][1]
        self.assertIn("• <@U000TEST1>: no refinement spike for FEAT-1, 4; FEAT-1 (epic EPIC-12 has no owner, "
                      "empty epic EPIC-12); FEAT-4 (no QA Contact, no epics)", release)
        self.assertIn("• Quinn Quality: FEAT-1 (test plan spike missing)", release)
        self.assertNotIn("FEAT-6", release)  # fine: only counted in "✓ N ready"
        self.assertIn("✓ 2 ready", release.splitlines()[0])

    def test_no_sme_is_one_leads_line_tagging_the_scrum_master(self):
        release = render_case(*CASES["refinement, week 2"])["replies"][1].splitlines()
        self.assertEqual(release[1], "• Leads, assign SMEs (Sam Scrum): 1 feature(s) have no SME: FEAT-2")
        self.assertFalse(any("FEAT-2" in ln for ln in release[2:]))  # its other problems wait for an SME
        features = {"features": [dict(FEATURES["features"][1], key=f"FEAT-{n}") for n in range(200, 260)]}
        line = render_case(*CASES["development"], features=features)["replies"][1].splitlines()[1]
        self.assertLessEqual(len(line), cr.LINE_CHARS)
        self.assertRegex(line, r"^• Leads, assign SMEs \(Sam Scrum\): 60 feature\(s\) have no SME: FEAT-200, 201, .* \(\+\d+\)$")
        none = cr._no_sme_line(["FEAT-1"], None)
        self.assertEqual(none, "• Leads, assign SMEs: 1 feature(s) have no SME: FEAT-1")

    def test_initiatives_are_not_checked(self):
        for case in ("refinement, week 2", "development"):
            with self.subTest(case=case):
                self.assertNotIn("INIT-7", "\n".join(render_case(*CASES[case])["replies"]))

    def test_week_1_refinement_spikes_are_one_count_and_not_the_biggest_risk(self):
        r = render_case(*CASES["refinement, week 1"])
        release = r["replies"][1]
        self.assertIn("• 2 feature(s) need a refinement spike this sprint", release)
        self.assertNotIn("no refinement spike", release)
        sec = {"release": "9.2", "phase": "refinement", "window": {}}
        week1 = cr.refinement_gaps(FEATURES, _spikes(False), sec, epics=EPICS, epic_issue_counts=COUNTS, week=1)
        self.assertFalse(any("refinement spike" in x["text"] for x in week1["risks"]))
        alone = cr.build_report(None, [week1])
        self.assertNotIn("refinement spike", alone["top_post"][1])
        week2 = cr.refinement_gaps(FEATURES, _spikes(False), sec, epics=EPICS, epic_issue_counts=COUNTS, week=2)
        self.assertIn("9.2: 2 feature(s) with a missing or overdue refinement spike", [x["text"] for x in week2["risks"]])
        self.assertEqual(week2["spike_needed"], 0)

    def test_people_line_counts_everyone_tagged_including_epic_fields(self):
        r = render_case(*CASES["development"])
        tagged = set()
        for reply in r["replies"]:
            for line in reply.splitlines():
                if line.startswith("• ") and ": " in line:
                    who = line[2:].split(": ")[0]
                    if who.startswith("Leads, assign SMEs"):
                        who = who[who.index("(") + 1:-1]
                    tagged.add(who)
        shown = {cr.mention(p, MENTIONS) for p in r["people"]}
        self.assertEqual(shown, tagged - {"no owner set"})
        self.assertIn("Morgan Manager", r["people"])  # epic fields owner

    def test_people_line(self):
        r = render_case(*CASES["development"])
        self.assertTrue(r["top_post"][2].endswith("have items: " + ", ".join(cr.mention(p, MENTIONS)
                                                                            for p in r["people"])))
        many = render_worst(*CASES["development"])["top_post"][2]
        self.assertRegex(many, r"^2\d people have items: (.+, ){7}.+, \+\d+ more$")

    def test_shared_labels_are_compressed(self):
        entries = [{"key": f"OCPSTRAT-{n}", "labels": ["test plan spike missing"], "new": False}
                   for n in (3331, 4055, 4057, 4065, 4070, 4071, 4072)]
        self.assertEqual(cr._owner_line("Someone", entries),
                         "• Someone: test plan spike missing for OCPSTRAT-3331, 4055, 4057, 4065, 4070 (+2)")
        mixed = [{"key": "A-1", "labels": ["x", "y"], "new": False}, {"key": "A-2", "labels": ["x"], "new": True},
                 {"key": "B-3", "labels": ["x", "z"], "new": False}]
        self.assertEqual(cr._owner_line("S", mixed), "• S: x for A-1, 🆕 2, B-3; A-1 (y); B-3 (z)")

    def test_owner_line_is_cut_at_200_chars(self):
        entries = [{"key": f"FEAT-{n}", "labels": [f"epic EPIC-{n}{k} has no owner" for k in range(3)], "new": False}
                   for n in range(10)]
        line = cr._owner_line("Someone", entries)
        self.assertLessEqual(len(line), cr.LINE_CHARS)
        self.assertRegex(line, r" \+\d+ more problem\(s\)$")
        long_one = [{"type": "Feature", "key": "FEAT-1", "labels": [f"epic EPIC-{n} has no owner" for n in range(20)], "new": False}]
        line = cr._owner_line("Someone", long_one)
        self.assertLessEqual(len(line), cr.LINE_CHARS)
        self.assertTrue(line.startswith("• Someone: FEAT-1 (epic EPIC-0 has no owner"))

    def test_epic_owner_problem_not_repeated_across_replies(self):
        gaps = copy.deepcopy(_mix()[2])
        gaps["epics"].append({"key": "EPIC-12", "owner": None, "problems": ["no assignee"], "new": None})
        gaps["flagged"] += 1
        r = render_case(*CASES["refinement, week 1"], epic_gaps=gaps)
        self.assertNotIn("epic EPIC-12 has no owner", r["replies"][1])
        self.assertIn("no assignee for EPIC-12, TEST-102, 103, 108", r["replies"][2])

    def test_failed_epic_run_shows_no_clean_header(self):
        failed = cr.epic_field_gaps({"issues": [], "fetch": {"complete": False, "problems": ["cut off"]}}, [])
        r = render_case(*CASES["between releases"], epic_gaps=failed)
        text = "\n".join(r["replies"])
        self.assertNotIn("✓ all", text)
        self.assertNotIn("*Epic fields*", text)
        self.assertIn("Epic Target Version and QA Contact checks: open-epic fetch incomplete", r["replies"][-1])

    def test_unread_links_are_not_claimed_as_unblocked(self):
        sec = {"release": "9.2", "phase": "development", "window": {}}
        none = cr.release_health(CHECKS, sec, None, features=FEATURES, epics=EPICS, spikes=None, stories=None,
                                 links=None)
        self.assertIn("on track (blocks not checked)", cr._section_header(none))
        self.assertIn("9.2 feature and epic blocks", [x["what"] for x in none["left_out"]])
        some = cr.release_health(CHECKS, sec, None, features=FEATURES, epics=EPICS, spikes=None, stories=None,
                                 links={"FEAT-3": ["OCPBUGS-1"]}, links_unread=["EPIC-61"])
        self.assertIn("on track (blocks not checked for 1)", cr._section_header(some))
        self.assertIn("blocked by OCPBUGS-1", _labels(some["items"], "FEAT-3"))  # confirmed blocker kept
        self.assertIn("9.2 blocks of EPIC-61", [x["what"] for x in some["left_out"]])
        start = START + dt.timedelta(weeks=6)
        sprint = {"name": "Test Sprint 12", "start": start.isoformat(),
                  "end": (start + dt.timedelta(weeks=3)).isoformat(), "week": 2}
        out = cr.sprint_health(_sprint_issues(), sprint, _day(12, 2), "mid-sprint", links_complete=False)
        head = cr.build_report(out, [])["replies"][0].splitlines()[0]
        self.assertIn("blocked-by links not checked", head)
        self.assertNotIn("not blocked", head)

    def test_epic_complete_count_excludes_unread_parents(self):
        epics = {"fetch": {"complete": True}, "issues": [
            {"key": "TEST-1", "status": {"name": "In Progress"}, "parent": {"key": "FEAT-9"},
             "assignee": {"display_name": "Alex Example"}, "customfield_10855": [{"name": "openshift-9.1"}],
             "customfield_10470": {"display_name": "Quinn Quality"}},  # nothing flagged, parent unread
            {"key": "TEST-2", "status": {"name": "In Progress"}, "parent": {"key": "FEAT-1"},
             "assignee": {"display_name": "Alex Example"}}]}  # flagged
        gaps = cr.epic_field_gaps(epics, [{"keys": ["FEAT-1", "FEAT-9"], "result": _adapt_parents()}])
        self.assertEqual((gaps["epics_checked"], gaps["flagged"], gaps["clean"]), (2, 1, 0))
        self.assertIn("✓ 0 complete", cr._epic_header(gaps, []))

    def test_release_keeps_epic_owner_problem_hidden_in_epic_reply(self):
        gaps = copy.deepcopy(_mix()[2])
        for n in range(11):  # eleven owners with five epics each rank ahead of the Scrum Master's four
            gaps["epics"] += [{"key": f"OCPEDGE-{900 + 5 * n + k}", "owner": f"Owner {n:02d}",
                               "problems": ["no QA Contact"], "new": None} for k in range(5)]
        gaps["epics"].append({"key": "EPIC-12", "owner": None, "problems": ["no assignee"], "new": None})
        gaps["flagged"] = len(gaps["epics"])
        r = render_case(*CASES["refinement, week 2"], epic_gaps=gaps)
        self.assertNotIn("EPIC-12", "\n".join(r["replies"][2].splitlines()[:-1]))  # Scrum Master line past the cap
        self.assertIn("epic EPIC-12 has no owner", r["replies"][1])  # so the release reply keeps it

    def test_no_clean_header_when_no_epic_was_checked(self):
        empty = {"kind": cr.EPIC_FIELDS, "epics_checked": 0, "flagged": 0, "complete": True,
                 "checks": {cid: [] for cid, _ in cr.EPIC_CHECKS + (cr.NO_ASSIGNEE,)}, "epics": [],
                 "flagged_keys": [], "risks": [], "actions": [],
                 "left_out": [{"what": "something", "why": "missing"}]}
        self.assertIsNone(cr._epic_header(empty, []))
        r = render_case(*CASES["between releases"], epic_gaps=empty)
        self.assertNotIn("✓ all 0", "\n".join(r["replies"]))
        self.assertIn("something: missing", r["replies"][-1])
        checked = dict(empty, epics_checked=5)
        self.assertEqual(cr._epic_header(checked, []), "*Epic fields* ✓ all 5 active epics complete")

    def test_malformed_epic_omission_changes_nothing_else(self):
        gaps = copy.deepcopy(_mix()[2])
        gaps["left_out"] = [{}]  # unreadable omission, valid findings
        r = render_case(*CASES["development"], epic_gaps=gaps)
        good = render_case(*CASES["development"], epic_gaps=None)
        self.assertEqual(r["people"], good["people"])  # no epic owner counted for a dropped reply
        self.assertEqual(len(r["replies"]), 2)  # the epic reply is dropped
        self.assertEqual(r["replies"][1].splitlines()[:-1], good["replies"][1].splitlines())  # release lines intact
        self.assertEqual(r["replies"][1].splitlines()[-1],
                         "Left out: Epic Target Version and QA Contact checks: result not readable")

    def test_epic_owner_dedupe_uses_the_epic_key(self):
        sec = {"kind": cr.REFINEMENT, "release": "9.2", "phase": "refinement", "refinement_sprint": 12,
               "checked": 1, "clean": 0, "risks": [], "left_out": [], "spike_needed": 0,
               "items": [{"key": "FEAT-1", "owner": "Alex Example", "label": "odd", "check": "epic_no_owner"}]}
        r = cr.build_report(None, [sec], epic_gaps=_mix()[2])  # a one-word label never raises
        self.assertIn("FEAT-1 (odd)", r["replies"][0])

    def test_nothing_to_flag_headers(self):
        clean = {"features": [FEATURES["features"][5]]}
        r = render_case(*CASES["refinement, week 2"], features=clean, epic_gaps=None,
                        sprint_issues={"issues": [], "total_sp": 0, "total_done_sp": 0, "total_remaining_sp": 0})
        self.assertEqual(r["replies"][1], "*9.2 refinement* (refinement sprint 12): 1 feature(s) checked; ✓ 1 ready")


class NoiseBudgetTest(unittest.TestCase):
    def test_every_phase_case_is_within_budget(self):
        for case, args in CASES.items():
            with self.subTest(case=case):
                self.assertEqual(budget_problems(render_case(*args)), [])

    def test_worst_case_is_within_budget_in_every_phase(self):
        for case, args in CASES.items():
            with self.subTest(case=case):
                report = render_worst(*args)
                self.assertEqual(budget_problems(report), [])
                for reply in report["replies"]:
                    self.assertLessEqual(len([ln for ln in reply.splitlines() if ln.startswith("• ")]), 10)

    def test_overflow_owners_are_keys_only_on_the_left_out_line(self):
        report = render_worst(*CASES["development"])
        left = report["replies"][-1].splitlines()[-1]
        self.assertIn("more owner(s) in sprint health: TEST-", left)
        self.assertIn("more owner(s) in release health: FEAT-", left)
        self.assertIn("more owner(s) in epic fields: OCPEDGE-", left)
        self.assertNotIn("Owner 1", left)  # keys only, no names


def _print(title, report):
    msgs = [("Top post", "\n".join(report["top_post"]))] + [(f"Reply {n}", r) for n, r in
                                                            enumerate(report["replies"], 1)]
    total = sum(len(t.splitlines()) for _, t in msgs)
    print(f"\n######## {title}: {len(msgs)} messages, {total} lines")
    for name, text in msgs:
        print(f"--- {name} ({len(text.splitlines())} lines)")
        print(text)


if __name__ == "__main__":
    for case, args in CASES.items():
        _print(case, render_case(*args))
    _print("worst case (development)", render_worst(*CASES["development"]))
