#!/usr/bin/env python3
"""Convert chai-bot ``query_jira`` text output into the JSON the edge-scrum transforms read.

chai-bot's Jira tool returns formatted **text**, not JSON, with no total count.
This module parses that text, checks that a key-ordered paged fetch is complete,
and emits issues in the mcp-atlassian ``jira_search`` shape that
``transform-*.py`` and ``compute-velocity.py`` already consume, so those scripts
run unchanged inside chai-bot's ``sandbox_exec``.

Text format (checked against real chai-bot output). An optional preamble
("Create metadata for …", "Invalid extra_fields entries: …") comes first, then
one of two headers::

    Found N issues (showing N):
    Showing the first N matching issues; more match:

then one indented block per issue. A line may hold several ``Label: value``
segments separated by `` | ``; a missing line or segment means empty, and an
unset custom field prints no line at all::

      [KEY-123] Summary text
        Type: Story | Status: Code Review | Priority: Major | Assignee: <name> | Reporter: <name> | Resolution: Done
        Created: 2030-01-02 | Updated: 2030-01-06 | Resolved: 2030-01-06
        Components: <name> | Labels: a, b | Fix Versions: x | Parent: KEY-100
        customfield_10020: <sprint name> (id N, <state>, board B, start <iso>, end <iso>)
        customfield_10028: <story points>
        customfield_<id>: <value>
        issuelinks: (set, but query_jira cannot render this field's structure)
        resolutiondate: <iso>

A truncated page ends with a "... More issues match than the N shown ..." hint.
A query with no results returns ``No issues found for JQL: <jql>``. Output may
be wrapped as ``{"result": "<text>"}``.

Fields are requested by id (story points ``customfield_10028``, sprint
``customfield_10020``, QA Contact ``customfield_10470``, SME
``customfield_10475``, Flagged ``customfield_10021``). Unknown lines are ignored
and counted in a warning. A field listed under "Invalid extra_fields entries"
was not returned: the fetch is incomplete, except for Flagged, which is then a
skipped signal (a missing Flagged line otherwise just means "not flagged").

``query_jira`` cannot render issue links. Links come from ``get_jira_issue``,
whose "Linked Jira Issues" section has lines like
``- is blocked by: KEY (Blocks) | status: <status> | <url>``
(:func:`parse_issue_links`, :func:`attach_issue_links`). A feature's spikes come
from ``issue in linkedIssues("<feature>", "is blocked by") AND issuetype = Spike``
(:func:`attach_linked_spikes`).

People: chai-bot returns display names only (no emails). A display name that
matches the roster (``_jira_transforms.roster_match``) gets the roster username
as its ``email``, so roster-keyed scripts work; an unmatched name is kept as
``name`` so its work still shows up (as roster drift) instead of as unassigned.

Paging recipe (see the SKILLs): ``(<JQL>) ORDER BY key DESC`` with max_results
50, then ``(<JQL>) AND key < <last key> ORDER BY key DESC`` until a page has the
"Found N issues" header or fewer than 50 issues. Jira's ``key <`` compares keys
within one project only, so when a multi-project query needs more than one page,
it is paged one project at a time (``AND project = P``).

CLI::

    chai_jira_adapter.py fetch --manifest m.json [--roster roster.json]
        [--issue-links links.json] [--linked-spikes spikes.json] --output out.json
    chai_jira_adapter.py roster --team-roster team-roster.md --output roster.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import (  # noqa: E402
    build_roster_index,
    normalize_name,
    roster_match,
    write_output,
)

PAGE_SIZE = 50
KEY_PATTERN = r"[A-Z][A-Z0-9_]*-\d+"
_HEADER_RE = re.compile(r"^\s*\[(" + KEY_PATTERN + r")\]\s*(.*?)\s*$")
_KEY_RE = re.compile(r"^(?P<project>[A-Z][A-Z0-9_]*)-(?P<num>\d+)$")
_LABEL_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_ ]{0,40}?)\s*:\s?(.*)$")
_SPRINT_RE = re.compile(
    r"(?P<name>[^()]*?)\s*\(\s*id\s+(?P<id>\d+)\s*,\s*(?P<state>[^,]+?)\s*,\s*"
    r"board\s+(?P<board>[^,]+?)\s*,\s*start\s+(?P<start>[^,]+?)\s*,\s*end\s+(?P<end>[^)]+?)\s*\)")
_FOUND_RE = re.compile(r"^\s*Found\s+(\d+)\s+issues?(?:\s*\(showing\s+(\d+)\))?", re.I)
_MORE_RE = re.compile(r"^\s*Showing the first\s+(\d+)\s+matching issues?.*more match", re.I)
_NO_RESULTS_RE = re.compile(r"^\s*No issues found for JQL:(.*)$", re.S)
_HINT_RE = re.compile(r"^\s*(\.\.\.|…)?\s*More issues match\b", re.I)
_INVALID_FIELDS_RE = re.compile(r"^Invalid extra_fields entries:\s*(.*?)(?:\.\s+Use\b.*)?\.?$", re.I)
_UNKNOWN_FIELDS_RE = re.compile(r"^Unknown fields:\s*(.*?)\.?$", re.I)
_METADATA_RE = re.compile(r"^(Create metadata for\b|Issue types available\b|Requested fields:|\[[^\]]+\]$)", re.I)
_LINKED_HEADER_RE = re.compile(r"linked jira issues", re.I)
_LINKED_LINE_RE = re.compile(
    r"^\s*[-*]\s*(?P<wording>[^:|]+?)\s*:\s*(?P<key>" + KEY_PATTERN + r")\s*"
    r"(?:\((?P<type>[^)]*)\))?\s*(?:\|\s*status\s*:\s*(?P<status>[^|]*?)\s*)?(?:\|.*)?$", re.I)

EMPTY_VALUES = {"", "none", "null", "n/a", "unassigned", "unresolved", "-"}

# Labels on the pipe-separated lines, mapped to output keys.
PIPE_FIELDS = {
    "type": "issue_type", "issue type": "issue_type", "status": "status",
    "priority": "priority", "assignee": "assignee", "reporter": "reporter",
    "resolution": "resolution", "created": "created", "updated": "updated",
    "resolved": "resolved",
}
USER_FIELDS = {"assignee", "customfield_10470", "customfield_10473", "customfield_10475"}
# Custom fields the transforms read.
CUSTOM_FIELDS = {
    "customfield_10020",  # Sprint
    "customfield_10028",  # Story Points
    "customfield_10470",  # QA Contact
    "customfield_10473",  # Docs approver
    "customfield_10475",  # SME
    "customfield_10795",  # T-shirt size
    "customfield_10855",  # Target Version
    "customfield_10014",  # Epic Link (legacy)
    "customfield_10018",  # Parent Link
    "customfield_10021",  # Flagged (no line when unset; skipped signal when rejected)
}
IGNORED_FIELDS = {"issuelinks"}  # query_jira prints "(set, but ... cannot render ...)"
LIST_FIELDS = {"components": "components", "labels": "labels", "fix versions": "fixVersions",
               "fixversions": "fixVersions", "fix version": "fixVersions"}

# Link wording → (type name, inward wording, outward wording). On issue A,
# "is blocked by: B" means B is A's inward issue; "blocks: B" its outward one.
_LINK_PAIRS = [
    ("Blocks", "is blocked by", "blocks"),
    ("Cloners", "is cloned by", "clones"),
    ("Duplicate", "is duplicated by", "duplicates"),
    ("Causality", "is caused by", "causes"),
    ("Related", "is related to", "relates to"),
]
LINK_WORDING = {}
for _name, _inward, _outward in _LINK_PAIRS:
    LINK_WORDING[_inward] = (_name, _inward, _outward, "inward_issue")
    LINK_WORDING[_outward] = (_name, _inward, _outward, "outward_issue")

SKIPPED_FLAGGED = "flagged (query_jira rejected customfield_10021)"
FLAGGED_FIELD = "customfield_10021"
SKIPPED_DESCRIPTION = "acceptance criteria (no description in chai-bot's Jira output)"


# ---------------------------------------------------------------- values


def _clean(value):
    """Stripped value, or None for an empty placeholder ("None", "Unassigned", …)."""
    if value is None:
        return None
    text = str(value).strip()
    return None if text.casefold() in EMPTY_VALUES else text


def _split_list(value):
    """Comma-separated list value → list of non-empty items."""
    text = _clean(value)
    if not text:
        return []
    return [item.strip() for item in text.split(",") if _clean(item)]


def _number(value):
    """Story points as a float, or None when empty or not a number."""
    text = _clean(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_sprints(value):
    """``customfield_10020`` text → sprint dicts (one per ``name (id …)`` entry)."""
    out = []
    for m in _SPRINT_RE.finditer(value or ""):
        name = m.group("name").strip().lstrip(",;").strip()
        start, end = _clean(m.group("start")), _clean(m.group("end"))
        out.append({"id": int(m.group("id")), "name": name,
                    "state": m.group("state").strip().lower(),
                    "boardId": _clean(m.group("board")),
                    "startDate": start, "endDate": end})
    return out


def parse_issue_links(text):
    """``get_jira_issue`` output → issuelinks from its "Linked Jira Issues" section.

    Lines look like ``- is blocked by: KEY (Blocks) | status: <status> | <url>``.
    Returns ``(links, found)``; ``found`` is False when the section is missing.
    """
    links, found, inside = [], False, False
    for line in (text or "").splitlines():
        if _LINKED_HEADER_RE.search(line) and not _LINKED_LINE_RE.match(line):
            found, inside = True, True
            continue
        if not inside:
            continue
        if not line.strip():
            continue
        m = _LINKED_LINE_RE.match(line)
        if not m:
            if line.lstrip().startswith(("-", "*")):
                continue  # a list line we cannot read; skip it
            break  # next section
        wording = re.sub(r"\s+", " ", m.group("wording").strip().lower())
        type_name = _clean(m.group("type"))
        if wording in LINK_WORDING:
            name, inward, outward, side = LINK_WORDING[wording]
        else:
            name, inward, outward, side = type_name or wording, wording, wording, "outward_issue"
        other = {"key": m.group("key")}
        status = _clean(m.group("status"))
        if status:
            other["status"] = {"name": status}
        links.append({"type": {"name": type_name or name, "inward": inward, "outward": outward},
                      side: other})
    return links, found


# ---------------------------------------------------------------- page parsing


def _split_blocks(text):
    """Split page text into ``(preamble_lines, [(key, summary, lines), ...], hint)``.

    ``hint`` is True when the trailing "... More issues match ..." paging hint is
    present; that line is not part of any issue block.
    """
    preamble, blocks, hint = [], [], False
    for line in (text or "").splitlines():
        if _HINT_RE.match(line):
            hint = True
            continue
        m = _HEADER_RE.match(line)
        if m:
            blocks.append((m.group(1), m.group(2), []))
        elif blocks:
            blocks[-1][2].append(line)
        else:
            preamble.append(line)
    return preamble, blocks, hint


def _segments(line):
    """``Label: value | Label: value`` → label matches; one segment when any part has no label."""
    parts = re.split(r"\s+\|\s+", line.strip())
    matches = [_LABEL_RE.match(p) for p in parts]
    if len(parts) > 1 and all(matches):
        return matches
    m = _LABEL_RE.match(line)
    return [m] if m else [None]


def _parse_block(key, summary, lines, unknown):
    """One issue block → raw field dict (values still text). Unknown labels go to ``unknown``.

    Any line may hold several ``Label: value`` segments separated by `` | ``
    (``Components: X | Labels: a, b | Fix Versions: v | Parent: KEY``).
    """
    raw = {"key": key, "summary": summary}
    for line in lines:
        if not line.strip():
            continue
        for m in _segments(line):
            if m is None:
                unknown.append("(unlabelled)")
                continue
            label, value = m.group(1).strip(), m.group(2)
            low = label.lower()
            if low in PIPE_FIELDS:
                raw[PIPE_FIELDS[low]] = value
            elif low in CUSTOM_FIELDS or low in ("resolutiondate", "parent"):
                raw[low] = value
            elif low in LIST_FIELDS:
                raw[LIST_FIELDS[low]] = value
            elif low == "description":
                raw["description"] = value
            elif low in IGNORED_FIELDS:
                pass  # e.g. "issuelinks: (set, but query_jira cannot render ...)"
            else:
                unknown.append(label)
    return raw


def parse_header(lines):
    """Page header → ``("found", total, shown)``, ``("more", shown, shown)`` or None."""
    for line in lines:
        m = _FOUND_RE.match(line)
        if m:
            total = int(m.group(1))
            return ("found", total, int(m.group(2)) if m.group(2) else total)
        m = _MORE_RE.match(line)
        if m:
            return ("more", int(m.group(1)), int(m.group(1)))
    return None


def parse_preamble(lines):
    """chai-bot notes before the results → ``(rejected_fields, unknown_fields, other_lines)``.

    ``Invalid extra_fields entries: A, B. Use Jira field IDs ...`` lists fields
    query_jira rejected (not returned at all). ``Unknown fields: ...`` comes from
    the create-metadata block and is informational. The rest of that block
    ("Create metadata for ...", "Issue types available ...", "[Feature]",
    "Requested fields: ...") is recognized and ignored.
    """
    rejected, unknown_fields, other = [], [], []
    for line in lines:
        text = line.strip()
        if not text or parse_header([text]):
            continue
        m = _INVALID_FIELDS_RE.match(text)
        if m:
            rejected.extend(f.strip() for f in m.group(1).split(",") if f.strip())
            continue
        m = _UNKNOWN_FIELDS_RE.match(text)
        if m:
            unknown_fields.extend(f.strip() for f in m.group(1).split(",") if f.strip())
            continue
        if _METADATA_RE.match(text):
            continue
        other.append(text)
    return rejected, unknown_fields, other


def unwrap(text):
    """chai-bot tool output → text: ``{"result": "<text>"}`` is unwrapped, bare text kept."""
    if isinstance(text, dict):
        return text.get("result") if isinstance(text.get("result"), str) else None
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except ValueError:
            return text
        if isinstance(data, dict) and isinstance(data.get("result"), str):
            return data["result"]
    return text


def parse_page(text, error=None, page_size=PAGE_SIZE, jql=None):
    """Parse one ``query_jira`` page and check it positively.

    Returns ``{"status": "ok"|"error"|"incomplete", "reason", "final", "blocks",
    "unknown", "rejected", "unknown_fields"}``. A page is ``ok`` only when:

    * non-final: header ``Showing the first N matching issues; more match`` with
      N = ``page_size``, exactly N parsed issues, and the "... More issues match"
      hint;
    * final: header ``Found N issues (showing N)`` with N = the number of parsed
      issues, and no hint.

    * no results: ``No issues found for JQL: <jql>`` where ``<jql>`` equals the
      query that was sent (``jql``, ignoring surrounding whitespace): a complete,
      empty final page.

    Every issue must also carry its ``Type: … | Status: …`` line (chai-bot always
    prints it), so a page cut inside its last issues is caught even when the
    issue count still matches. Anything else is ``incomplete``. ``text`` may be
    wrapped as ``{"result": "<text>"}`` (see :func:`unwrap`).
    """
    if error:
        return {"status": "error", "reason": "query_jira call failed", "final": False,
                "blocks": [], "unknown": [], "rejected": [], "unknown_fields": []}
    text = unwrap(text)
    empty = _NO_RESULTS_RE.match(text or "")
    if empty:
        out = {"status": "ok", "reason": None, "final": True, "blocks": [], "unknown": [],
               "rejected": [], "unknown_fields": []}
        if jql is None:
            out.update(status="incomplete", final=False,
                       reason="'No issues found' page without the sent JQL to compare")
        elif empty.group(1).strip() != str(jql).strip():
            out.update(status="incomplete", final=False,
                       reason="'No issues found' names a different JQL than the query sent")
        return out
    preamble, blocks, hint = _split_blocks(text)
    header = parse_header(preamble)
    rejected, unknown_fields, other = parse_preamble(preamble)
    unknown = ["(preamble)"] * len(other)
    parsed = [_parse_block(k, s, lines, unknown) for k, s, lines in blocks]
    out = {"status": "ok", "reason": None, "final": False, "blocks": parsed, "unknown": unknown,
           "rejected": rejected, "unknown_fields": unknown_fields}

    def incomplete(reason):
        out.update(status="incomplete", reason=reason, final=False)
        return out

    if header is None:
        return incomplete("no 'Found N issues (showing N)' or 'Showing the first N matching issues' header")
    kind, total, shown = header
    count = len(parsed)
    if kind == "more":
        if shown != page_size:
            return incomplete(f"'more match' page shows {shown} issues, expected {page_size} (max_results)")
        if count != shown:
            return incomplete(f"page lists {count} issues but its header says {shown}")
        if not hint:
            return incomplete("full page without the 'More issues match' paging hint")
    else:
        if total != shown:
            return incomplete(f"header says {total} issues but shows {shown}")
        if count != shown:
            return incomplete(f"page lists {count} issues but its header says {shown}")
        if hint:
            return incomplete("'Found N issues' page followed by a 'More issues match' hint")
        out["final"] = True
    cut = [raw["key"] for raw in parsed if "issue_type" not in raw or "status" not in raw]
    if cut:
        return incomplete(f"issue {cut[0]} has no 'Type: … | Status: …' line (page cut off)")
    return out


# ---------------------------------------------------------------- conversion


class PeopleMatcher:
    """Display name → user dict, matched to the roster by display name."""

    def __init__(self, roster=None):
        """Index the roster's contacts (or members) by display name and email."""
        people = (roster or {}).get("contacts") or (roster or {}).get("members") or []
        members = [{"username": p.get("email") or p.get("username"), "display_name": p.get("display_name")}
                   for p in people]
        self.index = build_roster_index({"members": members})
        self.matched = set()
        self.unmatched = set()

    def user(self, value):
        """Display name → user dict with the roster email when matched, else the name; None if empty."""
        name = _clean(value)
        if name is None:
            return None
        member = roster_match(None, name, self.index)
        user = {"display_name": name, "displayName": name}
        if member and member.get("username"):
            user["email"] = member["username"]
            self.matched.add(normalize_name(name))
        else:
            user["name"] = name
            self.unmatched.add(name)
        return user


_PARENT_TYPE_RE = re.compile(r"\b(Epic|Feature|Initiative|Outcome|Story|Task)\b")
_CHILD_OF_EPIC = {"story", "task", "spike", "bug"}


def to_issue(raw, people):
    """Raw text fields → an issue in the mcp-atlassian ``jira_search`` shape."""
    issue = {"key": raw["key"], "summary": raw.get("summary") or ""}
    for field in ("issue_type", "status", "priority", "resolution"):
        value = _clean(raw.get(field))
        issue[field] = {"name": value} if value else None
    if issue["status"] is None:
        issue["status"] = {"name": ""}
    # Dates are left out when missing: the transforms read them with defaults
    # (raw.get("updated", ...)), which a stored None would bypass.
    dates = {"created": _clean(raw.get("created")), "updated": _clean(raw.get("updated")),
             "resolutiondate": _clean(raw.get("resolutiondate")) or _clean(raw.get("resolved"))}
    issue.update({k: v for k, v in dates.items() if v})
    for field in USER_FIELDS:
        if field in raw or field == "assignee":
            issue[field] = people.user(raw.get(field))
    reporter = _clean(raw.get("reporter"))
    if reporter:
        issue["reporter"] = {"display_name": reporter, "displayName": reporter}
    issue["components"] = [{"name": c} for c in _split_list(raw.get("components"))]
    issue["labels"] = _split_list(raw.get("labels"))
    if "fixVersions" in raw:
        issue["fixVersions"] = [{"name": v} for v in _split_list(raw.get("fixVersions"))]
    parent_text = _clean(raw.get("parent"))
    if parent_text:
        key = re.search(KEY_PATTERN, parent_text)
        if key:
            parent = {"key": key.group(0)}
            ptype = _PARENT_TYPE_RE.search(parent_text[key.end():])
            child = (issue["issue_type"] or {}).get("name", "").lower()
            if ptype:
                parent["issue_type"] = {"name": ptype.group(1)}
            elif child in _CHILD_OF_EPIC:
                # A standard issue's native parent is its epic (Jira Cloud hierarchy).
                parent["issue_type"] = {"name": "Epic"}
            issue["parent"] = parent
    if "customfield_10020" in raw:
        issue["customfield_10020"] = parse_sprints(raw["customfield_10020"])
    if "customfield_10028" in raw:
        issue["customfield_10028"] = _number(raw["customfield_10028"])
    for field in ("customfield_10795",):
        if field in raw and _clean(raw[field]):
            issue[field] = {"value": _clean(raw[field])}
    if "customfield_10855" in raw:
        issue["customfield_10855"] = [{"name": v} for v in _split_list(raw["customfield_10855"])]
    for field in ("customfield_10014", "customfield_10018"):
        if field in raw and _clean(raw[field]):
            key = re.search(KEY_PATTERN, raw[field])
            if key:
                issue[field] = key.group(0)
    if "customfield_10021" in raw:
        flag = _clean(raw["customfield_10021"])
        issue["customfield_10021"] = [{"value": flag}] if flag else []
    if "description" in raw:
        issue["description"] = raw["description"] or ""
    issue["issuelinks"] = []
    return issue


def attach_issue_links(adapted, issue_texts):
    """Set ``issuelinks`` from ``get_jira_issue`` output on the matching issues.

    ``issue_texts`` maps key → that issue's ``get_jira_issue`` text (None when the
    call failed). Returns the problems found; a failed call or a missing "Linked
    Jira Issues" section means that issue's blocked signal is incomplete.
    """
    by_key = {i["key"]: i for i in adapted.get("issues", [])}
    problems = []
    for key, text in (issue_texts or {}).items():
        if key not in by_key:
            continue
        if text is None:
            problems.append(f"{key}: get_jira_issue failed")
            continue
        links, found = parse_issue_links(text)
        if not found:
            problems.append(f"{key}: no 'Linked Jira Issues' section")
            continue
        by_key[key]["issuelinks"] = links
    adapted.setdefault("fetch", {}).setdefault("link_problems", []).extend(problems)
    return problems


def attach_linked_spikes(features, spikes_by_feature):
    """Add an "is blocked by" link from each feature to each spike found for it.

    ``spikes_by_feature`` maps feature key → adapted spike issues returned by
    ``issue in linkedIssues("<feature>", "is blocked by") AND issuetype = Spike``.
    The links carry the spike's type, status, summary, assignee and resolution,
    which is what ``transform-features.py`` reads for spike candidates.
    """
    by_key = {i["key"]: i for i in features.get("issues", [])}
    for fkey, spikes in (spikes_by_feature or {}).items():
        feature = by_key.get(fkey)
        if feature is None:
            continue
        have = {(l.get("inward_issue") or {}).get("key") for l in feature["issuelinks"]}
        for spike in spikes or []:
            if spike["key"] in have:
                continue
            other = {"key": spike["key"], "issue_type": spike.get("issue_type") or {"name": "Spike"}}
            for field in ("status", "summary", "assignee", "resolution"):
                if spike.get(field):
                    other[field] = spike[field]
            feature["issuelinks"].append({
                "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
                "inward_issue": other})
            have.add(spike["key"])


def attach_linked_spike_fetches(features, fetches_by_feature):
    """:func:`attach_linked_spikes` from whole ``adapt_fetch`` results, keeping completeness.

    ``fetches_by_feature`` maps feature key → the ``adapt_fetch`` output of that
    feature's ``linkedIssues(...)`` query. An incomplete one is recorded in
    ``features["fetch"]["linked_spike_problems"]`` (feature and reason) and makes
    ``features["fetch"]["complete"]`` False: a missed spike would report a false
    missing-spike gap. Returns the problems.
    """
    problems = []
    for fkey, fetch in (fetches_by_feature or {}).items():
        status = (fetch or {}).get("fetch") or {}
        if not status.get("complete", False):
            why = "; ".join(status.get("problems") or []) or "fetch status missing"
            problems.append(f"{fkey}: linked-spike fetch incomplete ({why})")
    out = features.setdefault("fetch", {})
    out.setdefault("linked_spike_problems", []).extend(problems)
    if problems:
        out["complete"] = False
    attach_linked_spikes(features, {k: (v or {}).get("issues", []) for k, v in (fetches_by_feature or {}).items()})
    return problems


# ---------------------------------------------------------------- paging


def _key_sort(key):
    """Sort key for an issue key: (project, number), so TEST-9 sorts before TEST-10."""
    m = _KEY_RE.match(key or "")
    return (m.group("project"), int(m.group("num"))) if m else (key or "", 0)


def strip_order_by(jql):
    """JQL without a trailing ORDER BY clause."""
    return re.sub(r"\s+ORDER\s+BY\s+.*$", "", jql.strip(), flags=re.I | re.S).strip()


def page_jql(jql, project=None, last_key=None):
    """JQL for the next key-ordered page of ``jql`` (one project at a time)."""
    out = f"({strip_order_by(jql)})"
    if project:
        out += f" AND project = {project}"
    if last_key:
        out += f" AND key < {last_key}"
    return out + " ORDER BY key DESC"


def _load_page_text(page):
    """Text of one manifest page: inline ``text``, else the ``file`` contents, else None."""
    if page.get("text") is not None:
        return page["text"]
    if page.get("file"):
        with open(page["file"], encoding="utf-8") as fh:
            return fh.read()
    return None


def check_part(pages, project=None, order="key"):
    """Completeness problems of one fetch part, given parsed pages in fetch order.

    Key order: every page ok; every page but the last is non-final ("more match"
    header, or a full page with no header); the last page is final ("Found N
    issues" header, or fewer than 50 issues). Across pages, keys must stay in one
    project and strictly descend. Rank order: a single final page.
    """
    if not pages:
        return ["no pages fetched"]
    problems = [f"page {i}: {p['reason']}" for i, p in enumerate(pages, 1) if p["status"] != "ok"]
    if problems:
        return problems
    if order == "rank":
        if len(pages) != 1 or not pages[0]["final"]:
            problems.append("rank-ordered fetch did not fit on one page; rank order not available")
        return problems
    for i, page in enumerate(pages[:-1], 1):
        if page["final"]:
            problems.append(f"page {i} was the last page, but more pages were fetched")
    if not pages[-1]["final"]:
        problems.append("last page says more issues match; fetch stopped early")
    keys = [b["key"] for p in pages for b in p["blocks"]]
    if len(pages) > 1:
        projects = {_key_sort(k)[0] for k in keys}
        if project and projects - {project}:
            problems.append(f"issues from other projects in a {project}-only fetch")
        elif len(projects) > 1:
            problems.append("key paging spans several projects (Jira's 'key <' pages within one "
                            "project only); page each project separately")
        nums = [_key_sort(k) for k in keys]
        if any(a <= b for a, b in zip(nums, nums[1:])):
            problems.append("keys are not strictly descending across pages "
                            "(duplicate or out-of-order page)")
    return problems


def adapt_fetch(manifest, roster=None):
    """Parse and check one fetch described by ``manifest``; return issues + fetch status.

    ``manifest``::

        {"name": "sprint_issues", "order": "key" | "rank", "page_size": 50,
         "signals": ["flagged", "description"],   # optional: signals this fetch should carry
         "parts": [{"project": "OCPEDGE",          # optional; one part per paged project
                    "pages": [{"text": "...", "jql": "<query sent>"} | {"file": "p1.txt", "jql": ...}
                              | {"error": "..."}]}]}

    ``text`` may be bare or wrapped as ``{"result": "<text>"}``. ``jql`` (the exact
    query sent for that page) is needed to accept a ``No issues found for JQL``
    page as complete.

    ``"pages"`` at the top level is shorthand for a single part. The fetch is
    ``complete`` only if every page succeeded and every part reached its last
    page; callers must not compute figures from an incomplete fetch.
    """
    page_size = manifest.get("page_size") or PAGE_SIZE
    order = manifest.get("order") or "key"
    parts = manifest.get("parts")
    if parts is None:
        parts = [{"project": manifest.get("project"), "pages": manifest.get("pages") or []}]
    people = PeopleMatcher(roster)
    problems, unknown, issues, seen = [], [], [], set()
    rejected, unknown_fields = set(), set()
    page_count = 0
    for part in parts:
        parsed = []
        for page in part.get("pages") or []:
            try:
                text = _load_page_text(page)
            except OSError:
                parsed.append({"status": "error", "reason": "page file not readable", "final": False,
                               "blocks": [], "unknown": []})
                continue
            parsed.append(parse_page(text, error=page.get("error") or text is None,
                                     page_size=page_size, jql=page.get("jql")))
            rejected.update(parsed[-1].get("rejected", []))
            unknown_fields.update(parsed[-1].get("unknown_fields", []))
        page_count += len(parsed)
        label = part.get("project") or "fetch"
        problems.extend(f"{label}: {p}" for p in check_part(parsed, part.get("project"), order))
        for page in parsed:
            unknown.extend(page["unknown"])
            for raw in page["blocks"]:
                if raw["key"] in seen:
                    continue
                seen.add(raw["key"])
                issues.append(to_issue(raw, people))

    signals = set(manifest.get("signals") or [])
    skipped = []
    # Unset custom fields print no line, so a missing Flagged line means "not
    # flagged". Flagged is unavailable only when query_jira rejects the field;
    # any other rejected field makes the figures wrong, so the fetch is incomplete.
    if FLAGGED_FIELD in rejected:
        skipped.append(SKIPPED_FLAGGED)
    other_rejected = sorted(rejected - {FLAGGED_FIELD})
    if other_rejected:
        problems.append(f"query_jira rejected field(s): {', '.join(other_rejected)}")
    if "description" in signals and not any("description" in i for i in issues):
        skipped.append(SKIPPED_DESCRIPTION)
    warnings = []
    if unknown_fields:
        warnings.append(f"create metadata lists unknown field(s): {', '.join(sorted(unknown_fields))}")
    if unknown:
        labels = sorted({u for u in unknown if u not in ("(preamble)", "(unlabelled)")})
        warnings.append(f"ignored {len(unknown)} unrecognized line(s)"
                        + (f"; labels: {', '.join(labels)}" if labels else ""))
    return {
        "issues": issues,
        "fetch": {"name": manifest.get("name"), "complete": not problems, "pages": page_count,
                  "fetched": len(issues), "problems": problems},
        "skipped_signals": skipped,
        "people": {"matched": len(people.matched), "unmatched": sorted(people.unmatched)},
        "warnings": warnings,
    }


# ---------------------------------------------------------------- roster


ROSTER_ROLES = ("software engineer", "software quality engineer")
_ROVER_RE = re.compile(r"rover\.redhat\.com/people/profile/([A-Za-z0-9._-]+)")
_MD_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]*)\)$")


def parse_team_roster(markdown, roles=ROSTER_ROLES):
    """Parse edge-context ``people/team-roster.md``.

    Returns ``{"members", "contacts", "not_counted", "engineer_count", "warnings"}``:

    * ``contacts`` — every table row: display name, Rover email (profile id +
      ``@redhat.com``, or None), role. Used to map action-item owners to Slack.
    * ``members`` — the counted engineers in ``.roster.json`` shape: roles that
      contain "Software Engineer" or "Software Quality Engineer" (any level),
      minus anyone listed under "Not counted for sprint capacity".
    """
    contacts, not_counted, warnings = [], [], []
    section = None
    for line in (markdown or "").splitlines():
        heading = re.match(r"^\s*#+\s*(.*?)\s*$", line)
        if heading:
            section = heading.group(1).strip().casefold()
            continue
        if section and section.startswith("not counted for sprint capacity"):
            item = re.match(r"^\s*[-*]\s+(.*?)\s*$", line)
            if item:
                not_counted.append(_MD_LINK_RE.sub(r"\1", item.group(1)).strip())
            continue
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set("".join(cells)) <= set("-: ") or cells[0].casefold() == "name":
            continue
        name_cell = cells[0]
        link = _MD_LINK_RE.match(name_cell)
        name = link.group(1).strip() if link else name_cell
        rover = _ROVER_RE.search(link.group(2)) if link else None
        contacts.append({
            "display_name": name,
            "email": f"{rover.group(1)}@redhat.com" if rover else None,
            "github": cells[1] if len(cells) > 1 else None,
            "role": cells[2] if len(cells) > 2 else "",
            "location": cells[3] if len(cells) > 3 else None,
        })
    excluded = {normalize_name(n) for n in not_counted}
    known = {normalize_name(c["display_name"]) for c in contacts}
    for name in not_counted:
        if normalize_name(name) not in known:
            warnings.append("a 'Not counted' entry matches no roster row")
    members = []
    for c in contacts:
        role = (c["role"] or "").casefold()
        if not any(r in role for r in roles) or normalize_name(c["display_name"]) in excluded:
            continue
        members.append({"username": c["email"] or c["display_name"], "display_name": c["display_name"],
                        "country": c["location"], "sp_target": 8})
    return {"members": members, "contacts": contacts, "not_counted": not_counted,
            "engineer_count": len(members), "warnings": warnings}


_SCRUM_MASTER_LINE_RE = re.compile(
    r"^\s*[-*]?\s*\**\s*(?:current scrum master|current|scrum master|assigned to|held by)\s*\**\s*:\s*\**\s*(.+?)\s*\**\s*$",
    re.I)


def find_scrum_master(roster_markdown=None, role_markdown=None):
    """The current Scrum Master's display name from edge-context, or None.

    Looks for a ``team-roster.md`` row whose role contains "Scrum Master", then for
    a ``Current Scrum Master: <name>`` (or ``Current:``, ``Assigned to:``,
    ``Held by:``) line in ``roles/scrum-master.md``. None when neither names one.
    """
    for c in parse_team_roster(roster_markdown or "")["contacts"]:
        if "scrum master" in (c["role"] or "").casefold():
            return c["display_name"]
    for line in (role_markdown or "").splitlines():
        m = _SCRUM_MASTER_LINE_RE.match(line)
        if m:
            name = _MD_LINK_RE.sub(r"\1", m.group(1).strip()).strip()
            if name and _clean(name):
                return name
    return None


# ---------------------------------------------------------------- CLI


def _load(path):
    """Read a JSON file."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    """CLI entry point: ``fetch`` (adapt one paged fetch) or ``roster`` (parse team-roster.md)."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="parse and check one paged query_jira fetch")
    f.add_argument("--manifest", required=True)
    f.add_argument("--roster", default=None, help="roster JSON (from the 'roster' command)")
    f.add_argument("--issue-links", default=None,
                   help='JSON {key: get_jira_issue text or null} to attach issue links from')
    f.add_argument("--linked-spikes", default=None,
                   help='JSON {feature key: adapted spike fetch} from linkedIssues(...) queries; '
                        'an incomplete one marks the output fetch incomplete')
    f.add_argument("--output", required=True)
    r = sub.add_parser("roster", help="parse edge-context people/team-roster.md")
    r.add_argument("--team-roster", required=True)
    r.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "fetch":
        roster = _load(args.roster) if args.roster else None
        out = adapt_fetch(_load(args.manifest), roster=roster)
        if args.issue_links:
            attach_issue_links(out, _load(args.issue_links))
        if args.linked_spikes:
            attach_linked_spike_fetches(out, _load(args.linked_spikes))
    else:
        with open(args.team_roster, encoding="utf-8") as fh:
            out = parse_team_roster(fh.read())
    write_output(out, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
