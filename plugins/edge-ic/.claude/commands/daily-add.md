# Daily Add

Add a task to today's TODO file.

## Arguments

`$ARGUMENTS` — the task description to add.

## Steps

1. Run `date +%Y-%m-%d` to get today's date, then read `.daily/YYYY/MM/YYYY-MM-DD.md`. If the file doesn't exist, stop and tell the user.
2. Parse the argument as a task description. If it contains a URL, include it inline. If a Jira ticket key is present (e.g., `OCPEDGE-123`), preserve it as a prefix.
3. Check for duplicates — if an equivalent item already exists, tell the user and stop.
4. Append `- [ ] <description>` to the **Backlog** section.
5. Show the user what was added, then write the file.

## Rules

- Keep descriptions concise — match the existing style in the file.
- Preserve the file's section structure exactly.
