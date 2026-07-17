#!/usr/bin/env bash
# Clone a repo at a target ref, run govulncheck, and process the result.
#
# Shared by both execution modes:
#   - OpenShift Job (k8s/govulncheck-job.yaml.template), mounted from the
#     edge-cve-govulncheck-scripts ConfigMap
#   - Local podman runner (run_govulncheck_podman.sh), mounted as a bind mount
#
# Required env vars: REPO_URL, GIT_REF, TARGET_ID, CVE_IDS, TICKET_KEYS,
# REPO_SLUG, REPO_LABEL.
#
# Result publishing (handled by process_govulncheck_result.go):
#   - If RESULT_DIR is set, results are written to local files under it
#     (podman/local mode).
#   - Otherwise, results are published to a Kubernetes ConfigMap using the
#     in-cluster service account (OpenShift Job mode).
#
# Disk usage: the repo clone and Go build cache are removed on exit (see the
# cleanup trap below) so repeated runs against a shared cache volume/container
# don't grow disk usage without bound.
set -euo pipefail

workdir="/tmp/workspace/repo"
mkdir -p "${workdir}" /tmp/go/bin /tmp/go/cache /tmp/go/pkg/mod

# Always clean up the cloned repo tree and trim the build cache on exit, even
# on failure. GOMODCACHE (downloaded module sources) and the go toolchain
# under /tmp/go are left alone since they're reused heavily across targets
# (same deps across repo versions) and matter for scan speed; the git
# checkout and GOCACHE build objects have little/no reuse value across
# different repos/refs and are the main source of unbounded growth in the
# shared cache volume / container writable layer over many sequential runs.
cleanup() {
  local ec=$?
  cd / 2>/dev/null || true
  rm -rf "${workdir}" 2>/dev/null || true
  go clean -cache 2>/dev/null || true
  exit "${ec}"
}
trap cleanup EXIT

git clone --depth 1 --branch "${GIT_REF}" "${REPO_URL}" "${workdir}" 2>/dev/null \
  || git clone --depth 1 "${REPO_URL}" "${workdir}"

cd "${workdir}"
if ! git rev-parse --verify "${GIT_REF}" >/dev/null 2>&1; then
  git fetch --depth 1 origin "${GIT_REF}" || true
fi
if ! git checkout "${GIT_REF}" 2>/dev/null && ! git checkout "origin/${GIT_REF}" 2>/dev/null; then
  echo "Error: failed to checkout GIT_REF=${GIT_REF} (also tried origin/${GIT_REF})" >&2
  exit 1
fi

commit="$(git rev-parse HEAD)"
export GOTOOLCHAIN=auto
go install golang.org/x/vuln/cmd/govulncheck@latest
export PATH="/tmp/go/bin:${PATH}"

set +e
govulncheck -json ./... > /tmp/govulncheck.json 2>/tmp/govulncheck.err
scan_exit=$?
set -e

export COMMIT="${commit}"
export SCAN_EXIT="${scan_exit}"
go run /scripts/process_govulncheck_result.go

exit "${scan_exit}"
