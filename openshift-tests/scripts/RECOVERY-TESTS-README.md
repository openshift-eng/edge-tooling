# OpenShift Test Suite Infrastructure

Generic infrastructure for running OpenShift test suites interactively or in batch.

## Quick Start

```bash
cd <repo>/openshift-tests/scripts

# Run all two-node recovery tests without captures (batch mode)
./run-suite.sh --profile recovery --no-captures

# Run with captures enabled
./run-suite.sh --profile recovery --with-captures

# Interactive mode with captures
./run-suite.sh --profile recovery --interactive --with-captures

# Run a different profile (e2e conformance, dualreplica, cert-rotation, upgrade)
./run-suite.sh --profile e2e

# List available profiles
./run-suite.sh --list-profiles

# List available tests for a profile
./list-tests.sh --profile recovery
```

## Available Scripts

### `run-suite.sh` - Suite Runner

Run any test suite in batch or interactive mode.

**Usage:**

```bash
# Run all two-node tests without captures (batch mode - default)
./run-suite.sh --no-captures

# Run with captures enabled
./run-suite.sh --with-captures

# Interactive mode with captures (step-by-step review)
./run-suite.sh --interactive --with-captures

# Run different suite with filter
./run-suite.sh --suite openshift/conformance/parallel --filter upgrade

# List tests without running
./run-suite.sh --list-only

# Run each test 3 times
./run-suite.sh --repeat 3 --no-captures
```

**Options:**

- `--profile NAME` - Friendly profile that selects a suite (+ optional filter or
  run-upgrade mode); see [Profiles](#profiles). `--suite`/`--filter` override it.
- `--suite SUITE` - Test suite to run (default: openshift/two-node)
- `--filter PATTERN` - Regex to filter test names (default: "." - all tests)
- `--to-image IMAGE` - Target release image for the `upgrade` profile (or set `UPGRADE_TO_IMAGE`)
- `--repeat N` - Run each test N times
- `--with-captures` - Enable diagnostic captures
- `--no-captures` - Disable captures (default)
- `--interactive` - Interactive mode with user confirmation between tests
- `--list-only` - List tests and exit
- `--list-profiles` - Show available profiles and exit
- `--name NAME` - Session name (auto-generated if not specified)

**Interactive mode prompts:**

- Before starting: confirm you want to run the suite
- Before each test: [Y]es run / [n]o skip / [q]uit
- After each test: confirm review complete before continuing

### `list-tests.sh` - Test Discovery

List tests from any suite with optional filtering.

**Usage:**

```bash
# List all two-node tests (default)
./list-tests.sh

# List recovery tests (openshift/two-node)
./list-tests.sh --profile recovery

# List every DualReplica feature test across all suites
./list-tests.sh --profile dualreplica

# List upgrade tests
./list-tests.sh --suite openshift/conformance/parallel --filter upgrade

# Generate --test arguments for run-test.sh
./list-tests.sh --profile recovery --command-line
```

**Options:**

- `--profile NAME` - Friendly profile that selects a suite (+ optional filter);
  see [Profiles](#profiles). `--suite`/`--filter` override it.
- `--suite SUITE` - Test suite (default: openshift/two-node)
- `--filter PATTERN` - Regex filter (default: "." - all tests)
- `--command-line, -c` - Output as --test arguments
- `--list-profiles` - Show available profiles and exit

## Profiles

A **profile** is a friendly name that maps to a test target: a suite, an
optional default name/label filter, and a run mode. Profiles are defined once in
`test-helpers.sh` (`resolve_profile`) and shared by both `list-tests.sh` and
`run-suite.sh`. Explicit `--suite`/`--filter` always override a profile's defaults.

| Profile | Suite | Default filter | Mode |
|---------|-------|----------------|------|
| `e2e` | `openshift/conformance/parallel` | — | run |
| `recovery` | `openshift/two-node` | — | run |
| `dualreplica` | `all` | `DualReplica` (`[OCPFeatureGate:DualReplica]`) | run |
| `cert-rotation` | `openshift/etcd/certrotation` | — | run |
| `upgrade` | `all` | — | run-upgrade (needs `--to-image`) |

Notes:

- `dualreplica` searches **all** tests for the DualReplica feature gate — it is
  deliberately not limited to `openshift/two-node`, so those tests are found
  wherever they live.
- `cert-rotation`'s suite name should be verified once against the target cluster
  with `openshift-tests run openshift/etcd/certrotation --dry-run`.
- `upgrade` runs `openshift-tests run-upgrade` and requires a target release image
  via `--to-image` or `UPGRADE_TO_IMAGE`.

### `run-test.sh` - Generic Test Runner

Run one or more recovery tests with optional diagnostic captures.

**Key Features:**

- Multiple tests via `--test` (repeatable)
- Iterations via `--repeat N`
- Optional captures with `--with-captures` / `--no-captures`
- Stop on error pattern matching
- Organized output in `scratch/runs/`

**Usage:**

```bash
# Single test, no captures
./run-test.sh \
    --test "cluster recovers when a permanently failed node needing manual recovery is replaced" \
    --no-captures

# Multiple specific tests, 3 iterations
./run-test.sh \
    --test "etcd recovery should recover from network disruption with etcd member re-addition" \
    --test "etcd recovery should recover from graceful node shutdown with etcd member re-addition" \
    --test "cluster recovers when a permanently failed node needing manual recovery is replaced" \
    --repeat 3 \
    --no-captures

# All recovery tests, 3 iterations, no captures (RECOMMENDED)
./run-test.sh \
    $(./list-tests.sh --profile recovery --command-line) \
    --repeat 3 \
    --no-captures

# With captures enabled (diagnostic)
./run-test.sh \
    --test "cluster recovers when a permanently failed node needing manual recovery is replaced" \
    --repeat 1 \
    --with-captures
```

### `extract-tests-binary.sh` - Binary Extraction

Extract openshift-tests binary from cluster payload.

**Usage:**

```bash
./extract-tests-binary.sh
```

Extracts the `tests` image from your cluster's current release and pulls the openshift-tests binary to `scratch/tests-bin/`.

## Common Workflows

### Batch Testing (Default)

Run full suite non-interactively:

```bash
# All two-node tests without captures
./run-suite.sh --no-captures

# With captures enabled
./run-suite.sh --with-captures

# Run each test 3 times
./run-suite.sh --repeat 3 --no-captures

# Custom suite
./run-suite.sh --suite openshift/conformance/parallel --filter upgrade --no-captures
```

### Interactive Testing with Review

Run tests one at a time with user confirmation between tests:

```bash
# Interactive mode with captures
./run-suite.sh --interactive --with-captures

# Interactive without captures
./run-suite.sh --interactive --no-captures

# Run each test 3 times interactively
./run-suite.sh --interactive --with-captures --repeat 3
```

**Features:**

- Prompts before each test: [Y]es / [n]o skip / [q]uit
- Wait for user confirmation between tests
- Tests run in alphabetical order (node replacement typically last)

### Test Single Recovery Test Multiple Times

```bash
./run-test.sh \
    --test "recovery restore quorum" \
    --repeat 5 \
    --no-captures
```

### Test with Captures (Diagnostic Mode)

When you need full diagnostics for analysis:

```bash
./run-test.sh \
    --test "cluster recovers when a permanently failed node needing manual recovery is replaced" \
    --repeat 1 \
    --with-captures
```

## Output Structure

Results are stored in `scratch/runs/`:

```text
scratch/runs/
└── session-name-TIMESTAMP/
    ├── summary.tsv                         # Tab-separated summary
    ├── iter-01-test-01-TIMESTAMP-test-name/
    │   ├── test/
    │   │   ├── openshift-tests-raw.log
    │   │   ├── openshift-tests-timestamped.log
    │   │   ├── runner.log
    │   │   └── junit/
    │   └── captures/  (only with --with-captures)
    │       ├── capture-pids-*.txt
    │       └── various capture logs
    ├── iter-02-test-01-TIMESTAMP-test-name/
    └── iter-03-test-01-TIMESTAMP-test-name/
```

## Binary Management

All scripts use `test-helpers.sh` for binary discovery.

**Binary search order:**

1. `OPENSHIFT_TESTS` env var (explicit override)
2. `scratch/tests-bin/openshift-tests` (local)
3. `~/.cache/openshift-tests/openshift-tests` (shared cache)

**Extract binary:**

```bash
# To scratch/tests-bin (recommended - shared with existing scripts)
cd <repo>/openshift-tests
oc adm release extract --tools --command=openshift-tests --to=tests-bin

# Or to cache
mkdir -p ~/.cache/openshift-tests
oc adm release extract --tools --command=openshift-tests --to=~/.cache/openshift-tests
```

## Environment Configuration

### Proxy Setup

`PROXY_ENV` is **required** (no personal default is baked in). Point it at your
cluster's `proxy.env`:

```bash
export PROXY_ENV=<two-node-toolbox>/deploy/openshift-clusters/proxy.env
./run-test.sh --test "test name" --no-captures
```

If it is unset, the scripts exit immediately with a message naming the variable.

### Hypervisor Configuration

Configured from proxy.env or environment:

- `HYPERVISOR_IP` (or `EC2_PUBLIC_IP` from proxy.env)
- `SSH_USER` (default: ec2-user)
- `SSH_KEY_PATH` (default: ~/.ssh/id_redhat)

### Monitor Configuration

Matches openshift/release baremetalds-two-node-fencing-recovery workflow:

```bash
# Disabled monitors (default)
OPENSHIFT_TESTS_DISABLE_MONITORS='etcd-log-analyzer,legacy-cvo-invariants,legacy-etcd-invariants,node-lifecycle,oc-adm-upgrade-status'

# Cluster stability (default)
OPENSHIFT_TESTS_CLUSTER_STABILITY=Disruptive
```

Override via environment variables if needed.

## Tips

### Review Tests Before Running

```bash
# See what tests will run
./list-tests.sh --profile recovery

# Generate the command but don't run it (dry run via echo)
echo "./run-test.sh \\"
echo "    $(./list-tests.sh --profile recovery --command-line) \\"
echo "    --repeat 3 \\"
echo "    --no-captures"
```

### Run Subset of Tests

```bash
# Just the "restore" tests
./run-test.sh \
    --test "recovery restore full cluster from backup" \
    --test "recovery restore quorum" \
    --repeat 3 \
    --no-captures
```

### Stop on First Failure Pattern

```bash
./run-test.sh \
    --test "cluster recovers when a permanently failed node needing manual recovery is replaced" \
    --repeat 5 \
    --no-captures \
    --stop-on-match "timed out waiting for the learner to be promoted"
```

## Architecture

**Core scripts:**

- `run-suite.sh` - runner for any suite/profile (batch, interactive, or upgrade)
- `list-tests.sh` - generic test discovery with profile/suite/filter options
- `run-test.sh` - batch test runner (worker)
- `test-helpers.sh` - shared library (logging, binary management, env setup, profiles)
- `extract-tests-binary.sh` - binary extraction helper

**Replaced by profiles / flags (no longer separate scripts):**

- ~~`list-recovery-tests.sh`~~ → `--profile recovery`
- ~~`run-dualreplica-tests.sh`~~ → `--profile dualreplica`
- ~~`run-node-replacement-test-3x.sh`~~ → use `--repeat 3`
- ~~`run-node-replacement-with-captures.sh`~~ → use `--with-captures`

**Benefits:**

- Single generic implementation, no duplicate code
- Interactive mode for test-by-test review
- Batch mode for automated runs
- Consistent behavior across all test types
- Simpler maintenance

## Shared Library

All scripts now use `test-helpers.sh` for:

- Logging (`log()`, `log_info()`, `log_warn()`, `log_error()`)
- Binary management (`get_openshift_tests_binary()`, `verify_openshift_tests_binary()`)
- Environment setup (`load_proxy_env()`, `setup_hypervisor_config()`)
- Utilities (`sanitize_name()`, `ts()`)

See `test-helpers.sh` for available functions.
