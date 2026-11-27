#!/usr/bin/env python3
"""The weekly health report from fetched pages: the script chai-bot runs in its code sandbox.

chai-bot (the coordinator) fetches; this script decides what to fetch and builds the
report. They take turns, one *round* at a time:

1. ``step(state_dir)`` (no compact object yet) returns the first round's requests:
   ``{"status": "fetch", "round": 1, "requests": [{"id", "tool", "args"}, ...]}``.
2. The coordinator makes exactly those tool calls, **one at a time, in the listed order**,
   then calls ``compact(notes=..., match=<every tool of the round>, min_size=1)``. That
   stores the raw results as one object ``{"T1": {"tool", "result"}, "T2": ...}`` in call
   order, so nothing is retyped.
3. ``step(state_dir, compact_obj)`` (the object loaded with ``load_result_ids``) files each
   result under its request, then returns the next round, or
   ``{"status": "report", "text": ...}`` (the report with the scheduler's thread markers),
   or ``{"status": "incomplete", "text": "Weekly health report not posted: ..."}``.

Every round replays the report from the start with the answers received so far
(:class:`Engine`): a request whose answer is missing is asked for, and the run stops at
the end of that round. So the order and the content of the requests come from code, the
same on every run, and the coordinator follows a short loop.

Answers are kept as files under ``state_dir`` (the sandbox session directory: ``/tmp`` is
not writable there). Standard library only. The ``transform-*.py``, ``compute-velocity.py``
and ``run-checks.py`` scripts run in-process (``runpy``), not as subprocesses.
"""

import contextlib
import io
import json
import os
import re
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chai_jira_adapter as ad  # noqa: E402

BIN = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = "state.json"

# Tools the coordinator calls for this script; the compact ``match`` names them all.
TOOLS = ("query_jira", "get_jira_issue", "list_jira_board_sprints", "list_product_pages_entities",
         "browse_product_pages_schedule", "github_file_content", "resolve_identity",
         "get_jira_create_meta", "github_pr_checks", "result_head")
COMPACT_MATCH = ",".join(f"{t}*" for t in TOOLS)


class NeedRound(Exception):
    """The run needs answers it does not have yet: ``requests`` is the round to send."""

    def __init__(self, requests):
        super().__init__(f"{len(requests)} request(s) needed")
        self.requests = requests


class StateError(Exception):
    """The state directory or a compact object does not match the rounds sent."""


# ---------------------------------------------------------------- requests and state


def request_key(tool, args, attempt=1):
    """The identity of one tool call: the same call sent again (a retry) is ``attempt`` 2."""
    return json.dumps({"tool": tool, "args": args, "attempt": attempt}, sort_keys=True)


def load_state(state_dir):
    path = os.path.join(state_dir, STATE_FILE)
    if not os.path.exists(path):
        return {"rounds": [], "answers": {}}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state_dir, state):
    os.makedirs(state_dir, exist_ok=True)
    tmp = os.path.join(state_dir, STATE_FILE + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, sort_keys=True)
    os.replace(tmp, os.path.join(state_dir, STATE_FILE))


def _labels(compact_obj):
    """The compact object's labels in call order (T1, T2, ..., T10: numeric order)."""
    labels = [k for k in compact_obj if re.fullmatch(r"T\d+", k)]
    return sorted(labels, key=lambda k: int(k[1:]))


def load_round(state_dir, compact_obj):
    """File each result of a compact object under the request it answers.

    The last round sent must not be loaded yet. Its requests were made one at a time, in
    order, so label ``Tn`` answers request ``n``. A different number of results, or a
    result of another tool than its request, means the round cannot be matched (a
    result was compacted earlier, a call was left out or added): :class:`StateError`,
    never a guess."""
    state = load_state(state_dir)
    if not state["rounds"] or state["rounds"][-1].get("loaded"):
        raise StateError("no round is waiting for its results")
    rnd = state["rounds"][-1]
    if isinstance(compact_obj, str):
        try:
            compact_obj = json.loads(compact_obj)
        except ValueError as exc:
            raise StateError(f"round {rnd['round']}: the compact object is not readable JSON ({exc})") from None
    if not isinstance(compact_obj, dict):
        raise StateError(f"round {rnd['round']}: the compact object is not a JSON object")
    labels = _labels(compact_obj)
    if len(labels) != len(rnd["requests"]):
        raise StateError(f"round {rnd['round']}: the compact object holds {len(labels)} result(s), "
                         f"{len(rnd['requests'])} were requested")
    pages = os.path.join(state_dir, "answers")
    os.makedirs(pages, exist_ok=True)
    for label, req in zip(labels, rnd["requests"], strict=True):
        entry = compact_obj[label]
        tool = entry.get("tool") if isinstance(entry, dict) else None
        if tool != req["tool"]:
            raise StateError(f"round {rnd['round']}: {label} is a {tool} result, request {req['id']} "
                             f"is {req['tool']}")
        result = entry.get("result")
        if not isinstance(result, str):
            result = json.dumps(result)
        name = f"{req['id']}.txt"
        with open(os.path.join(pages, name), "w", encoding="utf-8") as fh:
            fh.write(result)
        state["answers"][req["key"]] = name
    rnd["loaded"] = True
    save_state(state_dir, state)


def new_round(state_dir, needed):
    """Record the requests of the next round and return them as the coordinator sends them."""
    state = load_state(state_dir)
    if state["rounds"] and not state["rounds"][-1].get("loaded"):
        raise StateError(f"round {state['rounds'][-1]['round']} has not been loaded yet")
    number = len(state["rounds"]) + 1
    requests = [{"id": f"r{number}.{n}", "tool": tool, "args": args, "key": request_key(tool, args, attempt)}
                for n, (tool, args, attempt) in enumerate(needed, 1)]
    state["rounds"].append({"round": number, "requests": requests, "loaded": False})
    save_state(state_dir, state)
    return number, [{k: r[k] for k in ("id", "tool", "args")} for r in requests]


def _not_posted(inputs):
    return ("Sprint-end check-in not posted" if inputs.get("mode") == "checkin"
            else "Weekly health report not posted")


def redo_round(state_dir, reason):
    """The last round once more, with all its calls, when its compact object did not match
    (a call left out, added or of another tool); None when it was already asked again."""
    state = load_state(state_dir)
    if not state["rounds"] or state["rounds"][-1].get("loaded"):
        return None
    rnd = state["rounds"][-1]
    if rnd.get("redone"):
        return None
    rnd["redone"] = reason
    save_state(state_dir, state)
    return {"status": "fetch", "round": rnd["round"], "redo": reason, "compact_match": COMPACT_MATCH,
            "requests": [{k: r[k] for k in ("id", "tool", "args")} for r in rnd["requests"]]}


class Answers:
    """The answers received so far, read from the state directory."""

    def __init__(self, state_dir):
        self.dir = state_dir
        self.files = load_state(state_dir)["answers"]

    def get(self, key):
        name = self.files.get(key)
        if name is None:
            return None
        with open(os.path.join(self.dir, "answers", name), encoding="utf-8") as fh:
            return fh.read()


# ---------------------------------------------------------------- tool answers


_SPRINT_LINE = re.compile(r"^- (?P<name>.+?) \((?P<details>[^()]*)\)\s*$")


def parse_board_sprints(text):
    """``list_jira_board_sprints``' text as ``scrum_context.board_sprints`` takes it.

    Each sprint is one line ``- <name> (id <n>, <state>, start <iso>, end <iso>[, completed
    <iso>][, origin board <n>])``. Returns ``(sprints, problem)``; ``problem`` names why
    the listing cannot be used (an error answer, no header, an unreadable line)."""
    text = ad.unwrap(text) if isinstance(text, str) else text
    if not isinstance(text, str) or not text.startswith("Board "):
        return None, f"board sprint fetch failed: {' '.join(str(text or 'no answer').split())[:200]}"
    sprints = []
    for line in text.splitlines()[1:]:
        if not line.startswith("- "):
            continue
        m = _SPRINT_LINE.match(line)
        if not m:
            return None, f"board sprint listing has an unreadable line: {line[:120]}"
        if not re.search(r"(?:^|,\s*)id \d+(?:,|$)", m.group("details")):
            return None, f"board sprint listing has a row without a sprint id: {line[:120]}"
        sprint = {"name": m.group("name")}
        for part in (p.strip() for p in m.group("details").split(",")):
            if part.startswith("id "):
                sprint["id"] = part[3:].strip()
            elif part.startswith("start "):
                sprint["start_date"] = part[6:].strip()
            elif part.startswith("end "):
                sprint["end_date"] = part[4:].strip()
            elif part.startswith("completed "):
                sprint["complete_date"] = part[10:].strip()
            elif part.startswith("origin board "):
                sprint["origin_board"] = part[13:].strip()
            elif part in ("active", "future", "closed"):
                sprint["state"] = part
        sprints.append(sprint)
    return sprints, None


def parse_json_answer(text):
    """A JSON tool answer (Product Pages, calendar, identity), or None when it is not JSON."""
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if isinstance(value, dict) and set(value) == {"result"} and isinstance(value["result"], str):
        with contextlib.suppress(ValueError):
            return json.loads(value["result"])
    return value


def as_page(text):
    """A query_jira answer as the adapter reads a page: the text, or ``{"error": <message>}``
    for a failed call (an ``{"error": ...}`` answer, or no answer)."""
    if text is None:
        return {"error": "no answer"}
    value = parse_json_answer(text) if text.lstrip().startswith("{") else None
    if isinstance(value, dict) and value.get("error") is not None and "result" not in value:
        return {"error": str(value["error"])}
    return text


def schedule_tasks(text):
    """``browse_product_pages_schedule``'s answer → its milestones (``[]``: listed, no dates), or
    ``{"error": ...}`` when the call failed or the answer cannot be read."""
    value = parse_json_answer(text)
    if isinstance(value, dict) and value.get("error") is not None:
        return {"error": str(value["error"])}
    if isinstance(value, dict):
        lists = [value[k] for k in ("data", "results", "tasks", "schedule", "items", "result")
                 if isinstance(value.get(k), list)]
        if not lists:
            return {"error": f"schedule answer not readable (keys: {', '.join(sorted(value)) or 'none'})"}
        value = lists[0]
    if not isinstance(value, list):
        return {"error": "schedule answer not readable"}
    keep = ("name", "date", "date_start", "date_finish", "start", "end")
    return [{k: t.get(k) for k in keep if t.get(k) is not None} for t in value if isinstance(t, dict)]


# A field value query_jira shortened at its inline limit, with where the whole value is:
# a stored result (up to 10 per call), else the get_jira_issue call that reads it whole.
_STORED_NOTE = re.compile(r" \(continues: [\d,]+ of [\d,]+ characters; the full value is (\[[^\]\s]+\]); "
                          r"read the rest with result_page or result_tail\)$")
_POINTER_NOTE = re.compile(r' \(cut at [\d,]+ of [\d,]+ characters; read it whole with '
                           r'get_jira_issue\("([A-Z][A-Z0-9_]*-\d+)", fields="([\w.]+)"\)\)$')
_FIELD_LINE = re.compile(r"^(\s+)([\w.]+): (.*)$")


def shortened_value_calls(text):
    """The reads that give the whole value of each shortened field on a query_jira page:
    ``[(line, call)]``, ``call`` a ``result_head`` of the stored value or a
    ``get_jira_issue(fields=...)``."""
    out = []
    for n, line in enumerate((text or "").splitlines()):
        m = _FIELD_LINE.match(line)
        if not m:
            continue
        stored, pointer = _STORED_NOTE.search(line), _POINTER_NOTE.search(line)
        if stored:
            out.append((n, ("result_head", {"result_id": stored.group(1), "count": 1000000, "as_chars": True})))
        elif pointer:
            out.append((n, ("get_jira_issue", {"issue_key": pointer.group(1), "fields": pointer.group(2)})))
    return out


def whole_value(call, text):
    """The whole value from a read's answer, or None (failed, cut, or not found)."""
    tool, args = call
    if tool == "result_head":
        value = parse_json_answer(text)
        if isinstance(value, dict) and isinstance(value.get("text"), str) and not value.get("truncated"):
            return value["text"]
        return None
    page = as_page(text)
    if isinstance(page, dict):
        return None
    lines = ad.unwrap(page).splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == "Requested fields:")
    except StopIteration:
        return None
    prefix = f"{args['fields']}: "
    for line in lines[start + 1:]:
        if line.startswith(prefix):
            value = line[len(prefix):]
            return None if _STORED_NOTE.search(" " + value) or _POINTER_NOTE.search(" " + value) else value
        if not line.strip():
            break
    return None


# ---------------------------------------------------------------- scripts, in-process


def run_script(script, *args):
    """Run one of the bin/ scripts in this process, as ``python <script> <args>`` would.

    The sandbox runs one Python process per session; scripts write their output files
    themselves. Returns the script's stdout; a non-zero exit raises RuntimeError."""
    argv, out = sys.argv, io.StringIO()
    sys.argv = [script, *[str(a) for a in args]]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            runpy.run_path(os.path.join(BIN, script), run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise RuntimeError(f"{script} exited with {exc.code}") from None
    finally:
        sys.argv = argv
    return out.getvalue()


# ---------------------------------------------------------------- the report's queries

import chai_report as cr  # noqa: E402
import scrum_context as sc  # noqa: E402
from _jira_transforms import extract_blocked_by  # noqa: E402

BOARD_ID = 11479
ROSTER_REPO, ROSTER_PATH = "openshift-eng/edge-context", "people/team-roster.md"
SPRINT_PROJECTS = "OCPEDGE, USHIFT, OCPBUGS"
EPIC_PROJECTS = "OCPEDGE, USHIFT"
GATE_JQL = ('project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") '
            'AND status != Closed AND "Target Version" is not EMPTY')
FEATURES_JQL = ('project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") '
                'AND "Target Version" = "openshift-{release}" '
                'AND (resolution is EMPTY OR resolution not in (Duplicate, Obsolete))')
BUGS_JQL = ('project = OCPBUGS AND component in ("Installer / Single Node OpenShift", "Two Node with Arbiter", '
            '"Two Node Fencing", "Logical Volume Manager Storage", "MicroShift", "MicroShift / Networking", '
            '"MicroShift / Storage") AND fixVersion in ("{release}", "{release}.0") AND "Epic Link" is EMPTY')
FIELD_EPICS_JQL = ('project = OCPEDGE AND issuetype = Epic AND status in ("To Do", "In Progress", '
                   '"Dev Complete", "Testing", "Release Pending") AND parent is not EMPTY')
EPIC_KEY_BATCH, PARENT_KEY_BATCH = 50, 40


def sprint_jql(sprint_id, projects=SPRINT_PROJECTS):
    return f"project in ({projects}) AND sprint = {sprint_id}"


def blocked_jql(sprint_id, projects=SPRINT_PROJECTS):
    return (f"({sprint_jql(sprint_id, projects)}) AND issueLinkType = \"is blocked by\" "
            "AND statusCategory != Done")


class Queries:
    """The report's Jira queries. One place, so tests can point them at synthetic projects."""

    def __init__(self, board_id=BOARD_ID, sprint_projects=SPRINT_PROJECTS, epic_projects=EPIC_PROJECTS,
                 gate_jql=GATE_JQL, features_jql=FEATURES_JQL, bugs_jql=BUGS_JQL,
                 field_epics_jql=FIELD_EPICS_JQL, story_projects=SPRINT_PROJECTS, spike_projects=EPIC_PROJECTS,
                 roster=(ROSTER_REPO, ROSTER_PATH)):
        self.board_id, self.sprint_projects, self.epic_projects = board_id, sprint_projects, epic_projects
        self.gate, self.features, self.bugs, self.field_epics = gate_jql, features_jql, bugs_jql, field_epics_jql
        self.story_projects, self.spike_projects, self.roster = story_projects, spike_projects, roster

    def sprint(self, sprint_id):
        return sprint_jql(sprint_id, self.sprint_projects)

    def blocked(self, sprint_id):
        return blocked_jql(sprint_id, self.sprint_projects)

    def ref_spikes(self, sprint_id):
        return f"project in ({self.spike_projects}) AND issuetype = Spike AND sprint = {sprint_id}"

    def epics(self, feature_keys):
        return f'project in ({self.epic_projects}) AND "Parent Link" in ({", ".join(feature_keys)})'

    def stories(self, epic_keys):
        return f"project in ({self.story_projects}) AND parent in ({', '.join(epic_keys)})"


class Incomplete(Exception):
    """A part of the report cannot be computed: it is left out with this reason."""


# ---------------------------------------------------------------- the engine

import velocity_cache as vc  # noqa: E402


def _empty_fetch(name):
    return {"issues": [], "fetch": {"name": name, "complete": True, "problems": [], "fetched": 0},
            "skipped_signals": [], "people": {}, "warnings": [], "left_out": []}


def _complete(name, adapted):
    if not adapted["fetch"]["complete"]:
        raise Incomplete(f"{name} fetch incomplete: {'; '.join(adapted['fetch']['problems'])}")
    return adapted


class Engine:
    """One weekly report, replayed from the answers received so far.

    The steps are the weekly report's shared fetch plan, ported from the chain
    test's ``WeeklyRun``: the board listing, roster, Product Pages and release Feature
    gate; then waves of Jira fetches, each built from the last one's results. Each call
    of :meth:`ask` is one round: the answers it needs are used if they were received,
    else the round is asked for (:class:`NeedRound`)."""

    def __init__(self, state_dir, inputs, queries=None):
        self.dir, self.q = state_dir, queries or Queries()
        self.answers = Answers(state_dir)
        self.today = inputs["today"]
        self.inputs = inputs
        self.work = os.path.join(state_dir, "work")
        os.makedirs(self.work, exist_ok=True)
        self.missing, self.left, self.read_cache = [], {}, {}
        self.read_tries = {}

    # -- rounds
    def ask(self, calls):
        """Answers for ``calls`` (``(tool, args, attempt)``), in order; NeedRound when any is missing."""
        got = [self.answers.get(request_key(t, a, n)) for t, a, n in calls]
        missing = [c for c, g in zip(calls, got, strict=True) if g is None]
        if missing:
            raise NeedRound(missing)
        return got

    # -- files for the bin/ scripts
    def write(self, name, data):
        out = os.path.join(self.work, name)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return out

    def read(self, name):
        with open(os.path.join(self.work, name), encoding="utf-8") as fh:
            return json.load(fh)

    def issues_file(self, name, adapted):
        return self.write(f"{name}_raw.json", {"issues": adapted["issues"]})

    def script(self, script, *args):
        run_script(script, *args)
        return args[list(args).index("--output") + 1]

    def take_left(self, *names):
        for name in names:
            self.missing += self.left.pop(name, [])

    # -- Jira paging, in lock-step rounds (the adapter's paging recipe)
    @staticmethod
    def _page_call(sent, kind, attempt):
        args = {"jql": sent, "max_results": ad.PAGE_SIZE}
        fields = ad.EXTRA_FIELDS.get(kind) or (("customfield_10020",) if kind == "checkin_sprint" else ())
        if fields:
            args["extra_fields"] = ", ".join(fields)
        return ("query_jira", args, attempt)

    def _read_calls(self, keys):
        """get_jira_issue(fields="issuelinks") for keys not read whole yet: attempt 1, then
        one retry of a failed or cut-off answer."""
        calls = []
        for key in dict.fromkeys(keys):
            if key in self.read_cache:
                continue
            calls.append(("get_jira_issue", {"issue_key": key, "fields": "issuelinks"},
                          self.read_tries.get(key, 1)))
        return calls

    def _take_reads(self, calls, answers):
        """File read answers; True when a read needs its retry (one more round)."""
        again = False
        for (_, args, attempt), text in zip(calls, answers, strict=True):
            key = args["issue_key"]
            page = as_page(text)
            failed = isinstance(page, dict)  # an error answer: no text to check
            whole = not failed and ad.issue_text_complete(ad.unwrap(page), key)
            if whole or attempt >= 2:
                # After its retry, an answer that is still cut off is kept: the adapter names
                # what is missing from it. A failed call is None ("get_jira_issue failed").
                self.read_cache[key] = None if failed else ad.unwrap(page)
            else:
                self.read_tries[key] = 2
                again = True
        return again

    def with_whole_values(self, pages):
        """Each page with its shortened field values read whole (Jira paging step 7): one more
        round for the reads, each sent again once when it fails. A value still not read whole
        stays shortened, and the adapter makes its fetch incomplete."""
        wanted = [(i, n, call) for i, text in enumerate(pages) if isinstance(text, str)
                  for n, call in shortened_value_calls(ad.unwrap(text))]
        if not wanted:
            return pages
        calls, got = [], {}
        for _, _, call in wanted:
            key = request_key(call[0], call[1], 1)
            first = self.answers.get(key)
            if first is not None and whole_value(call, first) is not None:
                got[key] = whole_value(call, first)
                continue
            attempt = 2 if first is not None else 1
            if attempt == 2:
                again = self.answers.get(request_key(call[0], call[1], 2))
                if again is not None:
                    got[key] = whole_value(call, again)  # None: still not whole after its retry
                    continue
            calls.append((call[0], call[1], attempt))
        if calls:  # one read per stored value, even when two pages name it
            raise NeedRound(list({request_key(*c): c for c in calls}.values()))
        out = list(pages)
        for i, n, call in wanted:
            value = got.get(request_key(call[0], call[1], 1))
            if value is None:
                continue
            lines = ad.unwrap(out[i]).splitlines()
            m = _FIELD_LINE.match(lines[n])
            lines[n] = f"{m.group(1)}{m.group(2)}: {value}"
            out[i] = "\n".join(lines)
        return out

    def wave(self, specs, reads=()):
        """Send ``specs`` (logical fetches; ``logical`` joins several into one) and ``reads``
        together; page every part in lock-step rounds; return the adapted fetches by
        logical name. A rank page that says more match goes on by key."""
        state, members = {}, {}
        for spec in specs:
            first = (f"({spec['jql']}) ORDER BY Rank ASC" if spec.get("order") == "rank"
                     else ad.page_jql(spec["jql"]))
            state[spec["name"]] = {"spec": spec, "slots": [{"project": None, "sent": first, "pages": [],
                                                            "retried": False, "done": False}]}
            members.setdefault(spec.get("logical") or spec["name"], []).append(spec["name"])
        pending_reads = list(reads)
        rank_more, adapted = set(), {}
        while True:
            open_slots = [(n, s) for n, st in state.items() for s in st["slots"] if not s["done"]]
            read_calls = self._read_calls(pending_reads)
            if not open_slots and not read_calls:
                break
            page_calls = [self._page_call(s["sent"], state[n]["spec"].get("kind", n), 2 if s["retried"] else 1)
                          for n, s in open_slots]
            answers = self.ask(page_calls + read_calls)
            if read_calls:
                self._take_reads(read_calls, answers[len(page_calls):])
            answers[:len(page_calls)] = self.with_whole_values(answers[:len(page_calls)])
            pending = {f"{n}/{s['project'] or 'other'}#{i}": {"jql": s["sent"], "page": as_page(answers[i]),
                                                              "retried": s["retried"]}
                       for i, (n, s) in enumerate(open_slots)}
            slots = dict(zip(pending, open_slots, strict=True))
            for key, decision in ad.paging_round(pending).items():
                name, slot = slots[key]
                page = pending[key]["page"]
                st = state[name]
                entry = (dict(page, jql=slot["sent"]) if isinstance(page, dict)
                         else {"text": page, "jql": slot["sent"]})
                if "retry" in decision:
                    slot["retried"] = True
                    continue
                if "split" in decision:
                    at = st["slots"].index(slot)
                    st["slots"][at:at + 1] = [{"project": p["project"], "sent": p["jql"], "pages": [],
                                               "retried": False, "done": False} for p in decision["split"]]
                    continue
                if "next" in decision and st["spec"].get("order") == "rank" and name not in rank_more:
                    rank_more.add(name)
                    slot["sent"], slot["retried"] = decision["next"], False
                    continue
                if "incomplete" in decision:
                    entry = dict(entry, incomplete=decision["incomplete"])
                slot["pages"].append(entry)
                if "next" in decision:
                    slot["sent"], slot["retried"] = decision["next"], False
                else:
                    slot["done"] = True
            finished = {}
            for lname, names in members.items():
                if lname not in adapted and all(sl["done"] for n in names for sl in state[n]["slots"]):
                    order = "rank" if any(state[n]["spec"].get("order") == "rank" and n not in rank_more
                                          for n in names) else "key"
                    parts = [{"project": sl["project"], "pages": sl["pages"]}
                             for n in names for sl in state[n]["slots"]]
                    finished[lname] = {"name": lname, "order": order, "parts": parts}
            adapted.update(ad.adapt_fetches(finished, self.roster) if finished else {})
        for lname, out in adapted.items():
            self.left[lname] = out["left_out"]
        return adapted

    # -- inputs read once: board listing, roster, Product Pages
    def board_listing(self):
        call = ("list_jira_board_sprints", {"board_id": str(self.q.board_id), "max_results": 100}, 1)
        sprints, problem = parse_board_sprints(self.ask([call])[0])
        if problem:
            sprints, problem = parse_board_sprints(self.ask([call[:2] + (2,)])[0])
        self.board_problem = problem
        return sprints or []

    def ask_retrying(self, calls, ok):
        """Answers for ``calls`` (``(tool, args)``): each call whose answer is not ``ok`` is sent
        again once, and its second answer is used."""
        first = self.ask([(t, a, 1) for t, a in calls])
        again = [i for i, text in enumerate(first) if not ok(text)]
        if not again:
            return first
        second = self.ask([(calls[i][0], calls[i][1], 2) for i in again])
        out = list(first)
        for i, text in zip(again, second, strict=True):
            out[i] = text
        return out

    @staticmethod
    def _roster_text(text):
        value = parse_json_answer(text)
        if isinstance(value, dict):
            if value.get("error") is not None:
                return None
            text = value.get("content") or value.get("text") or value.get("result")
        return text if isinstance(text, str) and text.strip() else None

    def read_roster(self):
        repo, path = self.q.roster

        def ok(text):
            roster = ad.parse_team_roster(self._roster_text(text) or "")
            return bool(roster.get("members"))
        text = self.ask_retrying([("github_file_content", {"repo": repo, "path": path})], ok)[0]
        roster = ad.parse_team_roster(self._roster_text(text) or "")
        if not roster.get("members"):
            raise Incomplete(f"team roster ({repo}: {path}) not readable")
        return roster

    def _pp(self, args):
        """One Product Pages listing page, sent again once when it fails or is not readable;
        None when the second answer is not readable either."""
        def readable(page):
            cursor = page.get("cursor", "") if isinstance(page, dict) else ""
            return (isinstance(page, dict) and isinstance(page.get("data"), list) and "cursor" in page
                    and (cursor is None or (isinstance(cursor, (str, int)) and not isinstance(cursor, bool)
                                            and str(cursor) != "")))
        page = parse_json_answer(self.ask([("list_product_pages_entities", args, 1)])[0])
        if not readable(page):
            page = parse_json_answer(self.ask([("list_product_pages_entities", args, 2)])[0])
            if isinstance(page, dict) and page.get("error") is not None:  # failed again: the lookup failed
                raise Incomplete(f"the call failed again after its retry: {' '.join(str(page['error']).split())}")
        return page if readable(page) else None

    def _pp_every_page(self, **filters):
        entities, seen, page = [], set(), self._pp(dict(filters))
        while True:
            if page is None:
                raise Incomplete("a Product Pages listing page still not readable after its retry")
            entities += page["data"]
            cursor = page["cursor"]
            if cursor is None:
                return entities
            if cursor in seen:
                raise Incomplete("a Product Pages listing cursor repeated")
            seen.add(cursor)
            page = self._pp(dict(filters, cursor=cursor))

    def schedules(self, candidates):
        """Each candidate release's milestones and the previous release's (scrum-context
        step 2): the milestone list, ``[]`` (not listed, no dates) or ``{"error": ...}``."""
        if not candidates:
            return {}
        try:
            products = [p for p in self._pp_every_page(kind="product", shortname="openshift")
                        if isinstance(p, dict) and p.get("shortname") == "openshift"]
            if len(products) != 1 or type(products[0].get("id")) is not int:
                raise Incomplete("no single OpenShift product with an integer id")
            releases = self._pp_every_page(kind="release", public_parent_id=products[0]["id"])
        except Incomplete as exc:
            return {r: {"error": str(exc)} for r in candidates}
        listed = {}
        for e in releases:
            name = str((e or {}).get("shortname") or "")
            m = re.fullmatch(r"openshift-(\d+\.\d+)", name)
            if m and type(e.get("id")) is int:
                listed[m.group(1)] = e["id"]
        needed = set(candidates)
        for release in candidates:
            lower = [r for r in listed if sc.parse_version(r) < sc.parse_version(release)]
            if lower:
                needed.add(max(lower, key=sc.parse_version))
        needed = sorted(needed, key=sc.parse_version)
        calls = [("browse_product_pages_schedule", {"entity_id": listed[r]}) for r in needed if r in listed]
        answers = (dict(zip([r for r in needed if r in listed],
                            self.ask_retrying(calls, lambda t: not isinstance(schedule_tasks(t), dict)), strict=True))
                   if calls else {})
        out = {}
        for r in needed:
            if r not in listed:
                out[r] = []
                continue
            out[r] = schedule_tasks(answers[r])
        return out

    # -- computations (ported from the chain test's WeeklyRun)
    def velocity_from(self, closed, hits, to_fetch, fetched, digest):
        files = []
        self.take_left(*[f"velocity_{s['number']}" for s in to_fetch])
        for s in to_fetch:
            out = fetched[f"velocity_{s['number']}"]
            if not out["fetch"]["complete"]:
                return None, f"velocity sprint {s['number']} fetch incomplete: {'; '.join(out['fetch']['problems'])}"
        for s in to_fetch:
            n = s["number"]
            meta = self.write(f"meta_{n}.json", {"sprint_id": int(s["id"]), "sprint_name": s["name"],
                                                 "sprint_start": s["start"].isoformat(),
                                                 "sprint_end": s["end"].isoformat()})
            files.append(self.script("transform-sprint-issues.py", "--input",
                                     self.issues_file(f"v{n}", fetched[f"velocity_{n}"]), "--sprint-meta", meta,
                                     "--today", self.today, "--output", os.path.join(self.work, f"v{n}.json")))
        fresh = {"sprints": []}
        if files:
            with open(self.script("compute-velocity.py", "--sprint-issues", *files, "--roster", self.roster_file,
                                  "--output", os.path.join(self.work, "velocity.json")), encoding="utf-8") as fh:
                fresh = json.load(fh)
        return cr.usable_velocity(vc.combine(closed, hits, fresh))

    def sprint_from(self, fetched, velocity, reason):
        cur = self.ctx["current_sprint"]
        mode = cr.sprint_mode(cur["week"])
        self.take_left("sprint_issues")
        try:
            adapted = _complete("sprint issue", fetched["sprint_issues"])
        except Incomplete as exc:
            self.missing.append({"what": cr.SPRINT_HEALTH, "why": str(exc)})
            return None
        links_complete, links_problems = True, []
        if mode == "mid-sprint":
            self.take_left("sprint_blocked")
            candidates = fetched["sprint_blocked"]
            problems = ad.attach_issue_links(adapted, {i["key"]: self.read_cache.get(i["key"])
                                                       for i in candidates["issues"]})
            links_complete = candidates["fetch"]["complete"] and not problems
            if not candidates["fetch"]["complete"]:
                problems = [f"blocked items fetch incomplete: {'; '.join(candidates['fetch']['problems'])}", *problems]
            links_problems = problems
        meta = self.write("meta.json", {"sprint_id": int(cur["id"]), "sprint_name": cur["name"],
                                        "sprint_start": cur["start"], "sprint_end": cur["end"]})
        out = self.script("transform-sprint-issues.py", "--input", self.issues_file("sprint", adapted),
                          "--sprint-meta", meta, "--today", self.today,
                          "--output", os.path.join(self.work, "sprint_issues.json"))
        with open(out, encoding="utf-8") as fh:
            issues = json.load(fh)
        return cr.sprint_health(issues, cur, self.today, mode, velocity, self.ctx["current_sprint_availability"],
                                adapted["skipped_signals"], links_complete, velocity_reason=reason,
                                links_problems=links_problems)

    def spikes_from(self, tag, features_fetch, spikes_fetch, refinement_closed):
        _complete("refinement-sprint spikes", spikes_fetch)
        spikes = json.loads(json.dumps(spikes_fetch))
        problems = ad.attach_issue_links(spikes, {i["key"]: self.read_cache.get(i["key"]) for i in spikes["issues"]})
        if problems:
            raise Incomplete(f"spike links incomplete: {'; '.join(problems)}")
        self.take_left(*self.linked_batches)
        linked = {f["key"]: self.linked[f["key"]] for f in features_fetch["issues"]}
        ad.attach_linked_spike_fetches(features_fetch, linked)
        _complete("features", features_fetch)
        all_linked = [i for fetch in linked.values() for i in fetch["issues"]]
        sprints = self.write(f"sprints_{tag}.json", {"refinement_sprint_closed": refinement_closed})
        features = self.script("transform-features.py", "--input", self.issues_file(f"features_{tag}", features_fetch),
                               "--output", os.path.join(self.work, f"features_{tag}.json"))
        out = self.script("transform-spikes.py", "--input", self.issues_file(f"spikes_{tag}", spikes),
                          "--linked-spikes", self.write(f"linked_raw_{tag}.json", {"issues": all_linked}),
                          "--features-file", features, "--epics-file", os.path.join(self.work, f"epics_{tag}.json"),
                          "--sprints-file", sprints, "--output", os.path.join(self.work, f"spikes_{tag}.json"))
        return self.read(f"features_{tag}.json"), self.read(os.path.basename(out))

    def epics_of(self, tag, part):
        self.take_left("epics")
        _complete("epics", part)
        self.script("transform-epics.py", "--input", self.issues_file(f"epics_{tag}", part),
                    "--output", os.path.join(self.work, f"epics_{tag}.json"))
        return self.read(f"epics_{tag}.json")

    def refinement_from(self, sec, f, data):
        tag = sec["release"]
        _complete("features", f)
        epics = self.epics_of(tag, data["epics"][tag])
        self.take_left("stories")
        mine = [i["key"] for i in data["epics"][tag]["issues"]]
        counts, why = cr.epic_issue_counts(data["fetched"].get("stories"), mine, data["all_epics"])
        self.take_left(data["names"]["ref_spikes"][tag])
        state = next((s.get("state") for s in self.listing
                      if str(s.get("id")) == str(sec["window"]["refinement_sprint"]["id"])), None)
        features, spikes = self.spikes_from(tag, f, data["fetched"][data["names"]["ref_spikes"][tag]],
                                            state == "closed")
        return cr.refinement_gaps(features, spikes, sec, state == "closed", epics=epics, epic_issue_counts=counts,
                                  week=self.ctx["current_sprint"]["week"], epic_issue_counts_why=why)

    def release_from(self, sec, f, data, velocity, reason):
        tag, window = sec["release"], sec["window"]
        self.take_left(data["names"]["features"][tag])
        _complete("features", f)
        epics = self.epics_of(tag, data["epics"][tag])
        epics_file = os.path.join(self.work, f"epics_{tag}.json")
        self.take_left("stories")
        stories = _complete("stories", data["stories"][tag])
        stories_file = self.script("transform-stories.py", "--input", self.issues_file(f"stories_{tag}", stories),
                                   "--today", self.today, "--output", os.path.join(self.work, f"stories_{tag}.json"))
        bugs_name = data["names"]["bugs"][tag]
        self.take_left(bugs_name)
        bugs = _complete("bugs", data["fetched"][bugs_name])
        bugs_file = self.script("transform-bugs.py", "--input", self.issues_file(f"bugs_{tag}", bugs),
                                "--today", self.today, "--output", os.path.join(self.work, f"bugs_{tag}.json"))
        bc = window["branch_cut_sprint"]["number"]
        entries = [s for s in self.release_results[tag]["sprint_availability"] if s["number"] <= bc]
        capacity = cr.release_capacity(velocity, entries, self.today)
        self.take_left(data["names"]["ref_spikes"][tag])
        features, spikes = self.spikes_from(tag, f, data["fetched"][data["names"]["ref_spikes"][tag]], True)
        args = ["--features", os.path.join(self.work, f"features_{tag}.json"), "--epics", epics_file,
                "--stories", stories_file, "--bugs", bugs_file, "--roster", self.roster_file,
                "--remaining-sprints", str(capacity["remaining_sprints"]),
                "--availability", capacity["availability_csv"],
                "--version", tag, "--today", self.today, "--first-sprint", str(window["refinement_sprint"]["number"]),
                "--pencils-down", str(bc), "--focus-priorities", "Blocker,Critical,Major",
                "--include-lower-when", "Refinement", "--no-hidden-scope-estimate",
                "--output", os.path.join(self.work, f"checks_{tag}.json")]
        if velocity is not None:
            args += ["--velocity-file", self.write("release_velocity.json",
                                                   {"team_velocity": velocity, "sprint_count": 3})]
        checks = self.read(os.path.basename(self.script("run-checks.py", *args)))
        self.take_left("blocks")
        blocks, keys = data["fetched"].get("blocks"), set(data["block_keys"][tag])
        links, unread = None, []
        problems = [f"block candidates fetch incomplete: {'; '.join(blocks['fetch']['problems'])}"] if blocks else []
        if not keys:
            links, problems = {}, []
        elif blocks and blocks["fetch"]["complete"]:
            candidates = [i for i in json.loads(json.dumps(blocks["issues"])) if i["key"] in keys]
            part = {"issues": candidates, "fetch": dict(blocks["fetch"])}
            problems = ad.attach_issue_links(part, {i["key"]: self.read_cache.get(i["key"]) for i in candidates})
            unread = sorted({p.split(":")[0] for p in problems})
            links = {i["key"]: extract_blocked_by(i) for i in candidates if i["key"] not in unread}
        return cr.release_health(checks, sec, capacity, cr.rank_order_available(f), reason,
                                 features=features, epics=epics, spikes=spikes,
                                 stories=self.read(f"stories_{tag}.json"), links=links,
                                 links_unread=unread, current_sprint_number=self.ctx["current_sprint"]["number"],
                                 links_problems=problems)

    # -- the report
    def run(self):
        """The report: ``{"status": "report", "text"}`` or ``{"status": "incomplete", "text"}``.
        Raises NeedRound while answers are missing."""
        q = self.q
        # Round 1: everything that needs nothing else (one tool round).
        self.ask([("list_jira_board_sprints", {"board_id": str(q.board_id), "max_results": 100}, 1),
                  ("github_file_content", {"repo": q.roster[0], "path": q.roster[1]}, 1),
                  ("list_product_pages_entities", {"kind": "product", "shortname": "openshift"}, 1),
                  self._page_call(ad.page_jql(q.gate), "feature_gate", 1)])
        self.listing = self.board_listing()
        try:
            self.roster = self.read_roster()
        except Incomplete as exc:
            self.roster, self.roster_problem = None, str(exc)
        roster = self.roster or {"members": [], "contacts": []}
        self.roster_file = self.write("roster.json", {"members": roster["members"], "contacts": roster["contacts"]})
        fetched = self.wave([{"name": "feature_gate", "kind": "feature_gate", "jql": q.gate}])
        gate_fetch = fetched["feature_gate"]
        self.take_left("feature_gate")
        gate = cr.release_feature_gate(gate_fetch)
        candidates = gate["releases"]
        base = {"today": self.today, "board_id": q.board_id, "schedules": self.schedules(candidates),
                "sprints": self.listing}
        for key in ("absences", "absence_window", "engineer_count", "events"):
            if self.inputs.get(key) is not None:
                base[key] = self.inputs[key]
        self.ctx = sc.build_context(base)
        self.release_results = {r: sc.build_context(dict(base, release=r)) for r in candidates}
        windows = [result["release"] for result in self.release_results.values()]
        sections, missing = cr.select_sections(self.ctx, windows, gate)
        self.missing += missing
        cur = self.ctx["current_sprint"]
        has_sprint = isinstance(cur, dict) and bool(cur.get("id"))
        number = cur.get("number") if isinstance(cur, dict) else None
        closed = sc.last_closed_sprints(self.listing, q.board_id, number) if number is not None else []
        digest = vc.roster_digest(roster)
        hits, to_fetch = vc.plan(closed, None, q.board_id, digest)
        mode = cr.sprint_mode(cur["week"]) if has_sprint else None
        releases = [s for s in sections if s["kind"] != cr.REFINEMENT]
        many = len(sections) > 1
        names = {"features": {}, "bugs": {}, "ref_spikes": {}}

        def named(base_name, tag):
            names[base_name][tag] = f"{base_name}_{tag}" if many else base_name
            return names[base_name][tag]
        # Wave 1: velocity, sprint issues and blocked candidates, each release's rank-ordered
        # features page and bugs, refinement-sprint spikes.
        specs = ([{"name": f"velocity_{s['number']}", "kind": "velocity", "jql": q.sprint(s["id"])}
                  for s in to_fetch] if self.roster else [])
        if has_sprint:
            specs.append({"name": "sprint_issues", "kind": "sprint_issues", "jql": q.sprint(cur["id"])})
        if mode == "mid-sprint":
            specs.append({"name": "sprint_blocked", "kind": "sprint_blocked", "jql": q.blocked(cur["id"])})
        for sec in releases:
            tag = sec["release"]
            specs.append({"name": named("features", tag), "kind": "features", "order": "rank",
                          "jql": q.features.format(release=tag)})
            specs.append({"name": named("bugs", tag), "kind": "bugs", "jql": q.bugs.format(release=tag)})
        for sec in sections:
            ref = sec["window"]["refinement_sprint"]
            specs.append({"name": named("ref_spikes", sec["release"]), "kind": "spikes",
                          "jql": q.ref_spikes(ref["id"])})
        fetched = self.wave(specs)
        if self.roster:
            velocity, reason = self.velocity_from(closed, hits, to_fetch, fetched, digest)
        else:
            velocity, reason = None, self.roster_problem
        sprint_ok = has_sprint and fetched["sprint_issues"]["fetch"]["complete"]
        reads = [i["key"] for i in fetched["sprint_blocked"]["issues"]] if mode == "mid-sprint" and sprint_ok else []
        for sec in sections:
            spikes = fetched[names["ref_spikes"][sec["release"]]]
            reads += [i["key"] for i in spikes["issues"]] if spikes["fetch"]["complete"] else []
        feats = {}
        for sec in sections:
            tag = sec["release"]
            feats[tag] = (cr.gate_features(gate_fetch, tag) if sec["kind"] == cr.REFINEMENT
                          else fetched[names["features"][tag]])
        # Wave 2: one release epics fetch, the decision 0024 epics, the spikes blocking each
        # feature, and the links of the blocked candidates and refinement-sprint spikes.
        keys_by_rel = {t: [i["key"] for i in f["issues"]] for t, f in feats.items() if f["fetch"]["complete"]}
        all_keys = sorted(set().union(*keys_by_rel.values()), key=cr._key_number) if keys_by_rel else []
        chunks = [all_keys[n:n + EPIC_KEY_BATCH] for n in range(0, len(all_keys), EPIC_KEY_BATCH)]
        specs = [{"name": f"epics_{n + 1}" if n else "epics", "logical": "epics", "kind": "epics",
                  "jql": q.epics(chunk)} for n, chunk in enumerate(chunks)]
        specs.append({"name": "field_epics", "kind": "field_epics", "jql": q.field_epics})
        linked_chunks, seen = [], set()
        for t in keys_by_rel:
            mine = [i["key"] for i in feats[t]["issues"] if i["key"] not in seen]
            seen.update(mine)
            linked_chunks += [mine[n:n + ad.LINKED_SPIKE_BATCH] for n in range(0, len(mine), ad.LINKED_SPIKE_BATCH)]
        self.linked_batches = {"linked" if n == 0 else f"linked_{n + 1}": chunk
                               for n, chunk in enumerate(linked_chunks)}
        specs += [{"name": name, "kind": "spikes", "jql": ad.linked_spikes_jql(chunk)}
                  for name, chunk in self.linked_batches.items()]
        more = self.wave(specs, reads=reads)
        parts = cr.partition_epics(more.get("epics"), keys_by_rel)
        epic_keys = {t: [i["key"] for i in part["issues"]] for t, part in parts.items()}
        # Wave 3: the stories, the block candidates, the epic-field parents not already read,
        # and the links of the linked spikes not read yet.
        all_epics = list(dict.fromkeys(k for t in epic_keys for k in epic_keys[t]))
        specs = []
        if all_epics:
            specs.append({"name": "stories", "kind": "stories", "jql": q.stories(all_epics)})
        block_keys = {s["release"]: keys_by_rel.get(s["release"], []) + epic_keys.get(s["release"], [])
                      for s in releases}
        all_block = list(dict.fromkeys(k for t in block_keys for k in block_keys[t]))
        if all_block:
            specs.append({"name": "blocks", "kind": "blocks",
                          "jql": f"key in ({', '.join(all_block)}) AND issueLinkType = \"is blocked by\""})
        field_parents = sorted({i["parent"]["key"] for i in more["field_epics"]["issues"]
                                if (i.get("parent") or {}).get("key")}, key=cr._key_number)
        reuse_from = [f for f in feats.values() if f["fetch"]["complete"]] + [gate_fetch]
        reused, to_read = cr.reuse_parents(field_parents, reuse_from)
        parent_chunks = [to_read[n:n + PARENT_KEY_BATCH] for n in range(0, len(to_read), PARENT_KEY_BATCH)]
        for n, chunk in enumerate(parent_chunks):
            specs.append({"name": "parents" if n == 0 else f"parents_{n + 1}", "kind": "parents",
                          "jql": f"key in ({', '.join(chunk)})"})
        spike_reads = [i["key"] for name in self.linked_batches for i in more[name]["issues"]
                       if i["key"] not in self.read_cache]
        later = self.wave(specs, reads=spike_reads) if specs or spike_reads else {}
        self.linked = {}
        for name, chunk in self.linked_batches.items():
            self.linked.update(ad.split_linked_spikes(chunk, more[name], self.read_cache))
        stories = cr.group_stories(later.get("stories", _empty_fetch("stories")), epic_keys)
        # Wave 4: the links of the block candidates.
        if later.get("blocks") and later["blocks"]["fetch"]["complete"]:
            self.wave([], reads=[i["key"] for i in later["blocks"]["issues"]])
        if has_sprint:
            sprint = self.sprint_from(fetched, velocity, reason)
        else:
            sprint = None
            self.missing.append({"what": cr.SPRINT_HEALTH, "why": (
                self.board_problem or "current sprint not on the board yet")})
        data = {"fetched": {**fetched, **later}, "epics": parts, "stories": stories, "names": names,
                "all_epics": all_epics, "block_keys": block_keys}
        computed = []
        for sec in sections:
            try:
                f = feats.get(sec["release"])
                if f is None:
                    raise Incomplete("features fetch incomplete")
                computed.append(self.refinement_from(sec, f, data) if sec["kind"] == cr.REFINEMENT
                                else self.release_from(sec, f, data, velocity, reason))
            except Incomplete as exc:
                self.missing.append({"what": f"{sec['release']} release section", "why": str(exc)})
        self.take_left("field_epics", *[f"parents_{n + 1}" if n else "parents" for n in range(len(parent_chunks))])
        batches = ([reused] if reused else []) + [
            {"keys": chunk, "result": later[f"parents_{n + 1}" if n else "parents"]}
            for n, chunk in enumerate(parent_chunks)]
        gaps = cr.epic_field_gaps(more["field_epics"], batches)
        scrum_master = self.inputs.get("scrum_master")
        mentions = self.mentions(sprint, computed, gaps, scrum_master)
        failure = cr.sprint_health_failure(sprint, self.missing)
        if failure is not None:
            return {"status": "incomplete", "text": failure}
        report = cr.build_report(sprint, computed, self.missing, mentions, team_owner=scrum_master, epic_gaps=gaps)
        return {"status": "report", "text": thread_marked(report["top_post_text"], report["replies"])}

    def mentions(self, sprint, computed, gaps, scrum_master):
        """Slack mentions for the report's owners who are on the roster: one
        ``resolve_identity`` round by roster email. Others stay plain names."""
        owners = report_owners(sprint, computed, gaps, scrum_master)
        from _jira_transforms import normalize_name
        contacts = {normalize_name(c["display_name"]): c for c in (self.roster or {}).get("contacts", [])}
        emails = {o: contacts[normalize_name(o)]["email"] for o in sorted(owners)
                  if normalize_name(o) in contacts and contacts[normalize_name(o)].get("email")}
        if not emails:
            return {}
        calls = [("resolve_identity", {"identifier": e}) for e in emails.values()]
        out = {}
        for owner, text in zip(emails, self.ask_retrying(calls, identity_answered), strict=True):
            value = parse_json_answer(text)
            mention = value.get("slack_mention") if isinstance(value, dict) else None
            if isinstance(mention, str) and re.fullmatch(r"<@[UW][A-Z0-9]+>", mention):
                out[owner] = mention
        return out


def identity_answered(text):
    """A resolve_identity answer (a person found or not found), not a failed call."""
    value = parse_json_answer(text)
    return isinstance(value, dict) and value.get("error") is None and (
        value.get("status") in ("ok", "not_found") or "slack_mention" in value)


def report_owners(sprint, computed, gaps, scrum_master=None):
    """Everyone the report names as an owner: sprint and section items, epic-field gaps, and
    the Scrum Master (team items) when one is set."""
    parts = ([sprint] if sprint else []) + computed
    owners = {i["owner"] for part in parts for i in part["items"] if i.get("owner")}
    owners |= {epic["owner"] for epic in gaps["epics"] if epic["owner"]}
    if scrum_master:
        owners.add(scrum_master)
    return owners


THREAD_DETAILS, THREAD_BREAK = "---THREAD_DETAILS---", "---THREAD_BREAK---"


def thread_marked(top_post, replies):
    """One text the scheduler posts as a top post and its thread replies."""
    if not replies:
        return top_post
    return f"{top_post}\n{THREAD_DETAILS}\n" + f"\n{THREAD_BREAK}\n".join(replies)


# ---------------------------------------------------------------- entry point


def step(state_dir, compact_obj=None, inputs=None, queries=None, mode=None):
    """One turn of the loop: file the last round's results (``compact_obj``), then return
    the next round, the report, or the reason it cannot be posted."""
    os.makedirs(state_dir, exist_ok=True)
    inputs_file = os.path.join(state_dir, "inputs.json")
    if inputs is not None and mode:
        inputs = dict(inputs, mode=mode)
    if inputs is not None:
        with open(inputs_file, "w", encoding="utf-8") as fh:
            json.dump(inputs, fh)
    with open(inputs_file, encoding="utf-8") as fh:
        inputs = json.load(fh)
    if compact_obj is not None:
        try:
            load_round(state_dir, compact_obj)
        except StateError as exc:
            redo = redo_round(state_dir, str(exc))
            if redo is None:
                return {"status": "incomplete", "text": f"{_not_posted(inputs)}: {exc}"}
            return redo
    mode = mode or inputs.get("mode") or "weekly"
    engine = CheckinEngine if mode == "checkin" else Engine
    try:
        return engine(state_dir, inputs, queries).run()
    except NeedRound as need:
        number, requests = new_round(state_dir, need.requests)
        return {"status": "fetch", "round": number, "requests": requests, "compact_match": COMPACT_MATCH}


# ---------------------------------------------------------------- the sprint-end check-in

CHECKIN_FILTER = 104882
CHECKIN_PROJECTS = ("OCPEDGE", "USHIFT", "OCPBUGS")
NOT_CODE_REPOS = {"openshift-eng/edge-context"}  # decisions and documentation, not code
_PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)\b")
JIRA_BROWSE = "https://redhat.atlassian.net/browse/"


def is_done(issue):
    """Done: Closed; an OCPBUGS issue also when Verified or Release Pending (QE decides; Release
    Pending counts as done, as in the report)."""
    status = ((issue.get("status") or {}).get("name") or "").strip()
    return status == "Closed" or (issue["key"].startswith("OCPBUGS-") and status in ("Verified", "Release Pending"))


def remote_pr_links(text):
    """The GitHub pull requests among ``get_jira_issue``'s Remote Links: ``[(repo, number)]``."""
    out, inside = [], False
    for line in (text or "").splitlines():
        if re.fullmatch(r"\s*Remote Links:\s*", line):
            inside = True
            continue
        if inside:
            if not line.startswith("- "):
                break
            m = _PR_URL.search(line)
            if m and (m.group(1), int(m.group(2))) not in out:
                out.append((m.group(1), int(m.group(2))))
    return out


def create_meta_components(text):
    """Component names from ``get_jira_create_meta(..., field_names="components")``, or None."""
    names, inside = [], False
    for line in (ad.unwrap(text) if isinstance(text, str) else "").splitlines():
        if re.match(r"- .*\[components\]", line):
            inside = True
            continue
        if inside:
            m = re.match(r"  - (.+?) \(id: [^)]*\)\s*$", line)
            if not m:
                break
            names.append(m.group(1))
    return names or None


def link(key):
    return f"<{JIRA_BROWSE}{key}|{key}>"


def slack_text(value):
    """Jira text for a Slack message: ``&``, ``<`` and ``>`` escaped, so a summary cannot make
    a link or a special mention (``<!channel>``). Links and mentions are added unescaped."""
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class CheckinEngine(Engine):
    """The sprint-end check-in's data (the planning-day check, memory records, component
    choices and posting stay with the coordinator): the ending sprint's not started,
    looks done, not checked, carrying over and unassigned team issues, OCPEDGE issues in
    progress with no open sprint, and component candidates, with owners' mentions and
    the reply texts. Every Jira fetch is mandatory: one still failing after its retry
    stops the check-in with that fetch and its error."""

    def team_base(self, project, sprint_id, emails):
        quoted = ", ".join(f'"{e}"' for e in emails)
        return (f"filter = {CHECKIN_FILTER} AND project = {project} AND sprint = {sprint_id} "
                f"AND assignee in ({quoted}) AND issuetype not in subTaskIssueTypes()")

    def run(self):
        q = self.q
        meta_call = ("get_jira_create_meta", {"project_key": "OCPEDGE", "issue_type": "Epic",
                                              "field_names": "components"}, 1)
        self.ask([("list_jira_board_sprints", {"board_id": str(q.board_id), "max_results": 100}, 1),
                  ("github_file_content", {"repo": q.roster[0], "path": q.roster[1]}, 1), meta_call])
        listing = self.board_listing()
        if self.board_problem:
            return self.stop(f"Sprint-end check-in not posted: {self.board_problem}")
        active = [s for s in sc.board_sprints(listing, q.board_id) if str(s.get("state")).lower() == "active"]
        if len(active) != 1:
            return self.stop(f"Sprint-end check-in not posted: the board has {len(active)} active sprints of its own.")
        sprint = active[0]
        try:
            self.roster = self.read_roster()
        except Incomplete as exc:
            return self.stop(f"Sprint-end check-in not posted: {exc}")
        members = self.roster["members"]
        emails = [m["username"] for m in members if "@" in (m.get("username") or "")]
        components = create_meta_components(self.ask([meta_call])[0])
        sid = sprint["id"]
        specs = []
        for p in CHECKIN_PROJECTS:
            base = self.team_base(p, sid, emails)
            specs.append({"name": f"not_started_{p}", "kind": "checkin", "jql": f'{base} AND statusCategory = "To Do"'})
            specs.append({"name": f"candidates_{p}", "kind": "checkin",
                          "jql": f'{base} AND statusCategory = "In Progress" AND (issuetype in (Story, Task, Spike) '
                                 'OR (project = OCPEDGE AND issuetype = Bug))'})
            specs.append({"name": f"in_progress_{p}", "kind": "checkin",
                          "jql": f'{base} AND statusCategory = "In Progress"'
                                 + (" AND status != Verified" if p == "OCPBUGS" else "")})
            specs.append({"name": f"unassigned_{p}", "kind": "checkin",
                          "jql": f"filter = {CHECKIN_FILTER} AND project = {p} AND sprint = {sid} AND assignee is EMPTY "
                                 "AND statusCategory != Done AND issuetype not in subTaskIssueTypes()"
                                 + (" AND status not in (Verified, Closed)" if p == "OCPBUGS" else "")})
        quoted = ", ".join(f'"{e}"' for e in emails)
        specs.append({"name": "no_open_sprint", "kind": "checkin_sprint",
                      "jql": f'filter = {CHECKIN_FILTER} AND project = OCPEDGE AND assignee in ({quoted}) '
                             'AND statusCategory = "In Progress" AND (sprint is EMPTY OR sprint not in openSprints()) '
                             'AND issuetype != Epic AND issuetype not in subTaskIssueTypes()'})
        specs.append({"name": "epics_no_component", "kind": "checkin",
                      "jql": f'filter = {CHECKIN_FILTER} AND project = OCPEDGE AND issuetype = Epic '
                             f'AND statusCategory = "In Progress" AND component is EMPTY AND assignee in ({quoted})'})
        specs.append({"name": "issues_no_component", "kind": "checkin",
                      "jql": f"filter = {CHECKIN_FILTER} AND project = OCPEDGE AND sprint = {sid} AND component is EMPTY "
                             "AND status != Closed AND issuetype != Epic AND issuetype not in subTaskIssueTypes() "
                             f"AND assignee in ({quoted})"})
        got = self.wave(specs)
        failed = [n for n, f in got.items() if not f["fetch"]["complete"]]
        if failed:
            f = got[failed[0]]
            return self.stop(f"Sprint-end check-in not posted: {failed[0]} fetch incomplete: "
                             f"{'; '.join(f['fetch']['problems'])}")

        # An issue whose status changed between the queries (To Do in one, In Progress in
        # another) gets no buttons: it is listed as not checked, in one reply only.
        def keys_of(prefix):
            return {i["key"] for p in CHECKIN_PROJECTS for i in got[f"{prefix}_{p}"]["issues"]}
        conflict = keys_of("not_started") & (keys_of("candidates") | keys_of("in_progress"))

        def issues(prefix):
            return [i for p in CHECKIN_PROJECTS for i in got[f"{prefix}_{p}"]["issues"] if i["key"] not in conflict]
        conflicted = [i for p in CHECKIN_PROJECTS for i in got[f"in_progress_{p}"]["issues"] if i["key"] in conflict]
        candidates = issues("candidates")
        cand_keys = [i["key"] for i in candidates]
        epic_keys = sorted({(i.get("parent") or {}).get("key") for i in got["issues_no_component"]["issues"]}
                           - {None}, key=cr._key_number)
        # Wave 2: each candidate's links and remote links, their children, the issues' epics.
        specs = []
        if cand_keys:
            specs.append({"name": "children", "kind": "checkin", "jql": f"parent in ({', '.join(cand_keys)})"})
        if epic_keys:
            specs.append({"name": "epics", "kind": "checkin", "jql": f"key in ({', '.join(epic_keys)})"})
        details = self.issue_details(cand_keys)
        more = self.wave(specs) if specs else {}
        for name, fetch in more.items():
            if not fetch["fetch"]["complete"]:
                return self.stop(f"Sprint-end check-in not posted: {name} fetch incomplete: "
                                 f"{'; '.join(fetch['fetch']['problems'])}")
        # Wave 3: the state of every linked code pull request.
        prs = {k: [(r, n) for r, n in remote_pr_links(details.get(k)) if r not in NOT_CODE_REPOS] for k in cand_keys}
        states = self.pr_states(sorted({pr for v in prs.values() for pr in v}))
        children = {}
        for c in (more.get("children") or _empty_fetch("children"))["issues"]:
            children.setdefault((c.get("parent") or {}).get("key"), []).append(c)
        looks_done, not_checked, why = [], [], {}
        for issue in candidates:
            verdict = self.looks_done(issue, details.get(issue["key"]), prs[issue["key"]], states,
                                      children.get(issue["key"], []))
            if verdict == "yes":
                looks_done.append(issue)
                why[issue["key"]] = len(prs[issue["key"]])
            elif verdict == "undecided":
                not_checked.append(issue)
        not_checked += conflicted
        out_keys = {i["key"] for i in looks_done + not_checked}
        carrying = [i for i in issues("in_progress") if i["key"] not in out_keys]
        no_sprint = [i for i in got["no_open_sprint"]["issues"] if not self.in_open_sprint(i)]
        epics = {i["key"]: i for i in (more.get("epics") or _empty_fetch("epics"))["issues"]}
        comp_epics = got["epics_no_component"]["issues"]
        comp_issues = []
        for i in got["issues_no_component"]["issues"]:
            epic = epics.get((i.get("parent") or {}).get("key"))
            names = [c["name"] for c in (epic or {}).get("components", [])]
            if len(names) == 1:
                continue  # a Jira rule copies the epic's one component
            comp_issues.append((i, epic["key"] if epic else None, names))
        owners = issues("not_started") + looks_done + carrying + no_sprint + comp_epics + [i for i, _, _ in comp_issues]
        mentions = self.owner_mentions(owners)

        def owner(i):
            a = i.get("assignee") or {}
            return mentions.get(a.get("email")) or a.get("display_name") or "unassigned"
        return self.render(sprint, issues("not_started"), looks_done, why, not_checked, carrying,
                           issues("unassigned"), no_sprint, comp_epics, comp_issues, components, owner)

    def stop(self, line):
        return {"status": "incomplete", "text": line}

    def issue_details(self, keys):
        """get_jira_issue (links and remote links) per key, a failed or cut-off answer sent
        again once; the text, or None when still not whole."""
        out, tries = {}, {}
        while True:
            calls = [("get_jira_issue", {"issue_key": k}, tries.get(k, 1)) for k in keys if k not in out]
            if not calls:
                return out
            for (_, args, attempt), text in zip(calls, self.ask(calls), strict=True):
                k, page = args["issue_key"], as_page(text)
                whole = not isinstance(page, dict) and ad.issue_text_complete(ad.unwrap(page), k)
                if whole or attempt >= 2:
                    out[k] = ad.unwrap(page) if whole else None
                else:
                    tries[k] = 2

    def pr_states(self, prs):
        """github_pr_checks per pull request, sent again once when it fails: ``pr_state`` or None."""
        out, tries = {}, {}
        while True:
            calls = [("github_pr_checks", {"repo": r, "pr_number": n}, tries.get((r, n), 1))
                     for r, n in prs if (r, n) not in out]
            if not calls:
                return out
            for (_, args, attempt), text in zip(calls, self.ask(calls), strict=True):
                pr, value = (args["repo"], args["pr_number"]), parse_json_answer(text)
                state = value.get("pr_state") if isinstance(value, dict) and value.get("status") == "ok" else None
                if state or attempt >= 2:
                    out[pr] = state or None
                else:
                    tries[pr] = 2

    @staticmethod
    def looks_done(issue, detail, prs, states, kids):
        """"yes", "no" (a check clearly fails) or "undecided" (no clear fail, a check unread)."""
        undecided = False
        if detail is None:
            undecided = True
        elif re.search(r"(?m)^- \.\.\. and \d+ more remote links", detail):
            undecided = True  # remote links cut off: a code pull request may be missing
        elif not remote_pr_links(detail) or not prs:
            return "no"  # check 1: no linked code pull request
        for pr in prs:
            state = states.get(pr)
            if state is None:
                undecided = True
            elif state != "merged":
                return "no"  # check 2: a pull request not merged
        if any(not is_done(c) for c in kids):
            return "no"  # check 3: a child not done
        if detail is not None:
            unreadable = []
            links, _ = ad.parse_issue_links(detail, unreadable)
            if unreadable or re.search(r"(?m)^- \.\.\. and \d+ more (linked issues|remote links)", detail):
                undecided = True  # cut-off links: a blocker may be missing
            for lk in links:
                blocker = lk.get("inward_issue")
                if blocker and lk.get("type", {}).get("inward") == "is blocked by" and not is_done(blocker):
                    return "no"  # check 4: an open blocker
        return "undecided" if undecided else "yes"

    @staticmethod
    def in_open_sprint(issue):
        sprints = issue.get("customfield_10020") or []
        return any(str((s or {}).get("state", "")).lower() in ("active", "future") for s in sprints
                   if isinstance(s, dict))

    def owner_mentions(self, issues):
        """Roster email → Slack mention, by ``resolve_identity(<Rover ID>, kind="kerberos")``."""
        emails = sorted({(i.get("assignee") or {}).get("email") for i in issues} - {None})
        if not emails:
            return {}
        calls = [("resolve_identity", {"identifier": e.split("@")[0], "kind": "kerberos"}) for e in emails]
        out = {}
        for e, text in zip(emails, self.ask_retrying(calls, identity_answered), strict=True):
            value = parse_json_answer(text)
            mention = value.get("slack_mention") if isinstance(value, dict) else None
            if isinstance(mention, str) and re.fullmatch(r"<@[UW][A-Z0-9]+>", mention):
                out[e] = mention
        return out

    def render(self, sprint, not_started, looks_done, why, not_checked, carrying, unassigned, no_sprint,
               comp_epics, comp_issues, components, owner):
        def status(i):
            return (i.get("status") or {}).get("name") or ""
        replies = []
        for i in not_started:
            replies.append({"kind": "not_started", "key": i["key"], "buttons": ["Move to backlog", "Carry over"],
                            "text": f"{link(i['key'])} {slack_text(i['summary'])}, {owner(i)}: not started. "
                                    "If nobody clicks, it carries over."})
        for i in looks_done:
            n = why[i["key"]]
            replies.append({"kind": "looks_done", "key": i["key"], "buttons": ["Close it", "Keep open"],
                            "text": f"{link(i['key'])} {slack_text(i['summary'])}, {owner(i)}: looks done ({n} linked code pull "
                                    f"request{'s' if n != 1 else ''} merged, child issues done, no open blockers). "
                                    "If nobody clicks, it stays open."})
        if not_checked:
            replies.append({"kind": "not_checked", "buttons": [], "text": "Not checked (status unverifiable): "
                            + ", ".join(link(i["key"]) for i in not_checked)})
        if no_sprint:
            replies.append({"kind": "no_open_sprint", "buttons": [], "text": "\n".join(
                ["In progress but in no open sprint: owners, please add each to the next sprint, move it back, "
                 "or close it (nothing was changed):"]
                + [f"{link(i['key'])} {slack_text(i['summary'])} ({status(i)}), {owner(i)}" for i in no_sprint])})
        if carrying:
            by_owner = {}
            for i in carrying:
                by_owner.setdefault(owner(i), []).append(i["key"])
            replies.append({"kind": "carrying_over", "buttons": [], "text": "\n".join(
                ["Carrying over:"] + [f"{o}: re-point remaining work and comment why: "
                                      + ", ".join(link(k) for k in keys) for o, keys in by_owner.items()])})
        if unassigned:
            replies.append({"kind": "unassigned", "buttons": [], "text": "\n".join(
                ["Unassigned and not done, for a manager to assign or move (nothing was changed):"]
                + [f"{link(i['key'])} {slack_text(i['summary'])} ({status(i)})" for i in unassigned])})
        counts = {"not started": len(not_started), "look done": len(looks_done), "carrying over": len(carrying),
                  "unassigned": len(unassigned), "in progress with no sprint": len(no_sprint)}
        candidates = {
            "components": components,
            "components_note": None if components else "OCPEDGE's components could not be read",
            "epics": [{"key": i["key"], "summary": i["summary"], "labels": [lb for lb in i.get("labels") or []],
                       "owner": owner(i)} for i in comp_epics],
            "issues": [{"key": i["key"], "summary": i["summary"], "labels": [lb for lb in i.get("labels") or []],
                        "owner": owner(i), "epic": epic, "epic_components": names,
                        "from": "one of the epic's components" if len(names) > 1 else "from its labels and summary"}
                       for i, epic, names in comp_issues]}
        return {"status": "checkin", "sprint": {"id": sprint["id"], "name": sprint["name"]}, "counts": counts,
                "first_message_parts": [f"{n} {label}" for label, n in counts.items() if n],
                "replies": replies, "component_candidates": candidates,
                "empty": not (not_started or looks_done or not_checked or carrying or unassigned or no_sprint
                              or comp_epics or comp_issues)}
