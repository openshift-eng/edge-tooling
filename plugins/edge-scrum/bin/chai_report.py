#!/usr/bin/env python3
"""Team-level figures and Slack text for the chai-bot weekly health report.

Pure functions used by the chai-bot skills ``sprint-health``, ``release-health``
and ``weekly-health-report`` (edge-context decision 0018) inside
``sandbox_exec``. Inputs are the outputs of the existing edge-scrum scripts
(``scrum_context.py``, ``transform-*.py``, ``compute-velocity.py``,
``run-checks.py``); nothing here fetches data. The feature and sprint checks
themselves live in ``_scrum_rules.py`` (shared with the Claude Code skills);
this module selects them by phase and formats the Slack text.

Output is **team-level only**: no per-person load, SP or PTO. A person's name
appears only as the owner of a specific action item (decision 0018).
"""

import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import _scrum_rules as rules  # noqa: E402
from _jira_transforms import DONE_STATUSES_SPRINT, normalize_name, sprint_day_counts  # noqa: E402
from scrum_context import NOT_SCHEDULED, PHASE_LOOKUP_FAILED, to_date, working_days  # noqa: E402

NOT_PTO_ADJUSTED = "not PTO-adjusted"
REFINEMENT, RELEASE_HEALTH = "refinement", "release-health"
DEV_PHASES = ("development", "pencils-down sprint", "pre-branch-cut")
SKIP_PHASES = ("not started", "finished")
REFINEMENT_NOT_PLACED = "refinement sprint could not be placed on a sprint"
# The release gate: Edge Features (OCPSTRAT, type Feature, not Closed, an Edge label)
# per Target Version "openshift-X.Y".
EDGE_FEATURE_LABELS = ("ocpedge-plan", "microshift")
GATE_INCOMPLETE = "release Feature gate incomplete"
_TARGET_RELEASE_RE = re.compile(r"^openshift-(\d+\.\d+)$", re.IGNORECASE)
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


def release_feature_gate(gate_fetch):
    """Releases with at least one Edge Feature, from the gate query's ``adapt_fetch`` output.

    The gate query (weekly-health-report SKILL) asks for every non-Closed
    OCPSTRAT Feature with an Edge label and a Target Version. Counted here,
    rechecked per issue: type Feature (Initiatives do not count), status not
    Closed, an :data:`EDGE_FEATURE_LABELS` label (any case), once per Target Version
    ``openshift-X.Y``. Returns ``{"complete", "counts", "releases", "why"}``:
    ``releases`` are the releases with a count, the candidates the report
    reads. A failed, cut-off or unreadable fetch is unknown (``complete``
    False, ``counts`` None, ``why`` the reason), never zero Features.
    """
    status = gate_fetch.get("fetch") if isinstance(gate_fetch, dict) else None
    issues = gate_fetch.get("issues") if isinstance(gate_fetch, dict) else None
    if not isinstance(status, dict) or status.get("complete") is not True or not isinstance(issues, list):
        why = "; ".join(status.get("problems") or []) if isinstance(status, dict) else ""
        return {"complete": False, "counts": None, "releases": [], "why": why or "fetch status missing"}
    counts = {}
    for issue in issues:
        key = issue.get("key") if isinstance(issue, dict) else "?"
        if not isinstance(issue, dict) or "customfield_10855" not in issue:
            return {"complete": False, "counts": None, "releases": [], "why": f"{key}: Target Version not returned"}
        kind = str((issue.get("issue_type") or {}).get("name") or "").strip()
        status_name = str((issue.get("status") or {}).get("name") or "").strip()
        if not kind or not status_name:  # unknown is not "not a Feature" or "Closed"
            what = "issue type" if not kind else "status"
            return {"complete": False, "counts": None, "releases": [], "why": f"{key}: {what} not returned"}
        labels = {str(label).casefold() for label in issue.get("labels") or []}
        if not labels:  # the query asks for an Edge label: none means the field was not returned
            return {"complete": False, "counts": None, "releases": [], "why": f"{key}: labels not returned"}
        if kind != "Feature" or status_name == "Closed" or not labels & set(EDGE_FEATURE_LABELS):
            continue
        releases = {m.group(1) for v in issue["customfield_10855"] or []
                    if isinstance(v, dict) and (m := _TARGET_RELEASE_RE.match(str(v.get("name") or "").strip()))}
        for release in releases:
            counts[release] = counts.get(release, 0) + 1
    return {"complete": True, "counts": counts, "releases": sorted(counts, key=_version_key), "why": None}


def _not_scheduled_reason(window):
    """Why a release has no refinement sprint: no schedule at all, a schedule
    without the refinement milestone, or a milestone no sprint starts near."""
    milestones = window.get("milestones")
    if not isinstance(milestones, dict) or not milestones:
        return "no Product Pages schedule yet"
    if milestones.get("development_branch_opens") not in (None, NOT_SCHEDULED):
        return REFINEMENT_NOT_PLACED
    return (f'Product Pages schedule has no readable "Development Branch opens" milestone '
            f'(nor a previous release\'s "Master Branch Open for {window.get("release")}")')


def select_sections(context, windows=(), gate=None):
    """Release sections for the report, from scrum-context results (decision 0018).

    ``context`` is the no-argument scrum-context result; ``windows`` are
    release-mode results, one per candidate release. ``gate`` is
    :func:`release_feature_gate` output: when given, only releases with at
    least one Edge Feature are reported, a release with none is left out
    silently (no section, nothing under Left out, whatever Product Pages
    says), and an incomplete gate reports no release section and says why
    (unknown, never zero). A complete gate with no Edge Feature at all adds
    nothing to ``missing``: no release was expected. Returns ``(sections, missing)``:

    * refinement phase → a ``refinement`` section;
    * development, pencils-down sprint, pre-branch-cut → a ``release-health``
      section, with ``pencils_down`` set in the pencils-down sprint;
    * not scheduled yet (no schedule, or no refinement milestone in it), an
      anchor that could not be placed on a sprint, a failed Product Pages
      lookup ("release phase unknown: Product Pages lookup failed"), or an
      unknown phase → listed in ``missing`` with the reason;
    * not started / finished → no section.

    When nothing is in progress, ``missing`` says so, unless a complete gate
    found no Edge Feature for any release.
    """
    # Accept a full release-mode scrum-context result as well as its "release" window.
    windows = [w["release"] if isinstance(w, dict) and isinstance(w.get("release"), dict) else w
               for w in windows or []]
    windows = [w for w in windows if isinstance(w, dict) and isinstance(w.get("release"), str)]
    by_release = {w["release"]: w for w in windows}
    in_progress = context.get("release_in_progress") if isinstance(context, dict) else None
    for entry in in_progress if isinstance(in_progress, list) else []:
        by_release.setdefault(entry.get("release"), entry)
    if gate is not None:
        if not isinstance(gate, dict) or gate.get("complete") is not True or not isinstance(gate.get("counts"), dict):
            why = gate.get("why") if isinstance(gate, dict) else None
            return [], [{"what": "Release sections", "why": f"{GATE_INCOMPLETE}: {why or 'result not readable'}"}]
        counts = gate["counts"]
        by_release = {r: w for r, w in by_release.items() if (counts.get(r) or 0) > 0}
        for release in counts:
            if (counts.get(release) or 0) > 0:
                by_release.setdefault(release, {"release": release, "phase": None})  # no window read
    sections, missing = [], []
    for release in sorted(by_release, key=_version_key):
        w = by_release[release]
        phase = w.get("phase")
        label = f"{release} release section"
        if phase in SKIP_PHASES:
            continue
        if phase == PHASE_LOOKUP_FAILED:
            missing.append({"what": label, "why": PHASE_LOOKUP_FAILED})
        elif phase == NOT_SCHEDULED:
            missing.append({"what": label, "why": _not_scheduled_reason(w)})
        elif phase == REFINEMENT:
            if not isinstance(w.get("refinement_sprint"), dict):
                missing.append({"what": label, "why": REFINEMENT_NOT_PLACED})
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
    expected = gate is None or any((n or 0) > 0 for n in gate["counts"].values())
    if not sections and not missing and expected:
        missing.append({"what": "Release section", "why": "no release in progress"})
    return sections, missing


# ---------------------------------------------------------------- sprint health


def _days(sprint, today):
    """(days elapsed, days remaining, total days): the shared ``sprint_day_counts`` definition."""
    return sprint_day_counts(sprint.get("start"), sprint.get("end"), today)


def _owner(issue):
    """An issue's assignee display name, or None when unassigned."""
    return rules.person(issue.get("assignee_display"))


def sprint_health(sprint_issues, sprint, today, mode, velocity=None, availability=None,
                  skipped_signals=(), links_complete=True, goal=None, velocity_reason=None):
    """Team-level sprint health for capacity or mid-sprint mode.

    ``sprint_issues`` is ``transform-sprint-issues.py`` output; ``sprint`` is
    scrum-context's ``current_sprint`` (with ``week``); ``velocity`` is
    ``team_velocity`` from ``compute-velocity.py``. Returns figures, ``risks``,
    ``items`` (``_scrum_rules`` findings, one per issue and problem: capacity →
    unpointed, no epic; mid-sprint → explicit blocks only, i.e. open "is
    blocked by" links and ``Blocked`` / ``Parked`` labels) and ``team_items``
    (re-plan / re-scope the sprint, owned by the Scrum Master). In the sprint's
    last week (week 3 or later) mid-sprint mode adds ``sprint_end``: delivered
    SP and %, and likely carry-over (open issues, listed as "carry-over" items).
    ``velocity_reason`` is :func:`usable_velocity`'s reason when velocity was
    rejected; it is shown as the reason capacity is left out. ``goal`` is an
    optional ``{"rating", "note"}`` shown in the sprint reply.
    """
    issues = sprint_issues.get("issues", [])
    skipped = list(skipped_signals)
    out = {"mode": mode, "sprint": sprint.get("name"), "week": sprint.get("week"),
           "committed_sp": sprint_issues.get("total_sp", 0), "issues": len(issues),
           "risks": [], "items": [], "team_items": [], "left_out": [], "goal": goal,
           "links_complete": links_complete}
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
        out["items"] = rules.sprint_findings(issues, "capacity")
        out["unpointed"] = [f["key"] for f in out["items"] if f["check"] == "unpointed"]
        out["no_epic"] = [f["key"] for f in out["items"] if f["check"] == "no_epic"]
        out["ready"] = len({i["key"] for i in issues} - {f["key"] for f in out["items"]})
        if cap["capacity_sp"] is not None and out["committed_sp"] > cap["capacity_sp"]:
            over = round(out["committed_sp"] - cap["capacity_sp"], 1)
            out["risks"].append({"severity": RED, "text": (
                f"Sprint committed {out['committed_sp']} SP vs capacity {cap['capacity_sp']:g} SP "
                f"({cap['label']}): {over:g} SP over")})
            out["team_items"].append(f"re-plan the sprint ({over:g} SP over capacity)")
        elif cap["capacity_sp"] is None:
            out["left_out"].append({"what": "sprint capacity", "why": velocity_reason or "velocity not measured"})
        for label, keys in (("unpointed", out["unpointed"]), ("without an epic", out["no_epic"])):
            if keys:
                out["risks"].append({"severity": YELLOW, "text": f"{len(keys)} sprint issue(s) {label}"})
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
                    out["team_items"].append(f"re-scope the sprint ({left} SP left, burndown behind)")
        out["burndown"] = burndown
        week = _num(sprint.get("week"))
        sprint_end = week is not None and week >= 3
        out["items"] = rules.sprint_findings(issues, "mid-sprint", sprint_end, links_complete)
        blocks = {}
        for f in out["items"]:
            if f["check"] != "carry_over":
                blocks.setdefault(f["key"], []).append(f["label"])
        out["blockers"] = [{"key": k, "reasons": v} for k, v in blocks.items()]
        open_issues = [i for i in issues if i.get("status") not in DONE_STATUSES_SPRINT]
        out["ready"] = len(open_issues) - len(blocks)
        if sprint_end:
            out["sprint_end"] = {"delivered_sp": done, "total_sp": total,
                                 "delivered_pct": round(done / total * 100) if total else None,
                                 "carry_over_issues": len(open_issues),
                                 "carry_over_sp": sum(i.get("sp") or 0 for i in open_issues)}
        if not links_complete:
            out["left_out"].append({"what": "blocked-by links",
                                    "why": "issue link details incomplete (get_jira_issue)"})
        if goal and goal.get("rating") == GREEN and not links_complete:
            # No visible blocker is not proof the goal work is unblocked when links are missing.
            out["goal"] = {"rating": None,
                           "note": f"{goal.get('note') or 'goal on track'} (not rated: blocked-by links not available)"}
        if blocks:
            out["risks"].append({"severity": RED, "text": f"{len(blocks)} sprint issue(s) blocked"})
    out["left_out"].extend({"what": s.split(" (")[0] + " signal", "why": s[s.find("(") + 1:-1]
                            if "(" in s else "not available"} for s in skipped)
    return out


# ---------------------------------------------------------------- release and refinement health

FEATURE_TYPES = ("Feature",)  # the release and refinement checks cover OCPSTRAT Features, not Initiatives


def _window_number(section, name):
    sprint = (section.get("window") or {}).get(name)
    return sprint.get("number") if isinstance(sprint, dict) else None


def _window_sprint_numbers(section):
    """The development window's sprint numbers in board order, for counting by
    position: scrum-context's ``development_window`` (the board's sprints after
    the refinement sprint through the branch-cut sprint). None when it is not
    known; ``expected_pct`` then counts from the refinement and branch-cut
    anchors."""
    window = section.get("window") or {}
    sprints = window.get("development_window")
    if not isinstance(sprints, list):
        return None
    numbers = [s["number"] for s in sprints if isinstance(s, dict) and isinstance(s.get("number"), int)]
    return numbers or None


def _feature_risks(tag, findings):
    """Risk lines for feature findings: 🔴 for schedule, blocks, missing or
    overdue refinement; 🟡 for the rest."""
    by_check = {}
    for f in findings:
        by_check.setdefault(f["check"], set()).add(f["key"])
    out = []
    for checks, text, severity in (
            (("no_sme",), "have no SME", RED),
            (("no_refinement_spike", "refinement_overdue"), "with a missing or overdue refinement spike", RED),
            (("behind_schedule",), "behind schedule", RED),
            (("blocked", "epic_blocked", "blocked_label", "parked_label"), "blocked", RED)):
        keys = set().union(*(by_check.get(c, set()) for c in checks))
        if keys:
            out.append({"severity": severity, "text": f"{tag}: {len(keys)} feature(s) {text}"})
    red = {"no_sme", "no_refinement_spike", "refinement_overdue", "behind_schedule", "blocked", "epic_blocked",
           "blocked_label", "parked_label", "below_cut_line"}
    rest = {f["key"] for f in findings if f["check"] not in red}
    if rest:
        out.append({"severity": YELLOW, "text": f"{tag}: {len(rest)} feature(s) with other gaps"})
    return out


def rank_order_available(features_fetch):
    """Whether the features fetch (``adapt_fetch`` output) is in PM rank order: a
    complete fetch checked as rank-ordered (``"order": "rank"``) with exactly one
    page in all. Several parts of one page each are complete but give no global
    rank, and the key-ordered fetch used when the features do not fit on one page
    is not rank order either: :func:`release_health` must leave the cut line out."""
    status = features_fetch.get("fetch") if isinstance(features_fetch, dict) else None
    return (isinstance(status, dict) and status.get("complete") is True and status.get("order") == "rank"
            and status.get("pages") == 1)


def release_health(checks, section, capacity=None, rank_available=True, velocity_reason=None,
                   features=None, epics=None, spikes=None, stories=None, links=None, current_sprint_number=None,
                   links_unread=()):
    """Team-level release health for development, pencils-down and pre-branch-cut.

    Scope vs capacity and the cut line come from ``run-checks.py`` output
    (``checks``). When ``features`` (``transform-features.py``), ``epics``,
    ``spikes`` (computed with the refinement sprint closed), ``stories`` and
    ``links`` ({feature or epic key: open blocker keys}) are given, the feature
    checks of ``_scrum_rules.development_findings`` run: refinement overdue,
    behind schedule (``_scrum_rules.expected_pct`` from the refinement and
    branch-cut sprints and ``current_sprint_number``), blocked, epic blocked,
    Blocked / Parked label, below the cut line, no QA Contact, no Doc Contact,
    epic with no owner. ``epics``, ``spikes`` or ``stories`` None means the
    checks needing them did not run: they are left out, and the header's
    on-track count says what was not checked (``not_checked``).
    ``links=None`` means the block check did not run, and
    ``links_unread`` lists the keys whose links could not be read: both are left
    out, and the header's on-track count says the blocks were not checked.
    ``capacity`` is :func:`release_capacity` output, for its
    label. ``rank_available=False`` leaves the cut line out, since key order is
    not PM rank: pass :func:`rank_order_available` of the features fetch.
    ``velocity_reason`` is shown when velocity is not available.
    """
    meta = checks.get("meta", {})
    scope, cap = meta.get("total_remaining_sp"), meta.get("total_capacity_sp")
    tag = section["release"]
    out = {"kind": RELEASE_HEALTH, "release": tag, "phase": section["phase"],
           "pencils_down": section.get("pencils_down", False),
           "scope_sp": scope, "capacity_sp": cap, "capacity_basis": meta.get("capacity_basis"),
           "capacity_label": (capacity or {}).get("label"),
           "effective_sprints": (capacity or {}).get("effective_sprints"),
           "velocity_reason": velocity_reason, "velocity": meta.get("team_velocity"),
           "remaining_sprints": meta.get("remaining_sprints"), "gap_sp": meta.get("gap_sp"),
           # A count, never an SP estimate (decision 0018).
           "unpointed_items": (meta.get("total_unpointed_stories") or 0) + (meta.get("unpointed_bugs") or 0),
           "below_cut_line": [], "items": [], "checked": None, "clean": None, "expected_pct": None,
           "blocks_unchecked": None, "not_checked": [],
           "risks": [], "left_out": []}
    velocity_ok = meta.get("capacity_basis") == "velocity"
    if not velocity_ok:
        # Roster-sum capacity is an estimate (decision 0018: never estimate).
        out["capacity_sp"] = None
        out["left_out"].append({"what": f"{tag} capacity and cut line", "why": velocity_reason or "velocity not available"})
    elif not rank_available:
        out["left_out"].append({"what": f"{tag} cut line",
                                "why": "features did not fit on one rank-ordered page, so PM rank order is not available"})
    else:
        out["below_cut_line"] = [r["feature_key"] for r in checks.get("cut_line", [])
                                 if r.get("fits") in ("partial", "over")]
    if velocity_ok and scope is not None and cap is not None and scope > cap:
        out["risks"].append({"severity": RED, "text": (
            f"{tag}: scope {scope} SP vs capacity {cap} SP ({out['capacity_label'] or meta.get('capacity_basis')}): "
            f"{scope - cap} SP over")})
    drift = checks.get("process_gaps", {}).get("roster_drift", [])
    out["roster_drift"] = {"people": len(drift), "sp": sum(d.get("assigned_sp", 0) for d in drift)}
    if drift:
        out["left_out"].append({"what": f"{tag}: capacity from {len(drift)} assignee(s) not on the roster",
                                "why": f"their {sum(d.get('assigned_sp', 0) for d in drift)} SP counts as scope, not capacity"})
    if features is None:
        # Cut line only (no feature data): owner is the row's SME.
        out["items"] = [{"key": r["feature_key"], "owner": rules.person(r.get("sme")),
                         "label": rules.LABEL_CUT_LINE, "check": "below_cut_line"}
                        for r in checks.get("cut_line", []) if r.get("feature_key") in out["below_cut_line"]]
    else:
        exact = rules.expected_pct(_window_number(section, "refinement_sprint"),
                                   _window_number(section, "branch_cut_sprint"), current_sprint_number,
                                   _window_sprint_numbers(section), exact=True)
        out["expected_pct"] = None if exact is None else round(exact)  # for display; compared unrounded
        if exact is None:
            out["not_checked"].append("schedule")
            out["left_out"].append({"what": f"{tag} schedule check", "why": "development window not known"})
        # A check whose input was not given did not run: left out, and named in the header.
        if epics is None:
            skipped = (["epic owner"] + (["epic blocks"] if links is not None else [])
                       + (["schedule"] if exact is not None else []))
            out["not_checked"] += ["epics"] + (["schedule"] if exact is not None else [])
            out["left_out"].append({"what": f"{tag} epic checks ({', '.join(skipped)})", "why": "epics not fetched"})
        elif stories is None and exact is not None:
            out["not_checked"].append("schedule")
            out["left_out"].append({"what": f"{tag} schedule check", "why": "epic issues not fetched"})
        if spikes is None:
            out["not_checked"].append("refinement spikes")
            out["left_out"].append({"what": f"{tag} refinement-overdue check", "why": "refinement spikes not fetched"})
        if links is None:
            out["blocks_unchecked"] = "all"
            out["left_out"].append({"what": f"{tag} feature and epic blocks", "why": "issue links not read"})
        elif links_unread:
            out["blocks_unchecked"] = len(links_unread)
            out["left_out"].append({"what": f"{tag} blocks of {', '.join(links_unread)}",
                                    "why": "issue links could not be read"})
        result = rules.development_findings(features, epics, spikes, stories, links, exact,
                                            out["below_cut_line"], FEATURE_TYPES)
        out.update(items=result["findings"], checked=result["checked"], clean=result["clean"])
        out["left_out"] += [dict(x, what=f"{tag} {x['what']}") for x in result["left_out"]]
    out["risks"] += _feature_risks(tag, [f for f in out["items"] if f["check"] != "below_cut_line"])
    if out["below_cut_line"] and not any(r["severity"] == RED and "SP over" in r["text"] for r in out["risks"]):
        out["risks"].append({"severity": RED, "text": f"{tag}: {len(out['below_cut_line'])} feature(s) below the cut line"})
    return out


def refinement_gaps(features, spikes, section, refinement_sprint_closed=False, epics=None, epic_issue_counts=None,
                    week=None):
    """Feature checks for the release in its refinement sprint (``_scrum_rules.refinement_findings``).

    ``features`` is ``transform-features.py`` output for every non-Closed
    feature targeting the release (any status); ``spikes`` is
    ``transform-spikes.py`` output; ``epics`` is ``transform-epics.py`` output
    (None leaves the epic checks out); ``epic_issue_counts`` maps epic key →
    issue count (None leaves "empty epic" out). Owners: the SME, the QE Lead
    for test plan spikes; features with no SME only count as "no SME".
    Only Features are checked (:data:`FEATURE_TYPES`). In the refinement
    sprint's first ``week`` "no refinement spike" is expected: it becomes one
    count (``spike_needed``), not owner items, and never a risk.
    """
    tag = section["release"]
    result = rules.refinement_findings(features, epics, spikes, epic_issue_counts, refinement_sprint_closed,
                                       FEATURE_TYPES)
    items, spike_needed = result["findings"], 0
    if week == 1:
        spike_needed = len({f["key"] for f in items if f["check"] == "no_refinement_spike"})
        items = [f for f in items if f["check"] != "no_refinement_spike"]
    out = {"kind": REFINEMENT, "release": tag, "phase": section["phase"],
           "refinement_sprint": _window_number(section, "refinement_sprint"),
           "items": items, "spike_needed": spike_needed, "checked": result["checked"], "clean": result["clean"],
           "risks": _feature_risks(tag, items),
           "left_out": [dict(x, what=f"{tag} {x['what']}") for x in result["left_out"]]}
    if epics is None:
        out["left_out"].append({"what": f"{tag} epic checks", "why": "epics not fetched"})
    elif epic_issue_counts is None:
        out["left_out"].append({"what": f"{tag} empty-epic check", "why": "epic issues not fetched"})
    return out

# ---------------------------------------------------------------- epic Target Version and QA Contact

EPIC_FIELDS = "epic-fields"
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
       (``unassigned_jira`` counts as none): "no QA Contact" when the parent
       has one, "no QA Contact, feature has none", or "no QA Contact, feature
       could not be read" (decision 0024; never the parent's QA Contact name).
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
            # Decision 0024: note when the parent (feature) has none too. Never its QA Contact's name.
            note = ("" if parent and _qa_contact(parent) else ", feature has none" if parent
                    else ", feature could not be read")
            out["checks"]["no_qa_contact"].append(dict(base, detail=f"no QA Contact{note}"))
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
    unchecked = {k for keys in skipped.values() for k in keys}  # parent checks skipped
    out["clean"] = out["epics_checked"] - len(set(by_key) | unchecked)
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


# ---------------------------------------------------------------- report

OWNER_LINE_LIMIT = 10  # owner lines per reply
LINE_CHARS = 200  # longest owner line
PEOPLE_NAMES = 8  # names on the top post's third line


def mention(owner, mentions):
    """Slack mention for an owner when mapped (``<@U…>``), else the plain name."""
    if not owner:
        return "no owner set"
    key = normalize_name(owner)
    return next((v for k, v in (mentions or {}).items() if normalize_name(k) == key), None) or owner


def _entries(findings, team_owner, team_items=(), new_keys=None):
    """Findings → {owner: [entry]}, one entry per key with its labels in order.

    Owner None goes to the Scrum Master (``team_owner``), else stays None
    ("no owner set"). Team items (``team_items`` texts) are owned by the Scrum
    Master and listed first on that line.
    """
    groups = {}
    for text in team_items:
        groups.setdefault(team_owner, []).append({"key": None, "labels": [text], "new": False})
    index = {}
    for f in findings:
        owner = f.get("owner") or team_owner
        entry = index.get((owner, f["key"]))
        if entry is None:
            entry = {"key": f["key"], "labels": [], "new": bool(new_keys and f["key"] in new_keys)}
            index[(owner, f["key"])] = entry
            groups.setdefault(owner, []).append(entry)
        if f["label"] not in entry["labels"]:
            entry["labels"].append(f["label"])
    for owner, items in groups.items():
        items.sort(key=lambda e: (e["key"] is not None, -len(e["labels"]), _key_number(e["key"] or "")))
    return groups


SEGMENT_KEYS = 5  # keys shown for one shared label, then "(+N)"


def _item_text(entry, labels=None):
    labels = entry["labels"] if labels is None else labels
    if entry["key"] is None:
        return ", ".join(labels)
    return f"{'🆕 ' if entry.get('new') else ''}{entry['key']} ({', '.join(labels)})"


def _key_list(entries, limit=SEGMENT_KEYS):
    """``OCPSTRAT-3331, 4055, 4057 (+2)``: a key repeating the previous key's
    project is shortened to its number; keys past ``limit`` become "(+N)"."""
    parts, prev = [], None
    for e in entries[:limit]:
        project, _, num = e["key"].rpartition("-")
        parts.append(("🆕 " if e.get("new") else "") + (num if project and project == prev else e["key"]))
        prev = project
    more = len(entries) - limit
    return ", ".join(parts) + (f" (+{more})" if more > 0 else "")


def _segments(entries):
    """An owner's entries as ``(kind, payload, problems)`` segments: team items
    first; then each label shared by several keys as "label for KEY, KEY";
    then each key's other labels as "KEY (label, label)". Returns
    ``(segments, compressed)``."""
    keyed = [e for e in entries if e["key"] is not None]
    by_label = {}
    for e in keyed:
        for label in e["labels"]:
            by_label.setdefault(label, []).append(e)
    shared = sorted((label for label, es in by_label.items() if len(es) > 1), key=lambda label: -len(by_label[label]))
    segs = [("text", ", ".join(e["labels"]), len(e["labels"])) for e in entries if e["key"] is None]
    for label in shared:
        es = sorted(by_label[label], key=lambda e: _key_number(e["key"]))
        segs.append(("text", f"{label} for {_key_list(es)}", len(es)))
    for e in keyed:
        rest = [label for label in e["labels"] if label not in shared]
        if rest:
            segs.append(("item", dict(e, labels=rest), len(rest)))
    return segs, bool(shared)


def _owner_line(who, entries):
    """``• who: KEY (label, label), KEY (label)``, at most ``LINE_CHARS`` long.

    A label several of the owner's keys share is written once: "label for
    KEY, KEY, KEY (+N)" (segments then separated by "; "). What does not fit
    ends as "+N more problem(s)".
    """
    head = f"• {who}: "
    segs, compressed = _segments(entries)
    sep = "; " if compressed else ", "
    texts = [_item_text(p) if kind == "item" else p for kind, p, _ in segs]
    parts = []
    for n, text in enumerate(texts):
        rest = sum(c for _, _, c in segs[n + 1:])
        tail = f" +{rest} more problem(s)" if rest else ""
        if len(head + sep.join(parts + [text]) + tail) <= LINE_CHARS:
            parts.append(text)
            continue
        dropped = sum(c for _, _, c in segs[n:])
        if parts:
            return head + sep.join(parts) + f" +{dropped} more problem(s)"
        kind, payload, count = segs[0]
        if kind == "item":  # one key with many labels: keep its first labels
            for k in range(count - 1, 0, -1):
                text = _item_text(payload, payload["labels"][:k])
                more = f" +{dropped - k} more problem(s)"
                if len(head + text + more) <= LINE_CHARS:
                    return head + text + more
        more = f" +{dropped - 1} more problem(s)" if dropped > 1 else ""
        return (head + text)[:LINE_CHARS - len(more) - 1] + "…" + more
    return head + sep.join(parts)


def _owner_block(groups, mentions, limit=OWNER_LINE_LIMIT):
    """``(lines, overflow keys, owners in order)``: the owner of team items
    (re-plan / re-scope the sprint) first, then owners with the most items, at
    most ``limit`` lines; the rest's keys overflow."""
    owners = sorted(groups, key=lambda o: (not any(e["key"] is None for e in groups[o]), -len(groups[o]),
                                           o is None, str(o or "").casefold()))
    lines = [_owner_line(mention(o, mentions), groups[o]) for o in owners[:limit]]
    overflow = [e["key"] for o in owners[limit:] for e in groups[o] if e["key"]]
    return lines, overflow, owners


def _shown(owners, label, limit=OWNER_LINE_LIMIT):
    return f"; {limit} of {len(owners)} {label} shown" if len(owners) > limit else ""


def _no_sme_line(keys, who):
    """``• Leads, assign SMEs (who): N feature(s) have no SME: KEY, KEY (+N)``, within ``LINE_CHARS``."""
    head = f"• Leads, assign SMEs{f' ({who})' if who else ''}: {len(keys)} feature(s) have no SME: "
    entries = [{"key": k} for k in keys]
    for limit in range(len(entries), 0, -1):
        line = head + _key_list(entries, limit)
        if len(line) <= LINE_CHARS:
            return line
    return head + f"(+{len(keys)})"


def _sprint_header(s, owners):
    head = f"*Sprint health: {s['sprint']}* (week {s['week']}, {s['mode']}): "
    if s["mode"] == "capacity":
        cap = s.get("capacity") or {}
        cap_txt = (f"{cap['capacity_sp']:g} SP ({cap['label']})" if cap.get("capacity_sp") is not None
                   else "not available")
        head += (f"committed {s['committed_sp']} SP in {s['issues']} issue{'s' if s['issues'] != 1 else ''}; "
                 f"capacity {cap_txt}; ✓ {s.get('ready', 0)} ready")
    else:
        b = s.get("burndown", {})
        head += (f"{b['done_sp']} of {b['total_sp']} SP done, {b['expected_done_sp']:g} expected ({b['status']})"
                 if "expected_done_sp" in b else f"{s['committed_sp']} SP committed ({b.get('status', 'burndown not available')})")
        end = s.get("sprint_end")
        if end:
            pct = f" ({end['delivered_pct']}%)" if end["delivered_pct"] is not None else ""
            head += (f"; delivered {end['delivered_sp']} of {end['total_sp']} SP{pct}; likely carry-over "
                     f"{end['carry_over_issues']} issue{'s' if end['carry_over_issues'] != 1 else ''} "
                     f"({end['carry_over_sp']} SP)")
        else:
            head += (f"; ✓ {s.get('ready', 0)} open issues not blocked" if s.get("links_complete", True)
                     else "; blocked-by links not checked")
    goal = s.get("goal")
    if goal:
        head += f"; goal: {(goal.get('rating') + ' ') if goal.get('rating') else ''}{goal.get('note') or ''}".rstrip()
    return head + _shown(owners, "owners")


def _sprint_figures(s):
    """Short sprint figures for the "No risks" top line."""
    if s["mode"] == "capacity":
        cap = s.get("capacity") or {}
        cap_txt = (f"{cap['capacity_sp']:g} SP ({cap['label']})" if cap.get("capacity_sp") is not None
                   else "not available")
        return f"committed {s['committed_sp']} SP in {s['issues']} issue{'s' if s['issues'] != 1 else ''}; capacity {cap_txt}"
    b = s.get("burndown", {})
    if "expected_done_sp" in b:
        return f"{b['done_sp']} of {b['total_sp']} SP done, {b['expected_done_sp']:g} expected ({b['status']})"
    return f"{s['committed_sp']} SP committed ({b.get('status', 'burndown not available')})"


def _section_header(sec):
    """One section's header text (no owner lines)."""
    if sec.get("kind") == REFINEMENT:
        where = f"refinement sprint {sec['refinement_sprint']}" if sec.get("refinement_sprint") else "refinement"
        return f"*{sec['release']} refinement* ({where}): {sec['checked']} feature(s) checked; ✓ {sec['clean']} ready"
    phase = "pencils-down sprint" if sec.get("pencils_down") else sec["phase"]
    head = f"*{sec['release']} release health* ({phase}): "
    if sec.get("capacity_sp") is not None:
        head += f"scope {sec['scope_sp']} SP vs capacity {sec['capacity_sp']} SP"
    else:
        head += f"scope {sec['scope_sp']} SP; capacity not available ({sec.get('velocity_reason') or 'velocity not measured'})"
    if sec.get("unpointed_items"):
        head += f"; {sec['unpointed_items']} unpointed items not counted"
    if sec.get("expected_pct") is not None:
        head += f"; {sec['expected_pct']}% of development expected done"
    if sec.get("checked") is not None:
        not_checked = [c for c in sec.get("not_checked") or [] if c != "schedule"]
        if "schedule" in (sec.get("not_checked") or []):
            head += "; schedule not checked"  # no on-track count without the schedule check
        else:
            head += f"; ✓ {sec['clean']} of {sec['checked']} feature(s) on track"
        unchecked = sec.get("blocks_unchecked")
        if unchecked:
            head += " (blocks not checked)" if unchecked == "all" else f" (blocks not checked for {unchecked})"
        if not_checked:
            head += f" ({', '.join(not_checked)} not checked)"
    return head


def _epic_header(sec, owners):
    counts = [f"{len(sec['checks'][cid])} {label}" for cid, label in EPIC_CHECKS + (NO_ASSIGNEE,)
              if sec["checks"].get(cid)]
    n = sec["flagged"]
    if not n:  # "✓ all complete" only when epics were actually checked
        return f"*Epic fields* ✓ all {sec['epics_checked']} active epics complete" if sec["epics_checked"] else None
    head = (f"*Epic fields* ({n} epic{'s' if n != 1 else ''}: {', '.join(counts)}; "
            f"✓ {sec.get('clean', sec['epics_checked'] - n)} complete{_shown(owners, 'owners')})")
    if sec["checks"].get("target_version_differs"):
        head += f'. Key: "{DRIFT_LABEL}" = {DRIFT_QUESTION}'
    return head


def _left_out_line(entries):
    return "Left out: " + "; ".join(f"{x['what']}: {x['why']}" for x in entries)


def build_report(sprint, sections, missing=(), mentions=None, team_owner=None, epic_gaps=None):
    """Assemble the Slack post: a 3-line top post and at most 3 thread replies.

    ``sprint`` is :func:`sprint_health` output (or None); ``sections`` are
    :func:`release_health` / :func:`refinement_gaps` outputs; ``missing`` lists
    ``{"what", "why"}`` left out; ``mentions`` maps a display name to a Slack
    mention; ``team_owner`` is the Scrum Master: owner of team items and of
    anything with no owner. ``epic_gaps`` is :func:`epic_field_gaps` output,
    informational: it never changes the top post.

    Top post: status and where we are; the biggest risk (or "No risks this
    week"); "N people have items: …" (sprint and release owners, at most 8
    names). Replies, in order: sprint health; release or refinement health (all
    sections in one reply; none when no release is in progress); epic fields.
    Each reply is one header line and at most 10 owner lines
    (``• owner: KEY (label, label)``, at most 200 characters); the keys of
    further owners and everything left out go on ONE "Left out:" line at the
    end of the last reply. Returns ``{"top_post", "top_post_text", "replies",
    "action_count", "people"}``. ``top_post`` is the list of the three lines;
    ``top_post_text`` (one string) and each entry of ``replies`` (one string per
    thread reply) are the Slack message texts to post.
    """
    left_out_sprint = []
    if sprint and sprint.get("skipped"):
        left_out_sprint, sprint = sprint.get("left_out", []), None
    parts = ([sprint] if sprint else []) + list(sections)
    if any("risks" not in p for p in parts):
        raise ValueError("build_report needs sprint_health / release_health / refinement_gaps results, "
                         "not select_sections descriptors")
    risks = [r for p in parts for r in p["risks"]]
    left_out = list(missing) + left_out_sprint + [x for p in parts for x in p.get("left_out", [])]
    status = RED if any(r["severity"] == RED for r in risks) else YELLOW if risks or left_out else GREEN
    replies, overflow_notes, people = [], [], {}

    def count_people(groups):
        for owner, entries in groups.items():
            if owner:
                people[owner] = people.get(owner, 0) + len(entries)

    epic_no_owner = set()
    epic_lines, epic_left_out, epic_ok, epic_overflow = [], [], None, []
    if epic_gaps is not None:
        try:
            if epic_gaps.get("kind") != EPIC_FIELDS:
                raise TypeError("not an epic_field_gaps result")
            # Validate the whole section before it changes anything else in the report.
            omitted = [{"what": str(x["what"]), "why": str(x["why"])} for x in epic_gaps["left_out"]]
            no_owner = set()
            lines, groups, overflow, owners, ok = [], {}, [], [], None
            if not epic_gaps["flagged"]:
                if epic_gaps.get("complete"):  # "✓ all complete" only after a complete run
                    ok = _epic_header(epic_gaps, [])  # shown only to carry the Left out line
            else:
                findings = [{"key": e["key"], "owner": e["owner"], "label": label, "check": "epic"}
                            for e in epic_gaps["epics"] for label in e["problems"]]
                new_keys = {e["key"] for e in epic_gaps["epics"] if e.get("new")}
                groups = _entries(findings, team_owner, new_keys=new_keys)
                lines, overflow, owners = _owner_block(groups, mentions)
                # Shown means on an owner line within the cap (the label itself may still be cut).
                no_owner = {e["key"] for o in owners[:OWNER_LINE_LIMIT] for e in groups[o]
                            if NO_ASSIGNEE[1] in e["labels"]}
                lines = [_epic_header(epic_gaps, owners)] + lines
        except (AttributeError, KeyError, TypeError, ValueError):
            epic_left_out = [{"what": _EPIC_SECTION, "why": "result not readable"}]
        else:
            epic_lines, epic_ok, epic_left_out, epic_no_owner = lines, ok, omitted, no_owner
            count_people(groups)  # everyone tagged counts, epic fields included
            if overflow:
                epic_overflow = [{"what": f"{len(owners) - OWNER_LINE_LIMIT} more owner(s) in epic fields",
                                  "why": ", ".join(overflow)}]

    if sprint:
        groups = _entries(sprint["items"], team_owner, sprint.get("team_items", ()))
        lines, overflow, owners = _owner_block(groups, mentions)
        replies.append([_sprint_header(sprint, owners)] + lines)
        count_people(groups)
        if overflow:
            overflow_notes.append({"what": f"{len(owners) - OWNER_LINE_LIMIT} more owner(s) in sprint health",
                                   "why": ", ".join(overflow)})
    if sections:
        # Not repeated across replies: an epic's missing owner is shown once, in epic fields.
        findings = [f for sec in sections for f in sec["items"]
                    if not (f["check"] == "epic_no_owner" and f.get("epic") in epic_no_owner)]
        specials = []
        no_sme = sorted({f["key"] for f in findings if f["check"] == "no_sme"}, key=_key_number)
        if no_sme:  # one line for the leads, tagging the Scrum Master
            specials.append(_no_sme_line(no_sme, mention(team_owner, mentions) if team_owner else None))
            if team_owner:
                people[team_owner] = people.get(team_owner, 0) + 1
        needed = sum(sec.get("spike_needed") or 0 for sec in sections)
        if needed:
            specials.append(f"• {needed} feature(s) need a refinement spike this sprint")
        groups = _entries([f for f in findings if f["check"] != "no_sme"], team_owner)
        limit = OWNER_LINE_LIMIT - len(specials)
        lines, overflow, owners = _owner_block(groups, mentions, limit)
        replies.append([" · ".join(_section_header(sec) for sec in sections) + _shown(owners, "owners", limit)]
                       + specials + lines)
        count_people(groups)
        if overflow:
            overflow_notes.append({"what": f"{len(owners) - limit} more owner(s) in release health",
                                   "why": ", ".join(overflow)})
    all_left_out = left_out + overflow_notes + epic_overflow + epic_left_out
    if epic_lines:
        replies.append(epic_lines)
    elif epic_ok and all_left_out:
        replies.append([epic_ok])
    if all_left_out:
        if replies:
            replies[-1] = replies[-1] + [_left_out_line(all_left_out)]
        else:
            replies.append([_left_out_line(all_left_out)])

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
    ranked = sorted(people, key=lambda p: (-people[p], p.casefold()))
    names = ", ".join(mention(p, mentions) for p in ranked[:PEOPLE_NAMES])
    more = f", +{len(ranked) - PEOPLE_NAMES} more" if len(ranked) > PEOPLE_NAMES else ""
    top.append(f"{len(ranked)} {'person has' if len(ranked) == 1 else 'people have'} items"
               + (f": {names}{more}" if ranked else ""))
    action_count = sum(len(p.get("items", [])) + len(p.get("team_items", [])) for p in parts)
    return {"top_post": top, "top_post_text": "\n".join(top), "replies": ["\n".join(r) for r in replies],
            "action_count": action_count, "people": ranked}
