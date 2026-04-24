---
name: github:pr-queue
description: "List actionable open PRs in a GitHub repo, excluding drafts, WIP, and held PRs by default"
argument-hint: "<owner/repo> [<owner/repo> ...] [--all]"
user-invocable: true
allowed-tools: Bash
---

# github:pr-queue

List open pull requests in a GitHub repository that are ready for attention.
Drafts, WIP, and held PRs are excluded by default.

## Prerequisites

- `gh` CLI authenticated with access to the target repository
- `jq` available on PATH

## Steps

1. Run the pr-queue script:

   ```bash
   bash "${PLUGIN_DIR}/skills/pr-queue/pr-queue.sh" ${ARGUMENTS}
   ```

2. If the script exits non-zero, report the error to the user.
3. Present the script output to the user.
