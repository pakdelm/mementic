"""Guard tests: the enforcement template and README name only registered CLI commands."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mementic.cli import _LAZY_COMMANDS
from mementic.inject import END_MARKER, START_MARKER, load_template

_REPO = Path(__file__).resolve().parents[1]

_COMMAND_RE = re.compile(r"\bmementic\s+([a-z][a-z0-9-]*)")
_CODE_SPAN_RE = re.compile(r"`([^`\n]+)`")


def named_commands(text: str) -> set[str]:
    """Return the top-level ``mementic`` subcommands named inside inline code spans.

    Only inline code spans are scanned, so prose such as "If mementic fails" and
    placeholders such as ``uv run mementic <command>`` and ``[mementic: <id>]``
    cannot produce a false match.

    Args:
        text: Document text, in Markdown.

    Returns:
        The set of top-level subcommand names, for example ``{"query", "add"}``.
    """
    commands: set[str] = set()
    for span in _CODE_SPAN_RE.findall(text):
        commands.update(_COMMAND_RE.findall(span))
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


def test_readme_drops_token_goal_framing() -> None:
    """The README motivation rests on repeated discovery work, not on token savings."""
    readme = " ".join((_REPO / "README.md").read_text(encoding="utf-8").split())
    sentence = (
        "Without it, agents reread the same ADRs and instruction files before every task, "
        "and every agent repeats the same work."
    )
    assert sentence in readme
    assert "That burns tokens" not in readme
