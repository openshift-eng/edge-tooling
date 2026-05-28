#!/usr/bin/env python3
"""Generate Disk IO Performance graph from pcp2json-derived data.

Reads a JSON file with arrays: timestamps, bi, bo, iops, await, aveq.
Produces a line chart with read/write/total IOPS, disk await, and queue length.
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
    parser = argparse.ArgumentParser(description="Plot disk IO from JSON data")
    parser.add_argument("json_file", help="Path to JSON file with timestamps/bi/bo/await")
    parser.add_argument("-o", "--output", default="/data/disk_io_performance.png",
                        help="Output PNG file path")
    parser.add_argument("--timezone", default="UTC",
                        help="Timezone label for the chart (default: UTC)")
    args = parser.parse_args()

    with open(args.json_file) as f:
        data = json.load(f)

    timestamps = []
    bi_values = []
    bo_values = []
    iops_values = []
    await_values = []
    aveq_values = []

    iops_data = data.get("iops", [])
    aveq_data = data.get("aveq", [])

    for i, ts_str in enumerate(data["timestamps"]):
        try:
            ts = datetime.strptime(ts_str, FMT)
        except ValueError:
            continue
        timestamps.append(ts)
        bi_values.append(float(data["bi"][i]))
        bo_values.append(float(data["bo"][i]))
        iops_values.append(float(iops_data[i]) if i < len(iops_data) else 0)
        await_values.append(float(data["await"][i]))
        aveq_values.append(float(aveq_data[i]) if i < len(aveq_data) else 0)

    if not timestamps:
        print("ERROR: No data points found in JSON", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(timestamps)} data points")

    # Find peak values
    bi_peak_idx = max(range(len(bi_values)), key=lambda i: bi_values[i])
    bo_peak_idx = max(range(len(bo_values)), key=lambda i: bo_values[i])
    iops_peak_idx = max(range(len(iops_values)), key=lambda i: iops_values[i])
    q_peak_idx = max(range(len(await_values)), key=lambda i: await_values[i])
    aveq_peak_idx = max(range(len(aveq_values)), key=lambda i: aveq_values[i])
    bi_peak_ts = timestamps[bi_peak_idx].strftime("%H:%M:%S")
    bo_peak_ts = timestamps[bo_peak_idx].strftime("%H:%M:%S")
    iops_peak_ts = timestamps[iops_peak_idx].strftime("%H:%M:%S")
    q_peak_ts = timestamps[q_peak_idx].strftime("%H:%M:%S")
    aveq_peak_ts = timestamps[aveq_peak_idx].strftime("%H:%M:%S")
    bi_peak_val = bi_values[bi_peak_idx]
    bo_peak_val = bo_values[bo_peak_idx]
    iops_peak_val = iops_values[iops_peak_idx]
    q_peak_val = await_values[q_peak_idx]
    aveq_peak_val = aveq_values[aveq_peak_idx]

    print(f"Peak bi (read):  {bi_peak_val:,.0f} ops/s at {bi_peak_ts}")
    print(f"Peak bo (write): {bo_peak_val:,.0f} ops/s at {bo_peak_ts}")
    print(f"Peak total IOPS: {iops_peak_val:,.0f} ops/s at {iops_peak_ts}")
    print(f"Peak await:      {q_peak_val:,.1f} ms at {q_peak_ts}")
    print(f"Peak queue:      {aveq_peak_val:,.2f} at {aveq_peak_ts}")

    fig, (ax_iops, ax_lat, ax_tbl) = plt.subplots(
        3, 1, figsize=(16, 9),
        gridspec_kw={"height_ratios": [5, 3, 1]},
    )

    # Top subplot: IOPS
    ax_iops.fill_between(timestamps, iops_values, alpha=0.15, color="gray",
                         label="Total IOPS")
    ax_iops.plot(timestamps, bi_values, label="Disk Read OPS (bi)", color="tab:blue",
                 linewidth=0.8, alpha=0.9)
    ax_iops.plot(timestamps, bo_values, label="Disk Write OPS (bo)", color="tab:red",
                 linewidth=0.8, alpha=0.9)
    ax_iops.set_ylabel("I/O Operations (ops/s)")
    ax_iops.set_title("Disk I/O Performance (15-second intervals)")
    ax_iops.grid(True, alpha=0.3)
    ax_iops.legend(loc="upper right")
    ax_iops.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_iops.tick_params(labelbottom=False)

    # Bottom subplot: Latency and queue length (own Y-scale)
    ax_lat.plot(timestamps, await_values, label="Disk Await (ms)", color="tab:green",
                linewidth=0.8, alpha=0.9)
    ax_lat.set_ylabel("Disk Await (ms)")
    ax_lat.grid(True, alpha=0.3)

    ax_q = ax_lat.twinx()
    ax_q.plot(timestamps, aveq_values, label="Disk Queue Length", color="tab:orange",
              linewidth=0.8, alpha=0.9)
    ax_q.set_ylabel("Queue Length")

    lines_lat, labels_lat = ax_lat.get_legend_handles_labels()
    lines_q, labels_q = ax_q.get_legend_handles_labels()
    ax_lat.legend(lines_lat + lines_q, labels_lat + labels_q, loc="upper right")

    ax_lat.set_xlabel(f"Time ({args.timezone})")
    ax_lat.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_lat.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax_lat.xaxis.get_majorticklabels(), rotation=30, ha="right")

    # Small table showing peak values
    ax_tbl.axis("off")
    table = ax_tbl.table(
        cellText=[
            ["Total IOPS", iops_peak_ts, f"{iops_peak_val:,.0f}"],
            ["Disk Read (bi)", bi_peak_ts, f"{bi_peak_val:,.0f}"],
            ["Disk Write (bo)", bo_peak_ts, f"{bo_peak_val:,.0f}"],
            ["Disk Await (ms)", q_peak_ts, f"{q_peak_val:,.1f}"],
            ["Disk Queue", aveq_peak_ts, f"{aveq_peak_val:,.2f}"],
        ],
        colLabels=["Type", "Time", "Peak"],
        cellColours=[
            ["#e0e0e0"] * 3,
            ["#dbe9f6"] * 3,
            ["#f6dbdb"] * 3,
            ["#dbf6db"] * 3,
            ["#fce5cd"] * 3,
        ],
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
