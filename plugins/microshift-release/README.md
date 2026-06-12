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
| `/microshift-release:pre-check` | Pre-Check (Phase 0) | Evaluate whether MicroShift should participate in an OCP release (z-stream, nightly, EC/RC) |
| `/microshift-release:release-versions` | Pre-Check (Phase 0) | Check if a MicroShift version is available and where to find RPMs, bootc images, and Brew builds |
| `/microshift-release:validate-artifacts` | Build Validation (Phase 1) | Validate MicroShift built artifacts (RPMs and bootc images) produced by ART |
| `/microshift-release:automated-testing` | Automated Testing (Phase 2) | Run the full Prow CI release testing workflow — create PR, trigger jobs, check status, download and upload artifacts |

## Roadmap

| Phase | Skill | Status |
|---|---|---|
| Pre-Check (Phase 0) | `pre-check` | Done |
| Pre-Check (Phase 0) | `release-versions` | Done |
| Build Validation (Phase 1) | `validate-artifacts` | Done |
| Automated Testing (Phase 2) | `automated-testing` | Done |
| Advisory Promotion (Phase 3) | `advisory-promotion` | Planned |
| Post-Release (Phase 4) | `post-release` | Planned |

## Requirements

- VPN (for Brew RPM checks, advisory reports)
- `GITLAB_API_TOKEN` (optional, for 4.20+ bootc shipment MR checks)
- Atlassian MCP server (for ART ticket queries and OCPBUGS lookups via OAuth)
- `gh` CLI (for PR operations in automated testing)
- `aws` CLI (for S3 build cache and artifact upload)
- `gsutil` CLI (for GCS artifact download)
- Python 3 with `requests` and `pyyaml`
- **Category:** ci-cd

## Author

agullon
