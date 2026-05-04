#!/bin/sh
if [ "$OPENSHIFT_CI" != "" ]; then
  # Ignore build directory and submodule directories
  # TODO: Resolve sno-deploy errors in a different PR then remove the ignore
  TOP_DIR="${1:-.}"
  find "${TOP_DIR}" \
    -path "${TOP_DIR}/vendor" -prune \
    -o -path "${TOP_DIR}/two-node-toolbox" -prune \
    -o -path "${TOP_DIR}/sno-deploy" -prune \
    -o -path "${TOP_DIR}/watchman/.aws-sam" -prune \
    -o -type f -name '*.sh' -exec shellcheck --format=gcc {} \+
else
  podman run --rm \
    --env OPENSHIFT_CI=TRUE \
    --volume "${PWD}:/workdir:ro,z" \
    --entrypoint sh \
    --workdir /workdir \
    quay.io/coreos/shellcheck-alpine:v0.5.0 \
    /workdir/scripts/lint-shellcheck.sh "${@}"
fi;