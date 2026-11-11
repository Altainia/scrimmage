"""K5 D2: the stock climber climbs a thin rigid bar (tests/data/thin_bar: the 2024 blue STAGE's 1/2 in CHAIN bars, the
spike's robot and script), and a rigid 1.66 in one, and hangs from it without bouncing.

Before the fix (the trace at 1 ms, the bar's center in the tip body's frame, x toward the tip, z up):
- The hook landed on the bar at 0.78 m/s (3.082 s), on crown1 and crown2 alone. crown2's normal stayed
  (-0.38, -0.92); crown1's turned from (+0.41, -0.91) to (+1.00, 0.00).
- The bar's center went up into the crown, 4 mm toward the tip from its middle (x 0.0634-0.0641; crown center
  0.0595), not sideways and not out through the mouth. z rose from 0.4213 to 0.4339, past the seat (0.4227), at
  0.6 mm per ms: 20 ms after touching, the tip still closed at 0.39 m/s.
- The penetration passed HOOK_RADIUS (5 mm) at 3.090 s and the bar's radius (6.35 mm) at 3.092 s. At 3.102 s it reached
  11.2 mm, the bar's radius plus the hook's (11.35 mm). There the bar's axis crossed crown2's, crown2's normal flipped
  and its force fell from 1313 N to 0 in one step: the one jump, at the capsule's axis, not at a crown vertex. The bar
  left through the top of the hook, and the robot dropped back to the carpet: "not hanging".
- A rigid 1.66 in bar sank 11.3 mm too, short of its 26.1 mm, and held. But the robot bounced on its hooks for good
  (15 mm, about 8 Hz, hanging a third of the time) and ended "not hanging" as well. The 2022 rungs' 160 kN/m spring
  hid that.

The cause was the telescope, not the hook's shape. The extend motor's back-EMF damping (57 kN s/m for two Krakens at
1600 rad/m) was implicit dof damping on stages of 0.6 and 0.8 kg (b dt / m = 61 and 26 at 1 ms), with no armature.
MuJoCo's constraint solver sees that damping as an explicit force (bindings._coupled_dofs), so the hook's contact
pushed too little. The bounce went on with the position loop's voltage held fixed, and stopped with the damping cut
to a tenth or with 2 kg of armature a stage.

The fix: the telescope carries its motors' rotor inertia, count x ROTOR_MOI x (motor rad per m)^2 (climber.py,
telescope_armature): 512 kg on the stock climber, 256 kg a stage. b dt / M is then 0.11.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scrimmage.climb.run import ClimbRun
from scrimmage.climb.script import load_script
from scrimmage.config import build_world, load_config

THIN = Path(__file__).resolve().parent / "data" / "thin_bar"
HANG_S = 3.0              # s: the last part of the hang that must have no gap


def climb(folder: Path):
    """The fixture's climb on `folder`'s robot and range; the result and how many times the robot lost every bar for
    a moment (climb.hanging 1 to 0) over the last HANG_S."""
    w = build_world(load_config(folder / "robot.toml"))
    run = ClimbRun(w, load_script(THIN / "climb.toml"))
    gaps, prev = [], [False]

    def watch(t, v):
        hanging = bool(v["climb.hanging"])
        if prev[0] and not hanging:
            gaps.append(t)
        prev[0] = hanging
    run.on_step = watch
    r = run.run()
    return r, len([t for t in gaps if t > r.duration - HANG_S])


@pytest.mark.parametrize("diameter_in", [0.5, 1.66])
def test_the_stock_climber_climbs_a_rigid_bar(diameter_in, tmp_path):
    """The fixture's 1/2 in bar, and the same bars at 1.66 in (the 2022 rungs' OD, still rigid): both climbed, and
    the robot hangs with no gap at the end."""
    folder = THIN
    if diameter_in != 0.5:
        folder = tmp_path
        for f in ("robot.toml", "range.toml"):
            (tmp_path / f).write_text((THIN / f).read_text())
        rng = (THIN / "range.toml").read_text()
        assert rng.count("diameter_in = 0.5 ") == 3
        (tmp_path / "range.toml").write_text(rng.replace("diameter_in = 0.5 ", f"diameter_in = {diameter_in} "))
    r, gaps = climb(folder)
    assert r.status == "reached ONSTAGE", [(row.t, row.event) for row in r.rows if row.event]
    assert r.reached_goal
    assert r.peak_hook_load < 4000.0                # research notes 4.4: a sane hook peak, as test_climb_run's
    assert not any("walked off" in row.event or "fell" in row.event for row in r.rows)
    assert gaps == 0
