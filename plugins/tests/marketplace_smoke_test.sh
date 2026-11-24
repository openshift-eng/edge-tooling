#!/usr/bin/env bash
#
# Marketplace CLI Smoke Tests
# Validates core marketplace functionality against Claude Code plugin format
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SCRIPT_DIR}/../.."
MARKETPLACE="${REPO_ROOT}/marketplace"

PASS=0
FAIL=0

pass() {
    echo "  PASS: $1"
    ((PASS++)) || true
}

fail() {
    echo "  FAIL: $1"
    ((FAIL++)) || true
}

run_test() {
    local name="$1"
    local cmd="$2"
    local expected="$3"

    if eval "$cmd" 2>&1 | grep "$expected" >/dev/null; then
        pass "$name"
    else
        fail "$name"
    fi
}

echo "=== Marketplace Smoke Tests ==="
echo ""

# --- Help ---
echo "Testing: help"
run_test "help shows usage" "$MARKETPLACE help" "Plugin Marketplace CLI"
run_test "help shows catalog-update" "$MARKETPLACE help" "catalog-update"

# Verify install is not listed as a command
if "$MARKETPLACE" help 2>&1 | grep -E '^\s+.*install.*\s+Install a plugin' >/dev/null 2>&1; then
    fail "help should not list install as a command"
else
    pass "help does not list install as a command"
fi

# --- Catalog Update ---
echo ""
echo "Testing: catalog-update"
run_test "catalog-update succeeds" "$MARKETPLACE catalog-update" "Catalog updated"
run_test "marketplace.json exists" "test -f ${REPO_ROOT}/.claude-plugin/marketplace.json && echo exists" "exists"
run_test "marketplace.json is valid JSON" "jq empty ${REPO_ROOT}/.claude-plugin/marketplace.json && echo valid" "valid"
run_test "marketplace.json has plugins array" "jq -e '.plugins | length > 0' ${REPO_ROOT}/.claude-plugin/marketplace.json" "true"
run_test "marketplace.json has owner" "jq -r '.owner.name' ${REPO_ROOT}/.claude-plugin/marketplace.json" "ggiguash"
run_test "marketplace.json plugins use source field" "jq -e '.plugins[0].source' ${REPO_ROOT}/.claude-plugin/marketplace.json" "./plugins/"

# --- List ---
echo ""
echo "Testing: list"
run_test "list shows plugins" "$MARKETPLACE list" "Available Plugins"
run_test "list shows hello-world" "$MARKETPLACE list" "hello-world"

# --- Show ---
echo ""
echo "Testing: show"
run_test "show displays details" "$MARKETPLACE show hello-world" "Plugin Details"
run_test "show displays version" "$MARKETPLACE show hello-world" "Version"
run_test "show errors on unknown" "$MARKETPLACE show nonexistent 2>&1 || true" "not found"

# --- Validate ---
echo ""
echo "Testing: validate"
run_test "validate passes for hello-world" "$MARKETPLACE validate hello-world" "Validation passed"
run_test "validate errors on unknown" "$MARKETPLACE validate nonexistent 2>&1 || true" "not found"

# --- Dropped commands ---
echo ""
echo "Testing: dropped commands"
run_test "install is removed" "$MARKETPLACE install foo 2>&1 || true" "Unknown command"
run_test "uninstall is removed" "$MARKETPLACE uninstall foo 2>&1 || true" "Unknown command"
run_test "update is removed" "$MARKETPLACE update 2>&1 || true" "Unknown command"
run_test "status is removed" "$MARKETPLACE status 2>&1 || true" "Unknown command"

# --- Plugin structure validation ---
echo ""
echo "Testing: plugin structure"
run_test "hello-world has plugin.json" "test -f ${REPO_ROOT}/plugins/hello-world/.claude-plugin/plugin.json && echo exists" "exists"
run_test "plugin.json is valid JSON" "jq empty ${REPO_ROOT}/plugins/hello-world/.claude-plugin/plugin.json && echo valid" "valid"
run_test "plugin.json has name" "jq -e '.name' ${REPO_ROOT}/plugins/hello-world/.claude-plugin/plugin.json" "hello-world"

# --- Summary ---
echo ""
echo "=== Results ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo ""

if [ $FAIL -gt 0 ]; then
    echo "SMOKE TESTS FAILED"
    exit 1
else
    echo "ALL SMOKE TESTS PASSED"
    exit 0
fi
