"""Tests for run-checks.py — deterministic planning risk checks."""

import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from importlib import import_module

_mod = import_module("run-checks")
build_hierarchy = _mod.build_hierarchy
run_data_quality_gate = _mod.run_data_quality_gate
run_capacity_check = _mod.run_capacity_check
run_timeline_check = _mod.run_timeline_check
run_assignment_check = _mod.run_assignment_check
run_bug_load_check = _mod.run_bug_load_check
run_sizing_check = _mod.run_sizing_check
run_composite_check = _mod.run_composite_check
is_story_done = _mod.is_story_done


def _story(key="OCPEDGE-1", assignee="alice@redhat.com", sp=5, epic_key="EPIC-1",
           status="To Do", issue_type="Story", display="Alice", priority="Major"):
    return {
        "key": key, "summary": f"Story {key}", "type": issue_type, "status": status,
        "assignee": assignee, "assignee_display": display, "sp": sp, "epic_key": epic_key,
        "flagged": False, "blocked_by": [], "stale": False, "labels": [], "priority": priority,
    }


def _feature(key="OCPSTRAT-1", summary="Feature 1", size="M", epics=None, all_stories=None):
    return {
        "key": key, "summary": summary, "status": "In Progress", "size": size,
        "sme": "None", "type": "Feature",
        "epics": epics or [], "all_stories": all_stories or [],
    }


def _roster(*members):
    return {"members": [{"username": m[0], "display_name": m[1], "sp_target": m[2]} for m in members]}


class TestIsDone(unittest.TestCase):
    def test_ocpedge_closed_is_done(self):
        assert is_story_done({"key": "OCPEDGE-1", "status": "Closed"}) is True

    def test_ocpedge_done_is_not_done(self):
        assert is_story_done({"key": "OCPEDGE-1", "status": "Done"}) is False

    def test_ocpbugs_verified_is_done(self):
        assert is_story_done({"key": "OCPBUGS-1", "status": "Verified"}) is True

    def test_ocpbugs_done_is_not_done(self):
        assert is_story_done({"key": "OCPBUGS-1", "status": "Done"}) is False


class TestDataQualityGate(unittest.TestCase):
    def test_pass_with_pointed_stories(self):
        f = _feature(epics=[{"key": "E-1", "stories": [_story(sp=5)]}],
                     all_stories=[_story(sp=5)])
        result = run_data_quality_gate([f])
        assert result[0]["status"] == "PASS"

    def test_fail_no_epics(self):
        f = _feature(epics=[], all_stories=[])
        result = run_data_quality_gate([f])
        assert result[0]["status"] == "FAIL"

    def test_fail_no_stories(self):
        f = _feature(epics=[{"key": "E-1", "stories": []}], all_stories=[])
        result = run_data_quality_gate([f])
        assert result[0]["status"] == "FAIL"

    def test_warn_low_pointed(self):
        stories = [_story(f"S-{i}", sp=0) for i in range(8)] + [_story("S-9", sp=5)]
        f = _feature(epics=[{"key": "E-1", "stories": stories}], all_stories=stories)
        result = run_data_quality_gate([f])
        assert result[0]["status"] == "WARN"

    def test_bugs_dont_count_for_gate(self):
        stories = [_story("B-1", issue_type="Bug", sp=0)]
        f = _feature(epics=[{"key": "E-1", "stories": stories}], all_stories=stories)
        result = run_data_quality_gate([f])
        assert result[0]["status"] == "FAIL"

    def test_closed_unpointed_excluded_from_pointed_pct(self):
        stories = [
            _story("S-1", sp=5, status="To Do"),
            _story("S-2", sp=0, status="Closed"),
        ]
        f = _feature(epics=[{"key": "E-1", "stories": stories}], all_stories=stories)
        result = run_data_quality_gate([f])
        assert result[0]["status"] == "PASS"
        assert result[0]["pointed_pct"] == 100


class TestCapacityCheck(unittest.TestCase):
    def test_over_capacity(self):
        stories = [_story("S-1", "alice@x.com", sp=20)]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = run_capacity_check([f], gate, roster, 2)
        assert result[0]["status"] == "OVER"
        assert result[0]["overrun"] == 4

    def test_under_capacity(self):
        stories = [_story("S-1", "alice@x.com", sp=5)]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = run_capacity_check([f], gate, roster, 2)
        assert result[0]["status"] == "OK"

    def test_excludes_fail_features(self):
        stories = [_story("S-1", "alice@x.com", sp=100)]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "FAIL"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = run_capacity_check([f], gate, roster, 2)
        # Roster members always appear so capacity covers the whole team; the
        # FAIL feature's stories must not count.
        assert len(result) == 1
        assert result[0]["person"] == "alice@x.com"
        assert result[0]["assigned_sp"] == 0
        assert result[0]["remaining_capacity"] == 16

    def test_excludes_bugs_from_sp(self):
        stories = [
            _story("S-1", "alice@x.com", sp=5),
            _story("B-1", "alice@x.com", sp=0, issue_type="Bug"),
        ]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = run_capacity_check([f], gate, roster, 2)
        assert result[0]["assigned_sp"] == 5

    def test_non_roster_flagged(self):
        stories = [_story("S-1", "bot@x.com", sp=5)]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster()
        result = run_capacity_check([f], gate, roster, 2)
        assert result[0]["in_roster"] is False


class TestTimelineCheck(unittest.TestCase):
    def test_on_track(self):
        stories = [_story("S-1", "alice@x.com", sp=8)]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = run_timeline_check([f], gate, roster, 3)
        assert result[0]["risk"] == "OK"

    def test_behind_schedule(self):
        stories = [_story("S-1", "alice@x.com", sp=30)]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = run_timeline_check([f], gate, roster, 1)
        assert result[0]["risk"] == "HIGH"
        assert result[0]["gap"] > 0

    def test_proportional_velocity(self):
        s1 = _story("S-1", "alice@x.com", sp=10, epic_key="E-1")
        s2 = _story("S-2", "alice@x.com", sp=10, epic_key="E-2")
        f1 = _feature("F-1", all_stories=[s1])
        f2 = _feature("F-2", all_stories=[s2])
        gate = [{"feature_key": "F-1", "status": "PASS"}, {"feature_key": "F-2", "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = run_timeline_check([f1, f2], gate, roster, 3)
        assert result[0]["velocity_per_sprint"] == 4.0
        assert result[1]["velocity_per_sprint"] == 4.0

    def test_no_contributors(self):
        stories = [_story("S-1", assignee=None, sp=10)]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        result = run_timeline_check([f], gate, _roster(), 3)
        assert result[0]["risk"] == "NO_CONTRIBUTORS"

    def test_zero_remaining_sprints(self):
        stories = [_story("S-1", "alice@x.com", sp=10)]
        f = _feature(all_stories=stories)
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        roster = _roster(("alice@x.com", "Alice", 8))
        result = run_timeline_check([f], gate, roster, 0)
        assert result[0]["risk"] == "HIGH"


class TestAssignmentCheck(unittest.TestCase):
    def test_spof_detected(self):
        stories = [_story("S-1", "alice@x.com"), _story("S-2", "alice@x.com")]
        f = _feature(all_stories=stories)
        result = run_assignment_check([f])
        assert len(result["spof"]) == 1

    def test_no_spof_with_multiple_contributors(self):
        stories = [_story("S-1", "alice@x.com"), _story("S-2", "bob@x.com")]
        f = _feature(all_stories=stories)
        result = run_assignment_check([f])
        assert len(result["spof"]) == 0

    def test_unassigned_detected(self):
        stories = [_story("S-1", assignee=None, sp=5)]
        f = _feature(all_stories=stories)
        result = run_assignment_check([f])
        assert len(result["unassigned"]) == 1
        assert result["unassigned"][0]["count"] == 1

    def test_bugs_excluded_from_spof(self):
        stories = [
            _story("S-1", "alice@x.com", sp=5),
            _story("B-1", "bot@x.com", sp=0, issue_type="Bug"),
        ]
        f = _feature(all_stories=stories)
        result = run_assignment_check([f])
        assert len(result["spof"]) == 1

    def test_bugs_excluded_from_unassigned(self):
        stories = [_story("B-1", assignee=None, sp=0, issue_type="Bug")]
        f = _feature(all_stories=stories)
        result = run_assignment_check([f])
        assert len(result["unassigned"]) == 0


class TestCompositeCheck(unittest.TestCase):
    def _run(self, features, gate_status="PASS", timeline_risk="OK", capacity_over=False,
             has_spof=False, has_unassigned=False, has_sizing=False, count_sizing=False):
        gate = [{"feature_key": f["key"], "status": gate_status} for f in features]
        capacity = [{"person": "alice@redhat.com", "status": "OVER"}] if capacity_over else []
        timeline = [{"feature_key": f["key"], "risk": timeline_risk} for f in features]
        spof = [{"feature_key": feat["key"], "sole_contributor": "alice"} for feat in features] if has_spof else []
        unassigned_list = [{"feature_key": feat["key"], "count": 1, "sp": 5} for feat in features] if has_unassigned else []
        assignment = {"spof": spof, "unassigned": unassigned_list}
        sizing = [{"feature_key": feat["key"], "assessment": "Undersized"} for feat in features] if has_sizing else []
        return run_composite_check(features, gate, capacity, timeline, assignment, sizing,
                                   count_sizing=count_sizing)

    def test_low_no_signals(self):
        f = _feature(all_stories=[_story()])
        result = self._run([f])
        assert result[0]["composite_risk"] == "LOW"

    def test_medium_two_signals(self):
        f = _feature(all_stories=[_story()])
        result = self._run([f], has_spof=True, capacity_over=True)
        assert result[0]["composite_risk"] == "MEDIUM"

    def test_high_three_signals(self):
        f = _feature(all_stories=[_story()])
        result = self._run([f], timeline_risk="HIGH", has_spof=True, capacity_over=True)
        assert result[0]["composite_risk"] == "HIGH"

    def test_sizing_shown_but_not_counted_by_default(self):
        f = _feature(all_stories=[_story()])
        result = self._run([f], has_spof=True, has_sizing=True)
        assert result[0]["sizing"] == "Undersized"
        assert result[0]["signal_count"] == 1
        assert result[0]["composite_risk"] == "LOW"

    def test_sizing_counted_when_opted_in(self):
        f = _feature(all_stories=[_story()])
        result = self._run([f], has_spof=True, has_sizing=True, count_sizing=True)
        assert result[0]["signal_count"] == 2

    def test_spof_and_unassigned_merged_as_one_signal(self):
        f = _feature(all_stories=[_story()])
        result = self._run([f], has_spof=True, has_unassigned=True)
        assert result[0]["signal_count"] == 1
        assert result[0]["composite_risk"] == "LOW"

    def test_data_quality_fail_counts_as_signal(self):
        f = _feature(all_stories=[_story()])
        result = self._run([f], gate_status="FAIL", has_spof=True)
        assert result[0]["signal_count"] == 2
        assert result[0]["composite_risk"] == "MEDIUM"

    def test_sorted_high_first(self):
        f1 = _feature("F-1", all_stories=[_story("S-1")])
        f2 = _feature("F-2", all_stories=[_story("S-2")])
        gate = [{"feature_key": "F-1", "status": "PASS"}, {"feature_key": "F-2", "status": "PASS"}]
        capacity = []
        timeline = [{"feature_key": "F-1", "risk": "OK"}, {"feature_key": "F-2", "risk": "HIGH"}]
        assignment = {"spof": [{"feature_key": "F-2", "sole_contributor": "a"}],
                      "unassigned": [{"feature_key": "F-2", "count": 1, "sp": 0}]}
        capacity = [{"person": "alice@redhat.com", "status": "OVER"}]
        sizing = [{"feature_key": "F-2", "assessment": "Undersized"}]
        result = run_composite_check([f1, f2], gate, capacity, timeline, assignment, sizing)
        assert result[0]["feature_key"] == "F-2"
        assert result[0]["composite_risk"] == "HIGH"


class TestBuildHierarchy(unittest.TestCase):
    def test_basic_hierarchy(self):
        features_data = {"features": [{"key": "F-1", "summary": "Feature 1", "status": "In Progress",
                                        "size": "M", "sme": "None", "type": "Feature"}]}
        epics_data = {"epics": [{"key": "E-1", "labels": []}], "feature_to_epics": {"F-1": ["E-1"]}}
        stories_data = {"stories": [_story("S-1", epic_key="E-1")]}
        bugs_data = {"bugs": []}
        result, _bugs = build_hierarchy(features_data, epics_data, stories_data, bugs_data, "none")
        assert len(result) == 1
        assert len(result[0]["epics"]) == 1
        assert len(result[0]["all_stories"]) == 1

    def test_component_filter_by_label(self):
        features_data = {"features": [
            {"key": "F-1", "summary": "F1", "status": "IP", "size": "M", "sme": "N", "type": "Feature"},
            {"key": "F-2", "summary": "F2", "status": "IP", "size": "M", "sme": "N", "type": "Feature"},
        ]}
        epics_data = {"epics": [
            {"key": "E-1", "labels": ["tna"]},
            {"key": "E-2", "labels": ["lvms"]},
        ], "feature_to_epics": {"F-1": ["E-1"], "F-2": ["E-2"]}}
        stories_data = {"stories": [_story("S-1", epic_key="E-1"), _story("S-2", epic_key="E-2")]}
        bugs_data = {"bugs": []}
        result, _ = build_hierarchy(features_data, epics_data, stories_data, bugs_data, "tna")
        assert len(result) == 1
        assert result[0]["key"] == "F-1"

    def test_no_filter(self):
        features_data = {"features": [{"key": "F-1", "summary": "F1", "status": "IP", "size": "M", "sme": "N", "type": "Feature"}]}
        epics_data = {"epics": [], "feature_to_epics": {}}
        stories_data = {"stories": []}
        bugs_data = {"bugs": []}
        result, _ = build_hierarchy(features_data, epics_data, stories_data, bugs_data, "none")
        assert len(result) == 1

    def _make_data(self, story_key="OCPEDGE-1", epic_labels=None, story_component=""):
        s = _story(story_key, epic_key="E-1")
        s["component"] = story_component
        features_data = {"features": [{"key": "F-1", "summary": "F1", "status": "IP", "size": "M", "sme": "N", "type": "Feature"}]}
        epics_data = {"epics": [{"key": "E-1", "labels": epic_labels or []}], "feature_to_epics": {"F-1": ["E-1"]}}
        stories_data = {"stories": [s]}
        bugs_data = {"bugs": []}
        return features_data, epics_data, stories_data, bugs_data

    def test_microshift_filter_matches_microshift_component(self):
        fd, ed, sd, bd = self._make_data(story_component="MicroShift")
        result, _ = build_hierarchy(fd, ed, sd, bd, "microshift")
        assert len(result) == 1

    def test_microshift_filter_matches_subcomponent(self):
        fd, ed, sd, bd = self._make_data(story_component="MicroShift / Networking")
        result, _ = build_hierarchy(fd, ed, sd, bd, "microshift")
        assert len(result) == 1

    def test_microshift_filter_matches_future_subcomponent(self):
        fd, ed, sd, bd = self._make_data(story_component="MicroShift / etcd")
        result, _ = build_hierarchy(fd, ed, sd, bd, "microshift")
        assert len(result) == 1

    def test_microshift_filter_matches_ushift_key(self):
        fd, ed, sd, bd = self._make_data(story_key="USHIFT-100")
        result, _ = build_hierarchy(fd, ed, sd, bd, "microshift")
        assert len(result) == 1

    def test_sno_filter_does_not_match_ushift(self):
        fd, ed, sd, bd = self._make_data(story_key="USHIFT-100")
        result, _ = build_hierarchy(fd, ed, sd, bd, "sno")
        assert len(result) == 0

    def test_sno_filter_matches_sno_component(self):
        fd, ed, sd, bd = self._make_data(story_component="Installer / Single Node OpenShift")
        result, _ = build_hierarchy(fd, ed, sd, bd, "sno")
        assert len(result) == 1

    def test_tna_filter_does_not_match_microshift(self):
        fd, ed, sd, bd = self._make_data(story_component="MicroShift")
        result, _ = build_hierarchy(fd, ed, sd, bd, "tna")
        assert len(result) == 0

    def test_tna_filter_matches_label(self):
        fd, ed, sd, bd = self._make_data(epic_labels=["tna"])
        result, _ = build_hierarchy(fd, ed, sd, bd, "tna")
        assert len(result) == 1


class TestBugLoadFilter(unittest.TestCase):
    def _bug(self, key="OCPBUGS-1", component="MicroShift"):
        return {"key": key, "summary": f"Bug {key}", "type": "Bug", "status": "NEW",
                "priority": "Blocker", "assignee": None, "component": component}

    def test_microshift_filter_matches_all_subcomponents(self):
        bugs = [self._bug("B-1", "MicroShift"), self._bug("B-2", "MicroShift / Networking"),
                self._bug("B-3", "Two Node Fencing")]
        result = run_bug_load_check(bugs, [], "microshift")
        assert len(result["unassigned_blocker_critical"]) == 2

    def test_microshift_filter_matches_future_subcomponent(self):
        bugs = [self._bug("B-1", "MicroShift / etcd")]
        result = run_bug_load_check(bugs, [], "microshift")
        assert len(result["unassigned_blocker_critical"]) == 1

    def test_sno_filter_excludes_microshift(self):
        bugs = [self._bug("B-1", "MicroShift"), self._bug("B-2", "Installer / Single Node OpenShift")]
        result = run_bug_load_check(bugs, [], "sno")
        assert len(result["unassigned_blocker_critical"]) == 1
        assert result["unassigned_blocker_critical"][0]["key"] == "B-2"

    def test_tna_filter(self):
        bugs = [self._bug("B-1", "Two Node with Arbiter"), self._bug("B-2", "MicroShift")]
        result = run_bug_load_check(bugs, [], "tna")
        assert len(result["unassigned_blocker_critical"]) == 1
        assert result["unassigned_blocker_critical"][0]["key"] == "B-1"


class TestBugLoadCheck(unittest.TestCase):
    def _bug(self, key="OCPBUGS-1", priority="Blocker", assignee=None, component="Two Node Fencing"):
        return {"key": key, "summary": f"Bug {key}", "type": "Bug", "status": "NEW",
                "priority": priority, "assignee": assignee, "component": component}

    def test_unassigned_blocker_detected(self):
        result = run_bug_load_check([self._bug()], [], "none")
        assert len(result["unassigned_blocker_critical"]) == 1

    def test_assigned_blocker_not_flagged(self):
        result = run_bug_load_check([self._bug(assignee="alice@x.com")], [], "none")
        assert len(result["unassigned_blocker_critical"]) == 0

    def test_major_not_flagged(self):
        result = run_bug_load_check([self._bug(priority="Major")], [], "none")
        assert len(result["unassigned_blocker_critical"]) == 0

    def test_component_filter(self):
        bugs = [self._bug("B-1", component="Two Node Fencing"), self._bug("B-2", component="Two Node with Arbiter")]
        result = run_bug_load_check(bugs, [], "tnf")
        assert len(result["unassigned_blocker_critical"]) == 1

    def test_epic_bugs_included(self):
        epic_story_bug = _story("OCPBUGS-99", assignee=None, sp=0, issue_type="Bug", priority="Critical")
        epic_story_bug["component"] = "Two Node Fencing"
        result = run_bug_load_check([], [epic_story_bug], "none")
        assert len(result["unassigned_blocker_critical"]) == 1


class TestSizingCheck(unittest.TestCase):
    def test_unsized_flagged(self):
        f = _feature(size="Unsized", all_stories=[_story(sp=5)])
        result = run_sizing_check([f], [{"feature_key": f["key"], "sprints_needed": 1, "risk": "OK"}])
        assert result[0]["assessment"] == "Unsized"

    def test_correctly_sized_not_flagged(self):
        f = _feature(size="M", all_stories=[_story(sp=5)], epics=[{"key": "E-1"}])
        result = run_sizing_check([f], [{"feature_key": f["key"], "sprints_needed": 2, "risk": "OK"}])
        assert len(result) == 0

    def test_undersized_by_sprints(self):
        f = _feature(size="XS", all_stories=[_story(sp=30)], epics=[{"key": "E-1"}])
        result = run_sizing_check([f], [{"feature_key": f["key"], "sprints_needed": 5, "risk": "HIGH"}])
        assert result[0]["assessment"] == "Undersized"


class TestCompositeCheckBugSignal(unittest.TestCase):
    def test_bug_signal_fires_for_epic_linked_bugs(self):
        bug_story = _story("OCPBUGS-1", assignee=None, sp=0, issue_type="Bug", priority="Blocker")
        f = _feature(all_stories=[_story("S-1"), bug_story])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        result = run_composite_check(
            [f], gate, [], [{"feature_key": f["key"], "risk": "OK"}],
            {"spof": [], "unassigned": []}, []
        )
        assert result[0]["bugs"] == "HIGH"
        assert result[0]["signal_count"] == 1

    def test_no_bug_signal_when_bugs_assigned(self):
        bug_story = _story("OCPBUGS-1", assignee="alice@x.com", sp=0, issue_type="Bug", priority="Blocker")
        f = _feature(all_stories=[_story("S-1"), bug_story])
        gate = [{"feature_key": f["key"], "status": "PASS"}]
        result = run_composite_check(
            [f], gate, [], [{"feature_key": f["key"], "risk": "OK"}],
            {"spof": [], "unassigned": []}, []
        )
        assert result[0]["bugs"] == "OK"


if __name__ == "__main__":
    unittest.main()
