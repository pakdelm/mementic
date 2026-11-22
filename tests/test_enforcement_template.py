"""Guard tests: the enforcement template and README name only registered CLI commands."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from mementic.cli import _LAZY_COMMANDS
from mementic.inject import END_MARKER, START_MARKER, load_template, sync_enforcement

_REPO = Path(__file__).resolve().parents[1]

_COMMAND_RE = re.compile(r"\bmementic[ \t]+([a-z][a-z0-9-]*)")
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")
_FENCED_BLOCK_RE = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_QUOTED_DOC_LANGUAGES = {"markdown", "md"}


def named_commands(text: str) -> set[str]:
    """Return the top-level ``mementic`` subcommands named inside code spans or fences.

    Inline code spans are scanned everywhere. Shell and console fences are
    scanned too, because a quick start example puts its commands in a fence.
    Markdown fences are skipped: they only quote an instruction file whose
    commands already appear as inline code spans. Prose is never scanned, so
    sentences such as "If mementic fails" and placeholders such as
    ``uv run mementic <command>`` and ``[mementic: <id>]`` cannot produce a
    false match.

    Args:
        text: Document text, in Markdown.

    Returns:
        The set of top-level subcommand names, for example ``{"query", "add"}``.
    """
    commands: set[str] = set()
    for span in _CODE_SPAN_RE.findall(text):
        commands.update(_COMMAND_RE.findall(span))
    for info, body in _FENCED_BLOCK_RE.findall(text):
        if info.strip().lower() in _QUOTED_DOC_LANGUAGES:
            continue
        commands.update(_COMMAND_RE.findall(body))
    return commands


def test_template_names_only_registered_commands() -> None:
    """Every command the enforcement template names is registered in _LAZY_COMMANDS."""
    commands = named_commands(load_template())
    assert commands >= {"query", "add", "deprecate", "history"}
    assert commands - set(_LAZY_COMMANDS) == set()


def test_parser_flags_unregistered_command() -> None:
    """The parser surfaces the spec's unregistered ``capture`` command."""
    commands = named_commands("run `mementic capture --agent opencode` first")
    assert commands == {"capture"}
    assert "capture" not in _LAZY_COMMANDS


def test_parser_detects_unknown_command_name() -> None:
    """Parsing is independent of the registry: any subcommand token is returned."""
    assert named_commands("`mementic definitely-not-a-command`") == {"definitely-not-a-command"}


def test_parser_scans_fenced_blocks() -> None:
    """A shell fence is parsed, so a quick start example cannot hide a bad command."""
    fenced = "Intro.\n\n```bash\nmementic capture --agent opencode\n```\n"
    assert named_commands(fenced) == {"capture"}


def test_parser_skips_markdown_quoted_documents() -> None:
    """A Markdown fence quotes prose, so its words are not read as subcommands."""
    quoted = "```markdown\nRun every mementic command from that directory.\n```\n"
    assert named_commands(quoted) == set()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("`uv run mementic <command>` is the retry form.", id="retry-placeholder"),
        pytest.param("`mementic` is on PATH.", id="bare-name"),
        pytest.param("Echo the `[mementic: ...]` summary line.", id="citation-marker"),
        pytest.param("If mementic fails, report it.", id="prose"),
    ],
)
def test_parser_ignores_placeholders_and_prose(text: str) -> None:
    """Placeholders, bare names, citation markers, and prose are not command names."""
    assert named_commands(text) == set()


def test_no_capture_reference_in_docs() -> None:
    """No shipped document offers the unregistered ``mementic capture`` command."""
    documents = {
        "mementic/templates/enforcement.md": load_template(),
        "AGENTS.md": (_REPO / "AGENTS.md").read_text(encoding="utf-8"),
        "README.md": (_REPO / "README.md").read_text(encoding="utf-8"),
    }
    offenders = [name for name, text in documents.items() if "mementic capture" in text]
    assert offenders == []


def test_readme_names_only_registered_commands() -> None:
    """Every command the README names is registered in _LAZY_COMMANDS."""
    commands = named_commands((_REPO / "README.md").read_text(encoding="utf-8"))
    assert commands
    assert commands - set(_LAZY_COMMANDS) == set()


def test_repository_agents_md_matches_template() -> None:
    """The marker block in AGENTS.md equals the template block after newline normalization."""
    text = (_REPO / "AGENTS.md").read_text(encoding="utf-8")
    start = text.index(START_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    assert text[start:end] == load_template().strip()


def test_init_leaves_agents_md_byte_identical(tmp_path: Path) -> None:
    """Re-syncing the shipped AGENTS.md changes no bytes, so init is a no-op."""
    agents_md = tmp_path / "AGENTS.md"
    shutil.copyfile(_REPO / "AGENTS.md", agents_md)
    sync_enforcement(agents_md)
    assert agents_md.read_bytes() == (_REPO / "AGENTS.md").read_bytes()


def test_readme_drops_token_goal_framing() -> None:
    """The README motivation rests on repeated discovery work, not on token savings."""
    readme = " ".join((_REPO / "README.md").read_text(encoding="utf-8").split())
    sentence = (
        "Without it, agents reread the same ADRs and instruction files before every task, "
        "and every agent repeats the same work."
    )
    assert sentence in readme
    assert "That burns tokens" not in readme
