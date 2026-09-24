#!/usr/bin/bash
#
# Test suite runner
#
# Runs all tests in a suite (batch mode by default, optional interactive mode).
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared test helpers
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/test-helpers.sh"

SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNS_ROOT="${SCRATCH_ROOT}/runs"
mkdir -p "${RUNS_ROOT}"

# Setup default monitor configuration (can be overridden by env vars)
if [[ ! -v OPENSHIFT_TESTS_DISABLE_MONITORS ]]; then
    export OPENSHIFT_TESTS_DISABLE_MONITORS='etcd-log-analyzer,legacy-cvo-invariants,legacy-etcd-invariants,node-lifecycle,oc-adm-upgrade-status'
fi
if [[ ! -v OPENSHIFT_TESTS_CLUSTER_STABILITY ]]; then
    export OPENSHIFT_TESTS_CLUSTER_STABILITY=Disruptive
fi
export OPENSHIFT_SKIP_EXTERNAL_TESTS=1

# Parse arguments
SUITE="openshift/two-node"
FILTER="."
SESSION_NAME=""
REPEAT=1
LIST_ONLY=false
INTERACTIVE=false
WITH_CAPTURES=false
PROFILE=""
SUITE_SET=false
FILTER_SET=false
RUN_MODE="run"
DISCOVER_SUITES=""
EXCLUDE=""
GATHER_RA="false"
UPGRADE_TO_IMAGE="${UPGRADE_TO_IMAGE:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --suite)
            SUITE="$2"
            SUITE_SET=true
            shift 2
            ;;
        --filter)
            FILTER="$2"
            FILTER_SET=true
            shift 2
            ;;
        --to-image)
            UPGRADE_TO_IMAGE="$2"
            shift 2
            ;;
        --name)
            SESSION_NAME="$2"
            shift 2
            ;;
        --repeat)
            REPEAT="$2"
            shift 2
            ;;
        --with-captures)
            WITH_CAPTURES=true
            shift
            ;;
        --no-captures)
            WITH_CAPTURES=false
            shift
            ;;
        --interactive)
            INTERACTIVE=true
            shift
            ;;
        --list-only)
            LIST_ONLY=true
            shift
            ;;
        --list-profiles)
            list_profiles
            exit 0
            ;;
        --help|-h)
            cat <<HELP
Usage: $0 [options]

Run all tests in a suite (batch mode by default).

OPTIONS:
    --profile NAME       Friendly profile that selects a suite (+ optional filter
                         or run-upgrade mode). See --list-profiles.
                         --suite/--filter override the profile's defaults.
    --suite SUITE        Test suite to run (default: openshift/two-node)
    --filter PATTERN     Filter test names by regex (default: "." - all tests)
    --to-image IMAGE     Target release image for the 'upgrade' profile
                         (or set UPGRADE_TO_IMAGE).
    --name NAME          Session name (auto-generated if not specified)
    --repeat N           Run each test N times (default: 1)
    --with-captures      Enable diagnostic captures
    --no-captures        Disable diagnostic captures (default)
    --interactive        Run interactively with user confirmation between tests
    --list-only          List tests and exit (no execution)
    --list-profiles      Show available profiles and exit
    --help, -h           Show this help

EXAMPLES:
    # Run all two-node recovery tests without captures (batch mode)
    $0 --profile recovery

    # Verify resource-agents across cert-rotation + two-node WITHOUT node
    # replacement (no reprovision, so both masters stay on the same build)
    $0 --profile ra-verification

    # Run every DualReplica feature test across all suites, with captures
    $0 --profile dualreplica --with-captures

    # Run e2e conformance
    $0 --profile e2e

    # Run an upgrade
    $0 --profile upgrade --to-image registry.example/ocp/release:4.20.0

    # Run interactively with captures
    $0 --profile recovery --interactive --with-captures

    # Run the two-node suite 3 times
    $0 --profile recovery --repeat 3

    # List tests only
    $0 --profile recovery --list-only

NOTES:
    - Batch mode by default (no user prompts)
    - Use --interactive for step-by-step execution with review
    - Results stored in scratch/runs/SESSION-NAME-TIMESTAMP/
    - Requires proxy.env and hypervisor configuration
HELP
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Resolve profile into suite/filter/mode defaults (explicit flags win).
if [[ -n "${PROFILE}" ]]; then
    if ! resolve_profile "${PROFILE}"; then
        exit 1
    fi
    RUN_MODE="${PROFILE_MODE}"
    DISCOVER_SUITES="${PROFILE_DISCOVER_SUITES:-}"
    EXCLUDE="${PROFILE_EXCLUDE:-}"
    GATHER_RA="${PROFILE_GATHER_RA:-false}"
    [[ "${SUITE_SET}" == "false" ]] && SUITE="${PROFILE_SUITE}"
    if [[ "${FILTER_SET}" == "false" && -n "${PROFILE_FILTER}" ]]; then
        FILTER="${PROFILE_FILTER}"
    fi
fi

# Auto-generate session name if not provided
if [[ -z "$SESSION_NAME" ]]; then
    if [[ -n "${PROFILE}" ]]; then
        SESSION_NAME="${PROFILE}-suite"
    else
        SUITE_NAME="${SUITE//\//-}"
        SESSION_NAME="${SUITE_NAME}-suite"
    fi
fi

# Get openshift-tests binary
TESTS_BIN=$(get_openshift_tests_binary)
if ! verify_openshift_tests_binary "${TESTS_BIN}"; then
    log_error "Could not find openshift-tests binary"
    log_info "Extract it first: ./extract-tests-binary.sh"
    exit 1
fi

# Load hypervisor config
if ! load_proxy_env; then
    log_error "Failed to load proxy.env"
    exit 1
fi
setup_hypervisor_config

# Get all tests from suite
setup_test_provider

# ----------------------------------------------------------------------------
# Upgrade mode: openshift-tests run-upgrade (no per-test discovery/iteration)
# ----------------------------------------------------------------------------
if [[ "${RUN_MODE}" == "upgrade" ]]; then
    if [[ -z "${UPGRADE_TO_IMAGE}" ]]; then
        log_error "Upgrade profile requires a target image."
        log_info "Set --to-image <release-image> or export UPGRADE_TO_IMAGE."
        exit 1
    fi

    log_info "Mode: UPGRADE (run-upgrade)"
    log_info "Upgrade suite: ${SUITE}"
    log_info "Target image:  ${UPGRADE_TO_IMAGE}"

    # Monitor / stability args (same conventions as the run path)
    DISABLE_MONITOR_ARGS=()
    CLUSTER_STABILITY_ARGS=()
    if [[ -n "${OPENSHIFT_TESTS_DISABLE_MONITORS:-}" ]]; then
        DISABLE_MONITOR_ARGS+=("--disable-monitor=${OPENSHIFT_TESTS_DISABLE_MONITORS}")
    fi
    if [[ -n "${OPENSHIFT_TESTS_CLUSTER_STABILITY:-}" ]]; then
        CLUSTER_STABILITY_ARGS+=("--cluster-stability=${OPENSHIFT_TESTS_CLUSTER_STABILITY}")
    fi
    read -ra EXTRA_ARGS <<< "${OPENSHIFT_TESTS_EXTRA_ARGS:-}"

    SESSION_TS="$(date -u +%Y%m%d-%H%M%S)"
    SESSION_DIR="${RUNS_ROOT}/${SESSION_NAME}-${SESSION_TS}"
    RUN_DIR="${SESSION_DIR}/test"
    JUNIT_DIR="${RUN_DIR}/junit"
    mkdir -p "${JUNIT_DIR}"
    RAW_LOG="${RUN_DIR}/openshift-tests-raw.log"
    TIMED_LOG="${RUN_DIR}/openshift-tests-timestamped.log"
    CONSOLE_LOG="${RUN_DIR}/runner.log"

    log_info "Run directory: ${RUN_DIR}"

    if [[ "${WITH_CAPTURES}" == "true" ]]; then
        mkdir -p "${SESSION_DIR}/captures"
        CAPTURE_LOG_DIR="${SESSION_DIR}/captures" CAPTURE_TIMESTAMP="${SESSION_TS}" \
            "${SCRIPT_DIR}/run-all-captures.sh" > "${SESSION_DIR}/captures/start-captures.log" 2>&1
        CAP_PID_FILE="${SESSION_DIR}/captures/capture-pids-${SESSION_TS}.txt"
        log_info "Captures started: ${CAP_PID_FILE}"
    fi

    set +e
    "${TESTS_BIN}" run-upgrade "${SUITE}" \
        --to-image "${UPGRADE_TO_IMAGE}" \
        --provider "${TEST_PROVIDER}" \
        --with-hypervisor-json="${HYPERVISOR_JSON}" \
        "${DISABLE_MONITOR_ARGS[@]}" \
        "${CLUSTER_STABILITY_ARGS[@]}" \
        --timeout=120m \
        -o "${RAW_LOG}" \
        --junit-dir "${JUNIT_DIR}" \
        "${EXTRA_ARGS[@]}" \
        2>&1 | while IFS= read -r line; do
            printf "%s %s\\n" "$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")" "${line}"
        done | tee -a "${TIMED_LOG}" >> "${CONSOLE_LOG}"
    TEST_STATUS=${PIPESTATUS[0]}
    set -e

    if [[ "${WITH_CAPTURES}" == "true" ]] && [[ -f "${CAP_PID_FILE}" ]]; then
        "${SCRIPT_DIR}/stop-all-captures.sh" "${CAP_PID_FILE}" > "${SESSION_DIR}/captures/stop-captures.log" 2>&1
        log_info "Captures stopped"
    fi

    if [[ ${TEST_STATUS} -eq 0 ]]; then
        log_info "✓ Upgrade run passed (results: ${RUN_DIR})"
    else
        log_error "✗ Upgrade run failed with exit code ${TEST_STATUS} (results: ${RUN_DIR})"
    fi
    exit "${TEST_STATUS}"
fi

# Suites to DISCOVER test names from. A profile may curate tests from several
# suites (e.g. cert-rotation + two-node); in that case the actual run uses
# ${SUITE} (a superset such as 'all') restricted to the discovered union via
# --file. Defaults to just ${SUITE}.
# shellcheck disable=SC2206  # intentional word-splitting into an array
DISCOVER_LIST=(${DISCOVER_SUITES:-${SUITE}})

log_info "Discovering tests from: ${DISCOVER_LIST[*]}"
[[ "${FILTER}" != "." ]] && log_info "Filter pattern: ${FILTER}"
[[ -n "${EXCLUDE}" ]] && log_info "Exclude pattern: ${EXCLUDE}"

# Collect the raw (unquoted) test names from every discovery suite.
RAW_TESTS=()
for dsuite in "${DISCOVER_LIST[@]}"; do
    mapfile -t SUITE_TESTS < <("${TESTS_BIN}" run "${dsuite}" \
        --provider "${TEST_PROVIDER}" \
        --with-hypervisor-json="${HYPERVISOR_JSON}" \
        --dry-run 2>&1 | \
        grep '^"' | \
        sed 's/^"\(.*\)"$/\1/')
    if [[ ${#SUITE_TESTS[@]} -eq 0 ]]; then
        log_error "Discovered 0 tests in suite '${dsuite}'."
        log_error "Verify the suite name: ${TESTS_BIN} run ${dsuite} --dry-run"
        exit 1
    fi
    log_info "  ${dsuite}: ${#SUITE_TESTS[@]} tests"
    RAW_TESTS+=("${SUITE_TESTS[@]}")
done

# Apply the include filter, then the exclude filter, then dedup across suites.
mapfile -t TESTS < <(
    printf '%s\n' "${RAW_TESTS[@]}" \
        | grep -iE "${FILTER}" \
        | { if [[ -n "${EXCLUDE}" ]]; then grep -ivE "${EXCLUDE}"; else cat; fi; } \
        | sort -u
)

if [[ ${#TESTS[@]} -eq 0 ]]; then
    log_error "No tests left after filter='${FILTER}' exclude='${EXCLUDE}'"
    exit 1
fi

echo ""
log_info "Found ${#TESTS[@]} tests (from: ${DISCOVER_LIST[*]})"
echo ""

# Decide whether to pin the exact set via --file. openshift-tests --file honors
# the test SET (exact-match membership) but NOT the file's order -- origin
# shuffles the surviving set with a hardcoded seed (cmd_runsuite.go: seed=42)
# and exposes no order-preserving flag. So --file is used purely to constrain
# WHICH tests run when the set is a curated union/subset that differs from
# running ${SUITE} whole: i.e. we discovered from >1 suite, or we excluded tests.
USE_FILE=false
if [[ ${#DISCOVER_LIST[@]} -gt 1 || -n "${EXCLUDE}" ]]; then
    USE_FILE=true
fi

if [[ "$LIST_ONLY" == "true" ]]; then
    for test in "${TESTS[@]}"; do
        echo "  - ${test}"
    done
    exit 0
fi

log_info "Session: ${SESSION_NAME}"
log_info "Repeats per test: ${REPEAT}"
if [[ "${WITH_CAPTURES}" == "true" ]]; then
    log_info "Captures: ENABLED"
else
    log_info "Captures: DISABLED"
fi
if [[ "${INTERACTIVE}" == "true" ]]; then
    log_info "Mode: INTERACTIVE"
else
    log_info "Mode: BATCH"
fi
echo ""

# Confirm before starting in interactive mode
if [[ "${INTERACTIVE}" == "true" ]]; then
    read -p "Start interactive test suite? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Aborted"
        exit 0
    fi
fi

echo ""
echo "========================================"
if [[ "${INTERACTIVE}" == "true" ]]; then
    echo "Starting Interactive Test Suite"
else
    echo "Starting Test Suite"
fi
echo "========================================"
echo ""

TOTAL_TESTS=${#TESTS[@]}
CURRENT=0

# Build capture args
CAPTURE_ARGS=()
if [[ "${WITH_CAPTURES}" == "true" ]]; then
    CAPTURE_ARGS+=(--with-captures)
else
    CAPTURE_ARGS+=(--no-captures)
fi

# Optimization: batch mode runs suite directly (repeat entire suite N times, not each test N times)
if [[ "${INTERACTIVE}" != "true" ]]; then
    log_info "Running ${SUITE} suite ${REPEAT} time(s) (${TOTAL_TESTS} tests matched by filter)"
    echo ""

    # Create session directory
    SESSION_TS="$(date -u +%Y%m%d-%H%M%S)"
    SESSION_DIR="${RUNS_ROOT}/${SESSION_NAME}-${SESSION_TS}"
    mkdir -p "${SESSION_DIR}"
    SUMMARY_FILE="${SESSION_DIR}/summary.tsv"
    printf "iter\\ttest_index\\tresult\\trun_dir\\tfocus\\n" > "${SUMMARY_FILE}"

    # Setup test provider
    setup_test_provider

    # Build the run selector.
    #   - USE_FILE: pin the exact test SET via --file (curated union/subset). The
    #     file must list names in the SAME double-quoted form that `openshift-tests
    #     --dry-run` emits -- that is what --file matches against (canonical
    #     openshift/release pattern: `run <suite> --dry-run | ... | run <suite>
    #     -f -`). Our discovery strips the surrounding quotes for filtering and
    #     display, so we re-add them when writing the set file. Bare names do NOT
    #     match and the run aborts with "no tests to run". NOTE: --file pins the
    #     SET, not the order (origin shuffles with a fixed seed).
    #   - else FILTER == ".": run the entire suite in its native order.
    #   - else: narrow the suite with --run.
    if [[ "${USE_FILE}" == "true" ]]; then
        SET_FILE="${SESSION_DIR}/test-set.txt"
        printf '"%s"\n' "${TESTS[@]}" > "${SET_FILE}"
        RUN_ARGS=(--file "${SET_FILE}")
        log_info "Pinning test set via --file (${#TESTS[@]} tests): ${SET_FILE}"

        # Pre-flight: openshift-tests --file only reads quoted lines and
        # strconv.Unquote-matches them EXACTLY against test names; a format
        # mismatch silently matches nothing (match-function drops all tests ->
        # "no tests to run"). This also catches a discovery suite whose tests
        # are NOT present in the run suite ${SUITE}. Confirm the set file
        # round-trips BEFORE committing to a long run, and fail fast with a clear
        # message instead of burning a 60m timeout on an empty run.
        preflight_count=$("${TESTS_BIN}" run "${SUITE}" \
            --provider "${TEST_PROVIDER}" \
            --with-hypervisor-json="${HYPERVISOR_JSON}" \
            --file "${SET_FILE}" --dry-run 2>&1 | grep -c '^"' || true)
        if [[ "${preflight_count}" -ne "${#TESTS[@]}" ]]; then
            log_error "Pre-flight --file check matched ${preflight_count}/${#TESTS[@]} tests -- the set file did not round-trip through 'run ${SUITE} --file'."
            log_error "Likely cause: a discovered test is not present in the run suite '${SUITE}' (use a superset suite like 'all'), or a name-quoting mismatch."
            log_error "Set file: ${SET_FILE}"
            log_error "Inspect raw output: ${TESTS_BIN} run ${SUITE} --provider '${TEST_PROVIDER}' --file '${SET_FILE}' --dry-run 2>&1 | head"
            exit 1
        fi
        log_info "Pre-flight --file check OK: ${preflight_count}/${#TESTS[@]} tests match."
    elif [[ "${FILTER}" == "." ]]; then
        # No filter - run entire suite
        RUN_ARGS=()
    else
        # Pass filter as regex to --run
        RUN_ARGS=(--run "${FILTER}")
    fi

    # Setup monitors
    DISABLE_MONITOR_ARGS=()
    CLUSTER_STABILITY_ARGS=()

    if [[ -n "${OPENSHIFT_TESTS_DISABLE_MONITORS:-}" ]]; then
        DISABLE_MONITOR_ARGS+=("--disable-monitor=${OPENSHIFT_TESTS_DISABLE_MONITORS}")
    fi

    if [[ -n "${OPENSHIFT_TESTS_CLUSTER_STABILITY:-}" ]]; then
        CLUSTER_STABILITY_ARGS+=("--cluster-stability=${OPENSHIFT_TESTS_CLUSTER_STABILITY}")
    fi
    read -ra EXTRA_ARGS <<< "${OPENSHIFT_TESTS_EXTRA_ARGS:-}"

    # Repeat the ENTIRE suite N times
    for ((iter=1; iter<=REPEAT; iter++)); do
        echo ""
        log_info "========================================"
        log_info "Suite iteration ${iter}/${REPEAT}"
        log_info "========================================"

        # Create run directory for this iteration
        RUN_TS="$(date -u +%Y%m%d-%H%M%S)"
        RUN_DIR="${SESSION_DIR}/iter-$(printf "%02d" "${iter}")-${RUN_TS}"
        mkdir -p "${RUN_DIR}/test"

        RAW_LOG="${RUN_DIR}/test/openshift-tests-raw.log"
        TIMED_LOG="${RUN_DIR}/test/openshift-tests-timestamped.log"
        CONSOLE_LOG="${RUN_DIR}/test/runner.log"
        JUNIT_DIR="${RUN_DIR}/test/junit"
        mkdir -p "${JUNIT_DIR}"

        log_info "Run directory: ${RUN_DIR}"

        # Start captures if requested
        if [[ "${WITH_CAPTURES}" == "true" ]]; then
            mkdir -p "${RUN_DIR}/captures"
            CAPTURE_LOG_DIR="${RUN_DIR}/captures" CAPTURE_TIMESTAMP="${RUN_TS}" \
                "${SCRIPT_DIR}/run-all-captures.sh" > "${RUN_DIR}/captures/start-captures.log" 2>&1
            CAP_PID_FILE="${RUN_DIR}/captures/capture-pids-${RUN_TS}.txt"
            log_info "Captures started: ${CAP_PID_FILE}"
        fi

        # Run the suite
        log_info "Starting openshift-tests run ${SUITE} (iteration ${iter}/${REPEAT})"
        set +e
        "${TESTS_BIN}" run "${SUITE}" \
            --provider "${TEST_PROVIDER}" \
            --with-hypervisor-json="${HYPERVISOR_JSON}" \
            "${DISABLE_MONITOR_ARGS[@]}" \
            "${CLUSTER_STABILITY_ARGS[@]}" \
            "${RUN_ARGS[@]}" \
            --max-parallel-tests=1 \
            --timeout=60m \
            -o "${RAW_LOG}" \
            --junit-dir "${JUNIT_DIR}" \
            "${EXTRA_ARGS[@]}" \
            2>&1 | while IFS= read -r line; do
                printf "%s %s\\n" "$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")" "${line}"
            done | tee -a "${TIMED_LOG}" >> "${CONSOLE_LOG}"
        TEST_STATUS=${PIPESTATUS[0]}
        set -e

        log_info "Iteration ${iter}/${REPEAT} completed with exit code: ${TEST_STATUS}"

        # Stop captures if running
        if [[ "${WITH_CAPTURES}" == "true" ]] && [[ -f "${CAP_PID_FILE}" ]]; then
            "${SCRIPT_DIR}/stop-all-captures.sh" "${CAP_PID_FILE}" > "${RUN_DIR}/captures/stop-captures.log" 2>&1
            log_info "Captures stopped"
        fi

        # Record to summary
        RESULT="PASS"
        [[ "${TEST_STATUS}" -ne 0 ]] && RESULT="FAIL(${TEST_STATUS})"
        printf "%d\\tALL\\t%s\\t%s\\t%s\\n" "${iter}" "${RESULT}" "${RUN_DIR}" "${SUITE} (${TOTAL_TESTS} tests)" >> "${SUMMARY_FILE}"

        if [[ $TEST_STATUS -eq 0 ]]; then
            log_info "✓ Iteration ${iter}/${REPEAT} passed"
        else
            log_error "✗ Iteration ${iter}/${REPEAT} failed"
        fi
    done

    echo ""
    log_info "All ${REPEAT} iterations complete. Summary: ${SUMMARY_FILE}"
else
    # Interactive mode or repeat>1: run tests one at a time
    for test in "${TESTS[@]}"; do
    CURRENT=$((CURRENT + 1))

    echo ""
    echo "========================================"
    log_info "Test ${CURRENT}/${TOTAL_TESTS}"
    echo "========================================"
    echo ""
    echo "Test: ${test}"
    echo ""

    # Interactive prompt before running
    if [[ "${INTERACTIVE}" == "true" ]]; then
        read -p "Run this test? [Y/n/q] " -n 1 -r
        echo

        case "$REPLY" in
            [Qq])
                log_info "Quit requested - stopping suite"
                exit 0
                ;;
            [Nn])
                log_warn "Skipping test ${CURRENT}/${TOTAL_TESTS}"
                continue
                ;;
            *)
                # Default to Yes - run the test
                ;;
        esac

        echo ""
    fi

    log_info "Running test ${CURRENT}/${TOTAL_TESTS}..."
    echo ""

    # Run the test
    "${SCRIPT_DIR}/run-test.sh" \
        --test "${test}" \
        --repeat "${REPEAT}" \
        "${CAPTURE_ARGS[@]}" \
        --name "${SESSION_NAME}"

    TEST_STATUS=$?

    echo ""
    if [[ $TEST_STATUS -eq 0 ]]; then
        log_info "✓ Test ${CURRENT}/${TOTAL_TESTS} completed"
    else
        log_error "✗ Test ${CURRENT}/${TOTAL_TESTS} failed (exit code: ${TEST_STATUS})"
    fi
    echo ""

    # Interactive prompt after running (don't prompt after last test)
    if [[ "${INTERACTIVE}" == "true" ]] && [[ $CURRENT -lt $TOTAL_TESTS ]]; then
        read -p "Review complete. Continue to next test? [Y/n/q] " -n 1 -r
        echo

        case "$REPLY" in
            [Qq])
                log_info "Quit requested - stopping suite"
                exit 0
                ;;
            [Nn])
                log_info "Pausing - press Enter when ready to continue..."
                read -r
                ;;
            *)
                # Default to Yes - continue
                ;;
        esac
    fi
done
fi

# Report the resource-agents build on both masters (mixed-build check). For
# 'recovery' this catches a node-replacement test having reverted a master to a
# stock RPM; for 'ra-verification' it confirms the two masters stayed on the same
# build throughout (they should, since node replacement was excluded). Non-fatal:
# never fail the run over a diagnostic query.
if [[ "${GATHER_RA}" == "true" ]]; then
    echo ""
    log_info "Gathering resource-agents versions from both masters (mixed-build check)..."
    "${SCRIPT_DIR}/gather-resource-agent-versions.sh" || \
        log_warn "Could not gather resource-agents versions (see errors above)"
fi

echo ""
echo "========================================"
if [[ "${INTERACTIVE}" == "true" ]]; then
    echo "Interactive Test Suite Complete!"
else
    echo "Test Suite Complete!"
fi
echo "========================================"
echo ""
log_info "Completed ${TOTAL_TESTS} tests"
log_info "Results in: scratch/runs/${SESSION_NAME}-*/"
echo ""
