#!/usr/bin/bash
# Capture update-setup job status and pod details during node replacement
# CEO logs are collected separately by capture-ceo.sh.
# Polls every N seconds (UPDATE_SETUP_POLL_SEC, default 10)
# Started by run-all-captures.sh, stopped by stop-all-captures.sh
# Output: ${CAPTURE_LOG_DIR:-runs}/update-setup-job-<timestamp>.log; startup on stdout.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../utils/capture-helpers.sh"
setup_capture_output
POLL_SEC="${UPDATE_SETUP_POLL_SEC:-10}"
require_capture_interval "${POLL_SEC}" UPDATE_SETUP_POLL_SEC

# shellcheck disable=SC2153 # TIMESTAMP is assigned by setup_capture_output.
LOG_FILE="${LOG_DIR}/update-setup-job-${TIMESTAMP}.log"

# Required: set PROXY_ENV to the cluster proxy.env path for KUBECONFIG.
load_proxy_env

if [[ -z "${KUBECONFIG:-}" ]]; then
    echo "$(ts) KUBECONFIG not set, cannot monitor update-setup job" >&2
    exit 1
fi

echo "$(ts) Starting update-setup job monitor (poll every ${POLL_SEC}s) -> ${LOG_FILE}"

trap 'exit 0' INT TERM

# Track last seen pod to detect new pods
last_pod=""

while true; do
    timestamp=$(ts)

    # Get job status
    if ! job_json=$(oc get job -n openshift-etcd tnf-update-setup-job --ignore-not-found -o json 2>/dev/null); then
        echo "${timestamp} Job query failed"
    elif [[ -z "${job_json}" ]]; then
        echo "${timestamp} Job not found"
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
            | jq -r '.items[-1] | if . == null then "none" else "\(.metadata.name)|\(.spec.nodeName)|\(.status.phase)|\(.metadata.creationTimestamp)" end' 2>/dev/null) || latest_pod=""

        # Log job status
        echo "${timestamp} Job: active=${active} succeeded=${succeeded} failed=${failed}"
        echo "  nodeName=${node_name} nodeSelector=${node_selector}"
        if [[ -n "${conditions}" ]]; then
            echo "  conditions: ${conditions}"
        fi
        echo "  latest pod: ${latest_pod:-<query failed>}"

        # If new pod appeared, log details
        IFS='|' read -r current_pod pod_node pod_phase pod_created <<< "${latest_pod}"
        if [[ -n "${current_pod}" && "${current_pod}" != "none" && "${current_pod}" != "${last_pod}" ]]; then
            last_pod="${current_pod}"
            echo "${timestamp} NEW POD: ${current_pod}"

            echo "  created=${pod_created} node=${pod_node} phase=${pod_phase}"

            # Get container state
            container_state=$(oc get pod -n openshift-etcd "${current_pod}" -o json 2>/dev/null \
                | jq -r '.status.containerStatuses[]? | "\(.name): \(.state | keys[0]) \(.state[.state | keys[0]] | @json)"' 2>/dev/null || echo "unknown")
            echo "  container: ${container_state}"
        fi
    fi

    echo ""
    sleep "${POLL_SEC}"
done >> "${LOG_FILE}"
