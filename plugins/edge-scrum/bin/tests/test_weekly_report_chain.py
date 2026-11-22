"""End-to-end dry run of the chai weekly health report, step by step as the SKILLs say.

``query_jira`` / ``get_jira_issue`` text → ``chai_jira_adapter`` → ``transform-*.py``,
``compute-velocity.py`` and ``run-checks.py`` (run as scripts, as ``sandbox_exec``
runs them) → ``chai_report`` → the Slack message texts, built by the weekly SKILL's
own code blocks. Covers sprint weeks 1-3, the refinement, development and
pencils-down phases, more than 50 features (no rank order), and failed, missing
and shortened input: what is not complete is left out with its reason, never
reported as healthy.

All keys, names, sprints, releases and dates are synthetic (public repository).
Run this file directly to print the rendered reports.
"""

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
from _jira_transforms import extract_blocked_by, normalize_name  # noqa: E402
from tests.test_chai_epic_fields import _mix, skill_owner_snippet, skill_snippet  # noqa: E402
from tests.test_chai_jira_adapter import HINT, ROSTER_MD  # noqa: E402
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
SCHEDULES = {RELEASE: [
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


_KEY_BELOW_RE = re.compile(r"\bAND key < (" + ad.KEY_PATTERN + r") ORDER BY key DESC$")


def query_jira(sent, items, ignores_key_below=False):
    """query_jira's answer to one key-ordered page request ``sent`` over ``items``
    ({key: block}). ``ignores_key_below`` stands for a tool that does not apply
    ``key <``: it returns the first page again."""
    keys = sorted(items, key=_num, reverse=True)
    below = _KEY_BELOW_RE.search(sent)
    if below and not ignores_key_below:
        keys = [k for k in keys if _num(k) < _num(below.group(1))]
    if not keys:
        return f"No issues found for JQL: {sent}"
    body = "\n".join(items[k] for k in keys[:PAGE])
    if len(keys) > PAGE:
        return f"Showing the first {PAGE} matching issues; more match:\n{body}\n\n" + HINT.format(n=PAGE) + "\n"
    return f"Found {len(keys)} issues (showing {len(keys)}):\n{body}\n"


def page_through(jql, answer, guard=100):
    """The SKILL "Jira paging" loop (steps 1-3, one project) over the tool ``answer(sent) -> text``.

    First page ``(<JQL>) ORDER BY key DESC``; while the page says more match, the next
    page is ``(<JQL>) AND key < <last key> ORDER BY key DESC``
    (``chai_jira_adapter.page_jql``). Each page is read with
    ``chai_jira_adapter.parse_page``. It stops at a final page (``Found N issues``, fewer
    than 50, no results) or a page that is not readable, and also when the last key
    number does not go down (the page did not advance): that fetch is incomplete and is
    not fetched again. Returns the pages as adapt_fetch takes them. ``guard`` only keeps
    a broken loop from hanging the test."""
    sent, pages, last = ad.page_jql(jql), [], None
    while True:
        text = answer(sent)
        pages.append({"text": text, "jql": sent})
        page = ad.parse_page(text, jql=sent)
        if page["status"] != "ok" or page["final"] or not page["blocks"]:
            return pages
        key = page["blocks"][-1]["key"]
        if last is not None and _num(key) >= _num(last):
            return pages
        if len(pages) >= guard:
            raise AssertionError(f"paging did not stop after {guard} pages")
        last = key
        sent = ad.page_jql(jql, last_key=last)


def rank_page(jql, items):
    """The single rank-ordered features page (``ORDER BY Rank ASC``), in the given order."""
    keys = list(items)
    sent = f"({jql}) ORDER BY Rank ASC"
    body = "\n".join(items[k] for k in keys[:PAGE])
    if len(keys) > PAGE:
        text = f"Showing the first {PAGE} matching issues; more match:\n{body}\n\n" + HINT.format(n=PAGE) + "\n"
    else:
        text = f"Found {len(keys)} issues (showing {len(keys)}):\n{body}\n"
    return [{"text": text, "jql": sent}]


def issue_text(key, links):
    """get_jira_issue output with its "Linked Jira Issues" section."""
    rows = "\n".join(f"- {wording}: {other} (Blocks) | status: {status} | https://example.invalid/browse/{other}"
                     for wording, other, status in links)
    return (f"Latest Jira State:\n[{key}] Synthetic item\nType: Story | Status: In Progress\n"
            f"Linked Jira Issues:\n{rows}\nURL: https://example.invalid/browse/{key}\n")


# ---------------------------------------------------------------- synthetic team data


def _feature(key, sme="Alex Example", labels="ocpedge-plan", status="In Progress", size="M"):
    fields = [f"Labels: {labels}", f"customfield_10795: {size}", "customfield_10855: openshift-9.2",
              "customfield_10470: Quinn Quality", "customfield_10473: Dana Docs"]
    if sme:
        fields.append(f"customfield_10475: {sme}")
    return block(key, "Feature", status, "Pat Product", fields=fields)


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
                        "TEST-920": issue_text("TEST-920", [("blocks", "FEAT-20", "In Progress")])},
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
        "fail": set(),  # fetch names whose tool call fails
        "stuck": set(),  # fetch names whose tool ignores "key <" (returns the first page again)
        "schedules": SCHEDULES,
        "gaps": _mix()[2],
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


def skill_build_snippet():
    """The build-and-post code block from weekly-health-report/SKILL.md, verbatim."""
    return skill_snippet("build_report(")


class Incomplete(Exception):
    """A fetch of the section was incomplete: the section is left out (SKILL rule)."""


class WeeklyRun:
    """One weekly report, run step by step as the SKILLs say, against ``w``."""

    def __init__(self, w, today, sandbox):
        self.w, self.today, self.box = w, today, sandbox
        self.roster = ad.parse_team_roster(ROSTER_MD)
        self.roster_file = sandbox.write("roster.json", {"members": self.roster["members"],
                                                         "contacts": self.roster["contacts"]})
        self.missing, self.posted, self.pages = [], [], {}

    # -- Jira tools
    def fetch(self, name, jql, items, order="key"):
        if name in self.w["fail"]:
            pages = [{"error": "tool call failed"}]
        elif order == "rank":
            pages = rank_page(jql, items)
        else:
            stuck = name in self.w["stuck"]
            pages = page_through(jql, lambda sent: query_jira(sent, items, stuck))
            if len(pages) > 1 and "project in (" in jql:
                raise AssertionError(f"{name}: a query over several projects that does not fit on one page "
                                     "is paged per project (SKILL Jira paging step 4)")
        self.pages[name] = len(pages)
        out = ad.adapt_fetch({"name": name, "order": order, "pages": pages}, self.roster)
        self.missing += out["left_out"]
        return out

    def complete(self, name, adapted):
        if not adapted["fetch"]["complete"]:
            raise Incomplete(f"{name} fetch incomplete: {'; '.join(adapted['fetch']['problems'])}")
        return adapted

    def get_jira_issue(self, key):
        return None if f"get_jira_issue {key}" in self.w["fail"] else self.w["issue_links"].get(key)

    # -- scrum-context (step 1) and sections (step 2)
    def context(self):
        base = {"today": self.today, "board_id": BOARD, "sprints": board(self.active), "schedules": self.w["schedules"],
                "engineer_count": 3, "absences": []}
        self.ctx = sc.build_context(base)
        self.release_results = {r: sc.build_context(dict(base, release=r)) for r in self.w["schedules"]}
        windows = [result["release"] for result in self.release_results.values()]
        sections, missing = cr.select_sections(self.ctx, windows)
        self.missing += missing
        return sections

    # -- velocity (sprint-health step 4), computed once
    def velocity(self):
        files = []
        cur = self.ctx["current_sprint"]["number"]
        for n in [n for n in sorted(self.w["velocity"]) if n < cur][-3:]:
            try:
                adapted = self.complete(f"velocity sprint {n}", self.fetch(
                    f"velocity_{n}", f"project in (OCPEDGE, USHIFT, OCPBUGS) AND sprint = {_sprint_id(n)}",
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
                "sprint_issues", f"project in (OCPEDGE, USHIFT, OCPBUGS) AND sprint = {cur['id']}", self.w["sprint"]))
        except Incomplete as exc:
            self.missing.append({"what": "Sprint health", "why": str(exc)})
            return None
        links_complete = True
        if mode == "mid-sprint":
            candidates = self.fetch("sprint_blocked",
                                    f"(project in (OCPEDGE, USHIFT, OCPBUGS) AND sprint = {cur['id']}) "
                                    "AND issueLinkType = \"is blocked by\" AND statusCategory != Done",
                                    {k: self.w["sprint"][k] for k in self.w["sprint_blocked"]})
            problems = ad.attach_issue_links(adapted, {i["key"]: self.get_jira_issue(i["key"])
                                                       for i in candidates["issues"]})
            links_complete = candidates["fetch"]["complete"] and not problems
        meta = self.box.write("meta.json", {"sprint_id": int(cur["id"]), "sprint_name": cur["name"],
                                            "sprint_start": cur["start"], "sprint_end": cur["end"]})
        out = self.box.run("transform-sprint-issues.py", "--input", self.box.issues("sprint", adapted),
                           "--sprint-meta", meta, "--today", self.today, "--output", "sprint_issues.json")
        with open(out, encoding="utf-8") as fh:
            issues = json.load(fh)
        return cr.sprint_health(issues, cur, self.today, mode, velocity, self.ctx["current_sprint_availability"],
                                adapted["skipped_signals"], links_complete, velocity_reason=reason)

    # -- release-health steps 3 and 4
    def _epics(self, feature_keys):
        items = {k: v for k, v in self.w["epics"].items()
                 if any(f"customfield_10018: {f}" in v for f in feature_keys)}
        adapted = self.complete("epics", self.fetch(
            "epics", f'project in (OCPEDGE, USHIFT) AND "Parent Link" in ({", ".join(feature_keys)})', items))
        return self.box.run("transform-epics.py", "--input", self.box.issues("epics", adapted),
                            "--output", "epics.json")

    def _children(self, epic_keys):
        items = {k: v for k, v in self.w["stories"].items() if any(f"Parent: {e}" in v for e in epic_keys)}
        return self.complete("stories", self.fetch(
            "stories", f"project in (OCPEDGE, USHIFT, OCPBUGS) AND parent in ({', '.join(epic_keys)})", items))

    def _spikes(self, features_fetch, window, refinement_closed):
        ref = window["refinement_sprint"]
        spikes = self.complete("refinement-sprint spikes", self.fetch(
            "ref_spikes", f"project in (OCPEDGE, USHIFT) AND issuetype = Spike AND sprint = {ref['id']}",
            self.w["ref_spikes"]))
        problems = ad.attach_issue_links(spikes, {i["key"]: self.get_jira_issue(i["key"]) for i in spikes["issues"]})
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
        jql = (f'project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") AND '
               f'"Target Version" = "openshift-{sec["release"]}" AND status != Closed')
        fetch = self.complete("features", self.fetch("features", jql, self.w["features"]))
        keys = [i["key"] for i in fetch["issues"]]
        self._epics(keys)
        epics = self.box.read("epics.json")
        counted = self._children(epics["epic_keys"])
        counts = {}
        for i in counted["issues"]:
            parent = (i.get("parent") or {}).get("key")
            counts[parent] = counts.get(parent, 0) + 1
        state = next(s["state"] for s in board(self.active) if s["id"] == sec["window"]["refinement_sprint"]["id"])
        features, spikes = self._spikes(fetch, sec["window"], state == "closed")
        return cr.refinement_gaps(features, spikes, sec, state == "closed", epics=epics, epic_issue_counts=counts,
                                  week=self.ctx["current_sprint"]["week"])

    def release(self, sec, velocity, reason):
        tag, window = sec["release"], sec["window"]
        jql = (f'project = OCPSTRAT AND issuetype = Feature AND labels in ("ocpedge-plan", "microshift") AND '
               f'"Target Version" = "openshift-{tag}"')
        features_fetch = self.fetch("features", jql, self.w["features"], order="rank")
        if not features_fetch["fetch"]["complete"] and "features" not in self.w["fail"]:
            # More than one page: fetch again by key; the cut line is then left out.
            features_fetch = self.fetch("features", jql, self.w["features"])
        self.complete("features", features_fetch)
        keys = [i["key"] for i in features_fetch["issues"]]
        epics_file = self._epics(keys)
        epics = self.box.read("epics.json")
        stories_raw = self.box.issues("stories", self._children(epics["epic_keys"]))
        stories_file = self.box.run("transform-stories.py", "--input", stories_raw, "--today", self.today,
                                    "--output", "stories.json")
        bugs_jql = f'project = OCPBUGS AND fixVersion in ("{tag}", "{tag}.0") AND "Epic Link" is EMPTY'
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
                                {k: self.w["features"][k] for k in self.w["feature_blocks"]})
        links, unread = None, []
        if candidates["fetch"]["complete"]:
            problems = ad.attach_issue_links(candidates, {i["key"]: self.get_jira_issue(i["key"])
                                                          for i in candidates["issues"]})
            unread = sorted({p.split(":")[0] for p in problems})
            links = {i["key"]: extract_blocked_by(i) for i in candidates["issues"] if i["key"] not in unread}
        return cr.release_health(checks, sec, capacity, cr.rank_order_available(features_fetch), reason,
                                 features=features, epics=epics, spikes=spikes,
                                 stories=self.box.read(os.path.basename(stories_file)), links=links,
                                 links_unread=unread, current_sprint_number=self.ctx["current_sprint"]["number"])

    # -- owners, build and post (steps 5 and 6)
    def mentions(self, sprint, computed, gaps):
        scope = {"sprint": sprint, "computed": computed, "gaps": gaps, "scrum_master": SCRUM_MASTER}
        exec(skill_owner_snippet(), {}, scope)  # noqa: S102 - our own SKILL text
        contacts = {normalize_name(c["display_name"]): c for c in self.roster["contacts"]}
        out = {}
        for owner in scope["owners"]:
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
        gaps = self.w["gaps"]
        scope = {"chai_report": cr, "sprint": sprint, "computed": computed, "missing": self.missing,
                 "mentions": self.mentions(sprint, computed, gaps), "scrum_master": SCRUM_MASTER, "gaps": gaps}
        exec(skill_build_snippet(), {}, scope)  # noqa: S102 - our own SKILL text
        thread = self.post(scope["top_post_text"])
        for reply in scope["replies"]:
            self.post(reply, thread)
        return scope["report"]


def weekly(today, active, change=None):
    """The posted messages and the report for the synthetic team on ``today``."""
    if change is None:
        return _unchanged(today, active)
    run, report = weekly_run(today, active, change)
    return run.posted, report


def weekly_run(today, active, change=None):
    """The finished :class:`WeeklyRun` (posted messages, pages per fetch) and the report."""
    w = world(active)
    if change:
        change(w)
    with tempfile.TemporaryDirectory() as path:
        run = WeeklyRun(w, today, Sandbox(path))
        report = run(active)
    return run, report


@functools.cache
def _unchanged(today, active):
    """The unchanged world's run, computed once per day (the tests only read it)."""
    run, report = weekly_run(today, active)
    return run.posted, report


def _left_out(posted):
    return next((ln for ln in posted[-1].splitlines() if ln.startswith("Left out: ")), "")


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

    def test_week_3_sprint_end_view(self):
        posted, _ = weekly(day(14, 3), 14)
        self.assertIn("delivered 5 of 8 SP (62%); likely carry-over 2 issues (3 SP)", posted[1])
        self.assertIn("carry-over", posted[1])


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
        run, _ = weekly_run(day(14, 2), 14, change)
        posted = run.posted
        self.assertEqual(run.pages["features"], 2)  # the key re-fetch, paged to the end
        self.assertIn("✓ 60 of 60 feature(s) on track", posted[2])
        self.assertNotIn("below the cut line", "\n".join(posted))
        self.assertIn("9.2 cut line: features did not fit on one rank-ordered page", _left_out(posted))


class PartialInputTest(unittest.TestCase):
    def assert_partial(self, posted, what, why):
        self.assertFalse(posted[0].startswith("🟢"))
        self.assertIn(f"{what}: {why}", _left_out(posted))

    def test_failed_features_call_leaves_the_release_section_out(self):
        posted, report = weekly(day(14, 2), 14, lambda w: w["fail"].add("features"))
        self.assert_partial(posted, "9.2 release section",
                            "features fetch incomplete: fetch: page 1: query_jira call failed")
        self.assertNotIn("release health", "\n".join(posted))
        self.assertTrue(posted[1].startswith("*Sprint health: Test Sprint 14*"))  # the rest is posted
        self.assertEqual(budget_problems(report), [])

    def test_shortened_sme_is_not_read_as_a_person(self):
        def change(w):
            w["features"]["FEAT-20"] = _feature("FEAT-20", sme="(value stored; read it with result_page)")
        posted, _ = weekly(day(14, 2), 14, change)
        self.assert_partial(posted, "9.2 release section",
                            "features fetch incomplete: FEAT-20: customfield_10475 value shortened (not read whole)")
        self.assertNotIn("value stored", posted[0])

    def test_shortened_sprint_labels_leave_sprint_health_out(self):
        def change(w):
            w["sprint"]["TEST-13"] = block("TEST-13", fields=["Labels: Parked, +1 more"])
        posted, _ = weekly(day(14, 2), 14, change)
        self.assert_partial(posted, "Sprint health", "sprint issue fetch incomplete: TEST-13: labels value "
                            "shortened (not read whole)")
        self.assertNotIn("*Sprint health", "\n".join(posted))
        self.assertTrue(posted[1].startswith("*9.2 release health*"))

    def test_non_advancing_pages_stop_and_leave_the_fetch_out(self):
        def change(w):  # 60 unlinked bugs (one project); the tool ignores "key <"
            w["bugs"] = {f"BUG-{n}": block(f"BUG-{n}", "Bug", fields=["Fix Versions: 9.2"]) for n in range(100, 160)}
            w["stuck"].add("bugs")
        run, report = weekly_run(day(14, 2), 14, change)
        self.assertEqual(run.pages["bugs"], 2)  # stopped at the repeated page, never fetched again
        self.assert_partial(run.posted, "9.2 release section", "bugs fetch incomplete: fetch: ")
        self.assertIn("keys are not strictly descending", _left_out(run.posted))
        self.assertNotIn("release health", "\n".join(run.posted))
        self.assertTrue(run.posted[1].startswith("*Sprint health: Test Sprint 14*"))  # the rest is posted
        self.assertEqual(budget_problems(report), [])

    def test_paging_reaches_the_last_page(self):
        items = {f"TEST-{n}": block(f"TEST-{n}") for n in range(1, 121)}
        pages = page_through("project = TEST", lambda sent: query_jira(sent, items))
        self.assertEqual([p["jql"] for p in pages], [
            "(project = TEST) ORDER BY key DESC",
            "(project = TEST) AND key < TEST-71 ORDER BY key DESC",
            "(project = TEST) AND key < TEST-21 ORDER BY key DESC"])
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
        self.assertIn("9.2 blocks of FEAT-30: issue links could not be read", _left_out(posted))

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


def _print():
    for title, (today, active) in {"week 1, development": (day(13, 1), 13), "week 2, development": (day(14, 2), 14),
                                   "week 3, development": (day(14, 3), 14), "refinement, week 1": (day(12, 1), 12),
                                   "pencils down": (day(15, 2), 15)}.items():
        posted, _ = weekly(today, active)
        print(f"===== {title}")
        for n, msg in enumerate(posted):
            print(f"--- {'Top post' if n == 0 else f'Reply {n}'}\n{msg}")


if __name__ == "__main__":
    if "--print" in sys.argv:
        _print()
    else:
        unittest.main()
