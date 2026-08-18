# MicroShift Release Activities

Automate MicroShift Release Resting Activities — from pre-release evaluation through build validation, CI verification, advisory promotion, and post-release checks.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install microshift-release
```

## Skills

| Skill | Phase | When | Description |
|---|---|---|---|
| `/microshift-release:pre-check` | Pre-Check (Phase 0) | Every Friday before EOB | Evaluate whether MicroShift should participate in an OCP release (z-stream, nightly, EC/RC) |
| `/microshift-release:release-versions` | Info (Phase 0) | Anytime after ART created RPMs and bootc images | Report details about all artifacts: RPMs, bootc images, and Brew builds |
| `/microshift-release:validate-artifacts` | Build Validation (Phase 1) | As soon as ART created RPMS and bootc images | Validate MicroShift built artifacts (RPMs and bootc images) produced by ART |
| `/microshift-release:automated-testing` | Automated Testing (Phase 2) | As soon as Phase 1 is done | Run the full Prow CI release testing workflow — create PR, trigger jobs, check status, download and upload artifacts |
| Not Applicable | Manual Testing (Phase 3) | Only for RHEA versions (X.Y.0). As soon as Phase 1 is done | Run some manual tests  |
| `/microshift-release:advisory-promotion` | Advisory Promotion (Phase 4) | This can be done in parallel as Phase 1 but must wait for Phase 2 to be done to sign-off Errata and Shipment | Validate Konflux bootc advisory promotion for QE sign-off — verify advisory YAML, catalog presence, shipment MR, and commit provenance |
| `/microshift-release:post-release` | Post-Release (Phase 5) | As soon as Phase 3 is done | Verify all artifacts and docs are publicly available after shipping — bootc images, RPMs, errata, documentation, and lifecycle page |

## How To

### Request ART to create a new Z-Stream

For every Z-Stream marked as `ASK ART` in Pre-Check (Phase 0) output:
 1. Clone this [template ART jira ticket](https://redhat.atlassian.net/browse/ART-11857) and replace the target version in the title
 1. Share it in Slack [#forum-ocp-art](https://redhat.enterprise.slack.com/archives/CB95J6R4N) channel, template message example:
``` 
Hello, @release-artists I've opened ${ID_TO_ART_TICKET_CREATED} requesting MicroShift X.Y.Z builds (due date DDth MM). Please help create RPMa and bootc images, thanks!
cc @Pablo Acevedo @Rama kasturi @AlejandroGullon @Tami Love
```

### Track Release Testing Activities

For every Z-Stream release requested to ART:
 1. Cone this [template USHIFT jira ticket](https://redhat.atlassian.net/browse/USHIFT-6945) and replace the target version and date in the title
 1. Go through the steps described in the template ticket, log results and add ✅ during your progress.

## Requirements

- VPN connection (for Brew RPM checks, advisory reports)
- kerberos login: `kinit`
- Gitlab API token: `GITLAB_API_TOKEN`
- Jira API token: `JIRA_API_TOKEN` and `JIRA_USERNAME`
- `gh` CLI (for PR operations in automated testing)
- `aws` CLI (for S3 build cache and artifact upload)
- `gsutil` CLI (for GCS artifact download)
