---
description: Display today's TODO list, or a past day's, with optional section and count filters
argument-hint: "[YYYY-MM-DD] [--section=priority,progress,waiting,requests,completed,backlog,issues] [--limit=<n>]"
allowed-tools:
  - Read
  - Bash
---

# Daily Show

Display the TODO list for today, or for a specific past day. Supports filtering to specific sections and limiting the number of items shown.

## Arguments

`$ARGUMENTS` — optional, any combination, in any order:

- a date in `YYYY-MM-DD` format to show a past day's TODO instead of today's
- `--section=<name>[,<name>...]` — show only the named section(s). Matches `Priority`, `In Progress`, `Waiting on Review`, `Review Requests`, `Completed`, `Backlog`, `Key Issues Tracked` (case-insensitive; accepts common aliases like `progress`, `waiting`, `requests`, `done`, `issues`)
- `--limit=<n>` — show only the first `<n>` items, counted in file order across whatever sections are being displayed

Examples: `/edge-ic:daily-show`, `/edge-ic:daily-show 2026-07-01`, `/edge-ic:daily-show --section=priority,progress`, `/edge-ic:daily-show --limit=5`.

## Steps

1. Parse `$ARGUMENTS`: extract a date token (if present), a `--section=` value, and a `--limit=` value. Reject any remaining/unrecognized token and show the valid forms instead of silently ignoring it.
2. Determine the target date: use the parsed date if present, otherwise run `date +%Y-%m-%d` for today.
3. Read `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md` for the target date. If the file doesn't exist, tell the user and stop — do not create it.
4. **If `--section` was given:** keep only the matching sections, in their original file order. If any named section doesn't exist in the TODO format, tell the user the valid section names and stop.
5. **If `--limit` was given:** walk items in display order across the sections being shown (a checkbox item plus its indented sub-lines counts as one item; a `Key Issues Tracked` line counts as one item) and stop after `<n>` items. Never split an item from its sub-lines. If items were cut off, add a trailing note: "... N more item(s) not shown."
6. Read `plugins/edge-ic/references/DAILY_SHOW_OUTPUT_FORMAT.md` and render the resulting sections and items in that format — same content, checkbox states, and sub-lines as written in the file, organized into headings similar to `edge-ic:morning`'s briefing instead of a flat markdown dump.
7. If the sections being displayed are all empty, say so plainly (e.g., "Nothing tracked for 2026-07-09 yet") instead of printing an empty shell.

## Rules

- Read-only — never modify the TODO file.
- Apply `--section` filtering before `--limit` counting.
- Reformat presentation only — never summarize, paraphrase, or drop an item, checkbox state, or sub-line.
