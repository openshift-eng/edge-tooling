# MicroShift Release Activities

Automate MicroShift Release Testing Activities — from pre-release evaluation through build validation, CI verification, advisory promotion, and post-release checks.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install microshift-release
```

## Skills

| Skill | Phase | When | Description |
|---|---|---|---|
| `/microshift-release:pre-check` | Pre-Check (Phase 0) | Every Friday before end of the day | Evaluate whether MicroShift should participate in an OCP release (z-stream, nightly, EC/RC) |
| `/microshift-release:release-versions` | Info (Phase 0) | Anytime after ART has created RPMs and bootc images | Report details about all artifacts: RPMs, bootc images, and Brew builds |
| `/microshift-release:validate-artifacts` | Build Validation (Phase 1) | As soon as ART has created RPMs and bootc images | Validate MicroShift built artifacts (RPMs and bootc images) produced by ART |
| `/microshift-release:automated-testing` | Automated Testing (Phase 2) | As soon as Phase 1 is done | Run the full Prow CI release testing workflow — create PR, trigger jobs, check status, download and upload artifacts |
| Not Applicable | Manual Testing (Phase 2) | Manual testing must be performed on new features only for non-z-stream versions (X.Y.0 or RHEA), in case they cannot be automated or haven't been automated yet. As soon as Phase 1 is done | Run some manual tests  |
| `/microshift-release:advisory-promotion` | Advisory Promotion (Phase 3) | Start in parallel with Phase 1, but wait for Phase 2 to finish before signing off Errata and Shipment | Validate Konflux bootc advisory promotion for QE sign-off — verify advisory YAML, catalog presence, shipment MR, and commit provenance |
| `/microshift-release:post-release` | Post-Release (Phase 4) | After sign-off in Errata Tool (REL_PREP) and Shipment (approved) | Verify all artifacts and docs are publicly available after shipping — bootc images, RPMs, errata, documentation, and lifecycle page |

## Running the scripts directly

Prefer not to go through Claude? Every skill except `release-versions` is a thin
wrapper over a bash script in `scripts/` and produces the same output when run
directly.

Each `.sh` wrapper auto-creates a Python venv at `_output/release_testing` and
installs `scripts/requirements.txt` on first run, so the only extra prerequisite
is `python3` — plus the tokens and VPN listed under [Requirements](#requirements).
The scripts resolve the repository root via `git rev-parse --show-toplevel`, so
**run them from inside the `edge-tooling` git checkout**. Most scripts accept
`--json` for machine-readable output.

Paths below are relative to the repository root:

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

| Skill | Direct bash equivalent |
|---|---|
| `pre-check` (Z/X/Y) | `bash $SCRIPTS_DIR/precheck.sh xyz <versions...> [--verbose] [--json]` |
| `pre-check` (nightly) | `bash $SCRIPTS_DIR/precheck.sh nightly [version] [--verbose]` |
| `pre-check` (EC/RC) | `bash $SCRIPTS_DIR/precheck.sh ecrc <EC\|RC> [version] [--verbose]` |
| `release-versions` | *No script — Claude/WebFetch only (see notes below)* |
| `validate-artifacts` | `bash $SCRIPTS_DIR/validate.sh <version> [--verbose]` |
| `automated-testing` | `bash $SCRIPTS_DIR/prow_testing.sh <action> <version> [--execute]` |
| `advisory-promotion` (bootc) | `bash $SCRIPTS_DIR/advisory_promotion.sh <version> [-s] [-p] [--json]` |
| `advisory-promotion` (errata) | `bash $SCRIPTS_DIR/errata_promotion.sh <version> <advisory_id> [--verbose]` |
| `post-release` | `bash $SCRIPTS_DIR/post_release.sh <version> [--json]` |

### Automated testing is multi-step

`automated-testing` runs `prow_testing.sh` once per step, in this order:

```text
preflight → create-pr → trigger → status → scenarios → download → upload → complete
```

Mutating actions (`create-pr`, `trigger`, `download`, `upload`, `complete`) are
**dry-run by default** — pass `--execute` to actually perform them. The
non-mutating actions (`preflight`, `status`, `scenarios`) always run for real.

### Notes on running scripts directly

- **`release-versions`** has no bash script — it is implemented entirely via
  Claude's WebFetch and inline `curl` calls, so it must be run through Claude.
- **`pre-check`** run directly skips the Jira/MCP enrichment the skill performs:
  there is no ART-ticket schedule table and no natural-language time-range
  resolution (e.g. "this week"), so pass explicit versions instead of a time
  range. The script still runs and degrades gracefully; everything else is
  identical.

## How To

### Request ART to create a new Z-Stream

For every Z-Stream marked as `ASK ART` in Pre-Check (Phase 0) output:

 1. Clone this [template ART Jira ticket](https://redhat.atlassian.net/browse/ART-11857) and replace the target version in the title
 1. Share it in Slack [#forum-ocp-art](https://redhat.enterprise.slack.com/archives/CB95J6R4N) channel, template message example:

```text
Hello, @release-artists I've opened ${ID_TO_ART_TICKET_CREATED} requesting MicroShift X.Y.Z builds (due date DDth MM). Please help create RPMs and bootc images, thanks!
cc @Pablo Acevedo @Rama kasturi @AlejandroGullon @Tami Love
```

### Track Release Testing Activities

For every Z-Stream release requested to ART:

 1. Clone this [template USHIFT Jira ticket](https://redhat.atlassian.net/browse/USHIFT-6945) and replace the target version and date in the title
 1. Go through the steps described in the template ticket, log results, and mark each completed step.

## Requirements

- VPN connection (for Brew RPM checks, advisory reports)
- Kerberos login: `kinit`
- GitLab API token: `GITLAB_API_TOKEN`
- Jira API token: `JIRA_API_TOKEN` and `JIRA_USERNAME`
- `gh` CLI (for PR operations in automated testing)
- `aws` CLI (for S3 build cache and artifact upload)
- `gsutil` CLI (for GCS artifact download)
