"""Tests for scope handling in run-checks.py: active/dormant classification,
whole-team capacity, done/other-release epic exclusion, load-independent
sizing, cut line, hidden scope, and the method block."""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from importlib import import_module

_mod = import_module("run-checks")
_jt = import_module("_jira_transforms")


def _story(key="OCPEDGE-1", assignee="alice@x.com", sp=5, epic_key="E-1", status="To Do",
           issue_type="Story", updated=None, sprints=None):
    return {
        "key": key, "summary": key, "type": issue_type, "status": status,
        "assignee": assignee, "assignee_display": assignee, "sp": sp, "epic_key": epic_key,
        "flagged": False, "blocked_by": [], "stale": False, "labels": [], "priority": "Major",
        "updated": updated, "sprints": sprints or [],
    }


def _feature(key="OCPSTRAT-1", status="In Progress", size="M", epics=None, stories=None, rank=None, sme="None"):
    return {"key": key, "summary": f"Feature {key}", "status": status, "size": size, "sme": sme,
            "type": "Feature", "rank": rank, "epics": epics if epics is not None else [{"key": "E-1"}],
            "all_stories": stories or [], "excluded_epics": []}


def _roster(*members):
    return {"members": [{"username": m[0], "display_name": m[1], "sp_target": m[2]} for m in members]}


class TestHelpers(unittest.TestCase):
    def test_version_names_from_list_of_dicts(self):
        assert _jt.extract_version_names([{"name": "openshift-5.1"}, {"name": "5.2.0"}]) == ["openshift-5.1", "5.2.0"]

    def test_version_names_from_string(self):
        assert _jt.extract_version_names("5.1") == ["5.1"]

    def test_sprints_from_jira_string_form(self):
        raw = ["com.atlassian.greenhopper.service.sprint.Sprint@1[id=123,rapidViewId=1,state=ACTIVE,name=OCPEDGE Sprint 295,goal=]"]
        assert _jt.extract_sprints(raw) == [{"id": 123, "name": "OCPEDGE Sprint 295"}]

    def test_sprints_from_dicts(self):
        assert _jt.extract_sprints([{"id": "7", "name": "S 1"}]) == [{"id": 7, "name": "S 1"}]

    def test_sprints_non_matching_string_keeps_name_without_id(self):
        # A plain string with no "id=...,name=..." payload is kept verbatim as the name.
        assert _jt.extract_sprints(["Backlog"]) == [{"id": None, "name": "Backlog"}]

    def test_sprints_empty_inputs_give_no_records(self):
        for empty in (None, "", [], [""]):
            assert _jt.extract_sprints(empty) == [], empty

    def test_sprints_partial_payload_without_name_is_not_matched(self):
        # id= present but no name= -> regex does not match; the raw string is kept as the name.
        raw = "com.x.Sprint@1[id=123,state=ACTIVE]"
        assert _jt.extract_sprints([raw]) == [{"id": None, "name": raw}]

    def test_sprint_number(self):
        assert _jt.extract_sprint_number("OpenShift Edge Sprint 296") == 296
        assert _jt.extract_sprint_number("no number") is None


class TestClassification(unittest.TestCase):
    def test_non_new_feature_is_active_without_evidence(self):
        f = _feature(status="Backlog", stories=[])
        assert _mod.classify_feature(f)[0] == "active"

    def test_new_no_epics_is_dormant(self):
        f = _feature(status="New", epics=[])
        cls, reason = _mod.classify_feature(f)
        assert cls == "dormant" and "no epics" in reason

    def test_new_no_stories_is_dormant(self):
        f = _feature(status="New", stories=[])
        assert _mod.classify_feature(f)[0] == "dormant"

    def test_new_with_only_old_todo_story_is_dormant(self):
        # Regression: an untouched To Do story is not evidence of work.
        f = _feature(status="New", stories=[_story(updated="2025-01-01")])
        cls, _ = _mod.classify_feature(f, today="2026-09-29", activity_days=30, window=(293, 296))
        assert cls == "dormant"

    def test_new_with_in_progress_story_is_active(self):
        f = _feature(status="New", stories=[_story(status="In Progress")])
        assert _mod.classify_feature(f)[0] == "active"

    def test_new_with_recently_updated_story_is_active(self):
        f = _feature(status="New", stories=[_story(updated="2026-09-20")])
        cls, reason = _mod.classify_feature(f, today="2026-09-29", activity_days=30)
        assert cls == "active" and "updated within" in reason

    def test_new_with_story_in_release_sprint_is_active(self):
        f = _feature(status="New", stories=[_story(sprints=[{"id": 1, "name": "OCPEDGE Sprint 295"}])])
        assert _mod.classify_feature(f, window=(293, 296))[0] == "active"

    def test_story_in_sprint_outside_window_is_not_evidence(self):
        f = _feature(status="New", stories=[_story(sprints=[{"id": 1, "name": "OCPEDGE Sprint 280"}])])
        assert _mod.classify_feature(f, window=(293, 296))[0] == "dormant"

    def test_bug_activity_is_ignored(self):
        f = _feature(status="New", stories=[_story(issue_type="Bug", status="In Progress")])
        assert _mod.classify_feature(f)[0] == "dormant"


class TestCapacityPopulation(unittest.TestCase):
    def test_idle_roster_members_contribute_capacity(self):
        f = _feature(stories=[_story(sp=10)])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8), ("bob@x.com", "Bob", 8))
        result = _mod.run_capacity_check([f], gate, roster, 2)
        total = sum(c["remaining_capacity"] for c in result if c["in_roster"])
        assert total == 32
        assert {c["person"] for c in result} == {"alice@x.com", "bob@x.com"}

    def test_non_roster_person_has_zero_capacity_and_is_flagged(self):
        f = _feature(stories=[_story(assignee="ghost@x.com", sp=20)])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = _mod.run_capacity_check([f], gate, roster, 2)
        ghost = next(c for c in result if c["person"] == "ghost@x.com")
        assert ghost["in_roster"] is False
        assert ghost["remaining_capacity"] == 0
        assert ghost["status"] == "NOT_IN_ROSTER"
        assert ghost["assigned_sp"] == 20

    def test_unpointed_assigned_counted_separately(self):
        f = _feature(stories=[_story("S-1", sp=5), _story("S-2", sp=0), _story("S-3", sp=0)])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        result = _mod.run_capacity_check([f], gate, _roster(("alice@x.com", "Alice", 8)), 2)
        assert result[0]["assigned_sp"] == 5
        assert result[0]["unpointed_assigned"] == 2


class TestScopeExclusion(unittest.TestCase):
    def _run(self, epic, version="5.1"):
        features = {"features": [{"key": "F-1", "summary": "F1", "status": "In Progress", "size": "M"}]}
        epics = {"epics": [epic], "feature_to_epics": {"F-1": [epic["key"]]}}
        stories = {"stories": [_story(sp=13, epic_key=epic["key"])]}
        feats, _ = _mod.build_hierarchy(features, epics, stories, {"bugs": []}, "none", version=version)
        return feats[0]

    def test_dev_complete_epic_excluded(self):
        f = self._run({"key": "E-1", "status": "Dev Complete", "labels": []})
        assert f["epics"] == []
        assert f["excluded_epics"][0]["reason"] == "epic is Dev Complete"
        assert f["excluded_epics"][0]["open_sp"] == 13

    def test_other_release_epic_excluded(self):
        f = self._run({"key": "E-1", "status": "In Progress", "labels": [], "target_versions": ["openshift-5.2"]})
        assert f["epics"] == []
        assert "5.2" in f["excluded_epics"][0]["reason"]

    def test_matching_release_epic_kept(self):
        for name in ("5.1", "5.1.0", "openshift-5.1", "openshift-5.1.0"):
            f = self._run({"key": "E-1", "status": "In Progress", "labels": [], "target_versions": [name]})
            assert len(f["epics"]) == 1, name

    def test_epic_without_version_kept(self):
        f = self._run({"key": "E-1", "status": "In Progress", "labels": []})
        assert len(f["epics"]) == 1

    def test_no_version_arg_keeps_everything_open(self):
        f = self._run({"key": "E-1", "status": "To Do", "labels": [], "target_versions": ["openshift-9.9"]}, version=None)
        assert len(f["epics"]) == 1


class TestSizingIndependence(unittest.TestCase):
    def test_small_feature_not_undersized_when_contributor_busy_elsewhere(self):
        # F1: 8 SP, size S. alice also has 80 SP on F2. Old logic flagged F1 Undersized.
        f1 = _feature("F-1", size="S", stories=[_story("S-1", sp=8)])
        f2 = _feature("F-2", size="L", stories=[_story("S-2", sp=80, epic_key="E-2")], epics=[{"key": "E-2"}])
        gate = [{"feature_key": "F-1", "status": "PASS"}, {"feature_key": "F-2", "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        tl = _mod.run_timeline_check([f1, f2], gate, roster, 2)
        sz = _mod.run_sizing_check([f1, f2], tl, roster)
        keys = {s["feature_key"]: s for s in sz}
        assert "F-1" not in keys
        assert keys["F-2"]["assessment"] == "Undersized"
        assert keys["F-2"]["dedicated_sprints"] == 10.0

    def test_size_bands_follow_law_05(self):
        assert _mod.SIZE_TO_MAX_SPRINTS["M"] < _mod.SIZE_TO_MAX_SPRINTS["L"]


class TestCutLine(unittest.TestCase):
    def test_fits_partial_over(self):
        tl = [
            {"feature_key": "A", "summary": "A", "rank": 1, "remaining_sp": 10, "risk": "OK"},
            {"feature_key": "B", "summary": "B", "rank": 2, "remaining_sp": 10, "risk": "OK"},
            {"feature_key": "C", "summary": "C", "rank": 3, "remaining_sp": 10, "risk": "OK"},
            {"feature_key": "X", "summary": "X", "rank": 0, "remaining_sp": 0, "risk": "N/A"},
        ]
        rows = _mod.build_cut_line(tl, capacity_sp=15)
        assert [r["feature_key"] for r in rows] == ["A", "B", "C"]
        assert [r["fits"] for r in rows] == ["fits", "partial", "over"]
        assert rows[-1]["cumulative_sp"] == 30

    def test_unranked_sorted_last(self):
        tl = [
            {"feature_key": "U", "summary": "U", "rank": None, "remaining_sp": 1, "risk": "OK"},
            {"feature_key": "R", "summary": "R", "rank": 5, "remaining_sp": 1, "risk": "OK"},
        ]
        assert [r["feature_key"] for r in _mod.build_cut_line(tl, 100)] == ["R", "U"]


class TestHiddenScope(unittest.TestCase):
    def test_uses_closed_story_quartiles(self):
        closed = [_story(f"C-{i}", sp=sp, status="Closed") for i, sp in enumerate([1, 2, 3, 3, 5, 8, 2, 3])]
        open_unpointed = [_story(f"U-{i}", sp=0, assignee="alice@x.com" if i < 2 else None) for i in range(4)]
        f = _feature(stories=closed + open_unpointed)
        h = _mod.estimate_hidden_scope([f], {f["key"]})
        assert h["unpointed_open"] == 4
        assert h["unpointed_assigned"] == 2
        assert h["sp_per_story_typical"] == 3
        assert h["estimate_low"] <= h["estimate_typical"] <= h["estimate_high"]
        assert "quartiles" in h["basis"]

    def test_fallback_when_no_closed_stories(self):
        f = _feature(stories=[_story(sp=0)])
        h = _mod.estimate_hidden_scope([f], {f["key"]})
        assert (h["sp_per_story_low"], h["sp_per_story_typical"], h["sp_per_story_high"]) == _mod.FALLBACK_SP_RANGE
        assert "fallback" in h["basis"]

    def test_dormant_features_excluded(self):
        f = _feature(stories=[_story(sp=0)])
        assert _mod.estimate_hidden_scope([f], set())["unpointed_open"] == 0


class TestMethodBlock(unittest.TestCase):
    def test_every_entry_has_formula_inputs_result(self):
        meta = {k: 0 for k in (
            "total_capacity_sp", "active_features", "assessed_features", "total_remaining_sp", "gap_sp",
            "gap_with_hidden_low", "gap_with_hidden_high", "dormant_features", "total_features",
            "overloaded_people_count", "non_roster_people_count", "timeline_high_count", "undersized_count",
            "high_risk_count", "medium_risk_count", "low_risk_count", "excluded_epics_count", "excluded_open_sp",
            "dq_pass", "dq_warn", "dq_fail")}
        hidden = {"unpointed_open": 0, "unpointed_assigned": 0, "sp_per_story_low": 2, "sp_per_story_typical": 3,
                  "sp_per_story_high": 5, "estimate_low": 0, "estimate_typical": 0, "estimate_high": 0, "basis": "x"}
        method = _mod.build_method(meta, _roster(("a", "A", 8)), 2, hidden, 30, (293, 296), "5.1")
        ids = [m["id"] for m in method]
        assert ids == ["capacity", "scope", "hidden_scope", "gap", "dormant", "overload", "timeline",
                       "sizing", "data_quality", "composite", "scope_filter"]
        for m in method:
            for k in ("label", "formula", "inputs", "result", "shown_as"):
                assert k in m and m[k] not in (None, ""), (m["id"], k)



class TestReviewRegressions(unittest.TestCase):
    """Findings from the independent review of the transparency change set."""

    def test_non_roster_sole_owner_gives_no_velocity(self):
        f = _feature(stories=[_story(assignee="ghost@x.com", sp=10)])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        tl = _mod.run_timeline_check([f], gate, _roster(("alice@x.com", "Alice", 8)), 2)
        assert tl[0]["risk"] == "NO_CONTRIBUTORS"
        assert tl[0]["velocity_per_sprint"] == 0

    def test_version_match_accepts_z_stream_and_prefixed_names(self):
        for name in ("5.1.z", "MicroShift 5.1", "openshift-5.1.z", "OCP 5.1.0"):
            assert _mod._version_matches([name], "5.1"), name

    def test_version_match_rejects_lookalikes(self):
        for name in ("5.10", "5.11.0", "openshift-5.12", "15.1"):
            assert not _mod._version_matches([name], "5.1"), name

    def test_dormant_reason_names_excluded_epics(self):
        f = _feature(status="New", epics=[], stories=[])
        f["excluded_epics"] = [{"key": "E-9", "reason": "epic targets openshift-5.2", "open_sp": 5}]
        cls, reason = _mod.classify_feature(f)
        assert cls == "dormant"
        assert "excluded" in reason and "5.2" in reason

    def test_closed_bugs_not_counted_as_bug_load(self):
        closed = {"key": "OCPBUGS-1", "priority": "Blocker", "component": "X", "status": "Closed", "assignee": None, "type": "Bug"}
        open_ = {"key": "OCPBUGS-2", "priority": "Blocker", "component": "X", "status": "NEW", "assignee": None, "type": "Bug"}
        r = _mod.run_bug_load_check([closed, open_], [], "none")
        assert [b["key"] for b in r["unassigned_blocker_critical"]] == ["OCPBUGS-2"]

    def test_closed_bug_does_not_add_composite_signal(self):
        bug = _story("OCPBUGS-1", assignee=None, sp=0, issue_type="Bug", status="Closed")
        bug["priority"] = "Blocker"
        f = _feature(stories=[_story(), bug])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        comp = _mod.run_composite_check([f], gate, [], [{"feature_key": f["key"], "risk": "OK"}],
                                        {"spof": [], "unassigned": []}, [])
        assert comp[0]["bugs"] == "OK"

    def test_composite_sizing_echoes_assessment_not_blanket_mismatch(self):
        # The composite "Sizing" column must show the real assessment (Unsized /
        # Undersized), matching the sizing table, not a generic "Mismatch".
        f = _feature(stories=[_story()])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        sizing = [{"feature_key": f["key"], "assessment": "Unsized"}]
        comp = _mod.run_composite_check([f], gate, [], [{"feature_key": f["key"], "risk": "OK"}],
                                        {"spof": [], "unassigned": []}, sizing)
        assert comp[0]["sizing"] == "Unsized"

    def test_composite_sizing_ok_when_not_in_sizing_results(self):
        f = _feature(stories=[_story()])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        comp = _mod.run_composite_check([f], gate, [], [{"feature_key": f["key"], "risk": "OK"}],
                                        {"spof": [], "unassigned": []}, [])
        assert comp[0]["sizing"] == "OK"

    def test_quartiles_are_not_the_maximum(self):
        closed = [_story(f"C-{i}", sp=sp, status="Closed") for i, sp in enumerate([1, 2, 3, 8])]
        f = _feature(stories=closed + [_story("U", sp=0)])
        h = _mod.estimate_hidden_scope([f], {f["key"]})
        assert h["sp_per_story_high"] < 8

    def test_zero_sp_feature_after_cut_still_fits(self):
        tl = [
            {"feature_key": "A", "summary": "A", "rank": 1, "remaining_sp": 20, "risk": "OK"},
            {"feature_key": "B", "summary": "B", "rank": 2, "remaining_sp": 0, "risk": "OK"},
        ]
        rows = _mod.build_cut_line(tl, capacity_sp=10)
        assert [r["fits"] for r in rows] == ["partial", "fits"]


class TestGateExclusionAndEmptyEpics(unittest.TestCase):
    """Review fixes: gate reason for all-excluded epics, and empty-epic WARN."""

    def test_all_epics_excluded_reason_not_no_epics(self):
        # A feature whose only epic is Dev Complete (excluded) is finished/mis-targeted,
        # not un-refined: the gate must say "excluded", not "no epics created".
        f = _feature(epics=[], stories=[])
        f["excluded_epics"] = [{"key": "E-1", "reason": "epic is Dev Complete", "open_sp": 0}]
        g = _mod.run_data_quality_gate([f])[0]
        assert g["status"] == "FAIL"
        assert g["all_epics_excluded"] is True
        assert "excluded" in g["reason"]
        assert "no epics created" not in g["reason"]

    def test_empty_epics_downgrade_pass_to_warn(self):
        # 3 epics; one carries a pointed open story, two have none -> WARN "2 of 3".
        story = _story("OCPEDGE-1", sp=5, epic_key="E-1", status="To Do")
        epics = [
            {"key": "E-1", "stories": [story]},
            {"key": "E-2", "stories": []},
            {"key": "E-3", "stories": []},
        ]
        f = _feature(epics=epics, stories=[story])
        g = _mod.run_data_quality_gate([f])[0]
        assert g["status"] == "WARN"
        assert g["empty_epic_count"] == 2
        assert g["reason"] == "2 of 3 epics have no stories"

    def test_all_epics_populated_stays_pass(self):
        story = _story("OCPEDGE-1", sp=5, epic_key="E-1", status="To Do")
        f = _feature(epics=[{"key": "E-1", "stories": [story]}], stories=[story])
        g = _mod.run_data_quality_gate([f])[0]
        assert g["status"] == "PASS"
        assert g["empty_epic_count"] == 0


class TestCutLineTimelineRisk(unittest.TestCase):
    """Review fix (Step 3): the cut line carries each feature's timeline risk so the
    assembler can flag rows that fit in total capacity but not at current allocation."""

    def test_timeline_risk_carried_per_feature(self):
        tl = [
            {"feature_key": "A", "summary": "A", "rank": 1, "remaining_sp": 10, "risk": "OK"},
            {"feature_key": "B", "summary": "B", "rank": 2, "remaining_sp": 10, "risk": "HIGH"},
        ]
        risk_by_key = {"A": "OK", "B": "HIGH"}
        rows = _mod.build_cut_line(tl, capacity_sp=100, timeline_risk_by_key=risk_by_key)
        assert rows[0]["fits"] == "fits" and rows[0]["timeline_risk"] == "OK"
        assert rows[1]["fits"] == "fits" and rows[1]["timeline_risk"] == "HIGH"

    def test_timeline_risk_falls_back_to_row_risk(self):
        tl = [{"feature_key": "A", "summary": "A", "rank": 1, "remaining_sp": 10, "risk": "HIGH"}]
        rows = _mod.build_cut_line(tl, capacity_sp=100)
        assert rows[0]["timeline_risk"] == "HIGH"


if __name__ == "__main__":
    unittest.main()
