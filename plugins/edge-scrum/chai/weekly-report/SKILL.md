---
name: weekly-report
description: Read-only coordinator skill that runs the OpenShift Edge weekly health report (edge-context decision 0018) and the sprint-end check-in's data as a script in the code sandbox — chai-bot fetches what the script asks for, round by round, and posts the result exactly as returned
---

# Weekly report and sprint-end check-in (chai-bot coordinator)

This playbook is for the **chai-bot coordinator**. The report is computed by
`plugins/edge-scrum/bin/weekly_report_files.py` in the **code sandbox**; you
only fetch what it asks for and post what it returns. It is read-only in Jira.

The script decides every query (and its paging, retries and order). You take turns:
it returns a **round** of tool calls, you make them, it reads the results and returns
the next round, until it returns the report. Results reach the sandbox by reference
through `compact`, so you never retype a Jira page.

## 1. Inputs

- `today`: today's date, `YYYY-MM-DD` (UTC).
- `mode`: `"weekly"` (the weekly health report) or `"checkin"` (the sprint-end check-in's data).
- Weekly only, for team availability: `absences`, `absence_window` and
  `engineer_count`, built exactly as the **scrum-context** skill's step 3 says (calendar
  absences mapped to anonymized ids outside the sandbox; never names, emails or event
  titles in the sandbox). Leave out a key whose source was not available.

## 2. Start (one `sandbox_exec`)

```python
import io, json, os, sys, tarfile, urllib.request
STATE = os.path.join(os.getcwd(), "weekly-report")  # the session directory (/tmp is not writable)
SRC = os.path.join(os.getcwd(), "edge-tooling")
if not os.path.exists(os.path.join(SRC, "bin", "weekly_report_files.py")):
    data = urllib.request.urlopen(
        "https://codeload.github.com/openshift-eng/edge-tooling/tar.gz/refs/heads/main", timeout=60).read()
    os.makedirs(SRC, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for m in tar.getmembers():
            parts = m.name.split("/")
            if parts[1:4] == ["plugins", "edge-scrum", "bin"] and m.name.endswith(".py") and "tests" not in parts:
                m.name = "/".join(["bin"] + parts[4:])
                tar.extract(m, SRC, filter="data")
sys.path.insert(0, os.path.join(SRC, "bin"))
import weekly_report_files as wr
out = wr.step(STATE, inputs=INPUTS)  # INPUTS: the dict of section 1
results.store(json.dumps(out), label="round", kind="text")
print(json.dumps(out)[:3500])
```

If the download fails, the run cannot start: say so as in section 5 with
`Weekly health report not posted: edge-tooling could not be downloaded: <error>`.

## 3. Each round

While `out["status"] == "fetch"`:

1. Make the round's tool calls: for each entry of `out["requests"]`, **in the listed order,
   one at a time**, call `<tool>(**args)` with exactly those arguments. Do not add, skip,
   reorder or repeat a call, and make no other call to these tools during the round.
   A call that fails is fine: its error is the result.
2. Call `compact(notes="weekly report round <n>", match=out["compact_match"], min_size=1)`.
   It stores the round's results as one object in call order; note its object ID.
3. One `sandbox_exec` with `load_result_ids=[<that object ID>]`:

```python
out = wr.step(STATE, results.get("<that object ID>"))
results.store(json.dumps(out), label="round", kind="text")
print(json.dumps(out)[:3500])
```

If `out` has a `redo` key, the round's results did not match its requests (a call was left
out, added or made for another tool). Make **all** of the round's calls again, in order, then
`compact` and the `sandbox_exec` above. The script asks for a round again only once.

When the printed output is cut (it ends before the closing `}`), read the whole JSON
from the stored result with `result_head(<its ID>, count=200000, as_chars=True)`. Copy
each request's `tool` and `args` from it exactly.

## 4. The result

- `"status": "report"` (weekly): `out["text"]` is the whole report: the top post, then
  `---THREAD_DETAILS---` and the thread replies separated by `---THREAD_BREAK---`, with
  the owners' mentions already in it. Post it **exactly as returned**: no edits, no
  reformatting, nothing added or left out. In a scheduled run, register it as the one
  response element and call `send_response(mode="report", result_ids=[])`; in a DM, reply
  with it. Read it with `result_head` when the printed output is cut.
- `"status": "checkin"`: the sprint-end check-in's data: `sprint`, `counts`,
  `first_message_parts`, `replies` (each with `kind`, `key`, `buttons` and its `text`) and
  `component_candidates`. Follow the `sprint_end_checkin` task's instructions for the
  planning-day check, the records, the component choices and posting; use each reply's
  `text` exactly as returned.
- `"status": "incomplete"`: `out["text"]` is the one line to deliver, as section 5 says.

## 5. When the report cannot be posted

`failure` is the line itself, for example `Weekly health report not posted: Sprint health
incomplete: sprint issue fetch incomplete: fetch: page 1: query_jira call failed: <error>`.
Post no figures and no partial report. End the run with these coordinator steps (not in
the sandbox), in order:

```text
scheduled run: 1. register one response element, its whole content: failure
scheduled run: 2. send_response(mode="report", result_ids=[])
interactive run (DM): 1. no_action_required(reason=failure)
```

## Rules

- Never retype or summarize a Jira page, a script output or the report.
- Never change a request's arguments, and never fetch anything the script did not ask for.
- The script's line is the whole explanation of a failure; do not add figures or guesses.
