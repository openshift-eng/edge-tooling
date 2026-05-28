"""Shared failure-type classification for CI analysis scripts.

Provides a single classify_breakdown() used by aggregate.py and search-bugs.py
so that the HTML report and bug-creation pipeline always agree on whether a
failure is build / test / infrastructure.
"""

INFRA_LAYERS = {"aws infra", "external infrastructure"}
BUILD_LAYERS = {"build phase"}

# Step-name substrings that override the LLM's STACK_LAYER.
INFRA_STEP_PATTERNS = ("infra-aws", "infra-gcp", "infra-setup")
BUILD_STEP_PATTERNS = ("update-origin", "build-image", "iso-build")

# Error-signature substrings that catch build operations running inside
# a test step (e.g. "make update-origin" in e2e-metal-tests).
BUILD_SIGNATURE_PATTERNS = ("update-origin", "build-image")


def classify_breakdown(stack_layer, step_name="", error_signature="",
                       infrastructure_failure=False):
    """Classify a CI failure as build, test, or infrastructure.

    The INFRASTRUCTURE_FAILURE flag from the LLM analysis overrides
    pattern-based classification when set.  Otherwise uses deterministic
    step-name and error-signature pattern matching, falling back to the
    LLM's STACK_LAYER when no pattern matches.
    """
    if infrastructure_failure:
        return "infrastructure"

    lower_step = step_name.lower()
    lower_sig = error_signature.lower()

    if any(k in lower_step for k in INFRA_STEP_PATTERNS):
        return "infrastructure"
    if any(k in lower_step for k in BUILD_STEP_PATTERNS):
        return "build"
    if any(k in lower_sig for k in BUILD_SIGNATURE_PATTERNS):
        return "build"

    lower = stack_layer.lower()
    if lower in INFRA_LAYERS:
        return "infrastructure"
    if lower in BUILD_LAYERS:
        return "build"
    return "test"
