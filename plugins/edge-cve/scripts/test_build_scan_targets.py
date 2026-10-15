#!/usr/bin/env python3
"""Unit tests for build_scan_targets.target_id collision resistance."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from build_scan_targets import slugify, target_id  # noqa: E402


class TargetIdTests(unittest.TestCase):
    def test_preserves_readable_slug_prefix(self) -> None:
        tid = target_id("openshift/lvm-operator", "release-4.18")
        self.assertTrue(tid.startswith("openshift-lvm-operator--release-4-18--"))
        self.assertEqual(len(tid.rsplit("--", 1)[-1]), 8)

    def test_digest_disambiguates_truncated_slug_collision(self) -> None:
        # Positive: two distinct long slugs that share a slugify() prefix must
        # not share a target_id once the digest is appended.
        a = "openshift/" + ("a" * 80) + "-one"
        b = "openshift/" + ("a" * 80) + "-two"
        self.assertEqual(slugify(a), slugify(b))
        self.assertNotEqual(target_id(a, "release-4.18"), target_id(b, "release-4.18"))

    def test_same_inputs_stable(self) -> None:
        # Negative: identical inputs always produce the same id.
        self.assertEqual(
            target_id("openshift/microshift", "main"),
            target_id("openshift/microshift", "main"),
        )

    def test_normalization_case_insensitive_digest(self) -> None:
        self.assertEqual(
            target_id("OpenShift/MicroShift", "Release-4.18"),
            target_id("openshift/microshift", "release-4.18"),
        )


if __name__ == "__main__":
    unittest.main()
