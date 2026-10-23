"""Generate the animated terminal SVG used in the README.

The transcript lives in ``docs/demo.txt`` and is captured from a real run at 96
columns. The only edit is the working directory, shown as ``~/dev/shop`` instead
of a temporary path. Regenerate after CLI output changes:

    uv run python scripts/make_demo_svg.py

The result is `docs/demo.svg`, a self contained SVG whose CSS animations type the
commands and fade in the output. GitHub renders it inline like a GIF, with no
video or extra tooling involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

DOCS = Path(__file__).resolve().parents[1] / "docs"
OUT = DOCS / "demo.svg"
TRANSCRIPT = DOCS / "demo.txt"

WIDTH_FLOOR = 760
TITLEBAR = 34
PAD_X = 22
PAD_TOP = 24
PAD_BOTTOM = 26
LINE_HEIGHT = 21
FONT_SIZE = 13
CHAR_WIDTH = 7.85
TYPE_MS = 22

BG = "#0d1117"
BAR = "#161b22"
BORDER = "#30363d"
PANEL = "#484f58"
TEXT = "#e6edf3"
DIM = "#8b949e"
PROMPT = "#7ee787"
ACCENT = "#79c0ff"
HEADING = "#d2a8ff"


@dataclass
class Line:
    """One terminal line, either a typed command or printed output."""

    runs: list[tuple[str, str]] = field(default_factory=list)
    typed: bool = False


def command(text: str) -> Line:
    """Return a typed line with a shell prompt."""
    return Line(runs=[("$ ", PROMPT), (text, TEXT)], typed=True)


def rich(text: str) -> Line:
    """Return a captured output line with colors chosen from its shape."""
    stripped = text.lstrip()

    if text.startswith(("┌", "└")) or stripped.startswith("│ ─"):
        color = PANEL
    elif "Mementic initialized" in text or "Context pack:" in text:
        color = HEADING
    elif stripped.startswith("│") and (
        "Constraints (MUST follow)" in text or "Relevant Decisions" in text
    ):
        color = TEXT
    elif text.startswith("[mementic:"):
        color = ACCENT
    else:
        color = DIM

    return Line(runs=[(text, color)])


def load_script() -> list[Line]:
    """Read the captured transcript into renderable lines."""
    script: list[Line] = []
    for raw in TRANSCRIPT.read_text(encoding="utf-8").splitlines():
        if raw.startswith("$ "):
            script.append(command(raw[2:]))
        elif raw.strip():
            script.append(rich(raw))
        else:
            script.append(Line())
    return script


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
        if line.typed:
            clock += length * TYPE_MS / 1000 + 0.35
        elif length == 0:
            clock += 0.18
        else:
            clock += 0.22
    return times, clock + 0.6


def render_line(line: Line, index: int, start: float) -> str:
    """Render one line with its reveal animation."""
    y = PAD_TOP + TITLEBAR + index * LINE_HEIGHT

    if line.typed:
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
    spans = "".join(f'<tspan fill="{color}">{escape(text)}</tspan>' for text, color in line.runs)
    return (
        f'<g style="animation:reveal 0.28s ease-out {delay}s both">'
        f'<text x="{PAD_X}" y="{y}" xml:space="preserve" '
        f'font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" '
        f'font-size="{FONT_SIZE}">{spans}</text></g>'
    )


def build() -> str:
    """Return the SVG document as text."""
    script = load_script()
    times, total = build_timeline(script)
    longest = max(len(line_text(line)) for line in script)
    width = max(WIDTH_FLOOR, int(longest * CHAR_WIDTH) + PAD_X * 2 + 16)
    height = PAD_TOP + TITLEBAR + len(script) * LINE_HEIGHT + PAD_BOTTOM

    rendered = [render_line(line, index, times[index]) for index, line in enumerate(script)]
    body = "\n".join(part for part in rendered if part)

    last_text = next(
        (line for line in reversed(script) if line_text(line)),
        Line(runs=[("", DIM)]),
    )
    cursor_x = PAD_X + len(line_text(last_text)) * CHAR_WIDTH + 3
    cursor_y = PAD_TOP + TITLEBAR + (len(script) - 1) * LINE_HEIGHT - 11
    cursor_delay = round(total - 0.4, 3)

    dots = "".join(
        f'<circle cx="{22 + index * 18}" cy="17" r="5" fill="{color}"/>'
        for index, color in enumerate(("#ff5f56", "#ffbd2e", "#27c93f"))
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="Terminal recording of mementic init, add and query">
<title>mementic demo</title>
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
<text x="{width / 2}" y="22" text-anchor="middle" font-size="11" fill="{DIM}">mementic</text>
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
