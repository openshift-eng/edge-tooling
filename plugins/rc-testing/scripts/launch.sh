#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GANGWAY_BIN="GANGWAY_BIN="${GANGWAY_BIN:-$(command -v gangway-cli || true)}"
GANGWAY_API="https://gangway-ci.apps.ci.l2s4.p1.openshiftapps.com"
SIPPY_API="https://sippy.dptools.openshift.org/api/jobs"
IMAGE_BASE="quay.io/openshift-release-dev/ocp-release"
ARCH="x86_64"
DELAY=10
OCP_RELEASE="4.22"

# Sippy search terms per topology
declare -A SIPPY_FILTER=(
    [tnf]="two-node-fencing"
    [tna]="two-node-arbiter"
    [sno]="-4vcpu"
)

to_image() {
    local version="$1"
    echo "${IMAGE_BASE}:${version}-${ARCH}"
}

usage() {
    echo "Usage: $0 <topology> <version> [options]"
    echo "       $0 <topology> --list"
    echo ""
    echo "Topologies: tnf, tna, sno"
    echo ""
    echo "Options:"
    echo "  --list              List available jobs (numbered) and exit (version not required)"
    echo "  --refresh           Update job file from Sippy and exit (version not required)"
    echo "  --job <selector>    Launch specific jobs: all, number (3), list (3,7,12), or pattern (recovery)"
    echo "  --initial <version> Set RELEASE_IMAGE_INITIAL for cross-upgrade jobs (e.g., upgrade-from-stable-4.21)"
    echo "  --run <name>        Custom run directory name (defaults to YYYY-MM-DD)"
    echo "  --dry-run           Print what would be launched without calling gangway-cli"
    echo ""
    echo "Examples:"
    echo "  $0 tna --list                                # list TNA jobs"
    echo "  $0 tna --refresh                             # update TNA job list from Sippy"
    echo "  $0 tnf 4.22.0-rc.0 --job all                 # launch all jobs"
    echo "  $0 tnf 4.22.0-rc.0 --job 3                  # launch job #3 only"
    echo "  $0 tnf 4.22.0-rc.0 --job 3,7,12             # launch jobs 3, 7, and 12"
    echo "  $0 tnf 4.22.0-rc.0 --job recovery           # launch all jobs matching 'recovery'"
    echo "  $0 tna 4.22.0-rc.0 --initial 4.21.0         # TNA cross-upgrade jobs use 4.21 as initial"
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
            RUN_NAME="$2"; shift 2 ;;
        --job)
            if [[ -z "${2:-}" || "$2" == -* ]]; then
                echo "Error: --job requires a selector argument (all, number, list, or pattern)"
                exit 1
            fi
            JOB_FILTER="$2"; shift 2 ;;
        --list)    LIST_ONLY=true; shift ;;
        --refresh) REFRESH=true; shift ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if ! $DRY_RUN; then
    if [[ ! -x "$GANGWAY_BIN" ]]; then
        echo "Error: gangway-cli not found or not executable at $GANGWAY_BIN"
        echo "Build it: cd ~/Projects/gangway-cli && go build -o gangway-cli ."
        exit 1
    fi
fi

if [[ -n "$RELEASE_IMAGE" ]]; then
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
fi

JOB_FILE="$SCRIPT_DIR/jobs/${TOPOLOGY}.txt"

if $REFRESH; then
    SEARCH_TERM="${SIPPY_FILTER[$TOPOLOGY]:-}"
    if [[ -z "$SEARCH_TERM" ]]; then
        echo "No Sippy filter configured for topology '$TOPOLOGY'."
        echo "Manage $JOB_FILE manually (one job name per line)."
        exit 0
    fi

    SIPPY_FILTER_JSON=$(printf '{"items":[{"columnField":"name","operatorValue":"contains","value":"%s"}],"linkOperator":"and"}' "$SEARCH_TERM")
    ENCODED_FILTER=$(printf '%s' "$SIPPY_FILTER_JSON" | jq -sRr '@uri')

    echo "Fetching $TOPOLOGY jobs from Sippy (release $OCP_RELEASE, filter: $SEARCH_TERM)..."
    SIPPY_RESPONSE=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 30 \
         "${SIPPY_API}?release=${OCP_RELEASE}&filter=${ENCODED_FILTER}&period=default&sortField=name&sort=asc")

    # Extract nightly job names, sort, tag cross-upgrade jobs
    echo "$SIPPY_RESPONSE" \
        | jq -r '.[].name' 2>/dev/null \
        | grep '^periodic-ci-openshift-release-main-nightly' \
        | sort \
        | while IFS= read -r name; do
            if [[ "$name" == *"upgrade-from-stable"* ]]; then
                echo "cross-upgrade:$name"
            else
                echo "$name"
            fi
        done > "$JOB_FILE"

    JOB_COUNT=$(grep -cv '^\s*$' "$JOB_FILE" || true)
    CROSS_COUNT=$(grep -c '^cross-upgrade:' "$JOB_FILE" || true)
    echo "Wrote $JOB_COUNT jobs to $JOB_FILE ($CROSS_COUNT cross-upgrade)"
    echo ""
    cat "$JOB_FILE"
    exit 0
fi

if [[ ! -f "$JOB_FILE" ]]; then
    echo "Error: no job file for topology '$TOPOLOGY' at $JOB_FILE"
    echo "Available: $(ls "$SCRIPT_DIR/jobs/" | sed 's/\.txt//' | tr '\n' ' ')"
    echo ""
    echo "Run './launch.sh $TOPOLOGY --refresh' to fetch from Sippy"
    exit 1
fi

if ! $LIST_ONLY && [[ -z "$RELEASE_IMAGE" ]]; then
    echo "Error: version is required (e.g., ./launch.sh $TOPOLOGY 4.22.0-rc.0)"
    exit 1
fi

if ! $LIST_ONLY && ! $REFRESH && [[ -n "$RELEASE_IMAGE" ]] && [[ -z "$JOB_FILTER" ]]; then
    echo "Error: --job is required. Use --job all to launch everything, or --job <selector> to pick."
    echo "       Run './launch.sh $TOPOLOGY --list' to see available jobs."
    exit 1
fi

if $LIST_ONLY; then
    echo "=== $TOPOLOGY jobs ==="
    local_num=0
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        local_num=$((local_num + 1))
        job_name="${line#cross-upgrade:}"
        if [[ "$line" == cross-upgrade:* ]]; then
            printf "%3d  [cross-upgrade] %s\n" "$local_num" "$job_name"
        else
            printf "%3d  %s\n" "$local_num" "$job_name"
        fi
    done < "$JOB_FILE"
    echo ""
    echo "Use --job all, --job <number>, --job <n,n,n>, or --job <pattern> to select"
    exit 0
fi

RUN_DIR="$SCRIPT_DIR/runs/${RUN_NAME}/${TOPOLOGY}"
mkdir -p "$RUN_DIR"

cat > "$SCRIPT_DIR/runs/${RUN_NAME}/config.env" <<EOF
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

TOTAL=$(grep -cv '^\s*$' "$JOB_FILE" || true)
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

while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    LINE_NUM=$((LINE_NUM + 1))

    # Handle cross-upgrade: prefix — these jobs need --initial set to a different version
    if [[ "$line" == cross-upgrade:* ]]; then
        JOB="${line#cross-upgrade:}"
        if [[ -z "${INITIAL_IMAGE:-}" ]]; then
            echo "  SKIPPED (cross-upgrade job requires --initial)"
            continue
        fi
        JOB_INITIAL="$INITIAL_IMAGE"
    else
        JOB="$line"
        JOB_INITIAL="$RELEASE_IMAGE"
    fi

    # Apply --job filter: by number or pattern
    if [[ -n "$SELECTED_NUMS" ]] && [[ "$SELECTED_NUMS" != *",$LINE_NUM,"* ]]; then
        continue
    fi
    if [[ -n "$JOB_PATTERN" ]] && [[ "$JOB" != *"$JOB_PATTERN"* ]]; then
        continue
    fi

    COUNT=$((COUNT + 1))
    echo "[$COUNT] $JOB"

    if $DRY_RUN; then
        echo "  [dry-run] would launch with --initial=$JOB_INITIAL --latest=$RELEASE_IMAGE"
    else
        if "$GANGWAY_BIN" \
            --api-url="$GANGWAY_API" \
            --initial "$JOB_INITIAL" \
            --latest "$RELEASE_IMAGE" \
            --job-name "$JOB" \
            --jobs-file-path="$RUN_DIR"; then
            echo "  launched"
        else
            echo "  FAILED to launch"
            FAILED=$((FAILED + 1))
        fi
        sleep "$DELAY"
    fi
done < "$JOB_FILE"

echo ""
echo "=== Done: $COUNT jobs launched, $FAILED failures ==="
echo "    Tracking JSONs: $RUN_DIR/"
