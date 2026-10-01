#!/usr/bin/env python3
"""Build unique govulncheck scan targets from grouped CVE tickets.

Usage:
    build_scan_targets.py --workdir DIR [--input FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:50]


def _normalize_for_digest(value: str) -> str:
    return value.strip().lower()


def target_id(repo_slug: str, git_ref: str) -> str:
    """Readable slug pair plus a short digest of the full normalized inputs.

    Truncated slugify alone can collide (long slugs/refs); the digest keeps
    ids distinct while preserving the human-readable prefix.
    """
    digest = hashlib.sha256(
        f"{_normalize_for_digest(repo_slug)}\n{_normalize_for_digest(git_ref)}".encode()
    ).hexdigest()[:8]
    return f"{slugify(repo_slug)}--{slugify(git_ref)}--{digest}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build govulncheck scan targets")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    input_path = Path(args.input) if args.input else workdir / "jira" / "cves-grouped.json"
    output_path = Path(args.output) if args.output else workdir / "scans" / "scan-targets.json"

    if not input_path.is_file():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding="utf-8") as fh:
        grouped = json.load(fh)

    targets_map: dict[str, dict] = {}

    for group in grouped.get("groups", []):
        cve_ids = sorted({group["cve_id"]} | set())
        if group["cve_id"] != "UNKNOWN-CVE":
            group_cves = [group["cve_id"]]
        else:
            group_cves = sorted(
                {
                    cve
                    for ticket in group.get("tickets", [])
                    for cve in ticket.get("cve_ids", [])
                }
            )
            if group_cves:
                cve_ids = group_cves
            else:
                cve_ids = []

        for ticket in group.get("tickets", []):
            ticket_cves = ticket.get("cve_ids") or cve_ids
            for target in ticket.get("scan_targets", []):
                repo = target["repo"]
                repo_slug = repo["slug"]
                language = repo.get("language", "go")
                for git_ref in target.get("git_refs") or []:
                    if not git_ref:
                        continue
                    tid = target_id(repo_slug, git_ref)
                    if tid not in targets_map:
                        targets_map[tid] = {
                            "id": tid,
                            "repo_slug": repo_slug,
                            "repo_url": repo["url"],
                            "git_ref": git_ref,
                            "language": language,
                            "cve_ids": sorted(set(ticket_cves)),
                            "ticket_keys": [],
                            "components": [],
                            "versions": [],
                        }
                    entry = targets_map[tid]
                    entry["cve_ids"] = sorted(set(entry["cve_ids"]) | set(ticket_cves))
                    if ticket["key"] not in entry["ticket_keys"]:
                        entry["ticket_keys"].append(ticket["key"])
                    comp = ticket.get("component")
                    if comp and comp not in entry["components"]:
                        entry["components"].append(comp)
                    for version in ticket.get("versions", []):
                        if version not in entry["versions"]:
                            entry["versions"].append(version)

    targets = sorted(targets_map.values(), key=lambda t: (t["repo_slug"], t["git_ref"]))
    for target in targets:
        target["ticket_keys"] = sorted(target["ticket_keys"])
        target["components"] = sorted(target["components"])
        target["versions"] = sorted(target["versions"])

    go_targets = [t for t in targets if t.get("language") == "go"]
    skipped = [t for t in targets if t.get("language") != "go"]

    result = {
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": str(input_path),
        "target_count": len(targets),
        "go_target_count": len(go_targets),
        "skipped_non_go": len(skipped),
        "targets": go_targets,
        "skipped_targets": skipped,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print(
        f"Built {len(go_targets)} Go scan targets ({len(skipped)} non-Go skipped)",
        file=sys.stderr,
    )
    print(f"Written: {output_path}", file=sys.stderr)
    print(
        json.dumps(
            {
                "go_target_count": len(go_targets),
                "skipped_non_go": len(skipped),
                "output": str(output_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
