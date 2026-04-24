# GitHub Plugin

GitHub workflow automation skills for Claude Code.

## Skills

### apply-tide-squash-label

Labels a GitHub PR for squash merging by posting a `/label tide/merge-method-squash` comment.

**Usage:** `/github:apply-tide-squash-label <PR URL>`

### pr-queue

Lists actionable open PRs in a GitHub repository, excluding drafts, WIP, and held PRs by default.

**Usage:** `/github:pr-queue <owner/repo> [<owner/repo> ...] [--all]`

## Prerequisites

- `gh` CLI authenticated with access to the target repository
