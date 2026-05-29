from __future__ import annotations

import re
from datetime import datetime, timezone

from mementic.models import Constraint, Decision


def slugify(text: str) -> str:
    """Convert text to URL-friendly ID. Lowercase, hyphens, strip special chars."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text.strip("-")


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
            lines.append(f"• {d.title} ({d.timestamp})")
            lines.append(f"  {d.rationale}")
            lines.append(f"  Rejected: {d.rejected_alternatives}")

    return "\n".join(lines)
