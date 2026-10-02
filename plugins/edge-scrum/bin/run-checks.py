#!/usr/bin/env python3
"""Run data-quality gate and planning risk checks. Deterministic — no LLM needed.

Every figure that reaches the report is computed here and recorded in the
``method`` block of checks.json (formula, inputs, result) so the report can
show how each conclusion was reached. See
references/release-planning-method.md for the rationale behind thresholds.
"""

import argparse
import re
import statistics
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import load_json, write_output, parse_date, extract_sprint_number

DONE_STATUSES_STORIES = {"Closed"}
DONE_STATUSES_OCPBUGS = {"Closed", "Verified"}
DONE_STATUSES_EPICS = {"Closed", "Dev Complete"}
ACTIVE_STORY_STATUSES = {"In Progress", "Review"}
NEW_FEATURE_STATUSES = {"New"}

# Law 05: XS ~2 dev sprints, S ~2-3, M ~3-4, L ~4+, XL ~5 (entire release).
SIZE_TO_MAX_SPRINTS = {"XS": 2, "S": 3, "M": 4, "L": 5, "XL": 5}
DEFAULT_SP_TARGET = 8
IMPORTANT_PRIORITIES = {"Blocker", "Critical"}
SMALL_UNPOINTED_REMAINDER = 2   # low-priority unpointed leftovers tolerated on active work
DEFAULT_ACTIVITY_DAYS = 30
FALLBACK_SP_RANGE = (2, 3, 5)  # low / typical / high when no closed pointed stories exist

COMPONENT_SHORT_NAMES = {
    "tna": "Two Node with Arbiter",
    "tnf": "Two Node Fencing",
    "lvms": "Logical Volume Manager Storage",
    "topolvm": "Logical Volume Manager Storage",
    "microshift": "MicroShift",
    "sno": "Installer / Single Node OpenShift",
}


def is_microshift_component(comp):
    """Match MicroShift and any subcomponent (MicroShift / Networking, etc.)."""
    return comp == "MicroShift" or comp.startswith("MicroShift / ")


def _version_matches(names, version):
    """True if any version name refers to `version`.

    Accepts 5.1, 5.1.0, 5.1.z, openshift-5.1, openshift-5.1.0 and any name in
    which the version appears as a whole token ("MicroShift 5.1"). A 5.10
    name does not match 5.1.
    """
    if not version:
        return True
    v = re.escape(str(version).removesuffix(".0"))
    pattern = re.compile(rf"(?<![\d.]){v}(?:\.0|\.z)?(?![\d.])", re.IGNORECASE)
    return any(pattern.search(str(n)) for n in names)


# --- Hierarchy ---


def build_hierarchy(features, epics, stories, bugs, component_filter, version=None):
    """Build Feature -> Epics -> Stories tree. Returns (feature dicts, unlinked bugs).

    Epics that are already done (Dev Complete/Closed) or that target another
    release are removed from the scope math and recorded on the feature as
    ``excluded_epics`` so the report can show what was left out and why.
    """
    epic_by_key = {e["key"]: e for e in epics.get("epics", [])}
    feature_to_epics = epics.get("feature_to_epics", {})

    stories_by_epic = {}
    for s in stories.get("stories", []):
        stories_by_epic.setdefault(s["epic_key"], []).append(s)

    cf_lower = component_filter.lower() if component_filter and component_filter != "none" else None
    cf_full = COMPONENT_SHORT_NAMES.get(cf_lower, cf_lower) if cf_lower else None
    is_microshift = cf_lower == "microshift" if cf_lower else False

    def matches_component(comp):
        if is_microshift:
            return is_microshift_component(comp)
        return comp == cf_full if cf_full else False

    result = []
    for f in features.get("features", []):
        fkey = f["key"]
        f_epics = []
        excluded = []
        for ekey in feature_to_epics.get(fkey, []):
            epic = epic_by_key.get(ekey)
            if not epic:
                continue
            epic_stories = stories_by_epic.get(ekey, [])
            open_sp = sum(s["sp"] for s in epic_stories if not is_story_done(s) and s["type"] != "Bug")
            open_count = sum(1 for s in epic_stories if not is_story_done(s) and s["type"] != "Bug")
            reason = None
            if epic.get("status") in DONE_STATUSES_EPICS:
                reason = f"epic is {epic['status']}"
            else:
                targets = list(dict.fromkeys(
                    list(epic.get("target_versions") or []) + list(epic.get("fix_versions") or [])
                ))
                if version and targets and not _version_matches(targets, version):
                    reason = f"epic targets {', '.join(str(t) for t in targets)}"
            if reason:
                excluded.append({
                    "key": ekey,
                    "summary": epic.get("summary", ""),
                    "status": epic.get("status", ""),
                    "reason": reason,
                    "open_stories": open_count,
                    "open_sp": open_sp,
                })
                continue
            f_epics.append({**epic, "stories": epic_stories})

        if cf_lower:
            has_match = False
            for e in f_epics:
                labels_lower = [lbl.lower() for lbl in e.get("labels", [])]
                if cf_lower in labels_lower:
                    has_match = True
                    break
                for s in e.get("stories", []):
                    s_comp = s.get("component", "")
                    if s_comp and matches_component(s_comp):
                        has_match = True
                        break
                    if is_microshift and s["key"].startswith("USHIFT-"):
                        has_match = True
                        break
                if has_match:
                    break
            if not has_match:
                continue

        all_stories = []
        for e in f_epics:
            all_stories.extend(e.get("stories", []))

        result.append({
            "key": fkey,
            "summary": f.get("summary", ""),
            "status": f.get("status", ""),
            "size": f.get("size", "Unsized"),
            "sme": f.get("sme", "None"),
            "type": f.get("type", "Feature"),
            "rank": f.get("rank"),
            "epics": f_epics,
            "all_stories": all_stories,
            "excluded_epics": excluded,
        })

    return result, bugs.get("bugs", [])


def is_story_done(story):
    if story["key"].startswith("OCPBUGS-"):
        return story["status"] in DONE_STATUSES_OCPBUGS
    return story["status"] in DONE_STATUSES_STORIES


# --- Active / dormant classification ---


def _recently_updated(story, today, activity_days):
    upd = story.get("updated")
    if not upd or not today:
        return False
    try:
        return (parse_date(today) - parse_date(upd)).days <= activity_days
    except (ValueError, TypeError):
        return False


def _in_window_sprint(story, window):
    if not window:
        return False
    lo, hi = window
    for sp in story.get("sprints") or []:
        num = extract_sprint_number(sp.get("name", ""))
        if num is not None and lo <= num <= hi:
            return True
    return False


def feature_activity(f, today=None, activity_days=DEFAULT_ACTIVITY_DAYS, window=None):
    """Return (has_activity, evidence string). Evidence is the first signal found.

    A feature shows activity if any non-bug story is in progress/review, already
    done, was updated within `activity_days`, or sits in a sprint inside the
    release window. Assignment alone is not evidence — an assignee set at
    creation and never touched does not mean work is happening.
    """
    for s in f["all_stories"]:
        if s["type"] == "Bug":
            continue
        if s["status"] in ACTIVE_STORY_STATUSES:
            return True, f"{s['key']} is {s['status']}"
        if is_story_done(s):
            return True, f"{s['key']} is done"
    for s in f["all_stories"]:
        if s["type"] == "Bug":
            continue
        if _in_window_sprint(s, window):
            return True, f"{s['key']} is in a release sprint"
        if _recently_updated(s, today, activity_days):
            return True, f"{s['key']} updated within {activity_days} days"
    return False, "no story in progress, done, in a release sprint, or updated recently"


def classify_feature(f, today=None, activity_days=DEFAULT_ACTIVITY_DAYS, window=None):
    """Return ('active'|'dormant', reason).

    Dormant means: status New (law 05: not a commitment) AND no evidence of work.
    Anything past New is active regardless of evidence — the workflow state is
    authoritative (law 12).
    """
    if f["status"] not in NEW_FEATURE_STATUSES:
        return "active", f"status {f['status'] or 'unknown'}"
    if not f["epics"]:
        excluded = f.get("excluded_epics") or []
        if excluded:
            return "dormant", f"New, all {len(excluded)} epics excluded ({excluded[0]['reason']})"
        return "dormant", "New, no epics"
    non_bug = [s for s in f["all_stories"] if s["type"] != "Bug"]
    if not non_bug:
        return "dormant", "New, epics have no stories"
    has_activity, evidence = feature_activity(f, today, activity_days, window)
    if has_activity:
        return "active", f"New, but {evidence}"
    return "dormant", f"New, {evidence}"


def split_features(features, today=None, activity_days=DEFAULT_ACTIVITY_DAYS, window=None):
    active, dormant = [], []
    for f in features:
        cls, reason = classify_feature(f, today, activity_days, window)
        f["classification"] = cls
        f["classification_reason"] = reason
        (active if cls == "active" else dormant).append(f)
    return active, dormant


# --- Data Quality Gate ---


def run_data_quality_gate(features):
    """Categorize each feature as PASS/WARN/FAIL by how well its REMAINING work is
    estimated — completion-aware and importance-weighted.

    Finished stories are excluded from the ratio, so a nearly-done feature is not
    penalised for the estimates on work it has already shipped. Escalated
    (Blocker/Critical) open work without points always WARNs, because that is the
    unrefined work most likely to hurt. A couple of low-priority unpointed leftovers
    on a feature that is clearly underway is treated as noise, not a risk.
    Story size cannot weight unpointed items (they have no points by definition), so
    priority stands in for importance here.
    """
    results = []
    for f in features:
        epic_count = len(f["epics"])
        non_bug = [s for s in f["all_stories"] if s["type"] != "Bug"]
        total = len(non_bug)
        done_count = sum(1 for s in non_bug if is_story_done(s))
        open_stories = [s for s in non_bug if not is_story_done(s)]
        open_count = len(open_stories)
        unpointed_open = [s for s in open_stories if s["sp"] <= 0]
        pointed_open_count = open_count - len(unpointed_open)
        critical_unpointed = [s for s in unpointed_open
                              if s.get("priority") in IMPORTANT_PRIORITIES]

        completion_pct = round(done_count / total * 100) if total else 0
        pointed_pct = round(pointed_open_count / open_count * 100) if open_count else 100

        # Epics that were removed from scope (done, or targeting another release).
        # A feature whose only epics were excluded is finished or mis-targeted, not
        # un-refined, so the reason must say so rather than "no epics created".
        excluded = f.get("excluded_epics") or []
        all_epics_excluded = epic_count == 0 and bool(excluded)

        if epic_count == 0:
            if excluded:
                status, reason = "FAIL", f"all {len(excluded)} epics excluded ({excluded[0]['reason']})"
            else:
                status, reason = "FAIL", "no epics created"
        elif total == 0:
            status, reason = "FAIL", "epics have no stories"
        elif open_count == 0:
            status, reason = "PASS", f"all {total} stories complete"
        elif not unpointed_open:
            status, reason = "PASS", ""
        elif critical_unpointed:
            n = len(critical_unpointed)
            verb = "story lacks" if n == 1 else "stories lack"
            status, reason = "WARN", f"{n} blocker/critical open {verb} an estimate"
        elif pointed_pct >= 50:
            status, reason = "PASS", ""
        elif len(unpointed_open) <= SMALL_UNPOINTED_REMAINDER and done_count >= 1:
            status, reason = "PASS", (
                f"nearly done ({completion_pct}% complete), "
                f"only {len(unpointed_open)} low-priority open {'story' if len(unpointed_open)==1 else 'stories'} unpointed")
        else:
            status, reason = "WARN", f"{len(unpointed_open)} of {open_count} open stories unestimated"

        # An otherwise-clean feature that still has an epic with no stories is only
        # partially refined — surface it as a WARN rather than a silent PASS.
        empty_epics = [e for e in f["epics"]
                       if not any(s["type"] != "Bug" for s in e.get("stories", []))]
        if status == "PASS" and len(empty_epics) >= 1:
            status = "WARN"
            reason = f"{len(empty_epics)} of {len(f['epics'])} epics have no stories"

        results.append({
            "feature_key": f["key"],
            "summary": f["summary"],
            "sme": f.get("sme", "None"),
            "epic_count": epic_count,
            "story_count": total,
            "done_count": done_count,
            "open_count": open_count,
            "completion_pct": completion_pct,
            "pointed_pct": pointed_pct,
            "unpointed_count": len(unpointed_open),
            "critical_unpointed": len(critical_unpointed),
            "all_epics_excluded": all_epics_excluded,
            "empty_epic_count": len(empty_epics),
            "status": status,
            "reason": reason,
        })

    return results


# --- Check 1: Capacity ---


def run_capacity_check(features, gate_results, roster, remaining_sprints):
    """Per-person load vs capacity.

    Every roster member appears (even with no work) so the capacity total covers
    the whole team. Non-roster assignees appear with capacity 0 and
    ``in_roster: false`` — their work counts as scope, and the report asks for
    the roster to be reconciled. Only stories under PASS/WARN features count.
    """
    passed_keys = {g["feature_key"] for g in gate_results if g["status"] in ("PASS", "WARN")}
    roster_map = {m["username"]: m for m in roster.get("members", [])}

    sp_by_person = {}
    unpointed_by_person = {}
    person_features = {}
    person_display = {m["username"]: m.get("display_name", m["username"]) for m in roster.get("members", [])}

    for f in features:
        if f["key"] not in passed_keys:
            continue
        for s in f["all_stories"]:
            if is_story_done(s) or s["type"] == "Bug" or not s["assignee"]:
                continue
            person_display.setdefault(s["assignee"], s.get("assignee_display", s["assignee"]))
            person_features.setdefault(s["assignee"], set()).add(f["key"])
            if s["sp"] > 0:
                sp_by_person[s["assignee"]] = sp_by_person.get(s["assignee"], 0) + s["sp"]
            else:
                unpointed_by_person[s["assignee"]] = unpointed_by_person.get(s["assignee"], 0) + 1

    people = set(roster_map) | set(sp_by_person) | set(unpointed_by_person)
    results = []
    for person in people:
        member = roster_map.get(person)
        assigned_sp = sp_by_person.get(person, 0)
        sp_target = member.get("sp_target", DEFAULT_SP_TARGET) if member else DEFAULT_SP_TARGET
        capacity = sp_target * remaining_sprints if member else 0
        overrun = max(0, assigned_sp - capacity) if member else assigned_sp
        results.append({
            "person": person,
            "display_name": person_display.get(person, person),
            "assigned_sp": assigned_sp,
            "unpointed_assigned": unpointed_by_person.get(person, 0),
            "sp_target": sp_target,
            "remaining_capacity": capacity,
            "overrun": overrun,
            "status": "OVER" if (member and overrun > 0) else ("NOT_IN_ROSTER" if not member else "OK"),
            "features": sorted(person_features.get(person, set())),
            "in_roster": member is not None,
        })

    results.sort(key=lambda r: (-r["assigned_sp"], r["display_name"]))
    return results


# --- Check 2: Timeline ---


def run_timeline_check(features, gate_results, roster, remaining_sprints):
    """Per-feature timeline projection with proportional velocity."""
    passed_keys = {g["feature_key"] for g in gate_results if g["status"] in ("PASS", "WARN")}
    roster_map = {m["username"]: m for m in roster.get("members", [])}

    global_sp = {}
    for f in features:
        for s in f["all_stories"]:
            if is_story_done(s) or s["type"] == "Bug" or s["sp"] <= 0 or not s["assignee"]:
                continue
            global_sp[s["assignee"]] = global_sp.get(s["assignee"], 0) + s["sp"]

    results = []
    for f in features:
        base = {"feature_key": f["key"], "summary": f["summary"], "sme": f.get("sme", "None"),
                "rank": f.get("rank")}
        if f["key"] not in passed_keys:
            results.append({**base, "remaining_sp": 0, "unpointed_open": 0, "velocity_per_sprint": 0,
                            "sprints_needed": 0, "sprints_left": remaining_sprints, "gap": 0, "risk": "N/A"})
            continue

        remaining_sp = 0
        unpointed_open = 0
        feature_sp_by_person = {}
        for s in f["all_stories"]:
            if is_story_done(s) or s["type"] == "Bug":
                continue
            remaining_sp += s["sp"]
            if s["sp"] <= 0:
                unpointed_open += 1
            if s["sp"] <= 0 or not s["assignee"]:
                continue
            feature_sp_by_person[s["assignee"]] = feature_sp_by_person.get(s["assignee"], 0) + s["sp"]

        velocity = 0.0
        for person, person_feature_sp in feature_sp_by_person.items():
            # Non-roster people contribute no velocity, matching capacity (zero)
            # — the roster-drift prompt is where this gets resolved.
            sp_target = roster_map.get(person, {}).get("sp_target", 0)
            total_sp = global_sp.get(person, person_feature_sp)
            fraction = person_feature_sp / total_sp if total_sp > 0 else 0
            velocity += sp_target * fraction

        if velocity <= 0:
            sprints_needed = float("inf") if remaining_sp > 0 else 0
        else:
            sprints_needed = remaining_sp / velocity

        gap = max(0, sprints_needed - remaining_sprints) if sprints_needed != float("inf") else remaining_sprints
        risk = "NO_CONTRIBUTORS" if velocity <= 0 and remaining_sp > 0 else (
            "HIGH" if gap > 0 else "OK"
        )

        results.append({
            **base,
            "remaining_sp": remaining_sp,
            "unpointed_open": unpointed_open,
            "velocity_per_sprint": round(velocity, 1),
            "sprints_needed": round(sprints_needed, 1) if sprints_needed != float("inf") else "inf",
            "sprints_left": remaining_sprints,
            "gap": round(gap, 1),
            "risk": risk,
        })

    return results


# --- Check 3: Assignment ---


def run_assignment_check(features):
    """SPOF detection and unassigned work. Non-bug stories only."""
    spof = []
    unassigned = []

    for f in features:
        non_done_non_bug = [s for s in f["all_stories"] if not is_story_done(s) and s["type"] != "Bug"]
        contributors = set(s["assignee"] for s in non_done_non_bug if s["assignee"])
        unassigned_stories = [s for s in non_done_non_bug if not s["assignee"]]

        if len(contributors) == 1:
            sole = list(contributors)[0]
            display = next(
                (s.get("assignee_display", sole) for s in non_done_non_bug if s["assignee"] == sole),
                sole
            )
            spof.append({
                "feature_key": f["key"],
                "summary": f["summary"],
                "sme": f.get("sme", "None"),
                "sole_contributor": sole,
                "sole_contributor_display": display,
            })

        if unassigned_stories:
            unassigned_sp = sum(s["sp"] for s in unassigned_stories)
            unassigned.append({
                "feature_key": f["key"],
                "summary": f["summary"],
                "sme": f.get("sme", "None"),
                "count": len(unassigned_stories),
                "sp": unassigned_sp,
            })

    return {"spof": spof, "unassigned": unassigned}


# --- Check 4: Bug Load ---


def run_bug_load_check(all_bugs, epic_stories, component_filter):
    """Unassigned Blocker/Critical bugs."""
    bugs = [b for b in all_bugs if not is_story_done(b)]
    for s in epic_stories:
        if s["type"] == "Bug" and not is_story_done(s):
            bugs.append(s)

    cf_lower = component_filter.lower() if component_filter and component_filter != "none" else None
    is_ms = cf_lower == "microshift" if cf_lower else False
    cf_full = COMPONENT_SHORT_NAMES.get(cf_lower, cf_lower) if cf_lower else None

    if cf_lower:
        def bug_matches(comp):
            if is_ms:
                return is_microshift_component(comp)
            return comp == cf_full if cf_full else False
        bugs = [b for b in bugs if bug_matches(b.get("component", ""))]

    unassigned_bc = []
    by_component = {}

    for b in bugs:
        comp = b.get("component", "Unknown")
        priority = b.get("priority", "Major")
        by_component.setdefault(comp, {"total": 0, "blocker": 0, "critical": 0, "unassigned": 0})
        by_component[comp]["total"] += 1
        if priority == "Blocker":
            by_component[comp]["blocker"] += 1
        elif priority == "Critical":
            by_component[comp]["critical"] += 1

        if priority in ("Blocker", "Critical") and not b.get("assignee"):
            by_component[comp]["unassigned"] += 1
            unassigned_bc.append({
                "key": b["key"],
                "summary": b.get("summary", ""),
                "priority": priority,
                "component": comp,
                "status": b.get("status", ""),
            })

    return {
        "unassigned_blocker_critical": unassigned_bc,
        "by_component": by_component,
    }


# --- Check 5: Sizing (informational) ---


def run_sizing_check(features, timeline_results, roster=None):
    """T-shirt size vs actual scope.

    Compares the feature's total pointed scope (done + open) against what the
    T-shirt size implies if its contributors worked on it exclusively
    (``dedicated_sprints``). Unlike the timeline projection this does not
    depend on how busy contributors are elsewhere, so an overloaded person does
    not make every feature they touch look undersized.
    """
    timeline_map = {t["feature_key"]: t for t in timeline_results}
    roster_map = {m["username"]: m for m in (roster or {}).get("members", [])}
    # No roster at all → assume the default target; roster present but person
    # missing → zero, consistent with capacity and timeline.
    missing_target = DEFAULT_SP_TARGET if roster is None else 0

    results = []
    for f in features:
        size = f["size"]
        open_sp = sum(s["sp"] for s in f["all_stories"] if not is_story_done(s))
        total_sp = sum(s["sp"] for s in f["all_stories"] if s["type"] != "Bug")
        epic_count = len(f["epics"])
        contributors = set(
            s["assignee"] for s in f["all_stories"]
            if not is_story_done(s) and s["type"] != "Bug" and s["assignee"]
        )
        dedicated_velocity = sum(
            roster_map.get(p, {}).get("sp_target", missing_target) for p in contributors
        )
        dedicated_sprints = round(total_sp / dedicated_velocity, 1) if dedicated_velocity > 0 else None

        timeline = timeline_map.get(f["key"], {})
        sprints_needed = timeline.get("sprints_needed", 0)
        if sprints_needed == "inf":
            sprints_needed = 99

        assessment = "OK"
        if size == "Unsized":
            assessment = "Unsized"
        elif size in SIZE_TO_MAX_SPRINTS:
            max_sprints = SIZE_TO_MAX_SPRINTS[size]
            if dedicated_sprints is not None and dedicated_sprints > max_sprints:
                assessment = "Undersized"
            elif size in ("XS", "S") and epic_count >= 4 and total_sp >= 50:
                assessment = "Undersized"
        else:
            assessment = "Unknown size"

        if assessment != "OK":
            results.append({
                "feature_key": f["key"],
                "summary": f["summary"],
                "sme": f.get("sme", "None"),
                "tshirt": size,
                "actual_sp": open_sp,
                "total_sp": total_sp,
                "epic_count": epic_count,
                "contributor_count": len(contributors),
                "dedicated_sprints": dedicated_sprints if dedicated_sprints is not None else "N/A",
                "sprints_needed": sprints_needed if sprints_needed != 99 else "N/A",
                "assessment": assessment,
            })

    return results


# --- Check 6: Composite ---


def run_composite_check(features, gate_results, capacity_results, timeline_results,
                         assignment_results, sizing_results, count_sizing=False):
    """Count independent signals per feature and assign composite risk.

    Sizing is displayed but not counted by default: it is derived from the same
    load numbers as the timeline and capacity signals and would triple-count
    one overloaded contributor.
    """
    gate_map = {g["feature_key"]: g["status"] for g in gate_results}
    overloaded_people = {c["person"] for c in capacity_results if c["status"] == "OVER"}
    timeline_risks = {t["feature_key"] for t in timeline_results if t["risk"] in ("HIGH", "NO_CONTRIBUTORS")}
    spof_keys = {s["feature_key"] for s in assignment_results["spof"]}
    unassigned_keys = {u["feature_key"] for u in assignment_results["unassigned"]}
    assignment_risk_keys = spof_keys | unassigned_keys
    sizing_assessment = {s["feature_key"]: s["assessment"] for s in sizing_results}

    bug_keys_with_issues = set()
    for f in features:
        feature_bugs = [s for s in f["all_stories"] if s["type"] == "Bug" and not is_story_done(s)
                        and s.get("priority") in ("Blocker", "Critical") and not s.get("assignee")]
        if feature_bugs:
            bug_keys_with_issues.add(f["key"])

    results = []
    for f in features:
        fkey = f["key"]
        gate_status = gate_map.get(fkey, "PASS")
        signals = 0
        signal_details = {}

        if gate_status == "FAIL":
            signals += 1
            signal_details["data_quality"] = "FAIL"
        else:
            signal_details["data_quality"] = gate_status

        if fkey in timeline_risks:
            signals += 1
            signal_details["timeline"] = "HIGH"
        else:
            signal_details["timeline"] = "OK"

        feature_contributors = set(
            s["assignee"] for s in f["all_stories"]
            if not is_story_done(s) and s["type"] != "Bug" and s["assignee"]
        )
        if feature_contributors & overloaded_people:
            signals += 1
            signal_details["capacity"] = "HIGH"
        else:
            signal_details["capacity"] = "OK"

        if fkey in assignment_risk_keys:
            signals += 1
            has_spof = fkey in spof_keys
            has_unassigned = fkey in unassigned_keys
            if has_spof and has_unassigned:
                signal_details["assignment"] = "SPOF+Unassigned"
            elif has_spof:
                signal_details["assignment"] = "SPOF"
            else:
                signal_details["assignment"] = "Unassigned"
        else:
            signal_details["assignment"] = "OK"

        if fkey in bug_keys_with_issues:
            signals += 1
            signal_details["bugs"] = "HIGH"
        else:
            signal_details["bugs"] = "OK"

        if fkey in sizing_assessment:
            if count_sizing:
                signals += 1
            # Echo the real assessment (Unsized / Undersized / Unknown size) so the
            # composite column agrees with the sizing table, rather than a blanket "Mismatch".
            signal_details["sizing"] = sizing_assessment[fkey]
        else:
            signal_details["sizing"] = "OK"

        if signals >= 3:
            composite_risk = "HIGH"
        elif signals == 2:
            composite_risk = "MEDIUM"
        else:
            composite_risk = "LOW"

        results.append({
            "feature_key": fkey,
            "summary": f["summary"],
            "sme": f.get("sme", "None"),
            "rank": f.get("rank"),
            "signal_count": signals,
            "composite_risk": composite_risk,
            **signal_details,
        })

    results.sort(key=lambda x: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(x["composite_risk"], 3), -x["signal_count"]))
    return results


# --- Cut line, hidden scope, process gaps ---


def build_cut_line(timeline_results, capacity_sp, timeline_risk_by_key=None):
    """Walk active features in PM rank order accumulating remaining SP.

    fits: cumulative <= capacity; partial: this feature crosses the line;
    over: entirely beyond capacity.

    ``timeline_risk`` is carried through so the report can flag features that fit
    within total team capacity but not at the current allocation of people (a
    feature the whole team could finish, but the assigned few cannot in time).
    """
    risk_by_key = timeline_risk_by_key or {}
    rows = [t for t in timeline_results if t["risk"] != "N/A"]
    rows.sort(key=lambda t: (t.get("rank") is None, t.get("rank") or 0))
    cum = 0
    out = []
    for t in rows:
        before = cum
        cum += t["remaining_sp"]
        if cum <= capacity_sp or t["remaining_sp"] == 0:
            fits = "fits"
        elif before < capacity_sp:
            fits = "partial"
        else:
            fits = "over"
        out.append({
            "rank": t.get("rank"),
            "feature_key": t["feature_key"],
            "summary": t["summary"],
            "sme": t.get("sme", "None"),
            "remaining_sp": t["remaining_sp"],
            "unpointed_open": t.get("unpointed_open", 0),
            "cumulative_sp": cum,
            "fits": fits,
            "timeline_risk": risk_by_key.get(t["feature_key"], t.get("risk")),
        })
    return out


def estimate_hidden_scope(features, active_keys):
    """Estimate SP for unpointed open stories from closed pointed stories' distribution."""
    closed_pointed = [
        s["sp"] for f in features for s in f["all_stories"]
        if is_story_done(s) and s["type"] != "Bug" and s["sp"] > 0
    ]
    unpointed = [
        s for f in features if f["key"] in active_keys for s in f["all_stories"]
        if not is_story_done(s) and s["type"] != "Bug" and s["sp"] <= 0
    ]
    n = len(unpointed)
    assigned = sum(1 for s in unpointed if s["assignee"])
    if len(closed_pointed) >= 4:
        srt = sorted(closed_pointed)
        q1, q2, q3 = statistics.quantiles(srt, n=4, method="inclusive")
        low, typical, high = round(q1, 1), round(q2, 1), round(q3, 1)
        basis = f"quartiles of {len(srt)} closed pointed stories"
    else:
        low, typical, high = FALLBACK_SP_RANGE
        basis = "fallback range (fewer than 4 closed pointed stories)"
    return {
        "unpointed_open": n,
        "unpointed_assigned": assigned,
        "sp_per_story_low": low,
        "sp_per_story_typical": typical,
        "sp_per_story_high": high,
        "estimate_low": round(n * low),
        "estimate_typical": round(n * typical),
        "estimate_high": round(n * high),
        "basis": basis,
    }


def build_process_gaps(features, active, dormant, capacity, sizing, hidden):
    """Systemic findings the team can act on, computed — not narrated."""
    non_roster = [c for c in capacity if not c["in_roster"]]
    no_sme_active = [f["key"] for f in active if f.get("sme") in (None, "", "None")]
    no_sme_dormant = [f["key"] for f in dormant if f.get("sme") in (None, "", "None")]
    excluded = [(f["key"], e) for f in features for e in f.get("excluded_epics", [])]
    sized = [f for f in active if f["size"] != "Unsized"]
    undersized = [s for s in sizing if s["assessment"] == "Undersized"]
    return {
        "roster_drift": [
            {"person": c["person"], "display_name": c["display_name"], "assigned_sp": c["assigned_sp"],
             "unpointed_assigned": c["unpointed_assigned"]} for c in non_roster
        ],
        "features_without_sme": {"active": no_sme_active, "dormant": no_sme_dormant},
        "unpointed_assigned_stories": hidden["unpointed_assigned"],
        "unpointed_open_stories": hidden["unpointed_open"],
        "undersized_features": len(undersized),
        "sized_active_features": len(sized),
        "excluded_epics": [
            {"feature_key": fk, **e} for fk, e in excluded
        ],
        "excluded_open_sp": sum(e["open_sp"] for _, e in excluded),
    }


def build_method(meta, roster, remaining_sprints, hidden, activity_days, window, version):
    """Formula + inputs + result for every headline figure. Rendered verbatim by the report."""
    members = roster.get("members", [])
    targets = [m.get("sp_target", DEFAULT_SP_TARGET) for m in members]
    avg_target = round(sum(targets) / len(targets), 1) if targets else DEFAULT_SP_TARGET
    win = f"S{window[0]}–S{window[1]}" if window else "n/a"
    return [
        {
            "id": "capacity",
            "label": "Capacity",
            "formula": "Σ over roster members of sp_target × sprints left (to pencils down)",
            "inputs": {"roster_members": len(members), "avg_sp_target": avg_target, "sprints_left": remaining_sprints},
            "result": f"{meta['total_capacity_sp']} SP",
            "shown_as": (f"{len(members)} people × {avg_target} SP × {remaining_sprints} sprints = {meta['total_capacity_sp']} SP"
                         if len(set(targets)) <= 1 else
                         f"Σ targets {sum(targets)} SP/sprint × {remaining_sprints} sprints = {meta['total_capacity_sp']} SP ({len(members)} people, targets {min(targets)}–{max(targets)})"),
        },
        {
            "id": "scope",
            "label": "Scope (pointed)",
            "formula": "Σ story points of open, non-bug stories under active features that pass the data-quality gate",
            "inputs": {"active_features": meta["active_features"], "assessed_features": meta["assessed_features"]},
            "result": f"{meta['total_remaining_sp']} SP",
            "shown_as": f"{meta['assessed_features']} assessed features → {meta['total_remaining_sp']} SP open",
        },
        {
            "id": "hidden_scope",
            "label": "Hidden scope",
            "formula": "unpointed open stories × SP per story (low / typical / high from closed pointed stories)",
            "inputs": {"unpointed_open": hidden["unpointed_open"], "sp_low": hidden["sp_per_story_low"],
                       "sp_typical": hidden["sp_per_story_typical"], "sp_high": hidden["sp_per_story_high"],
                       "basis": hidden["basis"]},
            "result": f"{hidden['estimate_low']}–{hidden['estimate_high']} SP",
            "shown_as": f"{hidden['unpointed_open']} × {hidden['sp_per_story_low']}–{hidden['sp_per_story_high']} SP = {hidden['estimate_low']}–{hidden['estimate_high']} SP",
        },
        {
            "id": "gap",
            "label": "Gap",
            "formula": "capacity − scope (negative = overcommitted); with hidden scope: capacity − scope − estimate",
            "inputs": {"capacity": meta["total_capacity_sp"], "scope": meta["total_remaining_sp"],
                       "hidden_low": hidden["estimate_low"], "hidden_high": hidden["estimate_high"]},
            "result": f"{meta['gap_sp']} SP",
            "shown_as": f"{meta['total_capacity_sp']} − {meta['total_remaining_sp']} = {meta['gap_sp']} SP "
                        f"({meta['gap_with_hidden_low']} to {meta['gap_with_hidden_high']} incl. hidden)",
        },
        {
            "id": "dormant",
            "label": "Active / dormant",
            "formula": f"dormant = status New AND (no epics OR no stories OR no story in progress / done / in a {win} sprint / updated within {activity_days} days)",
            "inputs": {"activity_days": activity_days, "window": win},
            "result": f"{meta['dormant_features']} of {meta['total_features']} dormant",
            "shown_as": f"{meta['active_features']} active, {meta['dormant_features']} dormant",
        },
        {
            "id": "overload",
            "label": "Person over target",
            "formula": "assigned open SP > sp_target × sprints left; unpointed assigned stories shown separately, never summed",
            "inputs": {"sprints_left": remaining_sprints},
            "result": f"{meta['overloaded_people_count']} people over",
            "shown_as": f"{meta['overloaded_people_count']} over target, {meta['non_roster_people_count']} not on roster",
        },
        {
            "id": "timeline",
            "label": "Feature timeline",
            "formula": "sprints needed = remaining SP ÷ Σ roster contributors (sp_target × share of that person's total open SP on this feature); non-roster contributors add no velocity; HIGH if > sprints left",
            "inputs": {"sprints_left": remaining_sprints},
            "result": f"{meta['timeline_high_count']} features HIGH",
            "shown_as": "see appendix timeline table for per-feature values",
        },
        {
            "id": "sizing",
            "label": "Sizing (informational)",
            "formula": "dedicated sprints = total SP ÷ Σ contributors' sp_target; Undersized if > max sprints for T-shirt (XS 2, S 3, M 4, L 5, XL 5)",
            "inputs": {"size_to_max_sprints": SIZE_TO_MAX_SPRINTS},
            "result": f"{meta['undersized_count']} undersized",
            "shown_as": "not counted in composite risk",
        },
        {
            "id": "data_quality",
            "label": "Data-quality gate",
            "formula": ("per active feature, evaluated in order: no live epics → FAIL; epics but no stories → FAIL; "
                        "all stories done → PASS; no open unpointed story → PASS; an open Blocker/Critical story without "
                        "an estimate → WARN; ≥50% of open stories pointed → PASS; ≤2 low-priority unpointed left on a "
                        "started feature → PASS; otherwise WARN; finally, any PASS that still has an epic with no stories → WARN"),
            "inputs": {"important_priorities": sorted(IMPORTANT_PRIORITIES),
                       "small_unpointed_remainder": SMALL_UNPOINTED_REMAINDER,
                       "completion_aware": "done stories are excluded from the pointed ratio"},
            "result": f"{meta['dq_pass']} PASS · {meta['dq_warn']} WARN · {meta['dq_fail']} FAIL",
            "shown_as": f"{meta['dq_pass']} PASS · {meta['dq_warn']} WARN · {meta['dq_fail']} FAIL",
        },
        {
            "id": "composite",
            "label": "Composite risk",
            "formula": "count of signals in {timeline, capacity, assignment, bugs, data quality}; ≥3 HIGH, 2 MEDIUM, ≤1 LOW",
            "inputs": {"signals_counted": ["timeline", "capacity", "assignment", "bugs", "data_quality"]},
            "result": f"{meta['high_risk_count']} HIGH · {meta['medium_risk_count']} MEDIUM · {meta['low_risk_count']} LOW",
            "shown_as": f"{meta['high_risk_count']} HIGH · {meta['medium_risk_count']} MEDIUM · {meta['low_risk_count']} LOW",
        },
        {
            "id": "scope_filter",
            "label": "Excluded from scope",
            "formula": f"epics that are Dev Complete/Closed, or whose Target/Fix Version is set and is not {version or 'this release'}",
            "inputs": {"version": version},
            "result": f"{meta['excluded_epics_count']} epics, {meta['excluded_open_sp']} SP",
            "shown_as": f"{meta['excluded_epics_count']} epics ({meta['excluded_open_sp']} open SP) left out",
        },
    ]


# --- Main ---


def main():
    parser = argparse.ArgumentParser(description="Run planning risk checks")
    parser.add_argument("--features", required=True)
    parser.add_argument("--epics", required=True)
    parser.add_argument("--stories", required=True)
    parser.add_argument("--bugs", required=True)
    parser.add_argument("--roster", required=True)
    parser.add_argument("--remaining-sprints", type=int, required=True)
    parser.add_argument("--component-filter", default="none")
    parser.add_argument("--version", default=None, help="Release version (e.g. 5.1) for scope filtering")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD; enables recent-activity evidence")
    parser.add_argument("--first-sprint", type=int, default=None)
    parser.add_argument("--pencils-down", type=int, default=None)
    parser.add_argument("--activity-days", type=int, default=DEFAULT_ACTIVITY_DAYS)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    features_data = load_json(args.features)
    epics_data = load_json(args.epics)
    stories_data = load_json(args.stories)
    bugs_data = load_json(args.bugs)
    roster = load_json(args.roster)

    if args.remaining_sprints == 0:
        print("WARNING: 0 sprints remaining — running post-branch-cut.", file=sys.stderr)

    window = (args.first_sprint, args.pencils_down) if args.first_sprint and args.pencils_down else None

    features, unlinked_bugs = build_hierarchy(
        features_data, epics_data, stories_data, bugs_data, args.component_filter, version=args.version
    )
    active, dormant = split_features(features, args.today, args.activity_days, window)
    active_keys = {f["key"] for f in active}

    all_epic_stories = []
    for f in features:
        all_epic_stories.extend(f["all_stories"])

    # Risk checks run on active features only; dormant features are a scope decision, not a risk.
    gate = run_data_quality_gate(active)
    capacity = run_capacity_check(active, gate, roster, args.remaining_sprints)
    timeline = run_timeline_check(active, gate, roster, args.remaining_sprints)
    assignment = run_assignment_check(active)
    bug_load = run_bug_load_check(unlinked_bugs, all_epic_stories, args.component_filter)
    sizing = run_sizing_check(active, timeline, roster)
    composite = run_composite_check(active, gate, capacity, timeline, assignment, sizing)
    hidden = estimate_hidden_scope(features, active_keys)

    unknown_contributors = [c["person"] for c in capacity if not c["in_roster"]]

    stories_skipped = stories_data.get("skipped_issues", [])
    bugs_skipped = bugs_data.get("skipped_issues", [])

    high_count = sum(1 for c in composite if c["composite_risk"] == "HIGH")
    med_count = sum(1 for c in composite if c["composite_risk"] == "MEDIUM")
    low_count = sum(1 for c in composite if c["composite_risk"] == "LOW")
    assessed = sum(1 for g in gate if g["status"] in ("PASS", "WARN"))
    unassessable = sum(1 for g in gate if g["status"] == "FAIL")
    dq_pass = sum(1 for g in gate if g["status"] == "PASS")
    dq_warn = sum(1 for g in gate if g["status"] == "WARN")
    dq_fail = sum(1 for g in gate if g["status"] == "FAIL")
    total_remaining = sum(t["remaining_sp"] for t in timeline if t["risk"] != "N/A")
    total_capacity = sum(c["remaining_capacity"] for c in capacity if c["in_roster"])
    gap = total_capacity - total_remaining
    excluded = [e for f in features for e in f.get("excluded_epics", [])]

    overall = "HIGH" if high_count > 0 else ("MEDIUM" if med_count > 0 else "LOW")

    meta = {
        "version": args.version,
        "today": args.today,
        "total_features": len(features),
        "active_features": len(active),
        "dormant_features": len(dormant),
        "assessed_features": assessed,
        "unassessable_features": unassessable,
        "high_risk_count": high_count,
        "medium_risk_count": med_count,
        "low_risk_count": low_count,
        "overall_risk": overall,
        "total_remaining_sp": total_remaining,
        "total_capacity_sp": total_capacity,
        "gap_sp": gap,
        "hidden_scope_low": hidden["estimate_low"],
        "hidden_scope_typical": hidden["estimate_typical"],
        "hidden_scope_high": hidden["estimate_high"],
        "gap_with_hidden_low": gap - hidden["estimate_high"],
        "gap_with_hidden_high": gap - hidden["estimate_low"],
        "total_unpointed_stories": hidden["unpointed_open"],
        "total_unpointed_assigned": hidden["unpointed_assigned"],
        "overloaded_people_count": sum(1 for c in capacity if c["status"] == "OVER"),
        "non_roster_people_count": len(unknown_contributors),
        "spof_features_count": len(assignment["spof"]),
        "timeline_high_count": sum(1 for t in timeline if t["risk"] in ("HIGH", "NO_CONTRIBUTORS")),
        "undersized_count": sum(1 for s in sizing if s["assessment"] == "Undersized"),
        "unassigned_blocker_bugs": len(bug_load["unassigned_blocker_critical"]),
        "data_quality_failures": unassessable,
        "dq_pass": dq_pass,
        "dq_warn": dq_warn,
        "dq_fail": dq_fail,
        "excluded_epics_count": len(excluded),
        "excluded_open_sp": sum(e["open_sp"] for e in excluded),
        "component_filter": args.component_filter,
        "remaining_sprints": args.remaining_sprints,
        "skipped_stories": len(stories_skipped),
        "skipped_bugs": len(bugs_skipped),
    }

    output = {
        "meta": meta,
        "method": build_method(meta, roster, args.remaining_sprints, hidden, args.activity_days, window, args.version),
        "data_quality": gate,
        "capacity": capacity,
        "timeline": timeline,
        "assignment": assignment,
        "bug_load": bug_load,
        "sizing": sizing,
        "composite": composite,
        "cut_line": build_cut_line(timeline, total_capacity, {t["feature_key"]: t["risk"] for t in timeline}),
        "hidden_scope": hidden,
        "scope_confirmation": [
            {"feature_key": f["key"], "summary": f["summary"], "sme": f.get("sme", "None"),
             "status": f["status"], "epic_count": len(f["epics"]),
             "story_count": sum(1 for s in f["all_stories"] if s["type"] != "Bug"),
             "reason": f["classification_reason"]} for f in dormant
        ],
        "feature_names": {f["key"]: f["summary"] for f in features},
        "process_gaps": build_process_gaps(features, active, dormant, capacity, sizing, hidden),
        "unknown_contributors": unknown_contributors,
        "skipped_issues": stories_skipped + bugs_skipped,
    }

    write_output(output, args.output)


if __name__ == "__main__":
    main()
