#!/usr/bin/bash
# Shared reconnect loop for the Pacemaker and Corosync capture entrypoints.
# SCRIPT_DIR is the caller's captures directory (also used for default log paths).

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/capture-helpers.sh"

capture_master_log() {
    local NODE="$1" component="$2" reconnect_variable="$3" stream_label="$4" remote_command="$5"
    local LOG_DIR TIMESTAMP LOG_FILE MASTER_IP EXPLICIT_IP RECONNECT_SLEEP new_ip
    if [[ -z "${NODE}" ]] || [[ ! "${NODE}" =~ ^master-[01]$ ]]; then
        echo "Usage: $0 master-0|master-1"
        exit 1
    fi

    setup_capture_output "${SCRIPT_DIR}/../debug"
    LOG_FILE="${LOG_DIR}/${component}-${NODE}-${TIMESTAMP}.log"

    HYPERVISOR_IP="${HYPERVISOR_IP:-}"
    # shellcheck source=/dev/null
    source "$(dirname "${BASH_SOURCE[0]}")/ssh-helpers.sh"
    setup_ssh_config
    MASTER_SSH_USER="${MASTER_SSH_USER:-core}"
    MASTER_0_IP="${MASTER_0_IP:-}"
    MASTER_1_IP="${MASTER_1_IP:-}"
    VIRSH_LEASE_NETWORK="${VIRSH_LEASE_NETWORK:-ostestbm}"
    RECONNECT_SLEEP="${!reconnect_variable:-15}"
    require_capture_interval "${RECONNECT_SLEEP}" "${reconnect_variable}"

    # Nested SSH: local -> hypervisor.
    # Keepalives + BatchMode so a hung or half-open hop returns control to the reconnect
    # loop (e.g. when a node is ungracefully killed) instead of blocking indefinitely.
    setup_hypervisor_ssh 12 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o BatchMode=yes
    setup_guest_ssh_options

    # shellcheck source=/dev/null
    source "$(dirname "${BASH_SOURCE[0]}")/libvirt-helpers.sh"

    # Honor an explicit override IP if provided; otherwise resolve from DHCP leases and
    # keep re-resolving in the reconnect loop (a rebooted node may get a new lease).
    if [[ "${NODE}" == "master-0" ]]; then
        EXPLICIT_IP="${MASTER_0_IP}"
    else
        EXPLICIT_IP="${MASTER_1_IP}"
    fi
    MASTER_IP="$(refresh_master_ip "${NODE}" "${EXPLICIT_IP}")"

    if [[ -z "${HYPERVISOR_IP}" ]]; then
        echo "Error: HYPERVISOR_IP not set."
        exit 1
    fi
    if [[ -z "${MASTER_IP}" ]]; then
        echo "Warning: could not resolve ${NODE} IP yet from libvirt DHCP leases ${VIRSH_LEASE_NETWORK}; will keep retrying (set MASTER_0_IP/MASTER_1_IP to override)."
    fi

    echo "Tailing ${stream_label} on ${NODE} (${MASTER_SSH_USER}@${MASTER_IP}) -> ${LOG_FILE}"
    echo "Connection will drop when the node is destroyed; will reconnect every ${RECONNECT_SLEEP}s."
    echo "Stop with Ctrl+C when the test ends."
    echo ""

    trap 'exit 0' INT TERM

    while true; do
        new_ip="$(refresh_master_ip "${NODE}" "${EXPLICIT_IP}" "${MASTER_IP}")"
        if [[ -n "${new_ip}" && "${new_ip}" != "${MASTER_IP}" ]]; then
            echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ${NODE} IP changed ${MASTER_IP:-none} -> ${new_ip} ===" >> "${LOG_FILE}"
            MASTER_IP="${new_ip}"
        fi

        if [[ -z "${MASTER_IP}" ]]; then
            echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) ${NODE} IP unresolved, retrying in ${RECONNECT_SLEEP}s ===" >> "${LOG_FILE}"
            sleep "${RECONNECT_SLEEP}"
            continue
        fi

        echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) connected to ${NODE} (${MASTER_IP}) ===" >> "${LOG_FILE}"
        run_on_hypervisor ssh "${INNER_SSH_OPTS[@]}" -- "${MASTER_SSH_USER}@${MASTER_IP}" \
            "${remote_command}" \
            >> "${LOG_FILE}" 2>&1 || true
        echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) disconnected from ${NODE}, reconnecting in ${RECONNECT_SLEEP}s ===" >> "${LOG_FILE}"
        sleep "${RECONNECT_SLEEP}"
    done
}
