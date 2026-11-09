#!/bin/bash
#
# Test suite for scripts/setup.sh (workspace plugin)
# ==================================================
# Run from the repo root:  bash tests/test_setup.sh
#
# The plugin is read-only and self-derives its root from setup.sh's location,
# so tests build a throwaway "plugin" dir (scripts/ + domains/ + templates/)
# that behaves like an install, and separate throwaway "workspace" dirs. All
# temp dirs are cleaned up.
#
# Requirements: bash 3.2+, git 2.27+, python3

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOTAL=0 PASSED=0 FAILED=0
_GROUP=""

# ─── Mini test framework ──────────────────────────────────────────────────────

group()   { _GROUP="$1"; echo; echo "── $_GROUP"; }
ok()      { echo "  ✓ $1"; TOTAL=$((TOTAL+1)); PASSED=$((PASSED+1)); }
fail()    { echo "  ✗ $1${2:+  ($2)}"; TOTAL=$((TOTAL+1)); FAILED=$((FAILED+1)); }

assert_success() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then ok "$desc"; else fail "$desc" "expected exit 0"; fi
}

assert_failure() {
    local desc="$1"; shift
    if ! "$@" >/dev/null 2>&1; then ok "$desc"; else fail "$desc" "expected non-zero exit"; fi
}

assert_file() {
    local desc="$1" path="$2"
    if [[ -f "$path" ]]; then ok "$desc"; else fail "$desc" "missing: $path"; fi
}

assert_dir() {
    local desc="$1" path="$2"
    if [[ -d "$path" ]]; then ok "$desc"; else fail "$desc" "missing dir: $path"; fi
}

assert_contains() {
    local desc="$1" path="$2" pattern="$3"
    if grep -q "$pattern" "$path" 2>/dev/null; then
        ok "$desc"
    else
        fail "$desc" "pattern '$pattern' not found in $path"
    fi
}

assert_not_contains() {
    local desc="$1" path="$2" pattern="$3"
    if ! grep -q "$pattern" "$path" 2>/dev/null; then
        ok "$desc"
    else
        fail "$desc" "unexpected pattern '$pattern' in $path"
    fi
}

assert_output_contains() {
    local desc="$1" pattern="$2" output="$3"
    if echo "$output" | grep -q "$pattern"; then
        ok "$desc"
    else
        fail "$desc" "pattern '$pattern' not in output"
    fi
}

# ─── Plugin + workspace + fixture helpers ─────────────────────────────────────

# Build a throwaway plugin dir that behaves like an install: scripts/setup.sh
# (which self-derives its plugin root) plus bundled domains/ and templates/.
new_plugin() {
    local plugin
    plugin=$(mktemp -d)
    mkdir -p "$plugin/scripts"
    cp "$REPO_ROOT"/scripts/*.sh "$REPO_ROOT"/scripts/*.py "$plugin/scripts/"
    cp -r "$REPO_ROOT/domains" "$plugin/"
    cp -r "$REPO_ROOT/templates" "$plugin/"
    echo "$plugin"
}

# A bare workspace dir (no git init — workspaces need not be git repos).
new_workspace() { mktemp -d; }

cleanup() { rm -rf "$@"; }

# Run setup.sh against a plugin + workspace, passing --workspace explicitly.
run_setup() {
    local plugin="$1" ws="$2"; shift 2
    bash "$plugin/scripts/setup.sh" --workspace "$ws" "$@"
}

# Same, feeding a single answer to a prompt.
run_setup_yn() {
    local plugin="$1" ws="$2" answer="$3"; shift 3
    echo "$answer" | bash "$plugin/scripts/setup.sh" --workspace "$ws" "$@"
}

# Write a minimal valid domain into a directory.
make_domain_dir() {
    local dir="$1" name="${2:-test-domain}"
    mkdir -p "$dir/context"
    printf 'name: %s\ndescription: "Test domain for automated tests"\n' "$name" \
        > "$dir/domain.yaml"
    printf 'repos:\n  - name: testrepo\n    url: https://example.com/r.git\n    branch: main\n    category: testing\n    summary: "test repo"\n' \
        > "$dir/dev-env.yaml"
    printf '# testrepo — test context (domain: %s)\n' "$name" \
        > "$dir/context/testrepo.md"
}

# Turn a directory into a git repo with a single commit (for file:// cloning).
git_commit_all() {
    local dir="$1"
    git init -q -b main "$dir"
    git -C "$dir" config user.email "test@test.com"
    git -C "$dir" config user.name "Test"
    git -C "$dir" config commit.gpgsign false
    git -C "$dir" config gpg.format openpgp
    git -C "$dir" add -A
    git -C "$dir" commit -q -m "init"
}

# Create a real cloneable git repo with a single file.
make_cloneable_repo() {
    local dir="$1" name="${2:-testrepo}"
    mkdir -p "$dir"
    printf '# %s\n' "$name" > "$dir/README.md"
    git_commit_all "$dir"
}

# One shared read-only plugin for the whole run.
PLUGIN=$(new_plugin)

# ─── Tests ────────────────────────────────────────────────────────────────────

# ── 1. Bundled domain init ────────────────────────────────────────────────────
group "Bundled domain init"

ws=$(new_workspace)

out=$(run_setup "$PLUGIN" "$ws" init 2>&1) || true
assert_output_contains "init (no args) exits 0"          "Available domains"  "$out"
assert_output_contains "init (no args) lists example"    "example"            "$out"
assert_output_contains "init (no args) tags bundled"     "bundled"            "$out"
assert_output_contains "init (no args) shows URL hint"   "url"                "$out"

run_setup_yn "$PLUGIN" "$ws" "y" init example >/dev/null 2>&1
assert_file      "init example creates dev-env.yaml"        "$ws/dev-env.yaml"
assert_contains  "domain block: name"                       "$ws/dev-env.yaml" "name: example"
assert_contains  "domain block: source bundled"             "$ws/dev-env.yaml" "source: bundled"
assert_not_contains "no stray scalar domain: line"          "$ws/dev-env.yaml" "^domain: example"
assert_file      "settings.local.json created from template" "$ws/.claude/settings.local.json"
assert_contains  "settings has workspace skill allow"       "$ws/.claude/settings.local.json" "workspace:\*"
assert_not_contains "settings template has no hooks block"   "$ws/.claude/settings.local.json" "SessionStart"
assert_dir       ".claude/skills dir created by init"        "$ws/.claude/skills"

# Re-init with tnf to verify scalar 'domain: tnf' is replaced by the mapping
run_setup_yn "$PLUGIN" "$ws" "y" init tnf >/dev/null 2>&1
assert_contains  "tnf scalar replaced: name"   "$ws/dev-env.yaml" "name: tnf"
assert_contains  "tnf scalar replaced: source" "$ws/dev-env.yaml" "source: bundled"
assert_not_contains "scalar form gone"         "$ws/dev-env.yaml" "^domain: tnf"

cleanup "$ws"

# ── 2. Validation errors ──────────────────────────────────────────────────────
group "Validation errors"

ws=$(new_workspace)

assert_failure "unknown domain name exits non-zero" \
    run_setup "$PLUGIN" "$ws" init does-not-exist

# Workspace domain missing dev-env.yaml
bad_domain="$ws/domains/no-devenv"
mkdir -p "$bad_domain"
printf 'name: no-devenv\n' > "$bad_domain/domain.yaml"
out=$(run_setup "$PLUGIN" "$ws" init no-devenv 2>&1) || true
assert_output_contains "missing dev-env.yaml: error shown" "dev-env.yaml" "$out"
assert_output_contains "missing dev-env.yaml: layout hint shown" "domain.yaml" "$out"

# Workspace domain missing domain.yaml
bad_domain2="$ws/domains/no-domainyaml"
mkdir -p "$bad_domain2"
printf 'repos: []\n' > "$bad_domain2/dev-env.yaml"
out=$(run_setup "$PLUGIN" "$ws" init no-domainyaml 2>&1) || true
assert_output_contains "missing domain.yaml: error shown" "domain.yaml" "$out"

cleanup "$ws"

# ── 3. External domain init (file://) ─────────────────────────────────────────
group "External domain init (file://)"

ws=$(new_workspace)
fixture=$(mktemp -d)
make_domain_dir "$fixture" "ext-domain"
git_commit_all "$fixture"

run_setup_yn "$PLUGIN" "$ws" "y" init "file://$fixture" >/dev/null 2>&1

assert_dir      "external domain installed in workspace domains/" "$ws/domains/ext-domain"
assert_file     "installed domain has domain.yaml"         "$ws/domains/ext-domain/domain.yaml"
assert_file     "installed domain has dev-env.yaml"        "$ws/domains/ext-domain/dev-env.yaml"
assert_file     "dev-env.yaml created at workspace root"   "$ws/dev-env.yaml"
assert_contains "domain block has source URL"              "$ws/dev-env.yaml" "source: file://$fixture"
assert_contains "domain block has name from domain.yaml"   "$ws/dev-env.yaml" "name: ext-domain"
# External domain must NOT leak into the read-only plugin
if [[ ! -d "$PLUGIN/domains/ext-domain" ]]; then
    ok "external domain not written into plugin"
else
    fail "external domain not written into plugin" "leaked into $PLUGIN/domains"
fi

cleanup "$ws" "$fixture"

# ── 4. External domain — #subdir form ─────────────────────────────────────────
group "External domain pack with #subdir"

ws=$(new_workspace)
pack=$(mktemp -d)

make_domain_dir "$pack/team-alpha" "team-alpha"
make_domain_dir "$pack/team-beta"  "team-beta"
git_commit_all "$pack"

run_setup_yn "$PLUGIN" "$ws" "y" init "file://$pack#team-alpha" >/dev/null 2>&1

assert_dir      "correct subdir installed"                 "$ws/domains/team-alpha"
assert_file     "subdir domain.yaml present"               "$ws/domains/team-alpha/domain.yaml"
if [[ ! -d "$ws/domains/team-beta" ]]; then
    ok "other subdir not installed"
else
    fail "other subdir not installed" "team-beta was unexpectedly installed"
fi
assert_contains "subdir recorded in dev-env.yaml"          "$ws/dev-env.yaml" "subdir: team-alpha"

cleanup "$ws" "$pack"

# ── 5. External domain — broken layout errors ──────────────────────────────────
group "External domain validation (file://)"

ws=$(new_workspace)
bad_pack=$(mktemp -d)
echo "just a file" > "$bad_pack/README.md"
git_commit_all "$bad_pack"

out=$(run_setup_yn "$PLUGIN" "$ws" "y" init "file://$bad_pack" 2>&1) || true
assert_output_contains "broken pack: error mentions domain.yaml"  "domain.yaml"  "$out"
assert_output_contains "broken pack: error mentions dev-env.yaml" "dev-env.yaml" "$out"

cleanup "$ws" "$bad_pack"

# ── 6. refresh-domain ─────────────────────────────────────────────────────────
group "refresh-domain"

ws=$(new_workspace)

out=$(run_setup "$PLUGIN" "$ws" refresh-domain 2>&1) || true
assert_output_contains "no dev-env.yaml: helpful error" "init" "$out"

run_setup_yn "$PLUGIN" "$ws" "y" init example >/dev/null 2>&1
run_setup_yn "$PLUGIN" "$ws" "y" refresh-domain >/dev/null 2>&1
assert_file     "dev-env.yaml still present after bundled refresh" "$ws/dev-env.yaml"
assert_contains "source still bundled after refresh"               "$ws/dev-env.yaml" "source: bundled"

cleanup "$ws"

# External refresh: modify fixture between init and refresh, verify update applied
ws=$(new_workspace)
fixture=$(mktemp -d)
make_domain_dir "$fixture" "refreshable"
git_commit_all "$fixture"

run_setup_yn "$PLUGIN" "$ws" "y" init "file://$fixture" >/dev/null 2>&1

echo "new_field: added" >> "$fixture/domain.yaml"
git -C "$fixture" add -A
git -C "$fixture" commit -q -m "update domain"

run_setup_yn "$PLUGIN" "$ws" "y" refresh-domain >/dev/null 2>&1
assert_file     "dev-env.yaml present after external refresh"  "$ws/dev-env.yaml"
assert_contains "updated domain.yaml installed"                \
    "$ws/domains/refreshable/domain.yaml" "new_field"

cleanup "$ws" "$fixture"

# ── 7. parse_yaml_domain — scalar format ──────────────────────────────────────
group "parse_yaml_domain scalar format"

ws=$(new_workspace)

cat > "$ws/dev-env.yaml" << 'EOF'
domain: example

repos: []
EOF

run_setup_yn "$PLUGIN" "$ws" "y" refresh-domain >/dev/null 2>&1
assert_contains "scalar parsed: refresh finds domain name"    "$ws/dev-env.yaml" "name: example"
assert_contains "scalar upgraded to mapping after refresh"    "$ws/dev-env.yaml" "source: bundled"
assert_not_contains "scalar form gone after refresh"          "$ws/dev-env.yaml" "^domain: example"

cleanup "$ws"

# ── 8. Active-domain scoping in clone_repo ────────────────────────────────────
group "clone_repo: context files from active domain only"

ws=$(new_workspace)

mkdir -p "$ws/domains/alpha/context" "$ws/domains/beta/context"
printf 'name: alpha\ndescription: "Alpha"\n' > "$ws/domains/alpha/domain.yaml"
printf 'name: beta\ndescription: "Beta"\n'   > "$ws/domains/beta/domain.yaml"
printf '# context from ALPHA\n'  > "$ws/domains/alpha/context/sharedrepo.md"
printf '# context from BETA\n'   > "$ws/domains/beta/context/sharedrepo.md"

shared_src=$(mktemp -d)
make_cloneable_repo "$shared_src" "sharedrepo"

cat > "$ws/dev-env.yaml" << EOF
domain:
  name: alpha
  source: bundled

repos:
  - name: sharedrepo
    url: file://$shared_src
    branch: main
    category: testing
    summary: "shared test repo"
EOF

run_setup "$PLUGIN" "$ws" clone >/dev/null 2>&1

assert_file     "sharedrepo cloned"                       "$ws/repos/sharedrepo/README.md"
assert_file     "DOMAIN-CONTEXT.md distributed"           "$ws/repos/sharedrepo/DOMAIN-CONTEXT.md"
assert_contains "context is from ALPHA (active domain)"   "$ws/repos/sharedrepo/DOMAIN-CONTEXT.md" "ALPHA"
assert_not_contains "context is NOT from BETA"            "$ws/repos/sharedrepo/DOMAIN-CONTEXT.md" "BETA"
assert_dir       ".claude/skills dir created by clone"       "$ws/.claude/skills"
assert_file      "root CLAUDE.md created by clone"          "$ws/CLAUDE.md"
assert_contains  "root CLAUDE.md names the domain"          "$ws/CLAUDE.md" "\*\*alpha\*\*"
assert_contains  "root CLAUDE.md has the repo row"          "$ws/CLAUDE.md" "| \`sharedrepo\` | testing | shared test repo |"
assert_file      "clone applies the settings template"      "$ws/.claude/settings.local.json"

printf '\n## My notes\nkeep me\n' >> "$ws/CLAUDE.md"
sed -i.bak 's/shared test repo/renamed summary/' "$ws/dev-env.yaml" && rm -f "$ws/dev-env.yaml.bak"
run_setup "$PLUGIN" "$ws" clone >/dev/null 2>&1
assert_contains  "re-clone refreshes the repo table"        "$ws/CLAUDE.md" "renamed summary"
assert_not_contains "old table row is gone"                 "$ws/CLAUDE.md" "shared test repo"
assert_contains  "content outside the markers is kept"      "$ws/CLAUDE.md" "keep me"

cleanup "$ws" "$shared_src"

# ── 8b. clone links repo-shipped skills ───────────────────────────────────────
group "clone links repo skills"

ws=$(new_workspace)
skill_src=$(mktemp -d)
mkdir -p "$skill_src/.claude/skills/hello"
printf -- '---\nname: hello\ndescription: test skill\n---\n# hello\n' \
    > "$skill_src/.claude/skills/hello/SKILL.md"
make_cloneable_repo "$skill_src" "skillrepo"
printf 'repos:\n  - name: skillrepo\n    url: file://%s\n    branch: main\n    category: testing\n    summary: "s"\n' \
    "$skill_src" > "$ws/dev-env.yaml"
out=$(run_setup "$PLUGIN" "$ws" clone 2>&1) || true
assert_success "skill symlinked into .claude/skills" test -L "$ws/.claude/skills/hello"
assert_output_contains "clone reports the linked skill" "Linked skill /hello" "$out"
assert_contains "custom workspace CLAUDE.md says custom" "$ws/CLAUDE.md" "\*\*custom\*\*"

cleanup "$ws" "$skill_src"

# ── 9. Workspace domain shadows bundled ───────────────────────────────────────
group "Workspace domain shadows bundled"

ws=$(new_workspace)

# Workspace-local domain named 'example' with a distinguishing marker
mkdir -p "$ws/domains/example"
printf 'name: example\ndescription: "workspace override"\n' > "$ws/domains/example/domain.yaml"
printf 'repos:\n  - name: wsrepo\n    url: https://example.com/ws.git\n    branch: main\n    category: testing\n    summary: "workspace repo"\n' \
    > "$ws/domains/example/dev-env.yaml"

run_setup_yn "$PLUGIN" "$ws" "y" init example >/dev/null 2>&1
assert_contains "init used the WORKSPACE example (wsrepo)" "$ws/dev-env.yaml" "wsrepo"
assert_not_contains "did NOT use the bundled example (gitignore)" "$ws/dev-env.yaml" "gitignore"

cleanup "$ws"

# ── 10. init creates a missing workspace dir ──────────────────────────────────
group "init creates a missing workspace dir"

parent=$(mktemp -d)
fresh="$parent/does-not-exist-yet"
run_setup_yn "$PLUGIN" "$fresh" "y" init example >/dev/null 2>&1
assert_dir  "fresh workspace dir created"        "$fresh"
assert_file "dev-env.yaml created in fresh dir"  "$fresh/dev-env.yaml"

cleanup "$parent"

# ── 11. CLAUDE_PROJECT_DIR fallback (no --workspace) ──────────────────────────
group "CLAUDE_PROJECT_DIR fallback"

ws=$(new_workspace)
echo "y" | env -u WORKSPACE_ROOT CLAUDE_PROJECT_DIR="$ws" \
    bash "$PLUGIN/scripts/setup.sh" init example >/dev/null 2>&1
assert_file     "dev-env.yaml created via CLAUDE_PROJECT_DIR" "$ws/dev-env.yaml"
assert_contains "domain recorded"                            "$ws/dev-env.yaml" "name: example"

cleanup "$ws"

# ── 12. Walk-up resolution from a subdirectory ────────────────────────────────
group "Walk-up resolution from a subdirectory"

ws=$(new_workspace)
run_setup_yn "$PLUGIN" "$ws" "y" init example >/dev/null 2>&1
mkdir -p "$ws/sub/deep"

# No --workspace, no env: resolution must walk up from cwd to find dev-env.yaml
out=$(cd "$ws/sub/deep" && env -u WORKSPACE_ROOT -u CLAUDE_PROJECT_DIR \
    bash "$PLUGIN/scripts/setup.sh" list 2>&1) || true
assert_output_contains "walk-up found workspace (repo listed)" "gitignore" "$out"

cleanup "$ws"

# ── 13. init --self (single-repo self-workspace) ──────────────────────────────
group "init --self (single-repo self-workspace)"

plugin=$(new_plugin)
ws=$(new_workspace)
git init -q -b main "$ws"

assert_success "init --self succeeds in a git repo root" \
    run_setup "$plugin" "$ws" init --self myrepo
assert_file   "creates dev-env.yaml" "$ws/dev-env.yaml"
assert_contains "dev-env.yaml has a self: block" "$ws/dev-env.yaml" "^self:"
assert_contains "self block records the repo name" "$ws/dev-env.yaml" "name: myrepo"
assert_contains "repos list is empty" "$ws/dev-env.yaml" "^repos: \[\]"
assert_not_contains "no domain block" "$ws/dev-env.yaml" "^domain:"
assert_dir  "creates projects/" "$ws/projects"
assert_dir  "creates .claude/skills/" "$ws/.claude/skills"
assert_file "creates settings.local.json from template" "$ws/.claude/settings.local.json"

assert_failure "second init --self refuses (dev-env.yaml exists)" \
    run_setup "$plugin" "$ws" init --self myrepo

assert_success "clone is a clean no-op with empty repos" \
    run_setup "$plugin" "$ws" clone
assert_success "clone never writes a root CLAUDE.md in a self-workspace" \
    test ! -e "$ws/CLAUDE.md"

out=$(run_setup "$plugin" "$ws" detect 2>/dev/null)
assert_output_contains "detect: self" '"mode": "self"' "$out"

cleanup "$ws"

ws=$(new_workspace)
git init -q -b main "$ws"
printf 'build/\n' > "$ws/.gitignore"
run_setup "$plugin" "$ws" init --self myrepo --summary 'A "quoted" repo' --ignore-projects >/dev/null 2>&1
assert_contains "--summary is written YAML-quoted" "$ws/dev-env.yaml" 'summary: "A \\"quoted\\" repo"'
assert_success "--summary yields valid YAML" python3 -c \
    "import yaml,sys; d=yaml.safe_load(open(sys.argv[1])); assert d['self']['summary']=='A \"quoted\" repo'" "$ws/dev-env.yaml"
assert_contains "--ignore-projects adds projects/ to .gitignore" "$ws/.gitignore" "^projects/$"
assert_contains "existing .gitignore lines kept" "$ws/.gitignore" "^build/$"
cleanup "$ws"

ws=$(new_workspace)
git init -q -b main "$ws"
mkdir -p "$ws/sub"
out=$(run_setup "$plugin" "$ws/sub" detect 2>/dev/null)
assert_output_contains "detect: repo (no dev-env.yaml, in git)" '"mode": "repo"' "$out"
assert_output_contains "detect: root is the repo top level" "\"root\": \"$ws\"" "$out"
cleanup "$ws"

ws=$(new_workspace)
out=$(run_setup "$plugin" "$ws" detect 2>/dev/null)
assert_output_contains "detect: new" '"mode": "new"' "$out"
run_setup_yn "$plugin" "$ws" "y" init example >/dev/null 2>&1
out=$(run_setup "$plugin" "$ws" detect 2>/dev/null)
assert_output_contains "detect: domain" '"mode": "domain"' "$out"
cleanup "$ws"

ws=$(new_workspace)
git init -q -b main "$ws"
run_setup "$plugin" "$ws" init --self >/dev/null 2>&1
assert_contains "name defaults to the workspace basename" \
    "$ws/dev-env.yaml" "name: $(basename "$ws")"
cleanup "$ws"

ws=$(new_workspace)
assert_failure "init --self refuses a non-git directory" \
    run_setup "$plugin" "$ws" init --self myrepo
cleanup "$ws" "$plugin"

# ── 14. init --self with metacharacter-bearing names ─────────────────────────────
group "init --self with metacharacter-bearing names"

plugin=$(new_plugin)

# Test with & (ampersand) metacharacter
ws=$(new_workspace)
git init -q -b main "$ws"
assert_success "init --self with & in name succeeds" \
    run_setup "$plugin" "$ws" init --self "lib&api"
assert_file   "dev-env.yaml created with & in name" "$ws/dev-env.yaml"
assert_contains "& is literal in dev-env.yaml" "$ws/dev-env.yaml" "name: lib&api"
cleanup "$ws"

# Test with / (forward slash) metacharacter
ws=$(new_workspace)
git init -q -b main "$ws"
assert_success "init --self with / in name succeeds" \
    run_setup "$plugin" "$ws" init --self "a/b"
assert_file   "dev-env.yaml created with / in name" "$ws/dev-env.yaml"
assert_contains "/ is literal in dev-env.yaml" "$ws/dev-env.yaml" "name: a/b"
cleanup "$ws"

# Test with \ (backslash) metacharacter
ws=$(new_workspace)
git init -q -b main "$ws"
assert_success "init --self with \\ in name succeeds" \
    run_setup "$plugin" "$ws" init --self 'a\b'
assert_file   "dev-env.yaml created with \\ in name" "$ws/dev-env.yaml"
assert_contains "\\ is literal in dev-env.yaml" "$ws/dev-env.yaml" 'name: a\\b'
cleanup "$ws"

# Test with multiple metacharacters combined
ws=$(new_workspace)
git init -q -b main "$ws"
assert_success "init --self with multiple metacharacters succeeds" \
    run_setup "$plugin" "$ws" init --self 'api/v2&old'
assert_file   "dev-env.yaml created with / and & together" "$ws/dev-env.yaml"
assert_contains "multiple metacharacters are literal" "$ws/dev-env.yaml" 'name: api/v2&old'
cleanup "$ws"

cleanup "$plugin"

# ─── Self-mode guards ─────────────────────────────────────────────────────────

group "self-mode guards"

plugin=$(new_plugin)
ws=$(new_workspace)
git init -q -b main "$ws"
run_setup "$plugin" "$ws" init --self myrepo >/dev/null 2>&1

out=$(run_setup "$plugin" "$ws" refresh-domain 2>&1)
assert_failure "refresh-domain refuses on a self workspace" \
    run_setup "$plugin" "$ws" refresh-domain
assert_output_contains "refresh-domain explains it is a self workspace" \
    "single-repo" "$out"

printf 'domain:\n  name: fake\n  source: bundled\n' >> "$ws/dev-env.yaml"
assert_failure "clone rejects self: and domain: together" \
    run_setup "$plugin" "$ws" clone
out=$(run_setup "$plugin" "$ws" clone 2>&1)
assert_output_contains "mutual-exclusion error names both blocks" \
    "mutually exclusive" "$out"
cleanup "$ws" "$plugin"

# ─── refresh-domain warns on local domain updates ─────────────────────────────

group "refresh warns on local domain updates"

ws=$(new_workspace)
fixture=$(mktemp -d)
make_domain_dir "$fixture" "haslessons"
git_commit_all "$fixture"

run_setup_yn "$PLUGIN" "$ws" "y" init "file://$fixture" >/dev/null 2>&1
echo "# Domain Updates" > "$ws/domains/haslessons/UPDATES.md"

out=$(run_setup_yn "$PLUGIN" "$ws" "n" refresh-domain 2>&1) || true
assert_output_contains "warns about local domain updates" \
    "UPDATES.md" "$out"
assert_file "declining overwrite keeps UPDATES.md" "$ws/domains/haslessons/UPDATES.md"

run_setup_yn "$PLUGIN" "$ws" "y" refresh-domain >/dev/null 2>&1
assert_success "accepting overwrite still refreshes the domain" \
    test -f "$ws/domains/haslessons/domain.yaml"

cleanup "$ws" "$fixture"

# ─── Summary ─────────────────────────────────────────────────────────────────

cleanup "$PLUGIN"

echo
echo "──────────────────────────────────────────"
printf "Results: %d passed, %d failed, %d total\n" "$PASSED" "$FAILED" "$TOTAL"
echo "──────────────────────────────────────────"

[[ $FAILED -eq 0 ]]
