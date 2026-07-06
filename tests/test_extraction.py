from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from mementic.config import LLMConfig
from mementic.extraction import (
    _extract_architecture_windows,
    _strip_session_noise,
    _truncate_tail,
    ARCHITECTURE_KEYWORDS,
    extract_from_adr_consequences,
    extract_from_codebase_summary,
    extract_from_freeform,
    extract_from_session,
)
from mementic.extraction.llm import _parse_llm_json  # noqa: E402
from mementic.models import Constraint, Decision, DecisionStatus, ExtractionResult, GraphEdge, LLMJsonParseError
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)


def _make_mock_response(result: ExtractionResult) -> MagicMock:
    choice = MagicMock()
    choice.message.content = result.model_dump_json()
    response = MagicMock()
    response.choices = [choice]
    return response


def _mock_client(result: ExtractionResult) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = _make_mock_response(result)
    return client


class TestExtractFromFreeform:
    def test_returns_extraction_result(self) -> None:
        config = LLMConfig()
        result = ExtractionResult(
            decisions=[
                Decision(
                    id="d-1",
                    title="Use async",
                    rationale="Better performance",
                    source="freeform",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-01-01T00:00:00Z",
                    modified_at="2024-01-01T00:00:00Z",
                )
            ],
            constraints=[],
            affected_entities=["src/main.py"],
            edges_to_create=[],
        )

        with patch(
            "mementic.extraction.llm.get_client", return_value=_mock_client(result)
        ):
            output, _usage = extract_from_freeform(
                "We should use async for I/O.", "test-source", config
            )

        assert isinstance(output, ExtractionResult)
        assert len(output.decisions) == 1
        assert output.decisions[0].title == "Use async"
        assert output.affected_entities == ["src/main.py"]

    def test_returns_decisions_and_constraints(self) -> None:
        config = LLMConfig()
        decision = Decision(
            id="d-1",
            title="Adopt pydantic",
            rationale="Type safety",
            source="freeform",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-02-01T00:00:00Z",
            modified_at="2024-02-01T00:00:00Z",
        )
        constraint = Constraint(
            id="c-1",
            description="All models must use pydantic",
            bound_entities=["src/models.py"],
            enforced_by="d-1",
            source="freeform",
            status="active",
            created_at="2024-02-01T00:00:00Z",
        )
        result = ExtractionResult(
            decisions=[decision],
            constraints=[constraint],
            affected_entities=["src/models.py"],
            edges_to_create=[GraphEdge(source="d-1", target="c-1", relation="enforces")],
        )

        with patch(
            "mementic.extraction.llm.get_client", return_value=_mock_client(result)
        ):
            output, _usage = extract_from_freeform(
                "We use pydantic for all models.", "adr-002", config
            )

        assert len(output.decisions) == 1
        assert len(output.constraints) == 1
        assert output.constraints[0].description == "All models must use pydantic"
        assert len(output.edges_to_create) == 1
        assert output.edges_to_create[0].relation == "enforces"

    def test_empty_text_returns_empty_result(self) -> None:
        config = LLMConfig()
        output, _usage = extract_from_freeform("", "empty", config)
        assert isinstance(output, ExtractionResult)
        assert output.decisions == []
        assert output.constraints == []
        assert output.affected_entities == []
        assert output.edges_to_create == []

    def test_whitespace_only_text_returns_empty_result(self) -> None:
        config = LLMConfig()
        output, _usage = extract_from_freeform("   \n  \t  ", "ws", config)
        assert isinstance(output, ExtractionResult)
        assert output.decisions == []

    def test_passes_temperature_and_model(self) -> None:
        config = LLMConfig(extraction_model="deepseek-v3")
        result = ExtractionResult()

        with patch(
            "mementic.extraction.llm.get_client", return_value=_mock_client(result)
        ) as mock_get_client:
            extract_from_freeform("some text", "src", config)

        mock_client = mock_get_client.return_value
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0.1
        assert call_kwargs["model"] == "deepseek-v3"
        assert call_kwargs["response_format"] == {"type": "json_object"}

    def test_none_content_returns_empty_result(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = None
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response

        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_freeform("text", "src", config)

        assert output == ExtractionResult()


class TestExtractFromSession:
    def test_returns_extraction_result(self) -> None:
        config = LLMConfig()
        result = ExtractionResult(
            decisions=[
                Decision(
                    id="d-sess-1",
                    title="Switch to httpx",
                    rationale="Better async support",
                    source="session",
                    status=DecisionStatus.ACCEPTED,
                    created_at="2024-03-01T00:00:00Z",
                    modified_at="2024-03-01T00:00:00Z",
                )
            ],
            constraints=[],
            affected_entities=["requirements.txt", "src/http_client.py"],
            edges_to_create=[],
        )

        with patch(
            "mementic.extraction.llm.get_client", return_value=_mock_client(result)
        ):
            output, _usage = extract_from_session(
                session_log="Agent started...",
                git_diff="+httpx==1.0",
                user_notes="Switch to httpx",
                config=config,
            )

        assert isinstance(output, ExtractionResult)
        assert len(output.decisions) == 1
        assert output.decisions[0].title == "Switch to httpx"

    def test_empty_inputs_return_empty_result(self) -> None:
        config = LLMConfig()
        output, _usage = extract_from_session("", "", "", config)
        assert output == ExtractionResult()

    def test_whitespace_only_inputs_return_empty_result(self) -> None:
        config = LLMConfig()
        output, _usage = extract_from_session("  \n", "  ", "\t", config)
        assert output == ExtractionResult()


class TestExtractFromAdrConsequences:
    def test_returns_constraints(self) -> None:
        config = LLMConfig()
        constraints = [
            Constraint(
                id="c-adr-1",
                description="Must not use raw SQL",
                bound_entities=["src/db.py"],
                enforced_by="d-use-orm",
                source="ADR-005",
                status="active",
                created_at="2024-01-01T00:00:00Z",
            )
        ]

        client = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps(
            {"constraints": [c.model_dump() for c in constraints]}
        )
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj

        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_adr_consequences(
                "Therefore, all DB access must go through the ORM.",
                config,
            )

        assert len(output) == 1
        assert isinstance(output[0], Constraint)
        assert output[0].description == "Must not use raw SQL"

    def test_empty_text_returns_empty_list(self) -> None:
        config = LLMConfig()
        output, _usage = extract_from_adr_consequences("", config)
        assert output == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        config = LLMConfig()
        output, _usage = extract_from_adr_consequences("   \n  ", config)
        assert output == []

    def test_none_content_returns_empty_list(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = None
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj

        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_adr_consequences("some text", config)

        assert output == []

    def test_no_constraints_in_response(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps({"constraints": []})
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj

        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_adr_consequences("some text", config)

        assert output == []


class TestErrorHandling:
    def _make_raising_client(self, exception: Exception) -> MagicMock:
        client = MagicMock()
        client.chat.completions.create.side_effect = exception
        return client

    def _make_auth_error(self) -> AuthenticationError:
        return AuthenticationError(
            message="bad key", response=MagicMock(), body=None
        )

    def _make_rate_limit_error(self) -> RateLimitError:
        return RateLimitError(
            message="limit", response=MagicMock(), body=None
        )

    def test_freeform_auth_error_returns_empty(self) -> None:
        config = LLMConfig()
        with patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(self._make_auth_error())):
            with pytest.raises(RuntimeError, match="Authentication failed"):
                extract_from_freeform("text", "src", config)

    def test_freeform_rate_limit_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(self._make_rate_limit_error())),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_freeform("text", "src", config)
        assert output == ExtractionResult()

    def test_freeform_timeout_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(APITimeoutError(request=MagicMock()))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_freeform("text", "src", config)
        assert output == ExtractionResult()

    def test_freeform_connection_error_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(APIConnectionError(request=MagicMock()))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_freeform("text", "src", config)
        assert output == ExtractionResult()

    def test_freeform_api_error_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(
                        APIError("err", request=MagicMock(), body=None))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_freeform("text", "src", config)
        assert output == ExtractionResult()

    def test_freeform_json_decode_error_returns_empty(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = "not valid json"
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj
        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_freeform("text", "src", config)
        assert output == ExtractionResult()

    def test_freeform_validation_error_returns_empty(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps({"decisions": "not_a_list"})
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj
        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_freeform("text", "src", config)
        assert output == ExtractionResult()

    def test_session_auth_error_returns_empty(self) -> None:
        config = LLMConfig()
        with patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(self._make_auth_error())):
            with pytest.raises(RuntimeError, match="Authentication failed"):
                extract_from_session("log", "diff", "notes", config)

    def test_session_rate_limit_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(self._make_rate_limit_error())),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_session("log", "diff", "notes", config)
        assert output == ExtractionResult()

    def test_session_timeout_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(APITimeoutError(request=MagicMock()))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_session("log", "diff", "notes", config)
        assert output == ExtractionResult()

    def test_session_connection_error_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(APIConnectionError(request=MagicMock()))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_session("log", "diff", "notes", config)
        assert output == ExtractionResult()

    def test_session_api_error_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(
                        APIError("err", request=MagicMock(), body=None))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_session("log", "diff", "notes", config)
        assert output == ExtractionResult()

    def test_session_json_decode_error_returns_empty(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = "not valid json"
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj
        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_session("log", "diff", "notes", config)
        assert output == ExtractionResult()

    def test_session_validation_error_returns_empty(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps({"decisions": "not_a_list"})
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj
        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_session("log", "diff", "notes", config)
        assert output == ExtractionResult()

    def test_adr_consequences_auth_error_returns_empty(self) -> None:
        config = LLMConfig()
        with patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(self._make_auth_error())):
            with pytest.raises(RuntimeError, match="Authentication failed"):
                extract_from_adr_consequences("consequences", config)

    def test_adr_consequences_rate_limit_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(self._make_rate_limit_error())),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_adr_consequences("consequences", config)
        assert output == []

    def test_adr_consequences_timeout_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(APITimeoutError(request=MagicMock()))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_adr_consequences("consequences", config)
        assert output == []

    def test_adr_consequences_connection_error_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(APIConnectionError(request=MagicMock()))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_adr_consequences("consequences", config)
        assert output == []

    def test_adr_consequences_api_error_returns_empty(self) -> None:
        config = LLMConfig()
        with (patch("mementic.extraction.llm.get_client",
                   return_value=self._make_raising_client(
                        APIError("err", request=MagicMock(), body=None))),
              patch("time.sleep", return_value=None)):
            output, _usage = extract_from_adr_consequences("consequences", config)
        assert output == []

    def test_adr_consequences_json_decode_error_returns_empty(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = "not json"
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj
        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_adr_consequences("text", config)
        assert output == []

    def test_adr_consequences_validation_error_returns_empty(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps({"constraints": "not_a_list"})
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj
        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, _usage = extract_from_adr_consequences("text", config)
        assert output == []


class TestMissingApiKey:
    """Tests for D4: missing API key produces clear error (Spec 06a)."""

    def test_extraction_missing_key_raises_runtime_error(self) -> None:
        """When get_client raises RuntimeError (missing key), extraction propagates it."""
        config = LLMConfig(provider="openai", model="gpt-4o")
        with patch(
            "mementic.extraction.llm.get_client",
            side_effect=RuntimeError("Mementic requires an API key for provider 'openai'.\n"
                                     "  Set the environment variable: OPENAI_API_KEY"),
        ):
            with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
                extract_from_freeform("some text", "test", config)

    def test_session_extraction_missing_key_raises(self) -> None:
        """Session extraction also propagates missing-key error."""
        config = LLMConfig(provider="openai", model="gpt-4o")
        with patch(
            "mementic.extraction.llm.get_client",
            side_effect=RuntimeError("Mementic requires an API key for provider 'openai'."),
        ):
            with pytest.raises(RuntimeError, match="openai"):
                extract_from_session("log", "diff", "notes", config)


# ── Spec 10a: Session Truncation Tests ─────────────────────────────


def _build_session(num_messages: int, keyword_line: int | None = None) -> str:
    """Build a synthetic session log with ``## User`` / ``## Assistant`` messages.

    Args:
        num_messages: Total number of messages (pairs of User/Assistant = 2*N).
        keyword_line: If set, insert ``rejected`` at this 0-based line index.
    """
    lines: list[str] = []
    for i in range(num_messages):
        lines.append("## User")
        lines.append(f"User message {i}")
        lines.append("## Assistant")
        lines.append(f"Assistant message {i}")
    if keyword_line is not None and 0 <= keyword_line < len(lines):
        lines[keyword_line] = lines[keyword_line] + " rejected"
    return "\n".join(lines)


class TestArchitectureWindows:
    """Tests for _extract_architecture_windows keyword-scanning logic."""

    def test_windows_keyword_match(self) -> None:
        """Session containing 'architecture' and 'rejected' returns windowed output."""
        log = _build_session(50, keyword_line=100)
        # Also add a second keyword on another line
        lines = log.split("\n")
        lines[80] = lines[80] + " architecture"
        log = "\n".join(lines)

        result = _extract_architecture_windows(
            log, ARCHITECTURE_KEYWORDS, context_messages=20
        )
        assert result != ""
        # Should contain the keyword lines and their context
        assert "rejected" in result.lower()
        assert "architecture" in result.lower()

    def test_windows_no_match_returns_empty(self) -> None:
        """Session log with no keywords returns empty string."""
        log = _build_session(50)
        result = _extract_architecture_windows(
            log, ARCHITECTURE_KEYWORDS, context_messages=20
        )
        assert result == ""

    def test_windows_merges_overlapping(self) -> None:
        """Two keywords close together produce one merged window, not two."""
        lines = []
        for i in range(100):
            lines.append("## User")
            lines.append(f"Message {i}")
            lines.append("## Assistant")
            lines.append(f"Reply {i}")
        # Place keywords close together
        lines[380] = lines[380] + " architecture"
        lines[390] = lines[390] + " rejected"
        log = "\n".join(lines)

        result = _extract_architecture_windows(
            log, ARCHITECTURE_KEYWORDS, context_messages=20
        )
        # Should be one merged window (no "\n...\n" separator)
        assert result != ""
        assert "...\n" not in result

    def test_windows_mid_session_match(self) -> None:
        """Keyword in the middle of a large session is captured with surrounding context."""
        lines = []
        for i in range(200):
            lines.append("## User")
            lines.append(f"User {i}")
            lines.append("## Assistant")
            lines.append(f"Assistant {i}")
        # Place keyword near the middle
        lines[400] = lines[400] + " record_decision here"
        log = "\n".join(lines)

        result = _extract_architecture_windows(
            log, ARCHITECTURE_KEYWORDS, context_messages=20
        )
        assert result != ""
        assert "record_decision" in result.lower()
        # Should include lines around the match, not the full session
        assert len(result.split("\n")) < len(lines)

    def test_windows_multiple_keywords(self) -> None:
        """Multiple different keywords all get captured."""
        lines = []
        for i in range(150):
            lines.append("## User")
            lines.append(f"U {i}")
            lines.append("## Assistant")
            lines.append(f"A {i}")
        # Scatter keywords far apart
        lines[100] = lines[100] + " migration"
        lines[300] = lines[300] + " tradeoff"
        lines[500] = lines[500] + " ADR"
        log = "\n".join(lines)

        result = _extract_architecture_windows(
            log, ARCHITECTURE_KEYWORDS, context_messages=5
        )
        assert result != ""
        assert "migration" in result.lower()
        assert "tradeoff" in result.lower()
        assert "adr" in result.lower()
        # Three non-overlapping windows joined by separators
        assert result.count("\n...\n") == 2


class TestTailTruncation:
    """Tests for _truncate_tail message-counting logic."""

    def test_tail_fewer_than_n(self) -> None:
        """Session with fewer messages than max_messages returns all messages."""
        log = _build_session(5)
        result = _truncate_tail(log, max_messages=100)
        assert result == log

    def test_tail_zero(self) -> None:
        """max_messages=0 returns empty string."""
        log = _build_session(10)
        result = _truncate_tail(log, max_messages=0)
        assert result == ""

    def test_tail_truncates_to_n_messages(self) -> None:
        """Session with more messages than max_messages returns only the last N."""
        log = _build_session(50)
        result = _truncate_tail(log, max_messages=20)
        assert result != log
        # 50 pairs = 100 boundary lines; last 20 boundaries = 10 pairs
        result_lines = result.split("\n")
        assert result_lines[0].startswith("## ")
        assert "User message 40" in result_lines[1]

    def test_tail_empty_input(self) -> None:
        """Empty session log returns empty string."""
        result = _truncate_tail("", max_messages=100)
        assert result == ""

    def test_tail_no_recognized_boundaries_falls_back_to_lines(self) -> None:
        """Unrecognized format falls back to last N lines."""
        lines = [f"Line {i}" for i in range(200)]
        log = "\n".join(lines)
        result = _truncate_tail(log, max_messages=10)
        expected = "\n".join(lines[-10:])
        assert result == expected


class TestStripSessionNoise:
    """Tests for _strip_session_noise text-cleaning logic."""

    def test_strips_opencode_log_prefix(self) -> None:
        """Timestamp/level/run prefixes are removed, message content kept."""
        raw = (
            "timestamp=2026-06-17T10:00:00Z level=info run=abc123 "
            "This is a user message about architecture\n"
            "timestamp=2026-06-17T10:00:01Z level=info run=abc123 "
            "And this is about tradeoffs"
        )
        cleaned = _strip_session_noise(raw)
        assert "timestamp=2026-06-17T10:00:00Z" not in cleaned
        assert "level=info" not in cleaned
        assert "run=abc123" not in cleaned
        assert "This is a user message about architecture" in cleaned
        assert "And this is about tradeoffs" in cleaned

    def test_hash_only_lines_dropped(self) -> None:
        """Lines with hash= but no message= are dropped entirely."""
        raw = (
            "timestamp=2026-06-17T10:00:00Z level=info run=abc123 "
            "hash=abc123def456 cwd=/home/user\n"
            "timestamp=2026-06-17T10:00:01Z level=info run=abc123 "
            "Some actual content here"
        )
        cleaned = _strip_session_noise(raw)
        assert "hash=abc123def456" not in cleaned
        assert "Some actual content here" in cleaned

    def test_hash_with_message_line_kept(self) -> None:
        """Lines with both hash= and message= are kept (content preserved)."""
        raw = (
            "timestamp=2026-06-17T10:00:00Z level=info run=abc123 "
            "hash=abc123def456 message=User said something important"
        )
        cleaned = _strip_session_noise(raw)
        assert "message=User said something important" in cleaned

    def test_non_opencode_format_passthrough(self) -> None:
        """Plain text without opencode log metadata passes through unchanged."""
        raw = "Just a normal sentence.\nAnother line here."
        cleaned = _strip_session_noise(raw)
        # Byte-identity, not just containment: non-opencode text must not be
        # mutated (no blank-line stripping, no hash= heuristic).
        assert cleaned == raw

    def test_non_opencode_hash_line_preserved(self) -> None:
        """Non-opencode text with `hash=` but no `message=` is not dropped.

        Regression: previously the `hash=` heuristic fired on any input,
        which would silently drop a Claude-style transcript line like
        ``[agent] checked the hash=abc123 commit``.
        """
        raw = "[agent] checked the hash=abc123 commit\n[user] ok"
        cleaned = _strip_session_noise(raw)
        assert cleaned == raw
        assert "hash=abc123" in cleaned

    def test_empty_input(self) -> None:
        """Empty string returns empty string."""
        assert _strip_session_noise("") == ""

    def test_whitespace_only_input(self) -> None:
        """Whitespace-only input returns unchanged."""
        result = _strip_session_noise("   \n  \t  ")
        assert result == "   \n  \t  "

    def test_blank_lines_removed(self) -> None:
        """Blank lines (after stripping) are removed from output."""
        raw = (
            "timestamp=2026-06-17T10:00:00Z level=info run=abc123 "
            "Line one\n"
            "\n"
            "timestamp=2026-06-17T10:00:01Z level=info run=abc123 "
            "Line two"
        )
        cleaned = _strip_session_noise(raw)
        lines = cleaned.split("\n")
        assert lines == ["Line one", "Line two"]


class TestExtractFromSessionTruncation:
    """Tests that extract_from_session applies truncation before LLM call."""

    def _get_user_content(self, client_mock: MagicMock) -> str:
        """Extract the user message content from the mocked LLM call."""
        call_args = client_mock.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        for msg in messages:
            if msg["role"] == "user":
                return str(msg["content"])
        return ""

    def test_extract_uses_windows_when_match(self) -> None:
        """extract_from_session with keyword-rich input uses windows for session_log."""
        config = LLMConfig()
        # Build session with keyword early on
        lines = []
        for i in range(200):
            lines.append("## User")
            lines.append(f"U {i}")
            lines.append("## Assistant")
            lines.append(f"A {i}")
        lines[50] = lines[50] + " architecture tradeoff"
        log = "\n".join(lines)

        result_obj = ExtractionResult()
        client = _mock_client(result_obj)

        with patch("mementic.extraction.llm.get_client", return_value=client):
            extract_from_session(
                session_log=log,
                git_diff="",
                user_notes="",
                config=config,
            )

        user_content = self._get_user_content(client)
        # Windows should contain early content, not just the tail
        assert "architecture" in user_content.lower()
        # Should NOT contain the full session — windowed output is shorter
        assert len(user_content) < len(log)

    def test_extract_falls_back_to_tail(self) -> None:
        """extract_from_session with no keywords falls back to tail truncation."""
        config = LLMConfig()
        log = _build_session(200)
        assert "architecture" not in log.lower()

        result_obj = ExtractionResult()
        client = _mock_client(result_obj)

        with patch("mementic.extraction.llm.get_client", return_value=client):
            extract_from_session(
                session_log=log,
                git_diff="",
                user_notes="",
                config=config,
                max_messages=40,
            )

        user_content = self._get_user_content(client)
        # 200 pairs = 400 boundaries; max_messages=40 => last 40 boundaries = 20 pairs
        # Those should be pairs 180-199
        assert "User message 180" in user_content
        # Should NOT contain early messages
        assert "User message 0" not in user_content

    def test_extract_tail_zero_no_match(self) -> None:
        """extract_from_session with max_messages=0 and no keywords passes no session to LLM."""
        config = LLMConfig()
        log = "Some text without any special terms\n## User\nnothing here\n## Assistant\nno keywords"

        result_obj = ExtractionResult()
        client = _mock_client(result_obj)

        with patch("mementic.extraction.llm.get_client", return_value=client):
            extract_from_session(
                session_log=log,
                git_diff="+import os",
                user_notes="",
                config=config,
                max_messages=0,
            )

        user_content = self._get_user_content(client)
        # Session log section should be absent; only git diff should be present
        assert "Agent Session Log" not in user_content
        assert "Git Diff" in user_content

    def test_extract_custom_keyword_creates_windows(self) -> None:
        """extract_from_session with a custom keyword that matches uses windows."""
        config = LLMConfig()
        log = _build_session(200)
        # Inject a custom keyword that doesn't appear in built-in list
        lines = log.split("\n")
        lines[50] = lines[50] + " custom_widget_framework"
        log = "\n".join(lines)

        result_obj = ExtractionResult()
        client = _mock_client(result_obj)

        with patch("mementic.extraction.llm.get_client", return_value=client):
            extract_from_session(
                session_log=log,
                git_diff="",
                user_notes="",
                config=config,
                custom_keywords=["custom_widget_framework"],
            )

        user_content = self._get_user_content(client)
        # The custom keyword should appear in the windowed output
        assert "custom_widget_framework" in user_content.lower()
        # Should NOT contain the full session — windowed output is shorter
        assert len(user_content) < len(log)

    def test_extract_custom_keyword_merges_with_builtin(self) -> None:
        """extract_from_session merges custom keywords with built-in keywords."""
        config = LLMConfig()
        log = _build_session(200)
        # Inject both a built-in keyword and a custom one
        lines = log.split("\n")
        lines[50] = lines[50] + " record_decision"
        lines[300] = lines[300] + " custom_integration"
        log = "\n".join(lines)

        result_obj = ExtractionResult()
        client = _mock_client(result_obj)

        with patch("mementic.extraction.llm.get_client", return_value=client):
            extract_from_session(
                session_log=log,
                git_diff="",
                user_notes="",
                config=config,
                custom_keywords=["custom_integration"],
            )

        user_content = self._get_user_content(client)
        # Both built-in and custom keywords should be captured
        assert "record_decision" in user_content.lower()
        assert "custom_integration" in user_content.lower()
        # Should NOT contain the full session
        assert len(user_content) < len(log)

    def test_extract_no_custom_keywords_uses_builtin_only(self) -> None:
        """extract_from_session with no custom_keywords falls back to built-in only."""
        config = LLMConfig()
        log = _build_session(200)
        # Inject only a custom-looking keyword
        lines = log.split("\n")
        lines[50] = lines[50] + " widget_framework"
        log = "\n".join(lines)

        result_obj = ExtractionResult()
        client = _mock_client(result_obj)

        with patch("mementic.extraction.llm.get_client", return_value=client):
            extract_from_session(
                session_log=log,
                git_diff="",
                user_notes="",
                config=config,
            )

        user_content = self._get_user_content(client)
        # Without custom_keywords, "widget_framework" should not trigger windows;
        # the function falls back to tail truncation.
        # The session log should be present but truncated (tail only).
        assert "Agent Session Log" in user_content


# ── Spec 09b B1: LLM Retry Tests ────────────────────────────────────


class TestLLMRetry:
    """Tests for _call_llm_with_retry exponential backoff behaviour."""

    def _mock_response(self) -> MagicMock:
        choice = MagicMock()
        choice.message.content = "{}"
        response = MagicMock()
        response.choices = [choice]
        return response

    def _make_raising_client(self, exceptions: list[Exception]) -> MagicMock:
        """Return a mock client that raises each exception in sequence.

        The last call succeeds by returning a valid response.
        """
        client = MagicMock()
        side_effects: list[Exception | MagicMock] = list(exceptions)
        side_effects.append(self._mock_response())
        client.chat.completions.create.side_effect = side_effects
        return client

    def _make_always_raising_client(self, exception: Exception) -> MagicMock:
        """Return a mock client that always raises the given exception."""
        client = MagicMock()
        client.chat.completions.create.side_effect = exception
        return client

    def test_retry_on_rate_limit_succeeds(self) -> None:
        """RateLimitError twice then succeed on 3rd attempt — verifies retry."""
        from mementic.extraction import _call_llm_with_retry

        rate_err = RateLimitError(message="rate", response=MagicMock(), body=None)
        client = self._make_raising_client([rate_err, rate_err])

        with patch("time.sleep", return_value=None) as mock_sleep:
            response = _call_llm_with_retry(
                client,
                messages=[{"role": "user", "content": "hello"}],
                model="test-model",
            )

        assert response is not None
        # 3 total calls (2 failures + 1 success)
        assert client.chat.completions.create.call_count == 3
        # Sleep called twice with 2^0=1s and 2^1=2s
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    def test_retry_on_rate_limit_exhausted(self) -> None:
        """Always RateLimitError — raises after 4 attempts (max_retries=3)."""
        from mementic.extraction import _call_llm_with_retry

        rate_err = RateLimitError(message="rate", response=MagicMock(), body=None)
        client = self._make_always_raising_client(rate_err)

        with patch("time.sleep", return_value=None):
            with pytest.raises(RateLimitError):
                _call_llm_with_retry(
                    client,
                    messages=[{"role": "user", "content": "hello"}],
                    model="test-model",
                    max_retries=2,
                )

        # 3 attempts (max_retries + 1)
        assert client.chat.completions.create.call_count == 3

    def test_no_retry_on_auth_error(self) -> None:
        """AuthenticationError raises immediately — no retry."""
        from mementic.extraction import _call_llm_with_retry

        auth_err = AuthenticationError(
            message="bad key", response=MagicMock(), body=None
        )
        client = self._make_always_raising_client(auth_err)

        with patch("time.sleep", return_value=None) as mock_sleep:
            with pytest.raises(AuthenticationError):
                _call_llm_with_retry(
                    client,
                    messages=[{"role": "user", "content": "hello"}],
                    model="test-model",
                )

        # Only 1 attempt — no retry
        assert client.chat.completions.create.call_count == 1
        # No sleep calls
        assert mock_sleep.call_count == 0

    def test_retry_on_connection_error(self) -> None:
        """APIConnectionError once then succeed — verifies retry with 1s delay."""
        from mementic.extraction import _call_llm_with_retry

        conn_err = APIConnectionError(request=MagicMock())
        client = self._make_raising_client([conn_err])

        with patch("time.sleep", return_value=None) as mock_sleep:
            response = _call_llm_with_retry(
                client,
                messages=[{"role": "user", "content": "hello"}],
                model="test-model",
            )

        assert response is not None
        # 2 calls (1 failure + 1 success)
        assert client.chat.completions.create.call_count == 2
        # Sleep called once with 1s
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_once_with(1)

    def test_retry_on_timeout_error(self) -> None:
        """APITimeoutError once then succeed — verifies retry with 1s delay."""
        from mementic.extraction import _call_llm_with_retry

        timeout_err = APITimeoutError(request=MagicMock())
        client = self._make_raising_client([timeout_err])

        with patch("time.sleep", return_value=None) as mock_sleep:
            response = _call_llm_with_retry(
                client,
                messages=[{"role": "user", "content": "hello"}],
                model="test-model",
            )

        assert response is not None
        assert client.chat.completions.create.call_count == 2
        mock_sleep.assert_called_once_with(1)

    def test_rate_limit_sleep_intervals_exponential(self) -> None:
        """RateLimitError 3 times — sleep intervals are 1, 2, 4 seconds."""
        from mementic.extraction import _call_llm_with_retry

        rate_err = RateLimitError(message="rate", response=MagicMock(), body=None)
        client = self._make_always_raising_client(rate_err)

        with patch("time.sleep", return_value=None) as mock_sleep:
            with pytest.raises(RateLimitError):
                _call_llm_with_retry(
                    client,
                    messages=[{"role": "user", "content": "hello"}],
                    model="test-model",
                    max_retries=3,
                )

        # 4 attempts (max_retries=3 + initial call)
        assert client.chat.completions.create.call_count == 4
        # Sleep called 3 times with 1, 2, 4
        assert mock_sleep.call_count == 3
        assert mock_sleep.call_args_list[0].args == (1,)
        assert mock_sleep.call_args_list[1].args == (2,)
        assert mock_sleep.call_args_list[2].args == (4,)

# ── Spec 12a: Codebase Summary Extraction Tests ─────────────────────


class TestExtractFromCodebaseSummary:
    """Tests for extract_from_codebase_summary codebase cold-start extraction."""

    def _make_codebase_mock_response(
        self, decisions: list | None = None
    ) -> MagicMock:
        """Build a mock LLM response with decisions."""
        result = ExtractionResult(
            decisions=decisions or [],
            constraints=[],
            affected_entities=["src/main.py"],
            edges_to_create=[],
        )
        choice = MagicMock()
        choice.message.content = result.model_dump_json()
        response = MagicMock()
        response.choices = [choice]
        return response

    def test_extracts_decisions_with_proposed_status(self) -> None:
        """LLM-returned decisions are forced to proposed status with codebase source."""
        config = LLMConfig()
        decision = Decision(
            id="d-fastapi",
            title="Use FastAPI",
            rationale="FastAPI is the web framework",
            source="freeform",
            status=DecisionStatus.ACCEPTED,
            created_at="2024-01-01T00:00:00Z",
            modified_at="2024-01-01T00:00:00Z",
        )
        client = MagicMock()
        response = self._make_codebase_mock_response(decisions=[decision])
        client.chat.completions.create.return_value = response

        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, usage = extract_from_codebase_summary(
                "## Repository Structure\nsrc/app.py (imports: fastapi)",
                config,
            )

        assert isinstance(output, ExtractionResult)
        assert len(output.decisions) == 1
        d = output.decisions[0]
        assert d.title == "Use FastAPI"
        assert d.status == DecisionStatus.ACCEPTED
        assert d.source == "init"

    def test_empty_summary_returns_empty_result(self) -> None:
        """Empty summary string returns empty ExtractionResult with zero tokens."""
        config = LLMConfig()
        output, usage = extract_from_codebase_summary("", config)

        assert isinstance(output, ExtractionResult)
        assert output.decisions == []
        assert output.constraints == []
        assert usage.total_tokens == 0
        assert usage.status == "success"

    def test_whitespace_only_summary_returns_empty_result(self) -> None:
        """Whitespace-only summary returns empty ExtractionResult."""
        config = LLMConfig()
        output, usage = extract_from_codebase_summary("   \n  \t  ", config)

        assert isinstance(output, ExtractionResult)
        assert output.decisions == []
        assert output.constraints == []

    def test_llm_returns_invalid_json(self) -> None:
        """LLM returning invalid JSON is handled gracefully with error status."""
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = "not valid json {{"
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response

        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, usage = extract_from_codebase_summary(
                "## Repository Structure\nsome info",
                config,
            )

        assert isinstance(output, ExtractionResult)
        assert output.decisions == []
        assert usage.status == "error"

    def test_llm_returns_validation_error(self) -> None:
        """LLM returning valid JSON but wrong schema is handled gracefully."""
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = '{"decisions": "not_a_list"}'
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response

        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, usage = extract_from_codebase_summary(
                "## Repository Structure\nsome info",
                config,
            )

        assert isinstance(output, ExtractionResult)
        assert output.decisions == []
        assert usage.status == "error"

    def test_none_content_returns_empty_result(self) -> None:
        """LLM returning None content is handled gracefully."""
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = None
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response

        with patch("mementic.extraction.llm.get_client", return_value=client):
            output, usage = extract_from_codebase_summary(
                "## Repository Structure\nsome info",
                config,
            )

        assert isinstance(output, ExtractionResult)
        assert output.decisions == []

    def test_auth_error_raises(self) -> None:
        """AuthenticationError is re-raised as RuntimeError."""
        config = LLMConfig()
        auth_err = AuthenticationError(
            message="bad key", response=MagicMock(), body=None
        )
        client = MagicMock()
        client.chat.completions.create.side_effect = auth_err

        with patch("mementic.extraction.llm.get_client", return_value=client):
            with pytest.raises(RuntimeError, match="Authentication failed"):
                extract_from_codebase_summary("summary", config)

    def test_rate_limit_returns_empty(self) -> None:
        """RateLimitError returns empty result with rate_limited status."""
        config = LLMConfig()
        client = MagicMock()
        client.chat.completions.create.side_effect = RateLimitError(
            message="limit", response=MagicMock(), body=None
        )

        with (patch("mementic.extraction.llm.get_client", return_value=client),
              patch("time.sleep", return_value=None)):
            output, usage = extract_from_codebase_summary("summary", config)

        assert output == ExtractionResult()
        assert usage.status == "rate_limited"

    def test_timeout_returns_empty(self) -> None:
        """APITimeoutError returns empty result with error status."""
        config = LLMConfig()
        client = MagicMock()
        client.chat.completions.create.side_effect = APITimeoutError(
            request=MagicMock()
        )

        with (patch("mementic.extraction.llm.get_client", return_value=client),
              patch("time.sleep", return_value=None)):
            output, usage = extract_from_codebase_summary("summary", config)

        assert output == ExtractionResult()
        assert usage.status == "error"


class TestParseLlmJson:
    """Tests for _parse_llm_json sanitization and error handling."""

    def test_valid_json_passthrough(self) -> None:
        """Clean JSON passes through unchanged."""
        result = _parse_llm_json('{"key": "value"}', source="test")
        assert result == {"key": "value"}

    def test_valid_json_array(self) -> None:
        """Valid JSON array passes through unchanged."""
        result = _parse_llm_json('[1, 2, 3]', source="test")
        assert result == [1, 2, 3]

    def test_markdown_fence_stripped(self) -> None:
        """```json fence is stripped before parsing."""
        result = _parse_llm_json(
            '```json\n{"key": "value"}\n```',
            source="test",
        )
        assert result == {"key": "value"}

    def test_markdown_fence_no_language(self) -> None:
        """``` fence without language specifier is stripped."""
        result = _parse_llm_json(
            '```\n{"key": "value"}\n```',
            source="test",
        )
        assert result == {"key": "value"}

    def test_preamble_text_before_json(self) -> None:
        """Explanatory text before JSON is ignored via bracket-matching."""
        result = _parse_llm_json(
            'Here is the result you asked for:\n{"key": "value"}\nHope that helps!',
            source="test",
        )
        assert result == {"key": "value"}

    def test_postamble_text_after_json(self) -> None:
        """Text after JSON closing brace is ignored."""
        result = _parse_llm_json(
            '{"key": "value"} Some extra text here.',
            source="test",
        )
        assert result == {"key": "value"}

    def test_trailing_comma_in_object(self) -> None:
        """Trailing comma before closing brace is removed."""
        result = _parse_llm_json(
            '{"key": "value",}',
            source="test",
        )
        assert result == {"key": "value"}

    def test_trailing_comma_in_array(self) -> None:
        """Trailing comma before closing bracket is removed."""
        result = _parse_llm_json(
            '[1, 2, 3,]',
            source="test",
        )
        assert result == [1, 2, 3]

    def test_control_characters_stripped(self) -> None:
        """Control characters (except \\t, \\n, \\r) are removed from strings."""
        result = _parse_llm_json(
            '{"key": "val\x00ue\x01"}',
            source="test",
        )
        assert result == {"key": "value"}

    def test_preserves_tab_newline_carriage_return(self) -> None:
        """Tab, newline, and carriage return are preserved in strings."""
        result = _parse_llm_json(
            '{"key": "line1\\nline2\\tindented\\r"}',
            source="test",
        )
        assert result == {"key": "line1\nline2\tindented\r"}

    def test_nested_objects_bracket_matching(self) -> None:
        """Nested braces are handled correctly by depth tracking."""
        result = _parse_llm_json(
            '{"outer": {"inner": [1, 2]}} trailing',
            source="test",
        )
        assert result == {"outer": {"inner": [1, 2]}}

    def test_deeply_nested_bracket_matching(self) -> None:
        """Deeply nested braces and brackets are handled correctly."""
        result = _parse_llm_json(
            '{"a": {"b": {"c": [{"d": "e"}]}}}',
            source="test",
        )
        assert result == {"a": {"b": {"c": [{"d": "e"}]}}}

    def test_mixed_object_and_array_root(self) -> None:
        """Finds the first brace or bracket as the root."""
        result = _parse_llm_json(
            'Before [1, 2, 3] after',
            source="test",
        )
        assert result == [1, 2, 3]

    def test_combined_failure_modes(self) -> None:
        """Fence, trailing comma, control chars — all handled together."""
        result = _parse_llm_json(
            '```json\n{\n  "key": "val\x1fue",\n  "x": 1,\n}\n```',
            source="test",
        )
        assert result == {"key": "value", "x": 1}

    def test_unparseable_raises_llm_json_parse_error(self) -> None:
        """Totally invalid content raises LLMJsonParseError with metadata."""
        with pytest.raises(LLMJsonParseError) as exc_info:
            _parse_llm_json("not json at all", source="extract_from_freeform")
        assert exc_info.value.source == "extract_from_freeform"
        assert exc_info.value.position is not None

    def test_unparseable_set_position(self) -> None:
        """Position field is set from json.JSONDecodeError.pos."""
        with pytest.raises(LLMJsonParseError) as exc_info:
            _parse_llm_json("{bad json}", source="test_func")
        assert exc_info.value.position == 1  # 'b' at position 1 is invalid
        assert "test_func" in str(exc_info.value.source)

    def test_empty_content_unparseable(self) -> None:
        """Empty string after sanitization raises LLMJsonParseError."""
        with pytest.raises(LLMJsonParseError) as exc_info:
            _parse_llm_json(" ", source="test")
        assert exc_info.value.source == "test"

    def test_bare_non_json_text_unparseable(self) -> None:
        """Bare text with no braces raises LLMJsonParseError."""
        with pytest.raises(LLMJsonParseError) as exc_info:
            _parse_llm_json("just some words", source="test")
        assert exc_info.value.source == "test"

    def test_no_raw_content_leaked(self) -> None:
        """LLMJsonParseError message does not contain raw LLM content."""
        with pytest.raises(LLMJsonParseError) as exc_info:
            _parse_llm_json("secret_api_key_12345 {invalid", source="test")
        assert "secret_api_key" not in str(exc_info.value)
        assert "12345" not in str(exc_info.value)

