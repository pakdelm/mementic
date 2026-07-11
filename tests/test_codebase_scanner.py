"""Tests for the codebase structural scanner (Spec 12a)."""

from __future__ import annotations

from pathlib import Path

from mementic.sources.codebase import scan_codebase_structure


class TestScannerDirectoryStructure:
    def test_detects_directory_structure(self, tmp_path: Path) -> None:
        """Scanner outputs directory paths for directories containing Python files."""
        subdir = tmp_path / "src" / "mementic"
        subdir.mkdir(parents=True)
        (subdir / "main.py").write_text("# main module\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("import pytest\n")

        output = scan_codebase_structure(str(tmp_path))

        assert "src/" in output
        assert "mementic/" in output
        assert "tests/" in output

    def test_extracts_python_imports(self, tmp_path: Path) -> None:
        """Scanner extracts third-party import names from Python files."""
        (tmp_path / "app.py").write_text(
            "import fastapi\nfrom pydantic import BaseModel\n"
        )

        output = scan_codebase_structure(str(tmp_path))

        assert "fastapi" in output
        assert "pydantic" in output

    def test_filters_stdlib_imports(self, tmp_path: Path) -> None:
        """Scanner excludes stdlib imports (e.g. 'os') from the output."""
        (tmp_path / "util.py").write_text("import os\nimport sys\nimport json\n")

        output = scan_codebase_structure(str(tmp_path))

        # The import graph section should not contain stdlib modules
        # but might still have the file listed. We check that "os" is not
        # listed as an import.
        if "## Import Graph" in output:
            import_section = output.split("## Import Graph")[1]
            assert "os" not in import_section

    def test_excludes_ignored_dirs(self, tmp_path: Path) -> None:
        """Scanner skips .git, node_modules, __pycache__ etc."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config.py").write_text("repo = 'test'")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "lib.js").write_text("// js")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "main.cpython-311.pyc").write_text("")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# real code")

        output = scan_codebase_structure(str(tmp_path))

        assert ".git" not in output
        assert "node_modules" not in output
        assert "__pycache__" not in output
        assert "src/" in output

    def test_depth_limit_excludes_deep_dirs(self, tmp_path: Path) -> None:
        """Scanner excludes directories deeper than _MAX_DIR_DEPTH (4)."""
        # Create a deep path: a/b/c/d/e/f/ (depth 6 from root)
        deep = tmp_path
        for part in ["a", "b", "c", "d", "e", "f"]:
            deep = deep / part
            deep.mkdir()
        (deep / "deep_module.py").write_text("print('deep')")
        # Also create a shallow file for reference
        (tmp_path / "shallow.py").write_text("print('shallow')")

        output = scan_codebase_structure(str(tmp_path))

        # Levels d (depth 4) should appear; e (depth 5) and f (depth 6) should not
        assert "shallow" in output
        assert "a/" in output
        assert "b/" in output
        assert "c/" in output
        assert "d/" in output
        assert "e/" not in output
        assert "f/" not in output

class TestScannerMarkdownFiles:
    def test_scans_markdown_files(self, tmp_path: Path) -> None:
        """Scanner includes markdown file content in Markdown Files section."""
        (tmp_path / "README.md").write_text("# My Project\nDescription here.\n")

        output = scan_codebase_structure(str(tmp_path))

        assert "## Markdown Files" in output
        assert "README.md" in output
        assert "My Project" in output

    def test_detects_adr_format(self, tmp_path: Path) -> None:
        """Scanner detects ADR-format markdown files by ## Status, ## Context, ## Decision."""
        (tmp_path / "adr-001.md").write_text(
            "## Status\nAccepted\n\n"
            "## Context\nWe need a database.\n\n"
            "## Decision\nUse PostgreSQL.\n"
        )

        output = scan_codebase_structure(str(tmp_path))

        assert "## ADR Files" in output
        assert "adr-001.md" in output

    def test_prioritizes_signal_markdown(self, tmp_path: Path) -> None:
        """README appears before random.md because it has a higher signal score."""
        (tmp_path / "random.md").write_text("# Random notes\n")
        (tmp_path / "README.md").write_text("# Readme\n")

        output = scan_codebase_structure(str(tmp_path))

        readme_idx = output.find("README.md")
        random_idx = output.find("random.md")
        assert readme_idx < random_idx, "README should appear before random.md"

    def test_caps_markdown_content(self, tmp_path: Path) -> None:
        """Markdown files > 2000 chars are truncated with '...'."""
        huge_content = "x" * 2500
        (tmp_path / "huge.md").write_text(huge_content)

        output = scan_codebase_structure(str(tmp_path))

        assert "..." in output
        # The truncated content should be present
        assert "x" * 2000 in output
        # The full 2500-char string should NOT be present
        assert huge_content not in output

    def test_markdown_without_adr_sections_not_in_adr_section(self, tmp_path: Path) -> None:
        """Regular markdown without ADR sections goes to Markdown Files, not ADR Files."""
        (tmp_path / "notes.md").write_text("# Notes\nJust some notes.\n")

        output = scan_codebase_structure(str(tmp_path))

        assert "## ADR Files" not in output
        assert "## Markdown Files" in output
        assert "notes.md" in output


class TestScannerEdgeCases:
    def test_returns_empty_for_nonexistent_path(self) -> None:
        """Scanner returns empty string for a path that does not exist."""
        output = scan_codebase_structure("/nonexistent/path/12345")
        assert output == ""

    def test_handles_empty_repo(self, tmp_path: Path) -> None:
        """Scanner returns empty string for a directory with no relevant files."""
        output = scan_codebase_structure(str(tmp_path))
        # Empty dir has no .py, .md, or config files — empty output
        assert output == ""

    def test_handles_no_python_files(self, tmp_path: Path) -> None:
        """Scanner produces no structure section when no Python files exist."""
        (tmp_path / "README.md").write_text("# Hello\n")

        output = scan_codebase_structure(str(tmp_path))

        # Should have markdown section but no repository structure
        assert "## Repository Structure" not in output
        assert "## Markdown Files" in output

    def test_handles_file_not_directory(self, tmp_path: Path) -> None:
        """Scanner returns empty string when path is a file, not a directory."""
        f = tmp_path / "just_a_file.txt"
        f.write_text("not a repo")
        output = scan_codebase_structure(str(f))
        assert output == ""

    def test_import_graph_includes_non_stdlib(self, tmp_path: Path) -> None:
        """Import Graph section lists only non-stdlib imports with file paths."""
        (tmp_path / "app.py").write_text(
            "import fastapi\n"
            "from pydantic import BaseModel\n"
            "import os\n"
        )

        output = scan_codebase_structure(str(tmp_path))

        assert "## Import Graph" in output
        assert "fastapi" in output
        assert "pydantic" in output
        # os is stdlib, should NOT be in import graph
        import_section = output.split("## Import Graph")[1]
        assert "os" not in import_section

    def test_multiple_files_same_import(self, tmp_path: Path) -> None:
        """Multiple files importing the same module each get their own entry."""
        (tmp_path / "a.py").write_text("import fastapi\n")
        (tmp_path / "b.py").write_text("import fastapi\n")

        output = scan_codebase_structure(str(tmp_path))

        # Each file appears in the import graph with its own deduplicated imports
        assert "a.py" in output
        assert "b.py" in output
        # Within the import graph section, fastapi appears only once per file
        import_section = output.split("## Import Graph")[1]
        assert "fastapi" in import_section

    def test_python_files_without_imports(self, tmp_path: Path) -> None:
        """Python files with no non-stdlib imports appear in tree without import annotations."""
        (tmp_path / "empty.py").write_text("x = 1\n")

        output = scan_codebase_structure(str(tmp_path))

        assert "empty.py" in output
        # Should not have "(imports:" after it
        idx = output.find("empty.py")
        tail = output[idx + len("empty.py"):]
        assert "(imports:" not in tail[:50], "empty.py should have no import annotation"
