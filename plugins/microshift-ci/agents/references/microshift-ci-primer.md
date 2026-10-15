# MicroShift CI Artifact Primer

Reference for analyzing MicroShift Prow job artifacts. Read this when
unfamiliar with the artifact layout — it answers "which file answers
which question".

## Job types

- **Scenario-based e2e jobs** (`e2e-aws-tests-*`): the
  `openshift-microshift-e2e-metal-tests` step boots ~20 VM-based test
  scenarios on a hypervisor host. Failures are per-scenario; "the job
  failed" usually means at least 1 scenario failed.
- **Direct-test jobs** (`*-ocp-conformance-*`, `e2e-aws-ai-model-serving-nightly`,
  `e2e-aws-footprint-and-performance-*`): run their test suite directly,
  no scenario fan-out. Job history IS the test history for these.

## Test framework

Tests are written in [Robot Framework](https://robotframework.org).
Suites live in `test/suites/` as `.robot` files. Shared keywords and
Python helpers live in `test/resources/` (e.g. `common.resource`,
`microshift-host.resource`, `ostree.resource`, `sos-on-failure-listener.py`).
Each scenario defines which suites to run, the VM image to boot, and
any Robot variables (e.g. `EXPECTED_OS_VERSION`, `TARGET_REF`).

Key runtime settings (overridable per scenario):

- `TEST_EXECUTION_TIMEOUT` — default `30m`; the scenario runner wraps
  Robot Framework in `timeout -v --kill-after=5m <timeout>`. When the
  suite total exceeds this, the current test dies with
  `Execution terminated by signal` and every subsequent test reports
  `Test execution stopped due to a fatal error` — a cascade with ONE
  root cause (the time budget), not independent failures.
- `TEST_RANDOMIZATION` — default `all`; tests run in random order, so
  test ordering in `rf-debug.log` varies between runs.
- `TEST_EXCLUDES` — tag-based exclusion (default `none`).

## Deployment types: ostree vs bootc vs RPM

There are three distinct deployment pipelines for MicroShift on VMs.
Scenarios (`.sh` files) are the same structure for all three, but how
MicroShift gets onto the VM differs:

- **ostree (rpm-ostree)** — images defined as TOML blueprints in
  `test/image-blueprints/`. Built by `osbuild-composer` into
  edge-commit images, installed via kickstart + ISO. Scenarios live
  under `test/scenarios/`.
- **bootc** — images defined as Containerfiles (with Go template
  support) in `test/image-blueprints-bootc/`. Built as OCI container
  images, installed via bootc. Scenarios live under
  `test/scenarios-bootc/`.
- **RPM** — a non-ostree RHEL system installed from a live image
  (`kickstart-liveimg.ks.template` with `main-liveimg.cfg`), similar
  to isolated/offline scenarios. MicroShift may be pre-installed in the
  image or installed at test time via `dnf` from source-built or Brew
  RPM repos. RPM suites live in `test/suites/rpm/` (install,
  upgrade, remove).

The job name indicates which pipeline was used (e.g.
`e2e-aws-tests-bootc-*` vs `e2e-aws-tests-*`). All three produce the
same artifact layout under `scenario-info/`.

## Scenario naming

Scenario names encode OS image, MicroShift version source, and suite.
The `@` separator chains stages left-to-right: starting image →
intermediate upgrades → final image → test suite.

### Version-source markers

- `src` — built from source (the code in the PR or branch)
- `base` — built from the PR's target branch
- `prel` — previous minor release (Y-1 as a released build)
- `crel` — current minor release (already-released RPMs: EC, RC, or
  z-stream); skipped shortly after branch cut before the first EC
- `lrel` — latest available release (EC, RC, or z-stream) from internal
  Red Hat staging repositories
- `zprel` — z-previous release: latest z-stream from the rhocp repository
- `y1` / `y2` — Y-1 / Y-2 minor versions back (e.g. on release-4.22,
  `y1` = 4.21, `y2` = 4.20); also spelled `yminus1` / `yminus2` in
  some scenario filenames

### OS version tokens

- `el96` / `el98` / `el102` — RHEL 9.6 / 9.8 / 10.2

### Reading multi-@ names

| Name | Meaning |
| ---- | ------- |
| `el96-lrel@standard1` | RHEL 9.6 + latest release of MicroShift, standard suite 1 |
| `el94-y2@el96-lrel@standard1` | Start on RHEL 9.4 + Y-2 MicroShift, upgrade to RHEL 9.6 + latest release of MicroShift, run standard suite 1 |
| `el96-yminus2@prel@src@delta-upgrade-ok` | Start on RHEL 9.6 + Y-2, upgrade through Y-1 (prel) to source, using static deltas |

The last `@`-segment is always the test suite or test type.

### Suite tokens

`standard1`/`standard2`, `lvm`, `dual-stack`, `ipv6`, `multi-nic`,
`low-latency`, `ginkgo-tests`, `ai-model-serving-online`, `osconfig`,
`storage`, `tlsv13-*`, `multi-config-*`, `c2cc`, `c2cc-ipv6`,
`c2cc-ipsec`, `upgrade-ok`, `upgrade-fails-*`, `auto-recovery`,
`greenboot`, `fips`, `offline`, `isolated-net`, `cncf-conformance`,
`rpm-*`, `delta-upgrade-*`

### Disabled scenarios

Scenario files ending in `.sh.disabled` are skipped by the CI runner.
They appear in the repo but produce no artifacts.

Scenario definitions (what each one deploys and runs) live in
openshift/microshift under `test/scenarios*/` (e.g.
`test/scenarios-bootc/el9/`); Robot Framework suites under
`test/suites/`.

## How scenarios run in CI

All scenarios run **in parallel** via GNU `parallel`:

```text
parallel --results <scenario-info>/{/.}/boot_and_run.log \
    --delay 5 \
    bash -x ./bin/scenario.sh create-and-run ::: <scenarios>/*.sh
```

`create-and-run` executes two phases per scenario:

1. **create** (`action_create`) — load scenario script, create VMs,
   wait for greenboot health check, collect SOS report + PCP archives
   on failure. Infrastructure junit goes to `phase_create/junit.xml`.
2. **run** (`action_run`) — execute `scenario_run_tests()` (which calls
   Robot Framework), collect SOS + PCP on failure. Infrastructure junit
   goes to `phase_run/junit.xml`.

Because scenarios run in parallel on the same hypervisor, resource
contention (CPU, disk I/O, memory) can cause timeouts that don't
reproduce in isolation. These are infrastructure failures — still
report them, but attribute them to shared-hypervisor contention rather
than a product or test bug.

## Where the evidence lives

At the artifact root and, for scenario-specific files, under
`artifacts/<TEST_NAME>/openshift-microshift-e2e-metal-tests/artifacts/scenario-info/<scenario>/`:

| File | Answers |
| ---- | ------- |
| `<ARTIFACTS_DIR>/build-log.txt` | Prow job output — AWS infra and hypervisor errors surface here. The step diagram URL at the end links to the step execution graph |
| `<STEP>/build-log.txt` | Per-step log — each CI step has its own `build-log.txt` |
| `junit.xml` | Which tests failed; the top-level `testsuite name` IS the scenario name |
| `rf-debug.log` | Robot Framework execution trace with timestamps — failures marked `\| FAIL \|`; the primary test-failure evidence |
| `boot_and_run.log` | VM boot + scenario orchestration; timeouts killing the whole scenario show up here (`timeout: sending signal TERM`) |
| `phase_create/junit.xml` | Infrastructure-level junit from VM creation (greenboot check, kickstart, SOS collection) — distinct from the test-level `junit.xml` |
| `phase_run/junit.xml` | Infrastructure-level junit from the test run phase |
| `vms/host1/sos/journal_*.log` | **Plain-text journal exports** — readable without extracting anything; check these FIRST for service failures, x509 errors, OOM kills |
| `vms/host1/sos/sosreport-*.tar.xz` | Full sosreports (see below) |

## Sosreports

- Two types of sosreport are collected:
  1. **On-failure** — the `sos-on-failure-listener.py` Robot Framework
     listener captures a sosreport at each test-case-level keyword
     failure. This report includes the namespaces that the test created
     (detected by tracking Robot variables containing "namespace" or
     "ns"). **Prefer this report**: by the end of the scenario, test
     namespaces are cleaned up and their pod logs are gone.
  2. **End-of-scenario** — collected during teardown regardless of
     pass/fail. Contains system state but may lack test-created
     workloads.
  Match report to failure by comparing the sosreport's capture timestamp
  with the failure timestamp from `rf-debug.log`.
- **Journals:** use the plain-text `journal_*.log` files next to the
  sosreport tarballs — no extraction needed.
- **Pod logs:** extract a specific tarball with
  `bash plugins/shared/scripts/extract-sosreport.sh <tarball>`.
  This extracts pod logs, inspect outputs, and cluster-scoped
  resources (not journals or the full filesystem) into
  `<tarball-parent>/sos-extracted/<sosreport-name>/`.
- Inside an extracted report:
  - Per-namespace pod logs:
    `sos_commands/microshift/namespaces/<ns>/pods/<pod>/<container>/<container>/logs/current.log`
    — and `previous.log` when the container was restarted. **The tail of
    `previous.log` states why the container died** (fatal error, leader
    election lost, panic).
  - Cluster-scoped resources:
    `sos_commands/microshift/cluster-scoped-resources/` — nodes, CRDs,
    webhooks.
  - Component inspect outputs: `sos_commands/*/inspect_*`.

## Greenboot health check

Before running tests, the scenario runner waits for
`greenboot-healthcheck.service` to reach `exited` state. This verifies
MicroShift started successfully. If greenboot fails or times out
(`VM_GREENBOOT_TIMEOUT`), the scenario aborts with
`pre_test_greenboot_check FAILED` in `phase_create/junit.xml` and no
tests run.

In the journal, look for `40_microshift_running_check.sh` lines —
they show which deployments greenboot waited for and when each became
ready. The final verdict is
`greenboot[...]: Script '40_microshift_running_check.sh' SUCCESS/FAILURE`.

## Reading the journal for component failures

A timestamped component timeline from these events supports fault attribution:

- Pod lifecycle: kubelet `SyncLoop (PLEG)` events, `Created container` /
  `Started container` (crio), `SyncLoop (probe)` readiness transitions,
  `prober.go "Probe failed"` lines.
- **Two `Created container` events for the same pod = the first instance
  died and was restarted** — a single startup narrative is wrong; read
  `previous.log` for the exit reason.
- etcd pressure: `apply request took too long` warnings indicate
  apiserver/etcd latency (can cost components their leader-election
  leases).

## Common failure patterns

### Timeout cascade

When `TEST_EXECUTION_TIMEOUT` (default 30m) expires, the `timeout`
command sends TERM to Robot Framework. The current test dies with
`Execution terminated by signal` and every subsequent test reports
`Test execution stopped due to a fatal error`. This is a cascade
with ONE root cause — identify what consumed the time budget.

### Greenboot failure masking test failures

If greenboot fails, no tests run — the only junit is the
infrastructure-level `phase_create/junit.xml` recording the
`pre_test_greenboot_check FAILED`. The root cause is in the journal
(MicroShift didn't start, a deployment didn't become ready, etc.).

### Resource contention from parallel scenarios

All scenarios share a single hypervisor. When many scenarios boot
simultaneously, CPU/memory/disk contention can cause:

- Slow MicroShift startup → greenboot timeouts
- etcd `apply request took too long` → leader election loss
- Image pull timeouts

Report these as infrastructure failures attributed to
shared-hypervisor contention.

## PCP performance metrics

When `graphs_dir` is provided, it contains JSON files produced by
`plugins/microshift-ci/scripts/pcp-graphs/generate-graphs.sh`:

| File | Contents |
| ---- | ------- |
| `cpu.json` | Per-second CPU utilization samples |
| `mem.json` | Memory usage samples (RSS, cache, swap) |
| `io.json` | Disk I/O samples (throughput, IOPS, await latency) |
| `disk.json` | Filesystem usage samples (used, available, percent) |

Each file is a JSON object with metric names as keys and arrays of
timestamped samples as values. Correlate timestamps with the failure
window from `rf-debug.log` or journal entries. Look for sustained
patterns (4+ consecutive samples above threshold), not isolated spikes —
a single high `await` value is noise; six consecutive minutes of
`await > 500ms` is I/O contention.

## Source correlation

When a MicroShift source checkout is available, use `repo-log.sh` to
list potentially related commits:

```text
bash plugins/microshift-ci/scripts/repo-log.sh <SOURCE_DIR> \
    --since <1_MONTH_BEFORE_FINISHED> \
    --until <FINISHED_DATE> \
    --paths test/
```

Derive `FINISHED_DATE` from `finished.json`. Drop `--paths` to see all
changes. Name candidate commits in the causal chain when timing and
touched paths match.

## Search/index coverage of external tools

- Sippy tracks these jobs at **job level only** — scenario junits and RF
  suite names are not ingested.
- Search.CI indexes build logs and junit, **not** scenario-internal logs
  (`rf-debug.log` content is not searchable).
