"""Optional velocity cache for the chai weekly report: closed-sprint aggregates.

Opt-in. The weekly report uses it only when a dedicated persona memory namespace
has been provisioned for it (``VELOCITY_STORE`` in the weekly SKILL). With no
store, or a store that cannot be read, velocity is computed cold: every closed
sprint is fetched, exactly as without this module. The store only saves the
fetches of sprints already measured (see below for edits after a sprint closed).

The store holds **aggregate figures only**, never issue keys, summaries or people.
One entry per closed sprint and roster version::

    key    edge-scrum/velocity/v1/<board id>/<sprint id>/<roster digest>
    value  {"schema": 1, "board": "<id>", "sprint_id": "<id>", "sprint_end": "<YYYY-MM-DD>",
            "state": "closed", "roster": "<digest>", "complete": true,
            "done_team_sp": <number>, "team_done_issues": <int>,
            "skipped_no_sprint_data": <int>, "unmatched_done_sp": <number>}

An entry is created only from a complete fetch of a sprint the board lists as
closed, and never changed afterwards (create-only): a run that finds a key
already present leaves it. Two runs creating the same key at once compute it from
the same closed sprint and roster. An entry is used only when it is valid for
this schema, board, sprint (id and end date) and roster; anything else is fetched
again. Only the sprints with no valid entry are fetched, including a sprint that
closed since the last run. A sprint with done team issues lacking sprint data
(velocity not usable) is never stored, so it is read again once the data is fixed.

Entries are not refreshed: an edit to a closed sprint after its entry was created
is not seen (an admin clears the entry to refresh it). Bump ``SCHEMA`` whenever
compute-velocity or the sprint transform counts differently, so every older entry
is read again.
"""

import hashlib
import json
import math
import numbers

SCHEMA = 1
KEY_PREFIX = "edge-scrum/velocity/v1"
FIGURES = ("done_team_sp", "team_done_issues", "skipped_no_sprint_data", "unmatched_done_sp")
_COUNTS = ("team_done_issues", "skipped_no_sprint_data")


def roster_digest(roster):
    """A short digest of the roster members compute-velocity matches owners against.
    Any change to the members (added, removed, renamed) gives a new digest, so the
    entries computed with the old roster are not used."""
    members = [m for m in (roster or {}).get("members") or [] if isinstance(m, dict)]
    canonical = sorted(json.dumps(m, sort_keys=True, default=str) for m in members)
    return hashlib.sha256(json.dumps(canonical).encode("utf-8")).hexdigest()[:16]


def entry_key(board_id, sprint_id, digest):
    """The store key of one closed sprint's entry for one roster version."""
    return f"{KEY_PREFIX}/{board_id}/{sprint_id}/{digest}"


def _day(value):
    text = value.isoformat() if hasattr(value, "isoformat") else str(value or "")
    return text[:10] or None


def _figure(name, value):
    """``value`` when it is a finite, non-negative number (a whole one for a count), else None."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        if not math.isfinite(value) or value < 0:
            return None
    except OverflowError:  # an int no float can hold
        return None
    if name in _COUNTS and int(value) != value:
        return None
    return value


def make_entry(board_id, sprint, figures, digest, complete):
    """The entry to create for one sprint, or None when it must not be stored: the
    fetch was not complete, the board does not list the sprint as closed, a figure is
    not a finite, non-negative number, or done team issues lacked sprint data (velocity not
    usable, ``chai_report.usable_velocity``). ``figures`` is that sprint's row of
    compute-velocity output."""
    if not complete or str((sprint or {}).get("state") or "").lower() != "closed" or not sprint.get("id"):
        return None
    values = {name: _figure(name, (figures or {}).get(name)) for name in FIGURES}
    if any(v is None for v in values.values()) or values["skipped_no_sprint_data"]:
        return None
    return {"schema": SCHEMA, "board": str(board_id), "sprint_id": str(sprint["id"]),
            "sprint_end": _day(sprint.get("end")), "state": "closed", "roster": digest, "complete": True,
            **values}


def valid_entry(entry, board_id, sprint, digest):
    """True only for a complete closed-sprint entry of this schema, board, sprint (id
    and end date) and roster, with every figure a finite, non-negative number."""
    if not isinstance(entry, dict):
        return False
    expected = {"schema": SCHEMA, "board": str(board_id), "sprint_id": str(sprint.get("id")),
                "sprint_end": _day(sprint.get("end")), "state": "closed", "roster": digest, "complete": True}
    if any(entry.get(k) != v for k, v in expected.items()):
        return False
    return all(_figure(name, entry.get(name)) is not None for name in FIGURES)


def plan(closed_sprints, stored, board_id, digest):
    """``(hits, to_fetch)`` for the velocity sprints (``closed_sprints``, oldest first).

    ``stored`` maps store keys to what the store returned for them, or is None when
    no store is configured or it could not be read (then every sprint is fetched).
    ``hits`` maps a sprint id to its valid entry; ``to_fetch`` lists the sprints with
    no valid entry, in order. A valid entry is always used: the latest closed sprint
    is not read again when its entry is valid.
    """
    hits, to_fetch = {}, []
    for sprint in closed_sprints:
        entry = (stored or {}).get(entry_key(board_id, sprint.get("id"), digest)) if stored is not None else None
        if valid_entry(entry, board_id, sprint, digest):
            hits[str(sprint["id"])] = entry
        else:
            to_fetch.append(sprint)
    return hits, to_fetch


def combine(closed_sprints, hits, fresh):
    """compute-velocity output over ``closed_sprints``: the stored figures of the
    ``hits`` and compute-velocity output ``fresh`` over the sprints fetched this run.
    The same team velocity, sprint count and skipped count as compute-velocity over
    all of them (``unmatched_owners`` names are not kept in the store). None when a
    sprint has neither."""
    fresh_rows = {str(row.get("sprint_id")): row for row in (fresh or {}).get("sprints") or []}
    rows = []
    for sprint in closed_sprints:
        sid = str(sprint.get("id"))
        source = hits.get(sid) or fresh_rows.get(sid)
        if source is None:
            return None
        rows.append({"sprint_id": sid, "name": sprint.get("name"),
                     **{name: source.get(name) for name in FIGURES}})
    count = len(rows)
    return {"team_velocity": round(sum(r["done_team_sp"] for r in rows) / count, 1) if count else None,
            "sprint_count": count,
            "skipped_no_sprint_data": sum(r["skipped_no_sprint_data"] for r in rows),
            "unmatched_done_sp": sum(r["unmatched_done_sp"] for r in rows),
            "sprints": rows}


def creates(closed_sprints, fresh, complete_ids, stored, board_id, digest):
    """The entries to create: ``{key: entry}`` for the sprints fetched this run whose
    fetch was complete (``complete_ids``), only where the store has no entry at that
    key (create-only; an existing entry, valid or not, is never overwritten). Empty
    when no store is configured or it could not be read (``stored`` is None)."""
    if stored is None:
        return {}
    fresh_rows = {str(row.get("sprint_id")): row for row in (fresh or {}).get("sprints") or []}
    out = {}
    for sprint in closed_sprints:
        sid = str(sprint.get("id"))
        key = entry_key(board_id, sid, digest)
        if sid not in fresh_rows or stored.get(key) is not None:  # present: never overwritten
            continue
        entry = make_entry(board_id, sprint, fresh_rows[sid], digest, sid in {str(c) for c in complete_ids})
        if entry:
            out[key] = entry
    return out
