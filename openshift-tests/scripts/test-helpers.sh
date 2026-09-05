#!/usr/bin/bash
# Shared test helper functions for openshift-tests runners
# Source this file: source "$(dirname "${BASH_SOURCE[0]}")/test-helpers.sh"

# ============================================================================
# Logging and UX
# ============================================================================

ts() {
    date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"
}

log() {
    printf "%s %s\n" "$(ts)" "$*"
}

log_info() {
    echo -e "\033[0;32mINFO:\033[0m $*"
}

log_warn() {
    echo -e "\033[1;33mWARN:\033[0m $*"
}

log_error() {
    echo -e "\033[0;31mERROR:\033[0m $*"
}

sanitize_name() {
    local in="$1"
    in="${in// /-}"
    in="${in//\//-}"
    in="${in//:/-}"
    in="${in//[^a-zA-Z0-9_.-]/-}"
    printf "%s" "${in:0:80}"
}

# ============================================================================
# Directory Setup
# ============================================================================

setup_test_directories() {
    # Sets up standard directory structure
    # Expects SCRIPT_DIR to be set by caller

    if [[ -z "${SCRIPT_DIR:-}" ]]; then
        log_error "SCRIPT_DIR must be set before calling setup_test_directories"
        return 1
    fi

    export SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
    export RUN_DIR="${SCRATCH_ROOT}/runs"
    export TESTS_BIN_DIR="${SCRATCH_ROOT}/tests-bin"

    mkdir -p "${RUN_DIR}"
    mkdir -p "${TESTS_BIN_DIR}"
}

# ============================================================================
# Proxy and Environment Setup
# ============================================================================

load_proxy_env() {
    local proxy_env="${PROXY_ENV:?PROXY_ENV must be set to the cluster proxy.env path (e.g. <two-node-toolbox-deploy>/openshift-clusters/proxy.env)}"

    if [[ -f "${proxy_env}" ]]; then
        # shellcheck source=/dev/null
        source "${proxy_env}"
        log "Sourced proxy.env from ${proxy_env}"
        return 0
    else
        log_warn "proxy.env not found at ${proxy_env}"
        return 1
    fi
}

setup_hypervisor_config() {
    # Sets up hypervisor connection variables from environment or proxy.env
    # Requires proxy.env to be sourced first for EC2_PUBLIC_IP

    export HYPERVISOR_IP="${EC2_PUBLIC_IP:-${HYPERVISOR_IP:-}}"
    export SSH_USER="${SSH_USER:-ec2-user}"
    export SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_redhat}"

    if [[ -z "${SSH_KEY_PATH}" ]] || [[ ! -f "${SSH_KEY_PATH}" ]]; then
        SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
    fi

    if [[ -z "${HYPERVISOR_IP:-}" ]]; then
        log_error "HYPERVISOR_IP is not set (check proxy.env or set manually)"
        return 1
    fi

    export HYPERVISOR_JSON="{\"hypervisorIP\":\"${HYPERVISOR_IP}\", \"sshUser\":\"${SSH_USER}\", \"privateKeyPath\":\"${SSH_KEY_PATH}\"}"

    log "Hypervisor: ${SSH_USER}@${HYPERVISOR_IP} (key: ${SSH_KEY_PATH})"
}

# ============================================================================
# openshift-tests Binary Management
# ============================================================================

get_openshift_tests_binary() {
    # Returns path to openshift-tests binary
    # Checks in order:
    #   1. OPENSHIFT_TESTS env var
    #   2. scratch/tests-bin/openshift-tests
    #   3. ~/.cache/openshift-tests/openshift-tests
    # Does NOT extract automatically - use extract_openshift_tests for that

    local tests_bin=""

    if [[ -n "${OPENSHIFT_TESTS:-}" ]] && [[ -x "${OPENSHIFT_TESTS}" ]]; then
        tests_bin="${OPENSHIFT_TESTS}"
    elif [[ -x "${TESTS_BIN_DIR}/openshift-tests" ]]; then
        tests_bin="${TESTS_BIN_DIR}/openshift-tests"
    elif [[ -x "$HOME/.cache/openshift-tests/openshift-tests" ]]; then
        tests_bin="$HOME/.cache/openshift-tests/openshift-tests"
    fi

    echo "${tests_bin}"
}

verify_openshift_tests_binary() {
    local tests_bin="$1"

    if [[ -z "${tests_bin}" ]] || [[ ! -x "${tests_bin}" ]]; then
        log_error "openshift-tests binary not found or not executable"
        log_info "Searched:"
        log_info "  1. OPENSHIFT_TESTS env var: ${OPENSHIFT_TESTS:-not set}"
        log_info "  2. ${TESTS_BIN_DIR}/openshift-tests"
        log_info "  3. ~/.cache/openshift-tests/openshift-tests"
        log_info ""
        log_info "To extract from cluster payload:"
        log_info "  oc adm release extract --tools --command=openshift-tests --to=${TESTS_BIN_DIR}"
        return 1
    fi

    log "Using openshift-tests: ${tests_bin}"
    return 0
}

verify_openshift_tests_version() {
    local tests_bin="$1"
    local skip_check="${2:-false}"

    if [[ "${skip_check}" == "true" ]]; then
        return 0
    fi

    # Get cluster version
    local cluster_version
    cluster_version=$(oc get clusterversion version -o jsonpath='{.status.desired.version}' 2>/dev/null || echo "")

    if [[ -z "$cluster_version" ]]; then
        log_warn "Could not determine cluster version - skipping version check"
        return 0
    fi

    # Get openshift-tests version
    local tests_version
    tests_version=$("${tests_bin}" version 2>/dev/null | head -1 || echo "unknown")

    log_info "Cluster version: ${cluster_version}"
    log_info "openshift-tests version: ${tests_version}"

    # Check if versions match
    if echo "${tests_version}" | grep -q "${cluster_version}"; then
        log_info "✓ Version match confirmed"
        return 0
    else
        log_warn "Version mismatch detected!"
        log_warn "  Cluster:         ${cluster_version}"
        log_warn "  openshift-tests: ${tests_version}"
        log_warn "Consider re-extracting binary from cluster payload"
        return 0  # Just warn, don't fail
    fi
}

extract_openshift_tests() {
    local output_dir="${1:-${TESTS_BIN_DIR}}"
    mkdir -p "${output_dir}"

    log_info "Extracting openshift-tests from cluster payload..."

    # Get current release image
    local release_image
    release_image=$(oc get clusterversion version -o jsonpath='{.status.desired.image}' 2>/dev/null || echo "")

    if [[ -z "$release_image" ]]; then
        log_error "Could not determine cluster release image"
        log_info "Make sure KUBECONFIG is set and cluster is accessible"
        return 1
    fi

    log_info "Release image: ${release_image}"

    # Get the tests image reference from the release
    local tests_image
    tests_image=$(oc adm release info "${release_image}" --image-for=tests)

    if [[ -z "${tests_image}" ]]; then
        log_error "Failed to get tests image from release"
        return 1
    fi

    log_info "Tests image: ${tests_image}"

    # Extract openshift-tests binary from the tests image
    oc image extract "${tests_image}" \
        --path=/usr/bin/openshift-tests:"${output_dir}" \
        --confirm 2>&1 | grep -v "Extracting" || true

    local tests_bin="${output_dir}/openshift-tests"
    if [[ ! -f "${tests_bin}" ]]; then
        log_error "Failed to extract openshift-tests binary"
        return 1
    fi

    chmod +x "${tests_bin}"
    log_info "Extracted to: ${tests_bin}"
    echo "${tests_bin}"
}

# ============================================================================
# Test Configuration
# ============================================================================

setup_test_provider() {
    # Sets up standard test provider configuration for baremetal two-node tests
    export TEST_PROVIDER='{"type":"baremetal"}'
    export OPENSHIFT_SKIP_EXTERNAL_TESTS=1
}

setup_monitor_configuration() {
    # Sets up monitor disable and cluster stability settings
    # Matches openshift/release baremetalds-two-node-fencing-recovery workflow

    if [[ ! -v OPENSHIFT_TESTS_DISABLE_MONITORS ]]; then
        export OPENSHIFT_TESTS_DISABLE_MONITORS='etcd-log-analyzer,legacy-cvo-invariants,legacy-etcd-invariants,node-lifecycle,oc-adm-upgrade-status'
    fi

    if [[ ! -v OPENSHIFT_TESTS_CLUSTER_STABILITY ]]; then
        export OPENSHIFT_TESTS_CLUSTER_STABILITY=Disruptive
    fi

    # Build monitor arguments array (caller should declare DISABLE_MONITOR_ARGS)
    if [[ -n "${OPENSHIFT_TESTS_DISABLE_MONITORS}" ]]; then
        DISABLE_MONITOR_ARGS+=(--disable-monitor="${OPENSHIFT_TESTS_DISABLE_MONITORS}")
    fi

    # Build cluster stability arguments array (caller should declare CLUSTER_STABILITY_ARGS)
    if [[ -n "${OPENSHIFT_TESTS_CLUSTER_STABILITY}" ]]; then
        CLUSTER_STABILITY_ARGS+=(--cluster-stability="${OPENSHIFT_TESTS_CLUSTER_STABILITY}")
    fi
}

# ============================================================================
# Test Profiles
# ============================================================================
# A profile is a friendly name that maps to a test target:
#   PROFILE_SUITE  - openshift-tests suite to run/list
#   PROFILE_FILTER - default name/label regex to narrow the suite ("" = none)
#   PROFILE_MODE   - "run" (openshift-tests run <suite>) or
#                    "upgrade" (openshift-tests run-upgrade, needs --to-image)
#
# Callers read those three vars after a successful `resolve_profile <name>`.
# --suite / --filter on the command line always override the profile defaults.
#
# Suites marked (runtime-verify) below should be confirmed against the target
# cluster once with: openshift-tests run <suite> --dry-run
#
#   e2e            openshift/conformance/parallel
#   recovery       openshift/two-node
#   dualreplica    all tests, filtered to the DualReplica feature gate
#                  ([OCPFeatureGate:DualReplica]) — deliberately NOT limited to
#                  openshift/two-node, so DualReplica tests are found wherever
#                  they live.
#   cert-rotation  openshift/etcd/certrotation      (runtime-verify)
#   upgrade        all, via run-upgrade             (runtime-verify; --to-image)

resolve_profile() {
    local profile="$1"
    PROFILE_SUITE=""
    PROFILE_FILTER=""
    PROFILE_MODE="run"

    case "${profile}" in
        e2e)           PROFILE_SUITE="openshift/conformance/parallel" ;;
        recovery)      PROFILE_SUITE="openshift/two-node" ;;
        dualreplica)   PROFILE_SUITE="all"; PROFILE_FILTER="DualReplica" ;;
        cert-rotation) PROFILE_SUITE="openshift/etcd/certrotation" ;;
        upgrade)       PROFILE_SUITE="all"; PROFILE_MODE="upgrade" ;;
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
  e2e            openshift/conformance/parallel
  recovery       openshift/two-node
  dualreplica    all tests filtered to [OCPFeatureGate:DualReplica]
  cert-rotation  openshift/etcd/certrotation      (verify against cluster)
  upgrade        run-upgrade (requires --to-image / UPGRADE_TO_IMAGE)
PROFILES
}

# ============================================================================
# Utility Functions
# ============================================================================

matches_stop_pattern() {
    local pattern="$1"
    local file="$2"

    if command -v rg >/dev/null 2>&1; then
        rg --fixed-strings --quiet "${pattern}" "${file}"
    else
        grep -Fq -- "${pattern}" "${file}"
    fi
}

# ============================================================================
# Initialization
# ============================================================================

# Auto-setup if SCRIPT_DIR is already set
if [[ -n "${SCRIPT_DIR:-}" ]]; then
    setup_test_directories
fi
