#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIPPY_API="https://sippy.dptools.openshift.org/api/jobs"

RELEASES=("4.22" "4.23" "5.0")
TOPOLOGIES=("tnf" "tna" "sno")

sippy_filter_for() {
    case "$1" in
        tnf) echo "two-node-fencing" ;;
        tna) echo "two-node-arbiter" ;;
        sno) echo "-4vcpu" ;;
        *)   echo "" ;;
    esac
}

GRAND_TOTAL=0

for release in "${RELEASES[@]}"; do
    for topo in "${TOPOLOGIES[@]}"; do
        search=$(sippy_filter_for "$topo")
        [[ -z "$search" ]] && continue
        # tr instead of ${topo^^}: collect.sh must run on bash 3.2 (macOS default)
        topo_label=$(printf '%s' "$topo" | tr '[:lower:]' '[:upper:]')

        filter_json=$(printf '{"items":[{"columnField":"name","operatorValue":"contains","value":"%s"}],"linkOperator":"and"}' "$search")
        encoded=$(printf '%s' "$filter_json" | jq -sRr '@uri')

        echo "Fetching $topo_label jobs from Sippy (release $release, filter: $search)..."
        response=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 30 \
            "${SIPPY_API}?release=${release}&filter=${encoded}&period=default&sortField=name&sort=asc")

        out_dir="$SCRIPT_DIR/jobs/${release}"
        mkdir -p "$out_dir"

        job_file="$out_dir/${topo}.txt"
        job_file_z="$out_dir/${topo}-z-stream.txt"
        job_file_y="$out_dir/${topo}-y-stream.txt"

        # Stage beside the destination so mv stays a same-filesystem rename
        # (atomic) even when TMPDIR is on another filesystem.
        tmp_file=$(mktemp "${job_file}.XXXXXX") tmp_z=$(mktemp "${job_file_z}.XXXXXX") tmp_y=$(mktemp "${job_file_y}.XXXXXX")
        trap 'rm -f "$tmp_file" "$tmp_z" "$tmp_y"' EXIT

        echo "$response" \
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

        mv "$tmp_file" "$job_file"; mv "$tmp_z" "$job_file_z"; mv "$tmp_y" "$job_file_y"
        trap - EXIT

        regular=$([[ -f "$job_file" ]] && wc -l < "$job_file" || echo 0)
        z_count=$([[ -f "$job_file_z" ]] && wc -l < "$job_file_z" || echo 0)
        y_count=$([[ -f "$job_file_y" ]] && wc -l < "$job_file_y" || echo 0)
        total=$((regular + z_count + y_count))
        GRAND_TOTAL=$((GRAND_TOTAL + total))

        echo "  $topo_label ${release}: $total jobs ($regular regular, $z_count z-stream, $y_count y-stream)"
        echo ""
    done
done

echo "=== Collected $GRAND_TOTAL jobs total ==="
echo "    Files written to: $SCRIPT_DIR/jobs/{$(IFS=,; echo "${RELEASES[*]}")}/"
