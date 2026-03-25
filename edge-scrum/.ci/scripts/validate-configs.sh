#!/usr/bin/env bash
#
# validate-configs.sh
#
# Validates .jira-config JSON files against their schemas and checks for
# duplicate IDs/names within each file.
#
# Usage:
#   ./validate-configs.sh
#
# Requirements:
#   - jq
#   - check-jsonschema (pip install check-jsonschema)

set -euo pipefail

readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONFIG_DIR="${REPO_ROOT}/.jira-config"
SCHEMA_DIR="${SCRIPT_DIR}/../schemas"

ERRORS=0

log_info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

check_dependencies() {
    local missing=()
    for cmd in jq check-jsonschema; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required commands: ${missing[*]}"
        exit 1
    fi
}

# Validate a config file against its schema, then check for duplicate values.
# Args: config_file schema_file array_key id_field
validate_file() {
    local config_file="$1"
    local schema_file="$2"
    local array_key="$3"
    local id_field="$4"
    local name
    name=$(basename "$config_file")

    log_info "Validating ${name}..."

    if ! check-jsonschema --schemafile "$schema_file" "$config_file"; then
        ERRORS=$((ERRORS + 1))
        return
    fi

    local dupes
    dupes=$(jq -r --arg key "$array_key" --arg field "$id_field" \
        '[.[$key][][$field]] | group_by(.) | map(select(length > 1)) | map(.[0])[]' \
        "$config_file")

    if [[ -n "$dupes" ]]; then
        log_error "[${name}] Duplicate ${id_field}s: $(echo "$dupes" | tr '\n' ' ')"
        ERRORS=$((ERRORS + 1))
        return
    fi

    log_success "${name} is valid"
}

main() {
    check_dependencies
    log_info "Validating .jira-config files..."
    echo ""

    validate_file "${CONFIG_DIR}/boards.json"     "${SCHEMA_DIR}/boards.schema.json"     "boards"     "id"
    validate_file "${CONFIG_DIR}/filters.json"    "${SCHEMA_DIR}/filters.schema.json"    "filters"    "id"
    validate_file "${CONFIG_DIR}/projects.json"   "${SCHEMA_DIR}/projects.schema.json"   "projects"   "id"
    validate_file "${CONFIG_DIR}/components.json" "${SCHEMA_DIR}/components.schema.json" "components" "name"
    validate_file "${CONFIG_DIR}/labels.json"     "${SCHEMA_DIR}/labels.schema.json"     "labels"     "name"

    echo ""
    if [[ $ERRORS -eq 0 ]]; then
        log_success "All config files valid"
        exit 0
    else
        log_error "Validation failed: ${ERRORS} error(s) found"
        exit 1
    fi
}

main
