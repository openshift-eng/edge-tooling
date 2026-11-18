"""Chai-backed frontend for the shared Edge OCP payload-monitor engine."""

import sys
from pathlib import Path

# Both standalone tools live in the same checkout/image. Keep the existing
# collector package authoritative rather than maintaining a second copy.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "payload-monitor"))
