#!/bin/bash
#
# Clean up old test runs from scratch/runs directory
#
# Usage:
#   ./clean-test-runs.sh                    # Show what will be deleted
#   ./clean-test-runs.sh --all              # Delete all runs
#   ./clean-test-runs.sh --keep 3           # Keep only the 3 most recent runs
#   ./clean-test-runs.sh --older-than 7d    # Delete runs older than 7 days
#   ./clean-test-runs.sh --pattern "name*"  # Delete runs matching pattern
#   ./clean-test-runs.sh --force            # Skip confirmation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRATCH_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_DIR="${SCRATCH_ROOT}/runs"

usage() {
    cat <<'EOF'
Usage:
  clean-test-runs.sh [options]

Options:
  --all                   Delete all test runs
  --keep N                Keep only the N most recent runs, delete the rest
  --older-than DURATION   Delete runs older than DURATION (e.g., 7d, 24h, 30m)
  --pattern PATTERN       Delete runs matching glob pattern
  --force                 Skip confirmation prompt
  -h, --help              Show this help

Examples:
  # Preview what will be deleted
  ./clean-test-runs.sh --all

  # Keep only the 5 most recent runs
  ./clean-test-runs.sh --keep 5 --force

  # Delete runs older than 7 days
  ./clean-test-runs.sh --older-than 7d --force

  # Delete specific run pattern
  ./clean-test-runs.sh --pattern "non-replacement-*" --force
EOF
}

MODE=""
KEEP_COUNT=""
OLDER_THAN=""
PATTERN=""
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --all)
            MODE="all"
            shift
            ;;
        --keep)
            [[ $# -lt 2 ]] && { echo "Missing value for --keep"; exit 1; }
            MODE="keep"
            KEEP_COUNT="$2"
            shift 2
            ;;
        --older-than)
            [[ $# -lt 2 ]] && { echo "Missing value for --older-than"; exit 1; }
            MODE="older-than"
            OLDER_THAN="$2"
            shift 2
            ;;
        --pattern)
            [[ $# -lt 2 ]] && { echo "Missing value for --pattern"; exit 1; }
            MODE="pattern"
            PATTERN="$2"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

if [[ -z "${MODE}" ]]; then
    echo "Error: Must specify --all, --keep, --older-than, or --pattern"
    echo ""
    usage
    exit 1
fi

if [[ ! -d "${RUN_DIR}" ]]; then
    echo "No runs directory found at: ${RUN_DIR}"
    exit 0
fi

# Build list of directories to delete
declare -a TO_DELETE=()

case "${MODE}" in
    all)
        echo "Collecting all test runs..."
        mapfile -t TO_DELETE < <(find "${RUN_DIR}" -mindepth 1 -maxdepth 1 -type d | sort)
        ;;
    keep)
        if [[ ! "${KEEP_COUNT}" =~ ^[0-9]+$ ]]; then
            echo "Error: --keep value must be a positive integer"
            exit 1
        fi
        echo "Finding runs to delete (keeping ${KEEP_COUNT} most recent)..."
        mapfile -t ALL_RUNS < <(find "${RUN_DIR}" -mindepth 1 -maxdepth 1 -type d -printf "%T@ %p\n" | sort -rn | cut -d' ' -f2-)
        if [[ ${#ALL_RUNS[@]} -gt ${KEEP_COUNT} ]]; then
            TO_DELETE=("${ALL_RUNS[@]:${KEEP_COUNT}}")
        fi
        ;;
    older-than)
        echo "Finding runs older than ${OLDER_THAN}..."
        # Convert duration to minutes for find -mmin
        MINUTES=""
        if [[ "${OLDER_THAN}" =~ ^([0-9]+)m$ ]]; then
            MINUTES="${BASH_REMATCH[1]}"
        elif [[ "${OLDER_THAN}" =~ ^([0-9]+)h$ ]]; then
            MINUTES=$((${BASH_REMATCH[1]} * 60))
        elif [[ "${OLDER_THAN}" =~ ^([0-9]+)d$ ]]; then
            MINUTES=$((${BASH_REMATCH[1]} * 1440))
        else
            echo "Error: Duration must be in format: 30m, 24h, or 7d"
            exit 1
        fi
        mapfile -t TO_DELETE < <(find "${RUN_DIR}" -mindepth 1 -maxdepth 1 -type d -mmin +${MINUTES} | sort)
        ;;
    pattern)
        echo "Finding runs matching pattern: ${PATTERN}..."
        mapfile -t TO_DELETE < <(find "${RUN_DIR}" -mindepth 1 -maxdepth 1 -type d -name "${PATTERN}" | sort)
        ;;
esac

if [[ ${#TO_DELETE[@]} -eq 0 ]]; then
    echo "No runs to delete."
    exit 0
fi

# Calculate total size
TOTAL_SIZE=0
for dir in "${TO_DELETE[@]}"; do
    SIZE=$(du -sb "${dir}" 2>/dev/null | cut -f1 || echo 0)
    TOTAL_SIZE=$((TOTAL_SIZE + SIZE))
done

# Convert bytes to human readable
if [[ ${TOTAL_SIZE} -gt 1073741824 ]]; then
    SIZE_HR="$(echo "scale=1; ${TOTAL_SIZE}/1073741824" | bc)G"
elif [[ ${TOTAL_SIZE} -gt 1048576 ]]; then
    SIZE_HR="$(echo "scale=1; ${TOTAL_SIZE}/1048576" | bc)M"
elif [[ ${TOTAL_SIZE} -gt 1024 ]]; then
    SIZE_HR="$(echo "scale=1; ${TOTAL_SIZE}/1024" | bc)K"
else
    SIZE_HR="${TOTAL_SIZE}B"
fi

echo ""
echo "Found ${#TO_DELETE[@]} run(s) to delete (${SIZE_HR} total):"
echo ""
for dir in "${TO_DELETE[@]}"; do
    DIR_SIZE=$(du -sh "${dir}" 2>/dev/null | cut -f1 || echo "?")
    echo "  ${dir##*/} (${DIR_SIZE})"
done
echo ""

if [[ ${FORCE} -eq 0 ]]; then
    read -p "Delete these runs? [y/N] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled."
        exit 0
    fi
fi

echo "Deleting..."
for dir in "${TO_DELETE[@]}"; do
    rm -rf "${dir}"
    echo "  Deleted: ${dir##*/}"
done

echo ""
echo "Done! Freed ${SIZE_HR}"
