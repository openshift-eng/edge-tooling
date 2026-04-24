<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# enhancements — TNF Context

**Category**: Docs

**Purpose**: OpenShift enhancement proposals repository

**TNF Relevance**: Contains the authoritative TNF enhancement document defining:
- Architecture and workflow for two-node clusters with fencing
- Component changes required (CEO, MCO, installer, BMO, etc.)
- Installation flow via Assisted Installer and Agent-Based Installer
- Failure handling scenarios and recovery procedures
- Integration between RHEL-HA stack and OpenShift

**When to use this repo**:
- Understanding design rationale and architectural decisions
- Answering "why" questions about TNF behavior
- Referencing the original design spec for component responsibilities

**Key files**:
- `enhancements/two-node-fencing/tnf.md` - Main enhancement document
- `enhancements/two-node-fencing/etcd-flowchart-both-nodes-reboot-scenarios.svg` - Reboot scenarios flowchart
- `enhancements/two-node-fencing/etcd-flowchart-gns-nogns-happy-paths.svg` - Happy path flowchart (GNS/non-GNS)

**Enhancement proposal structure**: Documents follow a standard format with sections for Summary, Motivation, Goals, Non-Goals, Proposal, Design Details, Risks, and Alternatives. Navigate using these headings to find specific information.
