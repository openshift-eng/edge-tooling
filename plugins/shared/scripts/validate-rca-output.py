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


def _read_lines(path, cache):
    """Read file lines with caching to avoid re-reading large build logs."""
    if path in cache:
        return cache[path]
    try:
        with open(path, errors="replace") as f:
            lines = f.readlines()
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

    cited_line = " ".join(lines[line_no - 1].split()).lower()
    normalized_quote = " ".join(quote.split()).lower()
    if normalized_quote not in cited_line:
        return [f"{prefix}: quote not found on line {line_no}"]

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

    for field in ("analysis_gaps", "scenarios"):
        val = entry.get(field)
        if not isinstance(val, list):
            errors.append(f"entry[{index}]: '{field}' must be an array, got {type(val).__name__}")
        elif any(not isinstance(item, str) for item in val):
            errors.append(f"entry[{index}]: '{field}' items must all be strings")

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
    Find the first ``[`` and greedily match to the last ``]``, then
    try json.loads on that substring.  Returns the parsed list on
    success, None on failure.
    """
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket == -1 or last_bracket == -1 or last_bracket <= first_bracket:
        return None
    candidate = text[first_bracket:last_bracket + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list):
        return data
    return None


def validate_json_text(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Fallback: try to extract a JSON array from prose-wrapped text.
        # The LLM sometimes writes prose before/after the JSON array;
        # extracting it avoids a rejection → retry spiral.
        extracted = _try_extract_json_array(text)
        if extracted is not None:
            data = extracted
        else:
            return [f"Output is not valid JSON: {e}. Your entire response must be a valid JSON array."]

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

    errors = validate_message(message)

    if errors:
        reason = "RCA output validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        json.dump({"decision": "block", "reason": reason}, sys.stdout)

    sys.exit(0)


if __name__ == "__main__":
    main()
