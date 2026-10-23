"""Generate the agent-workflow demo SVG for the README.

Unlike ``demo.svg`` (a plain terminal recording of CLI commands), this shows a
harness transcript: the user names a task, the agent thinks, calls mementic
through ``uv run`` the way an agent does, and states its outcome. The flow
follows the enforcement block: query the pack before touching code, read the
constraint, run the file gate, cite what was applied, record the decision
inline. Turns are prefixed ``user`` / ``agent`` / ``tool``; tool results follow
real CLI output shapes.

Run after CLI output changes:

    uv run python scripts/make_agent_svg.py

The result is ``docs/agent.svg``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "docs" / "agent.svg"

WIDTH_FLOOR = 760
TITLEBAR = 34
PAD_X = 22
PAD_TOP = 24
PAD_BOTTOM = 26
LINE_HEIGHT = 21
FONT_SIZE = 13
CHAR_WIDTH = 7.85
TYPE_MS = 16

BG = "#0d1117"
BAR = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
DIM = "#8b949e"
PROMPT = "#7ee787"
ACCENT = "#79c0ff"
AGENT = "#ffa657"
HEADING = "#d2a8ff"


@dataclass
class Line:
    """One transcript line: user turn, agent turn, tool call, or tool result."""

    runs: list[tuple[str, str]] = field(default_factory=list)
    kind: str = "out"  # "cmd", "agent", "user", "out"


def cmd(text: str) -> Line:
    """Return a tool invocation line, as a harness shows it."""
    return Line(runs=[("tool   ", ACCENT), (text, TEXT)], kind="cmd")


def agent(text: str) -> Line:
    """Return an agent thought or narration line."""
    return Line(runs=[("agent  ", AGENT), (text, TEXT)], kind="agent")


def user(text: str) -> Line:
    """Return the user's task line."""
    return Line(runs=[("user   ", PROMPT), (text, TEXT)], kind="user")


def out(*runs: tuple[str, str]) -> Line:
    """Return a tool result line."""
    return Line(runs=list(runs))


def dim(text: str) -> Line:
    """Return a dim tool-result line."""
    return Line(runs=[(text, DIM)])


SCRIPT: list[Line] = [
    user("Task: add a refresh-token flow to the auth module"),
    Line(),
    agent("First I check the project memory for rules on auth and sessions."),
    cmd("uv run mementic query keywords auth tokens session --compact"),
    out(("[mementic: 1 of 1 constraints, 2 of 3 decisions | auth, tokens, session]", ACCENT)),
    out(("### Constraints (MUST follow)", HEADING)),
    out(
        (
            "- Sessions must stay stateless (#sessions-stay-stateless, bound: src/api/session.py)",
            DIM,
        )
    ),
    out(("### Relevant Decisions", HEADING)),
    out(("- Tokens carry the session (#use-jwt-auth, affects: src/api/auth.py)", DIM)),
    Line(),
    agent(
        "There is a constraint: sessions must stay stateless. A refresh-token flow still fits\n"
        "if the refresh token rotates and nothing is stored server side.\n"
        "Before opening the module, the file gate:"
    ),
    cmd("uv run mementic query file src/api/auth.py --compact"),
    out(("[mementic: 0 constraints, 1 decisions | src/api/auth.py]", ACCENT)),
    out(("## Evolution of src/api/auth.py", DIM)),
    out(("- Tokens carry the session (#use-jwt-auth)", DIM)),
    Line(),
    agent(
        "Outcome: I will implement the refresh flow with rotating tokens\n"
        "and no server-side session state.\n"
        "[mementic: sessions-stay-stateless, use-jwt-auth]"
    ),
    Line(),
    agent("This decision is new. Recording it so the next session finds it:"),
    cmd(
        'uv run mementic add decision "Refresh tokens rotate on use" '
        '--rationale "Rotation limits replay windows for stolen tokens" '
        "--entity src/api/auth.py"
    ),
    out(("Added decision ", DIM), ("refresh-tokens-rotate-on-use", ACCENT)),
]


def line_text(line: Line) -> str:
    """Return the raw text of a line for width measurement."""
    return "".join(text for text, _ in line.runs)


def build_timeline(script: list[Line]) -> tuple[list[float], int, float]:
    """Return each line's start time, the total row count, and the duration."""
    times: list[float] = []
    clock = 0.5
    rows = 0
    for line in script:
        times.append(clock)
        count = _row_count(line)
        rows += count
        if line.kind == "cmd":
            clock += len(line_text(line)) * TYPE_MS / 1000 + 0.35
        elif count > 1:
            clock += 0.2 + (count - 1) * 0.18
        elif not line_text(line):
            clock += 0.18
        else:
            clock += 0.24
    return times, rows, clock + 0.6


def render_line(line: Line, index: int, start: float) -> list[str]:
    """Render one script line into one or more SVG text rows.

    A multi-line ``agent`` turn renders its first row with the ``agent``
    prefix and its continuation rows indented to align under the text, so a
    single thought reads as one block.
    """
    rows: list[str] = []
    family = (
        'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
        f'font-size="{FONT_SIZE}"'
    )

    if line.kind == "cmd":
        parts: list[str] = []
        clock = start
        for run_index, (text, color) in enumerate(line.runs):
            if run_index:
                clock += 0.1
            # Word-level typing: one tspan per word-plus-space keeps the
            # tspan count low enough that every renderer runs all delays.
            for word in re.findall(r"\S+\s*", text):
                delay = round(clock, 3)
                parts.append(
                    f'<tspan fill="{color}" style="animation-delay:{delay}s">{escape(word)}</tspan>'
                )
                clock += len(word) * TYPE_MS / 1000
        rows.append(
            f'<text x="{PAD_X}" y="{_y(index)}" xml:space="preserve" {family}>'
            f"{''.join(parts)}</text>"
        )
        return rows

    if not line.runs:
        return rows

    if line.kind == "agent":
        chunks = line.runs[-1][0].split("\n")
        for row_offset, chunk in enumerate(chunks):
            delay = round(start + row_offset * 0.18, 3)
            style = f"animation:reveal 0.28s ease-out {delay}s both"
            if row_offset == 0:
                prefix = f'<tspan fill="{AGENT}">agent  </tspan>'
            else:
                prefix = f'<tspan fill="transparent">{" " * len("agent  ")}</tspan>'
            rows.append(
                f'<g style="{style}">'
                f'<text x="{PAD_X}" y="{_y(index + row_offset)}" xml:space="preserve" {family}>'
                f'{prefix}<tspan fill="{TEXT}">{escape(chunk)}</tspan></text></g>'
            )
        return rows

    spans = "".join(f'<tspan fill="{color}">{escape(text)}</tspan>' for text, color in line.runs)
    delay = round(start, 3)
    rows.append(
        f'<g style="animation:reveal 0.28s ease-out {delay}s both">'
        f'<text x="{PAD_X}" y="{_y(index)}" xml:space="preserve" {family}>{spans}</text></g>'
    )
    return rows


def _y(index: int) -> int:
    """Return the baseline y coordinate for a rendered row index."""
    return PAD_TOP + TITLEBAR + index * LINE_HEIGHT


def _row_count(line: Line) -> int:
    """Return how many visual rows one script line occupies."""
    if not line.runs:
        return 1
    payload = line.runs[-1][0]
    return len(payload.split("\n"))


def build() -> str:
    """Return the SVG document as text."""
    times, rows, total = build_timeline(SCRIPT)
    longest = max(len(part) for line in SCRIPT for part in line_text(line).split("\n"))
    width = max(WIDTH_FLOOR, int(longest * CHAR_WIDTH) + PAD_X * 2 + 16)
    height = PAD_TOP + TITLEBAR + rows * LINE_HEIGHT + PAD_BOTTOM

    rendered: list[str] = []
    row_cursor = 0
    for index, line in enumerate(SCRIPT):
        parts = render_line(line, row_cursor, times[index])
        rendered.extend(part for part in parts if part)
        row_cursor += _row_count(line)
    body = "\n".join(rendered)

    last_text = next(
        (line for line in reversed(SCRIPT) if line_text(line)),
        Line(runs=[("", DIM)]),
    )
    last_chunks = line_text(last_text).split("\n")
    cursor_row = row_cursor - 1
    cursor_x = PAD_X + (len("agent  ") + len(last_chunks[-1])) * CHAR_WIDTH + 3
    cursor_y = _y(cursor_row) - 11
    cursor_delay = round(total - 0.4, 3)

    dots = "".join(
        f'<circle cx="{22 + index * 18}" cy="17" r="5" fill="{color}"/>'
        for index, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f"))
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Harness transcript: a user names a task, the agent queries mementic, cites the constraint it follows, and records a new decision">
<title>mementic agent demo</title>
<style>
  @keyframes reveal {{ from {{ opacity: 0 }} to {{ opacity: 1 }} }}
  @keyframes blink {{ 0%, 49% {{ opacity: 1 }} 50%, 100% {{ opacity: 0 }} }}
  @keyframes ap {{ from {{ opacity: 0 }} to {{ opacity: 1 }} }}
  text {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
  tspan {{ animation: ap 0.01s linear both; }}
  @media (prefers-reduced-motion: reduce) {{
    tspan {{ animation: none; }}
    g {{ animation: none; }}
  }}
</style>
<rect width="{width}" height="{height}" rx="10" fill="{BG}" stroke="{BORDER}"/>
<path d="M0 10a10 10 0 0 1 10-10h{width - 20}a10 10 0 0 1 10 10v{TITLEBAR - 10}H0z" fill="{BAR}"/>
{dots}
<text x="{width / 2}" y="22" text-anchor="middle" font-size="11" fill="{DIM}">mementic in an agent harness</text>
{body}
<rect x="{cursor_x:.1f}" y="{cursor_y}" width="7" height="14" fill="{PROMPT}" style="animation:blink 1s step-end {cursor_delay}s infinite"/>
</svg>
"""


def main() -> None:
    """Write the SVG and report the result."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    svg = build()
    OUT.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
