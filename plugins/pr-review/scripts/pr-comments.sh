#!/usr/bin/bash
set -euo pipefail

# Fetch unresolved inline review comments, output structured JSON.
# Single GraphQL query with cursor-based pagination replaces separate
# REST + GraphQL calls. Resolved threads are filtered server-side.
# Exit codes: 0=has unresolved comments, 1=no unresolved comments, 3=error

URL_PATTERN='^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/pull/[0-9]+$'

GRAPHQL_QUERY='
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewDecision
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          comments(first: 100) {
            nodes {
              databaseId
              author { login __typename }
              body
              path
              line
              originalLine
              diffHunk
              createdAt
              updatedAt
            }
          }
        }
      }
    }
  }
}'

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

fetch_all_data() {
    local org="$1" repo="$2" pr_number="$3"
    local all_threads="[]"
    local review_decision=""
    local has_next="true"
    local cursor=""

    while [[ "${has_next}" == "true" ]]; do
        local -a gh_args=(
            -f query="${GRAPHQL_QUERY}"
            -f owner="${org}"
            -f repo="${repo}"
            -F number="${pr_number}"
        )
        if [[ -n "${cursor}" ]]; then
            gh_args+=(-f after="${cursor}")
        fi

        local result
        result=$(gh api graphql "${gh_args[@]}") \
            || die "Failed to fetch PR data for ${org}/${repo}#${pr_number}"

        if [[ -z "${review_decision}" ]]; then
            review_decision=$(echo "${result}" | jq -r '.data.repository.pullRequest.reviewDecision // "PENDING"')
        fi

        local page_threads
        page_threads=$(echo "${result}" | jq -c '.data.repository.pullRequest.reviewThreads.nodes')
        all_threads=$(jq -nc --argjson a "${all_threads}" --argjson b "${page_threads}" '$a + $b')

        has_next=$(echo "${result}" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
        cursor=$(echo "${result}" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
    done

    jq -nc \
        --arg decision "${review_decision}" \
        --argjson threads "${all_threads}" \
        '{"review_decision": $decision, "threads": $threads}'
}

build_output() {
    local graphql_data="$1" addressed_ids="$2" skip_users="$3"

    local addressed_filter
    if [[ -n "${addressed_ids}" ]]; then
        addressed_filter=$(echo "${addressed_ids}" | jq -Rc 'split(",") | map(tonumber)')
    else
        addressed_filter="[]"
    fi

    local review_decision
    review_decision=$(echo "${graphql_data}" | jq -r '.review_decision')

    local inline_block
    inline_block=$(echo "${graphql_data}" | jq -c \
        --argjson addressed "${addressed_filter}" \
        --argjson skip_bots_only "${skip_users}" \
        '[
          .threads[]
          | select(.isResolved | not)
          | .comments.nodes as $comments
          | ($comments[0].databaseId) as $root_id
          | $comments[]
          | {
              id: .databaseId,
              author: .author.login,
              body: .body,
              file: .path,
              line: (.line // .originalLine),
              diff_hunk: .diffHunk,
              is_bot: (.author.__typename == "Bot" or (.author.login | test("\\[bot\\]$"))),
              created_at: .createdAt,
              updated_at: .updatedAt,
              in_reply_to_id: (if .databaseId == $root_id then null else $root_id end)
            }
          | select(.id as $id | ($addressed | index($id)) | not)
          | if $skip_bots_only then select(.is_bot) else . end
        ]')

    local inline_count
    inline_count=$(echo "${inline_block}" | jq 'length')

    jq -nc \
        --argjson inline "${inline_block}" \
        --arg decision "${review_decision}" \
        --argjson total "${inline_count}" \
        '{
            inline_comments: $inline,
            review_decision: $decision,
            summary: {
                total_new: $total
            }
        }'
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <github-pr-url> [addressed-comment-ids] [--skip-users]"

    local pr_url="" addressed_ids="" skip_users=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-users)
                skip_users=true
                shift
                ;;
            *)
                if [[ -z "${pr_url}" ]]; then
                    pr_url="$1"
                elif [[ -z "${addressed_ids}" ]]; then
                    addressed_ids="$1"
                fi
                shift
                ;;
        esac
    done

    [[ -z "${pr_url}" ]] && die "Usage: $(basename "$0") <github-pr-url> [addressed-comment-ids] [--skip-users]"

    check_dependencies
    validate_url "${pr_url}"
    parse_url "${pr_url}"

    local graphql_data
    graphql_data=$(fetch_all_data "${ORG}" "${REPO}" "${PR_NUMBER}")

    local output
    output=$(build_output "${graphql_data}" "${addressed_ids}" "${skip_users}")

    echo "${output}"

    local total_new
    total_new=$(echo "${output}" | jq -r '.summary.total_new')
    if [[ "${total_new}" -eq 0 ]]; then
        exit 1
    fi
}

main "$@"
