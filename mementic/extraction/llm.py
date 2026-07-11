from __future__ import annotations

import re
import sys
import time
from typing import Any

import json5
from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError, model_validator

load_dotenv()

from mementic.config import LLMConfig  # noqa: E402
from mementic.models import Constraint, DecisionStatus, ExtractionResult, LLMJsonParseError  # noqa: E402


def _parse_llm_json(content: str, source: str = "unknown") -> dict:
    """Parse JSON from an LLM response, handling common failure modes.

    Applies fence stripping and string-aware bracket matching, then
    delegates to :func:`json5.loads` which handles single quotes,
    unquoted keys, trailing commas, ``NaN`` / ``Infinity``, comments,
    and control characters in a single pass.

    Args:
        content: The raw LLM response text.
        source: Name of the calling extraction function for error metadata.

    Returns:
        Parsed dictionary.

    Raises:
        LLMJsonParseError: If parsing fails after all attempts.
    """
    text = content.strip()

    # 1. Strip markdown code fences if present.
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()

    # 2. String-aware bracket match to find the outermost JSON object.
    start = text.find("{")
    if start == -1:
        start = text.find("[")
    if start >= 0:
        open_char = text[start]
        close_char = "}" if open_char == "{" else "]"
        depth = 0
        in_string = False
        escape_next = False
        end = -1
        for i in range(start, len(text)):
            ch = text[i]
            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == open_char:
                depth += 1
            elif ch == close_char:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > start:
            text = text[start:end]

    # 3. Parse with json5 (handles trailing commas, control chars,
    #    single quotes, unquoted keys, NaN/Infinity, comments, hex).
    try:
        return json5.loads(text)
    except Exception as e:
        position: int | None = getattr(e, "pos", None)
        raise LLMJsonParseError(
            f"Failed to parse LLM response: {e}",
            source=source,
            position=position,
        ) from None


# Spec 10a — Session truncation

ARCHITECTURE_KEYWORDS: list[str] = [
    "record_decision", "record_constraint",  # MCP tool calls (strongest signal)
    "rejected", "instead of",                  # tradeoff/explicit rejection
    "ADR", "tradeoff", "trade-off",             # formal constructs
    "design decision", "design choice",          # explicit design talk
    "migrate", "migration",                      # structural changes
    "deprecate", "alternatively",                # lifecycle + tradeoff discussion
]
# NOTE: "architecture", "architectural", "constraint", "pattern", "refactor",
# "library", "dependency", "implement" were removed — they match nearly every
# message in architecture-focused sessions, producing massive merged windows.

_MESSAGE_BOUNDARY_PREFIXES = (
    "## ", "[user]", "[agent]", "[assistant]", "[system]", "---"
)

# Default tail size for session truncation when no explicit max is given.
# Halved from 100 in Spec 13 to reduce token cost when keyword windowing
# does not match or its windows overflow.
_DEFAULT_MAX_MESSAGES = 50

_KEYWORD_PATTERN = re.compile(
    "|".join(re.escape(kw) for kw in ARCHITECTURE_KEYWORDS),
    re.IGNORECASE,
)

# Pattern to strip opencode log metadata prefixes from each line
_LOG_PREFIX_PATTERN = re.compile(
    r"^timestamp=[^ ]+ level=[^ ]+ run=[^ ]+ "
)


def _is_opencode_log(text: str) -> bool:
    """Return True if ``text`` is in opencode log format.

    Detection is based on at least one line carrying the
    ``timestamp=... level=... run=...`` prefix. The guard prevents the
    blank-line stripping and ``hash=`` heuristic (both opencode-specific)
    from mutating non-opencode text such as Claude-style transcripts or
    freeform notes.
    """
    return any(_LOG_PREFIX_PATTERN.search(line) for line in text.split("\n"))


def _strip_session_noise(text: str) -> str:
    """Strip opencode operational log noise from session text.

    Only operates on text in opencode log format (see :func:`_is_opencode_log`).
    Plain text without that marker is returned unchanged so non-opencode
    sessions (Claude-style ``[user]`` / ``[agent]`` transcripts, freeform
    notes) are not mutated.

    Removes: timestamp/level/run prefixes, tracking lines (hash/cwd/git
    metadata), and blank lines. Keeps message content.

    Args:
        text: Raw session log text.

    Returns:
        Cleaned text, or original if not in opencode log format.
    """
    if not text.strip():
        return text

    if not _is_opencode_log(text):
        return text

    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if " hash=" in stripped and " message=" not in stripped:
            continue
        cleaned.append(_LOG_PREFIX_PATTERN.sub("", stripped))
    return "\n".join(cleaned)


def _extract_architecture_windows(
    session_log: str, keywords: list[str], context_messages: int = 20
) -> str:
    """Scan session log for architecture keywords and extract context windows.

    Splits the session log into lines, searches case-insensitive for each
    keyword, and creates windows of ±context_messages lines around each match.
    Overlapping windows are merged, and non-contiguous windows are joined
    with "\\n...\\n" separators.

    Args:
        session_log: The full session log text.
        keywords: Architecture keywords to search for.
        context_messages: Number of lines of context before and after a match.

    Returns:
        Extracted windows joined as a single string, or empty string if no
        keywords matched.
    """
    if not session_log.strip():
        return ""

    lines = session_log.split("\n")

    match_indices: set[int] = set()
    for i, line in enumerate(lines):
        line_lower = line.lower()
        for kw in keywords:
            if kw.lower() in line_lower:
                match_indices.add(i)
                break

    if not match_indices:
        return ""

    windows: list[tuple[int, int]] = []
    for idx in sorted(match_indices):
        start = max(0, idx - context_messages)
        end = min(len(lines), idx + context_messages + 1)
        windows.append((start, end))

    merged: list[tuple[int, int]] = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    parts = ["\n".join(lines[s:e]) for s, e in merged]
    return "\n...\n".join(parts)


def _truncate_tail(session_log: str, max_messages: int = _DEFAULT_MAX_MESSAGES) -> str:
    """Truncate session log to the last N messages.

    A message boundary is a line starting with one of the recognized prefixes
    (``## `` for opencode, ``[user]``/``[agent]``/``[assistant]``/``[system]``
    for Claude-style, ``---`` horizontal rules). If no recognized boundaries
    are found, falls back to the last N lines.

    Args:
        session_log: The full session log text.
        max_messages: Maximum number of messages (or lines) to keep.

    Returns:
        Truncated session log, or the full log if shorter than max_messages.
        Returns empty string when max_messages is 0.
    """
    if max_messages == 0:
        return ""

    if not session_log.strip():
        return session_log

    lines = session_log.split("\n")

    boundary_indices: list[int] = []
    for i, line in enumerate(lines):
        if line.startswith(_MESSAGE_BOUNDARY_PREFIXES):
            boundary_indices.append(i)

    if not boundary_indices:
        if len(lines) <= max_messages:
            return session_log
        return "\n".join(lines[-(max_messages):])

    if len(boundary_indices) <= max_messages:
        return session_log

    start_idx = boundary_indices[len(boundary_indices) - max_messages]
    return "\n".join(lines[start_idx:])


# Spec 13 — Compact extraction formats (86% token savings over JSON Schema)

_EXTRACTION_FORMAT = """{
  "decisions": [{"id": "slug", "title": "Title", "rationale": "Why", "rejected_alternatives": ["Alt1"], "source": "origin", "status": "accepted", "created_at": "ISO8601", "modified_at": "ISO8601"}],
  "constraints": [{"id": "slug", "description": "Hard rule", "bound_entities": ["file.py"], "source": "origin", "status": "active", "created_at": "ISO8601"}],
  "affected_entities": ["path/to/file.py"],
  "edges_to_create": [{"source": "slug", "target": "slug", "relation": "enforces"}]
}"""

_CONSTRAINTS_FORMAT = """{
  "constraints": [{"id": "slug", "description": "Hard rule", "bound_entities": ["file.py"], "source": "origin", "status": "active", "created_at": "ISO8601"}]
}"""

FREE_FORM_SYSTEM_PROMPT = """You are a software architecture analyst. Given text from an engineering document, identify architectural decisions and constraints.

Rules:
1. DECISION: Only when a design pattern changed, a new library was introduced, or a strategic path was chosen. NOT routine refactors.
2. CONSTRAINT: A hard rule future developers/agents must not violate.
3. Include specific file paths as affected entities when possible.
4. Output ONLY valid JSON in this format — no markdown, no explanation.

{extraction_format}
"""

SESSION_SYSTEM_PROMPT = """You are a software architecture analyst. Given an agent session log, git diff, and optional human notes, identify architectural decisions and constraints that were made or changed during this session.

Rules:
1. DECISION: Only when a design pattern changed, a new library was introduced, or a strategic path was chosen. NOT routine refactors, syntax fixes, or variable renames.
2. CONSTRAINT: A hard rule future agents must not violate.
3. Include specific file paths from the diff as affected entities.
4. Output ONLY valid JSON in this format — no markdown, no explanation.

{extraction_format}
"""

ADR_CONSEQUENCES_SYSTEM_PROMPT = """You are a software architecture analyst. Given the "Consequences" section of an Architecture Decision Record (ADR), extract explicit constraints that future developers or agents must respect.

Rules:
1. A constraint is a hard rule that must not be violated.
2. Extract only constraints that represent architectural guardrails, not observations or notes.
3. Include specific file paths or module references as bound entities when mentioned.
4. If no constraints are found, return an empty list.
5. Output ONLY valid JSON in this format — no markdown, no explanation.

{constraints_format}
"""

CODEBASE_DETECTION_SYSTEM_PROMPT = """You are a software architecture analyst. Given a structural summary of a codebase, detect the architectural decisions that are evident from the codebase itself — imports, directory layout, configuration, and documentation.

Rules:
1. DECISION: Detect technology choices that are clearly present in the codebase:
   - Tech stack choices (database, framework, message queue, cache, ORM)
   - Architectural patterns (layered, microservices, event-driven, CQRS)
   - Library/tool choices (HTTP client, serializer, testing framework)
   - Protocol/API decisions (REST, GraphQL, gRPC, WebSocket)
   - Infrastructure decisions (Docker, k8s, serverless, CI/CD)
   - Language/framework conventions (async patterns, type enforcement)
2. Only include decisions where there is CLEAR evidence in the structural summary.
   Skip if you're uncertain. It's better to miss a decision than to hallucinate one.
 3. For each detected decision, set status to "accepted".
4. Include specific file paths as affected entities when possible.
5. Output ONLY valid JSON in this format — no markdown, no explanation.

{extraction_format}
"""


class _ConstraintListResponse(BaseModel):
    constraints: list[Constraint]

    @model_validator(mode="before")
    @classmethod
    def _coerce_llm_inputs(cls, data: Any) -> Any:
        """Coerce common LLM mistakes: null → [], dict → [dict], string → [string]."""
        if not isinstance(data, dict):
            return data
        val = data.get("constraints")
        if val is None:
            data["constraints"] = []
        elif isinstance(val, dict):
            data["constraints"] = [val]
        elif isinstance(val, str):
            data["constraints"] = [val]

        # Strip null from optional fields on nested constraints so
        # Pydantic uses declared defaults (pipeline fills real values).
        items = data.get("constraints")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    for list_field in ("bound_entities",):
                        fv = item.get(list_field)
                        if fv is None:
                            item[list_field] = []
                        elif isinstance(fv, str):
                            item[list_field] = [fv]
                    for str_field in ("enforced_by", "repo"):
                        if str_field in item and item[str_field] is None:
                            del item[str_field]
        return data


def get_client(config: LLMConfig) -> OpenAI:
    """Create an OpenAI-compatible client for the configured LLM provider.

    Uses config-driven base_url and API key. Raises RuntimeError with a
    provider-specific message when the required API key is missing.

    Args:
        config: LLM configuration from MementicConfig.

    Returns:
        An OpenAI client pointed at the configured provider.

    Raises:
        RuntimeError: If the provider requires an API key and it's not set.
    """
    base_url = config.base_url
    api_key = config.ensure_api_key()
    return OpenAI(api_key=api_key, base_url=base_url, timeout=300.0)


def _provider_prefix(config: LLMConfig) -> str:
    """Return a human-readable provider label for error messages."""
    provider = config.provider.capitalize()
    env_var = config.key_env_var
    if env_var:
        return f"{provider} ({env_var})"
    return provider


def _call_llm_with_retry(
    client: OpenAI,
    messages: list[dict[str, str]],
    model: str,
    response_format: dict | None = None,
    max_retries: int = 3,
) -> Any:
    """Call the LLM API with exponential backoff on transient errors.

    Retries on RateLimitError and server errors (5xx).
    Fails immediately on AuthenticationError and other permanent errors.

    Args:
        client: The OpenAI client.
        messages: The message list for the API call.
        model: The model name.
        response_format: Optional response format dict.
        max_retries: Maximum number of retries (default 3).

    Returns:
        The ChatCompletion response.

    Raises:
        AuthenticationError: Immediately re-raised (fatal).
        RateLimitError: Re-raised after exhausting retries.
        APITimeoutError, APIConnectionError: Re-raised after exhausting retries.
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "temperature": 0.1,
                "max_tokens": 16384,
                "messages": messages,
            }
            if response_format is not None:
                kwargs["response_format"] = response_format

            return client.chat.completions.create(**kwargs)
        except AuthenticationError:
            raise  # Fatal — no retry
        except RateLimitError as e:
            last_exception = e
            if attempt < max_retries:
                delay = 2**attempt  # 1, 2, 4, ...
                time.sleep(delay)
        except (APITimeoutError, APIConnectionError) as e:
            last_exception = e
            if attempt < max_retries:
                time.sleep(1)
        except APIError as e:
            last_exception = e
            if attempt < max_retries:
                time.sleep(1)

    # Exhausted retries
    if last_exception is not None:
        raise last_exception
    # Should never reach here but satisfy type checker
    raise RuntimeError("Unexpected: retry loop exhausted with no exception")


def extract_from_freeform(
    text: str,
    source_label: str,
    config: LLMConfig,
) -> ExtractionResult:
    """
    Extract architectural decisions and constraints from freeform text.

    Sends text to LLM with a system prompt containing a compact format
    template (``_EXTRACTION_FORMAT``) and ``response_format={"type": "json_object"}``
    to constrain the output. Returns an ExtractionResult with decisions,
    constraints, affected_entities, and edges_to_create.

    Uses the extraction_model from config. Temperature is set to 0.1 for
    deterministic output.

    Returns:
        An ExtractionResult with decisions, constraints, affected_entities,
        and edges_to_create.
    """
    if not text.strip():
        return ExtractionResult()

    client = get_client(config)

    system_prompt = FREE_FORM_SYSTEM_PROMPT.format(extraction_format=_EXTRACTION_FORMAT)
    prefix = _provider_prefix(config)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Source: {source_label}\n\nText:\n{text}"},
    ]

    try:
        response = _call_llm_with_retry(
            client, messages, config.extraction_model,
            response_format={"type": "json_object"},
        )
    except AuthenticationError:
        msg = (
            f"Authentication failed for {prefix}. "
            f"The API key is set but appears to be invalid. "
            f"Check {config.key_env_var}."
        )
        raise RuntimeError(msg) from None
    except RateLimitError:
        sys.stderr.write(f"WARNING: {prefix} rate limit hit. Skipping this source.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except (APITimeoutError, APIConnectionError):
        sys.stderr.write(f"WARNING: {prefix} API unreachable. Skipping this source.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except APIError as e:
        sys.stderr.write(f"WARNING: {prefix} API error: {e}. Skipping this source.\n")
        sys.stderr.flush()
        return ExtractionResult()

    content = response.choices[0].message.content
    if content is None:
        return ExtractionResult()

    try:
        data = _parse_llm_json(content, source="extract_from_freeform")
        result = ExtractionResult.model_validate(data)
        return result
    except LLMJsonParseError:
        sys.stderr.write("WARNING: LLM returned unparseable text. Skipping.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except ValidationError:
        sys.stderr.write("WARNING: LLM returned valid JSON but schema mismatch. Skipping.\n")
        sys.stderr.flush()
        return ExtractionResult()


def extract_from_session(
    session_log: str,
    git_diff: str,
    user_notes: str,
    config: LLMConfig,
    max_messages: int = _DEFAULT_MAX_MESSAGES,
    custom_keywords: list[str] | None = None,
) -> ExtractionResult:
    """
    Extract decisions from an agent session (log + diff + human notes).
    Similar to extract_from_freeform but with a session-specific system prompt.

    Applies Spec 10a session truncation: smart windowing via architecture
    keyword scan with tail fallback.

    Args:
        session_log: Raw agent session log.
        git_diff: Git diff from the session.
        user_notes: Optional human-provided notes.
        config: LLM configuration.
        max_messages: Max tail messages when no architecture keywords match.
        custom_keywords: Additional architecture keywords to scan for,
            merged with built-in ARCHITECTURE_KEYWORDS.

    Returns:
        An ExtractionResult with decisions and constraints from the session.
    """
    # Spec 10a: truncate session_log before building the prompt
    if session_log.strip():
        keywords = (
            ARCHITECTURE_KEYWORDS + custom_keywords
            if custom_keywords
            else ARCHITECTURE_KEYWORDS
        )
        windows = _extract_architecture_windows(
            session_log, keywords, context_messages=20
        )
        # Cap: if windowing produces too much, fall back to tail.
        # 120K chars ≈ 30K tokens. More than that means the keywords
        # saturated (every message matched), defeating the purpose.
        if windows and len(windows) < 120_000:
            session_log = windows
        elif windows:
            # Windows are too large — keywords saturated. Tail is better.
            session_log = _truncate_tail(session_log, max_messages)
        else:
            session_log = _truncate_tail(session_log, max_messages)

    # Strip operational noise before sending to LLM
    if session_log.strip():
        session_log = _strip_session_noise(session_log)

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

    system_prompt = SESSION_SYSTEM_PROMPT.format(extraction_format=_EXTRACTION_FORMAT)
    prefix = _provider_prefix(config)

    session_messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": combined},
    ]

    try:
        response = _call_llm_with_retry(
            client, session_messages, config.extraction_model,
            response_format={"type": "json_object"},
        )
    except AuthenticationError:
        msg = (
            f"Authentication failed for {prefix}. "
            f"The API key is set but appears to be invalid. "
            f"Check {config.key_env_var}."
        )
        raise RuntimeError(msg) from None
    except RateLimitError:
        sys.stderr.write(f"WARNING: {prefix} rate limit hit. Skipping this source.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except (APITimeoutError, APIConnectionError):
        sys.stderr.write(f"WARNING: {prefix} API unreachable. Skipping this source.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except APIError as e:
        sys.stderr.write(f"WARNING: {prefix} API error: {e}. Skipping this source.\n")
        sys.stderr.flush()
        return ExtractionResult()

    content = response.choices[0].message.content
    if content is None:
        return ExtractionResult()

    try:
        data = _parse_llm_json(content, source="extract_from_session")
        result = ExtractionResult.model_validate(data)
        return result
    except LLMJsonParseError:
        sys.stderr.write("WARNING: LLM returned unparseable text. Skipping.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except ValidationError:
        sys.stderr.write("WARNING: LLM returned valid JSON but schema mismatch. Skipping.\n")
        sys.stderr.flush()
        return ExtractionResult()


def extract_from_adr_consequences(
    consequences_text: str,
    config: LLMConfig,
) -> list[Constraint]:
    """
    Extract Constraint nodes from an ADR Consequences section (freeform text).
    Uses LLM. Returns list of Constraint objects.

    Returns:
        A list of extracted Constraint objects.
    """
    if not consequences_text.strip():
        return []

    client = get_client(config)

    system_prompt = ADR_CONSEQUENCES_SYSTEM_PROMPT.format(constraints_format=_CONSTRAINTS_FORMAT)
    prefix = _provider_prefix(config)

    adr_messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": consequences_text},
    ]

    try:
        response = _call_llm_with_retry(
            client, adr_messages, config.extraction_model,
            response_format={"type": "json_object"},
        )
    except AuthenticationError:
        msg = (
            f"Authentication failed for {prefix}. "
            f"The API key is set but appears to be invalid. "
            f"Check {config.key_env_var}."
        )
        raise RuntimeError(msg) from None
    except RateLimitError:
        sys.stderr.write(f"WARNING: {prefix} rate limit hit. Skipping this source.\n")
        sys.stderr.flush()
        return []
    except (APITimeoutError, APIConnectionError):
        sys.stderr.write(f"WARNING: {prefix} API unreachable. Skipping this source.\n")
        sys.stderr.flush()
        return []
    except APIError as e:
        sys.stderr.write(f"WARNING: {prefix} API error: {e}. Skipping this source.\n")
        sys.stderr.flush()
        return []

    content = response.choices[0].message.content
    if content is None:
        return []

    try:
        data = _parse_llm_json(content, source="extract_from_adr_consequences")
        parsed = _ConstraintListResponse.model_validate(data)
        return parsed.constraints
    except LLMJsonParseError:
        sys.stderr.write("WARNING: LLM returned unparseable text. Skipping.\n")
        sys.stderr.flush()
        return []
    except ValidationError:
        sys.stderr.write("WARNING: LLM returned valid JSON but schema mismatch. Skipping.\n")
        sys.stderr.flush()
        return []


def extract_from_codebase_summary(
    summary: str,
    config: LLMConfig,
) -> ExtractionResult:
    """Extract architectural decisions from a codebase structural summary.

    Sends the deterministic structural summary (from
    :func:`mementic.sources.codebase.scan_codebase_structure`) to the LLM
    for pattern detection. All detected decisions are marked ``accepted``
    — automatically accepted via auto-resolve capture.

    Args:
        summary: Markdown summary produced by the structural scanner.
        config: LLM configuration.

    Returns:
        An ExtractionResult with detected decisions and constraints.
    """
    if not summary.strip():
        return ExtractionResult()

    client = get_client(config)

    system_prompt = CODEBASE_DETECTION_SYSTEM_PROMPT.format(extraction_format=_EXTRACTION_FORMAT)
    prefix = _provider_prefix(config)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": summary},
    ]

    try:
        response = _call_llm_with_retry(
            client, messages, config.extraction_model,
            response_format={"type": "json_object"},
        )
    except AuthenticationError:
        msg = (
            f"Authentication failed for {prefix}. "
            f"The API key is set but appears to be invalid. "
            f"Check {config.key_env_var}."
        )
        raise RuntimeError(msg) from None
    except RateLimitError:
        sys.stderr.write(f"WARNING: {prefix} rate limit hit. Skipping codebase scan.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except (APITimeoutError, APIConnectionError):
        sys.stderr.write(f"WARNING: {prefix} API unreachable. Skipping codebase scan.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except APIError as e:
        sys.stderr.write(f"WARNING: {prefix} API error: {e}. Skipping codebase scan.\n")
        sys.stderr.flush()
        return ExtractionResult()

    content = response.choices[0].message.content
    if content is None:
        return ExtractionResult()

    try:
        data = _parse_llm_json(content, source="extract_from_codebase_summary")
        result = ExtractionResult.model_validate(data)
    except LLMJsonParseError:
        sys.stderr.write("WARNING: LLM returned unparseable text for codebase scan. Skipping.\n")
        sys.stderr.flush()
        return ExtractionResult()
    except ValidationError:
        sys.stderr.write("WARNING: LLM returned valid JSON for codebase scan but schema mismatch. Skipping.\n")
        sys.stderr.flush()
        return ExtractionResult()

    # Force all detected decisions to accepted status
    for decision in result.decisions:
        decision.status = DecisionStatus.ACCEPTED
        decision.source = "init"

    return result
