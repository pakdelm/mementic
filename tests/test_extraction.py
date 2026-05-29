from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


from mementic.config import LLMConfig
from mementic.extraction import (
    extract_from_adr_consequences,
    extract_from_freeform,
    extract_from_session,
)
from mementic.models import Constraint, Decision, DecisionStatus, ExtractionResult, GraphEdge


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
                    timestamp="2024-01-01T00:00:00Z",
                )
            ],
            constraints=[],
            affected_entities=["src/main.py"],
            edges_to_create=[],
        )

        with patch(
            "mementic.extraction.get_client", return_value=_mock_client(result)
        ):
            output = extract_from_freeform(
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
            timestamp="2024-02-01T00:00:00Z",
        )
        constraint = Constraint(
            id="c-1",
            description="All models must use pydantic",
            bound_entities=["src/models.py"],
            enforced_by="d-1",
            source="freeform",
            status="active",
            timestamp="2024-02-01T00:00:00Z",
        )
        result = ExtractionResult(
            decisions=[decision],
            constraints=[constraint],
            affected_entities=["src/models.py"],
            edges_to_create=[GraphEdge(source="d-1", target="c-1", relation="enforces")],
        )

        with patch(
            "mementic.extraction.get_client", return_value=_mock_client(result)
        ):
            output = extract_from_freeform(
                "We use pydantic for all models.", "adr-002", config
            )

        assert len(output.decisions) == 1
        assert len(output.constraints) == 1
        assert output.constraints[0].description == "All models must use pydantic"
        assert len(output.edges_to_create) == 1
        assert output.edges_to_create[0].relation == "enforces"

    def test_empty_text_returns_empty_result(self) -> None:
        config = LLMConfig()
        output = extract_from_freeform("", "empty", config)
        assert isinstance(output, ExtractionResult)
        assert output.decisions == []
        assert output.constraints == []
        assert output.affected_entities == []
        assert output.edges_to_create == []

    def test_whitespace_only_text_returns_empty_result(self) -> None:
        config = LLMConfig()
        output = extract_from_freeform("   \n  \t  ", "ws", config)
        assert isinstance(output, ExtractionResult)
        assert output.decisions == []

    def test_passes_temperature_and_model(self) -> None:
        config = LLMConfig(extraction_model="deepseek-v3")
        result = ExtractionResult()

        with patch(
            "mementic.extraction.get_client", return_value=_mock_client(result)
        ) as mock_get_client:
            extract_from_freeform("some text", "src", config)

        mock_client = mock_get_client.return_value
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0.1
        assert call_kwargs["model"] == "deepseek-v3"
        assert "response_format" in call_kwargs
        rf = call_kwargs["response_format"]
        assert rf["type"] == "json_schema"
        assert "schema" in rf["json_schema"]
        assert rf["json_schema"]["name"] == "extraction_result"

    def test_none_content_returns_empty_result(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = None
        response = MagicMock()
        response.choices = [choice]
        client.chat.completions.create.return_value = response

        with patch("mementic.extraction.get_client", return_value=client):
            output = extract_from_freeform("text", "src", config)

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
                    timestamp="2024-03-01T00:00:00Z",
                )
            ],
            constraints=[],
            affected_entities=["requirements.txt", "src/http_client.py"],
            edges_to_create=[],
        )

        with patch(
            "mementic.extraction.get_client", return_value=_mock_client(result)
        ):
            output = extract_from_session(
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
        output = extract_from_session("", "", "", config)
        assert output == ExtractionResult()

    def test_whitespace_only_inputs_return_empty_result(self) -> None:
        config = LLMConfig()
        output = extract_from_session("  \n", "  ", "\t", config)
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
                timestamp="2024-01-01T00:00:00Z",
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

        with patch("mementic.extraction.get_client", return_value=client):
            output = extract_from_adr_consequences(
                "Therefore, all DB access must go through the ORM.",
                config,
            )

        assert len(output) == 1
        assert isinstance(output[0], Constraint)
        assert output[0].description == "Must not use raw SQL"

    def test_empty_text_returns_empty_list(self) -> None:
        config = LLMConfig()
        output = extract_from_adr_consequences("", config)
        assert output == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        config = LLMConfig()
        output = extract_from_adr_consequences("   \n  ", config)
        assert output == []

    def test_none_content_returns_empty_list(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = None
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj

        with patch("mementic.extraction.get_client", return_value=client):
            output = extract_from_adr_consequences("some text", config)

        assert output == []

    def test_no_constraints_in_response(self) -> None:
        config = LLMConfig()
        client = MagicMock()
        choice = MagicMock()
        choice.message.content = json.dumps({"constraints": []})
        response_obj = MagicMock()
        response_obj.choices = [choice]
        client.chat.completions.create.return_value = response_obj

        with patch("mementic.extraction.get_client", return_value=client):
            output = extract_from_adr_consequences("some text", config)

        assert output == []

