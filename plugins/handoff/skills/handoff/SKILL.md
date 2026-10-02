---
name: handoff
description: Use at a natural breaking point to write a handoff note so the next session (after /clear or a relaunch) resumes exactly where this one stopped, in any repo, without the workspace plugin. Not for workspace projects, which use /workspace:handoff
argument-hint: "[focus and/or when you will resume]"
user-invocable: true
disable-model-invocation: true
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# Hand Off a Session

Write a note that lets a fresh session pick up this work cold, then arm it
so the next `/clear` or fresh launch in this directory loads it
automatically. The repo's CLAUDE.md already
loads in every session; the note carries only what CLAUDE.md does not:
this session's state, decisions, and next step.

## Steps

### Step 1: Locate the Note

Run via Bash:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" path --project-dir "${CLAUDE_PROJECT_DIR}"
```

Parse the JSON:

- **`status: "error"`** — show `message` and stop.
- **`workspace_detected: true`** and a workspace project is loaded in this
  conversation — tell the user `/workspace:handoff` keeps their project docs
  in sync and ask (AskUserQuestion) whether to use it instead or continue
  here. Stop if they pick the workspace one.
- **`exists: true`** — a handoff is already armed for this directory. It is
  overwritten in Step 4; say so in the report.

Keep `path`, `now`, `weekday`, `ttl_minutes` and `max_ttl_minutes` for
Step 4.

### Step 2: Capture the Git State

Run via Bash:

```bash
git branch --show-current; git status --short | head -30; git log --oneline -5; git log --oneline @{upstream}..HEAD 2>/dev/null
```

Skip this step outside a git repo. Uncommitted and unpushed work is the
detail the next session most often gets wrong, so record it exactly.

### Step 3: Compose the Note

Write from this conversation, not from re-reading files. `$ARGUMENTS` may
carry a focus, a resume time ("tomorrow morning", "after lunch", "Monday"),
or both. Make the focus the next task unless the conversation clearly
contradicts it; say so if it does. The resume time is for Step 4, not the
note.

Use exactly this structure, and keep it under 60 lines:

```markdown
# Handoff: <short task label>

**Goal:** <one or two sentences: what we are trying to achieve and why>

## State
- Branch `<branch>`; <uncommitted files / unpushed commits, or "clean">
- <what is done, one line each, the facts the next session would otherwise re-derive>

## Decisions
- <decision> — <why; include rejected alternatives worth not retrying>

## Dead ends
- <what was tried and failed, and the evidence>

## Next task
<the single next action, concrete enough to start without asking>

## Read first
- `<repo-relative path>` — <why it matters for the next task>

## Open questions
- <anything waiting on the user or an external answer>
```

Omit any section that would be empty, except **Goal**, **State** and
**Next task**. List under **Read first** only files the next task needs,
at most five; never CLAUDE.md, which loads by itself.

### Step 4: Write and Arm

Write the note to `path` from Step 1 with the Write tool, then arm it.
Choose the expiry from what the user said, with `now` and `weekday` from
Step 1 as the reference:

- **No timeframe mentioned** — run `arm` with no flag (default TTL,
  normally 60 minutes).
- **A duration** ("back in 3 hours") — `--ttl-minutes`, with about 25%
  slack, rounded up.
- **A point in time** — `--until`, set to the *end* of the window the user
  named, not its start: a late resume should still find the note, and
  the next session retires it anyway. "Tomorrow morning" → `12:00`;
  "after lunch" → `17:00`; "tonight" → `23:59`; "tomorrow" → tomorrow
  `23:59`. A day other than today or tomorrow ("Monday") → an ISO
  timestamp at the end of that day, e.g. `2026-10-05T23:59`. `HH:MM` means
  its next occurrence.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" arm --project-dir "${CLAUDE_PROJECT_DIR}" [--ttl-minutes N | --until HH:MM|ISO]
```

Parse the JSON:

- **`status: "ok"`** — keep `expires_at` and `expires_in` for Step 6.
- **`status: "error"`** — if the requested time is over `max_ttl_minutes`
  (one week), tell the user the handoff cannot live that long and that a
  stale note firing into an unrelated session is the risk it guards
  against; offer the maximum or a manual paste of the note instead.
  For any other error, show `message`; the note is on disk but unarmed
  and expires after the default TTL.

### Step 5: Offer Durable Learnings

If the session surfaced something every future session in this repo
should know (a build quirk, a convention, a misleading test), list it
for the user as a proposed CLAUDE.md addition. **Do not edit CLAUDE.md
yourself:** it is checked in and shared, and a handoff is personal and
temporary.

### Step 6: Report

If `status` was `"ok"`, tell the user, filling in the values:

> Handoff armed for `<project_dir>` until `<expires_at>` (`<expires_in>`).
> The next `/clear` or new session in this directory resumes at
> `<next task>`, once. The note is at `<path>`; edit it before then if
> anything is off, keeping its `expires_at` header.

If `status` was `"error"`, you already reported it in Step 4 — do not
produce the armed report above.

## Examples

```text
/handoff:handoff
/handoff:handoff rerun the failing e2e with the new fixture
/handoff:handoff we'll continue tomorrow morning     # arm --until 12:00
/handoff:handoff back after a 2h meeting             # arm --ttl-minutes 150
```

To disarm without clearing:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" clear --project-dir "${CLAUDE_PROJECT_DIR}"
```

## Notes

- **Read-write.** Writes one file under `~/.claude/handoffs/`. Never
  writes inside the repository, never edits CLAUDE.md, never commits.
- **Keyed by launch directory.** Each git worktree gets its own note. A
  note armed in one directory does not fire in another.
- **`/clear` and fresh launches consume it**; `--resume`/`--continue` do
  not, since those sessions already have their context. After it fires,
  or expires, the note moves to `<key>.consumed.md` beside it, so it can
  still be pasted by hand.
