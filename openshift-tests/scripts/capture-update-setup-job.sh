#!/usr/bin/bash
# Capture update-setup job status, pod details, and CEO errors during node replacement
# Polls every N seconds (UPDATE_SETUP_POLL_SEC, default 10)
# Started by run-all-captures.sh, stopped by stop-all-captures.sh

set -euo pipefail

POLL_SEC="${UPDATE_SETUP_POLL_SEC:-10}"
TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${CAPTURE_LOG_DIR:-${SCRATCH_ROOT}/runs}"

LOG_FILE="${LOG_DIR}/update-setup-job-${TIMESTAMP}.log"

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }

# Source proxy.env if available for KUBECONFIG
PROXY_ENV="${PROXY_ENV:?PROXY_ENV must be set to the cluster proxy.env path (e.g. <two-node-toolbox-deploy>/openshift-clusters/proxy.env)}"
if [[ -f "${PROXY_ENV}" ]]; then
    set -a && source "${PROXY_ENV}" && set +a
fi

if [[ -z "${KUBECONFIG:-}" ]]; then
    echo "$(ts) KUBECONFIG not set, cannot monitor update-setup job" | tee -a "${LOG_FILE}"
    exit 1
fi

echo "$(ts) Starting update-setup job monitor (poll every ${POLL_SEC}s)" | tee -a "${LOG_FILE}"

trap 'exit 0' INT TERM

# Track last seen pod to detect new pods
last_pod=""

while true; do
    timestamp=$(ts)

    # Get job status
    job_json=$(oc get job -n openshift-etcd tnf-update-setup-job -o json 2>/dev/null || echo '{"kind":"NotFound"}')

    if [[ $(echo "$job_json" | jq -r '.kind') == "NotFound" ]]; then
        echo "${timestamp} Job not found" | tee -a "${LOG_FILE}"
    else
        # Extract job info
        active=$(echo "$job_json" | jq -r '.status.active // 0')
        succeeded=$(echo "$job_json" | jq -r '.status.succeeded // 0')
        failed=$(echo "$job_json" | jq -r '.status.failed // 0')
        node_name=$(echo "$job_json" | jq -r '.spec.template.spec.nodeName // "none"')
        node_selector=$(echo "$job_json" | jq -r '.spec.template.spec.nodeSelector // {}' | jq -c)

        # Get conditions
        conditions=$(echo "$job_json" | jq -r '.status.conditions[]? | "\(.type)=\(.status) (reason=\(.reason // "none") msg=\(.message // "none"))"' | paste -sd ';' -)

        # Get most recent pod (by creation time)
        latest_pod=$(oc get pods -n openshift-etcd -l job-name=tnf-update-setup-job \
            --sort-by=.metadata.creationTimestamp -o json 2>/dev/null \
            | jq -r '.items[-1] | "\(.metadata.name)|\(.spec.nodeName)|\(.status.phase)|\(.metadata.creationTimestamp)"' 2>/dev/null || echo "none")

        # Log job status
        {
            echo "${timestamp} Job: active=${active} succeeded=${succeeded} failed=${failed}"
            echo "  nodeName=${node_name} nodeSelector=${node_selector}"
            if [[ -n "${conditions}" ]]; then
                echo "  conditions: ${conditions}"
            fi
            echo "  latest pod: ${latest_pod}"
        } | tee -a "${LOG_FILE}"

        # If new pod appeared, log details
        current_pod=$(echo "${latest_pod}" | cut -d'|' -f1)
        if [[ -n "${current_pod}" && "${current_pod}" != "none" && "${current_pod}" != "${last_pod}" ]]; then
            last_pod="${current_pod}"
            echo "${timestamp} NEW POD: ${current_pod}" | tee -a "${LOG_FILE}"

            # Get pod details
            pod_node=$(echo "${latest_pod}" | cut -d'|' -f2)
            pod_phase=$(echo "${latest_pod}" | cut -d'|' -f3)
            pod_created=$(echo "${latest_pod}" | cut -d'|' -f4)

            echo "  created=${pod_created} node=${pod_node} phase=${pod_phase}" | tee -a "${LOG_FILE}"

            # Get container state
            container_state=$(oc get pod -n openshift-etcd "${current_pod}" -o json 2>/dev/null \
                | jq -r '.status.containerStatuses[]? | "\(.name): \(.state | keys[0]) \(.state[.state | keys[0]] | @json)"' 2>/dev/null || echo "unknown")
            echo "  container: ${container_state}" | tee -a "${LOG_FILE}"
        fi
    fi

    # Get recent CEO errors (only lines with update-setup or job-related errors)
    ceo_relevant=$(oc logs -n openshift-etcd-operator deployment/etcd-operator --tail=30 --since=${POLL_SEC}s 2>/dev/null \
        | grep -i "update-setup\|tnf.*job\|job.*failed\|job.*complete\|getActivePacemakerNodes\|schedulableNodesFunc" \
        | tail -5 || echo "")

    if [[ -n "${ceo_relevant}" ]]; then
        echo "${timestamp} CEO:" | tee -a "${LOG_FILE}"
        echo "${ceo_relevant}" | sed 's/^/  /' | tee -a "${LOG_FILE}"
    fi

    echo "" >> "${LOG_FILE}"
    sleep "${POLL_SEC}"
done
