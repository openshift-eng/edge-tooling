#!/usr/bin/bash
# Run govulncheck locally via podman, one target at a time (sequential), for
# CVE scan targets. Mirrors run_govulncheck_jobs.sh but requires no
# OpenShift cluster - useful for local testing and dev loops.
#
# Usage:
#   run_govulncheck_podman.sh --workdir DIR [--repo SLUG ...] [--image IMAGE]
#
# Examples:
#   run_govulncheck_podman.sh --workdir DIR --repo openshift/lvm-operator
#   run_govulncheck_podman.sh --workdir DIR --repo openshift/lvm-operator --repo openshift/microshift
#
# Prerequisites:
#   - podman installed and able to pull registry.redhat.io images
#   - scan-targets.json produced by build_scan_targets.py
#
# Results land under ${WORKDIR}/scans/results/<target-id>/{result.json,govulncheck.json}
# and are aggregated into ${WORKDIR}/scans/govulncheck-results.json, in the
# same shape produced by collect_govulncheck_results.py, so generate_report.py
# works unchanged regardless of execution mode.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR=""
REPO_FILTERS=()
IMAGE="registry.redhat.io/ubi9/go-toolset:1.23"
CACHE_VOLUME="edge-cve-govulncheck-gocache"
# govulncheck's source-mode call-graph analysis (plus the go1.25 toolchain
# auto-download it triggers via GOTOOLCHAIN=auto) can need several GB of RAM
# for larger operator repos - 1-2Gi is not enough and gets SIGKILL'd (exit 137).
CONTAINER_MEMORY="16g"
CONTAINER_CPUS="3"
# Hard wall-clock cap per target so a hung clone/toolchain-download can't sit
# forever holding a container's writable layer open (this is what previously
# left an orphaned multi-GB container behind and filled the podman VM disk).
CONTAINER_TIMEOUT="1800"
# Opt-in only: never prune the host's podman store unless the caller explicitly
# asked for it (--prune). Unrelated images/containers on a shared machine must
# not be deleted as a side effect of a CVE scan.
RUN_PRUNE=0

usage() {
  cat <<EOF
Usage: $(basename "$0") --workdir DIR [--repo SLUG ...] [--image IMAGE] [--memory MEM] [--cpus N] [--timeout SECONDS] [--prune|--no-prune]

Runs govulncheck for each Go scan target sequentially in its own podman
container (no cluster required). A named podman volume (${CACHE_VOLUME})
is reused across targets to cache the Go toolchain/module downloads.

Options:
  --repo SLUG      Only scan this repository (e.g. openshift/lvm-operator).
                   Repeatable to scope to a set of repositories.
  --image IMAGE    Container image to use (default: ${IMAGE})
  --memory MEM     Container memory limit, podman --memory syntax (default: ${CONTAINER_MEMORY})
  --cpus N         Container CPU limit (default: ${CONTAINER_CPUS})
  --timeout SEC    Kill a single target's container after this many seconds (default: ${CONTAINER_TIMEOUT})
  --prune          Opt-in: run "podman system prune -f" before starting
  --no-prune       Explicitly skip prune (default; kept for callers that pass it)

Disk usage: each target runs in its own --rm container and the shared
${CACHE_VOLUME} volume only holds the Go module/toolchain cache (build cache
and the repo clone are cleaned up inside the container after each target -
see scan_target.sh). Host-wide "podman system prune -f" is opt-in via
--prune - it is never run unless you ask for it.

If a target's govulncheck run exits with 137 (SIGKILL), it was almost
certainly OOM-killed - re-run with a higher --memory.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workdir) WORKDIR="$2"; shift 2 ;;
    --repo) REPO_FILTERS+=("$2"); shift 2 ;;
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

if [[ -z "${WORKDIR}" ]]; then
  echo "Error: --workdir is required" >&2
  usage
  exit 1
fi

TARGETS_FILE="${WORKDIR}/scans/scan-targets.json"
if [[ ! -f "${TARGETS_FILE}" ]]; then
  echo "Error: ${TARGETS_FILE} not found. Run build_scan_targets.py first." >&2
  exit 1
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

# Force-remove whatever container is currently in flight if this script itself
# gets interrupted (Ctrl-C, killed by a wrapper/tool timeout, etc.) - this is
# exactly the scenario that previously orphaned a multi-GB container and filled
# the podman VM's disk, since a `podman run --rm` container that never exits
# cleanly never gets its writable layer reclaimed.
CURRENT_CONTAINER=""
cleanup_current_container() {
  if [[ -n "${CURRENT_CONTAINER}" ]]; then
    podman rm -f "${CURRENT_CONTAINER}" >/dev/null 2>&1 || true
  fi
}
# EXIT for normal termination; INT/TERM must exit after cleanup so the scan
# loop cannot resume with an orphaned/half-removed container.
trap cleanup_current_container EXIT
trap 'cleanup_current_container; exit 130' INT
trap 'cleanup_current_container; exit 143' TERM

if [[ ${RUN_PRUNE} -eq 1 ]]; then
  echo "Pruning stopped containers / dangling images (--prune explicitly requested)..." >&2
  podman system prune -f >&2 || true
fi
podman system df >&2 || true

RESULTS_DIR="${WORKDIR}/scans/results"
mkdir -p "${RESULTS_DIR}"

PYTHON_ARGS=("${TARGETS_FILE}")
if [[ ${#REPO_FILTERS[@]} -gt 0 ]]; then
  PYTHON_ARGS+=("${REPO_FILTERS[@]}")
fi

mapfile -t TARGET_LINES < <(
  python3 - <<'PY' "${PYTHON_ARGS[@]}"
import json, sys
targets_file = sys.argv[1]
repo_filters = sys.argv[2:]
with open(targets_file) as fh:
    data = json.load(fh)
targets = data.get("targets", [])
if repo_filters:
    targets = [t for t in targets if t.get("repo_slug") in repo_filters]
    print(f"Filtering to repos {repo_filters!r}: {len(targets)} target(s)", file=sys.stderr)
for target in targets:
    print("|".join([
        target["id"],
        target["repo_url"],
        target["repo_slug"],
        target["git_ref"],
        ",".join(target.get("cve_ids", [])),
        ",".join(target.get("ticket_keys", [])),
    ]))
PY
)

if [[ ${#TARGET_LINES[@]} -eq 0 ]]; then
  if [[ ${#REPO_FILTERS[@]} -gt 0 ]]; then
    echo "No Go scan targets found for repos (${REPO_FILTERS[*]}) in ${TARGETS_FILE}" >&2
  else
    echo "No Go scan targets found in ${TARGETS_FILE}" >&2
  fi
  exit 0
fi

echo "Running ${#TARGET_LINES[@]} target(s) sequentially with podman (image: ${IMAGE}, memory: ${CONTAINER_MEMORY}, cpus: ${CONTAINER_CPUS})" >&2

fail_count=0
oom_count=0
index=0
for line in "${TARGET_LINES[@]}"; do
  index=$((index + 1))
  IFS='|' read -r target_id repo_url repo_slug git_ref cve_ids ticket_keys <<< "${line}"
  repo_label="${repo_slug//\//--}"
  repo_label="$(printf '%s' "${repo_label}" | tr -cd 'A-Za-z0-9._-')"
  repo_label="${repo_label:0:63}"

  echo "[${index}/${#TARGET_LINES[@]}] Scanning ${repo_slug}@${git_ref} (target: ${target_id})" >&2

  container_name="edge-cve-scan-$(printf '%s' "${target_id}" | tr -cd 'A-Za-z0-9_.-')"
  container_name="${container_name:0:63}"
  # Clean up any same-named leftover from a previous interrupted run before reusing the name.
  podman rm -f "${container_name}" >/dev/null 2>&1 || true
  CURRENT_CONTAINER="${container_name}"

  run_cmd=(podman run --rm --name "${container_name}"
    --memory="${CONTAINER_MEMORY}" --cpus="${CONTAINER_CPUS}"
    -e REPO_URL="${repo_url}"
    -e REPO_SLUG="${repo_slug}"
    -e REPO_LABEL="${repo_label}"
    -e GIT_REF="${git_ref}"
    -e TARGET_ID="${target_id}"
    -e CVE_IDS="${cve_ids}"
    -e TICKET_KEYS="${ticket_keys}"
    -e RESULT_DIR=/results
    -e HOME=/tmp
    -e GOPATH=/tmp/go
    -e GOCACHE=/tmp/go/cache
    -e GOMODCACHE=/tmp/go/pkg/mod
    -e GOTOOLCHAIN=auto
    -v "${SCRIPT_DIR}/process_govulncheck_result.go:/scripts/process_govulncheck_result.go:ro,Z"
    -v "${SCRIPT_DIR}/scan_target.sh:/scripts/scan_target.sh:ro,Z"
    -v "${RESULTS_DIR}:/results:Z"
    -v "${CACHE_VOLUME}:/tmp/go:Z"
    "${IMAGE}" /bin/bash /scripts/scan_target.sh)
  if [[ -n "${TIMEOUT_BIN}" ]]; then
    run_cmd=("${TIMEOUT_BIN}" --kill-after=30 "${CONTAINER_TIMEOUT}" "${run_cmd[@]}")
  fi

  set +e
  "${run_cmd[@]}"
  scan_exit=$?
  set -e

  if [[ ${scan_exit} -eq 124 ]]; then
    echo "  -> timed out after ${CONTAINER_TIMEOUT}s (hung clone or toolchain download?) - forcing cleanup" >&2
    podman rm -f "${container_name}" >/dev/null 2>&1 || true
    fail_count=$((fail_count + 1))
  elif [[ ${scan_exit} -eq 137 ]]; then
    echo "  -> OOM-killed (exit 137, memory=${CONTAINER_MEMORY}). Re-run with a higher --memory." >&2
    oom_count=$((oom_count + 1))
    fail_count=$((fail_count + 1))
  elif [[ ${scan_exit} -ne 0 ]]; then
    echo "  -> govulncheck exited ${scan_exit} (see ${RESULTS_DIR}/${target_id}/ for details)" >&2
    fail_count=$((fail_count + 1))
  else
    echo "  -> clean" >&2
  fi
  CURRENT_CONTAINER=""
done

podman system df >&2 || true

AGGREGATE_FILE="${WORKDIR}/scans/govulncheck-results.json"
AGGREGATE_ARGS=("${RESULTS_DIR}" "${AGGREGATE_FILE}")
if [[ ${#REPO_FILTERS[@]} -gt 0 ]]; then
  AGGREGATE_ARGS+=("${REPO_FILTERS[@]}")
fi

python3 - <<PY "${AGGREGATE_ARGS[@]}"
import json, sys
from datetime import datetime, timezone
from pathlib import Path

results_dir, output_path = sys.argv[1], sys.argv[2]
repo_filters = sys.argv[3:]
results = []
for result_file in sorted(Path(results_dir).glob("*/result.json")):
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"Warning: invalid JSON in {result_file}", file=sys.stderr)
        continue
    # When --repo filters were used, only aggregate matching result files so a
    # prior unfiltered run's leftovers under results/ don't leak into the
    # output. No filters => keep every valid result (existing behavior).
    if repo_filters and result.get("repo_slug") not in repo_filters:
        continue
    results.append(result)

payload = {
    "collected_at": datetime.now(timezone.utc).isoformat(),
    "namespace": "local-podman",
    "repo_filters": repo_filters,
    "wait": {"skipped": True, "mode": "sequential-podman"},
    "results": results,
}
Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

affected = sum(1 for r in results if r.get("affected"))
print(f"Collected {len(results)} results ({affected} affected)", file=sys.stderr)
print(f"Written: {output_path}", file=sys.stderr)
print(json.dumps({"result_count": len(results), "affected_count": affected, "output": output_path}, indent=2))
PY

if [[ ${oom_count} -gt 0 ]]; then
  echo "Warning: ${oom_count}/${#TARGET_LINES[@]} target(s) were OOM-killed (exit 137) at --memory=${CONTAINER_MEMORY}." >&2
  echo "Their results are incomplete (govulncheck never finished) - re-run with e.g. --memory 6g." >&2
elif [[ ${fail_count} -gt 0 ]]; then
  echo "Note: ${fail_count} target(s) had a non-zero govulncheck exit code (may just mean findings were reported)." >&2
fi
