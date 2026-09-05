#!/usr/bin/bash
# Paper trail for OVN-K chassis propagation: API + host OVS + SB + virsh VM signatures, all timestamped (UTC, ms).
# Run via run-all-captures.sh alongside the NR test (not inside openshift-tests).
#
# Env:
#   OVN_CHASSIS_ONCE                – if 1 or true: run a single sample (API + host OVS + optional SB/virsh per *_EVERY), then exit.
#                                     Use to validate a new env before run-all-captures.sh / the disruptive NR test (not openshift-tests).
#   OVN_CHASSIS_POLL_INTERVAL_SEC   – base poll (default: 1, increased for replacement forensics)
#   OVN_CHASSIS_SB_EVERY            – every N samples: ovn-sbctl Chassis from a pod with sbdb (default: 1)
#   OVN_CHASSIS_VIRSH_EVERY         – every N samples: virsh list + per-VM uuid/disk/mac summary on hypervisor (default: 10)
#   OVN_CHASSIS_IDENTITY_EVERY      – every N samples: oc logs tail network-node-identity (default: 10)
#   OVN_CHASSIS_TRACE_EXTRA_EVERY   – every N samples: ovnkube-node pod table + grep-filtered log tails (default: 15)
#   OVN_CHASSIS_L3GW_EVERY          – every N samples: dump k8s.ovn.org/l3-gateway-config snippet (first 200 chars) per node (default: 20)
#   HYPERVISOR_IP, SSH_USER, SSH_KEY_PATH – virsh + nested SSH to masters via hypervisor
#   MASTER_0_IP, MASTER_1_IP, MASTER_SSH_USER
#   KUBECONFIG – API / oc exec SB / identity tail
#
# Output: stdout (redirect to runs/ovn-chassis-trace-<CAPTURE_TIMESTAMP>.log).

set -uo pipefail

ts() { date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"; }

TIMESTAMP="${CAPTURE_TIMESTAMP:-$(date +%Y%m%d-%H%M%S)}"

HYPERVISOR_IP="${HYPERVISOR_IP:-}"
SSH_USER="${SSH_USER:-ec2-user}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_redhat}"
[[ -z "${SSH_KEY_PATH}" || ! -f "${SSH_KEY_PATH}" ]] && SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
MASTER_SSH_USER="${MASTER_SSH_USER:-core}"
MASTER_0_IP="${MASTER_0_IP:-}"
MASTER_1_IP="${MASTER_1_IP:-}"
VIRSH_LEASE_NETWORK="${VIRSH_LEASE_NETWORK:-ostestbm}"

POLL_SEC="${OVN_CHASSIS_POLL_INTERVAL_SEC:-1}"
EXTRA_EVERY="${OVN_CHASSIS_TRACE_EXTRA_EVERY:-15}"
SB_EVERY="${OVN_CHASSIS_SB_EVERY:-1}"
VIRSH_EVERY="${OVN_CHASSIS_VIRSH_EVERY:-10}"
IDENTITY_EVERY="${OVN_CHASSIS_IDENTITY_EVERY:-10}"
L3GW_EVERY="${OVN_CHASSIS_L3GW_EVERY:-20}"

OVN_NS="openshift-ovn-kubernetes"

if [[ -z "${HYPERVISOR_IP}" ]]; then
    echo "$(ts) capture-ovn-chassis-trace: HYPERVISOR_IP not set" >&2
    exit 1
fi

HYPERVISOR_SSH=(ssh -o "ConnectTimeout=12" -o "StrictHostKeyChecking=no" -i "${SSH_KEY_PATH}" "${SSH_USER}@${HYPERVISOR_IP}")

OVS_CMD='sudo ovs-vsctl --if-exists get Open_vSwitch . external_ids:system-id 2>&1; echo " ovs_hostname=$(sudo ovs-vsctl --if-exists get Open_vSwitch . external_ids:hostname 2>&1 | tr -d "\n")"'

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

if [[ -z "${MASTER_0_IP}" ]]; then
    MASTER_0_IP="$(resolve_master_ip_from_leases "master-0" || true)"
fi
if [[ -z "${MASTER_1_IP}" ]]; then
    MASTER_1_IP="$(resolve_master_ip_from_leases "master-1" || true)"
fi
if [[ -z "${MASTER_0_IP}" ]] || [[ -z "${MASTER_1_IP}" ]]; then
    echo "$(ts) capture-ovn-chassis-trace: failed to resolve master IPs from virsh net-dhcp-leases ${VIRSH_LEASE_NETWORK}; set MASTER_0_IP/MASTER_1_IP explicitly" >&2
    exit 1
fi
echo "$(ts) capture-ovn-chassis-trace: resolved master-0=${MASTER_0_IP} master-1=${MASTER_1_IP} from ${VIRSH_LEASE_NETWORK}"

sample_host() {
    local name="$1" ip="$2"
    local out rc=0
    # Nested SSH: local -> hypervisor -> master. This avoids local known_hosts checks for 192.168.111.x.
    out=$("${HYPERVISOR_SSH[@]}" \
        "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 -o ServerAliveInterval=20 ${MASTER_SSH_USER}@${ip} '${OVS_CMD}'" \
        2>&1) || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
        echo "$(ts)  [host ${name} ${ip}] SSH_FAILED rc=${rc} out=${out//$'\n'/ }"
        return 0
    fi
    echo "$(ts)  [host ${name} ${ip}] ${out//$'\n'/ }"
}

sample_api_nodes() {
    if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
        echo "$(ts)  [api] (skip: KUBECONFIG unset or oc not in PATH)"
        return 0
    fi
    local names
    names=$(oc get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null) || {
        echo "$(ts)  [api] oc get nodes failed"
        return 0
    }
    echo "$(ts)  [api] Node uid / InternalIP / node-chassis-id / rv / creationTimestamp:"
    local n
    for n in ${names}; do
        local uid ip ch rv created
        uid=$(oc get node "${n}" -o jsonpath='{.metadata.uid}' 2>/dev/null || echo "?")
        ip=$(oc get node "${n}" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "?")
        ch=$(oc get node "${n}" -o jsonpath='{.metadata.annotations.k8s\.ovn\.org/node-chassis-id}' 2>/dev/null || true)
        rv=$(oc get node "${n}" -o jsonpath='{.metadata.resourceVersion}' 2>/dev/null || echo "?")
        created=$(oc get node "${n}" -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null || echo "?")
        [[ -z "${ch}" ]] && ch="<empty>"
        echo "$(ts)    node=${n} uid=${uid} internal-ip=${ip} rv=${rv} created=${created} node-chassis-id=${ch}"
    done
}

# Pick first Running pod that has an sbdb container accepting exec (ovnkube-node, else ovnkube-control-plane).
pick_sb_pod() {
    local cand
    while read -r cand; do
        [[ -z "${cand}" ]] && continue
        if oc exec -n "${OVN_NS}" "${cand}" -c sbdb -- true &>/dev/null; then
            echo "${cand}"
            return 0
        fi
    done < <(oc get pods -n "${OVN_NS}" -l app=ovnkube-node -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\n"}{end}' 2>/dev/null)
    while read -r cand; do
        [[ -z "${cand}" ]] && continue
        if oc exec -n "${OVN_NS}" "${cand}" -c sbdb -- true &>/dev/null; then
            echo "${cand}"
            return 0
        fi
    done < <(oc get pods -n "${OVN_NS}" -l app=ovnkube-control-plane -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\n"}{end}' 2>/dev/null)
    return 1
}

sample_sb_chassis() {
    if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
        echo "$(ts)  [sb] (skip: no oc)"
        return 0
    fi
    local pod
    pod=$(pick_sb_pod) || {
        echo "$(ts)  [sb] no Running pod with working sbdb exec in ${OVN_NS}"
        return 0
    }
    echo "$(ts)  [sb] Chassis name+hostname (ovn-sbctl via pod=${pod} container=sbdb)"
    local row
    while IFS= read -r row || [[ -n "${row}" ]]; do
        [[ -z "${row}" ]] && continue
        echo "$(ts)    [sb-row] ${row}"
    done < <(oc exec -n "${OVN_NS}" "${pod}" -c sbdb -- \
        ovn-sbctl --columns=name,hostname --no-headings find chassis 2>&1) || echo "$(ts)    [sb] ovn-sbctl find chassis failed"

    echo "$(ts)  [sb] Per-node hostname filter (matches Node name):"
    local n node_names
    node_names=$(oc get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null) || node_names=""
    for n in ${node_names}; do
        local rows
        rows=$(oc exec -n "${OVN_NS}" "${pod}" -c sbdb -- \
            ovn-sbctl --columns=name,hostname --no-headings find chassis "hostname==\"${n}\"" 2>/dev/null | tr '\n' '; ')
        echo "$(ts)    [sb] hostname==${n} -> ${rows:-<none>}"
    done
}

sample_virsh_snapshot() {
    echo "$(ts)  [virsh] hypervisor=${HYPERVISOR_IP} list --all + domuuid + dumpxml disk/uuid/mac grep"
    while IFS= read -r line || [[ -n "${line}" ]]; do
        echo "$(ts)    [virsh] ${line}"
    done < <("${HYPERVISOR_SSH[@]}" bash -s <<'REMOTE'
set -uo pipefail
echo "[list]"
virsh -c qemu:///system list --all || true
echo "[domains]"
while read -r vm; do
  [[ -z "${vm}" ]] && continue
  echo "=== name=${vm} ==="
  virsh -c qemu:///system domuuid "${vm}" 2>/dev/null || true
  virsh -c qemu:///system dumpxml "${vm}" 2>/dev/null | grep -E '<uuid>|<name>|<disk |<source file=|<source dev=|<mac address' || true
done < <(virsh -c qemu:///system list --all --name | sed '/^$/d')
REMOTE
)
}

sample_identity_tail() {
    if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
        return 0
    fi
    echo "$(ts)  [nnid] tail network-node-identity (openshift-network-operator, last 50 lines)"
    if oc get deployment network-node-identity -n openshift-network-operator &>/dev/null; then
        while IFS= read -r row || [[ -n "${row}" ]]; do
            echo "$(ts)    [nnid] ${row}"
        done < <(oc logs -n openshift-network-operator deployment/network-node-identity --tail=50 --timestamps=true 2>&1) || true
    else
        echo "$(ts)    [nnid] deployment not found (skip)"
    fi
}

sample_l3_gateway_snippet() {
    if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
        return 0
    fi
    echo "$(ts)  [l3gw] k8s.ovn.org/l3-gateway-config first 220 chars (per node; chassis id also in k8s.ovn.org/node-chassis-id):"
    local n v node_names
    node_names=$(oc get nodes -o jsonpath='{.items[*].metadata.name}' 2>/dev/null) || node_names=""
    for n in ${node_names}; do
        v=$(oc get node "${n}" -o jsonpath="{.metadata.annotations['k8s.ovn.org/l3-gateway-config']}" 2>/dev/null || true)
        if [[ -z "${v}" ]]; then
            echo "$(ts)    ${n}: <empty>"
        else
            echo "$(ts)    ${n}: ${v:0:220}$([[ ${#v} -gt 220 ]] && echo '...')"
        fi
    done
}

sample_ovn_pods_and_logs() {
    if [[ -z "${KUBECONFIG:-}" ]] || ! command -v oc &>/dev/null; then
        return 0
    fi
    echo "$(ts)  [ovn-k-extra] ovnkube-node pods wide:"
    while IFS= read -r row || [[ -n "${row}" ]]; do
        echo "$(ts)    [pods] ${row}"
    done < <(oc get pods -n "${OVN_NS}" -l app=ovnkube-node -o wide 2>/dev/null) || echo "$(ts)    (oc get pods failed)"
    echo "$(ts)  [ovn-k-extra] grep-filtered ovnkube-node container logs (last 120 lines scanned, show up to 30 matches/pod):"
    local p
    while read -r p; do
        [[ -z "${p}" ]] && continue
        echo "$(ts)    --- pod ${p} ---"
        while IFS= read -r row || [[ -n "${row}" ]]; do
            echo "$(ts)      [ovn-k] ${row}"
        done < <(oc logs -n "${OVN_NS}" "${p}" -c ovnkube-node --tail=120 2>/dev/null \
            | grep -iE 'chassis|system-id|gateway|node-chassis|annot|SetL3|OvnNode|mismatch|stale|identity|webhook' \
            | tail -n 30) || echo "$(ts)      (no matches or logs failed)"
    done < <(oc get pods -n "${OVN_NS}" -l app=ovnkube-node -o jsonpath='{range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\n"}{end}' 2>/dev/null)
}

echo "$(ts) capture-ovn-chassis-trace: start file_ts=${TIMESTAMP} poll=${POLL_SEC}s sb_every=${SB_EVERY} virsh_every=${VIRSH_EVERY} identity_every=${IDENTITY_EVERY} extra_every=${EXTRA_EVERY} l3gw_every=${L3GW_EVERY}"
echo "$(ts) Correlate: Node k8s.ovn.org/node-chassis-id vs host ovs external_ids:system-id vs SB Chassis.name; virsh shows disk path/uuid for same-VM/same-disk suspicions."

trap 'exit 0' INT TERM

n=0
while true; do
    echo ""
    echo "$(ts) ========== OVN-CHASSIS-TRACE sample=${n} =========="

    sample_api_nodes
    sample_host "master-0" "${MASTER_0_IP}"
    sample_host "master-1" "${MASTER_1_IP}"

    if [[ "${SB_EVERY}" =~ ^[0-9]+$ ]] && [[ "${SB_EVERY}" -gt 0 ]] && (( n % SB_EVERY == 0 )); then
        sample_sb_chassis
    fi

    if [[ "${VIRSH_EVERY}" =~ ^[0-9]+$ ]] && [[ "${VIRSH_EVERY}" -gt 0 ]] && (( n % VIRSH_EVERY == 0 )); then
        sample_virsh_snapshot
    fi

    if [[ "${IDENTITY_EVERY}" =~ ^[0-9]+$ ]] && [[ "${IDENTITY_EVERY}" -gt 0 ]] && (( n % IDENTITY_EVERY == 0 )); then
        sample_identity_tail
    fi

    if [[ "${L3GW_EVERY}" =~ ^[0-9]+$ ]] && [[ "${L3GW_EVERY}" -gt 0 ]] && (( n % L3GW_EVERY == 0 )); then
        sample_l3_gateway_snippet
    fi

    if [[ "${EXTRA_EVERY}" =~ ^[0-9]+$ ]] && [[ "${EXTRA_EVERY}" -gt 0 ]]; then
        if (( n % EXTRA_EVERY == 0 && n > 0 )); then
            sample_ovn_pods_and_logs
        fi
    fi

    n=$((n + 1))
    case "${OVN_CHASSIS_ONCE:-}" in
        1|true|yes)
            echo "$(ts) capture-ovn-chassis-trace: OVN_CHASSIS_ONCE set, exiting after one sample"
            exit 0
            ;;
    esac
    sleep "${POLL_SEC}"
done
