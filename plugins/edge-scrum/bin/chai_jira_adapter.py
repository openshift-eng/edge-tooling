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
        Created: 2030-01-02 | Updated: 2030-01-06 | Due: 2030-01-06 | Resolved: 2030-01-06
        Components: <name> | Labels: a, b | Fix Versions: x | Parent: KEY-100
        URL: <browse link>
        customfield_10020: <name> (id N, <state>, board B, start <iso>, end <iso>, completed <iso>); <name> (...)
        customfield_10028: <story points>
        customfield_<id>: <value>
        issuelinks: (set, but query_jira cannot render this field's structure)
        resolutiondate: <iso>

A page with more issues after it (``Showing the first N matching issues; more
match:``) ends with a "... More issues match than the N shown ..." hint. That page
is whole: :func:`next_page_jql` gives the query for the next one.
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
(:func:`parse_issue_links`, :func:`attach_issue_links`). Links are read with
``get_jira_issue(<key>, fields="issuelinks")``: its Requested fields area says
whether the field is empty (no links, no section) or set (read the section)
(:func:`issuelinks_state`). A missing, unknown or ambiguous state, a failed
call, or a cut-off or unreadable response is a problem, never "no links". A feature's spikes come
from ``issue in linkedIssues("<feature>", "is blocked by") AND issuetype = Spike``
(:func:`attach_linked_spikes`).

People: chai-bot returns display names only (no emails). A display name that
matches the roster (``_jira_transforms.roster_match``) gets the roster username
as its ``email``, so roster-keyed scripts work; an unmatched name is kept as
``name`` so its work still shows up (as roster drift) instead of as unassigned.

Paging recipe (``weekly_report_files.py`` follows it): ``(<JQL>) ORDER BY key DESC`` with max_results
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
    parse_date,
    roster_match,
    write_output,
)

PAGE_SIZE = 50
ERROR_CHARS = 160  # a failed call's error message is cut to this length, before Slack escaping
KEY_PATTERN = r"[A-Z][A-Z0-9_]*-\d+"
_HEADER_RE = re.compile(r"^\s*\[(" + KEY_PATTERN + r")\]\s*(.*?)\s*$")
_KEY_RE = re.compile(r"^(?P<project>[A-Z][A-Z0-9_]*)-(?P<num>\d+)$")
_LABEL_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_ ]{0,40}?)\s*:\s?(.*)$")
_SPRINT_ENTRY_RE = re.compile(r"([^;()]*?)\s*\(([^()]*)\)")
_SPRINT_STATES = {"active", "closed", "future"}
# Signs that a field value is not whole (chai-bot shortened it or stored it for result_page).
_SHORTENED_RE = re.compile(r"\+\s*\d+\s+more\b|(\.\.\.|…)\s*$|\b(stored|shortened|truncated|result_page)\b", re.I)
_FOUND_RE = re.compile(r"^\s*Found\s+(\d+)\s+issues?(?:\s*\(showing\s+(\d+)\))?", re.I)
_MORE_RE = re.compile(r"^\s*Showing the first\s+(\d+)\s+matching issues?.*more match", re.I)
_NO_RESULTS_RE = re.compile(r"^\s*No issues found for JQL:(.*)$", re.S)
_HINT_RE = re.compile(r"^\s*(\.\.\.|…)?\s*More issues match\b", re.I)
_INVALID_FIELDS_RE = re.compile(r"^Invalid extra_fields entries:\s*(.*?)(?:\.\s+Use\b.*)?\.?$", re.I)
_UNKNOWN_FIELDS_RE = re.compile(r"^Unknown fields:\s*(.*?)\.?$", re.I)
_METADATA_RE = re.compile(r"^(Create metadata for\b|Issue types available\b|Requested fields:|\[[^\]]+\]$)", re.I)
_LINKED_LINE_RE = re.compile(
    r"^\s*[-*]\s*(?P<wording>[^:|]+?)\s*:\s*(?P<key>" + KEY_PATTERN + r")\s*"
    r"(?:\((?P<type>[^)]*)\))?\s*(?:\|\s*status\s*:\s*(?P<status>[^|]*?)\s*)?(?:\|.*)?$", re.I)

EMPTY_VALUES = {"", "none", "null", "n/a", "unassigned", "unresolved", "-"}

# Labels on the pipe-separated lines, mapped to output keys.
PIPE_FIELDS = {
    "type": "issue_type", "issue type": "issue_type", "status": "status",
    "priority": "priority", "assignee": "assignee", "reporter": "reporter",
    "resolution": "resolution", "created": "created", "updated": "updated",
    "resolved": "resolved", "due": "duedate",
}
DATE_FIELDS = ("created", "updated", "resolutiondate", "duedate")
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
# issuelinks: query_jira prints "(set, but ... cannot render ...)"; url: the issue's browse link.
IGNORED_FIELDS = {"issuelinks", "url"}
LIST_FIELDS = {"components": "components", "labels": "labels", "fix versions": "fixVersions",
               "fixversions": "fixVersions", "fix version": "fixVersions"}
# Labels that may share a line with others (`` | ``-separated); see _segments.
KNOWN_LABELS = (set(PIPE_FIELDS) | CUSTOM_FIELDS | set(LIST_FIELDS)
                | {"description", "parent", "resolutiondate"} | IGNORED_FIELDS)

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


def parse_sprint_value(value):
    """``customfield_10020`` text → ``(sprint dicts, problem or None)``.

    Entries look like ``<name> (id N, <state>, board B, start <iso>, end <iso>,
    completed <iso>)``, separated by ``; `` (or ``, ``). Only the name, ``id N``
    and the state (active / closed / future) are required; other ``key value``
    parts are kept when known (board, start, end, completed) and ignored
    otherwise, in any order. An empty value means no sprint. A value that still
    looks shortened ("+N more", a trailing "...", a note that it was stored), an
    entry without an id, or text with no entry at all returns a ``problem``: the
    caller must not guess the missing sprints (decision 0018: never estimate).
    """
    text = _clean(value)
    if text is None:
        return [], None
    out, problem = [], None
    for m in _SPRINT_ENTRY_RE.finditer(text):
        entry = {"name": m.group(1).strip(" ,;"), "id": None, "state": None}
        for part in (p.strip() for p in m.group(2).split(",")):
            idm = re.fullmatch(r"id\s+(\d+)", part, re.I)
            key, _, val = part.partition(" ")
            if idm:
                entry["id"] = int(idm.group(1))
            elif part.lower() in _SPRINT_STATES:
                entry["state"] = part.lower()
            elif key.lower() in ("board", "start", "end", "completed"):
                entry[{"board": "boardId", "start": "startDate", "end": "endDate",
                       "completed": "completeDate"}[key.lower()]] = _clean(val)
        if entry["id"] is None:
            problem = problem or f"sprint entry '{entry['name']}' has no id"
            continue
        out.append(entry)
    # Every part of the value must belong to an entry: text left over besides the
    # "; " / ", " separators means an entry was cut off (e.g. a missing ")").
    remainder = _SPRINT_ENTRY_RE.sub("", text).strip(" \t\n;,")
    if _SHORTENED_RE.search(text):
        problem = "sprint value was shortened; read it whole with result_page"
    elif remainder:
        problem = problem or "sprint value has unparsed text after the last entry (cut off?)"
    elif not out and problem is None:
        problem = "sprint value not recognized"
    return out, problem


# Fields the report's checks read (besides the sprint, checked by parse_sprint_value):
# a shortened or stored value would be read as data (a "+2 more" label, a stored
# note as the SME), so the fetch is incomplete until the value is read whole.
WHOLE_VALUE_FIELDS = (
    "assignee", "labels", "components", "fixVersions",
    "customfield_10018",  # Parent Link: a shared epics fetch is partitioned by it
    "customfield_10028",  # Story Points
    "customfield_10470",  # QA Contact
    "customfield_10473",  # Docs approver
    "customfield_10475",  # SME
    "customfield_10795",  # T-shirt size
    "customfield_10855",  # Target Version
)
# A value that is not whole: it names where the whole value is (a stored result for
# result_page / result_tail, or a get_jira_issue call), ends in a "+N more" list item
# or an ellipsis, or ends in a bracketed note. Whole values that merely contain a word
# such as "stored" (a "log-truncated" label) are not matched.
_SHORTENED_VALUE_RE = re.compile(
    r"\b(?:result_page|result_tail|get_jira_issue)\b"
    r"|(?:^|,)\s*\+\s*\d+\s+more\s*$"
    r"|(?:\.\.\.|…)\s*$"
    r"|[(\[][^()\[\]]*\b(?:stored|shortened|truncated|shown)\b[^()\[\]]*[)\]]\s*$",
    re.IGNORECASE)


def shortened_fields(raw):
    """The :data:`WHOLE_VALUE_FIELDS` of a parsed issue block whose value is not
    whole (see ``_SHORTENED_VALUE_RE``)."""
    return [f for f in WHOLE_VALUE_FIELDS if raw.get(f) and _SHORTENED_VALUE_RE.search(str(raw[f]))]


def parse_sprints(value):
    """``customfield_10020`` text → sprint dicts (see :func:`parse_sprint_value`)."""
    return parse_sprint_value(value)[0]


_ISSUE_LINE_RE = re.compile(r"^\s*\[(" + KEY_PATTERN + r")\]")
_STATE_LINE_RE = re.compile(r"^\s*Type:\s*[^|]+\|\s*Status:", re.IGNORECASE)
_COMMENTS_LINE_RE = re.compile(r"^\s*Comments:\s*(?:none\b|\d+\s+total\b)", re.IGNORECASE)
_FIELD_ENTRY_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*):\s?(.*)$")
# The issuelinks state get_jira_issue(fields="issuelinks") prints in its Requested fields area.
LINKS_EMPTY = "(empty, or not a field of this issue)"
LINKS_SET = "(set, but get_jira_issue cannot render this field's structure)"


def issuelinks_state(text):
    """The issuelinks state of a ``get_jira_issue(fields="issuelinks")`` response.

    Read only from the response's own Requested fields area: a ``Requested
    fields:`` line whose ``field: value`` entries are followed by the comments
    header (``Comments: N total…`` / ``Comments: none``), as the tool prints it
    after the description. Returns ``"empty"`` (:data:`LINKS_EMPTY`), ``"set"``
    (:data:`LINKS_SET`), ``"unknown"`` (another value), ``"ambiguous"`` (more
    than one such area: a description or comment holds a whole example, and the
    real one cannot be told apart, so none is trusted) or None (no such area,
    or no issuelinks entry: the field was not requested, or the response is cut).
    """
    lines = (text or "").splitlines()
    found = []
    for i, line in enumerate(lines):
        if line.strip() != "Requested fields:":
            continue
        entries, j = {}, i + 1
        while j < len(lines) and lines[j].strip() and (m := _FIELD_ENTRY_RE.match(lines[j])):
            entries[m.group(1)] = m.group(2).strip()
            j += 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and _ends_response(lines, j):
            found.append(entries.get("issuelinks"))
    if len(found) > 1:
        return "ambiguous"
    if not found or found[0] is None:
        return None
    return {LINKS_EMPTY.casefold(): "empty", LINKS_SET.casefold(): "set"}.get(found[0].casefold(), "unknown")


def _ends_response(lines, j):
    """Whether ``lines[j]`` is the comments header that ends a response: ``Comments: N
    total…`` (comments follow), or ``Comments: none`` with nothing after it."""
    if not _COMMENTS_LINE_RE.match(lines[j]):
        return False
    if re.match(r"^\s*Comments:\s*none\b", lines[j], re.IGNORECASE):
        return not any(line.strip() for line in lines[j + 1:])
    return True


def issue_text_complete(text, key):
    """Whether ``text`` is a whole, normal ``get_jira_issue`` response for ``key``.

    It names the issue on its ``[KEY] summary`` line, has the ``Type: … |
    Status: …`` state line, and reaches the comments section that ends the
    response (after the linked issues, attachments and description). A failed
    call's explanation, a response for another issue, or one cut off before its
    comments section is not whole.
    """
    issue = state = False
    lines = (text or "").splitlines()
    for j, line in enumerate(lines):
        m = _ISSUE_LINE_RE.match(line)
        if m and not issue:
            if m.group(1) != key:
                return False
            issue = True
        elif issue and _STATE_LINE_RE.match(line):
            state = True
        elif issue and state and _ends_response(lines, j):
            return True
    return False


def _mentions_links(text):
    """Whether the state part of an issue response (before ``Description:``) looks like it
    has links in another form: a row with a known link wording (``- is blocked by: KEY``),
    or a "linked jira issues" heading other than the issue's own ``[KEY]`` summary line."""
    for line in (text or "").splitlines():
        if re.match(r"^\s*Description\s*:", line, re.IGNORECASE):
            break
        m = _LINKED_LINE_RE.match(line)
        if m and re.sub(r"\s+", " ", m.group("wording").strip().lower()) in LINK_WORDING:
            return True
        if "linked jira issues" in line.casefold() and not _ISSUE_LINE_RE.match(line):
            return True
    return False


def _links_section_open_at_end(text):
    """Whether the response ends inside its "Linked Jira Issues" section (no other
    section began after it), so a final unterminated row is a row cut off there."""
    inside = False
    for line in (text or "").splitlines():
        if re.fullmatch(r"[*#>\s]*linked jira issues:?\s*", line, re.I):
            inside = True
        elif inside and line.strip() and not line.lstrip().startswith(("-", "*")):
            inside = False
    return inside


def _first_issue_key(text):
    """The key on the first ``[KEY] summary`` line of an issue response, or None."""
    return next((m.group(1) for line in (text or "").splitlines() if (m := _ISSUE_LINE_RE.match(line))), None)


def parse_issue_links(text, unreadable=None):
    """``get_jira_issue`` output → issuelinks from its "Linked Jira Issues" section.

    Lines look like ``- is blocked by: KEY (Blocks) | status: <status> | <url>``.
    Returns ``(links, found)``; ``found`` is False when the section is missing.
    A list line in the section that cannot be read is skipped; when it mentions
    a block ("blocks" / "is blocked by", e.g. a cut-off row) it is appended to
    ``unreadable`` (a list, when given), so the caller can treat that issue's
    blocked signal as incomplete instead of unblocked.
    """
    links, found, inside = [], False, False
    for line in (text or "").splitlines():
        # The section starts only at a standalone "Linked Jira Issues:" header line
        # (chai-bot prints it on its own line), never at prose containing the phrase.
        if re.fullmatch(r"[*#>\s]*linked jira issues:?\s*", line, re.I):
            found, inside = True, True
            continue
        if not inside:
            continue
        if not line.strip():
            continue
        m = _LINKED_LINE_RE.match(line)
        if not m:
            if line.lstrip().startswith(("-", "*")):
                if unreadable is not None and "block" in line.lower():
                    unreadable.append(line.strip())
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


def _known(match):
    """Whether a label match names a known field."""
    return bool(match) and match.group(1).strip().lower() in KNOWN_LABELS


def _segments(line):
    """``Label: value | Label: value`` → label matches.

    A line is split on `` | `` when any part starts with a known field label:
    known parts are read, and an unknown part comes back as its own match (or
    None) so the caller ignores it with a warning instead of letting it run
    into a known field's value (``Created: x | Due: y`` must not make
    "Created" read "x | Due: y"). A line with no known label, or a
    ``Description:`` line (free text), stays one value, so a value containing
    `` | Note: x`` is not cut at the pipe.
    """
    parts = re.split(r"\s+\|\s+", line.strip())
    matches = [_LABEL_RE.match(p) for p in parts]
    free_text = matches[0] and matches[0].group(1).strip().lower() == "description"
    if len(parts) > 1 and not free_text and any(_known(m) for m in matches):
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


def call_failed(error=None):
    """Reason for a failed ``query_jira`` call, naming the tool's error message when
    there is one (``{"error": "<message>"}`` in the page slot): one line, cut to
    :data:`ERROR_CHARS` characters, with ``&``, ``<`` and ``>`` escaped so the
    tool's text never becomes Slack markup (a mention or a link)."""
    message = "" if error is None or isinstance(error, bool) else " ".join(str(error).split())
    if len(message) > ERROR_CHARS:
        message = message[:ERROR_CHARS - 1] + "…"
    message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"query_jira call failed: {message}" if message else "query_jira call failed"


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
    issue count still matches; no issue may be listed twice; every page must stay
    within the restriction paging added to ``jql`` (``AND project = P``, ``AND
    project not in (...)``) and below its ``key <`` cursor, in the cursor's project (a
    more-match page that is not below it: ``did_not_advance``). A ``key <`` that is not of
    the query's own project, decided from the query alone, any page (an empty one too):
    ``query_error``. Anything else is ``incomplete``. ``text`` may be
    wrapped as ``{"result": "<text>"}`` (see :func:`unwrap`). ``error`` (a failed
    call) gives ``error`` with the tool's message as the reason (:func:`call_failed`).
    """
    if error:
        return {"status": "error", "reason": call_failed(error), "final": False,
                "blocks": [], "unknown": [], "rejected": [], "unknown_fields": []}
    text = unwrap(text)
    empty = _NO_RESULTS_RE.match(text or "")
    if empty:
        out = {"status": "ok", "reason": None, "final": True, "blocks": [], "unknown": [],
               "rejected": [], "unknown_fields": []}
        wrong = _cursor_mismatch(*_page_query(jql)[:4]) if jql else None
        if jql is None:
            out.update(status="incomplete", final=False,
                       reason="'No issues found' page without the sent JQL to compare")
        elif wrong:  # no issues because the query's own key < is wrong: not a whole answer
            out.update(status="incomplete", final=False, reason=wrong, query_error=True)
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
    keys = [raw["key"] for raw in parsed]
    twice = next((k for i, k in enumerate(keys) if k in keys[:i]), None)
    if twice:  # in any order (a rank page too): never one issue silently dropped
        return incomplete(f"issue {twice} is listed twice on the page")
    if jql:  # the restriction paging added to the query sent, on every page (the last one too)
        predicate, excluded, project, cursor, _ = _page_query(jql)
        wrong = _cursor_mismatch(predicate, excluded, project, cursor)
        if wrong:  # the query's own key < is wrong, whatever the page holds (QueryError)
            out["query_error"] = True
            return incomplete(wrong)
        outside = _restriction_problem(keys, project, excluded)
        if outside:
            return incomplete(outside)
        if cursor:  # the key < sent: a page with a key not below it cannot be used
            part, below = _key_sort(cursor)
            stale = next((k for k, (p, n) in ((k, _key_sort(k)) for k in keys) if p == part and n >= below), None)
            if stale and out["final"]:
                return incomplete(f"key {stale} on the last page is not below the key < {cursor} sent")
            outside = next((k for k in keys if _key_sort(k)[0] != part), None)
            # Jira's key < keeps its own project: a last page with another project's key, or
            # any page with none of its project's keys, is not this query's answer (sent again
            # once). A more-match page that also holds the cursor's project splits, as before.
            if outside and (out["final"] or part not in {_key_sort(k)[0] for k in keys}):
                return incomplete(f"issue {outside} is outside project {part} of the key < {cursor} sent")
            if stale:  # a more-match page that did not advance (PageDidNotAdvance)
                out["did_not_advance"] = True
                return incomplete(f"key {stale} on the page is not below the key < {cursor} sent "
                                  "(the tool did not apply it)")
    return out


# ---------------------------------------------------------------- conversion


class PeopleMatcher:
    """Display name → user dict, matched to the roster by display name."""

    def __init__(self, roster=None):
        """Index the roster's contacts (or members) by display name and email."""
        people = (roster or {}).get("contacts") or (roster or {}).get("members") or []
        # A contact without a Rover email falls back to its display name, as
        # parse_team_roster does for members, so it still matches.
        members = [{"username": p.get("email") or p.get("username") or p.get("display_name"),
                    "display_name": p.get("display_name")}
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


def _date(value):
    """A date or ISO timestamp as given, or None when empty or not a readable date."""
    text = _clean(value)
    if text is None:
        return None
    try:
        parse_date(text)
    except (ValueError, TypeError, AttributeError):
        return None
    return text


def to_issue(raw, people, date_problems=None):
    """Raw text fields → an issue in the mcp-atlassian ``jira_search`` shape.

    A date that is not readable is left out (the transforms then use their
    defaults) and, when ``date_problems`` is given, recorded there as
    ``"<key> <field> '<value>'"``; it never raises.
    """
    issue = {"key": raw["key"], "summary": raw.get("summary") or ""}
    for field in ("issue_type", "status", "priority", "resolution"):
        value = _clean(raw.get(field))
        issue[field] = {"name": value} if value else None
    if issue["status"] is None:
        issue["status"] = {"name": ""}
    # Dates are left out when missing: the transforms read them with defaults
    # (raw.get("updated", ...)), which a stored None would bypass.
    given = {"created": _clean(raw.get("created")), "updated": _clean(raw.get("updated")),
             "resolutiondate": _clean(raw.get("resolutiondate")) or _clean(raw.get("resolved")),
             "duedate": _clean(raw.get("duedate"))}
    for field in DATE_FIELDS:
        value = _date(given[field])
        if value:
            issue[field] = value
        elif given[field] and date_problems is not None:
            date_problems.append(f"{raw['key']} {field} '{given[field]}'")
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
        sprints, problem = parse_sprint_value(raw["customfield_10020"])
        issue["customfield_10020"] = sprints
        if problem:
            issue["sprint_data_incomplete"] = problem
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

    ``issue_texts`` maps key → that issue's ``get_jira_issue(fields="issuelinks")``
    text (None when the call failed). The Requested fields area's issuelinks
    state (:func:`issuelinks_state`) is the signal:

    * ``empty`` on a whole response for the issue (:func:`issue_text_complete`)
      with no links section → no links;
    * ``set`` → the "Linked Jira Issues" section is read; missing or not
      readable → a problem;
    * a missing, unknown, ambiguous or cut state is a problem and never makes
      the links empty; confirmed rows of a links section that is there are kept.

    Also problems: a failed call, a response for another issue, a state that
    contradicts the response, and a response cut off before its comments
    section. Confirmed rows read so far are kept; only an unterminated last row
    of a links section that runs to the cut is dropped. Returns the problems.
    """
    by_key = {i["key"]: i for i in adapted.get("issues", [])}
    problems = []
    for key, text in (issue_texts or {}).items():
        if key not in by_key:
            continue
        if text is None:
            problems.append(f"{key}: get_jira_issue failed")
            continue
        first = _first_issue_key(text)
        if first not in (None, key):
            problems.append(f"{key}: get_jira_issue response is for {first}")
            continue
        unreadable = []
        links, found = parse_issue_links(text, unreadable)
        state, whole = issuelinks_state(text), issue_text_complete(text, key)
        if state not in ("empty", "set"):  # no usable state: never "no links"
            problems.append(f"{key}: " + {
                "unknown": "issuelinks state not readable",
                "ambiguous": "issuelinks state ambiguous (more than one Requested fields area)",
            }.get(state, "no issuelinks state in Requested fields (request fields=\"issuelinks\")"))
            if not found:
                continue
        elif state == "empty" and not (found and links):
            if not whole:
                problems.append(f"{key}: get_jira_issue response cut off or not readable")
            elif _mentions_links(text):
                problems.append(f"{key}: issuelinks state contradicts the response")
            else:
                by_key[key]["issuelinks"] = []  # the field says: no links
            continue
        elif state == "empty":
            problems.append(f"{key}: issuelinks state contradicts the response")
        elif not found or (not links and not unreadable):
            problems.append(f"{key}: issuelinks is set but no readable 'Linked Jira Issues' section")
            if not found:
                continue
        if not whole:  # another issue's response has already returned above
            problems.append(f"{key}: get_jira_issue response cut off")
            last = text.splitlines()[-1] if text.strip() else ""
            if (links and not text.endswith("\n") and _LINKED_LINE_RE.match(last)
                    and _links_section_open_at_end(text)):
                links = links[:-1]  # the row at the cut may be partial: never a confirmed blocker
        by_key[key]["issuelinks"] = links  # readable links are kept (confirmed blockers stay)
        if unreadable:
            problems.append(f"{key}: {len(unreadable)} block link line(s) could not be read")
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
    ``features["fetch"]["linked_spike_problems"]`` and in its ``problems`` (feature and
    reason, so the Left out line names them) and makes ``features["fetch"]["complete"]``
    False: a missed spike would report a false missing-spike gap. Returns the problems.
    """
    problems, by_reason = [], {}
    for fkey, fetch in (fetches_by_feature or {}).items():
        status = (fetch or {}).get("fetch") or {}
        if not status.get("complete", False):
            why = "; ".join(status.get("problems") or []) or "fetch status missing"
            problems.append(f"{fkey}: linked-spike fetch incomplete ({why})")
            by_reason.setdefault(why, []).append(fkey)
    out = features.setdefault("fetch", {})
    out.setdefault("linked_spike_problems", []).extend(problems)
    if problems:  # named once per reason (one batch's features share it)
        out["problems"] = [*(out.get("problems") or []),
                           *(f"{', '.join(keys)}: linked-spike fetch incomplete ({why})"
                             for why, keys in by_reason.items())]
        out["complete"] = False
    attach_linked_spikes(features, {k: (v or {}).get("issues", []) for k, v in (fetches_by_feature or {}).items()})
    return problems


LINKED_SPIKE_BATCH = 20  # features per batched linked-spike query


def linked_spikes_jql(feature_keys):
    """One query for the spikes that block any of ``feature_keys``: the per-feature
    ``issue in linkedIssues("<key>", "is blocked by") AND issuetype = Spike`` queries
    joined by OR, so the same issues, link direction and filter."""
    if not feature_keys:
        raise ValueError("linked_spikes_jql needs at least one feature key")
    clauses = " OR ".join(f'issue in linkedIssues("{k}", "is blocked by")' for k in feature_keys)
    return f"issuetype = Spike AND ({clauses})"


def _blocked_by_spike(spike):
    """The keys a spike ``blocks`` (its outward Blocks links)."""
    return {(link.get("outward_issue") or {}).get("key") for link in spike.get("issuelinks") or []
            if (link.get("type") or {}).get("name") == "Blocks" and link.get("outward_issue")}


def split_linked_spikes(feature_keys, batch_fetch, link_texts):
    """Per-feature linked-spike fetches, as :func:`attach_linked_spike_fetches` takes
    them, from one batched query (:func:`linked_spikes_jql`).

    Each returned spike is attributed by its own links (``link_texts``: key →
    ``get_jira_issue(<spike>, fields="issuelinks")`` text, None for a failed call). It
    belongs to every feature of the batch it ``blocks`` (that feature "is blocked by"
    it), so a spike that blocks two features is in both; a link in the other direction
    does not count. When the batch fetch is incomplete, a spike's links cannot be
    read, or a returned spike blocks none of the batch's features, every feature of the
    batch is incomplete with that reason: the association is unknown, never guessed.
    """
    batch_fetch = batch_fetch if isinstance(batch_fetch, dict) else {}
    status = batch_fetch.get("fetch") or {"complete": False, "problems": ["fetch status missing"]}
    spikes = json.loads(json.dumps({"issues": batch_fetch.get("issues") or [], "fetch": {}}))
    problems = [] if status.get("complete") else list(status.get("problems") or ["fetch status missing"])
    problems += attach_issue_links(spikes, {i["key"]: link_texts.get(i["key"]) for i in spikes["issues"]})
    keys, unread = set(feature_keys), {p.split(":")[0] for p in problems}
    targets = {}
    for spike in spikes["issues"]:
        targets[spike["key"]] = _blocked_by_spike(spike) & keys
        if not targets[spike["key"]] and spike["key"] not in unread:
            problems.append(f"{spike['key']}: returned for {', '.join(feature_keys)} but blocks none of them")
    out = {}
    for fkey in feature_keys:
        issues = json.loads(json.dumps([i for i in batch_fetch.get("issues") or []
                                        if fkey in targets.get(i.get("key"), set())]))
        out[fkey] = {"issues": issues, "left_out": [], "skipped_signals": [], "warnings": [],
                     "fetch": dict(status, name=f"linked_{fkey}", fetched=len(issues),
                                   complete=bool(status.get("complete")) and not problems, problems=list(problems))}
    return out


# ---------------------------------------------------------------- paging


def _key_sort(key):
    """Sort key for an issue key: (project, number), so TEST-9 sorts before TEST-10."""
    m = _KEY_RE.match(key or "")
    return (m.group("project"), int(m.group("num"))) if m else (key or "", 0)


def _scan_top(text):
    """Yield ``(index, char)`` for each character of ``text`` outside quotes and parentheses."""
    depth, quote, i = 0, None, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif depth == 0:
            yield i, c
        i += 1


def _split_top(text, word):
    """``text`` split on the JQL keyword ``word`` (AND, OR) outside quotes and parentheses,
    as a word of its own (``x OR(y)`` splits; ``COLOR`` does not)."""
    pattern = re.compile(r"(?<![\w.\-])" + word + r"(?![\w.\-])", re.IGNORECASE)
    parts, start, skip_to = [], 0, 0
    for i, _ in _scan_top(text):
        if i < skip_to:
            continue
        m = pattern.match(text, i)
        if m:
            parts.append(text[start:i].strip())
            start = skip_to = m.end()
    return [*parts, text[start:].strip()]


def _split_order(jql):
    """``(query, order clause or "")``: a trailing ORDER BY outside quotes and parentheses."""
    text = (jql or "").strip()
    pattern = re.compile(r"\s+ORDER\s+BY\s+", re.IGNORECASE)
    for i, _ in _scan_top(text):
        m = pattern.match(text, i)
        if m:
            return text[:i].strip(), text[m.end():].strip()
    return text, ""


def strip_order_by(jql):
    """JQL without a trailing ORDER BY clause (one inside a quoted value stays)."""
    return _split_order(jql)[0]


def page_jql(jql, project=None, last_key=None, excluded=()):
    """JQL for a key-ordered page of ``jql``: ``(<predicate>)``, then ``AND project not in
    (<excluded>)``, ``AND project = <project>`` and ``AND key < <last_key>`` when given,
    then ``ORDER BY key DESC``."""
    out = f"({strip_order_by(jql)})"
    if excluded:
        out += f" AND project not in ({', '.join(excluded)})"
    if project:
        out += f" AND project = {project}"
    if last_key:
        out += f" AND key < {last_key}"
    return out + " ORDER BY key DESC"


class PageNotWhole(ValueError):
    """The page cannot be used as it is: the call failed; the text is cut off or not
    readable; it lists an issue twice or one outside the restriction paging added; a last
    page has a key not below its ``key <`` or of another project; or a page under a valid
    ``key <`` holds no key of its project. Send the same query again once; if it is still
    not whole, the fetch is incomplete, with this reason."""


class PageDidNotAdvance(PageNotWhole):
    """A key on a more-match page is not below the ``key <`` sent: the tool did not apply
    it, so the page cannot be used. Like any :class:`PageNotWhole`, the same query is sent
    again once; if it still does not advance, the fetch is incomplete with this reason."""


class QueryError(ValueError):
    """The query sent cannot be paged as it is: its ``key <`` is not of the project(s) the
    query is restricted to, or is of one it excludes (decided from the query, whatever the
    page holds). Sending it again unchanged cannot help, so it is not retried; the fetch is
    incomplete with this reason."""


class SplitByProject(Exception):
    """A key-ordered page over several projects says more match. Jira's ``key <`` compares
    keys within one project, so the query goes on per project: ``parts`` is a list of
    ``{"project": <P or None>, "jql": <that part's first page>}`` (``None``: every project
    not listed before it). Not a failure: fetch each part from its first page with
    :func:`next_page_jql`, pass each as its own manifest part to :func:`adapt_fetch`, and
    leave out the pages of the query that raised this (the parts fetch them again)."""

    def __init__(self, parts):
        self.parts = parts
        super().__init__("page each project separately: " + "; ".join(p["jql"] for p in parts))


_PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROJECT_CLAUSE_RE = re.compile(r"^project\s*(?:=\s*(\S+)|in\s*\(([^()]*)\))$", re.IGNORECASE)
_KEY_LIST_RE = re.compile(r"^(?:key|issuekey|issue)\s+in\s*\(([^()]*)\)$", re.IGNORECASE)
_EXCLUDED_RE = re.compile(r"^project\s+not\s+in\s*\(([^()]*)\)$", re.IGNORECASE)
_PART_RE = re.compile(r"^project\s*=\s*\"?([A-Z][A-Z0-9_]*)\"?$", re.IGNORECASE)
_CURSOR_RE = re.compile(r"^key\s*<\s*(" + KEY_PATTERN + r")$", re.IGNORECASE)


def _group_inner(text):
    """The inside of ``text`` when one pair of parentheses encloses all of it, else None."""
    if not text.startswith("("):
        return None
    depth, quote, escaped = 0, None, False
    for i, c in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "()":
            depth += 1 if c == "(" else -1
            if depth == 0:
                return text[1:i].strip() if i == len(text) - 1 else None
    return None


def _names(values, pattern):
    """Upper-cased comma-separated values that all match ``pattern``, else None."""
    names = [v.strip().strip("\"'").upper() for v in values.split(",")]
    return names if names and all(pattern.match(n) for n in names) else None


def _scope(predicate):
    """Projects every result of ``predicate`` must be in (a set), or None when the JQL does
    not say: only ``project = P``, ``project in (...)`` and ``key in (...)`` clauses joined by
    top-level AND count; a top-level OR (also ``||``) makes it unknown, and a NOT clause
    says nothing."""
    if len(_split_top(predicate, "OR")) > 1 or any(c == "|" for _, c in _scan_top(predicate)):
        return None
    scope = None
    for clause in _split_top(predicate, "AND"):
        inner = _group_inner(clause)
        if inner is not None:
            found = _scope(inner)
        elif re.match(r"^(?:NOT\b|!)", clause, re.IGNORECASE):
            found = None
        else:
            found = None
            m = _PROJECT_CLAUSE_RE.match(clause)
            keys = _KEY_LIST_RE.match(clause)
            if m:
                names = _names(m.group(1) or m.group(2), _PROJECT_KEY_RE)
                found = set(names) if names else None
            elif keys:
                listed = _names(keys.group(1), _KEY_RE)
                found = {_key_sort(k)[0] for k in listed} if listed else None
        if found is not None:
            scope = found if scope is None else scope & found
    return scope


def _page_query(jql):
    """``(predicate, excluded, project, cursor, key_ordered)`` of a query sent for a page.
    The clauses :func:`page_jql` adds after ``(<predicate>)`` are read back; any other query
    is a predicate of its own."""
    body, order = _split_order(jql)
    key_ordered = re.fullmatch(r"key\s+DESC", order, re.IGNORECASE) is not None
    clauses = _split_top(body, "AND")
    inner = _group_inner(clauses[0]) if clauses else None
    tail = clauses[1:]
    excluded = project = cursor = None
    if inner is not None:
        rest = list(tail)
        m = _EXCLUDED_RE.match(rest[0]) if rest else None
        if m and _names(m.group(1), _PROJECT_KEY_RE):
            excluded, rest = _names(m.group(1), _PROJECT_KEY_RE), rest[1:]
        m = _PART_RE.match(rest[0]) if rest else None
        if m:
            project, rest = m.group(1).upper(), rest[1:]
        m = _CURSOR_RE.match(rest[0]) if rest else None
        if m:
            cursor, rest = m.group(1), rest[1:]
        if not rest:
            return inner, excluded or [], project, cursor, key_ordered
    return body, [], None, None, key_ordered


def _query_scope(predicate, excluded, project):
    """Projects every result of a page query must be in (a set), or None when unknown: the
    part's ``AND project = P``, else (no ``project not in``) what the predicate names."""
    return {project} if project else (None if excluded else _scope(predicate))


def _cursor_mismatch(predicate, excluded, project, cursor):
    """Why the ``key <`` of a query sent (read back by :func:`_page_query`) cannot page it
    (:class:`QueryError`), or None. It is decided from the query alone, never from the page:
    a ``key <`` of a project the query excludes, or not of the projects it is restricted to."""
    if not cursor:
        return None
    part, scope = _key_sort(cursor)[0], _query_scope(predicate, excluded, project)
    if part in excluded:
        return f"the key < {cursor} sent is of excluded project {part}: the query cannot be paged"
    if scope is not None and part not in scope:
        return f"the key < {cursor} sent is not of project {', '.join(sorted(scope))}: the query cannot be paged"
    return None


def _restriction_problem(keys, project, excluded):
    """Why a page breaks the restriction paging added to its query (``AND project = P``,
    or ``AND project not in (...)``), or None."""
    on_page = {_key_sort(k)[0] for k in keys}
    if project and on_page - {project}:
        outside = next(k for k in keys if _key_sort(k)[0] != project)
        return f"issue {outside} is outside project {project}"
    if excluded and on_page & set(excluded):
        return f"issues of excluded project(s) {', '.join(sorted(on_page & set(excluded)))} returned"
    return None


def next_page_jql(jql, page_text, page_size=PAGE_SIZE):
    """The query for the page after ``page_text``, or None when it was the last page.

    ``jql`` is the exact query sent for that page (the first page:
    ``page_jql(<query>)``); ``page_text`` is ``query_jira``'s answer, bare or wrapped, or
    ``{"error": "<message>"}`` / None for a failed call.

    * ``Showing the first 50 matching issues; more match:`` (a whole page with more after
      it) → ``(<predicate>) AND key < <last key> ORDER BY key DESC``, keeping a part's
      ``AND project = P``. It never raises for such a page, except to split it by project.
    * ``Found N issues (showing N):`` or ``No issues found for JQL: <the query sent>`` →
      None: the fetch has all its pages (once the page is whole, as below).
    * A failed call, a cut-off or unreadable page, an issue listed twice, or (any page,
      the last one too) an issue outside the part's project or of an excluded project, a
      last page with a key not below its ``key <`` or of another project, or a page with
      no key of the ``key <``'s project → :class:`PageNotWhole` (never None, so it is sent
      again once); on a more-match page also keys not in descending order.
    * A more-match page with a key not below the ``key <`` sent → :class:`PageDidNotAdvance`
      (a :class:`PageNotWhole`: sent again once); a ``key <`` not of the query's own
      project, decided from the query (the query itself is wrong) → :class:`QueryError`
      (not sent again).
    * More match over several projects → :class:`SplitByProject`: one part per project the
      query names (``project in (...)``, or the projects of ``key in (...)``); when the
      query does not name them, one per project on the page and one for every other
      project, so no result is dropped.
    * More match on a page not ordered by key (the rank-ordered features page) → the
      first key-ordered page, ``(<predicate>) ORDER BY key DESC``: the fetch starts again
      by key.
    """
    error = None
    if isinstance(page_text, dict) and (page_text.get("error") is not None or "text" in page_text):  # page slot
        error = page_text.get("error") or page_text.get("text") is None
        page_text = page_text.get("text")
    elif isinstance(page_text, dict):  # the answer already parsed, e.g. {"result": "<text>"}
        page_text = json.dumps(page_text)
    page = parse_page(page_text, error=error or page_text is None, page_size=page_size, jql=jql)
    if page["status"] != "ok":
        if page.get("query_error"):
            raise QueryError(page["reason"])
        raise (PageDidNotAdvance if page.get("did_not_advance") else PageNotWhole)(page["reason"])
    if page["final"]:
        return None
    predicate, excluded, project, cursor, key_ordered = _page_query(jql)
    keys = [b["key"] for b in page["blocks"]]
    if not key_ordered:
        return page_jql(predicate, project=project, excluded=excluded)
    on_page = list(dict.fromkeys(_key_sort(k)[0] for k in keys))
    scope = _query_scope(predicate, excluded, project)
    if scope is not None and len(scope) == 1 and set(on_page) <= scope:
        numbers = [_key_sort(k)[1] for k in keys]
        if numbers != sorted(set(numbers), reverse=True):
            raise PageNotWhole("keys on the page are not in descending order (ORDER BY key DESC not applied)")
        last = keys[-1]  # a key < of another project never gets here (parse_page: query_error)
        if cursor and numbers[0] >= _key_sort(cursor)[1]:
            raise PageDidNotAdvance(f"key {keys[0]} on the page is not below the key < {cursor} sent "
                                    "(the tool did not apply it)")
        return page_jql(predicate, project=project, last_key=last, excluded=excluded)
    if scope is not None and set(on_page) <= scope:
        raise SplitByProject([{"project": p, "jql": page_jql(predicate, project=p)} for p in sorted(scope)])
    parts = [{"project": p, "jql": page_jql(predicate, project=p)} for p in on_page]
    parts.append({"project": None, "jql": page_jql(predicate, excluded=list(excluded) + on_page)})
    raise SplitByProject(parts)


# query_jira prints these base fields on every issue (title, the Type / Status line,
# the dates line, the Components / Labels / Fix Versions / Parent line); they cannot be
# chosen. Every other field is requested with ``extra_fields``.
BASE_FIELDS = ("summary", "issuetype", "status", "priority", "assignee", "reporter", "resolution",
               "created", "updated", "duedate", "resolved", "components", "labels", "fixVersions", "parent")
# The extra_fields each logical fetch requests: only what its checks read, with
# max_results 50 (PAGE_SIZE) on every page.
EXTRA_FIELDS = {
    # The release Feature gate also serves the refinement features, so it
    # requests their fields.
    "feature_gate": ("customfield_10855", "customfield_10795", "customfield_10470", "customfield_10473",
                     "customfield_10475"),
    "features": ("customfield_10795", "customfield_10855", "customfield_10470", "customfield_10473",
                 "customfield_10475"),
    "epics": ("customfield_10028", "customfield_10795", "customfield_10855", "customfield_10018",
              "customfield_10470"),
    "field_epics": ("customfield_10855", "customfield_10470"),
    "parents": ("customfield_10855", "customfield_10470"),
    "stories": ("customfield_10028", "customfield_10020"),
    "epic_issue_counts": (),
    "bugs": (),
    "spikes": (),
    "sprint_issues": ("customfield_10028", "customfield_10014", "customfield_10470"),
    "sprint_blocked": (),
    "blocks": (),
    "velocity": ("resolutiondate", "customfield_10028", "customfield_10470", "customfield_10020"),
}


def paging_round(pending, page_size=PAGE_SIZE):
    """The continuation decision for every page received in one paging round, in one
    sandbox call (a sandbox cannot call Jira: the pages are fetched by the tool calls
    of the round, then passed here).

    ``pending`` maps a name to ``{"jql": <query sent>, "page": <answer text, or
    {"error": ...}>, "retried": <bool>}``. Returns name → one of:

    * ``{"next": <query>}``: a whole page with more after it; send that query now;
    * ``{"done": True}``: a whole last page;
    * ``{"retry": <reason>}``: not whole, first time: send the same query again;
    * ``{"incomplete": <reason>}``: not whole after the retry (a page that did not advance
      included), or a query that cannot be paged (:class:`QueryError`, not sent again):
      keep the page and stop that part;
    * ``{"split": <parts>}``: page each project's part from its first page
      (:class:`SplitByProject`), and drop the pages of that query.
    """
    out = {}
    for name, item in pending.items():
        try:
            nxt = next_page_jql(item["jql"], item["page"], page_size)
            out[name] = {"next": nxt} if nxt else {"done": True}
        except PageNotWhole as exc:  # a page that did not advance too: sent again once
            out[name] = {"incomplete": str(exc)} if item.get("retried") else {"retry": str(exc)}
        except QueryError as exc:
            out[name] = {"incomplete": str(exc)}
        except SplitByProject as exc:
            out[name] = {"split": exc.parts}
    return out


def adapt_fetches(manifests, roster=None):
    """:func:`adapt_fetch` for every logical fetch that finished, in one call:
    ``{name: manifest}`` → ``{name: adapted}``. Each fetch is adapted once, from all
    its pages."""
    return {name: adapt_fetch(dict(manifest, name=manifest.get("name") or name), roster)
            for name, manifest in manifests.items()}


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

    A part of one project holds only that project's keys (one page or several, either
    order). Key order: every page ok; every page but the last is non-final ("more match"
    header, or a full page with no header); the last page is final ("Found N
    issues" header, or fewer than 50 issues); across pages, keys must stay in one
    project and strictly descend. Rank order: a single final page.
    """
    if not pages:
        return ["no pages fetched"]
    problems = [f"page {i}: {p['reason']}" for i, p in enumerate(pages, 1) if p["status"] != "ok"]
    if problems:
        return problems
    keys = [b["key"] for p in pages for b in p["blocks"]]
    projects = {_key_sort(k)[0] for k in keys}
    if project and projects - {project}:  # one page or several, a rank page too
        problems.append(f"issues from other projects in a {project}-only fetch")
    if order == "rank":
        if len(pages) != 1 or not pages[0]["final"]:
            problems.append("rank-ordered fetch did not fit on one page; rank order not available")
        return problems
    for i, page in enumerate(pages[:-1], 1):
        if page["final"]:
            problems.append(f"page {i} was the last page, but more pages were fetched")
    if not pages[-1]["final"]:
        problems.append("last page says more issues match; fetch stopped early")
    if len(pages) > 1:
        if not project and len(projects) > 1:
            problems.append("key paging spans several projects (Jira's 'key <' pages within one "
                            "project only); page each project separately")
        nums = [_key_sort(k) for k in keys]
        if any(a <= b for a, b in zip(nums, nums[1:])):
            problems.append("keys are not strictly descending across pages "
                            "(duplicate or out-of-order page)")
    return problems


# Parsed but read by no check or transform: two copies of an issue may differ in them.
UNREAD_FIELDS = ("reporter", "duedate")


def _differing_fields(kept, other):
    """The fields two copies of one issue (parsed blocks) disagree on: every field a check
    or transform reads (``updated`` too: staleness), not :data:`UNREAD_FIELDS`, and not a
    value that is not whole in either copy (a shortened value, an unreadable sprint): it
    cannot be compared, and :func:`adapt_fetch` reports it for any copy. Names only."""
    def not_whole(raw):
        sprint = raw.get("customfield_10020")
        unreadable = sprint is not None and parse_sprint_value(sprint)[1]
        return set(shortened_fields(raw)) | ({"customfield_10020"} if unreadable else set())

    def norm(value):
        return " ".join((_clean(value) or "").split())
    skip = {"key", *UNREAD_FIELDS} | not_whole(kept) | not_whole(other)
    return sorted(f for f in kept.keys() | other.keys() if f not in skip and norm(kept.get(f)) != norm(other.get(f)))


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
    page as complete. ``incomplete`` (the reason ``paging_round`` stopped that part,
    kept with its last page) makes the fetch incomplete with that reason.

    ``"pages"`` at the top level is shorthand for a single part. The fetch is
    ``complete`` only if every page succeeded and every part reached its last
    page; callers must not compute figures from an incomplete fetch. An issue listed by
    two of the fetch's queries or parts is kept once; if its copies differ in a field the
    report reads (Parent Link, status, …), the whole fetch is incomplete (a problem
    of the fetch, not of that issue: every part a shared fetch is split into), naming the
    issue and the field(s), never their values: which copy is current is unknown. A value
    that still looks shortened in a field the checks read
    (:data:`WHOLE_VALUE_FIELDS`, and the sprint), in any copy, makes the fetch incomplete.
    ``fetch.order`` is the order the pages were checked against: PM rank is
    available only from a complete ``"rank"`` fetch. A date
    that is not readable is dropped (never raises) and listed in ``left_out``
    (``[{"what", "why"}]``), which the caller adds to the report's Left out.
    """
    page_size = manifest.get("page_size") or PAGE_SIZE
    order = manifest.get("order") or "key"
    parts = manifest.get("parts")
    if parts is None:
        parts = [{"project": manifest.get("project"), "pages": manifest.get("pages") or []}]
    people = PeopleMatcher(roster)
    problems, unknown, issues, seen, date_problems, shortened = [], [], [], {}, [], []
    conflicts, copy_sprint = {}, {}
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
            if page.get("incomplete") and parsed[-1]["status"] == "ok":  # the reason paging stopped there
                parsed[-1].update(status="incomplete", reason=str(page["incomplete"]), final=False)
            rejected.update(parsed[-1].get("rejected", []))
            unknown_fields.update(parsed[-1].get("unknown_fields", []))
        page_count += len(parsed)
        label = part.get("project") or "fetch"
        problems.extend(f"{label}: {p}" for p in check_part(parsed, part.get("project"), order))
        for page in parsed:
            unknown.extend(page["unknown"])
            for raw in page["blocks"]:
                key = raw["key"]
                if key not in seen:
                    seen[key] = raw
                    issues.append(to_issue(raw, people, date_problems))
                else:  # a second copy (another query or part of this fetch), kept once
                    differ = _differing_fields(seen[key], raw)
                    # Which copy is current is unknown, so this concerns the whole fetch (not
                    # "KEY: ..."): a shared fetch's every part, wherever either copy would go.
                    if differ and key not in conflicts:
                        conflicts[key] = (f"copies of {key} in this fetch differ in "
                                          f"{', '.join(differ)} (it changed between reads)")
                    sprint = raw.get("customfield_10020")
                    why = parse_sprint_value(sprint)[1] if sprint is not None else None
                    if why:  # not comparable, so not whole either
                        copy_sprint.setdefault(key, why)
                cut = shortened_fields(raw)  # a value not read whole, in any copy (Jira paging step 7)
                note = f"{key}: {', '.join(cut)} value shortened (not read whole)"
                if cut and note not in shortened:
                    shortened.append(note)

    # An issue whose sprint value is not whole: its sprint data is incomplete, so
    # nothing that depends on sprints (velocity, activity) is computed from it.
    incomplete_why = {i["key"]: i["sprint_data_incomplete"]
                      for i in issues if i.get("sprint_data_incomplete")}
    sprint_incomplete = list(incomplete_why)
    problems.extend(f"{key}: {why}" for key, why in incomplete_why.items())
    problems.extend(f"{key}: {why}" for key, why in copy_sprint.items() if key not in incomplete_why)
    problems.extend(shortened)
    problems.extend(conflicts.values())
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
    if "description" in signals and issues and not any("description" in i for i in issues):
        skipped.append(SKIPPED_DESCRIPTION)
    warnings = []
    if unknown_fields:
        warnings.append(f"create metadata lists unknown field(s): {', '.join(sorted(unknown_fields))}")
    if unknown:
        labels = sorted({u for u in unknown if u not in ("(preamble)", "(unlabelled)")})
        warnings.append(f"ignored {len(unknown)} unrecognized line(s)"
                        + (f"; labels: {', '.join(labels)}" if labels else ""))
    # Unreadable dates are dropped, not guessed; the report lists them under Left out.
    left_out = [{"what": f"Unreadable dates in the {manifest.get('name') or 'fetch'} fetch "
                         f"({len({p.split()[0] for p in date_problems})} issue(s))",
                 "why": "; ".join(date_problems) + "; those dates are ignored"}] if date_problems else []
    return {
        "issues": issues,
        "fetch": {"name": manifest.get("name"), "order": order, "complete": not problems, "pages": page_count,
                  "fetched": len(issues), "problems": problems, "sprint_incomplete": sprint_incomplete},
        "skipped_signals": skipped,
        "people": {"matched": len(people.matched), "unmatched": sorted(people.unmatched)},
        "warnings": warnings,
        "left_out": left_out,
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
