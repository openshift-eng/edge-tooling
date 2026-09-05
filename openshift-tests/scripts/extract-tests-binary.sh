#!/bin/bash
#
# Extract openshift-tests binary from current cluster payload
#
# This ensures the binary matches your cluster version and includes
# the correct test suite for two-node fencing recovery tests.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TESTS_BIN_DIR="${SCRIPT_DIR}/../tests-bin"

# Source shared helpers for logging
source "${SCRIPT_DIR}/test-helpers.sh"

# Check for proxy.env
PROXY_ENV="${PROXY_ENV:?PROXY_ENV must be set to the cluster proxy.env path (e.g. <two-node-toolbox-deploy>/openshift-clusters/proxy.env)}"
if [[ ! -f "${PROXY_ENV}" ]]; then
    log_error "proxy.env not found at ${PROXY_ENV}"
    log_info "Set PROXY_ENV or ensure cluster is deployed"
    exit 1
fi

# Source proxy.env
log_info "Loading proxy.env..."
set -a
source "${PROXY_ENV}"
set +a

# Verify cluster access
log_info "Checking cluster access..."
if ! oc whoami &>/dev/null; then
    log_error "Cannot reach cluster. Check proxy.env and ensure cluster is running."
    exit 1
fi

# Get cluster version
CLUSTER_VERSION=$(oc get clusterversion version -o jsonpath='{.status.desired.version}')
RELEASE_IMAGE=$(oc get clusterversion version -o jsonpath='{.status.desired.image}')

log_info "Cluster version: ${CLUSTER_VERSION}"
log_info "Release image: ${RELEASE_IMAGE}"

# Check existing binary
if [[ -f "${TESTS_BIN_DIR}/openshift-tests" ]]; then
    OLD_VERSION=$("${TESTS_BIN_DIR}/openshift-tests" version 2>/dev/null | head -1 || echo "unknown")
    log_warn "Existing binary: ${OLD_VERSION}"

    read -p "Replace existing binary? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "Aborted"
        exit 0
    fi

    # Backup old binary
    mv "${TESTS_BIN_DIR}/openshift-tests" "${TESTS_BIN_DIR}/openshift-tests.bak"
    log_info "Old binary backed up to openshift-tests.bak"
fi

# Extract new binary
mkdir -p "${TESTS_BIN_DIR}"
log_info "Extracting openshift-tests from ${RELEASE_IMAGE}..."

# Get the tests image reference from the release
TESTS_IMAGE=$(oc adm release info "${RELEASE_IMAGE}" --image-for=tests)
if [[ -z "${TESTS_IMAGE}" ]]; then
    log_error "Failed to get tests image from release"
    if [[ -f "${TESTS_BIN_DIR}/openshift-tests.bak" ]]; then
        mv "${TESTS_BIN_DIR}/openshift-tests.bak" "${TESTS_BIN_DIR}/openshift-tests"
        log_info "Restored backup binary"
    fi
    exit 1
fi

log_info "Tests image: ${TESTS_IMAGE}"

# Extract openshift-tests binary from the tests image
oc image extract "${TESTS_IMAGE}" \
    --path=/usr/bin/openshift-tests:"${TESTS_BIN_DIR}" \
    --confirm

if [[ ! -f "${TESTS_BIN_DIR}/openshift-tests" ]]; then
    log_error "Failed to extract openshift-tests binary"
    if [[ -f "${TESTS_BIN_DIR}/openshift-tests.bak" ]]; then
        mv "${TESTS_BIN_DIR}/openshift-tests.bak" "${TESTS_BIN_DIR}/openshift-tests"
        log_info "Restored backup binary"
    fi
    exit 1
fi

chmod +x "${TESTS_BIN_DIR}/openshift-tests"

# Verify new binary
NEW_VERSION=$("${TESTS_BIN_DIR}/openshift-tests" version 2>/dev/null | head -1 || echo "unknown")
log_info "✓ Extracted: ${NEW_VERSION}"

# Verify it matches cluster
if echo "${NEW_VERSION}" | grep -q "${CLUSTER_VERSION}"; then
    log_info "✓ Version matches cluster: ${CLUSTER_VERSION}"
else
    log_warn "Version mismatch (may be OK if format differs):"
    log_warn "  Cluster: ${CLUSTER_VERSION}"
    log_warn "  Binary:  ${NEW_VERSION}"
fi

# Remove backup if successful
if [[ -f "${TESTS_BIN_DIR}/openshift-tests.bak" ]]; then
    rm "${TESTS_BIN_DIR}/openshift-tests.bak"
    log_info "Removed backup"
fi

log_info "Done! Binary location: ${TESTS_BIN_DIR}/openshift-tests"
echo ""
log_info "Now try: ./list-tests.sh --profile recovery"
