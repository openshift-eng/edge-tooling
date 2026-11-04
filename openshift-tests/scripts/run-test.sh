#!/usr/bin/bash
# Generic test runner for openshift-tests
# Runs one or more tests with optional repeats and diagnostic capture.

set -euo pipefail
umask 077

# True if the focused test itself reported failure in the test log.
# openshift-tests prints one line per test: '<ts> failed: (<dur>) <ts> "<full name>"'.
# A 'failed: (' line that also contains the focus substring means THIS test failed
# -- unlike a MonitorTest/suite-level failure, which does not emit such a line, so
# this avoids false stops on monitor-only failures (which still make rc != 0).
test_failed_in_log() {
    local focus="$1"
    local file="$2"
    [[ -f "${file}" ]] || return 1
    grep -F 'failed: (' "${file}" 2>/dev/null | grep -F -- "${focus}" >/dev/null
}

usage() {
    cat <<'EOF'
Usage:
  scripts/run-test.sh [options]

Options:
  --test "<focus>"            Add one openshift-tests --run focus string (repeatable).
  --suite SUITE               Test suite to run (default: openshift/two-node).
  --repeat N                  Repeat full test list up to N times (default: 1).
  --timeout DURATION          openshift-tests timeout per run (default: 60m).
  --name LABEL                Session label prefix (cannot start with --; default: tnf-two-node).
  --stop-on-match "text"      Stop early if the test log contains this text.
  --stop-on-fail              Stop early if the focused test itself reports failure
                              (ignores monitor-only/suite failures).
  --with-captures             Start run-all-captures.sh per run (default).
  --no-captures               Do not start captures.
  --wait-for-cluster          Poll until cluster is ready before running tests.
  -h, --help                  Show this help.

Environment:
  OPENSHIFT_TESTS              Use this executable unchanged; unset/empty extracts
                              from the cluster payload before running.
  POLL_SEC                    Positive integer seconds between readiness checks (default: 900).
  MIN_NODES                   Positive integer minimum Ready-node count (default: 2).

Examples:
  # One test, up to 5 runs, stop when learner promotion timeout appears
  scripts/run-test.sh \
    --test "etcd recovery should recover from network disruption with etcd member re-addition" \
    --repeat 5 \
    --stop-on-match "timed out waiting for the learner to be promoted"

  # Multiple tests in sequence, repeated 3 times
  scripts/run-test.sh \
    --test "etcd recovery should recover from network disruption with etcd member re-addition" \
    --test "etcd recovery should recover from graceful node shutdown with etcd member re-addition" \
    --test "cluster recovers when a permanently failed node needing manual recovery is replaced" \
    --repeat 3

  # Wait for cluster after install/upgrade, then run node replacement
  scripts/run-test.sh --wait-for-cluster \
    --test "cluster recovers when a permanently failed node needing manual recovery is replaced"
EOF
}

sanitize_name() {
    local in="$1"
    in="${in// /-}"
    in="${in//\//-}"
    in="${in//:/-}"
    in="${in//[^a-zA-Z0-9_.-]/-}"
    printf "%s" "${in:0:80}"
}

is_cluster_ready() {
    if ! oc get --raw /healthz &>/dev/null; then
        echo "[$(date -Iseconds)] API not reachable (healthz)"
        return 1
    fi

    local nodes
    nodes=$(oc get nodes --no-headers 2>/dev/null) || {
        echo "[$(date -Iseconds)] Node query failed"
        return 1
    }
    if [[ -z "${nodes}" ]]; then
        echo "[$(date -Iseconds)] No Node objects yet"
        return 1
    fi

    local n
    n=$(echo "${nodes}" | wc -l)
    if [[ "${n}" -lt "${MIN_NODES}" ]]; then
        echo "[$(date -Iseconds)] Only ${n} node(s); need ${MIN_NODES}"
        return 1
    fi

    if echo "${nodes}" | awk '$2 != "Ready" { exit 1 }'; then
        :
    else
        echo "[$(date -Iseconds)] Not all nodes Ready"
        return 1
    fi

    local avail
    avail=$(oc get clusterversion version -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null) || {
        echo "[$(date -Iseconds)] ClusterVersion query failed"
        return 1
    }
    if [[ "${avail}" != "True" ]]; then
        echo "[$(date -Iseconds)] ClusterVersion Available != True (got: ${avail:-empty})"
        return 1
    fi

    return 0
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/utils/test-helpers.sh"
trap stop_captures EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

setup_test_directories

SUITE="openshift/two-node"
REPEAT_COUNT=1
TEST_TIMEOUT="${TEST_TIMEOUT:-60m}"
SESSION_NAME="tnf-two-node"
WITH_CAPTURES=true
STOP_ON_MATCH="${STOP_ON_MATCH:-}"
STOP_ON_FAIL=0
WAIT_FOR_CLUSTER=0
POLL_SEC="${POLL_SEC:-900}"
MIN_NODES="${MIN_NODES:-2}"
declare -a TEST_FOCUSES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --suite)
            require_option_value "$@"
            SUITE="$2"
            shift 2
            ;;
        --test)
            require_option_value "$@"
            TEST_FOCUSES+=("$2")
            shift 2
            ;;
        --repeat)
            require_option_value "$@"
            REPEAT_COUNT="$2"
            shift 2
            ;;
        --timeout)
            require_option_value "$@"
            TEST_TIMEOUT="$2"
            shift 2
            ;;
        --name)
            require_option_value "$@"
            SESSION_NAME="$2"
            shift 2
            ;;
        --stop-on-match)
            require_option_value "$@"
            STOP_ON_MATCH="$2"
            shift 2
            ;;
        --stop-on-fail)
            STOP_ON_FAIL=1
            shift
            ;;
        --with-captures)
            WITH_CAPTURES=true
            shift
            ;;
        --no-captures)
            WITH_CAPTURES=false
            shift
            ;;
        --wait-for-cluster)
            WAIT_FOR_CLUSTER=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ ${#TEST_FOCUSES[@]} -eq 0 ]]; then
    TEST_FOCUSES+=('cluster recovers when a permanently failed node needing manual recovery is replaced')
fi

require_positive_integer "${REPEAT_COUNT}" --repeat

load_proxy_env

setup_hypervisor_config

setup_test_provider

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/utils/monitor-helpers.sh"
setup_monitor_defaults
setup_monitor_args

read -ra EXTRA_ARGS <<< "${OPENSHIFT_TESTS_EXTRA_ARGS:-}"

if [[ "${WAIT_FOR_CLUSTER}" -eq 1 ]]; then
    for setting in POLL_SEC MIN_NODES; do
        require_positive_integer "${!setting}" "${setting}"
    done
    log "Polling every ${POLL_SEC}s until cluster is ready (min ${MIN_NODES} Ready nodes, ClusterVersion Available)."
    log "proxy.env: ${PROXY_ENV}"
    while true; do
        if is_cluster_ready; then
            log "Cluster is ready."
            break
        fi
        log "Sleeping ${POLL_SEC}s..."
        sleep "${POLL_SEC}"
    done
fi

setup_test_binary

SESSION_TS="$(date -u +%Y%m%d-%H%M%S)"
SESSION_NAME_SANITIZED="$(sanitize_name "${SESSION_NAME}")"

# Check if a session with this name already exists today (for suite runs)
# shellcheck disable=SC2153 # RUN_DIR is assigned by setup_test_directories.
EXISTING_SESSION=$(find "${RUN_DIR}" -maxdepth 1 -type d -name "${SESSION_NAME_SANITIZED}-$(date -u +%Y%m%d)-*" 2>/dev/null | sort | tail -1)

if [[ -n "${EXISTING_SESSION}" ]]; then
    # Reuse existing session directory (for suite runs with multiple tests)
    SESSION_DIR="${EXISTING_SESSION}"
    log "Reusing session dir: ${SESSION_DIR}"
else
    # Create new session directory
    SESSION_DIR="${RUN_DIR}/${SESSION_NAME_SANITIZED}-${SESSION_TS}"
    mkdir -p "${SESSION_DIR}"
fi

SUMMARY_FILE="${SESSION_DIR}/summary.tsv"
# Create summary header if it doesn't exist
if [[ ! -f "${SUMMARY_FILE}" ]]; then
    printf "iter\\ttest_index\\tresult\\trun_dir\\tfocus\\n" > "${SUMMARY_FILE}"
fi

log "Session dir: ${SESSION_DIR}"
log "Tests per iteration: ${#TEST_FOCUSES[@]}, repeat: ${REPEAT_COUNT}, captures: ${WITH_CAPTURES}"
log "openshift-tests (baremetalds-two-node-fencing-recovery): --disable-monitor=${OPENSHIFT_TESTS_DISABLE_MONITORS:-} --cluster-stability=${OPENSHIFT_TESTS_CLUSTER_STABILITY:-}"
[[ -n "${STOP_ON_MATCH}" ]] && log "Will stop early on match: ${STOP_ON_MATCH}"
[[ "${STOP_ON_FAIL}" -eq 1 ]] && log "Will stop early if a focused test reports failure."

# Uses run_dir/run_ts; leaves logs and result for summary and stop checks.
run_tests() {
    local selector="$1" start_message="$2"
    local junit_dir rc
    mkdir -p "${run_dir}/test" "${run_dir}/captures"

    timed_log="${run_dir}/test/openshift-tests-timestamped.log"
    console_log="${run_dir}/test/runner.log"
    junit_dir="${run_dir}/test/junit"
    mkdir -p "${junit_dir}"

    log "${start_message}" | tee -a "${console_log}"
    log "Run dir: ${run_dir}" | tee -a "${console_log}"

    start_captures "${run_dir}" "${run_ts}"

    set +e
    "${OPENSHIFT_TESTS}" run "${SUITE}" \
        --provider "${TEST_PROVIDER}" \
        --with-hypervisor-json="${HYPERVISOR_JSON}" \
        "${MONITOR_ARGS[@]}" \
        --run "${selector}" \
        --max-parallel-tests=1 \
        --timeout="${TEST_TIMEOUT}" \
        --junit-dir "${junit_dir}" \
        "${EXTRA_ARGS[@]}" \
        2>&1 | tee "${timed_log}"
    rc=${PIPESTATUS[0]}
    set -e

    result="PASS"
    [[ "${rc}" -ne 0 ]] && result="FAIL(${rc})"
    log "Test completed: ${result}" | tee -a "${console_log}"

    stop_captures
}

ESCAPED_FOCUSES=()
for focus in "${TEST_FOCUSES[@]}"; do
    ESCAPED_FOCUSES+=("$(printf '%s\n' "${focus}" | sed 's/[]\[^$.*+?{}()|]/\\&/g')")
done

stop_requested=0

# Optimization: batch a single iteration when no stop controls are active
# This avoids reinitializing monitors between tests
if [[ ${REPEAT_COUNT} -eq 1 ]] && [[ ${#TEST_FOCUSES[@]} -gt 1 ]] \
    && [[ "${STOP_ON_FAIL}" -eq 0 ]] && [[ -z "${STOP_ON_MATCH}" ]]; then
    # Combine all test patterns into a single --run flag with | (regex OR)
    # openshift-tests only uses the last --run flag, so we must combine them
    COMBINED_PATTERN="$(IFS='|'; printf '%s' "${ESCAPED_FOCUSES[*]}")"
    iter=1
    run_ts="$(date -u +%Y%m%d-%H%M%S)"
    run_dir="${SESSION_DIR}/iter-01-all-tests-${run_ts}"
    run_tests "${COMBINED_PATTERN}" "Starting single run with ${#TEST_FOCUSES[@]} tests"

    # Record to summary - one line for the batch
    printf "%d\\t%s\\t%s\\t%s\\t%s\\n" \
        "${iter}" "ALL" "${result}" "${run_dir}" "Multiple tests (${#TEST_FOCUSES[@]} total)" >> "${SUMMARY_FILE}"

else
    # Original behavior: loop through iterations and tests separately
    for ((iter=1; iter<=REPEAT_COUNT; iter++)); do
        for ((ti=0; ti<${#TEST_FOCUSES[@]}; ti++)); do
            focus="${TEST_FOCUSES[$ti]}"
            test_num=$((ti + 1))
            run_ts="$(date -u +%Y%m%d-%H%M%S)"
            run_slug="$(sanitize_name "${focus}")"
            run_dir="${SESSION_DIR}/iter-$(printf "%02d" "${iter}")-test-$(printf "%02d" "${test_num}")-${run_ts}-${run_slug}"
            run_tests "${ESCAPED_FOCUSES[$ti]}" "Starting iter=${iter} test=${test_num}: ${focus}"

            printf "%s\\t%s\\t%s\\t%s\\t%s\\n" \
                "${iter}" "${test_num}" "${result}" "${run_dir}" "${focus}" >> "${SUMMARY_FILE}"

            if [[ -n "${STOP_ON_MATCH}" ]] && [[ -f "${timed_log}" ]]; then
                if grep -Fq -- "${STOP_ON_MATCH}" "${timed_log}"; then
                    log "Stop pattern matched in ${timed_log}" | tee -a "${console_log}"
                    stop_requested=1
                    break
                fi
            fi

            if [[ "${STOP_ON_FAIL}" -eq 1 ]] && test_failed_in_log "${focus}" "${timed_log}"; then
                log "Focused test reported failure; stopping early (--stop-on-fail): ${focus}" | tee -a "${console_log}"
                stop_requested=1
                break
            fi
        done
        [[ "${stop_requested}" -eq 1 ]] && break
    done
fi

log "Done. Summary: ${SUMMARY_FILE}"
