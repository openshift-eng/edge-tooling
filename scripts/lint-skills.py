#!/usr/bin/env python3
"""Lint SKILL.md files for content quality.

Checks skill content beyond what `marketplace validate` covers (structural).
See plugins/docs/SKILL-GUIDELINES.md for the full quality standards.

Usage:
    python3 scripts/lint-skills.py [OPTIONS] [FILE...]

    --check-all-files  Lint all SKILL.md files in plugins/
    --hook             Hook mode: read JSON stdin, output JSON, always exit 0
    --severity LEVEL   Minimum severity to report: error, warning (default: warning)
    FILE               One or more SKILL.md paths (default: changed files vs main)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Finding:
    file: str
    check_id: str
    check_name: str
    severity: str
    message: str
    line: int = 0


@dataclass
class SkillFile:
    path: str
    frontmatter: dict = field(default_factory=dict)
    frontmatter_lines: dict = field(default_factory=dict)
    body: str = ""
    body_start_line: int = 0
    raw: str = ""
    lines: list = field(default_factory=list)
    frontmatter_raw: str = ""

    def parse(self):
        content = Path(self.path).read_text(encoding="utf-8")
        self.raw = content
        self.lines = content.splitlines()

        fm_lines = []
        body_lines = []
        in_frontmatter = False
        fm_closed = False

        for lineno, line in enumerate(self.lines, 1):
            if not fm_closed and line.strip() == "---":
                if not in_frontmatter:
                    in_frontmatter = True
                    continue
                else:
                    fm_closed = True
                    in_frontmatter = False
                    self.body_start_line = lineno + 1
                    continue

            if in_frontmatter:
                fm_lines.append((lineno, line))
            elif fm_closed:
                body_lines.append(line)

        self.frontmatter_raw = "\n".join(line for _, line in fm_lines)
        self.body = "\n".join(body_lines)
        self._body_outside_code = None
        self.frontmatter, self.frontmatter_lines = self._parse_frontmatter(fm_lines)
        return fm_closed

    def _parse_frontmatter(self, lines):
        result = {}
        result_lines = {}
        current_key = None

        for lineno, line in lines:
            if not line.strip():
                continue

            kv_match = re.match(r"^([a-zA-Z_-]+)\s*:\s*(.*)", line)
            if kv_match:
                current_key = kv_match.group(1)
                value = kv_match.group(2).strip()
                value = value.strip('"').strip("'")
                result[current_key] = value
                result_lines[current_key] = lineno
            elif current_key and re.match(r"^\s+-\s+", line):
                existing = result.get(current_key, "")
                item = re.sub(r"^\s+-\s+", "", line).strip()
                if existing:
                    result[current_key] = f"{existing}, {item}"
                else:
                    result[current_key] = item

        return result, result_lines

    def fm_line(self, key):
        return self.frontmatter_lines.get(key, 1)

    def body_outside_code_blocks(self):
        if self._body_outside_code is not None:
            return self._body_outside_code
        outside = []
        in_code_block = False
        for line in self.body.splitlines():
            if line.strip().startswith("```"):
                in_code_block = not in_code_block
                continue
            if not in_code_block:
                outside.append(line)
        self._body_outside_code = "\n".join(outside)
        return self._body_outside_code

    def find_body_line(self, pattern, flags=0):
        for i, line in enumerate(self.body.splitlines(), self.body_start_line):
            if re.search(pattern, line, flags):
                return i
        return self.body_start_line


PLACEHOLDER_PATTERNS = [
    r"\{\{.*\}\}",
    r"^TODO\b(?!\s+file)",
    r"FIXME",
    r"CHANGEME",
]

DESTRUCTIVE_KEYWORDS = [
    "deploy",
    "delete",
    "destroy",
    "push",
    "commit",
    "reset",
    "force",
    "rm -rf",
    "ansible-playbook",
    "kubectl delete",
    "kubectl apply",
    "oc delete",
    "oc adm",
    "helm install",
    "helm upgrade",
]

READONLY_DESCRIPTION_PATTERNS = [
    r"\bread[- ]?only\b",
    r"\bdoes not modify\b",
    r"\bnon[- ]?destructive\b",
    r"\bno side[- ]?effects?\b",
]

CREDENTIAL_PATTERNS = [
    r"Bearer\s+[A-Za-z0-9_\-\.]{20,}",
    r"token\s*=\s*['\"][A-Za-z0-9_\-\.]{20,}['\"]",
    r"password\s*=\s*['\"][^'\"]{8,}['\"]",
    r"api_key\s*=\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]",
    r"secret\s*=\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]",
]

CONFIRMATION_PATTERNS = [
    r"AskUserQuestion",
    r"[Cc]onfirm",
    r"[Aa]sk the user",
    r"[Uu]ser confirmation",
    r"[Pp]roceed\?",
]


def check_name_present(skill):
    if not skill.frontmatter.get("name"):
        return Finding(
            skill.path, "E002", "name-present", "error",
            "'name' field missing or empty in frontmatter", line=1,
        )
    return None


def check_description_present(skill):
    if not skill.frontmatter.get("description"):
        return Finding(
            skill.path, "E003", "description-present", "error",
            "'description' field missing or empty in frontmatter", line=1,
        )
    return None


def check_description_not_placeholder(skill):
    desc = skill.frontmatter.get("description", "")
    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, desc, re.IGNORECASE):
            return Finding(
                skill.path, "E004", "description-not-placeholder", "error",
                f"Description contains placeholder text: {desc!r}",
                line=skill.fm_line("description"),
            )
    return None


def check_body_not_empty(skill):
    if not skill.body.strip():
        return Finding(
            skill.path, "E005", "body-not-empty", "error",
            "Skill body below frontmatter is empty",
            line=skill.body_start_line,
        )
    return None


def check_description_length(skill):
    desc = skill.frontmatter.get("description", "")
    if len(desc) > 1024:
        return Finding(
            skill.path, "E006", "description-length", "error",
            f"Description is {len(desc)} chars (max 1024)",
            line=skill.fm_line("description"),
        )
    return None


NAME_SEGMENT_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _validate_name_segment(segment):
    """Validate a single name segment (before or after colon)."""
    if "--" in segment:
        return "consecutive hyphens"
    if not NAME_SEGMENT_RE.match(segment):
        return ("must contain only lowercase letters, numbers, and hyphens, "
                "and must not start or end with a hyphen")
    return None


def check_name_format(skill):
    name = skill.frontmatter.get("name", "")
    if not name:
        return None
    if len(name) > 64:
        return Finding(
            skill.path, "E008", "name-format", "error",
            f"Name is {len(name)} chars (max 64)",
            line=skill.fm_line("name"),
        )
    # Claude Code extension: names may use plugin:skill namespace separator
    segments = name.split(":")
    if len(segments) > 2:
        return Finding(
            skill.path, "E008", "name-format", "error",
            "Name may contain at most one colon (plugin:skill namespace)",
            line=skill.fm_line("name"),
        )
    for segment in segments:
        if not segment:
            return Finding(
                skill.path, "E008", "name-format", "error",
                "Name has empty segment around colon",
                line=skill.fm_line("name"),
            )
        err = _validate_name_segment(segment)
        if err:
            return Finding(
                skill.path, "E008", "name-format", "error",
                f"Name segment '{segment}' — {err}",
                line=skill.fm_line("name"),
            )
    return None


def check_name_matches_dir(skill):
    name = skill.frontmatter.get("name", "")
    if not name:
        return None
    # For namespaced names (plugin:skill), the skill part must match the dir
    dir_name = name.split(":")[-1] if ":" in name else name
    parent_dir = os.path.basename(os.path.dirname(skill.path))
    if parent_dir and parent_dir != dir_name:
        return Finding(
            skill.path, "E009", "name-matches-dir", "error",
            f"Name '{name}' (skill segment '{dir_name}') does not match "
            f"parent directory '{parent_dir}'",
            line=skill.fm_line("name"),
        )
    return None


def check_description_quality(skill):
    desc = skill.frontmatter.get("description", "")
    if not desc:
        return None

    bad_starts = [
        "a skill", "this skill", "skill that", "a plugin", "this plugin",
    ]
    lower = desc.lower().lstrip()
    for bad in bad_starts:
        if lower.startswith(bad):
            return Finding(
                skill.path, "W001", "description-quality", "warning",
                f"Description starts with '{bad}...' — front-load the action verb "
                f"or use 'Use when...' pattern",
                line=skill.fm_line("description"),
            )
    return None


def check_line_count(skill):
    count = len(skill.lines)
    if count > 1000:
        return Finding(
            skill.path, "E007", "line-count-max", "error",
            f"File is {count} lines (max 1000). "
            f"Split into orchestrator + sub-agents or move "
            f"reference material to separate files",
            line=1,
        )
    if count > 500:
        return Finding(
            skill.path, "W002", "line-count", "warning",
            f"File is {count} lines (recommended max 500). "
            f"Consider splitting into orchestrator + sub-agents or moving "
            f"reference material to separate files",
            line=1,
        )
    return None


def check_has_examples(skill):
    patterns = [
        r"^#+\s*(Example|Usage|Synopsis)",
        r"^#+\s*.*[Ee]xample",
        r"\*\*User:\*\*",
        r"^```\s*text\s*$",
    ]
    for pattern in patterns:
        if re.search(pattern, skill.body, re.MULTILINE):
            return None
    return Finding(
        skill.path, "W003", "has-examples", "warning",
        "No Examples, Usage, or Synopsis section found. "
        "Adding concrete examples helps Claude understand expected behavior",
        line=skill.body_start_line,
    )


def check_allowed_tools(skill):
    if skill.frontmatter.get("user-invocable", "false").lower() == "false":
        return None
    if not skill.frontmatter.get("allowed-tools"):
        return Finding(
            skill.path, "W004", "allowed-tools", "warning",
            "'allowed-tools' not specified in frontmatter. "
            "Specifying tools limits blast radius",
            line=skill.fm_line("user-invocable"),
        )
    return None


def check_has_steps(skill):
    step_patterns = [
        r"^#+\s*(Step|Phase|Workflow)",
        r"^###?\s*\d+[\.\):]",
        r"^#+\s*\d+[\.\)]",
    ]
    for pattern in step_patterns:
        if re.search(pattern, skill.body, re.MULTILINE):
            return None

    numbered_lines = re.findall(r"^\d+\.\s+", skill.body, re.MULTILINE)
    if len(numbered_lines) >= 2:
        return None

    return Finding(
        skill.path, "W005", "has-steps", "warning",
        "No Steps, Workflow, or Phase sections found. "
        "Numbered steps help Claude execute the skill reliably",
        line=skill.body_start_line,
    )


def check_argument_hint(skill):
    if "$ARGUMENTS" not in skill.body and "${ARGUMENTS}" not in skill.body:
        return None
    if not skill.frontmatter.get("argument-hint"):
        return Finding(
            skill.path, "W006", "argument-hint", "warning",
            "Skill uses $ARGUMENTS but 'argument-hint' is not set in frontmatter. "
            "Add argument-hint to show users what arguments are expected",
            line=skill.find_body_line(r"\$\{?ARGUMENTS\}?"),
        )
    return None


def check_plugin_dir_usage(skill):
    text = skill.body_outside_code_blocks()
    for match in re.finditer(r"/(usr|home|opt|etc)/\S+", text):
        start = match.start()
        prefix = text[max(0, start - 20):start]
        if "$PLUGIN_DIR" in prefix or "$WORKDIR" in prefix or "${PLUGIN_DIR" in prefix:
            continue
        hit_line = skill.find_body_line(re.escape(match.group()))
        return Finding(
            skill.path, "W007", "plugin-dir-usage", "warning",
            "Absolute paths found in skill body. "
            "Consider using $PLUGIN_DIR for plugin-relative paths",
            line=hit_line,
        )
    return None


def check_side_effect_guard(skill):
    text = skill.body_outside_code_blocks().lower()

    first_keyword_line = 0
    for keyword in DESTRUCTIVE_KEYWORDS:
        if keyword in text:
            first_keyword_line = skill.find_body_line(
                re.escape(keyword), flags=re.IGNORECASE
            )
            break

    if not first_keyword_line:
        return None

    for pattern in CONFIRMATION_PATTERNS:
        if re.search(pattern, skill.body_outside_code_blocks(), re.IGNORECASE):
            return None

    if skill.frontmatter.get("disable-model-invocation", "").lower() == "true":
        return None

    return Finding(
        skill.path, "W008", "side-effect-guard", "warning",
        "Skill appears to have side effects (destructive keywords found) "
        "but no user confirmation pattern or disable-model-invocation detected. "
        "Add AskUserQuestion for user confirmation or set "
        "disable-model-invocation: true",
        line=first_keyword_line,
    )


def check_description_intent_mismatch(skill):
    desc = skill.frontmatter.get("description", "").lower()
    if not desc:
        return None

    claims_readonly = any(
        re.search(p, desc) for p in READONLY_DESCRIPTION_PATTERNS
    )
    if not claims_readonly:
        return None

    body_lower = skill.body_outside_code_blocks().lower()
    for keyword in DESTRUCTIVE_KEYWORDS:
        if keyword in body_lower:
            if keyword in desc or f"{keyword}s" in desc:
                continue
            hit = skill.find_body_line(
                re.escape(keyword), flags=re.IGNORECASE
            )
            return Finding(
                skill.path, "W009", "description-intent-mismatch",
                "warning",
                f"Description implies read-only but body contains "
                f"'{keyword}'. Align description with actual capabilities",
                line=hit,
            )
    return None


def check_credential_pattern(skill):
    text = skill.body_outside_code_blocks()
    for pattern in CREDENTIAL_PATTERNS:
        match = re.search(pattern, text)
        if match:
            hit = skill.find_body_line(re.escape(match.group()))
            return Finding(
                skill.path, "W010", "credential-pattern", "warning",
                "Possible hardcoded credential pattern found. "
                "Use environment variables instead",
                line=hit,
            )
    return None


def check_orchestrator_edge_cases(skill):
    is_orchestrator = bool(
        re.search(r"^#+\s*Phase\b", skill.body, re.MULTILINE)
        or re.search(r"\bAgent\b.*\bspawn", skill.body, re.IGNORECASE)
        or ("Agent" in skill.frontmatter.get("allowed-tools", "")
            and len(skill.lines) > 150)
    )
    if not is_orchestrator:
        return None

    if re.search(r"^#+\s*Edge\s+Case", skill.body, re.MULTILINE):
        return None

    return Finding(
        skill.path, "W011", "orchestrator-edge-cases", "warning",
        "Orchestrator skill (has phases/agents) but no Edge Cases "
        "section. Document failure modes and how they are handled",
        line=skill.body_start_line,
    )


def check_description_too_short(skill):
    desc = skill.frontmatter.get("description", "")
    if desc and len(desc) < 20:
        return Finding(
            skill.path, "W012", "description-too-short", "warning",
            f"Description is only {len(desc)} chars. Descriptions "
            f"under 20 chars are too terse for reliable auto-invocation",
            line=skill.fm_line("description"),
        )
    return None


ALL_CHECKS = [
    check_name_present,
    check_name_format,
    check_name_matches_dir,
    check_description_present,
    check_description_not_placeholder,
    check_body_not_empty,
    check_description_length,
    check_description_quality,
    check_description_too_short,
    check_description_intent_mismatch,
    check_line_count,
    check_has_examples,
    check_allowed_tools,
    check_has_steps,
    check_argument_hint,
    check_plugin_dir_usage,
    check_side_effect_guard,
    check_credential_pattern,
    check_orchestrator_edge_cases,
]


def lint_file(path):
    skill = SkillFile(path=path)
    try:
        has_frontmatter = skill.parse()
    except FileNotFoundError:
        return [Finding(path, "E000", "file-not-found", "error",
                        f"File not found: {path}", line=0)]
    except (PermissionError, UnicodeDecodeError, OSError) as e:
        return [Finding(path, "E000", "file-read-error", "error",
                        f"Cannot read file: {e}", line=0)]

    if not has_frontmatter:
        return [Finding(
            path, "E001", "frontmatter-exists", "error",
            "YAML frontmatter between --- markers not found", line=1,
        )]

    findings = []
    for check_fn in ALL_CHECKS:
        result = check_fn(skill)
        if result:
            findings.append(result)

    return findings


def find_all_skills(repo_root):
    skills = []
    plugins_dir = os.path.join(repo_root, "plugins")
    if not os.path.isdir(plugins_dir):
        return skills

    for root, dirs, files in os.walk(plugins_dir):
        dirs[:] = [d for d in dirs if d not in (".templates", "tests", "docs")]
        for f in files:
            if f == "SKILL.md":
                skills.append(os.path.join(root, f))

    return sorted(skills)


def find_changed_skills(repo_root):
    base_ref = os.environ.get("BASE_REF", "main")
    merge_base = subprocess.run(
        ["git", "merge-base", f"origin/{base_ref}", "HEAD"],
        capture_output=True, text=True, cwd=repo_root,
    )
    if merge_base.returncode == 0 and merge_base.stdout.strip():
        resolved = merge_base.stdout.strip()
    else:
        merge_base_local = subprocess.run(
            ["git", "merge-base", base_ref, "HEAD"],
            capture_output=True, text=True, cwd=repo_root,
        )
        if merge_base_local.returncode == 0 and merge_base_local.stdout.strip():
            resolved = merge_base_local.stdout.strip()
        else:
            print(f"Warning: could not determine merge-base for '{base_ref}'. "
                  f"No changed files will be detected. Use --check-all-files or provide "
                  f"explicit file paths.", file=sys.stderr)
            resolved = "HEAD"

    result = subprocess.run(
        [
            "git",
            "diff",
            resolved,
            "--name-only",
            "--diff-filter=ACM",
            "--",
            "*/SKILL.md",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )

    if result.returncode != 0:
        print(f"Warning: git diff failed (ref={resolved}): "
              f"{result.stderr.strip()}", file=sys.stderr)
        return []

    files = []
    for line in result.stdout.strip().splitlines():
        if line:
            full_path = os.path.join(repo_root, line)
            if os.path.isfile(full_path):
                files.append(full_path)

    return sorted(files)


def format_human(findings, use_color=False):
    if not findings:
        print("No skill quality issues found.", file=sys.stderr)
        return

    if use_color:
        c = {
            "error": "\033[31m", "warning": "\033[33m",
            "bold": "\033[1m", "dim": "\033[2m", "reset": "\033[0m",
        }
    else:
        c = {"error": "", "warning": "", "bold": "", "dim": "", "reset": ""}

    grouped = {}
    for f in findings:
        grouped.setdefault(f.file, []).append(f)

    for filepath, file_findings in grouped.items():
        print(f"{c['bold']}{filepath}{c['reset']}", file=sys.stderr)

        for f in file_findings:
            loc = f"L{f.line}" if f.line else ""
            prefix = f"  {c['dim']}{loc:>4}{c['reset']} " if loc else "  "
            severity = f"{c[f.severity]}{f.severity.upper()}{c['reset']}"
            check = f"{c['dim']}[{f.check_id}]{c['reset']}"
            print(f"{prefix}{severity} {check} {f.message}", file=sys.stderr)

        print("", file=sys.stderr)

    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    summary = f"{errors} error(s), {warnings} warning(s) in {len(grouped)} file(s)"
    print(summary, file=sys.stderr)


def format_json(findings):
    errors = [f for f in findings if f.severity == "error"]
    if not errors:
        print(json.dumps({}))
        return

    lines = []
    for f in errors:
        lines.append(f"  {f.file}:{f.line} [{f.check_id}] {f.message}")

    reason = (
        "SKILL QUALITY ERRORS found in modified SKILL.md files. "
        "See plugins/docs/SKILL-GUIDELINES.md for details.\n"
        + "\n".join(lines)
    )

    output = {"decision": "block", "reason": reason}
    print(json.dumps(output))


def get_repo_root():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        print("Warning: git not found, using current directory as repo root",
              file=sys.stderr)
        return os.getcwd()
    if result.returncode == 0:
        return result.stdout.strip()
    return os.getcwd()


def _read_hook_stdin():
    """Read JSON from stdin in hook mode to extract cwd."""
    if sys.stdin.isatty():
        return None
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return None
        hook_input = json.loads(raw)
        return hook_input.get("cwd")
    except (json.JSONDecodeError, ValueError):
        return None


def _run_linter(args):
    """Core linting logic, separated for hook-mode crash resilience."""
    repo_root = get_repo_root()

    if args.files:
        files = [os.path.abspath(f) for f in args.files]
    elif args.check_all_files:
        files = find_all_skills(repo_root)
    else:
        files = find_changed_skills(repo_root)

    if not files:
        if args.hook:
            if not args.hook:
                print(json.dumps({}))
        else:
            print("No SKILL.md files to lint", file=sys.stderr)
        return False

    all_findings = []
    for f in files:
        if not os.path.isfile(f):
            print(f"Warning: {f} not found, skipping", file=sys.stderr)
            continue
        all_findings.extend(lint_file(f))

    if args.severity == "error":
        all_findings = [f for f in all_findings if f.severity == "error"]

    for f in all_findings:
        f.file = os.path.relpath(f.file, repo_root)

    if args.hook:
        errors = [f for f in all_findings if f.severity == "error"]
        if errors:
            format_json(all_findings)
        elif not args.hook:
            print(json.dumps({}))
    else:
        use_color = args.color or sys.stderr.isatty()
        format_human(all_findings, use_color=use_color)

    return any(f.severity == "error" for f in all_findings)


def main():
    hook_cwd = _read_hook_stdin()

    parser = argparse.ArgumentParser(
        description="Lint SKILL.md files for content quality"
    )
    parser.add_argument(
        "--check-all-files",
        action="store_true",
        help="Lint all SKILL.md files in plugins/",
    )
    parser.add_argument(
        "--hook",
        action="store_true",
        help="Hook mode: read JSON from stdin, output JSON, always exit 0",
    )
    parser.add_argument(
        "--severity",
        choices=["error", "warning"],
        default="warning",
        help="Minimum severity to report (default: warning)",
    )
    parser.add_argument(
        "--color",
        action="store_true",
        help="Force colored output",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="SKILL.md files to lint (default: changed files vs main)",
    )

    args = parser.parse_args()

    if hook_cwd:
        os.chdir(hook_cwd)

    if args.hook:
        try:
            _run_linter(args)
        except Exception as e:
            print(json.dumps({
                "decision": "block",
                "reason": f"Skill linter crashed: {e}",
            }))
        sys.exit(0)
    else:
        has_errors = _run_linter(args)
        sys.exit(1 if has_errors else 0)


if __name__ == "__main__":
    main()
