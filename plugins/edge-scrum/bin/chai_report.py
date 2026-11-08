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
from _jira_transforms import DONE_STATUSES_SPRINT, IN_PROGRESS_STATUSES, days_between, is_stale  # noqa: E402
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
    """(days elapsed, days remaining, total days) of a sprint, calendar days, as transform-sprints counts them."""
    start, end = sprint.get("start"), sprint.get("end")
    total = days_between(start, end)
    elapsed = max(0, min(days_between(start, today), total))
    return elapsed, total - elapsed, total


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
                             "text": f"{tag}: {gaps} of {out['features']} feature(s) without a closed or open refinement spike"})
    tp = len(out["missing_test_plan_spike"]) + len(out["test_plan_spike_overdue"])
    if tp:
        out["risks"].append({"severity": YELLOW, "text": f"{tag}: {tp} feature(s) missing a test plan spike"})
    if out["spike_not_in_refinement_sprint"]:
        out["risks"].append({"severity": YELLOW,
                             "text": f"{tag}: {len(out['spike_not_in_refinement_sprint'])} refinement spike(s) not in the refinement sprint"})
    return out


# ---------------------------------------------------------------- report


def mention(owner, mentions):
    """Slack mention for an owner when mapped (``<@U…>``), else the plain name."""
    if not owner:
        return "no owner set"
    return (mentions or {}).get(owner) or owner


def _sprint_figures(s):
    """One-line sprint figures for the top post and the sprint reply."""
    if s["mode"] == "capacity":
        cap = s.get("capacity") or {}
        cap_txt = f"{cap['capacity_sp']:g} SP ({cap['label']})" if cap.get("capacity_sp") is not None \
            else "not available"
        return f"committed {s['committed_sp']} SP in {s['issues']} issues; capacity {cap_txt}"
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
              f"• Scope {sec['scope_sp']} SP; capacity not available (velocity not measured)"),
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


def build_report(sprint, sections, missing=(), mentions=None, team_owner=None):
    """Assemble the Slack post: a 3-line top post and thread replies.

    ``sprint`` is :func:`sprint_health` output (or None when it could not be
    computed); ``sections`` are :func:`release_health` / :func:`refinement_gaps`
    outputs (``kind`` set); ``missing`` lists ``{"what", "why"}`` left out.
    ``mentions`` maps an owner display name to a Slack mention. ``team_owner``
    (the Scrum Master) owns team-level actions such as re-planning; without it
    they keep "no owner set". Returns
    ``{"top_post": [3 lines], "replies": [...], "action_count": N}``.
    """
    left_out_sprint = []
    if sprint and sprint.get("skipped"):
        left_out_sprint, sprint = sprint.get("left_out", []), None
    parts = ([sprint] if sprint else []) + list(sections)
    if any("risks" not in p for p in parts):
        raise ValueError("build_report needs sprint_health / release_health / refinement_gaps results, "
                         "not select_sections descriptors")
    risks = [r for p in parts for r in p["risks"]]
    actions = [dict(a, owner=team_owner) if a.get("team") and not a.get("owner") and team_owner else a
               for p in parts for a in p["actions"]]
    left_out = list(missing) + left_out_sprint + [x for p in parts for x in p.get("left_out", [])]
    status = RED if any(r["severity"] == RED for r in risks) else YELLOW if risks or left_out else GREEN
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
    if actions:
        replies.append("\n".join(["*Action items*"] + [
            f"{n}. {a['text']} (owner: {mention(a['owner'], mentions)})" for n, a in enumerate(actions, 1)]))
    if left_out:
        replies.append("\n".join(["*Left out*"] + [f"• {x['what']}: {x['why']}" for x in left_out]))
    return {"top_post": top, "replies": replies, "action_count": len(actions)}
