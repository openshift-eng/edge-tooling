#!/usr/bin/env python3
"""Build an LLM-safe ticket file for govulncheck analysis.

Private tickets (is_private) are reduced to key/url stubs so summaries, CVE
IDs, and other fields never reach the analysis subagent. Non-private tickets
are copied unchanged.

Usage:
    redact_parsed_for_analysis.py --workdir DIR
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def redact_ticket(ticket: dict) -> dict:
    if ticket.get("is_private"):
        return {
            "key": ticket.get("key", ""),
            "url": ticket.get("url", ""),
            "is_private": True,
            "redacted": True,
        }
    return ticket


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Redact private tickets for LLM analysis input"
    )
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--input", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    input_path = Path(args.input) if args.input else workdir / "jira" / "cves-parsed.json"
    output_path = (
        Path(args.output)
        if args.output
        else workdir / "jira" / "cves-parsed-for-analysis.json"
    )

    if not input_path.is_file():
        print(f"Error: input not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, encoding="utf-8") as fh:
        data = json.load(fh)

    tickets = data.get("tickets", [])
    if not isinstance(tickets, list):
        print("Error: 'tickets' must be a list", file=sys.stderr)
        sys.exit(1)

    redacted_tickets = [redact_ticket(t) for t in tickets if isinstance(t, dict)]
    private_count = sum(1 for t in redacted_tickets if t.get("is_private"))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(input_path),
        "purpose": "LLM analysis input; private tickets redacted to key/url only",
        "count": len(redacted_tickets),
        "private_redacted_count": private_count,
        "tickets": redacted_tickets,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print(
        f"Wrote {len(redacted_tickets)} tickets "
        f"({private_count} private redacted) -> {output_path}",
        file=sys.stderr,
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "ticket_count": len(redacted_tickets),
                "private_redacted_count": private_count,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
