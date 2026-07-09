# TODO File Format

Format for daily TODO files stored in `.daily/YYYY/MM/YYYY-MM-DD.md`.

## Structure

```markdown
# TODO - YYYY-MM-DD

## Priority
- [ ] High priority item

## In Progress
- [ ] Item currently being worked on

## Waiting on Review
- [ ] PR opened, waiting for others to review

## Review Requests
- [ ] PR someone else opened, waiting for you to review it

## Completed
- [x] Completed item
- [x] TICKET-123: Completed with ticket reference

## Backlog
- [ ] Future items

## Key Issues Tracked
- TICKET-456: Issue being monitored
```

## File Location

Files stored in `.daily/YYYY/MM/` directory (git-ignored).

Example: `.daily/2026/04/2026-04-17.md`

## Item Links

Any task item (`- [ ]` or `- [x]`) may carry indented sub-lines with links or notes relevant to completing it — PRs, docs, Jira issues, build results, Slack threads. Sub-lines are optional; items without them work exactly as before.

```markdown
- [ ] OCPEDGE-2510: Investigate TNF deployment issue
  - PR: https://github.com/openshift/installer/pull/1234
  - Doc: https://docs.openshift.com/container-platform/4.18/installing/
  - Jira: https://redhat.atlassian.net/browse/OCPEDGE-2510
  - Note: Reproduces on 4.18 nightly only, needs metal cluster

- [x] TICKET-123: Completed with ticket reference
  - PR: https://github.com/openshift-eng/edge-tooling/pull/42
```

### Supported Link Types

| Type | Value | Auto-classified from |
|------|-------|----------------------|
| `PR` | GitHub pull/issue URL | `github.com/*/pull/*`, `github.com/*/issues/*` |
| `Jira` | Jira issue URL | `*.atlassian.net/browse/*` |
| `Doc` | Documentation URL | `docs.*`, everything else without a more specific match |
| `Build` | CI/build URL | `prow.ci.openshift.org`, `ci.*` |
| `Slack` | Slack thread/message URL | `*.slack.com/*` |
| `Note` | Free text, no URL | N/A — plain context for the task |

If the value has no URL, treat it as `Note`. When a value has a URL whose type can't be determined, ask the user or default to `Doc`.

### Rules

- Sub-lines are indented two spaces under their parent item.
- Preserve all sub-lines when moving, checking off, or carrying over an item.
- Auto-generate a `Jira` sub-line when an item's description contains a bare ticket key (e.g., `OCPEDGE-2510`) but no explicit Jira link exists.
- Avoid duplicate sub-lines — don't add a link that's already present under an item (same type and URL).
