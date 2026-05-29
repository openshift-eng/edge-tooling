# microshift-release

Automate MicroShift release testing activities — from pre-release evaluation through build validation, CI verification, advisory promotion, and post-release checks.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install microshift-release
```

## Skills

| Skill | Phase | Description |
|---|---|---|
| `/microshift-release:pre-check` | Pre-Check | Evaluate whether MicroShift should participate in an OCP release (z-stream, nightly, EC/RC) |
| `/microshift-release:release-versions` | Pre-Check | Check if a MicroShift version is available and where to find RPMs, bootc images, and Brew builds |

## Roadmap

Additional skills are planned for each release testing phase:

| Phase | Skill | Status |
|---|---|---|
| Pre-Check (Phase 0) | `pre-check` | Done |
| Build Validation (Phase 1) | `validate-artifacts` | Planned |
| Automated Testing (Phase 2) | `automated-testing` | Planned |
| Advisory Promotion (Phase 3) | `advisory-promotion` | Planned |
| Post-Release (Phase 4) | `post-release` | Planned |

## Requirements

- VPN (for Brew RPM checks, advisory reports)
- `GITLAB_API_TOKEN` (optional, for 4.20+ advisory reports)
- Atlassian MCP server (for OCPBUGS lookups via OAuth)
- Product Pages MCP server (optional, for time range lookups)
- Python 3
- **Category:** ci-cd

## Author

agullon
