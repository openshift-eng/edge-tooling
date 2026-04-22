#!/usr/bin/env python3
"""Generate Memory Usage graph from pcp2json-derived data.

Reads a JSON file with arrays: timestamps, used_gb, cached_gb, free_gb, total_gb.
Produces a stacked area chart of memory usage.
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


def main():
    parser = argparse.ArgumentParser(description="Plot memory usage from JSON data")
    parser.add_argument("json_file", help="Path to JSON file")
    parser.add_argument("-o", "--output", default="/data/mem_usage.png",
                        help="Output PNG file path")
    parser.add_argument("--timezone", default="UTC",
                        help="Timezone label for the chart (default: UTC)")
    args = parser.parse_args()

    with open(args.json_file) as f:
        data = json.load(f)

    timestamps = []
    used = []
    cached = []
    total = []

    for i, ts_str in enumerate(data["timestamps"]):
        try:
            ts = datetime.strptime(ts_str, FMT)
        except ValueError:
            continue
        timestamps.append(ts)
        used.append(float(data["used_gb"][i]))
        cached.append(float(data["cached_gb"][i]))
        total.append(float(data["total_gb"][i]))

    if not timestamps:
        print("ERROR: No data points found in JSON", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(timestamps)} data points")

    used_peak_idx = max(range(len(used)), key=lambda i: used[i])
    total_mem = total[0] if total else 0

    print(f"Total memory: {total_mem:.1f} GB")
    print(f"Peak used:    {used[used_peak_idx]:.1f} GB at {timestamps[used_peak_idx].strftime('%H:%M:%S')}")

    fig, (ax, ax_tbl) = plt.subplots(
        2, 1, figsize=(16, 7),
        gridspec_kw={"height_ratios": [8, 1]},
    )

    ax.stackplot(timestamps, used, cached,
                 labels=["Used", "Cached"],
                 colors=["tab:red", "tab:orange"],
                 alpha=0.7)

    if total:
        ax.plot(timestamps, total, label="Total", color="black",
                linewidth=1, linestyle="--", alpha=0.5)

    ax.set_xlabel(f"Time ({args.timezone})")
    ax.set_ylabel("Memory (GB)")
    ax.set_title("Memory Usage (15-second intervals)")
    ax.set_ylim(0, max(total) * 1.05 if total else None)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

    # Summary table
    avg_used = sum(used) / len(used)
    ax_tbl.axis("off")
    table = ax_tbl.table(
        cellText=[
            ["Total", "—", f"{total_mem:.1f} GB"],
            ["Peak Used", timestamps[used_peak_idx].strftime("%H:%M:%S"), f"{used[used_peak_idx]:.1f} GB"],
            ["Avg Used", "—", f"{avg_used:.1f} GB"],
        ],
        colLabels=["Type", "Time", "Value"],
        cellColours=[["#f0f0f0"] * 3, ["#f6dbdb"] * 3, ["#dbe9f6"] * 3],
        colColours=["#cccccc"] * 3,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(0.4, 1.2)

    fig.text(0.99, 0.01, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             ha="right", va="bottom", fontsize=7, color="gray")

    plt.tight_layout()
    plt.savefig(args.output, dpi=150)
    print(f"Saved chart to {args.output}")


if __name__ == "__main__":
    main()
