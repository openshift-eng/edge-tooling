#\!/bin/bash
#
# List tests from a suite with optional filtering
#
# Usage:
#   ./list-tests.sh                           # List all two-node tests
#   ./list-tests.sh --suite openshift/conformance/parallel --filter upgrade
#   ./list-tests.sh --filter "recovery" --command-line    # Recovery tests as --test arguments
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared test helpers
source "${SCRIPT_DIR}/test-helpers.sh"

SUITE="openshift/two-node"
FILTER="."
MODE="list"
PROFILE=""
SUITE_SET=false
FILTER_SET=false

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
        --command-line|-c)
            MODE="cmdline"
            shift
            ;;
        --list-profiles)
            list_profiles
            exit 0
            ;;
        --help|-h)
            cat <<HELP
Usage: $0 [options]

List tests from a test suite with optional filtering.

OPTIONS:
    --profile NAME        Friendly profile that selects a suite (+ optional filter).
                          See --list-profiles. --suite/--filter override it.
    --suite SUITE         Test suite (default: openshift/two-node)
    --filter PATTERN      Filter test names by regex (default: "." - all tests)
    --command-line, -c    Output as --test arguments for run-test.sh
    --list-profiles       Show available profiles and exit
    --help, -h            Show this help

EXAMPLES:
    # List all two-node tests (default)
    $0

    # List recovery tests (openshift/two-node)
    $0 --profile recovery

    # List all DualReplica feature tests across every suite
    $0 --profile dualreplica

    # List e2e conformance tests
    $0 --profile e2e

    # List upgrade tests
    $0 --suite openshift/conformance/parallel --filter upgrade

    # Generate command line arguments for all recovery tests
    $0 --profile recovery --command-line
HELP
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Resolve profile into suite/filter defaults (explicit --suite/--filter win).
if [[ -n "${PROFILE}" ]]; then
    if ! resolve_profile "${PROFILE}"; then
        exit 1
    fi
    if [[ "${PROFILE_MODE}" == "upgrade" ]]; then
        log_warn "Profile '${PROFILE}' targets run-upgrade, which has no discrete test list."
        log_warn "Listing the underlying '${PROFILE_SUITE}' suite instead."
    fi
    [[ "${SUITE_SET}" == "false" ]] && SUITE="${PROFILE_SUITE}"
    if [[ "${FILTER_SET}" == "false" && -n "${PROFILE_FILTER}" ]]; then
        FILTER="${PROFILE_FILTER}"
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
    log_error "Failed to load proxy.env - using dummy hypervisor config"
    HYPERVISOR_JSON='{"hypervisorIP":"127.0.0.1", "sshUser":"core", "privateKeyPath":"/dev/null"}'
else
    setup_hypervisor_config
fi

# Get all tests from suite
setup_test_provider

mapfile -t TESTS < <("${TESTS_BIN}" run "${SUITE}" \
    --provider "${TEST_PROVIDER}" \
    --with-hypervisor-json="${HYPERVISOR_JSON}" \
    --dry-run 2>&1 | \
    grep '^"' | \
    sed 's/^"\(.*\)"$/\1/' | \
    grep -iE "${FILTER}" | \
    sort -u)

if [[ ${#TESTS[@]} -eq 0 ]]; then
    log_error "No tests found"
    exit 1
fi

if [[ "$MODE" == "list" ]]; then
    log_info "Found ${#TESTS[@]} tests in ${SUITE} matching '${FILTER}':"
    echo ""
    for test in "${TESTS[@]}"; do
        echo "  - ${test}"
    done
    echo ""
else
    # Output command line arguments
    for test in "${TESTS[@]}"; do
        printf -- '--test "%s" ' "${test}"
    done
    echo ""  # Trailing newline
fi
