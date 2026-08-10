"""Welcome screen renderables for the vague-code TUI.

A compact XCLAW block-letter wordmark with a small particle twinkle,
degrading to a one-line wordmark on small terminals.
"""

from __future__ import annotations

from rich.align import Align
from rich.text import Text

WELCOME_LOGO_PALETTE = {
    "X": "#81e8bb",
    "C": "#18cfcb",
    "L": "#1ba59e",
    "A": "#45e6df",
    "W": "#b8ffdf",
    "P": "#f5fcfa",
}

WELCOME_LOGO_PIXELS = (
    "█ █ ███ █    █  █   █",
    " █  █   █    █ █ █   █",
    " █  █   █    ███ █ █ █",
    " █  █   █    █ █ ██ ██",
    "█ █ ███ ███  █ █ █   █",
)

WELCOME_PARTICLE_FRAMES = (
    ((0, 16, "P"), (2, 18, "P"), (4, 2, "P")),
    ((1, 14, "P"), (3, 20, "P"), (0, 4, "P")),
    ((2, 16, "P"), (0, 20, "P"), (4, 10, "P")),
    ((3, 6, "P"), (1, 2, "P"), (2, 12, "P")),
)

WELCOME_TAGLINE = "local coding agent"


def welcome_renderable(*, compact: bool = False, particle_frame: int = 0) -> Align:
    """Render the animated wordmark, or a small-screen version when space is tight."""
    if compact:
        return Align.center(
            Text.assemble(
                ("vaguecode", "#81e8bb bold"),
                (f"\n{WELCOME_TAGLINE}", "#6e6d72"),
            )
        )
    rows = [list(row) for row in WELCOME_LOGO_PIXELS]
    frame = WELCOME_PARTICLE_FRAMES[particle_frame % len(WELCOME_PARTICLE_FRAMES)]
    for row_index, column_index, pixel in frame:
        if not 0 <= row_index < len(rows):
            continue
        row = rows[row_index]
        if column_index >= len(row):
            row.extend(" " for _ in range(column_index - len(row) + 1))
        if row[column_index] == " ":
            row[column_index] = pixel

    text = Text()
    for row_index, row in enumerate(rows):
        if row_index:
            text.append("\n")
        for pixel in row:
            color = WELCOME_LOGO_PALETTE.get(pixel)
            text.append("██" if color else "  ", style=color)
    text.append(f"\n{WELCOME_TAGLINE}", style="#6e6d72")
    return Align.center(text)
