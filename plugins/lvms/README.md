# lvms

LVMS (Logical Volume Manager Storage) release, QE, and operational workflows.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install lvms
```

## Skills

| Skill | Description |
|---|---|
| `/lvms:check-release-readiness` | Verify branches, dependencies, and configuration for an LVMS release |
| `/lvms:z-stream-report` | Generate z-stream release urgency report for all supported versions |
| `/lvms:setup-prereq` | Set up prerequisites to test unreleased LVMS operator builds |

## Usage

### Release readiness

```text
/lvms:check-release-readiness --version 4.21 --k8s 1.34
```

### Z-stream urgency

```text
/lvms:z-stream-report
```

### Set up prereqs for unreleased builds

```text
/lvms:setup-prereq connected
/lvms:setup-prereq disconnected
```

## Requirements

- `oc` CLI (authenticated with cluster-admin)
- `gh` CLI (authenticated with GitHub access)
- `oc-mirror` (for disconnected setup workflow)
- `skopeo` (for z-stream report registry queries)
- Jira credentials (`JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`) for z-stream report
- **Category:** operator

## Author

sakbas
