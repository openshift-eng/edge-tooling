#!/usr/bin/bash
# Load the required cluster environment for runners and capture entrypoints.

load_proxy_env() {
    local proxy_env="${PROXY_ENV:?PROXY_ENV must be set to the cluster proxy.env path (e.g. <two-node-toolbox-deploy>/openshift-clusters/proxy.env)}"
    local had_allexport=false source_status=0
    if [[ ! -f "${proxy_env}" ]]; then
        printf 'Error: proxy.env not found at %s\n' "${proxy_env}" >&2
        return 1
    fi

    [[ $- == *a* ]] && had_allexport=true
    set -a
    # shellcheck source=/dev/null
    source "${proxy_env}" || source_status=$?
    if [[ "${had_allexport}" == false ]]; then
        set +a
    fi
    return "${source_status}"
}
