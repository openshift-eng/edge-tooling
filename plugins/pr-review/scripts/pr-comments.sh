#!/usr/bin/bash
set -euo pipefail

# Fetch unresolved inline review comments, output structured JSON.
# Single GraphQL query with cursor-based pagination replaces separate
# REST + GraphQL calls. Resolved threads are filtered server-side.
# Exit codes: 0=has unresolved comments, 1=no unresolved comments, 3=error

URL_PATTERN='^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/pull/[0-9]+$'

# shellcheck disable=SC2016
GRAPHQL_QUERY='
query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewDecision
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
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

GH_USER=""

resolve_gh_user() {
    if [[ -z "${GH_USER}" ]]; then
        GH_USER=$(gh api user --jq '.login') || die "Failed to get GitHub username"
    fi
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
            -f "query=${GRAPHQL_QUERY}"
            -f "owner=${org}"
            -f "repo=${repo}"
            -F "number=${pr_number}"
        )
        if [[ -n "${cursor}" ]]; then
            gh_args+=(-f "after=${cursor}")
        fi

        local result
        result=$(gh api graphql "${gh_args[@]}") \
            || die "Failed to fetch PR data for ${org}/${repo}#${pr_number}"

        if [[ -z "${review_decision}" ]]; then
            review_decision=$(echo "${result}" | jq -r '.data.repository.pullRequest.reviewDecision // "PENDING"') \
                || die "Failed to parse reviewDecision from GraphQL response"
        fi

        local page_threads
        page_threads=$(echo "${result}" | jq -c '.data.repository.pullRequest.reviewThreads.nodes') \
            || die "Failed to parse reviewThreads from GraphQL response"
        all_threads=$(jq -s '.[0] + .[1]' <(echo "${all_threads}") <(echo "${page_threads}")) \
            || die "Failed to merge thread pages"

        has_next=$(echo "${result}" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage') \
            || die "Failed to parse pageInfo from GraphQL response"
        cursor=$(echo "${result}" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor') \
            || die "Failed to parse endCursor from GraphQL response"
    done

    echo "${all_threads}" | jq -c --arg decision "${review_decision}" \
        '{"review_decision": $decision, "threads": .}'
}

build_output() {
    local graphql_data="$1" addressed_ids="$2" include_users="$3"

    local addressed_filter
    if [[ -n "${addressed_ids}" ]]; then
        addressed_filter=$(echo "${addressed_ids}" | jq -Rc 'split(",") | map(tonumber)') \
            || die "Failed to parse addressed IDs: ${addressed_ids}"
    else
        addressed_filter="[]"
    fi

    local review_decision
    review_decision=$(echo "${graphql_data}" | jq -r '.review_decision') \
        || die "Failed to extract review_decision from data"

    local inline_block
    inline_block=$(echo "${graphql_data}" | jq -c \
        --argjson addressed "${addressed_filter}" \
        --argjson include_users "${include_users}" \
        --arg gh_user "${GH_USER}" \
        '[
          .threads[]
          | .id as $thread_id
          | select(.isResolved | not)
          | .comments.nodes as $comments
          | ($comments[0]) as $root
          | $root.databaseId as $root_id
          | (($addressed | index($root_id)) != null) as $was_addressed
          | select(
              if $was_addressed then
                ($gh_user != "") and (($comments | last | .author.login) != $gh_user)
              else
                true
              end
            )
          | {
              id: $root_id,
              thread_id: $thread_id,
              author: $root.author.login,
              body: $root.body,
              file: $root.path,
              line: ($root.line // $root.originalLine),
              diff_hunk: $root.diffHunk,
              is_bot: ($root.author.__typename == "Bot" or ($root.author.login | test("\\[bot\\]$"))),
              created_at: $root.createdAt,
              updated_at: $root.updatedAt,
              resurfaced: $was_addressed,
              thread_context: (
                if ($comments | length) > 1 then
                  [$comments[1:][] | {author: .author.login, body: .body, created_at: .createdAt}]
                else
                  []
                end
              )
            }
          | if $include_users then . else select(.is_bot) end
        ]') \
        || die "Failed to build inline comments from GraphQL data"

    echo "${inline_block}" | jq -c \
        --arg decision "${review_decision}" \
        '{
            inline_comments: .,
            review_decision: $decision,
            summary: {
                total_new: [.[] | select(.resurfaced | not)] | length,
                total_resurfaced: [.[] | select(.resurfaced)] | length
            }
        }'
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <github-pr-url> [addressed-comment-ids] [--include-users]"

    local pr_url="" addressed_ids="" include_users=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --include-users)
                include_users=true
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

    [[ -z "${pr_url}" ]] && die "Usage: $(basename "$0") <github-pr-url> [addressed-comment-ids] [--include-users]"

    check_dependencies
    validate_url "${pr_url}"
    parse_url "${pr_url}"

    if [[ -n "${addressed_ids}" ]]; then
        resolve_gh_user
    fi

    local graphql_data
    graphql_data=$(fetch_all_data "${ORG}" "${REPO}" "${PR_NUMBER}")

    local output
    output=$(build_output "${graphql_data}" "${addressed_ids}" "${include_users}")

    echo "${output}"

    local total_new total_resurfaced
    total_new=$(echo "${output}" | jq -r '.summary.total_new')
    total_resurfaced=$(echo "${output}" | jq -r '.summary.total_resurfaced')
    if [[ "${total_new}" -eq 0 && "${total_resurfaced}" -eq 0 ]]; then
        exit 1
    fi
}

main "$@"
