#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GANGWAY_BIN="${GANGWAY_BIN:-$(command -v gangway-cli || true)}"
GANGWAY_API="https://gangway-ci.apps.ci.l2s4.p1.openshiftapps.com"
GCSWEB_BASE="gcs.ci.openshift.org/gcs"
SIPPY_API="https://sippy.dptools.openshift.org/api/jobs"
IMAGE_BASE="quay.io/openshift-release-dev/ocp-release"
ARCH="x86_64"
DELAY="${DELAY:-10}"
STAGGER=0             # seconds between jobs (0 = use DELAY only)
WAVE_SIZE=0            # jobs per wave (0 = no wave batching)
WAVE_DELAY=3600        # seconds to wait between waves (default 1h)

# Sippy search terms per topology
sippy_filter_for() {
    case "$1" in
        tnf) echo "two-node-fencing" ;;
        tna) echo "two-node-arbiter" ;;
        sno) echo "-4vcpu" ;;
        *)   echo "" ;;
    esac
}

human_duration() {
    local secs="$1"
    if (( secs >= 3600 && secs % 3600 == 0 )); then
        echo "${secs}s ($((secs / 3600))h)"
    elif (( secs >= 60 && secs % 60 == 0 )); then
        echo "${secs}s ($((secs / 60))m)"
    else
        echo "${secs}s"
    fi
}

to_image() {
    local version="$1"
    if [[ "$version" == */* ]]; then
        echo "$version"
    else
        echo "${IMAGE_BASE}:${version}-${ARCH}"
    fi
}

release_from_job_name() {
    local name="$1"
    # Bash's regex match uses the first occurrence in the name. This preserves
    # the release of a nightly job even if a later segment mentions a version.
    if [[ "$name" =~ (nightly-|release-)([0-9]+\.[0-9]+) ]]; then
        echo "${BASH_REMATCH[2]}"
    fi
}

# Extract the version tag from an image reference. The tag follows the last
# colon in the final path component; stripping from the first colon of the
# whole reference would mistake a registry port (registry:5000/ocp:4.22.0-rc.0)
# for the tag separator.
image_tag() {
    local component="${1##*/}"
    printf '%s' "${component#*:}"
}

detect_release() {
    local version_tag="${1:-}"

    if [[ -n "$version_tag" && "$version_tag" =~ ^([0-9]+\.[0-9]+) ]]; then
        echo "${BASH_REMATCH[1]}"
        return
    fi

    local -a releases=()
    for d in "$SCRIPT_DIR"/jobs/*/; do
        [[ -d "$d" ]] || continue
        # A directory holding only the LVMS seed entry is not evidence of the
        # user's current release — the seed must not choose a release for
        # --refresh. Only regular or upgrade lists establish a release.
        local has_job_lists=false f
        for f in "$d"/*.txt; do
            [[ -f "$f" ]] || continue
            case "$(basename "$f")" in
                *-lvms.txt) ;;
                *) has_job_lists=true; break ;;
            esac
        done
        $has_job_lists && releases+=("$(basename "$d")")
    done

    if [[ ${#releases[@]} -eq 1 ]]; then
        echo "${releases[0]}"
        return
    elif [[ ${#releases[@]} -gt 1 ]]; then
        echo "Error: multiple releases found (${releases[*]}). Provide an explicit version." >&2
        return 1
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
    echo "  --refresh           Update job files from Sippy (infer release from existing regular/upgrade jobs)"
    echo "  --job <selector>    Launch specific jobs: all, number (3), list (3,7,12), or pattern (lvms)"
    echo "  --relaunch-failed   Re-launch failed jobs from the latest run"
    echo "  --stagger <secs>    Delay between each job launch (default: 10s API throttle only)"
    echo "  --wave-size <N>     Group into waves of N jobs, pause between waves"
    echo "  --wave-delay <secs> Seconds between waves (default: 3600 = 1 hour)"
    echo "                      --stagger and --wave-size can be combined: stagger"
    echo "                      applies within a wave, wave-delay applies between waves"
    echo "  --initial <version> Set RELEASE_IMAGE_INITIAL — required for z-stream and y-stream upgrade jobs"
    echo "  --run <name>        Custom run directory name (defaults to YYYY-MM-DD)"
    echo "  --dry-run           Print what would be launched without calling gangway-cli"
    echo ""
    echo "Examples:"
    echo "  $0 tna --list                                # list TNA jobs"
    echo "  $0 tna --refresh                             # update TNA lists using existing jobs' release"
    echo "  $0 tna 4.23.0-rc.0 --refresh                 # update TNA job lists for a new release"
    echo "  $0 tnf 4.22.0-rc.0 --job all                 # launch regular jobs only"
    echo "  $0 tnf 4.22.0-rc.0 --job 3                   # launch job #3 only"
    echo "  $0 tnf 4.22.0-rc.0 --job 3,7,12              # launch jobs 3, 7, and 12"
    echo "  $0 tnf 4.22.0-rc.0 --job recovery            # launch all jobs matching 'recovery'"
    echo "  $0 tnf 5.0.0-rc.5 --job lvms                # launch the TNF LVMS lane"
    echo "  $0 tna 4.22.0-rc.0 --initial 4.21.0 --job all  # launch all jobs including upgrades"
    echo "  $0 tnf 4.22.0-rc.1 --relaunch-failed         # re-launch failures from latest run"
    # Spread jobs 10 minutes apart (like a mini-Prow cron stagger):
    echo "  $0 tnf 5.0.0-rc.0 --job all --stagger 600"

    # Launch in waves of 8, with 1h between waves:
    echo "  $0 tnf 5.0.0-rc.0 --job all --wave-size 8"

    # Waves of 10, 45 minutes between:
    echo "  $0 tnf 5.0.0-rc.0 --job all --wave-size 10 --wave-delay 2700"

    # Original behavior (unchanged):
    echo "  $0 tnf 5.0.0-rc.0 --job all"
    echo ""
    echo "Job files are stored under jobs/<release>/ (e.g., jobs/4.22/tnf.txt)."
    echo "Use scripts/collect.sh to fetch all topologies and releases at once."
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
        --stagger)
            if [[ -z "${2:-}" || ! "${2:-}" =~ ^(0|[1-9][0-9]*)$ ]]; then
                echo "Error: --stagger must be a non-negative decimal integer"
                exit 1
            fi
            STAGGER="$2"; shift 2 ;;
        --wave-size)
            if [[ -z "${2:-}" || ! "${2:-}" =~ ^(0|[1-9][0-9]*)$ ]]; then
                echo "Error: --wave-size must be a non-negative decimal integer"
                exit 1
            fi
            WAVE_SIZE="$2"; shift 2 ;;
        --wave-delay)
            if [[ -z "${2:-}" || ! "${2:-}" =~ ^(0|[1-9][0-9]*)$ ]]; then
                echo "Error: --wave-delay must be a non-negative decimal integer"
                exit 1
            fi
            WAVE_DELAY="$2"; shift 2 ;;
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

if [[ -n "$RELEASE_IMAGE" ]] && ! $REFRESH; then
    if [[ "$RELEASE_IMAGE" == "${IMAGE_BASE}:"* ]]; then
        RELEASE_TAG="$(image_tag "$RELEASE_IMAGE")"
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

# One-time migration: a checkout from before the release-directory layout has
# flat jobs/*.txt files. Move them into jobs/<release>/ (inferred from each
# file's job names) so existing checkouts keep working without any manual step.
# This must also run when release directories already exist: a checkout updated
# to this layout ships the committed jobs/5.0 LVMS seed, so legacy flat lists
# would otherwise be ignored forever. Nonconflicting flat files are migrated;
# a flat file whose destination already exists never overwrites it.
migrate_flat_job_layout() {
    local -a flat_files=()
    local f
    for f in "$SCRIPT_DIR"/jobs/*.txt; do
        [[ -f "$f" ]] && flat_files+=("$f")
    done
    [[ ${#flat_files[@]} -eq 0 ]] && return 0

    echo "Migrating flat job files to the jobs/<release>/ layout..." >&2
    local file line job_release dest
    for file in "${flat_files[@]}"; do
        job_release=""
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            job_release=$(release_from_job_name "$line")
            [[ -n "$job_release" ]] && break
        done < "$file"
        if [[ -z "$job_release" ]]; then
            echo "Warning: cannot infer a release for ${file##*/}; leaving it in place." >&2
            continue
        fi
        dest="$SCRIPT_DIR/jobs/$job_release/${file##*/}"
        if [[ -e "$dest" ]]; then
            echo "Warning: jobs/$job_release/${file##*/} already exists; keeping it and ignoring the flat ${file##*/}." >&2
            continue
        fi
        mkdir -p "$SCRIPT_DIR/jobs/$job_release"
        mv "$file" "$dest"
        echo "  ${file##*/} -> jobs/$job_release/" >&2
    done
}
migrate_flat_job_layout

# Detect release and set up job file paths under jobs/{release}/.
# --list without a version enumerates every release directory instead, so it
# must not fail when several releases coexist.
OCP_RELEASE=""
if ! $LIST_ONLY || [[ -n "$RELEASE_IMAGE" ]]; then
    if ! OCP_RELEASE=$(detect_release "$(image_tag "$RELEASE_IMAGE")"); then
        exit 1
    fi
fi

# Sippy-managed topology lists plus a TNF LVMS entry seeded for new lanes.
JOB_DIR="$SCRIPT_DIR/jobs/${OCP_RELEASE}"
JOB_FILE="$JOB_DIR/${TOPOLOGY}.txt"
JOB_FILE_LVMS="$JOB_DIR/${TOPOLOGY}-lvms.txt"
JOB_FILE_Z="$JOB_DIR/${TOPOLOGY}-z-stream.txt"
JOB_FILE_Y="$JOB_DIR/${TOPOLOGY}-y-stream.txt"

if $REFRESH; then
    SEARCH_TERM=$(sippy_filter_for "$TOPOLOGY")
    if [[ -z "$SEARCH_TERM" ]]; then
        echo "No Sippy filter configured for topology '$TOPOLOGY'."
        echo "Manage job files manually (one job name per line)."
        exit 0
    fi

    if [[ -z "$OCP_RELEASE" ]]; then
        echo "Error: cannot detect OCP release version."
        echo "Provide a version (e.g., ./launch.sh $TOPOLOGY 4.22.0-rc.0 --refresh)"
        exit 1
    fi

    mkdir -p "$JOB_DIR"

    SIPPY_FILTER_JSON=$(printf '{"items":[{"columnField":"name","operatorValue":"contains","value":"%s"}],"linkOperator":"and"}' "$SEARCH_TERM")
    ENCODED_FILTER=$(printf '%s' "$SIPPY_FILTER_JSON" | jq -sRr '@uri')

    echo "Fetching $TOPOLOGY jobs from Sippy (release $OCP_RELEASE, filter: $SEARCH_TERM)..."
    SIPPY_RESPONSE=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 30 \
         "${SIPPY_API}?release=${OCP_RELEASE}&filter=${ENCODED_FILTER}&period=default&sortField=name&sort=asc")

    if [[ "$TOPOLOGY" == "tnf" ]]; then
        # The regular TNF query excludes lvm-operator jobs. Refresh the LVMS
        # entry separately, including registered jobs without run history.
        LVMS_FILTER_JSON='{"items":[{"columnField":"name","operatorValue":"contains","value":"tnf-lvms-mno-qe-integration-tests"}],"linkOperator":"and"}'
        LVMS_ENCODED_FILTER=$(printf '%s' "$LVMS_FILTER_JSON" | jq -sRr '@uri')
        if LVMS_RESPONSE=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 30 \
             "${SIPPY_API}?release=${OCP_RELEASE}&filter=${LVMS_ENCODED_FILTER}&period=default&sortField=name&sort=asc"); then
            LVMS_JOBS=$(printf '%s' "$LVMS_RESPONSE" | jq -r --arg release "$OCP_RELEASE" \
                '.[] | select(.name | startswith("periodic-ci-openshift-lvm-operator-release-" + $release + "-nightly-") and endswith("-tnf-lvms-mno-qe-integration-tests")) | .name' | sort -u)
            if [[ -n "$LVMS_JOBS" ]]; then
                printf '%s\n' "$LVMS_JOBS" > "$JOB_FILE_LVMS"
                echo "Updated TNF LVMS jobs from Sippy for release $OCP_RELEASE"
            else
                echo "No TNF LVMS job found in Sippy for release $OCP_RELEASE; keeping the tracked entry"
            fi
        else
            echo "Warning: could not refresh TNF LVMS jobs from Sippy; keeping the tracked entry" >&2
        fi
    fi

    # Stage beside the destinations so the moves below stay same-filesystem
    # renames (atomic) even when TMPDIR is on another filesystem.
    tmp_file=$(mktemp "${JOB_FILE}.XXXXXX") tmp_z=$(mktemp "${JOB_FILE_Z}.XXXXXX") tmp_y=$(mktemp "${JOB_FILE_Y}.XXXXXX")
    trap 'rm -f "$tmp_file" "$tmp_z" "$tmp_y"' EXIT

    # Sort jobs into separate files by type
    echo "$SIPPY_RESPONSE" \
        | jq -r '.[] | select(.current_runs > 0) | .name' 2>/dev/null \
        | { grep '^periodic-ci-openshift-release-main-nightly' || true; } \
        | sort \
        | while IFS= read -r name; do
            if [[ "$name" == *"upgrade-from-stable"* ]]; then
                echo "$name" >> "$tmp_y"
            elif [[ "$name" == *"-upgrade"* ]]; then
                echo "$name" >> "$tmp_z"
            else
                echo "$name" >> "$tmp_file"
            fi
        done

    mv "$tmp_file" "$JOB_FILE"; mv "$tmp_z" "$JOB_FILE_Z"; mv "$tmp_y" "$JOB_FILE_Y"
    trap - EXIT

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
    [[ -f "$JOB_FILE_LVMS" && -s "$JOB_FILE_LVMS" ]] && return 0
    [[ -f "$JOB_FILE_Z" && -s "$JOB_FILE_Z" ]] && return 0
    [[ -f "$JOB_FILE_Y" && -s "$JOB_FILE_Y" ]] && return 0
    return 1
}

if ! $LIST_ONLY && ! has_job_files; then
    echo "Error: no job files for topology '$TOPOLOGY' (release ${OCP_RELEASE:-unknown})"
    echo "Expected files in jobs/${OCP_RELEASE:-<release>}/"
    echo ""
    echo "Run './launch.sh $TOPOLOGY <version> --refresh' or './scripts/collect.sh' to fetch from Sippy"
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

check_job_release() {
if ! $LIST_ONLY && [[ -n "$RELEASE_IMAGE" ]]; then
    REQUESTED_RELEASE=$(image_tag "$RELEASE_IMAGE" | grep -oE '^[0-9]+\.[0-9]+' || true)
    if [[ "$TOPOLOGY" == "tnf" && "$JOB_FILTER" == "lvms" && -z "$REQUESTED_RELEASE" ]]; then
        echo "Error: cannot determine the payload release for the TNF LVMS lane"
        exit 1
    fi
    if [[ "$JOB_FILTER" =~ ^[0-9,]+$ ]]; then
        # Numbering must match --list, including the tracked LVMS file.
        JOB_NUMBER=0
        for file in "$JOB_FILE" "$JOB_FILE_LVMS" "$JOB_FILE_Z" "$JOB_FILE_Y"; do
            [[ ! -f "$file" ]] && continue
            skip_upgrade_validation=false
            if [[ -z "${INITIAL_IMAGE:-}" && ( "$file" == "$JOB_FILE_Z" || "$file" == "$JOB_FILE_Y" ) ]]; then
                skip_upgrade_validation=true
            fi
            while IFS= read -r job; do
                [[ -z "$job" ]] && continue
                JOB_NUMBER=$((JOB_NUMBER + 1))
                [[ ",$JOB_FILTER," != *",$JOB_NUMBER,"* ]] && continue
                if $skip_upgrade_validation; then
                    continue
                fi
                job_release=$(release_from_job_name "$job")
                if [[ "$job" == *"-tnf-lvms-mno-qe-integration-tests" && -z "$REQUESTED_RELEASE" ]]; then
                    echo "Error: cannot determine the payload release for TNF LVMS job #$JOB_NUMBER"
                    exit 1
                fi
                if [[ "$job" == *"-tnf-lvms-mno-qe-integration-tests" && -z "$job_release" ]]; then
                    echo "Error: cannot determine the release of TNF LVMS job #$JOB_NUMBER"
                    exit 1
                fi
                if [[ -n "$job_release" && -n "$REQUESTED_RELEASE" && "$job_release" != "$REQUESTED_RELEASE" ]]; then
                    echo "Error: selected job #$JOB_NUMBER targets $job_release, but you requested $REQUESTED_RELEASE"
                    exit 1
                fi
            done < "$file"
        done
        JOBS_RELEASE="$REQUESTED_RELEASE"
    elif [[ "$TOPOLOGY" == "tnf" && "$JOB_FILTER" == "lvms" ]]; then
        if [[ ! -s "$JOB_FILE_LVMS" ]]; then
            echo "Error: no TNF LVMS job is available; run './launch.sh tnf $REQUESTED_RELEASE --refresh'"
            exit 1
        fi
        JOBS_RELEASE=$(release_from_job_name "$(head -1 "$JOB_FILE_LVMS")")
        if [[ -z "$JOBS_RELEASE" ]]; then
            echo "Error: cannot determine the release of the TNF LVMS job; run --refresh"
            exit 1
        fi
    elif [[ -n "$JOB_FILTER" && "$JOB_FILTER" != "all" ]]; then
        # A text selector may match a job in a different file than the first
        # Sippy list, so validate the matching jobs rather than that list.
        for file in "$JOB_FILE" "$JOB_FILE_LVMS" "$JOB_FILE_Z" "$JOB_FILE_Y"; do
            [[ ! -f "$file" ]] && continue
            if [[ -z "${INITIAL_IMAGE:-}" && ( "$file" == "$JOB_FILE_Z" || "$file" == "$JOB_FILE_Y" ) ]]; then
                continue
            fi
            while IFS= read -r job; do
                [[ -z "$job" || "$job" != *"$JOB_FILTER"* ]] && continue
                job_release=$(release_from_job_name "$job")
                if [[ "$job" == *"-tnf-lvms-mno-qe-integration-tests" && -z "$REQUESTED_RELEASE" ]]; then
                    echo "Error: cannot determine the payload release for the TNF LVMS lane"
                    exit 1
                fi
                if [[ "$job" == *"-tnf-lvms-mno-qe-integration-tests" && -z "$job_release" ]]; then
                    echo "Error: cannot determine the release of the TNF LVMS job; run --refresh"
                    exit 1
                fi
                if [[ -n "$job_release" && -n "$REQUESTED_RELEASE" && "$job_release" != "$REQUESTED_RELEASE" ]]; then
                    # Broad patterns can match a stale LVMS seed alongside
                    # compatible jobs. Let job_selected report that lane as skipped.
                    if [[ "$job" == *"-tnf-lvms-mno-qe-integration-tests" ]]; then
                        continue
                    fi
                    echo "Error: selected job '$job' targets $job_release, but you requested $REQUESTED_RELEASE"
                    exit 1
                fi
            done < "$file"
        done
        JOBS_RELEASE="$REQUESTED_RELEASE"
    else
        # --job all (or no filter) selects every job, so validate each one's
        # release the way the pattern branch does. Job files are scoped to the
        # requested release's directory, but a misplaced entry must still be
        # caught rather than launched against the wrong payload.
        for file in "$JOB_FILE" "$JOB_FILE_LVMS" "$JOB_FILE_Z" "$JOB_FILE_Y"; do
            [[ ! -f "$file" ]] && continue
            if [[ -z "${INITIAL_IMAGE:-}" && ( "$file" == "$JOB_FILE_Z" || "$file" == "$JOB_FILE_Y" ) ]]; then
                continue
            fi
            while IFS= read -r job; do
                [[ -z "$job" ]] && continue
                job_release=$(release_from_job_name "$job")
                if [[ -n "$job_release" && -n "$REQUESTED_RELEASE" && "$job_release" != "$REQUESTED_RELEASE" ]]; then
                    # Broad patterns can match a stale LVMS seed alongside
                    # compatible jobs. Let job_selected report that lane as skipped.
                    if [[ "$job" == *"-tnf-lvms-mno-qe-integration-tests" ]]; then
                        continue
                    fi
                    echo "Error: selected job '$job' targets $job_release, but you requested $REQUESTED_RELEASE"
                    exit 1
                fi
            done < "$file"
        done
        JOBS_RELEASE="$REQUESTED_RELEASE"
    fi
    if [[ -n "$REQUESTED_RELEASE" && -n "$JOBS_RELEASE" && "$REQUESTED_RELEASE" != "$JOBS_RELEASE" ]]; then
        if [[ "$TOPOLOGY" == "tnf" && "$JOB_FILTER" == "lvms" ]]; then
            echo "Error: the TNF LVMS lane targets $JOBS_RELEASE, but you requested $REQUESTED_RELEASE"
        else
            echo "Error: job files are for $JOBS_RELEASE but you requested $REQUESTED_RELEASE"
            echo "Run './launch.sh $TOPOLOGY $REQUESTED_RELEASE --refresh' to update job files."
        fi
        exit 1
    fi
fi
}

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
    if [[ -n "$RELEASE_IMAGE" ]]; then
        LINE_NUM=0
        echo "=== $TOPOLOGY jobs (release $OCP_RELEASE) ==="
        list_file "$JOB_FILE" "$TOPOLOGY"
        list_file "$JOB_FILE_LVMS" "$TOPOLOGY LVMS"
        list_file "$JOB_FILE_Z" "$TOPOLOGY z-stream upgrades (--initial required)"
        list_file "$JOB_FILE_Y" "$TOPOLOGY y-stream upgrades (--initial required)"
        echo ""
        echo "Use --job all, --job <number>, --job <n,n,n>, or --job <pattern> to select"
    else
        for release_dir in "$SCRIPT_DIR"/jobs/*/; do
            [[ ! -d "$release_dir" ]] && continue
            rel=$(basename "$release_dir")
            rel_file="$release_dir/${TOPOLOGY}.txt"
            rel_file_lvms="$release_dir/${TOPOLOGY}-lvms.txt"
            rel_file_z="$release_dir/${TOPOLOGY}-z-stream.txt"
            rel_file_y="$release_dir/${TOPOLOGY}-y-stream.txt"
            [[ ! -f "$rel_file" && ! -f "$rel_file_lvms" && ! -f "$rel_file_z" && ! -f "$rel_file_y" ]] && continue
            LINE_NUM=0
            echo "=== $TOPOLOGY jobs (release $rel) ==="
            list_file "$rel_file" "$TOPOLOGY"
            list_file "$rel_file_lvms" "$TOPOLOGY LVMS"
            list_file "$rel_file_z" "$TOPOLOGY z-stream upgrades (--initial required)"
            list_file "$rel_file_y" "$TOPOLOGY y-stream upgrades (--initial required)"
            echo ""
        done
        echo "Use --job all, --job <number>, --job <n,n,n>, or --job <pattern> to select"
    fi
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

    # The latest run is selected per topology, regardless of release, and a
    # run directory is shared by every release launched for the topology on
    # the same day. Scope failure mapping to the requested release's records;
    # a run holding no record for that release is rejected explicitly so its
    # failures are never silently dropped from the requested release's list.
    REQUESTED_RELEASE=$(image_tag "$RELEASE_IMAGE" | grep -oE '^[0-9]+\.[0-9]+' || true)
    if [[ -n "$REQUESTED_RELEASE" ]]; then
        matching_records=0
        for f in "$PREV_RUN_DIR"/gangway_*.json; do
            [[ ! -e "$f" ]] && continue
            prev_job=$(jq -r '.[0].JobName // "unknown"' "$f" 2>/dev/null)
            if [[ $(release_from_job_name "$prev_job") == "$REQUESTED_RELEASE" ]]; then
                matching_records=$((matching_records + 1))
            fi
        done
        if [[ "$matching_records" -eq 0 ]]; then
            echo "Error: previous run $(basename "$(dirname "$PREV_RUN_DIR")") holds no $REQUESTED_RELEASE jobs for '$TOPOLOGY'."
            echo "Re-launch with the payload matching that run."
            exit 1
        fi
    fi

    # Build combined job list with continuous numbering (same order as --list and launch)
    COMBINED_JOBS=$(mktemp)
    trap 'rm -f "$COMBINED_JOBS"' EXIT
    for f in "$JOB_FILE" "$JOB_FILE_LVMS" "$JOB_FILE_Z" "$JOB_FILE_Y"; do
        [[ -f "$f" && -s "$f" ]] && cat "$f" >> "$COMBINED_JOBS"
    done

    FAILED_NUMS=""
    for f in "$PREV_RUN_DIR"/gangway_*.json; do
        [[ ! -e "$f" ]] && continue
        prev_job=$(jq -r '.[0].JobName // "unknown"' "$f" 2>/dev/null)
        prev_url=$(jq -r '.[0].JobURL // "no-url"' "$f" 2>/dev/null)
        # Records from another release launched into the same run directory
        # are not part of this payload's job list.
        if [[ -n "$REQUESTED_RELEASE" && $(release_from_job_name "$prev_job") != "$REQUESTED_RELEASE" ]]; then
            continue
        fi

        if [[ "$prev_url" != "no-url" && "$prev_url" != "null" ]]; then
            prev_gcs="${prev_url/prow.ci.openshift.org\/view\/gs\//${GCSWEB_BASE}/}"
            prev_result=$(curl -sL --max-time 10 "${prev_gcs}/finished.json" 2>/dev/null \
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

check_job_release

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
    if ! HTTP_CODE=$(curl --silent --show-error --connect-timeout 5 --max-time 20 -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer ${MY_APPCI_TOKEN}" \
        "${GANGWAY_API}/v1/executions/" 2>/dev/null); then
        echo "Error: cannot reach Gangway API at $GANGWAY_API. Check network/proxy."
        exit 1
    fi
    if [[ "$HTTP_CODE" == "401" || "$HTTP_CODE" == "403" ]]; then
        echo "Error: token rejected by Gangway API (HTTP $HTTP_CODE). Refresh MY_APPCI_TOKEN."
        exit 1
    elif [[ "$HTTP_CODE" == "000" ]]; then
        echo "Error: cannot reach Gangway API at $GANGWAY_API. Check network/proxy."
        exit 1
    elif [[ "$HTTP_CODE" == 2?? ]]; then
        echo "Token OK (HTTP $HTTP_CODE)"
    else
        echo "Warning: Gangway preflight returned HTTP $HTTP_CODE; token validation is inconclusive. Attempting job submission."
    fi
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

# A job at a given line number passes the --job filter
job_selected() {
    local job="$1" line_num="$2"
    if [[ -n "$SELECTED_NUMS" ]] && [[ "$SELECTED_NUMS" != *",$line_num,"* ]]; then
        return 1
    fi
    if [[ -n "$JOB_PATTERN" ]] && [[ "$job" != *"$JOB_PATTERN"* ]]; then
        return 1
    fi
    if [[ "$job" == *"-tnf-lvms-mno-qe-integration-tests" ]]; then
        local job_release
        job_release=$(release_from_job_name "$job")
        [[ -n "$REQUESTED_RELEASE" && "$job_release" == "$REQUESTED_RELEASE" ]] || return 2
    fi
    return 0
}

# Count jobs that will actually be attempted, so wave pacing can skip the
# pause after the final launch instead of waiting for a wave that never comes
count_selected_jobs() {
    local file="$1"
    [[ ! -f "$file" || ! -s "$file" ]] && return
    while IFS= read -r job; do
        [[ -z "$job" ]] && continue
        LINE_NUM=$((LINE_NUM + 1))
        if job_selected "$job" "$LINE_NUM"; then
            TOTAL_SELECTED=$((TOTAL_SELECTED + 1))
        fi
    done < "$file"
}

TOTAL_SELECTED=0
count_selected_jobs "$JOB_FILE"
count_selected_jobs "$JOB_FILE_LVMS"
if [[ -n "${INITIAL_IMAGE:-}" ]]; then
    count_selected_jobs "$JOB_FILE_Z"
    count_selected_jobs "$JOB_FILE_Y"
fi
LINE_NUM=0
LAUNCHED_COUNT=0
LVMS_SKIP_TOTAL=0
LVMS_SKIP_TARGETS=""

echo "=== Launching $TOPOLOGY jobs against $RELEASE_IMAGE ==="
echo "    Run directory: $RUN_DIR"
if (( WAVE_SIZE > 0 )); then
    echo "    Pacing: waves of $WAVE_SIZE, $(human_duration "$WAVE_DELAY") between waves"
    if (( STAGGER > 0 )); then
        echo "            $(human_duration "$STAGGER") between jobs within a wave"
    fi
elif (( STAGGER > 0 )); then
    echo "    Pacing: $(human_duration "$STAGGER") between jobs"
else
    echo "    Pacing: $(human_duration "$DELAY") between jobs (default)"
fi
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

        selection_status=0
        job_selected "$JOB" "$LINE_NUM" || selection_status=$?
        if (( selection_status != 0 )); then
            if (( selection_status == 2 )); then
                LVMS_SKIP_TOTAL=$((LVMS_SKIP_TOTAL + 1))
                LVMS_SKIP_TARGETS="${LVMS_SKIP_TARGETS:+$LVMS_SKIP_TARGETS, }$(release_from_job_name "$JOB")"
            fi
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
                LAUNCHED_COUNT=$((LAUNCHED_COUNT + 1))
                # Wave batching: pause between waves, but not after the last selected job
                # Split the WAVE_SIZE>0 guard into its own (( )) so the modulo below is
                # never evaluated when WAVE_SIZE=0 — bash 3.2 (macOS default) emits a
                # "division by 0" warning if both are combined in a single (( && )).
                if (( WAVE_SIZE > 0 )) && (( LAUNCHED_COUNT % WAVE_SIZE == 0 && COUNT < TOTAL_SELECTED )); then
                    echo ""
                    echo "  >>> Wave complete ($LAUNCHED_COUNT launched). Waiting ${WAVE_DELAY}s for leases to free..."
                    sleep "$WAVE_DELAY"
                    echo "  >>> Resuming launches"
                    echo ""
                elif (( STAGGER > 0 )); then
                    echo "  waiting ${STAGGER}s (stagger)..."
                    sleep "$STAGGER"
                else
                    sleep "$DELAY"
                fi
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
launch_from_file "$JOB_FILE_LVMS" "$RELEASE_IMAGE"

# Upgrade jobs — only when --initial is provided
if [[ -n "${INITIAL_IMAGE:-}" ]]; then
    launch_from_file "$JOB_FILE_Z" "$INITIAL_IMAGE"
    launch_from_file "$JOB_FILE_Y" "$INITIAL_IMAGE"
else
    Z_COUNT=0
    Y_COUNT=0
    if [[ -f "$JOB_FILE_Z" ]]; then Z_COUNT=$(wc -l < "$JOB_FILE_Z"); fi
    if [[ -f "$JOB_FILE_Y" ]]; then Y_COUNT=$(wc -l < "$JOB_FILE_Y"); fi
    SKIP_TOTAL=$((Z_COUNT + Y_COUNT))
    if [[ "$SKIP_TOTAL" -gt 0 ]]; then
        echo "Skipped $SKIP_TOTAL upgrade jobs (no --initial provided)"
    fi
fi

if (( LVMS_SKIP_TOTAL > 0 )); then
    echo "Skipped $LVMS_SKIP_TOTAL TNF LVMS lane(s) (targets ${LVMS_SKIP_TARGETS:-unknown}, payload ${REQUESTED_RELEASE:-unknown}) — run --refresh once a matching lane exists"
fi

echo ""

if [[ "$COUNT" -eq 0 && -n "$JOB_FILTER" && "$JOB_FILTER" != "all" ]]; then
    echo "Error: no jobs matched selector '$JOB_FILTER'"
    echo "Run './launch.sh $TOPOLOGY --list' to see available jobs."
    exit 1
fi

if [[ "$COUNT" -eq 0 && "$LVMS_SKIP_TOTAL" -gt 0 ]]; then
    echo "Error: no jobs launched because no TNF LVMS lane matches the payload release"
    exit 1
fi

echo "=== Done: $COUNT jobs launched, $FAILED failures ==="
echo "    Tracking JSONs: $RUN_DIR/"
