---
name: github:pr-queue
description: "List actionable open PRs in a GitHub repo, excluding drafts, WIP, and held PRs by default"
argument-hint: "<owner/repo> [--all]"
user-invocable: true
allowed-tools: Bash
---

# github:pr-queue

List open pull requests in a GitHub repository that are ready for attention. By default, drafts, WIP, and held PRs are excluded.

## Prerequisites

- `gh` CLI authenticated with access to the target repository

## Arguments

- `$ARGUMENTS` must contain `owner/repo` (e.g., `openshift-eng/edge-tooling`)
- Optional `--all` flag includes excluded PRs with an Exclusion column

## Steps

1. Parse `$ARGUMENTS` to extract `owner/repo` and detect the `--all` flag.
2. Validate that `owner/repo` is present. If missing, report usage and stop.
3. Fetch all open PRs:

   ```bash
   gh pr list --repo <owner/repo> --state open --limit 200 \
     --json number,title,author,url,isDraft,labels,createdAt
   ```

4. Classify each PR. A PR is **excluded** if any of these are true:
   - `isDraft` is `true` — reason: `draft`
   - Title matches `\bwip\b` (case-insensitive) — reason: `WIP`
   - Has label `do-not-merge/hold` — reason: `hold`
   - Has label `do-not-merge/work-in-progress` — reason: `WIP`

   A PR may have multiple exclusion reasons (e.g., `draft, WIP`).

5. Format output as a Markdown table:

   **Default (no `--all`)** — show only qualifying PRs:

   ```markdown
   **N actionable PRs in `owner/repo`:**

   | Created | PR | Author | Title |
   |---|---|---|---|
   | 2026-04-21 | [#66](url) | @login | PR title |
   ```

   If zero qualifying PRs, report:

   ```markdown
   No actionable PRs in `owner/repo`. N open PRs are excluded (use `--all` to see them).
   ```

   **With `--all`** — show all open PRs, with an Exclusion column:

   ```markdown
   **N open PRs in `owner/repo`** (M actionable, K excluded):

   | Created | PR | Author | Title | Exclusion |
   |---|---|---|---|---|
   | 2026-04-21 | [#66](url) | @login | PR title | hold |
   | 2026-04-20 | [#49](url) | @login | PR title | draft, WIP |
   | 2026-04-18 | [#42](url) | @login | Actionable PR | |
   ```

6. Sort rows by creation date, most recent first.
