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

## Pre-check decision rules

- Already shipped/non-applicable CVEs are excluded. For the remaining trackers,
  explicit `release-required` / `release-not-required` labels take precedence.
- A CVE in `Verified` with an empty resolution needs human review; the report
  highlights the missing resolution rather than automatically requesting a release.
- A known release-required fix takes priority over another CVE needing review:
  recommend `ASK ART` when OCP is available, or `BLOCKED` otherwise, and retain
  the review warning.
- Zero MicroShift source commits do not rule out a release: compiler fixes or
  updated bootc image dependencies can require rebuilding the same source commit.
  Unknown advisory data or CVEs pending Jira enrichment result in `NEEDS REVIEW`,
  even without source changes. Use `SKIP` only after assessment finds no
  release-required fixes or other release gate.

## How To

### Request ART to create a new Z-Stream

For every Z-Stream marked as `ASK ART` in Pre-Check (Phase 0) output.

This is automated by `scripts/request_art_builds.sh`, which creates (or reuses
a matching existing) ART and USHIFT ticket, links them, and posts the Slack
request for you:

```shell
scripts/request_art_builds.sh <X.Y.Z> <ART_DUE_DATE> <RELEASE_DATE> --reason "<TEXT>"
```

Use the ART and USHIFT ticket keys printed by the run — don't clone the
templates below on top of them. See
[Requirements](#requirements)
for required secrets, and run with `--dry-run` first to preview the result
without writing to Jira or Slack. Use `--help` for the full flag list.

To do this manually instead:

 1. Clone this [template ART Jira ticket](https://redhat.atlassian.net/browse/ART-11857) and replace the target version in the title
 1. Share it in Slack [#forum-ocp-art](https://redhat.enterprise.slack.com/archives/CB95J6R4N) channel, template message example:

```text
Hello, @release-artists I've opened ${ID_TO_ART_TICKET_CREATED} requesting MicroShift X.Y.Z builds (due date DDth MM). Please help create RPMs and bootc images, thanks!
cc @Pablo Acevedo @Rama kasturi @AlejandroGullon @Tami Love
```

### Track Release Testing Activities

For every Z-Stream release requested to ART:

 1. Open the USHIFT ticket created by `request_art_builds.sh` (or clone the
    [template USHIFT Jira ticket](https://redhat.atlassian.net/browse/USHIFT-6945)
    and replace the target version and date in the title if tracking manually)
 1. Go through the steps described in the template ticket, log results, and mark each completed step.

## Unit tests

Run both unit-test suites without VPN access or service credentials.

Requires `jq` and Kerberos development headers/tools (`krb5-config`) to build
the `gssapi` dependency. On Ubuntu/Debian, install them with
`sudo apt-get install -y jq libkrb5-dev`.

```shell
cd plugins/microshift-release/scripts
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-test.txt
python3 -m pytest unit_tests/ -q
bash unit_tests/test_request_art_builds.sh
```

## Requirements

- VPN connection (for Brew RPM checks, advisory reports)
- Kerberos login: `kinit`
- GitLab API token: `GITLAB_API_TOKEN`
- Jira API token: `JIRA_API_TOKEN` and `JIRA_USERNAME`
- `gh` CLI (for PR operations in automated testing)
- `aws` CLI (for S3 build cache and artifact upload)
- `gsutil` CLI (for GCS artifact download)
