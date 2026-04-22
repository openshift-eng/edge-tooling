#!/usr/bin/env python3
"""Check pagination info from a raw MCP jira_search response file.

Usage: python3 check-page.py <file_path>
Output: JSON with issues_count, has_more, and (when has_more is true) next_page_token.
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from _jira_transforms import load_json, unwrap_mcp_response


def main():
    if len(sys.argv) < 2:
        print("Usage: check-page.py <file_path>", file=sys.stderr)
        sys.exit(1)

    data = unwrap_mcp_response(load_json(sys.argv[1]))

    if isinstance(data, list):
        count = len(data)
    elif isinstance(data, dict):
        issues = data.get("issues", [])
        count = len(issues)
    else:
        print(f"Warning: unexpected MCP response type: {type(data).__name__}", file=sys.stderr)
        count = 0

    max_results = 50
    if isinstance(data, dict):
        max_results = data.get("max_results", 50)

    next_token = None
    if isinstance(data, dict):
        next_token = data.get("next_page_token")

    has_more = next_token is not None or count >= max_results

    result = {"issues_count": count, "has_more": has_more}
    if has_more:
        result["next_page_token"] = next_token
    print(json.dumps(result))


if __name__ == "__main__":
    main()
