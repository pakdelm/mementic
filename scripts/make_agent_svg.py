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
    agent("There is a constraint: sessions must stay stateless. A refresh-token flow still fits"),
    agent("if the refresh token rotates and nothing is stored server side."),
    agent("Before opening the module, the file gate:"),
    cmd("uv run mementic query file src/api/auth.py --compact"),
    out(("[mementic: 0 constraints, 1 decisions | src/api/auth.py]", ACCENT)),
    out(("## Evolution of src/api/auth.py", DIM)),
    out(("- Tokens carry the session (#use-jwt-auth)", DIM)),
    Line(),
    agent("Outcome: I will implement the refresh flow with rotating tokens"),
    agent("and no server-side session state."),
    agent("[mementic: sessions-stay-stateless, use-jwt-auth]"),
    Line(),
    agent("This decision is new. Recording it so the next session finds it:"),
    cmd(
        'uv run mementic add decision "Refresh tokens rotate on use" '
        '--rationale "Rotation limits replay windows for stolen tokens" '
        "--entity src/api/auth.py"
    ),
    out(("Added decision ", DIM), ("refresh-tokens-rotate-on-use", ACCENT)),
    Line(),
    agent("Graph updated. I will query again before the next module."),
]


def line_text(line: Line) -> str:
    """Return the raw text of a line for width measurement."""
    return "".join(text for text, _ in line.runs)


def build_timeline(script: list[Line]) -> tuple[list[float], float]:
    """Return each line's start time in seconds, plus the total duration."""
    times: list[float] = []
    clock = 0.5
    for line in script:
        times.append(clock)
        length = len(line_text(line))
        if line.kind == "cmd":
            clock += length * TYPE_MS / 1000 + 0.35
        elif length == 0:
            clock += 0.18
        else:
            clock += 0.24
    return times, clock + 0.6


def render_line(line: Line, index: int, start: float) -> str:
    """Render one line with its reveal animation."""
    y = PAD_TOP + TITLEBAR + index * LINE_HEIGHT

    if line.kind == "cmd":
        parts: list[str] = []
        clock = start
        for run_index, (text, color) in enumerate(line.runs):
            if run_index:
                clock += 0.1
            for char in text:
                delay = round(clock, 3)
                parts.append(
                    f'<tspan fill="{color}" style="animation-delay:{delay}s">{escape(char)}</tspan>'
                )
                clock += TYPE_MS / 1000
        return (
            f'<text x="{PAD_X}" y="{y}" xml:space="preserve" '
            f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
            f'font-size="{FONT_SIZE}">{"".join(parts)}</text>'
        )

    if not line.runs:
        return ""

    delay = round(start, 3)
    style = f"animation:reveal 0.28s ease-out {delay}s both"
    spans = "".join(f'<tspan fill="{color}">{escape(text)}</tspan>' for text, color in line.runs)
    return (
        f'<g style="{style}">'
        f'<text x="{PAD_X}" y="{y}" xml:space="preserve" '
        f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
        f'font-size="{FONT_SIZE}">{spans}</text></g>'
    )


def build() -> str:
    """Return the SVG document as text."""
    times, total = build_timeline(SCRIPT)
    longest = max(len(line_text(line)) for line in SCRIPT)
    width = max(WIDTH_FLOOR, int(longest * CHAR_WIDTH) + PAD_X * 2 + 16)
    height = PAD_TOP + TITLEBAR + len(SCRIPT) * LINE_HEIGHT + PAD_BOTTOM

    rendered = [render_line(line, index, times[index]) for index, line in enumerate(SCRIPT)]
    body = "\n".join(part for part in rendered if part)

    last_text = next(
        (line for line in reversed(SCRIPT) if line_text(line)),
        Line(runs=[("", DIM)]),
    )
    cursor_x = PAD_X + len(line_text(last_text)) * CHAR_WIDTH + 3
    cursor_y = PAD_TOP + TITLEBAR + (len(SCRIPT) - 1) * LINE_HEIGHT - 11
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
