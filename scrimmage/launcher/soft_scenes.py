"""The two scenes a soft NOTE is measured by, as numbers only: a squeeze between two rollers and a drop on the carpet.

The world's soft ring (testrange/soft.py) and the bench's flex NOTE (bench/flex_note.py) are each checked against
these same scenes, so their numbers live here once (docs/measuring.md describes the real tests they stand for).

- The squeeze: the NOTE lies flat with no gravity, between two rollers on a line across its middle (each crosses
  the rim twice). They close from SQUEEZE_START_GAP to SQUEEZE_GAP apart over SQUEEZE_CLOSE_S and hold for
  SQUEEZE_HOLD_S. The top roller's normal force, averaged over the hold's last SQUEEZE_READ_S, is the reading.
- The drop: the NOTE flat, its bottom DROP_HEIGHT above the carpet. Its restitution is sqrt(rebound apex above its
  rest height / DROP_HEIGHT).
"""

from __future__ import annotations

IN = 0.0254

SQUEEZE_START_GAP = 2.3 * IN     # source: soft-note spike (scenes.squeeze): just clear of the 2 in NOTE
SQUEEZE_GAP = 1.5 * IN           # source: soft-note spike: a 25 % squeeze of the 2 in NOTE
SQUEEZE_CLOSE_S = 0.6            # source: soft-note spike: slow enough that the damping adds little
SQUEEZE_HOLD_S = 0.4             # source: soft-note spike
SQUEEZE_READ_S = 0.15            # source: soft-note spike: the force averaged over the hold's last 0.15 s
ROLLER_DIAMETER = 4.0 * IN       # source: soft-note spike: 4 in shooter wheels
ROLLER_WIDTH = 18.0 * IN         # source: soft-note spike: wider than the 14 in NOTE
ROLLER_FRICTION = (0.7, 0.005, 0.0001)  # source: soft-note spike: [wheels.60A]'s tread (presets.toml estimate)

DROP_HEIGHT = 1.0                # source: soft-note spike (scenes.drop): 1 m, the NOTE's bottom above the carpet
DROP_TIME_S = 1.2                # source: soft-note spike (calibrate.fit_damping): long enough to rebound and settle
CARPET_FRICTION = (1.0, 0.01, 0.001)    # source: parts/field.py's carpet
