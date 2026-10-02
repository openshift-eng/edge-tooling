# MicroShift Release Testing — CLI Reference

Command-line scripts for running MicroShift release validation phases.
Each phase is a standalone shell wrapper that sets up a Python venv and
runs the corresponding check script.

All scripts live under `plugins/microshift-release/scripts/`.

---

## Prerequisites

### Network & Authentication

| Requirement | What For | Setup |
|-------------|----------|-------|
| Red Hat VPN | Brew, Errata Tool, internal GitLab | Connect before running any script |
| Kerberos ticket | Brew & Errata Tool access | `kinit <your-id>@REDHAT.COM` |

### Environment Variables

```bash
export GITLAB_API_TOKEN="<your-gitlab-pat>"   # GitLab PAT for gitlab.cee.redhat.com (ocp-shipment-data access)
export JIRA_API_TOKEN="<your-jira-pat>"       # Jira Cloud API token (for CVE ticket lookups)
export JIRA_USERNAME="<your-jira-email>"      # Jira Cloud email (paired with JIRA_API_TOKEN)
```

### External CLI Tools

- `gh` — GitHub CLI, authenticated (`gh auth login`)
- `aws` — AWS CLI, configured with appropriate credentials
- `gsutil` — Google Cloud Storage CLI
- `git` — Git
- `python3` — Python 3 with venv support

### Python Dependencies

Each shell wrapper automatically creates a venv in `_output/release_testing/`
and installs dependencies from `requirements.txt`:

- `requests`
- `requests-gssapi`
- `pyyaml`

You don't need to manage the venv manually — the `.sh` wrappers handle it.

---

## Phase 0 — Pre-Check (Release Readiness)

Evaluate whether a release is ready to proceed. Run before starting any
build or test work.

### Check X.Y / X.Y.Z Release Readiness

```bash
./precheck.sh xyz <version> [<version2> ...] [--json]
```

- Accepts one or more version strings (e.g. `4.18` or `4.18.3`)
- Checks OCP release status, MicroShift Brew builds, OCPBUGS blockers, CVE trackers

#### What xyz checks

- **Lifecycle status** — whether MicroShift is active for this OCP minor
  version. Inactive versions are skipped with a `SKIP` recommendation.
- **OCP release availability** — whether an OCP payload exists for the
  target version (queried from the OCP release controller).
- **Advisory CVEs** — scans cumulative advisories across all skipped
  z-streams since the last MicroShift release, then enriches each CVE
  via Jira to determine whether fixes have landed in the MicroShift
  codebase.
- **OCPBUGS blockers** — release-required bugs, unresolved issues, and
  items flagged as `needs-review` that could block shipping.
- **Commit count since last release** — runs `git log` against the
  openshift/microshift repo to count commits on the release branch
  since the last tagged release.
- **90-day rule** — if more than 90 days have elapsed since the last
  MicroShift release and new commits exist, the tool triggers a release
  recommendation regardless of other signals.

Each version receives one of four recommendations:

| Recommendation | Meaning |
|----------------|---------|
| `ASK ART TO CREATE ARTIFACTS` | Ready — request ART to build |
| `NEEDS REVIEW` | Ambiguous — human decision required |
| `SKIP` | No release needed (no commits, inactive, etc.) |
| `BLOCKED` | Cannot release — unresolved blockers |

**Examples:**

```bash
./precheck.sh xyz 4.18
./precheck.sh xyz 4.18 4.19 4.20
./precheck.sh xyz 4.18.3 --json
```

### Check Nightly Build Gaps

```bash
./precheck.sh nightly [version] [--json] [--verbose]
```

- Version is optional (e.g. `4.18`) — omit to check all active branches
- Compares Brew nightly RPM timestamps against OCP accepted nightlies

#### What nightly checks

Iterates over all active OCP minor branches (or just the specified one)
and compares the latest Brew nightly build timestamp for MicroShift RPMs
against the OCP release controller's most recently accepted nightly
payload. Flags builds that are missing entirely or lagging behind OCP —
for example, if OCP accepted a nightly 12 hours ago but no corresponding
MicroShift Brew build exists, the version is flagged.

**Examples:**

```bash
./precheck.sh nightly
./precheck.sh nightly 4.19 --verbose
./precheck.sh nightly --json
```

### Discover EC/RC Candidates

```bash
./precheck.sh ecrc <EC|RC> [version] [--json] [--verbose]
```

- First argument must be `EC` or `RC`
- Version is optional — omit to auto-discover the latest

#### What ecrc checks

Queries the OCP release controller API for the latest EC or RC payload
tag, then checks whether MicroShift RPMs exist in Brew for that exact
version string. Also probes for potential next versions (e.g. if EC.2
exists, checks whether EC.3 is emerging). When no version is specified,
auto-discovers the current latest from the release controller.

**Examples:**

```bash
./precheck.sh ecrc EC
./precheck.sh ecrc RC 4.19
./precheck.sh ecrc EC --json
```

### Enrich OCPBUGS Data (Pipe from stdin)

```bash
echo '<json-array>' | ./precheck.sh enrich
```

- Reads a JSON array from stdin with bug details
- Outputs enriched markdown with release action recommendations
- Typically used to post-process OCPBUGS data from the `xyz` precheck

#### What enrich checks

For each bug in the input array, determines a `release_action`:

- **release-required** — bug must be fixed before shipping
- **release-not-required** — bug does not block the release
- **needs-review** — ambiguous; requires human triage

Flags unresolved bugs that block shipping and identifies CVE tracker
tickets that need special handling. Output is enriched markdown suitable
for pasting into a release tracking document.

---

## Phase 1 — Build Validation

Validate that ART produced correct RPMs and bootc images for a given release.
Run as soon as ART confirms builds are ready.

```bash
./validate.sh <version> [--verbose] [--json]
```

- Covers all release types: X.Y (GA), X.Y.Z (z-stream), RC, EC, nightly
- Checks RPM packages, filenames, commit IDs, RHEL versions, mirrors
- For 4.18+: also checks bootc shipment MRs, catalogs, advisories, image SHAs

### What validate checks

#### RPM checks

- **Package list completeness** — all expected MicroShift RPM packages
  are present in the Brew build
- **Filename format validation** — RPM filenames follow the expected
  naming convention for the release type
- **Commit ID verification** — the commit ID embedded in the RPM matches
  the expected source commit from the release branch
- **RHEL version correctness** — RPMs are built against the correct RHEL
  version(s) for the target OCP release
- **EC/RC mirror availability** — for EC and RC releases, checks that
  RPMs are available on the expected mirror paths
- **X.Y.0 commit match** — for GA releases, verifies the RPM commit
  matches the tagged X.Y.0 release commit

#### bootc checks (4.18+)

- **Shipment MR existence** — a merge request exists on
  `gitlab.cee.redhat.com` in the ocp-shipment-data project for this
  release
- **YAML count validation** — the shipment MR contains the expected
  number of YAML definition files
- **X.Y.0 shipment type** — for GA releases, validates the shipment
  type field is set correctly
- **Release notes** — shipment MR includes release notes content
- **Stage advisory URL** — a stage advisory URL is present and reachable
- **Catalog entries** — bootc images are listed in the stage catalog
  with correct tags
- **Prod advisory URL** — a prod advisory URL is present and reachable
- **Image SHA match** — image SHAs match between stage and prod
  advisories (ensures the same image is promoted)
- **EC/RC mirror availability** — for EC and RC releases, checks bootc
  image availability on expected mirrors

**Examples:**

```bash
./validate.sh 4.18.3
./validate.sh 4.19.0-ec.1 --verbose
./validate.sh 4.18.3 --json
```

---

## Phase 2 — Automated Testing (Prow CI)

Manage the lifecycle of release testing PRs in Prow CI.
Supports 4.21+ only.

```bash
./prow_testing.sh <action> <version> [--json] [--execute]
```

Mutating actions default to *dry-run* — add `--execute` to actually perform them.

### Actions (run in this order)

| # | Action | Mutating? | Description |
|---|--------|-----------|-------------|
| 1 | `preflight` | No | Pre-flight checks (branch exists, no existing PR, etc.) |
| 2 | `create-pr` | Yes | Create a draft release testing PR |
| 3 | `trigger` | Yes | Trigger CI jobs on the PR |
| 4 | `status` | No | Check CI job statuses |
| 5 | `scenarios` | No | Validate expected test scenarios |
| 6 | `download` | Yes | Download test artifacts from GCS |
| 7 | `upload` | Yes | Upload test artifacts to S3 |
| 8 | `complete` | Yes | Close the PR and post completion comment |

### Action details

- **`preflight`** — Checks that the release branch exists in the
  openshift/microshift repo, no existing testing PR is open for this
  version, and RPMs are available in the S3 build cache for both
  x86_64 and aarch64 architectures.

- **`create-pr`** — Creates a draft PR against the release branch in
  openshift/microshift that updates test scenario configs for the
  target version. The PR description includes the release version and
  links to the expected CI jobs.

- **`trigger`** — Posts `/test` comments on the PR to trigger Prow CI
  jobs for all release test scenarios. Each scenario gets its own
  `/test` invocation to ensure independent scheduling.

- **`status`** — Queries GitHub check runs on the PR and formats a
  table of job name, pass/fail status, and Prow URL. Reports overall
  pass/fail/pending counts.

- **`scenarios`** — Parses the GCS test results page to validate that
  all expected test scenarios ran. Reports pass/fail/skip counts per
  scenario and flags any scenarios that are missing entirely.

- **`download`** — Downloads test artifact tarballs from GCS (gcsweb)
  for both x86_64 and aarch64 architectures. Artifacts are saved to
  the local `_output/release_testing/` directory.

- **`upload`** — Uploads downloaded artifacts to the S3 build cache
  so they are available for downstream consumers and archival.

- **`complete`** — Closes the testing PR with a completion comment
  summarizing results (pass/fail counts, artifact locations, and any
  notable issues).

**Examples:**

```bash
# Dry-run workflow
./prow_testing.sh preflight 4.21.2
./prow_testing.sh create-pr 4.21.2              # dry-run
./prow_testing.sh create-pr 4.21.2 --execute    # actually create

# Monitor
./prow_testing.sh status 4.21.2
./prow_testing.sh status 4.21.2 --json

# Complete
./prow_testing.sh download 4.21.2 --execute
./prow_testing.sh upload 4.21.2 --execute
./prow_testing.sh complete 4.21.2 --execute
```

---

## Phase 3a — Advisory Promotion (bootc via Konflux)

Validate bootc image advisory promotion readiness. Can start in parallel
with Phase 1, finalize after Phase 2.

```bash
./advisory_promotion.sh <version> (-s | -p | -s -p) [--json]
```

- `-s` / `--stage` — check stage catalog
- `-p` / `--prod` — check prod catalog
- At least one of `-s` or `-p` is required

### What advisory_promotion checks

#### Per-variant checks

Run for each architecture × RHEL version combination (e.g. x86_64/el9,
aarch64/el9, x86_64/el10, aarch64/el10):

- **Advisory image present** — the bootc image for this variant exists
  in the advisory
- **Correct repository name** — the image is published to the expected
  container repository
- **Image SHA match** — the image SHA in the advisory matches the SHA
  in the catalog (ensures no tampering or mismatch during promotion)
- **Stage catalog** — presence in stage catalog, tag-commit consistency,
  tag-date validation, no stale X.Y.0 tag, Container Health Index (CHI)
  grade
- **Prod catalog** — presence in prod catalog, tag-commit consistency,
  tag-date validation, no stale X.Y.0 tag, Container Health Index (CHI)
  grade

#### Global checks

- **Advisory type** — RHEA for GA releases, RHBA or RHSA for z-stream
  releases
- **Shipment type validation** — shipment type in the MR matches the
  release type

**Examples:**

```bash
./advisory_promotion.sh 4.18.3 -s            # stage only
./advisory_promotion.sh 4.18.3 -p            # prod only
./advisory_promotion.sh 4.18.3 -s -p         # both
./advisory_promotion.sh 4.18.3 -s -p --json
```

---

## Phase 3b — Errata Promotion (RPMs)

QE sign-off checks for MicroShift RPM advisories in Errata Tool.
Requires VPN and a valid Kerberos ticket.

```bash
./errata_promotion.sh <version> <advisory_id> [--verbose] [--json]
```

- `advisory_id` can be numeric ID or advisory name

### What errata_promotion checks

| Check | Description |
|-------|-------------|
| `et_advisory_exists` | Advisory is fetchable from the Errata Tool |
| `et_advisory_type` | Advisory type matches release type (RHEA for X.Y GA, RHBA/RHSA for z-stream) |
| `et_qa_owner` | QA ownership has been changed from default |
| `et_bugs_verified` | All linked OCPBUGS are in accepted state (Verified/Closed/Release Pending) |
| `et_rpms_present` | All expected MicroShift RPM packages are attached to the advisory |
| `et_rpms_product_listed` | RPMs are product-listed for CDN distribution |
| `et_cdn_staging` | Packages are staged on CDN |
| `et_cat_tests` | CAT (Customer Acceptance Testing) tests pass |
| `et_status_rel_prep` | Advisory status is set to REL_PREP (release preparation) |

**Examples:**

```bash
./errata_promotion.sh 4.18.3 RHBA-2026:12345
./errata_promotion.sh 4.18.3 123456 --verbose
./errata_promotion.sh 4.18.3 123456 --json
```

---

## Phase 4 — Post-Release Verification

Verify all artifacts and documentation are publicly available after shipping.
*GA and z-stream releases only* — EC/RC/nightly versions are rejected.

```bash
./post_release.sh <version> [--json]
```

- Confirms bootc images, RPMs, errata, docs, and lifecycle page are customer-accessible

### What post_release checks

#### Errata

- **RPM advisory** — found and shipped
- **bootc stage advisory** — found, shipped, and images are correct
- **bootc prod advisory** — found, shipped, and images are correct

#### bootc catalog

- **el9 catalog entry** — publicly accessible in the container catalog
- **el10 catalog entry** — publicly accessible in the container catalog

#### RPMs

- **Customer Portal** — RPMs available on the Red Hat Customer Portal
- **CDN** — RPMs available on CDN mirrors

#### Documentation

- **Release notes** — published on docs.openshift.com for this version

#### Lifecycle

- **Version listed** — version appears on the Red Hat product lifecycle
  page
- **Status** — version is marked as active/supported

**Examples:**

```bash
./post_release.sh 4.18.3
./post_release.sh 4.19.0 --json
```

---

## Quick Reference

```text
Phase 0:  ./precheck.sh xyz <ver...> [--json]
          ./precheck.sh nightly [ver] [--json] [--verbose]
          ./precheck.sh ecrc <EC|RC> [ver] [--json] [--verbose]
          echo '<json>' | ./precheck.sh enrich

Phase 1:  ./validate.sh <ver> [--verbose] [--json]

Phase 2:  ./prow_testing.sh preflight <ver> [--json]
          ./prow_testing.sh create-pr <ver> [--json] [--execute]
          ./prow_testing.sh trigger <ver> [--json] [--execute]
          ./prow_testing.sh status <ver> [--json]
          ./prow_testing.sh scenarios <ver> [--json]
          ./prow_testing.sh download <ver> [--json] [--execute]
          ./prow_testing.sh upload <ver> [--json] [--execute]
          ./prow_testing.sh complete <ver> [--json] [--execute]

Phase 3a: ./advisory_promotion.sh <ver> (-s|-p|-s -p) [--json]
Phase 3b: ./errata_promotion.sh <ver> <advisory> [--verbose] [--json]

Phase 4:  ./post_release.sh <ver> [--json]
```

---

## Output Modes

All scripts support two output modes:

- *Human-readable* (default) — formatted text with emoji status indicators
- *JSON* (`--json`) — structured JSON for programmatic consumption

All scripts print results to stdout and log messages to stderr.
