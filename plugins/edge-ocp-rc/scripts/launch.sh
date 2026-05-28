#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GANGWAY_BIN="${GANGWAY_BIN:-$(command -v gangway-cli || true)}"
GANGWAY_API="https://gangway-ci.apps.ci.l2s4.p1.openshiftapps.com"
GCSWEB_BASE="gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs"
SIPPY_API="https://sippy.dptools.openshift.org/api/jobs"
IMAGE_BASE="quay.io/openshift-release-dev/ocp-release"
ARCH="x86_64"
DELAY=10

# Sippy search terms per topology
sippy_filter_for() {
    case "$1" in
        tnf) echo "two-node-fencing" ;;
        tna) echo "two-node-arbiter" ;;
        sno) echo "-4vcpu" ;;
        *)   echo "" ;;
    esac
}

to_image() {
    local version="$1"
    if [[ "$version" == */* ]]; then
        echo "$version"
    else
        echo "${IMAGE_BASE}:${version}-${ARCH}"
    fi
}

detect_release() {
    local version_tag="${1:-}"

    for f in "$JOB_FILE" "$JOB_FILE_Z" "$JOB_FILE_Y"; do
        if [[ -f "$f" ]]; then
            local from_jobs
            from_jobs=$(awk -F'nightly-' '/nightly-/{split($2,a,"[^0-9.]"); print a[1]; exit}' "$f")
            if [[ -n "$from_jobs" ]]; then
                echo "$from_jobs"
                return
            fi
        fi
    done

    if [[ -n "$version_tag" && "$version_tag" =~ ^([0-9]+\.[0-9]+) ]]; then
        echo "${BASH_REMATCH[1]}"
        return
    fi

    echo ""
}

usage() {
    echo "Usage: $0 <topology> <version> [options]"
    echo "       $0 <topology> --list"
    echo ""
    echo "Topologies: tnf, tna, sno"
    echo ""
    echo "Options:"
    echo "  --list              List available jobs (numbered) and exit (version not required)"
    echo "  --refresh           Update job files from Sippy and exit (auto-detects release from existing jobs)"
    echo "  --job <selector>    Launch specific jobs: all, number (3), list (3,7,12), or pattern (recovery)"
    echo "  --relaunch-failed   Re-launch failed jobs from the latest run"
    echo "  --initial <version> Set RELEASE_IMAGE_INITIAL — required for z-stream and y-stream upgrade jobs"
    echo "  --run <name>        Custom run directory name (defaults to YYYY-MM-DD)"
    echo "  --dry-run           Print what would be launched without calling gangway-cli"
    echo ""
    echo "Examples:"
    echo "  $0 tna --list                                # list TNA jobs"
    echo "  $0 tna --refresh                             # update TNA job lists (release from existing jobs)"
    echo "  $0 tna 4.23.0-rc.0 --refresh                 # update TNA job lists for a new release"
    echo "  $0 tnf 4.22.0-rc.0 --job all                 # launch regular jobs only"
    echo "  $0 tnf 4.22.0-rc.0 --job 3                   # launch job #3 only"
    echo "  $0 tnf 4.22.0-rc.0 --job 3,7,12              # launch jobs 3, 7, and 12"
    echo "  $0 tnf 4.22.0-rc.0 --job recovery            # launch all jobs matching 'recovery'"
    echo "  $0 tna 4.22.0-rc.0 --initial 4.21.0 --job all  # launch all jobs including upgrades"
    echo "  $0 tnf 4.22.0-rc.1 --relaunch-failed         # re-launch failures from latest run"
    echo ""
    echo "When --initial is provided, z-stream and y-stream upgrade jobs are included."
    echo "Without --initial, upgrade jobs are skipped."
    echo ""
    echo "The version is expanded to: ${IMAGE_BASE}:<version>-${ARCH}"
    exit 1
}

[[ $# -lt 1 ]] && usage

TOPOLOGY="$1"
shift

# Parse version — may be absent if --list is used
RELEASE_IMAGE=""
if [[ $# -gt 0 && "$1" != --* ]]; then
    RELEASE_IMAGE=$(to_image "$1")
    shift
fi

INITIAL_IMAGE=""
RUN_NAME="$(date +%Y-%m-%d)"
DRY_RUN=false
LIST_ONLY=false
REFRESH=false
RELAUNCH_FAILED=false
JOB_FILTER=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --initial)
            if [[ -z "${2:-}" || "$2" == -* ]]; then
                echo "Error: --initial requires a version argument"
                exit 1
            fi
            INITIAL_IMAGE=$(to_image "$2"); shift 2 ;;
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
        --job)
            if [[ -z "${2:-}" || "$2" == -* ]]; then
                echo "Error: --job requires a selector argument (all, number, list, or pattern)"
                exit 1
            fi
            JOB_FILTER="$2"; shift 2 ;;
        --list)    LIST_ONLY=true; shift ;;
        --refresh) REFRESH=true; shift ;;
        --relaunch-failed) RELAUNCH_FAILED=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if ! $DRY_RUN && ! $LIST_ONLY && ! $REFRESH; then
    if [[ ! -f "$GANGWAY_BIN" || ! -x "$GANGWAY_BIN" ]]; then
        echo "Error: gangway-cli not found or not executable at $GANGWAY_BIN"
        echo "Set GANGWAY_BIN or ensure gangway-cli is in PATH."
        exit 1
    fi
fi

if [[ -n "$RELEASE_IMAGE" ]]; then
    if [[ "$RELEASE_IMAGE" == "${IMAGE_BASE}:"* ]]; then
        RELEASE_TAG="${RELEASE_IMAGE#*:}"
        echo "Verifying image tag: $RELEASE_TAG"
        TAG_EXISTS=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 20 \
             "https://quay.io/api/v1/repository/openshift-release-dev/ocp-release/tag/?specificTag=${RELEASE_TAG}" \
            | jq -r '.tags | length' 2>/dev/null || echo "unknown")
        if [[ "$TAG_EXISTS" == "0" ]]; then
            echo "Error: tag '$RELEASE_TAG' not found on quay.io"
            exit 1
        elif [[ "$TAG_EXISTS" == "unknown" ]]; then
            echo "Warning: could not verify tag (quay.io unreachable or jq missing), proceeding anyway"
        else
            echo "Image OK ($RELEASE_IMAGE)"
        fi
    else
        echo "Using custom image: $RELEASE_IMAGE (skipping quay.io verification)"
    fi
fi

# Three job files per topology: regular, z-stream upgrades, y-stream upgrades
JOB_FILE="$SCRIPT_DIR/jobs/${TOPOLOGY}.txt"
JOB_FILE_Z="$SCRIPT_DIR/jobs/${TOPOLOGY}-z-stream.txt"
JOB_FILE_Y="$SCRIPT_DIR/jobs/${TOPOLOGY}-y-stream.txt"

if $REFRESH; then
    SEARCH_TERM=$(sippy_filter_for "$TOPOLOGY")
    if [[ -z "$SEARCH_TERM" ]]; then
        echo "No Sippy filter configured for topology '$TOPOLOGY'."
        echo "Manage job files manually (one job name per line)."
        exit 0
    fi

    OCP_RELEASE=$(detect_release "${RELEASE_IMAGE#*:}")
    if [[ -z "$OCP_RELEASE" ]]; then
        echo "Error: cannot detect OCP release version."
        echo "Provide a version (e.g., ./launch.sh $TOPOLOGY 4.22.0-rc.0 --refresh)"
        exit 1
    fi

    SIPPY_FILTER_JSON=$(printf '{"items":[{"columnField":"name","operatorValue":"contains","value":"%s"}],"linkOperator":"and"}' "$SEARCH_TERM")
    ENCODED_FILTER=$(printf '%s' "$SIPPY_FILTER_JSON" | jq -sRr '@uri')

    echo "Fetching $TOPOLOGY jobs from Sippy (release $OCP_RELEASE, filter: $SEARCH_TERM)..."
    SIPPY_RESPONSE=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 30 \
         "${SIPPY_API}?release=${OCP_RELEASE}&filter=${ENCODED_FILTER}&period=default&sortField=name&sort=asc")

    # Clear existing files before writing
    rm -f "$JOB_FILE" "$JOB_FILE_Z" "$JOB_FILE_Y"

    # Sort jobs into separate files by type
    echo "$SIPPY_RESPONSE" \
        | jq -r '.[] | select(.current_runs > 0) | .name' 2>/dev/null \
        | { grep '^periodic-ci-openshift-release-main-nightly' || true; } \
        | sort \
        | while IFS= read -r name; do
            if [[ "$name" == *"upgrade-from-stable"* ]]; then
                echo "$name" >> "$JOB_FILE_Y"
            elif [[ "$name" == *"-upgrade"* ]]; then
                echo "$name" >> "$JOB_FILE_Z"
            else
                echo "$name" >> "$JOB_FILE"
            fi
        done

    JOB_COUNT=$(wc -l < "$JOB_FILE" 2>/dev/null || echo 0)
    Z_COUNT=$(wc -l < "$JOB_FILE_Z" 2>/dev/null || echo 0)
    Y_COUNT=$(wc -l < "$JOB_FILE_Y" 2>/dev/null || echo 0)
    TOTAL=$((JOB_COUNT + Z_COUNT + Y_COUNT))

    echo "Wrote $TOTAL jobs: $JOB_COUNT regular, $Z_COUNT z-stream, $Y_COUNT y-stream"
    echo ""
    if [[ "$JOB_COUNT" -gt 0 ]]; then
        echo "--- ${TOPOLOGY}.txt ---"
        cat "$JOB_FILE"
        echo ""
    fi
    if [[ "$Z_COUNT" -gt 0 ]]; then
        echo "--- ${TOPOLOGY}-z-stream.txt ---"
        cat "$JOB_FILE_Z"
        echo ""
    fi
    if [[ "$Y_COUNT" -gt 0 ]]; then
        echo "--- ${TOPOLOGY}-y-stream.txt ---"
        cat "$JOB_FILE_Y"
        echo ""
    fi
    exit 0
fi

has_job_files() {
    [[ -f "$JOB_FILE" && -s "$JOB_FILE" ]] && return 0
    [[ -f "$JOB_FILE_Z" && -s "$JOB_FILE_Z" ]] && return 0
    [[ -f "$JOB_FILE_Y" && -s "$JOB_FILE_Y" ]] && return 0
    return 1
}

if ! has_job_files; then
    echo "Error: no job files for topology '$TOPOLOGY'"
    echo "Expected: ${TOPOLOGY}.txt, ${TOPOLOGY}-z-stream.txt, or ${TOPOLOGY}-y-stream.txt in jobs/"
    echo ""
    echo "Run './launch.sh $TOPOLOGY --refresh' to fetch from Sippy"
    exit 1
fi

if ! $LIST_ONLY && [[ -z "$RELEASE_IMAGE" ]]; then
    echo "Error: version is required (e.g., ./launch.sh $TOPOLOGY 4.22.0-rc.0)"
    exit 1
fi

if ! $LIST_ONLY && ! $REFRESH && ! $RELAUNCH_FAILED && [[ -n "$RELEASE_IMAGE" ]] && [[ -z "$JOB_FILTER" ]]; then
    echo "Error: --job is required. Use --job all to launch everything, or --job <selector> to pick."
    echo "       Run './launch.sh $TOPOLOGY --list' to see available jobs."
    exit 1
fi

if ! $LIST_ONLY && [[ -n "$RELEASE_IMAGE" ]]; then
    REQUESTED_RELEASE=$(echo "${RELEASE_IMAGE#*:}" | grep -oE '^[0-9]+\.[0-9]+' || true)
    JOBS_RELEASE=$(detect_release "")
    if [[ -n "$REQUESTED_RELEASE" && -n "$JOBS_RELEASE" && "$REQUESTED_RELEASE" != "$JOBS_RELEASE" ]]; then
        echo "Error: job files are for $JOBS_RELEASE but you requested $REQUESTED_RELEASE"
        echo "Run './launch.sh $TOPOLOGY $REQUESTED_RELEASE --refresh' to update job files."
        exit 1
    fi
fi

# List jobs from a file with continuous numbering
# Args: file, section_label, counter_var_name (LINE_NUM is global)
list_file() {
    local file="$1"
    local label="$2"
    [[ ! -f "$file" || ! -s "$file" ]] && return

    echo ""
    echo "--- $label ---"
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        LINE_NUM=$((LINE_NUM + 1))
        printf "%3d  %s\\n" "$LINE_NUM" "$line"
    done < "$file"
}

if $LIST_ONLY; then
    LINE_NUM=0
    echo "=== $TOPOLOGY jobs ==="
    list_file "$JOB_FILE" "$TOPOLOGY"
    list_file "$JOB_FILE_Z" "$TOPOLOGY z-stream upgrades (--initial required)"
    list_file "$JOB_FILE_Y" "$TOPOLOGY y-stream upgrades (--initial required)"
    echo ""
    echo "Use --job all, --job <number>, --job <n,n,n>, or --job <pattern> to select"
    exit 0
fi

if $RELAUNCH_FAILED; then
    # shellcheck disable=SC2012
    PREV_RUN_DIR=$(ls -td "$SCRIPT_DIR/runs"/*/"$TOPOLOGY" 2>/dev/null | head -1 || true)

    if [[ -z "$PREV_RUN_DIR" || ! -d "$PREV_RUN_DIR" ]]; then
        echo "Error: no previous run found for '$TOPOLOGY'"
        exit 1
    fi

    echo "Scanning previous run: $(basename "$(dirname "$PREV_RUN_DIR")")"

    # Build combined job list with continuous numbering (same order as --list and launch)
    COMBINED_JOBS=$(mktemp)
    trap 'rm -f "$COMBINED_JOBS"' EXIT
    for f in "$JOB_FILE" "$JOB_FILE_Z" "$JOB_FILE_Y"; do
        [[ -f "$f" && -s "$f" ]] && cat "$f" >> "$COMBINED_JOBS"
    done

    FAILED_NUMS=""
    for f in "$PREV_RUN_DIR"/gangway_*.json; do
        [[ ! -e "$f" ]] && continue
        prev_job=$(jq -r '.[0].JobName // "unknown"' "$f" 2>/dev/null)
        prev_url=$(jq -r '.[0].JobURL // "no-url"' "$f" 2>/dev/null)

        if [[ "$prev_url" != "no-url" && "$prev_url" != "null" ]]; then
            prev_gcs="${prev_url/prow.ci.openshift.org\/view\/gs\//${GCSWEB_BASE}/}"
            prev_result=$(curl -s --max-time 10 "${prev_gcs}/finished.json" 2>/dev/null \
                | jq -r '.result // "unknown"' 2>/dev/null || echo "unknown")
            if [[ "$prev_result" == "FAILURE" || "$prev_result" == "ABORTED" ]]; then
                prev_num=0
                while IFS= read -r line; do
                    [[ -z "$line" ]] && continue
                    prev_num=$((prev_num + 1))
                    if [[ "$line" == "$prev_job" ]]; then
                        FAILED_NUMS="${FAILED_NUMS:+$FAILED_NUMS,}$prev_num"
                        break
                    fi
                done < "$COMBINED_JOBS"
            fi
        fi
    done

    rm -f "$COMBINED_JOBS"
    trap - EXIT

    if [[ -z "$FAILED_NUMS" ]]; then
        echo "No failures found in previous run — nothing to re-launch."
        exit 0
    fi

    echo "Re-launching failed jobs: $FAILED_NUMS"
    JOB_FILTER="$FAILED_NUMS"
fi

RUN_DIR="$SCRIPT_DIR/runs/${RUN_NAME}/${TOPOLOGY}"
mkdir -p "$RUN_DIR"

cat > "$RUN_DIR/config.env" <<EOF
RELEASE_IMAGE_LATEST=$RELEASE_IMAGE
RELEASE_IMAGE_INITIAL=${INITIAL_IMAGE:-same as latest}
TOPOLOGY=$TOPOLOGY
LAUNCHED=$(date -Iseconds)
EOF

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY
unset K8S_AUTH_PROXY

if [[ -z "${MY_APPCI_TOKEN:-}" ]] && ! $DRY_RUN; then
    echo "Error: MY_APPCI_TOKEN is not set"
    exit 1
fi

if ! $DRY_RUN; then
    echo "Verifying token against Gangway API..."
    HTTP_CODE=$(curl --silent --show-error --connect-timeout 5 --max-time 20 -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer ${MY_APPCI_TOKEN}" \
        "${GANGWAY_API}/v1/executions/" 2>/dev/null || echo "000")
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        echo "Error: token rejected by Gangway API (HTTP $HTTP_CODE). Refresh MY_APPCI_TOKEN."
        exit 1
    elif [[ "$HTTP_CODE" == "000" ]]; then
        echo "Error: cannot reach Gangway API at $GANGWAY_API. Check network/proxy."
        exit 1
    fi
    echo "Token OK (HTTP $HTTP_CODE)"
    echo ""
fi

LINE_NUM=0
COUNT=0
FAILED=0

# Parse --job filter into selected numbers (if numeric) or pattern
SELECTED_NUMS=""
JOB_PATTERN=""
if [[ -n "$JOB_FILTER" && "$JOB_FILTER" != "all" ]]; then
    if [[ "$JOB_FILTER" =~ ^[0-9,]+$ ]]; then
        SELECTED_NUMS=",$JOB_FILTER,"
    else
        JOB_PATTERN="$JOB_FILTER"
    fi
fi

echo "=== Launching $TOPOLOGY jobs against $RELEASE_IMAGE ==="
echo "    Run directory: $RUN_DIR"
echo ""

# Launch jobs from a single file
# Args: file, job_initial_image
launch_from_file() {
    local file="$1"
    local job_initial="$2"

    [[ ! -f "$file" || ! -s "$file" ]] && return

    while IFS= read -r JOB; do
        [[ -z "$JOB" ]] && continue
        LINE_NUM=$((LINE_NUM + 1))

        if [[ -n "$SELECTED_NUMS" ]] && [[ "$SELECTED_NUMS" != *",$LINE_NUM,"* ]]; then
            continue
        fi
        if [[ -n "$JOB_PATTERN" ]] && [[ "$JOB" != *"$JOB_PATTERN"* ]]; then
            continue
        fi

        COUNT=$((COUNT + 1))
        echo "[$COUNT] $JOB"

        if $DRY_RUN; then
            echo "  [dry-run] would launch with --initial=$job_initial --latest=$RELEASE_IMAGE"
        else
            if GANGWAY_OUTPUT=$("$GANGWAY_BIN" \
                --api-url="$GANGWAY_API" \
                --initial "$job_initial" \
                --latest "$RELEASE_IMAGE" \
                --job-name "$JOB" \
                --jobs-file-path="$RUN_DIR" 2>&1); then
                echo "  launched"
                sleep "$DELAY"
            else
                if echo "$GANGWAY_OUTPUT" | grep -q "500 Internal Server Error"; then
                    echo "  SKIPPED — job not found in Prow (HTTP 500). Remove from job file or run --refresh."
                else
                    echo "  FAILED to launch: $GANGWAY_OUTPUT"
                    FAILED=$((FAILED + 1))
                fi
            fi
        fi
    done < "$file"
}

# Regular jobs
launch_from_file "$JOB_FILE" "$RELEASE_IMAGE"

# Upgrade jobs — only when --initial is provided
if [[ -n "${INITIAL_IMAGE:-}" ]]; then
    launch_from_file "$JOB_FILE_Z" "$INITIAL_IMAGE"
    launch_from_file "$JOB_FILE_Y" "$INITIAL_IMAGE"
else
    Z_COUNT=$(wc -l < "$JOB_FILE_Z" 2>/dev/null || echo 0)
    Y_COUNT=$(wc -l < "$JOB_FILE_Y" 2>/dev/null || echo 0)
    SKIP_TOTAL=$((Z_COUNT + Y_COUNT))
    if [[ "$SKIP_TOTAL" -gt 0 ]]; then
        echo "Skipped $SKIP_TOTAL upgrade jobs (no --initial provided)"
    fi
fi

echo ""

if [[ "$COUNT" -eq 0 && -n "$JOB_FILTER" && "$JOB_FILTER" != "all" ]]; then
    echo "Error: no jobs matched selector '$JOB_FILTER'"
    echo "Run './launch.sh $TOPOLOGY --list' to see available jobs."
    exit 1
fi

echo "=== Done: $COUNT jobs launched, $FAILED failures ==="
echo "    Tracking JSONs: $RUN_DIR/"
