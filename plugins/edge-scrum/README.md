# Edge Scrum Plugin

Agents, skills, and workflows for scrum process management on the OpenShift Edge team.

## Reference Documents

**[`Edge-Scrum-Laws.md`](references/Edge-Scrum-Laws.md)** — The authoritative governance document for the OCPEDGE unified scrum. Skills and agents load specific law files from [`references/laws/`](references/laws/) at runtime. It defines:

- **Team roster** — 18 team members with Jira usernames and per-sprint SP targets
- **Jira projects** — OCPEDGE, OCPBUGS, OCPSTRAT, USHIFT and their roles
- **Issue types and sizing** — Story/Spike/Task/Bug (fibonacci SP), Epic/Feature/Initiative (T-shirt sizes)
- **Workflow states** — per issue type, including the OCPBUGS bug lifecycle
- **Sprint policies** — 8 SP/person target, churn rules, bug handling
- **Bug triage process** — severity, priority, target versions, PR title format
- **Epic and feature refinement** — required fields, description template, SME responsibilities
- **Key roles** — SME, Epic Assignee, Scrum Master, Team Lead, Payload Manager

## Components

### MCP Servers

**`mcp-atlassian`** — Jira integration via the `mcp-atlassian` container server.

Connects Claude to the Red Hat Jira instance (`redhat.atlassian.net`) so skills and agents can query issues, sprints, epics, and project metadata without leaving the CLI.

**Required environment variables:**

*Note:* There are likely multiple locations that these need to be set (ex: `.bashrc`, `.zshrc`, `.profile`)

| Variable | Description |
|----------|-------------|
| `JIRA_USERNAME` | Your Red Hat Jira username (email) |
| `JIRA_API_TOKEN` | Jira API token |

The server runs via Podman (`ghcr.io/sooperset/mcp-atlassian:latest`) and is configured in [`.mcp.json`](.mcp.json).

---

### Skills

#### `release-health`

Analyzes the health of an OCP release cycle. Traverses the full Jira hierarchy — Features/Initiatives (OCPSTRAT) → Epics (OCPEDGE) → Stories/Tasks/Bugs — and produces a structured report with risk assessment, refinement gaps, sprint forecasting, and prioritized actions.

**Usage:**

```shell
/release-health [version] [sprint-range] [bc:branch-cut-sprint]
```

| Example | Description |
|---------|-------------|
| `/release-health` | Interactive mode — prompts for all parameters |
| `/release-health 4.19 281-285 bc:285` | Analyze OCP 4.19, sprints 281–285, branch cut at sprint 285 |
| `/release-health 4.20` | Prompts for sprint range |

**What it produces:**

1. **Executive Summary** — overall release health at a glance
2. **Release Dashboard** — one-line status per Feature/Initiative
3. **Feature/Initiative Detail** — per-feature breakdown with Epic rollups and action items
4. **Epic Detail** — issue-level view for active or at-risk epics
5. **Risk Register** — all risks sorted by severity (schedule, staffing, refinement, blocked work)
6. **Refinement Backlog** — issues needing grooming
7. **Sprint Forecast** — velocity-based projection through branch cut
8. **Recommended Actions** — prioritized, owner-assigned

Output is saved to `.reports/release_health_{version}_{YYYY-MM-DD}.md`.

See [`skills/release-health/README.md`](skills/release-health/README.md) for full usage details.
