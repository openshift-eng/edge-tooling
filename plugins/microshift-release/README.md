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

## Scripts

All scripts live under `scripts/` and are invoked by the skills above. They can also be run standalone via bash.

### Entry Points

| Script | Description | Usage |
|---|---|---|
| `precheck.sh` | Dispatcher for pre-check subcommands | `bash scripts/precheck.sh <xyz\|nightly\|ecrc\|enrich> [args...]` |
| `prow_testing.sh` | Dispatcher for Prow CI release testing | `bash scripts/prow_testing.sh <action> <version> [--execute]` |
| `validate.sh` | Dispatcher for artifact validation | `bash scripts/validate.sh <version> [--verbose] [--json]` |
| `run_unit_tests.sh` | Run unit tests for the library and scripts | `bash scripts/run_unit_tests.sh` |

### Pre-Check Scripts (`precheck.sh`)

| Subcommand | Script | Description |
|---|---|---|
| `xyz` | `precheck_xyz.py` | X/Y/Z release evaluation — checks lifecycle, OCP payload, advisory CVEs, OCPBUGS, code changes, and the 90-day rule |
| `nightly` | `precheck_nightly.py` | Nightly build gap detection — compares Brew nightly RPMs against OCP accepted nightlies |
| `ecrc` | `precheck_ecrc.py` | EC/RC discovery — finds the latest EC or RC from the OCP release controller and verifies Brew RPMs |
| `enrich` | `enrich_ocpbugs.py` | OCPBUGS enrichment — reads JSON from stdin and classifies bugs by release action |

### Prow CI Testing Script (`prow_testing.sh`)

| Action | Description |
|---|---|
| `preflight` | Verify RPMs exist in the S3 build cache before creating a PR |
| `create-pr` | Create a draft PR with an empty commit for release testing |
| `trigger` | Post `/test` comments to trigger failed or not-started CI jobs |
| `status` | Check and display CI job statuses |
| `scenarios` | Validate test scenarios in completed jobs (check for skips and version mismatches) |
| `download` | Download job artifacts from GCS |
| `upload` | Compress artifacts into tar.gz and upload to S3 |
| `complete` | Post completion comment, close PR, and delete branch |

Mutating actions (`create-pr`, `trigger`, `download`, `upload`, `complete`) run in dry-run mode by default — pass `--execute` to apply changes.

### Artifact Validation Script (`validate.sh`)

Runs RPM and bootc image checks for all release types (GA, Z-stream, RC, EC, nightly). See the `validate-artifacts` skill for the full list of checks.

## Library (`scripts/lib/`)

Shared Python modules used by the scripts above.

| Module | Description |
|---|---|
| `art_jira.py` | ART Jira queries for release schedule (reads pre-fetched JSON via `ART_TICKETS_JSON` env var) |
| `artifacts.py` | Artifact validation helpers — NVR format validation, commit provenance, mirror checks, shipment MR, bootc SHA matching |
| `brew.py` | Brew (brewweb) scraping for RPM builds, VPN connectivity checks, nightly build lookups |
| `git_ops.py` | Git operations — clone/fetch MicroShift repo, commit counting, tag lookups, release branch listing |
| `lifecycle.py` | Red Hat Product Lifecycle API client — checks support phase and end-of-life dates |
| `ocpbugs.py` | OCPBUGS commit scanning — extracts bug references from git commit messages on release branches |
| `prow.py` | Prow CI and GCS operations — version parsing, PR lookup, job listing, parallel status fetching |
| `pyxis.py` | Red Hat Catalog (Pyxis) API client — checks published MicroShift versions and bootc images |
| `release_controller.py` | OCP Release Controller API client — payload status, nightly lookups, EC/RC discovery |

## Unit Tests

```bash
bash scripts/run_unit_tests.sh
```

Tests cover:

- `test_logic.py` — Pure logic functions: recommendation engine, CVE interpretation, gap classification, text formatting
- `test_prow.py` — Prow library: version parsing, NVR normalization

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
