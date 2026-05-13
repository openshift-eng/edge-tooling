# Daily Update

Update today's TODO file based on what was accomplished in this conversation.

## Steps

1. Run `date +%Y-%m-%d` to get today's date, then read `.daily/YYYY/MM/YYYY-MM-DD.md`. If the file doesn't exist, stop and tell the user.
2. Review the conversation for: completed work, progress on existing items, and new tasks discovered.
3. For each outcome:
   - If it matches an existing `- [ ]` item, change it to `- [x]` (keep any ticket reference).
   - If it represents progress but not completion, leave `- [ ]` and append a brief status note only if an equivalent note is not already present.
   - If it's a new task not in the file, append it to the appropriate section (Priority, In Progress, or Backlog) only if no equivalent item already exists.
4. Show the user a short summary of changes (not the full file), then write the updated file.

## Rules

- Never remove existing items.
- Keep descriptions concise — match the existing style in the file.
- Preserve the file's section structure exactly.
- If nothing in the conversation maps to a TODO update, say so and stop.
