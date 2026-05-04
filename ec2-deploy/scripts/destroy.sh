#!/bin/bash

# shellcheck disable=SC1091
source ./.env

instance_ip="$(cat "${SHARED_DIR}/public_address")"
host="$(cat "${SHARED_DIR}/ssh_user")"

ssh_host_ip="$host@$instance_ip"

ssh "$ssh_host_ip" "sudo subscription-manager unregister"

aws --region "$REGION" cloudformation delete-stack --stack-name "${STACK_NAME}"

echo "waiting for stack ${STACK_NAME} to be deleted"
aws --region "$REGION" cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}" &
wait "$!"

rm -rf "./${SHARED_DIR:?}/"*

echo "deleted stack ${STACK_NAME}" > "${SHARED_DIR}/.done"