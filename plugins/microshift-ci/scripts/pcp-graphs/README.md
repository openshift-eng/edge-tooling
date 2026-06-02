# PCP Performance Graphs for MicroShift CI

Generate performance graphs from PCP (Performance Co-Pilot) archives
collected during MicroShift CI job runs. Produces CPU, memory, disk I/O,
and disk usage charts that are embedded in the ci-doctor HTML report.

## Background

MicroShift CI jobs collect PCP archives via the `pcp-zeroconf` package
on the test host throughout the run, capturing system-wide performance
metrics at high resolution. This tool processes those archives and
produces time-series graphs at 15-second intervals.

### CPU Usage

Stacked area chart showing **User**, **System**, and **I/O Wait** CPU
usage (0-100%). Useful for identifying CPU saturation and I/O-bound
workloads during test runs.

### Memory Usage

Stacked area chart showing **Used** and **Cached** memory in GB, with
a dashed **Total** line. Useful for detecting memory pressure.

### Disk I/O

Shows **Disk Read OPS**, **Disk Write OPS**, and **Disk Await** (ms).
Disk Await is the average time I/O requests spend waiting to be serviced.
When await rises above ~10 ms, etcd heartbeats can be missed. Reports
the max await across all block devices at each sample point.

### Disk Usage

Per-partition line chart showing fill percentage (0-100%) over time.
Legend includes device name, mount point, capacity, and peak usage.
Useful for detecting disk space exhaustion during image builds and tests.

## PCP Data Location in CI Artifacts

```text
artifacts/<test_name>/openshift-microshift-infra-pmlogs/artifacts/<ci_hostname>/
```

The directory contains files like `yyyymmdd.hh.mm.{0,index,meta}` and a
`Latest` folio file.

## Prerequisites

- `pcp-export-pcp2json` package (provides the `pcp2json` command)
- Python 3 with `matplotlib`

Install:

```bash
sudo dnf install -y pcp-export-pcp2json
pip install matplotlib
```

## Usage

Graphs are generated automatically by `doctor.sh graphs`:

```bash
bash doctor.sh graphs --workdir /tmp/microshift-ci-claude-workdir.YYMMDD
```

This finds all PCP archives in downloaded artifacts and produces PNG
graphs at `${WORKDIR}/graphs/<build_id>/`. The `finalize` step then
embeds them as base64 in the HTML report with a tabbed UI per job.

## Output

| File | Description |
|---|---|
| `1_cpu_usage.png` | CPU usage stacked area: User (blue), I/O Wait (orange), System (red) |
| `2_mem_usage.png` | Memory usage stacked area: Used (red), Cached (orange), Total (dashed) |
| `3_disk_io.png` | Disk I/O chart: Read OPS (blue), Write OPS (red), Await (green dashed) |
| `4_disk_usage.png` | Disk usage by partition: fill % per mount point |

Numeric prefixes control tab display order in the HTML report.

## Files

| File | Purpose |
|---|---|
| `generate-graphs.sh` | Orchestrator: finds PCP archives, runs extraction and plotting in parallel |
| `extract_cpu.sh` | Runs `pcp2json` for CPU metrics, pipes through `parse_cpu.py` |
| `parse_cpu.py` | Parses pcp2json CPU output (user, sys, iowait, idle), normalizes to percentages |
| `plot_cpu.py` | Generates CPU usage PNG with stacked area chart and peak table |
| `extract_mem.sh` | Runs `pcp2json` for memory metrics, pipes through `parse_mem.py` |
| `parse_mem.py` | Parses pcp2json memory output (used, free, cached, physmem), converts to GB |
| `plot_mem.py` | Generates memory usage PNG with stacked area chart |
| `extract_io.sh` | Runs `pcp2json` for disk metrics, pipes through `parse_pcp.py` |
| `parse_pcp.py` | Parses pcp2json disk output, aggregates per-device (sum read/write, max await) |
| `plot_io.py` | Generates disk I/O PNG with dual Y-axes (OPS + await) |
| `extract_disk_usage.sh` | Runs `pcp2json` for filesystem metrics, pipes through `parse_disk_usage.py` |
| `parse_disk_usage.py` | Parses pcp2json filesys output, tracks all partitions as usage percentages |
| `plot_disk_usage.py` | Generates per-partition disk usage PNG with mount points in legend |

## Adding a New Graph Type

1. Create `extract_<type>.sh` and `parse_<type>.py` (follow existing patterns)
2. Create `plot_<type>.py`
3. Add a block to `generate-graphs.sh` with the next numeric prefix (e.g. `5_`)
4. No changes needed in `create-report.py` — it auto-discovers all `*.png` files
