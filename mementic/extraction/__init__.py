"""Extraction module — LLM-based and deterministic extractors.

Public functions and constants are re-exported from the sub-modules
so callers can continue to use ``from mementic.extraction import X``.
"""

from __future__ import annotations

# LLM extractor (formerly mementic/extraction.py)
from mementic.extraction.llm import (
    ADR_CONSEQUENCES_SYSTEM_PROMPT,
    ARCHITECTURE_KEYWORDS,
    CODEBASE_DETECTION_SYSTEM_PROMPT,
    FREE_FORM_SYSTEM_PROMPT,
    SESSION_SYSTEM_PROMPT,
    _CONSTRAINTS_FORMAT,
    _ConstraintListResponse,
    _DEFAULT_MAX_MESSAGES,
    _EXTRACTION_FORMAT,
    _MESSAGE_BOUNDARY_PREFIXES,
    _call_llm_with_retry,
    _extract_architecture_windows,
    _is_opencode_log,
    _provider_prefix,
    _strip_session_noise,
    _truncate_tail,
    extract_from_adr_consequences,
    extract_from_codebase_summary,
    extract_from_freeform,
    extract_from_session,
    get_client,
)

# Deterministic extractor (formerly mementic/deterministic.py)
from mementic.extraction.deterministic import (
    extract_constraints_from_consequences as _det_extract_constraints_from_consequences,
    extract_from_markdown,
    extract_from_session_deterministic,
)

__all__ = [
    "ADR_CONSEQUENCES_SYSTEM_PROMPT",
    "ARCHITECTURE_KEYWORDS",
    "CODEBASE_DETECTION_SYSTEM_PROMPT",
    "FREE_FORM_SYSTEM_PROMPT",
    "SESSION_SYSTEM_PROMPT",
    "_CONSTRAINTS_FORMAT",
    "_ConstraintListResponse",
    "_DEFAULT_MAX_MESSAGES",
    "_EXTRACTION_FORMAT",
    "_MESSAGE_BOUNDARY_PREFIXES",
    "_call_llm_with_retry",
    "_extract_architecture_windows",
    "_is_opencode_log",
    "_provider_prefix",
    "_strip_session_noise",
    "_truncate_tail",
    "extract_from_adr_consequences",
    "extract_from_codebase_summary",
    "extract_from_freeform",
    "extract_from_session",
    "extract_constraints_from_consequences",
    "extract_from_markdown",
    "extract_from_session_deterministic",
    "get_client",
]

# Re-export the deterministic consequences extractor under the name the pipeline
# uses (it takes the same signature but lives in the deterministic sub-module).
extract_constraints_from_consequences = _det_extract_constraints_from_consequences
