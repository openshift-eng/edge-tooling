#!/usr/bin/env python3
"""Team-level figures and Slack text for the chai-bot weekly health report.

Pure functions used by the chai-bot skills ``sprint-health``, ``release-health``
and ``weekly-health-report`` (edge-context decision 0018) inside
``sandbox_exec``. Inputs are the outputs of the existing edge-scrum scripts
(``scrum_context.py``, ``transform-*.py``, ``compute-velocity.py``,
``run-checks.py``); nothing here fetches data.

Output is **team-level only**: no per-person load, SP or PTO. A person's name
appears only as the owner of a specific action item (decision 0018).
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import (  # noqa: E402
    DONE_STATUSES_SPRINT, IN_PROGRESS_STATUSES, is_stale, normalize_name, sprint_day_counts)
from scrum_context import to_date, working_days  # noqa: E402

NOT_PTO_ADJUSTED = "not PTO-adjusted"
# Active-work statuses for the stale check, per law 07 as written: the transform's
# In Progress / Review, plus OCPBUGS ASSIGNED ("being actively worked, equivalent
# to In Progress"). Chai path only; the shared transform is unchanged.
ACTIVE_STATUSES = set(IN_PROGRESS_STATUSES) | {"ASSIGNED"}
REFINEMENT, RELEASE_HEALTH = "refinement", "release-health"
DEV_PHASES = ("development", "pencils-down sprint", "pre-branch-cut")
SKIP_PHASES = ("not started", "finished")
RED, YELLOW, GREEN = "🔴", "🟡", "🟢"


def _num(value):
    """A real number (not bool), else None."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ---------------------------------------------------------------- mode and capacity


def sprint_mode(week):
    """Sprint-health mode for a sprint week: week 1 → capacity, later → mid-sprint."""
    week = _num(week)
    if week is None:
        return None
    return "capacity" if week <= 1 else "mid-sprint"


def sprint_capacity(velocity, availability):
    """Current sprint capacity = velocity × availability (decision 0018).

    When availability is unknown (a "not available …" string or None), capacity
    is the velocity unadjusted and labelled "not PTO-adjusted".
    """
    velocity = _num(velocity)
    if velocity is None:
        return {"capacity_sp": None, "pto_adjusted": False,
                "label": "not available (velocity not measured)"}
    avail = _num(availability)
    if avail is None:
        return {"capacity_sp": round(velocity, 1), "pto_adjusted": False, "label": NOT_PTO_ADJUSTED}
    avail = max(0.0, min(1.0, avail))
    return {"capacity_sp": round(velocity * avail, 1), "pto_adjusted": True,
            "label": f"velocity {velocity:g} × availability {avail:.2f}"}


def remaining_share(sprint, today):
    """Share of a sprint's working days (Mon–Fri, start day to the day before the
    end day) still ahead on ``today``, today included: 1 for a sprint that has not
    started, 0 for one that has ended."""
    start, end, day = to_date(sprint.get("start")), to_date(sprint.get("end")), to_date(today)
    if day is None or start is None or end is None or day < start:
        return 1.0
    if day >= end:
        return 0.0
    total = working_days(start, end)
    return len(working_days(day, end)) / len(total) if total else 0.0


def release_capacity(velocity, sprints, today=None):
    """Capacity over the remaining sprints = velocity × Σ (availability × share left).

    ``sprints`` are scrum-context ``sprint_availability`` entries (current sprint
    through the branch-cut sprint). The current sprint counts only for its
    remaining working days (velocity × availability × remaining working days ÷
    sprint working days); upcoming sprints count in full. A sprint with unknown
    availability counts as 1.0 and is listed in ``not_adjusted``.
    ``availability_csv`` is the ``--availability`` value for ``run-checks.py``.
    """
    values, not_adjusted, current = [], [], None
    for s in sprints or []:
        avail = _num(s.get("availability"))
        if avail is None:
            not_adjusted.append(s.get("number"))
            avail = 1.0
        share = remaining_share(s, today) if today else 1.0
        if share < 1.0 and current is None:
            start, end = to_date(s.get("start")), to_date(s.get("end"))
            current = {"number": s.get("number"), "share": round(share, 4),
                       "days_left": len(working_days(max(to_date(today), start), end)) if end > to_date(today) else 0,
                       "days": len(working_days(start, end))}
        values.append(round(max(0.0, min(1.0, avail)) * share, 4))
    velocity = _num(velocity)
    capacity = round(velocity * sum(values)) if velocity is not None else None
    if not values:
        label = "no sprints remaining"
    elif not not_adjusted:
        label = f"PTO-adjusted ({sum(values):.2f} effective sprints)"
    elif len(not_adjusted) == len(values):
        label = NOT_PTO_ADJUSTED
    else:
        label = f"{NOT_PTO_ADJUSTED} for sprint(s) {', '.join(str(n) for n in not_adjusted)}"
    if current:
        label += (f"; current sprint {current['days_left']} of {current['days']} working days left")
    return {"capacity_sp": capacity, "remaining_sprints": len(values), "current_sprint": current,
            "effective_sprints": round(sum(values), 2), "availability_csv": ",".join(f"{v:g}" for v in values),
            "pto_adjusted": bool(values) and not not_adjusted, "not_adjusted": not_adjusted, "label": label}


def usable_velocity(velocity_data):
    """``(team_velocity, None)`` from compute-velocity output, or ``(None, reason)``.

    Velocity is not used when it was averaged over fewer than 3 closed sprints, or
    when done team-owned issues were skipped for missing sprint and resolution
    data: either would understate it (decision 0018: never estimate).
    """
    data = velocity_data or {}
    value = _num(data.get("team_velocity"))
    if value is None or value <= 0:
        return None, "velocity not measured"
    if (data.get("sprint_count") or 0) < 3:
        return None, f"only {data.get('sprint_count') or 0} closed sprint(s) measured, 3 needed"
    skipped = data.get("skipped_no_sprint_data") or 0
    if skipped:
        return None, f"{skipped} done team issue(s) had no sprint or resolution data"
    return value, None


# ---------------------------------------------------------------- sections


def _version_key(version):
    """Numeric sort key for "X.Y" (9.10 after 9.9); anything else sorts last."""
    try:
        major, minor = str(version).split(".")
        return (int(major), int(minor))
    except ValueError:
        return (9999, 9999)


def select_sections(context, windows=()):
    """Release sections for the report, from scrum-context results (decision 0018).

    ``context`` is the no-argument scrum-context result; ``windows`` are
    release-mode results (one per release in progress, plus any release Product
    Pages lists as planned or in development). Returns ``(sections, missing)``:

    * refinement phase → a ``refinement`` section;
    * development, pencils-down sprint, pre-branch-cut → a ``release-health``
      section, with ``pencils_down`` set in the pencils-down sprint;
    * not scheduled yet, an anchor that could not be placed on a sprint, or an
      unknown phase → listed in ``missing`` with the reason;
    * not started / finished → no section.

    When nothing is in progress, ``missing`` says so.
    """
    # Accept a full release-mode scrum-context result as well as its "release" window.
    windows = [w["release"] if isinstance(w, dict) and isinstance(w.get("release"), dict) else w
               for w in windows or []]
    windows = [w for w in windows if isinstance(w, dict) and isinstance(w.get("release"), str)]
    by_release = {w["release"]: w for w in windows}
    in_progress = context.get("release_in_progress") if isinstance(context, dict) else None
    for entry in in_progress if isinstance(in_progress, list) else []:
        by_release.setdefault(entry.get("release"), entry)
    sections, missing = [], []
    for release in sorted(by_release, key=_version_key):
        w = by_release[release]
        phase = w.get("phase")
        label = f"{release} release section"
        if phase in SKIP_PHASES:
            continue
        if phase == "not scheduled yet":
            missing.append({"what": label, "why": "no Product Pages schedule yet"})
        elif phase == REFINEMENT:
            if not isinstance(w.get("refinement_sprint"), dict):
                missing.append({"what": label, "why": "refinement sprint could not be placed on a sprint"})
                continue
            sections.append({"release": release, "phase": phase, "kind": REFINEMENT,
                             "pencils_down": False, "window": w})
        elif phase in DEV_PHASES:
            if not isinstance(w.get("branch_cut_sprint"), dict) or not isinstance(w.get("refinement_sprint"), dict):
                missing.append({"what": label,
                                "why": "a release milestone could not be placed on a sprint"})
                continue
            sections.append({"release": release, "phase": phase, "kind": RELEASE_HEALTH,
                             "pencils_down": phase == "pencils-down sprint", "window": w})
        else:
            missing.append({"what": label, "why": f"phase unknown ({phase or 'not available'})"})
    if not sections and not missing:
        missing.append({"what": "Release section", "why": "no release in progress"})
    return sections, missing


# ---------------------------------------------------------------- sprint health


def _days(sprint, today):
    """(days elapsed, days remaining, total days): the shared ``sprint_day_counts`` definition."""
    return sprint_day_counts(sprint.get("start"), sprint.get("end"), today)


def _stale(issue, today):
    """In an active status (law 07, :data:`ACTIVE_STATUSES`) with no update for more than 5 business days."""
    if issue.get("stale"):
        return True
    if issue.get("status") not in ACTIVE_STATUSES or not issue.get("updated"):
        return False
    try:
        return is_stale(issue["updated"], today)
    except (ValueError, TypeError):
        return False


def _owner(issue):
    """An issue's assignee display name, or None when unassigned."""
    name = issue.get("assignee_display")
    return None if not name or name in ("Unassigned", "None") else name


def sprint_health(sprint_issues, sprint, today, mode, velocity=None, availability=None,
                  skipped_signals=(), links_complete=True, goal=None, velocity_reason=None):
    """Team-level sprint health for capacity or mid-sprint mode.

    ``sprint_issues`` is ``transform-sprint-issues.py`` output; ``sprint`` is
    scrum-context's ``current_sprint``; ``velocity`` is ``team_velocity`` from
    ``compute-velocity.py``. Returns figures, ``risks`` and ``actions`` (each
    action has an ``owner`` display name or None). No per-person figures.
    ``goal`` (mid-sprint) is the coordinator's sprint-goal rating,
    ``{"rating": "🟢"|"🟡"|"🔴"|None, "note": "..."}``, carried into the reply.
    ``velocity_reason`` is :func:`usable_velocity`'s reason when velocity was
    rejected; it is shown as the reason capacity is left out.
    """
    issues = sprint_issues.get("issues", [])
    skipped = list(skipped_signals)
    out = {"mode": mode, "sprint": sprint.get("name"), "week": sprint.get("week"),
           "committed_sp": sprint_issues.get("total_sp", 0), "issues": len(issues),
           "risks": [], "actions": [], "left_out": [], "goal": goal}
    if mode not in ("capacity", "mid-sprint"):
        # sprint_mode() returned None (week missing or not numeric): run neither
        # analysis; the section is left out with the reason.
        out.update(mode=None, skipped=True)
        out["left_out"].append({"what": "Sprint health", "why": "sprint week unknown"})
        return out
    if mode == "capacity":
        by_type = {}
        for i in issues:
            t = by_type.setdefault(i["type"], {"issues": 0, "sp": 0})
            t["issues"] += 1
            t["sp"] += i["sp"]
        cap = sprint_capacity(velocity, availability)
        out.update(by_type=by_type, velocity=_num(velocity), capacity=cap)
        unpointed = [i for i in issues if not i.get("sp")]
        no_epic = [i for i in issues if i.get("epic_key") == "No Epic"]
        out["unpointed"] = [i["key"] for i in unpointed]
        out["no_epic"] = [i["key"] for i in no_epic]
        ac_skipped = any(s.startswith("acceptance criteria") for s in skipped)
        out["missing_ac"] = None if ac_skipped else [
            i["key"] for i in issues if i["type"] in ("Story", "Task", "Spike") and not i.get("has_ac")]
        if cap["capacity_sp"] is not None and out["committed_sp"] > cap["capacity_sp"]:
            over = round(out["committed_sp"] - cap["capacity_sp"], 1)
            out["risks"].append({"severity": RED, "text": (
                f"Sprint committed {out['committed_sp']} SP vs capacity {cap['capacity_sp']:g} SP "
                f"({cap['label']}): {over:g} SP over")})
            out["actions"].append({"text": f"Re-plan the sprint: {over:g} SP over capacity", "owner": None,
                                   "team": True})
        elif cap["capacity_sp"] is None:
            out["left_out"].append({"what": "sprint capacity", "why": velocity_reason or "velocity not measured"})
        for label, items, verb in (("unpointed", unpointed, "Point"),
                                   ("without an epic", no_epic, "Link to an epic")):
            if items:
                out["risks"].append({"severity": YELLOW, "text": f"{len(items)} sprint issue(s) {label}"})
                out["actions"].extend({"text": f"{verb}: {i['key']}", "owner": _owner(i)} for i in items)
        if out["missing_ac"]:
            out["risks"].append({"severity": YELLOW,
                                 "text": f"{len(out['missing_ac'])} story/task/spike issue(s) without acceptance criteria"})
    else:
        elapsed, remaining, total_days = _days(sprint, today)
        total = sprint_issues.get("total_sp", 0)
        done = sprint_issues.get("total_done_sp", 0)
        left = sprint_issues.get("total_remaining_sp", total - done)
        burndown = {"total_sp": total, "done_sp": done, "days_elapsed": elapsed, "total_days": total_days}
        if total == 0:
            burndown["status"] = "No SP committed"
        elif elapsed == 0:
            burndown["status"] = "Sprint not yet started"
        else:
            expected = round(total * elapsed / total_days, 1)
            delta = round(done - expected, 1)
            status = ("On Track" if delta >= 0 else
                      "Slightly Behind" if abs(delta) <= total * 0.1 else "Behind")
            if done == 0:
                finish = "unknown (no completed SP yet)"
            else:
                need = left / (done / elapsed)
                over = math.ceil(need - remaining) if need > remaining else 0
                finish = ("on time" if over == 0 else
                          f"{over} day{'s' if over != 1 else ''} after the sprint ends")
            burndown.update(expected_done_sp=expected, delta_sp=delta, status=status, projected_finish=finish)
            if status != "On Track":
                out["risks"].append({"severity": RED if status == "Behind" else YELLOW, "text": (
                    f"Burndown {status.lower()}: {done} of {total} SP done, {expected:g} expected; "
                    f"projected to finish {finish}")})
                if status == "Behind":
                    out["actions"].append({"text": f"Re-scope the sprint: {left} SP left, burndown behind",
                                           "owner": None, "team": True})
        out["burndown"] = burndown
        flagged_known = not any(s.startswith("flagged") for s in skipped)
        blockers = []
        for i in issues:
            if i.get("status") in DONE_STATUSES_SPRINT:
                continue  # finished work needs no unblocking, whatever its links say
            reasons = []
            if flagged_known and i.get("flagged"):
                reasons.append("flagged")
            if links_complete and i.get("blocked_by"):
                reasons.append("blocked by " + ", ".join(i["blocked_by"]))
            if "Blocked" in (i.get("labels") or []):
                reasons.append("labeled Blocked")
            if _stale(i, today):
                reasons.append("no update for more than 5 business days")
            if reasons:
                blockers.append({"key": i["key"], "reasons": reasons, "owner": _owner(i)})
        out["blockers"] = [{"key": b["key"], "reasons": b["reasons"]} for b in blockers]
        if not links_complete:
            out["left_out"].append({"what": "blocked-by links",
                                    "why": "issue link details incomplete (get_jira_issue)"})
        if goal and goal.get("rating") == GREEN and (not links_complete or not flagged_known):
            # No visible blocker is not proof the goal work is unblocked when a
            # blocked signal is missing: do not report green.
            missing = "blocked-by links" if not links_complete else "Flagged"
            out["goal"] = {"rating": None,
                           "note": f"{goal.get('note') or 'goal on track'} (not rated: {missing} not available)"}
        if blockers:
            hard = [b for b in blockers if any(not r.startswith("no update") for r in b["reasons"])]
            out["risks"].append({"severity": RED if hard else YELLOW,
                                 "text": f"{len(blockers)} sprint issue(s) blocked or stalled"})
            out["actions"].extend({"text": ("Unblock" if any(not r.startswith("no update") for r in b["reasons"])
                                            else "Check stalled") + f" {b['key']} ({'; '.join(b['reasons'])})",
                                   "owner": b["owner"]} for b in blockers)
    out["left_out"].extend({"what": s.split(" (")[0] + " signal", "why": s[s.find("(") + 1:-1]
                            if "(" in s else "not available"} for s in skipped)
    return out


# ---------------------------------------------------------------- release health


def release_health(checks, section, capacity=None, rank_available=True, velocity_reason=None):
    """Team-level release health from ``run-checks.py`` output (development phases).

    Keeps scope vs capacity, the cut line and features at risk. Drops the
    per-person capacity rows (``ABOVE_GUIDELINE``) and reduces roster drift to a
    count. ``capacity`` is :func:`release_capacity` output, for its label.
    ``rank_available=False`` (features did not fit on one rank-ordered page)
    leaves the cut line out, since key order is not PM rank. ``velocity_reason``
    (from :func:`usable_velocity`) is shown when velocity is not available.
    """
    meta = checks.get("meta", {})
    scope, cap = meta.get("total_remaining_sp"), meta.get("total_capacity_sp")
    out = {"kind": RELEASE_HEALTH, "release": section["release"], "phase": section["phase"],
           "pencils_down": section.get("pencils_down", False),
           "scope_sp": scope, "capacity_sp": cap, "capacity_basis": meta.get("capacity_basis"),
           "capacity_label": (capacity or {}).get("label"),
           "effective_sprints": (capacity or {}).get("effective_sprints"),
           "velocity_reason": velocity_reason,
           "remaining_sprints": meta.get("remaining_sprints"), "velocity": meta.get("team_velocity"),
           "gap_sp": meta.get("gap_sp"),
           # A count, never an SP estimate (decision 0018).
           "unpointed_items": (meta.get("total_unpointed_stories") or 0) + (meta.get("unpointed_bugs") or 0),
           "risks": [], "actions": [], "left_out": []}
    if meta.get("capacity_basis") != "velocity":
        # Roster-sum capacity is an estimate (decision 0018: never estimate); the
        # timeline's pace depends on velocity too. Report scope only.
        out.update(capacity_sp=None, below_cut_line=[], features_at_risk=[],
                   roster_drift={"people": 0, "sp": 0})
        out["left_out"].append({"what": f"{section['release']} capacity, cut line and features at risk",
                                "why": velocity_reason or "velocity not available"})
        return out
    below = [r for r in checks.get("cut_line", []) if r.get("fits") in ("partial", "over")]
    if not rank_available:
        below = []
        out["left_out"].append({"what": f"{section['release']} cut line",
                                "why": "features did not fit on one rank-ordered page, so PM rank order is not available"})
    out["below_cut_line"] = [r["feature_key"] for r in below]
    at_risk = {}
    for c in checks.get("composite", []):
        if c.get("composite_risk") == "HIGH":
            at_risk[c["feature_key"]] = c.get("sme")
    for t in checks.get("timeline", []):
        if t.get("risk") in ("HIGH", "NO_CONTRIBUTORS"):
            at_risk.setdefault(t["feature_key"], t.get("sme"))
    out["features_at_risk"] = sorted(at_risk)
    drift = checks.get("process_gaps", {}).get("roster_drift", [])
    out["roster_drift"] = {"people": len(drift), "sp": sum(d.get("assigned_sp", 0) for d in drift)}
    tag = f"{section['release']}" + (" (pencils-down sprint)" if out["pencils_down"] else "")
    if scope is not None and cap is not None and scope > cap:
        out["risks"].append({"severity": RED, "text": (
            f"{tag}: scope {scope} SP vs capacity {cap} SP ({out['capacity_label'] or meta.get('capacity_basis')}): "
            f"{scope - cap} SP over"
            + (f"; {len(below)} feature(s) below the cut line" if rank_available else ""))})
    for r in below:
        out["actions"].append({"text": f"{tag}: decide scope for {r['feature_key']} (below the cut line)",
                               "owner": _sme(r.get("sme"))})
    if at_risk:
        out["risks"].append({"severity": RED, "text": f"{tag}: {len(at_risk)} feature(s) at risk"})
        for key in sorted(at_risk):
            if key not in out["below_cut_line"]:
                out["actions"].append({"text": f"{tag}: review plan for {key} (at risk)",
                                       "owner": _sme(at_risk[key])})
    if out["roster_drift"]["people"]:
        out["left_out"].append({"what": f"{tag}: capacity from {out['roster_drift']['people']} assignee(s) not on the roster",
                                "why": f"their {out['roster_drift']['sp']} SP counts as scope, not capacity"})
    return out


def _sme(name):
    """A feature's SME display name, or None when unset."""
    return None if name in (None, "", "None") else name


def refinement_gaps(features, spikes, section, refinement_sprint_closed=False):
    """Refinement and test-plan spike gaps (release-health ``--refinement`` rules).

    ``features`` is ``transform-features.py`` output for features in Refinement;
    ``spikes`` is ``transform-spikes.py`` output. Owners: the SME for the
    refinement spike, the QE Lead (QA Contact) for the test plan spike.
    """
    sprint = (section.get("window") or {}).get("refinement_sprint") or {}
    number = sprint.get("number") if isinstance(sprint, dict) else None
    spike_map = spikes.get("spike_map", {})
    tag = section["release"]
    out = {"kind": REFINEMENT, "release": tag, "phase": section["phase"],
           "features": len(features.get("features", [])),
           "missing_refinement_spike": [], "spike_not_in_refinement_sprint": [],
           "refinement_spike_overdue": [], "missing_test_plan_spike": [], "test_plan_spike_overdue": [],
           "risks": [], "actions": [], "left_out": []}
    for f in features.get("features", []):
        s = spike_map.get(f["key"])
        if s is None:
            continue
        sme, qe = _sme(f.get("sme")), _sme(f.get("qa_contact"))
        if s["spike_status"] == "Closed" or (s["spike_missing"] and (s["spike_on_epic"] or s["epics_appear_refined"])):
            pass
        elif s["spike_missing"]:
            out["missing_refinement_spike"].append(f["key"])
            out["actions"].append({"text": f"{tag}: create a refinement spike that blocks {f['key']}"
                                   + (f" in sprint {number}" if number and not refinement_sprint_closed else ""),
                                   "owner": sme})
        elif refinement_sprint_closed and s["spike_overdue"]:
            out["refinement_spike_overdue"].append(f["key"])
            out["actions"].append({"text": f"{tag}: close refinement spike {s['spike_key']} ({f['key']})",
                                   "owner": sme})
        elif not refinement_sprint_closed and not s["spike_in_ref_sprint"]:
            out["spike_not_in_refinement_sprint"].append(f["key"])
            out["actions"].append({"text": f"{tag}: move refinement spike {s['spike_key']} ({f['key']}) "
                                   f"to sprint {number}" if number else
                                   f"{tag}: move refinement spike {s['spike_key']} to the refinement sprint",
                                   "owner": sme})
        if not s["test_plan_check_skipped"]:
            if s["test_plan_spike_missing"]:
                out["missing_test_plan_spike"].append(f["key"])
                out["actions"].append({"text": f"{tag}: create a test plan spike that blocks {f['key']}",
                                       "owner": qe})
            elif s["test_plan_spike_overdue"]:
                out["test_plan_spike_overdue"].append(f["key"])
                out["actions"].append({"text": f"{tag}: close test plan spike {s['test_plan_spike_key']}",
                                       "owner": qe})
    gaps = len(out["missing_refinement_spike"]) + len(out["refinement_spike_overdue"])
    if gaps:
        out["risks"].append({"severity": RED,
                             "text": f"{tag}: {gaps} of {out['features']} feature(s) with a missing or overdue refinement spike"})
    tp = len(out["missing_test_plan_spike"]) + len(out["test_plan_spike_overdue"])
    if tp:
        out["risks"].append({"severity": YELLOW, "text": f"{tag}: {tp} feature(s) missing a test plan spike"})
    if out["spike_not_in_refinement_sprint"]:
        out["risks"].append({"severity": YELLOW,
                             "text": f"{tag}: {len(out['spike_not_in_refinement_sprint'])} refinement spike(s) not in the refinement sprint"})
    return out


# ---------------------------------------------------------------- epic Target Version and QA Contact

EPIC_FIELDS = "epic-fields"
EPIC_LINE_LIMIT = 10  # owner lines in the epic section
# Parent statuses flagged by decision 0024, by their exact OCPSTRAT names.
PARENT_FLAGGED_STATUSES = ("Backlog", "Closed")
# Epic statuses, by their exact law 04 names (decision 0024): active epics are
# "To Do" through "Release Pending" (refinement done, not Closed; New and
# Planning are still being refined). Drift is flagged only before work is done.
ACTIVE_EPIC_STATUSES = ("To Do", "In Progress", "Dev Complete", "Testing", "Release Pending")
DRIFT_EPIC_STATUSES = ("To Do", "In Progress")
NO_QA_CONTACT = ("unassigned_jira",)  # placeholder account used for "no QA Contact"
DRIFT_LABEL = "Target Version \u2260 feature"  # the epic's Target Version differs from its parent's
DRIFT_QUESTION = "retarget, split or keep?"
# (check id, label in the section header), in the order labels are listed for an epic.
EPIC_CHECKS = (
    ("no_target_version", "no Target Version"),
    ("no_qa_contact", "no QA Contact"),
    ("target_version_differs", DRIFT_LABEL),
    ("parent_backlog_or_closed", "feature closed or in backlog"),
)
# Not a decision 0024 check, but listed with the epic; the epic goes on the Scrum Master's line.
NO_ASSIGNEE = ("no_assignee", "no assignee")
_EPIC_SECTION = "Epic Target Version and QA Contact checks"


def _text(value):
    """A field value as stripped text: a dict's ``name`` / ``display_name`` /
    ``displayName`` / ``value``, a plain string; None when empty or unreadable."""
    if isinstance(value, dict):
        value = next((value.get(k) for k in ("name", "display_name", "displayName", "value")
                      if isinstance(value.get(k), str) and value.get(k).strip()), None)
    if not isinstance(value, str) or not value.strip() or value.strip() in ("None", "Unassigned"):
        return None
    return value.strip()


def _versions(value):
    """Target Version names: from the adapter's ``[{"name": ...}]`` list, a list of
    strings, or a comma-separated string. A missing field is an empty list."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        value = [value] if isinstance(value, dict) else []
    return [name for name in (_text(v) for v in value) if name]


def _qa_contact(issue):
    """QA Contact display name, or None when unset or the ``unassigned_jira`` placeholder."""
    name = _text(issue.get("customfield_10470"))
    return None if name is None or name.casefold() in NO_QA_CONTACT else name


def _status_is(issue, *names):
    """Whether the issue's status is one of ``names`` (case-insensitive)."""
    status = _text(issue.get("status"))
    return status is not None and status.casefold() in {n.casefold() for n in names}


def _issues_of(fetch):
    """``(issues, problem, rejected)`` from an ``adapt_fetch`` result.

    ``problem`` is None when the fetch is complete. ``rejected`` counts the
    records that are not a dict or have no string ``key``: they are skipped, the
    valid ones are still returned.
    """
    if not isinstance(fetch, dict):
        return [], "result not readable", 0
    status = fetch.get("fetch")
    if not isinstance(status, dict) or not isinstance(fetch.get("issues"), list):
        return [], "fetch status missing", 0
    if not status.get("complete"):
        problems = status.get("problems")
        why = "; ".join(str(p) for p in problems) if isinstance(problems, list) and problems else None
        return [], f"fetch incomplete ({why or 'reason not given'})", 0
    issues = [i for i in fetch["issues"] if isinstance(i, dict) and isinstance(i.get("key"), str)]
    return issues, None, len(fetch["issues"]) - len(issues)


def _read_parents(parent_batches):
    """Parent issues by key, and the reason each unreadable requested key was not read.

    Each batch is ``{"keys": [requested keys], "result": adapt_fetch output}`` or
    ``{"keys": [...], "error": "<why>"}``. A failed or incomplete batch makes all
    its keys unread; a requested key a complete batch did not return is unread
    (restricted, deleted or no permission).
    """
    parents, unread = {}, {}
    if isinstance(parent_batches, dict):
        parent_batches = [parent_batches]
    if not isinstance(parent_batches, (list, tuple)):
        return parents, unread, "parent results not readable"
    for batch in parent_batches:
        if not isinstance(batch, dict):
            continue
        keys = [k for k in batch.get("keys") or [] if isinstance(k, str)] \
            if isinstance(batch.get("keys"), list) else []
        if batch.get("error"):
            unread.update({k: f"query failed ({batch['error']})" for k in keys})
            continue
        issues, problem, _ = _issues_of(batch.get("result"))  # a rejected record's key stays unread
        if problem:
            unread.update({k: problem for k in keys})
            continue
        parents.update({i["key"]: i for i in issues})
        unread.update({k: "not returned by Jira (restricted, deleted or no permission)"
                       for k in keys if k not in parents})
    for key in parents:
        unread.pop(key, None)  # read in another batch
    return parents, unread, None


def epic_field_gaps(epics, parent_batches=(), previous_flagged=None):
    """Decision 0024 flags for active OCPEDGE epics with a parent.

    ``epics`` is the ``adapt_fetch`` output of the active-epics query (fields
    summary, status, assignee, parent, Target Version ``customfield_10855``, QA
    Contact ``customfield_10470``); ``parent_batches`` holds the parent lookups
    (see :func:`_read_parents`). Only active epics are checked
    (:data:`ACTIVE_EPIC_STATUSES`: To Do through Release Pending; New, Planning
    and Closed are ignored):

    1. ``target_version_differs`` — To Do or In Progress only, and only when the
       parent has a Target Version: the epic's Target Version is not among the
       parent's (a parent with several versions matches any of them). From Dev
       Complete on, finished work keeps its release.
    2. ``no_target_version`` — any active epic with no Target Version.
    3. ``no_qa_contact`` — any active epic with no QA Contact
       (``unassigned_jira`` counts as none).
    4. ``parent_backlog_or_closed`` — any active epic whose parent is in Backlog
       or Closed.

    An active epic with no assignee also gets ``no_assignee`` and goes on the
    Scrum Master's line (``build_report``'s ``team_owner``).

    ``checks`` keeps each check's items (counts). ``epics`` merges them into one
    entry per flagged epic (``key``, ``summary``, ``owner``, ``problems``: labels
    in check order, ``new``), sorted by number of problems (most first), then
    key. ``previous_flagged`` is the previous report's flagged epic keys, from
    persona memory: when given, ``new`` marks epics not in it (🆕); when None,
    ``new`` is None and no marker is shown. ``flagged_keys`` is this run's set,
    to store for next time, but only when ``complete`` is True: every epic was
    read and checked (no failed or cut-off fetch, no unreadable parent or
    record). An incomplete set stored as last week's would mark returning flags
    🆕; ``[]`` from a complete run with no flags is a valid set.

    An epic whose parent could not be read skips the checks that need the
    parent (1 and 4) and is listed as left out with the reason. An incomplete epic fetch computes nothing. Never
    raises on malformed input: what cannot be read is left out.

    Informational: ``risks`` and ``actions`` stay empty, so the flags never change
    the report's colour, risk line or action count; the count is in the section
    header only.
    """
    out = {"kind": EPIC_FIELDS, "epics_checked": 0, "flagged": 0,
           "checks": {cid: [] for cid, _ in EPIC_CHECKS + (NO_ASSIGNEE,)}, "epics": [], "flagged_keys": [], "complete": False,
           "risks": [], "actions": [], "left_out": []}
    issues, problem, rejected = _issues_of(epics)
    if problem:
        out["left_out"].append({"what": _EPIC_SECTION, "why": f"open-epic {problem}"})
        return out
    parents, unread, parents_problem = _read_parents(parent_batches)
    no_parent, skipped = [], {}
    for epic in issues:
        if not _status_is(epic, *ACTIVE_EPIC_STATUSES):
            continue
        parent_key = _text(epic.get("parent")) if not isinstance(epic.get("parent"), dict) \
            else _text(epic["parent"].get("key"))
        if not parent_key:
            no_parent.append(epic["key"])
            continue
        out["epics_checked"] += 1
        parent = parents.get(parent_key)
        versions, owner = _versions(epic.get("customfield_10855")), _owner_of(epic)
        p_versions = _versions(parent.get("customfield_10855")) if parent else []
        base = {"key": epic["key"], "summary": _text(epic.get("summary")) or "", "owner": owner}
        if not versions:
            out["checks"]["no_target_version"].append(dict(base, detail="no Target Version"))
        if parent is None:
            skipped.setdefault(parent_key, []).append(epic["key"])
        # A parent with no Target Version is not drift (nothing to differ from).
        if (parent and versions and p_versions and _status_is(epic, *DRIFT_EPIC_STATUSES)
                and any(v not in p_versions for v in versions)):
            out["checks"]["target_version_differs"].append(dict(base, detail=DRIFT_LABEL))
        if _qa_contact(epic) is None:
            out["checks"]["no_qa_contact"].append(dict(base, detail="no QA Contact"))
        if parent and _status_is(parent, *PARENT_FLAGGED_STATUSES):
            closed = _status_is(parent, "Closed")
            out["checks"]["parent_backlog_or_closed"].append(
                dict(base, detail="feature closed" if closed else "feature in backlog"))
        if owner is None:
            out["checks"][NO_ASSIGNEE[0]].append(dict(base, detail=NO_ASSIGNEE[1]))
    by_key = {}
    for cid, _ in EPIC_CHECKS + (NO_ASSIGNEE,):
        out["checks"][cid].sort(key=lambda i: _key_number(i["key"]))
        for item in out["checks"][cid]:
            entry = by_key.setdefault(item["key"], {"key": item["key"], "summary": item["summary"],
                                                    "owner": item["owner"], "problems": [], "new": None})
            entry["problems"].append(item["detail"])
    previous = ({k for k in previous_flagged if isinstance(k, str)}
                if isinstance(previous_flagged, (list, tuple, set, frozenset)) else None)
    for entry in by_key.values():
        entry["new"] = None if previous is None else entry["key"] not in previous
    out["epics"] = sorted(by_key.values(), key=lambda e: (-len(e["problems"]), _key_number(e["key"])))
    out["flagged"] = len(out["epics"])
    out["flagged_keys"] = sorted(by_key, key=_key_number)
    by_reason = {}
    for key in sorted(skipped, key=_key_number):
        why = unread.get(key) or parents_problem or "not fetched"
        by_reason.setdefault(why, []).append(key)
    for why, keys in by_reason.items():
        count = sum(len(skipped[k]) for k in keys)
        out["left_out"].append({"what": f"Target Version and parent status checks for {count} epic(s) "
                                        f"under {', '.join(keys)}",
                                "why": f"parent could not be read: {why}"})
    if no_parent:
        out["left_out"].append({"what": f"{len(no_parent)} epic(s) returned without a parent",
                                "why": "not checked: " + ", ".join(sorted(no_parent, key=_key_number))})
    if rejected:
        out["left_out"].append({"what": f"{rejected} epic record(s) could not be read",
                                "why": "not an issue record or no issue key; not checked"})
    out["complete"] = not (skipped or no_parent or rejected)
    return out


def _owner_of(issue):
    """An adapted issue's assignee display name, or None when unassigned."""
    return _text(issue.get("assignee"))


def _key_number(key):
    """Sort key for an issue key: (project, number); ``(key, 0)`` when the suffix is
    not a plain decimal number (``isdigit`` alone accepts "²", which ``int`` rejects)."""
    project, _, num = str(key).rpartition("-")
    if num.isdecimal():
        try:
            return (project, int(num))
        except ValueError:
            pass
    return (str(key), 0)


def _epic_section(sec, mentions, team_owner=None):
    """``(lines, left_out)`` for the epic section: one line per owner, capped.

    Header: counts per check (zero counts omitted) and the key for
    "Target Version ≠ feature". Each owner line lists that owner's epics as
    ``KEY (label, label)``, most problems first, then key; 🆕 marks an epic
    not flagged last time (only when the previous set was known). An epic with
    no assignee goes on the Scrum Master's line (``team_owner``), else under
    "no owner set". Owners are sorted by number of epics; after 10 owner lines,
    "+N more owner(s)" and their epics go to Left out.
    """
    counts = [f"{len(sec['checks'][cid])} {label}" for cid, label in EPIC_CHECKS + (NO_ASSIGNEE,)
              if sec["checks"].get(cid)]
    n = sec["flagged"]
    head = f"*Epic fields* ({n} epic{'s' if n != 1 else ''}: {', '.join(counts)})"
    if sec["checks"].get("target_version_differs"):
        head += f'. Key: "{DRIFT_LABEL}" = {DRIFT_QUESTION}'
    groups = {}
    for e in sec["epics"]:  # already sorted by number of problems, then key
        groups.setdefault(e["owner"] or team_owner, []).append(e)
    owners = sorted(groups, key=lambda o: (-len(groups[o]), o is None, str(o or "").casefold()))

    def items(owner):
        return ", ".join(f"{'🆕 ' if e.get('new') else ''}{e['key']} ({', '.join(e['problems'])})"
                         for e in groups[owner])

    lines = [head] + [f"• {mention(o, mentions)}: {items(o)}" for o in owners[:EPIC_LINE_LIMIT]]
    rest = owners[EPIC_LINE_LIMIT:]
    left_out = []
    if rest:
        lines.append(f"• +{len(rest)} more owner(s) (listed under Left out)")
        left_out.append({"what": f"{len(rest)} more owner(s) over the {EPIC_LINE_LIMIT}-line limit",
                         "why": "; ".join(f"{mention(o, mentions)}: {items(o)}" for o in rest)})
    return lines, left_out


# ---------------------------------------------------------------- report


def mention(owner, mentions):
    """Slack mention for an owner when mapped (``<@U…>``), else the plain name."""
    if not owner:
        return "no owner set"
    key = normalize_name(owner)
    return next((v for k, v in (mentions or {}).items() if normalize_name(k) == key), None) or owner


def _sprint_figures(s):
    """One-line sprint figures for the top post and the sprint reply."""
    if s["mode"] == "capacity":
        cap = s.get("capacity") or {}
        cap_txt = f"{cap['capacity_sp']:g} SP ({cap['label']})" if cap.get("capacity_sp") is not None \
            else "not available"
        return f"committed {s['committed_sp']} SP in {s['issues']} issue{'s' if s['issues'] != 1 else ''}; capacity {cap_txt}"
    b = s.get("burndown", {})
    if "expected_done_sp" in b:
        return (f"{b['done_sp']} of {b['total_sp']} SP done, {b['expected_done_sp']:g} expected "
                f"({b['status']})")
    return f"{s['committed_sp']} SP committed ({b.get('status', 'burndown not available')})"


def _section_lines(sec):
    """Thread reply lines for one release section (refinement or release health)."""
    if sec.get("kind") == REFINEMENT:
        return [f"*{sec['release']} refinement* ({sec['features']} feature(s) in Refinement)",
                f"• Missing refinement spike: {len(sec['missing_refinement_spike'])}",
                f"• Refinement spike not in the refinement sprint: {len(sec['spike_not_in_refinement_sprint'])}",
                f"• Missing or open test plan spike: "
                f"{len(sec['missing_test_plan_spike']) + len(sec['test_plan_spike_overdue'])}"]
    lines = [f"*{sec['release']} release health* ("
             + ("pencils-down sprint: the last development sprint" if sec.get("pencils_down") else sec["phase"]) + ")",
             (f"• Scope {sec['scope_sp']} SP vs capacity {sec['capacity_sp']} SP to branch cut "
              + (f"({sec['velocity']:g} SP velocity × {sec['effective_sprints']:g} sprint(s); {sec['capacity_label']})"
                 if sec.get("effective_sprints") is not None and sec.get("velocity") is not None
                 else f"({sec.get('capacity_label') or sec.get('capacity_basis')})")
              if sec["capacity_sp"] is not None else
              f"• Scope {sec['scope_sp']} SP; capacity not available "
              f"({sec.get('velocity_reason') or 'velocity not measured'})"),
             f"• Below the cut line: {', '.join(sec['below_cut_line']) or 'none'}",
             f"• Features at risk: {', '.join(sec['features_at_risk']) or 'none'}"]
    if sec.get("unpointed_items"):
        lines.append(f"• {sec['unpointed_items']} unpointed items, not counted in scope")
    return lines


def _sprint_lines(s):
    """Thread reply lines for the sprint section, with the goal rating when given."""
    head = f"*Sprint health: {s['sprint']}* (week {s['week']}, {s['mode']} mode)"
    lines = [head, f"• {_sprint_figures(s)}"]
    if s["mode"] == "capacity":
        types = ", ".join(f"{t} {v['issues']} ({v['sp']} SP)" for t, v in sorted(s.get("by_type", {}).items()))
        lines.append(f"• By type: {types or 'none'}")
        lines.append(f"• Unpointed: {len(s['unpointed'])}; without an epic: {len(s['no_epic'])}"
                     + ("" if s.get("missing_ac") is None else f"; without acceptance criteria: {len(s['missing_ac'])}"))
    else:
        lines.append(f"• Blocked or stalled: {len(s.get('blockers', []))}")
    goal = s.get("goal")
    if goal:
        lines.append(f"• Sprint goal: {(goal.get('rating') or '') + ' ' if goal.get('rating') else ''}"
                     f"{goal.get('note') or ''}".rstrip())
    return lines


def build_report(sprint, sections, missing=(), mentions=None, team_owner=None, epic_gaps=None):
    """Assemble the Slack post: a 3-line top post and thread replies.

    ``sprint`` is :func:`sprint_health` output (or None when it could not be
    computed); ``sections`` are :func:`release_health` / :func:`refinement_gaps`
    outputs (``kind`` set); ``missing`` lists ``{"what", "why"}`` left out.
    ``mentions`` maps an owner display name to a Slack mention. ``team_owner``
    (the Scrum Master) owns team-level actions such as re-planning; without it
    they keep "no owner set". ``epic_gaps`` is :func:`epic_field_gaps` output:
    its flags get one reply after the release sections (none when nothing is
    flagged), one line per owner tagging them (the Scrum Master, ``team_owner``,
    for epics with no assignee); what it left out joins
    "Left out". It is informational: it never changes the top post (colour,
    risk line, action count). A malformed value is left out, never raised. Returns
    ``{"top_post": [3 lines], "replies": [...], "action_count": N}``.
    """
    left_out_sprint = []
    if sprint and sprint.get("skipped"):
        left_out_sprint, sprint = sprint.get("left_out", []), None
    parts = ([sprint] if sprint else []) + list(sections)
    if any("risks" not in p for p in parts):
        raise ValueError("build_report needs sprint_health / release_health / refinement_gaps results, "
                         "not select_sections descriptors")
    epic_lines, epic_left_out = [], []
    if epic_gaps is not None:
        try:
            if epic_gaps.get("kind") != EPIC_FIELDS:
                raise TypeError("not an epic_field_gaps result")
            epic_lines, capped = (_epic_section(epic_gaps, mentions, team_owner) if epic_gaps["flagged"]
                                  else ([], []))
            epic_left_out = [{"what": str(x["what"]), "why": str(x["why"])} for x in epic_gaps["left_out"]] + capped
        except (AttributeError, KeyError, TypeError, ValueError):
            epic_lines, epic_left_out = [], [{"what": _EPIC_SECTION, "why": "result not readable"}]
    risks = [r for p in parts for r in p["risks"]]
    actions = [dict(a, owner=team_owner) if a.get("team") and not a.get("owner") and team_owner else a
               for p in parts for a in p["actions"]]
    left_out = list(missing) + left_out_sprint + [x for p in parts for x in p.get("left_out", [])]
    status = RED if any(r["severity"] == RED for r in risks) else YELLOW if risks or left_out else GREEN
    left_out += epic_left_out  # listed, but informational: not part of the colour
    where = []
    if sprint:
        where.append(f"{sprint['sprint']} (week {sprint['week']}, {sprint['mode']})")
    where += [f"{s['release']} {s['phase']}" for s in sections]
    top = [f"{status} Edge weekly health: " + ("; ".join(where) or "sprint data not available")]
    if risks:
        biggest = next((r for r in risks if r["severity"] == RED), risks[0])
        top.append(f"Biggest risk: {biggest['text']}")
    else:
        top.append("No risks this week" + (f": {_sprint_figures(sprint)}" if sprint else ""))
    top.append(f"{len(actions)} item{'s' if len(actions) != 1 else ''} need{'s' if len(actions) == 1 else ''} action")
    replies = []
    if sprint:
        replies.append("\n".join(_sprint_lines(sprint)))
    for sec in sections:
        replies.append("\n".join(_section_lines(sec)))
    if epic_lines:
        replies.append("\n".join(epic_lines))
    if actions:
        replies.append("\n".join(["*Action items*"] + [
            f"{n}. {a['text']} (owner: {mention(a['owner'], mentions)})" for n, a in enumerate(actions, 1)]))
    if left_out:
        replies.append("\n".join(["*Left out*"] + [f"• {x['what']}: {x['why']}" for x in left_out]))
    return {"top_post": top, "replies": replies, "action_count": len(actions)}
