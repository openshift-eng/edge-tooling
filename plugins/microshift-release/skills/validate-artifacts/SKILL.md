---
name: microshift-release:validate-artifacts
argument-hint: <version> [--verbose]
description: Validate MicroShift built artifacts (RPMs and bootc images) produced by ART for a release
user-invocable: true
allowed-tools: Bash
---

# microshift-release:validate-artifacts

## Synopsis

```bash
/microshift-release:validate-artifacts <version> [--verbose]
```

## Description

Phase 1 of the MicroShift release process: verify that ART produced correct RPMs and bootc images for a given release. Checks Brew builds, NVR format, commit provenance, RHEL variants, mirror availability, shipment MR, stage catalog, and bootc image SHA consistency.

Supports all release types: X/Y (GA), Z (z-stream), RC, EC, and nightly.

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| VPN | Brew RPM checks, git commit verification | Yes — Brew checks WARN and degrade without it |
| `GITLAB_API_TOKEN` | Shipment MR checks (`bootc_shipment_*`) | No — bootc MR checks WARN and skip without it |
| Internet (mirrors) | `rpm_mirror_*`, `bootc_mirror_*` | Yes for RC/EC mirror checks |

## Arguments

- `version` (required): Full version string
  - Z-stream: `4.21.8`
  - X/Y (GA): `4.22.0`
  - RC: `4.22.0-rc.2`
  - EC: `4.22.0-ec.5`
  - nightly: `4.21.0-0.nightly-2026-03-23-021947`
- `--verbose` (optional): Show detailed markdown report with evidence per check

## Scripts Directory

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

### Step 1: Parse Arguments

1. Extract `version` from `$ARGUMENTS` — the first non-flag token
2. Pass through `--verbose` and `--json` flags if present

### Step 2: Run the Script

```bash
bash $SCRIPTS_DIR/validate.sh <version> [--verbose]
```

Display stderr only if the script exits non-zero.

### Step 3: Display Output

Display output **verbatim** — do not reformat, summarize, or add commentary. The script produces deterministic pre-formatted text.

### Step 4: Handle Errors

If the script exits non-zero:

- **VPN errors**: Connect to VPN (Brew and git operations require it)
- **Missing GITLAB_API_TOKEN**: `export GITLAB_API_TOKEN=<token>` for shipment MR checks
- **HTTP errors on mirrors**: Mirrors may not be populated yet — check with ART

## Checks Performed

### RPM Checks (all release types)

| Check | Description |
|---|---|
| `rpm_packages_list` | All expected RPM packages present in Brew build |
| `rpm_filename_format` | NVR matches expected pattern for this release type |
| `rpm_commit_id` | Commit hash from NVR is on the correct `release-X.Y` branch |
| `rpm_rhel_version` | Both el9 and el10 builds are present |
| `rpm_mirror_ec` | EC only: RPMs available at `mirror.openshift.com/ocp-dev-preview/` |
| `rpm_mirror_rc` | RC only: RPMs available at `mirror.openshift.com/ocp/` |
| `rpm_xy0_commit_match` | X.Y.0 only: commit matches the latest RC commit |

### Bootc Checks (4.18+, not nightly)

| Check | Description |
|---|---|
| `bootc_shipment_mr` | Shipment MR exists in `ocp-shipment-data` GitLab repo |
| `bootc_shipment_yaml_count` | Exactly 1 YAML file in the MR |
| `bootc_shipment_xy0_type` | X/Y.0 only: `spec.type == RHEA` |
| `bootc_shipment_xy0_release_notes` | X/Y.0 only: `releaseNotes.solution` URL present |
| `bootc_stage_advisory_url` | `environments.stage.advisory.internal_url` present |
| `bootc_catalog` | Image published in catalog (stage or prod) |
| `bootc_prod_xy0_type` | X/Y.0 only: `releaseNotes.type == RHEA` |
| `bootc_prod_advisory_url` | X/Y.0 only: `environments.prod.advisory.internal_url` present |
| `bootc_image_sha_match` | RC/EC only: pullspec SHA matches advisory YAML SHA |
| `bootc_mirror_ec` | EC only: `bootc-pullspec.txt` at mirror/ocp-dev-preview/ |
| `bootc_mirror_rc` | RC only: `bootc-pullspec.txt` at mirror/ocp/ |

## Output Format

**Short (default):** Only actionable checks are shown. Skipped checks (not applicable
for the release type) are hidden and summarized as a count. Details are shown only on failure.

```text
Validating 4.21.8

── RPM ──────────────────────────────────────────────────────
✅  rpm_packages_list       All 7 expected packages found in Brew
✅  rpm_filename_format     NVR matches Z pattern
✅  rpm_commit_id           Commit abc1234 from 2026-05-08 is on release-4.21
✅  rpm_rhel_version        el9 and el10 builds present

── Bootc ────────────────────────────────────────────────────
✅  bootc_shipment_mr       MR !1234: microshift 4.21.8 shipment
✅  bootc_shipment_yaml_count  1 YAML file in MR
✅  bootc_catalog           Image found in prod catalog

(6 checks skipped — not applicable)
```

On failure, details appear below the failing check:

```text
❌  rpm_packages_list       2 package(s) missing from Brew build
                            Missing: microshift-libs, microshift-selinux
                            Expected: microshift, microshift-libs, ...
```

**Verbose (--verbose):** Markdown table with full evidence per check (all checks shown including skips).

## Examples

```bash
/microshift-release:validate-artifacts 4.21.8              # Z-stream
/microshift-release:validate-artifacts 4.22.0              # X/Y GA
/microshift-release:validate-artifacts 4.22.0-rc.2         # Release Candidate
/microshift-release:validate-artifacts 4.22.0-ec.5         # Engineering Candidate
/microshift-release:validate-artifacts 4.21.8 --verbose    # detailed report
```

## Notes

- Read-only — does NOT create tickets, advisories, or modify external state. No confirmation required.
- VPN is required for Brew queries and git commit verification on internal repos
- GITLAB_API_TOKEN enables shipment MR checks; without it those checks show WARN
- Bootc checks are skipped for versions below 4.18 and for nightly builds
- Exit code is non-zero if any check returns FAIL
