"""Tests for the recommendations validator in assemble-report.py.

validate_recs() gates --strict (exit 2); soft_warnings() is advisory only.
Both are validation logic and need positive and negative cases.
"""

import json
import subprocess
import sys
import tempfile
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from importlib import import_module

_mod = import_module("assemble-report")
validate_recs = _mod.validate_recs
soft_warnings = _mod.soft_warnings
GENDERED_RE = _mod.GENDERED_RE
jira_linkify = _mod.jira_linkify
build_blocks = _mod.build_blocks
VERSION_RE = _mod.VERSION_RE

CHECKS = {"feature_names": {"OCPSTRAT-1": "Feature one", "OCPSTRAT-2": "Feature two"}}


def _recs(**overrides):
    base = {
        "headline": "Scope must shrink; the lever is ownership.",
        "decisions": [
            {"decision": "Defer Feature one (OCPSTRAT-1)", "frees": "20 SP", "owner": "PM", "by": "S295"},
        ],
        "scope_decisions": ["Feature two (OCPSTRAT-2) looks like a real candidate."],
        "process_gaps": ["Roster drift needs reconciling."],
        "per_feature": [],
        "per_person": [],
    }
    base.update(overrides)
    return base


class TestValidateRecsPositive(unittest.TestCase):
    def test_clean_file_has_no_problems(self):
        assert validate_recs(_recs(), CHECKS) == []

    def test_exactly_five_decisions_is_allowed(self):
        recs = _recs(decisions=[{"decision": f"d{i} (OCPSTRAT-1)", "frees": "", "owner": "PM", "by": "S295"} for i in range(5)])
        assert validate_recs(recs, CHECKS) == []

    def test_non_ocpstrat_keys_are_not_checked_against_feature_names(self):
        recs = _recs(per_feature=["Point OCPEDGE-999 and USHIFT-42 before S295."])
        assert validate_recs(recs, CHECKS) == []

    def test_no_feature_names_in_checks_skips_key_check(self):
        recs = _recs(per_feature=["OCPSTRAT-999 is unknown but there is nothing to check against."])
        assert validate_recs(recs, {}) == []

    def test_gendered_regex_ignores_substrings(self):
        # "he"/"her"/"his" inside other words must not fire.
        text = "Chester and Hershey reviewed the theme with this checklist; Sheila shipped it."
        assert not GENDERED_RE.search(text)
        assert validate_recs(_recs(headline=text), CHECKS) == []


class TestValidateRecsNegative(unittest.TestCase):
    def test_prebuilt_jira_link_is_rejected(self):
        recs = _recs(per_feature=["See [OCPSTRAT-1](https://redhat.atlassian.net/browse/OCPSTRAT-1)."])
        problems = validate_recs(recs, CHECKS)
        assert any("pre-built" in p for p in problems)

    def test_gendered_pronoun_is_rejected_once(self):
        recs = _recs(per_person=["She should pair with Bob; his load is lower and he agreed."])
        problems = [p for p in validate_recs(recs, CHECKS) if "pronoun" in p]
        assert len(problems) == 1
        assert "'She'" in problems[0]

    def test_gendered_pronoun_is_case_insensitive(self):
        recs = _recs(headline="HIS feature is late.")
        assert any("pronoun" in p for p in validate_recs(recs, CHECKS))

    def test_unknown_ocpstrat_key_is_rejected(self):
        recs = _recs(per_feature=["Defer OCPSTRAT-999 too."])
        problems = validate_recs(recs, CHECKS)
        assert any("OCPSTRAT-999" in p and "not in this run" in p for p in problems)

    def test_more_than_five_decisions_is_rejected(self):
        recs = _recs(decisions=[{"decision": f"d{i}", "frees": "", "owner": "PM", "by": "S295"} for i in range(6)])
        assert any("more than 5" in p for p in validate_recs(recs, CHECKS))

    def test_decision_fields_are_scanned_too(self):
        recs = _recs(decisions=[{"decision": "Rebalance", "frees": "", "owner": "her manager", "by": "S295"}])
        assert any("pronoun" in p for p in validate_recs(recs, CHECKS))

    def test_multiple_problems_are_all_reported(self):
        recs = _recs(
            per_feature=["[OCPSTRAT-1](https://redhat.atlassian.net/browse/OCPSTRAT-1) and OCPSTRAT-999"],
            per_person=["He is overloaded."],
        )
        problems = validate_recs(recs, CHECKS)
        assert len(problems) == 3


class TestSoftWarnings(unittest.TestCase):
    def test_no_warnings_for_distinct_bullets(self):
        recs = _recs(process_gaps=[
            "No SME on OCPSTRAT-1; 20 SP at risk.",
            "OCPSTRAT-2 has 3 unassigned stories.",
        ])
        assert soft_warnings(recs) == []

    def test_duplicate_bullets_same_keys_and_numbers_warn(self):
        recs = _recs(process_gaps=[
            "13 epics (94 SP) excluded, e.g. USHIFT-6926.",
            "USHIFT-6926 alone carries 94 SP across 13 excluded epics.",
        ])
        warnings = soft_warnings(recs)
        assert len(warnings) == 1
        assert "duplicated" in warnings[0]

    def test_duplicate_warning_does_not_leak_narrative(self):
        # process_gaps is free-form narrative that may carry names, emails or other
        # sensitive values, and the warning is printed verbatim to stderr
        # (main: "WARNING: ... {w}"). The dedup warning must report only the shared
        # Jira keys — never the bullet text, and never any value (numbers included)
        # extracted from the narrative.
        recs = _recs(process_gaps=[
            "OCPSTRAT-1 owner alice@example.com carries 1234567 SP across 3 epics.",
            "Across 3 epics OCPSTRAT-1 still has 1234567 SP; ping alice@example.com.",
        ])
        warnings = soft_warnings(recs)
        assert len(warnings) == 1
        assert "alice@example.com" not in warnings[0]
        assert "@" not in warnings[0]
        assert "1234567" not in warnings[0]  # numeric values from narrative redacted
        assert "OCPSTRAT-1" in warnings[0]   # allowlisted Jira key is still reported

    def test_same_keys_different_numbers_do_not_warn(self):
        recs = _recs(process_gaps=[
            "OCPSTRAT-1 has 20 SP open.",
            "OCPSTRAT-1 has 3 unassigned stories.",
        ])
        assert soft_warnings(recs) == []

    def test_bullets_without_keys_are_ignored(self):
        recs = _recs(process_gaps=["Roster drift.", "Roster drift."])
        assert soft_warnings(recs) == []

    def test_missing_or_null_process_gaps(self):
        assert soft_warnings({}) == []
        assert soft_warnings({"process_gaps": None}) == []


class TestJiraLinkify(unittest.TestCase):
    def test_bare_key_is_linked(self):
        out = jira_linkify("see OCPSTRAT-1.")
        assert out == "see [OCPSTRAT-1](https://redhat.atlassian.net/browse/OCPSTRAT-1)."

    def test_existing_link_is_not_double_linked(self):
        text = "[OCPSTRAT-1](https://redhat.atlassian.net/browse/OCPSTRAT-1)"
        assert jira_linkify(text) == text

    def test_unknown_project_prefix_is_left_alone(self):
        assert jira_linkify("RHEL-123") == "RHEL-123"


def _synthetic_checks():
    """A compact but structurally complete checks.json for the HTML renderer."""
    return {
        "feature_names": {"OCPSTRAT-1": "Feature one", "OCPSTRAT-2": "Feature two"},
        "meta": {
            "overall_risk": "HIGH", "gap_sp": -36,
            "gap_with_hidden_low": -46, "gap_with_hidden_high": -36,
            "total_remaining_sp": 140, "total_capacity_sp": 104,
            "active_features": 2, "dormant_features": 1, "total_features": 3,
            "overloaded_people_count": 1, "spof_features_count": 1,
            "high_risk_count": 1, "medium_risk_count": 0, "low_risk_count": 1,
            "component_filter": "none",
        },
        "hidden_scope": {"unpointed_open": 5, "unpointed_assigned": 2,
                         "estimate_low": 10, "estimate_high": 20},
        "process_gaps": {
            "roster_drift": [], "unpointed_open_stories": 5, "unpointed_assigned_stories": 2,
            "features_without_sme": {"active": ["OCPSTRAT-1"], "dormant": []},
            "sized_active_features": 2, "undersized_features": 1,
            "excluded_open_sp": 8,
            "excluded_epics": [{"feature_key": "OCPSTRAT-1", "key": "OCPEDGE-9",
                                "status": "New", "open_sp": 8, "reason": "epic targets openshift-5.2"}],
        },
        "timeline": [{"feature_key": "OCPSTRAT-1", "remaining_sp": 90, "unpointed_open": 3,
                      "velocity_per_sprint": 10, "sprints_needed": 9, "sprints_left": 2,
                      "gap": 7, "risk": "HIGH"}],
        "cut_line": [
            {"rank": 1, "feature_key": "OCPSTRAT-1", "sme": "Alice", "remaining_sp": 90,
             "unpointed_open": 3, "cumulative_sp": 90, "fits": "fits", "timeline_risk": "HIGH"},
            {"rank": 2, "feature_key": "OCPSTRAT-2", "sme": None, "remaining_sp": 50,
             "unpointed_open": 0, "cumulative_sp": 140, "fits": "over"},
        ],
        "capacity": [
            {"status": "OVER", "in_roster": True, "assigned_sp": 26, "unpointed_assigned": 1,
             "features": ["OCPSTRAT-1"], "display_name": "Alice", "person": "alice",
             "remaining_capacity": 20},
            {"status": "OK", "in_roster": True, "assigned_sp": 5, "unpointed_assigned": 3,
             "features": ["OCPSTRAT-2"], "display_name": "Bob", "person": "bob",
             "remaining_capacity": 15},
        ],
        "assignment": {
            "spof": [{"feature_key": "OCPSTRAT-1", "sole_contributor": "alice",
                      "sole_contributor_display": "Alice"}],
            "unassigned": [{"feature_key": "OCPSTRAT-2", "count": 2, "sp": 5}],
        },
        "scope_confirmation": [{"feature_key": "OCPSTRAT-2", "sme": None, "epic_count": 0,
                                "story_count": 0, "reason": "status New, no epics"}],
        "method": [{"label": "Capacity", "formula": "Σ sp_target × sprints_left",
                    "shown_as": "104 SP"}],
        "composite": [{"feature_key": "OCPSTRAT-1", "composite_risk": "HIGH",
                       "data_quality": "PASS", "timeline": "HIGH", "capacity": "OVER",
                       "assignment": "SPOF", "bugs": "OK", "sizing": "Undersized"}],
        "data_quality": [{"feature_key": "OCPSTRAT-1", "epic_count": 2, "story_count": 10,
                          "pointed_pct": 80, "status": "PASS", "reason": "",
                          "all_epics_excluded": False}],
        "bug_load": {
            "unassigned_blocker_critical": [{"key": "OCPBUGS-1", "priority": "Blocker",
                                             "component": "TNA", "status": "New"}],
            "by_component": {"TNA": {"total": 3, "blocker": 1, "critical": 0, "unassigned": 1}},
        },
        "sizing": [{"feature_key": "OCPSTRAT-1", "tshirt": "M", "total_sp": 90, "actual_sp": 90,
                    "epic_count": 2, "contributor_count": 1, "dedicated_sprints": 9,
                    "assessment": "Undersized"}],
    }


def _synthetic_recs():
    return {
        "headline": "Scope must shrink; the lever is ownership.",
        "decisions": [{"decision": "Defer Feature one (OCPSTRAT-1)", "frees": "90 SP",
                       "owner": "PM", "by": "S295"}],
        "scope_decisions": ["Feature two (OCPSTRAT-2) is a real 5.x candidate."],
        "process_gaps": ["Roster needs reconciling before capacity is trusted."],
        "per_feature": ["Point OCPSTRAT-1 before S295."],
        "per_person": ["Move a feature off Alice."],
    }


def _synthetic_params():
    return {"version": "5.1", "today": "2026-09-30", "first_sprint": "293",
            "last_sprint": "297", "pencils_down": "296", "remaining_sprints": "2",
            "total_dev_sprints": "3"}


def _all_text(blocks_dict):
    """Flatten every rendered string in a block dict (recursing into details)."""
    out = []

    def walk(blist):
        for b in blist:
            if b["t"] in ("p", "small", "h3"):
                out.append(b.get("text", ""))
            elif b["t"] == "bullets":
                out.extend(b.get("items", []))
            elif b["t"] == "details":
                out.append(b.get("summary", ""))
                walk(b.get("blocks", []))
    for blist in blocks_dict.values():
        walk(blist)
    return "\n".join(out)


class TestPluralization(unittest.TestCase):
    """Count-driven sentences must agree in number — a single feature reads
    "1 feature is", never "1 features are" (CodeRabbit cosmetic finding)."""

    def _text(self, checks):
        blocks = build_blocks(checks, _mod.normalize_recs(_synthetic_recs()), _synthetic_params())
        return _all_text(blocks)

    def test_single_dormant_feature_is_singular(self):
        text = self._text(_synthetic_checks())  # exactly one dormant feature
        assert "1 feature is still" in text
        assert "1 features are" not in text
        assert "It has no SME." in text

    def test_multiple_dormant_features_are_plural(self):
        checks = _synthetic_checks()
        checks["scope_confirmation"].append(
            {"feature_key": "OCPSTRAT-1", "sme": None, "epic_count": 0,
             "story_count": 0, "reason": "status New, no epics"})
        text = self._text(checks)
        assert "2 features are still" in text
        assert "2 of them have no SME." in text

    def test_single_undersized_feature_is_singular(self):
        text = self._text(_synthetic_checks())  # undersized_features == 1
        assert "1 of 2 sized active features is larger than its T-shirt implies." in text

    def test_single_excluded_only_feature_is_singular(self):
        checks = _synthetic_checks()
        checks["data_quality"][0]["all_epics_excluded"] = True
        text = self._text(checks)
        assert "1 feature has only done or out-of-release epics" in text
        assert "it is finished or mis-targeted" in text
        assert "1 features have" not in text


class TestNormalizeRecs(unittest.TestCase):
    """recommendations.json is LLM-written; null list fields must become [] so no
    consumer (validate_recs/soft_warnings iterate, build_blocks slices) sees None."""

    def test_null_and_missing_list_fields_become_empty_lists(self):
        recs = _mod.normalize_recs({"headline": "x", "decisions": None, "process_gaps": None})
        for k in _mod.RECS_LIST_FIELDS:
            assert recs[k] == []

    def test_existing_lists_are_preserved(self):
        recs = _mod.normalize_recs({"decisions": [{"decision": "d"}], "process_gaps": ["g"]})
        assert recs["decisions"] == [{"decision": "d"}]
        assert recs["process_gaps"] == ["g"]

    def test_build_blocks_survives_null_fields_after_normalize(self):
        recs = _synthetic_recs()
        recs["scope_decisions"] = None
        recs["per_feature"] = None
        blocks = build_blocks(_synthetic_checks(), _mod.normalize_recs(recs), _synthetic_params())
        assert isinstance(blocks, dict)



class TestVersionArg(unittest.TestCase):
    """--version is allow-listed at the trust boundary; it flows into Markdown unescaped."""

    def test_accepts_valid_versions(self):
        for v in ("5.1", "5.1.0", "5.1.z"):
            self.assertIsNotNone(VERSION_RE.fullmatch(v), f"{v!r} should be accepted")

    def test_rejects_invalid_versions(self):
        for v in ("5", "5.1.1.1", "v5.1", "5.1<b>", ""):
            self.assertIsNone(VERSION_RE.fullmatch(v), f"{v!r} should be rejected")




class NoHiddenScopeEstimateEndToEndTest(unittest.TestCase):
    """run-checks.py --no-hidden-scope-estimate → assemble-report.py renders without None values."""

    BIN = os.path.join(os.path.dirname(__file__), "..")

    def _write(self, d, name, obj):
        path = os.path.join(d, name)
        with open(path, "w") as f:
            json.dump(obj, f)
        return path

    def _story(self, key, sp, status="In Progress"):
        return {"key": key, "summary": "Synthetic story", "type": "Story", "status": status, "sp": sp,
                "assignee": "aexample@example.com", "assignee_display": "Alex Example", "epic_key": "EPIC-1",
                "priority": "Major", "labels": [], "updated": "2030-01-20", "sprints": [],
                "flagged": False, "blocked_by": [], "stale": False}

    def _run(self, d, extra):
        files = {
            "features": self._write(d, "features.json", {"features": [
                {"key": "OCPSTRAT-9001", "summary": "Synthetic feature", "status": "In Progress", "priority": "Major",
                 "size": "M", "sme": "Alex Example", "rank": 1, "labels": [], "type": "Feature"}]}),
            "epics": self._write(d, "epics.json", {"feature_to_epics": {"OCPSTRAT-9001": ["EPIC-1"]}, "epics": [
                {"key": "EPIC-1", "summary": "Synthetic epic", "status": "In Progress",
                 "feature_key": "OCPSTRAT-9001", "target_versions": [], "fix_versions": [], "labels": []}]}),
            "stories": self._write(d, "stories.json", {"stories": [
                self._story("TEST-1", 5), self._story("TEST-2", 0), self._story("TEST-3", 0),
                self._story("TEST-4", 3, "Closed"), self._story("TEST-5", 5, "Closed"),
                self._story("TEST-6", 2, "Closed"), self._story("TEST-7", 8, "Closed")]}),
            "bugs": self._write(d, "bugs.json", {"bugs": []}),
            "roster": self._write(d, "roster.json", {"members": [
                {"username": "aexample@example.com", "display_name": "Alex Example", "sp_target": 8}]}),
        }
        checks = os.path.join(d, "checks.json")
        subprocess.run([sys.executable, os.path.join(self.BIN, "run-checks.py"),
                        *[x for k, v in files.items() for x in (f"--{k}", v)],
                        "--remaining-sprints", "2", "--version", "9.1", "--today", "2030-01-21",
                        "--output", checks, *extra], check=True, capture_output=True)
        recs = self._write(d, "recs.json", {"headline": "Synthetic headline.", "decisions": []})
        out = os.path.join(d, "report")
        r = subprocess.run([sys.executable, os.path.join(self.BIN, "assemble-report.py"),
                            "--checks", checks, "--recommendations", recs,
                            "--template", os.path.join(self.BIN, "..", "references", "release-planning-report-template.md"),
                            "--version", "9.1", "--today", "2030-01-21", "--first-sprint", "10",
                            "--last-sprint", "12", "--remaining-sprints", "2", "--total-dev-sprints", "2",
                            "--output", out], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(out + ".md") as f:
            return json.load(open(checks)), f.read()

    def test_report_renders_without_hidden_scope(self):
        with tempfile.TemporaryDirectory() as d:
            checks, report = self._run(d, ["--no-hidden-scope-estimate"])
        self.assertIsNone(checks["meta"]["gap_with_hidden_low"])
        self.assertNotIn("None", report)
        self.assertNotIn("incl. hidden", report)
        self.assertNotIn("Hidden scope", report)
        self.assertNotIn("≈", report)
        self.assertIn("| Scope (pointed) | Capacity | Gap |", report)
        # The gap covers pointed work only: the verdict counts the unpointed stories, never sizes them.
        self.assertIn("pointed scope fits with ~11 SP to spare; 2 unpointed items not counted.", report)

    def test_default_run_still_shows_hidden_scope(self):
        with tempfile.TemporaryDirectory() as d:
            checks, report = self._run(d, [])
        self.assertIsNotNone(checks["meta"]["gap_with_hidden_low"])
        self.assertIn("incl. hidden", report)


if __name__ == "__main__":
    unittest.main()
