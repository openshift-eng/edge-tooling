#!/usr/bin/env bash

set -euo pipefail

# sync-boards.sh - Sync Jira board properties from boards.json configuration
#
# Usage:
#   ./sync-boards.sh [--dry-run]
#
# This script reads board configuration from edge-scrum/.jira-config/boards.json
# and updates board properties via the Jira REST API. It uses credentials from
# edge-scrum/.env (JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN).
#
# Board creation/deletion is manual - this script only updates properties.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"
CONFIG_DIR="${REPO_ROOT}/.jira-config"
BOARDS_JSON="${CONFIG_DIR}/boards.json"

DRY_RUN=false
EXIT_CODE=0

# Parse command-line arguments
for arg in "$@"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            echo "Usage: $0 [--dry-run]" >&2
            exit 1
            ;;
    esac
done

# Verify dependencies
if ! command -v jq &>/dev/null; then
    echo "Error: jq is required but not installed" >&2
    exit 1
fi

if ! command -v curl &>/dev/null; then
    echo "Error: curl is required but not installed" >&2
    exit 1
fi

# Verify files exist
if [[ ! -f "${BOARDS_JSON}" ]]; then
    echo "Error: boards.json not found at ${BOARDS_JSON}" >&2
    exit 1
fi

# Validate required environment variables
JIRA_URL="${JIRA_URL:-https://redhat.atlassian.net}"
JIRA_URL="${JIRA_URL%/}"
: "${JIRA_USERNAME:?JIRA_USERNAME must be set}"
: "${JIRA_API_TOKEN:?JIRA_API_TOKEN must be set}"
JIRA_AUTH=$(printf '%s:%s' "${JIRA_USERNAME}" "${JIRA_API_TOKEN}" | base64 -w0)

# Validate boards.json
if ! jq -e '.boards' "${BOARDS_JSON}" &>/dev/null; then
    echo "Error: Invalid boards.json format - missing 'boards' array" >&2
    exit 1
fi

# Secure temporary directory (cleaned up on exit)
TMPDIR=$(mktemp -d)
trap 'rm -rf "${TMPDIR}"' EXIT

echo "Syncing Jira board properties from ${BOARDS_JSON}"
if [[ "${DRY_RUN}" == "true" ]]; then
    echo "[DRY RUN MODE] No changes will be made"
fi
echo

# Get board count
BOARD_COUNT=$(jq '.boards | length' "${BOARDS_JSON}")
echo "Found ${BOARD_COUNT} board(s) to process"
echo

# Process each board
for i in $(seq 0 $((BOARD_COUNT - 1))); do
    BOARD_ID=$(jq -r ".boards[${i}].id" "${BOARDS_JSON}")
    BOARD_NAME=$(jq -r ".boards[${i}].name" "${BOARDS_JSON}")

    echo "Processing board: ${BOARD_NAME} (ID: ${BOARD_ID})"

    # Get properties object
    PROPERTIES=$(jq -r ".boards[${i}].properties" "${BOARDS_JSON}")

    # Check if properties object is empty
    if [[ "${PROPERTIES}" == "{}" ]] || [[ "${PROPERTIES}" == "null" ]]; then
        echo "  No properties to sync"
        echo
        continue
    fi

    # Get property keys
    PROPERTY_KEYS=$(echo "${PROPERTIES}" | jq -r 'keys[]')

    if [[ -z "${PROPERTY_KEYS}" ]]; then
        echo "  No properties to sync"
        echo
        continue
    fi

    # Process each property
    while IFS= read -r PROPERTY_KEY; do
        PROPERTY_VALUE=$(echo "${PROPERTIES}" | jq -r ".\"${PROPERTY_KEY}\"")

        echo "  Updating property: ${PROPERTY_KEY} = ${PROPERTY_VALUE}"

        if [[ "${DRY_RUN}" == "true" ]]; then
            echo "    [DRY RUN] Would PUT to: ${JIRA_URL}/rest/agile/1.0/board/${BOARD_ID}/properties/${PROPERTY_KEY}"
            echo "    [DRY RUN] Payload: ${PROPERTY_VALUE}"
        else
            # Make the API request
            HTTP_CODE=$(curl -s -w "%{http_code}" -o "${TMPDIR}/response.json" \
                -X PUT \
                -H "Content-Type: application/json" \
                -H "Authorization: Basic ${JIRA_AUTH}" \
                -d "${PROPERTY_VALUE}" \
                "${JIRA_URL}/rest/agile/1.0/board/${BOARD_ID}/properties/${PROPERTY_KEY}")

            if [[ "${HTTP_CODE}" -ge 200 ]] && [[ "${HTTP_CODE}" -lt 300 ]]; then
                echo "    ✓ Success (HTTP ${HTTP_CODE})"
            else
                echo "    ✗ Failed (HTTP ${HTTP_CODE})" >&2
                if [[ -f "${TMPDIR}/response.json" ]]; then
                    echo "    Response: $(cat "${TMPDIR}/response.json")" >&2
                fi
                EXIT_CODE=1
            fi
        fi
    done <<< "${PROPERTY_KEYS}"

    echo
done

if [[ "${EXIT_CODE}" -eq 0 ]]; then
    if [[ "${DRY_RUN}" == "true" ]]; then
        echo "Dry run completed successfully"
    else
        echo "All board properties synced successfully"
    fi
else
    echo "Some properties failed to sync - see errors above" >&2
fi

exit "${EXIT_CODE}"
