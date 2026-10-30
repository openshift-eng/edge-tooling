#!/usr/bin/bash
# Source for shared SSH defaults and command construction.

# Set SSH_USER/SSH_KEY_PATH defaults; a missing selected key falls back
# to ~/.ssh/id_ed25519.
setup_ssh_config() {
    SSH_USER="${SSH_USER:-ec2-user}"
    SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_redhat}"
    if [[ ! -f "${SSH_KEY_PATH}" ]]; then
        SSH_KEY_PATH="$HOME/.ssh/id_ed25519"
    fi
}

# Call setup_ssh_config first. Arguments: connect timeout, then extra SSH options.
# Populate HYPERVISOR_SSH using those settings and HYPERVISOR_IP.
setup_hypervisor_ssh() {
    local connect_timeout="$1"
    shift
    # Command array consumed by callers and libvirt-helpers.sh.
    # shellcheck disable=SC2034
    HYPERVISOR_SSH=(
        ssh -o "ConnectTimeout=${connect_timeout}" "$@"
        -o StrictHostKeyChecking=no -i "${SSH_KEY_PATH}" --
        "${SSH_USER}@${HYPERVISOR_IP}"
    )
}

# Populate INNER_SSH_OPTS as an array for guest SSH through the hypervisor.
setup_guest_ssh_options() {
    # shellcheck disable=SC2034
    INNER_SSH_OPTS=(
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
        -o LogLevel=ERROR -o BatchMode=yes -o ConnectTimeout=10
        -o ServerAliveInterval=15 -o ServerAliveCountMax=3
    )
}

# Serialize argv for the hypervisor's Bash shell without evaluating its values.
# Requires HYPERVISOR_SSH; forwards output and returns the SSH exit status.
run_on_hypervisor() {
    local remote_command
    printf -v remote_command '%q ' "$@"
    "${HYPERVISOR_SSH[@]}" "${remote_command}"
}
