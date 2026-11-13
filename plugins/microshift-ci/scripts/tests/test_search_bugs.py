#!/usr/bin/env python3
"""Tests for the deterministic decision policy in search-bugs.py --categorize.

Zero-dependency (stdlib unittest + subprocess + tempfile). Run with:

    python3 plugins/microshift-ci/scripts/tests/test_search_bugs.py

The categorization that decides suggest/linked/skip — including how a CI
failure that matches a closed Jira bug is reported — is deterministic. Each case
builds a merged-candidates JSON, runs --categorize as a subprocess, and asserts
the resulting bug-results-*.json. The script self-checks its output against
_validate_results, so a passing run (exit 0) also proves the byte contract
consumed by --report is satisfied.
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "search-bugs.py"

# Add the scripts directory to sys.path so jira_search can be imported
_scripts_dir = SCRIPT.parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

# Load search-bugs.py as a module for in-process testing
_spec = importlib.util.spec_from_file_location("search_bugs", SCRIPT)
_search_bugs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_search_bugs)


def _merged(candidates, sources=None, date="2026-09-01"):
    """Wrap candidate dicts in a merged-candidates file structure."""
    return {
        "sources": sources or ["4.22"],
        "date": date,
        "total_candidates": len(candidates),
        "candidates": candidates,
    }


def _candidate(sig, failure_type="test", duplicates=None, regressions=None,
               finished=None):
    """Build a minimal merged candidate. Empty duplicates/regressions keys are
    omitted, matching merge_candidate_files output."""
    cand = {
        "error_signature": sig,
        "severity": 3,
        "affected_jobs": len(finished) if finished else 1,
        "step_name": "e2e",
        "failure_type": failure_type,
        "releases": [{"source": "4.22", "affected_jobs": 1}],
        "jobs": [{"job_name": "j", "job_url": "http://x", "finished": f}
                 for f in (finished or [])],
    }
    if duplicates:
        cand["duplicates"] = duplicates
    if regressions:
        cand["regressions"] = regressions
    return cand


def categorize(candidates, sources=None):
    """Run --categorize on *candidates*; return the parsed results list.

    Writes the merged file under <workdir>/bugs/bug-candidates-merged-<tag>.json
    so the default output-path derivation is exercised too.
    """
    with tempfile.TemporaryDirectory() as workdir:
        bugs = Path(workdir) / "bugs"
        bugs.mkdir()
        merged_path = bugs / "bug-candidates-merged-test.json"
        merged_path.write_text(json.dumps(_merged(candidates, sources)))
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--categorize", str(merged_path),
             "--workdir", workdir],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr}"
        out_path = bugs / "bug-results-test.json"
        assert out_path.is_file(), f"no output at {out_path}; stderr: {proc.stderr}"
        data = json.loads(out_path.read_text())
        assert data["mode"] == "search"
        return data["results"]


def one(candidate):
    """Categorize a single candidate and return its result entry."""
    return categorize([candidate])[0]


def make_text_search_fake(issue, matching_text):
    """Build a search_fn faithful to JQL conjunction semantics.

    Returns *issue* only when every `text ~ "term"` in the JQL occurs in
    *matching_text* (the issue's searchable text), so conjunctive
    (multi-keyword) queries behave like real Jira AND-matches.
    """
    def fake_search(jql, **kwargs):
        terms = re.findall(r'text ~ "([^"]*)"', jql)
        if terms and all(t in matching_text for t in terms):
            return [issue]
        return []
    return fake_search


class DecisionPolicyTests(unittest.TestCase):
    def test_infrastructure_skips(self):
        r = one(_candidate("dns timeout", failure_type="infrastructure"))
        self.assertEqual(r["action"], "skip")
        self.assertEqual(r["skip_category"], "infrastructure")
        self.assertEqual(r["jira_key"], "")

    def test_open_duplicate_links_to_first(self):
        r = one(_candidate("etcd crash", duplicates=[
            {"key": "USHIFT-100", "updated": "2026-08-01"},
            {"key": "USHIFT-200", "updated": "2026-08-02"},
        ]))
        self.assertEqual(r["action"], "linked")
        self.assertEqual(r["jira_key"], "USHIFT-100")
        self.assertIn("USHIFT-100", r["reason"])

    def test_duplicate_wins_over_regression(self):
        """Rule order: an open duplicate links even when regressions exist."""
        r = one(_candidate(
            "flake",
            duplicates=[{"key": "USHIFT-1", "updated": "2026-01-01"}],
            regressions=[{"key": "USHIFT-9", "updated": "2026-01-01"}],
            finished=["2026-09-01"],
        ))
        self.assertEqual(r["action"], "linked")
        self.assertEqual(r["jira_key"], "USHIFT-1")

    def test_no_bugs_suggests(self):
        r = one(_candidate("brand new failure", finished=["2026-09-01"]))
        self.assertEqual(r["action"], "suggest")
        self.assertEqual(r["reason"],
                         "No matching existing bugs found — suggest filing a new bug")
        self.assertEqual(r["jira_key"], "")
        self.assertEqual(r["skip_category"], "")

    # A closed bug matching the failure context is only possibly related:
    # its 'updated' date moves on any edit and is not proof that its fix was
    # in the tested code, so job finish dates neither confirm a regression
    # nor hide the failure as stale.

    def assertPossiblyRelated(self, result, *keys):
        self.assertEqual(result["action"], "suggest")
        self.assertEqual(result["jira_key"], "")
        self.assertEqual(result["skip_category"], "")
        self.assertIn("Possibly related to closed", result["reason"])
        self.assertIn("fix inclusion in the tested code is not established",
                      result["reason"])
        self.assertIn("review before filing a new bug", result["reason"])
        self.assertNotIn("regression", result["reason"].lower())
        for key in keys:
            self.assertIn(key, result["reason"])

    def test_closed_match_with_job_after_updated_is_not_a_confirmed_regression(self):
        r = one(_candidate(
            "gitops mismatch",
            regressions=[{"key": "SYNTH-101", "updated": "2026-04-09"}],
            finished=["2026-08-18"],
        ))
        self.assertPossiblyRelated(r, "SYNTH-101")

    def test_closed_match_updated_after_failure_is_not_skipped_as_stale(self):
        """A closed bug edited after the failure (comment, label, triage) says
        nothing about when its fix landed, so the failure stays visible."""
        r = one(_candidate(
            "old failure",
            regressions=[{"key": "SYNTH-102", "updated": "2026-08-01"}],
            finished=["2026-07-01", "2026-08-01"],
        ))
        self.assertPossiblyRelated(r, "SYNTH-102")
        self.assertNotIn("2026-08-01", r["reason"])

    def test_closed_match_with_unlisted_jobs_is_not_skipped(self):
        """affected_jobs can count jobs that a merge with post-Jira bug-matches
        inputs (affected_jobs, no jobs) could not list. Whatever the count,
        the listed jobs' finish dates do not hide the closed match as stale."""
        for affected in (None, 0, 1, 2, 3):
            with self.subTest(affected_jobs=affected):
                cand = _candidate(
                    "old failure",
                    regressions=[{"key": "SYNTH-500", "updated": "2026-08-01"}],
                    finished=["2026-07-01"],  # the only listed job predates 'updated'
                )
                if affected is None:
                    del cand["affected_jobs"]
                else:
                    cand["affected_jobs"] = affected
                r = one(cand)
                self.assertEqual(r["action"], "suggest")
                self.assertEqual(r["jira_key"], "")
                self.assertEqual(r["skip_category"], "")
                self.assertIn("SYNTH-500", r["reason"])

    def test_multiple_closed_matches_are_all_named_in_key_order(self):
        regs = [
            {"key": "SYNTH-202", "updated": "2026-08-15"},
            {"key": "SYNTH-201", "updated": "2026-03-01"},
        ]
        for finished in (["2026-06-01"], ["2026-09-01"]):
            with self.subTest(finished=finished):
                r = one(_candidate("multi", regressions=regs, finished=finished))
                self.assertPossiblyRelated(r, "SYNTH-201", "SYNTH-202")
                self.assertLess(r["reason"].index("SYNTH-201"),
                                r["reason"].index("SYNTH-202"))

    def test_closed_match_with_missing_metadata_is_possibly_related(self):
        """Missing 'updated', match_evidence or finish dates change nothing."""
        cases = [
            ([{"key": "SYNTH-301"}], ["2026-09-01"]),
            ([{"key": "SYNTH-302", "updated": "2026-08-01"}], []),
            ([{"key": "SYNTH-303", "updated": "2026-08-01"}], ["2026-07-01", ""]),
        ]
        for regs, finished in cases:
            with self.subTest(key=regs[0]["key"]):
                r = one(_candidate("missing metadata", regressions=regs,
                                   finished=finished))
                self.assertPossiblyRelated(r, regs[0]["key"])

    def test_one_result_per_candidate_and_signatures_match(self):
        cands = [
            _candidate("a", failure_type="infrastructure"),
            _candidate("b", duplicates=[{"key": "USHIFT-1", "updated": "2026-01-01"}]),
            _candidate("c", finished=["2026-09-01"]),
        ]
        results = categorize(cands)
        self.assertEqual(len(results), 3)
        self.assertEqual(
            {r["error_signature"] for r in results}, {"a", "b", "c"},
        )
        for r in results:
            self.assertTrue(r["reason"])  # never empty


class JiraSearchTests(unittest.TestCase):
    """In-process tests for Jira search functions (JQL builders, search logic)."""

    def test_convert_jira_issue_to_entry_truncates_dates(self):
        """_convert_jira_issue_to_entry truncates updated/created to YYYY-MM-DD."""
        issue = {
            "key": "USHIFT-123",
            "fields": {
                "summary": "Test bug",
                "status": {"name": "New"},
                "assignee": {"displayName": "John Doe"},
                "updated": "2026-08-09T12:34:56.789Z",
                "created": "2026-07-01T08:00:00.000Z",
                "priority": {"name": "Major"},
            }
        }
        entry = _search_bugs._convert_jira_issue_to_entry(issue, include_priority_created=True)
        self.assertEqual(entry["updated"], "2026-08-09")
        self.assertEqual(entry["created"], "2026-07-01")
        self.assertEqual(entry["priority"], "Major")

    def test_jql_builders_have_correct_filters(self):
        """JQL queries filter status correctly for open vs closed bugs."""
        # Open bugs by keyword (for duplicates)
        jql_a = _search_bugs.build_search_jql_open_match_keyword("greenboot")
        self.assertIn("status not in (Closed, Verified)", jql_a)
        self.assertIn('text ~ "greenboot"', jql_a)

        # Open bugs by test ID (for duplicates)
        jql_b_bare = _search_bugs.build_search_jql_open_match_test_id("68256", ocp_prefixed=False)
        self.assertIn("status not in (Closed, Verified)", jql_b_bare)
        self.assertIn('text ~ "68256"', jql_b_bare)

        jql_b_ocp = _search_bugs.build_search_jql_open_match_test_id("68256", ocp_prefixed=True)
        self.assertIn("status not in (Closed, Verified)", jql_b_ocp)
        self.assertIn('text ~ "OCP-68256"', jql_b_ocp)

        # Closed bugs by keyword (for regressions)
        jql_c = _search_bugs.build_search_jql_closed_match_keyword("greenboot")
        self.assertIn("status in (Closed, Verified)", jql_c)
        self.assertIn('text ~ "greenboot"', jql_c)

        # Conjunctive (compound-evidence) builders AND their terms
        jql_d = _search_bugs.build_search_jql_open_match_keywords(
            ["greenboot", "timeout"])
        self.assertIn("status not in (Closed, Verified)", jql_d)
        self.assertIn('text ~ "greenboot" AND text ~ "timeout"', jql_d)

        jql_e = _search_bugs.build_search_jql_closed_match_keywords(
            ["greenboot", "timeout"])
        self.assertIn("status in (Closed, Verified)", jql_e)
        self.assertIn('text ~ "greenboot" AND text ~ "timeout"', jql_e)
        self.assertIn("ORDER BY updated DESC", jql_e)

        inventory_jql = _search_bugs.build_search_jql_open_all()
        self.assertIn("project = USHIFT", inventory_jql)
        self.assertIn('labels = "microshift-ci-ai-generated"', inventory_jql)
        self.assertNotIn("OCPBUGS", inventory_jql)

    def test_find_jira_bugs_for_candidate_emits_both_test_id_forms(self):
        """find_jira_bugs_for_candidate queries both bare '68256' and 'OCP-68256' forms."""
        queries_run = []

        def fake_search(jql, **kwargs):
            queries_run.append(jql)
            return []  # empty results

        cand = {"error_signature": "test OCP-68256 fails"}
        _search_bugs.find_jira_bugs_for_candidate(cand, search_fn=fake_search)

        # Should have queried both "68256" and "OCP-68256"
        bare_queries = [q for q in queries_run if 'text ~ "68256"' in q]
        ocp_queries = [q for q in queries_run if 'text ~ "OCP-68256"' in q]
        self.assertGreater(len(bare_queries), 0, "Should query bare test ID")
        self.assertGreater(len(ocp_queries), 0, "Should query OCP-prefixed test ID")

    def test_find_jira_bugs_for_candidate_dedups_by_key(self):
        """find_jira_bugs_for_candidate dedups duplicates and regressions by key."""
        def fake_search(jql, **kwargs):
            if "status not in" in jql:  # open bugs (A or B)
                return [
                    {"key": "USHIFT-100", "fields": {"summary": "Bug 100", "status": {"name": "New"},
                                                      "assignee": None, "updated": "2026-08-01T00:00:00Z"}},
                    {"key": "USHIFT-100", "fields": {"summary": "Bug 100", "status": {"name": "New"},
                                                      "assignee": None, "updated": "2026-08-01T00:00:00Z"}},  # dup
                ]
            else:  # closed bugs (C)
                # Closed hits must share the failure context to be kept.
                return [
                    {"key": "USHIFT-200", "fields": {"summary": "greenboot timeout on boot", "status": {"name": "Closed"},
                                                      "assignee": None, "updated": "2026-07-01T00:00:00Z"}},
                    {"key": "USHIFT-200", "fields": {"summary": "greenboot timeout on boot", "status": {"name": "Closed"},
                                                      "assignee": None, "updated": "2026-07-01T00:00:00Z"}},  # dup
                ]

        cand = {"error_signature": "greenboot timeout"}
        result = _search_bugs.find_jira_bugs_for_candidate(cand, search_fn=fake_search)

        # Should dedup USHIFT-100 in duplicates
        self.assertEqual(len(result["duplicates"]), 1)
        self.assertEqual(result["duplicates"][0]["key"], "USHIFT-100")

        # Should dedup USHIFT-200 in regressions
        self.assertEqual(len(result["regressions"]), 1)
        self.assertEqual(result["regressions"][0]["key"], "USHIFT-200")

    def test_single_keyword_hit_is_not_accepted(self):
        issue = {"key": "OCPBUGS-126892", "fields": {
            "summary": "Unrelated generic timeout", "status": {"name": "New"},
            "assignee": None, "updated": "2026-08-01T00:00:00Z"}}

        # The issue matches only the "timeout" keyword; conjunctive pair
        # queries must not return it (faithful AND semantics).
        fake_search = make_text_search_fake(issue, "unrelated generic timeout")

        result = _search_bugs.find_jira_bugs_for_candidate(
            {"error_signature": "greenboot health timeout"}, search_fn=fake_search)
        self.assertEqual(result["duplicates"], [])

    def test_two_keyword_hits_are_accepted(self):
        issue = {"key": "USHIFT-100", "fields": {
            "summary": "Greenboot health timeout", "status": {"name": "New"},
            "assignee": None, "updated": "2026-08-01T00:00:00Z"}}

        fake_search = make_text_search_fake(issue, "greenboot health timeout")

        result = _search_bugs.find_jira_bugs_for_candidate(
            {"error_signature": "greenboot health timeout"}, search_fn=fake_search)
        self.assertEqual([d["key"] for d in result["duplicates"]], ["USHIFT-100"])

    def test_compound_match_outside_single_keyword_page_is_accepted(self):
        """A duplicate matching two keywords is accepted even when it falls
        outside the first page of a broad single-keyword query (the conjunctive
        pair query retrieves the compound evidence directly)."""
        issue = {"key": "USHIFT-100", "fields": {
            "summary": "Greenboot health timeout", "status": {"name": "New"},
            "assignee": None, "updated": "2026-08-01T00:00:00Z"}}

        def fake_search(jql, **kwargs):
            terms = re.findall(r'text ~ "([^"]*)"', jql)
            if terms == ["greenboot"]:
                # Broad keyword: its first page is full of unrelated issues
                # and does not include the duplicate.
                return [{"key": f"USHIFT-{n}", "fields": {
                    "summary": f"unrelated greenboot issue {n}",
                    "status": {"name": "New"}, "assignee": None,
                    "updated": "2026-08-01T00:00:00Z"}} for n in range(5)]
            if terms and all(t in "greenboot health timeout" for t in terms):
                return [issue]
            return []

        result = _search_bugs.find_jira_bugs_for_candidate(
            {"error_signature": "greenboot health timeout"}, search_fn=fake_search)
        self.assertEqual([d["key"] for d in result["duplicates"]], ["USHIFT-100"])

    def test_bare_id_keyword_match_outside_bare_id_page_is_accepted(self):
        """A duplicate matching a bare test ID and a keyword is accepted even
        when it falls outside the first page of the weak bare-ID query (the
        conjunctive tid+keyword query retrieves the compound evidence
        directly)."""
        issue = {"key": "USHIFT-68256", "fields": {
            "summary": "Greenboot 68256", "status": {"name": "New"},
            "assignee": None, "updated": "2026-08-01T00:00:00Z"}}

        def fake_search(jql, **kwargs):
            terms = re.findall(r'text ~ "([^"]*)"', jql)
            if terms == ["68256"]:
                # A bare 4-6 digit number is a weak search term: the first
                # page fills with build/PR-number matches, not the duplicate.
                return [{"key": f"USHIFT-90{n}", "fields": {
                    "summary": f"unrelated build 68256 ref {n}",
                    "status": {"name": "New"}, "assignee": None,
                    "updated": "2026-08-01T00:00:00Z"}} for n in range(5)]
            if terms and all(t in "greenboot 68256" for t in terms):
                return [issue]
            return []

        result = _search_bugs.find_jira_bugs_for_candidate(
            {"error_signature": "greenboot case 68256 failed"}, search_fn=fake_search)
        self.assertEqual([d["key"] for d in result["duplicates"]], ["USHIFT-68256"])

    def test_ocp_test_id_hit_is_accepted(self):
        issue = {"key": "OCPBUGS-100", "fields": {
            "summary": "OCP-68256 failed", "status": {"name": "New"},
            "assignee": None, "updated": "2026-08-01T00:00:00Z"}}

        def fake_search(jql, **kwargs):
            return [issue] if 'text ~ "OCP-68256"' in jql else []

        result = _search_bugs.find_jira_bugs_for_candidate(
            {"error_signature": "OCP-68256 failed"}, search_fn=fake_search)
        self.assertEqual([d["key"] for d in result["duplicates"]], ["OCPBUGS-100"])

    def test_bare_id_requires_keyword(self):
        issue = {"key": "USHIFT-68256", "fields": {
            "summary": "Greenboot 68256", "status": {"name": "New"},
            "assignee": None, "updated": "2026-08-01T00:00:00Z"}}

        candidate = {"error_signature": "greenboot case 68256 failed"}

        # The issue's text carries the bare test ID but no keyword.
        bare_only = make_text_search_fake(issue, "68256")
        result = _search_bugs.find_jira_bugs_for_candidate(
            candidate, search_fn=bare_only)
        self.assertEqual(result["duplicates"], [])

        # The issue's text carries the bare test ID and one keyword.
        bare_and_keyword = make_text_search_fake(issue, "greenboot 68256")
        result = _search_bugs.find_jira_bugs_for_candidate(
            candidate, search_fn=bare_and_keyword)
        self.assertEqual([d["key"] for d in result["duplicates"]], ["USHIFT-68256"])

    def test_find_jira_bugs_for_source_preserves_candidate_fields(self):
        """find_jira_bugs_for_source output includes required fields for bug-matches contract."""
        def fake_search(jql, **kwargs):
            return []

        candidates_data = {
            "source": "4.22",
            "candidates": [
                {"error_signature": "etcd crash", "severity": 5, "failure_type": "test",
                 "step_name": "e2e", "affected_jobs": 3},
            ]
        }

        result = _search_bugs.find_jira_bugs_for_source(candidates_data, search_fn=fake_search, include_open_bugs=False)

        self.assertEqual(result["source"], "4.22")
        self.assertEqual(len(result["candidates"]), 1)
        cand = result["candidates"][0]
        self.assertEqual(cand["error_signature"], "etcd crash")
        self.assertEqual(cand["severity"], 5)
        self.assertEqual(cand["failure_type"], "test")
        self.assertEqual(cand["step_name"], "e2e")
        self.assertEqual(cand["affected_jobs"], 3)
        self.assertIn("duplicates", cand)
        self.assertIn("regressions", cand)

    def test_find_jira_bugs_for_source_includes_open_bugs_when_requested(self):
        """find_jira_bugs_for_source includes top-level open_bugs when include_open_bugs=True."""
        def fake_search(jql, **kwargs):
            if "status not in (Closed, Verified) ORDER BY" in jql:  # broad open-bugs query
                return [
                    {"key": "USHIFT-300", "fields": {"summary": "Open bug", "status": {"name": "New"},
                                                      "assignee": None, "updated": "2026-08-15T00:00:00Z",
                                                      "created": "2026-08-01T00:00:00Z", "priority": {"name": "Major"}}},
                ]
            return []

        candidates_data = {"source": "4.22", "candidates": []}
        result = _search_bugs.find_jira_bugs_for_source(candidates_data, search_fn=fake_search, include_open_bugs=True)

        self.assertIn("open_bugs", result)
        self.assertEqual(len(result["open_bugs"]), 1)
        self.assertEqual(result["open_bugs"][0]["key"], "USHIFT-300")
        self.assertEqual(result["open_bugs"][0]["priority"], "Major")

    def test_search_failure_propagates(self):
        def failed_search(jql, **kwargs):
            raise _search_bugs.jira_search.JiraSearchError("network failed")

        with self.assertRaises(_search_bugs.jira_search.JiraSearchError):
            _search_bugs.find_jira_bugs_for_candidate(
                {"error_signature": "test fails"}, search_fn=failed_search)


def _closed_issue(key, summary, description=None, status="Closed",
                  updated="2026-08-01T00:00:00Z"):
    """Build a Jira API issue as returned by the closed-bug search."""
    fields = {"summary": summary, "status": {"name": status},
              "assignee": None, "updated": updated}
    if description is not None:
        fields["description"] = description
    return {"key": key, "fields": fields}


def _adf(text):
    """Wrap text in a minimal Atlassian Document Format code block."""
    return {"type": "doc", "version": 1, "content": [
        {"type": "codeBlock", "content": [{"type": "text", "text": text}]}]}


class ClosedMatchContextTests(unittest.TestCase):
    """Closed Jira hits must share the failure context before being kept.

    Each fake search returns its closed issue for every closed-bug query,
    standing in for Jira's broad stemmed text search, so the local
    failure-context check is what decides acceptance.
    """

    BUILD_PATH_SIG = "toolchain download timed out in tools/pkg-build/build-rpm.sh"

    @staticmethod
    def closed_search(*issues, queries=None):
        def fake_search(jql, **kwargs):
            if queries is not None:
                queries.append((jql, kwargs.get("fields", "")))
            return list(issues) if "status in (Closed, Verified)" in jql else []
        return fake_search

    def find(self, signature, *issues, queries=None):
        return _search_bugs.find_jira_bugs_for_candidate(
            {"error_signature": signature},
            search_fn=self.closed_search(*issues, queries=queries))

    def test_version_like_tokens(self):
        for token in ("1.2.3", "go9.87.6", "rhel-10.2", "release-4.18",
                      "4.18.0-rc.1", "x86_64", "68256", "ocp-68256", "v2",
                      "base-9-tool-9.87-dist-1.2", "component-1.31",
                      # short names are indistinguishable from "go1"/"el9"
                      "ipv6", "x509", "k8s"):
            with self.subTest(token=token):
                self.assertTrue(_search_bugs.is_version_like(token))
        for token in ("toolchain", "build-rpm.sh", "pkg-build",
                      "kube-apiserver", "v1beta1", "3-node", "go.mod"):
            with self.subTest(token=token):
                self.assertFalse(_search_bugs.is_version_like(token))

    def test_context_terms_exclude_version_tokens(self):
        sig = "toolchain mismatch: go9.87.6 builder requires 9.87.6 on rhel-10.2"
        # Keyword ranking favours punctuated/numeric tokens ...
        self.assertTrue(all(_search_bugs.is_version_like(kw)
                            for kw in _search_bugs.extract_keywords(sig)[:2]))
        # ... so context terms leave them out.
        context, ignored = _search_bugs.extract_context_terms(sig)
        self.assertEqual(context, ["toolchain", "mismatch", "requires", "builder"])
        self.assertEqual(ignored, ["rhel-10.2", "go9.87.6", "9.87.6"])

    def test_context_terms_drop_dotted_abbreviations(self):
        context, _ = _search_bugs.extract_context_terms(
            "toolchain download timed out, e.g. in build-rpm.sh")
        self.assertNotIn("e.g", context)
        self.assertEqual(context[0], "build-rpm.sh")

    def test_required_context_terms(self):
        required = _search_bugs.required_context_terms
        self.assertEqual(required([]), [])
        self.assertEqual(required(["toolchain"]), [])
        self.assertEqual(required(["toolchain", "download", "timed"]),
                         ["toolchain", "download"])
        self.assertEqual(required(["build-rpm.sh", "toolchain", "run_x"]),
                         ["build-rpm.sh", "toolchain"])
        # A structured identifier outside the top two is required as well,
        # and therefore also part of the retrieval query.
        self.assertEqual(
            required(["toolchain", "download", "run_x", "build-rpm.sh"]),
            ["toolchain", "download", "run_x"])

    def test_jira_text_flattens_adf(self):
        text = _search_bugs._jira_text
        doc = {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "failed in tools/pkg-"},
                {"type": "text", "text": "build/build-",
                 "marks": [{"type": "strong"}]},
                {"type": "text", "text": "rpm.sh"}]},
            {"type": "bulletList", "content": [
                {"type": "listItem", "content": [
                    {"type": "paragraph", "content": [
                        {"type": "text", "text": "toolchain"},
                        {"type": "hardBreak"},
                        {"type": "inlineCard",
                         "attrs": {"url": "https://example.test/img-build"}},
                        {"type": "text", "text": " download"}]}]}]},
            {"type": "rule"},
            {"type": "paragraph"},
            {"type": "text", "text": 5},
        ]}
        tokens = _search_bugs.tokenize(text(doc))
        for token in ("pkg-build", "build-rpm.sh", "toolchain", "img-build",
                      "download"):
            self.assertIn(token, tokens)
        self.assertNotIn("build", tokens)  # a mark boundary does not split
        self.assertEqual(text("plain description"), "plain description")
        self.assertEqual(text(["a", {"type": "text", "text": "b"}]), "a\nb")
        for empty in (None, 5, {}, {"type": "text", "text": 5},
                      {"type": "mention", "attrs": None}):
            with self.subTest(value=empty):
                self.assertEqual(text(empty), "")

    def test_mixed_result_page_keeps_only_contextual_hits_in_key_order(self):
        genuine = "toolchain download timed out in tools/pkg-build/build-rpm.sh"
        issues = [
            _closed_issue("SYNTH-23", genuine),
            _closed_issue("SYNTH-22", "toolchain download timed out",
                          "Seen in tools/img-build/build-iso.sh only."),
            _closed_issue("SYNTH-21", "build-rpm.sh in pkg-build", genuine),
            _closed_issue("SYNTH-23", genuine),
        ]
        result = self.find(self.BUILD_PATH_SIG, *issues)
        self.assertEqual([r["key"] for r in result["regressions"]],
                         ["SYNTH-21", "SYNTH-23"])

    def test_versioned_name_in_signature_is_not_context(self):
        unrelated = _closed_issue(
            "SYNTH-9", "Bump builder to base-9-tool-9.87-dist-1.2",
            "Pull it from registry.example.test.")
        result = self.find(
            "version mismatch: registry.example.test/builder:"
            "base-9-tool-9.87-dist-1.2 requires go9.87.6", unrelated)
        self.assertEqual(result["regressions"], [])

    def test_numeric_only_unrelated_closed_hit_is_rejected(self):
        queries = []
        unrelated = _closed_issue(
            "SYNTH-1", "Rebuild images with go9.87.6 on rhel-10.2",
            "Pin 9.87.6 in every builder image.")
        result = self.find(
            "toolchain mismatch: go9.87.6 builder requires 9.87.6 on rhel-10.2",
            unrelated, queries=queries)
        self.assertEqual(result["regressions"], [])
        closed = [(jql, fields) for jql, fields in queries
                  if "status in (Closed, Verified)" in jql]
        self.assertEqual(len(closed), 1)
        jql, fields = closed[0]
        self.assertIn('text ~ "toolchain" AND text ~ "mismatch"', jql)
        for version in ("9.87.6", "rhel-10.2"):
            self.assertNotIn(version, jql)
        self.assertIn("description", fields.split(","))

    def test_same_symptom_on_different_build_path_is_rejected(self):
        other_path = _closed_issue(
            "SYNTH-2", "toolchain download timed out",
            "Seen in tools/img-build/build-iso.sh only.")
        self.assertEqual(
            self.find(self.BUILD_PATH_SIG, other_path)["regressions"], [])

        # A structured identifier must match even when the two most specific
        # terms are plain words.
        terms = ["toolchain", "download", "build-rpm.sh"]
        self.assertIsNone(_search_bugs.match_closed_issue_context(
            other_path, terms))
        same_path = _closed_issue(
            "SYNTH-3", "toolchain download timed out", "Seen in build-rpm.sh.")
        self.assertIsNotNone(_search_bugs.match_closed_issue_context(
            same_path, terms))

    def test_genuine_contextual_match_is_kept_with_evidence(self):
        queries = []
        genuine = _closed_issue(
            "SYNTH-4", "toolchain download timed out",
            _adf("tools/pkg-build/build-rpm.sh: toolchain download timed out "
                 "(go9.87.6)"),
            status="Verified")
        result = self.find(self.BUILD_PATH_SIG + " with go9.87.6", genuine,
                           queries=queries)
        self.assertEqual(len(result["regressions"]), 1)
        entry = result["regressions"][0]
        self.assertEqual(entry["key"], "SYNTH-4")
        self.assertEqual(entry["status"], "Verified")
        self.assertEqual(entry["match_evidence"], {
            "matched_terms": ["build-rpm.sh", "pkg-build", "toolchain",
                              "download", "timed", "tools", "out"],
            "matched_fields": ["summary", "description"],
            "ignored_terms": ["go9.87.6"],
        })
        closed_jql = [jql for jql, _ in queries
                      if "status in (Closed, Verified)" in jql]
        self.assertEqual(len(closed_jql), 1)
        self.assertIn('text ~ "build-rpm.sh" AND text ~ "pkg-build"',
                      closed_jql[0])

    def test_closed_hit_with_missing_metadata_is_rejected(self):
        for issue in ({"key": "SYNTH-5"},
                      {"key": "SYNTH-6", "fields": None},
                      {"key": "SYNTH-7", "fields": {"summary": None,
                                                     "description": None}},
                      {"fields": {"summary": self.BUILD_PATH_SIG}}):
            with self.subTest(issue=issue):
                self.assertEqual(
                    self.find(self.BUILD_PATH_SIG, issue)["regressions"], [])

    def test_signature_without_two_context_terms_skips_closed_search(self):
        hit = _closed_issue("SYNTH-8", "anything 9.87.6")
        for sig in ("", "9.87.6 go9.87.6 rhel-10.2", "toolchain 9.87.6"):
            with self.subTest(sig=sig):
                queries = []
                self.assertEqual(self.find(sig, hit, queries=queries)["regressions"], [])
                self.assertFalse(any("status in (Closed, Verified)" in jql
                                     for jql, _ in queries))


class ClosedSearchPaginationTests(unittest.TestCase):
    """Closed-bug matches read every result page and fail closed."""

    SIG = "toolchain download timed out in tools/pkg-build/build-rpm.sh"

    @staticmethod
    def filler(count):
        return [_closed_issue(f"SYNTH-{1000 + i}", f"unrelated closed bug {i}")
                for i in range(count)]

    @staticmethod
    def qualifying():
        return _closed_issue(
            "SYNTH-51", "toolchain download timed out",
            _adf("tools/pkg-build/build-rpm.sh: toolchain download timed out"))

    def test_production_defaults_use_paginated_search(self):
        search_fn, search_all_fn = _search_bugs._resolve_search_fns()
        self.assertIs(search_fn, _search_bugs.jira_search.search)
        self.assertIs(search_all_fn, _search_bugs.jira_search.search_all)

    def test_qualifying_closed_hit_beyond_first_page_is_kept(self):
        page_queries, all_queries = [], []

        def search_fn(jql, **kwargs):
            page_queries.append((jql, kwargs.get("max_results")))
            return []

        def search_all_fn(jql, fields):
            all_queries.append((jql, fields))
            return self.filler(50) + [self.qualifying()]

        result = _search_bugs.find_jira_bugs_for_candidate(
            {"error_signature": self.SIG}, search_fn=search_fn,
            search_all_fn=search_all_fn)
        self.assertEqual([r["key"] for r in result["regressions"]], ["SYNTH-51"])
        self.assertEqual(len(all_queries), 1)
        self.assertIn("status in (Closed, Verified)", all_queries[0][0])
        self.assertIn("description", all_queries[0][1].split(","))
        # Open-bug duplicate queries stay on bounded single pages.
        self.assertTrue(page_queries)
        self.assertFalse(any("status in (Closed, Verified)" in jql
                             for jql, _ in page_queries))
        self.assertTrue(all(limit in (5, 50) for _, limit in page_queries))

    def test_closed_search_failure_propagates(self):
        def search_all_fn(jql, fields):
            raise _search_bugs.jira_search.JiraSearchError("incomplete result")

        with self.assertRaises(_search_bugs.jira_search.JiraSearchError):
            _search_bugs.find_jira_bugs_for_candidate(
                {"error_signature": self.SIG},
                search_fn=lambda jql, **kwargs: [], search_all_fn=search_all_fn)

    def test_source_search_threads_search_all_to_closed_matches(self):
        all_queries = []

        def search_all_fn(jql, fields):
            all_queries.append(jql)
            if "status in (Closed, Verified)" in jql:
                return self.filler(50) + [self.qualifying()]
            return []

        result = _search_bugs.find_jira_bugs_for_source(
            {"source": "4.22", "candidates": [{"error_signature": self.SIG}]},
            search_fn=lambda jql, **kwargs: [], include_open_bugs=True,
            search_all_fn=search_all_fn)
        self.assertEqual(
            [r["key"] for r in result["candidates"][0]["regressions"]],
            ["SYNTH-51"])
        self.assertEqual(
            sum("status in (Closed, Verified)" in q for q in all_queries), 1)
        self.assertEqual(result["open_bugs"], [])


class _JiraResponse:
    def __init__(self, data):
        self.body = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


class ClosedSearchCliTests(unittest.TestCase):
    """--pipeline reads closed matches through the real paginated client.

    Only urlopen is faked: main(), jira_search.search_all and _request run
    unchanged, so the CLI path and the pagination/error checks are covered.
    """

    SIG = ClosedSearchPaginationTests.SIG

    def setUp(self):
        self.addCleanup(_search_bugs.configure_component,
                        _search_bugs.DEFAULT_COMPONENT)
        env = mock.patch.dict(os.environ, {"JIRA_USERNAME": "user",
                                           "JIRA_API_TOKEN": "token"})
        env.start()
        self.addCleanup(env.stop)
        self.requests = []

    def run_pipeline(self, workdir, component, closed_pages):
        jobs = Path(workdir) / "jobs"
        jobs.mkdir()
        (jobs / "release-4.22-job-0-1.json").write_text(json.dumps([{
            "severity": 3, "stack_layer": "test", "step_name": "build",
            "error_signature": self.SIG, "root_cause": "rc",
            "raw_error": self.SIG, "infrastructure_failure": False,
            "job_url": "https://prow.example/1", "job_name": "periodic-build",
            "release": "4.22", "remediation": "", "finished": "2026-09-01"}]))

        def urlopen(request, timeout=None):
            payload = json.loads(request.data)
            self.requests.append(payload)
            if "status in (Closed, Verified)" in payload["jql"]:
                return _JiraResponse(closed_pages[payload.get("nextPageToken")])
            return _JiraResponse({"issues": [], "isLast": True})

        argv = [str(SCRIPT), "--component", component, "--pipeline", "4.22",
                "--workdir", workdir]
        with mock.patch.object(_search_bugs.jira_search.urllib.request,
                               "urlopen", urlopen), \
                mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            _search_bugs.main()

    def test_pipeline_reads_closed_results_past_first_page(self):
        pages = {
            None: {"issues": ClosedSearchPaginationTests.filler(50),
                   "isLast": False, "nextPageToken": "page-2"},
            "page-2": {"issues": [ClosedSearchPaginationTests.qualifying()],
                       "isLast": True},
        }
        for component in ("microshift", "lvm-operator"):
            with self.subTest(component=component), \
                    tempfile.TemporaryDirectory() as workdir:
                self.requests = []
                self.run_pipeline(workdir, component, pages)
                bugs = Path(workdir) / "bugs"
                matches = json.loads((bugs / "bug-matches-4.22.json").read_text())
                regressions = matches["candidates"][0]["regressions"]
                self.assertEqual([r["key"] for r in regressions], ["SYNTH-51"])
                self.assertIn("match_evidence", regressions[0])
                closed = [r for r in self.requests
                          if "status in (Closed, Verified)" in r["jql"]]
                self.assertEqual([r.get("nextPageToken") for r in closed],
                                 [None, "page-2"])
                self.assertTrue(all(r["maxResults"] == 50 for r in closed))
                report = (bugs / "bug-search-4.22.txt").read_text()
                self.assertIn("SYNTH-51 [Closed] (matched:", report)

    def test_incomplete_closed_pagination_fails_without_publishing(self):
        pages = {None: {"issues": [ClosedSearchPaginationTests.qualifying()],
                        "isLast": False}}
        with tempfile.TemporaryDirectory() as workdir:
            with self.assertRaisesRegex(
                    _search_bugs.jira_search.JiraSearchError, "nextPageToken"):
                self.run_pipeline(workdir, "microshift", pages)
            bugs = Path(workdir) / "bugs"
            self.assertFalse((bugs / "bug-matches-4.22.json").exists())
            self.assertFalse((bugs / "bug-results-4.22.json").exists())


class ClosedMatchReportTests(unittest.TestCase):
    """match_evidence survives merge, categorize and the text report."""

    SIG = "toolchain download timed out in tools/pkg-build/build-rpm.sh"

    def merged(self, regressions):
        with tempfile.TemporaryDirectory() as workdir:
            bugs = Path(workdir) / "bugs"
            bugs.mkdir()
            cand = _candidate(self.SIG, finished=["2026-07-01"])
            (bugs / "bug-candidates-4.22.json").write_text(json.dumps(
                {"source": "4.22", "candidates": [cand]}))
            (bugs / "bug-matches-4.22.json").write_text(json.dumps(
                {"source": "4.22", "candidates": [{
                    "error_signature": self.SIG, "duplicates": [],
                    "regressions": regressions}]}))
            return _search_bugs.merge_candidate_files(
                [str(bugs / "bug-candidates-4.22.json")], workdir=workdir)

    def test_evidence_reaches_text_report(self):
        evidence = {"matched_terms": ["build-rpm.sh", "pkg-build", "toolchain"],
                    "matched_fields": ["description"], "ignored_terms": []}
        merged = self.merged([
            {"key": "SYNTH-11", "summary": "s", "status": "Closed",
             "updated": "2026-08-01", "match_evidence": evidence},
            {"key": "SYNTH-12", "summary": "legacy", "status": "Verified"},
        ])
        regs = {r["key"]: r for r in merged["candidates"][0]["regressions"]}
        self.assertEqual(regs["SYNTH-11"]["match_evidence"], evidence)

        results = _search_bugs.categorize_candidates(merged)
        report = _search_bugs.format_report(merged, results)
        self.assertIn(
            "Possibly Related Closed Bugs (fix inclusion unverified): "
            "SYNTH-11 [Closed] (matched: build-rpm.sh, pkg-build, toolchain); "
            "SYNTH-12 [Verified] (match evidence unavailable)", report)
        self.assertIn("Decision: Possibly related to closed SYNTH-11, SYNTH-12;",
                      report)
        self.assertIn("    Possibly related to closed bugs: 1", report)
        self.assertNotIn("Potential Regressions", report)
        self.assertNotIn("Skipped (stale regression)", report)

    def test_legacy_stale_results_still_report(self):
        merged = self.merged([{"key": "SYNTH-13", "status": "Closed"}])
        results = {"mode": "search", "date": "2026-09-01", "results": [{
            "error_signature": self.SIG, "action": "skip", "jira_key": "",
            "skip_category": "stale_regression", "reason": "legacy decision"}]}
        _search_bugs._validate_results(results, merged)
        report = _search_bugs.format_report(merged, results)
        self.assertIn("  Skipped (stale regression): 1", report)
        self.assertIn("    Possibly related to closed bugs: 0", report)


class JiraMergeTests(unittest.TestCase):
    @staticmethod
    def candidate(signature, duplicates=None, regressions=None):
        return {"error_signature": signature,
                "duplicates": [{"key": key} for key in (duplicates or [])],
                "regressions": [{"key": key} for key in (regressions or [])]}

    def test_jira_bridge_does_not_merge_all_groups(self):
        groups = [
            [self.candidate("a", ["X"])],
            [self.candidate("b", ["X", "Y"])],
            [self.candidate("c", ["Y"])],
        ]
        merged = _search_bugs._merge_groups_by_jira(groups)
        self.assertEqual(sorted(len(group) for group in merged), [1, 2])

    def test_regressions_never_merge_groups(self):
        groups = [
            [self.candidate("a", regressions=["X"])],
            [self.candidate("b", regressions=["X"])],
        ]
        self.assertEqual(len(_search_bugs._merge_groups_by_jira(groups)), 2)


def _source_candidate(sig, step_name, job_ids, raw_error="", root_cause="",
                      severity=5):
    """Build a per-source (prepare-stage) candidate, as build_candidates
    writes it, with one job entry per id in *job_ids*."""
    return {
        "error_signature": sig,
        "root_cause": root_cause,
        "raw_error": raw_error,
        "remediation": "",
        "severity": severity,
        "failure_type": "build",
        "step_name": step_name,
        "affected_jobs": len(job_ids),
        "confidence": "high",
        "causal_chain": [],
        "analysis_gaps": [],
        "scenarios": [],
        "keywords": [],
        "test_ids": [],
        "jobs": [{"job_name": f"job-{i}",
                  "job_url": f"https://prow.example/{i}",
                  "finished": "2026-11-13"} for i in job_ids],
    }


class ExactSignatureMergeTests(unittest.TestCase):
    """merge_candidate_files must emit unique error_signatures.

    Categorize, _validate_results, format_report and create-report key every
    merged candidate by its exact error_signature. Prow run
    2121136260092268544 aborted the bugs stage because one rebase source held
    the same signature in two CI steps, which the step-bucketed fuzzy merge
    kept apart.
    """

    # Signature, steps and job counts observed in that run's
    # bug-candidates-rebase-release-5.1.json.
    SIG = ("RPM build fails: OLM kustomization.x86_64.yaml deleted by rebase "
           "but still referenced in spec")
    RAW = ("cat: assets/optional/operator-lifecycle-manager/"
           "kustomization.x86_64.yaml: No such file or directory")

    def _merge(self, files):
        """Write {source: candidates} files and merge them in order."""
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for index, (source, candidates) in enumerate(files):
                path = Path(tmp) / f"bug-candidates-{index}-{source}.json"
                path.write_text(json.dumps(
                    {"source": source, "candidates": candidates}))
                paths.append(str(path))
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                return _search_bugs.merge_candidate_files(paths)

    def _observed_rebase_source(self):
        return [
            _source_candidate(
                self.SIG, "openshift-microshift-infra-rpm-install-aws", [1, 2],
                raw_error=self.RAW,
                root_cause="rebase PR removed OLM arch-specific kustomization "
                           "files without updating RPM spec references"),
            _source_candidate(
                self.SIG, "test-rpm", [3],
                raw_error=self.RAW,
                root_cause="auto-rebase script deleted OLM kustomization "
                           "overlays without updating RPM spec file"),
            _source_candidate(
                "verify-assets: stale entries in assets.yaml reference "
                "deleted files", "verify-verify", [4], severity=4),
        ]

    def test_same_source_identical_signature_across_steps_merges(self):
        merged = self._merge([("rebase-release-5.1",
                               self._observed_rebase_source())])
        sigs = [c["error_signature"] for c in merged["candidates"]]
        self.assertEqual(len(sigs), len(set(sigs)), sigs)
        self.assertEqual(len(sigs), 2)
        cand = next(c for c in merged["candidates"]
                    if c["error_signature"] == self.SIG)
        self.assertEqual(cand["affected_jobs"], 3)
        self.assertEqual(sorted(j["job_url"] for j in cand["jobs"]),
                         [f"https://prow.example/{i}" for i in (1, 2, 3)])
        self.assertEqual(cand["step_name"],
                         "openshift-microshift-infra-rpm-install-aws, test-rpm")
        self.assertEqual(cand["releases"],
                         [{"source": "rebase-release-5.1", "affected_jobs": 3}])
        self.assertNotIn("merged_signatures", cand)

    def test_cross_source_identical_signature_across_steps_merges(self):
        merged = self._merge([
            ("5.1", [_source_candidate(self.SIG, "e2e-rpm", [10],
                                       raw_error="spec references missing")]),
            ("rebase-release-5.1", [_source_candidate(
                self.SIG, "test-rpm", [11], raw_error=self.RAW)]),
        ])
        self.assertEqual(len(merged["candidates"]), 1)
        cand = merged["candidates"][0]
        self.assertEqual(cand["affected_jobs"], 2)
        self.assertEqual(cand["releases"], [
            {"source": "5.1", "affected_jobs": 1},
            {"source": "rebase-release-5.1", "affected_jobs": 1},
        ])
        self.assertEqual(len(cand["jobs"]), 2)

    def test_member_signature_does_not_absorb_other_group(self):
        """A signature that is only a fuzzy-merged member elsewhere keeps its
        own candidate (and categorize decision): representatives differ."""
        merged = self._merge([("5.1", [
            _source_candidate("sig X", "step-a", [1], raw_error="boom alpha",
                              severity=4),
            _source_candidate("sig Y", "step-a", [2], raw_error="boom alpha"),
            _source_candidate("sig X", "step-b", [3], raw_error="other"),
        ])])
        by_sig = {c["error_signature"]: c for c in merged["candidates"]}
        self.assertEqual(sorted(by_sig), ["sig X", "sig Y"])
        self.assertEqual(by_sig["sig Y"]["merged_signatures"], ["sig X"])
        self.assertEqual(by_sig["sig X"]["affected_jobs"], 1)

    def test_jira_merge_runs_before_same_signature_merge(self):
        """Groups sharing an open Jira duplicate still merge; the same-signature
        pass only joins what is left with equal representatives."""
        dup = [{"key": "USHIFT-1", "updated": "2026-11-01"}]
        x_a = _source_candidate("sig X", "step-a", [1], raw_error="aaa")
        x_a["duplicates"] = dup
        w_c = _source_candidate("sig W", "step-c", [2], raw_error="ccc",
                                severity=4)
        w_c["duplicates"] = dup
        z_b = _source_candidate("sig Z", "step-b", [3], raw_error="boom beta",
                                severity=4)
        x_b = _source_candidate("sig X", "step-b", [4], raw_error="boom beta")
        x_b["duplicates"] = dup
        merged = self._merge([("5.1", [x_a, w_c, z_b, x_b])])
        self.assertEqual(len(merged["candidates"]), 1)
        cand = merged["candidates"][0]
        self.assertEqual(cand["error_signature"], "sig X")
        self.assertEqual(cand["merged_signatures"], ["sig W", "sig Z"])
        self.assertEqual(cand["affected_jobs"], 4)

    def test_representative_merge_unit(self):
        def c(sig, severity=5):
            return {"error_signature": sig, "severity": severity,
                    "affected_jobs": 1}
        groups = [
            [c("X")],
            [c("Y")],
            [c("X"), c("Z", severity=4)],
            [c("")],
            [c("X", severity=4), c("Y", severity=3)],
            [c("")],
        ]
        merged = _search_bugs._merge_groups_by_representative_signature(groups)
        self.assertEqual(
            [[m["error_signature"] for m in g] for g in merged],
            [["X", "X", "Z", "X", "Y"], ["Y"], [""], [""]])

    def test_same_job_in_two_steps_is_counted_once(self):
        """One job reporting the same signature in two steps is one job."""
        merged = self._merge([("rebase-release-5.1", [
            _source_candidate(self.SIG, "rpm-build", [7], raw_error="a"),
            _source_candidate(self.SIG, "test-rpm", [7, 8], raw_error="b"),
        ])])
        self.assertEqual(len(merged["candidates"]), 1)
        cand = merged["candidates"][0]
        self.assertEqual(cand["affected_jobs"], 2)
        self.assertEqual([j["job_url"] for j in cand["jobs"]],
                         ["https://prow.example/7", "https://prow.example/8"])
        self.assertEqual(cand["releases"],
                         [{"source": "rebase-release-5.1", "affected_jobs": 2}])

    def test_joined_step_names_are_not_repeated(self):
        merged = self._merge([("5.1", [
            _source_candidate(self.SIG, "rpm-build, test-rpm", [1]),
            _source_candidate(self.SIG, "test-rpm", [2], raw_error="other"),
        ])])
        self.assertEqual(merged["candidates"][0]["step_name"],
                         "rpm-build, test-rpm")

    def test_same_job_url_in_two_sources_counts_per_source(self):
        merged = self._merge([
            ("5.1", [_source_candidate(self.SIG, "rpm-build", [7])]),
            ("rebase-release-5.1", [_source_candidate(self.SIG, "test-rpm",
                                                      [7])]),
        ])
        cand = merged["candidates"][0]
        self.assertEqual(cand["releases"], [
            {"source": "5.1", "affected_jobs": 1},
            {"source": "rebase-release-5.1", "affected_jobs": 1},
        ])
        self.assertEqual(cand["affected_jobs"], 2)

    def test_same_source_files_aggregate_source_once(self):
        """Two inputs with the same source list each source, release entry
        and job once, for merged and unmerged candidates alike."""
        merged = self._merge([
            ("5.1", [_source_candidate(self.SIG, "test-rpm", [1]),
                     _source_candidate("unrelated failure", "e2e", [2],
                                       raw_error="zzz", severity=3)]),
            ("5.1", [_source_candidate(self.SIG, "rpm-build", [1, 3],
                                       raw_error="other")]),
        ])
        self.assertEqual(merged["sources"], ["5.1"])
        by_sig = {c["error_signature"]: c for c in merged["candidates"]}
        self.assertEqual(
            [j["job_url"] for j in by_sig[self.SIG]["jobs"]],
            ["https://prow.example/1", "https://prow.example/3"])
        self.assertEqual(by_sig[self.SIG]["affected_jobs"], 2)
        self.assertEqual(by_sig[self.SIG]["releases"],
                         [{"source": "5.1", "affected_jobs": 2}])
        other = by_sig["unrelated failure"]
        self.assertEqual(len(other["jobs"]), 1)
        self.assertEqual(other["releases"],
                         [{"source": "5.1", "affected_jobs": 1}])

    def test_jobs_without_url_are_not_deduplicated(self):
        first = _source_candidate(self.SIG, "rpm-build", [1])
        second = _source_candidate(self.SIG, "test-rpm", [2])
        for cand in (first, second):
            del cand["jobs"][0]["job_url"]
        merged = self._merge([("5.1", [first, second])])
        cand = merged["candidates"][0]
        self.assertEqual(cand["affected_jobs"], 2)
        self.assertEqual(len(cand["jobs"]), 2)

    def test_candidates_without_jobs_keep_affected_jobs(self):
        """Post-Jira bug-matches candidates carry affected_jobs but no jobs."""
        first = _source_candidate(self.SIG, "rpm-build", [1, 2])
        second = _source_candidate(self.SIG, "test-rpm", [3])
        del first["jobs"], second["jobs"]
        merged = self._merge([("5.1", [first, second])])
        self.assertEqual(len(merged["candidates"]), 1)
        cand = merged["candidates"][0]
        self.assertEqual(cand["affected_jobs"], 3)
        self.assertEqual(cand["jobs"], [])

    def test_distinct_signatures_in_different_steps_stay_separate(self):
        """Only exact signatures cross step buckets; similar ones do not."""
        merged = self._merge([("5.1", [
            _source_candidate("RPM build fails: spec references missing file",
                              "rpm-build", [1], raw_error="missing file"),
            _source_candidate("RPM build fails: spec references missing files",
                              "test-rpm", [2], raw_error="missing file"),
        ])])
        self.assertEqual(len(merged["candidates"]), 2)

    def test_empty_signatures_are_not_merged(self):
        """Malformed candidates without a signature are never collapsed into
        one; they reach categorize unchanged and fail validation there."""
        merged = self._merge([("5.1", [
            _source_candidate("", "rpm-build", [1], raw_error="first"),
            _source_candidate("", "test-rpm", [2], raw_error="second"),
        ])])
        self.assertEqual(len(merged["candidates"]), 2)

    def _run(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True)

    def _merge_and_categorize(self, workdir, candidates):
        bugs = Path(workdir) / "bugs"
        bugs.mkdir()
        source_path = bugs / "bug-candidates-rebase-release-5.1.json"
        source_path.write_text(json.dumps(
            {"source": "rebase-release-5.1", "candidates": candidates}))
        merged_path = bugs / "bug-candidates-merged-rebase-release-5.1.json"
        proc = self._run("--merge", str(source_path), "--output",
                         str(merged_path), "--workdir", workdir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return self._run("--categorize", str(merged_path),
                         "--workdir", workdir), bugs

    def test_observed_duplicate_signature_categorizes(self):
        """Reproduces the aborted bugs stage: merge then categorize."""
        with tempfile.TemporaryDirectory() as workdir:
            proc, bugs = self._merge_and_categorize(
                workdir, self._observed_rebase_source())
            self.assertEqual(proc.returncode, 0, proc.stderr)
            results = json.loads(
                (bugs / "bug-results-rebase-release-5.1.json").read_text()
            )["results"]
            self.assertEqual(sorted(r["error_signature"] for r in results), [
                self.SIG,
                "verify-assets: stale entries in assets.yaml reference "
                "deleted files",
            ])

    def test_merge_with_post_jira_matches_does_not_hide_regression(self):
        """--merge accepts bug-matches files, whose candidates carry
        affected_jobs but no jobs. Merged with a dated candidate of the same
        signature, the closed match is still suggested, not hidden."""
        with tempfile.TemporaryDirectory() as workdir:
            bugs = Path(workdir) / "bugs"
            bugs.mkdir()
            listed = _source_candidate(self.SIG, "test-rpm", [1],
                                       raw_error=self.RAW)
            listed["jobs"][0]["finished"] = "2026-07-01"
            candidates_path = bugs / "bug-candidates-5.1.json"
            candidates_path.write_text(json.dumps(
                {"source": "5.1", "candidates": [listed]}))
            matches_path = bugs / "bug-matches-rebase-release-5.1.json"
            matches_path.write_text(json.dumps({
                "source": "rebase-release-5.1",
                "candidates": [{
                    "error_signature": self.SIG,
                    "severity": 5,
                    "failure_type": "build",
                    "step_name": "test-rpm",
                    "affected_jobs": 2,
                    "duplicates": [],
                    "regressions": [{"key": "USHIFT-500",
                                     "updated": "2026-08-01"}],
                }],
            }))
            merged_path = bugs / "bug-candidates-merged-5.1.json"
            proc = self._run("--merge", str(candidates_path),
                             str(matches_path), "--output", str(merged_path),
                             "--workdir", workdir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            merged = json.loads(merged_path.read_text())["candidates"]
            self.assertEqual(len(merged), 1)
            self.assertEqual(merged[0]["affected_jobs"], 3)
            self.assertEqual(len(merged[0]["jobs"]), 1)
            proc = self._run("--categorize", str(merged_path),
                             "--workdir", workdir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(
                (bugs / "bug-results-5.1.json").read_text())["results"][0]
            self.assertEqual(result["action"], "suggest")
            self.assertEqual(result["skip_category"], "")
            self.assertIn("USHIFT-500", result["reason"])

    def test_same_source_post_jira_matches_do_not_hide_regression(self):
        """bug-candidates and bug-matches files of the same source: the
        source is aggregated once, so its listed job is not repeated. The
        bug-matches count has no job URLs to match against the listed job,
        so it stays counted as jobs with unknown dates and the closed match
        is still suggested."""
        with tempfile.TemporaryDirectory() as workdir:
            bugs = Path(workdir) / "bugs"
            bugs.mkdir()
            listed = _source_candidate(self.SIG, "test-rpm", [1],
                                       raw_error=self.RAW)
            listed["jobs"][0]["finished"] = "2026-07-01"
            candidates_path = bugs / "bug-candidates-5.1.json"
            candidates_path.write_text(json.dumps(
                {"source": "5.1", "candidates": [listed]}))
            matches_path = bugs / "bug-matches-5.1.json"
            matches_path.write_text(json.dumps({
                "source": "5.1",
                "candidates": [{
                    "error_signature": self.SIG,
                    "severity": 5,
                    "failure_type": "build",
                    "step_name": "test-rpm",
                    "affected_jobs": 1,
                    "duplicates": [],
                    "regressions": [{"key": "USHIFT-500",
                                     "updated": "2026-08-01"}],
                }],
            }))
            merged_path = bugs / "bug-candidates-merged-5.1.json"
            proc = self._run("--merge", str(candidates_path),
                             str(matches_path), "--output", str(merged_path),
                             "--workdir", workdir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            merged = json.loads(merged_path.read_text())
            self.assertEqual(merged["sources"], ["5.1"])
            cand = merged["candidates"][0]
            self.assertEqual(cand["affected_jobs"], 2)
            self.assertEqual([j["job_url"] for j in cand["jobs"]],
                             ["https://prow.example/1"])
            self.assertEqual(cand["releases"],
                             [{"source": "5.1", "affected_jobs": 2}])
            proc = self._run("--categorize", str(merged_path),
                             "--workdir", workdir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(
                (bugs / "bug-results-5.1.json").read_text())["results"][0]
            self.assertEqual(result["action"], "suggest")

    def test_empty_signature_still_fails_categorize(self):
        with tempfile.TemporaryDirectory() as workdir:
            proc, _ = self._merge_and_categorize(workdir, [
                _source_candidate("", "rpm-build", [1], raw_error="first"),
                _source_candidate("", "test-rpm", [2], raw_error="second"),
            ])
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("missing error_signature", proc.stderr)

    def test_validator_still_rejects_duplicate_results(self):
        result = {"error_signature": "a", "action": "suggest", "jira_key": "",
                  "skip_category": "", "reason": "r"}
        with self.assertRaises(SystemExit) as ctx, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            _search_bugs._validate_results(
                {"mode": "search", "date": "2026-11-13",
                 "results": [result, dict(result)]},
                {"candidates": [{"error_signature": "a"},
                                {"error_signature": "a"}]})
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertIn("results[1]: duplicate error_signature 'a'",
                      err.getvalue())


class PipelineFailureTests(unittest.TestCase):
    def test_missing_credentials_fails_without_publishing_matches(self):
        with tempfile.TemporaryDirectory() as workdir:
            jobs = Path(workdir) / "jobs"
            jobs.mkdir()
            job = [{
                "severity": 3,
                "stack_layer": "test",
                "step_name": "e2e",
                "error_signature": "greenboot health timeout",
                "root_cause": "health check timed out",
                "raw_error": "timeout",
                "infrastructure_failure": False,
                "job_url": "https://prow.example/1",
                "job_name": "periodic-ci-microshift-release-4.22",
                "release": "4.22",
                "remediation": "inspect health check",
                "finished": "2026-09-01",
            }]
            (jobs / "release-4.22-job-0-1.json").write_text(json.dumps(job))
            env = dict(os.environ)
            env.pop("JIRA_USERNAME", None)
            env.pop("JIRA_API_TOKEN", None)
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--pipeline", "4.22",
                 "--workdir", workdir],
                capture_output=True, text=True, env=env,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("JIRA_USERNAME and JIRA_API_TOKEN are required",
                          proc.stderr)
            self.assertFalse(
                (Path(workdir) / "bugs" / "bug-matches-4.22.json").exists())

    def _run_pipeline(self, workdir, sources):
        """Run --pipeline with Jira credentials unset; return the process."""
        env = dict(os.environ)
        env.pop("JIRA_USERNAME", None)
        env.pop("JIRA_API_TOKEN", None)
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--pipeline", sources,
             "--workdir", workdir],
            capture_output=True, text=True, env=env,
        )

    @staticmethod
    def _write_healthy_release(jobs_dir, release):
        """Simulate a release the collect stage recorded as failure-free."""
        (jobs_dir / f"release-{release}-jobs.json").write_text("[]")
        (jobs_dir / f"release-{release}-status.json").write_text(json.dumps([
            {"job": "periodic-a", "status": "SUCCESS"},
            {"job": "periodic-b", "status": "SUCCESS"},
        ]))

    def test_zero_failure_release_publishes_empty_candidates(self):
        """A release the collect stage recorded as failure-free prepares an
        empty candidate set instead of failing the pipeline."""
        with tempfile.TemporaryDirectory() as workdir:
            jobs = Path(workdir) / "jobs"
            jobs.mkdir()
            self._write_healthy_release(jobs, "4.22")

            proc = self._run_pipeline(workdir, "4.22")

            # Prepare succeeded for the healthy source; the run stopped at
            # the credentials gate (no Jira access in tests).
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("No failed jobs for release 4.22", proc.stderr)
            self.assertNotIn("no analysis reports found", proc.stderr)
            self.assertIn("JIRA_USERNAME and JIRA_API_TOKEN are required",
                          proc.stderr)
            candidates = json.loads(
                (Path(workdir) / "bugs" / "bug-candidates-4.22.json").read_text())
            self.assertEqual(candidates["candidates"], [])

    def test_zero_failure_rebase_pr_prepares_empty_candidates(self):
        """All releases and rebase PRs green: every source prepares an empty
        candidate set and the pipeline proceeds (no 'No candidates prepared'
        pipeline error)."""
        with tempfile.TemporaryDirectory() as workdir:
            jobs = Path(workdir) / "jobs"
            jobs.mkdir()
            self._write_healthy_release(jobs, "4.22")
            (jobs / "prs-status.json").write_text(json.dumps([
                {"pr_number": 4567, "title": "rebase-release-4.22 onto main",
                 "url": "http://pr", "passed": 3, "failed": 0, "pending": 0,
                 "total": 3},
            ]))

            proc = self._run_pipeline(workdir, "4.22")

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Auto-appended rebase source: rebase-release-4.22",
                          proc.stderr)
            self.assertIn("No failed jobs for release 4.22", proc.stderr)
            self.assertIn("No failed jobs for rebase PR for 4.22", proc.stderr)
            self.assertNotIn("no analysis reports found", proc.stderr)
            self.assertIn("JIRA_USERNAME and JIRA_API_TOKEN are required",
                          proc.stderr)
            for source in ("4.22", "rebase-release-4.22"):
                candidates = json.loads(
                    (Path(workdir) / "bugs" /
                     f"bug-candidates-{source}.json").read_text())
                self.assertEqual(candidates["candidates"], [])

    def test_prepare_zero_failure_release_writes_empty_candidates(self):
        """Standalone prepare mode exits 0 for a confirmed failure-free
        release and writes an empty candidates file."""
        with tempfile.TemporaryDirectory() as workdir:
            jobs = Path(workdir) / "jobs"
            jobs.mkdir()
            self._write_healthy_release(jobs, "4.21")

            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "4.21", "--workdir", workdir],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            out = Path(workdir) / "bugs" / "bug-candidates-4.21.json"
            self.assertTrue(out.is_file())
            data = json.loads(out.read_text())
            self.assertEqual(data["candidates"], [])
            self.assertEqual(data["job_files_found"], 0)

    def test_missing_analysis_reports_fail_pipeline(self):
        """Failures were collected but analysis reports are missing: the
        pipeline must fail instead of skipping the source and finalizing
        stale mappings."""
        with tempfile.TemporaryDirectory() as workdir:
            jobs = Path(workdir) / "jobs"
            jobs.mkdir()
            job = [{
                "severity": 3,
                "stack_layer": "test",
                "step_name": "e2e",
                "error_signature": "greenboot health timeout",
                "root_cause": "health check timed out",
                "raw_error": "timeout",
                "infrastructure_failure": False,
                "job_url": "https://prow.example/1",
                "job_name": "periodic-ci-microshift-release-4.21",
                "release": "4.21",
                "remediation": "inspect health check",
                "finished": "2026-09-01",
            }]
            (jobs / "release-4.21-job-0-1.json").write_text(json.dumps(job))
            # 4.22 recorded failed jobs, but its per-job analysis reports are
            # absent (e.g. the analysis agents failed).
            (jobs / "release-4.22-jobs.json").write_text(json.dumps([
                {"job": "periodic-x", "build_id": "9", "status": "failure"},
            ]))
            (jobs / "release-4.22-status.json").write_text(json.dumps([
                {"job": "periodic-x", "status": "failure"},
            ]))

            proc = self._run_pipeline(workdir, "4.21,4.22")

            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("no analysis reports found for release 4.22",
                          proc.stderr)
            self.assertNotIn("skipping", proc.stderr)
            # The failure precedes the search stage: no mapping is published
            # for any source, so no partial or stale data can be finalized.
            bugs = Path(workdir) / "bugs"
            self.assertFalse((bugs / "bug-matches-4.21.json").exists())
            self.assertFalse((bugs / "bug-matches-4.22.json").exists())

    def test_unknown_release_fails_prepare(self):
        """A source that cannot be confirmed failure-free (e.g. a mistyped
        release with an empty status list) is an error, not an empty result."""
        with tempfile.TemporaryDirectory() as workdir:
            jobs = Path(workdir) / "jobs"
            jobs.mkdir()
            (jobs / "release-9.9-jobs.json").write_text("[]")
            (jobs / "release-9.9-status.json").write_text("[]")

            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "9.9", "--workdir", workdir],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("no analysis reports found", proc.stderr)

    def test_collection_error_release_fails_prepare(self):
        """A release whose data collection failed (error file present) is a
        genuine preparation failure, not a confirmed zero-failure source."""
        with tempfile.TemporaryDirectory() as workdir:
            jobs = Path(workdir) / "jobs"
            jobs.mkdir()
            (jobs / "release-4.22-jobs.json").write_text("[]")
            (jobs / "release-4.22-status.json").write_text("[]")
            (jobs / "release-4.22-error.txt").write_text("gsutil: boom")

            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "4.22", "--workdir", workdir],
                capture_output=True, text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("no analysis reports found", proc.stderr)


if __name__ == "__main__":
    unittest.main()
