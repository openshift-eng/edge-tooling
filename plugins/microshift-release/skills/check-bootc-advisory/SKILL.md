---
name: microshift-release:check-bootc-advisory
argument-hint: <version> [-s | -p | -s -p] [--json]
description: Step 3 — check the bootc image advisory for QE sign-off before shipment
user-invocable: true
allowed-tools: Bash
---

# microshift-release:check-bootc-advisory

## Synopsis

```bash
/microshift-release:check-bootc-advisory <version> [-s | -p | -s -p] [--json]
```

## Description

Step 3 of the MicroShift release process: verify that the Konflux-built bootc
image advisory is ready for QE sign-off and shipping. For the RPM advisory in the
Errata Tool, use `/microshift-release:check-rpm-errata` (step 4).

Validates three data sources:

- **advisory.yaml** — image presence, repository, SHA, and advisory type
- **Pyxis catalog** (via GraphQL) — stage and prod catalog presence, assembly tags, commit provenance
- **Shipment MR** — YAML filename, NVR commit vs Brew, release type, MR approval

All per-image checks run independently per variant (arch + RHEL version). For versions 4.22.2+, both el9 and el10 bootc images are checked. Repository names are version-aware (`openshift4` for 4.x, `openshift5` for 5.x).

Supports Z-stream, X/Y GA, RC, and EC release types. Requires version 4.18+.

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| VPN | GitLab API, Brew | Yes |
| `GITLAB_API_TOKEN` | Shipment MR, approval check | Yes |
| Internet | Pyxis catalog queries (GraphQL) | Yes |

## Arguments

- `version` (required): Full version string (4.18+)
  - Z-stream: `4.20.26`
  - X/Y (GA): `4.22.0`
  - RC: `4.22.0-rc.2`
  - EC: `5.0.0-ec.3`
- `-s`/`--stage` (optional): Check stage catalog and shipment errata (common checks always included)
- `-p`/`--prod` (optional): Check prod catalog and shipment errata (common checks always included)
- `--json` (optional): Output raw JSON

If neither `--stage` nor `--prod` is provided, both are checked by default. Either can be passed alone to scope the run.

## Scripts Directory

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

### Step 1: Parse Arguments

1. Extract `version` from `$ARGUMENTS` — the first non-flag token
2. Pass through `-s`/`--stage`, `-p`/`--prod`, and `--json` flags if present

### Step 2: Run Bootc Checks

```bash
bash $SCRIPTS_DIR/check_bootc-advisory.sh <version> <stage/prod flags> [--json]
```

Forward the `-s`/`-p` flags the user provided. If neither was supplied, default to checking both — pass `-s -p`. Display stderr only if the script exits non-zero.

### Step 3: Display Output

Paste the **complete stdout** of the script into the response as a code block. Every check line must be visible to the user. Do not summarize, abbreviate, or replace the output with commentary.

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
...

── Global ──────────────────────────────────────────────────
✅  advisory_type                          spec.type = RHBA
✅  shipment_mr_approved                   MR !594 approved by tlove, knarra, adobes
```

## Examples

```bash
/microshift-release:check-bootc-advisory 4.20.26              # stage + prod (default)
/microshift-release:check-bootc-advisory 4.20.26 -s           # stage only
/microshift-release:check-bootc-advisory 4.20.26 -p           # prod only
/microshift-release:check-bootc-advisory 4.20.26 -s -p        # all checks (explicit)
/microshift-release:check-bootc-advisory 4.22.2 -s -p         # el9 + el10
/microshift-release:check-bootc-advisory 4.20.26 -s --json    # machine-readable
```

## Notes

- Read-only — does NOT modify advisories, tickets, or external state. No confirmation required.
- VPN + GITLAB_API_TOKEN required; version 4.18+ only
- Exit code is non-zero if any check returns FAIL
- Supports `--json` for machine-readable JSON output
- These checks can start alongside step 1, but wait for step 2 to finish before signing off Shipment.
