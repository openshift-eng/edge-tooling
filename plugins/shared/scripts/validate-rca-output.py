#!/usr/bin/env python3
"""Hook validator for prow-job-analyzer agent output.

Detects the hook type via the payload's ``hook_event_name`` field:
  - SubagentStop: validate ``last_assistant_message`` directly, falling
    back to the JSONL transcript when it is absent.
  - Stop: gated by the CI_DOCTOR_RCA_SESSION env var (no-op when unset);
    the last assistant message is extracted from the transcript.
  - Any other/absent event: fail safe and skip (never block).

Do not detect the hook type by payload shape: on Claude Code 2.1.x the
main-agent Stop payload also carries ``last_assistant_message``, so a
shape check would validate ordinary prose and block every turn.

Validates the message against the expected JSON schema and returns a
block decision with specific corrections when validation fails.
"""

import html as html_mod
import json
import os
import re
import sys

REQUIRED_FIELDS = {
    "severity", "stack_layer", "step_name", "error_signature",
    "root_cause", "raw_error", "infrastructure_failure",
    "job_url", "job_name", "release", "remediation", "finished",
    "causal_chain", "confidence", "analysis_gaps", "scenarios",
}

NON_EMPTY_STRING_FIELDS = {
    "error_signature", "raw_error", "job_url", "job_name", "finished",
    "step_name", "root_cause", "remediation", "release",
}

# Keep in sync with prow-job-analyzer.md (field descriptions,
# severity rubric, and JSON schema) in each plugin.
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_STACK_LAYERS = {
    "AWS Infra", "External Infrastructure", "build phase", "deploy phase",
    "test setup phase", "Test Configuration", "test", "teardown",
}


BINARY_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".tar.xz", ".gz", ".bz2", ".xz", ".zip")


def _log_debug(event, **fields):
    """Append a JSONL event to the debug log (CI_DOCTOR_HOOK_LOG).

    Uses O_APPEND for concurrency safety — multiple hook processes may
    write to the same file simultaneously.
    """
    log_path = os.environ.get("CI_DOCTOR_HOOK_LOG")
    print(f"DEBUG: _log_debug: CI_DOCTOR_HOOK_LOG={log_path!r}, event={event}", file=sys.stderr)
    if not log_path:
        return
    entry = {"event": event, **fields}
    try:
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, (json.dumps(entry) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        pass


def _read_lines(path, cache):
    """Read file lines with caching to avoid re-reading large build logs.

    Opens with newline='' and strips ``\\r`` to match grep's line counting.
    Python's universal newlines treat standalone ``\\r`` as a line break,
    but grep splits only on ``\\n`` — log files with bare carriage returns
    cause line-number mismatches without this normalization.
    """
    if path in cache:
        return cache[path]
    try:
        with open(path, newline='', errors="replace") as f:
            lines = f.read().replace('\r', '').splitlines(True)
    except OSError:
        lines = None
    cache[path] = lines
    return lines


def validate_evidence(evidence, quote, prefix, file_cache):
    """Validate that a causal_chain evidence citation is real.

    Checks: format (absolute_path:line), file exists, line in range,
    quote appears on cited line.  Returns a list of error strings.
    """
    m = re.fullmatch(r"(.+):(\d+)", evidence)
    if not m:
        return [f"{prefix}: evidence must be absolute_path:line_number, got: {evidence}"]

    path, line_no = m.group(1), int(m.group(2))

    if not os.path.isabs(path):
        return [f"{prefix}: evidence path must be absolute, got: {path}"]

    if not os.path.isfile(path):
        return [f"{prefix}: evidence file not found: {path}"]

    if any(path.endswith(ext) for ext in BINARY_EXTENSIONS):
        return []

    lines = _read_lines(path, file_cache)
    if lines is None:
        return [f"{prefix}: evidence file could not be read: {path}"]

    if line_no < 1 or line_no > len(lines):
        return [f"{prefix}: evidence cites line {line_no} but file has only {len(lines)} lines"]

    if not isinstance(quote, str) or not quote:
        return [f"{prefix}: 'quote' must be a non-empty string"]

    # Decode HTML entities first (&#34; \u2192 ", &#xa; \u2192 \n, &amp; \u2192 &, etc.)
    # so that entity-encoded log lines match plain-text quotes.
    cited_raw = html_mod.unescape(lines[line_no - 1])
    quote_raw = html_mod.unescape(quote)

    cited_line = " ".join(cited_raw.split()).lower()
    normalized_quote = " ".join(quote_raw.split()).lower()

    # Normalize escape sequences and Unicode smart quotes so that
    # cosmetic differences in quoting style don't cause false negatives.
    cited_line = (cited_line
                  .replace('\\"', '"')
                  .replace('\u201c', '"').replace('\u201d', '"')
                  .replace('\u2018', "'").replace('\u2019', "'"))
    normalized_quote = (normalized_quote
                        .replace('\\"', '"')
                        .replace('\u201c', '"').replace('\u201d', '"')
                        .replace('\u2018', "'").replace('\u2019', "'"))

    if normalized_quote not in cited_line:
        actual_preview = cited_line[:200] + ("..." if len(cited_line) > 200 else "")
        return [
            f"{prefix}: quote not found on line {line_no}. "
            f"Expected: \"{normalized_quote}\". "
            f"Actual line {line_no}: \"{actual_preview}\""
        ]

    return []


def validate_entry(entry, index, file_cache):
    errors = []

    missing = REQUIRED_FIELDS - set(entry.keys())
    if missing:
        errors.append(f"entry[{index}]: missing required fields: {', '.join(sorted(missing))}")

    for field in NON_EMPTY_STRING_FIELDS:
        val = entry.get(field)
        if not isinstance(val, str) or not val:
            errors.append(f"entry[{index}]: '{field}' must be a non-empty string")

    sev = entry.get("severity")
    if isinstance(sev, bool) or not isinstance(sev, int) or not (1 <= sev <= 5):
        errors.append(f"entry[{index}]: 'severity' must be an integer 1-5, got {sev!r}")

    infra = entry.get("infrastructure_failure")
    if not isinstance(infra, bool):
        errors.append(f"entry[{index}]: 'infrastructure_failure' must be a boolean, got {type(infra).__name__}")

    layer = entry.get("stack_layer")
    if not isinstance(layer, str) or layer not in VALID_STACK_LAYERS:
        errors.append(f"entry[{index}]: 'stack_layer' must be one of {sorted(VALID_STACK_LAYERS)}, got {layer!r}")

    conf = entry.get("confidence")
    if not isinstance(conf, str) or conf not in VALID_CONFIDENCE:
        errors.append(f"entry[{index}]: 'confidence' must be one of {sorted(VALID_CONFIDENCE)}, got {conf!r}")

    chain = entry.get("causal_chain")
    if not isinstance(chain, list):
        errors.append(f"entry[{index}]: 'causal_chain' must be a non-empty array, got {type(chain).__name__}")
    elif not chain:
        errors.append(f"entry[{index}]: 'causal_chain' must be a non-empty array")
    else:
        for ci, link in enumerate(chain):
            if not isinstance(link, dict):
                errors.append(f"entry[{index}].causal_chain[{ci}]: must be an object")
                continue
            for key in ("cause", "evidence", "quote"):
                val = link.get(key)
                if not isinstance(val, str) or not val:
                    errors.append(f"entry[{index}].causal_chain[{ci}]: '{key}' must be a non-empty string")
            evidence = link.get("evidence", "")
            quote = link.get("quote", "")
            if isinstance(evidence, str) and evidence:
                errors.extend(validate_evidence(
                    evidence, quote,
                    f"entry[{index}].causal_chain[{ci}]", file_cache))

    VALID_GAP_REASONS = {
        "artifact_unavailable", "extraction_failed", "deprioritized",
        "not_realized", "out_of_scope",
    }

    gaps = entry.get("analysis_gaps")
    if not isinstance(gaps, list):
        errors.append(f"entry[{index}]: 'analysis_gaps' must be an array, got {type(gaps).__name__}")
    else:
        for gi, item in enumerate(gaps):
            if isinstance(item, str):
                # Backward compat: plain strings are accepted
                continue
            if not isinstance(item, dict):
                errors.append(f"entry[{index}].analysis_gaps[{gi}]: must be a string or object, got {type(item).__name__}")
                continue
            gap_text = item.get("gap")
            if not isinstance(gap_text, str) or not gap_text:
                errors.append(f"entry[{index}].analysis_gaps[{gi}]: 'gap' must be a non-empty string")
            reason = item.get("reason")
            if not isinstance(reason, str) or reason not in VALID_GAP_REASONS:
                errors.append(
                    f"entry[{index}].analysis_gaps[{gi}]: 'reason' must be one of "
                    f"{sorted(VALID_GAP_REASONS)}, got {reason!r}"
                )
            detail = item.get("detail")
            if detail is not None and not isinstance(detail, str):
                errors.append(f"entry[{index}].analysis_gaps[{gi}]: 'detail' must be a string")

    scenarios_val = entry.get("scenarios")
    if not isinstance(scenarios_val, list):
        errors.append(f"entry[{index}]: 'scenarios' must be an array, got {type(scenarios_val).__name__}")
    elif any(not isinstance(item, str) for item in scenarios_val):
        errors.append(f"entry[{index}]: 'scenarios' items must all be strings")

    scenarios = entry.get("scenarios")
    layer = entry.get("stack_layer", "")
    if isinstance(scenarios, list) and not scenarios and layer == "test":
        errors.append(
            f"entry[{index}]: 'scenarios' is empty but stack_layer is 'test' — "
            "populate with the names of the failing test cases"
        )

    return errors


def _try_extract_json_array(text):
    """Attempt to extract a JSON array from text with surrounding prose.

    LLMs sometimes prepend or append prose around the JSON array.
    Tries up to 10 ``[`` positions (first to last) paired with the
    last ``]`` after each one.  Returns a ``(parsed_list, debug_reason)``
    tuple — the list on success or ``None`` on failure, with a reason
    string for diagnostics.
    """
    # Collect all '[' positions, capped at 10 to avoid pathological input.
    MAX_ATTEMPTS = 10
    bracket_positions = []
    pos = 0
    while len(bracket_positions) < MAX_ATTEMPTS:
        idx = text.find("[", pos)
        if idx == -1:
            break
        bracket_positions.append(idx)
        pos = idx + 1

    if not bracket_positions:
        return None, "no opening bracket found"

    total = len(bracket_positions)
    last_error = None

    for attempt, first_bracket in enumerate(bracket_positions, 1):
        last_bracket = text.rfind("]", first_bracket)
        if last_bracket == -1 or last_bracket <= first_bracket:
            last_error = f"no valid closing bracket (first={first_bracket}, last={last_bracket})"
            continue
        candidate = text[first_bracket:last_bracket + 1]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as e:
            last_error = f"json.loads failed: {e} (bracket {attempt}/{total}, first={first_bracket}, last={last_bracket})"
            continue
        if isinstance(data, list):
            return data, f"success (bracket {attempt}/{total}, first={first_bracket}, last={last_bracket})"
        last_error = f"parsed value is {type(data).__name__}, not list (bracket {attempt}/{total}, first={first_bracket}, last={last_bracket})"

    return None, last_error or "no opening bracket found"


def parse_json_output(text):
    """Parse agent output text as a JSON array.

    Tries ``json.loads`` first; on failure, falls back to
    ``_try_extract_json_array`` to handle prose-wrapped output.

    Returns ``(parsed_data, errors)`` — *parsed_data* is the parsed
    list on success or ``None`` on failure, and *errors* is a
    (possibly empty) list of error strings.
    """
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data, []
        return None, [f"Parsed JSON is {type(data).__name__}, expected array"]
    except json.JSONDecodeError:
        extracted, reason = _try_extract_json_array(text)
        if extracted is not None:
            return extracted, []
        return None, [f"Failed to parse JSON: {reason}"]


def validate_json_text(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Fallback: try to extract a JSON array from prose-wrapped text.
        # The LLM sometimes writes prose before/after the JSON array;
        # extracting it avoids a rejection → retry spiral.
        extracted, extract_debug = _try_extract_json_array(text)
        _log_debug("extract_attempt", success=extracted is not None, reason=extract_debug)
        if extracted is not None:
            data = extracted
        else:
            return [f"Output is not valid JSON: {e}. Extract attempt: {extract_debug}. Your entire response must be a valid JSON array."]

    if isinstance(data, dict):
        return [
            "Output is a JSON object, not an array. "
            "Wrap your output in [...] — single failures must still be a JSON array."
        ]
    elif not isinstance(data, list):
        return [f"Expected a JSON array, got {type(data).__name__}"]

    if not data:
        return ["JSON array is empty. Expected at least one failure entry."]

    file_cache = {}
    all_errors = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            all_errors.append(f"entry[{i}]: expected an object, got {type(entry).__name__}")
            continue
        all_errors.extend(validate_entry(entry, i, file_cache))

    return all_errors


def validate_message(message):
    if not message or not message.strip():
        return ["Agent produced empty output. Expected a JSON array."]

    return validate_json_text(message.strip())


def _extract_last_assistant_message_from_transcript(transcript_path):
    """Read a JSONL transcript and return the last assistant text message."""
    last_text = None
    try:
        with open(transcript_path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if record.get("type") != "assistant":
                    continue
                # Extract text from message.content blocks
                message = record.get("message", {})
                if not isinstance(message, dict):
                    continue
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if text:
                            texts.append(text)
                if texts:
                    last_text = "\n".join(texts)
    except OSError as e:
        print(f"WARNING: validate-rca-output: could not read transcript: {e}", file=sys.stderr)
    return last_text


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        print("WARNING: validate-rca-output: malformed JSON on stdin, skipping validation", file=sys.stderr)
        sys.exit(0)

    print(f"DEBUG: validate-rca-output: stdin input: {json.dumps(payload)}", file=sys.stderr)

    if not isinstance(payload, dict):
        print("WARNING: validate-rca-output: expected dict payload, skipping validation", file=sys.stderr)
        sys.exit(0)

    # Detect hook type by the authoritative hook_event_name field.
    hook_event = payload.get("hook_event_name")

    if hook_event == "SubagentStop":
        # prow-job-analyzer output — validate directly (with transcript fallback)
        message = payload.get("last_assistant_message", "")
        if not message:
            transcript_path = payload.get("transcript_path")
            if transcript_path and os.path.isfile(transcript_path):
                message = _extract_last_assistant_message_from_transcript(transcript_path)

    elif hook_event == "Stop":
        # Main-agent Stop — only validate inside an explicit RCA session
        if not os.environ.get("CI_DOCTOR_RCA_SESSION"):
            sys.exit(0)

        message = None
        transcript_path = payload.get("transcript_path")
        if transcript_path and os.path.isfile(transcript_path):
            message = _extract_last_assistant_message_from_transcript(transcript_path)

        if not message:
            print("WARNING: validate-rca-output: Stop hook could not locate assistant message, skipping", file=sys.stderr)
            sys.exit(0)

    else:
        # Unknown/absent hook_event_name (older CC, unexpected payload): fail safe — do not block.
        print(f"WARNING: validate-rca-output: unrecognized hook_event_name {hook_event!r}, skipping", file=sys.stderr)
        sys.exit(0)

    # Log what the hook actually received for debugging stale-output issues.
    if message:
        preview = message[:500] + ("..." if len(message) > 500 else "")
        print(
            f"DEBUG: validate-rca-output: last_assistant_message "
            f"({len(message)} chars): {preview}",
            file=sys.stderr,
        )
    else:
        print(
            "DEBUG: validate-rca-output: last_assistant_message is empty/None",
            file=sys.stderr,
        )

    _log_debug("hook_input",
               input_len=len(message) if message else 0,
               input_text=(message if message else ""))

    errors = validate_message(message)

    decision = "block" if errors else "allow"
    _log_debug("validation_result", errors=errors, decision=decision)

    if errors:
        reason = "RCA output validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        result = {"decision": "block", "reason": reason}
        print(f"DEBUG: validate-rca-output: stdout output: {json.dumps(result)}", file=sys.stderr)
        json.dump(result, sys.stdout)
    else:
        print("DEBUG: validate-rca-output: stdout output: (none, allowing)", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
