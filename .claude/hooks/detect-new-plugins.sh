#!/usr/bin/env bash
#
# Claude Code Hook: Detect New Plugins
# Automatically detects newly added plugins in the marketplace
# and notifies Claude to update the catalog
#

set -euo pipefail

# Get repository root
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PLUGINS_DIR="${REPO_ROOT}/plugins"
CATALOG_FILE="${PLUGINS_DIR}/.registry/catalog.yml"

# Exit if plugins directory doesn't exist
[[ ! -d "$PLUGINS_DIR" ]] && exit 0

# Exit if catalog doesn't exist (marketplace not initialized)
[[ ! -f "$CATALOG_FILE" ]] && exit 0

# Function to check if plugin is in catalog (exact match)
plugin_in_catalog() {
    local plugin_name="$1"
    yq eval ".plugins[] | select(.name == \"$plugin_name\") | .name" "$CATALOG_FILE" 2>/dev/null | grep -q .
}

# Find all plugin directories (contain plugin.yml)
new_plugins=()
while IFS= read -r -d '' plugin_file; do
    plugin_dir=$(dirname "$plugin_file")
    plugin_name=$(basename "$plugin_dir")

    # Skip special directories
    [[ "$plugin_name" == ".templates" || "$plugin_name" == ".registry" || "$plugin_name" == "docs" ]] && continue

    # Check if plugin is in catalog
    if ! plugin_in_catalog "$plugin_name"; then
        new_plugins+=("$plugin_name")
    fi
done < <(find "$PLUGINS_DIR" -maxdepth 2 -name "plugin.yml" -print0 2>/dev/null)

# If new plugins found, notify Claude
if [ ${#new_plugins[@]} -gt 0 ]; then
    echo "NEW PLUGINS DETECTED: The following plugin(s) exist but are not in the marketplace catalog: ${new_plugins[*]}."
    echo "Please notify the user and offer to update the marketplace catalog by running: ./marketplace catalog-update"
fi

exit 0
