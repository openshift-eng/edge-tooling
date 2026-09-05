#!/usr/bin/bash
# Generic test runner for openshift-tests
# Runs one or more tests with optional repeats and diagnostic capture.

set -euo pipefail

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }
log() { printf "%s %s\n" "$(ts)" "$*"; }
matches_stop_pattern() {
    local pattern="$1"
    local file="$2"
    if command -v rg >/dev/null 2>&1; then
        rg --fixed-strings --quiet "${pattern}" "${file}"
    else
        grep -Fq -- "${pattern}" "${file}"
    fi
}

# True if the focused test itself reported failure in the test log.
# openshift-tests prints one line per test: '<ts> failed: (<dur>) <ts> "<full name>"'.
# A 'failed: (' line that also contains the focus substring means THIS test failed
# -- unlike a MonitorTest/suite-level failure, which does not emit such a line, so
# this avoids false stops on monitor-only failures (which still make rc != 0).
test_failed_in_log() {
    local focus="$1"
    local file="$2"
    [[ -f "${file}" ]] || return 1
    grep -F 'failed: (' "${file}" 2>/dev/null | grep -Fq -- "${focus}"
}

usage() {
    cat <<'EOF'
Usage:
  run-test.sh [options]

Options:
  --test "<focus>"            Add one openshift-tests --run focus string (repeatable).
  --repeat N                  Repeat full test list up to N times (default: 1).
  --timeout DURATION          openshift-tests timeout per run (default: 60m).
  --name LABEL                Session label prefix under scratch/runs/ (default: tnf-two-node).
  --stop-on-match "text"      Stop early if the test log contains this text.
  --stop-on-fail              Stop early if the focused test itself reports failure
                              (ignores monitor-only/suite failures).
  --with-captures             Start run-all-captures.sh per run (default).
  --no-captures               Do not start captures.
  --wait-for-cluster          Poll until cluster is ready before running tests.
  -h, --help                  Show this help.

Environment:
  POLL_SEC                    Seconds between cluster readiness checks (default: 900).
  MIN_NODES                   Minimum Ready nodes required (default: 2).

Examples:
  # One test, up to 5 runs, stop when learner promotion timeout appears
  ./run-test.sh \
    --test "etcd recovery should recover from network disruption with etcd member re-addition" \
    --repeat 5 \
    --stop-on-match "timed out waiting for the learner to be promoted"

  # Multiple tests in sequence, repeated 3 times
  ./run-test.sh \
    --test "etcd recovery should recover from network disruption with etcd member re-addition" \
    --test "etcd recovery should recover from graceful node shutdown with etcd member re-addition" \
    --test "cluster recovers when a permanently failed node needing manual recovery is replaced" \
    --repeat 3

  # Wait for cluster after install/upgrade, then run node replacement
  ./run-test.sh --wait-for-cluster \
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
    if [[ ! -f "${PROXY_ENV}" ]]; then
        echo "[$(date -Iseconds)] proxy.env not found: ${PROXY_ENV}"
        return 1
    fi
    # shellcheck source=/dev/null
    source "${PROXY_ENV}"

    if ! oc get --raw /healthz &>/dev/null; then
        echo "[$(date -Iseconds)] API not reachable (healthz)"
        return 1
    fi

    local nodes
    nodes=$(oc get nodes --no-headers 2>/dev/null) || true
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
    avail=$(oc get clusterversion version -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null) || true
    if [[ "${avail}" != "True" ]]; then
        echo "[$(date -Iseconds)] ClusterVersion Available != True (got: ${avail:-empty})"
        return 1
    fi

    return 0
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${SCRATCH_ROOT}/runs"
mkdir -p "${RUN_DIR}"

PROXY_ENV="${PROXY_ENV:?PROXY_ENV must be set to the cluster proxy.env path (e.g. <two-node-toolbox-deploy>/openshift-clusters/proxy.env)}"
OPENSHIFT_TESTS="${OPENSHIFT_TESTS:-${SCRATCH_ROOT}/tests-bin/openshift-tests}"
REPEAT_COUNT=1
TEST_TIMEOUT="${TEST_TIMEOUT:-60m}"
SESSION_NAME="tnf-two-node"
WITH_CAPTURES=1
STOP_ON_MATCH="${STOP_ON_MATCH:-}"
STOP_ON_FAIL=0
WAIT_FOR_CLUSTER=0
POLL_SEC="${POLL_SEC:-900}"
MIN_NODES="${MIN_NODES:-2}"
declare -a TEST_FOCUSES=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --test)
            [[ $# -lt 2 ]] && { echo "Missing value for --test"; exit 1; }
            TEST_FOCUSES+=("$2")
            shift 2
            ;;
        --repeat)
            [[ $# -lt 2 ]] && { echo "Missing value for --repeat"; exit 1; }
            REPEAT_COUNT="$2"
            shift 2
            ;;
        --timeout)
            [[ $# -lt 2 ]] && { echo "Missing value for --timeout"; exit 1; }
            TEST_TIMEOUT="$2"
            shift 2
            ;;
        --name)
            [[ $# -lt 2 ]] && { echo "Missing value for --name"; exit 1; }
            SESSION_NAME="$2"
            shift 2
            ;;
        --stop-on-match)
            [[ $# -lt 2 ]] && { echo "Missing value for --stop-on-match"; exit 1; }
            STOP_ON_MATCH="$2"
            shift 2
            ;;
        --stop-on-fail)
            STOP_ON_FAIL=1
            shift
            ;;
        --with-captures)
            WITH_CAPTURES=1
            shift
            ;;
        --no-captures)
            WITH_CAPTURES=0
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

if [[ ! "${REPEAT_COUNT}" =~ ^[0-9]+$ ]] || [[ "${REPEAT_COUNT}" -lt 1 ]]; then
    echo "Error: --repeat must be a positive integer."
    exit 1
fi

if [[ -f "${PROXY_ENV}" ]]; then
    # shellcheck source=/dev/null
    source "${PROXY_ENV}"
    log "Sourced proxy.env from ${PROXY_ENV}"
fi

HYPERVISOR_IP="${EC2_PUBLIC_IP:-${HYPERVISOR_IP:-}}"
SSH_USER="${SSH_USER:-ec2-user}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_redhat}"
if [[ -z "${SSH_KEY_PATH}" ]] || [[ ! -f "${SSH_KEY_PATH}" ]]; then
    SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
fi

if [[ -z "${HYPERVISOR_IP:-}" ]]; then
    echo "Error: HYPERVISOR_IP is not set."
    exit 1
fi

if [[ ! -x "${OPENSHIFT_TESTS}" ]]; then
    echo "Error: openshift-tests binary not found at: ${OPENSHIFT_TESTS}"
    exit 1
fi

if [[ -z "${KUBECONFIG:-}" ]] && [[ ! -f "${PROXY_ENV}" ]]; then
    echo "Error: No proxy.env at ${PROXY_ENV} and KUBECONFIG not set."
    exit 1
fi

export TEST_PROVIDER='{"type":"baremetal"}'
export OPENSHIFT_SKIP_EXTERNAL_TESTS=1
HYPERVISOR_JSON="{\"hypervisorIP\":\"${HYPERVISOR_IP}\", \"sshUser\":\"${SSH_USER}\", \"privateKeyPath\":\"${SSH_KEY_PATH}\"}"

# Monitor defaults match openshift/release CI workflow baremetalds-two-node-fencing-recovery:
#   ci-operator/step-registry/baremetalds/two-node/fencing/recovery/baremetalds-two-node-fencing-recovery-workflow.yaml
#   TEST_SUITE=openshift/two-node, TEST_SKIPS=\[Degraded\], TEST_ARGS=--disable-monitor=... --cluster-stability=Disruptive
# Override with OPENSHIFT_TESTS_DISABLE_MONITORS / OPENSHIFT_TESTS_CLUSTER_STABILITY / OPENSHIFT_TESTS_EXTRA_ARGS.

if [[ ! -v OPENSHIFT_TESTS_DISABLE_MONITORS ]]; then
    OPENSHIFT_TESTS_DISABLE_MONITORS='etcd-log-analyzer,legacy-cvo-invariants,legacy-etcd-invariants,node-lifecycle,oc-adm-upgrade-status'
fi
if [[ ! -v OPENSHIFT_TESTS_CLUSTER_STABILITY ]]; then
    OPENSHIFT_TESTS_CLUSTER_STABILITY=Disruptive
fi

DISABLE_MONITOR_ARGS=()
if [[ -n "${OPENSHIFT_TESTS_DISABLE_MONITORS}" ]]; then
    DISABLE_MONITOR_ARGS+=(--disable-monitor="${OPENSHIFT_TESTS_DISABLE_MONITORS}")
fi
CLUSTER_STABILITY_ARGS=()
if [[ -n "${OPENSHIFT_TESTS_CLUSTER_STABILITY}" ]]; then
    CLUSTER_STABILITY_ARGS+=(--cluster-stability="${OPENSHIFT_TESTS_CLUSTER_STABILITY}")
fi

if [[ -n "${OPENSHIFT_TESTS_EXTRA_ARGS:-}" ]]; then
    tmp="${OPENSHIFT_TESTS_EXTRA_ARGS}"
    mon_count=0
    while [[ "${tmp}" == *"--monitor"* ]]; do
        tmp="${tmp#*--monitor}"
        mon_count=$((mon_count + 1))
    done
    if [[ "${mon_count}" -gt 2 ]]; then
        echo "Error: OPENSHIFT_TESTS_EXTRA_ARGS contains ${mon_count} '--monitor' flags. Max allowed: 2."
        exit 1
    fi
fi

if [[ "${WAIT_FOR_CLUSTER}" -eq 1 ]]; then
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

SESSION_TS="$(date -u +%Y%m%d-%H%M%S)"
SESSION_NAME_SANITIZED="$(sanitize_name "${SESSION_NAME}")"

# Check if a session with this name already exists today (for suite runs)
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
    printf "iter\ttest_index\tresult\trun_dir\tfocus\n" > "${SUMMARY_FILE}"
fi

log "Session dir: ${SESSION_DIR}"
log "Tests per iteration: ${#TEST_FOCUSES[@]}, repeat: ${REPEAT_COUNT}, captures: ${WITH_CAPTURES}"
log "openshift-tests (baremetalds-two-node-fencing-recovery): --disable-monitor=${OPENSHIFT_TESTS_DISABLE_MONITORS:-} --cluster-stability=${OPENSHIFT_TESTS_CLUSTER_STABILITY:-}"
[[ -n "${STOP_ON_MATCH}" ]] && log "Will stop early on match: ${STOP_ON_MATCH}"
[[ "${STOP_ON_FAIL}" -eq 1 ]] && log "Will stop early if a focused test reports failure."

stop_requested=0

# Optimization: if repeat=1, run all tests in a single openshift-tests invocation
# This avoids reinitializing monitors between tests
if [[ ${REPEAT_COUNT} -eq 1 ]] && [[ ${#TEST_FOCUSES[@]} -gt 1 ]]; then
    # Combine all test patterns into a single --run flag with | (regex OR)
    # openshift-tests only uses the last --run flag, so we must combine them
    COMBINED_PATTERN=""
    for focus in "${TEST_FOCUSES[@]}"; do
        focus_escaped=$(printf '%s\n' "$focus" | sed 's/[]\[^$.*+?{}()|]/\\&/g')
        if [[ -z "${COMBINED_PATTERN}" ]]; then
            COMBINED_PATTERN="${focus_escaped}"
        else
            COMBINED_PATTERN="${COMBINED_PATTERN}|${focus_escaped}"
        fi
    done
    RUN_ARGS=(--run "${COMBINED_PATTERN}")

    iter=1
    run_ts="$(date -u +%Y%m%d-%H%M%S)"
    run_dir="${SESSION_DIR}/iter-01-all-tests-${run_ts}"
    mkdir -p "${run_dir}/test" "${run_dir}/captures"

    raw_log="${run_dir}/test/openshift-tests-raw.log"
    timed_log="${run_dir}/test/openshift-tests-timestamped.log"
    console_log="${run_dir}/test/runner.log"
    junit_dir="${run_dir}/test/junit"
    mkdir -p "${junit_dir}"

    log "Starting single run with ${#TEST_FOCUSES[@]} tests" | tee -a "${console_log}"
    log "Run dir: ${run_dir}" | tee -a "${console_log}"

    cap_pid_file=""
    if [[ "${WITH_CAPTURES}" -eq 1 ]]; then
        CAPTURE_LOG_DIR="${run_dir}/captures" CAPTURE_TIMESTAMP="${run_ts}" \
            "${SCRIPT_DIR}/run-all-captures.sh" > "${run_dir}/captures/start-captures.log" 2>&1
        cap_pid_file="${run_dir}/captures/capture-pids-${run_ts}.txt"
        log "Captures started: ${cap_pid_file}" | tee -a "${console_log}"
    fi

    set +e
    # shellcheck disable=SC2086
    "${OPENSHIFT_TESTS}" run openshift/two-node \
        --provider "${TEST_PROVIDER}" \
        --with-hypervisor-json="${HYPERVISOR_JSON}" \
        "${DISABLE_MONITOR_ARGS[@]}" \
        "${CLUSTER_STABILITY_ARGS[@]}" \
        "${RUN_ARGS[@]}" \
        --max-parallel-tests=1 \
        --timeout="${TEST_TIMEOUT}" \
        --junit-dir "${junit_dir}" \
        ${OPENSHIFT_TESTS_EXTRA_ARGS:-} \
        2>&1 | tee "${timed_log}"
    rc=${PIPESTATUS[0]}
    set -e

    result="PASS"
    [[ "${rc}" -ne 0 ]] && result="FAIL(${rc})"
    log "Test completed: ${result}" | tee -a "${console_log}"

    if [[ -n "${cap_pid_file}" ]] && [[ -f "${cap_pid_file}" ]]; then
        "${SCRIPT_DIR}/stop-all-captures.sh" "${cap_pid_file}" > "${run_dir}/captures/stop-captures.log" 2>&1
        log "Captures stopped" | tee -a "${console_log}"
    fi

    # Record to summary - one line for the batch
    printf "%d\t%s\t%s\t%s\t%s\n" \
        "${iter}" "ALL" "${result}" "${run_dir}" "Multiple tests (${#TEST_FOCUSES[@]} total)" >> "${SUMMARY_FILE}"

else
    # Original behavior: loop through iterations and tests separately
    for ((iter=1; iter<=REPEAT_COUNT; iter++)); do
        for ((ti=0; ti<${#TEST_FOCUSES[@]}; ti++)); do
            focus="${TEST_FOCUSES[$ti]}"
            # Escape regex special characters for openshift-tests --run flag
            focus_escaped=$(printf '%s\n' "$focus" | sed 's/[]\[^$.*+?{}()|]/\\&/g')
            test_num=$((ti + 1))
        run_ts="$(date -u +%Y%m%d-%H%M%S)"
        run_slug="$(sanitize_name "${focus}")"
        run_dir="${SESSION_DIR}/iter-$(printf "%02d" "${iter}")-test-$(printf "%02d" "${test_num}")-${run_ts}-${run_slug}"
        mkdir -p "${run_dir}/test" "${run_dir}/captures"

        raw_log="${run_dir}/test/openshift-tests-raw.log"
        timed_log="${run_dir}/test/openshift-tests-timestamped.log"
        console_log="${run_dir}/test/runner.log"
        junit_dir="${run_dir}/test/junit"
        mkdir -p "${junit_dir}"

        log "Starting iter=${iter} test=${test_num}: ${focus}" | tee -a "${console_log}"
        log "Run dir: ${run_dir}" | tee -a "${console_log}"

        cap_pid_file=""
        if [[ "${WITH_CAPTURES}" -eq 1 ]]; then
            CAPTURE_LOG_DIR="${run_dir}/captures" CAPTURE_TIMESTAMP="${run_ts}" \
                "${SCRIPT_DIR}/run-all-captures.sh" > "${run_dir}/captures/start-captures.log" 2>&1
            cap_pid_file="${run_dir}/captures/capture-pids-${run_ts}.txt"
            log "Captures started: ${cap_pid_file}" | tee -a "${console_log}"
        fi

        set +e
        # shellcheck disable=SC2086
        "${OPENSHIFT_TESTS}" run openshift/two-node \
            --provider "${TEST_PROVIDER}" \
            --with-hypervisor-json="${HYPERVISOR_JSON}" \
            "${DISABLE_MONITOR_ARGS[@]}" \
            "${CLUSTER_STABILITY_ARGS[@]}" \
            --run "${focus_escaped}" \
            --max-parallel-tests=1 \
            --timeout="${TEST_TIMEOUT}" \
            --junit-dir "${junit_dir}" \
            ${OPENSHIFT_TESTS_EXTRA_ARGS:-} \
            2>&1 | tee "${timed_log}"
        rc=${PIPESTATUS[0]}
        set -e

        result="PASS"
        [[ "${rc}" -ne 0 ]] && result="FAIL(${rc})"
        log "Test completed: ${result}" | tee -a "${console_log}"

        if [[ -n "${cap_pid_file}" ]] && [[ -f "${cap_pid_file}" ]]; then
            "${SCRIPT_DIR}/stop-all-captures.sh" "${cap_pid_file}" >> "${run_dir}/captures/stop-captures.log" 2>&1
            log "Captures stopped for run." | tee -a "${console_log}"
        fi

        printf "%s\t%s\t%s\t%s\t%s\n" \
            "${iter}" "${test_num}" "${result}" "${run_dir}" "${focus}" >> "${SUMMARY_FILE}"

        if [[ -n "${STOP_ON_MATCH}" ]] && [[ -f "${timed_log}" ]]; then
            if matches_stop_pattern "${STOP_ON_MATCH}" "${timed_log}"; then
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
