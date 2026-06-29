---
name: microshift-release:advisory-promotion
argument-hint: <version> [--prod] [--verbose]
description: Validate Konflux bootc advisory promotion for QE sign-off — verify advisory YAML, catalog presence, shipment MR, and commit provenance
user-invocable: true
allowed-tools: Bash
---

# microshift-release:advisory-promotion

## Synopsis

```bash
/microshift-release:advisory-promotion <version> [--prod] [--verbose]
```

## Description

Phase 3 of the MicroShift release process: verify that a Konflux-built bootc advisory is ready for QE sign-off and shipping. Validates three data sources:

- **advisory.yaml** — image presence, repository, SHA, and advisory type
- **Pyxis catalog** (via GraphQL) — stage and prod catalog presence, assembly tags, commit provenance
- **Shipment MR** — YAML filename, NVR commit vs Brew, release type, MR approval

All per-image checks run independently per variant (arch + RHEL version). For versions 4.22.2+, both el9 and el10 bootc images are checked. Repository names are version-aware (`openshift4` for 4.x, `openshift5` for 5.x).

Supports Z-stream, X/Y GA, RC, and EC release types. Requires version 4.18+.

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| VPN | GitLab API, Brew RPM lookup | Yes — shipment and NVR checks WARN without it |
| `GITLAB_API_TOKEN` | Shipment MR fetch, MR approval check | Yes — shipment checks WARN without it |
| Internet | Pyxis catalog queries (GraphQL) | Yes for catalog checks |

## Arguments

- `version` (required): Full version string (4.18+)
  - Z-stream: `4.20.26`
  - X/Y (GA): `4.22.0`
  - RC: `4.22.0-rc.2`
  - EC: `5.0.0-ec.3`
- `--prod` (optional): Check both stage and prod catalogs (default: stage only, prod checks skipped)
- `--verbose` (optional): Show detailed markdown report with evidence per check

## Scripts Directory

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

### Step 1: Parse Arguments

1. Extract `version` from `$ARGUMENTS` — the first non-flag token
2. Pass through `--prod`, `--verbose`, and `--json` flags if present

### Step 2: Run the Script

```bash
bash $SCRIPTS_DIR/advisory_promotion.sh <version> [--prod] [--verbose]
```

Display stderr only if the script exits non-zero.

### Step 3: Display Output

Display output **verbatim** — do not reformat, summarize, or add commentary. The script produces deterministic pre-formatted text.

### Step 4: Handle Errors

If the script exits non-zero:

- **VPN errors**: Connect to VPN (GitLab API and Brew require it)
- **Missing GITLAB_API_TOKEN**: `export GITLAB_API_TOKEN=<token>` for shipment MR and approval checks
- **Version too low**: Advisory promotion requires 4.18+ (Konflux builds)

## Checks Performed

All per-image checks run independently per variant (`{arch}_el{rhel}`). For versions < 4.22.2 only el9 is checked; for 4.22.2+ both el9 and el10.

### Per-variant checks (`{arch}_el{rhel}_*`)

**From advisory.yaml** (`rhtap-release/advisories/.../advisory.yaml`):

| Check | Description |
|---|---|
| `{v}_advisory_image_present` | Variant is present in `spec.content.images` |
| `{v}_advisory_repository` | Image references the correct `registry.stage.redhat.io/openshift{major}/microshift-bootc-rhel{rhel}` |
| `{v}_advisory_image_sha` | Advisory contains a non-empty image SHA for this variant |

**From Pyxis catalog** (GraphQL API, both stage and prod):

| Check | Description |
|---|---|
| `{v}_catalog_stage_present` | Image found in stage catalog |
| `{v}_catalog_stage_tag_commit` | Assembly tag commit hash matches stage catalog image labels |
| `{v}_catalog_stage_tag_date` | Assembly tag contains a valid build date timestamp (stage) |
| `{v}_catalog_stage_no_xy0_tag` | Z-stream only: no X.Y.0 assembly tag on stage image |
| `{v}_catalog_stage_chi` | Container Health Index grade is A (stage) |
| `{v}_catalog_prod_present` | Image found in prod catalog (skipped in stage mode / EC/RC) |
| `{v}_catalog_prod_tag_commit` | Assembly tag commit hash matches prod catalog image labels |
| `{v}_catalog_prod_tag_date` | Assembly tag contains a valid build date timestamp (prod) |
| `{v}_catalog_prod_no_xy0_tag` | Z-stream only: no X.Y.0 assembly tag on prod image |
| `{v}_catalog_prod_chi` | Container Health Index grade is A (prod) |

### Global checks

**From advisory.yaml:**

| Check | Description |
|---|---|
| `advisory_type` | `spec.type` is RHBA/RHSA (z-stream, EC, RC) or RHEA (X.Y.0) |
| `advisory_sha_distinct_el{rhel}` | amd64 and arm64 SHAs are different per RHEL version |

**From shipment MR** (`ocp-shipment-data` GitLab repo):

| Check | Description |
|---|---|
| `shipment_type` | `releaseNotes.type` matches expected advisory type |
| `shipment_filename` | YAML path matches `shipment/ocp/openshift-{minor}/.../{version}.microshift-bootc.{timestamp}.yaml` |
| `shipment_nvr_commit` | Commit hash in shipment `snapshot.nvrs` matches the Brew RPM build commit |
| `shipment_mr_approved` | Shipment MR has required approvals |

## Output Format

**Short (default):** All checks shown, grouped by variant. Skipped checks use ⏭️.

```text
Advisory Promotion: 4.20.26

── amd64_el9 ───────────────────────────────────────────────
✅  amd64_el9_advisory_image_present       amd64/el9 present
✅  amd64_el9_advisory_repository          registry.stage.redhat.io/openshift4/microshift-bootc-rhel9
✅  amd64_el9_advisory_image_sha           sha256:f839eb91f716
✅  amd64_el9_catalog_stage_present        Found in stage catalog
✅  amd64_el9_catalog_stage_tag_commit     Commit b79e4b0 matches catalog
✅  amd64_el9_catalog_stage_tag_date       2026-06-19 09:02
✅  amd64_el9_catalog_stage_no_xy0_tag     No 4.20.0 tags (7 checked)
✅  amd64_el9_catalog_stage_chi            CHI grade A
⏭️  amd64_el9_catalog_prod_present         N/A (stage mode)
⏭️  amd64_el9_catalog_prod_tag_commit      N/A (prod not queried)
⏭️  amd64_el9_catalog_prod_tag_date        N/A (prod not queried)
⏭️  amd64_el9_catalog_prod_no_xy0_tag      N/A (prod not queried)
⏭️  amd64_el9_catalog_prod_chi             N/A (prod not queried)

── arm64_el9 ───────────────────────────────────────────────
✅  arm64_el9_advisory_image_present       arm64/el9 present
...

── Global ──────────────────────────────────────────────────
✅  advisory_type                          spec.type = RHBA
✅  shipment_type                          releaseNotes.type = RHBA
✅  shipment_filename                      shipment/ocp/openshift-4.20/openshift-4-20/prod/4.20.26.microshift-bootc...yaml
✅  shipment_nvr_commit                    Commit b79e4b0 matches Brew
✅  advisory_sha_distinct_el9              SHAs are distinct
✅  shipment_mr_approved                   MR !594 approved by tlove, knarra, adobes
```

With `--prod`, both stage and prod catalog checks run:

```text
✅  amd64_el9_catalog_stage_present        Found in stage catalog
✅  amd64_el9_catalog_stage_chi            CHI grade A
✅  amd64_el9_catalog_prod_present         Found in prod catalog
✅  amd64_el9_catalog_prod_chi             CHI grade A
```

On failure, details appear below the failing check:

```text
❌  amd64_el9_advisory_repository          Wrong repository: registry.redhat.io/openshift4/microshift-bootc-rhel9
                                           Expected: registry.stage.redhat.io/openshift4/microshift-bootc-rhel9
                                           Got: registry.redhat.io/openshift4/microshift-bootc-rhel9
```

For EC/RC, prod catalog checks are always skipped:

```text
⏭️  amd64_el9_catalog_prod_present         N/A (EC not shipped to prod)
```

**Verbose (--verbose):** Markdown table with full evidence per check, grouped by variant.

## Examples

```bash
/microshift-release:advisory-promotion 4.20.26             # Z-stream (el9 only)
/microshift-release:advisory-promotion 4.22.2              # Z-stream (el9 + el10)
/microshift-release:advisory-promotion 4.22.0              # X/Y GA
/microshift-release:advisory-promotion 5.0.0-ec.3          # Engineering Candidate
/microshift-release:advisory-promotion 4.22.0-rc.2         # Release Candidate
/microshift-release:advisory-promotion 4.20.26 --verbose   # detailed report
```

## Notes

- Read-only — does NOT modify advisories, tickets, or external state. No confirmation required.
- VPN is required for GitLab API access and Brew RPM commit comparison
- GITLAB_API_TOKEN enables shipment MR checks; without it those checks show WARN
- Catalog checks use the Pyxis GraphQL API (works for both stage and prod)
- Default mode is stage — prod catalog checks are skipped. Use `--prod` to check both catalogs
- For EC/RC, `catalog_prod_present` is always skipped (not shipped to prod)
- For versions 4.22.2+, el10 bootc images are also checked
- Repository names are version-aware: `openshift4` for 4.x, `openshift5` for 5.x
- Only supports versions 4.18+ (Konflux bootc builds)
- Nightly builds are skipped (no advisories)
- Exit code is non-zero if any check returns FAIL
