"""weekly_report_files: the round-by-round script posts the same report as the chain test.

Each scenario runs twice on the chain test's synthetic team (``test_weekly_report_chain``):
once as ``WeeklyRun`` (the weekly SKILL's steps with the tools simulated), once through
``weekly_report_files.step`` with a fake coordinator that answers each round's requests from
the same simulated tools, in order, and hands them back as a compact object
(``{"T1": {"tool", "result"}, ...}``). The posted text must be identical, or both runs
must end with the same "not posted" line.
"""

import json
import os
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, BIN)
import chai_jira_adapter as ad  # noqa: E402
import weekly_report_files as wr  # noqa: E402
from tests import test_weekly_report_chain as ch  # noqa: E402
from tests.test_chai_epic_fields import EPICS_JQL  # noqa: E402
from tests.test_chai_jira_adapter import ROSTER_MD  # noqa: E402


class Recording(ch.WeeklyRun):
    """WeeklyRun that remembers every fetch it sent: query predicate → (name, items)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.specs = {}

    def wave(self, label, specs, reads=(), read_step="links"):
        for spec in specs:
            self.specs[spec["jql"]] = (spec["name"], spec["items"])
        return super().wave(label, specs, reads, read_step)


class FakeCoordinator:
    """Answers the script's requests from the chain test's simulated tools."""

    def __init__(self, w, today, specs, sandbox):
        self.w, self.specs = w, specs
        self.run = ch.WeeklyRun(w, today, sandbox)  # its simulated tools, with their own call counts
        self.run.active = None
        self.answerers, self.reads = {}, {}

    def query_jira(self, args):
        predicate = ad._page_query(args["jql"])[0]
        name, items = self.specs[predicate]
        answer = self.answerers.setdefault(name, self.run.answerer(name, items))
        return answer(args["jql"])

    def get_jira_issue(self, args):
        key = args["issue_key"]
        attempt = self.reads[key] = self.reads.get(key, 0) + 1
        name = f"get_jira_issue {key}"
        text = None if name in self.w["fail"] else self.w["issue_links"].get(key)
        flaky = self.w["flaky"].get(name) if attempt == 1 else None
        if text is not None and flaky:
            text = None if flaky == "error" else text.split("Description:")[0]
        return {"error": "tool call failed"} if text is None else text

    def list_jira_board_sprints(self, args):
        if self.w["board_error"]:
            return self.w["board_error"]  # the tool's error text
        sprints = self.run.listing()
        lines = [f"Board {args['board_id']}: {len(sprints)} sprints."]
        for s in sprints:
            lines.append(f"- {s['name']} (id {s['id']}, {s['state']}, start {s['start_date']}, end {s['end_date']})")
        return "\n".join(lines)

    def list_product_pages_entities(self, args):
        try:
            page = self.run.list_entities(**args)
        except RuntimeError as exc:
            return {"error": str(exc)}
        data = [dict(id=e["id"], shortname=f"openshift-{e['release']}") if "release" in e else e
                for e in page["data"]]
        return {"data": data, "cursor": page["cursor"]}

    def browse_product_pages_schedule(self, args):
        release = {900 + n: r for n, r in enumerate(sorted(self.w["schedules"], key=ch.sc.parse_version))}
        return self.w["schedules"].get(release.get(args["entity_id"]), [])

    def github_file_content(self, args):
        return ROSTER_MD

    def resolve_identity(self, args):
        user = ch.SLACK.get(args["identifier"])
        return {"status": "ok", "slack_mention": f"<@{user}>"} if user else {"status": "not_found"}

    def answer(self, request):
        result = getattr(self, request["tool"])(request["args"])
        return result if isinstance(result, str) else json.dumps(result)


def queries(w):
    P = w["projects"]
    return wr.Queries(
        board_id=ch.BOARD, sprint_projects=P["sprint"], epic_projects=", ".join(P["release_epics"]),
        gate_jql=ch.GATE_JQL.replace("project = OCPSTRAT", f"project = {P['features']}"),
        features_jql=ch.features_jql(w, "{release}"),
        bugs_jql=f'project = {P["bugs"]} AND fixVersion in ("{{release}}", "{{release}}.0") AND "Epic Link" is EMPTY',
        field_epics_jql=EPICS_JQL, story_projects=P["stories"], spike_projects="OCPEDGE, USHIFT",
        roster=("example/roster", "team-roster.md"))


def run_script(today, active, change=None, max_rounds=40):
    """The script's outcome and its rounds, answered by the fake coordinator."""
    w = ch.world(active)
    if change:
        change(w)
    with tempfile.TemporaryDirectory() as ref_dir, tempfile.TemporaryDirectory() as fake_dir, \
            tempfile.TemporaryDirectory() as state:
        ref = Recording(w, today, ch.Sandbox(ref_dir))
        report = ref(active)
        expected = ({"status": "incomplete", "text": ref.status} if report is None
                    else {"status": "report", "text": wr.thread_marked(ref.posted[0], ref.posted[1:])})
        w2 = ch.world(active)
        if change:
            change(w2)
        fake = FakeCoordinator(w2, today, {ad._page_query(ad.page_jql(j))[0]: v for j, v in ref.specs.items()},
                               ch.Sandbox(fake_dir))
        fake.run.active = active
        inputs = {"today": today, "engineer_count": 3, "absences": [], "scrum_master": ch.SCRUM_MASTER}
        out, rounds = wr.step(state, inputs=inputs, queries=queries(w2)), []
        while out["status"] == "fetch":
            if len(rounds) >= max_rounds:
                raise AssertionError(f"more than {max_rounds} rounds")
            rounds.append(out["requests"])
            compact = {f"T{n}": {"tool": r["tool"], "result": fake.answer(r)}
                       for n, r in enumerate(out["requests"], 1)}
            out = wr.step(state, compact, queries=queries(w2))
        return expected, out, rounds


class SameReportTest(unittest.TestCase):
    def check(self, today, active, change=None):
        expected, got, rounds = run_script(today, active, change)
        self.assertEqual(expected["status"], got["status"], got["text"][:500])
        self.assertEqual(expected["text"], got["text"])
        return rounds

    def test_week_1_capacity(self):
        self.check(ch.day(14, 1), 14)

    def test_mid_sprint(self):
        self.check(ch.day(14, 2), 14)

    def test_final_week(self):
        self.check(ch.day(14, 3), 14)


if __name__ == "__main__":
    unittest.main()


SKIPPED = []  # scenarios the script does not model: (reason, today, active)


def compare_every_chain_scenario():
    """Run the whole chain suite; every WeeklyRun it makes is also run through the script.
    Returns (mismatches, compared count)."""
    mismatches, compared = [], [0]
    original = ch.weekly_run

    def patched(today, active, change=None, legacy=False):
        run, report = original(today, active, change, legacy)
        if legacy:
            return run, report
        w = ch.world(active)
        if change:
            change(w)
        if w["after_gate"] is not None:
            SKIPPED.append(("world changes during the run (after_gate)", today, active))
        elif w["velocity_store"] is not None:
            SKIPPED.append(("velocity store (not used by the script)", today, active))
        else:
            compared[0] += 1
            try:
                expected, got, _ = run_script(today, active, change)
                if expected != got:
                    mismatches.append((today, active, getattr(change, "__name__", repr(change)), expected, got))
            except Exception as exc:  # noqa: BLE001 - reported as a mismatch
                mismatches.append((today, active, getattr(change, "__name__", repr(change)), "error", repr(exc)))
        return run, report

    ch.weekly_run = patched
    ch._unchanged.cache_clear()  # cached runs would skip the patched weekly_run
    ch.cached_run.cache_clear()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromModule(ch)
        unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
    finally:
        ch.weekly_run = original
        ch._unchanged.cache_clear()
        ch.cached_run.cache_clear()
    return mismatches, compared[0]


class EveryChainScenarioTest(unittest.TestCase):
    def test_every_weekly_run_of_the_chain_suite_posts_the_same(self):
        """Every scenario of the chain suite (sprint weeks, release phases, partial and failed
        input, retries, paging, two releases) posts the same text through the script. Not
        modelled: the optional velocity store (the scheduled report runs without it) and a
        world that changes mid-run."""
        mismatches, compared = compare_every_chain_scenario()
        self.assertGreater(compared, 80)
        self.assertEqual([], [(m[0], m[1], m[2]) for m in mismatches])


class RoundMechanicsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir))
        with open(os.path.join(self.dir, "inputs.json"), "w", encoding="utf-8") as fh:
            json.dump({"today": "2030-01-08"}, fh)

    def first_round(self):
        out = wr.step(self.dir, queries=wr.Queries(roster=("x/y", "r.md")))
        self.assertEqual("fetch", out["status"])
        self.assertEqual(1, out["round"])
        return out["requests"]

    def test_first_round_needs_nothing_else(self):
        tools = [r["tool"] for r in self.first_round()]
        self.assertEqual(["list_jira_board_sprints", "github_file_content", "list_product_pages_entities",
                          "query_jira"], tools)

    def test_a_compact_object_with_another_count_asks_for_the_round_again_once(self):
        reqs = self.first_round()
        out = wr.step(self.dir, {"T1": {"tool": "list_jira_board_sprints", "result": "x"}})
        self.assertEqual(("fetch", 1, reqs), (out["status"], out["round"], out["requests"]))
        self.assertIn("holds 1 result(s), 4 were requested", out["redo"])
        out = wr.step(self.dir, {"T1": {"tool": "list_jira_board_sprints", "result": "x"}})
        self.assertEqual("incomplete", out["status"])
        self.assertEqual("Weekly health report not posted: round 1: the compact object holds 1 result(s), "
                         "4 were requested", out["text"])

    def test_the_round_asked_again_is_filed_when_it_matches(self):
        reqs = self.first_round()
        wr.step(self.dir, {"T1": {"tool": "list_jira_board_sprints", "result": "x"}})
        compact = {f"T{n}": {"tool": r["tool"], "result": "x"} for n, r in enumerate(reqs, 1)}
        out = wr.step(self.dir, compact)
        self.assertNotIn("redo", out)
        self.assertTrue(wr.load_state(self.dir)["rounds"][0]["loaded"])

    def test_a_result_of_another_tool_is_not_matched(self):
        reqs = self.first_round()
        compact = {f"T{n}": {"tool": r["tool"], "result": "x"} for n, r in enumerate(reqs, 1)}
        compact["T2"]["tool"] = "query_jira"
        out = wr.step(self.dir, compact)
        self.assertEqual("fetch", out["status"])
        self.assertIn("T2 is a query_jira result, request r1.2 is github_file_content", out["redo"])

    def test_labels_sort_numerically(self):
        self.assertEqual(["T1", "T2", "T10"], wr._labels({"T10": 0, "T2": 0, "T1": 0, "kept": 0}))

    def test_a_round_is_loaded_once(self):
        reqs = self.first_round()
        compact = {f"T{n}": {"tool": r["tool"], "result": "x"} for n, r in enumerate(reqs, 1)}
        wr.load_round(self.dir, compact)
        with self.assertRaises(wr.StateError):
            wr.load_round(self.dir, compact)


class AnswerParsingTest(unittest.TestCase):
    def test_board_listing(self):
        text = ("Board 11479: 2 sprints.\n"
                "- OpenShift Edge Sprint 297 (id 86357, active, start 2026-11-09T14:43:12.700Z, "
                "end 2026-11-30T13:00:00.000Z)\n"
                "- Other Sprint 5 (id 12, closed, start 2026-01-01T00:00:00.000Z, end 2026-01-22T00:00:00.000Z, "
                "completed 2026-01-22T01:00:00.000Z, origin board 99)")
        sprints, problem = wr.parse_board_sprints(text)
        self.assertIsNone(problem)
        self.assertEqual(["86357", "12"], [s["id"] for s in sprints])
        self.assertEqual("99", sprints[1]["origin_board"])
        self.assertEqual(["86357"], [s["id"] for s in ch.sc.board_sprints(sprints, 11479)])

    def test_board_listing_error(self):
        sprints, problem = wr.parse_board_sprints("Jira refused the sprint list for board 11479: 401")
        self.assertIsNone(sprints)
        self.assertEqual("board sprint fetch failed: Jira refused the sprint list for board 11479: 401", problem)

    def test_failed_call_is_an_error_page(self):
        self.assertEqual({"error": "tool call failed"}, wr.as_page('{"error": "tool call failed"}'))
        self.assertEqual({"error": "no answer"}, wr.as_page(None))
        page = '{"result": "Found 1 issues (showing 1):"}'
        self.assertEqual(page, wr.as_page(page))


# ---------------------------------------------------------------- the sprint-end check-in

B = ch.block
CLOSED_SPRINT = "customfield_10020: Old Sprint (id 1, closed, board 11479, start 2026-01-01T00:00:00.000Z, end 2026-01-22T00:00:00.000Z)"
FUTURE_SPRINT = "customfield_10020: Next Sprint (id 3, future, board 11479, start 2026-12-01T00:00:00.000Z, end 2026-12-22T00:00:00.000Z)"


def checkin_world():
    return {
        "not_started": {"OCPEDGE": {"OCPEDGE-1": B("OCPEDGE-1", status="To Do", summary="Write the plan")},
                        "USHIFT": {}, "OCPBUGS": {}},
        "candidates": {"OCPEDGE": {k: B(k, summary=f"Item {k}") for k in
                                   ("OCPEDGE-14", "OCPEDGE-13", "OCPEDGE-12", "OCPEDGE-11", "OCPEDGE-10")},
                       "USHIFT": {"USHIFT-20": B("USHIFT-20", assignee="Quinn Quality", summary="No PR yet")},
                       "OCPBUGS": {}},
        "in_progress": {"OCPEDGE": {k: B(k, summary=f"Item {k}") for k in
                                    ("OCPEDGE-14", "OCPEDGE-13", "OCPEDGE-12", "OCPEDGE-11", "OCPEDGE-10")},
                        "USHIFT": {"USHIFT-20": B("USHIFT-20", assignee="Quinn Quality", summary="No PR yet")},
                        "OCPBUGS": {"OCPBUGS-25": B("OCPBUGS-25", "Bug", status="POST", summary="A bug")}},
        "unassigned": {"OCPEDGE": {}, "USHIFT": {}, "OCPBUGS": {
            "OCPBUGS-30": B("OCPBUGS-30", "Bug", status="New", assignee="Unassigned", summary="Nobody's bug")}},
        "no_sprint": {"OCPEDGE-41": B("OCPEDGE-41", summary="Next sprint already", fields=[FUTURE_SPRINT]),
                      "OCPEDGE-40": B("OCPEDGE-40", summary="Fell out", fields=[CLOSED_SPRINT])},
        "epics_nc": {"OCPEDGE-50": B("OCPEDGE-50", "Epic", summary="Epic without component",
                                     fields=["Labels: microshift"])},
        "issues_nc": {"OCPEDGE-61": B("OCPEDGE-61", summary="Epic has one", fields=["Parent: OCPEDGE-71"]),
                      "OCPEDGE-60": B("OCPEDGE-60", summary="Epic has two", fields=["Parent: OCPEDGE-70"])},
        "epics": {"OCPEDGE-71": B("OCPEDGE-71", "Epic", fields=["Components: MicroShift"]),
                  "OCPEDGE-70": B("OCPEDGE-70", "Epic", fields=["Components: MicroShift, SNO"])},
        "children": {"OCPEDGE-81": B("OCPEDGE-81", status="Closed", fields=["Parent: OCPEDGE-11"]),
                     "OCPEDGE-80": B("OCPEDGE-80", status="Closed", fields=["Parent: OCPEDGE-10"])},
        # key -> (remote PR URLs, links)
        "details": {"OCPEDGE-10": (["https://github.com/openshift/a/pull/1"], None),
                    "OCPEDGE-11": (["https://github.com/openshift/a/pull/2"], None),
                    "OCPEDGE-12": (["https://github.com/openshift/a/pull/3"], None),
                    "OCPEDGE-13": (["https://github.com/openshift-eng/edge-context/pull/9"], None),
                    "OCPEDGE-14": (["https://github.com/openshift/a/pull/4"], [("is blocked by", "OCPEDGE-99", "To Do")]),
                    "USHIFT-20": ([], None)},
        "prs": {1: "merged", 2: "open", 3: None, 4: "merged"},  # None: unreadable, the retry too
    }


def detail_text(key, urls, links):
    text = ch.issue_text(key, links)
    if urls:
        remote = "Remote Links:\n" + "".join(f"- GitHub PR: {u}\n" for u in urls)
        text = text.replace("Attachments: none", remote + "Attachments: none")
    return text


class FakeCheckin:
    def __init__(self, cw):
        self.cw = cw

    def query_jira(self, args):
        jql = args["jql"]
        pred = ad._page_query(jql)[0]
        m = __import__("re").search(r"\bproject = (\w+) AND sprint", pred)
        project = m.group(1) if m else None
        if pred.startswith("parent in ("):
            items = self.cw["children"]
        elif pred.startswith("key in ("):
            items = self.cw["epics"]
        elif "sprint not in openSprints()" in pred:
            items = self.cw["no_sprint"]
        elif "issuetype = Epic AND statusCategory" in pred:
            items = self.cw["epics_nc"]
        elif "component is EMPTY AND status != Closed" in pred:
            items = self.cw["issues_nc"]
        elif "assignee is EMPTY" in pred:
            items = self.cw["unassigned"][project]
        elif 'statusCategory = "To Do"' in pred:
            items = self.cw["not_started"][project]
        elif "issuetype in (Story, Task, Spike)" in pred:
            items = self.cw["candidates"][project]
        else:
            items = self.cw["in_progress"][project]
        return {"error": "tool call failed"} if items is None else ch.query_jira(jql, items)

    def get_jira_issue(self, args):
        urls, links = self.cw["details"][args["issue_key"]]
        return detail_text(args["issue_key"], urls, links)

    def github_pr_checks(self, args):
        state = self.cw["prs"][args["pr_number"]]
        return {"status": "ok", "pr_state": state} if state else {"error": "GitHub API timed out"}

    def list_jira_board_sprints(self, args):
        return ("Board 11479: 2 sprints.\n"
                "- OpenShift Edge Sprint 297 (id 86357, active, start 2026-11-09T14:43:12.700Z, end 2026-11-30T13:00:00.000Z)\n"
                "- OpenShift Edge Sprint 298 (id 90264, future, start 2026-11-30T13:00:00.000Z, end 2026-12-21T13:00:00.000Z)")

    def github_file_content(self, args):
        return ROSTER_MD

    def get_jira_create_meta(self, args):
        return ("Create metadata for OCPEDGE (OpenShift Edge)\nIssue types available (1): Epic\n\n[Epic]\n"
                "- Component/s [components] (required: no)\n  - MicroShift (id: 1)\n  - SNO (id: 2)\n")

    def resolve_identity(self, args):
        user = ch.SLACK.get(f"{args['identifier']}@redhat.com")
        return {"status": "ok", "slack_mention": f"<@{user}>"} if user else {"status": "not_found"}

    def answer(self, request):
        result = getattr(self, request["tool"])(request["args"])
        return result if isinstance(result, str) else json.dumps(result)


def run_checkin(cw, max_rounds=20):
    fake = FakeCheckin(cw)
    with tempfile.TemporaryDirectory() as state:
        out = wr.step(state, inputs={"today": "2026-11-30"}, mode="checkin")
        rounds = 0
        while out["status"] == "fetch":
            rounds += 1
            assert rounds <= max_rounds
            compact = {f"T{n}": {"tool": r["tool"], "result": fake.answer(r)} for n, r in enumerate(out["requests"], 1)}
            out = wr.step(state, compact)
        return out, rounds


class CheckinTest(unittest.TestCase):
    def setUp(self):
        self.out, self.rounds = run_checkin(checkin_world())
        self.by_kind = {}
        for r in self.out.get("replies", []):
            self.by_kind.setdefault(r["kind"], []).append(r)

    def test_status_and_sprint(self):
        self.assertEqual("checkin", self.out["status"], self.out.get("text"))
        self.assertEqual({"id": "86357", "name": "OpenShift Edge Sprint 297"}, self.out["sprint"])

    def test_looks_done_needs_all_four_checks(self):
        self.assertEqual(["OCPEDGE-10"], [r["key"] for r in self.by_kind["looks_done"]])
        self.assertIn("looks done (1 linked code pull request merged, child issues done, no open blockers)",
                      self.by_kind["looks_done"][0]["text"])
        self.assertEqual(["Close it", "Keep open"], self.by_kind["looks_done"][0]["buttons"])

    def test_unreadable_pr_is_not_checked(self):
        self.assertEqual("Not checked (status unverifiable): <https://redhat.atlassian.net/browse/OCPEDGE-12|OCPEDGE-12>",
                         self.by_kind["not_checked"][0]["text"])

    def test_carrying_over_per_owner_without_looks_done_or_not_checked(self):
        text = self.by_kind["carrying_over"][0]["text"]
        self.assertTrue(text.startswith("Carrying over:\n"))
        for key in ("OCPEDGE-11", "OCPEDGE-13", "OCPEDGE-14", "USHIFT-20", "OCPBUGS-25"):
            self.assertIn(f"|{key}>", text)
        for key in ("OCPEDGE-10", "OCPEDGE-12"):
            self.assertNotIn(f"|{key}>", text)
        self.assertIn("<@U000TEST1>: re-point remaining work and comment why:", text)
        self.assertIn("<@U000TEST2>: re-point remaining work and comment why: "
                      "<https://redhat.atlassian.net/browse/USHIFT-20|USHIFT-20>", text)

    def test_not_started_and_unassigned_and_no_sprint(self):
        self.assertEqual("<https://redhat.atlassian.net/browse/OCPEDGE-1|OCPEDGE-1> Write the plan, <@U000TEST1>: "
                         "not started. If nobody clicks, it carries over.", self.by_kind["not_started"][0]["text"])
        self.assertIn("OCPBUGS-30|OCPBUGS-30> Nobody's bug (New)", self.by_kind["unassigned"][0]["text"])
        no_sprint = self.by_kind["no_open_sprint"][0]["text"]
        self.assertIn("|OCPEDGE-40> Fell out (In Progress), <@U000TEST1>", no_sprint)
        self.assertNotIn("OCPEDGE-41", no_sprint)

    def test_component_candidates(self):
        c = self.out["component_candidates"]
        self.assertEqual(["MicroShift", "SNO"], c["components"])
        self.assertEqual(["OCPEDGE-50"], [e["key"] for e in c["epics"]])
        self.assertEqual([("OCPEDGE-60", "OCPEDGE-70", ["MicroShift", "SNO"], "one of the epic's components")],
                         [(i["key"], i["epic"], i["epic_components"], i["from"]) for i in c["issues"]])

    def test_counts(self):
        self.assertEqual(["1 not started", "1 look done", "5 carrying over", "1 unassigned",
                          "1 in progress with no sprint"], self.out["first_message_parts"])

    def test_a_failed_fetch_stops_the_checkin(self):
        cw = checkin_world()
        cw["in_progress"]["USHIFT"] = None  # makes the fake's answer fail
        out, _ = run_checkin(cw)
        self.assertEqual("incomplete", out["status"])
        self.assertIn("in_progress_USHIFT fetch incomplete", out["text"])


# ---------------------------------------------------------------- shortened values, schedules

SPRINTS_WHOLE = ("Test Sprint 10 (id 500, closed, board 1000, start 2030-01-07T12:00:00.000Z, end 2030-01-28T12:00:00.000Z); "
                 "Test Sprint 11 (id 501, active, board 1000, start 2030-01-28T12:00:00.000Z, end 2030-02-18T12:00:00.000Z)")
STORED = "    customfield_10020: Test Sprint 10 (id 500, clo (continues: 30 of 205 characters; the full value is [buffer:query#3]; read the rest with result_page or result_tail)"
POINTER = '    customfield_10020: Test Sprint 10 (id 500, clo (cut at 30 of 205 characters; read it whole with get_jira_issue("TEST-7", fields="customfield_10020"))'


def page_with(line):
    return ("Found 1 issues (showing 1):\n  [TEST-7] Synthetic\n    Type: Story | Status: In Progress | Priority: Major\n"
            f"{line}\n")


class ShortenedValueTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir))
        self.engine = wr.Engine(self.dir, {"today": "2030-02-01"})
        self.given = {}
        self.engine.answers.get = lambda key: self.given.get(key)

    def test_both_notes_name_their_read(self):
        self.assertEqual([(3, ("result_head", {"result_id": "[buffer:query#3]", "count": 1000000, "as_chars": True}))],
                         wr.shortened_value_calls(page_with(STORED)))
        self.assertEqual([(3, ("get_jira_issue", {"issue_key": "TEST-7", "fields": "customfield_10020"}))],
                         wr.shortened_value_calls(page_with(POINTER)))

    def test_a_stored_value_is_read_in_the_next_round_and_put_in_place(self):
        with self.assertRaises(wr.NeedRound) as need:
            self.engine.with_whole_values([page_with(STORED)])
        (tool, args, attempt), = need.exception.requests
        self.assertEqual(("result_head", 1), (tool, attempt))
        self.given[wr.request_key(tool, args, 1)] = json.dumps({"text": SPRINTS_WHOLE, "truncated": False})
        page = self.engine.with_whole_values([page_with(STORED)])[0]
        self.assertIn(f"    customfield_10020: {SPRINTS_WHOLE}\n".rstrip("\n"), page)
        self.assertEqual("ok", ad.parse_page(page)["status"])
        self.assertEqual([], ad.shortened_fields(ad.parse_page(page)["blocks"][0]))

    def test_a_pointed_value_is_read_with_get_jira_issue(self):
        call = ("get_jira_issue", {"issue_key": "TEST-7", "fields": "customfield_10020"})
        answer = ("[TEST-7] Synthetic\nType: Story | Status: In Progress\n\nRequested fields:\n"
                  f"customfield_10020: {SPRINTS_WHOLE}\n\nComments: none\n")
        self.given[wr.request_key(*call, 1)] = answer
        page = self.engine.with_whole_values([page_with(POINTER)])[0]
        self.assertIn(SPRINTS_WHOLE, page)

    def test_a_failed_read_is_sent_again_once_then_the_value_stays_shortened(self):
        call = ("result_head", {"result_id": "[buffer:query#3]", "count": 1000000, "as_chars": True})
        self.given[wr.request_key(*call, 1)] = json.dumps({"error": "not found"})
        with self.assertRaises(wr.NeedRound) as need:
            self.engine.with_whole_values([page_with(STORED)])
        self.assertEqual([(call[0], call[1], 2)], need.exception.requests)
        self.given[wr.request_key(*call, 2)] = json.dumps({"error": "not found"})
        page = self.engine.with_whole_values([page_with(STORED)])[0]
        self.assertIn("(continues:", page)  # the adapter then makes the fetch incomplete


class ScheduleAnswerTest(unittest.TestCase):
    def test_an_empty_schedule_is_no_dates_not_a_failure(self):
        self.assertEqual([], wr.schedule_tasks(json.dumps({"data": []})))
        self.assertEqual([], wr.schedule_tasks("[]"))

    def test_milestones_are_kept(self):
        tasks = [{"name": "Main Branch Open for 9.3", "date_start": "2030-03-01", "date_finish": "2030-03-01", "id": 7}]
        self.assertEqual([{"name": "Main Branch Open for 9.3", "date_start": "2030-03-01", "date_finish": "2030-03-01"}],
                         wr.schedule_tasks(json.dumps({"results": tasks})))

    def test_errors_and_unknown_shapes(self):
        self.assertEqual({"error": "timed out"}, wr.schedule_tasks(json.dumps({"error": "timed out"})))
        self.assertEqual({"error": "schedule answer not readable (keys: count)"},
                         wr.schedule_tasks(json.dumps({"count": 0})))
