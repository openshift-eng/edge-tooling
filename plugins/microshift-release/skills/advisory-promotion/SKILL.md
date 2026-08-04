---
name: microshift-release:advisory-promotion
argument-hint: <version> [--prod] [--verbose] [--json] [--errata <advisory_id>]
description: Validate advisory promotion for QE sign-off — Konflux bootc images (default) or Errata Tool RPM advisory (--errata)
user-invocable: true
allowed-tools: Bash
---

# microshift-release:advisory-promotion

## Synopsis

```bash
/microshift-release:advisory-promotion <version> [--prod] [--verbose]
/microshift-release:advisory-promotion <version> --errata <advisory_id> [--verbose]
```

## Description

Phase 3 of the MicroShift release process: verify that advisories are ready for QE sign-off and shipping.

**Two modes:**

1. **Bootc mode (default)** — validates Konflux-built bootc image advisory via advisory YAML, Pyxis catalog, and shipment MR
2. **Errata mode (`--errata`)** — validates RPM advisory in the Errata Tool via its REST API

### Bootc Mode

Validates three data sources:

- **advisory.yaml** — image presence, repository, SHA, and advisory type
- **Pyxis catalog** (via GraphQL) — stage and prod catalog presence, assembly tags, commit provenance
- **Shipment MR** — YAML filename, NVR commit vs Brew, release type, MR approval

All per-image checks run independently per variant (arch + RHEL version). For versions 4.22.2+, both el9 and el10 bootc images are checked. Repository names are version-aware (`openshift4` for 4.x, `openshift5` for 5.x).

Supports Z-stream, X/Y GA, RC, and EC release types. Requires version 4.18+.

### Errata Mode

Validates the MicroShift RPM advisory in the Red Hat Errata Tool:

- **Advisory metadata** — exists, correct type (RHEA for GA, RHBA/RHSA for z-stream), QA ownership changed
- **Bugs** — all linked OCPBUGS in Verified state
- **Builds** — all MicroShift RPMs attached and mapped to product listings
- **Distribution** — CDN staging push completed, CAT tests passing, advisory moved to REL_PREP

Requires VPN and a valid Kerberos ticket (`kinit`).

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| VPN | GitLab API, Brew, Errata Tool | Yes |
| `GITLAB_API_TOKEN` | Bootc: shipment MR, approval check | Yes (bootc mode) |
| Kerberos ticket (`kinit`) | Errata Tool API | Yes (errata mode) |
| Internet | Pyxis catalog queries (GraphQL) | Yes (bootc mode) |

## Arguments

- `version` (required): Full version string (4.18+)
  - Z-stream: `4.20.26`
  - X/Y (GA): `4.22.0`
  - RC: `4.22.0-rc.2`
  - EC: `5.0.0-ec.3`
- `--errata <advisory_id>` (optional): Switch to Errata Tool mode. `advisory_id` is the ET advisory numeric ID or name (e.g., `12345` or `RHBA-2026:12345`)
- `--prod` (optional, bootc only): Check both stage and prod catalogs (default: stage only)
- `--verbose` (optional): Show detailed markdown report

## Scripts Directory

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

### Step 1: Parse Arguments

1. Extract `version` from `$ARGUMENTS` — the first non-flag token
2. Check if `--errata` is present:
   - If present, extract the `<advisory_id>` (the token immediately after `--errata`)
   - Route to **Errata mode** (Step 2b)
   - If `--errata` is present but no advisory ID follows, error: "Missing advisory ID after --errata"
3. If `--errata` is not present, route to **Bootc mode** (Step 2a)
4. Pass through `--verbose` and `--json` flags if present

### Step 2a: Run Bootc Checks (default)

```bash
bash $SCRIPTS_DIR/advisory_promotion.sh <version> --prod [--verbose]
```

Always pass `--prod` so that all checks run (stage and prod catalogs). Display stderr only if the script exits non-zero.

### Step 2b: Run Errata Tool Checks

```bash
bash $SCRIPTS_DIR/errata_promotion.sh <version> <advisory_id> [--verbose]
```

Display stderr only if the script exits non-zero.

### Step 3: Display Output

Paste the **complete stdout** of each script into the response as a code block. Every check line must be visible to the user. Do not summarize, abbreviate, or replace the output with commentary.

### Step 4: Handle Errors

If the script exits non-zero:

**Bootc mode:**
- **VPN errors**: Connect to VPN (GitLab API and Brew require it)
- **Missing GITLAB_API_TOKEN**: `export GITLAB_API_TOKEN=<token>` for shipment MR and approval checks
- **Version too low**: Advisory promotion requires 4.18+ (Konflux builds)

**Errata mode:**
- **Kerberos auth failed**: Run `kinit <username>@REDHAT.COM` to obtain a Kerberos ticket
- **VPN errors**: Connect to VPN (Errata Tool requires it)
- **Advisory not found**: Verify the advisory ID/name is correct in the Errata Tool UI

## Checks Performed

### Bootc Mode Checks

All per-image checks run independently per variant (`{arch}_el{rhel}`). For versions < 4.22.2 only el9 is checked; for 4.22.2+ both el9 and el10.

#### Per-variant checks (`{arch}_el{rhel}_*`)

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

#### Global checks

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

### Errata Mode Checks

| Check | Section | Description |
|---|---|---|
| `et_advisory_exists` | Advisory | Advisory found and fetchable from the Errata Tool |
| `et_advisory_type` | Advisory | Advisory type is RHEA (GA) or RHBA/RHSA (z-stream, EC, RC) |
| `et_qa_owner` | Advisory | QA ownership changed from default |
| `et_status_rel_prep` | Advisory | Advisory status is REL_PREP (or later) |
| `et_bugs_verified` | Bugs | All linked OCPBUGS are in Verified state |
| `et_rpms_present` | Builds | All expected MicroShift RPMs attached to advisory |
| `et_rpms_product_listed` | Builds | MicroShift RPMs mapped to product version listings |
| `et_cdn_staging` | Distribution | CDN staging push completed |
| `et_cat_tests` | Distribution | RHN QA testing passed (rhnqa field) |

## Output Format

### Bootc Mode

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

### Errata Mode

**Short (default):**

```text
Errata Tool Promotion: 4.20.26 (RHBA-2026:12345)

── Advisory ────────────────────────────────────────────────
✅  et_advisory_exists       Advisory found: RHBA-2026:12345-05
✅  et_advisory_type         RHBA (expected for Z)
✅  et_qa_owner              QA: MicroShift QE
✅  et_status_rel_prep       Status: REL_PREP

── Bugs ────────────────────────────────────────────────────
✅  et_bugs_verified         3/3 bugs in Verified state

── Builds ──────────────────────────────────────────────────
✅  et_rpms_present          8/8 MicroShift RPMs in advisory
✅  et_rpms_product_listed   RPMs listed in 2 product version(s)

── Distribution ────────────────────────────────────────────
✅  et_cdn_staging           CDN staging push completed (status: REL_PREP)
✅  et_cat_tests             2 external test(s) passing
```

On failure, details appear below the failing check:

```text
❌  et_bugs_verified         1/3 verified — 2 not yet verified
                             OCPBUGS-12345: Modified
                             OCPBUGS-67890: ON_QA
```

**Verbose (--verbose):** Markdown table with full evidence per check.

## Examples

```bash
# Bootc mode
/microshift-release:advisory-promotion 4.20.26             # Z-stream (el9 only)
/microshift-release:advisory-promotion 4.22.2              # Z-stream (el9 + el10)
/microshift-release:advisory-promotion 4.22.0              # X/Y GA
/microshift-release:advisory-promotion 4.20.26 --prod      # check prod catalog too
/microshift-release:advisory-promotion 4.20.26 --verbose   # detailed report

# Errata Tool mode
/microshift-release:advisory-promotion 4.20.26 --errata 12345
/microshift-release:advisory-promotion 4.20.26 --errata RHBA-2026:12345
/microshift-release:advisory-promotion 4.22.0 --errata RHEA-2026:67890 --verbose
```

## Notes

- Read-only — does NOT modify advisories, tickets, or external state. No confirmation required.
- **Bootc mode**: VPN + GITLAB_API_TOKEN required; version 4.18+ only
- **Errata mode**: VPN + Kerberos ticket (`kinit`) required; works with any MicroShift version that has an ET advisory
- Exit code is non-zero if any check returns FAIL
- Both modes support `--verbose` for detailed markdown output and `--json` for machine-readable JSON
