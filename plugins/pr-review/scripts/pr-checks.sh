#!/usr/bin/bash
set -euo pipefail

# Fetch PR metadata and Prow CI check statuses, output structured JSON.
# Exit codes: 0=all pass, 1=failures, 2=pending only, 3=error

URL_PATTERN='^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/pull/[0-9]+$'

die() {
    echo "Error: $1" >&2
    exit 3
}

check_dependencies() {
    command -v gh >/dev/null 2>&1 || die "gh CLI is not installed"
    command -v jq >/dev/null 2>&1 || die "jq is not installed"
    gh auth status >/dev/null 2>&1 || die "gh CLI is not authenticated — run 'gh auth login'"
}

validate_url() {
    local url="$1"
    if [[ ! "${url}" =~ ${URL_PATTERN} ]]; then
        die "Invalid PR URL: ${url}"
    fi
}

parse_url() {
    local url="$1"
    ORG=$(echo "${url}" | cut -d'/' -f4)
    REPO=$(echo "${url}" | cut -d'/' -f5)
    PR_NUMBER=$(echo "${url}" | cut -d'/' -f7)
}

fetch_pr_metadata() {
    local org="$1" repo="$2" pr_number="$3"
    local pr_json
    pr_json=$(gh pr view "${pr_number}" --repo "${org}/${repo}" \
        --json number,title,author,state,url,headRefName,headRefOid,headRepository,headRepositoryOwner) \
        || die "Failed to fetch PR metadata for ${org}/${repo}#${pr_number}"
    echo "${pr_json}"
}

fetch_pr_checks() {
    local org="$1" repo="$2" pr_number="$3"
    local checks_json
    local gh_exit=0
    checks_json=$(gh pr checks "${pr_number}" --repo "${org}/${repo}" \
        --json name,state,link,bucket 2>/dev/null) || gh_exit=$?
    if [[ ${gh_exit} -gt 1 ]]; then
        die "Failed to fetch CI checks for ${org}/${repo}#${pr_number} (gh exit ${gh_exit})"
    fi
    if [[ -z "${checks_json}" || "${checks_json}" == "null" ]]; then
        echo "[]"
    else
        echo "${checks_json}"
    fi
}

build_output() {
    local pr_json="$1" checks_json="$2" org="$3" repo="$4"

    local pr_block
    pr_block=$(echo "${pr_json}" | jq -c \
        --arg repo "${org}/${repo}" \
        '{
            number: .number,
            title: .title,
            author: .author.login,
            state: .state,
            url: .url,
            branch: .headRefName,
            sha: .headRefOid,
            repo: $repo
        }')

    local jobs_block
    jobs_block=$(echo "${checks_json}" | jq -c '
        [.[] | select(.name != "tide") |
        {
            name: (.name | sub("^ci/prow/"; "")),
            status: (if .bucket == "pass" then "pass"
                     elif .bucket == "fail" then "fail"
                     else "pending" end),
            url: .link
        }]')

    local summary_block
    summary_block=$(echo "${jobs_block}" | jq -c '{
        total: length,
        passed: [.[] | select(.status == "pass")] | length,
        failed: [.[] | select(.status == "fail")] | length,
        pending: [.[] | select(.status == "pending")] | length
    }')

    jq -nc \
        --argjson pr "${pr_block}" \
        --argjson jobs "${jobs_block}" \
        --argjson summary "${summary_block}" \
        '{pr: $pr, jobs: $jobs, summary: $summary}'
}

determine_exit_code() {
    local summary_json="$1"
    local failed pending
    failed=$(echo "${summary_json}" | jq -r '.failed')
    pending=$(echo "${summary_json}" | jq -r '.pending')

    if [[ "${failed}" -gt 0 ]]; then
        return 1
    elif [[ "${pending}" -gt 0 ]]; then
        return 2
    fi
    return 0
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <github-pr-url>"

    local pr_url="$1"

    check_dependencies
    validate_url "${pr_url}"
    parse_url "${pr_url}"

    local pr_json checks_json
    pr_json=$(fetch_pr_metadata "${ORG}" "${REPO}" "${PR_NUMBER}")
    checks_json=$(fetch_pr_checks "${ORG}" "${REPO}" "${PR_NUMBER}")

    local output
    output=$(build_output "${pr_json}" "${checks_json}" "${ORG}" "${REPO}")

    echo "${output}"

    local summary
    summary=$(echo "${output}" | jq -c '.summary')
    determine_exit_code "${summary}" || exit $?
}

main "$@"
