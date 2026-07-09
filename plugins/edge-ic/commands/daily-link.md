---
description: Add a PR, doc, Jira, or note link to a task in today's TODO file
argument-hint: "<Jira key | task text | item N> [Type] <url | note text>"
allowed-tools:
  - Read
  - Write
  - Bash
  - AskUserQuestion
---

# Daily Link

Add a link (PR, doc, Jira, or note) to a task in today's TODO file.

## Arguments

`$ARGUMENTS` — the task identifier followed by the link. Examples:

- `OCPEDGE-2510 PR https://github.com/openshift/installer/pull/1234`
- `TNF deploy Doc https://docs.openshift.com/container-platform/4.18/installing/`
- `item 2 Note: needs metal cluster to reproduce`

If the link type prefix (`PR`, `Doc`, `Jira`, `Build`, `Slack`, `Note`) is omitted, infer it from the URL per `plugins/edge-ic/references/TODO_FILE_FORMAT.md` (e.g., `github.com/*/pull/*` → PR, `*.atlassian.net/browse/*` → Jira). If it can't be inferred and there's no URL, treat the remaining text as a `Note`.

## Steps

1. Run `date +%Y-%m-%d` to get today's date, then read `$HOME/.daily/YYYY/MM/YYYY-MM-DD.md`. If the file doesn't exist, stop and tell the user.
2. Parse `$ARGUMENTS` into a task identifier and a link (type + value).
3. Find the matching task (same matching approach as `daily-done`: Jira key, then substring, then positional). If zero or multiple matches, resolve the same way `daily-done` does.
4. Determine the link type: use the explicit prefix if given, otherwise auto-classify the URL. If there's no URL and no prefix, treat the remaining text as a `Note`; if a URL is present but its type can't be classified, ask the user.
5. Check the item's existing sub-lines for a duplicate (same type and value). If found, tell the user it's already there and stop.
6. Append a new indented sub-line under the matched item: `- Type: value` (indented two spaces beneath the parent item).
7. Show the user what was added, then write the file.

## Rules

- Preserve the file's section structure and all other sub-lines exactly.
- Don't add a link to the wrong item — if the match is ambiguous, ask before writing.
