# GitHub Plugin

GitHub workflow skills for PR summaries and cross-repo reporting.

## Skills

| Skill | Invocation | Description |
|-------|-----------|-------------|
| get-prs | `/github:get-prs <org/repo [...]>` | Fetch open PRs as structured JSON |

## Commands

| Command | Invocation | Description |
|---------|-----------|-------------|
| get-edge-tooling-prs | `/github:get-edge-tooling-prs` | Formatted PR list for edge-tooling, grouped by age |

## Usage

### Raw JSON for any repo

```text
/github:get-prs openshift-eng/edge-tooling
/github:get-prs openshift-eng/edge-tooling openshift/microshift
```

### Formatted edge-tooling PR summary

```text
/github:get-edge-tooling-prs
```

## Requirements

- `gh` CLI authenticated with access to target repositories
- `jq` available on PATH

## Author

brandisher
