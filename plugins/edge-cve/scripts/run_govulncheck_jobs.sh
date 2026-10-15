#!/usr/bin/env bash
# Launch OpenShift govulncheck jobs for CVE scan targets.
#
# Usage:
#   run_govulncheck_jobs.sh --workdir DIR [--namespace NS] [--repo SLUG ...] [--dry-run]
#
# Examples:
#   run_govulncheck_jobs.sh --workdir DIR --repo openshift/lvm-operator --dry-run
#   run_govulncheck_jobs.sh --workdir DIR --repo openshift/lvm-operator --repo openshift/microshift
#
# Prerequisites:
#   - oc logged into an OpenShift cluster
#   - scan-targets.json produced by build_scan_targets.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKDIR=""
NAMESPACE="edge-cve-scans"
REPO_FILTERS=()
DRY_RUN=false

usage() {
  cat <<EOF
Usage: $(basename "$0") --workdir DIR [--namespace NS] [--repo SLUG ...] [--dry-run]

Options:
  --repo SLUG   Only launch jobs for this repository (e.g. openshift/lvm-operator).
                Repeatable to scope to a set of repositories.

Creates the edge-cve-scans namespace/RBAC (if missing) and one Job per Go target.
Each job publishes its result as a labeled ConfigMap (edge-cve/repo, edge-cve/target-id)
instead of writing to shared storage.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workdir) WORKDIR="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --repo) REPO_FILTERS+=("$2"); shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
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

JOBS_DIR="${WORKDIR}/scans/jobs"
mkdir -p "${JOBS_DIR}"

if [[ "${DRY_RUN}" == "false" ]]; then
  if ! command -v oc >/dev/null 2>&1; then
    echo "Error: oc is required (OpenShift CLI)" >&2
    exit 1
  fi

  if ! oc whoami >/dev/null 2>&1; then
    echo "Error: not logged into OpenShift. Run 'oc login' first." >&2
    exit 1
  fi
fi

if [[ "${DRY_RUN}" == "false" ]]; then
  oc apply -f "${PLUGIN_DIR}/k8s/namespace.yaml"
  sed "s/namespace: edge-cve-scans/namespace: ${NAMESPACE}/g" \
    "${PLUGIN_DIR}/k8s/rbac.yaml" | oc apply -f -
  oc -n "${NAMESPACE}" create configmap edge-cve-govulncheck-scripts \
    --from-file=process_govulncheck_result.go="${SCRIPT_DIR}/process_govulncheck_result.go" \
    --from-file=scan_target.sh="${SCRIPT_DIR}/scan_target.sh" \
    --dry-run=client -o yaml | oc apply -f -
else
  echo "[dry-run] would apply namespace, RBAC (edge-cve-scanner ServiceAccount/Role/RoleBinding),"
  echo "[dry-run] and ConfigMap edge-cve-govulncheck-scripts from process_govulncheck_result.go + scan_target.sh"
fi

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

MANIFEST_INDEX="${JOBS_DIR}/index.json"
echo '{"jobs":[]}' > "${MANIFEST_INDEX}"
job_count=0

for line in "${TARGET_LINES[@]}"; do
  IFS='|' read -r target_id repo_url repo_slug git_ref cve_ids ticket_keys <<< "${line}"
  job_name="govulncheck-${target_id}"
  job_name="${job_name:0:63}"
  rendered="${JOBS_DIR}/${target_id}.yaml"
  repo_label="${repo_slug//\//--}"
  repo_label="$(printf '%s' "${repo_label}" | tr -cd 'A-Za-z0-9._-')"
  repo_label="${repo_label:0:63}"

  sed \
    -e "s|__TARGET_ID__|${target_id}|g" \
    -e "s|__REPO_URL__|${repo_url}|g" \
    -e "s|__REPO_SLUG__|${repo_slug}|g" \
    -e "s|__REPO_LABEL__|${repo_label}|g" \
    -e "s|__GIT_REF__|${git_ref}|g" \
    -e "s|__CVE_IDS__|${cve_ids}|g" \
    -e "s|__TICKET_KEYS__|${ticket_keys}|g" \
    -e "s|namespace: edge-cve-scans|namespace: ${NAMESPACE}|g" \
    "${PLUGIN_DIR}/k8s/govulncheck-job.yaml.template" > "${rendered}"

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[dry-run] would apply ${rendered}"
  else
    oc -n "${NAMESPACE}" delete job "${job_name}" --ignore-not-found=true >/dev/null 2>&1 || true
    oc apply -f "${rendered}"
    echo "Launched job ${job_name}"
  fi

  python3 - <<PY "${MANIFEST_INDEX}" "${target_id}" "${job_name}" "${rendered}"
import json, sys
path, target_id, job_name, manifest = sys.argv[1:5]
with open(path) as fh:
    data = json.load(fh)
data["jobs"].append({
    "target_id": target_id,
    "job_name": job_name,
    "manifest": manifest,
})
with open(path, "w") as fh:
    json.dump(data, fh, indent=2)
PY
  job_count=$((job_count + 1))
done

REPO_FILTERS_JSON="[]"
if [[ ${#REPO_FILTERS[@]} -gt 0 ]]; then
  REPO_FILTERS_JSON="$(printf '%s\n' "${REPO_FILTERS[@]}" | python3 -c 'import json, sys; print(json.dumps([l.rstrip() for l in sys.stdin]))')"
fi

cat <<EOF
{
  "namespace": "${NAMESPACE}",
  "repo_filters": ${REPO_FILTERS_JSON},
  "job_count": ${job_count},
  "manifest_index": "${MANIFEST_INDEX}",
  "dry_run": ${DRY_RUN}
}
EOF
