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
import re
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


# Sample 8: full Sprint value after CHAI-1254 (no "+N more" cut; long values read whole
# with result_page). Entries are separated by "; ". Generic summary, anonymized assignee.
SPRINT_FULL = ("OCPEDGE Sprint 250 (id 12577, closed, board 8557, start 2024-02-26T10:32:56.158Z, "
               "end 2024-03-18T04:01:00.000Z, completed 2024-03-18T10:02:55.043Z); "
               "OCPEDGE Sprint 249 (id 14536, closed, board 8557, start 2024-02-05T16:21:59.538Z, "
               "end 2024-02-26T05:01:00.000Z, completed 2024-02-26T10:08:48.181Z)")
SAMPLE_SPRINTS = f"""Found 1 issues (showing 1):
  [TEST-250] Synthetic story carried across two sprints
    Type: Story | Status: Closed | Priority: Major | Assignee: Engineer A | Reporter: Engineer A | Resolution: Done
    Created: 2024-02-20 | Updated: 2024-03-18 | Resolved: 2024-03-18
    customfield_10020: {SPRINT_FULL}
    customfield_10028: 3.0
"""


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
        self.assertIn("page 2: query_jira call failed: tool error", " ".join(out["fetch"]["problems"]))

    def test_failed_call_names_its_error_on_one_line(self):
        self.assertEqual(ad.call_failed("403 Forbidden:\n  no permission\tfor OCPEDGE"),
                         "query_jira call failed: 403 Forbidden: no permission for OCPEDGE")
        self.assertEqual(ad.call_failed(True), "query_jira call failed")  # no message
        self.assertEqual(ad.call_failed("   "), "query_jira call failed")
        self.assertEqual(ad.call_failed({"code": 403}), "query_jira call failed: {'code': 403}")
        self.assertEqual(ad.call_failed(RuntimeError("timed out")), "query_jira call failed: timed out")
        # Never Slack markup: no mention, link or HTML from the tool's text.
        self.assertEqual(ad.call_failed("<!here> 502 <html>Bad & Gateway</html>"),
                         "query_jira call failed: &lt;!here&gt; 502 &lt;html&gt;Bad &amp; Gateway&lt;/html&gt;")
        long = ad.call_failed("x" * 500)
        self.assertEqual(len(long), len("query_jira call failed: ") + ad.ERROR_CHARS)
        self.assertTrue(long.endswith("x…"))
        self.assertEqual(ad.parse_page(None, error="timed out")["reason"], "query_jira call failed: timed out")
        self.assertEqual(ad.adapt_fetch({"pages": [{"text": None}]})["fetch"]["problems"],
                         ["fetch: page 1: query_jira call failed"])

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

    def test_a_rank_part_holds_only_its_project(self):
        other = ad.adapt_fetch({"order": "rank", "project": "TEST", "pages": [{"text": page(["OTHER-1"])}]})
        self.assertFalse(other["fetch"]["complete"])  # no page JQL to catch it: the part check does
        self.assertEqual(other["fetch"]["problems"], ["TEST: issues from other projects in a TEST-only fetch"])
        ok = ad.adapt_fetch({"order": "rank", "project": "TEST",
                             "pages": [{"text": page(["TEST-3", "TEST-9", "TEST-1"])}]})
        self.assertEqual((ok["fetch"]["complete"], ok["fetch"]["order"], [i["key"] for i in ok["issues"]]),
                         (True, "rank", ["TEST-3", "TEST-9", "TEST-1"]))  # a valid page keeps its rank order

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


class SprintValueTest(unittest.TestCase):
    """customfield_10020 after CHAI-1254: whole values, tolerant entries, never guessed."""

    def test_full_multi_sprint_value(self):
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_SPRINTS}]})
        self.assertTrue(out["fetch"]["complete"], out["fetch"]["problems"])
        issue = out["issues"][0]
        self.assertEqual(issue["assignee"]["name"], "Engineer A")
        sprints = issue["customfield_10020"]
        self.assertEqual([(s["id"], s["name"], s["state"]) for s in sprints],
                         [(12577, "OCPEDGE Sprint 250", "closed"), (14536, "OCPEDGE Sprint 249", "closed")])
        self.assertEqual(sprints[0]["endDate"], "2024-03-18T04:01:00.000Z")  # "completed" not glued on
        self.assertEqual(sprints[0]["completeDate"], "2024-03-18T10:02:55.043Z")
        row = transform_sprint_issues.transform_issue(issue, "2024-03-20")
        self.assertEqual(row["sprint_details"], [{"id": 12577, "end": "2024-03-18"}, {"id": 14536, "end": "2024-02-26"}])
        self.assertNotIn("sprint_data_incomplete", issue)

    def test_parts_in_any_order_and_unknown_keys(self):
        sprints, problem = ad.parse_sprint_value("Sprint X (future, foo bar, id 7, end 2030-01-02T00:00:00.000Z)")
        self.assertIsNone(problem)
        self.assertEqual((sprints[0]["id"], sprints[0]["state"], sprints[0]["endDate"]),
                         (7, "future", "2030-01-02T00:00:00.000Z"))

    def test_empty_sprint(self):
        self.assertEqual(ad.parse_sprint_value(""), ([], None))
        text = SAMPLE_SPRINTS.replace(SPRINT_FULL, "")
        out = ad.adapt_fetch({"pages": [{"text": text}]})
        self.assertTrue(out["fetch"]["complete"])
        self.assertEqual(out["issues"][0]["customfield_10020"], [])
        unset = ad.adapt_fetch({"pages": [{"text": SAMPLE_SPRINTS.replace(f"    customfield_10020: {SPRINT_FULL}\n", "")}]})
        self.assertTrue(unset["fetch"]["complete"])
        self.assertNotIn("customfield_10020", unset["issues"][0])

    def test_shortened_value_is_marked_incomplete(self):
        first = SPRINT_FULL.split("; ")[0]
        for shortened in (first + " +1 more", first + "; OCPEDGE Sprint 249 (id 14536, clo...",
                          first + " (value stored; read the rest with result_page)"):
            out = ad.adapt_fetch({"pages": [{"text": SAMPLE_SPRINTS.replace(SPRINT_FULL, shortened)}]})
            self.assertFalse(out["fetch"]["complete"], shortened)
            self.assertEqual(out["fetch"]["sprint_incomplete"], ["TEST-250"])
            self.assertIn("TEST-250: sprint value was shortened", out["fetch"]["problems"][0])
            self.assertIn("result_page", out["issues"][0]["sprint_data_incomplete"])

    def test_cut_off_entry_without_ellipsis_is_incomplete(self):
        sprints, problem = ad.parse_sprint_value("Sprint A (id 1, closed); Sprint B (id 2, closed")
        self.assertEqual([s["id"] for s in sprints], [1])
        self.assertIn("unparsed text", problem)
        cut = SPRINT_FULL[:SPRINT_FULL.rindex(", completed")]  # second entry loses its ")"
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_SPRINTS.replace(SPRINT_FULL, cut)}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertEqual(out["fetch"]["sprint_incomplete"], ["TEST-250"])
        # Whole values with either separator still parse cleanly.
        self.assertIsNone(ad.parse_sprint_value(SPRINT_FULL)[1])
        self.assertIsNone(ad.parse_sprint_value(SPRINT_FULL.replace("; ", ", "))[1])

    def test_entry_without_id_is_incomplete(self):
        sprints, problem = ad.parse_sprint_value("Sprint X (closed, board 1)")
        self.assertEqual(sprints, [])
        self.assertIn("has no id", problem)


def with_links_state(text, value):
    """``text`` as get_jira_issue(fields="issuelinks") prints it: the Requested fields area
    (after the description, right before the comments header) with this issuelinks value."""
    return text.replace("\nComments: none", f"\nRequested fields:\nissuelinks: {value}\n\nComments: none", 1)


NO_LINKS_ISSUE = with_links_state(re.sub(r"Linked Jira Issues:\n(?:- .*\n)+", "", SAMPLE_GET_ISSUE), ad.LINKS_EMPTY)
LINKED_ISSUE = with_links_state(SAMPLE_GET_ISSUE, ad.LINKS_SET)


class ShortenedFieldTest(unittest.TestCase):
    """Fields the checks read must be whole; a shortened one makes the fetch incomplete."""

    def _adapt(self, *fields, summary="Synthetic item"):
        text = page(["TEST-1"], sprints=None, summary=summary, extra=fields)
        return ad.adapt_fetch({"name": "features", "pages": [{"text": text}]})

    def test_shortened_values_are_incomplete(self):
        for line, field in (("Labels: Blocked, +2 more", "labels"),
                            ("customfield_10855: openshift-9.2, openshift-9.3...", "customfield_10855"),
                            ("customfield_10475: (value stored; read it with result_page)", "customfield_10475"),
                            ("customfield_10470: Quinn Q [shown 20 of 900 characters]", "customfield_10470"),
                            ('customfield_10795: M (cut; get_jira_issue(issue_key, fields="customfield_10795"))',
                             "customfield_10795"),
                            ("Fix Versions: 9.2 (truncated)", "fixVersions")):
            with self.subTest(line=line):
                out = self._adapt(line)
                self.assertFalse(out["fetch"]["complete"])
                self.assertEqual(out["fetch"]["problems"],
                                 [f"TEST-1: {field} value shortened (not read whole)"])

    def test_whole_values_stay_complete(self):
        out = self._adapt("Labels: Blocked, Parked", "Components: MicroShift / Storage",
                          "customfield_10855: openshift-9.2", "customfield_10475: Alex Example",
                          "customfield_10028: 3.0", summary="Summary ending in an ellipsis...")
        self.assertTrue(out["fetch"]["complete"], out["fetch"]["problems"])
        self.assertEqual(ad.shortened_fields({"summary": "cut...", "description": "+3 more"}), [])
        # Whole values that only contain a word such as "stored" are not shortened.
        for raw in ({"labels": "log-truncated, data-stored"}, {"components": "Stored Procedures"},
                    {"assignee": "Alex Stored"}, {"components": "Storage (Local)"}, {"labels": "+2"}):
            with self.subTest(raw=raw):
                self.assertEqual(ad.shortened_fields(raw), [])

    def test_fetch_records_its_order(self):
        self.assertEqual(self._adapt()["fetch"]["order"], "key")
        rank = ad.adapt_fetch({"order": "rank", "pages": [{"text": page(["TEST-1"])}]})
        self.assertEqual((rank["fetch"]["order"], rank["fetch"]["complete"]), ("rank", True))


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
        # The Left out reason ("<fetch> incomplete: <problems>") names it too.
        self.assertTrue(out["fetch"]["problems"][-1].startswith("FEAT-602: linked-spike fetch incomplete (fetch: "))
        # Spikes from complete fetches are still attached.
        links = out["issues"][0]["issuelinks"]
        self.assertEqual([link["inward_issue"]["key"] for link in links], ["TEST-50"])

    def test_features_sharing_a_linked_spike_failure_are_named_once(self):
        features = ad.adapt_fetch({"order": "rank", "pages": [{"text": SAMPLE_COMPLETE}]})
        failed = {"issues": [], "fetch": {"complete": False, "problems": ["TEST-930: get_jira_issue failed"]}}
        problems = ad.attach_linked_spike_fetches(features, {"FEAT-601": failed, "FEAT-602": failed})
        self.assertEqual(len(problems), 2)  # one per feature, as before
        self.assertEqual(features["fetch"]["problems"][-1],
                         "FEAT-601, FEAT-602: linked-spike fetch incomplete (TEST-930: get_jira_issue failed)")
        self.assertFalse(features["fetch"]["complete"])

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
        # Unknown labels are not split on " | " (see test_pipe_inside_a_value_is_not_split).
        self.assertIn("ignored 2 unrecognized line(s); labels: Mystery", out["warnings"][0])
        self.assertTrue(out["fetch"]["complete"])

    def test_pipe_inside_a_value_is_not_split(self):
        out = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"], extra=["Description: step one | Note: see docs"])}]})
        self.assertEqual(out["issues"][0]["description"], "step one | Note: see docs")
        self.assertEqual(out["warnings"], [])
        # Known labels still split.
        self.assertEqual(out["issues"][0]["components"], [{"name": "Component A"}])
        self.assertEqual(out["issues"][0]["parent"]["key"], "TEST-1")

    def test_empty_fetch_reports_no_skipped_description(self):
        out = ad.adapt_fetch({"pages": [{"text": SAMPLE_NO_RESULTS, "jql": SAMPLE_NO_RESULTS_JQL}],
                              "signals": ["description"]})
        self.assertEqual(out["skipped_signals"], [])
        some = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"])}], "signals": ["description"]})
        self.assertEqual(some["skipped_signals"], [ad.SKIPPED_DESCRIPTION])

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

    def test_contact_without_rover_email_matches_by_display_name(self):
        roster = {"contacts": [{"display_name": "Nolink Person", "email": None}]}
        out = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"], assignee="Nolink Person")}]}, roster)
        self.assertEqual(out["people"], {"matched": 1, "unmatched": []})
        self.assertEqual(out["issues"][0]["assignee"]["email"], "Nolink Person")

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

    def test_phrase_in_summary_does_not_hide_the_real_section(self):
        text = SAMPLE_GET_ISSUE.replace("[TEST-303] Synthetic docs: final re-assembly",
                                        "[TEST-303] Clean up linked Jira issues for feature X")
        links, found = ad.parse_issue_links(text)
        self.assertTrue(found)
        self.assertEqual(extract_blocked_by({"issuelinks": links}), ["TEST-152", "TEST-154", "TEST-55"])

    def test_phrase_in_description_with_bullets_adds_no_links(self):
        text = ("Latest Jira State:\n[TEST-7] Synthetic\nType: Story | Status: To Do | Priority: Major\n"
                "Description:\nWe track Linked Jira Issues: manually\n"
                "- is blocked by: TEST-8 (Blocks) | status: To Do | u\n")
        links, found = ad.parse_issue_links(text)
        self.assertEqual((links, found), ([], False))

    def test_section_ends_at_next_field(self):
        links, _ = ad.parse_issue_links(SAMPLE_GET_ISSUE + "\n- is blocked by: TEST-99 (Blocks) | status: New | u\n")
        self.assertNotIn("TEST-99", [(l.get("inward_issue") or {}).get("key") for l in links])

    def test_attached_links_feed_blocked_by(self):
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-304", "TEST-303"])}]}, ROSTER)
        problems = ad.attach_issue_links(adapted, {"TEST-303": LINKED_ISSUE, "TEST-304": None})
        self.assertEqual(problems, ["TEST-304: get_jira_issue failed"])
        rows = {i["key"]: transform_sprint_issues.transform_issue(i, "2030-02-05") for i in adapted["issues"]}
        self.assertEqual(rows["TEST-303"]["blocked_by"], ["TEST-152", "TEST-154", "TEST-55"])
        self.assertEqual(rows["TEST-304"]["blocked_by"], [])

    def test_missing_linked_section_is_a_problem(self):
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-5"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-5": "Latest Jira State:\n[TEST-5] x\n"}),
                         ["TEST-5: no issuelinks state in Requested fields (request fields=\"issuelinks\")"])

    def test_whole_response_without_links_is_an_empty_link_list(self):
        no_links = NO_LINKS_ISSUE
        self.assertTrue(ad.issue_text_complete(no_links, "TEST-303"))
        self.assertEqual(ad.issuelinks_state(no_links), "empty")
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": no_links}), [])
        self.assertEqual(adapted["issues"][0]["issuelinks"], [])
        self.assertEqual(adapted["fetch"]["link_problems"], [])
        # A summary naming linked issues, or a row that is not a link (a subtask), is still no links.
        for text in (no_links.replace("[TEST-303] Synthetic docs: final re-assembly",
                                      "[TEST-303] Clean up linked Jira issues"),
                     no_links.replace("Attachments: none", "Subtasks:\n- Subtask: TEST-9 (Sub-task) | status: New\n"
                                                           "Attachments: none")):
            with self.subTest(text=text.splitlines()[1]):
                adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
                self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": text}), [])

    def test_links_in_another_form_are_not_read_as_no_links(self):
        for header in ("Linked Jira Issues (5):", "**Linked Jira Issues:**"):
            with self.subTest(header=header):
                text = LINKED_ISSUE.replace("Linked Jira Issues:", header)
                adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
                self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": text}),
                                 ["TEST-303: issuelinks is set but no readable 'Linked Jira Issues' section"])

    def test_response_cut_inside_the_links_section_is_a_problem(self):
        cut = SAMPLE_GET_ISSUE[:SAMPLE_GET_ISSUE.index("- is blocked by: TEST-154")]
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": cut}),
                         ["TEST-303: no issuelinks state in Requested fields (request fields=\"issuelinks\")",
                          "TEST-303: get_jira_issue response cut off"])
        self.assertEqual(extract_blocked_by(adapted["issues"][0]), ["TEST-152"])  # confirmed blockers are kept

    def test_row_cut_at_the_end_is_never_a_blocker(self):
        cut = SAMPLE_GET_ISSUE[:SAMPLE_GET_ISSUE.index("- is blocked by: TEST-154") + len("- is blocked by: TEST-15")]
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": cut}),
                         ["TEST-303: no issuelinks state in Requested fields (request fields=\"issuelinks\")",
                          "TEST-303: get_jira_issue response cut off"])
        self.assertEqual(extract_blocked_by(adapted["issues"][0]), ["TEST-152"])  # not "TEST-15"

    def test_response_for_another_issue_is_not_attached(self):
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-304"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-304": SAMPLE_GET_ISSUE}),
                         ["TEST-304: get_jira_issue response is for TEST-303"])
        self.assertEqual(adapted["issues"][0]["issuelinks"], [])

    def test_requested_fields_examples_are_scoped_or_fail_closed(self):
        head = "Latest Jira State:\n[TEST-1] Synthetic spike\nType: Spike | Status: New\nAttachments: none\n"
        example = "Description:\nExample:\nRequested fields:\nissuelinks: {}\n\n{}\nmore text\n\n"
        for comments in ("Comments: none", "Comments: 2 total; showing the 2 most recent, newest first:"):
            for real in (ad.LINKS_SET, ad.LINKS_EMPTY):
                with self.subTest(comments=comments, real=real):
                    text = head + example.format(ad.LINKS_EMPTY, comments) + \
                        f"Requested fields:\nissuelinks: {real}\n\nComments: none\n"
                    adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-1"])}]})
                    problems = ad.attach_issue_links(adapted, {"TEST-1": text})
                    if comments == "Comments: none":  # an example followed by text is not the end: scoped
                        self.assertEqual(ad.issuelinks_state(text), "set" if real == ad.LINKS_SET else "empty")
                        self.assertEqual(problems, [] if real == ad.LINKS_EMPTY else
                                         ["TEST-1: issuelinks is set but no readable 'Linked Jira Issues' section"])
                    else:  # cannot tell the real area apart: fail closed
                        self.assertEqual(ad.issuelinks_state(text), "ambiguous")
                        self.assertEqual(problems,
                                         ["TEST-1: issuelinks state ambiguous (more than one Requested fields area)"])
        # A lone example, response cut before the real area: never empty links.
        cut = head + example.format(ad.LINKS_EMPTY, "Comments: none")
        self.assertIsNone(ad.issuelinks_state(cut))
        self.assertFalse(ad.issue_text_complete(cut, "TEST-1"))

    def test_ambiguous_state_with_readable_links_keeps_them_as_a_problem(self):
        example = (f"Description:\nRequested fields:\nissuelinks: {ad.LINKS_EMPTY}\n\n"
                   "Comments: 2 total; showing the 2 most recent, newest first:\nmore\n")
        text = LINKED_ISSUE.replace("Description:\n", example, 1)
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": text}),
                         ["TEST-303: issuelinks state ambiguous (more than one Requested fields area)"])
        self.assertEqual(extract_blocked_by(adapted["issues"][0]), ["TEST-152", "TEST-154", "TEST-55"])

    def test_headerless_cut_row_is_never_a_blocker(self):
        text = "Linked Jira Issues:\n- is blocked by: TEST-9 (Blocks) | status: New | u\n- is blocked by: TEST-15"
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-1"])}]})
        problems = ad.attach_issue_links(adapted, {"TEST-1": text})
        self.assertIn("TEST-1: get_jira_issue response cut off", problems)
        self.assertEqual(extract_blocked_by(adapted["issues"][0]), ["TEST-9"])  # not "TEST-15"

    def test_missing_or_unknown_state_with_readable_links_keeps_them_as_a_problem(self):
        no_area = SAMPLE_GET_ISSUE
        unknown = with_links_state(SAMPLE_GET_ISSUE, "(something else)")
        for text, why in ((no_area, 'no issuelinks state in Requested fields (request fields="issuelinks")'),
                          (unknown, "issuelinks state not readable")):
            with self.subTest(why=why):
                adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
                self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": text}), [f"TEST-303: {why}"])
                self.assertEqual(extract_blocked_by(adapted["issues"][0]), ["TEST-152", "TEST-154", "TEST-55"])

    def test_failed_cut_off_or_unreadable_response_is_still_a_problem(self):
        no_state = 'no issuelinks state in Requested fields (request fields="issuelinks")'
        cases = {"failed call": (None, "get_jira_issue failed"),
                 "cut off before the comments": (NO_LINKS_ISSUE.split("Description:")[0], no_state),
                 "cut inside the Requested fields": (NO_LINKS_ISSUE.split("issuelinks: (empty")[0], no_state),
                 "explanation instead of the issue": ("TEST-303 could not be read: not returned by Jira\n", no_state),
                 "no state line": (NO_LINKS_ISSUE.replace("Type: Story | Status: To Do | Priority: Major\n", ""),
                                   "get_jira_issue response cut off or not readable"),
                 "another issue": (NO_LINKS_ISSUE.replace("[TEST-303]", "[TEST-304]"),
                                   "get_jira_issue response is for TEST-304"),
                 "field not requested": (re.sub(r"Linked Jira Issues:\n(?:- .*\n)+", "", SAMPLE_GET_ISSUE), no_state),
                 "unknown state": (with_links_state(NO_LINKS_ISSUE.replace(
                     "Requested fields:\nissuelinks: " + ad.LINKS_EMPTY + "\n\n", ""), "(something else)"),
                                   "issuelinks state not readable"),
                 "set, but no section": (with_links_state(NO_LINKS_ISSUE.replace(
                     "Requested fields:\nissuelinks: " + ad.LINKS_EMPTY + "\n\n", ""), ad.LINKS_SET),
                                         "issuelinks is set but no readable 'Linked Jira Issues' section")}
        for name, (text, why) in cases.items():
            with self.subTest(name):
                adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
                self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": text}), [f"TEST-303: {why}"])

    def test_set_state_reads_the_links_section(self):
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
        self.assertEqual(ad.issuelinks_state(LINKED_ISSUE), "set")
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": LINKED_ISSUE}), [])
        self.assertEqual(extract_blocked_by(adapted["issues"][0]), ["TEST-152", "TEST-154", "TEST-55"])

    def test_set_state_with_an_empty_section_is_not_no_links(self):
        empty_section = with_links_state(re.sub(r"(Linked Jira Issues:\n)(?:- .*\n)+", r"\1", SAMPLE_GET_ISSUE),
                                         ad.LINKS_SET)
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": empty_section}),
                         ["TEST-303: issuelinks is set but no readable 'Linked Jira Issues' section"])

    def test_empty_state_with_links_is_a_contradiction_keeping_confirmed_links(self):
        contradictory = with_links_state(SAMPLE_GET_ISSUE, ad.LINKS_EMPTY)
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": contradictory}),
                         ["TEST-303: issuelinks state contradicts the response"])
        self.assertEqual(extract_blocked_by(adapted["issues"][0]), ["TEST-152", "TEST-154", "TEST-55"])

    def test_state_is_read_only_from_the_requested_fields_area(self):
        example = "Description:\nRequested fields:\nissuelinks: " + ad.LINKS_EMPTY + "\nmore text\n"
        spoofed = LINKED_ISSUE.replace("Description:\n", example, 1)  # an example in the description
        self.assertEqual(ad.issuelinks_state(spoofed), "set")
        no_area = re.sub(r"Linked Jira Issues:\n(?:- .*\n)+", "", SAMPLE_GET_ISSUE)
        no_area = no_area.replace("Description:\n", example, 1)
        self.assertIsNone(ad.issuelinks_state(no_area))  # an example alone is not the area
        combined = NO_LINKS_ISSUE.replace("Requested fields:\n", "Requested fields:\ncustomfield_10020: Sprint A "
                                          "(id 1, closed)\n")
        self.assertEqual(ad.issuelinks_state(combined), "empty")  # fields="customfield_10020,issuelinks"

    def test_cut_description_ending_in_a_bullet_keeps_confirmed_links(self):
        bullet = "Steps:\n- is blocked by: TEST-8 (Blocks) | status: To"
        cut = LINKED_ISSUE.replace("Synthetic description text.", bullet)
        cut = cut[:cut.rindex("status: To") + len("status: To")]  # cut off inside the description
        adapted = ad.adapt_fetch({"pages": [{"text": page(["TEST-303"])}]})
        self.assertEqual(ad.attach_issue_links(adapted, {"TEST-303": cut}),
                         ["TEST-303: no issuelinks state in Requested fields (request fields=\"issuelinks\")",
                          "TEST-303: get_jira_issue response cut off"])
        links = adapted["issues"][0]["issuelinks"]  # all five confirmed rows, including the last ("blocks: TEST-26")
        self.assertEqual([(link.get("inward_issue") or link.get("outward_issue"))["key"] for link in links],
                         ["TEST-151", "TEST-152", "TEST-154", "TEST-55", "TEST-26"])

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


# ---------------------------------------------------------------- the SKILL paging loop

MORE_MATCH = "Showing the first 50 matching issues; more match:"
SKILL_SENTENCE = ("`Showing the first 50 matching issues; more match:` is normal. It is never an error, never "
                  "partial data and never a reason to stop: call `next_page_jql` and fetch that page now, until "
                  "it returns `None`.")
CHAI = os.path.join(BIN, "..", "chai")


def _project(key):
    return key.rsplit("-", 1)[0]


def _number(key):
    return int(key.rsplit("-", 1)[1])


def after_group(sent):
    """What follows the leading ``(<predicate>)`` of a query (the paging clauses); a
    parenthesis inside a quoted value does not count."""
    depth, quote, escaped = 0, None, False
    for i, c in enumerate(sent):
        if escaped:
            escaped = False
            continue
        if quote:
            escaped = c == "\\"
            quote = None if c == quote else quote
            continue
        if c in "\"'":
            quote = c
            continue
        depth += (c == "(") - (c == ")")
        if depth == 0:
            return sent[i + 1:]
    return sent


def jira_keys(sent, keys, ignores_key_below=False):
    """The keys a stand-in Jira returns for ``sent``, in order, from ``keys`` (every one
    matching the query's predicate; synthetic projects). It applies the clauses paging adds
    after ``(<predicate>)``: ``AND project = P``, ``AND project not in (...)`` and
    ``AND key < K`` (keys of K's project only, as Jira does); it orders by key, or keeps the
    given order for ``ORDER BY Rank``. ``ignores_key_below`` stands for a tool that does not
    apply ``key <``."""
    if re.search(r"ORDER BY Rank ASC\s*$", sent):  # the trailing clause, not text inside a literal
        return list(keys)
    found = sorted(keys, key=lambda k: (_project(k), _number(k)), reverse=True)
    tail = after_group(sent) if sent.startswith("(") else sent
    part = re.search(r"AND project = ([A-Z][A-Z0-9_]*)", tail)
    if part:
        found = [k for k in found if _project(k) == part.group(1)]
    excluded = re.search(r"AND project not in \(([^)]*)\)", tail)
    if excluded:
        names = {n.strip() for n in excluded.group(1).split(",")}
        found = [k for k in found if _project(k) not in names]
    below = re.search(r"AND key < (" + ad.KEY_PATTERN + ")", tail)
    if below and not ignores_key_below:
        cursor = below.group(1)
        found = [k for k in found if _project(k) == _project(cursor) and _number(k) < _number(cursor)]
    return found


def jira_answer(keys, ignores_key_below=False, log=None):
    """A stand-in for ``query_jira`` over ``keys`` (see :func:`jira_keys`), 50 to a page."""
    def answer(sent):
        if log is not None:
            log.append(sent)
        found = jira_keys(sent, keys, ignores_key_below)
        if not found:
            return f"No issues found for JQL: {sent}"
        return page(found[:ad.PAGE_SIZE], final=len(found) <= ad.PAGE_SIZE)
    return answer


def manifest_page(reply, sent):
    """The manifest page the SKILL keeps for one answer: ``{"text": <answer>, "jql": sent}``,
    the answer bare or wrapped (``{"result": ...}``), or ``{"error": ..., "jql": sent}`` for
    a failed call."""
    if isinstance(reply, dict) and (reply.get("error") is not None or "text" in reply):
        return dict(reply, jql=sent)  # a failed call's slot (or a page slot already), as next_page_jql reads it
    return {"text": reply, "jql": sent}


def send_page(answer, sent):
    """One page as the SKILL sends it: ``next_page_jql`` after it, and the same query sent
    again once when it raises :class:`PageNotWhole`. Returns ``(manifest page, outcome)``:
    the next query, None, or the exception (not whole twice, a page that did not advance
    included; a query error; split)."""
    for _ in range(2):
        reply = answer(sent)
        entry = manifest_page(reply, sent)
        try:
            return entry, ad.next_page_jql(sent, reply)
        except ad.PageNotWhole as exc:
            outcome = exc
        except ad.QueryError as exc:  # not sent again; the kept page carries the reason
            return dict(entry, incomplete=str(exc)), exc
        except ad.SplitByProject as exc:
            return entry, exc
    return dict(entry, incomplete=str(outcome)), outcome


def fetch_by_skill(query, answer, first=None, guard=200):
    """A fetch as the SKILL "Jira paging" recipe runs it over the tool ``answer(sent)``: the
    first page ``page_jql(query)`` (or ``first``), then the page ``next_page_jql`` returns,
    until it returns None. A page it reports not whole is sent again once; a page still not
    whole after it (one that did not advance included), or a query error, is kept and the
    fetch stops there (``adapt_fetch`` then says why it is incomplete). On
    :class:`SplitByProject` that query's pages are dropped and each part is fetched from
    its first page. Returns the manifest parts.
    ``guard`` only keeps a broken loop from hanging the test."""
    parts, queue, sends = [], [(None, first or ad.page_jql(query))], 0
    while queue:
        project, sent = queue.pop(0)
        pages = []
        while isinstance(sent, str):
            sends += 1
            if sends > guard:
                raise AssertionError(f"paging did not end after {guard} pages")
            entry, sent = send_page(answer, sent)
            if isinstance(sent, ad.SplitByProject):
                queue += [(p["project"], p["jql"]) for p in sent.parts]
                break
            pages.append(entry)
        else:
            parts.append({"project": project, "pages": pages})
    return parts


def fetched_keys(parts):
    return [i["key"] for i in ad.adapt_fetch({"parts": parts})["issues"]]


class NextPageJqlTest(unittest.TestCase):
    """``next_page_jql``: the page after a more-match page; None only after the last one."""

    Q = "project = TEST AND status != Closed"

    def test_the_literal_more_match_page_gives_the_next_page_now(self):
        text = page(keys_desc("TEST", 200, 50), final=False)
        self.assertTrue(text.startswith(MORE_MATCH + "\n"))
        expected = "(project = TEST AND status != Closed) AND key < TEST-151 ORDER BY key DESC"
        self.assertEqual(ad.next_page_jql(ad.page_jql(self.Q), text), expected)
        self.assertEqual(ad.next_page_jql(ad.page_jql(self.Q), json.dumps({"result": text})), expected)
        self.assertEqual(ad.page_jql(self.Q), "(project = TEST AND status != Closed) ORDER BY key DESC")

    def test_every_page_is_fetched_until_none_and_never_retried(self):
        log = []
        parts = fetch_by_skill(self.Q, jira_answer(keys_desc("TEST", 300, 107), log=log))
        self.assertEqual(log, ["(project = TEST AND status != Closed) ORDER BY key DESC",
                               "(project = TEST AND status != Closed) AND key < TEST-251 ORDER BY key DESC",
                               "(project = TEST AND status != Closed) AND key < TEST-201 ORDER BY key DESC"])
        out = ad.adapt_fetch({"parts": parts})
        self.assertEqual((out["fetch"]["complete"], out["fetch"]["fetched"], out["fetch"]["problems"]),
                         (True, 107, []))
        self.assertEqual(out["left_out"], [])  # a more-match page is never left out

    def test_only_a_whole_last_page_is_none(self):
        sent = ad.page_jql(self.Q)
        self.assertIsNone(ad.next_page_jql(sent, page(keys_desc("TEST", 12, 12))))
        self.assertIsNone(ad.next_page_jql(sent, f"No issues found for JQL: {sent}"))
        self.assertIsNone(ad.next_page_jql(sent, page(keys_desc("TEST", 60, 50))))  # exactly 50, the last

    def test_an_error_or_cut_off_page_never_looks_like_the_last(self):
        sent = ad.page_jql(self.Q)
        more = page(keys_desc("TEST", 200, 50), final=False)
        cases = {
            "call failed": None,
            "error slot": {"error": "403 Forbidden: no permission"},
            "empty answer": "",
            "fewer issues than the header": page(keys_desc("TEST", 200, 49), final=False, shown=50),
            "no paging hint": more.split("\n\n  ... More issues match")[0] + "\n",
            "last issue cut": more.rpartition("\n    Type: ")[0] + "\n",
            "last page cut": page(keys_desc("TEST", 12, 12)).rpartition("\n    Type: ")[0] + "\n",
            "no results for another query": "No issues found for JQL: project = OTHER",
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(ad.PageNotWhole) as raised:
                ad.next_page_jql(sent, text)
            if name == "error slot":
                self.assertEqual(str(raised.exception), "query_jira call failed: 403 Forbidden: no permission")

    def test_a_page_that_does_not_advance_is_sent_again_once(self):
        sent = "(project = TEST AND status != Closed) AND key < TEST-151 ORDER BY key DESC"
        why = "key TEST-200 on the page is not below the key < TEST-151 sent (the tool did not apply it)"
        stuck = page(keys_desc("TEST", 200, 50), final=False)
        with self.assertRaises(ad.PageDidNotAdvance) as raised:  # a PageNotWhole: the page cannot be used
            ad.next_page_jql(sent, stuck)
        self.assertIsInstance(raised.exception, ad.PageNotWhole)
        self.assertEqual(str(raised.exception), why)
        self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": stuck, "retried": False}})["f"], {"retry": why})
        self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": stuck, "retried": True}})["f"],
                         {"incomplete": why})
        log = []  # a tool that ignores "key <" every time: page 2 sent twice, then the fetch stops with the cause
        parts = fetch_by_skill(self.Q, jira_answer(keys_desc("TEST", 160, 60), ignores_key_below=True, log=log))
        self.assertEqual(len(log), 3)
        self.assertEqual(log[1], log[2])  # sent again unchanged, never a third time, never from the start
        out = ad.adapt_fetch({"parts": parts})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn("key TEST-160 on the page is not below the key < TEST-111 sent (the tool did not apply it)",
                      " ".join(out["fetch"]["problems"]))  # the actual cause, kept to the end
        good, log = jira_answer(keys_desc("TEST", 160, 60)), []
        seen = set()

        def once(sent):  # the tool ignores "key <" on the first send of each query only
            log.append(sent)
            ignore = "key <" in sent and sent not in seen
            seen.add(sent)
            return jira_answer(keys_desc("TEST", 160, 60), ignores_key_below=True)(sent) if ignore else good(sent)
        out = ad.adapt_fetch({"parts": fetch_by_skill(self.Q, once)})
        self.assertEqual((out["fetch"]["complete"], out["fetch"]["fetched"], len(log)), (True, 60, 3))  # recovered

    def test_a_query_whose_cursor_is_outside_its_project_is_not_sent_again(self):
        sent = "(project = TESTA) AND key < TESTB-10 ORDER BY key DESC"  # a query error: resending cannot help
        more = page(keys_desc("TESTA", 200, 50), final=False)
        with self.assertRaises(ad.QueryError):
            ad.next_page_jql(sent, more)
        why = "the key < TESTB-10 sent is not of project TESTA: the query cannot be paged"
        self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": more, "retried": False}})["f"],
                         {"incomplete": why})  # not sent again
        out = ad.adapt_fetch({"pages": [{"text": more, "jql": sent}]})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn(why, " ".join(out["fetch"]["problems"]))  # the cause kept to the end
        with self.assertRaises(ad.QueryError):  # on a last page too
            ad.next_page_jql(sent, page(["TESTA-3"]))

    def test_order_by_is_replaced_and_quoted_text_kept(self):
        page_1 = page(keys_desc("TEST", 200, 50), final=False)
        for sent, expected in (
                ("(project = TEST) order by key desc", "(project = TEST) AND key < TEST-151 ORDER BY key DESC"),
                ("project = TEST ORDER BY key DESC", "(project = TEST) AND key < TEST-151 ORDER BY key DESC"),
                ('(project = TEST AND summary ~ "ORDER BY x") ORDER BY key DESC',
                 '(project = TEST AND summary ~ "ORDER BY x") AND key < TEST-151 ORDER BY key DESC'),
                ("(project = TEST) AND key < TEST-251 ORDER BY key DESC",
                 "(project = TEST) AND key < TEST-151 ORDER BY key DESC")):
            with self.subTest(sent=sent):
                self.assertEqual(ad.next_page_jql(sent, page_1), expected)
        self.assertEqual(ad.strip_order_by('summary ~ "a ORDER BY b"'), 'summary ~ "a ORDER BY b"')

    def test_a_rank_ordered_page_with_more_starts_the_key_fetch(self):
        sent = "(project = TEST AND issuetype = Feature) ORDER BY Rank ASC"
        self.assertIsNone(ad.next_page_jql(sent, page(["TEST-5", "TEST-9", "TEST-7"])))
        self.assertEqual(ad.next_page_jql(sent, page(keys_desc("TEST", 200, 50), final=False)),
                         "(project = TEST AND issuetype = Feature) ORDER BY key DESC")

    def test_the_cursor_stays_in_one_project(self):
        more = page([*keys_desc("TESTA", 200, 49), "TESTB-9"], final=False)
        with self.assertRaises(ad.PageNotWhole):  # a project part never returns another project
            ad.next_page_jql("(p = 1) AND project = TESTA ORDER BY key DESC", more)
        unordered = page([*keys_desc("TEST", 200, 48), "TEST-300", "TEST-100"], final=False)
        with self.assertRaises(ad.PageNotWhole):  # not in key order: no key would be safe
            ad.next_page_jql(ad.page_jql(self.Q), unordered)
        with self.assertRaises(ad.SplitByProject) as raised:  # project = TESTA, yet TESTB on the page
            ad.next_page_jql(ad.page_jql("project = TESTA"), more)
        self.assertEqual([p["jql"] for p in raised.exception.parts],
                         ["(project = TESTA) AND project = TESTA ORDER BY key DESC",
                          "(project = TESTA) AND project = TESTB ORDER BY key DESC",
                          "(project = TESTA) AND project not in (TESTA, TESTB) ORDER BY key DESC"])

    SPRINT_Q = "project in (TESTA, TESTB) AND sprint = 1001"
    SPRINT_KEYS = keys_desc("TESTA", 180, 80) + keys_desc("TESTB", 12, 12)

    def test_named_projects_are_paged_per_project_with_nothing_dropped(self):
        log = []
        parts = fetch_by_skill(self.SPRINT_Q, jira_answer(self.SPRINT_KEYS, log=log))
        self.assertEqual(log, [
            "(project in (TESTA, TESTB) AND sprint = 1001) ORDER BY key DESC",
            "(project in (TESTA, TESTB) AND sprint = 1001) AND project = TESTA ORDER BY key DESC",
            "(project in (TESTA, TESTB) AND sprint = 1001) AND project = TESTA AND key < TESTA-131 ORDER BY key DESC",
            "(project in (TESTA, TESTB) AND sprint = 1001) AND project = TESTB ORDER BY key DESC"])
        self.assertEqual([(p["project"], len(p["pages"])) for p in parts], [("TESTA", 2), ("TESTB", 1)])
        out = ad.adapt_fetch({"parts": parts})
        self.assertEqual((out["fetch"]["complete"], out["fetch"]["problems"]), (True, []))
        self.assertEqual(sorted(fetched_keys(parts)), sorted(self.SPRINT_KEYS))

    def test_splitting_before_the_first_call_is_the_same_fetch(self):
        answer = jira_answer(self.SPRINT_KEYS)
        parts = [{"project": p, "pages": fetch_by_skill(None, answer, first=ad.page_jql(self.SPRINT_Q, project=p))
                  [0]["pages"]} for p in ("TESTA", "TESTB")]
        self.assertEqual(parts, fetch_by_skill(self.SPRINT_Q, answer))

    def test_a_key_list_is_paged_per_project_of_its_keys(self):
        listed = keys_desc("TESTA", 70, 40) + keys_desc("TESTB", 30, 20)
        query = f"key in ({', '.join(listed)}) AND issueLinkType = \"is blocked by\""
        log = []
        parts = fetch_by_skill(query, jira_answer(listed, log=log))
        self.assertEqual([p["project"] for p in parts], ["TESTA", "TESTB"])  # no "every other project" part
        self.assertTrue(log[1].endswith(") AND project = TESTA ORDER BY key DESC"))
        self.assertEqual(sorted(fetched_keys(parts)), sorted(listed))

    def test_unnamed_projects_split_into_the_seen_ones_and_every_other(self):
        query = 'issue in linkedIssues("TEST-1", "is blocked by") AND issuetype = Spike'
        keys = keys_desc("TESTC", 30, 30) + keys_desc("TESTB", 30, 30) + keys_desc("TESTA", 90, 60)
        log = []
        parts = fetch_by_skill(query, jira_answer(keys, log=log))
        base = '(issue in linkedIssues("TEST-1", "is blocked by") AND issuetype = Spike)'
        self.assertEqual(log, [
            f"{base} ORDER BY key DESC",
            f"{base} AND project = TESTC ORDER BY key DESC",
            f"{base} AND project = TESTB ORDER BY key DESC",
            f"{base} AND project not in (TESTC, TESTB) ORDER BY key DESC",  # every other project
            f"{base} AND project = TESTA ORDER BY key DESC",
            f"{base} AND project = TESTA AND key < TESTA-41 ORDER BY key DESC",
            f"{base} AND project not in (TESTC, TESTB, TESTA) ORDER BY key DESC"])
        self.assertEqual([p["project"] for p in parts], ["TESTC", "TESTB", "TESTA", None])
        out = ad.adapt_fetch({"parts": parts})
        self.assertEqual((out["fetch"]["complete"], out["fetch"]["problems"]), (True, []))
        self.assertEqual(sorted(fetched_keys(parts)), sorted(keys))


class StandInTest(unittest.TestCase):
    """The Jira stand-in applies only the clauses paging added after the predicate."""

    def test_a_quoted_parenthesis_is_part_of_the_predicate(self):
        sent = ad.page_jql('summary ~ "x) \\" AND project = TESTB AND (y"', project="TESTA")
        self.assertEqual(after_group(sent), " AND project = TESTA ORDER BY key DESC")
        self.assertEqual(jira_keys(sent, ["TESTA-2", "TESTB-1"]), ["TESTA-2"])


class TrailingOrderTest(unittest.TestCase):
    def test_the_stand_in_reads_the_trailing_order_clause(self):
        sent = ad.page_jql('summary ~ "ORDER BY Rank"')  # rank only inside the literal; ordered by key
        self.assertEqual(jira_keys(sent, ["TEST-1", "TEST-3", "TEST-2"]), ["TEST-3", "TEST-2", "TEST-1"])
        below = ad.page_jql('summary ~ "ORDER BY Rank"', last_key="TEST-3")
        self.assertEqual(jira_keys(below, ["TEST-1", "TEST-3", "TEST-2"]), ["TEST-2", "TEST-1"])
        self.assertEqual(jira_keys("(project = TEST) ORDER BY Rank ASC", ["TEST-1", "TEST-3"]), ["TEST-1", "TEST-3"])


class WrappedAnswerTest(unittest.TestCase):
    """A ``{"result": <text>}`` answer is kept as the page's ``text`` and adapts like bare text."""

    def test_a_wrapped_answer_goes_through_paging_and_adaptation(self):
        bare = jira_answer(keys_desc("TEST", 60, 60))
        parts = fetch_by_skill("project = TEST", lambda sent: {"result": bare(sent)})
        self.assertEqual([len(p["pages"]) for p in parts], [2])
        self.assertTrue(all(isinstance(pg["text"], dict) and "result" in pg["text"] for pg in parts[0]["pages"]))
        out = ad.adapt_fetch({"parts": parts})
        self.assertEqual((out["fetch"]["complete"], out["fetch"]["fetched"]), (True, 60))
        failed = manifest_page({"error": "tool call failed"}, "q")
        self.assertEqual(failed, {"error": "tool call failed", "jql": "q"})  # a failed call keeps its slot
        for reply in ({"result": "x", "error": "boom"}, {"result": "x", "error": None}):
            with self.subTest(reply=reply):  # read the same way as next_page_jql reads it
                slot = manifest_page(reply, "q")
                self.assertEqual("text" in slot, reply["error"] is None)


class NextPageJqlEdgeTest(unittest.TestCase):
    """The less common answers and queries ``next_page_jql`` meets."""

    MORE = page(keys_desc("TEST", 200, 50), final=False)

    def test_an_answer_given_as_a_dict(self):
        sent = ad.page_jql("project = TEST")
        expected = "(project = TEST) AND key < TEST-151 ORDER BY key DESC"
        self.assertEqual(ad.next_page_jql(sent, {"result": self.MORE}), expected)  # already parsed JSON
        self.assertEqual(ad.next_page_jql(sent, {"result": self.MORE, "error": None}), expected)
        self.assertEqual(ad.next_page_jql(sent, {"text": self.MORE, "jql": sent}), expected)  # a manifest page
        self.assertIsNone(ad.next_page_jql(sent, {"result": f"No issues found for JQL: {sent}"}))
        for slot in ({"text": None}, {"error": True}):
            with self.subTest(slot=slot), self.assertRaises(ad.PageNotWhole) as raised:
                ad.next_page_jql(sent, slot)
            self.assertEqual(str(raised.exception), "query_jira call failed")

    def test_a_final_page_must_keep_the_restriction_paging_added(self):
        part = ad.page_jql("issuetype = Story", project="TESTA")
        rest = ad.page_jql("issuetype = Story", excluded=["TESTA"])
        for sent, answer, why in ((part, page(["TESTB-9"]), "issue TESTB-9 is outside project TESTA"),
                                  (rest, page(["TESTB-9", "TESTA-3"]), "excluded project(s) TESTA returned")):
            with self.subTest(sent=sent):
                with self.assertRaises(ad.PageNotWhole) as raised:
                    ad.next_page_jql(sent, answer)  # sent again once (Jira paging step 3)
                self.assertIn(why, str(raised.exception))
                project = "TESTA" if sent == part else None
                out = ad.adapt_fetch({"parts": [{"project": project, "pages": [{"text": answer, "jql": sent}]}]})
                self.assertFalse(out["fetch"]["complete"])  # still rejected after the retry
                self.assertIn(why, "; ".join(out["fetch"]["problems"]))
        no_jql = ad.adapt_fetch({"parts": [{"project": "TESTA", "pages": [{"text": page(["TESTB-9"])}]}]})
        self.assertFalse(no_jql["fetch"]["complete"])  # one page without its query: the part's project still checked
        self.assertIn("issues from other projects in a TESTA-only fetch", no_jql["fetch"]["problems"][0])
        self.assertIsNone(ad.next_page_jql(part, f"No issues found for JQL: {part}"))  # empty and matching
        self.assertIsNone(ad.next_page_jql(part, page(["TESTA-3"])))
        self.assertIsNone(ad.next_page_jql(rest, page(["TESTB-9", "TESTC-1"])))
        anywhere = ad.page_jql("issuetype = Story")
        self.assertIsNone(ad.next_page_jql(anywhere, page(["TESTB-9", "TESTA-3"])))  # no restriction: any project
        out = ad.adapt_fetch({"pages": [{"text": page(["TESTB-9", "TESTA-3"]), "jql": anywhere}]})
        self.assertTrue(out["fetch"]["complete"])

    def test_a_page_listing_an_issue_twice_is_not_whole(self):
        dup = "Found 2 issues (showing 2):\n" + block("TEST-9") + "\n" + block("TEST-9") + "\n"
        sent = ad.page_jql("project = TEST")
        self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": dup, "retried": False}})["f"],
                         {"retry": "issue TEST-9 is listed twice on the page"})  # sent again once
        self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": dup, "retried": True}})["f"],
                         {"incomplete": "issue TEST-9 is listed twice on the page"})
        out = ad.adapt_fetch({"pages": [{"text": dup, "jql": sent}]})
        self.assertFalse(out["fetch"]["complete"])  # never one issue silently dropped
        self.assertIn("issue TEST-9 is listed twice on the page", out["fetch"]["problems"][0])
        rank = "Found 2 issues (showing 2):\n" + block("TEST-3") + "\n" + block("TEST-3") + "\n"
        self.assertEqual(ad.parse_page(rank, jql="(project = TEST) ORDER BY Rank ASC")["status"], "incomplete")
        apart = "Found 3 issues (showing 3):\n" + "\n".join(block(k) for k in ("TEST-3", "TEST-9", "TEST-3")) + "\n"
        self.assertEqual(ad.parse_page(apart, jql="(project = TEST) ORDER BY Rank ASC")["reason"],
                         "issue TEST-3 is listed twice on the page")  # not only next to each other
        mixed = "Found 2 issues (showing 2):\n" + block("TEST-3") + "\n" + block("TEST-9") + "\n"
        self.assertEqual(ad.parse_page(mixed, jql="(project = TEST) ORDER BY Rank ASC")["status"], "ok")  # any order

    def test_a_last_page_must_stay_below_the_cursor(self):
        sent = "(project = TEST) AND key < TEST-151 ORDER BY key DESC"
        bad = page(["TEST-151", "TEST-150"])
        self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": bad, "retried": False}})["f"],
                         {"retry": "key TEST-151 on the last page is not below the key < TEST-151 sent"})
        first = page(keys_desc("TEST", 200, 50), final=False)
        out = ad.adapt_fetch({"pages": [{"text": first, "jql": "(project = TEST) ORDER BY key DESC"},
                                        {"text": bad, "jql": sent}]})
        self.assertFalse(out["fetch"]["complete"])  # still rejected after its retry, with that reason
        self.assertIn("key TEST-151 on the last page is not below the key < TEST-151 sent",
                      "; ".join(out["fetch"]["problems"]))
        self.assertIsNone(ad.next_page_jql(sent, page(["TEST-150", "TEST-149"])))  # below the cursor
        self.assertIsNone(ad.next_page_jql(sent, f"No issues found for JQL: {sent}"))  # empty and matching

    def test_a_last_page_with_a_key_outside_the_cursor_project_is_sent_again(self):
        sent = "(project = TEST) AND key < TEST-151 ORDER BY key DESC"  # a single-project continuation
        why = "issue OTHER-1 is outside project TEST of the key < TEST-151 sent"
        bad = page(["TEST-150", "OTHER-1"])
        self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": bad, "retried": False}})["f"], {"retry": why})
        self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": bad, "retried": True}})["f"], {"incomplete": why})
        anywhere = ad.page_jql("project in (TEST, OTHER)")  # no cursor: a whole last page of both projects
        self.assertIsNone(ad.next_page_jql(anywhere, page(["TEST-3", "OTHER-1"])))
        with self.assertRaises(ad.SplitByProject):  # a more-match page still splits, as before
            ad.next_page_jql(anywhere, page(keys_desc("TEST", 200, 25) + keys_desc("OTHER", 99, 25), final=False))

    def test_a_valid_cursor_page_of_another_project_only_is_sent_again(self):
        sent = "(project = TEST) AND key < TEST-151 ORDER BY key DESC"  # valid: its key < is of its project
        unknown = "(filter = 5) AND key < TEST-151 ORDER BY key DESC"  # the query names no project
        last, more = page(["OTHER-1"]), page(keys_desc("OTHER", 200, 50), final=False)
        for query, answer, key in ((sent, last, "OTHER-1"), (sent, more, "OTHER-200"), (unknown, last, "OTHER-1")):
            why = f"issue {key} is outside project TEST of the key < TEST-151 sent"
            with self.subTest(query=query, key=key):
                self.assertEqual(ad.paging_round({"f": {"jql": query, "page": answer, "retried": False}})["f"],
                                 {"retry": why})  # not a query error: sent again once
                self.assertEqual(ad.paging_round({"f": {"jql": query, "page": answer, "retried": True}})["f"],
                                 {"incomplete": why})  # the exact cause kept
        good, sends = jira_answer(keys_desc("TEST", 160, 60)), []

        def once(query):  # another project's issue on the first send of the key < page only
            sends.append(query)
            return page(["OTHER-1"]) if "key <" in query and query not in sends[:-1] else good(query)
        out = ad.adapt_fetch({"parts": fetch_by_skill("project = TEST", once)})
        self.assertEqual((out["fetch"]["complete"], out["fetch"]["fetched"], len(sends)), (True, 60, 3))  # retried

    def test_a_wrong_cursor_is_decided_from_the_query_not_the_page(self):
        sent = "(project = TESTA) AND key < TESTB-10 ORDER BY key DESC"
        why = "the key < TESTB-10 sent is not of project TESTA: the query cannot be paged"
        for answer in (page(["TESTA-3"]), page(["TESTB-3"]), page(keys_desc("TESTB", 60, 50), final=False),
                       f"No issues found for JQL: {sent}"):  # whatever the page holds, an empty one too
            with self.subTest(answer=answer[:40]):
                self.assertEqual(ad.paging_round({"f": {"jql": sent, "page": answer, "retried": False}})["f"],
                                 {"incomplete": why})  # never sent again
        for query, why in (
                ("(project in (TESTA, TESTB)) AND project = TESTA AND key < TESTB-10 ORDER BY key DESC",
                 "the key < TESTB-10 sent is not of project TESTA: the query cannot be paged"),
                ("(x = 1) AND project not in (TESTA) AND key < TESTA-10 ORDER BY key DESC",
                 "the key < TESTA-10 sent is of excluded project TESTA: the query cannot be paged")):
            with self.subTest(query=query), self.assertRaises(ad.QueryError) as caught:
                ad.next_page_jql(query, page(["TESTB-3"]))
            self.assertEqual(str(caught.exception), why)
        self.assertIsNone(ad.next_page_jql("(project in (TESTA, TESTB)) AND key < TESTA-10 ORDER BY key DESC",
                                           page(["TESTA-3"])))  # a key < of one of its projects: valid
        self.assertIsNone(ad.next_page_jql(ad.page_jql("project in (TESTA, TESTB)"),
                                           page(["TESTB-3", "TESTA-1"])))  # no cursor: whole, as before

    def test_the_reason_paging_stopped_is_kept_and_a_good_retry_leaves_none(self):
        unordered = page(list(reversed(keys_desc("TEST", 200, 50))), final=False)
        why = "keys on the page are not in descending order (ORDER BY key DESC not applied)"
        parts = fetch_by_skill("project = TEST", lambda sent: unordered)
        self.assertEqual(parts[0]["pages"][-1]["incomplete"], why)  # kept with the page
        out = ad.adapt_fetch({"parts": parts})
        self.assertFalse(out["fetch"]["complete"])
        self.assertIn(why, out["fetch"]["problems"][0])  # the actual cause, not "fetch stopped early"
        sends = []
        good = jira_answer(keys_desc("TEST", 60, 60))

        def once(sent):
            sends.append(sent)
            return unordered if len(sends) == 1 else good(sent)
        out = ad.adapt_fetch({"parts": fetch_by_skill("project = TEST", once)})
        self.assertEqual((out["fetch"]["complete"], out["fetch"]["problems"]), (True, []))  # no stale reason

    def test_split_parts_page_together_under_their_own_slots(self):
        log = []
        answer = jira_answer(keys_desc("TESTA", 160, 60) + keys_desc("TESTB", 10, 10), log=log)
        first = ad.page_jql("project in (TESTA, TESTB)")
        split = ad.paging_round({"sprint_issues": {"jql": first, "page": answer(first), "retried": False}})
        parts = {f"sprint_issues/{p['project']}": p for p in split["sprint_issues"]["split"]}
        self.assertEqual(sorted(parts), ["sprint_issues/TESTA", "sprint_issues/TESTB"])
        sent = {slot: part["jql"] for slot, part in parts.items()}
        kept = {slot: [] for slot in parts}
        while sent:  # both parts in every round, each under its own slot
            pending = {slot: {"jql": q, "page": answer(q), "retried": False} for slot, q in sent.items()}
            decisions = ad.paging_round(pending)
            self.assertEqual(set(decisions), set(sent))  # a decision for every part
            for slot, decision in decisions.items():  # the very answer decided, with the query sent
                kept[slot].append({"text": pending[slot]["page"], "jql": pending[slot]["jql"]})
                if "next" in decision:
                    sent[slot] = decision["next"]
                else:
                    del sent[slot]
        out = ad.adapt_fetches({"sprint_issues": {"parts": [
            {"project": parts[slot]["project"], "pages": pages} for slot, pages in kept.items()]}})
        self.assertEqual((out["sprint_issues"]["fetch"]["complete"], out["sprint_issues"]["fetch"]["fetched"]),
                         (True, 70))  # one logical fetch under its own name
        self.assertEqual(sorted(log[1:]), sorted(p["jql"] for pages in kept.values() for p in pages))  # each once

    def test_another_page_size(self):
        sent = ad.page_jql("project = TEST")
        self.assertEqual(ad.next_page_jql(sent, page(keys_desc("TEST", 9, 3), final=False), page_size=3),
                         "(project = TEST) AND key < TEST-7 ORDER BY key DESC")
        with self.assertRaises(ad.PageNotWhole):  # a full 50 is not a page of 3
            ad.next_page_jql(sent, self.MORE, page_size=3)

    def test_a_part_that_does_not_advance(self):
        sent = "(project in (TESTA, TESTB)) AND project = TESTA AND key < TESTA-151 ORDER BY key DESC"
        with self.assertRaises(ad.PageDidNotAdvance):  # the whole page again
            ad.next_page_jql(sent, page(keys_desc("TESTA", 200, 50), final=False))
        with self.assertRaises(ad.PageDidNotAdvance):  # starts above the key < sent, ends below it
            ad.next_page_jql(sent, page(keys_desc("TESTA", 170, 50), final=False))
        with self.assertRaises(ad.QueryError):  # a key < of another project: the query itself is wrong
            ad.next_page_jql("(project = TESTA) AND key < TESTB-10 ORDER BY key DESC",
                             page(keys_desc("TESTA", 200, 50), final=False))

    def test_every_other_project_part_never_returns_an_excluded_one(self):
        sent = "(issue in linkedIssues(\"TEST-1\")) AND project not in (TESTA, TESTB) ORDER BY key DESC"
        with self.assertRaises(ad.PageNotWhole):
            ad.next_page_jql(sent, page(keys_desc("TESTC", 60, 40) + keys_desc("TESTA", 10, 10), final=False))

    def test_or_and_not_leave_the_projects_unknown(self):
        more = page(keys_desc("TESTA", 200, 50), final=False)
        for query in ("project = TESTA OR assignee = x", "project = TESTA AND x = 1 || y = 2",
                      "project = TESTA AND x = 1 OR(y = 2)", "NOT project = TESTB"):
            with self.subTest(query=query), self.assertRaises(ad.SplitByProject) as raised:
                ad.next_page_jql(ad.page_jql(query), more)
            self.assertIsNone(raised.exception.parts[-1]["project"])  # every other project is fetched too
        # A NOT clause says nothing, and the rest of the AND still names the project.
        self.assertEqual(ad.next_page_jql(ad.page_jql("project = TESTA AND NOT status = Closed"), more),
                         "(project = TESTA AND NOT status = Closed) AND key < TESTA-151 ORDER BY key DESC")

    def test_named_projects_with_a_page_outside_them(self):
        more = page(keys_desc("TESTC", 200, 50), final=False)  # the query names TESTA, TESTB
        with self.assertRaises(ad.SplitByProject) as raised:
            ad.next_page_jql(ad.page_jql("project in (TESTA, TESTB)"), more)
        self.assertEqual([(p["project"], p["jql"]) for p in raised.exception.parts], [
            ("TESTC", "(project in (TESTA, TESTB)) AND project = TESTC ORDER BY key DESC"),
            (None, "(project in (TESTA, TESTB)) AND project not in (TESTC) ORDER BY key DESC")])

    def test_a_quoted_project_part_keeps_paging(self):
        self.assertEqual(ad.next_page_jql('(x = 1) AND project = "TEST" ORDER BY key DESC', self.MORE),
                         "(x = 1) AND project = TEST AND key < TEST-151 ORDER BY key DESC")


class SkillPagingTest(unittest.TestCase):
    """Every chai skill with a Jira paging section starts it with the rule, and its worked
    example is what ``next_page_jql`` does."""

    SKILLS = ("sprint-health", "release-health", "weekly-health-report")

    @staticmethod
    def section(skill):
        with open(os.path.join(CHAI, skill, "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        start = text.index("## Jira paging\n")
        return text[start:text.index("\n## ", start + 1)]

    def test_every_paging_section_starts_with_the_rule(self):
        for skill in self.SKILLS:
            with self.subTest(skill=skill):
                lines = [ln for ln in self.section(skill).splitlines()[1:] if ln.strip()]
                self.assertEqual(lines[0], "> " + SKILL_SENTENCE)

    def test_query_jira_is_paged_only_by_these_skills(self):
        for name in sorted(os.listdir(CHAI)):
            with open(os.path.join(CHAI, name, "SKILL.md"), encoding="utf-8") as fh:
                pages_jira = "query_jira" in fh.read()
            self.assertEqual(pages_jira, name in self.SKILLS, name)

    def replay(self, skill, query, keys, first=None):
        log = []
        parts = fetch_by_skill(query, jira_answer(keys, log=log), first=first)
        section = self.section(skill)
        for sent in log:
            self.assertIn(f"`{sent}`", section)
        return parts, log, section

    def test_weekly_example(self):
        parts, log, section = self.replay("weekly-health-report", "project = TESTA AND issuetype = Feature",
                                          keys_desc("TESTA", 300, 107))
        self.assertEqual((len(log), len(fetched_keys(parts))), (3, 107))
        self.assertIn("`Found 7 issues (showing 7):`", section)
        self.assertIn("Three pages, 107 issues", section)

    def test_sprint_health_example(self):
        parts, log, section = self.replay("sprint-health", NextPageJqlTest.SPRINT_Q, NextPageJqlTest.SPRINT_KEYS)
        self.assertEqual((len(log), len(fetched_keys(parts))), (4, 92))
        self.assertIn("`TESTA-131`", section)
        self.assertIn("`Found 30 issues (showing 30):`", section)
        self.assertIn("`Found 12 issues (showing 12):`", section)

    def test_release_health_example(self):
        query, keys = "project = TESTA AND issuetype = Feature", keys_desc("TESTA", 200, 60)
        rank_first = f"({query}) ORDER BY Rank ASC"
        rank_page = jira_answer(keys)(rank_first)
        self.assertTrue(rank_page.startswith(MORE_MATCH))
        key_first = ad.next_page_jql(rank_first, rank_page)
        parts, log, section = self.replay("release-health", query, keys, first=key_first)
        self.assertIn(f"`{rank_first}`", section)
        self.assertEqual((len(log), len(fetched_keys(parts))), (2, 60))
        self.assertIn("`Found 10 issues (showing 10):`", section)
        self.assertIn("2 pages and 60 features", section)


class SharedPagingAdapterTest(unittest.TestCase):
    """One paging round per sandbox call, one adaptation per logical fetch, extra_fields."""

    def test_paging_round_decides_every_page_of_a_round(self):
        sent = ad.page_jql("project = TEST")
        more = page(keys_desc("TEST", 200, 50), final=False)
        split_sent = ad.page_jql("project in (TESTA, TESTB)")
        split_page = page(keys_desc("TESTB", 40, 20) + keys_desc("TESTA", 200, 30), final=False)
        out = ad.paging_round({
            "more": {"jql": sent, "page": more},
            "last": {"jql": sent, "page": page(["TEST-3"])},
            "failed": {"jql": sent, "page": {"error": "timed out"}},
            "failed again": {"jql": sent, "page": None, "retried": True},
            "stuck": {"jql": "(project = TEST) AND key < TEST-151 ORDER BY key DESC", "page": more},
            "split": {"jql": split_sent, "page": split_page}})
        self.assertEqual(out["more"], {"next": "(project = TEST) AND key < TEST-151 ORDER BY key DESC"})
        self.assertEqual(out["last"], {"done": True})
        self.assertEqual(out["failed"], {"retry": "query_jira call failed: timed out"})
        self.assertEqual(out["failed again"], {"incomplete": "query_jira call failed"})
        self.assertIn("retry", out["stuck"])  # a page that did not advance: sent again once
        self.assertEqual([p["project"] for p in out["split"]["split"]], ["TESTA", "TESTB"])

    def test_adapt_fetches_adapts_each_logical_fetch_once(self):
        out = ad.adapt_fetches({"a": {"pages": [{"text": page(["TEST-1", "TEST-2"])}]},
                                "b": {"parts": [{"project": "TEST", "pages": [{"text": page(["TEST-9"])}]}]}})
        self.assertEqual({k: (v["fetch"]["name"], v["fetch"]["fetched"]) for k, v in out.items()},
                         {"a": ("a", 2), "b": ("b", 1)})

    def test_copies_of_one_issue_from_two_queries_of_a_fetch(self):
        def epic(parent, status="In Progress", edit=("", "")):  # one "Parent Link" in (...) query's page
            return {"pages": [{"text": page(["TEST-11"], type_="Epic", status=status, sprints=None, sp=None,
                                            extra=[f"customfield_10018: {parent}"]).replace(*edit)}]}

        def fetch(second):
            return ad.adapt_fetch({"name": "epics", "parts": [epic("FEAT-1"), second]})
        same = fetch(epic("FEAT-1"))  # identical copies (an issue two queries of the fetch both match)
        self.assertEqual((same["fetch"]["complete"], same["fetch"]["fetched"]), (True, 1))
        moved = fetch(epic("FEAT-60"))  # its Parent Link changed between the two reads
        self.assertFalse(moved["fetch"]["complete"])
        self.assertEqual(moved["fetch"]["problems"],
                         ["copies of TEST-11 in this fetch differ in customfield_10018 (it changed between reads)"])
        self.assertEqual(moved["fetch"]["fetched"], 1)
        self.assertNotIn("FEAT-", " ".join(moved["fetch"]["problems"]))  # field names only, never values
        closed = fetch(epic("FEAT-60", status="Closed"))
        self.assertIn("differ in customfield_10018, resolution, status", closed["fetch"]["problems"][0])
        touched = fetch(epic("FEAT-1", edit=("Updated: 2030-02-01", "Updated: 2030-02-02")))  # staleness reads it
        self.assertIn("differ in updated (it changed between reads)", touched["fetch"]["problems"][0])
        unread = fetch(epic("FEAT-1", edit=("Reporter: Sam Sample", "Reporter: Riley Other")))  # no check reads it
        self.assertEqual((unread["fetch"]["complete"], unread["fetch"]["problems"]), (True, []))
        stored = "FEAT-60 ... (value stored; read it with result_page)"
        for second in (epic(stored), None):  # a value not read whole, in the copy dropped or the one kept
            with self.subTest(dropped=second is not None):
                out = (fetch(second) if second else
                       ad.adapt_fetch({"name": "epics", "parts": [epic(stored), epic("FEAT-1")]}))
                self.assertEqual(out["fetch"]["problems"],  # Jira paging step 7, not compared
                                 ["TEST-11: customfield_10018 value shortened (not read whole)"])

    def test_extra_fields_are_known_custom_fields_only(self):
        known = ad.CUSTOM_FIELDS | {"resolutiondate"}
        for name, fields in ad.EXTRA_FIELDS.items():
            with self.subTest(fetch=name):
                self.assertTrue(set(fields) <= known, set(fields) - known)
                self.assertFalse(set(fields) & set(ad.BASE_FIELDS))  # base fields are always printed
        self.assertIn("customfield_10018", ad.WHOLE_VALUE_FIELDS)
        cut = page(["TEST-5"], extra=["customfield_10018: FEAT-1 ... (value stored; read it with result_page)"])
        out = ad.adapt_fetch({"pages": [{"text": cut}]})
        self.assertFalse(out["fetch"]["complete"])  # a shortened Parent Link would put an epic in the wrong release
        self.assertIn("customfield_10018 value shortened", " ".join(out["fetch"]["problems"]))


def _spikes_page(keys):
    return page(keys, type_="Spike")


def _links(key, rows):
    """A whole get_jira_issue(fields="issuelinks") response with these link rows."""
    section = "".join(f"- {w}: {o} (Blocks) | status: To Do | https://example.invalid/browse/{o}\n" for w, o in rows)
    state = ad.LINKS_SET if rows else ad.LINKS_EMPTY
    return (f"Latest Jira State:\n[{key}] Synthetic spike\nType: Spike | Status: To Do\n"
            + (f"Linked Jira Issues:\n{section}" if rows else "")
            + f"Attachments: none\nURL: https://example.invalid/browse/{key}\n\nDescription:\nSynthetic.\n\n"
            f"Requested fields:\nissuelinks: {state}\n\nComments: none\n")


class LinkedSpikeBatchTest(unittest.TestCase):
    """One linked-spike query for many features keeps every association."""

    FEATS = ("FEAT-1", "FEAT-2", "FEAT-3")

    def test_the_batched_query_joins_the_per_feature_queries(self):
        self.assertEqual(ad.linked_spikes_jql(["FEAT-1", "FEAT-2"]),
                         'issuetype = Spike AND (issue in linkedIssues("FEAT-1", "is blocked by") OR '
                         'issue in linkedIssues("FEAT-2", "is blocked by"))')
        with self.assertRaises(ValueError):
            ad.linked_spikes_jql([])  # no feature, no query

    def test_each_spike_goes_to_every_feature_it_blocks(self):
        batch = ad.adapt_fetch({"pages": [{"text": _spikes_page(["TEST-9", "TEST-8"])}]})
        texts = {"TEST-9": _links("TEST-9", [("blocks", "FEAT-1"), ("blocks", "FEAT-3")]),  # two targets
                 "TEST-8": _links("TEST-8", [("blocks", "FEAT-2"), ("is blocked by", "FEAT-3")])}  # one each way
        out = ad.split_linked_spikes(self.FEATS, batch, texts)
        self.assertEqual({k: [i["key"] for i in v["issues"]] for k, v in out.items()},
                         {"FEAT-1": ["TEST-9"], "FEAT-2": ["TEST-8"], "FEAT-3": ["TEST-9"]})
        self.assertTrue(all(v["fetch"]["complete"] for v in out.values()))

    def test_an_unknown_association_fails_the_whole_batch(self):
        batch = ad.adapt_fetch({"pages": [{"text": _spikes_page(["TEST-9", "TEST-8"])}]})
        cases = {"links not read": {"TEST-9": _links("TEST-9", [("blocks", "FEAT-1")]), "TEST-8": None},
                 "blocks none": {"TEST-9": _links("TEST-9", [("blocks", "FEAT-1")]),
                                 "TEST-8": _links("TEST-8", [("blocks", "FEAT-77")])}}
        for name, texts in cases.items():
            with self.subTest(name):
                out = ad.split_linked_spikes(self.FEATS, batch, texts)
                self.assertFalse(any(v["fetch"]["complete"] for v in out.values()))
                self.assertIn("TEST-8", " ".join(out["FEAT-3"]["fetch"]["problems"]))
        failed = ad.adapt_fetch({"pages": [{"error": "timed out"}]})
        out = ad.split_linked_spikes(self.FEATS, failed, {})
        self.assertEqual(out["FEAT-1"]["fetch"]["problems"], ["fetch: page 1: query_jira call failed: timed out"])


if __name__ == "__main__":
    unittest.main()
