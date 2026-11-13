#!/usr/bin/env python3
"""Tests for the deterministic decision policy in search-bugs.py --categorize.

Zero-dependency (stdlib unittest + subprocess + tempfile). Run with:

    python3 plugins/microshift-ci/scripts/tests/test_search_bugs.py

The categorization that decides suggest/linked/skip — including whether a CI
failure is a regression of a closed Jira bug — is deterministic. Each case
builds a merged-candidates JSON, runs --categorize as a subprocess, and asserts
the resulting bug-results-*.json. The script self-checks its output against
_validate_results, so a passing run (exit 0) also proves the byte contract
consumed by --report is satisfied.
"""

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
        self.assertEqual(r["jira_key"], "")
        self.assertEqual(r["skip_category"], "")

    def test_regression_job_after_fix_suggests(self):
        r = one(_candidate(
            "gitops mismatch",
            regressions=[{"key": "USHIFT-6786", "updated": "2026-04-09"}],
            finished=["2026-08-18"],
        ))
        self.assertEqual(r["action"], "suggest")
        self.assertIn("USHIFT-6786", r["reason"])
        self.assertIn("Potential regression", r["reason"])

    def test_regression_all_jobs_before_fix_is_stale(self):
        r = one(_candidate(
            "old failure",
            regressions=[{"key": "USHIFT-500", "updated": "2026-08-01"}],
            finished=["2026-07-01", "2026-08-01"],  # on/before fix
        ))
        self.assertEqual(r["action"], "skip")
        self.assertEqual(r["skip_category"], "stale_regression")
        self.assertIn("USHIFT-500", r["reason"])
        self.assertIn("2026-08-01", r["reason"])

    def test_multiple_regressions_use_most_recent_fix(self):
        """The reference fix is the regression with the latest 'updated' date."""
        regs = [
            {"key": "USHIFT-OLD", "updated": "2026-03-01"},
            {"key": "USHIFT-NEW", "updated": "2026-08-15"},
        ]
        # Job after old fix but before new fix -> stale relative to newest.
        r = one(_candidate("multi", regressions=regs, finished=["2026-06-01"]))
        self.assertEqual(r["action"], "skip")
        self.assertEqual(r["skip_category"], "stale_regression")
        self.assertIn("USHIFT-NEW", r["reason"])  # newest key referenced
        self.assertNotIn("USHIFT-OLD", r["reason"])

        # Job after the newest fix -> suggest, referencing the newest key.
        r2 = one(_candidate("multi", regressions=regs, finished=["2026-09-01"]))
        self.assertEqual(r2["action"], "suggest")
        self.assertIn("USHIFT-NEW", r2["reason"])

    def test_regression_missing_updated_is_indeterminate_suggest(self):
        r = one(_candidate(
            "no date",
            regressions=[{"key": "USHIFT-700"}],  # no 'updated'
            finished=["2026-09-01"],
        ))
        self.assertEqual(r["action"], "suggest")
        self.assertIn("USHIFT-700", r["reason"])

    def test_regression_no_finished_dates_is_indeterminate_suggest(self):
        r = one(_candidate(
            "no jobs finished",
            regressions=[{"key": "USHIFT-800", "updated": "2026-08-01"}],
            finished=[],  # no comparable job dates
        ))
        self.assertEqual(r["action"], "suggest")
        self.assertIn("USHIFT-800", r["reason"])

    def test_regression_partial_missing_finished_dates_is_indeterminate_suggest(self):
        """A job with an unknown finish date cannot be shown to predate the
        fix, so a mixed dated/undated candidate must not be skipped as stale."""
        r = one(_candidate(
            "mixed dates",
            regressions=[{"key": "USHIFT-900", "updated": "2026-08-01"}],
            finished=["2026-07-01", ""],  # one dated before fix, one unknown
        ))
        self.assertEqual(r["action"], "suggest")
        self.assertEqual(r["skip_category"], "")
        self.assertIn("USHIFT-900", r["reason"])
        self.assertIn("Potential regression", r["reason"])

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
                return [
                    {"key": "USHIFT-200", "fields": {"summary": "Bug 200", "status": {"name": "Closed"},
                                                      "assignee": None, "updated": "2026-07-01T00:00:00Z"}},
                    {"key": "USHIFT-200", "fields": {"summary": "Bug 200", "status": {"name": "Closed"},
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
            for source, candidates in files:
                path = Path(tmp) / f"bug-candidates-{source}.json"
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
