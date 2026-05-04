#!/bin/bash

sudo hostnamectl set-hostname "aws-${STACK_NAME}"

user=${1-pitadmin}
if id "$user" >/dev/null 2>&1; then
    echo "user $user found"
else
    echo "user $user not found, creating"
    sudo useradd -m "$user"
    sudo passwd "$user"
    printf '%s\tALL=(ALL)\tNOPASSWD: ALL\n' "$user" | sudo tee "/etc/sudoers.d/${user}"
fi

sudo rm -rf /etc/yum.repos.d/*
sudo subscription-manager config --rhsm.manage_repos=1 --rhsmcertd.disable=redhat-access-insights
sudo subscription-manager register
sudo subscription-manager attach --pool=8a85f99c7d76f2fd017d96c411c70667
sudo subscription-manager repos \
--enable "rhel-9-for-$(uname -m)-appstream-rpms" \
--enable "rhel-9-for-$(uname -m)-baseos-rpms" \
--enable "rhocp-4.14-for-rhel-9-$(uname -m)-rpms"