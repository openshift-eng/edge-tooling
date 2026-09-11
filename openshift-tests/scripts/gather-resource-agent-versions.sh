#!/usr/bin/bash
# Query the installed resource-agents RPM version on both master nodes.
#
# The node-replacement recovery test reprovisions a node from the base image,
# which drops the layered/patched resource-agents RPM and reverts that node to
# stock. After such a run the two masters can silently disagree on which
# resource-agent build they carry (a "mixed build"), which is exactly the kind of
# skew that produced the dual-force-new-cluster wedge. This script reports the
# build on each node so the user can decide whether to re-patch.
#
# Masters are resolved from virsh net-dhcp-leases on the hypervisor by default.
#
# Usage: gather-resource-agent-versions.sh
#
# Requires: HYPERVISOR_IP, SSH_USER, SSH_KEY_PATH (auto-loaded from proxy.env via
#           test-helpers.sh if not already set). Optional: MASTER_0_IP,
#           MASTER_1_IP, MASTER_SSH_USER (default: core).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load hypervisor connection details from proxy.env if the caller has not already
# exported them (lets this script run standalone as well as from run-suite.sh).
if [[ -z "${HYPERVISOR_IP:-}" ]]; then
    # shellcheck source=/dev/null
    source "${SCRIPT_DIR}/test-helpers.sh"
    load_proxy_env || true
    setup_hypervisor_config || true
fi

HYPERVISOR_IP="${HYPERVISOR_IP:-}"
SSH_USER="${SSH_USER:-ec2-user}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_redhat}"
[[ -z "${SSH_KEY_PATH}" || ! -f "${SSH_KEY_PATH}" ]] && SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
MASTER_SSH_USER="${MASTER_SSH_USER:-core}"
MASTER_0_IP="${MASTER_0_IP:-}"
MASTER_1_IP="${MASTER_1_IP:-}"
VIRSH_LEASE_NETWORK="${VIRSH_LEASE_NETWORK:-ostestbm}"

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "Error: HYPERVISOR_IP not set (check proxy.env or export it)." >&2
    exit 1
fi

HYPERVISOR_SSH=(ssh -o "ConnectTimeout=12" -o "ServerAliveInterval=15" -o "ServerAliveCountMax=3" -o "BatchMode=yes" -o "StrictHostKeyChecking=no" -i "${SSH_KEY_PATH}" "${SSH_USER}@${HYPERVISOR_IP}")
INNER_SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3"

resolve_master_ip_from_leases() {
    local node_name="$1"
    "${HYPERVISOR_SSH[@]}" "virsh -c qemu:///system net-dhcp-leases \"${VIRSH_LEASE_NETWORK}\" 2>/dev/null" \
        | awk -v node="${node_name}" '
            $0 ~ /^[[:space:]]*$/ { next }
            $0 ~ /^[[:space:]]*Expiry/ { next }
            $0 ~ /^[[:space:]]*-+/ { next }
            index($0, node) {
                for (i = 1; i <= NF; i++) {
                    if ($i ~ /^[0-9a-fA-F:.]+\/[0-9]+$/) {
                        split($i, a, "/")
                        print a[1]
                        exit
                    }
                }
            }
        '
}

query_node() {
    local node="$1" explicit_ip="$2"
    local ip
    if [[ -n "${explicit_ip}" ]]; then
        ip="${explicit_ip}"
    else
        ip="$(resolve_master_ip_from_leases "${node}" || true)"
    fi

    if [[ -z "${ip}" ]]; then
        printf '%s\tUNRESOLVED\t(could not resolve IP from virsh net-dhcp-leases %s)\n' \
            "${node}" "${VIRSH_LEASE_NETWORK}"
        return
    fi

    local version
    version="$("${HYPERVISOR_SSH[@]}" \
        "ssh ${INNER_SSH_OPTS} ${MASTER_SSH_USER}@${ip} 'rpm -q resource-agents'" 2>/dev/null || true)"

    if [[ -z "${version}" ]]; then
        printf '%s\t%s\tUNREACHABLE (node down or ssh failed)\n' "${node}" "${ip}"
    else
        printf '%s\t%s\t%s\n' "${node}" "${ip}" "${version}"
    fi
}

echo "========================================"
echo "resource-agents version on each master"
echo "========================================"
M0="$(query_node master-0 "${MASTER_0_IP}")"
M1="$(query_node master-1 "${MASTER_1_IP}")"
printf '%s\n%s\n' "${M0}" "${M1}" | column -t -s $'\t'
echo ""

# Report whether the two masters disagree on the resource-agents build.
#
# A "mixed build" (differing version strings) is NOT automatically a problem.
# The hazard is specifically one master on the STOCK (unpatched) resource-agents
# while the other carries the patched podman-etcd OCF agent -- that skew is what
# produces the dual-force-new-cluster wedge. It typically happens when a node is
# reprovisioned (e.g. the node-replacement recovery test) and loses its layered
# RPM. If BOTH masters carry a PATCHED resource-agents RPM, differing versions
# are SAFE: the fix is present on both, so recovery behaves correctly.
#
# rpm -q alone cannot always tell patched from stock. Set PATCHED_RA_MARKER to a
# substring unique to your patched build (e.g. the scratch-build release tag) to
# get a definitive patched/stock verdict; otherwise this prints guidance.
V0="$(printf '%s' "${M0}" | cut -f3)"
V1="$(printf '%s' "${M1}" | cut -f3)"
PATCHED_RA_MARKER="${PATCHED_RA_MARKER:-}"

ra_is_patched() {
    # Returns 0 (patched) only when PATCHED_RA_MARKER is set and present in the
    # version string. Without a marker we cannot classify, so return 1 (unknown).
    [[ -n "${PATCHED_RA_MARKER}" && "$1" == *"${PATCHED_RA_MARKER}"* ]]
}

if [[ "${V0}" == resource-agents-* && "${V1}" == resource-agents-* && "${V0}" != "${V1}" ]]; then
    echo "NOTE: masters report DIFFERENT resource-agents builds (mixed build):"
    echo "        master-0: ${V0}"
    echo "        master-1: ${V1}"
    if [[ -n "${PATCHED_RA_MARKER}" ]]; then
        if ra_is_patched "${V0}" && ra_is_patched "${V1}"; then
            echo "SAFE: both masters carry the PATCHED resource-agents (marker '${PATCHED_RA_MARKER}')."
            echo "      Differing version strings are fine -- the podman-etcd OCF fix is on both."
        else
            echo "WARNING: at least one master is NOT on the patched build (marker '${PATCHED_RA_MARKER}')."
            echo "         A node likely reverted to STOCK resource-agents (e.g. after reprovision)."
            echo "         Re-patch the stock master before trusting further recovery results."
        fi
    else
        echo "      This is only UNSAFE if one master is on the STOCK (unpatched) build."
        echo "      If BOTH carry the patched podman-etcd OCF agent, a mixed build is SAFE."
        echo "      To classify automatically, re-run with PATCHED_RA_MARKER=<patched-build-tag>."
    fi
elif [[ "${V0}" == resource-agents-* && "${V0}" == "${V1}" ]]; then
    echo "OK: both masters report the same resource-agents build."
fi
