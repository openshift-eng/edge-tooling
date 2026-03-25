#!/bin/bash
# validate-readme-sync.sh
# Validates that changes to metadata JSON files are reflected in the README

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_SCRUM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
JIRA_CONFIG_DIR="${EDGE_SCRUM_DIR}/.jira-config"
README_FILE="${JIRA_CONFIG_DIR}/README.md"

# Metadata files to check
METADATA_FILES=(
    "boards.json"
    "filters.json"
    "projects.json"
    "components.json"
    "plans.json"
    "labels.json"
)

# Determine base ref for comparison
# In CI, use the base branch (e.g., main, master)
# Locally, use the merge base with main
if [ -n "${GITHUB_BASE_REF:-}" ]; then
    # GitHub Actions PR context
    BASE_REF="origin/${GITHUB_BASE_REF}"
elif [ -n "${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-}" ]; then
    # GitLab CI PR context
    BASE_REF="origin/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}"
else
    # Local development - compare against main
    BASE_REF="$(git merge-base HEAD origin/main 2>/dev/null || echo 'HEAD~1')"
fi

echo "Comparing against base: ${BASE_REF}"

# Check if any metadata files have been modified
metadata_modified=false
modified_files=()

for file in "${METADATA_FILES[@]}"; do
    file_path="${JIRA_CONFIG_DIR}/${file}"

    # Check if file exists and has been modified
    if git diff --name-only "${BASE_REF}..HEAD" | grep -q "^edge-scrum/.jira-config/${file}$"; then
        metadata_modified=true
        modified_files+=("${file}")
        echo "✓ Detected change in ${file}"
    fi
done

# If no metadata files were modified, exit successfully
if [ "${metadata_modified}" = false ]; then
    echo "✓ No metadata file changes detected. Validation passed."
    exit 0
fi

# Check if README was also modified
if git diff --name-only "${BASE_REF}..HEAD" | grep -q "^edge-scrum/.jira-config/README.md$"; then
    echo "✓ README.md was updated alongside metadata changes. Validation passed."
    echo ""
    echo "Modified metadata files:"
    for file in "${modified_files[@]}"; do
        echo "  - ${file}"
    done
    exit 0
else
    echo "✗ ERROR: Metadata files were modified but README.md was not updated."
    echo ""
    echo "Modified metadata files:"
    for file in "${modified_files[@]}"; do
        echo "  - ${file}"
    done
    echo ""
    echo "Please update edge-scrum/.jira-config/README.md to reflect these changes."
    echo "The README should include human-readable lists of all items from the metadata files."
    exit 1
fi
