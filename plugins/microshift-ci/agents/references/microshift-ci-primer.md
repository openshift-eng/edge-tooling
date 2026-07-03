# MicroShift CI Artifact Primer

Reference for analyzing MicroShift Prow job artifacts — which file answers which question.

## Job types

- **Scenario-based e2e** (`e2e-aws-tests-*`): the `openshift-microshift-e2e-metal-tests` step boots ~20 VM-based test scenarios on a shared hypervisor. Failures are per-scenario.
- **Direct-test** (`*-ocp-conformance-*`, `e2e-aws-ai-model-serving-*`, `e2e-aws-footprint-*`): run their test suite directly, no scenario fan-out.

## Test framework

Tests use [Robot Framework](https://robotframework.org). Suites: `test/suites/*.robot`. Shared keywords: `test/resources/`. Scenario definitions: `test/scenarios*/`.

`TEST_EXECUTION_TIMEOUT` (default `30m`) wraps Robot Framework in `timeout`. When exceeded, the current test dies with `Execution terminated by signal` and every subsequent test reports `Test execution stopped due to a fatal error` — a cascade with ONE root cause (the time budget).

## Deployment types

Three deployment pipelines: **ostree** (scenarios in `test/scenarios/`), **bootc** (`test/scenarios-bootc/`), **RPM** (`test/suites/rpm/`). Job name indicates which (e.g. `e2e-aws-tests-bootc-*`). All produce the same artifact layout.

## Scenario naming

Scenario names encode OS, MicroShift version source, and suite. The `@` separator chains stages left-to-right; the **last segment** is always the test suite.

### Version-source markers

| Marker | Meaning |
|---|---|
| `src` | Built from source (PR or branch) |
| `base` | Built from PR's target branch |
| `prel` | Previous minor release (Y-1) |
| `crel` | Current minor release (EC/RC/z-stream) |
| `lrel` | Latest available release from staging repos |
| `zprel` | Latest z-stream from rhocp |
| `y1`/`y2` | Y-1/Y-2 minor versions back (also `yminus1`/`yminus2`) |

### OS tokens

`el96`/`el98`/`el102` — RHEL 9.6/9.8/10.2

### Reading multi-@ names

| Name | Meaning |
|---|---|
| `el96-lrel@standard1` | RHEL 9.6 + latest release, standard suite 1 |
| `el94-y2@el96-lrel@standard1` | Start Y-2 on RHEL 9.4, upgrade to RHEL 9.6 + latest release, run standard1 |
| `el96-yminus2@prel@src@delta-upgrade-ok` | Y-2 → Y-1 (prel) → source, static delta upgrade |

## Artifact layout

Per scenario, under `artifacts/<TEST_NAME>/openshift-microshift-e2e-metal-tests/artifacts/scenario-info/<scenario>/`:

| File | Answers |
|---|---|
| `junit.xml` | Which tests failed; `testsuite name` = scenario name |
| `rf-debug.log` | Robot Framework trace — failures marked `\| FAIL \|` |
| `boot_and_run.log` | VM boot + orchestration; scenario-killing timeouts appear here |
| `phase_create/junit.xml` | Infra junit from VM creation (greenboot check) |
| `phase_run/junit.xml` | Infra junit from test run phase |
| `vms/host1/sos/journal_*.log` | Plain-text journal exports — check FIRST for service failures, OOM, x509 |
| `vms/host1/sos/sosreport-*.tar.xz` | Full sosreports (see below) |

## Sosreports

Two types: **on-failure** (captured at each test failure, includes test-created namespaces — **prefer this one**) and **end-of-scenario** (teardown, may lack test workloads). Match to failure by comparing capture timestamp with `rf-debug.log` failure time.

**Journals**: use plain-text `journal_*.log` next to tarballs — no extraction needed.

**Pod logs**: extract with `bash plugins/shared/scripts/extract-sosreport.sh <tarball>`. Output lands in `<tarball-parent>/sos-extracted/<sosreport-name>/`:

- Pod logs: `sos_commands/microshift/namespaces/<ns>/pods/<pod>/<container>/<container>/logs/{current,previous}.log`
- `previous.log` tail states why a dead container exited (fatal error, leader election lost, panic)
- Cluster resources: `sos_commands/microshift/cluster-scoped-resources/`

## Greenboot

Before tests, the scenario waits for `greenboot-healthcheck.service` to exit. Failure → `pre_test_greenboot_check FAILED` in `phase_create/junit.xml`, no tests run. In the journal, `40_microshift_running_check.sh` lines show which deployments were waited on.

## Journal reading

Reconstruct a timestamped timeline before attributing fault:

- Pod lifecycle: `Created container`/`Started container` (crio), `SyncLoop (PLEG)`, probe readiness transitions
- Two `Created container` events for the same pod = first instance died — read `previous.log`
- `apply request took too long` = apiserver/etcd latency (can cause leader-election loss)

## Common patterns

**Timeout cascade**: `TEST_EXECUTION_TIMEOUT` expires → one test gets `Execution terminated by signal`, all subsequent get `Test execution stopped due to a fatal error`. ONE root cause — find what consumed the time budget.

**Greenboot masking**: greenboot failure → no tests run → only `phase_create/junit.xml` has the failure. Root cause is in the journal.

**Shared-hypervisor contention**: all scenarios share one host. CPU/memory/disk contention → greenboot timeouts, etcd pressure, image pull timeouts. Attribute to infrastructure, not product/test.
