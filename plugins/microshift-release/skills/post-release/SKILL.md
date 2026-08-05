---
name: microshift-release:post-release
argument-hint: <version> [--verbose] [--json]
description: Verify all artifacts and docs are publicly available after shipping — bootc images, RPMs, errata, documentation, and lifecycle page
user-invocable: true
allowed-tools: Bash
---

# microshift-release:post-release

## Synopsis

```bash
/microshift-release:post-release <version> [--verbose] [--json]
```

## Description

Phase 4 of the MicroShift release process: confirm all artifacts and documentation are publicly available after a GA or z-stream release has shipped.

**Only GA (X.Y.0) and z-stream (X.Y.Z) releases are supported.** EC and RC versions are rejected.

Verifies five areas (manual verification links included for reference):

1. **Errata** — public advisory exists and has reached SHIPPED_LIVE
   - [Red Hat Errata Search](https://access.redhat.com/errata-search/?q=microshift)
2. **Bootc Images** — container images available in the Red Hat Catalog
   - [microshift-bootc-rhel9](https://catalog.redhat.com/en/software/containers/openshift4/microshift-bootc-rhel9/66f6e711df74bb2d150c4bfb)
   - [microshift-bootc-rhel10](https://catalog.redhat.com/en/software/containers/openshift4/microshift-bootc-rhel10/67e36ee2a0c2a20e05f38a09) (4.22+ only)
3. **RPMs** — packages accessible on the Red Hat Customer Portal and pushed to CDN repos
   - [Red Hat Customer Portal — Package Browser](https://access.redhat.com/downloads/content/package-browser)
4. **Documentation** — release notes published on docs.redhat.com
   - [MicroShift Release Notes](https://docs.redhat.com/en/documentation/red_hat_build_of_microshift)
5. **Lifecycle** (X.Y.0 only) — version listed in the Product Life Cycle page with Full Support status
   - [Product Life Cycles — Red Hat build of MicroShift](https://access.redhat.com/product-life-cycles?product=Red%20Hat%20build%20of%20MicroShift)

The advisory is auto-discovered via the public Hydra search API — no advisory ID argument is needed.

Most checks work over the public internet. VPN-gated checks (errata SHIPPED_LIVE status, CDN repo verification) degrade gracefully to WARN when VPN is unavailable.

For versions 4.22.2+, both el9 and el10 bootc images are checked.

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| Internet | All checks | Yes |
| VPN | Errata SHIPPED_LIVE status, CDN repos | No (degrades to WARN) |
| Kerberos ticket (`kinit`) | Errata Tool API | No (degrades to WARN) |

## Arguments

- `version` (required): Full GA or z-stream version string
  - Z-stream: `4.21.7`
  - GA: `4.22.0`
  - EC/RC/nightly versions are **rejected**
- `--verbose` (optional): Show detailed markdown report
- `--json` (optional): Output raw JSON

## Scripts Directory

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

### Step 1: Parse Arguments

1. Extract `version` from `$ARGUMENTS` — the first non-flag token
2. Pass through `--verbose` and `--json` flags if present

### Step 2: Run Checks

```bash
bash $SCRIPTS_DIR/post_release.sh <version> [--verbose] [--json]
```

Display stderr only if the script exits non-zero.

### Step 3: Display Output

Paste the **complete stdout** of the script into the response as a code block. Every check line must be visible to the user. Do not summarize, abbreviate, or replace the output with commentary.

### Step 4: Handle Errors

- **EC/RC/nightly version**: Post-release checks are only for GA and z-stream releases
- **VPN unavailable**: Errata status and CDN repo checks degrade to WARN — all other checks still run
- **Kerberos auth failed**: Run `kinit <username>@REDHAT.COM` for Errata Tool access
- **Network errors**: Individual checks degrade to WARN with the error message

## Checks Performed

### Errata

| Check | Description |
|---|---|
| `pr_errata_found` | Public errata advisory exists for this version (Hydra search) |
| `pr_errata_shipped` | Advisory status is SHIPPED_LIVE (VPN required) |

### Bootc Images

| Check | Description |
|---|---|
| `pr_bootc_catalog_el9` | `microshift-bootc-rhel9` image in prod catalog (catalog.redhat.com) |
| `pr_bootc_catalog_el10` | `microshift-bootc-rhel10` image in prod catalog (4.22.2+ only) |

### RPMs

| Check | Description |
|---|---|
| `pr_rpms_customer_portal` | Advisory page accessible on Red Hat Customer Portal (access.redhat.com) |
| `pr_rpms_cdn` | RPMs pushed to rhocp CDN repos (VPN required) |

### Documentation

| Check | Description |
|---|---|
| `pr_docs_published` | Release notes published on docs.redhat.com |

### Lifecycle (X.Y.0 only)

| Check | Description |
|---|---|
| `pr_lifecycle_listed` | Version listed in Product Life Cycle API |
| `pr_lifecycle_active` | Lifecycle status is Full Support |

## Output Format

**Short (default):**

```text
Post-Release Verification: 4.21.7

── Errata ──────────────────────────────────────────────────
✅  pr_errata_found            RHBA-2026:12345 (published 2026-07-29)
✅  pr_errata_shipped          Status: SHIPPED_LIVE

── Bootc Images ────────────────────────────────────────────
✅  pr_bootc_catalog_el9       Found in prod catalog (rhel9)

── RPMs ────────────────────────────────────────────────────
✅  pr_rpms_customer_portal    Advisory page accessible
✅  pr_rpms_cdn                4 CDN repo(s) found

── Documentation ───────────────────────────────────────────
✅  pr_docs_published          Release notes page accessible (4.21)
```

On failure, details appear below the failing check:

```text
❌  pr_bootc_catalog_el9       Not found in prod catalog (rhel9)
                               No matching image found for version 4.21.7
```

**Verbose (--verbose):** Markdown table with full evidence per check.

## Examples

```bash
/microshift-release:post-release 4.21.7              # z-stream
/microshift-release:post-release 4.22.0              # GA (includes lifecycle checks)
/microshift-release:post-release 4.22.2              # z-stream (el9 + el10)
/microshift-release:post-release 4.21.7 --verbose    # detailed report
/microshift-release:post-release 4.21.7 --json       # machine-readable output
```

## Notes

- Read-only — does NOT modify advisories, tickets, or external state. No confirmation required.
- Advisory is auto-discovered via the public Hydra search API — no advisory ID needed
- EC, RC, and nightly versions are rejected with an error
- VPN is optional — most checks work over the public internet
- Exit code is non-zero if any check returns FAIL
