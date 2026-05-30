from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel

load_dotenv()

from mementic.config import LLMConfig  # noqa: E402
from mementic.models import Constraint, ExtractionResult  # noqa: E402


FREE_FORM_SYSTEM_PROMPT = """You are a software architecture analyst. Given text from an engineering document, identify architectural decisions and constraints.

Rules:
1. DECISION: Only when a design pattern changed, a new library was introduced, or a strategic path was chosen. NOT routine refactors.
2. CONSTRAINT: A hard rule future developers/agents must not violate.
3. Include specific file paths as affected entities when possible.
4. Output ONLY valid JSON matching this schema — no markdown, no explanation.

{extraction_schema}
"""

SESSION_SYSTEM_PROMPT = """You are a software architecture analyst. Given an agent session log, git diff, and optional human notes, identify architectural decisions and constraints that were made or changed during this session.

Rules:
1. DECISION: Only when a design pattern changed, a new library was introduced, or a strategic path was chosen. NOT routine refactors, syntax fixes, or variable renames.
2. CONSTRAINT: A hard rule future agents must not violate.
3. Include specific file paths from the diff as affected entities.
4. Output ONLY valid JSON matching this schema — no markdown, no explanation.

{extraction_schema}
"""

ADR_CONSEQUENCES_SYSTEM_PROMPT = """You are a software architecture analyst. Given the "Consequences" section of an Architecture Decision Record (ADR), extract explicit constraints that future developers or agents must respect.

Rules:
1. A constraint is a hard rule that must not be violated.
2. Extract only constraints that represent architectural guardrails, not observations or notes.
3. Include specific file paths or module references as bound entities when mentioned.
4. If no constraints are found, return an empty list.
5. Output ONLY valid JSON matching this schema — no markdown, no explanation.

{constraints_schema}
"""


class _ConstraintListResponse(BaseModel):
    constraints: list[Constraint]


def get_client(config: LLMConfig) -> OpenAI:  # pragma: no cover
    """Create OpenAI client pointed at DeepSeek. Reads key from DEEPSEEK_API_KEY env var."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    return OpenAI(api_key=api_key, base_url="https://api.deepseek.com")


def extract_from_freeform(
    text: str,
    source_label: str,
    config: LLMConfig,
) -> ExtractionResult:
    """
    Extract architectural decisions and constraints from freeform text.
    Sends text to LLM with system prompt. Returns ExtractionResult with decisions,
    constraints, affected_entities, and edges_to_create.
    Uses the extraction_model from config.
    Set temperature=0.1 for deterministic output.
    Pass response_format with json_schema from ExtractionResult.model_json_schema().
    """
    if not text.strip():
        return ExtractionResult()

    client = get_client(config)

    extraction_schema = json.dumps(ExtractionResult.model_json_schema(), indent=2)
    system_prompt = FREE_FORM_SYSTEM_PROMPT.format(extraction_schema=extraction_schema)

    try:
        response = client.chat.completions.create(
            model=config.extraction_model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Source: {source_label}\n\nText:\n{text}"},
            ],
            response_format={"type": "json_object"},
        )
    except AuthenticationError:
        sys.stderr.write("ERROR: DeepSeek API key is invalid. Check DEEPSEEK_API_KEY.\n")
        raise
    except RateLimitError:
        sys.stderr.write("WARNING: DeepSeek rate limit hit. Skipping this source.\n")
        return ExtractionResult()
    except (APITimeoutError, APIConnectionError):
        sys.stderr.write("WARNING: DeepSeek API unreachable. Skipping this source.\n")
        return ExtractionResult()
    except APIError as e:
        sys.stderr.write(f"WARNING: DeepSeek API error: {e}. Skipping this source.\n")
        return ExtractionResult()

    content = response.choices[0].message.content
    if content is None:
        return ExtractionResult()

    data = json.loads(content)
    result = ExtractionResult.model_validate(data)
    return result


def extract_from_session(
    session_log: str,
    git_diff: str,
    user_notes: str,
    config: LLMConfig,
) -> ExtractionResult:
    """
    Extract decisions from an agent session (log + diff + human notes).
    Similar to extract_from_freeform but with a session-specific system prompt.
    """
    combined = ""

    if session_log.strip():
        combined += f"## Agent Session Log\n{session_log}\n\n"

    if git_diff.strip():
        combined += f"## Git Diff\n{git_diff}\n\n"

    if user_notes.strip():
        combined += f"## Human Notes\n{user_notes}\n\n"

    if not combined.strip():
        return ExtractionResult()

    client = get_client(config)

    extraction_schema = json.dumps(ExtractionResult.model_json_schema(), indent=2)
    system_prompt = SESSION_SYSTEM_PROMPT.format(extraction_schema=extraction_schema)

    try:
        response = client.chat.completions.create(
            model=config.extraction_model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": combined},
            ],
            response_format={"type": "json_object"},
        )
    except AuthenticationError:
        sys.stderr.write("ERROR: DeepSeek API key is invalid. Check DEEPSEEK_API_KEY.\n")
        raise
    except RateLimitError:
        sys.stderr.write("WARNING: DeepSeek rate limit hit. Skipping this source.\n")
        return ExtractionResult()
    except (APITimeoutError, APIConnectionError):
        sys.stderr.write("WARNING: DeepSeek API unreachable. Skipping this source.\n")
        return ExtractionResult()
    except APIError as e:
        sys.stderr.write(f"WARNING: DeepSeek API error: {e}. Skipping this source.\n")
        return ExtractionResult()

    content = response.choices[0].message.content
    if content is None:
        return ExtractionResult()

    data = json.loads(content)
    result = ExtractionResult.model_validate(data)
    return result


def extract_from_adr_consequences(
    consequences_text: str,
    config: LLMConfig,
) -> list[Constraint]:
    """
    Extract Constraint nodes from an ADR Consequences section (freeform text).
    Uses LLM. Returns list of Constraint objects.
    """
    if not consequences_text.strip():
        return []

    client = get_client(config)

    constraints_schema = json.dumps(_ConstraintListResponse.model_json_schema(), indent=2)
    system_prompt = ADR_CONSEQUENCES_SYSTEM_PROMPT.format(constraints_schema=constraints_schema)

    try:
        response = client.chat.completions.create(
            model=config.extraction_model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": consequences_text},
            ],
            response_format={"type": "json_object"},
        )
    except AuthenticationError:
        sys.stderr.write("ERROR: DeepSeek API key is invalid. Check DEEPSEEK_API_KEY.\n")
        raise
    except RateLimitError:
        sys.stderr.write("WARNING: DeepSeek rate limit hit. Skipping this source.\n")
        return []
    except (APITimeoutError, APIConnectionError):
        sys.stderr.write("WARNING: DeepSeek API unreachable. Skipping this source.\n")
        return []
    except APIError as e:
        sys.stderr.write(f"WARNING: DeepSeek API error: {e}. Skipping this source.\n")
        return []

    content = response.choices[0].message.content
    if content is None:
        return []

    data = json.loads(content)
    parsed = _ConstraintListResponse.model_validate(data)
    return parsed.constraints
