"""Codebase structural scanner for cold-start architectural detection.

Produces a deterministic structural summary of a repository without any
LLM calls — directory tree, import graph, config files, and markdown content.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Python stdlib modules to skip in import extraction
_STDLIB_MODULES: frozenset[str] = frozenset({
    "__future__",
    "os",
    "sys",
    "re",
    "json",
    "typing",
    "pathlib",
    "datetime",
    "collections",
    "enum",
    "dataclasses",
    "itertools",
    "functools",
    "abc",
    "math",
    "logging",
    "subprocess",
    "unittest",
    "io",
    "random",
    "hashlib",
    "base64",
    "uuid",
    "tempfile",
    "shutil",
    "textwrap",
    "copy",
    "time",
    "contextlib",
    "warnings",
    "traceback",
})

# Directories to skip during traversal
_IGNORED_DIRS: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
    ".mementic",
    ".tox",
    ".nox",
})

# Dot-directories that are architecturally relevant and should NOT be skipped
_ARCH_DOT_DIRS: frozenset[str] = frozenset({
    ".github",
})

# Filename patterns that signal architectural relevance (case-insensitive match)
_ARCH_SIGNAL_PATTERNS: tuple[str, ...] = (
    "readme",
    "architecture",
    "design",
    "contributing",
    "adr",
    "decisions",
    "conventions",
    "guide",
    "spec",
    "roadmap",
    "changelog",
)

# Config files to read verbatim
_CONFIG_FILES: tuple[str, ...] = (
    "pyproject.toml",
    ".env.example",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".pre-commit-config.yaml",
    "Makefile",
)

_MAX_DIR_DEPTH = 4
_PER_MD_FILE_CAP = 2000
_TOTAL_MD_BUDGET = 5000


def _is_ignored_dir(name: str) -> bool:
    """Check if directory name should be skipped.

    The root directory (``.``) is never ignored. Architecturally relevant
    dot-directories (``.github``) are also not ignored.
    """
    if name == ".":
        return False
    if name in _ARCH_DOT_DIRS:
        return False
    return name in _IGNORED_DIRS or name.startswith(".")


def _should_skip_dir(dirpath: str, depth: int) -> bool:
    """Check if directory should be skipped based on depth and name."""
    if depth > _MAX_DIR_DEPTH:
        return True
    dirname = os.path.basename(dirpath)
    if dirname == ".":
        return False
    return _is_ignored_dir(dirname)


def _extract_python_imports(filepath: str) -> list[str]:
    """Extract non-stdlib imports from a Python file.

    Uses a line-based regex that does not handle:
    - Parenthesized multi-line imports (only the first matched line is captured)
    - Imports inside strings or comments (false positives possible)

    For a best-effort structural scanner this captures the majority of real
    imports accurately. Use the ``tokenize`` module for production-grade parsing
    that avoids string/comment false positives.

    Args:
        filepath: Path to a Python source file.

    Returns:
        A list of non-stdlib import module names found in the file.
    """
    imports: list[str] = []
    import_re = re.compile(
        r"^\s*(?:from\s+(\S+)\s+import|import\s+(\S+))",
        re.MULTILINE,
    )
    try:
        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return imports

    for match in import_re.finditer(content):
        module = match.group(1) or match.group(2)
        top_level = module.split(".")[0]
        if top_level not in _STDLIB_MODULES:
            imports.append(module)
    return imports


def _read_config_files(repo_path: str) -> dict[str, str]:
    """Read recognized config files from repo root.

    Args:
        repo_path: Path to the repository root.

    Returns:
        A dict mapping filename to content for found config files.
    """
    configs: dict[str, str] = {}
    for fname in _CONFIG_FILES:
        fpath = os.path.join(repo_path, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        # For pyproject.toml, only include project + tool sections
        if fname == "pyproject.toml":
            lines = content.split("\n")
            keep_lines: list[str] = []
            in_section = False
            skip_section = False
            for line in lines:
                if line.startswith("[project]") or line.startswith("[project."):
                    in_section = True
                    skip_section = False
                elif line.startswith("[tool"):
                    in_section = True
                    skip_section = False
                elif line.startswith("[build-system]"):
                    in_section = True
                    skip_section = True
                elif line.startswith("[") and not line.startswith("[[tool"):
                    in_section = False
                    skip_section = False
                if in_section and not skip_section:
                    keep_lines.append(line)
                elif not in_section:
                    keep_lines.append(line)
            content = "\n".join(keep_lines)
        configs[fname] = content
    return configs


def _md_signal_score(filename: str) -> int:
    """Return priority score for a markdown file. Higher = more relevant.

    Args:
        filename: The filename (basename or full path) of a markdown file.

    Returns:
        An integer score based on architectural signal patterns.
    """
    name_lower = os.path.splitext(os.path.basename(filename))[0].lower()
    for i, pattern in enumerate(_ARCH_SIGNAL_PATTERNS):
        if pattern in name_lower:
            return len(_ARCH_SIGNAL_PATTERNS) - i  # earlier patterns = higher score
    return 0


def _detect_adr(content: str) -> bool:
    """Detect if markdown content follows ADR format.

    An ADR must have ## Status, ## Context, and ## Decision sections.

    Args:
        content: The full markdown content as a string.

    Returns:
        True if the content matches ADR structure.
    """
    return bool(
        re.search(r"^##\s+Status", content, re.MULTILINE)
        and re.search(r"^##\s+Context", content, re.MULTILINE)
        and re.search(r"^##\s+Decision", content, re.MULTILINE)
    )


def _format_md_sections(
    md_files: list[tuple[str, str, int, bool]],
) -> tuple[list[str], list[str]]:
    """Format collected markdown file data into ADR and content sections.

    Sorts by signal score descending, applies the content budget.

    Args:
        md_files: List of (relpath, content, score, is_adr) tuples.

    Returns:
        A tuple of (adr_sections, content_sections).
    """
    md_files.sort(key=lambda x: (-x[2], x[0]))

    adr_sections: list[str] = []
    content_sections: list[str] = []
    total_chars = 0

    for relpath, content, score, is_adr in md_files:
        if is_adr:
            adr_sections.append(f"{relpath}:\n{content}")
        else:
            if total_chars >= _TOTAL_MD_BUDGET:
                break
            content_sections.append(f"{relpath}:\n{content}")
            total_chars += len(content)

    return adr_sections, content_sections


def _format_tree(
    root_py_files: list[tuple[str, list[str]]],
    dir_entries: list[tuple[int, str, list[tuple[str, list[str]]]]],
) -> list[str]:
    """Format collected tree data into display lines.

    Args:
        root_py_files: Root-level Python files with imports.
        dir_entries: Subdirectory entries with depth, name, and Python files.

    Returns:
        List of formatted tree lines.
    """
    lines: list[str] = []

    # Root-level Python files (no directory entry)
    for pf, (fname, imports) in enumerate(root_py_files):
        if pf >= 5:
            break
        if imports:
            import_str = ", ".join(imports[:8])
            lines.append(f"  {fname} (imports: {import_str})")
        else:
            lines.append(f"  {fname}")
    remaining = len(root_py_files) - 5
    if remaining > 0:
        lines.append(f"  ... (+{remaining} more .py files)")

    # Subdirectories
    for depth, dirname, py_files in dir_entries:
        indent = "  " * depth
        lines.append(f"{indent}{dirname}/")
        for pf, (fname, imports) in enumerate(py_files):
            if pf >= 5:
                break
            if imports:
                import_str = ", ".join(imports[:8])
                lines.append(f"{indent}  {fname} (imports: {import_str})")
            else:
                lines.append(f"{indent}  {fname}")
        remaining = len(py_files) - 5
        if remaining > 0:
            lines.append(f"{indent}  ... (+{remaining} more .py files)")

    return lines


def _format_import_graph(import_graph: dict[str, list[str]]) -> list[str]:
    """Format the import graph for display.

    Args:
        import_graph: Dict mapping file paths to lists of imported modules.

    Returns:
        List of formatted import graph lines.
    """
    lines: list[str] = []
    for filepath in sorted(import_graph):
        lines.append(f"{filepath}:")
        for imp in sorted(set(import_graph[filepath])):
            lines.append(f"  - {imp}")
    return lines


def scan_codebase_structure(repo_path: str) -> str:
    """Scan a repository and produce a structured markdown summary.

    Produces a deterministic summary without any LLM calls. Includes
    directory tree, configuration files, ADR-specific markdown files,
    other signal-rich markdown files, and an import graph of non-stdlib
    Python imports. Uses a single ``os.walk`` traversal for efficiency.

    Args:
        repo_path: Absolute or relative path to the repository root.

    Returns:
        A markdown string with repository structure, config, ADRs,
        markdown files, and import graph. Returns empty string if
        ``repo_path`` doesn't exist or is not a directory.
    """
    if not os.path.isdir(repo_path):
        return ""

    # ── Single os.walk traversal: collect tree, imports, and markdown data ──
    # (dir_name, depth, [(filename, imports)])
    _dir_entries: list[tuple[int, str, list[tuple[str, list[str]]]]] = []
    _root_py_files: list[tuple[str, list[str]]] = []
    _import_graph: dict[str, list[str]] = {}
    _md_files: list[tuple[str, str, int, bool]] = []

    for dirpath, dirnames, filenames in os.walk(repo_path):
        rel = os.path.relpath(dirpath, repo_path)
        depth = 0 if rel == "." else len(Path(rel).parts)
        if _should_skip_dir(dirpath, depth):
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if not _is_ignored_dir(d))

        # Collect Python files and their imports
        py_files_data: list[tuple[str, list[str]]] = []
        for fname in sorted(f for f in filenames if f.endswith(".py")):
            fpath = os.path.join(dirpath, fname)
            imports = _extract_python_imports(fpath)
            py_files_data.append((fname, imports))
            if imports:
                relpath = os.path.relpath(fpath, repo_path).replace("\\", "/")
                _import_graph[relpath] = imports

        if rel == ".":
            _root_py_files = py_files_data
        else:
            _dir_entries.append((depth, os.path.basename(dirpath), py_files_data))

        # Collect markdown files
        for fname in filenames:
            if not fname.lower().endswith(".md"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            score = _md_signal_score(fname)
            is_adr = _detect_adr(content)
            capped = content[:_PER_MD_FILE_CAP]
            if len(content) > _PER_MD_FILE_CAP:
                remaining = len(content) - _PER_MD_FILE_CAP
                capped += f"\n... [{remaining} more chars omitted]"
            _md_files.append(
                (os.path.relpath(fpath, repo_path), capped, score, is_adr)
            )

    # ── Assemble output ──
    parts: list[str] = []

    # 1. Directory tree
    tree_lines = _format_tree(_root_py_files, _dir_entries)
    if tree_lines:
        parts.append("## Repository Structure\n")
        parts.extend(tree_lines)
        parts.append("")

    # 2. Configuration files
    configs = _read_config_files(repo_path)
    if configs:
        parts.append("## Configuration\n")
        for fname, content in configs.items():
            parts.append(f"{fname}:")
            parts.append(content)
            parts.append("")

    # 3. Markdown files
    adr_sections, content_sections = _format_md_sections(_md_files)
    if adr_sections:
        parts.append("## ADR Files (auto-detected by format)\n")
        for section in adr_sections:
            parts.append(section)
            parts.append("")
    if content_sections:
        parts.append("## Markdown Files\n")
        for section in content_sections:
            parts.append(section)
            parts.append("")

    # 4. Import graph
    import_lines = _format_import_graph(_import_graph)
    if import_lines:
        parts.append("## Import Graph\n")
        parts.extend(import_lines)
        parts.append("")

    return "\n".join(parts)


def collect_markdown_files(repo_path: str, char_budget: int = 50000) -> list[tuple[str, str]]:
    """Collect all markdown files from repo, sorted by signal score descending.

    Scans the entire repo for .md files using the same directory filtering
    rules as ``scan_codebase_structure``. Returns ``(relpath, content)``
    tuples sorted by ``_md_signal_score`` descending (README, CHANGELOG,
    CONTRIBUTING rank highest). Each file's content is capped at
    ``_PER_MD_FILE_CAP`` chars. Total content across all files is capped at
    ``char_budget``.

    Args:
        repo_path: Absolute or relative path to the repository root.
        char_budget: Maximum total characters across all collected files.
            Prevents unbounded memory use in large repos. Files are
            included in signal-score order until the budget is exhausted.

    Returns:
        List of ``(relpath, content)`` tuples. Empty list if ``repo_path``
        doesn't exist or is not a directory.
    """
    if not os.path.isdir(repo_path):
        return []

    collected: list[tuple[str, str, int]] = []

    for dirpath, dirnames, filenames in os.walk(repo_path):
        rel = os.path.relpath(dirpath, repo_path)
        depth = 0 if rel == "." else len(Path(rel).parts)
        if _should_skip_dir(dirpath, depth):
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if not _is_ignored_dir(d))

        for fname in filenames:
            if not fname.lower().endswith(".md"):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            score = _md_signal_score(fname)
            capped = content[:_PER_MD_FILE_CAP]
            relpath = os.path.relpath(fpath, repo_path).replace("\\", "/")
            collected.append((relpath, capped, score))

    collected.sort(key=lambda x: (-x[2], x[0]))

    result: list[tuple[str, str]] = []
    total_chars = 0
    for relpath, content, _score in collected:
        if total_chars >= char_budget:
            break
        result.append((relpath, content))
        total_chars += len(content)

    return result
