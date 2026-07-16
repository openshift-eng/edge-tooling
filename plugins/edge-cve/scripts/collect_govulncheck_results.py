#!/usr/bin/env python3
"""Collect govulncheck job results from labeled ConfigMaps.

Each scan job publishes its result as a ConfigMap labeled
`app.kubernetes.io/name=edge-cve-govulncheck-result`, `edge-cve/target-id`,
and `edge-cve/repo`. This collects them with a single `oc get configmaps`
call rather than exec-ing into a helper pod against shared storage.

Usage:
    collect_govulncheck_results.py --workdir DIR [--namespace NS] [--repo SLUG ...] [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESULT_LABEL = "app.kubernetes.io/name=edge-cve-govulncheck-result"


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def oc_base(namespace: str) -> list[str]:
    return ["oc", "-n", namespace]


def sanitize_label(raw: str) -> str:
    """Mirror the sanitization applied to labels in run_govulncheck_jobs.sh."""
    label = raw.replace("/", "--")
    label = re.sub(r"[^A-Za-z0-9._-]", "", label)
    return label[:63]


def wait_for_jobs(namespace: str, timeout: int) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        proc = run(
            oc_base(namespace)
            + [
                "get",
                "jobs",
                "-l",
                "app.kubernetes.io/name=edge-cve-govulncheck",
                "-o",
                "json",
            ],
            check=False,
        )
        if proc.returncode != 0:
            time.sleep(10)
            continue
        data = json.loads(proc.stdout or "{}")
        items = data.get("items", [])
        if not items:
            return {"complete": True, "active": 0, "failed": 0, "succeeded": 0}

        active = failed = succeeded = 0
        for job in items:
            status = job.get("status", {})
            if status.get("active"):
                active += 1
            if status.get("failed"):
                failed += int(status["failed"])
            if status.get("succeeded"):
                succeeded += int(status["succeeded"])

        if active == 0:
            return {
                "complete": True,
                "active": active,
                "failed": failed,
                "succeeded": succeeded,
            }
        time.sleep(15)

    return {"complete": False, "timeout": timeout}


def collect_result_configmaps(namespace: str, repo_filters: list[str]) -> list[dict]:
    selector = RESULT_LABEL
    if repo_filters:
        sanitized = [sanitize_label(r) for r in repo_filters]
        if len(sanitized) == 1:
            selector = f"{selector},edge-cve/repo={sanitized[0]}"
        else:
            selector = f"{selector},edge-cve/repo in ({','.join(sanitized)})"

    proc = run(
        oc_base(namespace) + ["get", "configmaps", "-l", selector, "-o", "json"],
        check=False,
    )
    if proc.returncode != 0:
        print(f"Warning: failed to list result configmaps: {proc.stderr}", file=sys.stderr)
        return []

    data = json.loads(proc.stdout or "{}")
    results = []
    for cm in data.get("items", []):
        name = cm.get("metadata", {}).get("name", "<unknown>")
        raw = cm.get("data", {}).get("result.json")
        if not raw:
            print(f"Warning: configmap {name} has no result.json key", file=sys.stderr)
            continue
        try:
            results.append(json.loads(raw))
        except json.JSONDecodeError:
            print(f"Warning: invalid JSON in configmap {name}", file=sys.stderr)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect govulncheck scan results")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--namespace", default="edge-cve-scans")
    parser.add_argument(
        "--repo",
        action="append",
        default=[],
        help="Only collect results for this repo slug (e.g. openshift/lvm-operator). Repeatable.",
    )
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--skip-wait", action="store_true")
    args = parser.parse_args()

    proc = run(["oc", "whoami"], check=False)
    if proc.returncode != 0:
        print("Error: not logged into OpenShift. Run 'oc login' first.", file=sys.stderr)
        sys.exit(1)

    workdir = Path(args.workdir)
    output_path = workdir / "scans" / "govulncheck-results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wait_info = {"skipped": True}
    if not args.skip_wait:
        wait_info = wait_for_jobs(args.namespace, args.timeout)
        if not wait_info.get("complete"):
            print(
                f"Warning: timed out after {args.timeout}s waiting for jobs",
                file=sys.stderr,
            )

    parsed_results = collect_result_configmaps(args.namespace, args.repo)

    payload = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "namespace": args.namespace,
        "repo_filters": args.repo,
        "wait": wait_info,
        "results": parsed_results,
    }

    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    affected = sum(1 for r in parsed_results if r.get("affected"))
    print(
        f"Collected {len(parsed_results)} results ({affected} affected)",
        file=sys.stderr,
    )
    print(f"Written: {output_path}", file=sys.stderr)
    print(
        json.dumps(
            {
                "result_count": len(parsed_results),
                "affected_count": affected,
                "output": str(output_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
