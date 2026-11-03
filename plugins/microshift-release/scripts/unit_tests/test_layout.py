"""Keep executable scripts and skill documentation in separate directories."""

import os
from pathlib import Path
import tempfile
import unittest


SCRIPT_SUFFIXES = {
    ".py", ".sh", ".bash", ".zsh", ".fish", ".ksh", ".ps1",
    ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".lua", ".awk",
    ".php", ".r", ".bat", ".cmd", ".psm1",
}
MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdx"}
IGNORED_DIRECTORIES = {".venv", "__pycache__", ".pytest_cache"}


def layout_errors(plugin_dir):
    """Return misplaced paths, ignoring local test environments and caches."""
    errors = []
    for tree in ("skills", "scripts"):
        for directory, dirs, files in os.walk(plugin_dir / tree):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRECTORIES)
            parent = Path(directory)
            if tree == "scripts":
                for name in dirs:
                    if name.casefold() in {"skill", "skills"}:
                        errors.append(f"{(parent / name).relative_to(plugin_dir)}: "
                                      "skill directories belong under skills/")
            for name in sorted(files):
                path = parent / name
                relative = path.relative_to(plugin_dir)
                suffix = path.suffix.casefold()
                if tree == "scripts":
                    if suffix in MARKDOWN_SUFFIXES:
                        errors.append(f"{relative}: Markdown does not belong under scripts/")
                else:
                    with path.open("rb") as stream:
                        has_shebang = stream.read(2) == b"#!"
                    if suffix in SCRIPT_SUFFIXES or has_shebang:
                        errors.append(f"{relative}: scripts belong under scripts/")
    return errors


class TestPluginLayout(unittest.TestCase):
    def test_current_plugin_layout(self):
        plugin_dir = Path(__file__).resolve().parents[2]
        self.assertEqual(layout_errors(plugin_dir), [])


class TestLayoutChecks(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, relative, content=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_valid_layout(self):
        self.write("skills/pre-check/SKILL.md", "# Pre-check\n")
        self.write("scripts/precheck.py", "print('check')\n")
        self.write("scripts/unit_tests/fixtures/input.json", "{}\n")
        self.assertEqual(layout_errors(self.root), [])

    def test_script_extensions_under_nested_skills(self):
        for suffix in sorted(SCRIPT_SUFFIXES):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / "skills/pre-check/nested" / f"check{suffix.upper()}"
                path.parent.mkdir(parents=True)
                path.touch()
                self.assertEqual(len(layout_errors(root)), 1)

    def test_extensionless_script_under_skills(self):
        self.write("skills/pre-check/helpers/check", "#!/usr/bin/env python3\n")
        self.assertEqual(layout_errors(self.root), [
            "skills/pre-check/helpers/check: scripts belong under scripts/",
        ])

    def test_markdown_under_nested_scripts(self):
        for name in ("SKILL.md", "SKILLS.md", "README.MD", "notes.markdown", "notes.mdx"):
            self.write(f"scripts/nested/{name}", "# Documentation\n")
        errors = layout_errors(self.root)
        self.assertEqual(len(errors), 5)
        self.assertTrue(all("Markdown does not belong under scripts/" in e for e in errors))

    def test_skill_directories_under_scripts(self):
        for index, name in enumerate(("skill", "skills", "SKILLS")):
            (self.root / "scripts/nested" / str(index) / name).mkdir(parents=True)
        errors = layout_errors(self.root)
        self.assertEqual(len(errors), 3)
        self.assertTrue(all("skill directories belong under skills/" in e for e in errors))

    def test_local_environment_and_caches_ignored(self):
        for name in sorted(IGNORED_DIRECTORIES):
            self.write(f"scripts/{name}/README.md")
            self.write(f"skills/{name}/generated.py")
        self.assertEqual(layout_errors(self.root), [])


if __name__ == "__main__":
    unittest.main()
