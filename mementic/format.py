from __future__ import annotations

import re
from datetime import datetime, timezone

from mementic.models import Constraint, Decision


def count_tokens(text: str) -> int:
    """Count the number of tokens in a text string.

    Uses tiktoken if installed for accurate counting; falls back to
    ``len(text) // 4`` (reasonable estimate for English text).

    Args:
        text: The text to count tokens for.

    Returns:
        Estimated token count as an integer.
    """
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except (ImportError, Exception):
        return len(text) // 4


def slugify(text: str, max_length: int | None = None) -> str:
    """Convert text to URL-friendly ID. Lowercase, hyphens, strip special chars.

    Args:
        text: The text to slugify.
        max_length: If set, truncate the slug to this length at a word boundary.
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = text.strip("-")
    if max_length is not None and len(text) > max_length:
        # Truncate at word boundary (last hyphen within the limit)
        text = text[:max_length].rsplit("-", 1)[0].strip("-")
    return text


def timestamp() -> str:
    """Return ISO 8601 timestamp string."""
    return datetime.now(timezone.utc).isoformat()


def format_context_pack(
    task: str,
    decisions: list[Decision],
    constraints: list[Constraint],
) -> str:
    """Produces structured markdown for agent context injection."""
    lines = [f"## Architectural Context for: {task}"]

    if constraints:
        lines.append("")
        lines.append("### Constraints (MUST follow)")
        for c in constraints:
            lines.append(f"[C] {c.id}")
            lines.append(f'   "{c.description}"')
            enforced = c.enforced_by if c.enforced_by else "unknown"
            lines.append(f"   Established by: {enforced}")

    if decisions:
        lines.append("")
        lines.append("### Relevant Decisions")
        for d in decisions:
            lines.append(f"* {d.title} ({d.timestamp})")
            lines.append(f"  {d.rationale}")
            if d.rejected_alternatives:
                lines.append(f"  Rejected: {', '.join(d.rejected_alternatives)}")

    return "\n".join(lines)
