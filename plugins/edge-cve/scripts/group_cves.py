#!/usr/bin/env python3
"""Group parsed CVE tickets by component and CVE identity.

Deterministic grouping keys:
  - primary CVE ID (first CVE on ticket)
  - primary component
  - normalized summary stem (package/module token when present)

Tickets that share a group but differ only by OCP version are clustered
together. Ambiguous groups are flagged for optional LLM review.

Usage:
    group_cves.py --workdir DIR [--input FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

PACKAGE_TOKEN_RE = re.compile(r"\b(?:golang|go|openssl|glibc|kernel|etcd|cri-o|podman)\b", re.I)
MODULE_PATH_RE = re.compile(r"\b[\w./-]+/[\w./-]+\b")


def summary_stem(summary: str, go_modules: list[str]) -> str:
    """Derive a deterministic grouping stem from summary and modules."""
    if go_modules:
        return go_modules[0].lower()
    lowered = summary.lower()
    pkg = PACKAGE_TOKEN_RE.search(lowered)
    if pkg:
        return pkg.group(0).lower()
    # Fall back to first significant word chunk before version suffix.
    stem = re.sub(r"\[.*?\]", "", summary)
    stem = re.sub(r"\bCVE-\d{4}-\d+\b", "", stem, flags=re.I)
    stem = re.sub(r"\b4\.\d{1,2}\b", "", stem)
    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
    return stem[:80] or "unknown"


def group_key(ticket: dict) -> tuple:
    cve = ticket["cve_ids"][0] if ticket.get("cve_ids") else "UNKNOWN-CVE"
    component = ticket.get("component", "Unknown")
    stem = summary_stem(ticket.get("summary", ""), ticket.get("go_modules", []))
    return (cve, component, stem)


def build_group(key: tuple, tickets: list[dict]) -> dict:
    cve, component, stem = key
    versions = sorted({v for t in tickets for v in t.get("versions", [])})
    ticket_keys = sorted(t["key"] for t in tickets)
    repos = sorted({r["slug"] for t in tickets for r in t.get("repos", [])})

    needs_llm_review = False
    reasons: list[str] = []

    if cve == "UNKNOWN-CVE":
        needs_llm_review = True
        reasons.append("missing_cve_id")
    if len({t.get("summary", "") for t in tickets}) > 1 and len(versions) > 1:
        # Same CVE/component but materially different summaries across versions.
        summaries = {t.get("summary", "") for t in tickets}
        if len(summaries) > 1:
            needs_llm_review = True
            reasons.append("divergent_summaries_across_versions")
    if not repos:
        needs_llm_review = True
        reasons.append("no_repo_for_group")

    return {
        "group_id": f"{cve}::{component}::{stem}",
        "cve_id": cve,
        "component": component,
        "summary_stem": stem,
        "ticket_count": len(tickets),
        "ticket_keys": ticket_keys,
        "versions": versions,
        "repos": repos,
        "tickets": tickets,
        "needs_llm_review": needs_llm_review,
        "llm_review_reasons": reasons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Group parsed CVE tickets")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--llm-review-output", default="")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    input_path = Path(args.input) if args.input else workdir / "jira" / "cves-parsed.json"
    output_path = Path(args.output) if args.output else workdir / "jira" / "cves-grouped.json"
    llm_path = (
        Path(args.llm_review_output)
        if args.llm_review_output
        else workdir / "jira" / "cves-llm-review.json"
    )

    if not input_path.is_file():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding="utf-8") as fh:
        data = json.load(fh)

    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for ticket in data.get("tickets", []):
        buckets[group_key(ticket)].append(ticket)

    groups = [build_group(key, tickets) for key, tickets in sorted(buckets.items())]
    groups.sort(key=lambda g: (g["component"], g["cve_id"], g["summary_stem"]))

    llm_review = [g for g in groups if g["needs_llm_review"]]

    result = {
        "grouped_at": datetime.now(timezone.utc).isoformat(),
        "source": str(input_path),
        "group_count": len(groups),
        "ticket_count": data.get("count", 0),
        "llm_review_count": len(llm_review),
        "groups": groups,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    llm_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "instruction": (
            "Review these CVE groups and confirm whether tickets represent the same "
            "underlying vulnerability across versions. Merge or split groups if needed "
            "before launching govulncheck scans."
        ),
        "groups": llm_review,
    }
    with open(llm_path, "w", encoding="utf-8") as fh:
        json.dump(llm_payload, fh, indent=2)

    print(
        f"Grouped {result['ticket_count']} tickets into {len(groups)} groups "
        f"({len(llm_review)} need LLM review)",
        file=sys.stderr,
    )
    print(f"Written: {output_path}", file=sys.stderr)
    print(f"Written: {llm_path}", file=sys.stderr)
    print(
        json.dumps(
            {
                "group_count": len(groups),
                "llm_review_count": len(llm_review),
                "output": str(output_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
