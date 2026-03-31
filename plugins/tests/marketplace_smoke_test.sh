#!/usr/bin/env bash
#
# Smoke tests for the plugin marketplace CLI.
# No cluster required — pure CLI behavior.
#
# Usage: bash plugins/tests/marketplace_smoke_test.sh
#

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MARKETPLACE="${REPO_ROOT}/marketplace"
PLUGINS_DIR="${REPO_ROOT}/plugins"

PASS=0
FAIL=0

run_test() {
    local desc="$1"
    shift
    if "$@"; then
        echo "PASS: $desc"
        (( PASS++ )) || true
    else
        echo "FAIL: $desc"
        (( FAIL++ )) || true
    fi
}

# ---------------------------------------------------------------------------
# Test 1: help exits 0 and mentions "Usage"
# ---------------------------------------------------------------------------
test_help() {
    local output
    output=$("$MARKETPLACE" help 2>&1) || return 1
    echo "$output" | grep -qi "usage"
}

# ---------------------------------------------------------------------------
# Test 2: validate exits non-zero for a plugin not in the catalog
#
# Strategy: create a temp plugin directory symlinked into PLUGINS_DIR with a
# plugin.yml whose name field is empty. catalog-update requires a non-empty
# name and skips the entry; validate then reports "Plugin not found" because
# the plugin was never added to the catalog.
#
# Cleanup is handled inside a subshell so the EXIT trap is scoped locally
# and does not interfere with other tests.
# ---------------------------------------------------------------------------
test_validate_plugin_not_in_catalog() {
    (
        local tmpname="smoke-test-bad-$BASHPID"
        local tmpdir
        tmpdir="$(mktemp -d)"
        local link="${PLUGINS_DIR}/${tmpname}"
        trap 'rm -rf "$tmpdir"; rm -f "$link"' EXIT

        mkdir -p "${tmpdir}/${tmpname}"
        cat > "${tmpdir}/${tmpname}/plugin.yml" <<'EOF'
name: ""
EOF
        ln -s "${tmpdir}/${tmpname}" "$link"

        # catalog-update will skip this plugin (missing required fields).
        # validate will therefore not find it in the catalog and exit non-zero.
        "$MARKETPLACE" validate "$tmpname" >/dev/null 2>&1
        local rc=$?
        [[ $rc -ne 0 ]]
    )
}

# ---------------------------------------------------------------------------
# Test 3: validate rejects a plugin with an invalid field value
#
# Strategy: create a plugin with all fields required for catalog entry but
# with an invalid version string (not semver). The plugin IS added to the
# catalog (all required fields present) but validate catches the bad version.
# ---------------------------------------------------------------------------
test_validate_invalid_field() {
    (
        local tmpname="smoke-test-invalid-$BASHPID"
        local tmpdir
        tmpdir="$(mktemp -d)"
        local link="${PLUGINS_DIR}/${tmpname}"
        trap 'rm -rf "$tmpdir"; rm -f "$link"' EXIT

        mkdir -p "${tmpdir}/${tmpname}"
        cat > "${tmpdir}/${tmpname}/plugin.yml" <<EOF
name: ${tmpname}
version: not-semver
type: skill
category: util
description: Smoke test invalid field
author: smoke-test
EOF
        touch "${tmpdir}/${tmpname}/README.md"
        ln -s "${tmpdir}/${tmpname}" "$link"

        # validate should fail due to invalid version format
        "$MARKETPLACE" validate "$tmpname" >/dev/null 2>&1
        local rc=$?
        [[ $rc -ne 0 ]]
    )
}

# ---------------------------------------------------------------------------
# Test 4: list exits 0 and includes a known plugin by name
#
# Creates a minimal valid plugin in a temp directory, links it into
# PLUGINS_DIR, runs list, and checks that the plugin name appears in the
# output.  Cleanup is scoped to a subshell.
# ---------------------------------------------------------------------------
test_list_exits_zero() {
    (
        local plugin_name="smoke-test-list-$BASHPID"
        local tmpdir
        tmpdir="$(mktemp -d)"
        local link="${PLUGINS_DIR}/${plugin_name}"
        trap 'rm -rf "$tmpdir"; rm -f "$link"' EXIT

        mkdir -p "${tmpdir}/${plugin_name}"
        cat > "${tmpdir}/${plugin_name}/plugin.yml" <<EOF
name: ${plugin_name}
version: 1.0.0
type: skill
category: util
description: Smoke test list plugin
author: smoke-test
EOF
        touch "${tmpdir}/${plugin_name}/README.md"
        ln -s "${tmpdir}/${plugin_name}" "$link"

        local output
        output=$("$MARKETPLACE" list 2>&1) || exit 1
        echo "$output" | grep -q "${plugin_name}"
    )
}

# ---------------------------------------------------------------------------
# Test 5: create_plugin scaffolds expected files
#
# The 'new' command is fully interactive — it reads type, category,
# description, and author from stdin.  We drive it with a here-string.
# Cleanup is handled inside a subshell so the EXIT trap is scoped locally.
# ---------------------------------------------------------------------------
test_create_plugin() {
    (
        local plugin_name="smoke-test-create-$BASHPID"
        local plugin_dir="${PLUGINS_DIR}/${plugin_name}"
        trap 'rm -rf "$plugin_dir"' EXIT

        printf 'skill\nutil\nSmoke test plugin\nsmoke-test\n' \
            | "$MARKETPLACE" new "$plugin_name" >/dev/null 2>&1 || exit 1

        [[ -f "${plugin_dir}/plugin.yml" ]] || exit 1
        [[ -f "${plugin_dir}/skill.md" ]]   || exit 1
    )
}

# ---------------------------------------------------------------------------
# Test 6: create scaffolds expected files for subagent type
#
# Verifies that 'new' with type=subagent produces plugin.yml and agent.md,
# and that plugin.yml contains 'type: subagent'.
# ---------------------------------------------------------------------------
test_create_subagent_plugin() {
    (
        local plugin_name="smoke-test-subagent-$BASHPID"
        local plugin_dir="${PLUGINS_DIR}/${plugin_name}"
        trap 'rm -rf "$plugin_dir"' EXIT

        printf 'subagent\nutil\nSmoke test subagent plugin\nsmoke-test\n' \
            | "$MARKETPLACE" new "$plugin_name" >/dev/null 2>&1 || exit 1

        [[ -f "${plugin_dir}/plugin.yml" ]] || exit 1
        [[ -f "${plugin_dir}/agent.md" ]]   || exit 1
        grep -q "type: subagent" "${plugin_dir}/plugin.yml"
    )
}

# ---------------------------------------------------------------------------
# Test 7: create scaffolds README.md with all {{...}} tokens substituted
#
# Creates a skill plugin and verifies:
#   - README.md exists and is non-empty (template was processed)
#   - No '{{...}}' placeholder tokens remain (python3 substitution ran cleanly)
# Note: <!-- TODO: --> markers are intentional and expected to remain in the
# output for the developer to fill in — they are NOT checked here.
# ---------------------------------------------------------------------------
test_create_plugin_readme() {
    (
        local plugin_name="smoke-test-readme-$BASHPID"
        local plugin_dir="${PLUGINS_DIR}/${plugin_name}"
        trap 'rm -rf "$plugin_dir"' EXIT

        printf 'skill\nutil\nSmoke test readme plugin\nsmoke-test\n' \
            | "$MARKETPLACE" new "$plugin_name" >/dev/null 2>&1 || exit 1

        [[ -f "${plugin_dir}/README.md" ]] || exit 1
        [[ -s "${plugin_dir}/README.md" ]] || exit 1
        ! grep -q '{{' "${plugin_dir}/README.md"
    )
}

# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------
run_test "help exits 0 and output contains 'Usage'" test_help
run_test "validate exits non-zero for plugin not in catalog" test_validate_plugin_not_in_catalog
run_test "validate exits non-zero for plugin with invalid field value (bad version)" test_validate_invalid_field
run_test "list exits 0 and output contains a known plugin by name" test_list_exits_zero
run_test "create scaffolds plugin.yml and type-specific file" test_create_plugin
run_test "create subagent scaffolds agent.md and sets type: subagent in plugin.yml" test_create_subagent_plugin
run_test "create scaffolds README.md with no unfilled placeholders" test_create_plugin_readme

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[[ $FAIL -eq 0 ]]
