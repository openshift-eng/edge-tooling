---
description: Add a task to today's TODO file
argument-hint: "<task description> [DONE]"
allowed-tools:
  - Read
  - Write
  - Bash
  - AskUserQuestion
---

# Daily Add

Add a task to today's TODO file.

## Arguments

`$ARGUMENTS` — the task description to add. If empty, infer from current context.

## Steps

1. Run `date +%Y-%m-%d` to get today's date, then read `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`. If the file doesn't exist, stop and tell the user.
2. Determine the task description:
   - **If `$ARGUMENTS` is provided:** use it directly. Jira ticket keys stay inline in the description; URLs are handled separately in step 4.
   - **If empty:** infer from the current git branch and recent commits. Confirm with the user before adding.
3. If the description contains a done marker (e.g., `(DONE)`, `(done)`, "mark as done"), strip it and use `- [x]` instead of `- [ ]`.
4. Extract links from the description: pull out any URLs and classify each per `plugins/edge-ic/references/TODO_FILE_FORMAT.md` (e.g., `github.com/*/pull/*` → PR, `*.atlassian.net/browse/*` → Jira, otherwise Doc). Remove extracted URLs from the inline description text — they become indented sub-lines instead, not part of the description.
5. Check for duplicates against the normalized description (with URLs removed) and any extracted Jira key — if an item with the same Jira key or nearly identical wording already exists, tell the user and stop.
6. If the description contains a bare Jira key (e.g., `OCPEDGE-2510`) but no explicit Jira URL was extracted, auto-generate a `Jira` sub-line pointing to `https://redhat.atlassian.net/browse/<KEY>`.
7. Append the item to the **Backlog** section (or **Completed** if marked done), followed by any link sub-lines.
8. Show the user what was added, then write the file.

## Rules

- Keep descriptions concise — match the existing style in the file.
- Preserve the file's section structure exactly.
- Never leave a raw URL inline in the description — always move it to a sub-line.
