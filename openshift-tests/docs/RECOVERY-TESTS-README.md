# Recovery test workflows

Run commands from `<repo>/openshift-tests`. See `../README.md` for prerequisites,
profiles, and common entrypoints, and `TNF-AND-CLUSTER.md` for cluster access.
Use `scripts/run-suite.sh --help` and `scripts/run-test.sh --help` for option lists.

## Run and review tests

```bash
# Inspect the recovery test selection before running
scripts/run-suite.sh --profile recovery --list-only

# Repeat the whole suite three times in batch mode
scripts/run-suite.sh --profile recovery --repeat 3 --no-captures

# Review each test interactively, with diagnostic captures
scripts/run-suite.sh --profile recovery --interactive --with-captures
```

Batch mode repeats the whole suite. Interactive mode runs each selected test
`--repeat N` times, prompting before each test and between tests. Choose `n` to
skip a test or `q` to quit; a pause between tests waits for Enter.

For a focused investigation, repeat a single test and stop when that test fails:

```bash
scripts/run-test.sh \
    --test "recovery restore quorum" \
    --repeat 5 \
    --no-captures \
    --stop-on-fail
```

`--stop-on-fail` ignores monitor-only and suite-level failures. Use
`--stop-on-match "text"` to stop when a specific message appears in the test log.
Repeat `--test` to select multiple tests; a single iteration without stop controls
batches those selections into one invocation.

The direct runner enables captures by default; the suite runner disables them
by default. Set `--with-captures` or `--no-captures` explicitly when switching
between runners. The direct runner also accepts `--wait-for-cluster` to wait for
Ready nodes and an Available ClusterVersion before starting.

Captures add API polling, SSH sessions, and log I/O. For quick verification, use
`--no-captures`. To collect only selected diagnostics, disable automatic captures
and start the required `scripts/captures/capture-*.sh` collectors separately.

## Results and captures

The runners print the session directory under `runs/`. A normal run has:

```text
runs/<session>-<timestamp>/
├── summary.tsv
└── <iteration>/
    ├── test/
    │   ├── openshift-tests-timestamped.log
    │   ├── runner.log
    │   └── junit/
    └── captures/                    # when enabled
        ├── capture-pids-*.txt
        └── collector logs
```

Suite runs also save `test/openshift-tests-raw.log`. Upgrade results live directly
under the session's `test/` and `captures/` directories.

Captures keep each data source in its own files: `virsh-*.log` for VM history,
`ovn-k-*-follow-*.log` and `nnid-follow-*.log` for continuous network logs, and
`ceo-*.log` for operator logs. The OVN sampler records API/OVS/SB state, gateway
annotations, and pod placement; the update-job sampler records job/pod state.
When running either sampler standalone, start the other collectors as needed.

For collectors that write their own files, `.out` contains startup/status messages
and `.log` contains diagnostics. BMO and update-setup job output is saved only in
the `.log` file; use `tail -f` for a live view when running them standalone.

Each summary row records an invocation; a batch row can cover many tests. Inspect
JUnit and test logs for individual test and monitor outcomes. `run-suite.sh` exits
0 after normal batch completion even if tests or monitors fail; preflight failures
return nonzero. The upgrade profile returns the underlying
`run-upgrade` exit status. Keep original captures intact; follow
`../AGENTS.md` when preparing evidence to share.

## Binary selection and configuration

Both runners extract the matching binary from the cluster's release payload by
default, clearing the managed `tests-bin/payload/` directory before extraction.
Set `OPENSHIFT_TESTS=/absolute/path/to/openshift-tests` to use a preinstalled or
locally built executable unchanged instead. An empty value selects extraction.
Interactive suites extract once and pass the selected path to each test runner.

Set `PROXY_ENV` to the cluster's `proxy.env` as described in the main README.
Hypervisor access uses `HYPERVISOR_IP` or `EC2_PUBLIC_IP`, with `SSH_USER` defaulting
to `ec2-user` and `SSH_KEY_PATH` to `~/.ssh/id_redhat` (falling back to
`~/.ssh/id_ed25519` if the selected key is absent).

Monitor defaults match the `baremetalds-two-node-fencing-recovery` workflow.
Override them with `OPENSHIFT_TESTS_DISABLE_MONITORS` and
`OPENSHIFT_TESTS_CLUSTER_STABILITY` (default: `Disruptive`).
See `TNF-AND-CLUSTER.md` for monitor policy and the upstream suite definitions.

The `recovery` and `ra-verification` profiles query resource-agent versions after
running. Node replacement can restore a stock RPM on one master; the version
query helps identify differing builds. `ra-verification` excludes node replacement.
