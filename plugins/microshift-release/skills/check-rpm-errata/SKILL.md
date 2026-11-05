---
name: microshift-release:check-rpm-errata
argument-hint: <version> <advisory_id> [--verbose]
description: Step 4 — check the RPM advisory in Errata Tool for QE sign-off before shipment
user-invocable: true
allowed-tools: Bash
---

# microshift-release:check-rpm-errata

## Synopsis

```bash
/microshift-release:check-rpm-errata <version> <advisory_id> [--verbose]
```

## Description

Step 4 of the MicroShift release process: verify that the MicroShift RPM advisory
in the Red Hat Errata Tool is ready for QE sign-off and shipping. For the
Konflux-built bootc image advisory, use `/microshift-release:check-bootc-advisory`
(step 3).

Validates:

- **Advisory metadata** — exists, correct type (RHEA for GA, RHBA/RHSA for z-stream), QA ownership changed
- **Bugs** — all linked OCPBUGS in Verified state
- **Builds** — all MicroShift RPMs attached and mapped to product listings
- **Distribution** — CDN staging push completed, CAT tests passing, advisory moved to REL_PREP

Requires VPN and a valid Kerberos ticket (`kinit`).

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| VPN | Errata Tool | Yes |
| Kerberos ticket (`kinit`) | Errata Tool API | Yes |

## Arguments

- `version` (required): Full version string (4.18+)
  - Z-stream: `4.20.26`
  - X/Y (GA): `4.22.0`
  - RC: `4.22.0-rc.2`
  - EC: `5.0.0-ec.3`
- `advisory_id` (required): The Errata Tool advisory numeric ID or name (e.g., `12345` or `RHBA-2026:12345`)
- `--verbose` (optional): Show additional per-check detail

## Scripts Directory

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

### Step 1: Parse Arguments

1. Extract `version` from `$ARGUMENTS` — the first non-flag token
2. Extract `advisory_id` — the second non-flag token. If missing, error: "Missing advisory ID"
3. Pass through `--verbose` if present

### Step 2: Run Errata Tool Checks

```bash
bash $SCRIPTS_DIR/check_rpm-errata.sh <version> <advisory_id> [--verbose]
```

Display stderr only if the script exits non-zero.

### Step 3: Display Output

Paste the **complete stdout** of the script into the response as a code block. Every check line must be visible to the user. Do not summarize, abbreviate, or replace the output with commentary.

### Step 4: Handle Errors

If the script exits non-zero:

- **Kerberos auth failed**: Run `kinit <username>@REDHAT.COM` to obtain a Kerberos ticket
- **VPN errors**: Connect to VPN (Errata Tool requires it)
- **Advisory not found**: Verify the advisory ID/name is correct in the Errata Tool UI

## Checks Performed

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

## Examples

```bash
/microshift-release:check-rpm-errata 4.20.26 12345
/microshift-release:check-rpm-errata 4.20.26 RHBA-2026:12345
/microshift-release:check-rpm-errata 4.20.26 12345 --verbose
```

## Notes

- Read-only — does NOT modify advisories, tickets, or external state. No confirmation required.
- VPN + Kerberos ticket (`kinit`) required; works with any MicroShift version that has an ET advisory
- Exit code is non-zero if any check returns FAIL
- These checks can start alongside step 1, but wait for step 2 to finish before signing off Errata.
