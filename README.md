# Edge Tooling

Automation, AI skills, and deployment tools for OpenShift edge engineering. This repo is the OpenShift Edge team's shared toolkit — it provides cluster deployment automation for edge topologies (two-node HA, SNO, LVM), CI and release workflow tooling, and a Claude Code plugin marketplace that gives engineers AI-assisted skills for day-to-day work.

## Which tool do I need?

| Use case | Component |
|----------|-----------|
| Two-node HA cluster (arbiter or fencing) | [two-node-toolbox/](two-node-toolbox/) |
| EC2 dev host or hypervisor | [ec2-deploy/](ec2-deploy/) |
| Single Node OpenShift with DU config | [sno-deploy/](sno-deploy/) |
| LVM Operator development workspace | [environments/lvm-operator/](environments/lvm-operator/) |
| Nightly payload health monitoring | [payload-monitor/](payload-monitor/) |
| Multi-repo development environment | [multi-repo-development/](multi-repo-development/) |
| Claude Code plugins for edge workflows | [plugins/](plugins/) via the marketplace |

## Getting started

### Local setup

```bash
git clone git@github.com:openshift-eng/edge-tooling.git
cd edge-tooling
make setup-githooks
```

`make setup-githooks` installs a pre-commit hook that runs markdownlint on staged `.md` files.

Prerequisites: Node.js (for markdownlint), Python 3, Bash.

Each deployment tool has its own prerequisites — see the component README for what you need.

### Installing plugins

From within Claude Code:

```text
/plugin marketplace add openshift-eng/edge-tooling
```

Then enable whichever plugins you need. See the [plugin README](plugins/README.md) for details.

## Claude Code Plugins

| Plugin | What it does |
|--------|-------------|
| [challenge](plugins/challenge/) | Adversarial hypothesis reviewer for root cause analysis |
| [edge-ic](plugins/edge-ic/) | IC workflow automation — TODOs, status reports, Jira updates |
| [edge-ocp-ci](plugins/edge-ocp-ci/) | Monitor nightly payload health across edge topologies with AI-enriched analysis |
| [edge-ocp-rc](plugins/edge-ocp-rc/) | RC testing for OCP edge topologies (TNF, TNA, SNO) — launch Prow jobs, track results, classify failures |
| [edge-scrum](plugins/edge-scrum/) | Scrum process management — refinement, sprint planning, standups |
| [github](plugins/github/) | GitHub workflow skills — PR automation, labeling, queue management |
| [lvms](plugins/lvms/) | LVMS release, QE, and operational workflows |
| [lvms-ci](plugins/lvms-ci/) | LVMS CI automation — failure triage, Prow job analysis |
| [mcp-atlassian](plugins/mcp-atlassian/) | Atlassian Jira MCP server for Claude Code |
| [microshift-ci](plugins/microshift-ci/) | MicroShift CI failure analysis and JIRA bug creation |
| [microshift-dev](plugins/microshift-dev/) | MicroShift dev tools collection |
| [microshift-release](plugins/microshift-release/) | MicroShift release testing automation |
| [pr-review](plugins/pr-review/) | PR lifecycle toolkit — vet findings, triage CodeRabbit reviews, yolo-agent |
| [skills-review](plugins/skills-review/) | Lint SKILL.md files for quality and correctness |
| [threat-model](plugins/threat-model/) | Security threat analysis for OpenShift PRs |
| [two-node](plugins/two-node/) | Two-node topology workflow automation — RHEL verification, Jira integration |

## Deployment Tools

| Directory | What it does |
|-----------|-------------|
| [two-node-toolbox/](two-node-toolbox/) | Deploy two-node OpenShift clusters (arbiter/fencing topologies) |
| [ec2-deploy/](ec2-deploy/) | Spin up EC2 instances for development and hypervisor use |
| [sno-deploy/](sno-deploy/) | Deploy Single Node OpenShift with DU configuration |
| [payload-monitor/](payload-monitor/) | Nightly payload health monitoring for edge topologies |
| [environments/lvm-operator/](environments/lvm-operator/) | Development workspace for the LVM Storage operator |
| [multi-repo-development/](multi-repo-development/) | Multi-repo development environment for cross-project work |

## Documentation

| Resource | What it covers |
|----------|---------------|
| [CLAUDE.md](CLAUDE.md) | Repo overview, tool routing, component-level guides |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution model, code standards, architectural patterns |
| [docs/claude/workflows.md](docs/claude/workflows.md) | Deployment walkthroughs (EC2 instance to two-node cluster) |
| [docs/claude/prerequisites.md](docs/claude/prerequisites.md) | Required credentials and tools |
| Component READMEs | Per-tool setup, prerequisites, and usage |

## Contributing

PRs use the fork model — push to your fork, open a PR against `main`. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.
