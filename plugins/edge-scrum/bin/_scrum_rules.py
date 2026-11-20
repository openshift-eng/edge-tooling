"""Edge scrum feature and sprint checks, shared by the Claude Code skills and the chai report.

Pure functions over the edge-scrum transform outputs (``features.json``,
``epics.json``, ``spikes.json``, ``stories.json``, ``sprint_issues.json``) and
issue links; nothing here fetches data or formats text for Slack. Each check
returns findings ``{"key", "owner", "label", "check"}``: ``key`` is the
feature (or sprint issue) the finding belongs to, ``owner`` the display name
responsible for it (None when nobody is set: callers decide who owns it),
``label`` a full-word description, ``check`` a stable id.

Owners: a feature's SME (``customfield_10475``), never its assignee; the QE
Lead (the feature's QA Contact) for test plan spikes; the assignee for sprint
issues. A feature with no SME gets only the "no SME" finding (owner None): its
other problems are not reported until an SME is set.
Blocks are explicit only: an open "is blocked by" link, or a ``Blocked`` /
``Parked`` label. There is no stale (no recent update) check.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import DONE_STATUSES_SPRINT, is_sprint_done  # noqa: E402

# Placeholder values the transforms and chai-bot use for "nobody".
_NOBODY = {"", "none", "unassigned", "unassigned_jira", "field not configured", "null"}
# Feature statuses that are finished: no schedule check (law 05).
FEATURE_DONE_STATUSES = ("Closed", "Release Pending")
BEHIND_RATIO = 0.75  # behind schedule: % done below 75% of % expected

LABEL_NO_SME = "no SME"
LABEL_NO_QA = "no QA Contact"
LABEL_NO_DOC = "no Doc Contact"
LABEL_UNSIZED = "unsized"
LABEL_NO_EPICS = "no epics"
LABEL_NO_SPIKE = "no refinement spike"
LABEL_SPIKE_OUTSIDE = "spike not in the refinement sprint"
LABEL_OVERDUE = "refinement overdue"
LABEL_TEST_PLAN = "test plan spike missing"
LABEL_BLOCKED = "Blocked label"
LABEL_PARKED = "Parked"
LABEL_CUT_LINE = "below the cut line"
LABEL_UNPOINTED = "unpointed"
LABEL_NO_EPIC = "no epic"
LABEL_CARRY_OVER = "carry-over"


def epic_no_owner(key):
    return f"epic {key} has no owner"


def empty_epic(key):
    return f"empty epic {key}"


def blocked_by(key):
    return f"blocked by {key}"


def epic_blocked_by(epic, key):
    return f"epic {epic} blocked by {key}"


def behind_schedule(done_pct, expected_pct):
    return f"behind schedule: {done_pct}% done vs {expected_pct}% expected"


def person(value):
    """A display name, or None for an empty or placeholder value."""
    if isinstance(value, dict):
        value = value.get("display_name") or value.get("displayName") or value.get("name")
    if not isinstance(value, str) or value.strip().casefold() in _NOBODY:
        return None
    return value.strip()


def feature_owner(feature):
    """The feature's SME, or None when unset (the caller picks a fallback owner)."""
    return person(feature.get("sme"))


def _finding(key, owner, label, check):
    return {"key": key, "owner": owner, "label": label, "check": check}


def _epics_of(feature_key, epics):
    """``(epic records, known)`` for a feature, from ``epics.json``."""
    if not isinstance(epics, dict):
        return [], False
    keys = (epics.get("feature_to_epics") or {}).get(feature_key) or []
    by_key = {e.get("key"): e for e in epics.get("epics") or [] if isinstance(e, dict)}
    return [by_key.get(k, {"key": k}) for k in keys], True


def _skip(f, feature_types):
    """Closed features, and features of another type when ``feature_types`` is given, are not checked."""
    return f.get("status") == "Closed" or bool(feature_types and f.get("type") and f["type"] not in feature_types)


def _no_sme(f):
    """``[the "no SME" finding]`` when the feature has no SME, else ``[]``."""
    return [] if feature_owner(f) else [_finding(f["key"], None, LABEL_NO_SME, "no_sme")]


def _staffing(f, owner, epic_records):
    out = []
    if person(f.get("qa_contact")) is None:
        out.append(_finding(f["key"], owner, LABEL_NO_QA, "no_qa_contact"))
    if person(f.get("docs_approver")) is None:
        out.append(_finding(f["key"], owner, LABEL_NO_DOC, "no_doc_contact"))
    for e in epic_records:
        if person(e.get("assignee")) is None:
            out.append(dict(_finding(f["key"], owner, epic_no_owner(e["key"]), "epic_no_owner"), epic=e["key"]))
    return out


def _spike_findings(f, owner, s, refinement_sprint_closed):
    """Refinement spike rules, first match wins (release-health 7c)."""
    if s is None or s.get("spike_status") == "Closed":
        return []
    if s.get("spike_missing"):
        if s.get("spike_on_epic") or s.get("epics_appear_refined"):
            return []
        return [_finding(f["key"], owner, LABEL_NO_SPIKE, "no_refinement_spike")]
    if refinement_sprint_closed:
        return [_finding(f["key"], owner, LABEL_OVERDUE, "refinement_overdue")]
    if not s.get("spike_in_ref_sprint"):
        return [_finding(f["key"], owner, LABEL_SPIKE_OUTSIDE, "spike_not_in_refinement_sprint")]
    return []


def refinement_findings(features, epics, spikes, epic_issue_counts=None, refinement_sprint_closed=False,
                        feature_types=None):
    """Checks for the release being refined (every non-Closed feature targeting it).

    Per feature (``feature_types``, e.g. ``("Feature",)``, limits the issue
    types checked): no SME (alone: nothing else is reported for that feature),
    no QA Contact, no Doc Contact, epic with no owner, unsized, no epics, empty epic (``epic_issue_counts`` maps epic key → issue count; None
    leaves the check out), refinement spike missing / not in the refinement
    sprint (refinement overdue once that sprint is closed), and test plan spike
    missing (owner: the QE Lead). Returns ``{"findings", "checked", "clean",
    "left_out"}``; a feature whose data cannot be read is left out, never raised.
    """
    out = {"findings": [], "checked": 0, "clean": 0, "left_out": []}
    spike_map = (spikes or {}).get("spike_map") or {} if isinstance(spikes, dict) else {}
    for f in (features or {}).get("features") or [] if isinstance(features, dict) else []:
        try:
            if _skip(f, feature_types):
                continue
            owner = feature_owner(f)
            found = _no_sme(f)
            if not found:
                found = _refinement_checks(f, owner, epics, spike_map, epic_issue_counts,
                                           refinement_sprint_closed)
        except (AttributeError, KeyError, TypeError, ValueError):
            out["left_out"].append({"what": f"feature {f.get('key') if isinstance(f, dict) else '?'}",
                                    "why": "data not readable"})
            continue
        out["checked"] += 1  # only features that could be read
        out["findings"] += found
        out["clean"] += not found
    return out


def _refinement_checks(f, owner, epics, spike_map, epic_issue_counts, refinement_sprint_closed):
    """The refinement checks of a feature that has an SME."""
    epic_records, known = _epics_of(f["key"], epics)
    found = _staffing(f, owner, epic_records)
    if f.get("size") in (None, "", "Unsized"):
        found.append(_finding(f["key"], owner, LABEL_UNSIZED, "unsized"))
    if known and not epic_records:
        found.append(_finding(f["key"], owner, LABEL_NO_EPICS, "no_epics"))
    if epic_issue_counts is not None:
        found += [_finding(f["key"], owner, empty_epic(e["key"]), "empty_epic")
                  for e in epic_records if not epic_issue_counts.get(e["key"])]
    s = spike_map.get(f["key"])
    found += _spike_findings(f, owner, s, refinement_sprint_closed)
    if s and not s.get("test_plan_check_skipped") and s.get("test_plan_spike_missing"):
        found.append(_finding(f["key"], person(f.get("qa_contact")), LABEL_TEST_PLAN, "test_plan_spike_missing"))
    return found


def expected_pct(refinement_number, branch_cut_number, current_number, sprint_numbers=None, exact=False):
    """% of the development window expected done before the current sprint.

    Development sprints = the board's sprints after the refinement sprint up to
    and including the branch-cut sprint (``sprint_numbers``: the window's
    sprint numbers, in board order; counted by position, so a gap in the
    numbering changes nothing). Without ``sprint_numbers``, consecutive numbers
    are assumed. Expected % = development sprints finished before the current
    sprint ÷ development sprints × 100, rounded (``exact=True``: not rounded,
    for comparisons). None when the window is unknown or empty.
    """
    try:
        ref, bc, cur = int(refinement_number), int(branch_cut_number), int(current_number)
        dev = sorted({int(n) for n in sprint_numbers if ref < int(n) <= bc}) if sprint_numbers else \
            list(range(ref + 1, bc + 1))
    except (TypeError, ValueError):
        return None
    if not dev:
        return None
    pct = sum(1 for n in dev if n < cur) / len(dev) * 100
    return pct if exact else round(pct)


def done_pct(epic_keys, stories):
    """% done of the issues in ``epic_keys``: done SP ÷ total SP × 100 (law 07
    done states); by issue count when none is pointed; None with no issues.
    Not rounded: compare first, round only for display."""
    issues = [s for s in stories or [] if isinstance(s, dict) and s.get("epic_key") in set(epic_keys)]
    if not issues:
        return None
    total = sum(s.get("sp") or 0 for s in issues)
    if total:
        return sum(s.get("sp") or 0 for s in issues if is_sprint_done(s)) / total * 100
    return sum(1 for s in issues if is_sprint_done(s)) / len(issues) * 100


def development_findings(features, epics, spikes, stories=None, links=None, expected=None, cut_line=(),
                         feature_types=None):
    """Checks for a release in development, pencils-down or pre-branch-cut.

    Per non-Closed feature (``feature_types`` limits the issue types; a
    feature with no SME gets only "no SME"): refinement overdue (refinement spike still open),
    behind schedule (``done_pct`` of its epics' issues below ``BEHIND_RATIO`` ×
    ``expected``; finished features skipped), blocked by an open "is blocked
    by" issue (``links`` maps a feature or epic key → open blocker keys), epic
    blocked, ``Blocked`` / ``Parked`` label, below the cut line (``cut_line``
    keys), and the staffing checks (no QA Contact, no Doc Contact, epic with no
    owner). Returns ``{"findings", "checked", "clean", "left_out"}``.
    """
    out = {"findings": [], "checked": 0, "clean": 0, "left_out": []}
    spike_map = (spikes or {}).get("spike_map") or {} if isinstance(spikes, dict) else {}
    links = links if isinstance(links, dict) else {}
    cut = set(cut_line or ())
    story_list = (stories or {}).get("stories") if isinstance(stories, dict) else stories
    for f in (features or {}).get("features") or [] if isinstance(features, dict) else []:
        try:
            if _skip(f, feature_types):
                continue
            key, owner = f["key"], feature_owner(f)
            found = _no_sme(f) or _development_checks(f, key, owner, epics, spike_map, story_list, links,
                                                       expected, cut)
        except (AttributeError, KeyError, TypeError, ValueError):
            out["left_out"].append({"what": f"feature {f.get('key') if isinstance(f, dict) else '?'}",
                                    "why": "data not readable"})
            continue
        out["checked"] += 1  # only features that could be read
        out["findings"] += found
        out["clean"] += not found
    return out


def _development_checks(f, key, owner, epics, spike_map, story_list, links, expected, cut):
    """The development-phase checks of a feature that has an SME."""
    epic_records, _ = _epics_of(key, epics)
    found = _spike_findings(f, owner, spike_map.get(key), True) if spike_map.get(key, {}).get(
        "spike_overdue") else []
    if expected and f.get("status") not in FEATURE_DONE_STATUSES and story_list is not None:
        pct = done_pct([e["key"] for e in epic_records], story_list)
        # Compare exact % (done and expected); round only for display (a tie is not behind).
        threshold = expected * BEHIND_RATIO
        # An exact tie is not behind, even when floating point lands a hair above it.
        if pct is not None and pct < threshold and not math.isclose(pct, threshold):
            found.append(_finding(key, owner, behind_schedule(round(pct), round(expected)), "behind_schedule"))
    found += [_finding(key, owner, blocked_by(b), "blocked") for b in links.get(key) or []]
    found += [_finding(key, owner, epic_blocked_by(e["key"], b), "epic_blocked")
              for e in epic_records for b in links.get(e["key"]) or []]
    labels = f.get("labels") or []
    if "Blocked" in labels:
        found.append(_finding(key, owner, LABEL_BLOCKED, "blocked_label"))
    if "Parked" in labels:
        found.append(_finding(key, owner, LABEL_PARKED, "parked_label"))
    if key in cut:
        found.append(_finding(key, owner, LABEL_CUT_LINE, "below_cut_line"))
    found += _staffing(f, owner, epic_records)
    return found


def sprint_findings(issues, mode, sprint_end=False, links_complete=True):
    """Sprint issue checks. Capacity mode: unpointed, no epic. Mid-sprint:
    explicit blocks on open issues (open "is blocked by" links when
    ``links_complete``, ``Blocked`` / ``Parked`` labels). ``sprint_end`` (the
    sprint's last week) adds "carry-over" for every open issue. Owner: the
    assignee. Returns findings; unreadable issues are skipped."""
    out = []
    for i in issues or []:
        if not isinstance(i, dict) or not isinstance(i.get("key"), str):
            continue
        key, owner = i["key"], person(i.get("assignee_display"))
        if mode == "capacity":
            if not i.get("sp"):
                out.append(_finding(key, owner, LABEL_UNPOINTED, "unpointed"))
            if i.get("epic_key") == "No Epic":
                out.append(_finding(key, owner, LABEL_NO_EPIC, "no_epic"))
            continue
        if i.get("status") in DONE_STATUSES_SPRINT:
            continue
        if links_complete:
            out += [_finding(key, owner, blocked_by(b), "blocked") for b in i.get("blocked_by") or []]
        labels = i.get("labels") or []
        if "Blocked" in labels:
            out.append(_finding(key, owner, LABEL_BLOCKED, "blocked_label"))
        if "Parked" in labels:
            out.append(_finding(key, owner, LABEL_PARKED, "parked_label"))
        if sprint_end:
            out.append(_finding(key, owner, LABEL_CARRY_OVER, "carry_over"))
    return out
