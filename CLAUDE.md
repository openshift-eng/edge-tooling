# CLAUDE.md

## Repository Overview

Edge Tooling is a multi-tool deployment and development toolkit for OpenShift and edge computing environments. The repository provides automation for deploying OpenShift clusters in various configurations, managing cloud infrastructure, and setting up development workspaces.

## Tool Quick Reference

| Component | Path | Purpose |
|-----------|------|---------|
| Two-Node Toolbox | `two-node-toolbox/` | OpenShift two-node cluster deployment (arbiter/fencing topologies) |
| EC2 Deploy | `ec2-deploy/` | Standalone EC2 instance setup for development |
| SNO Deploy | `sno-deploy/` | Single Node OpenShift with DU configuration |
| Payload Monitor | `payload-monitor/` | Nightly payload health monitoring for edge topologies (SNO/TNA/TNF) |
| LVM Operator Environment | `environments/lvm-operator/` | Development workspace template for LVMS |
| Plugin Marketplace | `plugins/` | Claude Code plugin marketplace for OpenShift/edge workflows |

**Use case routing:**

- Two-node HA cluster → Two-Node Toolbox
- EC2 dev host → EC2 Deploy (often used as hypervisor for Two-Node Toolbox)
- Single-node OpenShift → SNO Deploy
- LVM Operator development → LVM Operator Environment
- Monitor nightly payload health for edge topologies → Use Payload Monitor
- Claude Code plugins for OpenShift/edge → Plugin Marketplace (`/plugin marketplace add openshift-eng/edge-tooling`)

For commands, flags, prerequisites, and workflows: read the component's README.md or Makefile.

## Contributing

PRs use the fork model: push to `fork` remote, open PR against `origin` (`openshift-eng/edge-tooling`).

Run `npx markdownlint-cli2 '**/*.md'` before committing to catch lint violations.

## Detailed Guides

- [Common Workflows](docs/claude/workflows.md) — EC2, SNO, and LVM deployment steps
- [Prerequisites](docs/claude/prerequisites.md) — Required credentials and tools
- [Daily Report Validation](docs/claude/daily-reports.md) — Slack report format rules and validation
- [Maintaining This Documentation](docs/claude/maintenance.md) — Hook configuration for submodules and new tool detection
- [MicroShift CI Plugin](docs/claude/microshift-ci.md) — CI triage workflow and skill reference

## Additional Resources

For detailed component-specific guidance, see:

- `two-node-toolbox/CLAUDE.md` - Full development guidelines, coding standards, architecture details
- `environments/lvm-operator/CLAUDE.md` - LVM Operator workspace navigation, Konflux build chain
- Component README files in each directory
