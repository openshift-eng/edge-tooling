#!/usr/bin/bash
# Monitor policy from the baremetalds-two-node-fencing-recovery CI workflow.
# Explicit empty values disable the corresponding flags.

setup_monitor_defaults() {
    : "${OPENSHIFT_TESTS_DISABLE_MONITORS=etcd-log-analyzer,legacy-cvo-invariants,legacy-etcd-invariants,node-lifecycle,oc-adm-upgrade-status}"
    : "${OPENSHIFT_TESTS_CLUSTER_STABILITY=Disruptive}"
}

setup_monitor_args() {
    MONITOR_ARGS=()
    if [[ -n "${OPENSHIFT_TESTS_DISABLE_MONITORS:-}" ]]; then
        MONITOR_ARGS+=("--disable-monitor=${OPENSHIFT_TESTS_DISABLE_MONITORS}")
    fi
    if [[ -n "${OPENSHIFT_TESTS_CLUSTER_STABILITY:-}" ]]; then
        MONITOR_ARGS+=("--cluster-stability=${OPENSHIFT_TESTS_CLUSTER_STABILITY}")
    fi
}
