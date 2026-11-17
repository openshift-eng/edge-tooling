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
from _jira_transforms import (
    load_json,
    write_output,
    parse_date,
    extract_sprint_number,
    build_roster_index,
    roster_match,
)

DONE_STATUSES_STORIES = {"Closed"}
DONE_STATUSES_OCPBUGS = {"Closed", "Verified"}
DONE_STATUSES_EPICS = {"Closed", "Dev Complete", "Testing", "Release Pending"}
ACTIVE_STORY_STATUSES = {"In Progress", "Review"}
NEW_FEATURE_STATUSES = {"New"}

# Priority focus: only these feature priorities are assessed by default; lower
# priorities are assessed only when their status is in the include set (the team
# works priority-first, so not-yet-started lower-priority work is by design, not risk).
FOCUS_PRIORITIES = {"Blocker", "Critical", "Major"}
LOWER_PRIORITY_INCLUDE_STATUSES = {"Refinement"}

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

    Epics that are already done (Dev Complete/Testing/Release Pending/Closed) or that target another
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
            # Bugs are pointed and counted like other work (edge-context decision 0003).
            open_sp = sum(s["sp"] for s in epic_stories if not is_story_done(s))
            open_count = sum(1 for s in epic_stories if not is_story_done(s))
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
            "priority": f.get("priority", "Undefined"),
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


_PLACEHOLDER_ASSIGNEE_DISPLAY = {"Unassigned", "None", "Field not configured"}


def canonicalize_assignees(features, roster_index):
    """Rewrite each story's ``assignee`` to its roster username when it matches.

    Live Jira returns users with ``email: null``, so a story's ``assignee`` may be a
    display name — or null while only ``assignee_display`` carries the name — that
    does not equal the roster username every capacity/timeline/sizing/SPOF lookup
    keys on. Resolve it once here, by email/username or display name
    (:func:`roster_match`, law 00), so each teammate's work groups under one
    canonical key. Work owned by a non-roster user keeps its id (or falls back to the
    display name when the id is null) so it still surfaces as roster drift rather than
    silently dropping out of the load math.
    """
    for f in features:
        for s in f.get("all_stories", []):
            member = roster_match(s.get("assignee"), s.get("assignee_display"), roster_index)
            if member:
                s["assignee"] = member["username"]
            elif not s.get("assignee"):
                disp = s.get("assignee_display")
                if disp and disp not in _PLACEHOLDER_ASSIGNEE_DISPLAY:
                    s["assignee"] = disp


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
    # Bugs are deliberately excluded here regardless of their points: bug activity
    # does not prove *feature delivery* is underway (edge-context decision 0003
    # points bugs, but that is a scope/capacity concern, not activity evidence).
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
    # Bugs under a New feature are not evidence that the feature is being delivered
    # (same rationale as feature_activity); decision 0003 points bugs but that is a
    # scope/capacity concern, not a dormant-vs-active signal.
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


def partition_by_priority(features, focus=FOCUS_PRIORITIES, include_statuses=LOWER_PRIORITY_INCLUDE_STATUSES):
    """Split features into (in_focus, deprioritized) by feature priority.

    A feature is in focus if its priority is in ``focus`` or its status is in
    ``include_statuses`` (e.g. Refinement). Everything else — including features
    with an Undefined priority — is deprioritized: listed in the appendix and
    excluded from every figure. The team works priority-first, so not-yet-started
    lower-priority work is a plan, not a risk.
    """
    in_focus, deprioritized = [], []
    for f in features:
        if f.get("priority", "Undefined") in focus or f.get("status", "") in include_statuses:
            in_focus.append(f)
        else:
            deprioritized.append(f)
    return in_focus, deprioritized


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
        # Bugs count toward the breakdown like other work (edge-context decision 0003).
        breakdown = list(f["all_stories"])
        total = len(breakdown)
        done_count = sum(1 for s in breakdown if is_story_done(s))
        open_stories = [s for s in breakdown if not is_story_done(s)]
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
            status, reason = "FAIL", "epics have no issues"
        elif open_count == 0:
            status, reason = "PASS", f"all {total} issues complete"
        elif not unpointed_open:
            status, reason = "PASS", ""
        elif critical_unpointed:
            n = len(critical_unpointed)
            verb = "issue lacks" if n == 1 else "issues lack"
            status, reason = "WARN", f"{n} blocker/critical open {verb} an estimate"
        elif pointed_pct >= 50:
            status, reason = "PASS", ""
        elif len(unpointed_open) <= SMALL_UNPOINTED_REMAINDER and done_count >= 1:
            status, reason = "PASS", (
                f"nearly done ({completion_pct}% complete), "
                f"only {len(unpointed_open)} low-priority open {'issue' if len(unpointed_open)==1 else 'issues'} unpointed")
        else:
            status, reason = "WARN", f"{len(unpointed_open)} of {open_count} open issues unestimated"

        # An otherwise-clean feature that still has an epic with no stories is only
        # partially refined — surface it as a WARN rather than a silent PASS. Bugs
        # are excluded here deliberately: an epic covered only by bugs is not
        # feature-refined (same rationale as the dormant check), independent of
        # decision 0003 counting those bugs' points toward scope/capacity.
        empty_epics = [e for e in f["epics"]
                       if not any(s["type"] != "Bug" for s in e.get("stories", []))]
        if status == "PASS" and len(empty_epics) >= 1:
            status = "WARN"
            reason = f"{len(empty_epics)} of {len(f['epics'])} epics have no non-bug stories"

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


def run_capacity_check(features, gate_results, roster, remaining_sprints, effective_sprints=None):
    """Per-person load vs the soft guideline.

    The ~sp_target SP/person/sprint figure is a soft planning guideline (edge-context
    decision 0015), not a cap: feature-level capacity risk comes from the velocity-based
    timeline check, not from one person being individually heavy. So a person is flagged
    ``ABOVE_GUIDELINE`` only when their assigned open SP is **far** above the guideline
    (> 1.5 × sp_target × effective sprints), matching sprint-health; a normal overrun of
    a point or two is left as OK.

    Every roster member appears (even with no work) so the report can show the whole
    team. Non-roster assignees appear with guideline 0 and ``in_roster: false`` — their
    work counts as scope, and the report asks for the roster to be reconciled. Only
    stories under PASS/WARN features count. ``effective_sprints`` is the
    availability-adjusted sprints left (Σ availability); it defaults to
    ``remaining_sprints`` so the guideline matches the capacity/timeline cut-line.
    """
    if effective_sprints is None:
        effective_sprints = remaining_sprints
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
            # Bugs load a person like any other pointed work (edge-context decision 0003).
            if is_story_done(s) or not s["assignee"]:
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
        # Soft guideline load for the release; flag only when far (>1.5×) above it.
        guideline = sp_target * effective_sprints if member else 0
        overrun = max(0, assigned_sp - guideline) if member else assigned_sp
        if not member:
            status = "NOT_IN_ROSTER"
        elif assigned_sp > 1.5 * guideline:
            status = "ABOVE_GUIDELINE"
        else:
            status = "OK"
        results.append({
            "person": person,
            "display_name": person_display.get(person, person),
            "assigned_sp": assigned_sp,
            "unpointed_assigned": unpointed_by_person.get(person, 0),
            "sp_target": sp_target,
            "remaining_capacity": round(guideline, 1),
            "overrun": round(overrun, 1),
            # ABOVE_GUIDELINE replaces the old OVER status; readers accept both.
            "status": status,
            "features": sorted(person_features.get(person, set())),
            "in_roster": member is not None,
        })

    results.sort(key=lambda r: (-r["assigned_sp"], r["display_name"]))
    return results


def team_capacity(team_velocity, roster, remaining_sprints, availability=None):
    """Total team capacity (SP) to pencils down, and the basis used.

    Preferred basis is the team's measured velocity (edge-context decision 0015):
    average completed SP over the last 3 closed sprints, projected across the
    sprints that remain. When no velocity is available we fall back to the roster
    sum (Σ sp_target × sprints left) and flag it so the report can say so.

    ``availability`` is an optional list of per-remaining-sprint availability
    fractions (0–1, law 09: velocity "adjusted for PTO and holidays"). Capacity
    is the per-sprint rate times the sum of those fractions, so a sprint where the
    team is only 80% available contributes 0.8 sprints of capacity. When omitted,
    every remaining sprint counts as fully available (1.0).

    Returns ``(capacity_sp, basis)`` where basis is ``"velocity"`` or
    ``"roster_fallback"``.
    """
    avail = availability if availability else [1.0] * remaining_sprints
    effective_sprints = sum(avail)
    if team_velocity is not None:
        return round(team_velocity * effective_sprints), "velocity"
    targets = [m.get("sp_target", DEFAULT_SP_TARGET) for m in roster.get("members", [])]
    return round(sum(targets) * effective_sprints), "roster_fallback"


# --- Check 2: Timeline ---


def velocity_scaling_factor(team_velocity, roster):
    """Scale per-person sp_target throughput to match measured team velocity.

    The timeline check projects each person delivering their ``sp_target`` per
    sprint. When the team's measured velocity is known it is usually below the
    roster-target sum (decision 0015), so without scaling the per-feature
    timelines would assume a faster pace than the capacity figure. Return
    ``team_velocity / Σ sp_target`` so timeline and capacity use the same pace;
    return ``1.0`` when velocity is unavailable or the roster has no targets.
    """
    if team_velocity is None:
        return 1.0
    target_sum = sum(m.get("sp_target", DEFAULT_SP_TARGET) for m in roster.get("members", []))
    if target_sum <= 0:
        return 1.0
    return team_velocity / target_sum


def run_timeline_check(features, gate_results, roster, remaining_sprints,
                       velocity_factor=1.0, effective_sprints=None):
    """Per-feature timeline projection with proportional velocity.

    ``velocity_factor`` scales each person's ``sp_target`` throughput so the
    timeline pace matches the measured team velocity (see
    :func:`velocity_scaling_factor`); it defaults to 1.0 (roster-target pace).

    ``effective_sprints`` is the availability-adjusted number of sprints left
    (Σ availability) to compare each feature against, so timeline risk agrees with
    the capacity cut-line, which also uses availability. Defaults to
    ``remaining_sprints`` (no PTO adjustment).
    """
    if effective_sprints is None:
        effective_sprints = remaining_sprints
    passed_keys = {g["feature_key"] for g in gate_results if g["status"] in ("PASS", "WARN")}
    roster_map = {m["username"]: m for m in roster.get("members", [])}

    global_sp = {}
    for f in features:
        for s in f["all_stories"]:
            # Bugs count toward remaining scope like other work (edge-context decision 0003).
            if is_story_done(s) or s["sp"] <= 0 or not s["assignee"]:
                continue
            global_sp[s["assignee"]] = global_sp.get(s["assignee"], 0) + s["sp"]

    results = []
    for f in features:
        base = {"feature_key": f["key"], "summary": f["summary"], "sme": f.get("sme", "None"),
                "rank": f.get("rank")}
        if f["key"] not in passed_keys:
            results.append({**base, "remaining_sp": 0, "unpointed_open": 0, "velocity_per_sprint": 0,
                            "sprints_needed": 0, "sprints_left": round(effective_sprints, 1),
                            "gap": 0, "risk": "N/A"})
            continue

        remaining_sp = 0
        unpointed_open = 0
        feature_sp_by_person = {}
        for s in f["all_stories"]:
            if is_story_done(s):
                continue
            remaining_sp += s["sp"]
            if s["sp"] <= 0:
                unpointed_open += 1
            if s["sp"] <= 0 or not s["assignee"]:
                continue
            feature_sp_by_person[s["assignee"]] = feature_sp_by_person.get(s["assignee"], 0) + s["sp"]

        velocity = 0.0
        for person, person_feature_sp in feature_sp_by_person.items():
            # A roster member missing the sp_target field falls back to the single
            # DEFAULT_SP_TARGET guideline (decision 0015); non-roster people
            # contribute no velocity (0), matching capacity — resolved at the
            # roster-drift prompt. (Must distinguish "in roster, no field" from
            # "not in roster" so dropping sp_target does not zero every member.)
            sp_target = (roster_map[person].get("sp_target", DEFAULT_SP_TARGET)
                         if person in roster_map else 0)
            total_sp = global_sp.get(person, person_feature_sp)
            fraction = person_feature_sp / total_sp if total_sp > 0 else 0
            # Scale sp_target pace to measured team velocity so the timeline
            # projection and the capacity figure use the same pace (I.4).
            velocity += sp_target * velocity_factor * fraction

        if velocity <= 0:
            sprints_needed = float("inf") if remaining_sp > 0 else 0
        else:
            sprints_needed = remaining_sp / velocity

        # Compare against the availability-adjusted sprints left so timeline risk
        # agrees with the capacity cut-line (both account for PTO/holidays).
        gap = max(0, sprints_needed - effective_sprints) if sprints_needed != float("inf") else effective_sprints
        risk = "NO_CONTRIBUTORS" if velocity <= 0 and remaining_sp > 0 else (
            "HIGH" if gap > 0 else "OK"
        )

        results.append({
            **base,
            "remaining_sp": remaining_sp,
            "unpointed_open": unpointed_open,
            "velocity_per_sprint": round(velocity, 1),
            "sprints_needed": round(sprints_needed, 1) if sprints_needed != float("inf") else "inf",
            "sprints_left": round(effective_sprints, 1),
            "gap": round(gap, 1),
            "risk": risk,
        })

    return results


# --- Check 3: Assignment ---


def run_assignment_check(features):
    """SPOF detection and unassigned work. Bugs count like other work
    (edge-context decision 0003)."""
    spof = []
    unassigned = []

    for f in features:
        non_done = [s for s in f["all_stories"] if not is_story_done(s)]
        contributors = set(s["assignee"] for s in non_done if s["assignee"])
        unassigned_stories = [s for s in non_done if not s["assignee"]]

        if len(contributors) == 1:
            sole = list(contributors)[0]
            display = next(
                (s.get("assignee_display", sole) for s in non_done if s["assignee"] == sole),
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
    # No roster at all → assume the default target for everyone. Roster present:
    # a member (even one missing the sp_target field) uses DEFAULT_SP_TARGET; a
    # non-roster contributor counts zero — consistent with capacity and timeline.
    missing_target = DEFAULT_SP_TARGET if roster is None else 0

    results = []
    for f in features:
        size = f["size"]
        # Bugs count toward a feature's size like other work (edge-context decision 0003).
        open_sp = sum(s["sp"] for s in f["all_stories"] if not is_story_done(s))
        total_sp = sum(s["sp"] for s in f["all_stories"])
        epic_count = len(f["epics"])
        contributors = set(
            s["assignee"] for s in f["all_stories"]
            if not is_story_done(s) and s["assignee"]
        )
        dedicated_velocity = sum(
            (roster_map[p].get("sp_target", DEFAULT_SP_TARGET) if p in roster_map
             else missing_target)
            for p in contributors
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

    Sizing and per-person capacity are displayed but not counted: both are derived
    from the same load numbers as the timeline signal and would double-count one
    heavy contributor. Feature-level capacity risk is the velocity-based timeline
    check (edge-context decision 0015), so an individual being above the soft
    guideline is surfaced for context but does not add a signal.
    """
    gate_map = {g["feature_key"]: g["status"] for g in gate_results}
    overloaded_people = {c["person"] for c in capacity_results
                         if c["status"] in ("ABOVE_GUIDELINE", "OVER")}
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

        # Per-person capacity load is informational only — it does NOT add a composite
        # signal. A contributor being above the soft guideline is shown for context, but
        # feature-level capacity risk is carried by the velocity-based timeline check
        # (edge-context decision 0015), so it is not double-counted here. Bugs are
        # story-pointed and count toward load too (decision 0003).
        feature_contributors = set(
            s["assignee"] for s in f["all_stories"]
            if not is_story_done(s) and s["assignee"]
        )
        if feature_contributors & overloaded_people:
            signal_details["capacity"] = "ABOVE_GUIDELINE"
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


def count_unpointed(features, active_keys):
    """Unpointed open items under active features: stories/tasks/spikes and bugs, counted.

    No SP estimate — this is what the chai-bot report uses (edge-context decision
    0018: never estimate). :func:`estimate_hidden_scope` builds on it.
    """
    active_open_unpointed = [
        s for f in features if f["key"] in active_keys for s in f["all_stories"]
        if not is_story_done(s) and s["sp"] <= 0
    ]
    unpointed = [s for s in active_open_unpointed if s["type"] != "Bug"]
    unpointed_bugs = [s for s in active_open_unpointed if s["type"] == "Bug"]
    return {
        "unpointed_open": len(unpointed),
        "unpointed_assigned": sum(1 for s in unpointed if s["assignee"]),
        "unpointed_bugs": len(unpointed_bugs),
        "unpointed_bug_keys": [s["key"] for s in unpointed_bugs],
    }


def unestimated_hidden_scope(features, active_keys):
    """:func:`count_unpointed` in the hidden-scope shape, with every estimate None."""
    return {**count_unpointed(features, active_keys),
            "sp_per_story_low": None, "sp_per_story_typical": None, "sp_per_story_high": None,
            "estimate_low": None, "estimate_typical": None, "estimate_high": None,
            "basis": "not estimated (--no-hidden-scope-estimate)"}


def estimate_hidden_scope(features, active_keys):
    """Estimate SP for unpointed open stories from closed pointed stories' distribution.

    Bugs are deliberately **excluded** from the hidden-scope estimate. Although
    bugs are story-pointed (edge-context decision 0003), in practice none carry
    points yet, so folding every unpointed bug into hidden scope would multiply
    the whole bug backlog by a story-sized estimate and badly inflate the figure.
    Unpointed open bugs are reported separately (``unpointed_bugs`` /
    ``unpointed_bug_keys``) so the gap is still visible without distorting scope.
    """
    closed_pointed = [
        s["sp"] for f in features for s in f["all_stories"]
        if is_story_done(s) and s["sp"] > 0
    ]
    counts = count_unpointed(features, active_keys)
    n = counts["unpointed_open"]
    if len(closed_pointed) >= 4:
        srt = sorted(closed_pointed)
        q1, q2, q3 = statistics.quantiles(srt, n=4, method="inclusive")
        low, typical, high = round(q1, 1), round(q2, 1), round(q3, 1)
        basis = f"quartiles of {len(srt)} closed pointed stories"
    else:
        low, typical, high = FALLBACK_SP_RANGE
        basis = "fallback range (fewer than 4 closed pointed stories)"
    return {
        **counts,
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
    estimated = hidden.get("estimate_low") is not None
    members = roster.get("members", [])
    targets = [m.get("sp_target", DEFAULT_SP_TARGET) for m in members]
    avg_target = round(sum(targets) / len(targets), 1) if targets else DEFAULT_SP_TARGET
    win = f"S{window[0]}–S{window[1]}" if window else "n/a"

    # law 09: capacity is velocity "adjusted for PTO and holidays". availability is
    # a per-remaining-sprint fraction (default 1.0); effective_sprints is their sum.
    availability = meta.get("availability") or [1.0] * remaining_sprints
    effective_sprints = round(sum(availability), 2)
    pto_adjusted = any(a < 1.0 for a in availability)
    pto_note = (f" (availability-adjusted: {availability} → {effective_sprints} effective sprints)"
                if pto_adjusted else " (not adjusted for PTO)")
    sprints_shown = effective_sprints if pto_adjusted else remaining_sprints

    if meta.get("capacity_basis") == "velocity":
        vel = meta["team_velocity"]
        sprint_count = len(meta.get("velocity_sprints") or [])
        capacity_entry = {
            "id": "capacity",
            "label": "Capacity",
            "formula": "team velocity × availability-adjusted sprints left (to pencils down); velocity = avg completed SP over the last 3 closed sprints (edge-context decision 0015); sprints left adjusted for PTO/holidays (law 09)",
            "inputs": {"team_velocity": vel, "velocity_sprints": sprint_count, "sprints_left": remaining_sprints,
                       "availability": availability, "effective_sprints": effective_sprints},
            "result": f"{meta['total_capacity_sp']} SP",
            "shown_as": f"{vel} SP/sprint (velocity over {sprint_count} sprints) × {sprints_shown} sprints = {meta['total_capacity_sp']} SP{pto_note}",
        }
    else:
        capacity_entry = {
            "id": "capacity",
            "label": "Capacity",
            "formula": "FALLBACK (no team velocity available): Σ over roster members of sp_target × availability-adjusted sprints left (to pencils down)",
            "inputs": {"roster_members": len(members), "avg_sp_target": avg_target, "sprints_left": remaining_sprints,
                       "availability": availability, "effective_sprints": effective_sprints},
            "result": f"{meta['total_capacity_sp']} SP",
            "shown_as": ((f"{len(members)} people × {avg_target} SP × {sprints_shown} sprints = {meta['total_capacity_sp']} SP"
                          if len(set(targets)) <= 1 else
                          f"Σ targets {sum(targets)} SP/sprint × {sprints_shown} sprints = {meta['total_capacity_sp']} SP ({len(members)} people, targets {min(targets)}–{max(targets)})")
                         + " — roster fallback; team velocity was unavailable" + pto_note),
        }
    rows = [
        capacity_entry,
        {
            "id": "scope",
            "label": "Scope (pointed)",
            "formula": "Σ story points of open issues (stories and bugs) under active features that pass the data-quality gate",
            "inputs": {"active_features": meta["active_features"], "assessed_features": meta["assessed_features"]},
            "result": f"{meta['total_remaining_sp']} SP",
            "shown_as": f"{meta['assessed_features']} assessed features → {meta['total_remaining_sp']} SP open",
        },
        {
            "id": "hidden_scope",
            "label": "Hidden scope",
            "formula": "unpointed open stories × SP per story (low / typical / high from closed pointed stories); bugs are excluded and reported separately as unpointed_bugs",
            "inputs": {"unpointed_open": hidden["unpointed_open"], "sp_low": hidden["sp_per_story_low"],
                       "sp_typical": hidden["sp_per_story_typical"], "sp_high": hidden["sp_per_story_high"],
                       "unpointed_bugs": hidden["unpointed_bugs"], "basis": hidden["basis"]},
            "result": (f"{hidden['estimate_low']}–{hidden['estimate_high']} SP" if estimated
                       else "not estimated"),
            "shown_as": (f"{hidden['unpointed_open']} × {hidden['sp_per_story_low']}–{hidden['sp_per_story_high']} SP = {hidden['estimate_low']}–{hidden['estimate_high']} SP"
                         if estimated else f"{hidden['unpointed_open']} unpointed open stories (not estimated)")
                        + (f" ({hidden['unpointed_bugs']} unpointed bugs excluded)" if hidden["unpointed_bugs"] else ""),
        },
        {
            "id": "gap",
            "label": "Gap",
            "formula": "capacity − scope (negative = overcommitted); with hidden scope: capacity − scope − estimate",
            "inputs": {"capacity": meta["total_capacity_sp"], "scope": meta["total_remaining_sp"],
                       "hidden_low": hidden["estimate_low"], "hidden_high": hidden["estimate_high"]},
            "result": f"{meta['gap_sp']} SP",
            "shown_as": f"{meta['total_capacity_sp']} − {meta['total_remaining_sp']} = {meta['gap_sp']} SP"
                        + (f" ({meta['gap_with_hidden_low']} to {meta['gap_with_hidden_high']} incl. hidden)"
                           if estimated else ""),
        },
        {
            "id": "priority_focus",
            "label": "Priority focus",
            "formula": f"features with priority in {meta['focus_priorities']} are assessed; lower priorities only if status in {meta['include_lower_when']}; the rest are listed in the appendix and excluded from every figure",
            "inputs": {"focus_priorities": meta["focus_priorities"], "include_lower_when": meta["include_lower_when"]},
            "result": f"{meta['focus_features']} assessed · {meta['deprioritized_features']} lower-priority not assessed",
            "shown_as": f"{meta['focus_features']} assessed · {meta['deprioritized_features']} lower-priority not assessed",
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
            "label": "Person far above the soft guideline",
            "formula": "flagged when assigned open SP > 1.5 × that person's sp_target × availability-adjusted sprints left; each person's own sp_target is the soft guideline (edge-context decision 0015), not a limit, so only load *far* above it is surfaced; informational — it adds no composite risk signal; unpointed assigned stories shown separately, never summed",
            "inputs": {"sprints_left": remaining_sprints, "effective_sprints": effective_sprints,
                       "threshold_multiple": 1.5},
            "result": f"{meta['overloaded_people_count']} people far above guideline",
            "shown_as": f"{meta['overloaded_people_count']} far above the soft guideline (>1.5× their sp_target), {meta['non_roster_people_count']} not on roster",
        },
        {
            "id": "timeline",
            "label": "Feature timeline",
            "formula": "sprints needed = remaining SP ÷ Σ roster contributors (sp_target × velocity_factor × share of that person's total open SP on this feature); velocity_factor = team velocity ÷ Σ roster sp_target scales the pace to measured velocity (1.0 when velocity unavailable, edge-context decision 0015); non-roster contributors add no velocity; HIGH if > availability-adjusted sprints left (same adjustment as capacity, law 09)",
            "inputs": {"sprints_left": remaining_sprints, "effective_sprints": effective_sprints,
                       "velocity_factor": meta.get("timeline_velocity_factor", 1.0)},
            "result": f"{meta['timeline_high_count']} features HIGH",
            "shown_as": f"velocity_factor {meta.get('timeline_velocity_factor', 1.0)} — see appendix timeline table for per-feature values",
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
            "formula": ("per active feature, evaluated in order: no live epics → FAIL; epics but no issues → FAIL; "
                        "all issues done → PASS; no open unpointed issue → PASS; an open Blocker/Critical issue without "
                        "an estimate → WARN; ≥50% of open issues pointed → PASS; ≤2 low-priority unpointed left on a "
                        "started feature → PASS; otherwise WARN; finally, any PASS that still has an epic with no non-bug stories → WARN"),
            "inputs": {"important_priorities": sorted(IMPORTANT_PRIORITIES),
                       "small_unpointed_remainder": SMALL_UNPOINTED_REMAINDER,
                       "completion_aware": "done issues are excluded from the pointed ratio"},
            "result": f"{meta['dq_pass']} PASS · {meta['dq_warn']} WARN · {meta['dq_fail']} FAIL",
            "shown_as": f"{meta['dq_pass']} PASS · {meta['dq_warn']} WARN · {meta['dq_fail']} FAIL",
        },
        {
            "id": "composite",
            "label": "Composite risk",
            "formula": "count of signals in {timeline, assignment, bugs, data quality}; ≥3 HIGH, 2 MEDIUM, ≤1 LOW. Per-person capacity load is shown for context but not counted (feature-level capacity risk is the velocity-based timeline check)",
            "inputs": {"signals_counted": ["timeline", "assignment", "bugs", "data_quality"]},
            "result": f"{meta['high_risk_count']} HIGH · {meta['medium_risk_count']} MEDIUM · {meta['low_risk_count']} LOW",
            "shown_as": f"{meta['high_risk_count']} HIGH · {meta['medium_risk_count']} MEDIUM · {meta['low_risk_count']} LOW",
        },
        {
            "id": "scope_filter",
            "label": "Excluded from scope",
            "formula": f"epics that are Dev Complete/Testing/Release Pending/Closed, or whose Target/Fix Version is set and is not {version or 'this release'}",
            "inputs": {"version": version},
            "result": f"{meta['excluded_epics_count']} epics, {meta['excluded_open_sp']} SP",
            "shown_as": f"{meta['excluded_epics_count']} epics ({meta['excluded_open_sp']} open SP) left out",
        },
    ]
    # Not estimated (--no-hidden-scope-estimate): leave the hidden-scope row out.
    return [row for row in rows if estimated or row["id"] != "hidden_scope"]


# --- Main ---


def load_velocity(path):
    """Load velocity.json, tolerating a missing path.

    Returns ``(team_velocity, sprints)``. When ``path`` is None or does not exist
    we warn and fall back to ``(None, [])`` so capacity uses the roster sum — a
    missing velocity file must not crash a planning run.

    A measured velocity of 0 (or negative) is treated as *no usable signal* and
    coerced to ``None`` so capacity and the timeline fall back to the roster sum.
    A real zero only happens when nothing matched the roster (e.g. live Jira
    returns display names with null emails, so no assignee/QA Contact resolves to
    a roster username) — reporting capacity 0 would falsely flag everything as a
    capacity risk, so we fall back and warn instead.
    """
    if not path:
        return None, []
    if not os.path.exists(path):
        print(f"WARNING: velocity file not found ({path}); using roster fallback.", file=sys.stderr)
        return None, []
    data = load_json(path)
    team_velocity = data.get("team_velocity")
    sprints = data.get("sprints", [])
    # velocity.json normally carries a number, but tolerate a stringified value
    # (e.g. "12.5") by coercing it; a non-numeric string is ignored with a warning
    # rather than crashing the planning run.
    if isinstance(team_velocity, str):
        try:
            team_velocity = float(team_velocity)
        except ValueError:
            print(f"WARNING: team_velocity in {path} is not numeric; using roster fallback.",
                  file=sys.stderr)
            team_velocity = None
    if team_velocity is not None and team_velocity <= 0:
        print("WARNING: measured team velocity is 0 (no issues matched the roster — "
              "often live Jira returning display names with null emails); using roster "
              "fallback instead of a degenerate zero capacity.", file=sys.stderr)
        team_velocity = None
    return team_velocity, sprints


def parse_availability(raw, remaining_sprints):
    """Normalize the --availability CSV to one fraction per remaining sprint.

    Each value is clamped to [0, 1]. An empty or non-numeric entry is treated as
    1.0 (fully available) with a warning, rather than dropped — dropping would
    silently shift every later sprint's availability onto the wrong sprint. A short
    list is padded with 1.0; a long list is truncated. ``None`` yields all-1.0.
    """
    if not raw:
        return [1.0] * remaining_sprints
    vals = []
    for index, part in enumerate(str(raw).split(",")):
        part = part.strip()
        if not part:
            print(f"WARNING: empty availability value at position {index + 1}; treating as 1.0 (fully available).", file=sys.stderr)
            vals.append(1.0)
            continue
        try:
            vals.append(max(0.0, min(1.0, float(part))))
        except ValueError:
            # Do not echo the raw token: --availability is caller-controlled and
            # may carry sensitive data. Report the position only.
            print(f"WARNING: non-numeric availability value at position {index + 1}; treating as 1.0 (fully available).", file=sys.stderr)
            vals.append(1.0)
    if len(vals) < remaining_sprints:
        vals += [1.0] * (remaining_sprints - len(vals))
    return vals[:remaining_sprints]


def main():
    parser = argparse.ArgumentParser(description="Run planning risk checks")
    parser.add_argument("--features", required=True)
    parser.add_argument("--epics", required=True)
    parser.add_argument("--stories", required=True)
    parser.add_argument("--bugs", required=True)
    parser.add_argument("--roster", required=True)
    parser.add_argument("--remaining-sprints", type=int, required=True)
    parser.add_argument("--velocity-file", default=None,
                        help="Optional velocity.json (team_velocity over last 3 closed sprints). "
                             "When absent or missing, capacity falls back to Σ sp_target × sprints left.")
    parser.add_argument("--availability", default=None,
                        help="CSV of per-remaining-sprint availability fractions (0–1) for the "
                             "PTO/holiday adjustment (law 09). Default: every sprint fully available.")
    parser.add_argument("--component-filter", default="none")
    parser.add_argument("--version", default=None, help="Release version (e.g. 5.1) for scope filtering")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD; enables recent-activity evidence")
    parser.add_argument("--first-sprint", type=int, default=None)
    parser.add_argument("--pencils-down", type=int, default=None)
    parser.add_argument("--activity-days", type=int, default=DEFAULT_ACTIVITY_DAYS)
    parser.add_argument("--focus-priorities", default=",".join(sorted(FOCUS_PRIORITIES)),
                        help="CSV of feature priorities to assess (default: Blocker,Critical,Major)")
    parser.add_argument("--include-lower-when", default=",".join(sorted(LOWER_PRIORITY_INCLUDE_STATUSES)),
                        help="CSV of statuses that pull a lower-priority feature into focus (default: Refinement)")
    parser.add_argument("--no-hidden-scope-estimate", action="store_true",
                        help="Count unpointed open items but do not estimate their SP (chai-bot report, "
                             "edge-context decision 0018: never estimate).")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    focus_priorities = {p.strip() for p in args.focus_priorities.split(",") if p.strip()}
    include_lower_when = {s.strip() for s in args.include_lower_when.split(",") if s.strip()}

    features_data = load_json(args.features)
    epics_data = load_json(args.epics)
    stories_data = load_json(args.stories)
    bugs_data = load_json(args.bugs)
    roster = load_json(args.roster)

    # Team velocity (edge-context decision 0015): average completed SP over the last
    # 3 closed sprints, used to project capacity. Absent/missing → roster-sum fallback.
    team_velocity, velocity_sprints = load_velocity(args.velocity_file)
    # Per-remaining-sprint availability for the PTO/holiday adjustment (law 09).
    availability = parse_availability(args.availability, args.remaining_sprints)

    if args.remaining_sprints == 0:
        print("WARNING: 0 sprints remaining — running post-branch-cut.", file=sys.stderr)

    window = (args.first_sprint, args.pencils_down) if args.first_sprint and args.pencils_down else None

    features, unlinked_bugs = build_hierarchy(
        features_data, epics_data, stories_data, bugs_data, args.component_filter, version=args.version
    )
    # Resolve each story's assignee to its roster username up front (law 00), so every
    # capacity/timeline/sizing/SPOF lookup groups a teammate's work under one key even
    # when live Jira returned a display name with a null email (edge-context decision 0015).
    roster_index = build_roster_index(roster)
    canonicalize_assignees(features, roster_index)
    # Priority focus: assess Blocker/Critical/Major (plus any in Refinement) and set
    # the rest aside — they go in the appendix and out of every figure.
    in_focus, deprioritized = partition_by_priority(features, focus_priorities, include_lower_when)
    active, dormant = split_features(in_focus, args.today, args.activity_days, window)
    active_keys = {f["key"] for f in active}

    # Bug load is assessed across every bug, regardless of feature priority focus.
    all_epic_stories = []
    for f in features:
        all_epic_stories.extend(f["all_stories"])

    # Risk checks run on active features only; dormant features are a scope decision, not a risk.
    gate = run_data_quality_gate(active)
    # Availability-adjusted sprints left, so the capacity guideline and timeline risk
    # both use the same PTO-adjusted cut-line (law 09).
    effective_sprints = sum(availability)
    capacity = run_capacity_check(active, gate, roster, args.remaining_sprints,
                                  effective_sprints=effective_sprints)
    # Scale the timeline's per-person sp_target pace to measured team velocity so
    # the per-feature timeline and the capacity figure use the same pace (I.4).
    timeline_velocity_factor = velocity_scaling_factor(team_velocity, roster)
    timeline = run_timeline_check(active, gate, roster, args.remaining_sprints,
                                  velocity_factor=timeline_velocity_factor,
                                  effective_sprints=effective_sprints)
    assignment = run_assignment_check(active)
    bug_load = run_bug_load_check(unlinked_bugs, all_epic_stories, args.component_filter)
    sizing = run_sizing_check(active, timeline, roster)
    composite = run_composite_check(active, gate, capacity, timeline, assignment, sizing)
    hidden = (unestimated_hidden_scope(in_focus, active_keys) if args.no_hidden_scope_estimate
              else estimate_hidden_scope(in_focus, active_keys))

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
    # Team capacity is planned against measured velocity (edge-context decision 0015);
    # per-person remaining_capacity stays a sp_target-based soft guideline.
    total_capacity, capacity_basis = team_capacity(
        team_velocity, roster, args.remaining_sprints, availability=availability)
    gap = total_capacity - total_remaining
    excluded = [e for f in in_focus for e in f.get("excluded_epics", [])]
    # Bugs count toward open scope like other work (edge-context decision 0003).
    deprioritized_open_sp = sum(
        s["sp"] for f in deprioritized for s in f["all_stories"]
        if not is_story_done(s)
    )

    overall = "HIGH" if high_count > 0 else ("MEDIUM" if med_count > 0 else "LOW")

    meta = {
        "version": args.version,
        "today": args.today,
        "total_features": len(features),
        "focus_features": len(in_focus),
        "deprioritized_features": len(deprioritized),
        "deprioritized_open_sp": deprioritized_open_sp,
        "focus_priorities": sorted(focus_priorities),
        "include_lower_when": sorted(include_lower_when),
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
        "capacity_basis": capacity_basis,
        "team_velocity": team_velocity,
        "velocity_sprints": velocity_sprints,
        "availability": availability,
        "gap_sp": gap,
        "hidden_scope_low": hidden["estimate_low"],
        "hidden_scope_typical": hidden["estimate_typical"],
        "hidden_scope_high": hidden["estimate_high"],
        "gap_with_hidden_low": gap - hidden["estimate_high"] if hidden["estimate_high"] is not None else None,
        "gap_with_hidden_high": gap - hidden["estimate_low"] if hidden["estimate_low"] is not None else None,
        "total_unpointed_stories": hidden["unpointed_open"],
        "total_unpointed_assigned": hidden["unpointed_assigned"],
        # Unpointed open bugs are reported separately, never folded into hidden scope.
        "unpointed_bugs": hidden["unpointed_bugs"],
        "unpointed_bug_keys": hidden["unpointed_bug_keys"],
        "overloaded_people_count": sum(1 for c in capacity if c["status"] in ("ABOVE_GUIDELINE", "OVER")),
        "non_roster_people_count": len(unknown_contributors),
        "spof_features_count": len(assignment["spof"]),
        "timeline_high_count": sum(1 for t in timeline if t["risk"] in ("HIGH", "NO_CONTRIBUTORS")),
        "timeline_velocity_factor": round(timeline_velocity_factor, 3),
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
             # story_count is a display count of stories (bugs shown via bug-load), not SP math.
             "story_count": sum(1 for s in f["all_stories"] if s["type"] != "Bug"),
             "reason": f["classification_reason"]} for f in dormant
        ],
        "deprioritized": [
            {"feature_key": f["key"], "summary": f["summary"], "priority": f.get("priority", "Undefined"),
             "status": f["status"], "sme": f.get("sme", "None"), "epic_count": len(f["epics"]),
             "open_sp": sum(s["sp"] for s in f["all_stories"] if not is_story_done(s))}
            for f in sorted(deprioritized, key=lambda x: x["rank"] if x.get("rank") is not None else float("inf"))
        ],
        "feature_names": {f["key"]: f["summary"] for f in features},
        "process_gaps": build_process_gaps(in_focus, active, dormant, capacity, sizing, hidden),
        "unknown_contributors": unknown_contributors,
        "skipped_issues": stories_skipped + bugs_skipped,
    }

    write_output(output, args.output)


if __name__ == "__main__":
    main()
