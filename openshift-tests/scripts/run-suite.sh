#!/usr/bin/bash
#
# Test suite runner
#
# Runs all tests in a suite (batch mode by default, optional interactive mode).
#

set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared test helpers
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/utils/test-helpers.sh"

trap stop_captures EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

setup_test_directories
RUNS_ROOT="${RUN_DIR}"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/utils/monitor-helpers.sh"
setup_monitor_defaults
export OPENSHIFT_TESTS_DISABLE_MONITORS OPENSHIFT_TESTS_CLUSTER_STABILITY

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
REPEAT_SET=false
RUN_MODE="run"
DISCOVER_SUITES=""
EXCLUDE=""
GATHER_RA="false"
UPGRADE_TO_IMAGE="${UPGRADE_TO_IMAGE:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --profile)
            require_option_value "$@"
            PROFILE="$2"
            shift 2
            ;;
        --suite)
            require_option_value "$@"
            SUITE="$2"
            SUITE_SET=true
            shift 2
            ;;
        --filter)
            require_option_value "$@"
            FILTER="$2"
            FILTER_SET=true
            shift 2
            ;;
        --to-image)
            require_option_value "$@"
            UPGRADE_TO_IMAGE="$2"
            shift 2
            ;;
        --name)
            require_option_value "$@"
            SESSION_NAME="$2"
            shift 2
            ;;
        --repeat)
            require_option_value "$@"
            REPEAT="$2"
            REPEAT_SET=true
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
    --name NAME          Session name (cannot start with --; auto-generated if unset)
    --repeat N           Repeat whole suite N times in batch mode, or each test
                         N times in interactive mode (default: 1)
    --with-captures      Enable diagnostic captures
    --no-captures        Disable diagnostic captures (default)
    --interactive        Run interactively with user confirmation between tests
    --list-only          List tests and exit (unavailable for the upgrade profile)
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
    - Extracts the cluster payload binary by default; set OPENSHIFT_TESTS to use
      a preinstalled executable unchanged.
    - Batch mode by default (no user prompts)
    - Normal batch completion exits 0 even if tests or monitors fail; inspect
      summary.tsv and JUnit for results. Preflight failures return nonzero.
    - Upgrade mode returns the underlying run-upgrade exit status.
      --filter, --repeat, --interactive and --list-only are unavailable.
    - Use --interactive for step-by-step execution with review
    - Results stored in runs/SESSION-NAME-TIMESTAMP/
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

require_positive_integer "${REPEAT}" --repeat

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

if [[ "${RUN_MODE}" == "upgrade" &&
      ( "${FILTER_SET}" == "true" || "${REPEAT_SET}" == "true" ||
        "${INTERACTIVE}" == "true" || "${LIST_ONLY}" == "true" ) ]]; then
    log_error "--filter, --repeat, --interactive and --list-only are not supported for the upgrade profile."
    exit 1
fi

for regex_name in FILTER EXCLUDE; do
    regex_status=0
    grep -E -- "${!regex_name}" </dev/null >/dev/null 2>&1 || regex_status=$?
    if [[ "${regex_status}" -gt 1 ]]; then
        log_error "Invalid ${regex_name,,} regular expression: ${!regex_name}"
        exit 1
    fi
done

# Auto-generate session name if not provided
if [[ -z "$SESSION_NAME" ]]; then
    if [[ -n "${PROFILE}" ]]; then
        SESSION_NAME="${PROFILE}-suite"
    else
        SUITE_NAME="${SUITE//\//-}"
        SESSION_NAME="${SUITE_NAME}-suite"
    fi
fi

# Load hypervisor config
load_proxy_env
setup_hypervisor_config

setup_test_binary
log "Using openshift-tests: ${OPENSHIFT_TESTS}"

# Get all tests from suite
setup_test_provider

# Shared execution steps for upgrade and batch runs; commands stay explicit below.
prepare_run() {
    JUNIT_DIR="$1/junit"
    mkdir -p "${JUNIT_DIR}"
    RAW_LOG="$1/openshift-tests-raw.log"
    TIMED_LOG="$1/openshift-tests-timestamped.log"
    CONSOLE_LOG="$1/runner.log"
    setup_monitor_args
    read -ra EXTRA_ARGS <<< "${OPENSHIFT_TESTS_EXTRA_ARGS:-}"
}

run_tests() {
    set +e
    "$@" \
        2>&1 | while IFS= read -r line; do
            printf "%s %s\\n" "$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")" "${line}"
        done | tee -a "${TIMED_LOG}" "${CONSOLE_LOG}"
    TEST_STATUS=${PIPESTATUS[0]}
    set -e
}

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

    SESSION_TS="$(date -u +%Y%m%d-%H%M%S)"
    SESSION_DIR="${RUNS_ROOT}/${SESSION_NAME}-${SESSION_TS}"
    RUN_DIR="${SESSION_DIR}/test"
    prepare_run "${RUN_DIR}"

    log_info "Run directory: ${RUN_DIR}"

    start_captures "${SESSION_DIR}" "${SESSION_TS}"

    run_tests "${OPENSHIFT_TESTS}" run-upgrade "${SUITE}" \
        --to-image "${UPGRADE_TO_IMAGE}" \
        --provider "${TEST_PROVIDER}" \
        --with-hypervisor-json="${HYPERVISOR_JSON}" \
        "${MONITOR_ARGS[@]}" \
        --timeout=120m \
        -o "${RAW_LOG}" \
        --junit-dir "${JUNIT_DIR}" \
        "${EXTRA_ARGS[@]}"

    stop_captures

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
    mapfile -t SUITE_TESTS < <("${OPENSHIFT_TESTS}" run "${dsuite}" \
        --provider "${TEST_PROVIDER}" \
        --with-hypervisor-json="${HYPERVISOR_JSON}" \
        --dry-run 2>&1 | \
        grep '^"' | \
        sed 's/^"\(.*\)"$/\1/')
    if [[ ${#SUITE_TESTS[@]} -eq 0 ]]; then
        log_error "Discovered 0 tests in suite '${dsuite}'."
        log_error "Verify the suite name: ${OPENSHIFT_TESTS} run ${dsuite} --dry-run"
        exit 1
    fi
    log_info "  ${dsuite}: ${#SUITE_TESTS[@]} tests"
    RAW_TESTS+=("${SUITE_TESTS[@]}")
done

if [[ -n "${EXCLUDE}" ]] && ! printf '%s\n' "${RAW_TESTS[@]}" | grep -iE -- "${EXCLUDE}" >/dev/null; then
    log_warn "Exclude pattern '${EXCLUDE}' matched no discovered tests; the exclusion may be stale."
fi

# Apply the include filter, then the exclude filter, then dedup across suites.
mapfile -t TESTS < <(
    printf '%s\n' "${RAW_TESTS[@]}" \
        | grep -iE -- "${FILTER}" \
        | { if [[ -n "${EXCLUDE}" ]]; then grep -ivE -- "${EXCLUDE}"; else cat; fi; } \
        | sort -u
)

if [[ ${#TESTS[@]} -eq 0 ]]; then
    log_error "No tests left after filter='${FILTER}' exclude='${EXCLUDE}'"
    exit 1
fi

echo ""
log_info "Found ${#TESTS[@]} tests (from: ${DISCOVER_LIST[*]})"
echo ""

# Pin curated selections with --file: discovery unions, exclusions, and filters.
# This preserves grep's case-insensitive ERE selection; --run uses Go regex.
# --file pins membership only; the runner still chooses test order.
USE_FILE=false
if [[ ${#DISCOVER_LIST[@]} -gt 1 || -n "${EXCLUDE}" || "${FILTER}" != "." ]]; then
    USE_FILE=true
fi

if [[ "$LIST_ONLY" == "true" ]]; then
    for test in "${TESTS[@]}"; do
        echo "  - ${test}"
    done
    exit 0
fi

log_info "Session: ${SESSION_NAME}"
log_info "Repeat count: ${REPEAT}"
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

    # --file expects the quoted names emitted by --dry-run. Discovery strips
    # those quotes for filtering; restore them for exact matches.
    if [[ "${USE_FILE}" == "true" ]]; then
        SET_FILE="${SESSION_DIR}/test-set.txt"
        printf '"%s"\n' "${TESTS[@]}" > "${SET_FILE}"
        RUN_ARGS=(--file "${SET_FILE}")
        log_info "Pinning test set via --file (${#TESTS[@]} tests): ${SET_FILE}"

        # Confirm the selected names round-trip through the run suite before
        # starting a long run; discovery suites may include tests absent from it.
        if ! preflight_tests=$("${OPENSHIFT_TESTS}" run "${SUITE}" \
            --provider "${TEST_PROVIDER}" \
            --with-hypervisor-json="${HYPERVISOR_JSON}" \
            --file "${SET_FILE}" --dry-run 2>&1 | grep '^"' | sort) ||
            [[ "${preflight_tests}" != "$(sort "${SET_FILE}")" ]]; then
            log_error "Pre-flight --file check did not return the exact selected test set."
            log_error "Likely cause: a discovered test is not present in the run suite '${SUITE}' (use a superset suite like 'all'), or a name-quoting mismatch."
            log_error "Set file: ${SET_FILE}"
            log_error "Inspect raw output: ${OPENSHIFT_TESTS} run ${SUITE} --provider '${TEST_PROVIDER}' --file '${SET_FILE}' --dry-run 2>&1 | head"
            exit 1
        fi
        log_info "Pre-flight --file check OK: all ${#TESTS[@]} selected tests match."
    else
        RUN_ARGS=()
    fi

    # Repeat the ENTIRE suite N times
    for ((iter=1; iter<=REPEAT; iter++)); do
        echo ""
        log_info "========================================"
        log_info "Suite iteration ${iter}/${REPEAT}"
        log_info "========================================"

        # Create run directory for this iteration
        RUN_TS="$(date -u +%Y%m%d-%H%M%S)"
        RUN_DIR="${SESSION_DIR}/iter-$(printf "%02d" "${iter}")-${RUN_TS}"
        prepare_run "${RUN_DIR}/test"

        log_info "Run directory: ${RUN_DIR}"

        start_captures "${RUN_DIR}" "${RUN_TS}"

        # Run the suite
        log_info "Starting openshift-tests run ${SUITE} (iteration ${iter}/${REPEAT})"
        run_tests "${OPENSHIFT_TESTS}" run "${SUITE}" \
            --provider "${TEST_PROVIDER}" \
            --with-hypervisor-json="${HYPERVISOR_JSON}" \
            "${MONITOR_ARGS[@]}" \
            "${RUN_ARGS[@]}" \
            --max-parallel-tests=1 \
            --timeout=60m \
            -o "${RAW_LOG}" \
            --junit-dir "${JUNIT_DIR}" \
            "${EXTRA_ARGS[@]}"

        log_info "Iteration ${iter}/${REPEAT} completed with exit code: ${TEST_STATUS}"

        stop_captures

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
    # Interactive mode: run tests one at a time
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

        log_info "Running test ${CURRENT}/${TOTAL_TESTS}..."
        echo ""

        # Run the test
        "${SCRIPT_DIR}/run-test.sh" \
            --suite "${SUITE}" \
            --test "${test}" \
            --repeat "${REPEAT}" \
            "${CAPTURE_ARGS[@]}" \
            --name "${SESSION_NAME}"

        echo ""
        log_info "✓ Test ${CURRENT}/${TOTAL_TESTS} completed"
        echo ""

        # Interactive prompt after running (don't prompt after last test)
        if [[ $CURRENT -lt $TOTAL_TESTS ]]; then
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
    "${SCRIPT_DIR}/utils/gather-resource-agent-versions.sh" || \
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
log_info "Results in: runs/${SESSION_NAME}-*/"
echo ""
