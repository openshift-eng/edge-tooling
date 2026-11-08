"""Tests for chai_jira_adapter.py — chai-bot query_jira text → transform input.

The fixtures below are anonymized copies of real chai-bot output:
same structure, line layout and wording, with synthetic keys, names, summaries,
components, labels and dates. The repository is public: never paste real chai-bot
output, names or Jira keys into fixtures. Error and cut-off pages have not been
seen from chai-bot yet; their fixtures are assumptions.
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, BIN)
import chai_jira_adapter as ad  # noqa: E402
from _jira_transforms import extract_blocked_by  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(BIN, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


transform_sprint_issues = _load("transform-sprint-issues")
transform_features = _load("transform-features")
compute_velocity = _load("compute-velocity")

ROSTER = {"contacts": [
    {"display_name": "Alex Example", "email": "aexample@redhat.com"},
    {"display_name": "Quinn Quality", "email": "qquality@redhat.com"},
]}
VELOCITY_ROSTER = {"members": [
    {"username": "aexample@redhat.com", "display_name": "Alex Example", "sp_target": 8},
    {"username": "qquality@redhat.com", "display_name": "Quinn Quality", "sp_target": 8},
]}

SPRINT_10 = ("Test Sprint 10 (id 500, closed, board 1000, start 2030-01-07T12:00:00.000Z, "
             "end 2030-01-28T12:00:00.000Z)")
SPRINT_11 = ("Test Sprint 11 (id 501, active, board 1000, start 2030-01-28T12:00:00.000Z, "
             "end 2030-02-18T12:00:00.000Z)")
HINT = ("  ... More issues match than the {n} shown (Jira does not say how many). To see the rest, raise "
        "max_results up to 50, or narrow the JQL (status, component, assignee, or a created/updated date "
        "range). To page, order by key (ORDER BY key DESC), put the filter in parentheses, and add "
        "`AND key < <last key shown>` after it for each next page.")

# Sample 1: truncated page with sprint, story points and resolution date.
SAMPLE_TRUNCATED = f"""Showing the first 3 matching issues; more match:
  [TEST-304] Synthetic check reports a false pass
    Type: Bug | Status: Closed | Priority: Undefined | Assignee: Alex Example | Reporter: Alex Example | Resolution: Done
    Created: 2030-02-04 | Updated: 2030-02-06 | Resolved: 2030-02-06
    Components: Component A
    customfield_10020: {SPRINT_11}
    customfield_10028: 2.0
    resolutiondate: 2030-02-06T17:41:00.327+0000
  [TEST-300] Stretch: synthetic CI coverage
    Type: Story | Status: Code Review | Priority: Major | Assignee: Alex Example | Reporter: Alex Example
    Created: 2030-02-02 | Updated: 2030-02-06
    Components: Component A | Parent: TEST-215
    customfield_10020: {SPRINT_11}
    customfield_10028: 2.0
    parent: TEST-215
  [TEST-297] Investigate a synthetic replacement
    Type: Story | Status: Closed | Priority: Undefined | Assignee: Jordan Sample | Reporter: Jordan Sample | Resolution: Done
    Created: 2030-01-30 | Updated: 2030-02-03 | Resolved: 2030-02-03
    customfield_10020: {SPRINT_11}
    customfield_10028: 3.0
    resolutiondate: 2030-02-03T11:32:36.842+0000

{HINT.format(n=3)}
"""

# Sample 2: create-metadata preamble, a rejected field, Labels / Fix Versions, issuelinks not renderable.
SAMPLE_PREAMBLE = f"""Create metadata for FEAT (Synthetic Strategy Project)
Issue types available (1): Feature

[Feature]
Requested fields: (none found for this issue type)
Unknown fields: QA Contact, SME

Invalid extra_fields entries: QA Contact. Use Jira field IDs such as duedate or customfield_12310220, comma-separated.

Showing the first 2 matching issues; more match:
  [FEAT-854] Allow setting a synthetic limit through the API
    Type: Feature | Status: Release Pending | Priority: Major | Assignee: Casey Owner | Reporter: Drew Filer
    Created: 2030-01-01 | Updated: 2030-01-22
    Components: Component B | Labels: alpha, beta-work, gamma | Fix Versions: example-9.1
    issuelinks: (set, but query_jira cannot render this field's structure)
  [FEAT-797] Simplify synthetic upgrades
    Type: Feature | Status: Release Pending | Priority: Major | Assignee: Erin Lead | Reporter: Frankie Filer
    Created: 2029-12-18 | Updated: 2030-01-19
    Components: Component C | Labels: delta, epsilon | Fix Versions: example-9.1
    issuelinks: (set, but query_jira cannot render this field's structure)

{HINT.format(n=2)}
"""

# Sample 3: complete single page, custom fields by id (QA Contact, SME).
SAMPLE_COMPLETE = """Found 1 issues (showing 1):
  [FEAT-601] Synthetic docs refactor
    Type: Initiative | Status: In Progress | Priority: Major | Assignee: Morgan Manager | Reporter: Pat Product
    Created: 2029-11-11 | Updated: 2030-02-06
    Components: Component D | Labels: edge, no_core_payload, plan-label | Parent: INIT-171
    customfield_10470: Quinn Quality
    customfield_10475: Alex Example
"""

# Sample 4: custom fields requested (incl. Flagged) but unset: no lines at all.
SAMPLE_UNSET = f"""Showing the first 3 matching issues; more match:
  [TEST-304] Synthetic check reports a false pass
    Type: Bug | Status: Closed | Priority: Undefined | Assignee: Alex Example | Reporter: Alex Example | Resolution: Done
    Created: 2030-02-04 | Updated: 2030-02-06 | Resolved: 2030-02-06
    Components: Component A
  [TEST-300] Stretch: synthetic CI coverage
    Type: Story | Status: Code Review | Priority: Major | Assignee: Alex Example | Reporter: Alex Example
    Created: 2030-02-02 | Updated: 2030-02-06
    Components: Component A | Parent: TEST-215
  [TEST-297] Investigate a synthetic replacement
    Type: Story | Status: Closed | Priority: Undefined | Assignee: Jordan Sample | Reporter: Jordan Sample | Resolution: Done
    Created: 2030-01-30 | Updated: 2030-02-03 | Resolved: 2030-02-03

{HINT.format(n=3).replace("`", "")}
"""

# Sample 5: issueLinkType = "is blocked by" candidates (key, summary, status).
SAMPLE_BLOCKED_CANDIDATES = f"""Showing the first 3 matching issues; more match:
  [ALPHA-584] [QE] Migrate synthetic tests: remaining cases
    Type: Task | Status: Closed | Priority: Undefined | Assignee: Quinn Quality | Reporter: Quinn Quality | Resolution: Done
    Created: 2030-01-21 | Updated: 2030-01-29 | Resolved: 2030-01-29
    Parent: ALPHA-690
  [TEST-220] Synthetic calendar with trigger rules
    Type: Story | Status: In Progress | Priority: Major | Assignee: Alex Example | Reporter: Alex Example
    Created: 2030-01-11 | Updated: 2030-02-03
    Labels: TEST:Scrum | Parent: TEST-205
  [TEST-208] Synthetic foundation: persona and rules
    Type: Task | Status: Closed | Priority: Major | Assignee: Alex Example | Reporter: Alex Example | Resolution: Done
    Created: 2030-01-11 | Updated: 2030-02-01 | Resolved: 2030-02-01
    Labels: TEST:Scrum | Parent: TEST-205

{HINT.format(n=3)}
"""

# Sample 6: get_jira_issue with links (direction and linked status).
SAMPLE_GET_ISSUE = """Latest Jira State:
[TEST-303] Synthetic docs: final re-assembly
Project: TEST
Assignee: Jordan Sample | Reporter: Jordan Sample
Date Created: 2030-02-04T09:37:39
Date Modified: 2030-02-04T21:26:44
Type: Story | Status: To Do | Priority: Major
Resolution: Unresolved
Components: Component D
Parent: TEST-112 | https://example.invalid/browse/TEST-112
Linked Jira Issues:
- is blocked by: TEST-151 (Blocks) | status: Closed | https://example.invalid/browse/TEST-151
- is blocked by: TEST-152 (Blocks) | status: To Do | https://example.invalid/browse/TEST-152
- is blocked by: TEST-154 (Blocks) | status: To Do | https://example.invalid/browse/TEST-154
- is blocked by: TEST-55 (Blocks) | status: In Progress | https://example.invalid/browse/TEST-55
- blocks: TEST-26 (Blocks) | status: To Do | https://example.invalid/browse/TEST-26
Attachments: none
URL: https://example.invalid/browse/TEST-303

Description:
Synthetic description text.

Comments: none
"""


# Sample 7: no results, raw tool output as chai-bot returns it (wrapped JSON; no names).
SAMPLE_NO_RESULTS = '{"result":"No issues found for JQL: key = OCPEDGE-1 AND key = OCPEDGE-2"}'
SAMPLE_NO_RESULTS_JQL = "key = OCPEDGE-1 AND key = OCPEDGE-2"


def block(key, summary="Synthetic item", type_="Story", status="In Progress", assignee="Alex Example",
          sp="3.0", sprints=SPRINT_11, extra=()):
    """One issue block in chai-bot's layout (indented, combined lines)."""
    resolution = " | Resolution: Done" if status == "Closed" else ""
    lines = [f"  [{key}] {summary}",
             f"    Type: {type_} | Status: {status} | Priority: Major | Assignee: {assignee} | "
             f"Reporter: Sam Sample{resolution}",
             "    Created: 2030-01-02 | Updated: 2030-02-01",
             "    Components: Component A | Parent: TEST-1"]
    if sprints:
        lines.append(f"    customfield_10020: {sprints}")
    if sp is not None:
        lines.append(f"    customfield_10028: {sp}")
    lines.extend(f"    {e}" for e in extra)
    return "\n".join(lines)


def page(keys, final=True, shown=None, **kw):
    """A query_jira page: complete ("Found N issues (showing N):") or truncated (with the hint)."""
    blocks = [block(k, **kw) for k in keys]
    n = len(blocks) if shown is None else shown
    if final:
        return f"Found {n} issues (showing {n}):\n" + "\n".join(blocks) + "\n"
    return (f"Showing the first {n} matching issues; more match:\n" + "\n".join(blocks)
            + "\n\n" + HINT.format(n=n) + "\n")


def keys_desc(project, start, count):
    return [f"{project}-{n}" for n in range(start, start - count, -1)]


class SampleTest(unittest.TestCase):
    """The anonymized chai-bot samples parse field by field."""

    def test_truncated_page_with_sprint_and_points(self):
        p = ad.parse_page(SAMPLE_TRUNCATED, page_size=3)  # the sample used max_results 3
        self.assertEqual((p["status"], p["final"], p["unknown"]), ("ok", False, []))
        out = ad.adapt_fetch({"page_size": 3, "pages": [{"text": SAMPLE_TRUNCATED}]}, ROSTER)
        bug, story, closed = out["issues"]
        self.assertEqual([i["key"] for i in out["issues"]], ["TEST-304", "TEST-300", "TEST-297"])
        self.assertEqual((bug["issue_type"], bug["status"], bug["resolution"]),
                         ({"name": "Bug"}, {"name": "Closed"}, {"name": "Done"}))
        self.assertEqual(bug["components"], [{"name": "Component A"}])
        self.assertEqual(bug["customfield_10028"], 2.0)
        self.assertEqual(bug["customfield_10020"][0]["id"], 501)
        self.assertEqual(bug["resolutiondate"], "2030-02-06T17:41:00.327+0000")
        self.assertEqual(story["components"], [{"name": "Component A"}])  # "| Parent:" not swallowed
        self.assertEqual(story["parent"], {"key": "TEST-215", "issue_type": {"name": "Epic"}})
        self.assertIsNone(story["resolution"])  # no Resolution segment
        self.assertNotIn("resolutiondate", story)
        self.assertEqual(closed["assignee"], {"display_name": "Jordan Sample", "displayName": "Jordan Sample",
                                              "name": "Jordan Sample"})
        # A truncated page on its own is not a complete fetch.
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("stopped early", out["fetch"]["problems"][0])

    def test_preamble_rejected_field_and_lists(self):
        p = ad.parse_page(SAMPLE_PREAMBLE)
        self.assertEqual(p["rejected"], ["QA Contact"])
        self.assertEqual(p["unknown_fields"], ["QA Contact", "SME"])
        self.assertEqual(p["unknown"], [])  # preamble and issuelinks lines are recognized
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_PREAMBLE}]})
        feat = out["issues"][0]
        self.assertEqual(feat["labels"], ["alpha", "beta-work", "gamma"])
        self.assertEqual(feat["fixVersions"], [{"name": "example-9.1"}])
        self.assertEqual(feat["components"], [{"name": "Component B"}])
        self.assertEqual(feat["issuelinks"], [])
        # A rejected field would silently zero a figure, so the fetch is incomplete.
        self.assertIn("query_jira rejected field(s): QA Contact", out["fetch"]["problems"])
        self.assertIn("create metadata lists unknown field(s): QA Contact, SME", out["warnings"])

    def test_complete_page_with_custom_fields_by_id(self):
        out = ad.adapt_fetch({"order": "rank", "pages": [{"text": SAMPLE_COMPLETE}]}, ROSTER)
        self.assertTrue(out["fetch"]["complete"])
        feat = out["issues"][0]
        self.assertEqual(feat["labels"], ["edge", "no_core_payload", "plan-label"])
        self.assertEqual(feat["parent"], {"key": "INIT-171"})  # an initiative's parent is not an epic
        self.assertEqual(feat["customfield_10470"]["email"], "qquality@redhat.com")
        self.assertEqual(feat["customfield_10475"]["email"], "aexample@redhat.com")
        row = transform_features.transform_feature(feat, rank=1)
        self.assertEqual((row["qa_contact"], row["sme"], row["type"]),
                         ("Quinn Quality", "Alex Example", "Initiative"))

    def test_unset_custom_fields_produce_no_lines(self):
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_UNSET}], "signals": ["flagged"]}, ROSTER)
        self.assertEqual(out["skipped_signals"], [])  # unset Flagged = not flagged, not unavailable
        self.assertEqual(out["warnings"], [])
        for issue in out["issues"]:
            for field in ("customfield_10470", "customfield_10475", "customfield_10021", "customfield_10028"):
                self.assertNotIn(field, issue)
            row = transform_sprint_issues.transform_issue(issue, "2030-02-08")
            self.assertEqual((row["flagged"], row["qa_contact"], row["sp"]), (False, None, 0))

    def test_rejected_flagged_is_a_skipped_signal_only(self):
        text = ("Invalid extra_fields entries: customfield_10021. Use Jira field IDs such as duedate or "
                "customfield_12310220, comma-separated.\n\n" + page(["TEST-9", "TEST-8"]))
        out = ad.adapt_fetch({"pages": [{"text": text}], "signals": ["flagged"]}, ROSTER)
        self.assertEqual(out["skipped_signals"], [ad.SKIPPED_FLAGGED])
        self.assertTrue(out["fetch"]["complete"])

    def test_blocked_candidates_page(self):
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_BLOCKED_CANDIDATES}]}, ROSTER)
        keys = [i["key"] for i in out["issues"]]
        self.assertEqual(keys, ["ALPHA-584", "TEST-220", "TEST-208"])
        self.assertEqual(out["issues"][0]["summary"], "[QE] Migrate synthetic tests: remaining cases")
        self.assertEqual(out["issues"][1]["labels"], ["TEST:Scrum"])
        self.assertEqual(out["issues"][1]["parent"]["key"], "TEST-205")

    def test_get_jira_issue_links(self):
        links, found = ad.parse_issue_links(SAMPLE_GET_ISSUE)
        self.assertTrue(found)
        self.assertEqual([(l.get("inward_issue") or l.get("outward_issue"))["key"] for l in links],
                         ["TEST-151", "TEST-152", "TEST-154", "TEST-55", "TEST-26"])
        self.assertEqual(links[0]["inward_issue"]["status"], {"name": "Closed"})
        self.assertEqual(links[0]["type"], {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"})
        self.assertIn("outward_issue", links[4])
        # The single pipeline rule reads these links: TEST-151 is Closed, the rest are open.
        self.assertEqual(extract_blocked_by({"issuelinks": links}), ["TEST-152", "TEST-154", "TEST-55"])


class PagingTest(unittest.TestCase):
    def test_complete_header_single_page(self):
        out = ad.adapt_fetch({"pages": [{"text": page(["TEST-9", "TEST-8"])}]}, ROSTER)
        self.assertTrue(out["fetch"]["complete"])
        self.assertEqual([i["key"] for i in out["issues"]], ["TEST-9", "TEST-8"])

    def test_truncated_page_then_complete_page(self):
        first = page(keys_desc("TEST", 120, 50), final=False)
        second = page(keys_desc("TEST", 70, 3))
        out = ad.adapt_fetch({"pages": [{"text": first}, {"text": second}]})
        self.assertTrue(out["fetch"]["complete"], out["fetch"]["problems"])
        self.assertEqual(out["fetch"]["fetched"], 53)
        self.assertEqual(out["warnings"], [])

    def test_found_with_more_than_shown_is_incomplete(self):
        text = "Found 80 issues (showing 2):\n" + "\n".join(block(k) for k in ["TEST-9", "TEST-8"]) + "\n"
        p = ad.parse_page(text)
        self.assertEqual((p["status"], p["final"]), ("incomplete", False))

    def test_final_page_count_must_match_found_header(self):
        text = "Found 5 issues (showing 5):\n" + "\n".join(block(k) for k in ["TEST-9", "TEST-8", "TEST-7"]) + "\n"
        out = ad.adapt_fetch({"pages": [{"text": text}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("page lists 3 issues but its header says 5", out["fetch"]["problems"][0])

    def test_page_truncated_inside_the_last_two_issues(self):
        full = page(["TEST-9", "TEST-8", "TEST-7"])
        # Cut right after the last two issues' key lines: both field groups lost, count still 3.
        lines = full.splitlines()  # header, then 6 lines per issue: keys at lines 1, 7 and 13
        cut = "\n".join(lines[:7] + [lines[7], lines[13]]) + "\n"
        self.assertEqual(len(ad._split_blocks(cut)[1]), 3)
        out = ad.adapt_fetch({"pages": [{"text": cut}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("has no 'Type: … | Status: …' line", out["fetch"]["problems"][0])
        # Cut inside the second-to-last issue: the last issue is lost entirely.
        cut2 = "\n".join(lines[:9]) + "\n"
        out2 = ad.adapt_fetch({"pages": [{"text": cut2}]})
        self.assertFalse(out2["fetch"]["complete"])
        self.assertIn("page lists 2 issues but its header says 3", out2["fetch"]["problems"][0])

    def test_type_status_line_must_be_present_not_filled(self):
        ok = "Found 1 issues (showing 1):\n  [TEST-9] Synthetic\n    Type:  | Status: To Do\n"
        self.assertEqual(ad.parse_page(ok)["status"], "ok")
        cut = "Found 1 issues (showing 1):\n  [TEST-9] Synthetic\n    Created: 2030-01-02\n"
        self.assertEqual(ad.parse_page(cut)["status"], "incomplete")

    def test_full_page_without_hint_is_incomplete(self):
        text = page(keys_desc("TEST", 120, 50), final=False).split("\n\n  ... More issues match")[0] + "\n"
        out = ad.adapt_fetch({"pages": [{"text": text}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("without the 'More issues match' paging hint", out["fetch"]["problems"][0])

    def test_stopping_after_a_truncated_page_is_incomplete(self):
        out = ad.adapt_fetch({"pages": [{"text": page(keys_desc("TEST", 120, 50), final=False)}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("stopped early", " ".join(out["fetch"]["problems"]))

    def test_failed_page_makes_fetch_incomplete(self):
        out = ad.adapt_fetch({"pages": [{"text": page(keys_desc("TEST", 120, 50), final=False)},
                                        {"error": "tool error"}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("query_jira call failed", " ".join(out["fetch"]["problems"]))

    # Not observed from chai-bot yet (assumptions): a cut-off page and an error page.
    def test_cut_off_page_lists_fewer_issues_than_header(self):
        text = page(["TEST-9", "TEST-8"], shown=3)
        out = ad.adapt_fetch({"pages": [{"text": text}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("header says 3", " ".join(out["fetch"]["problems"]))

    def test_complete_header_with_more_hint_is_incomplete(self):
        text = page(["TEST-9", "TEST-8"]) + "\n" + HINT.format(n=2) + "\n"
        self.assertEqual(ad.parse_page(text)["status"], "incomplete")

    def test_page_without_header_or_issues_is_an_error(self):
        out = ad.adapt_fetch({"pages": [{"text": "Something went wrong"}]})
        self.assertFalse(out["fetch"]["complete"])

    def test_found_zero_issues_is_complete_and_empty(self):
        # The real empty form is "No issues found for JQL: ..." (see NoResultsTest); a
        # "Found 0" header would also pass the header rule. Anything else is incomplete.
        out = ad.adapt_fetch({"pages": [{"text": "Found 0 issues (showing 0):\n"}]})
        self.assertTrue(out["fetch"]["complete"])
        self.assertEqual(out["issues"], [])
        self.assertFalse(ad.adapt_fetch({"pages": [{"text": "No issues found.\n"}]})["fetch"]["complete"])

    def test_headerless_page_is_incomplete(self):
        text = "\n".join(block(k) for k in ["TEST-3", "TEST-2"])
        out = ad.adapt_fetch({"pages": [{"text": text}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("no 'Found N issues", out["fetch"]["problems"][0])

    def test_multi_project_paging_must_be_split_by_project(self):
        first = page(keys_desc("BETA", 80, 50), final=False)
        second = page(keys_desc("ALPHA", 20, 5))
        out = ad.adapt_fetch({"pages": [{"text": first}, {"text": second}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("several projects", " ".join(out["fetch"]["problems"]))

    def test_per_project_parts_are_complete(self):
        parts = [{"project": "BETA", "pages": [{"text": page(keys_desc("BETA", 80, 50), final=False)},
                                               {"text": page(keys_desc("BETA", 30, 2))}]},
                 {"project": "ALPHA", "pages": [{"text": page(["ALPHA-4", "ALPHA-2"])}]}]
        out = ad.adapt_fetch({"parts": parts})
        self.assertTrue(out["fetch"]["complete"], out["fetch"]["problems"])
        self.assertEqual(out["fetch"]["fetched"], 54)

    def test_overlapping_pages_are_incomplete(self):
        first = page(keys_desc("TEST", 120, 50), final=False)
        second = page(["TEST-71", "TEST-60"])  # TEST-71 was already on page 1
        out = ad.adapt_fetch({"pages": [{"text": first}, {"text": second}]})
        self.assertFalse(out["fetch"]["complete"])

    def test_rank_order_complete_only_on_one_final_page(self):
        ok = ad.adapt_fetch({"order": "rank", "pages": [{"text": page(["TEST-3", "TEST-7"])}]})
        self.assertTrue(ok["fetch"]["complete"])
        self.assertEqual([i["key"] for i in ok["issues"]], ["TEST-3", "TEST-7"])  # fetch order kept
        cut = ad.adapt_fetch({"order": "rank",
                              "pages": [{"text": page(keys_desc("TEST", 120, 50), final=False)}]})
        self.assertFalse(cut["fetch"]["complete"])
        self.assertIn("rank order not available", cut["fetch"]["problems"][0])

    def test_page_jql(self):
        self.assertEqual(ad.page_jql("project = TEST AND sprint = 5 ORDER BY priority ASC"),
                         "(project = TEST AND sprint = 5) ORDER BY key DESC")
        self.assertEqual(ad.page_jql("sprint = 5", project="TEST", last_key="TEST-40"),
                         "(sprint = 5) AND project = TEST AND key < TEST-40 ORDER BY key DESC")


class NoResultsTest(unittest.TestCase):
    """chai-bot's no-results output, wrapped as {"result": ...} or bare."""

    def test_wrapped_no_results_with_matching_jql_is_complete_and_empty(self):
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_NO_RESULTS, "jql": SAMPLE_NO_RESULTS_JQL}]})
        self.assertTrue(out["fetch"]["complete"], out["fetch"]["problems"])
        self.assertEqual((out["issues"], out["fetch"]["fetched"], out["warnings"]), ([], 0, []))

    def test_bare_no_results_with_matching_jql_is_complete(self):
        bare = json.loads(SAMPLE_NO_RESULTS)["result"]
        p = ad.parse_page(bare, jql="  " + SAMPLE_NO_RESULTS_JQL + "\n")  # surrounding whitespace ignored
        self.assertEqual((p["status"], p["final"], p["blocks"]), ("ok", True, []))

    def test_no_results_for_a_different_jql_is_incomplete(self):
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_NO_RESULTS, "jql": "key = OCPEDGE-1"}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("different JQL", out["fetch"]["problems"][0])

    def test_no_results_without_the_sent_jql_is_incomplete(self):
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_NO_RESULTS}]})
        self.assertFalse(out["fetch"]["complete"])

    def test_wrapped_and_bare_pages_parse_the_same(self):
        wrapped = json.dumps({"result": SAMPLE_COMPLETE})
        a = ad.adapt_fetch({"pages": [{"text": wrapped}]}, ROSTER)
        b = ad.adapt_fetch({"pages": [{"text": SAMPLE_COMPLETE}]}, ROSTER)
        self.assertTrue(a["fetch"]["complete"])
        self.assertEqual(a["issues"], b["issues"])
        self.assertEqual(ad.unwrap({"result": "x"}), "x")
        self.assertEqual(ad.unwrap("{not json"), "{not json")


class LinkedSpikesCliTest(unittest.TestCase):
    """CLI --linked-spikes: an incomplete per-feature fetch marks the output incomplete."""

    def _write(self, d, name, obj):
        path = os.path.join(d, name)
        with open(path, "w") as f:
            json.dump(obj, f)
        return path

    def test_incomplete_linked_spike_fetch_is_recorded(self):
        complete_spikes = ad.adapt_fetch({"pages": [{"text": page(["TEST-50"], type_="Spike", status="Closed",
                                                                 summary="Refine FEAT-601")}]})
        cut_spikes = ad.adapt_fetch({"pages": [{"text": page(keys_desc("TEST", 120, 50), final=False)}]})
        self.assertFalse(cut_spikes["fetch"]["complete"])
        with tempfile.TemporaryDirectory() as d:
            manifest = self._write(d, "m.json", {"order": "rank", "pages": [{"text": SAMPLE_COMPLETE}]})
            linked = self._write(d, "linked.json", {"FEAT-601": complete_spikes, "FEAT-602": cut_spikes})
            out_path = os.path.join(d, "out.json")
            self.assertEqual(ad.main(["fetch", "--manifest", manifest, "--linked-spikes", linked,
                                      "--output", out_path]), 0)
            with open(out_path) as f:
                out = json.load(f)
        self.assertFalse(out["fetch"]["complete"])
        self.assertEqual(len(out["fetch"]["linked_spike_problems"]), 1)
        self.assertTrue(out["fetch"]["linked_spike_problems"][0].startswith(
            "FEAT-602: linked-spike fetch incomplete (fetch: last page says more issues match"))
        # Spikes from complete fetches are still attached.
        links = out["issues"][0]["issuelinks"]
        self.assertEqual([link["inward_issue"]["key"] for link in links], ["TEST-50"])

    def test_complete_linked_spike_fetches_keep_the_fetch_complete(self):
        features = ad.adapt_fetch({"order": "rank", "pages": [{"text": SAMPLE_COMPLETE}]})
        empty = ad.adapt_fetch({"pages": [{"text": SAMPLE_NO_RESULTS, "jql": SAMPLE_NO_RESULTS_JQL}]})
        self.assertEqual(ad.attach_linked_spike_fetches(features, {"FEAT-601": empty}), [])
        self.assertTrue(features["fetch"]["complete"])


class FieldTest(unittest.TestCase):
    def test_missing_lines_are_empty(self):
        out = ad.adapt_fetch({"pages": [{"text": "Found 1 issues (showing 1):\n  [TEST-6] Bare issue\n"}]})
        issue = out["issues"][0]
        self.assertIsNone(issue["assignee"])
        self.assertEqual(issue["status"], {"name": ""})
        self.assertNotIn("created", issue)  # left out, so transforms apply their defaults
        self.assertNotIn("customfield_10028", issue)
        self.assertEqual((issue["components"], issue["labels"], issue["issuelinks"]), ([], [], []))
        row = transform_sprint_issues.transform_issue(issue, "2030-02-05")
        self.assertEqual((row["sp"], row["type"], row["assignee"]), (0, "Task", None))

    def test_unknown_lines_are_counted_in_a_warning(self):
        out = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"], extra=["Mystery: one | Another: two",
                                                                          "free text"])}]})
        self.assertIn("ignored 3 unrecognized line(s); labels: Another, Mystery", out["warnings"][0])
        self.assertTrue(out["fetch"]["complete"])

    def test_flagged_is_parsed_when_set(self):
        out = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"], extra=["customfield_10021: Impediment"])}],
                              "signals": ["flagged"]})
        self.assertEqual(out["skipped_signals"], [])
        self.assertTrue(transform_sprint_issues.transform_issue(out["issues"][0], "2030-02-05")["flagged"])

    def test_multiple_sprints(self):
        out = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"], sprints=f"{SPRINT_10}, {SPRINT_11}")}]})
        sprints = out["issues"][0]["customfield_10020"]
        self.assertEqual([(s["id"], s["state"], s["endDate"]) for s in sprints],
                         [(500, "closed", "2030-01-28T12:00:00.000Z"), (501, "active", "2030-02-18T12:00:00.000Z")])


class PeopleTest(unittest.TestCase):
    def test_display_name_matches_roster_case_and_space_insensitively(self):
        out = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"], assignee="alex   EXAMPLE")}]}, ROSTER)
        self.assertEqual(out["issues"][0]["assignee"]["email"], "aexample@redhat.com")
        self.assertEqual(out["people"], {"matched": 1, "unmatched": []})

    def test_unmatched_name_is_kept_not_unassigned(self):
        out = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"], assignee="Robin Outsider")}]}, ROSTER)
        assignee = out["issues"][0]["assignee"]
        self.assertNotIn("email", assignee)
        self.assertEqual(assignee["name"], "Robin Outsider")
        self.assertEqual(out["people"]["unmatched"], ["Robin Outsider"])

    def test_velocity_counts_team_work_matched_by_display_name(self):
        text = "Found 3 issues (showing 3):\n" + "\n".join([
            block("TEST-9", status="Closed", sp="5", sprints=SPRINT_10),
            block("TEST-8", status="Closed", sp="3", assignee="Robin Outsider", sprints=SPRINT_10,
                  extra=["customfield_10470: Quinn Quality"]),
            block("TEST-7", status="Closed", sp="8", assignee="Robin Outsider", sprints=SPRINT_10),
        ]) + "\n"
        adapted = ad.adapt_fetch({"pages": [{"text": text}]}, ROSTER)
        rows = [transform_sprint_issues.transform_issue(i, "2030-02-05") for i in adapted["issues"]]
        vel = compute_velocity.compute_velocity(
            [{"sprint_id": 500, "sprint_name": "Test Sprint 10", "issues": rows}], VELOCITY_ROSTER)
        self.assertEqual(vel["team_velocity"], 8.0)  # TEST-9 (assignee) + TEST-8 (QA Contact)
        self.assertEqual(vel["unmatched_done_sp"], 8)  # TEST-7 is not team-owned


class LinkTest(unittest.TestCase):
    def test_blocked_only_when_a_blocker_is_open(self):
        def blocked_by(text):
            return extract_blocked_by({"issuelinks": ad.parse_issue_links(text)[0]})
        for status in ("Closed", "Verified", "Done", "Won't Fix"):
            text = f"Linked Jira Issues:\n- is blocked by: TEST-20 (Blocks) | status: {status} | u\n"
            self.assertEqual(blocked_by(text), [], status)
        self.assertEqual(blocked_by("Linked Jira Issues:\n- is blocked by: TEST-20 (Blocks) | status: To Do | u\n"),
                         ["TEST-20"])
        self.assertEqual(blocked_by("Linked Jira Issues:\n- blocks: TEST-30 (Blocks) | status: New | u\n"), [])

    def test_wont_fix_only_blocker_is_not_blocked(self):
        text = page(["TEST-41"])
        adapted = ad.adapt_fetch({"pages": [{"text": text}]})
        ad.attach_issue_links(adapted, {"TEST-41": "Linked Jira Issues:\n"
                                        "- is blocked by: TEST-42 (Blocks) | status: Won't Fix | u\n"})
        row = transform_sprint_issues.transform_issue(adapted["issues"][0], "2030-02-05")
        self.assertEqual(row["blocked_by"], [])

    def test_section_ends_at_next_field(self):
        links, _ = ad.parse_issue_links(SAMPLE_GET_ISSUE + "\n- is blocked by: TEST-99 (Blocks) | status: New | u\n")
        self.assertNotIn("TEST-99", [(l.get("inward_issue") or {}).get("key") for l in links])

    def test_attached_links_feed_blocked_by(self):
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-304", "TEST-303"])}]}, ROSTER)
        problems = ad.attach_issue_links(adapted, {"TEST-303": SAMPLE_GET_ISSUE, "TEST-304": None})
        self.assertEqual(problems, ["TEST-304: get_jira_issue failed"])
        rows = {i["key"]: transform_sprint_issues.transform_issue(i, "2030-02-05") for i in adapted["issues"]}
        self.assertEqual(rows["TEST-303"]["blocked_by"], ["TEST-152", "TEST-154", "TEST-55"])
        self.assertEqual(rows["TEST-304"]["blocked_by"], [])

    def test_missing_linked_section_is_a_problem(self):
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-5": "Latest Jira State:\n[TEST-5] x\n"}),
                         ["TEST-5: no 'Linked Jira Issues' section"])

    def test_linked_spikes_become_feature_spike_candidates(self):
        features = ad.adapt_fetch({"order": "rank", "pages": [{"text": SAMPLE_COMPLETE}]}, ROSTER)
        spikes = ad.adapt_fetch({"pages": [{"text": page(["TEST-50"], type_="Spike", status="Closed",
                                                         summary="Test plan for FEAT-601",
                                                         assignee="Quinn Quality")}]}, ROSTER)
        ad.attach_linked_spikes(features, {"FEAT-601": spikes["issues"]})
        ad.attach_linked_spikes(features, {"FEAT-601": spikes["issues"]})  # no duplicate link
        row = transform_features.transform_feature(features["issues"][0], rank=1)
        self.assertEqual(len(row["spike_candidates"]), 1)
        cand = row["spike_candidates"][0]
        self.assertEqual((cand["key"], cand["status"], cand["assignee"], cand["resolution"]),
                         ("TEST-50", "Closed", "Quinn Quality", "Done"))


ROSTER_MD = """# Team Roster

## Example Team

| Name | GitHub | Role | Location |
|------|--------|------|----------|
| [Morgan Manager](https://rover.redhat.com/people/profile/mmanager) | mm | Manager, Engineering | Nowhere |
| [Alex Example](https://rover.redhat.com/people/profile/aexample) | ae | Senior Software Engineer | Nowhere |
| [Quinn Quality](https://rover.redhat.com/people/profile/qquality) | qq | Principal Software Quality Engineer | Nowhere |
| [Riley Reserved](https://rover.redhat.com/people/profile/rreserved) | rr | Principal Software Engineer | Nowhere |
| [Pat Product](https://rover.redhat.com/people/profile/pproduct) | pp | Principal Product Manager - Technical | Nowhere |
| [Sec Person](https://rover.redhat.com/people/profile/sperson) | sp | Principal Product Security Engineer | Nowhere |
| Nolink Person | np | Software Engineer | Nowhere |

## Not counted for sprint capacity

Team members who don't take work from the sprint board.

- Riley Reserved
"""


class RosterTest(unittest.TestCase):
    def test_not_counted_members_are_excluded(self):
        r = ad.parse_team_roster(ROSTER_MD)
        names = [m["display_name"] for m in r["members"]]
        self.assertEqual(names, ["Alex Example", "Quinn Quality", "Nolink Person"])
        self.assertEqual(r["engineer_count"], 3)
        self.assertEqual(r["not_counted"], ["Riley Reserved"])
        self.assertEqual(r["warnings"], [])

    def test_contacts_carry_rover_email(self):
        r = ad.parse_team_roster(ROSTER_MD)
        by_name = {c["display_name"]: c for c in r["contacts"]}
        self.assertEqual(len(by_name), 7)
        self.assertEqual(by_name["Morgan Manager"]["email"], "mmanager@redhat.com")
        self.assertIsNone(by_name["Nolink Person"]["email"])
        self.assertEqual(r["members"][0]["username"], "aexample@redhat.com")
        self.assertEqual(r["members"][2]["username"], "Nolink Person")

    def test_unknown_not_counted_entry_warns(self):
        r = ad.parse_team_roster(ROSTER_MD + "- Nobody Here\n")
        self.assertEqual(len(r["warnings"]), 1)
        self.assertEqual(r["engineer_count"], 3)


if __name__ == "__main__":
    unittest.main()
