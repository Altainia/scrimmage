"""Named colors: what a field's `color = "red"` means, and the name a color sensor's reading is matched to.

A color is (r, g, b), each 0 to 1. A field gives a piece kind its color as one of NAMED_COLORS' names or as an RGB
list (`color = [0.9, 0.1, 0.1]`); `nearest_color` names any color by the nearest of the eight (straight-line distance
in RGB), so a student can write *sees red* for a piece drawn a slightly different red.
"""

from __future__ import annotations

import math

from .. import i18n

# Our choice of eight plain colors, spread out so an FRC game piece's color lands nearest the name a student would
# use: the 2020 and 2026 yellow balls, the 2019 orange cargo, 2022's red and blue cargo, the white and off-white
# discs and rigid pieces (build.py's drawn colors), and 2016's gray boulder.
NAMED_COLORS: dict[str, tuple[float, float, float]] = {
    "red": (0.9, 0.1, 0.1),
    "blue": (0.1, 0.25, 0.9),
    "yellow": (0.95, 0.85, 0.1),
    "orange": (1.0, 0.5, 0.0),
    "green": (0.1, 0.7, 0.2),
    "purple": (0.55, 0.2, 0.75),
    "white": (0.95, 0.95, 0.95),
    "gray": (0.5, 0.5, 0.5),
}


def nearest_color(rgb) -> str:
    """The name of the named color nearest `rgb` (r, g, b, each 0 to 1)."""
    r, g, b = (float(c) for c in rgb)
    return min(NAMED_COLORS, key=lambda n: math.dist((r, g, b), NAMED_COLORS[n]))


def parse_color(value, where: str) -> tuple[float, float, float]:
    """A config's `color`: a name from NAMED_COLORS or [r, g, b] (each 0 to 1). Raises ValueError naming `where`
    ("[piece.red_cargo]") and the key."""
    if isinstance(value, str):
        if value in NAMED_COLORS:
            return NAMED_COLORS[value]
        raise ValueError(i18n.t("{where}: color {value} isn't one of the named colors ({named_colors}); give one "
                                "of those or [r, g, b], each 0 to 1",
                                where=where, value=repr(value), named_colors=', '.join(NAMED_COLORS)))
    if (isinstance(value, list) and len(value) == 3
            and all(isinstance(c, (int, float)) and not isinstance(c, bool) and 0.0 <= c <= 1.0 for c in value)):
        return tuple(float(c) for c in value)
    raise ValueError(i18n.t("{where}: color must be a named color ({named_colors}) or [r, g, b], each 0 to 1, not "
                            "{value}", where=where, named_colors=', '.join(NAMED_COLORS), value=repr(value)))
