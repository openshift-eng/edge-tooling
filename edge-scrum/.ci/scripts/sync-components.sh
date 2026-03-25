#!/usr/bin/env bash
#
# sync-components.sh
#
# Synchronizes Jira components from .jira-config/components.json to Jira API.
# Only updates existing components (create/delete are manual operations).
#
# Usage:
#   ./sync-components.sh [--dry-run]
#
# Environment variables required (from edge-scrum/.env):
#   JIRA_URL - Jira instance URL
#   JIRA_USERNAME - Jira username
#   JIRA_API_TOKEN - Jira API token

set -euo pipefail

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Configuration paths
CONFIG_FILE="${REPO_ROOT}/.jira-config/components.json"

# Dry run flag
DRY_RUN=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--dry-run]"
            exit 1
            ;;
    esac
done

# Validate required environment variables
JIRA_URL="${JIRA_URL:-https://redhat.atlassian.net}"
JIRA_URL="${JIRA_URL%/}"
: "${JIRA_USERNAME:?JIRA_USERNAME must be set}"
: "${JIRA_API_TOKEN:?JIRA_API_TOKEN must be set}"
JIRA_AUTH=$(printf '%s:%s' "${JIRA_USERNAME}" "${JIRA_API_TOKEN}" | base64 -w0)

# Validate config file exists
if [[ ! -f "${CONFIG_FILE}" ]]; then
    echo "Error: Configuration file not found at ${CONFIG_FILE}"
    exit 1
fi

# Validate jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: jq is required but not installed"
    exit 1
fi

# Validate curl is installed
if ! command -v curl &> /dev/null; then
    echo "Error: curl is required but not installed"
    exit 1
fi

# Secure temporary directory (cleaned up on exit)
TMPDIR=$(mktemp -d)
trap 'rm -rf "${TMPDIR}"' EXIT

# Track if any updates failed
UPDATE_FAILED=false

# Read components from config file
COMPONENTS=$(jq -c '.components[]' "${CONFIG_FILE}")

echo "====================================="
echo "Jira Component Sync"
echo "====================================="
if [[ "${DRY_RUN}" == "true" ]]; then
    echo "MODE: DRY RUN (no changes will be made)"
else
    echo "MODE: LIVE UPDATE"
fi
echo "Config: ${CONFIG_FILE}"
echo "Jira: ${JIRA_URL}"
echo "====================================="
echo

# Process each component
while IFS= read -r component; do
    COMPONENT_NAME=$(echo "${component}" | jq -r '.name')
    COMPONENT_DESC=$(echo "${component}" | jq -r '.description')

    echo "Processing component: ${COMPONENT_NAME}"
    echo "  Description: ${COMPONENT_DESC}"

    # Process each project association
    PROJECTS=$(echo "${component}" | jq -c '.projects[]')

    while IFS= read -r project; do
        COMPONENT_ID=$(echo "${project}" | jq -r '.component_id // empty')
        PROJECT_KEY=$(echo "${project}" | jq -r '.project_key')

        # Skip if no component_id (not yet created)
        if [[ -z "${COMPONENT_ID}" ]]; then
            echo "  ⚠ Skipping ${PROJECT_KEY}: no component_id (component not yet created)"
            continue
        fi

        echo "  → Updating component ${COMPONENT_ID} in project ${PROJECT_KEY}"

        # Build the update payload
        UPDATE_PAYLOAD=$(jq -n \
            --arg name "${COMPONENT_NAME}" \
            --arg description "${COMPONENT_DESC}" \
            '{
                name: $name,
                description: $description
            }')

        if [[ "${DRY_RUN}" == "true" ]]; then
            echo "    [DRY RUN] Would update with payload:"
            echo "${UPDATE_PAYLOAD}" | jq '.'
        else
            # Make the API call
            HTTP_CODE=$(curl -s -w "%{http_code}" -o "${TMPDIR}/response.json" \
                -X PUT \
                -H "Content-Type: application/json" \
                -H "Authorization: Basic ${JIRA_AUTH}" \
                -d "${UPDATE_PAYLOAD}" \
                "${JIRA_URL}/rest/api/2/component/${COMPONENT_ID}")

            if [[ "${HTTP_CODE}" -eq 200 ]]; then
                echo "    ✓ Successfully updated component ${COMPONENT_ID}"
            else
                echo "    ✗ Failed to update component ${COMPONENT_ID} (HTTP ${HTTP_CODE})"
                echo "    Response:"
                jq '.' "${TMPDIR}/response.json" 2>/dev/null || cat "${TMPDIR}/response.json"
                UPDATE_FAILED=true
            fi
        fi

    done <<< "${PROJECTS}"

    echo

done <<< "${COMPONENTS}"

echo "====================================="
if [[ "${DRY_RUN}" == "true" ]]; then
    echo "Dry run complete. No changes were made."
    exit 0
elif [[ "${UPDATE_FAILED}" == "true" ]]; then
    echo "Component sync completed with errors."
    exit 1
else
    echo "All components synchronized successfully."
    exit 0
fi
