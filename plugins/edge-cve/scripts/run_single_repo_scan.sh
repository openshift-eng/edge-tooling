#!/usr/bin/bash
# Clone+scan a single repo@ref with govulncheck via podman - no scan-targets.json
# or Jira data required. Used for ad-hoc "is this repo/ref affected" checks
# (see cve-investigator.sh check-repo / edge-cve:investigate --check-repo).
#
# Usage:
#   run_single_repo_scan.sh --repo-url URL --ref REF --result-dir DIR
#     [--repo-slug SLUG] [--cve ID ...] [--ticket KEY ...]
#     [--image IMAGE] [--memory MEM] [--cpus N] [--timeout SECONDS] [--no-prune]
#
# --cve is repeatable and optional. If omitted, govulncheck's findings are
# treated as a general "any known vulnerability at this ref" check (see
# process_govulncheck_result.go); if given, only findings matching one of the
# listed CVE IDs/aliases are considered a match.
#
# Reuses the same scan_target.sh / process_govulncheck_result.go logic (and
# the shared edge-cve-govulncheck-gocache volume) as run_govulncheck_jobs.sh /
# run_govulncheck_podman.sh, so results are directly comparable, and the same
# disk-usage safeguards apply (named container, wall-clock timeout, cleanup
# trap, optional prune before starting).
#
# Writes RESULT-DIR/<target-id>/{result.json,govulncheck.json} and prints the
# result.json path on stdout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_URL=""
REPO_SLUG=""
GIT_REF=""
CVE_IDS=()
TICKET_KEYS=()
RESULT_DIR=""
IMAGE="registry.redhat.io/ubi9/go-toolset:1.23"
CACHE_VOLUME="edge-cve-govulncheck-gocache"
CONTAINER_MEMORY="6g"
CONTAINER_CPUS="3"
CONTAINER_TIMEOUT="1800"
# Opt-in only: never prune the host's podman store unless the caller explicitly
# asked for it (--prune).
RUN_PRUNE=0

usage() {
  cat <<EOF
Usage: $(basename "$0") --repo-url URL --ref REF --result-dir DIR [options]

Options:
  --repo-slug SLUG   Repo slug for labeling (default: derived from --repo-url)
  --cve ID           CVE to check for (repeatable). Omit for a general
                      "any known vulnerability" check.
  --ticket KEY       Jira ticket key for context (repeatable)
  --image IMAGE      Container image to use (default: ${IMAGE})
  --memory MEM       Container memory limit (default: ${CONTAINER_MEMORY})
  --cpus N           Container CPU limit (default: ${CONTAINER_CPUS})
  --timeout SEC      Kill the container after this many seconds (default: ${CONTAINER_TIMEOUT})
  --prune            Opt-in: run "podman system prune -f" before starting
  --no-prune         Explicitly skip prune (default; kept for callers that pass it)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --repo-slug) REPO_SLUG="$2"; shift 2 ;;
    --ref) GIT_REF="$2"; shift 2 ;;
    --cve) CVE_IDS+=("$2"); shift 2 ;;
    --ticket) TICKET_KEYS+=("$2"); shift 2 ;;
    --result-dir) RESULT_DIR="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    --memory) CONTAINER_MEMORY="$2"; shift 2 ;;
    --cpus) CONTAINER_CPUS="$2"; shift 2 ;;
    --timeout) CONTAINER_TIMEOUT="$2"; shift 2 ;;
    --prune) RUN_PRUNE=1; shift ;;
    --no-prune) RUN_PRUNE=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "${REPO_URL}" || -z "${GIT_REF}" || -z "${RESULT_DIR}" ]]; then
  echo "Error: --repo-url, --ref, and --result-dir are required" >&2
  usage
  exit 1
fi

if [[ -z "${REPO_SLUG}" ]]; then
  REPO_SLUG="$(printf '%s' "${REPO_URL}" | sed -E 's#^(https?://)?([^/]+/)?##; s#\.git$##')"
fi

if ! command -v podman >/dev/null 2>&1; then
  echo "Error: podman is required" >&2
  exit 1
fi

TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN="gtimeout"
else
  echo "Error: timeout (GNU coreutils) or gtimeout (macOS coreutils) is required for wall-clock container limits" >&2
  exit 1
fi

if [[ ${RUN_PRUNE} -eq 1 ]]; then
  echo "Pruning stopped containers / dangling images (--prune explicitly requested)..." >&2
  podman system prune -f >&2 || true
fi

mkdir -p "${RESULT_DIR}"

repo_label="${REPO_SLUG//\//--}"
repo_label="$(printf '%s' "${repo_label}" | tr -cd 'A-Za-z0-9._-')"
repo_label="${repo_label:0:63}"

ref_label="$(printf '%s' "${GIT_REF}" | tr -cd 'A-Za-z0-9._-')"
# Collision-resistant id: readable labels + digest of full URL/ref (normalized).
# Built before RESULT_DIR/<target-id>/ so truncated labels cannot collide.
digest="$(
  printf '%s\n%s\n' \
    "$(printf '%s' "${REPO_URL}" | tr '[:upper:]' '[:lower:]')" \
    "$(printf '%s' "${GIT_REF}" | tr '[:upper:]' '[:lower:]')" \
    | openssl dgst -sha256 \
    | awk '{print $NF}' \
    | cut -c1-8
)"
base="${repo_label}--${ref_label}"
max_base=$((120 - 2 - ${#digest}))
if (( max_base < 1 )); then
  max_base=1
fi
base="${base:0:${max_base}}"
target_id="${base}--${digest}"

cve_ids_csv=""
if [[ ${#CVE_IDS[@]} -gt 0 ]]; then
  cve_ids_csv="$(IFS=,; echo "${CVE_IDS[*]}")"
fi
ticket_keys_csv=""
if [[ ${#TICKET_KEYS[@]} -gt 0 ]]; then
  ticket_keys_csv="$(IFS=,; echo "${TICKET_KEYS[*]}")"
fi

container_name="edge-cve-check-$(printf '%s' "${target_id}" | tr -cd 'A-Za-z0-9_.-')"
container_name="${container_name:0:63}"
podman rm -f "${container_name}" >/dev/null 2>&1 || true

CURRENT_CONTAINER="${container_name}"
cleanup_current_container() {
  if [[ -n "${CURRENT_CONTAINER:-}" ]]; then
    podman rm -f "${CURRENT_CONTAINER}" >/dev/null 2>&1 || true
  fi
}
# EXIT for normal termination; INT/TERM must exit after cleanup so execution
# cannot resume after signal handling.
trap cleanup_current_container EXIT
trap 'cleanup_current_container; exit 130' INT
trap 'cleanup_current_container; exit 143' TERM

echo "Scanning ${REPO_SLUG}@${GIT_REF} (target: ${target_id})" >&2

run_cmd=(podman run --rm --name "${container_name}"
  --memory="${CONTAINER_MEMORY}" --cpus="${CONTAINER_CPUS}"
  -e REPO_URL="${REPO_URL}"
  -e REPO_SLUG="${REPO_SLUG}"
  -e REPO_LABEL="${repo_label}"
  -e GIT_REF="${GIT_REF}"
  -e TARGET_ID="${target_id}"
  -e CVE_IDS="${cve_ids_csv}"
  -e TICKET_KEYS="${ticket_keys_csv}"
  -e RESULT_DIR=/results
  -e HOME=/tmp
  -e GOPATH=/tmp/go
  -e GOCACHE=/tmp/go/cache
  -e GOMODCACHE=/tmp/go/pkg/mod
  -e GOTOOLCHAIN=auto
  -v "${SCRIPT_DIR}/process_govulncheck_result.go:/scripts/process_govulncheck_result.go:ro,Z"
  -v "${SCRIPT_DIR}/scan_target.sh:/scripts/scan_target.sh:ro,Z"
  -v "${RESULT_DIR}:/results:Z"
  -v "${CACHE_VOLUME}:/tmp/go:Z"
  "${IMAGE}" /bin/bash /scripts/scan_target.sh)
if [[ -n "${TIMEOUT_BIN}" ]]; then
  run_cmd=("${TIMEOUT_BIN}" --kill-after=30 "${CONTAINER_TIMEOUT}" "${run_cmd[@]}")
fi

# Capture the container's combined output (while still streaming it live via
# tee) so we can pull the result JSON straight from stdout instead of relying
# on the RESULT_DIR bind mount being immediately visible on the host right
# after the container exits - on podman machine (macOS/virtiofs) that can lag
# well behind the container's own exit, occasionally by tens of seconds.
#
# IMPORTANT: tee's own stdout is redirected to stderr (>&2) below. This
# script's actual stdout is the return-value channel (callers like
# cve-investigator.sh capture it via command substitution to get the
# result.json path) - if tee were left writing to stdout too, that capture
# would end up containing the whole container log instead of just the path.
container_log="$(mktemp)"
set +e
"${run_cmd[@]}" 2>&1 | tee "${container_log}" >&2
scan_exit=${PIPESTATUS[0]}
set -e
CURRENT_CONTAINER=""

if [[ ${scan_exit} -eq 124 ]]; then
  echo "Timed out after ${CONTAINER_TIMEOUT}s (hung clone or toolchain download?) - forcing cleanup" >&2
  podman rm -f "${container_name}" >/dev/null 2>&1 || true
elif [[ ${scan_exit} -eq 137 ]]; then
  echo "OOM-killed (exit 137, memory=${CONTAINER_MEMORY}) - re-run with a higher --memory" >&2
fi

result_file="${RESULT_DIR}/${target_id}/result.json"
mkdir -p "$(dirname "${result_file}")"
if sed -n '/^EDGE_CVE_RESULT_JSON_BEGIN$/,/^EDGE_CVE_RESULT_JSON_END$/p' "${container_log}" \
    | sed '1d;$d' > "${result_file}.tmp" && [[ -s "${result_file}.tmp" ]]; then
  mv "${result_file}.tmp" "${result_file}"
else
  rm -f "${result_file}.tmp"
  # Fall back to the bind-mounted copy (e.g. if markers weren't found for
  # some reason), retrying briefly for the same host/VM sync lag noted above.
  attempts=0
  while [[ ! -f "${result_file}" && ${attempts} -lt 30 ]]; do
    sleep 1
    attempts=$((attempts + 1))
  done
fi
rm -f "${container_log}"

if [[ ! -f "${result_file}" ]]; then
  echo "Error: expected result file not found/extractable: ${result_file} (scan exit ${scan_exit})" >&2
  exit 1
fi

echo "${result_file}"
