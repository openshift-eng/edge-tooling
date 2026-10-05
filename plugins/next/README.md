# next

Write a curated handoff note at a breaking point, and `/next:go` clears the
context and resumes in a fresh session from the note by itself — no `/clear`,
no confirmation. Works in any repository with no setup: it is the session
handoff from the [workspace](../workspace/README.md) plugin, without the
workspace.

This supersedes the old `handoff` plugin (`/handoff:handoff`), which required
a manual `/clear` after writing the note. Uninstall `handoff` before
installing `next`; the two write to different note directories so they won't
collide, but there's no reason to run both.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install next@edge-tooling
```

Then restart Claude Code.

Requirements:

- A Claude Code version that supports plugin function hooks (tested on
  2.1.289). On older versions the note still loads on your next manual
  `/clear`, but the automatic clear and resume do not happen.
- `python3` on your `PATH`.

## Usage

```text
/next:go
/next:go rerun the failing e2e with the new fixture
```

The optional text sets the next task. You do not confirm anything: as soon as
the turn ends, Claude clears the context and starts the new session from the
note.

Claude writes a note covering the goal, the git state (branch, uncommitted
and unpushed work), decisions with their reasons, dead ends, the single next
task, and the files the next task needs. A `SessionStart` hook on
`startup|clear` injects that note into the fresh session, which starts the
next task directly.

The repo's CLAUDE.md is not part of the note: Claude Code loads it in every
session already. If the session discovered something that belongs there
permanently, the skill proposes the addition and leaves the edit to you.

## How it compares

| | `/compact` | `/workspace:handoff` | `/next:go` |
|---|---|---|---|
| Setup | none | workspace + project | plugin install |
| Next session starts with | lossy summary of history | project docs + next task | curated note, near-empty context |
| Confirmation before continuing | n/a | yes | no — resumes automatically |
| State on disk | no | project CLAUDE.md + detail files (long-lived) | one note (single-use) |
| Lifetime | n/a | until the project closes | 60 min by default, max 7 days |

Use `/workspace:handoff` when the work is a tracked workspace project; use
this plugin everywhere else.

## Behavior

- **Storage:** `~/.claude/next/<flattened project path>.md`. Nothing is
  written inside the repository, so `git status` stays clean and nothing
  personal reaches teammates.
- **Scope:** keyed by the launch directory (`CLAUDE_PROJECT_DIR`), so each
  git worktree has its own note.
- **Single use:** the hook fires once, on the next `/clear` or fresh launch
  in the same directory (`--resume`/`--continue` sessions are skipped).
- **Expiry:** `scripts/handoff.py arm` stamps an `expires_at` header into the
  note: 60 minutes by default (override with `HANDOFF_TTL_MINUTES`), capped
  at 7 days. An unarmed note expires 60 minutes after it was last written.
  After firing or expiring, the note is kept as `<key>.consumed.md` for
  manual recovery.
- **Size cap:** `arm` rejects a note too big to fit, once wrapped, under
  Claude Code's 10,000-character hook output limit. An unarmed note that
  somehow exceeds it is truncated on injection, visibly, with the full note
  still recoverable from `<key>.consumed.md`.
- **Disarm:** `python3 <plugin root>/scripts/handoff.py clear`.

## How it works

Three parts work together:

1. **Skill** `skills/go/SKILL.md`: the model writes the note to the path
   `handoff.py path` gives, then runs `handoff.py arm`.
2. **Mod** `hooks/register.ts` (a plugin function hook, listed under
   `modules` in `hooks/hooks.json`): a `command.run` hook on `next:go` sets
   the phase to `running`. A `tool.call` hook on Bash sets `armed` when the
   `arm` command returns `"status": "ok"`. At `turn.complete`, if armed, the
   mod runs `/clear` and then submits the resume prompt.
3. **Script** `scripts/handoff.py`, run by the `SessionStart` command hook
   (`startup|clear`): `read` injects the live note as `additionalContext`
   and moves it to `<key>.consumed.md`.

Contracts that break silently if they drift: the mod's `ARM` regex must
match the `arm` command string in `SKILL.md`; the mod's `SKILL` constant
(`next:go`) must match the plugin name and skill folder; and `note_path()`
in `handoff.py` is the only place that computes a note's location — the
skill and the hook must agree on it.

## Design notes

- **No confirmation step, on purpose.** `/next:go` always clears and
  resumes; there is no flag to pause first. `/next:later` (arm without
  clearing, resume at a named time) is reserved but not built — the
  `--until`/`--ttl-minutes` flags on `handoff.py arm` already support it.
- **`python3`, stdlib only.** Plugins cannot install Python packages, and
  `read` runs on every `/clear`, so `handoff.py` has no dependencies.
- **Lossy on purpose.** The note holds only what CLAUDE.md and the repo
  do not — state, decisions, dead ends, next task — capped at 60 lines.
  It works best at task boundaries, not mid-debug-session.

## Testing

```bash
python3 plugins/next/tests/test_handoff.py   # script tests (stdlib unittest)
claude plugin test plugins/next              # mod tests (tests/*.test.ts)
claude plugin validate plugins/next          # manifest, hooks, $.state contract
```

Manual check: run `/next:go` in any repo, confirm the session clears and the
new one restates the next task without being asked. A second `/clear` must
not fire again. Repeat with a resumed or continued session and confirm it
does not fire.

## Feedback

Open an issue against [openshift-eng/edge-tooling](https://github.com/openshift-eng/edge-tooling).
