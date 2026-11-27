"""End-to-end dry run of the chai weekly health report, step by step as the SKILLs say.

``query_jira`` / ``get_jira_issue`` text → ``chai_jira_adapter`` → ``transform-*.py``,
``compute-velocity.py`` and ``run-checks.py`` (run as scripts, as ``sandbox_exec``
runs them) → ``chai_report`` → the Slack message texts, built by the weekly SKILL's
own code blocks. Covers sprint weeks 1-3, the refinement, development and
pencils-down phases, more than 50 features (no rank order), and failed, missing
and shortened input: a failed or cut-off page is sent again once; what is still
not complete is left out with its error, never reported as healthy, and an
incomplete sprint health posts nothing.

All keys, names, sprints, releases and dates are synthetic (public repository).
Run this file directly to print the rendered reports.
"""

import collections
import datetime as dt
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

BIN = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, BIN)
import chai_jira_adapter as ad  # noqa: E402
import chai_report as cr  # noqa: E402
import scrum_context as sc  # noqa: E402
import velocity_cache as vc  # noqa: E402
import weekly_report_files as wr  # noqa: E402
from _jira_transforms import extract_blocked_by, normalize_name  # noqa: E402
from tests.test_chai_epic_fields import (  # noqa: E402
    EPICS_JQL, EPICS_MIX, PARENTS, skill_snippet)
from tests.test_chai_jira_adapter import (  # noqa: E402
    HINT, MORE_MATCH, ROSTER_MD, fetch_by_skill, jira_keys, manifest_page, send_page)
from tests.test_scrum_context import SCRUM_CONTEXT_SKILL  # noqa: E402
from tests.test_feature_health import budget_problems  # noqa: E402

BOARD = 1000
START = dt.date(2030, 1, 7)  # Monday: Test Sprint 10 starts; 3-week sprints
RELEASE = "9.2"
SCRUM_MASTER = "Morgan Manager"
SLACK = {"aexample@redhat.com": "U000TEST1", "qquality@redhat.com": "U000TEST2",
         "mmanager@redhat.com": "U000TEST3"}
PAGE = ad.PAGE_SIZE


# ---------------------------------------------------------------- board and schedule


def _start(n):
    return START + dt.timedelta(weeks=3 * (n - 10))


def _sprint_id(n):
    return 490 + n


def board(active):
    return [{"id": str(_sprint_id(n)), "name": f"Test Sprint {n}", "originBoardId": BOARD,
             "state": "closed" if n < active else "active" if n == active else "future",
             "start_date": _start(n).isoformat(), "end_date": (_start(n) + dt.timedelta(weeks=3)).isoformat()}
            for n in range(10, 20)]


def day(sprint, week):
    """Tuesday of ``week`` in ``sprint``."""
    return (_start(sprint) + dt.timedelta(weeks=week - 1, days=1)).isoformat()


# 9.2: refinement = Test Sprint 12, pencils down = end of 15, branch cut in 16.
SCHEDULES = {"9.1": [], RELEASE: [
    {"name": "9.2 Development Branch opens", "date": _start(12).isoformat()},
    {"name": "Pencils Down / Stabilization", "date": _start(16).isoformat()},
    {"name": "Master Branch Open for 9.3", "date": (_start(16) + dt.timedelta(days=3)).isoformat()}]}


def sprint_value(n, state):
    end = _start(n) + dt.timedelta(weeks=3)
    return (f"Test Sprint {n} (id {_sprint_id(n)}, {state}, board {BOARD}, start {_start(n)}T12:00:00.000Z, "
            f"end {end}T12:00:00.000Z)")


# ---------------------------------------------------------------- query_jira text


def block(key, type_="Story", status="In Progress", assignee="Alex Example", summary="Synthetic item",
          fields=()):
    """One issue in query_jira's layout."""
    resolution = " | Resolution: Done" if status == "Closed" else ""
    return "\n".join([f"  [{key}] {summary}",
                      (f"    Type: {type_} | Status: {status} | Priority: Major | Assignee: {assignee} | "
                       f"Reporter: Sam Sample{resolution}"),
                      "    Created: 2030-01-02 | Updated: 2030-02-01"] + [f"    {f}" for f in fields])


def _num(key):
    return int(key.rsplit("-", 1)[1])


def query_jira(sent, items, ignores_key_below=False):
    """query_jira's answer to one page request ``sent`` over ``items`` ({key: block}, all
    matching the query's predicate), with the paging clauses applied as
    :func:`tests.test_chai_jira_adapter.jira_keys` does. ``ignores_key_below`` stands for a
    tool that does not apply ``key <``: it returns the first page again."""
    keys = jira_keys(sent, items, ignores_key_below)
    if not keys:
        return f"No issues found for JQL: {sent}"
    body = "\n".join(items[k] for k in keys[:PAGE])
    if len(keys) > PAGE:
        return f"{MORE_MATCH}\n{body}\n\n" + HINT.format(n=PAGE) + "\n"
    return f"Found {len(keys)} issues (showing {len(keys)}):\n{body}\n"


def cut_off(text):
    """``text`` cut off inside its last issue, before the issue's 'Type: … | Status: …' line
    (a page with no issue loses all its text)."""
    return text.rpartition("\n    Type: ")[0] + "\n"


def issue_text(key, links):
    """A whole get_jira_issue(fields="issuelinks") response; ``links=None`` is an issue with no
    links (the field is empty and there is no section)."""
    section, state = "", ad.LINKS_EMPTY
    if links is not None:
        rows = "\n".join(f"- {wording}: {other} (Blocks) | status: {status} | https://example.invalid/browse/{other}"
                         for wording, other, status in links)
        section, state = f"Linked Jira Issues:\n{rows}\n", ad.LINKS_SET
    return (f"Latest Jira State:\n[{key}] Synthetic item\nType: Story | Status: In Progress\n{section}"
            f"Attachments: none\nURL: https://example.invalid/browse/{key}\n\nDescription:\nSynthetic text.\n\n"
            f"Requested fields:\nissuelinks: {state}\n\nComments: none\n")


# ---------------------------------------------------------------- synthetic team data


def _feature(key, sme="Alex Example", labels="ocpedge-plan", status="In Progress", size="M", tv="openshift-9.2",
             type_="Feature"):
    fields = [f"Labels: {labels}", f"customfield_10795: {size}", f"customfield_10855: {tv}",
              "customfield_10470: Quinn Quality", "customfield_10473: Dana Docs"]
    if sme:
        fields.append(f"customfield_10475: {sme}")
    return block(key, type_, status, "Pat Product", fields=fields)


def gate_view(items):
    """What the gate query returns for these issues: only its fields (issuetype, status,
    labels, customfield_10855), as query_jira prints them."""
    keep = ("Labels:", "customfield_10855:")
    return {k: "\n".join(ln for n, ln in enumerate(v.splitlines()) if n < 3 or ln.strip().startswith(keep))
            for k, v in items.items()}


def blocks_of(text):
    """``{key: issue block}`` of a query_jira page text (the epic-field fixtures)."""
    out, key = {}, None
    for line in text.splitlines():
        m = re.match(r"^  \[(" + ad.KEY_PATTERN + r")\]", line)
        if m:
            key = m.group(1)
            out[key] = line
        elif key and line.startswith("    "):
            out[key] += "\n" + line
    return out


_EXTRA_LINE_RE = re.compile(r"^    (customfield_\d+|resolutiondate):")


def field_view(items, extra):
    """What query_jira prints for ``items`` when only ``extra`` are requested as
    extra_fields: the base lines, and only the requested custom-field lines."""
    def keep(line):
        m = _EXTRA_LINE_RE.match(line)
        return m is None or m.group(1) in extra
    return {k: "\n".join(ln for ln in v.splitlines() if keep(ln)) for k, v in items.items()}


def _closed(block_text):
    return re.search(r"\| Status: Closed \|", block_text) is not None


WEEKLY_SKILL = os.path.join(os.path.dirname(__file__), "..", "..", "chai", "weekly-report", "SKILL.md")
REFINEMENT_FEATURES_JQL = ('project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") '
                           'AND "Target Version" = "openshift-{release}" AND status != Closed '
                           'AND (resolution is EMPTY OR resolution not in (Duplicate, Obsolete))')


def features_jql(w, release, open_only=False):
    """The features query for ``release``, as the weekly report script sends it: the refinement
    one (``open_only``: ``status != Closed``, the old plan's) or the rank-ordered release one."""
    jql = REFINEMENT_FEATURES_JQL if open_only else wr.FEATURES_JQL
    return jql.replace("OCPSTRAT", w["projects"]["features"], 1).format(release=release)


def targeting(items, sent):
    """The features of ``items`` the features query ``sent`` matches (the server side of
    that query): the Target Version it names; non-Closed only when it says ``status !=
    Closed``; and only when it says ``resolution not in (...)``, a resolution not listed
    there (an unresolved one only with ``resolution is EMPTY OR`` before it: as in Jira,
    ``not in`` alone never matches an empty field). Project, type and labels hold for
    every fixture feature."""
    release = re.search(r'"Target Version" = "openshift-([^"]+)"', sent).group(1)
    open_only = re.search(r"\bstatus != Closed\b", sent) is not None
    listed = re.search(r"\b(resolution is EMPTY OR )?resolution not in \(([^)]*)\)", sent)
    excluded = {r.strip() for r in listed.group(2).split(",")} if listed else set()
    out = {}
    for k, v in items.items():
        tv = re.search(r"customfield_10855: (.*)", v)
        if not tv or f"openshift-{release}" not in [x.strip() for x in tv.group(1).split(",")]:
            continue
        resolution = re.search(r"\| Resolution: ([^|\n]+?)\s*(?:\||$)", v, re.MULTILINE)
        if listed and (resolution.group(1) in excluded if resolution else not listed.group(1)):
            continue
        if not (open_only and _closed(v)):
            out[k] = v
    return out


def parent_link_items(w, feature_keys):
    """The server side of ``"Parent Link" in (<feature_keys>)``: the epics whose printed
    Parent Link names one of them, or, in a world where Jira keeps the link in the
    native parent (``w["parent_link_is_parent"]``), whose parent does."""
    wanted = set(feature_keys)

    def link(text):
        m = re.search(r"customfield_10018: (\S+)", text)
        if m is None and w.get("parent_link_is_parent"):
            m = re.search(r"Parent: (\S+)", text)
        return m.group(1) if m else None
    return {k: v for k, v in w["epics"].items() if link(v) in wanted}


def block_rows(w):
    """The server side of the block candidates query: the features and epics with an "is
    blocked by" link (``w["feature_blocks"]`` names them)."""
    rows = {**w["epics"], **w["features"]}
    return {k: rows[k] for k in w["feature_blocks"]}


def whole_values(w, steps, text):
    """Jira paging step 7 over a page: each value ``w["whole_values"]`` names as shortened is
    read whole with one call, sent again once if it fails, and put in place of the shortened
    one; still failing, the page stays as it is (the adapter then flags the value)."""
    for spec in w["whole_values"].values():
        if not isinstance(text, str) or spec["short"] not in text:
            continue
        for attempt in (1, 2):
            steps["whole values"]["detail"] += 1
            if spec.get("fail") or (attempt == 1 and spec.get("fail_first")):
                continue
            text = text.replace(spec["short"], spec["whole"])
            break
    return text


def unordered(text):
    """A more-match page with its issue blocks in reverse key order (ORDER BY key DESC ignored)."""
    head, rest = text.split("\n", 1)
    body, sep, tail = rest.partition("\n\n")
    blocks = list(blocks_of(body).values())
    return "\n".join([head, *reversed(blocks)]) + sep + tail


def children_of(w, epic_keys):
    """The server side of ``parent in (<epic_keys>)``: the stories whose parent key is
    exactly one of them (TEST-1 never matches a story under TEST-10)."""
    wanted = set(epic_keys)
    return {k: v for k, v in w["stories"].items()
            if (m := re.search(r"Parent: (\S+)", v)) and m.group(1) in wanted}


def field_epic_items(w):
    """The server side of the decision 0024 query: Epics with an active status and a parent."""
    active = "|".join(re.escape(s) for s in cr.ACTIVE_EPIC_STATUSES)
    return {k: v for k, v in {**w["epics"], **w["field_epics"]}.items()
            if re.search(r"Type: Epic \| Status: (" + active + r") \|", v) and re.search(r"Parent: \S", v)}


def lookup(w, keys):
    """The server side of ``key in (<keys>)``: any issue the fake knows."""
    known = {**w["features"], **w["parents"]}
    return {k: known[k] for k in keys if k in known}


def step_of(name):
    """The report step a logical fetch belongs to (for the read counts)."""
    for prefix, step in (("feature_gate", "gate"), ("velocity_", "velocity"), ("sprint_issues", "sprint issues"),
                         ("sprint_blocked", "blocked candidates"), ("features", "features"),
                         ("field_epics", "epics"), ("epics", "epics"), ("stories", "stories"), ("bugs", "bugs"),
                         ("ref_spikes", "spikes"), ("linked", "spikes"), ("blocks", "block candidates"),
                         ("parents", "epic-field parents")):
        if name.startswith(prefix):
            return step
    return name


GATE_JQL = ('project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") AND '
            'status != Closed AND "Target Version" is not EMPTY')


def world(active):
    """Everything the fake Jira knows: issues by fetch, issue links, closed sprints."""
    ref_state = "active" if active == 12 else "closed"
    cur = sprint_value(active, "active")
    return {
        "sprint": {
            "TEST-13": block("TEST-13", assignee="Quinn Quality", fields=[f"customfield_10020: {cur}"]),
            "TEST-12": block("TEST-12", fields=["Parent: TEST-300", f"customfield_10020: {cur}",
                                                "customfield_10028: 3.0"]),
            "TEST-11": block("TEST-11", status="Closed", fields=["Parent: TEST-300",
                                                                 f"customfield_10020: {cur}",
                                                                 "customfield_10028: 5.0"]),
        },
        # Blocked-sprint-issue candidates and every get_jira_issue answer (None: the call failed).
        "sprint_blocked": ["TEST-12"],
        "issue_links": {"TEST-12": issue_text("TEST-12", [("is blocked by", "TEST-99", "To Do")]),
                        "FEAT-30": issue_text("FEAT-30", [("is blocked by", "BUG-77", "New")]),
                        "TEST-920": issue_text("TEST-920", [("blocks", "FEAT-20", "In Progress")]),
                        "TEST-930": issue_text("TEST-930", [("blocks", "FEAT-30", "In Progress")])},
        "velocity": {n: {f"TEST-{500 + n}": block(
            f"TEST-{500 + n}", status="Closed",
            fields=[f"customfield_10020: {sprint_value(n, 'closed')}", "customfield_10028: 8.0",
                    f"resolutiondate: {_start(n) + dt.timedelta(days=10)}T10:00:00.000+0000"])}
            for n in range(10, active)},
        # PM rank order: FEAT-30, 20, 10.
        "features": {"FEAT-30": _feature("FEAT-30"), "FEAT-20": _feature("FEAT-20", size="L"),
                     "FEAT-10": _feature("FEAT-10", sme=None)},
        "epics": {"TEST-300": block("TEST-300", "Epic", fields=["customfield_10018: FEAT-30"]),
                  "TEST-200": block("TEST-200", "Epic", fields=["customfield_10018: FEAT-20"])},
        "stories": {"TEST-301": block("TEST-301", status="Closed", fields=["Parent: TEST-300",
                                                                           "customfield_10028: 5.0"]),
                    "TEST-302": block("TEST-302", fields=["Parent: TEST-300", "customfield_10028: 3.0"]),
                    "TEST-201": block("TEST-201", fields=["Parent: TEST-200", "customfield_10028: 5.0"])},
        "bugs": {},
        # The refinement spike of FEAT-20, in the refinement sprint and still open; a
        # closed test plan spike for FEAT-30.
        "ref_spikes": {"TEST-920": block("TEST-920", "Spike", fields=[
            f"customfield_10020: {sprint_value(12, ref_state)}"])},
        "linked_spikes": {"FEAT-20": {"TEST-920": block("TEST-920", "Spike")},
                          "FEAT-30": {"TEST-930": block("TEST-930", "Spike", "Closed", "Quinn Quality",
                                                        summary="Test plan for the feature")}},
        "feature_blocks": ["FEAT-30"],
        "fail": set(),  # fetch names whose tool call fails, the retry too
        # Fetch names ("product_pages": the first release listing call) whose first answer is a
        # failed call ("error") or a cut-off page ("cut"); the retry is whole.
        "flaky": {},
        "stuck": set(),  # fetch names whose tool ignores "key <" (returns the first page again)
        "stuck_once": set(),  # fetch names whose tool ignores "key <" on a query's first send only
        # The projects the queries name, as the SKILLs do; a test with more than 50 synthetic
        # issues names the synthetic projects its keys are in.
        "projects": {"sprint": "OCPEDGE, USHIFT, OCPBUGS", "features": "OCPSTRAT", "bugs": "OCPBUGS",
                     "stories": "OCPEDGE, USHIFT, OCPBUGS",
                     # the synthetic project of the epic fixtures (OCPEDGE / USHIFT in the SKILL)
                     "release_epics": ("TEST",)},
        # Product Pages: release → milestones ([] = listed, no dates; {"error": ...} = lookup failed).
        "schedules": SCHEDULES,
        "after_gate": None,  # a change to the world once the gate is read (time passes between reads)
        "epic_reads": {},  # {epics query name: {key: block}}: an epic as that query read it (changed in between)
        "board_error": None,  # the board sprint listing still fails after its retry, with this error
        "whole_values": {},  # shortened values read whole (Jira paging step 7): {name: {short, whole, fail...}}
        "unordered": set(),  # fetch names whose tool returns a more-match page out of key order
        "board_last": None,  # the last sprint number the board lists (later sprints are projected)
        "interactive": False,  # a run from a DM (validation or an extra run), not the scheduled one
        "gate": None,  # what the release Feature gate query returns ({key: block}); None = the features
        "pp_fails": False,  # the Product Pages listing call fails
        # Active epics with a parent (decision 0024) and two that are not active (New, Closed),
        # and the parents a key lookup returns (FEAT-9 is not returned: restricted).
        "field_epics": blocks_of(EPICS_MIX),
        "parents": blocks_of(PARENTS),
        # The optional velocity store: None = not configured; a dict = what it holds
        # ({key: entry}); "unavailable" = configured but it cannot be read.
        "velocity_store": None,
    }


# ---------------------------------------------------------------- the weekly chain


class Sandbox:
    """Files and scripts as sandbox_exec runs them."""

    def __init__(self, path):
        self.path = path

    def write(self, name, data):
        out = os.path.join(self.path, name)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return out

    def read(self, name):
        with open(os.path.join(self.path, name), encoding="utf-8") as fh:
            return json.load(fh)

    def run(self, script, *args):
        proc = subprocess.run([sys.executable, os.path.join(BIN, script), *args], capture_output=True, text=True,
                              cwd=self.path, check=False)
        if proc.returncode:
            raise AssertionError(f"{script} exited {proc.returncode}: {proc.stderr}")
        return os.path.join(self.path, args[args.index("--output") + 1])

    def issues(self, name, adapted):
        return self.write(f"{name}_raw.json", {"issues": adapted["issues"]})


def build(sprint, computed, missing, mentions, scrum_master, gaps):
    """The report as the weekly report script builds it: ``failure`` (the "not posted" line,
    or None), ``report``, ``top_post_text`` and ``replies``."""
    failure = cr.sprint_health_failure(sprint, missing)
    if failure is not None:
        return {"failure": failure}
    report = cr.build_report(sprint, computed, missing, mentions, team_owner=scrum_master, epic_gaps=gaps)
    return {"failure": None, "report": report, "top_post_text": report["top_post_text"],
            "replies": report["replies"]}


def skill_end_steps():
    """``{"scheduled run": [steps], "interactive run (DM)": [steps]}``: the coordinator's steps,
    in order, when the report cannot be posted, from the ``text`` block of the weekly-report SKILL."""
    with open(WEEKLY_SKILL, encoding="utf-8") as fh:
        text = fh.read()
    block = re.search(r"```text\n(scheduled run: 1\. .*?)```", text, re.DOTALL).group(1)
    steps = {}
    for line in block.strip().splitlines():
        run, step = line.split(": ", 1)
        steps.setdefault(run, []).append(step.split(". ", 1)[1])
    return steps


def end_steps(failure, interactive=False):
    """The SKILL's steps for this run, done with stand-ins for the coordinator's delivery, in
    order: a response element registered (``{"element": <its content>}``) and each tool call
    (``{"tool": <name>, **its arguments}``)."""
    done = []

    def tool(name):
        return lambda **kwargs: done.append({"tool": name, **kwargs})
    names = {"failure": failure, "send_response": tool("send_response"),
             "no_action_required": tool("no_action_required")}
    for step in skill_end_steps()["interactive run (DM)" if interactive else "scheduled run"]:
        element = re.fullmatch(r"register one response element, its whole content: (\w+)", step)
        if element:
            done.append({"element": names[element.group(1)]})
        else:
            eval(step, {"__builtins__": {}, **names})  # the SKILL's own call
    return done


class Incomplete(Exception):
    """A fetch of the section was incomplete: the section is left out (SKILL rule)."""


class LegacyRun:
    """One weekly report with the fetch plan the SKILLs had before the shared plan
    (one features, epics and stories fetch per release section, a separate epic-field
    fetch, a closed-sprint board listing for velocity). Kept to prove the shared plan
    posts the same report, and to count the reads the old plan made."""

    def __init__(self, w, today, sandbox):
        self.w, self.today, self.box = w, today, sandbox
        # Tool calls by report step and kind: list (board sprints, Product Pages),
        # search (query_jira requests), detail (get_jira_issue), sandbox (Python calls).
        self.steps = collections.defaultdict(collections.Counter)
        self.scripts = collections.Counter()
        run = sandbox.run

        def counted(script, *args):
            self.scripts[script] += 1
            return run(script, *args)
        sandbox.run = counted
        self.roster = ad.parse_team_roster(ROSTER_MD)
        self.roster_file = sandbox.write("roster.json", {"members": self.roster["members"],
                                                         "contacts": self.roster["contacts"]})
        self.missing, self.posted, self.pages, self.pp_calls, self.issue_reads = [], [], {}, [], []
        self.calls, self.status = {}, None  # requests sent per fetch (retries included); the run's status line
        self.final_steps = None  # the coordinator's end steps when sprint health is incomplete (step 6)
        self.pp_attempts = 0  # Product Pages release listing calls, retries included
        if set(w["flaky"].values()) - {"error", "cut"}:
            raise ValueError(f"flaky answers are 'error' or 'cut': {w['flaky']}")

    # -- Jira tools
    def fetch(self, name, jql, items, order="key"):
        calls = self.calls.setdefault(name, [])

        def answer(sent):
            calls.append(sent)
            self.steps[step_of(name)]["search"] += 1
            if name in self.w["fail"]:
                return {"error": "tool call failed"}
            stuck = name in self.w["stuck"] or (name in self.w["stuck_once"] and calls.count(sent) == 1)
            text = whole_values(self.w, self.steps, query_jira(sent, items, stuck))
            if name in self.w["unordered"] and isinstance(text, str) and text.startswith(MORE_MATCH):
                text = unordered(text)
            flaky = self.w["flaky"].get(name) if len(calls) == 1 else None
            return {"error": "tool call timed out"} if flaky == "error" else cut_off(text) if flaky else text

        if order == "rank":  # release-health "Features keep PM rank"
            page, outcome = send_page(answer, f"({jql}) ORDER BY Rank ASC")
            if isinstance(outcome, str):  # more match: the features are fetched by key from there
                order, parts = "key", fetch_by_skill(jql, answer, first=outcome)
            else:
                parts = [{"pages": [page]}]
        else:
            parts = fetch_by_skill(jql, answer)  # the SKILL "Jira paging" loop, next_page_jql
        self.pages[name] = sum(len(p["pages"]) for p in parts)
        out = ad.adapt_fetch({"name": name, "order": order, "parts": parts}, self.roster)
        self.missing += out["left_out"]
        return out

    def complete(self, name, adapted):
        if not adapted["fetch"]["complete"]:
            raise Incomplete(f"{name} fetch incomplete: {'; '.join(adapted['fetch']['problems'])}")
        return adapted

    def get_jira_issue(self, key, fields, step="links"):
        """get_jira_issue as the SKILLs call it for links: fields="issuelinks". A failed call
        (None) or a cut-off response is sent again once; the retry's answer is used."""
        name = f"get_jira_issue {key}"
        for attempt in (1, 2):
            self.issue_reads.append((key, fields))
            self.steps[step]["detail"] += 1
            text = None if name in self.w["fail"] else self.w["issue_links"].get(key)
            flaky = self.w["flaky"].get(name) if attempt == 1 else None
            if text is not None and flaky:
                text = None if flaky == "error" else text.split("Description:")[0]
            if text is not None and ad.issue_text_complete(text, key):
                break
        return text

    # -- scrum-context (step 1) and sections (step 2)
    def list_entities(self, **kwargs):
        """A stand-in for the Product Pages tool over the synthetic store: one product, the
        releases one per page. ``release`` is this stand-in's own field, not a real one."""
        self.steps["product pages"]["list"] += 1
        if kwargs.get("kind") == "release":
            self.pp_attempts += 1
            if self.w["pp_fails"] or (self.pp_attempts == 1 and "product_pages" in self.w["flaky"]):
                raise RuntimeError("tool call failed")
        if kwargs.get("kind") == "product":
            return {"data": [{"id": 990001, "shortname": "openshift"}], "cursor": None}
        releases = sorted(self.w["schedules"], key=sc.parse_version)
        entities = [{"id": 900 + n, "release": r} for n, r in enumerate(releases)]
        start = int(kwargs.get("cursor") or 0)  # one entity per page, so the cursor path runs
        self.pp_calls.append(kwargs)
        return {"data": entities[start:start + 1], "cursor": str(start + 1) if start + 1 < len(entities) else None}

    def product_pages(self, candidates):
        """Weekly step 1.2: list releases with the scrum-context SKILL's block, then the
        candidates' schedules and the previous release of each ([] when a release is not
        listed; {"error": ...} when a lookup failed)."""
        scope = {}
        exec(skill_snippet("def openshift_releases(", path=SCRUM_CONTEXT_SKILL), scope)  # noqa: S102
        why = []
        listed = scope["openshift_releases"](self.list_entities, why)
        if listed is None:  # the SKILL: every needed release gets the listing's own error
            return {r: {"error": why[0]} for r in candidates}
        names = [e["release"] for e in listed]
        needed = set(candidates)
        for release in candidates:
            lower = [r for r in names if sc.parse_version(r) < sc.parse_version(release)]
            if lower:
                needed.add(max(lower, key=sc.parse_version))
        return {r: self.w["schedules"].get(r, []) if r in names else [] for r in needed}

    def context(self):
        # Step 1.1: the release Feature gate, then the SKILL's block, verbatim.
        gate_items = gate_view(self.w["features"] if self.w["gate"] is None else self.w["gate"])
        gate_jql = GATE_JQL.replace("project = OCPSTRAT", f"project = {self.w['projects']['features']}")
        scope = {"chai_report": cr, "gate_fetch": self.fetch("feature_gate", gate_jql, gate_items)}
        if self.w["after_gate"]:
            self.w["after_gate"](self.w)
        gate = cr.release_feature_gate(scope["gate_fetch"])
        candidates = gate["releases"]
        # Steps 1.2-1.4: Product Pages, scrum-context with no argument and per candidate release.
        base = {"today": self.today, "board_id": BOARD, "sprints": board(self.active),
                "schedules": self.product_pages(candidates), "engineer_count": 3, "absences": []}
        self.steps["board sprints"]["list"] += 1 + len(candidates)  # one listing per scrum-context run
        self.ctx = sc.build_context(base)
        self.release_results = {r: sc.build_context(dict(base, release=r)) for r in candidates}
        release_results = list(self.release_results.values())
        windows = [result["release"] for result in release_results]
        # Step 2: the SKILL's block, verbatim.
        sections, missing = cr.select_sections(self.ctx, windows, gate)
        self.missing += missing
        return sections

    # -- velocity (sprint-health step 4), computed once
    def velocity(self):
        files = []
        cur = self.ctx["current_sprint"]["number"]
        self.steps["board sprints"]["list"] += 1  # the closed-sprint listing (sprint-health step 4)
        for n in [n for n in sorted(self.w["velocity"]) if n < cur][-3:]:
            try:
                adapted = self.complete(f"velocity sprint {n}", self.fetch(
                    f"velocity_{n}", f"project in ({self.w['projects']['sprint']}) AND sprint = {_sprint_id(n)}",
                    self.w["velocity"][n]))
            except Incomplete as exc:
                return None, str(exc)
            meta = self.box.write(f"meta_{n}.json", {"sprint_id": _sprint_id(n), "sprint_name": f"Test Sprint {n}",
                                                     "sprint_start": _start(n).isoformat(),
                                                     "sprint_end": (_start(n) + dt.timedelta(weeks=3)).isoformat()})
            files.append(self.box.run("transform-sprint-issues.py", "--input", self.box.issues(f"v{n}", adapted),
                                      "--sprint-meta", meta, "--today", self.today, "--output", f"v{n}.json"))
        out = self.box.run("compute-velocity.py", "--sprint-issues", *files, "--roster", self.roster_file,
                           "--output", "velocity.json")
        with open(out, encoding="utf-8") as fh:
            return cr.usable_velocity(json.load(fh))

    # -- sprint health (step 3)
    def sprint(self, velocity, reason):
        cur = self.ctx["current_sprint"]
        mode = cr.sprint_mode(cur["week"])
        try:
            adapted = self.complete("sprint issue", self.fetch(
                "sprint_issues", f"project in ({self.w['projects']['sprint']}) AND sprint = {cur['id']}",
                self.w["sprint"]))
        except Incomplete as exc:
            self.missing.append({"what": cr.SPRINT_HEALTH, "why": str(exc)})
            return None
        links_complete, links_problems = True, []
        if mode == "mid-sprint":
            candidates = self.fetch("sprint_blocked",
                                    f"(project in ({self.w['projects']['sprint']}) AND sprint = {cur['id']}) "
                                    "AND issueLinkType = \"is blocked by\" AND statusCategory != Done",
                                    {k: self.w["sprint"][k] for k in self.w["sprint_blocked"]})
            problems = ad.attach_issue_links(adapted, {i["key"]: self.get_jira_issue(i["key"], fields="issuelinks")
                                                       for i in candidates["issues"]})
            links_complete = candidates["fetch"]["complete"] and not problems
            if not candidates["fetch"]["complete"]:  # sprint-health step 3: the failed fetch and its error
                problems = [f"blocked items fetch incomplete: {'; '.join(candidates['fetch']['problems'])}", *problems]
            links_problems = problems
        meta = self.box.write("meta.json", {"sprint_id": int(cur["id"]), "sprint_name": cur["name"],
                                            "sprint_start": cur["start"], "sprint_end": cur["end"]})
        out = self.box.run("transform-sprint-issues.py", "--input", self.box.issues("sprint", adapted),
                           "--sprint-meta", meta, "--today", self.today, "--output", "sprint_issues.json")
        with open(out, encoding="utf-8") as fh:
            issues = json.load(fh)
        return cr.sprint_health(issues, cur, self.today, mode, velocity, self.ctx["current_sprint_availability"],
                                adapted["skipped_signals"], links_complete, velocity_reason=reason,
                                links_problems=links_problems)

    # -- release-health steps 3 and 4
    def _batched(self, name, keys, size, jql_of, items_of):
        """One fetch per batch of ``size`` keys (as the old SKILL did), merged."""
        if not keys:  # nothing to ask for: no query (as the old SKILL)
            return {"issues": [], "fetch": {"name": name, "complete": True, "problems": [], "fetched": 0},
                    "skipped_signals": [], "people": {}, "warnings": [], "left_out": []}
        fetches = [self.fetch(name, jql_of(keys[n:n + size]), items_of(keys[n:n + size]))
                   for n in range(0, len(keys), size)]
        problems = [p for f in fetches for p in f["fetch"]["problems"]]
        issues = list({i["key"]: i for f in fetches for i in f["issues"]}.values())
        return dict(fetches[0], issues=issues, fetch=dict(fetches[0]["fetch"], complete=not problems,
                                                          problems=problems, fetched=len(issues)))

    def _epics(self, feature_keys):
        projects = ", ".join(self.w["projects"]["release_epics"])
        adapted = self.complete("epics", self._batched(
            "epics", feature_keys, 50,
            lambda chunk: f'project in ({projects}) AND "Parent Link" in ({", ".join(chunk)})',
            lambda chunk: parent_link_items(self.w, chunk)))
        return self.box.run("transform-epics.py", "--input", self.box.issues("epics", adapted),
                            "--output", "epics.json")

    def _children(self, epic_keys, required=True):
        fetch = self._batched(
            "stories", epic_keys, 20,
            lambda chunk: f"project in ({self.w['projects']['stories']}) AND parent in ({', '.join(chunk)})",
            lambda chunk: children_of(self.w, chunk))
        return self.complete("stories", fetch) if required else fetch

    def _spikes(self, features_fetch, window, refinement_closed):
        ref = window["refinement_sprint"]
        spikes = self.complete("refinement-sprint spikes", self.fetch(
            "ref_spikes", f"project in (OCPEDGE, USHIFT) AND issuetype = Spike AND sprint = {ref['id']}",
            self.w["ref_spikes"]))
        reads = {i["key"]: self.get_jira_issue(i["key"], fields="issuelinks") for i in spikes["issues"]}
        problems = ad.attach_issue_links(spikes, reads)
        if problems:
            raise Incomplete(f"spike links incomplete: {'; '.join(problems)}")
        linked = {f["key"]: self.fetch(f"linked_{f['key']}", f'issue in linkedIssues("{f["key"]}", "is blocked by") '
                                       "AND issuetype = Spike", self.w["linked_spikes"].get(f["key"], {}))
                  for f in features_fetch["issues"]}
        ad.attach_linked_spike_fetches(features_fetch, linked)
        self.complete("features", features_fetch)
        all_linked = [i for fetch in linked.values() for i in fetch["issues"]]
        sprints = self.box.write("sprints.json", {"refinement_sprint_closed": refinement_closed})
        features = self.box.run("transform-features.py", "--input", self.box.issues("features", features_fetch),
                                "--output", "features.json")
        out = self.box.run("transform-spikes.py", "--input", self.box.issues("spikes", spikes),
                           "--linked-spikes", self.box.write("linked_raw.json", {"issues": all_linked}),
                           "--features-file", features, "--epics-file", os.path.join(self.box.path, "epics.json"),
                           "--sprints-file", sprints, "--output", "spikes.json")
        return self.box.read("features.json"), self.box.read(os.path.basename(out))

    def refinement(self, sec):
        jql = features_jql(self.w, sec["release"], open_only=True)
        fetch = self.complete("features", self.fetch("features", jql, targeting(self.w["features"], jql)))
        keys = [i["key"] for i in fetch["issues"]]
        self._epics(keys)
        epics = self.box.read("epics.json")
        counted = self._children(epics["epic_keys"], required=False)
        counts, why = None, f"epic issues not fetched ({'; '.join(counted['fetch']['problems'])})"
        if counted["fetch"]["complete"]:  # else only the empty-epic check is left out (refinement step 2)
            why = None
            counts = {}
            for i in counted["issues"]:
                parent = (i.get("parent") or {}).get("key")
                counts[parent] = counts.get(parent, 0) + 1
        self.steps["board sprints"]["list"] += 1  # the refinement sprint's state (refinement step 5)
        state = next(s["state"] for s in board(self.active) if s["id"] == sec["window"]["refinement_sprint"]["id"])
        features, spikes = self._spikes(fetch, sec["window"], state == "closed")
        return cr.refinement_gaps(features, spikes, sec, state == "closed", epics=epics, epic_issue_counts=counts,
                                  week=self.ctx["current_sprint"]["week"], epic_issue_counts_why=why)

    def release(self, sec, velocity, reason):
        tag, window = sec["release"], sec["window"]
        jql = features_jql(self.w, tag)
        features_fetch = self.fetch("features", jql, targeting(self.w["features"], jql), order="rank")
        self.complete("features", features_fetch)
        keys = [i["key"] for i in features_fetch["issues"]]
        epics_file = self._epics(keys)
        epics = self.box.read("epics.json")
        stories_raw = self.box.issues("stories", self._children(epics["epic_keys"]))
        stories_file = self.box.run("transform-stories.py", "--input", stories_raw, "--today", self.today,
                                    "--output", "stories.json")
        bugs_jql = (f'project = {self.w["projects"]["bugs"]} AND fixVersion in ("{tag}", "{tag}.0") AND '
                    '"Epic Link" is EMPTY')
        bugs = self.complete("bugs", self.fetch("bugs", bugs_jql, self.w["bugs"]))
        bugs_file = self.box.run("transform-bugs.py", "--input", self.box.issues("bugs", bugs), "--today", self.today,
                                 "--output", "bugs.json")
        bc = window["branch_cut_sprint"]["number"]
        entries = [s for s in self.release_results[tag]["sprint_availability"] if s["number"] <= bc]
        capacity = cr.release_capacity(velocity, entries, self.today)
        features, spikes = self._spikes(features_fetch, window, True)  # step 8: the refinement sprint is closed
        args = ["--features", os.path.join(self.box.path, "features.json"), "--epics", epics_file,
                "--stories", stories_file, "--bugs", bugs_file, "--roster", self.roster_file,
                "--remaining-sprints", str(capacity["remaining_sprints"]),
                "--availability", capacity["availability_csv"],
                "--version", tag, "--today", self.today, "--first-sprint", str(window["refinement_sprint"]["number"]),
                "--pencils-down", str(bc), "--focus-priorities", "Blocker,Critical,Major",
                "--include-lower-when", "Refinement", "--no-hidden-scope-estimate", "--output", "checks.json"]
        if velocity is not None:
            args += ["--velocity-file", self.box.write("release_velocity.json",
                                                       {"team_velocity": velocity, "sprint_count": 3})]
        checks = self.box.read(os.path.basename(self.box.run("run-checks.py", *args)))
        # Step 9: feature and epic blocks.
        candidates = self.fetch("blocks", f"key in ({', '.join(keys + epics['epic_keys'])}) AND "
                                "issueLinkType = \"is blocked by\"",
                                block_rows(self.w))
        links, unread = None, []
        # release-health step 9: the failed fetch and its error when the candidates are incomplete
        problems = [f"block candidates fetch incomplete: {'; '.join(candidates['fetch']['problems'])}"]
        if candidates["fetch"]["complete"]:
            problems = ad.attach_issue_links(candidates, {i["key"]: self.get_jira_issue(i["key"], fields="issuelinks")
                                                          for i in candidates["issues"]})
            unread = sorted({p.split(":")[0] for p in problems})
            links = {i["key"]: extract_blocked_by(i) for i in candidates["issues"] if i["key"] not in unread}
        return cr.release_health(checks, sec, capacity, cr.rank_order_available(features_fetch), reason,
                                 features=features, epics=epics, spikes=spikes,
                                 stories=self.box.read(os.path.basename(stories_file)), links=links,
                                 links_unread=unread, current_sprint_number=self.ctx["current_sprint"]["number"],
                                 links_problems=problems)

    # -- owners, build and post (steps 5 and 6)
    def epic_fields(self):
        """Release-health step 5 as the old SKILL ran it: the active-epics query, then key
        lookups of their parents in batches of 40."""
        epics = self.fetch("field_epics", EPICS_JQL, field_epic_items(self.w))
        keys = sorted({i["parent"]["key"] for i in epics["issues"] if (i.get("parent") or {}).get("key")},
                      key=cr._key_number)
        batches = []
        for n in range(0, len(keys), 40):
            chunk = keys[n:n + 40]
            batches.append({"keys": chunk, "result": self.fetch(
                "parents", f"key in ({', '.join(chunk)})", lookup(self.w, chunk))})
        return cr.epic_field_gaps(epics, batches)

    def mentions(self, sprint, computed, gaps):
        self.owners = wr.report_owners(sprint, computed, gaps, SCRUM_MASTER)
        contacts = {normalize_name(c["display_name"]): c for c in self.roster["contacts"]}
        out = {}
        for owner in self.owners:
            row = contacts.get(normalize_name(owner))
            user = SLACK.get(row["email"]) if row and row["email"] else None
            if user:
                out[owner] = f"<@{user}>"
        return out

    def post(self, text, thread=None):
        """Slack post: one message text."""
        if not isinstance(text, str) or not text.strip():
            raise TypeError(f"a Slack message text must be a non-empty string, got {type(text).__name__}")
        self.posted.append(text)
        return len(self.posted)

    def __call__(self, active):
        self.active = active
        sections = self.context()
        velocity, reason = self.velocity()
        sprint = self.sprint(velocity, reason)
        computed = []
        for sec in sections:
            try:
                computed.append(self.refinement(sec) if sec["kind"] == cr.REFINEMENT
                                else self.release(sec, velocity, reason))
            except Incomplete as exc:
                self.missing.append({"what": f"{sec['release']} release section", "why": str(exc)})
        gaps = self.epic_fields()
        # The old SKILL: one Python call per page (next_page_jql), per script, and per
        # computation (gate, sections, sprint health, each section, links, epic flags, owners, build).
        attaches = (1 if sprint is not None and sprint.get("mode") == "mid-sprint" else 0) + 2 * len(sections)
        self.steps["python"]["sandbox"] = (sum(c["search"] for c in self.steps.values()) + sum(self.scripts.values())
                                           + 6 + len(sections) + attaches)
        mentions = self.mentions(sprint, computed, gaps)
        self.steps["slack"]["lookup"] += len(self.owners)
        scope = build(sprint, computed, self.missing, mentions, SCRUM_MASTER, gaps)
        if scope["failure"] is not None:  # sprint health incomplete: nothing is posted
            self.status = scope["failure"]  # the run ends with the SKILL's step-6 end steps
            self.final_steps = end_steps(scope["failure"], self.w["interactive"])
            return None
        thread = self.post(scope["top_post_text"])
        for reply in scope["replies"]:
            self.post(reply, thread)
        return scope["report"]


EPIC_KEY_BATCH, PARENT_KEY_BATCH = 50, 40


class WeeklyRun(LegacyRun):
    """One weekly report as the weekly SKILL runs it: the shared fetch plan.

    Logical fetches go out together in waves. Each paging round is one tool round,
    then one sandbox call: ``chai_jira_adapter.paging_round`` decides every page of
    the round, and ``adapt_fetches`` adapts each logical fetch that finished, once,
    from all its pages. The release Feature gate also gives the refinement features;
    one release epics fetch serves every release section (the decision 0024 epics
    are their own fetch); one stories fetch serves every section; links are read
    only for the candidates, once each. Velocity fetches only the closed sprints
    the optional store does not hold. Counts every tool call."""

    def __init__(self, w, today, sandbox):
        super().__init__(w, today, sandbox)
        self.store_writes, self.left, self.read_cache = {}, {}, {}
        # Per wave: the slots sent in each paging round, and the round each logical fetch
        # was adapted in (the SKILL's schedule, checked by the tests).
        self.rounds, self.adapted_in = {}, {}

    # -- tools
    def answerer(self, name, items):
        calls = self.calls.setdefault(name, [])

        def answer(sent):
            calls.append(sent)
            self.steps[step_of(name)]["search"] += 1
            if name in self.w["fail"]:
                return {"error": "tool call failed"}
            stuck = name in self.w["stuck"] or (name in self.w["stuck_once"] and calls.count(sent) == 1)
            text = whole_values(self.w, self.steps, query_jira(sent, items, stuck))
            if name in self.w["unordered"] and isinstance(text, str) and text.startswith(MORE_MATCH):
                text = unordered(text)
            flaky = self.w["flaky"].get(name) if len(calls) == 1 else None
            return {"error": "tool call timed out"} if flaky == "error" else cut_off(text) if flaky else text
        return answer

    def read(self, keys, step):
        """get_jira_issue(fields="issuelinks") for each key not read yet; True when a
        read needed its retry (one more round)."""
        before = len(self.issue_reads)
        new = [k for k in dict.fromkeys(keys) if k not in self.read_cache]
        for key in new:
            self.read_cache[key] = self.get_jira_issue(key, fields="issuelinks", step=step)
        return len(self.issue_reads) - before > len(new)

    def read_store(self):
        store = self.w["velocity_store"]
        if store is None:
            return None  # no store configured: no memory call
        self.steps["velocity store"]["memory"] += 1
        return None if store == "unavailable" else dict(store)

    @staticmethod
    def _slot(project, sent):
        return {"project": project, "sent": sent, "pages": [], "retried": False, "done": False}

    def wave(self, label, specs, reads=(), read_step="links"):
        """Send ``specs`` (logical fetches; ``logical`` joins several into one) and
        ``reads`` together, and page them in lock-step rounds, as the SKILL's Jira paging
        recipe does: one tool round with a page for every ready slot (a split's parts go
        out together, each under its own slot), then one sandbox call that decides every
        page (``paging_round``) and adapts each logical fetch whose parts all ended in that
        round (``adapt_fetches``). Returns the adapted fetches by logical name. A rank page
        that says more match goes on by key."""
        state, members = {}, collections.defaultdict(list)
        for spec in specs:
            first = (f"({spec['jql']}) ORDER BY Rank ASC" if spec.get("order") == "rank"
                     else ad.page_jql(spec["jql"]))
            state[spec["name"]] = {"spec": spec, "slots": [self._slot(None, first)],
                                   "answer": self.answerer(spec["name"], spec["items"])}
            members[spec.get("logical") or spec["name"]].append(spec["name"])
        read_rounds = (2 if self.read(reads, read_step) else 1) if reads else 0
        rounds, rank_more, adapted, log = 0, set(), {}, self.rounds.setdefault(label, [])
        while any(not slot["done"] for st in state.values() for slot in st["slots"]):
            rounds += 1
            open_slots = [(name, slot) for name, st in state.items() for slot in st["slots"] if not slot["done"]]
            ready = {f"{name}/{slot['project'] or 'other'}": (name, slot) for name, slot in open_slots}
            if len(ready) != len(open_slots):
                raise AssertionError(f"two parts of one fetch share a slot: {sorted(ready)}")
            log.append(sorted(ready))
            pending = {key: {"jql": slot["sent"], "page": state[name]["answer"](slot["sent"]),
                             "retried": slot["retried"]} for key, (name, slot) in ready.items()}
            for key, decision in ad.paging_round(pending).items():  # the round's one sandbox call
                (name, slot), page = ready[key], pending[key]["page"]
                st, entry = state[name], manifest_page(page, slot["sent"])
                if "retry" in decision:
                    slot["retried"] = True
                    continue
                if "split" in decision:  # its parts take its place, in order; its pages are dropped
                    at = st["slots"].index(slot)
                    st["slots"][at:at + 1] = [self._slot(p["project"], p["jql"]) for p in decision["split"]]
                    continue
                if "next" in decision and st["spec"].get("order") == "rank" and name not in rank_more:
                    # The rank page did not fit: next_page_jql gives the first key-ordered page;
                    # the whole release features query is fetched by key (the cut line is left out).
                    rank_more.add(name)
                    slot["sent"], slot["retried"] = decision["next"], False
                    continue
                if "incomplete" in decision:  # the kept page carries the reason paging stopped there
                    entry = dict(entry, incomplete=decision["incomplete"])
                slot["pages"].append(entry)
                if "next" in decision:
                    slot["sent"], slot["retried"] = decision["next"], False
                else:
                    slot["done"] = True
            finished = {}  # every part of the logical fetch ended: adapted in this round's call
            for lname, names in members.items():
                if lname not in adapted and all(sl["done"] for n in names for sl in state[n]["slots"]):
                    order = "rank" if any(state[n]["spec"].get("order") == "rank" and n not in rank_more
                                          for n in names) else "key"
                    parts = [{"project": sl["project"], "pages": sl["pages"]}
                             for n in names for sl in state[n]["slots"]]
                    self.pages[lname] = sum(len(part["pages"]) for part in parts)
                    finished[lname] = {"name": lname, "order": order, "parts": parts}
                    self.adapted_in[(label, lname)] = rounds
            adapted.update(ad.adapt_fetches(finished, self.roster) if finished else {})
        # One Python call per paging round (scripts and computations run inside those calls).
        self.steps["python"]["sandbox"] += max(rounds, read_rounds)
        for lname, out in adapted.items():
            self.left[lname] = out["left_out"]
        return adapted

    def take_left(self, *names):
        for name in names:
            self.missing += self.left.pop(name, [])

    # -- the weekly report
    def listing(self):
        """The board sprint listing step 1.3 reads once ([] when it still fails)."""
        if self.w["board_error"]:
            return []
        last = self.w["board_last"]
        return [s for s in board(self.active) if last is None or int(s["id"]) <= _sprint_id(last)]

    def gate_and_sections(self, gate_fetch):
        self.take_left("feature_gate")
        scope = {"chai_report": cr, "gate_fetch": gate_fetch}
        gate = cr.release_feature_gate(scope["gate_fetch"])
        candidates = gate["releases"]
        base = {"today": self.today, "board_id": BOARD, "schedules": self.product_pages(candidates),
                "sprints": self.listing(), "engineer_count": 3, "absences": []}
        self.ctx = sc.build_context(base)
        self.release_results = {r: sc.build_context(dict(base, release=r)) for r in candidates}
        windows = [result["release"] for result in self.release_results.values()]
        sections, missing = cr.select_sections(self.ctx, windows, gate)
        self.missing += missing
        return sections

    def velocity_from(self, closed, hits, to_fetch, fetched, stored, digest):
        files, complete = [], []
        self.take_left(*[f"velocity_{s['number']}" for s in to_fetch])
        for s in to_fetch:
            out = fetched[f"velocity_{s['number']}"]
            if not out["fetch"]["complete"]:
                return None, f"velocity sprint {s['number']} fetch incomplete: {'; '.join(out['fetch']['problems'])}"
        for s in to_fetch:
            n = s["number"]
            meta = self.box.write(f"meta_{n}.json", {"sprint_id": int(s["id"]), "sprint_name": s["name"],
                                                     "sprint_start": s["start"].isoformat(),
                                                     "sprint_end": s["end"].isoformat()})
            files.append(self.box.run("transform-sprint-issues.py", "--input",
                                      self.box.issues(f"v{n}", fetched[f"velocity_{n}"]), "--sprint-meta", meta,
                                      "--today", self.today, "--output", f"v{n}.json"))
            complete.append(s["id"])
        fresh = {"sprints": []}
        if files:
            with open(self.box.run("compute-velocity.py", "--sprint-issues", *files, "--roster", self.roster_file,
                                   "--output", "velocity.json"), encoding="utf-8") as fh:
                fresh = json.load(fh)
        self.store_writes = vc.creates(closed, fresh, complete, stored, BOARD, digest)
        return cr.usable_velocity(vc.combine(closed, hits, fresh))

    def sprint_from(self, fetched, velocity, reason):
        cur = self.ctx["current_sprint"]
        mode = cr.sprint_mode(cur["week"])
        self.take_left("sprint_issues")
        try:
            adapted = self.complete("sprint issue", fetched["sprint_issues"])
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
        meta = self.box.write("meta.json", {"sprint_id": int(cur["id"]), "sprint_name": cur["name"],
                                            "sprint_start": cur["start"], "sprint_end": cur["end"]})
        out = self.box.run("transform-sprint-issues.py", "--input", self.box.issues("sprint", adapted),
                           "--sprint-meta", meta, "--today", self.today, "--output", "sprint_issues.json")
        with open(out, encoding="utf-8") as fh:
            issues = json.load(fh)
        return cr.sprint_health(issues, cur, self.today, mode, velocity, self.ctx["current_sprint_availability"],
                                adapted["skipped_signals"], links_complete, velocity_reason=reason,
                                links_problems=links_problems)

    def spikes_from(self, tag, features_fetch, spikes_fetch, refinement_closed):
        self.complete("refinement-sprint spikes", spikes_fetch)
        spikes = json.loads(json.dumps(spikes_fetch))  # attach_issue_links fills the copy only
        problems = ad.attach_issue_links(spikes, {i["key"]: self.read_cache.get(i["key"]) for i in spikes["issues"]})
        if problems:
            raise Incomplete(f"spike links incomplete: {'; '.join(problems)}")
        self.take_left(*self.linked_batches)
        linked = {f["key"]: self.linked[f["key"]] for f in features_fetch["issues"]}
        ad.attach_linked_spike_fetches(features_fetch, linked)
        self.complete("features", features_fetch)
        all_linked = [i for fetch in linked.values() for i in fetch["issues"]]
        sprints = self.box.write(f"sprints_{tag}.json", {"refinement_sprint_closed": refinement_closed})
        features = self.box.run("transform-features.py", "--input", self.box.issues(f"features_{tag}", features_fetch),
                                "--output", f"features_{tag}.json")
        out = self.box.run("transform-spikes.py", "--input", self.box.issues(f"spikes_{tag}", spikes),
                           "--linked-spikes", self.box.write(f"linked_raw_{tag}.json", {"issues": all_linked}),
                           "--features-file", features,
                           "--epics-file", os.path.join(self.box.path, f"epics_{tag}.json"),
                           "--sprints-file", sprints, "--output", f"spikes_{tag}.json")
        return self.box.read(f"features_{tag}.json"), self.box.read(os.path.basename(out))

    def epics_of(self, tag, part):
        self.take_left("epics")
        self.complete("epics", part)
        self.box.run("transform-epics.py", "--input", self.box.issues(f"epics_{tag}", part),
                     "--output", f"epics_{tag}.json")
        return self.box.read(f"epics_{tag}.json")

    def refinement_from(self, sec, f, data):
        tag = sec["release"]
        self.complete("features", f)
        epics = self.epics_of(tag, data["epics"][tag])
        self.take_left("stories")
        mine = [i["key"] for i in data["epics"][tag]["issues"]]
        # None (a page not whole, a story not attributable): only the empty-epic check is left
        # out, with the reason; no epic: no story needed.
        counts, why = cr.epic_issue_counts(data["fetched"].get("stories"), mine, data["all_epics"])
        self.take_left(data["names"]["ref_spikes"][tag])
        state = next(s["state"] for s in board(self.active) if s["id"] == sec["window"]["refinement_sprint"]["id"])
        features, spikes = self.spikes_from(tag, f, data["fetched"][data["names"]["ref_spikes"][tag]],
                                            state == "closed")
        return cr.refinement_gaps(features, spikes, sec, state == "closed", epics=epics, epic_issue_counts=counts,
                                  week=self.ctx["current_sprint"]["week"], epic_issue_counts_why=why)

    def release_from(self, sec, f, data, velocity, reason):
        tag, window = sec["release"], sec["window"]
        self.take_left(data["names"]["features"][tag])
        self.complete("features", f)
        epics = self.epics_of(tag, data["epics"][tag])
        epics_file = os.path.join(self.box.path, f"epics_{tag}.json")
        self.take_left("stories")
        stories = self.complete("stories", data["stories"][tag])
        stories_file = self.box.run("transform-stories.py", "--input", self.box.issues(f"stories_{tag}", stories),
                                    "--today", self.today, "--output", f"stories_{tag}.json")
        bugs_name = data["names"]["bugs"][tag]
        self.take_left(bugs_name)
        bugs = self.complete("bugs", data["fetched"][bugs_name])
        bugs_file = self.box.run("transform-bugs.py", "--input", self.box.issues(f"bugs_{tag}", bugs),
                                 "--today", self.today, "--output", f"bugs_{tag}.json")
        bc = window["branch_cut_sprint"]["number"]
        entries = [s for s in self.release_results[tag]["sprint_availability"] if s["number"] <= bc]
        capacity = cr.release_capacity(velocity, entries, self.today)
        self.take_left(data["names"]["ref_spikes"][tag])
        features, spikes = self.spikes_from(tag, f, data["fetched"][data["names"]["ref_spikes"][tag]], True)
        args = ["--features", os.path.join(self.box.path, f"features_{tag}.json"), "--epics", epics_file,
                "--stories", stories_file, "--bugs", bugs_file, "--roster", self.roster_file,
                "--remaining-sprints", str(capacity["remaining_sprints"]),
                "--availability", capacity["availability_csv"],
                "--version", tag, "--today", self.today, "--first-sprint", str(window["refinement_sprint"]["number"]),
                "--pencils-down", str(bc), "--focus-priorities", "Blocker,Critical,Major",
                "--include-lower-when", "Refinement", "--no-hidden-scope-estimate", "--output", f"checks_{tag}.json"]
        if velocity is not None:
            args += ["--velocity-file", self.box.write("release_velocity.json",
                                                       {"team_velocity": velocity, "sprint_count": 3})]
        checks = self.box.read(os.path.basename(self.box.run("run-checks.py", *args)))
        self.take_left("blocks")
        blocks, keys = data["fetched"].get("blocks"), set(data["block_keys"][tag])
        links, unread = None, []
        problems = [f"block candidates fetch incomplete: {'; '.join(blocks['fetch']['problems'])}"] if blocks else []
        if not keys:  # no feature or epic: nothing to check, not an unchecked block
            links, problems = {}, []
        elif blocks and blocks["fetch"]["complete"]:
            candidates = [i for i in json.loads(json.dumps(blocks["issues"])) if i["key"] in keys]
            part = {"issues": candidates, "fetch": dict(blocks["fetch"])}
            problems = ad.attach_issue_links(part, {i["key"]: self.read_cache.get(i["key"]) for i in candidates})
            unread = sorted({p.split(":")[0] for p in problems})
            links = {i["key"]: extract_blocked_by(i) for i in candidates if i["key"] not in unread}
        return cr.release_health(checks, sec, capacity, cr.rank_order_available(f), reason,
                                 features=features, epics=epics, spikes=spikes,
                                 stories=self.box.read(f"stories_{tag}.json"), links=links,
                                 links_unread=unread, current_sprint_number=self.ctx["current_sprint"]["number"],
                                 links_problems=problems)

    def __call__(self, active):
        self.active = active
        P, X = self.w["projects"], ad.EXTRA_FIELDS
        # Stage 1, one tool round: the board listing (one, all states; it also gives the
        # velocity sprints and the refinement sprint's state), the velocity store, the
        # Product Pages listing, and the gate's first page.
        self.steps["board sprints"]["list"] += 2 if self.w["board_error"] else 1  # a failed listing: sent again once
        stored = self.read_store()
        gate_src = self.w["features"] if self.w["gate"] is None else self.w["gate"]
        gate_items = field_view({k: v for k, v in gate_src.items() if not _closed(v)}, X["feature_gate"])
        gate_jql = GATE_JQL.replace("project = OCPSTRAT", f"project = {P['features']}")
        fetched = self.wave("gate", [{"name": "feature_gate", "jql": gate_jql, "items": gate_items}])
        gate_fetch = fetched["feature_gate"]
        if self.w["after_gate"]:
            self.w["after_gate"](self.w)
        sections = self.gate_and_sections(gate_fetch)  # in the gate's last paging round
        cur = self.ctx["current_sprint"]
        # No current sprint id (the board listing failed after its retry, or the sprint is not
        # on the board yet): only the queries that need it are skipped (step 2b, wave 1); the
        # velocity sprints still come from a readable listing.
        has_sprint = isinstance(cur, dict) and bool(cur.get("id"))
        number = cur.get("number") if isinstance(cur, dict) else None
        closed = sc.last_closed_sprints(self.listing(), BOARD, number) if number is not None else []
        digest = vc.roster_digest(self.roster)
        hits, to_fetch = vc.plan(closed, stored, BOARD, digest)
        mode = cr.sprint_mode(cur["week"]) if has_sprint else None
        releases = [s for s in sections if s["kind"] != cr.REFINEMENT]
        many = len(sections) > 1
        names = {"features": {}, "bugs": {}, "ref_spikes": {}}

        def named(base, tag):
            names[base][tag] = f"{base}_{tag}" if many else base
            return names[base][tag]
        # Wave B: velocity (only the sprints the store lacks), sprint issues and blocked
        # candidates, each release's rank-ordered features page and bugs, refinement spikes.
        specs = [{"name": f"velocity_{s['number']}", "jql": f"project in ({P['sprint']}) AND sprint = {s['id']}",
                  "items": field_view(self.w["velocity"].get(s["number"], {}), X["velocity"])} for s in to_fetch]
        if has_sprint:
            specs.append({"name": "sprint_issues", "jql": f"project in ({P['sprint']}) AND sprint = {cur['id']}",
                          "items": field_view(self.w["sprint"], X["sprint_issues"])})
        if mode == "mid-sprint":
            specs.append({"name": "sprint_blocked",
                          "jql": f"(project in ({P['sprint']}) AND sprint = {cur['id']}) "
                                 "AND issueLinkType = \"is blocked by\" AND statusCategory != Done",
                          "items": field_view({k: self.w["sprint"][k] for k in self.w["sprint_blocked"]}, ())})
        for sec in releases:
            tag = sec["release"]
            jql = features_jql(self.w, tag)
            specs.append({"name": named("features", tag), "jql": jql, "order": "rank",
                          "items": field_view(targeting(self.w["features"], jql), X["features"])})
            specs.append({"name": named("bugs", tag), "items": field_view(self.w["bugs"], ()),
                          "jql": f'project = {P["bugs"]} AND fixVersion in ("{tag}", "{tag}.0") '
                                 'AND "Epic Link" is EMPTY'})
        for sec in sections:
            ref = sec["window"]["refinement_sprint"]
            specs.append({"name": named("ref_spikes", sec["release"]), "items": field_view(self.w["ref_spikes"], ()),
                          "jql": f"project in (OCPEDGE, USHIFT) AND issuetype = Spike AND sprint = {ref['id']}"})
        fetched = self.wave("fetches", specs)
        velocity, reason = self.velocity_from(closed, hits, to_fetch, fetched, stored, digest)
        sprint_ok = has_sprint and fetched["sprint_issues"]["fetch"]["complete"]
        reads = ([i["key"] for i in fetched["sprint_blocked"]["issues"]]
                 if mode == "mid-sprint" and sprint_ok else [])
        for sec in sections:
            spikes = fetched[names["ref_spikes"][sec["release"]]]
            reads += [i["key"] for i in spikes["issues"]] if spikes["fetch"]["complete"] else []
        feats = {}
        for sec in sections:
            tag = sec["release"]
            if sec["kind"] == cr.REFINEMENT:
                feats[tag] = cr.gate_features(gate_fetch, tag)
            else:  # its rank page, or (more matched) the whole query by key
                feats[tag] = fetched[names["features"][tag]]
        # Wave C2: one release epics fetch for every section, the decision 0024 epics (their
        # own fetch: another predicate) and the spikes that block each feature.
        keys_by_rel = {t: [i["key"] for i in f["issues"]] for t, f in feats.items() if f["fetch"]["complete"]}
        all_keys = sorted(set().union(*keys_by_rel.values()), key=cr._key_number) if keys_by_rel else []
        chunks = [all_keys[n:n + EPIC_KEY_BATCH] for n in range(0, len(all_keys), EPIC_KEY_BATCH)]
        specs = [{"name": name, "logical": "epics",
                  "jql": f'project in ({", ".join(P["release_epics"])}) AND "Parent Link" in ({", ".join(chunk)})',
                  "items": field_view({**parent_link_items(self.w, chunk), **self.w["epic_reads"].get(name, {})},
                                      X["epics"])}
                 for name, chunk in ((f"epics_{n + 1}" if n else "epics", c) for n, c in enumerate(chunks))]
        specs.append({"name": "field_epics", "jql": EPICS_JQL,
                      "items": field_view(field_epic_items(self.w), X["field_epics"])})
        # The spikes that block each feature: one OR-joined query per 20 features; each spike
        # is attributed to the features it blocks by its own links (read in the next wave).
        # Batched per section, so an unattributable spike leaves out only that section's
        # features (a feature of two sections is read once, with the first).
        linked_chunks, seen = [], set()
        for t in keys_by_rel:
            mine = [i["key"] for i in feats[t]["issues"] if i["key"] not in seen]
            seen.update(mine)
            linked_chunks += [mine[n:n + ad.LINKED_SPIKE_BATCH] for n in range(0, len(mine), ad.LINKED_SPIKE_BATCH)]
        self.linked_batches = {"linked" if n == 0 else f"linked_{n + 1}": chunk
                               for n, chunk in enumerate(linked_chunks)}
        specs += [{"name": name, "jql": ad.linked_spikes_jql(chunk),
                   "items": field_view({k: v for f in chunk
                                        for k, v in self.w["linked_spikes"].get(f, {}).items()}, ())}
                  for name, chunk in self.linked_batches.items()]
        more = self.wave("epics and spikes", specs, reads=reads, read_step="links")
        parts = cr.partition_epics(more.get("epics"), keys_by_rel)  # a release with no feature: no epics
        epic_keys = {t: [i["key"] for i in part["issues"]] for t, part in parts.items()}
        # Wave D: the shared stories fetch, the block candidates, the parents not already read.
        all_epics = list(dict.fromkeys(k for t in epic_keys for k in epic_keys[t]))
        specs = []
        if all_epics:
            specs.append({"name": "stories", "items": field_view(children_of(self.w, all_epics), X["stories"]),
                          "jql": f"project in ({P['stories']}) AND parent in ({', '.join(all_epics)})"})
        block_keys = {s["release"]: keys_by_rel.get(s["release"], []) + epic_keys.get(s["release"], [])
                      for s in releases}
        all_block = list(dict.fromkeys(k for t in block_keys for k in block_keys[t]))
        if all_block:
            specs.append({"name": "blocks", "items": field_view({k: v for k, v in block_rows(self.w).items()
                                                                 if k in set(all_block)}, ()),
                          "jql": f"key in ({', '.join(all_block)}) AND issueLinkType = \"is blocked by\""})
        field_parents = sorted({i["parent"]["key"] for i in more["field_epics"]["issues"]
                                if (i.get("parent") or {}).get("key")}, key=cr._key_number)
        # The latest whole copy first: the features fetches were read after the gate.
        reuse_from = [f for f in feats.values() if f["fetch"]["complete"]] + [gate_fetch]
        reused, missing = cr.reuse_parents(field_parents, reuse_from)
        parent_chunks = [missing[n:n + PARENT_KEY_BATCH] for n in range(0, len(missing), PARENT_KEY_BATCH)]
        for n, chunk in enumerate(parent_chunks):
            specs.append({"name": "parents" if n == 0 else f"parents_{n + 1}", "jql": f"key in ({', '.join(chunk)})",
                          "items": field_view(lookup(self.w, chunk), X["parents"])})
        spike_reads = [i["key"] for name in self.linked_batches for i in more[name]["issues"]
                       if i["key"] not in self.read_cache]
        later = (self.wave("stories, blocks, parents", specs, reads=spike_reads, read_step="links")
                 if specs or spike_reads else {})
        self.linked = {}
        for name, chunk in self.linked_batches.items():
            self.linked.update(ad.split_linked_spikes(chunk, more[name], self.read_cache))
        stories = cr.group_stories(later.get("stories", {"issues": [], "fetch": {"complete": True, "problems": []}}),
                                   epic_keys)
        # Wave E: links of the block candidates only (one tool round, one sandbox call).
        if later.get("blocks") and later["blocks"]["fetch"]["complete"]:
            block_reads = [i["key"] for i in later["blocks"]["issues"]]
            if block_reads:
                self.steps["python"]["sandbox"] += 2 if self.read(block_reads, "links") else 1
        if has_sprint:
            sprint = self.sprint_from(fetched, velocity, reason)
        else:  # step 3: sprint health has no result; step 6 posts nothing
            sprint = None
            self.missing.append({"what": cr.SPRINT_HEALTH, "why": (
                f"board sprint fetch failed: {self.w['board_error']}" if self.w["board_error"]
                else "current sprint not on the board yet")})
        data = {"fetched": {**fetched, **later}, "epics": parts, "stories": stories, "names": names,
                "all_epics": all_epics,
                "block_keys": block_keys}
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
        mentions = self.mentions(sprint, computed, gaps)
        self.steps["slack"]["lookup"] += len(self.owners)
        self.steps["python"]["sandbox"] += 1  # the build block, after the Slack lookups
        scope = build(sprint, computed, self.missing, mentions, SCRUM_MASTER, gaps)
        if scope["failure"] is not None:  # sprint health incomplete: nothing is posted
            self.status = scope["failure"]  # the run ends with the SKILL's step-6 end steps
            self.final_steps = end_steps(scope["failure"], self.w["interactive"])
            return None
        thread = self.post(scope["top_post_text"])
        for reply in scope["replies"]:
            self.post(reply, thread)
        if stored is not None and self.store_writes:  # create-only, after the post
            self.steps["velocity store"]["memory"] += len(self.store_writes)
            for key, entry in self.store_writes.items():
                self.w["velocity_store"].setdefault(key, entry)
        return scope["report"]


def weekly(today, active, change=None):
    """The posted messages and the report for the synthetic team on ``today``."""
    if change is None:
        return _unchanged(today, active)
    run, report = weekly_run(today, active, change)
    return run.posted, report


def weekly_run(today, active, change=None, legacy=False):
    """The finished :class:`WeeklyRun` (posted messages, pages per fetch, read counts) and
    the report; ``legacy`` runs the fetch plan from before the shared plan instead."""
    w = world(active)
    if change:
        change(w)
    with tempfile.TemporaryDirectory() as path:
        run = (LegacyRun if legacy else WeeklyRun)(w, today, Sandbox(path))
        report = run(active)
    return run, report


@functools.cache
def _unchanged(today, active):
    """The unchanged world's run, computed once per day (the tests only read it)."""
    run, report = weekly_run(today, active)
    return run.posted, report


def _left_out(posted):
    return next((ln for ln in posted[-1].splitlines() if ln.startswith("Left out: ")), "")


EPIC_REPLY = (
    "*Epic fields* (8 epics: 4 no Target Version, 5 no QA Contact, 1 Target Version ≠ feature, 3 feature closed "
    "or in backlog, 3 no assignee; ✓ 2 complete). Key: \"Target Version ≠ feature\" = retarget, split or keep?\n"
    "• <@U000TEST3>: no assignee for TEST-102, 103, 108; no Target Version for TEST-103, 112; no QA Contact for "
    "TEST-102, 103; TEST-106 (no QA Contact, feature has none, feature closed) +1 more problem(s)\n"
    "• <@U000TEST1>: no Target Version for TEST-104, 107; TEST-104 (no QA Contact, feature has none, feature "
    "closed); TEST-107 (feature in backlog); TEST-101 (Target Version ≠ feature)")
# The normal week-2 report (development) as Slack receives it: the top post, then each reply.
NORMAL_WEEK_2 = (
    ("🔴 Edge weekly health: Test Sprint 14 (week 2, mid-sprint); 9.2 development\n"
     "Biggest risk: 1 sprint issue(s) blocked\n"
     "2 people have items: <@U000TEST1>, <@U000TEST3>"),
    ("*Sprint health: Test Sprint 14* (week 2, mid-sprint): 5 of 8 SP done, 3.3 expected (On Track); "
     "✓ 1 open issues not blocked\n"
     "• <@U000TEST1>: TEST-12 (blocked by TEST-99)"),
    ("*9.2 release health* (development): scope 8 SP vs capacity 21 SP; 25% of development expected done; "
     "✓ 0 of 3 feature(s) on track\n"
     "• Leads, assign SMEs (<@U000TEST3>): 1 feature(s) have no SME: FEAT-10\n"
     "• <@U000TEST1>: FEAT-20 (refinement overdue, behind schedule: 0% done vs 25% expected), "
     "FEAT-30 (blocked by BUG-77)"),
    (EPIC_REPLY + "\n"
     "Left out: Target Version and parent status checks for 1 epic(s) under FEAT-9: parent could not be read: "
     "not returned by Jira (restricted, deleted or no permission)"),
)
# The same week with the features fetch failed after its retry: posted without the release section.
SECTION_FAILED_WEEK_2 = (
    ("🔴 Edge weekly health: Test Sprint 14 (week 2, mid-sprint)\n"
     "Biggest risk: 1 sprint issue(s) blocked\n"
     "2 people have items: <@U000TEST3>, <@U000TEST1>"),
    NORMAL_WEEK_2[1],
    (EPIC_REPLY + "\n"
     "Left out: 9.2 release section: features fetch incomplete: fetch: page 1: query_jira call failed: tool call "
     "failed; Target Version and parent status checks for 1 epic(s) under FEAT-9: parent could not be read: not "
     "returned by Jira (restricted, deleted or no permission)"),
)


# ---------------------------------------------------------------- tests


class PayloadTest(unittest.TestCase):
    def test_posted_messages_are_strings_from_the_skill_block(self):
        posted, report = weekly(day(14, 2), 14)
        self.assertEqual(posted[0], "\n".join(report["top_post"]))
        self.assertEqual(posted[1:], report["replies"])
        self.assertEqual(len(posted[0].splitlines()), 3)
        self.assertEqual(budget_problems(report), [])

    def test_the_line_list_is_not_a_message(self):
        report = cr.build_report(None, [])
        self.assertIsInstance(report["top_post"], list)
        self.assertIsInstance(report["top_post_text"], str)
        with tempfile.TemporaryDirectory() as path, self.assertRaises(TypeError):
            WeeklyRun(world(14), day(14, 2), Sandbox(path)).post(report["top_post"])


class SprintWeekTest(unittest.TestCase):
    def test_week_1_capacity(self):
        posted, _ = weekly(day(13, 1), 13)
        self.assertTrue(posted[0].startswith(("🔴", "🟡")))
        self.assertIn("Test Sprint 13 (week 1, capacity); 9.2 development", posted[0])
        sprint = posted[1]
        self.assertIn("committed 8 SP in 3 issues; capacity 8 SP (velocity 8 × availability 1.00)", sprint)
        self.assertIn("• <@U000TEST2>: TEST-13 (unpointed, no epic)", sprint)

    def test_week_2_mid_sprint_explicit_blocks(self):
        posted, _ = weekly(day(14, 2), 14)
        self.assertIn("(week 2, mid-sprint)", posted[1])
        self.assertIn("• <@U000TEST1>: TEST-12 (blocked by TEST-99)", posted[1])

    def test_final_week_view(self):
        posted, _ = weekly(day(14, 3), 14)
        self.assertIn("Test Sprint 14 (final week); 9.2 development", posted[0])
        self.assertNotIn("mid-sprint", posted[0] + posted[1])  # "final week" replaces it in both
        self.assertTrue(posted[1].startswith(
            "*Sprint health: Test Sprint 14* (final week): 5 of 8 SP done, 5.8 expected (Slightly Behind); "
            "2 still open (3 SP): 0 in review or testing (0 SP), 2 in progress or to do (3 SP)"))
        self.assertNotIn("carry-over", "\n".join(posted))
        self.assertNotIn("delivered", posted[1])
        self.assertIn("TEST-12 (blocked by TEST-99)", posted[1])  # a genuine blocker stays
        self.assertNotIn("TEST-13", posted[1])  # its only item was carry-over
        self.assertNotIn("<@U000TEST2>", posted[0])  # no mention for carry-over alone

    def test_final_week_stages_and_no_projection(self):
        final, _ = weekly(day(14, 3), 14, final_week_mix)
        self.assertEqual(final[0].splitlines()[1], "Biggest risk: Burndown behind: 5 of 14 SP done, 10.2 expected")
        self.assertIn("6 still open (9 SP): 2 in review or testing (3 SP), 3 in progress or to do (5 SP), "
                      "1 other status (1 SP)", final[1])
        self.assertNotIn("projected", "\n".join(final))
        self.assertNotIn("re-scope", "\n".join(final))
        week2, _ = weekly((_start(14) + dt.timedelta(days=10)).isoformat(), 14, final_week_mix)  # week 2, behind
        self.assertIn("Biggest risk: Burndown behind: 5 of 14 SP done, 7 expected; projected to finish 9 days after "
                      "the sprint ends", week2[0])  # unchanged before the final week
        self.assertIn("re-scope the sprint", week2[1])
        self.assertIn("(week 2, mid-sprint)", week2[0])


class ReleasePhaseTest(unittest.TestCase):
    def test_development_runs_every_feature_check(self):
        posted, _ = weekly(day(14, 2), 14)
        release = posted[2]
        self.assertTrue(release.startswith("*9.2 release health* (development): scope 8 SP vs capacity 21 SP"))
        self.assertIn("25% of development expected done; ✓ 0 of 3 feature(s) on track", release)
        self.assertIn("• Leads, assign SMEs (<@U000TEST3>): 1 feature(s) have no SME: FEAT-10", release)
        self.assertIn("FEAT-20 (refinement overdue, behind schedule: 0% done vs 25% expected)", release)
        self.assertIn("FEAT-30 (blocked by BUG-77)", release)
        self.assertNotIn("9.2", _left_out(posted))  # nothing of the release section left out

    def test_refinement_week_1_and_velocity_not_measured(self):
        posted, _ = weekly(day(12, 1), 12)
        self.assertIn("9.2 refinement", posted[0])
        refinement = posted[2]
        self.assertTrue(refinement.startswith(
            "*9.2 refinement* (refinement sprint 12): 3 feature(s) checked; ✓ 1 ready"))
        self.assertIn("• <@U000TEST2>: FEAT-20 (test plan spike missing)", refinement)
        # Two closed sprints only: capacity is left out with the reason, never estimated.
        self.assertIn("capacity not available", posted[1])
        self.assertIn("sprint capacity: only 2 closed sprint(s) measured, 3 needed", _left_out(posted))

    def test_pencils_down_sprint(self):
        posted, _ = weekly(day(15, 2), 15)
        self.assertIn("9.2 pencils-down sprint", posted[0])
        self.assertTrue(posted[2].startswith("*9.2 release health* (pencils-down sprint)"))

    def test_between_releases(self):
        posted, report = weekly(day(11, 2), 11)
        self.assertEqual(len(report["replies"]), 2)  # sprint, epic fields
        self.assertIn("Release section: no release in progress", _left_out(posted))

    def test_more_than_50_features_leave_the_cut_line_out(self):
        many = {f"FEAT-{n}": _feature(f"FEAT-{n}") for n in range(100, 160)}

        def change(w):
            w["features"], w["epics"], w["linked_spikes"], w["feature_blocks"] = many, {}, {}, []
            w["projects"]["features"] = "FEAT"
        run, _ = weekly_run(day(14, 2), 14, change)
        posted = run.posted
        jql = ('(project = FEAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") AND '
               '"Target Version" = "openshift-9.2" AND '
               '(resolution is EMPTY OR resolution not in (Duplicate, Obsolete)))')  # the documented query
        self.assertTrue(query_jira(f"{jql} ORDER BY Rank ASC", many).startswith(MORE_MATCH))
        # The rank page says more match: the whole release features query is fetched by key,
        # as before (the gate read earlier and a later Closed-only read could disagree when a
        # feature changes between them); each page is sent once.
        self.assertEqual(run.calls["features"], [f"{jql} ORDER BY Rank ASC", f"{jql} ORDER BY key DESC",
                                                 f"{jql} AND key < FEAT-110 ORDER BY key DESC"])
        self.assertNotIn("features_closed", run.calls)
        self.assertEqual(len(run.calls["feature_gate"]), 2)
        self.assertEqual(posted, weekly_run(day(14, 2), 14, change, legacy=True)[0].posted)  # the old key re-fetch
        self.assertIn("✓ 60 of 60 feature(s) on track", posted[2])
        self.assertNotIn("below the cut line", "\n".join(posted))
        self.assertIn("9.2 cut line: features did not fit on one rank-ordered page", _left_out(posted))


class PartialInputTest(unittest.TestCase):
    def assert_partial(self, posted, what, why):
        self.assertFalse(posted[0].startswith("🟢"))
        self.assertIn(f"{what}: {why}", _left_out(posted))

    def test_failed_features_call_leaves_the_release_section_out(self):
        run, report = weekly_run(day(14, 2), 14, lambda w: w["fail"].add("features"))
        posted = run.posted
        self.assertEqual(len(set(run.calls["features"])), 1)  # sent again once, unchanged
        self.assertEqual(len(run.calls["features"]), 2)
        self.assert_partial(posted, "9.2 release section",
                            "features fetch incomplete: fetch: page 1: query_jira call failed: tool call failed")
        self.assertNotIn("release health", "\n".join(posted))
        self.assertTrue(posted[1].startswith("*Sprint health: Test Sprint 14*"))  # the rest is posted
        self.assertEqual(budget_problems(report), [])
        self.assertEqual(posted, list(SECTION_FAILED_WEEK_2))

    def test_shortened_sme_is_not_read_as_a_person(self):
        def change(w):
            w["features"]["FEAT-20"] = _feature("FEAT-20", sme="(value stored; read it with result_page)")
        posted, _ = weekly(day(14, 2), 14, change)
        self.assert_partial(posted, "9.2 release section",
                            "features fetch incomplete: FEAT-20: customfield_10475 value shortened (not read whole)")
        self.assertNotIn("value stored", posted[0])

    def test_shortened_sprint_labels_post_nothing(self):
        def change(w):
            w["sprint"]["TEST-13"] = block("TEST-13", fields=["Labels: Parked, +1 more"])
        run, report = weekly_run(day(14, 2), 14, change)
        self.assertEqual((run.posted, report), ([], None))
        self.assertEqual(run.status, "Weekly health report not posted: Sprint health incomplete: sprint issue fetch "
                                     "incomplete: TEST-13: labels value shortened (not read whole)")

    def test_a_page_that_does_not_advance_once_is_recovered(self):
        def change(w):  # the tool ignores "key <" on the first send of a later page only
            w["bugs"] = {f"BUG-{n}": block(f"BUG-{n}", "Bug", fields=["Fix Versions: 9.2"]) for n in range(100, 160)}
            w["stuck_once"].add("bugs")
            w["projects"]["bugs"] = "BUG"
        run, _ = weekly_run(day(14, 2), 14, change)
        clean, _ = weekly_run(day(14, 2), 14, lambda w: (change(w), w["stuck_once"].clear()))
        self.assertEqual(len(run.calls["bugs"]), 3)  # page 2 sent again once; the corrected answer is used
        self.assertEqual(run.posted, clean.posted)  # the same report as with no glitch

    def test_non_advancing_pages_are_sent_again_once_then_left_out(self):
        def change(w):  # 60 unlinked bugs (one project); the tool ignores "key <"
            w["bugs"] = {f"BUG-{n}": block(f"BUG-{n}", "Bug", fields=["Fix Versions: 9.2"]) for n in range(100, 160)}
            w["stuck"].add("bugs")
            w["projects"]["bugs"] = "BUG"
        run, report = weekly_run(day(14, 2), 14, change)
        self.assertEqual(len(run.calls["bugs"]), 3)  # page 2 sent again once, unchanged, then the fetch stops
        self.assertEqual(run.calls["bugs"][1], run.calls["bugs"][2])
        self.assertEqual(run.pages["bugs"], 2)
        self.assert_partial(run.posted, "9.2 release section", "bugs fetch incomplete: fetch: ")
        self.assertIn("bugs fetch incomplete: fetch: page 2: key BUG-159 on the page is not below the key < BUG-110 "
                      "sent (the tool did not apply it)", _left_out(run.posted))  # the actual cause
        self.assertNotIn("release health", "\n".join(run.posted))
        self.assertTrue(run.posted[1].startswith("*Sprint health: Test Sprint 14*"))  # the rest is posted
        self.assertEqual(budget_problems(report), [])

    def test_paging_reaches_the_last_page(self):
        items = {f"TEST-{n}": block(f"TEST-{n}") for n in range(1, 121)}
        pages = fetch_by_skill("project = TEST", lambda sent: query_jira(sent, items))[0]["pages"]
        self.assertTrue(pages[0]["text"].startswith(MORE_MATCH))
        self.assertEqual([p["jql"] for p in pages], [
            "(project = TEST) ORDER BY key DESC",
            "(project = TEST) AND key < TEST-71 ORDER BY key DESC",
            "(project = TEST) AND key < TEST-21 ORDER BY key DESC"])
        out = ad.adapt_fetch({"pages": pages})
        self.assertEqual((out["fetch"]["complete"], out["fetch"]["fetched"]), (True, 120))

    def test_a_failed_or_cut_off_later_page_is_sent_again_once(self):
        items = {f"TEST-{n}": block(f"TEST-{n}") for n in range(1, 121)}
        for kind in ("error", "cut"):
            with self.subTest(first_answer=kind):
                sent = []

                def answer(jql, kind=kind, sent=sent):
                    sent.append(jql)
                    if len(sent) == 2:  # the page-2 request: its first answer fails
                        return {"error": "tool call timed out"} if kind == "error" else cut_off(query_jira(jql, items))
                    return query_jira(jql, items)
                pages = fetch_by_skill("project = TEST", answer)[0]["pages"]
                self.assertEqual(sent, ["(project = TEST) ORDER BY key DESC",
                                        "(project = TEST) AND key < TEST-71 ORDER BY key DESC",
                                        "(project = TEST) AND key < TEST-71 ORDER BY key DESC",
                                        "(project = TEST) AND key < TEST-21 ORDER BY key DESC"])
                self.assertEqual(len(pages), 3)  # the retry's answer in place of the failed one
                out = ad.adapt_fetch({"pages": pages})
                self.assertEqual((out["fetch"]["complete"], out["fetch"]["fetched"]), (True, 120))

    def test_refinement_milestone_no_sprint_starts_near(self):
        def change(w):
            # "Development Branch opens" mid-sprint: no sprint starts within the next 7 days.
            opens = dict(SCHEDULES[RELEASE][0], date=(_start(12) + dt.timedelta(days=10)).isoformat())
            w["schedules"] = {RELEASE: [opens] + SCHEDULES[RELEASE][1:]}
        posted, _ = weekly(day(14, 2), 14, change)
        self.assert_partial(posted, "9.2 release section", "refinement sprint could not be placed on a sprint")
        self.assertNotIn("release health", "\n".join(posted))

    def test_unread_issue_links_are_not_claimed_unblocked(self):
        def change(w):
            w["fail"] |= {"get_jira_issue TEST-12", "get_jira_issue FEAT-30"}
        posted, _ = weekly(day(14, 2), 14, change)
        self.assertIn("blocked-by links not checked", posted[1])
        self.assertIn("(blocks not checked for 1)", posted[2])
        self.assertIn("9.2 blocks of FEAT-30: FEAT-30: get_jira_issue failed", _left_out(posted))
        self.assertIn("blocked-by links: TEST-12: get_jira_issue failed", _left_out(posted))

    def test_failed_velocity_sprint_leaves_capacity_and_cut_line_out(self):
        posted, _ = weekly(day(14, 2), 14, lambda w: w["fail"].add("velocity_12"))
        self.assertIn("capacity not available (velocity sprint 12 fetch incomplete", posted[2])
        self.assertIn("9.2 capacity and cut line: velocity sprint 12 fetch incomplete", _left_out(posted))

    def test_scheduled_release_without_its_refinement_milestone(self):
        def change(w):
            w["schedules"] = {RELEASE: SCHEDULES[RELEASE][1:]}
        posted, _ = weekly(day(14, 2), 14, change)
        self.assertIn('9.2 release section: Product Pages schedule has no readable "Development Branch opens" '
                      'milestone',
                      _left_out(posted))


class MandatoryFetchTest(unittest.TestCase):
    """Every fetch runs; a failed or cut-off page is sent again once; sprint health incomplete posts nothing."""

    def test_incomplete_sprint_fetch_posts_nothing(self):
        run, report = weekly_run(day(14, 2), 14, lambda w: w["fail"].add("sprint_issues"))
        self.assertEqual((run.posted, report), ([], None))  # no top post, no replies
        self.assertEqual(run.status, "Weekly health report not posted: Sprint health incomplete: sprint issue fetch "
                                     "incomplete: fetch: page 1: query_jira call failed: tool call failed")
        self.assertEqual(len(run.calls["sprint_issues"]), 2)  # retried once
        self.assertEqual(len(set(run.calls["sprint_issues"])), 1)
        # The run did not stop early: every other fetch still ran.
        self.assertTrue({"feature_gate", "velocity_13", "features", "epics", "stories", "bugs", "blocks"}
                        <= set(run.calls))

    def test_retry_success_gives_the_complete_report(self):
        flaky = {"feature_gate": "cut", "velocity_12": "error", "sprint_issues": "error", "sprint_blocked": "cut",
                 "features": "cut", "epics": "error", "stories": "cut", "bugs": "cut",
                 "get_jira_issue TEST-12": "error", "get_jira_issue FEAT-30": "cut", "product_pages": "error"}
        run, report = weekly_run(day(14, 2), 14, lambda w: w["flaky"].update(flaky))
        self.assertIsNone(run.status)
        self.assertEqual(run.posted, list(NORMAL_WEEK_2))  # the same report as with no failure
        self.assertEqual(budget_problems(report), [])
        self.assertEqual(run.pp_attempts, 3)  # two listing pages, the first sent twice
        for name in flaky:
            if name == "product_pages":
                continue
            if name.startswith("get_jira_issue "):
                key = name.split()[1]
                self.assertEqual(sum(k == key for k, _ in run.issue_reads), 2, name)
            else:
                self.assertEqual(len(run.calls[name]), 2, name)  # one page, sent twice
                self.assertEqual(len(set(run.calls[name])), 1, name)  # unchanged

    def test_normal_report_is_unchanged(self):
        run, _ = weekly_run(day(14, 2), 14)
        self.assertIsNone(run.status)
        self.assertEqual(run.posted, list(NORMAL_WEEK_2))
        self.assertEqual({len(c) for c in run.calls.values()}, {1})  # no failure, no retry
        self.assertEqual(len(run.issue_reads), len(set(run.issue_reads)))
        self.assertEqual(run.pp_attempts, 2)

    def test_no_top_post_without_sprint_health(self):
        for name in ("sprint_issues", "sprint_blocked", "get_jira_issue TEST-12", "feature_gate", "features",
                     "epics", "bugs", "velocity_12"):
            with self.subTest(failed=name):
                run, _ = weekly_run(day(14, 2), 14, lambda w, n=name: w["fail"].add(n))
                text = "\n".join(run.posted)
                self.assertNotIn("assessment incomplete", text)
                self.assertNotIn("sprint data not available", text)
                if name == "sprint_issues":
                    self.assertEqual(run.posted, [])
                    self.assertTrue(run.status.startswith("Weekly health report not posted: Sprint health"))
                    continue
                # Any other failed fetch: posted with sprint health, the failure on the Left out line.
                self.assertIsNone(run.status)
                self.assertTrue(run.posted[1].startswith("*Sprint health: Test Sprint 14*"))
                self.assertFalse(run.posted[0].splitlines()[2].startswith("0 people"))
                self.assertNotEqual(_left_out(run.posted), _left_out(NORMAL_WEEK_2))  # the failure is added


class PagingChainTest(unittest.TestCase):
    """The literal more-match page through the whole chain: the next page is fetched now; it is
    never retried, never incomplete and never left out."""

    def test_more_than_50_sprint_issues_over_two_projects_are_paged_per_project(self):
        cur = sprint_value(13, "active")
        fields = [f"customfield_10020: {cur}", "Parent: TEST-300", "customfield_10028: 1.0"]
        issues = {k: block(k, fields=fields) for k in [f"TESTA-{n}" for n in range(101, 161)]
                  + [f"TESTB-{n}" for n in range(1, 6)]}

        def change(w):
            w["sprint"], w["sprint_blocked"] = issues, []
            w["projects"]["sprint"] = "TESTA, TESTB"
        run, report = weekly_run(day(13, 1), 13, change)
        base = f"(project in (TESTA, TESTB) AND sprint = {_sprint_id(13)})"
        self.assertTrue(query_jira(f"{base} ORDER BY key DESC", issues).startswith(MORE_MATCH))
        self.assertEqual(run.calls["sprint_issues"], [
            f"{base} ORDER BY key DESC",
            f"{base} AND project = TESTA ORDER BY key DESC",  # the split's parts go out together
            f"{base} AND project = TESTB ORDER BY key DESC",
            f"{base} AND project = TESTA AND key < TESTA-111 ORDER BY key DESC"])
        self.assertEqual(run.rounds["fetches"][1:], [["sprint_issues/TESTA", "sprint_issues/TESTB"],
                                                     ["sprint_issues/TESTA"]])  # one slot per part
        # Each logical fetch is adapted in the round its last part ends: the fetches that
        # ended on their first page in round 1, the sprint issues when TESTA ends.
        self.assertEqual({name: n for (wave, name), n in run.adapted_in.items() if wave == "fetches"},
                         {"velocity_10": 1, "velocity_11": 1, "velocity_12": 1, "features": 1, "bugs": 1,
                          "ref_spikes": 1, "sprint_issues": 3})
        self.assertEqual(run.pages["sprint_issues"], 3)  # TESTA 2 pages, TESTB 1; the split page is dropped
        self.assertIsNone(run.status)
        self.assertIn("committed 65 SP in 65 issues", run.posted[1])  # every issue of both projects
        self.assertNotIn("sprint issue", _left_out(run.posted))
        self.assertEqual(budget_problems(report), [])

    def test_more_match_pages_are_never_retried(self):
        many = {f"FEAT-{n}": _feature(f"FEAT-{n}") for n in range(100, 160)}

        def change(w):
            w["features"], w["epics"], w["linked_spikes"], w["feature_blocks"] = many, {}, {}, []
            w["projects"]["features"] = "FEAT"
            w["bugs"] = {f"BUG-{n}": block(f"BUG-{n}", "Bug", fields=["Fix Versions: 9.2"]) for n in range(100, 175)}
            w["projects"]["bugs"] = "BUG"
        run, _ = weekly_run(day(14, 2), 14, change)
        for name in ("features", "bugs"):
            with self.subTest(fetch=name):
                self.assertEqual(len(run.calls[name]), len(set(run.calls[name])))  # each page sent once
        self.assertEqual(run.pages["bugs"], 2)
        self.assertNotIn("fetch incomplete", _left_out(run.posted))
        self.assertIn("*9.2 release health*", run.posted[2])


class ReleaseGateChainTest(unittest.TestCase):
    """The weekly SKILL's gate and section blocks, run on the chain."""

    def test_incomplete_gate_reports_no_release_section_and_says_why(self):
        posted, report = weekly(day(14, 2), 14, lambda w: w["fail"].add("feature_gate"))
        self.assertNotIn("release health", "\n".join(posted))
        self.assertIn("Release sections: release Feature gate incomplete: fetch: page 1: query_jira call failed",
                      _left_out(posted))
        self.assertNotIn("no release in progress", _left_out(posted))  # unknown, not zero
        self.assertTrue(posted[1].startswith("*Sprint health: Test Sprint 14*"))
        self.assertEqual(budget_problems(report), [])

    def test_release_without_edge_features_is_omitted_silently(self):
        def change(w):  # only an Initiative and a Closed Feature target 9.2; 9.2 is in development
            w["gate"] = {"FEAT-50": _feature("FEAT-50", type_="Initiative"),
                         "FEAT-51": _feature("FEAT-51", status="Closed")}
        posted, _ = weekly(day(14, 2), 14, change)
        self.assertNotIn("9.2", posted[0])
        self.assertNotIn("release health", "\n".join(posted))
        # No release was expected: nothing about releases under Left out, not even "no release in progress".
        self.assertNotIn("Release section", _left_out(posted))
        self.assertNotIn("9.2", _left_out(posted))

    def test_product_pages_lookup_failure_leaves_only_that_section_out(self):
        posted, _ = weekly(day(14, 2), 14, lambda w: w.update(schedules={"9.2": {"error": "lookup failed"}}))
        self.assertIn("9.2 release section: release phase unknown: Product Pages lookup failed", _left_out(posted))
        self.assertTrue(posted[1].startswith("*Sprint health: Test Sprint 14*"))  # the rest goes on
        self.assertTrue(posted[2].startswith("*Epic fields*"))

    def test_every_links_read_requests_issuelinks_and_the_report_is_whole(self):
        run, report = weekly_run(day(14, 2), 14)
        self.assertEqual({fields for _, fields in run.issue_reads}, {"issuelinks"})
        # sprint candidate, refinement spike, feature block, and the linked spike TEST-930 (its
        # links attribute it to FEAT-30 in the batched linked-spike query)
        self.assertEqual({key for key, _ in run.issue_reads}, {"TEST-12", "TEST-920", "FEAT-30", "TEST-930"})
        # A text top post and three replies: sprint, the active release, epic fields.
        self.assertEqual(len(run.posted), 4)
        self.assertTrue(all(isinstance(m, str) for m in run.posted))
        self.assertTrue(run.posted[0].startswith(
            "🔴 Edge weekly health: Test Sprint 14 (week 2, mid-sprint); 9.2 development"))
        self.assertTrue(run.posted[2].startswith("*9.2 release health* (development)"))
        self.assertIn("FEAT-30 (blocked by BUG-77)", run.posted[2])  # read from the set issuelinks field
        self.assertIn("refinement overdue", run.posted[2])  # spike links read from the set field
        self.assertTrue(run.posted[3].startswith("*Epic fields*"))
        self.assertEqual(budget_problems(report), [])

    def test_product_pages_listing_follows_every_cursor(self):
        run, _ = weekly_run(day(14, 2), 14)
        self.assertEqual(run.pp_calls, [{"kind": "release", "public_parent_id": 990001},
                                        {"kind": "release", "public_parent_id": 990001, "cursor": "1"}])

    def test_the_script_reads_links_with_the_issuelinks_field(self):
        with open(os.path.join(BIN, "weekly_report_files.py"), encoding="utf-8") as fh:
            self.assertIn('{"issue_key": key, "fields": "issuelinks"}', fh.read())

    def test_gate_query_is_the_script_query(self):
        self.assertEqual(GATE_JQL, wr.GATE_JQL)

    def test_product_pages_listing_failure_leaves_release_sections_out(self):
        posted, _ = weekly(day(14, 2), 14, lambda w: w.update(pp_fails=True))
        self.assertIn("9.2 release section: release phase unknown: Product Pages lookup failed: the call failed "
                      "again after its retry: tool call failed", _left_out(posted))  # names the terminal error
        self.assertTrue(posted[1].startswith("*Sprint health: Test Sprint 14*"))

    def test_lookup_without_dates_is_not_a_lookup_failure(self):
        posted, _ = weekly(day(14, 2), 14, lambda w: w.update(schedules={"9.2": []}))
        self.assertIn("9.2 release section: no Product Pages schedule yet", _left_out(posted))
        self.assertNotIn("lookup failed", _left_out(posted))

    def _future(self, w):
        """9.3 Features only; 9.3 has no schedule of its own: 9.2's "Master Branch Open for 9.3" places it."""
        w["features"] = {"FEAT-40": _feature("FEAT-40", tv="openshift-9.3")}
        w["linked_spikes"], w["feature_blocks"] = {}, []

    def test_future_release_refinement_is_inferred_from_the_previous_release(self):
        posted, _ = weekly(day(17, 1), 17, self._future)
        self.assertIn("9.3 refinement", posted[0])
        self.assertTrue(posted[2].startswith("*9.3 refinement* (refinement sprint 17): 1 feature(s) checked"))

    def test_future_release_is_not_forced_into_refinement_early(self):
        posted, _ = weekly(day(16, 2), 16, self._future)
        self.assertNotIn("9.3", "\n".join(posted[:-1]))
        self.assertIn("Release section: no release in progress", _left_out(posted))

    def test_whole_issue_response_without_links_is_an_empty_link_list(self):
        def change(w):
            w["issue_links"]["FEAT-30"] = issue_text("FEAT-30", None)
        posted, _ = weekly(day(14, 2), 14, change)
        self.assertNotIn("blocks not checked", posted[2])
        self.assertNotIn("FEAT-30 (blocked by", posted[2])
        self.assertNotIn("blocks of FEAT-30", _left_out(posted))

    def test_spike_response_without_issuelinks_state_leaves_the_section_out(self):
        def change(w):  # the field was not requested: no Requested fields area
            w["issue_links"]["TEST-920"] = issue_text("TEST-920", None).replace(
                "Requested fields:\nissuelinks: " + ad.LINKS_EMPTY + "\n\n", "")
        posted, _ = weekly(day(14, 2), 14, change)
        self.assertIn("9.2 release section: spike links incomplete: TEST-920: no issuelinks state", _left_out(posted))
        self.assertNotIn("release health", "\n".join(posted))

    def test_failed_blocked_by_fetches_name_the_fetch_and_error(self):
        """The candidate query and the issue-link reads behind the blocked signals: a fetch that
        failed after its retry is named with its error, and nothing is claimed unblocked."""
        error = "fetch: page 1: query_jira call failed: tool call failed"
        cases = {"sprint_blocked": f"blocked-by links: blocked items fetch incomplete: {error}",
                 "get_jira_issue TEST-12": "blocked-by links: TEST-12: get_jira_issue failed",
                 "blocks": f"9.2 feature and epic blocks: block candidates fetch incomplete: {error}"}
        for failed, expected in cases.items():
            with self.subTest(failed=failed):
                run, _ = weekly_run(day(14, 2), 14, lambda w, f=failed: w["fail"].add(f))
                self.assertIsNone(run.status)  # posted: only that signal is left out
                self.assertIn(expected, _left_out(run.posted))
                self.assertNotIn("issue link details incomplete (get_jira_issue)", _left_out(run.posted))
                if failed == "blocks":
                    self.assertEqual(len(run.calls["blocks"]), 2)  # retried once
                    self.assertIn("(blocks not checked)", run.posted[2])
                    self.assertNotIn("blocked by BUG-77", run.posted[2])
                else:
                    self.assertIn("blocked-by links not checked", run.posted[1])  # never claimed unblocked
                    self.assertNotIn("TEST-12 (blocked by", run.posted[1])

    def test_cut_off_issue_response_without_links_is_not_read_as_unblocked(self):
        def change(w):
            w["issue_links"]["FEAT-30"] = issue_text("FEAT-30", None).split("Description:")[0]
        posted, _ = weekly(day(14, 2), 14, change)
        self.assertIn("(blocks not checked for 1)", posted[2])
        self.assertIn('9.2 blocks of FEAT-30: FEAT-30: no issuelinks state in Requested fields '
                      '(request fields="issuelinks")', _left_out(posted))


# ---------------------------------------------------------------- the shared fetch plan: equivalence and reads


def busy(w, active=14):
    """A realistic synthetic development week with multi-page fetches everywhere: 70
    sprint issues over two projects, three closed velocity sprints of 60 issues each,
    62 features (two Closed; more than one rank-ordered page), 101 release epics (one
    Closed, under a Closed feature), over 300 stories over two projects, 20 bugs, six
    refinement-sprint spikes, spikes linked to features, and blocked candidates."""
    w["projects"].update(sprint="TESTA, TESTB", stories="TESTA, TESTB", features="FEAT", bugs="BUG")
    people = ("Alex Example", "Quinn Quality", "Riley Reserved")
    cur = sprint_value(active, "active")

    def story(key, n, parent, sprint_field=None, closed=False, extra=()):
        fields = [f"Parent: {parent}", f"customfield_10028: {1 + n % 3}.0", *extra]
        if sprint_field:
            fields.append(f"customfield_10020: {sprint_field}")
        return block(key, status="Closed" if closed else ("To Do" if n % 4 else "In Progress"),
                     assignee=people[n % 3], fields=fields)
    w["sprint"] = {**{f"TESTA-{1000 + n}": story(f"TESTA-{1000 + n}", n, f"TEST-{2000 + n % 40}", cur, n % 3 == 0)
                      for n in range(1, 61)},
                   **{f"TESTB-{n}": story(f"TESTB-{n}", n, f"TEST-{2000 + n}", cur, n % 3 == 0) for n in range(1, 11)}}
    w["sprint_blocked"] = ["TESTA-1001", "TESTA-1002", "TESTB-1"]
    w["issue_links"].update({"TESTA-1001": issue_text("TESTA-1001", [("is blocked by", "TESTB-50", "To Do")]),
                             "TESTA-1002": issue_text("TESTA-1002", [("is blocked by", "TESTA-1010", "Closed")]),
                             "TESTB-1": issue_text("TESTB-1", None)})
    w["velocity"] = {}
    for s in range(10, active):
        done = f"resolutiondate: {_start(s) + dt.timedelta(days=12)}T10:00:00.000+0000"
        w["velocity"][s] = {
            **{f"TESTA-{s * 1000 + n}": story(f"TESTA-{s * 1000 + n}", n, "TEST-2000", sprint_value(s, "closed"),
                                              True, [done]) for n in range(1, 56)},
            **{f"TESTB-{s * 1000 + n}": story(f"TESTB-{s * 1000 + n}", n, "TEST-2001", sprint_value(s, "closed"),
                                              True, [done]) for n in range(1, 6)}}
    w["features"] = {f"FEAT-{100 + n}": _feature(f"FEAT-{100 + n}", sme=None if n % 10 == 0 else people[n % 2],
                                                 size=("S", "M", "L")[n % 3],
                                                 status="New" if n % 5 == 0 else "In Progress")
                     for n in range(60)}
    w["features"].update({f"FEAT-{k}": _feature(f"FEAT-{k}", status="Closed") for k in (160, 161)})
    w["epics"] = {f"TEST-{2000 + n}": block(f"TEST-{2000 + n}", "Epic", "Closed" if n % 7 == 0 else "In Progress",
                                            people[n % 3], fields=[f"customfield_10018: FEAT-{100 + n // 2}",
                                                                   "customfield_10855: openshift-9.2"])
                  for n in range(100)}
    w["epics"]["TEST-2100"] = block("TEST-2100", "Epic", "Closed", fields=["customfield_10018: FEAT-160"])
    w["stories"] = {}
    for n in range(101):
        for m in range(3):
            key = f"{'TESTA' if m < 2 else 'TESTB'}-{5000 + n * 3 + m}"
            w["stories"][key] = story(key, n + m, f"TEST-{2000 + n}", closed=(n + m) % 4 == 0)
    w["bugs"] = {f"BUG-{n}": block(f"BUG-{n}", "Bug", fields=["Fix Versions: 9.2"]) for n in range(1, 21)}
    w["ref_spikes"] = {f"TEST-{900 + n}": block(f"TEST-{900 + n}", "Spike", "Closed" if n % 2 else "In Progress",
                                                fields=[f"customfield_10020: {sprint_value(12, 'closed')}"])
                       for n in range(1, 7)}
    for n in range(1, 7):
        rows = [("blocks", f"FEAT-{100 + n}", "In Progress")] if n % 2 else None
        w["issue_links"][f"TEST-{900 + n}"] = issue_text(f"TEST-{900 + n}", rows)
    w["linked_spikes"] = {f"FEAT-{100 + n}": {f"TEST-{900 + n}": block(f"TEST-{900 + n}", "Spike")}
                          for n in range(1, 7, 2)}  # the spikes whose links say they block the feature
    w["feature_blocks"] = [f"FEAT-{100 + n}" for n in range(5)]
    for n in range(5):
        w["issue_links"][f"FEAT-{100 + n}"] = issue_text(f"FEAT-{100 + n}", [("is blocked by", f"BUG-{n + 1}", "New")])


def two_releases(w):
    """9.2 in its pencils-down sprint and 9.3 in refinement at once (sprint 15): two
    release filters, and one feature that targets both releases."""
    w["schedules"] = dict(SCHEDULES, **{"9.3": [
        {"name": "9.3 Development Branch opens", "date": _start(15).isoformat()},
        {"name": "Pencils Down / Stabilization", "date": _start(19).isoformat()}]})
    w["features"].update({"FEAT-40": _feature("FEAT-40", tv="openshift-9.3"),
                          "FEAT-41": _feature("FEAT-41", sme=None, tv="openshift-9.3"),
                          "FEAT-25": _feature("FEAT-25", tv="openshift-9.2, openshift-9.3")})
    w["epics"]["TEST-400"] = block("TEST-400", "Epic", fields=["customfield_10018: FEAT-40"])
    w["epics"]["TEST-250"] = block("TEST-250", "Epic", fields=["customfield_10018: FEAT-25"])
    w["stories"]["TEST-401"] = block("TEST-401", fields=["Parent: TEST-400", "customfield_10028: 2.0"])
    w["stories"]["TEST-251"] = block("TEST-251", fields=["Parent: TEST-250", "customfield_10028: 1.0"])


def shared_rows(w):
    """Rows the shared plan reuses: a field epic whose parent is a gate feature (its parent
    row comes from the gate, not a lookup), and an active release epic with a parent (in a
    release's epics and in the epic fields at once)."""
    w["field_epics"]["TEST-121"] = block("TEST-121", "Epic", "To Do", fields=["Parent: FEAT-20"])
    w["epics"]["TEST-300"] = w["epics"]["TEST-300"] + "\n    Parent: FEAT-30"


def link_in_parent(w):
    """:func:`two_releases` as Jira Cloud may print it: each epic's feature is its native
    parent, with no Parent Link line, and ``"Parent Link"`` matches the parent."""
    two_releases(w)
    w["parent_link_is_parent"] = True
    for key, text in w["epics"].items():
        has_parent = "\n    Parent: " in text
        w["epics"][key] = re.sub(r"\n    customfield_10018: (\S+)", "" if has_parent else r"\n    Parent: \1", text)


def reopened_after_gate(w):
    """:func:`busy` with a feature Closed when the gate is read and reopened before the
    release features are read (the rank page has more): it must still be checked."""
    busy(w)

    def change(w):
        w["features"]["FEAT-161"] = _feature("FEAT-161", sme=None, status="In Progress")
    w["after_gate"] = change


def duplicates_only(w):
    """Refinement with every feature of the release resolved Duplicate but not Closed: the
    gate selects the release, and no feature is left to check."""
    for key, text in list(w["features"].items()):
        w["features"][key] = text.replace("| Reporter: Sam Sample", "| Reporter: Sam Sample | Resolution: Duplicate", 1)


def final_week_mix(w):
    """Open sprint issues in every stage: review or testing (Review, ON_QA), in progress
    or to do (In Progress, To Do, one unpointed), and a status no workflow of the three
    projects has ("Testing")."""
    cur = sprint_value(14, "active")
    for key, status, sp in (("TEST-14", "Review", "2.0"), ("TEST-15", "ON_QA", "1.0"), ("TEST-16", "To Do", "2.0"),
                            ("TEST-17", "Testing", "1.0")):
        w["sprint"][key] = block(key, status=status, fields=[f"customfield_10020: {cur}", f"customfield_10028: {sp}"])


def no_epics_for_one_release(w):
    """:func:`two_releases` where 9.2 (pencils-down) has no epics and the shared stories
    fetch fails: 9.2 needs no story, so only 9.3 loses its empty-epic check."""
    two_releases(w)
    w["epics"] = {k: v for k, v in w["epics"].items() if "FEAT-40" in v}
    w["fail"].add("stories")


def parent_changed_after_gate(w):
    """:func:`shared_rows` where FEAT-20 (a field epic's parent) loses its QA Contact after
    the gate is read: the later features fetch has the current copy."""
    shared_rows(w)

    def change(w):
        w["features"]["FEAT-20"] = w["features"]["FEAT-20"].replace("    customfield_10470: Quinn Quality\n", "")
    w["after_gate"] = change


def read_counts(run):
    """``{step: {kind: count}}`` of one run and ``{kind: total}``."""
    steps = {step: dict(kinds) for step, kinds in sorted(run.steps.items()) if any(kinds.values())}
    totals = collections.Counter()
    for kinds in steps.values():
        totals.update(kinds)
    return steps, dict(totals)


EQUIVALENT_RUNS = {
    "week 1 development": (day(13, 1), 13, None),
    "week 2 development": (day(14, 2), 14, None),
    "week 3 development": (day(14, 3), 14, None),
    "refinement week 1": (day(12, 1), 12, None),
    "pencils down": (day(15, 2), 15, None),
    "two releases": (day(15, 2), 15, two_releases),
    "two releases, Parent Link in the parent": (day(15, 2), 15, link_in_parent),
    "busy week 2": (day(14, 2), 14, busy),
    "shared rows": (day(14, 2), 14, shared_rows),
    "a feature reopened after the gate read": (day(14, 2), 14, reopened_after_gate),
    "refinement with only Duplicate features": (day(12, 1), 12, duplicates_only),
    "development with only Duplicate features": (day(14, 2), 14, duplicates_only),
    "a release with no epics while the stories fetch fails": (day(15, 2), 15, no_epics_for_one_release),
    "a parent changed after the gate read": (day(14, 2), 14, parent_changed_after_gate),
}


@functools.cache
def cached_run(title, legacy=False):
    """One of :data:`EQUIVALENT_RUNS`, run once per plan (the tests only read it)."""
    today, active, change = EQUIVALENT_RUNS[title]
    return weekly_run(today, active, change, legacy=legacy)[0]


class SharedPlanEquivalenceTest(unittest.TestCase):
    """The shared fetch plan posts exactly the report the per-section plan posted."""

    def test_the_same_report_as_the_per_section_plan(self):
        for title in EQUIVALENT_RUNS:
            with self.subTest(title):
                new, old = cached_run(title), cached_run(title, legacy=True)
                self.assertEqual((new.posted, new.status), (old.posted, old.status))
                self.assertTrue(new.posted)

    def test_shared_rows_are_reused_not_read_again(self):
        new, old = cached_run("shared rows"), cached_run("shared rows", legacy=True)
        self.assertEqual(old.calls["parents"], [("(key in (FEAT-1, FEAT-2, FEAT-3, FEAT-4, FEAT-9, FEAT-20, FEAT-30)) "
                                                 "ORDER BY key DESC")])
        self.assertEqual(new.calls["parents"], ["(key in (FEAT-1, FEAT-2, FEAT-3, FEAT-4, FEAT-9)) ORDER BY key DESC"])
        # TEST-121 (its parent's row came from the gate) and TEST-300 (a release epic, in the
        # epic fields too) are both flagged; later keys of a project are shortened to the number.
        self.assertIn("no Target Version for TEST-104, 107, 121, 300", new.posted[3])
        # The decision 0024 epics stay their own query: another predicate, never partitioned.
        self.assertEqual((len(new.calls["epics"]), len(new.calls["field_epics"])), (1, 1))
        self.assertEqual((len(old.calls["epics"]), len(old.calls["field_epics"])), (1, 1))
        self.assertEqual(new.posted, old.posted)

    def test_each_release_and_check_keeps_its_own_scope(self):
        no_features = cached_run("development with only Duplicate features")
        self.assertIn("✓ 0 of 0 feature(s) on track\n", no_features.posted[2] + "\n")  # no block check left out
        self.assertNotIn("blocks", _left_out(no_features.posted))
        no_epics = cached_run("a release with no epics while the stories fetch fails")
        self.assertIn("*9.2 release health*", no_epics.posted[2])  # needed no story: still posted
        self.assertNotIn("9.2 release section", _left_out(no_epics.posted))
        self.assertIn("9.3 empty-epic check: epic issues not fetched (fetch: page 1: query_jira call failed",
                      _left_out(no_epics.posted))
        changed = cached_run("a parent changed after the gate read")
        self.assertIn("no QA Contact, feature has none for TEST-104, 121", changed.posted[3])  # the current FEAT-20

    def test_the_features_fixture_serves_what_the_documented_query_matches(self):
        w, clause = world(14), "(resolution is EMPTY OR resolution not in (Duplicate, Obsolete))"
        items = {"FEAT-90": _feature("FEAT-90").replace("Sam Sample", "Sam Sample | Resolution: Duplicate", 1),
                 "FEAT-91": _feature("FEAT-91"), "FEAT-92": _feature("FEAT-92", status="Closed"),
                 "FEAT-93": _feature("FEAT-93", status="Closed").replace("Resolution: Done", "Resolution: Obsolete")}
        release, refinement = features_jql(w, "9.2"), features_jql(w, "9.2", open_only=True)
        self.assertTrue(release.endswith(f'"Target Version" = "openshift-9.2" AND {clause}'))  # the SKILL's query
        self.assertTrue(refinement.endswith(f"status != Closed AND {clause}"))
        self.assertEqual(sorted(targeting(items, release)), ["FEAT-91", "FEAT-92"])  # the open Duplicate dropped
        self.assertEqual(sorted(targeting(items, refinement)), ["FEAT-91"])
        # Control: a query without the clause gets FEAT-90 back, so the fixture never hides it;
        # "not in" alone, as in Jira, drops the unresolved features too.
        self.assertEqual(sorted(targeting(items, release.replace(f" AND {clause}", ""))), sorted(items))
        self.assertEqual(sorted(targeting(items, release.replace(clause, "resolution not in (Duplicate, Obsolete)"))),
                         ["FEAT-92"])
        for legacy in (False, True):  # both plans send it, and only Duplicate features: none to check
            run = cached_run("development with only Duplicate features", legacy=legacy)
            self.assertEqual(run.calls["features"][0], f"({release}) ORDER BY Rank ASC")
            self.assertIn("✓ 0 of 0 feature(s) on track\n", run.posted[2] + "\n")

    def test_an_epic_block_candidate_reaches_the_release_reply(self):
        def epic_block(w):  # TEST-200 (FEAT-20's epic) is blocked; FEAT-30 (a feature) stays blocked too
            w["feature_blocks"] = w["feature_blocks"] + ["TEST-200"]
            w["issue_links"]["TEST-200"] = issue_text("TEST-200", [("is blocked by", "BUG-88", "New")])
        new, _ = weekly(day(14, 2), 14, epic_block)
        self.assertIn("epic TEST-200 blocked by BUG-88", new[2])
        self.assertIn("FEAT-30 (blocked by BUG-77)", new[2])
        self.assertEqual(new, weekly_run(day(14, 2), 14, epic_block, legacy=True)[0].posted)

    def test_a_shortened_value_read_whole_on_its_retry_is_used(self):
        def shortened(w, **spec):
            w["sprint"]["TEST-13"] = block("TEST-13", assignee="Quinn Quality", fields=[
                "Labels: Parked, +1 more", f"customfield_10020: {sprint_value(14, 'active')}"])
            w["whole_values"] = {"TEST-13 labels": {"short": "Labels: Parked, +1 more",
                                                   "whole": "Labels: Parked, Blocked", **spec}}
        run, _ = weekly_run(day(14, 2), 14, lambda w: shortened(w, fail_first=True))
        self.assertIsNone(run.status)  # posted: the second read was whole
        self.assertIn("TEST-13 (Blocked label, Parked)", run.posted[1])  # the whole labels, no stale omission
        self.assertNotIn("value shortened", "\n".join(run.posted))
        self.assertEqual(run.steps["whole values"]["detail"], 2)  # read, sent again once
        failed, _ = weekly_run(day(14, 2), 14, lambda w: shortened(w, fail=True))
        self.assertEqual(failed.status, "Weekly health report not posted: Sprint health incomplete: sprint issue "
                                        "fetch incomplete: TEST-13: labels value shortened (not read whole)")

    def test_the_reason_paging_stopped_is_on_the_left_out_line(self):
        def change(w):  # 60 bugs; the tool returns the first page out of key order every time
            w["bugs"] = {f"BUG-{n}": block(f"BUG-{n}", "Bug", fields=["Fix Versions: 9.2"]) for n in range(100, 160)}
            w["projects"]["bugs"] = "BUG"
            w["unordered"].add("bugs")
        posted, _ = weekly(day(14, 2), 14, change)
        self.assertIn("9.2 release section: bugs fetch incomplete: fetch: page 1: keys on the page are not in "
                      "descending order (ORDER BY key DESC not applied)", _left_out(posted))

    def test_the_stand_in_matches_parent_keys_exactly(self):
        w = {"stories": {"S-1": block("S-1", fields=["Parent: TEST-1"]),
                         "S-2": block("S-2", fields=["Parent: TEST-10"])}}
        self.assertEqual(list(children_of(w, ["TEST-1"])), ["S-1"])

    def test_two_release_filters_stay_apart(self):
        run, _ = weekly_run(day(15, 2), 15, two_releases)
        self.assertIn("9.2 pencils-down sprint; 9.3 refinement", run.posted[0])
        self.assertEqual(len(run.calls["epics"]), 1)  # one release epics fetch for both releases
        self.assertEqual(len(cached_run("two releases", legacy=True).calls["epics"]), 2)  # one per release before
        self.assertEqual(len(run.calls["stories"]), 1)  # one stories fetch for both releases
        self.assertNotIn("features_9.3", run.calls)  # the refinement features come from the gate
        # One linked-spike query per section's features (a feature of both releases once, with
        # the first section); each query sent once.
        linked = {name: calls for name, calls in run.calls.items() if name.startswith("linked")}
        self.assertEqual(sorted(linked), ["linked", "linked_2"])
        keys = {name: re.findall(r'linkedIssues\("([A-Z]+-\d+)"', calls[0]) for name, calls in linked.items()}
        self.assertEqual({name: len(calls) for name, calls in linked.items()}, {"linked": 1, "linked_2": 1})
        self.assertEqual(keys, {"linked": ["FEAT-30", "FEAT-20", "FEAT-10", "FEAT-25"],  # 9.2's features
                                "linked_2": ["FEAT-41", "FEAT-40"]})  # 9.3's own (FEAT-25 read once, with 9.2)

    def test_a_cut_off_rank_page_is_sent_again_then_fetched_by_key(self):
        run = weekly_run(day(14, 2), 14, lambda w: (busy(w), w["flaky"].update(features="cut")))[0]
        rank, retry, first, second = run.calls["features"]
        self.assertEqual((rank, retry), (rank, rank))  # the cut-off rank page sent again, unchanged
        self.assertTrue(rank.endswith("ORDER BY Rank ASC") and first.endswith(") ORDER BY key DESC"))
        self.assertIn("AND key < FEAT-", second)  # then the whole query by key, page by page
        self.assertEqual(run.posted, cached_run("busy week 2").posted)

    def test_failures_and_retries_match_too(self):
        for name in ("features", "epics", "field_epics", "bugs", "ref_spikes", "sprint_blocked", "blocks",
                     "get_jira_issue FEAT-30"):
            with self.subTest(failed=name):
                new = weekly_run(day(14, 2), 14, lambda w, n=name: w["fail"].add(n))[0]
                old = weekly_run(day(14, 2), 14, lambda w, n=name: w["fail"].add(n), legacy=True)[0]
                self.assertEqual((new.posted, new.status), (old.posted, old.status))
        flaky = {"feature_gate": "cut", "epics": "error", "field_epics": "cut", "stories": "cut", "features": "error"}
        run = weekly_run(day(14, 2), 14, lambda w: w["flaky"].update(flaky))[0]
        self.assertEqual(run.posted, list(NORMAL_WEEK_2))
        self.assertEqual({n: len(run.calls[n]) for n in flaky}, dict.fromkeys(flaky, 2))


class IncompleteSprintEndTest(unittest.TestCase):
    """Sprint health incomplete, the report's only "post nothing" exit: a scheduled run delivers
    one response element holding only the status line, then send_response; a DM run ends silently.
    Never a top post, a reply, figures or a store write."""

    NOT_POSTED = "Weekly health report not posted: Sprint health incomplete: "
    SCHEDULED = ({"tool": "send_response", "mode": "report", "result_ids": []},)

    def test_the_skill_names_the_steps_in_order(self):
        self.assertEqual(skill_end_steps(), {
            "scheduled run": ["register one response element, its whole content: failure",
                              'send_response(mode="report", result_ids=[])'],
            "interactive run (DM)": ["no_action_required(reason=failure)"]})

    def test_every_incomplete_sprint_variant_delivers_only_the_status_line(self):
        variants = {
            "sprint issue fetch failed": (day(14, 2), lambda w: w["fail"].add("sprint_issues"),
                                          ("sprint issue fetch incomplete: fetch: page 1: query_jira call failed: "
                                           "tool call failed")),
            "sprint value shortened": (day(14, 2), lambda w: w["sprint"].update(
                {"TEST-13": block("TEST-13", fields=["Labels: Parked, +1 more"])}),
                "sprint issue fetch incomplete: TEST-13: labels value shortened (not read whole)"),
            "board listing failed": (day(14, 2), lambda w: w.update(board_error="list_jira_board_sprints failed"),
                                     "board sprint fetch failed: list_jira_board_sprints failed"),
            "board error over several lines": (day(14, 2), lambda w: w.update(
                board_error="list_jira_board_sprints failed:\n  timeout"),
                "board sprint fetch failed: list_jira_board_sprints failed: timeout"),
            "sprint not on the board yet": (day(14, 2), lambda w: w.update(board_last=13),
                                            "current sprint not on the board yet"),
            "final week, sprint issue fetch failed": (day(14, 3), lambda w: w["fail"].add("sprint_issues"),
                                                      "sprint issue fetch incomplete: fetch: page 1: "),
        }
        for title, (today, change, why) in variants.items():
            with self.subTest(title):
                store = {}
                run, report = weekly_run(today, 14, lambda w, c=change, st=store: (c(w), w.update(velocity_store=st)))
                self.assertEqual((run.posted, report), ([], None))  # no top post, no reply, no figures
                self.assertTrue(run.status.startswith(self.NOT_POSTED + why))
                self.assertNotIn("\n", run.status)  # one line
                self.assertEqual(run.final_steps, [{"element": run.status}, *self.SCHEDULED])  # exactly, in order
                self.assertEqual(store, {})  # no velocity entry written

    def test_an_interactive_run_ends_silently(self):
        run, _ = weekly_run(day(14, 2), 14, lambda w: (w.update(interactive=True), w["fail"].add("sprint_issues")))
        self.assertEqual(run.posted, [])
        self.assertEqual(run.final_steps, [{"tool": "no_action_required", "reason": run.status}])

    def test_a_skipped_sprint_result_ends_the_same_way(self):
        skipped = cr.sprint_health({"issues": []}, {"name": "Test Sprint 14", "week": None}, day(14, 2), None)
        failure = cr.sprint_health_failure(skipped, [])
        self.assertEqual(failure, self.NOT_POSTED + "sprint week unknown")
        self.assertEqual(end_steps(failure), [{"element": failure}, *self.SCHEDULED])
        self.assertEqual(end_steps(failure, interactive=True), [{"tool": "no_action_required", "reason": failure}])

    def test_a_skipped_section_is_delivered_not_this_exit(self):
        for title, change in (("release Feature gate failed", lambda w: w["fail"].add("feature_gate")),
                              ("release features failed", lambda w: w["fail"].add("features")),
                              ("Product Pages failed", lambda w: w.update(pp_fails=True)),
                              ("epic fields failed", lambda w: w["fail"].add("field_epics"))):
            with self.subTest(title):
                run, _ = weekly_run(day(14, 2), 14, change)
                self.assertTrue(run.posted[0].startswith(("🔴 Edge weekly health:", "🟡 Edge weekly health:")))
                self.assertIn("Left out:", run.posted[-1])
                self.assertEqual((run.status, run.final_steps), (None, None))

    def test_ordinary_delivery_has_no_end_steps(self):
        for interactive in (False, True):
            with self.subTest(interactive=interactive):
                run, _ = weekly_run(day(14, 2), 14, lambda w, i=interactive: w.update(interactive=i))
                self.assertEqual(run.posted, list(NORMAL_WEEK_2))
                self.assertEqual((run.status, run.final_steps), (None, None))


class SharedFetchFailureTest(unittest.TestCase):
    """A shared fetch that fails after its retry is left out, with its error, by every part it serves."""

    def test_a_projected_current_sprint_skips_only_the_sprint_queries(self):
        run, _ = weekly_run(day(14, 2), 14, lambda w: w.update(board_last=13))  # sprint 14 not on the board yet
        self.assertEqual(run.posted, [])
        self.assertEqual(run.status, "Weekly health report not posted: Sprint health incomplete: "
                                     "current sprint not on the board yet")
        self.assertFalse({"sprint_issues", "sprint_blocked"} & set(run.calls))  # they need its id
        self.assertTrue({"velocity_11", "velocity_12", "velocity_13", "features", "bugs", "epics", "stories",
                         "linked", "blocks", "field_epics", "parents"} <= set(run.calls))  # everything else ran

    def test_an_epic_moved_between_two_epics_queries_is_never_placed_silently(self):
        def read_by_epics_2(text):  # TEST-2002 as the second epics query read it
            return lambda w: (busy(w), w.update(epic_reads={"epics_2": {"TEST-2002": text(w["epics"]["TEST-2002"])}}))
        moved = weekly_run(day(14, 2), 14, read_by_epics_2(
            lambda epic: epic.replace("customfield_10018: FEAT-101", "customfield_10018: FEAT-150")))[0]
        base = cached_run("busy week 2")
        self.assertIsNone(moved.status)  # posted, without the section the epics serve
        self.assertEqual((len(moved.posted), len(base.posted)), (3, 4))
        self.assertNotIn("9.2 release health", "\n".join(moved.posted))
        self.assertIn("9.2 release section: epics fetch incomplete: copies of TEST-2002 in this fetch differ in "
                      "customfield_10018 (it changed between reads)", _left_out(moved.posted))
        self.assertNotIn("FEAT-150", "\n".join(moved.posted))  # the field named, never its values
        self.assertEqual(read_counts(moved)[1], read_counts(base)[1])  # no extra read
        same = weekly_run(day(14, 2), 14, read_by_epics_2(lambda epic: epic))[0]  # identical copies
        self.assertEqual(same.posted, base.posted)

    def test_a_failed_linked_spike_read_is_named_on_the_left_out_line(self):
        run, _ = weekly_run(day(14, 2), 14, lambda w: w["fail"].add("get_jira_issue TEST-930"))
        self.assertIsNone(run.status)  # sprint health is complete: posted, without the release section
        self.assertIn("9.2 release section: features fetch incomplete: FEAT-30, FEAT-20, FEAT-10: linked-spike "
                      "fetch incomplete (TEST-930: get_jira_issue failed)", _left_out(run.posted))
        self.assertEqual([key for key, _ in run.issue_reads].count("TEST-930"), 2)  # read, then sent again once

    def test_no_current_sprint_id_skips_only_the_queries_that_need_it(self):
        run, _ = weekly_run(day(14, 2), 14, lambda w: w.update(board_error="list_jira_board_sprints failed"))
        self.assertEqual(run.posted, [])  # sprint health cannot be computed: nothing is posted
        self.assertEqual(run.status, "Weekly health report not posted: Sprint health incomplete: "
                                     "board sprint fetch failed: list_jira_board_sprints failed")
        self.assertFalse({"sprint_issues", "sprint_blocked"} & set(run.calls))  # no sprint id to query
        self.assertFalse([name for name in run.calls if name.startswith("velocity_")])
        self.assertTrue({"feature_gate", "field_epics"} <= set(run.calls))  # every other fetch still ran
        self.assertEqual(run.steps["board sprints"]["list"], 2)  # the listing was sent again once

    def test_failed_epics_fetch(self):
        run, _ = weekly_run(day(14, 2), 14, lambda w: w["fail"].add("epics"))
        error = "fetch: page 1: query_jira call failed: tool call failed"
        self.assertIsNone(run.status)  # sprint health is complete: posted
        self.assertIn(f"9.2 release section: epics fetch incomplete: {error}", _left_out(run.posted))
        self.assertNotIn("open-epic fetch incomplete", _left_out(run.posted))  # the epic fields: their own fetch
        field, _ = weekly_run(day(14, 2), 14, lambda w: w["fail"].add("field_epics"))
        self.assertIn(f"Epic Target Version and QA Contact checks: open-epic fetch incomplete ({error})",
                      _left_out(field.posted))
        self.assertNotIn("9.2 release section", _left_out(field.posted))
        self.assertTrue(run.posted[1].startswith("*Sprint health: Test Sprint 14*"))
        self.assertEqual(len(run.calls["epics"]), 2)  # retried once

    def test_failed_stories_fetch(self):
        run, _ = weekly_run(day(15, 2), 15, lambda w: (two_releases(w), w["fail"].add("stories")))
        self.assertIn("9.2 release section: stories fetch incomplete: fetch: page 1: query_jira call failed",
                      _left_out(run.posted))
        # The refinement section only counts epic issues: its empty-epic check alone is left
        # out, naming the failed fetch and its error.
        self.assertIn("9.3 empty-epic check: epic issues not fetched (fetch: page 1: query_jira call failed: "
                      "tool call failed)", _left_out(run.posted))
        self.assertIn("*9.3 refinement*", run.posted[2])
        old = weekly_run(day(15, 2), 15, lambda w: (two_releases(w), w["fail"].add("stories")), legacy=True)[0]
        self.assertEqual(run.posted, old.posted)

    def test_failed_gate_leaves_every_release_out(self):
        run, _ = weekly_run(day(14, 2), 14, lambda w: w["fail"].add("feature_gate"))
        self.assertIn("Release sections: release Feature gate incomplete", _left_out(run.posted))
        self.assertNotIn("features", run.calls)  # no section, no features fetch


class LinkScopeTest(unittest.TestCase):
    def test_links_are_read_only_for_candidates(self):
        run = cached_run("busy week 2")
        blocked = {"TESTA-1001", "TESTA-1002", "TESTB-1"}
        spikes = {f"TEST-{900 + n}" for n in range(1, 7)}
        blocks = {f"FEAT-{100 + n}" for n in range(5)}
        self.assertEqual({k for k, _ in run.issue_reads}, blocked | spikes | blocks)
        self.assertEqual(len(run.issue_reads), len(blocked | spikes | blocks))  # each read once


class ReadCountTest(unittest.TestCase):
    """Reads by step, the shared plan against the per-section plan (realistic busy week)."""

    def test_busy_week_counts(self):
        new_steps, new = read_counts(cached_run("busy week 2"))
        old_steps, old = read_counts(cached_run("busy week 2", legacy=True))
        self.assertLess(new["sandbox"], 30)
        self.assertLess(new["search"], old["search"])
        self.assertLess(new["list"], old["list"])
        self.assertLessEqual(new["detail"], old["detail"])
        self.assertLess(new_steps["stories"]["search"], old_steps["stories"]["search"])
        self.assertLessEqual(new_steps["epics"]["search"], old_steps["epics"]["search"])  # epic fields merged
        self.assertLess(new_steps["spikes"]["search"], old_steps["spikes"]["search"])  # one query per 20 features
        self.assertEqual(new_steps["board sprints"]["list"], 1)

    def test_busy_week_split_parts_page_in_the_same_rounds(self):
        run = cached_run("busy week 2")
        # The stories fetch splits by project; both parts go out in every round until TESTB
        # ends (5 TESTA pages, 3 TESTB), and the fetch is adapted when TESTA ends.
        self.assertEqual(run.rounds["stories, blocks, parents"][1:],
                         [["stories/TESTA", "stories/TESTB"]] * 3 + [["stories/TESTA"]] * 2)
        self.assertEqual((run.pages["stories"], len(run.calls["stories"])), (8, 9))  # the split page is dropped
        self.assertEqual(run.adapted_in[("stories, blocks, parents", "stories")], 6)
        self.assertEqual(run.adapted_in[("stories, blocks, parents", "blocks")], 1)  # ended in round 1
        self.assertEqual(read_counts(run)[1]["sandbox"], 15)  # one call per paging round, parts in parallel

    def test_velocity_reads_cold_newly_closed_and_warm(self):
        store = {}
        week3 = weekly_run(day(13, 3), 13, lambda w: (busy(w, 13), w.update(velocity_store=store)))[0]
        self.assertEqual(read_counts(week3)[0]["velocity"]["search"], 12)  # cold: 3 sprints x 4 pages
        cold = cached_run("busy week 2")
        newly = weekly_run(day(14, 2), 14, lambda w: (busy(w), w.update(velocity_store=dict(store))))[0]
        self.assertEqual(read_counts(newly)[0]["velocity"]["search"], 4)  # only the sprint closed since
        warm_store = dict(newly.w["velocity_store"])
        warm = weekly_run(day(14, 2), 14, lambda w: (busy(w), w.update(velocity_store=warm_store)))[0]
        self.assertNotIn("velocity", read_counts(warm)[0])  # nothing fetched
        self.assertEqual(cold.posted, newly.posted)
        self.assertEqual(cold.posted, warm.posted)


class VelocityStoreChainTest(unittest.TestCase):
    """The optional velocity store: the same report on every path; only missing sprints are read."""

    def run_with(self, store, today=None, active=14):
        return weekly_run(today or day(14, 2), active, lambda w: w.update(velocity_store=store))[0]

    def test_an_incomplete_sprint_fetch_is_neither_combined_nor_stored(self):
        store = {}
        run = weekly_run(day(14, 2), 14, lambda w: (w.update(velocity_store=store), w["fail"].add("velocity_12")))[0]
        self.assertIn("9.2 capacity and cut line: velocity sprint 12 fetch incomplete", _left_out(run.posted))
        self.assertEqual((run.store_writes, store), ({}, {}))  # the complete sprints are not stored either

    def test_no_store_configured(self):
        run = self.run_with(None)
        self.assertNotIn("velocity store", run.steps)  # no memory call at all
        self.assertEqual(run.posted, list(NORMAL_WEEK_2))

    def test_cold_then_newly_closed_then_warm(self):
        store = {}
        last_week = self.run_with(store, day(13, 3), 13)
        self.assertEqual(len(store), 3)  # sprints 10-12 created, aggregates only
        for entry in store.values():
            self.assertEqual(set(entry), {"schema", "board", "sprint_id", "sprint_end", "state", "roster", "complete",
                                          *vc.FIGURES})
        self.assertTrue(last_week.posted)
        newly = self.run_with(store)
        self.assertEqual([n for n in newly.calls if n.startswith("velocity_")], ["velocity_13"])
        self.assertEqual(len(store), 4)
        warm = self.run_with(store)
        self.assertFalse([n for n in warm.calls if n.startswith("velocity_")])
        self.assertEqual(newly.posted, list(NORMAL_WEEK_2))
        self.assertEqual(warm.posted, list(NORMAL_WEEK_2))

    def test_invalid_or_unreadable_store(self):
        store = {}
        self.run_with(store)
        digest = vc.roster_digest(ad.parse_team_roster(ROSTER_MD))
        key = vc.entry_key(BOARD, _sprint_id(12), digest)
        bad = dict(store, **{key: dict(store[key], sprint_end="2000-01-01")})
        run = self.run_with(bad)
        self.assertEqual([n for n in run.calls if n.startswith("velocity_")], ["velocity_12"])
        self.assertEqual(bad[key]["sprint_end"], "2000-01-01")  # create-only: never overwritten
        self.assertEqual(run.posted, list(NORMAL_WEEK_2))
        unreadable = self.run_with("unavailable")
        self.assertEqual(len([n for n in unreadable.calls if n.startswith("velocity_")]), 3)
        self.assertEqual(unreadable.store_writes, {})
        self.assertEqual(unreadable.posted, list(NORMAL_WEEK_2))

    def test_concurrent_creates_agree(self):
        first, second = {}, {}
        self.run_with(first)
        self.run_with(second)
        merged = dict(first)
        for key, entry in second.items():
            merged.setdefault(key, entry)
        self.assertEqual(merged, first)  # the same aggregates from the same closed sprints and roster
        self.assertEqual(first, second)


def _show(title, posted, status=None):
    print(f"===== {title}")
    for n, msg in enumerate(posted):
        print(f"--- {'Top post' if n == 0 else f'Reply {n}'}\n{msg}")
    if status:
        print(f"--- Nothing posted; run status\n{status}")


def _print():
    for title, (today, active) in {"week 1, development": (day(13, 1), 13), "week 2, development": (day(14, 2), 14),
                                   "week 3, development": (day(14, 3), 14), "refinement, week 1": (day(12, 1), 12),
                                   "pencils down": (day(15, 2), 15)}.items():
        _show(title, weekly(today, active)[0])
    for title, failed in {"week 2, features fetch failed after the retry": "features",
                          "week 2, sprint issue fetch failed after the retry": "sprint_issues"}.items():
        run, _ = weekly_run(day(14, 2), 14, lambda w, name=failed: w["fail"].add(name))
        _show(title, run.posted, run.status)


def _print_counts():
    """Reads by step and kind for the busy week: the per-section plan, then the shared plan
    cold, with one sprint newly closed, and warm."""
    store = {}
    weekly_run(day(13, 3), 13, lambda w: (busy(w, 13), w.update(velocity_store=store)))
    runs = {"old plan": weekly_run(day(14, 2), 14, busy, legacy=True)[0],
            "new, cold": weekly_run(day(14, 2), 14, busy)[0],
            "new, newly closed": weekly_run(day(14, 2), 14, lambda w: (busy(w), w.update(velocity_store=store)))[0]}
    runs["new, warm"] = weekly_run(day(14, 2), 14, lambda w: (busy(w), w.update(velocity_store=dict(store))))[0]
    counted = {title: read_counts(run) for title, run in runs.items()}
    steps = sorted({step for steps, _ in counted.values() for step in steps})
    print("step | " + " | ".join(counted))
    for step in steps:
        print(f"{step} | " + " | ".join(", ".join(f"{k} {v}" for k, v in sorted(counted[t][0].get(step, {}).items()))
                                        or "-" for t in counted))
    print("total | " + " | ".join(", ".join(f"{k} {v}" for k, v in sorted(totals.items()))
                                  for _, totals in counted.values()))


if __name__ == "__main__":
    if "--counts" in sys.argv:
        _print_counts()
    elif "--print" in sys.argv:
        _print()
    else:
        unittest.main()
