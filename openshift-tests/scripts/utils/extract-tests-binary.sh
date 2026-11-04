#!/usr/bin/bash
#
# Extract openshift-tests binary from current cluster payload
#
# This ensures the binary matches your cluster version and includes
# the correct test suite for two-node fencing recovery tests.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared helpers for logging
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/test-helpers.sh"
setup_test_directories
PAYLOAD_DIR="${TESTS_BIN_DIR}/payload"

load_proxy_env || exit 1

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

# Extract new binary
log_info "Extracting openshift-tests from ${RELEASE_IMAGE}..."

# Get the tests image reference from the release
TESTS_IMAGE=$(oc adm release info "${RELEASE_IMAGE}" --image-for=tests)
if [[ -z "${TESTS_IMAGE}" ]]; then
    log_error "Failed to get tests image from release"
    exit 1
fi

log_info "Tests image: ${TESTS_IMAGE}"

# Extract openshift-tests binary from the tests image
# This directory is owned by the extractor; always start empty.
rm -rf -- "${PAYLOAD_DIR}"
mkdir -p "${PAYLOAD_DIR}"
oc image extract "${TESTS_IMAGE}" \
    --path=/usr/bin/openshift-tests:"${PAYLOAD_DIR}" \
    --confirm

if [[ ! -f "${PAYLOAD_DIR}/openshift-tests" ]]; then
    log_error "Failed to extract openshift-tests binary"
    exit 1
fi

chmod +x "${PAYLOAD_DIR}/openshift-tests"

# Verify new binary
NEW_VERSION=$("${PAYLOAD_DIR}/openshift-tests" version 2>/dev/null | head -1 || echo "unknown")
log_info "✓ Extracted: ${NEW_VERSION}"

# Verify it matches cluster
if echo "${NEW_VERSION}" | grep -q "${CLUSTER_VERSION}"; then
    log_info "✓ Version matches cluster: ${CLUSTER_VERSION}"
else
    log_warn "Version mismatch (may be OK if format differs):"
    log_warn "  Cluster: ${CLUSTER_VERSION}"
    log_warn "  Binary:  ${NEW_VERSION}"
fi

log_info "Done! Binary location: ${PAYLOAD_DIR}/openshift-tests"
echo ""
log_info "Now try: ${SCRIPT_DIR}/../run-suite.sh --profile recovery --list-only"
