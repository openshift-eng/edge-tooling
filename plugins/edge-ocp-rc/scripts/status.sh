#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GCSWEB_BASE="gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs"
SIPPY_API="https://sippy.dptools.openshift.org/api/jobs"

ORIGINAL_ARGS=("$@")

usage() {
    echo "Usage: $0 [topology] [--run <name>] [--json] [--failed] [--logs] [--report]"
    echo ""
    echo "Shows status of launched Prow jobs by reading tracking JSONs."
    echo "If topology is omitted, shows all topologies in the run."
    echo ""
    echo "Options:"
    echo "  --json      Output structured JSON (for agentic consumption)"
    echo "  --failed    Show only failed/aborted jobs"
    echo "  --logs      Fetch failure reasons from Prow artifacts (for FAIL/ABORT jobs)"
    echo "  --classify  Classify failures using Sippy nightly pass rates (implies --logs)"
    echo "  --report    Output Jira-ready markdown (implies --logs)"
    echo "  --watch [N] Poll every N seconds (default 120) until all jobs complete"
    echo "  --run NAME  Use a specific run directory (defaults to latest)"
    echo ""
    echo "Examples:"
    echo "  $0              # Latest run, all topologies"
    echo "  $0 tnf          # Latest run, TNF only"
    echo "  $0 tna --run rc0  # Specific run, TNA only"
    echo "  $0 tnf --json   # JSON output for agentic workflow"
    echo "  $0 --failed     # Only show failures across all topologies"
    echo "  $0 tnf --failed --logs   # Failures with root cause"
    echo "  $0 tnf --failed --classify  # Failures classified against nightly history"
    echo "  $0 tnf --report          # Jira-ready markdown for all jobs"
    echo "  $0 tnf --report --failed # Jira-ready markdown, failures only"
    echo "  $0 tnf --watch           # Poll every 120s until all jobs complete"
    echo "  $0 tnf --watch 60        # Poll every 60s"
    echo ""
    echo "Exit code: 0 if all jobs passed or still running, 1 if any failed/aborted"
    exit 1
}

TOPOLOGY=""
RUN_NAME=""
JSON_OUTPUT=false
FAILED_ONLY=false
FETCH_LOGS=false
CLASSIFY=false
REPORT_MODE=false
WATCH_MODE=false
WATCH_INTERVAL=120

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run)
            if [[ -z "${2:-}" || "$2" == -* ]]; then
                echo "Error: --run requires a name argument"
                exit 1
            fi
            RUN_NAME="$2"
            if [[ ! "$RUN_NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
                echo "Error: --run may contain only letters, numbers, dot, underscore, and dash"
                exit 1
            fi
            shift 2 ;;
        --json)   JSON_OUTPUT=true; shift ;;
        --failed) FAILED_ONLY=true; shift ;;
        --logs)   FETCH_LOGS=true; shift ;;
        --classify) CLASSIFY=true; FETCH_LOGS=true; shift ;;
        --report) REPORT_MODE=true; FETCH_LOGS=true; shift ;;
        --watch)
            WATCH_MODE=true
            if [[ -n "${2:-}" && "$2" =~ ^[0-9]+$ ]]; then
                WATCH_INTERVAL="$2"; shift
            fi
            shift ;;
        --help)   usage ;;
        -*)       echo "Unknown option: $1"; usage ;;
        *)        TOPOLOGY="$1"; shift ;;
    esac
done

# Watch mode: re-exec without --watch/--json in a loop
if $WATCH_MODE; then
    PASS_ARGS=()
    prev_was_watch=false
    for arg in "${ORIGINAL_ARGS[@]}"; do
        if [[ "$arg" == "--watch" ]]; then
            prev_was_watch=true
            continue
        fi
        if $prev_was_watch && [[ "$arg" =~ ^[0-9]+$ ]]; then
            prev_was_watch=false
            continue
        fi
        prev_was_watch=false
        [[ "$arg" == "--json" ]] && continue
        PASS_ARGS+=("$arg")
    done

    while true; do
        rc=0
        output=$("$0" "${PASS_ARGS[@]}" 2>&1) || rc=$?
        echo "$output"
        if [[ "$rc" -ne 0 && "$rc" -ne 1 ]]; then
            exit "$rc"
        fi
        pending=$(echo "$output" | grep -oE '(Pending/Running: [0-9]+|[0-9]+ pending/running)' \
            | grep -oE '[0-9]+' | awk '{s+=$1} END {print s+0}')
        if [[ "$pending" -eq 0 ]]; then
            echo "All jobs complete."
            exit "$rc"
        fi
        echo "--- Next check in ${WATCH_INTERVAL}s (Ctrl-C to stop) ---"
        sleep "$WATCH_INTERVAL"
    done
fi

# Find run directory
if [[ -n "$RUN_NAME" ]]; then
    RUN_DIR="$SCRIPT_DIR/runs/$RUN_NAME"
else
    # shellcheck disable=SC2012
    RUN_DIR=$(ls -td "$SCRIPT_DIR/runs"/*/ 2>/dev/null | head -1 || true)
fi

if [[ -z "$RUN_DIR" || ! -d "$RUN_DIR" ]]; then
    echo "No runs found in $SCRIPT_DIR/runs/"
    exit 1
fi

RUN_DIR="${RUN_DIR%/}"
RUN_LABEL="$(basename "$RUN_DIR")"

load_config() {
    local config_file="$1"
    [[ ! -f "$config_file" ]] && return
    RELEASE_IMAGE=$(awk -F= '/^RELEASE_IMAGE_LATEST=/{print $2}' "$config_file" 2>/dev/null)
    LAUNCHED=$(awk -F= '/^LAUNCHED=/{print $2}' "$config_file" 2>/dev/null)
}

# Store per-topology config so rendering uses the correct values
save_topo_config() {
    local topo="$1"
    echo "${RELEASE_IMAGE}" > "$RESULTS_DIR/${topo}.image"
    echo "${LAUNCHED}" > "$RESULTS_DIR/${topo}.launched"
}

load_topo_config() {
    local topo="$1"
    RELEASE_IMAGE=$(cat "$RESULTS_DIR/${topo}.image" 2>/dev/null || true)
    LAUNCHED=$(cat "$RESULTS_DIR/${topo}.launched" 2>/dev/null || true)
}

RELEASE_IMAGE=""
LAUNCHED=""
load_config "$RUN_DIR/config.env"

HAS_FAILURES=false
RESULTS_DIR=$(mktemp -d)
trap 'rm -rf "$RESULTS_DIR"' EXIT

# Look up a job's number matching launch.sh --list numbering
job_num_lookup() {
    local topo="$1" target="$2"
    local n=0
    for f in "$SCRIPT_DIR/jobs/${topo}.txt" \
             "$SCRIPT_DIR/jobs/${topo}-z-stream.txt" \
             "$SCRIPT_DIR/jobs/${topo}-y-stream.txt"; do
        [[ ! -f "$f" ]] && continue
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            n=$((n + 1))
            if [[ "$line" == "$target" ]]; then
                echo "$n"
                return
            fi
        done < "$f"
    done
    echo "0"
}

# TSV columns: num, status, job_name, url, failure_reason, classification, pass_pct

fetch_failure_reason() {
    local url="$1"
    local gcs_base="${url/prow.ci.openshift.org\/view\/gs\//${GCSWEB_BASE}/}"
    local junit_url="${gcs_base}/artifacts/junit_operator.xml"

    local xml
    xml=$(curl -s --max-time 15 "$junit_url" 2>/dev/null) || true

    if [[ -z "$xml" ]]; then
        echo "unable to fetch logs"
        return
    fi

    local reason
    reason=$(echo "$xml" | python3 -c "
import sys, xml.etree.ElementTree as ET
try:
    tree = ET.parse(sys.stdin)
    for tc in tree.iter('testcase'):
        fail = tc.find('failure')
        if fail is None:
            continue
        name = tc.get('name', 'unknown')
        if ' - ' in name:
            step = name.split(' - ')[-1].replace(' container test', '')
        else:
            step = name
        if any(x in step.lower() for x in ['gather', 'collect', 'must-gather']):
            continue
        msg = (fail.get('message') or fail.text or '').strip()
        lines = [l.strip() for l in msg.split(chr(10)) if l.strip()]
        error_lines = [l for l in lines if 'error' in l.lower() or 'failed' in l.lower() or 'timed out' in l.lower()]
        reason = error_lines[0] if error_lines else (lines[-1] if lines else 'unknown')
        if len(reason) > 150:
            reason = reason[:147] + '...'
        print(f'{step}: {reason}')
        break
except Exception:
    print('unable to parse junit')
" 2>/dev/null) || true

    echo "${reason:-unable to fetch logs}"
}

classify_job() {
    local job_name="$1"
    local release="$2"

    local filter_json
    filter_json=$(printf '{"items":[{"columnField":"name","operatorValue":"equals","value":"%s"}]}' "$job_name")
    local encoded_filter
    encoded_filter=$(printf '%s' "$filter_json" | jq -sRr '@uri')

    local response
    response=$(curl -s --max-time 10 \
        "${SIPPY_API}?release=${release}&filter=${encoded_filter}" 2>/dev/null) || true

    if [[ -z "$response" ]]; then
        echo "UNKNOWN|0|0"
        return
    fi

    local pass_pct runs
    pass_pct=$(echo "$response" | jq -r '.[0].current_pass_percentage // 0' 2>/dev/null || echo "0")
    runs=$(echo "$response" | jq -r '.[0].current_runs // 0' 2>/dev/null || echo "0")

    local classification
    if [[ "$runs" == "0" || "$runs" == "null" ]]; then
        classification="NO-DATA"
    elif [[ "$pass_pct" == "0" ]]; then
        classification="KNOWN-FAIL"
    elif [[ "${pass_pct%.*}" -lt 50 ]]; then
        classification="FLAKY"
    elif [[ "${pass_pct%.*}" -lt 85 ]]; then
        classification="SOMETIMES-FAILS"
    else
        classification="REGRESSION"
    fi

    echo "${classification}|${pass_pct}|${runs}"
}

collect_topology() {
    local topo_dir="$1"
    local topo_name
    topo_name="$(basename "$topo_dir")"
    local results_file="$RESULTS_DIR/$topo_name.tsv"
    local json_files=("$topo_dir"/gangway_*.json)

    if [[ ! -e "${json_files[0]}" ]]; then
        touch "$results_file"
        return
    fi

    for f in "${json_files[@]}"; do
        local job_name url status reason=""

        job_name=$(jq -r '.[0].JobName // "unknown"' "$f" 2>/dev/null)
        local num
        num=$(job_num_lookup "$topo_name" "$job_name")
        url=$(jq -r '.[0].JobURL // "no-url"' "$f" 2>/dev/null)

        if [[ "$url" != "no-url" && "$url" != "null" ]]; then
            local gcs_url="${url/prow.ci.openshift.org\/view\/gs\//${GCSWEB_BASE}/}"
            local finished
            finished=$(curl -s --max-time 10 "${gcs_url}/finished.json" 2>/dev/null | jq -r '.result // "unknown"' 2>/dev/null || echo "unknown")
            case "$finished" in
                SUCCESS)  status="PASS" ;;
                FAILURE)  status="FAIL" ;;
                ABORTED)  status="ABORT" ;;
                unknown)  status="RUNNING" ;;
                *)        status="RUNNING" ;;
            esac
        else
            status="NO-URL"
        fi

        if $FETCH_LOGS && [[ "$status" == "FAIL" || "$status" == "ABORT" ]]; then
            reason=$(fetch_failure_reason "$url")
        fi

        local classification="" pass_pct=""
        if $CLASSIFY && [[ "$status" == "FAIL" || "$status" == "ABORT" ]]; then
            local release
            release=$(echo "$job_name" | awk -F'nightly-' '/nightly-/{split($2,a,"[^0-9.]"); print a[1]}')
            if [[ -n "$release" ]]; then
                local classify_result
                classify_result=$(classify_job "$job_name" "$release")
                classification="${classify_result%%|*}"
                pass_pct=$(echo "$classify_result" | cut -d'|' -f2)
            fi
        fi

        printf '%d\t%s\t%s\t%s\t%s\t%s\t%s\n' "$num" "$status" "$job_name" "$url" "$reason" "$classification" "$pass_pct" >> "$results_file"
    done

    sort -t$'\t' -k1,1n -o "$results_file" "$results_file"
}

render_table() {
    local topo_name="$1"
    local results_file="$RESULTS_DIR/$topo_name.tsv"

    echo "--- $topo_name ---"

    if [[ ! -s "$results_file" ]]; then
        echo "  (no jobs launched)"
        return
    fi

    local display_file="$results_file"
    if $FAILED_ONLY; then
        display_file="$RESULTS_DIR/${topo_name}_filtered.tsv"
        grep -E $'^[0-9]+\t(FAIL|ABORT)\t' "$results_file" > "$display_file" 2>/dev/null || true
    fi

    printf "%-3s %-12s %-80s %s\\n" "#" "Status" "Job" "URL"
    echo "    $(printf '%.0s-' {1..120})"

    if [[ -s "$display_file" ]]; then
        while IFS=$'\t' read -r num status job_name url reason classification pass_pct; do
            if $CLASSIFY && [[ -n "$classification" ]]; then
                local pct_display
                pct_display=$(printf '%.0f' "$pass_pct" 2>/dev/null || echo "?")
                printf "%-3d %-12s %-20s %-70s %s\\n" "$num" "$status" "${classification} (${pct_display}%)" "$job_name" "$url"
            else
                printf "%-3d %-12s %-80s %s\\n" "$num" "$status" "$job_name" "$url"
            fi
            if $FETCH_LOGS && [[ -n "$reason" ]]; then
                printf "                 → %s\\n" "$reason"
            fi
        done < "$display_file"
    elif $FAILED_ONLY; then
        echo "  (no failures)"
    fi

    echo ""

    local total pass fail pending
    total=$(wc -l < "$results_file")
    pass=$(grep -cE $'^[0-9]+\tPASS\t' "$results_file" || true)
    fail=$(grep -cE $'^[0-9]+\t(FAIL|ABORT)\t' "$results_file" || true)
    pending=$((total - pass - fail))

    echo "    Total: $total | Pass: $pass | Fail: $fail | Pending/Running: $pending"
}

build_topology_json() {
    local topo_name="$1"
    local results_file="$RESULTS_DIR/$topo_name.tsv"

    if [[ ! -s "$results_file" ]]; then
        jq -n '{total:0, pass:0, fail:0, pending:0, jobs:[]}'
        return
    fi

    local total pass fail pending
    total=$(wc -l < "$results_file")
    pass=$(grep -cE $'^[0-9]+\tPASS\t' "$results_file" || true)
    fail=$(grep -cE $'^[0-9]+\t(FAIL|ABORT)\t' "$results_file" || true)
    pending=$((total - pass - fail))

    local source_file="$results_file"
    if $FAILED_ONLY; then
        source_file="$RESULTS_DIR/${topo_name}_json_filtered.tsv"
        grep -E $'^[0-9]+\t(FAIL|ABORT)\t' "$results_file" > "$source_file" 2>/dev/null || true
    fi

    local jobs_json="[]"
    if [[ -s "$source_file" ]]; then
        jobs_json=$(while IFS=$'\t' read -r num status job_name url reason classification pass_pct; do
            local args=(--argjson num "$num" --arg status "$status" --arg job "$job_name" --arg url "$url")
            local fields="number:\$num, job:\$job, status:\$status, url:\$url"

            if $FETCH_LOGS && [[ -n "$reason" ]]; then
                args+=(--arg reason "$reason")
                fields+=", failure_reason:\$reason"
            fi
            if $CLASSIFY && [[ -n "$classification" ]]; then
                local pct_num
                pct_num=$(printf '%.1f' "$pass_pct" 2>/dev/null || echo "0")
                args+=(--arg classification "$classification" --argjson nightly_pass_rate "$pct_num")
                fields+=", classification:\$classification, nightly_pass_rate:\$nightly_pass_rate"
            fi

            jq -n "${args[@]}" "{${fields}}"
        done < "$source_file" | jq -s '.')
    fi

    jq -n --argjson total "$total" --argjson pass "$pass" --argjson fail "$fail" \
        --argjson pending "$pending" --argjson jobs "$jobs_json" \
        '{total:$total, pass:$pass, fail:$fail, pending:$pending, jobs:$jobs}'
}

render_report() {
    local topo_name="$1"
    local results_file="$RESULTS_DIR/$topo_name.tsv"

    echo "## RC Testing: ${RUN_LABEL} — ${topo_name}"
    echo ""
    echo "**Release**: \`${RELEASE_IMAGE}\`"
    echo "**Date**: ${LAUNCHED}"
    echo ""

    if [[ ! -s "$results_file" ]]; then
        echo "(no jobs launched)"
        return
    fi

    local display_file="$results_file"
    if $FAILED_ONLY; then
        display_file="$RESULTS_DIR/${topo_name}_report_filtered.tsv"
        grep -E $'^[0-9]+\t(FAIL|ABORT)\t' "$results_file" > "$display_file" 2>/dev/null || true
    fi

    if $CLASSIFY; then
        echo "| # | Result | Classification | Job | Notes |"
        echo "|---|--------|---------------|-----|-------|"
    else
        echo "| # | Result | Job | Notes |"
        echo "|---|--------|-----|-------|"
    fi

    if [[ -s "$display_file" ]]; then
        while IFS=$'\t' read -r num status job_name url reason classification pass_pct; do
            local job_link="[${job_name}](${url})"
            local note="${reason:-}"
            if $CLASSIFY && [[ -n "$classification" ]]; then
                local pct_display
                pct_display=$(printf '%.0f' "$pass_pct" 2>/dev/null || echo "?")
                echo "| ${num} | ${status} | ${classification} (${pct_display}%) | ${job_link} | ${note} |"
            else
                echo "| ${num} | ${status} | ${job_link} | ${note} |"
            fi
        done < "$display_file"
    fi

    echo ""

    local total pass fail pending
    total=$(wc -l < "$results_file")
    pass=$(grep -cE $'^[0-9]+\tPASS\t' "$results_file" || true)
    fail=$(grep -cE $'^[0-9]+\t(FAIL|ABORT)\t' "$results_file" || true)
    pending=$((total - pass - fail))

    echo "**Summary**: ${pass}/${total} passed, ${fail} failed, ${pending} pending/running"
}

# Determine which topologies to process
TOPO_LIST=()
if [[ -n "$TOPOLOGY" ]]; then
    TOPO_DIR="$RUN_DIR/$TOPOLOGY"
    if [[ ! -d "$TOPO_DIR" ]]; then
        echo "No results for topology '$TOPOLOGY' in $RUN_LABEL"
        exit 1
    fi
    TOPO_LIST=("$TOPOLOGY")
else
    for topo_dir in "$RUN_DIR"/*/; do
        [[ ! -d "$topo_dir" ]] && continue
        topo_name="$(basename "$topo_dir")"
        TOPO_LIST+=("$topo_name")
    done
fi

RUN_RELEASE_IMAGE="$RELEASE_IMAGE"
RUN_LAUNCHED="$LAUNCHED"

# Collect results for all topologies
for topo_name in "${TOPO_LIST[@]}"; do
    RELEASE_IMAGE="$RUN_RELEASE_IMAGE"
    LAUNCHED="$RUN_LAUNCHED"
    if [[ -f "$RUN_DIR/$topo_name/config.env" ]]; then
        load_config "$RUN_DIR/$topo_name/config.env"
    fi
    save_topo_config "$topo_name"
    collect_topology "$RUN_DIR/$topo_name"
done

RELEASE_IMAGE="$RUN_RELEASE_IMAGE"
LAUNCHED="$RUN_LAUNCHED"

# Check for failures
for topo_name in "${TOPO_LIST[@]}"; do
    results_file="$RESULTS_DIR/$topo_name.tsv"
    if [[ -s "$results_file" ]] && grep -qE $'^[0-9]+\t(FAIL|ABORT)\t' "$results_file"; then
        HAS_FAILURES=true
        break
    fi
done

# Render output
if $REPORT_MODE; then
    for topo_name in "${TOPO_LIST[@]}"; do
        load_topo_config "$topo_name"
        render_report "$topo_name"
        echo ""
    done
elif $JSON_OUTPUT; then
    TOPO_JSON="{}"
    for topo_name in "${TOPO_LIST[@]}"; do
        load_topo_config "$topo_name"
        topo_data=$(build_topology_json "$topo_name")
        TOPO_JSON=$(echo "$TOPO_JSON" | jq --arg key "$topo_name" --argjson val "$topo_data" '. + {($key): $val}')
    done

    jq -n --arg run "$RUN_LABEL" --arg image "$RELEASE_IMAGE" --arg launched "$LAUNCHED" \
        --argjson has_failures "$HAS_FAILURES" --argjson topologies "$TOPO_JSON" \
        '{run:$run, release_image:$image, launched:$launched, has_failures:$has_failures, topologies:$topologies}'
else
    echo "=== Run: $RUN_LABEL ==="
    [[ -n "$RELEASE_IMAGE" ]] && echo "    RELEASE_IMAGE_LATEST=$RELEASE_IMAGE"
    [[ -n "$LAUNCHED" ]] && echo "    LAUNCHED=$LAUNCHED"
    echo ""

    for topo_name in "${TOPO_LIST[@]}"; do
        render_table "$topo_name"
        echo ""
    done
fi

if $HAS_FAILURES; then
    exit 1
fi
