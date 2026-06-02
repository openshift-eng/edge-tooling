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

CMD="${1:?Usage: precheck.sh <xyz|nightly|ecrc|enrich> [args...]}"
shift

case "${CMD}" in
    xyz)
        "${ENVDIR}/bin/python3" "${SCRIPTDIR}/precheck_xyz.py" "$@"
        ;;
    nightly)
        "${ENVDIR}/bin/python3" "${SCRIPTDIR}/precheck_nightly.py" "$@"
        ;;
    ecrc)
        "${ENVDIR}/bin/python3" "${SCRIPTDIR}/precheck_ecrc.py" "$@"
        ;;
    enrich)
        "${ENVDIR}/bin/python3" "${SCRIPTDIR}/enrich_ocpbugs.py" "$@"
        ;;
    *)
        echo "Unknown command: ${CMD}" >&2
        echo "Usage: precheck.sh <xyz|nightly|ecrc|enrich> [args...]" >&2
        exit 1
        ;;
esac
