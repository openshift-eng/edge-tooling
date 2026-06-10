#!/usr/bin/bash
set -euo pipefail

# Prow Jobs for Pull Requests
# Data modes (summary, detail): JSON on stdout
# Action modes (approve, restart): text on stdout
# Progress/errors: stderr

GCS_API="https://storage.googleapis.com/storage/v1/b/test-platform-results/o"
GCS_BASE="https://storage.googleapis.com/test-platform-results"
PROW_VIEW="https://prow.ci.openshift.org/view/gs/test-platform-results"
GH_REPO="openshift/microshift"
GCS_PR_PREFIX="pr-logs/pull/openshift_microshift"
SIGNATURE=$'\n'"*Added by $(basename "${0}")* :robot:"$'\n'
SCRIPT_TMPDIR=$(mktemp -d)
trap 'rm -rf "${SCRIPT_TMPDIR}"' EXIT

# Get open PRs as JSON array
# Args: filter, author, extra_fields (comma-separated, appended to default fields)
fetch_open_prs() {
    local filter="${1:-}"
    local author="${2:-}"
    local extra_fields="${3:-}"
    local fields="number,title,url"
    [[ -n "${extra_fields}" ]] && fields+=",${extra_fields}"
    local -a gh_args=(--repo "${GH_REPO}" --state open --limit 100 --json "${fields}")

    [[ -n "${author}" ]] && gh_args+=(--author "${author}")

    local pr_data
    pr_data=$(gh pr list "${gh_args[@]}")

    if [[ -n "${filter}" ]]; then
        echo "${pr_data}" | jq -c --arg f "${filter}" '[.[] | select(.title | contains($f))]'
    else
        echo "${pr_data}"
    fi
}

# List job names for a PR from GCS
list_pr_jobs() {
    local pr="${1}"
    curl -s --max-time 60 --retry 3 --retry-delay 5 "${GCS_API}?prefix=${GCS_PR_PREFIX}/${pr}/&delimiter=/" | \
        jq -r '.prefixes[]? // empty' | \
        sed "s|${GCS_PR_PREFIX}/${pr}/||; s|/$||"
}

# Get latest build result as a JSON object
# Returns PENDING status for jobs still running (no finished.json).
get_latest_build() {
    local pr="${1}" job="${2}"
    local build_id finished_json

    build_id=$(curl -s --max-time 60 --retry 3 --retry-delay 5 "${GCS_BASE}/${GCS_PR_PREFIX}/${pr}/${job}/latest-build.txt" 2>/dev/null) || return 1
    [[ -z "${build_id}" || "${build_id}" == *"<"* ]] && return 1

    local url="${PROW_VIEW}/pr-logs/pull/openshift_microshift/${pr}/${job}/${build_id}"

    finished_json=$(curl -s --max-time 60 --retry 3 --retry-delay 5 "${GCS_BASE}/${GCS_PR_PREFIX}/${pr}/${job}/${build_id}/finished.json" 2>/dev/null)

    if [[ -z "${finished_json}" || "${finished_json}" == *"NoSuchKey"* || "${finished_json}" == *"<"* ]]; then
        # Job still running — no finished.json yet
        jq -nc --arg job "${job}" --arg url "${url}" --arg build_id "${build_id}" \
            '{job: $job, status: "PENDING", url: $url, build_id: $build_id, finished: null}'
        return 0
    fi

    echo "${finished_json}" | jq -c \
        --arg job "${job}" --arg url "${url}" --arg build_id "${build_id}" \
        '{
            job: $job,
            status: (.result // "PENDING"),
            url: $url,
            build_id: $build_id,
            finished: (if (.timestamp // 0) > 0 then .timestamp | todate else null end)
        }'
}

# Fetch job results for a single PR into temp dir (parallelized)
# Skips individual jobs that fail to fetch (e.g. no latest-build.txt).
fetch_pr_results() {
    local pr="${1}"
    local tmpdir="${2}"
    local jobs

    jobs=$(list_pr_jobs "${pr}") || return 1
    [[ -z "${jobs}" ]] && return 0

    while IFS= read -r job; do
        (
            result=$(get_latest_build "${pr}" "${job}" 2>/dev/null) || exit 0
            if [[ -n "${result}" ]]; then
                echo "${result}" > "${tmpdir}/${job}.json"
            fi
        ) &
    done <<< "${jobs}"
    wait
    return 0
}

# Collect per-job JSON files into a single JSON array
collect_jobs_json() {
    local tmpdir="${1}"
    local files=("${tmpdir}"/*.json)
    if [[ ! -f "${files[0]}" ]]; then
        echo "[]"
        return
    fi
    cat "${files[@]}" | jq -s '.'
}

# Summary mode: JSON array of PRs with pass/fail counts
mode_summary() {
    local filter="${1:-}" author="${2:-}"
    local pr_data output_tmp

    echo "Fetching open PRs..." >&2
    pr_data=$(fetch_open_prs "${filter}" "${author}")
    [[ "$(echo "${pr_data}" | jq 'length')" -eq 0 ]] && { echo "[]"; return; }

    echo "Fetching job results..." >&2
    local output_tmp="${SCRIPT_TMPDIR}/summary-output"
    echo -n "" > "${output_tmp}"

    while IFS=$'\t' read -r pr_number pr_title pr_url; do
        local tmpdir="${SCRIPT_TMPDIR}/summary-pr-${pr_number}"
        mkdir -p "${tmpdir}"

        if ! fetch_pr_results "${pr_number}" "${tmpdir}"; then
            echo "PR #${pr_number}: incomplete job results, skipping" >&2
            continue
        fi

        local passed=0 failed=0 other=0 total=0
        for f in "${tmpdir}"/*.json; do
            [[ -f "${f}" ]] || continue
            local status
            status=$(jq -r '.status' "${f}")
            total=$((total + 1))
            case "${status}" in
                SUCCESS) passed=$((passed + 1)) ;;
                FAILURE) failed=$((failed + 1)) ;;
                *)       other=$((other + 1)) ;;
            esac
        done

        jq -nc --argjson n "${pr_number}" --arg t "${pr_title}" --arg u "${pr_url}" \
            --argjson p "${passed}" --argjson f "${failed}" \
            --argjson o "${other}" --argjson to "${total}" \
            '{pr_number: $n, title: $t, url: $u, passed: $p, failed: $f, other: $o, total: $to}' \
            >> "${output_tmp}"
    done < <(echo "${pr_data}" | jq -r '.[] | [.number, .title, .url] | @tsv')

    jq -s '.' "${output_tmp}"
}

# Detail mode: JSON array of PRs with full job lists
mode_detail() {
    local filter="${1:-}" author="${2:-}"
    local pr_data output_tmp

    echo "Fetching open PRs..." >&2
    pr_data=$(fetch_open_prs "${filter}" "${author}")
    [[ "$(echo "${pr_data}" | jq 'length')" -eq 0 ]] && { echo "[]"; return; }

    echo "Fetching job results..." >&2
    local output_tmp="${SCRIPT_TMPDIR}/detail-output"
    echo -n "" > "${output_tmp}"

    while IFS=$'\t' read -r pr_number pr_title pr_url; do
        local tmpdir="${SCRIPT_TMPDIR}/detail-pr-${pr_number}"
        mkdir -p "${tmpdir}"

        if ! fetch_pr_results "${pr_number}" "${tmpdir}"; then
            echo "PR #${pr_number}: incomplete job results, skipping" >&2
            continue
        fi

        local jobs_json
        jobs_json=$(collect_jobs_json "${tmpdir}")

        jq -nc --argjson n "${pr_number}" --arg t "${pr_title}" --arg u "${pr_url}" \
            --argjson jobs "${jobs_json}" \
            '{pr_number: $n, title: $t, url: $u, jobs: $jobs}' >> "${output_tmp}"
    done < <(echo "${pr_data}" | jq -r '.[] | [.number, .title, .url] | @tsv')

    jq -s '.' "${output_tmp}"
}

# Approve mode: add /verified to PRs where all jobs pass
mode_approve() {
    local filter="${1:-}" author="${2:-}" execute="${3:-false}"
    local pr_data had_error=0

    ${execute} || echo "[DRY-RUN] Use --execute to actually post comments" >&2
    echo "Fetching open PRs..." >&2
    pr_data=$(fetch_open_prs "${filter}" "${author}")
    [[ "$(echo "${pr_data}" | jq 'length')" -eq 0 ]] && { echo "No open pull requests found."; return; }

    echo "Fetching job results..." >&2

    while IFS=$'\t' read -r pr_number pr_title pr_url; do
        local tmpdir="${SCRIPT_TMPDIR}/approve-pr-${pr_number}"
        mkdir -p "${tmpdir}"

        if ! fetch_pr_results "${pr_number}" "${tmpdir}"; then
            echo "PR #${pr_number}: incomplete job results, skipping"
            continue
        fi

        local total=0 success=0
        for f in "${tmpdir}"/*.json; do
            [[ -f "${f}" ]] || continue
            local status
            status=$(jq -r '.status' "${f}")
            total=$((total + 1))
            [[ "${status}" == "SUCCESS" ]] && success=$((success + 1))
        done

        if [[ "${total}" -eq 0 ]]; then
            echo "PR #${pr_number}: No jobs found, skipping"
            continue
        fi

        if [[ "${success}" -eq "${total}" ]]; then
            local comment=$'/verified by ci\n'
            comment+="${SIGNATURE}"
            echo "PR #${pr_number}: All ${total} jobs passed, approving..."
            if ${execute}; then
                if gh pr comment "${pr_number}" --repo "${GH_REPO}" --body "${comment}"; then
                    echo "PR #${pr_number}: Approved"
                else
                    echo "PR #${pr_number}: Failed to post approve comment" >&2
                    had_error=1
                fi
            else
                echo "gh pr comment ${pr_number} --repo ${GH_REPO} --body '/verified by ci ...'"
            fi
        else
            echo "PR #${pr_number}: ${success}/${total} jobs passed, skipping"
        fi
    done < <(echo "${pr_data}" | jq -r '.[] | [.number, .title, .url] | @tsv')

    return "${had_error}"
}

# Restart mode: comment /test for each failed job
mode_restart() {
    local filter="${1:-}" author="${2:-}" execute="${3:-false}"
    local pr_data had_error=0

    ${execute} || echo "[DRY-RUN] Use --execute to actually post comments" >&2
    echo "Fetching open PRs..." >&2
    pr_data=$(fetch_open_prs "${filter}" "${author}")
    [[ "$(echo "${pr_data}" | jq 'length')" -eq 0 ]] && { echo "No open pull requests found."; return; }

    echo "Fetching job results..." >&2

    while IFS=$'\t' read -r pr_number pr_title pr_url; do
        local tmpdir="${SCRIPT_TMPDIR}/restart-pr-${pr_number}"
        mkdir -p "${tmpdir}"

        if ! fetch_pr_results "${pr_number}" "${tmpdir}"; then
            echo "PR #${pr_number}: incomplete job results, skipping"
            continue
        fi

        local failed_jobs=()
        for f in "${tmpdir}"/*.json; do
            [[ -f "${f}" ]] || continue
            local job status
            job=$(jq -r '.job' "${f}")
            status=$(jq -r '.status' "${f}")
            [[ "${status}" == "FAILURE" ]] && failed_jobs+=("${job}")
        done

        if [[ ${#failed_jobs[@]} -eq 0 ]]; then
            echo "PR #${pr_number}: No failed jobs, skipping"
            continue
        fi

        # Fetch short /test names from prowjob.json for each failed job
        local comment=""
        for job in "${failed_jobs[@]}"; do
            local build_id short_name
            build_id=$(curl -s --max-time 60 --retry 3 --retry-delay 5 "${GCS_BASE}/${GCS_PR_PREFIX}/${pr_number}/${job}/latest-build.txt" 2>/dev/null) || continue
            short_name=$(curl -s --max-time 60 --retry 3 --retry-delay 5 "${GCS_BASE}/${GCS_PR_PREFIX}/${pr_number}/${job}/${build_id}/prowjob.json" 2>/dev/null | \
                jq -r '.spec.rerun_command // empty' 2>/dev/null | sed 's|^/test ||') || short_name=""
            short_name=$(echo "${short_name}" | xargs)
            [[ -z "${short_name}" ]] && continue
            comment+="/test ${short_name}"$'\n'
        done

        if [[ -z "${comment}" ]]; then
            echo "PR #${pr_number}: Could not resolve rerun commands for failed job(s), skipping"
            continue
        fi

        comment+="${SIGNATURE}"
        echo "PR #${pr_number}: Restarting ${#failed_jobs[@]} failed job(s): ${failed_jobs[*]}"
        if ${execute}; then
            if gh pr comment "${pr_number}" --repo "${GH_REPO}" --body "${comment}"; then
                echo "PR #${pr_number}: Restart comment posted"
            else
                echo "PR #${pr_number}: Failed to post restart comment" >&2
                had_error=1
            fi
        else
            echo "gh pr comment ${pr_number} --repo ${GH_REPO} --body '/test <failed-jobs> ...'"
        fi
    done < <(echo "${pr_data}" | jq -r '.[] | [.number, .title, .url] | @tsv')

    return "${had_error}"
}

# Close-duplicates mode: close older PRs superseded by newer ones.
# Groups PRs by target branch. Within each group, keeps the newest
# PR (highest number) and closes older ones.
mode_close_duplicates() {
    local filter="${1:-}" author="${2:-}" execute="${3:-false}" had_error=0

    if [[ -z "${filter}" || -z "${author}" ]]; then
        echo "Error: --filter and --author are required for close-duplicates mode" >&2
        echo "Example: --mode close-duplicates --filter 'rebase-release-' --author 'microshift-rebase-script[bot]'" >&2
        return 1
    fi

    ${execute} || echo "[DRY-RUN] Use --execute to actually close PRs" >&2

    echo "Fetching open PRs (author: ${author}, filter: ${filter})..." >&2
    local pr_data
    pr_data=$(fetch_open_prs "${filter}" "${author}" "baseRefName")

    [[ "$(echo "${pr_data}" | jq 'length')" -eq 0 ]] && { echo "No matching PRs found."; return; }

    # Group by target branch. All PRs matching the filter on the same
    # branch are considered duplicates of each other.
    local groups
    groups=$(echo "${pr_data}" | jq -c '
        group_by(.baseRefName)
        | map(select(length > 1) | sort_by(.number) | reverse)
    ')

    local group_count
    group_count=$(echo "${groups}" | jq 'length')

    if [[ "${group_count}" -eq 0 ]]; then
        echo "No duplicate PRs found."
        return
    fi

    for i in $(seq 0 $((group_count - 1))); do
        local group newest_number newest_title
        group=$(echo "${groups}" | jq -c ".[$i]")
        newest_number=$(echo "${group}" | jq '.[0].number')
        newest_title=$(echo "${group}" | jq -r '.[0].title')
        local base_ref
        base_ref=$(echo "${group}" | jq -r '.[0].baseRefName')

        echo "${base_ref}: keeping PR #${newest_number} (${newest_title})"

        local dup_count
        dup_count=$(echo "${group}" | jq 'length - 1')

        for j in $(seq 1 "${dup_count}"); do
            local dup_number dup_title
            dup_number=$(echo "${group}" | jq ".[$j].number")
            dup_title=$(echo "${group}" | jq -r ".[$j].title")

            local comment="Closing as duplicate: superseded by #${newest_number}."
            comment+=$'\n'"/close"
            comment+="${SIGNATURE}"

            echo "Closing PR #${dup_number} (${dup_title})..."
            if ${execute}; then
                if gh pr comment "${dup_number}" --repo "${GH_REPO}" --body "${comment}"; then
                    echo "PR #${dup_number}: Close comment posted"
                else
                    echo "PR #${dup_number}: Failed to post close comment" >&2
                    had_error=1
                fi
            else
                echo "gh pr comment ${dup_number} --repo ${GH_REPO} --body 'Closing as duplicate: superseded by #${newest_number}. ...'"
            fi
        done
    done

    return "${had_error}"
}

usage() {
    echo "Usage: ${0} [--mode MODE] [--filter STRING] [--author USER] [--execute]" >&2
    echo "  --mode MODE:       Operation mode (default: summary)" >&2
    echo "    summary:           JSON array of PRs with pass/fail counts" >&2
    echo "    detail:            JSON array of PRs with full job lists" >&2
    echo "    approve:           Approve PRs where ALL test jobs passed (dry-run by default)" >&2
    echo "    restart:           Restart failed test jobs by commenting /test (dry-run by default)" >&2
    echo "    close-duplicates:  Close older PRs with same target branch and title filter (dry-run by default)" >&2
    echo "                       Requires --filter and --author. PRs matching the filter are grouped by" >&2
    echo "                       target branch; the newest PR in each group is kept, older ones are closed" >&2
    echo "  --filter STRING:   Only include PRs whose title contains STRING" >&2
    echo "  --author USER:     Only include PRs authored by USER" >&2
    echo "  --execute:         Actually post comments/close PRs (action modes). Without this flag, only shows what would be done." >&2
    exit 1
}

main() {
    local mode="summary"
    local filter=""
    local author=""
    local execute=false

    while [[ ${#} -gt 0 ]]; do
        case "${1}" in
            --mode)
                [[ ${#} -lt 2 ]] && { echo "Error: mode requires an argument" >&2; usage; }
                mode="${2}"; shift 2 ;;
            --filter)
                [[ ${#} -lt 2 ]] && { echo "Error: filter requires an argument" >&2; usage; }
                filter="${2}"; shift 2 ;;
            --author)
                [[ ${#} -lt 2 ]] && { echo "Error: author requires an argument" >&2; usage; }
                author="${2}"; shift 2 ;;
            --execute)
                execute=true; shift ;;
            -*) echo "Unknown option: ${1}" >&2; usage ;;
            *) echo "Unknown argument: ${1}" >&2; usage ;;
        esac
    done

    case "${mode}" in
        summary) mode_summary "${filter}" "${author}" ;;
        detail)  mode_detail "${filter}" "${author}" ;;
        approve) mode_approve "${filter}" "${author}" "${execute}" ;;
        restart) mode_restart "${filter}" "${author}" "${execute}" ;;
        close-duplicates) mode_close_duplicates "${filter}" "${author}" "${execute}" ;;
        *) echo "Error: Unknown mode '${mode}'" >&2; usage ;;
    esac
}

main "${@}"
