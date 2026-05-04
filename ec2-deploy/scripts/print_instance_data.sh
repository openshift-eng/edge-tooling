#!/bin/bash
# shellcheck disable=SC1091
source ./.env

echo "Stack: $(cat "${SHARED_DIR}/rhel_host_stack_name")"
echo "Host: $(cat "${SHARED_DIR}/public_address")"
echo "User: $(cat "${SHARED_DIR}/ssh_user")"
echo "Cockpit URL: http://$(cat "${SHARED_DIR}/public_address"):9090"
