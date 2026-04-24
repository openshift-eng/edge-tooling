# gh-notifier

Python script that lists **open pull requests** in configured GitHub repos, keeps only PRs whose **authors** appear in the team roster derived from **`OWNERS`** and **`OWNERS_ALIASES`**, applies **draft / label** filters, flags PRs that **need attention** (stale activity, age, optional label rules), and writes a **self-contained HTML dashboard** with Slack **copy** helpers. If **`SLACK_WEBHOOK_URL`** is set, it also posts the same payload to Slack automatically.

There are **no third-party packages** (stdlib only: `urllib`, `json`, etc.).

Design background and operational expectations: [`../proposals/gh-notifier.md`](../proposals/gh-notifier.md).

## Requirements

- **Python 3.11+** (recommended; matches other tooling in this repository)
- A **GitHub token** with permission to **read** pull requests on the repos you configure (`GITHUB_TOKEN`)
- Optional: **Slack Incoming Webhook** URL for automatic channel posts (`SLACK_WEBHOOK_URL`)

## How it works (short)

1. Loads **`OWNERS`** and **`OWNERS_ALIASES`** from the **edge-tooling** repo root by default (the directory above `gh-notifier/`), unless you override paths with env vars.
2. Collects every raw entry under **`approvers:`** and **`reviewers:`** in `OWNERS`. Each entry is either an **alias** name defined under `aliases:` in `OWNERS_ALIASES` (expanded to GitHub logins) or treated as a **literal GitHub login**.
3. For each configured repo, fetches **open** PRs from the GitHub API, keeps **non-draft** PRs whose author is in that login set and that do not carry **excluded** labels.
4. Marks PRs **needing attention** when they are idle or old enough (`STALE_DAYS`), or when optional **required** / **forbidden** label rules fail.
5. Builds **Slack Block Kit** payloads, then writes **`GH_NOTIFIER_HTML_OUTPUT`** (default: `gh-notifier/pr-dashboard.html` under the repo root). Attention PRs are sorted by **longest open** first (`age_days` desc, then idle). The **Slack webhook** uses at most **`MAX_PRS_IN_MESSAGE`** rows (see script); the HTML **copy** area lists **every** attention PR and rewrites PR links from Slack angle-bracket form to CommonMark `[title](url)` for easier paste into docs, GitHub, or Slack. The **JSON** copy area matches the capped webhook body. The page includes metrics, a PR table (or an “all clear” state), and **Copy text** / **Copy JSON** buttons.
6. If **`SLACK_WEBHOOK_URL`** is set, POSTs the **capped** payload to Slack as before.

## Run locally

From the **edge-tooling** repository root (so the default `OWNERS` paths resolve):

```bash
export GITHUB_TOKEN="ghp_..."           # required
export SLACK_WEBHOOK_URL="https://..."  # optional automatic Slack post

python3 gh-notifier/gh-notifier.py
open gh-notifier/pr-dashboard.html      # macOS: view the dashboard
```

Stdout prints a one-line summary and the **absolute path** to the HTML file. Run from another working directory only if you set **`OWNERS_FILE`** and **`OWNERS_ALIASES_FILE`** to absolute paths inside a checkout of this repo.

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_TOKEN` | yes | — | Bearer token for `api.github.com`, we will be using the same token as the microshift ci-doctor |
| `SLACK_WEBHOOK_URL` | no | empty | If set, POSTs the Block Kit payload to Slack after writing HTML |
| `PROW_JOB_URL` | no | empty | If set (e.g. `PROW_JOB_URL` from Prow), only the **Slack** message includes a link under the header so users can open the job for the full PR list and logs; HTML / copy-paste mrkdwn are unchanged |
| `GH_NOTIFIER_HTML_OUTPUT` | no | `<repo>/gh-notifier/pr-dashboard.html` | Path to the generated dashboard (parent dirs are created if needed) |
| `OWNERS_FILE` | no | `<repo>/OWNERS` | Path to `OWNERS` (Kubernetes-style `approvers` / `reviewers`) |
| `OWNERS_ALIASES_FILE` | no | `<repo>/OWNERS_ALIASES` | Path to `OWNERS_ALIASES` (`aliases:` block) |
| `GITHUB_REPOS` | no | `openshift-eng/edge-tooling` | Comma- or whitespace-separated `org/repo` list |
| `STALE_DAYS` | no | `7` | Days of inactivity / age used for “needs attention” |
| `EXCLUDE_LABELS` | no | hold / WIP-style defaults | Comma or semicolon separated; PRs with any of these (case-insensitive) are skipped |
| `REQUIRED_LABELS` | no | empty | If set, PRs missing any of these labels are flagged |
| `FORBIDDEN_LABELS` | no | empty | If set, PRs carrying any of these labels are flagged |

`GITHUB_TOKEN`, `SLACK_WEBHOOK_URL`, and string list env vars are read **once at process start** (together with repo and label settings). `OWNERS` paths and the HTML output path are resolved in `main()`.

## Slack payload

The webhook message uses Slack **Block Kit** (header, context, sections). The number of PRs listed in one message is capped (`MAX_PRS_IN_MESSAGE` in the script). With **`PROW_JOB_URL`**, a Prow link section is added at the top of the webhook payload only. The HTML page exposes full-list **mrkdwn** (for pasting) and capped **JSON** matching the webhook (for replay or debugging).

## CI (openshift/release)

The intended integration is a **weekday periodic** job in **openshift/release** that checks out **edge-tooling** and runs this script with `GITHUB_TOKEN` (and optionally `SLACK_WEBHOOK_URL`) from CI secrets. Publish **`GH_NOTIFIER_HTML_OUTPUT`** as a job artifact so reviewers can open the dashboard in a browser. Team membership stays in **`OWNERS`** / **`OWNERS_ALIASES`** in this repository, not duplicated in release.
