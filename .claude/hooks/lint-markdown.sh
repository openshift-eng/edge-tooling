#!/usr/bin/env bash
set -euo pipefail

input=$(cat)
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')

[[ -z "$file_path" ]] && exit 0
[[ "$file_path" != *.md ]] && exit 0
[[ ! -f "$file_path" ]] && exit 0

output=$(markdownlint "$file_path" 2>&1) && exit 0

jq -n --arg reason "markdownlint failed:
$output" '{"decision":"block","reason":$reason}'
