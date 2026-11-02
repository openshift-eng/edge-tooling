#!/usr/bin/env python3
"""Sprint and release-cycle context for the chai-bot ``scrum-context`` skill.

Answers "where are we in the sprint and release cycle?" from data the caller has
already fetched (board sprints, Product Pages milestones, calendar events, and
anonymized absences). Everything here is a pure function with no network access,
so it runs unchanged inside chai-bot's ``sandbox_exec``. It can be imported, or
run as a CLI::

    scrum_context.py --input context_in.json --output context_out.json

See ``plugins/edge-scrum/chai/scrum-context/SKILL.md`` for the input contract.

Rules (see the SKILL for the reasoning):

* Sprint dates are compared as calendar dates (UTC date part of the timestamp).
* Today's sprint is the board sprint with state "active"; without one, the sprint
  starting on or before today and ending after it (a sprint's first day is in
  that sprint).
* Milestone mapping only: a date exactly on a sprint boundary belongs to the
  sprint **ending** that day.
* Refinement sprint: the sprint starting on "<X.Y> Development Branch opens", or
  the first sprint starting within the 7 days after it. When the release has no
  such milestone, the previous release's "Master Branch Open for <X.Y>" is used
  instead, mapped to the first sprint starting on or after it.
* Pencils-down sprint: the last sprint ending on or before "Pencils Down…".
* Branch-cut sprint: the sprint containing "Master Branch Open for <next>".
* Milestone names match by prefix, case-insensitively, after dropping a leading
  version ("5.1 ", "OpenShift 5.1 ").
* A milestone after the last board sprint maps to a sprint projected at the
  board's cadence; projected sprints carry ``"projected": true`` and no id.
* Anything without a source is "not scheduled yet" / "not available".
* A source item (sprint, milestone, event, absence) with a malformed date is
  skipped and reported in ``warnings`` without the raw value. A malformed
  ``today`` raises.
"""

import argparse
import datetime as dt
import json
import re
import sys

NOT_SCHEDULED = "not scheduled yet"
NOT_AVAILABLE = "not available"
ROSTER_NOT_CONFIGURED = "not available (roster not configured)"
INVALID_RELEASE = "not available (invalid release; expected X.Y)"

PHASE_NOT_STARTED = "not started"
PHASE_REFINEMENT = "refinement"
PHASE_DEVELOPMENT = "development"
PHASE_PENCILS_DOWN = "pencils-down sprint"
PHASE_PRE_BRANCH_CUT = "pre-branch-cut"
PHASE_FINISHED = "finished"
IN_PROGRESS_PHASES = (PHASE_REFINEMENT, PHASE_DEVELOPMENT, PHASE_PENCILS_DOWN,
                      PHASE_PRE_BRANCH_CUT)

CEREMONIES = ("Sprint Planning", "Mid-Sprint Feature Readback", "Sprint Demos",
              "Retrospective")
# Demos and retro close a sprint, so they are looked up in (start, end] and the
# latest one wins; planning and readback open or run inside it, so they use
# [start, end) and the earliest wins. A boundary day's closing ceremonies belong
# to the sprint ending that day, and its planning to the sprint starting then.
_CLOSING = {"Sprint Demos", "Retrospective"}

MAX_PROJECTED_SPRINTS = 12
LATE_START_DAYS = 7

_VERSION_RE = re.compile(r"^(?:openshift[\s-]*)?\d+\.\d+\s+")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CANCELLED_RE = re.compile(r"^\s*(cancel{1,2}ed|canceled)\b", re.IGNORECASE)


# ---------------------------------------------------------------- dates


def to_date(value):
    """Parse 'YYYY-MM-DD' or an ISO timestamp into a date (None stays None)."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise ValueError("expected a YYYY-MM-DD date")
    return dt.date.fromisoformat(value[:10])


def source_date(value):
    """Non-raising date parse for source data: ``(date_or_None, ok)``.

    Missing values give ``(None, True)``; malformed ones give ``(None, False)``.
    """
    if value is None or value == "":
        return None, True
    try:
        return to_date(value), True
    except (ValueError, TypeError):
        return None, False


def strict_today(value):
    """Parse ``today``; raise ValueError when it is provided but malformed."""
    if value is None or value == "":
        return dt.date.today()
    try:
        return to_date(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("today must be a YYYY-MM-DD date") from exc


def _iso(d):
    """ISO 'YYYY-MM-DD' string for a date, or None."""
    return d.isoformat() if d else None


# ---------------------------------------------------------------- versions


_RELEASE_RE = re.compile(r"^\d+\.\d+$")


def is_valid_release(version):
    """True for an 'X.Y' release string such as '5.1'."""
    return isinstance(version, str) and bool(_RELEASE_RE.match(version.strip()))


def parse_version(version):
    """Split an 'X.Y' release into an (X, Y) tuple of ints for ordering."""
    major, minor = str(version).strip().split(".")[:2]
    return int(major), int(minor)


def previous_version(version, schedules):
    """The highest version in ``schedules`` lower than ``version`` (or None)."""
    target = parse_version(version)
    lower = [v for v in schedules if parse_version(v) < target]
    return max(lower, key=parse_version) if lower else None


def next_version(version, schedules):
    """The lowest version in ``schedules`` higher than ``version`` (or None)."""
    target = parse_version(version)
    higher = [v for v in schedules if parse_version(v) > target]
    return min(higher, key=parse_version) if higher else None


# ---------------------------------------------------------------- sprints


def _sprint_number(name):
    """Trailing number of a sprint name ('… Sprint 296' -> 296), or None."""
    match = re.search(r"(\d+)\s*$", name or "")
    return int(match.group(1)) if match else None


def board_sprints(raw_sprints, board_id=None, warnings=None):
    """Normalize the board's own sprints, sorted by start date.

    Sprints marked as coming from another origin board are skipped: an explicit
    ``origin_board`` / ``originBoardId`` / ``origin_board_id`` that differs from
    ``board_id``, or ``origin_board: true`` (the chai-bot tool's marker). Sprints
    without dates are skipped too; a malformed date also adds a warning.
    """
    out = []
    for s in raw_sprints or []:
        origin = s.get("originBoardId", s.get("origin_board_id"))
        marker = s.get("origin_board")
        if marker is True:
            continue
        if marker not in (None, False, True):
            origin = marker
        if origin is not None and board_id is not None and str(origin) != str(board_id):
            continue
        start, start_ok = source_date(s.get("start_date") or s.get("startDate") or s.get("start"))
        end, end_ok = source_date(s.get("end_date") or s.get("endDate") or s.get("end"))
        if not (start_ok and end_ok):
            if warnings is not None:
                which = "start" if not start_ok else "end"
                warnings.append(f"skipped sprint '{s.get('name') or s.get('id')}': bad {which} date")
            continue
        if not start or not end:
            continue
        out.append({
            "id": str(s["id"]) if s.get("id") is not None else None,
            "name": s.get("name"),
            "number": _sprint_number(s.get("name")),
            "start": start,
            "end": end,
            "state": s.get("state"),
            "projected": False,
        })
    out.sort(key=lambda s: (s["start"], s["end"]))
    return out


def project_sprints(sprints, until):
    """Extend ``sprints`` at the last sprint's cadence until one starts on/after ``until``.

    Projected sprints are numbered on from the last sprint, have no id, and are
    marked ``projected``. At most MAX_PROJECTED_SPRINTS are added (about 36 weeks
    at a 3-week cadence); past that, dates map to no sprint, so the current sprint
    is "not available" and milestones are "not scheduled yet".
    """
    if not sprints or until is None:
        return list(sprints)
    out = list(sprints)
    last = out[-1]
    length = last["end"] - last["start"]
    if length.days <= 0:
        return out
    added = 0
    while out[-1]["start"] < until and added < MAX_PROJECTED_SPRINTS:
        prev = out[-1]
        number = prev["number"] + 1 if prev["number"] is not None else None
        name = None
        if prev["name"] and prev["number"] is not None:
            name = re.sub(r"\d+\s*$", str(number), prev["name"])
        out.append({"id": None, "name": name, "number": number,
                    "start": prev["end"], "end": prev["end"] + length,
                    "state": "projected", "projected": True})
        added += 1
    return out


def sprint_for_date(sprints, day):
    """The sprint containing ``day``; a boundary day belongs to the sprint ending then."""
    day = to_date(day)
    for s in sprints:
        if s["start"] < day <= s["end"]:
            return s
    for s in sprints:  # only reachable for the very first sprint's start day
        if s["start"] == day:
            return s
    return None


def sprint_on(sprints, day):
    """The sprint ``day`` falls in for "today" purposes (no boundary rule).

    A sprint runs from its start day up to the day before its end day, so a
    sprint's first day belongs to it. The end day of the last sprint still maps
    to that sprint.
    """
    day = to_date(day)
    for s in sprints:
        if s["start"] <= day < s["end"]:
            return s
    for s in reversed(sprints):
        if s["end"] == day:
            return s
    return None


def current_sprint(sprints, day):
    """Today's sprint: the board sprint with state "active", else by date.

    When several sprints are active, the one containing ``day`` wins, else the
    latest-starting one. Without an active sprint, a date past the last board
    sprint maps to a projected sprint at the board's cadence.
    """
    day = to_date(day)
    active = [s for s in sprints if str(s.get("state") or "").lower() == "active"]
    if active:
        return sprint_on(active, day) or active[-1]
    return sprint_on(project_sprints(sprints, day), day)


def sprint_week(sprint, day):
    """Week 1-3 of ``day`` within ``sprint`` (the end day counts as the last week)."""
    day = to_date(day)
    weeks = max(1, -(-(sprint["end"] - sprint["start"]).days // 7))
    return min(weeks, max(1, (day - sprint["start"]).days // 7 + 1))


def _index(sprints, sprint):
    """Position of ``sprint`` in ``sprints``, matched by identity or by dates."""
    for i, s in enumerate(sprints):
        if s is sprint or (s["start"] == sprint["start"] and s["end"] == sprint["end"]):
            return i
    return None


# ---------------------------------------------------------------- milestones


def _normalize_name(name):
    """Lowercase, collapse spaces, and drop a leading version from a milestone name."""
    text = re.sub(r"\s+", " ", (name or "").strip().lower())
    return _VERSION_RE.sub("", text)


def _milestone_date(m):
    """A milestone's point in time: its start for a range, else its date."""
    return to_date(m.get("date") or m.get("start") or m.get("date_start")
                   or m.get("end") or m.get("date_finish"))


def find_milestone(milestones, prefix):
    """Earliest-dated milestone whose normalized name starts with ``prefix``."""
    prefix = _normalize_name(prefix)
    hits = [(d, m) for m in milestones or []
            if _normalize_name(m.get("name")).startswith(prefix)
            and (d := _milestone_date(m)) is not None]
    if not hits:
        return None
    hits.sort(key=lambda h: h[0])
    return hits[0]


def release_milestones(version, schedules):
    """Source dates for a release's three anchors, or None per missing anchor.

    Returns ``{"dev_branch_opens", "dev_branch_source", "pencils_down",
    "master_branch_open", "next_version"}``.
    """
    own = schedules.get(version) or []
    result = {"dev_branch_opens": None, "dev_branch_source": None,
              "pencils_down": None, "master_branch_open": None, "next_version": None}

    hit = find_milestone(own, "development branch opens")
    if hit:
        result["dev_branch_opens"] = hit[0]
        result["dev_branch_source"] = "development branch opens"
    else:
        prev = previous_version(version, schedules)
        if prev:
            hit = find_milestone(schedules[prev], f"master branch open for {version}")
            if hit:
                result["dev_branch_opens"] = hit[0]
                result["dev_branch_source"] = f"{prev} master branch open for {version}"

    hit = find_milestone(own, "pencils down")
    if hit:
        result["pencils_down"] = hit[0]

    hit = find_milestone(own, "master branch open for")
    if hit:
        result["master_branch_open"] = hit[0]
        match = re.search(r"master branch open for\s+(\d+\.\d+)",
                          _normalize_name(hit[1].get("name")))
        result["next_version"] = match.group(1) if match else None
    return result


def map_release(version, schedules, sprints):
    """Map a release's milestones onto sprints.

    Returns a dict with ``refinement``, ``development`` (list), ``pencils_down``
    and ``branch_cut`` sprints (or None), the milestone dates, and the extended
    sprint list used (including projected sprints).
    """
    ms = release_milestones(version, schedules)
    anchors = [d for d in (ms["dev_branch_opens"], ms["pencils_down"],
                           ms["master_branch_open"]) if d]
    sprints = project_sprints(sprints, max(anchors)) if anchors else list(sprints)

    refinement = None
    if ms["dev_branch_opens"]:
        day = ms["dev_branch_opens"]
        if ms["dev_branch_source"] == "development branch opens":
            # The sprint starting that day, or one starting up to 7 days late.
            latest = day + dt.timedelta(days=LATE_START_DAYS)
            refinement = next((s for s in sprints if day <= s["start"] <= latest), None)
        else:
            refinement = next((s for s in sprints if s["start"] >= day), None)

    pencils_down = None
    if ms["pencils_down"]:
        ending = [s for s in sprints if s["end"] <= ms["pencils_down"]]
        pencils_down = ending[-1] if ending else None

    branch_cut = sprint_for_date(sprints, ms["master_branch_open"]) \
        if ms["master_branch_open"] else None

    development = []
    if refinement and pencils_down:
        lo, hi = _index(sprints, refinement), _index(sprints, pencils_down)
        development = sprints[lo + 1:hi]

    return {"version": version, "milestones": ms, "sprints": sprints,
            "refinement": refinement, "development": development,
            "pencils_down": pencils_down, "branch_cut": branch_cut}


def _today_anchor(mapping, day, current):
    """Start date of today's sprint, used to compare against release sprints."""
    if current is None:
        current = sprint_on(mapping["sprints"], day)
    return current["start"] if current else day


def phase_on(mapping, day, current=None):
    """Release phase on ``day`` for a ``map_release`` result.

    ``current`` is today's sprint (see ``current_sprint``); when omitted, the
    sprint containing ``day`` is used, with a sprint's first day belonging to it.
    """
    day = to_date(day)
    ref, pd = mapping["refinement"], mapping["pencils_down"]
    master = mapping["milestones"]["master_branch_open"]
    if ref is None:
        return NOT_SCHEDULED
    anchor = _today_anchor(mapping, day, current)
    if anchor < ref["start"]:
        return PHASE_NOT_STARTED
    if anchor == ref["start"]:
        return PHASE_REFINEMENT
    if pd is None:
        if master is None or day < master:
            return PHASE_DEVELOPMENT
        return PHASE_FINISHED
    if anchor < pd["start"]:
        return PHASE_DEVELOPMENT
    if anchor == pd["start"]:
        return PHASE_PENCILS_DOWN
    if master is None or day < master:
        return PHASE_PRE_BRANCH_CUT
    return PHASE_FINISHED


def dev_sprints_remaining(mapping, day, current=None):
    """Development sprints left up to and including the pencils-down sprint.

    Counts today's sprint when it is a development or pencils-down sprint; the
    refinement sprint is not a development sprint. Returns NOT_SCHEDULED when
    the refinement or pencils-down sprint is unknown.
    """
    if mapping["refinement"] is None or mapping["pencils_down"] is None:
        return NOT_SCHEDULED
    anchor = _today_anchor(mapping, to_date(day), current)
    window = mapping["development"] + [mapping["pencils_down"]]
    return sum(1 for s in window if s["start"] >= anchor)


# ---------------------------------------------------------------- ceremonies


def _event_label(event):
    """The event's ceremony label: ``ceremony``, else the legacy ``title``."""
    return event.get("ceremony") or event.get("title") or ""


def matched_ceremony(event):
    """The configured ceremony an event matches, or None."""
    label = _event_label(event).lower()
    return next((c for c in CEREMONIES if c.lower() in label), None)


def is_cancelled(event):
    """Cancelled by ``status``, or by a "Cancelled:" prefix on a legacy ``title``.

    Events built per the SKILL carry only the ``ceremony`` label, so for them
    ``status`` is the only signal.
    """
    status = str(event.get("status") or "").lower()
    return status in ("cancelled", "canceled") or bool(_CANCELLED_RE.match(event.get("title") or ""))


def ceremony_dates(sprint, events):
    """Date of each ceremony within ``sprint`` (None when missing or cancelled)."""
    out = {}
    for ceremony in CEREMONIES:
        days = sorted(
            d for e in events or []
            if matched_ceremony(e) == ceremony and not is_cancelled(e)
            and (d := to_date(e.get("start") or e.get("date"))) is not None
            and (sprint["start"] < d <= sprint["end"] if ceremony in _CLOSING
                 else sprint["start"] <= d < sprint["end"]))
        if not days:
            out[ceremony] = None
        else:
            out[ceremony] = _iso(days[-1] if ceremony in _CLOSING else days[0])
    return out


def next_ceremonies(events, today):
    """Next non-cancelled occurrence of each ceremony on or after ``today``."""
    today = to_date(today)
    out = {}
    for ceremony in CEREMONIES:
        days = sorted(
            d for e in events or []
            if matched_ceremony(e) == ceremony and not is_cancelled(e)
            and (d := to_date(e.get("start") or e.get("date"))) is not None and d >= today)
        out[ceremony] = _iso(days[0]) if days else None
    return out


# ---------------------------------------------------------------- availability


def working_days(start, end):
    """Mon-Fri dates in [start, end) — a sprint's end day is the next sprint's first day."""
    start, end = to_date(start), to_date(end)
    days, day = [], start
    while day < end:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def availability(absences, engineer_count, start, end):
    """Team availability = 1 - person-days off / person-days, rounded to 2 decimals.

    ``absences`` is a list of ``{person_id, start, end}`` (inclusive dates) for
    roster engineers only. Overlapping entries count once per person-day and
    weekends are excluded. Returns ROSTER_NOT_CONFIGURED when ``engineer_count``
    is missing or zero.
    """
    if not engineer_count:
        return ROSTER_NOT_CONFIGURED
    days = set(working_days(start, end))
    if not days:
        return NOT_AVAILABLE
    off = set()
    for a in absences or []:
        a_start, a_end = to_date(a.get("start")), to_date(a.get("end") or a.get("start"))
        if a_start is None or a.get("person_id") is None:
            continue
        day = a_start
        while day <= a_end:
            if day in days:
                off.add((str(a["person_id"]), day))
            day += dt.timedelta(days=1)
    total = engineer_count * len(days)
    return round(max(0.0, 1 - len(off) / total), 2)


# ---------------------------------------------------------------- assembly


def _sprint_out(s):
    """Output form of a sprint (ISO dates), or NOT_SCHEDULED when missing."""
    if s is None:
        return NOT_SCHEDULED
    return {"name": s["name"], "number": s["number"], "id": s["id"],
            "start": _iso(s["start"]), "end": _iso(s["end"]),
            "projected": s["projected"]}


def release_window(version, schedules, sprints, today, current=None):
    """A release's window: anchor sprints, dev sprints left, phase, and milestones."""
    if not is_valid_release(version):
        return {"release": version, "phase": INVALID_RELEASE}
    version = version.strip()
    if not schedules.get(version) and release_milestones(version, schedules)["dev_branch_opens"] is None:
        return {"release": version, "phase": NOT_SCHEDULED,
                "refinement_sprint": NOT_SCHEDULED, "development_sprints": NOT_SCHEDULED,
                "pencils_down_sprint": NOT_SCHEDULED, "branch_cut_sprint": NOT_SCHEDULED,
                "development_sprints_remaining": NOT_SCHEDULED, "milestones": {}}
    m = map_release(version, schedules, sprints)
    ms = m["milestones"]
    return {
        "release": version,
        "phase": phase_on(m, today, current),
        "refinement_sprint": _sprint_out(m["refinement"]),
        "development_sprints": ([_sprint_out(s) for s in m["development"]]
                                if m["refinement"] and m["pencils_down"] else NOT_SCHEDULED),
        "pencils_down_sprint": _sprint_out(m["pencils_down"]),
        "branch_cut_sprint": _sprint_out(m["branch_cut"]),
        "development_sprints_remaining": dev_sprints_remaining(m, today, current),
        "milestones": {
            "development_branch_opens": _iso(ms["dev_branch_opens"]) or NOT_SCHEDULED,
            "development_branch_source": ms["dev_branch_source"],
            "pencils_down": _iso(ms["pencils_down"]) or NOT_SCHEDULED,
            "master_branch_open": _iso(ms["master_branch_open"]) or NOT_SCHEDULED,
            "next_release": ms["next_version"],
        },
    }


def candidate_releases(schedules):
    """Releases with a schedule, plus any named by "Master Branch Open for <X.Y>"."""
    versions = set(schedules)
    for milestones in schedules.values():
        for m in milestones or []:
            match = re.search(r"master branch open for\s+(\d+\.\d+)",
                              _normalize_name(m.get("name")))
            if match:
                versions.add(match.group(1))
    return sorted(versions, key=parse_version)


def releases_in_progress(schedules, sprints, today, current=None):
    """Releases whose phase on ``today`` is refinement through pre-branch-cut.

    Includes a release with an empty or missing schedule when its refinement
    sprint can be inferred from the previous release's "Master Branch Open".
    """
    out = []
    for version in candidate_releases(schedules):
        window = release_window(version, schedules, sprints, today, current)
        if window["phase"] in IN_PROGRESS_PHASES:
            out.append(window)
    return out


_MILESTONE_DATE_KEYS = ("date", "start", "date_start", "end", "date_finish")


def clean_schedules(raw, warnings):
    """Drop non-'X.Y' releases and milestones with a malformed date (with a warning)."""
    out = {}
    for key, milestones in (raw or {}).items():
        if not is_valid_release(str(key)):
            continue  # ignored rather than crashing version ordering
        version, kept = str(key).strip(), []
        for m in milestones or []:
            if all(source_date(m.get(k))[1] for k in _MILESTONE_DATE_KEYS):
                kept.append(m)
            else:
                warnings.append(f"skipped milestone '{m.get('name')}' ({version}): bad date")
        out[version] = kept
    return out


def clean_events(raw, warnings):
    """Drop events with a malformed date. Warnings name only the matched ceremony."""
    if raw is None:
        return None
    out = []
    for e in raw:
        if source_date(e.get("start") or e.get("date"))[1]:
            out.append(e)
        else:
            label = matched_ceremony(e) or "unmatched"
            warnings.append(f"skipped calendar event ({label}): bad date")
    return out


def clean_absences(raw, warnings):
    """Drop absence entries with a malformed date (warning gives the entry number)."""
    if raw is None:
        return None
    out = []
    for i, a in enumerate(raw, 1):
        if source_date(a.get("start"))[1] and source_date(a.get("end"))[1]:
            out.append(a)
        else:
            warnings.append(f"skipped absence entry {i}: bad date")
    return out


def build_context(data):
    """Assemble the full scrum-context result from the CLI/sandbox input dict.

    Raises ValueError when ``today`` is provided but malformed. Source items
    with malformed dates are skipped and listed in ``warnings``.
    """
    today = strict_today(data.get("today"))
    warnings = []
    schedules = clean_schedules(data.get("schedules"), warnings)
    events = clean_events(data.get("events"), warnings)
    absences = clean_absences(data.get("absences"), warnings)
    sprints = board_sprints(data.get("sprints"), data.get("board_id"), warnings)

    result = {"today": _iso(today)}

    current = current_sprint(sprints, today) if sprints else None
    if current:
        result["current_sprint"] = {**_sprint_out(current),
                                    "week": sprint_week(current, today)}
    else:
        result["current_sprint"] = NOT_AVAILABLE

    following = None
    if current:
        ahead = project_sprints(sprints, current["end"])
        following = next((s for s in ahead if s["start"] >= current["end"]), None)
    result["next_sprint"] = _sprint_out(following) if following else NOT_AVAILABLE

    release = data.get("release")
    if release is not None and release != "":
        # Non-strings are rejected: a JSON number would turn 5.10 into 5.1.
        result["release"] = release_window(release, schedules, sprints, today, current)
    elif not schedules:
        result["release_in_progress"] = NOT_AVAILABLE
    else:
        in_progress = releases_in_progress(schedules, sprints, today, current)
        result["release_in_progress"] = (
            [{"release": w["release"], "phase": w["phase"],
              "development_sprints_remaining": w["development_sprints_remaining"]}
             for w in in_progress] or NOT_AVAILABLE)

    if events is None:
        result["next_ceremonies"] = NOT_AVAILABLE
        result["current_sprint_ceremonies"] = NOT_AVAILABLE
    else:
        result["next_ceremonies"] = next_ceremonies(events, today)
        result["current_sprint_ceremonies"] = (ceremony_dates(current, events)
                                               if current else NOT_AVAILABLE)

    if following is None:
        result["next_sprint_availability"] = NOT_AVAILABLE
    elif not data.get("engineer_count"):
        result["next_sprint_availability"] = ROSTER_NOT_CONFIGURED
    elif absences is None:
        result["next_sprint_availability"] = NOT_AVAILABLE
    else:
        result["next_sprint_availability"] = availability(
            absences, data["engineer_count"], following["start"], following["end"])
    result["warnings"] = warnings
    return result


def main(argv=None):
    """CLI entry point: read the input JSON, build the context, write the output JSON."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input", required=True, help="input JSON path ('-' for stdin)")
    parser.add_argument("--output", required=True, help="output JSON path ('-' for stdout)")
    args = parser.parse_args(argv)
    if args.input == "-":
        data = json.load(sys.stdin)
    else:
        with open(args.input, encoding="utf-8") as fh:
            data = json.load(fh)
    result = build_context(data)
    if args.output == "-":
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
