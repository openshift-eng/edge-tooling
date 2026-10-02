# handoff

Write a curated handoff note at a breaking point, then `/clear` (or quit
and come back later), and the next session resumes exactly where you
stopped. Works in any repository
with no setup: it is the session handoff from the
[workspace](../workspace/README.md) plugin, without the workspace.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install handoff@edge-tooling
```

## Usage

```text
/handoff:handoff [focus for the next session] [when you will resume]
/clear
```

Say when you will be back and the handoff lives that long:
`/handoff:handoff we'll continue tomorrow morning` arms it until noon
tomorrow, so a fresh `claude` launch then picks it up.

Claude writes a note covering the goal, the git state (branch, uncommitted
and unpushed work), decisions with their reasons, dead ends, the single
next task, and the files the next task needs. A `SessionStart` hook on
`startup|clear` injects that note into the fresh session, which reads the listed
files, re-checks git, and reports where things stand before doing anything.

The repo's CLAUDE.md is not part of the note: Claude Code loads it in every
session already. If the session discovered something that belongs there
permanently, the skill proposes the addition and leaves the edit to you.

## How it compares

| | `/compact` | `/workspace:handoff` | `/handoff:handoff` |
|---|---|---|---|
| Setup | none | workspace + project | plugin install |
| Next session starts with | lossy summary of history | project docs + next task | curated note, near-empty context |
| State on disk | no | project CLAUDE.md + detail files (long-lived) | one note (single-use) |
| Can review/edit before continuing | no | yes | yes |
| Lifetime | n/a | until the project closes | 60 min, or until the time you name (max 7 days) |

Use `/workspace:handoff` when the work is a tracked workspace project; use
this plugin everywhere else.

## Behavior

- **Storage:** `~/.claude/handoffs/<flattened project path>.md`. Nothing is
  written inside the repository, so `git status` stays clean and nothing
  personal reaches teammates.
- **Scope:** keyed by the launch directory (`CLAUDE_PROJECT_DIR`), so each
  git worktree has its own handoff.
- **Single use:** the hook fires once, on the next `/clear` or fresh launch
  in the same directory (`--resume`/`--continue` sessions are skipped).
- **Expiry:** `scripts/handoff.py arm` stamps an `expires_at` header into
  the note: 60 minutes by default (override with `HANDOFF_TTL_MINUTES`),
  or `--ttl-minutes N` / `--until HH:MM|ISO` when you named a time, capped
  at 7 days. An unarmed note expires 60 minutes after it was last written.
  After firing or expiring, the note is kept as `<key>.consumed.md` for
  manual recovery.
- **Size cap:** `arm` rejects a note too big to fit, once wrapped, under
  Claude Code's 10,000-character hook output limit. An unarmed note that
  somehow exceeds it is truncated on injection, visibly, with the full note
  still recoverable from `<key>.consumed.md`.
- **Disarm:** `python3 <plugin root>/scripts/handoff.py clear`.

## Testing

```bash
python3 plugins/handoff/tests/test_handoff.py
```

Manual check: run `/handoff:handoff` in any repo, inspect the reported note
path, `/clear`, send any message, and confirm the new session restates the
next task. A second `/clear` must not fire again. Repeat with
`/handoff:handoff back in 2 hours`, quit, relaunch `claude`, and confirm
it fires on startup.
