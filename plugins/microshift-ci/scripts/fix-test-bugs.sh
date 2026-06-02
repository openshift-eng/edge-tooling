#!/usr/bin/bash
set -euo pipefail

# Deterministic git/GitHub operations for the fix-test-bugs skill.
#
# Three subcommands called by the skill with LLM steps in between:
#
#   fix-test-bugs.sh clone --workdir DIR
#     - Clones openshift/microshift into DIR/microshift/
#     - Sets up remotes: upstream = openshift, origin = user fork
#
#   fix-test-bugs.sh branch --workdir DIR --jira-key KEY
#     - Creates branch microshift-ci/fix-test-bugs/KEY from upstream/main
#
#   fix-test-bugs.sh submit --workdir DIR --jira-key KEY --summary TEXT
#     - Safety checks, commit, push, create PR

ALLOWED_DIRS_RE='^(test|scripts|docs)/'
BRANCH_PREFIX='microshift-ci/fix-test-bugs/'
MAX_FILES=5

# ---------------------------------------------------------------------------
# cleanup_stale_branches — delete fork branches without an open PR
# ---------------------------------------------------------------------------

cleanup_stale_branches() {
    local repo_dir="${1}"
    pushd "${repo_dir}" >/dev/null
    trap 'popd >/dev/null' RETURN

    local remote_branches
    remote_branches=$(git ls-remote --heads origin "refs/heads/${BRANCH_PREFIX}*" 2>/dev/null \
        | sed 's|.*refs/heads/||' || true)
    if [[ -z "${remote_branches}" ]]; then
        return 0
    fi

    local open_branches
    if ! open_branches=$(gh pr list --repo openshift/microshift --author "@me" \
        --state open --json headRefName --jq '.[].headRefName' 2>/dev/null); then
        echo "  Skipping branch cleanup: failed to list open PRs" >&2
        return 0
    fi
    echo "Cleaning stale branches from fork..." >&2
    while IFS= read -r branch; do
        if ! echo "${open_branches}" | grep -qxF "${branch}"; then
            echo "  Deleting origin/${branch}" >&2
            git push origin --delete "${branch}" 2>/dev/null || true
        fi
    done <<< "${remote_branches}"
}

# ---------------------------------------------------------------------------
# clone
# ---------------------------------------------------------------------------

cmd_clone() {
    local workdir=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir) workdir="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; return 1 ;;
            *) echo "Unknown argument: ${1}" >&2; return 1 ;;
        esac
    done

    [[ -z "${workdir}" ]] && { echo "Error: --workdir is required" >&2; return 1; }

    local repo_dir="${workdir}/microshift"

    if [[ -d "${repo_dir}" ]]; then
        echo "Repository already exists at ${repo_dir}, skipping clone." >&2
        cleanup_stale_branches "${repo_dir}"
        jq -n --arg d "${repo_dir}" '{repo_dir: $d}'
        return 0
    fi

    echo "Cloning openshift/microshift into ${repo_dir}..." >&2
    git clone https://github.com/openshift/microshift.git "${repo_dir}"

    pushd "${repo_dir}" >/dev/null
    gh repo fork --remote --remote-name fork
    git remote rename origin upstream
    git remote rename fork origin
    echo "Remotes configured: upstream=openshift/microshift, origin=$(git remote get-url origin)" >&2
    popd >/dev/null

    cleanup_stale_branches "${repo_dir}"

    jq -n --arg d "${repo_dir}" '{repo_dir: $d}'
}

# ---------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------

cmd_branch() {
    local workdir=""
    local jira_key=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir) workdir="${2}"; shift 2 ;;
            --jira-key) jira_key="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; return 1 ;;
            *) echo "Unknown argument: ${1}" >&2; return 1 ;;
        esac
    done

    [[ -z "${workdir}" ]] && { echo "Error: --workdir is required" >&2; return 1; }
    [[ -z "${jira_key}" ]] && { echo "Error: --jira-key is required" >&2; return 1; }

    local repo_dir="${workdir}/microshift"
    [[ -d "${repo_dir}" ]] || { echo "Error: repo not found at ${repo_dir} — run clone first" >&2; return 1; }

    cd "${repo_dir}"
    # Clean leftover edits from a prior bug's failed fix attempt (before submit ran)
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
    git fetch upstream main

    local branch="${BRANCH_PREFIX}${jira_key}"

    if git rev-parse --verify "${branch}" >/dev/null 2>&1; then
        echo "Branch ${branch} already exists, deleting for clean retry..." >&2
        git checkout upstream/main --detach 2>/dev/null
        git branch -D "${branch}"
    fi

    git checkout -b "${branch}" upstream/main
    echo "Created branch ${branch} from upstream/main" >&2

    jq -n --arg b "${branch}" '{branch: $b, base: "main"}'
}

# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------

revert_changes() {
    echo "Reverting all changes..." >&2
    git reset HEAD -- . >/dev/null 2>&1 || true
    git checkout -- . 2>/dev/null || true
    git clean -fd 2>/dev/null || true
}

cmd_submit() {
    local workdir=""
    local jira_key=""
    local summary=""
    local rationale=""

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --workdir) workdir="${2}"; shift 2 ;;
            --jira-key) jira_key="${2}"; shift 2 ;;
            --summary) summary="${2}"; shift 2 ;;
            --rationale) rationale="${2}"; shift 2 ;;
            -*) echo "Unknown option: ${1}" >&2; return 1 ;;
            *) echo "Unknown argument: ${1}" >&2; return 1 ;;
        esac
    done

    [[ -z "${workdir}" ]] && { echo "Error: --workdir is required" >&2; return 1; }
    [[ -z "${jira_key}" ]] && { echo "Error: --jira-key is required" >&2; return 1; }
    [[ -z "${summary}" ]] && { echo "Error: --summary is required" >&2; return 1; }
    [[ -z "${rationale}" ]] && { echo "Error: --rationale is required" >&2; return 1; }

    local repo_dir="${workdir}/microshift"
    cd "${repo_dir}"

    # Stage all changes
    git add -A

    # Check for empty diff
    if git diff --cached --quiet; then
        echo "Error: no changes to commit" >&2
        return 1
    fi

    # Check allowed directories
    local disallowed
    disallowed=$(git diff --cached --name-only | grep -vE "${ALLOWED_DIRS_RE}" || true)
    if [[ -n "${disallowed}" ]]; then
        echo "Error: changes outside allowed directories:" >&2
        echo "${disallowed}" >&2
        revert_changes
        return 1
    fi

    # Check max files
    local file_count
    file_count=$(git diff --cached --name-only | wc -l)
    if [[ "${file_count}" -gt "${MAX_FILES}" ]]; then
        echo "Error: ${file_count} files changed, maximum is ${MAX_FILES}" >&2
        revert_changes
        return 1
    fi

    echo "Safety checks passed (${file_count} file(s), all in allowed directories)" >&2

    # Commit
    local commit_msg="${jira_key}: fix CI test: ${summary}"
    git commit -m "${commit_msg}"

    # Push
    local branch="${BRANCH_PREFIX}${jira_key}"
    git push -u origin "${branch}"

    # Build PR body — format as markdown list so filenames with spaces are safe
    local changed_files
    changed_files=$(git diff --name-only HEAD~1 | sed 's/^/- `/' | sed 's/$/ `/')

    local pr_title="${jira_key}: fix CI test: ${summary}"
    local pr_url
    pr_url=$(gh pr create --repo openshift/microshift --base main --draft \
        --title "${pr_title}" \
        --body "$(cat <<EOF
## Summary

Fix for [${jira_key}](https://issues.redhat.com/browse/${jira_key}).
*Auto-generated by [/microshift-ci:fix-test-bugs](https://github.com/openshift-eng/edge-tooling)* :robot:

## Rationale

${rationale}

## Changed files

${changed_files}

## Verification

- [ ] CI passes
- [ ] Changes are limited to test/scripts/docs
- [ ] Fix addresses the root cause described in the JIRA bug
EOF
)")

    echo "PR created: ${pr_url}" >&2
    jq -n --arg url "${pr_url}" --arg key "${jira_key}" '{pr_url: $url, jira_key: $key}'
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") <command> [options]

Commands:
  clone   --workdir DIR                              Clone openshift/microshift and set up remotes
  branch  --workdir DIR --jira-key KEY               Create branch from upstream/main
  submit  --workdir DIR --jira-key KEY --summary TXT --rationale TXT  Verify, commit, push, and create PR
EOF
    exit 1
}

main() {
    if [[ ${#} -lt 1 ]]; then
        usage
    fi

    local cmd="${1}"
    shift

    case "${cmd}" in
        clone)  cmd_clone "${@}" ;;
        branch) cmd_branch "${@}" ;;
        submit) cmd_submit "${@}" ;;
        *) echo "Unknown command: ${cmd}" >&2; usage ;;
    esac
}

main "${@}"
