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

MARKER="${ENVDIR}/.deps-installed"
if [[ ! -f "${MARKER}" ]] || [[ "${SCRIPTDIR}/requirements.txt" -nt "${MARKER}" ]]; then
    "${ENVDIR}/bin/python3" -m pip install -q -r "${SCRIPTDIR}/requirements.txt" >&2
    touch "${MARKER}"
fi

"${ENVDIR}/bin/python3" "${SCRIPTDIR}/validate_artifacts.py" "$@"
