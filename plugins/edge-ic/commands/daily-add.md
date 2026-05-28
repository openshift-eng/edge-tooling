# Daily Add

Add a task to today's TODO file.

## Arguments

`$ARGUMENTS` — the task description to add. If empty, infer from current context.

## Steps

1. Run `date +%Y-%m-%d` to get today's date, then read `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`. If the file doesn't exist, stop and tell the user.
2. Determine the task description:
   - **If `$ARGUMENTS` is provided:** use it directly. Preserve any URLs or Jira ticket keys inline.
   - **If empty:** infer from the current git branch and recent commits. Confirm with the user before adding.
3. If the description contains a done marker (e.g., `(DONE)`, `(done)`, "mark as done"), strip it and use `- [x]` instead of `- [ ]`.
4. Check for duplicates — if an item with the same Jira key or nearly identical wording already exists, tell the user and stop.
5. Append to the **Backlog** section (or **Completed** if marked done).
6. Show the user what was added, then write the file.

## Rules

- Keep descriptions concise — match the existing style in the file.
- Preserve the file's section structure exactly.
