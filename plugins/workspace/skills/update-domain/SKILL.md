---
name: update-domain
description: Feed lessons learned from a project session back into the domain it was built from — context files, supplemental CLAUDE.md, and reference docs
argument-hint: "[name-or-number] [PR link or focus hint]"
---

# Update Domain From Project Lessons

Close the knowledge loop: a project session often surfaces durable facts
about the domain it was built from (a repo's behavior, a proven command, a
cross-repo gotcha). This command mines those lessons and writes them into
the domain's own files, so future workspaces built from it start smarter.

The first token in `$ARGUMENTS` is an optional project name or numeric
shorthand. Everything after it is an optional PR/diff link or focus hint
for lesson mining.

## Guardrails — read before editing anything

- **Never write to a bundled domain directly.** A domain resolved with
  `location: "bundled"` lives under `${CLAUDE_PLUGIN_ROOT}/domains/` — the
  plugin's own shipped, read-only checkout. This is true even if you
  happen to be developing this very plugin and that directory is a
  writable git checkout you could technically edit: it is not *this*
  workspace's copy, and editing it silently changes the plugin for every
  other workspace instead of this one. Always run
  `domain-info.py --copy-on-write` (Step 5) first and use only the `dir`
  path it returns for Edit/Write calls.
- **No writes before Step 4's confirmation.** Mining and routing are
  read-only; nothing is written until the user approves the plan.
- **Never touch:** `domain.yaml` (domain identity), the domain's own
  `dev-env.yaml` (repo list), the workspace root `dev-env.yaml`,
  `settings.local.json.tpl`, `projects/` files (that's
  `/workspace:update-project`'s territory), memory files. Never delete a
  domain file.
- **Redistribution is copy-only.** Step 6 copies an edited
  `context/<repo>.md` over an already-cloned repo's `DOMAIN-CONTEXT.md`.
  Never overwrite a repo's own `CLAUDE.md` from `supplemental/<repo>.md`;
  it can have diverged. Tell the user instead.

## Step 1: Resolve Project and Domain

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/resume-project.py" <first-token>`
(no token: use the project loaded in this conversation, if any). If
`status` is not `ok`, do what `error_message` says.

Then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/domain-info.py" <project-name>`.
If `status` is not `ok`, tell the user the `error` and stop. Note
`domain.writable`, `domain.dir`, `domain.files`, `domain.repos`, and
`domain.source.type`.

## Step 2: Mine Lessons

Read `domain.files` from Step 1 first — never propose a lesson that's
already documented there.

Mine from: the current session's conversation, the project's CLAUDE.md and
Reference Files detail docs, and an optional PR/diff link or focus hint
passed after the project token in `$ARGUMENTS`.

Categories to look for:
- Repo capability or behavior changes
- Key paths worth orienting new sessions to
- Proven build/test/debug commands not already documented
- Cross-repo interactions and gotchas
- Domain-wide facts (apply to every repo in the domain)
- Long-form reference material (architecture notes, deep dives)

Filter aggressively: only durable, domain-level knowledge. Not project
status, not one-off debugging detail, not anything already in the target
file. If nothing qualifies, say so and stop — do not manufacture a lesson
to justify writing something.

## Step 3: Route Each Lesson

For each lesson, pick exactly one target:

| Lesson shape | Target |
|---|---|
| Repo-specific orientation | `context/<repo>.md` — create from `${CLAUDE_PLUGIN_ROOT}/skills/create-domain/context-template.md`'s shape if missing. The repo must appear in `domain.repos`. |
| Repo working-doc material | `supplemental/<repo>.md` — only if it already exists; never create a new supplemental file. |
| Domain-wide fact | `context.md` — keep the whole file ~20–40 lines. |
| Long-form reference | `docs/<topic>.md` — new or existing. |

Follow `context-template.md`'s line discipline (30–75 lines per-repo file).
If a per-repo file would overflow, move the long-form part to `docs/` and
leave a one-line pointer instead.

## Step 4: Confirm (Dry Run)

Show a table, one row per lesson:

```
| # | Lesson | Target file | New/Edit |
|---|--------|-------------|----------|
```

Followed by the actual diff (or full content, for new files) for each
target file — computed against the current files at `domain.dir` (Step 5 is a plain
copy, so the content is the same after it).

Ask via AskUserQuestion: apply all / apply a subset / cancel.

**No file is written before this is answered.**

## Step 5: Ensure Writable

If `domain.writable` was `false` in Step 1:

Explain briefly: this domain is bundled with the plugin, so the workspace
gets its own copy at `domains/<name>/` before editing (shadows the
bundled one automatically; `source: bundled` stays untouched in
`dev-env.yaml`, so `refresh-domain` remains safe). Confirm, then run:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/domain-info.py" --copy-on-write <project-name>
```

Use the `domain.dir` from **this** response for every Edit/Write in Step
7 — not the original bundled path.

## Step 6: Apply

Edit or write each approved lesson in `domain.dir`. Then record and
redistribute in one call, with one `--entry` per changed file:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/domain-info.py" --record <project-name> \
  --entry "context/<repo>.md: <what changed and why>" [--entry ...]
```

It appends an entry to the domain's `UPDATES.md` and copies each edited
`context/<repo>.md` over `repos/<repo>/DOMAIN-CONTEXT.md` when that file
exists. It never touches a repo's own `CLAUDE.md`.

## Step 7: Offer PR-Back (External Domains Only)

If `domain.source.type == "git"`, ask via AskUserQuestion whether to open
a PR against the source repo. If yes:

1. Clone `domain.source.url` to a temp dir.
2. Copy the changed files in (respecting `domain.source.subdir` when set)
   — exclude `UPDATES.md` by default, it's workspace-local bookkeeping.
3. Branch `update-domain/<name>-<YYYY-MM-DD>`, commit, confirm before
   pushing, then create the PR (or print manual push/PR instructions if
   `gh` isn't available or the user declines the push).

## Step 8: Report

Summarize: files changed, whether redistribution ran, the `UPDATES.md`
entry, and the PR link if one was opened.

If `domain.source.type == "git"`, add a reminder: `refresh-domain`
re-fetches from the source and discards local domain edits (it warns when
`UPDATES.md` is present, but the safest path is merging the PR first).
