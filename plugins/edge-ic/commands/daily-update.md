---
description: Update today's TODO file based on what was accomplished in this conversation
allowed-tools:
  - Read
  - Write
  - Bash
  - AskUserQuestion
---

# Daily Update

Update today's TODO file based on what was accomplished in this conversation.

## Steps

1. Run `date +%Y-%m-%d` to get today's date, then read `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`. If the file doesn't exist, stop and tell the user.
2. Review the conversation for: completed work, progress on existing items, new tasks discovered, and any PR/doc/Jira links mentioned for existing items.
3. For each outcome, build a proposed change (don't write yet):
   - If it matches an existing `- [ ]` item, propose changing it to `- [x]` (keep any ticket reference and sub-lines).
   - If it represents progress but not completion, leave `- [ ]` and propose appending a brief status note (as a `Note:` sub-line) only if an equivalent note is not already present.
   - If it's a new task not in the file, extract any URLs and Jira keys into link sub-lines first (classify per `plugins/edge-ic/references/TODO_FILE_FORMAT.md`), then propose appending it to the appropriate section (Priority, In Progress, Waiting on Review — if it's a PR the user just opened, Review Requests — if someone asked the user to review a PR, or Backlog) only if no equivalent item already exists.
   - If a PR, Doc, or Jira link was mentioned for an item that doesn't already have that link as a sub-line, propose adding it (classify per `plugins/edge-ic/references/TODO_FILE_FORMAT.md`).
4. **Confirm before completing:** for each item proposed to move to `- [x]`, show the user the specific item and ask them to confirm before applying it. Apply confirmed completions; skip declined ones. Additions of new tasks, status notes, and links don't require this per-item confirmation — cover them in the summary instead.
5. Show the user a short summary of all changes (not the full file), then write the updated file.

## Rules

- Never remove existing items or sub-lines.
- Keep descriptions concise — match the existing style in the file.
- Preserve the file's section structure exactly.
- Never mark an item `- [x]` without the user confirming that specific item.
- If nothing in the conversation maps to a TODO update, say so and stop.
