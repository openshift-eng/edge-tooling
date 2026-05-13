#!/usr/bin/env python3
"""Generate Disk Usage graph from pcp2json-derived data.

Reads a JSON file with timestamps and per-partition used_pct/used_gb arrays.
Produces a line chart showing each partition's fill percentage (0-100%).
"""

import argparse
import json
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402

FMT = "%Y-%m-%d %H:%M:%S"

COLORS = ["tab:purple", "tab:blue", "tab:green", "tab:orange", "tab:red",
          "tab:brown", "tab:pink", "tab:gray"]


def main():
    parser = argparse.ArgumentParser(description="Plot disk usage from JSON data")
    parser.add_argument("json_file", help="Path to JSON file")
    parser.add_argument("-o", "--output", default="/data/disk_usage.png",
                        help="Output PNG file path")
    parser.add_argument("--timezone", default="UTC",
                        help="Timezone label for the chart (default: UTC)")
    args = parser.parse_args()

    with open(args.json_file) as f:
        data = json.load(f)

    timestamps = []
    for ts_str in data["timestamps"]:
        try:
            timestamps.append(datetime.strptime(ts_str, FMT))
        except ValueError:
            timestamps.append(None)

    if not timestamps or all(t is None for t in timestamps):
        print("ERROR: No data points found in JSON", file=sys.stderr)
        sys.exit(1)

    partitions = data.get("partitions", [])
    if not partitions:
        print("ERROR: No partition data found", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(timestamps)} data points for {len(partitions)} partitions")

    fig, (ax, ax_tbl) = plt.subplots(
        2, 1, figsize=(16, 7),
        gridspec_kw={"height_ratios": [8, 1]},
    )

    table_rows = []

    for idx, part in enumerate(partitions):
        dev = part["device"]
        mountdir = part.get("mountdir", "")
        cap_gb = part["capacity_gb"]
        pct_vals = part["used_pct"]
        gb_vals = part["used_gb"]
        color = COLORS[idx % len(COLORS)]

        # Filter valid points
        valid_ts = []
        valid_pct = []
        valid_gb = []
        for i, ts in enumerate(timestamps):
            if ts is not None and i < len(pct_vals) and pct_vals[i] is not None:
                valid_ts.append(ts)
                valid_pct.append(pct_vals[i])
                valid_gb.append(gb_vals[i] if i < len(gb_vals) and gb_vals[i] is not None else 0)

        if not valid_ts:
            continue

        peak_idx = max(range(len(valid_pct)), key=lambda i: valid_pct[i])
        peak_pct = valid_pct[peak_idx]
        peak_gb = valid_gb[peak_idx]

        dev_label = f"{dev} ({mountdir})" if mountdir else dev
        label = f"{dev_label} ({cap_gb:.0f} GB, peak {peak_gb:.1f} GB)"
        ax.plot(valid_ts, valid_pct, label=label, color=color, linewidth=1.2, alpha=0.9)

        table_rows.append([dev_label, f"{cap_gb:.0f} GB",
                          valid_ts[peak_idx].strftime("%H:%M:%S"),
                          f"{peak_gb:.1f} GB ({peak_pct:.0f}%)"])

        print(f"  {dev_label}: {cap_gb:.0f} GB capacity, peak {peak_gb:.1f} GB ({peak_pct:.0f}%)")

    ax.set_xlabel(f"Time ({args.timezone})")
    ax.set_ylabel("Usage (%)")
    ax.set_title("Disk Usage by Partition (15-second intervals)")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right", fontsize=8)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

    # Summary table
    ax_tbl.axis("off")
    if table_rows:
        row_colors = [["#e8dff5", "#e8dff5", "#e8dff5", "#e8dff5"],
                      ["#dbe9f6", "#dbe9f6", "#dbe9f6", "#dbe9f6"],
                      ["#dbf6db", "#dbf6db", "#dbf6db", "#dbf6db"],
                      ["#fde2cc", "#fde2cc", "#fde2cc", "#fde2cc"],
                      ["#f6dbdb", "#f6dbdb", "#f6dbdb", "#f6dbdb"]]
        table = ax_tbl.table(
            cellText=table_rows,
            colLabels=["Partition", "Capacity", "Peak Time", "Peak Used"],
            cellColours=[row_colors[i % len(row_colors)] for i in range(len(table_rows))],
            colColours=["#cccccc"] * 4,
            loc="center",
            cellLoc="center",
        )
        table.auto_set_font_size(False)
        table.set_fontsize(9)
        table.scale(0.5, 1.2)

    fig.text(0.99, 0.01, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             ha="right", va="bottom", fontsize=7, color="gray")

    plt.tight_layout()
    plt.savefig(args.output, dpi=150)
    print(f"Saved chart to {args.output}")


if __name__ == "__main__":
    main()
