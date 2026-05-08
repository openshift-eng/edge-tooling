#!/usr/bin/bash

set -euo pipefail

SCRIPTDIR="$(dirname "${BASH_SOURCE[0]}")"
REPOROOT="$(git rev-parse --show-toplevel)"
OUTPUT_DIR="${REPOROOT}/_output"
ENVDIR="${OUTPUT_DIR}/release_testing"

if [[ ! -d "${ENVDIR}" ]]; then
    echo "Setting up required tools..." >&2
    mkdir -p "${OUTPUT_DIR}"
    python3 -m venv "${ENVDIR}"
fi

"${ENVDIR}/bin/python3" -m pip install -r "${SCRIPTDIR}/requirements.txt" >&2

"${ENVDIR}/bin/python3" "${SCRIPTDIR}/validate_artifacts.py" "$@"
