#!/usr/bin/env python3
"""Generate CPU Usage graph from pcp2json-derived data.

Reads a JSON file with arrays: timestamps, user, sys, iowait (all in %).
Produces a stacked area chart of CPU usage.
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
    parser = argparse.ArgumentParser(description="Plot CPU usage from JSON data")
    parser.add_argument("json_file", help="Path to JSON file with timestamps/user/sys/idle")
    parser.add_argument("-o", "--output", default="/data/cpu_usage.png",
                        help="Output PNG file path")
    parser.add_argument("--timezone", default="UTC",
                        help="Timezone label for the chart (default: UTC)")
    args = parser.parse_args()

    with open(args.json_file) as f:
        data = json.load(f)

    timestamps = []
    user_values = []
    sys_values = []
    iowait_values = []

    for i, ts_str in enumerate(data["timestamps"]):
        try:
            ts = datetime.strptime(ts_str, FMT)
        except ValueError:
            continue
        timestamps.append(ts)
        user_values.append(float(data["user"][i]))
        sys_values.append(float(data["sys"][i]))
        iowait_values.append(float(data.get("iowait", [0] * len(data["timestamps"]))[i]))

    if not timestamps:
        print("ERROR: No data points found in JSON", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(timestamps)} data points")

    # Peak values
    user_peak_idx = max(range(len(user_values)), key=lambda i: user_values[i])
    sys_peak_idx = max(range(len(sys_values)), key=lambda i: sys_values[i])
    iowait_peak_idx = max(range(len(iowait_values)), key=lambda i: iowait_values[i])

    print(f"Peak user:   {user_values[user_peak_idx]:.1f}% at {timestamps[user_peak_idx].strftime('%H:%M:%S')}")
    print(f"Peak sys:    {sys_values[sys_peak_idx]:.1f}% at {timestamps[sys_peak_idx].strftime('%H:%M:%S')}")
    print(f"Peak iowait: {iowait_values[iowait_peak_idx]:.1f}% at {timestamps[iowait_peak_idx].strftime('%H:%M:%S')}")

    fig, (ax, ax_tbl) = plt.subplots(
        2, 1, figsize=(16, 7),
        gridspec_kw={"height_ratios": [8, 1]},
    )

    ax.stackplot(timestamps, sys_values, iowait_values, user_values,
                 labels=["System", "I/O Wait", "User"],
                 colors=["tab:red", "tab:orange", "tab:blue"],
                 alpha=0.7)

    ax.set_xlabel(f"Time ({args.timezone})")
    ax.set_ylabel("CPU Usage (%)")
    ax.set_title("CPU Usage (15-second intervals)")
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")

    # Summary table
    ax_tbl.axis("off")
    table = ax_tbl.table(
        cellText=[
            ["User", timestamps[user_peak_idx].strftime("%H:%M:%S"), f"{user_values[user_peak_idx]:.1f}%"],
            ["System", timestamps[sys_peak_idx].strftime("%H:%M:%S"), f"{sys_values[sys_peak_idx]:.1f}%"],
            ["I/O Wait", timestamps[iowait_peak_idx].strftime("%H:%M:%S"), f"{iowait_values[iowait_peak_idx]:.1f}%"],
        ],
        colLabels=["Type", "Time", "Peak"],
        cellColours=[["#dbe9f6"] * 3, ["#f6dbdb"] * 3, ["#fde2cc"] * 3],
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
