#!/usr/bin/bash
# Shared test helper functions for openshift-tests runners
# Source from a runner: source "$(dirname "${BASH_SOURCE[0]}")/utils/test-helpers.sh"

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/ssh-helpers.sh"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/proxy-helpers.sh"

# ============================================================================
# Logging and UX
# ============================================================================

ts() {
    date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"
}

log() {
    printf "%s %s\\n" "$(ts)" "$*"
}

log_info() {
    echo -e "\\033[0;32mINFO:\\033[0m $*"
}

log_warn() {
    echo -e "\\033[1;33mWARN:\\033[0m $*"
}

log_error() {
    echo -e "\\033[0;31mERROR:\\033[0m $*"
}

# Check operands without consuming a following option. Regex/text operands may
# start with hyphens; session labels may start with one, but not with --.
# Only filters and optional stop text may be empty.
require_option_value() {
    if [[ $# -ge 2 ]]; then
        case "$1" in
            --filter|--stop-on-match) return 0 ;;
            --test) [[ -n "$2" ]] && return 0 ;;
            --name) [[ -n "$2" && "$2" != --* ]] && return 0 ;;
            *) [[ -n "$2" && "$2" != -* ]] && return 0 ;;
        esac
    fi
    log_error "Option $1 requires a value (see --help)."
    exit 1
}

require_positive_integer() {
    if [[ ! "$1" =~ ^[1-9][0-9]*$ ]]; then
        log_error "$2 must be a positive integer without leading zeros."
        exit 1
    fi
}

# ============================================================================
# Directory Setup
# ============================================================================

setup_test_directories() {
    # Resolve artifact directories from this helper's location.
    SCRATCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    export SCRATCH_ROOT
    export RUN_DIR="${SCRATCH_ROOT}/runs"
    export TESTS_BIN_DIR="${SCRATCH_ROOT}/tests-bin"

    mkdir -p "${RUN_DIR}"
    mkdir -p "${TESTS_BIN_DIR}"
}

setup_test_binary() {
    if [[ -z "${OPENSHIFT_TESTS:-}" ]]; then
        "${SCRATCH_ROOT}/scripts/utils/extract-tests-binary.sh" || return
        OPENSHIFT_TESTS="${SCRATCH_ROOT}/tests-bin/payload/openshift-tests"
    fi
    if [[ ! -x "${OPENSHIFT_TESTS}" ]]; then
        log_error "openshift-tests binary not found or not executable: ${OPENSHIFT_TESTS}"
        return 1
    fi
    export OPENSHIFT_TESTS
}

# Capture state belongs to the runner; it installs the exit/signal traps.
CAP_PID_FILE=""

start_captures() {
    local capture_dir="$1/captures" timestamp="$2"
    if [[ "${WITH_CAPTURES}" == "true" ]]; then
        mkdir -p "${capture_dir}"
        # Save before launch so an interrupted or partial startup is cleaned up.
        CAP_PID_FILE="${capture_dir}/capture-pids-${timestamp}.txt"
        CAPTURE_LOG_DIR="${capture_dir}" CAPTURE_TIMESTAMP="${timestamp}" \
            "${SCRIPT_DIR}/captures/run-all-captures.sh" > "${capture_dir}/start-captures.log" 2>&1
        log "Captures started: ${CAP_PID_FILE}"
    fi
}

stop_captures() {
    local pid_file="${CAP_PID_FILE:-}"
    CAP_PID_FILE=""
    if [[ -n "${pid_file}" && -f "${pid_file}" ]]; then
        local stop_log="${pid_file%/*}/stop-captures.log"
        if "${SCRIPT_DIR}/captures/stop-all-captures.sh" "${pid_file}" >> "${stop_log}" 2>&1; then
            log "Captures stopped"
        else
            log_warn "Capture cleanup failed; see ${stop_log}"
        fi
    fi
}

# ============================================================================
# Proxy and Environment Setup
# ============================================================================

setup_hypervisor_config() {
    # Sets up hypervisor connection variables from environment or proxy.env
    # Requires proxy.env to be sourced first for EC2_PUBLIC_IP

    export HYPERVISOR_IP="${EC2_PUBLIC_IP:-${HYPERVISOR_IP:-}}"
    setup_ssh_config
    export SSH_USER SSH_KEY_PATH

    if [[ -z "${HYPERVISOR_IP:-}" ]]; then
        log_error "HYPERVISOR_IP is not set (check proxy.env or set manually)"
        return 1
    fi

    HYPERVISOR_JSON=$(jq -cn \
        --arg ip "${HYPERVISOR_IP}" --arg user "${SSH_USER}" --arg key "${SSH_KEY_PATH}" \
        '{hypervisorIP: $ip, sshUser: $user, privateKeyPath: $key}') || return 1
    export HYPERVISOR_JSON

    log "Hypervisor: ${SSH_USER}@${HYPERVISOR_IP} (key: ${SSH_KEY_PATH})"
}

# ============================================================================
# Test Configuration
# ============================================================================

setup_test_provider() {
    # Sets up standard test provider configuration for baremetal two-node tests
    export TEST_PROVIDER='{"type":"baremetal"}'
    export OPENSHIFT_SKIP_EXTERNAL_TESTS=1
}

# ============================================================================
# Test Profiles
# ============================================================================
# A profile is a friendly name that maps to a test target:
#   PROFILE_SUITE            - openshift-tests suite to RUN (and to list, unless
#                              PROFILE_DISCOVER_SUITES is set). When tests are
#                              curated from several suites, this must be a
#                              superset that contains them all (e.g. "all").
#   PROFILE_FILTER           - default name/label regex to narrow the suite
#                              ("" = none)
#   PROFILE_MODE             - "run" (openshift-tests run <suite>) or
#                              "upgrade" (openshift-tests run-upgrade, needs
#                              --to-image)
#   PROFILE_DISCOVER_SUITES  - space-separated list of suites to DISCOVER test
#                              names from ("" = just PROFILE_SUITE). Lets a
#                              profile pull tests from more than one suite; the
#                              union is then pinned via --file against
#                              PROFILE_SUITE (which must contain them all).
#   PROFILE_EXCLUDE          - regex of tests to DROP from the discovered set
#                              ("" = none). Used to omit destructive tests such
#                              as node replacement, which reprovisions a master
#                              from the base image and reverts its patched
#                              resource-agents RPM to stock -- producing a mixed
#                              build across the two masters. Excluding it keeps
#                              every node on the same build for the whole run.
#   PROFILE_GATHER_RA        - "true" to query resource-agents RPM versions on
#                              both masters after the run (mixed-build check).
#
# Callers read those vars after a successful `resolve_profile <name>`.
# --suite / --filter on the command line always override the profile defaults.
#
# Suites marked (runtime-verify) below should be confirmed against the target
# cluster once with: openshift-tests run <suite> --dry-run
#
#   e2e             openshift/conformance/parallel
#   recovery        openshift/two-node (all recovery tests, incl. node
#                   replacement); gathers resource-agents versions afterward.
#   ra-verification openshift/etcd/certrotation + openshift/two-node, EXCLUDING
#                   node replacement -- exercises the resource-agents across the
#                   cert-rotation and recovery surface without ever reprovisioning
#                   a node, so both masters stay on the same build. (runtime-verify)
#   dualreplica     all tests, filtered to the DualReplica feature gate
#                   ([OCPFeatureGate:DualReplica]) — deliberately NOT limited to
#                   openshift/two-node, so DualReplica tests are found wherever
#                   they live.
#   cert-rotation   openshift/etcd/certrotation      (runtime-verify)
#   upgrade         all, via run-upgrade             (runtime-verify; --to-image)

resolve_profile() {
    local profile="$1"
    PROFILE_SUITE=""
    PROFILE_FILTER=""
    PROFILE_MODE="run"
    PROFILE_DISCOVER_SUITES=""
    PROFILE_EXCLUDE=""
    PROFILE_GATHER_RA="false"

    # PROFILE_* vars are consumed by scripts that source this file (e.g.
    # run-suite.sh) after calling resolve_profile.
    # shellcheck disable=SC2034
    case "${profile}" in
        e2e)             PROFILE_SUITE="openshift/conformance/parallel" ;;
        recovery)        PROFILE_SUITE="openshift/two-node"; PROFILE_GATHER_RA="true" ;;
        ra-verification) PROFILE_SUITE="all"
                         PROFILE_DISCOVER_SUITES="openshift/etcd/certrotation openshift/two-node"
                         PROFILE_EXCLUDE="needing manual recovery is replaced"
                         PROFILE_GATHER_RA="true" ;;
        dualreplica)     PROFILE_SUITE="all"; PROFILE_FILTER="DualReplica" ;;
        cert-rotation)   PROFILE_SUITE="openshift/etcd/certrotation" ;;
        upgrade)         PROFILE_SUITE="all"; PROFILE_MODE="upgrade" ;;
        *)
            log_error "Unknown profile: ${profile}"
            list_profiles
            return 1
            ;;
    esac
    return 0
}

list_profiles() {
    cat <<'PROFILES'
Profiles (--profile NAME):
  e2e             openshift/conformance/parallel
  recovery        openshift/two-node (all recovery tests, incl. node replacement)
  ra-verification openshift/etcd/certrotation + openshift/two-node, minus node
                  replacement (no reprovision -> no mixed build)
  dualreplica     all tests filtered to [OCPFeatureGate:DualReplica]
  cert-rotation   openshift/etcd/certrotation      (verify against cluster)
  upgrade         run-upgrade (requires --to-image / UPGRADE_TO_IMAGE)
PROFILES
}
