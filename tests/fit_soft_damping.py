"""Fit the soft ring's damping scale (testrange/soft.py DAMPING_SCALES) offline: at each stiffness, the scale that
makes soft_helpers.drop of the 2024-note at restitution FIT_E come back at FIT_E. The world never runs this: it reads
the shipped table. Refit after a change to the ring or its contact, and paste the printed rows into DAMPING_SCALES:

    PYTHONPATH=<repo> .venv/bin/python tests/fit_soft_damping.py

tests/test_range_soft.py::test_the_shipped_damping_scales_are_the_fit reruns it (slow, opt-in).
"""

from __future__ import annotations

import math
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soft_helpers  # noqa: E402
from scrimmage.testrange import soft  # noqa: E402
from scrimmage.testrange.pieces import kind_preset  # noqa: E402

STIFFNESSES = (1000.0, 2000.0, 6000.0, 20000.0)   # N/m: the soft range's ends, the preset's, and the spike's firm foam
FIT_E = 0.3             # the preset's restitution, the spike's fit's
BRACKET = (0.3, 2.0)    # the scale's search range: e falls as the scale rises
STEPS = 14              # log bisection steps: the bracket's ratio 6.7 to within 0.012 %


def drop_e(stiffness: float, scale: float, e: float = FIT_E) -> float:
    """The drop's restitution with the damping scale held at `scale` (DAMPING_SCALES replaced for the call)."""
    saved = soft.DAMPING_SCALES
    soft.DAMPING_SCALES = ((1.0, scale),)
    try:
        return soft_helpers.drop(replace(kind_preset("2024-note"), stiffness=stiffness, restitution=e))["e"]
    finally:
        soft.DAMPING_SCALES = saved


def fit(stiffness: float) -> float:
    lo, hi = BRACKET
    for _ in range(STEPS):
        mid = math.sqrt(lo * hi)
        lo, hi = (mid, hi) if drop_e(stiffness, mid) > FIT_E else (lo, mid)
    return math.sqrt(lo * hi)


def main() -> None:
    for s in STIFFNESSES:
        print(f"({s:g}, {fit(s):.4f}),", flush=True)


if __name__ == "__main__":
    main()
