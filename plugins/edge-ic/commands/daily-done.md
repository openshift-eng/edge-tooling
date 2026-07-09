---
description: Mark a specific task in today's TODO file as done
argument-hint: "<Jira key | task text | item N>"
allowed-tools:
  - Read
  - Write
  - Bash
  - AskUserQuestion
---

# Daily Done

Mark a specific task in today's TODO file as done.

## Arguments

`$ARGUMENTS` — identifies the task to mark done: a Jira key (e.g., `OCPEDGE-2510`), a substring of the description (e.g., `TNF deploy`), or a positional reference (e.g., `item 2`, `the second priority item`). If empty, ask the user which task to mark done.

## Steps

1. Run `date +%Y-%m-%d` to get today's date, then read `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`. If the file doesn't exist, stop and tell the user.
2. Find the matching task:
   - Try an exact Jira key match first.
   - Otherwise, try a substring match against item descriptions.
   - Otherwise, try a positional match within the section implied by the reference.
3. If zero matches, tell the user and list the closest candidates (if any) instead of guessing.
4. If multiple matches, show them (with their section) and ask the user to pick one.
5. Change the matched item's `- [ ]` to `- [x]`, preserving all indented sub-lines (links, notes) beneath it exactly as they are.
6. If the item was in **Priority**, **In Progress**, **Waiting on Review**, **Review Requests**, or **Backlog**, move the whole item (including sub-lines) to the **Completed** section. Leave items already in Completed in place.
7. Show the user the change, then write the file.

## Rules

- Never remove sub-lines (links, notes) when moving or completing an item.
- Preserve the file's section structure exactly.
- Only mark the one matched item — don't guess at related items.
