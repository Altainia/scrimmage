"""K5 D2: a thin rigid bar and the stock climber (tests/data/thin_bar: the 2024 blue STAGE's 1/2 in CHAIN bars, the
spike's robot and script). The stock climber can't climb it, and the fix that lets it would change two 2022 climb
tests, so a bar under 1 in is warned about instead (ruling R16).

What happens (contact trace at 1 ms, the bar's center in the tip body's frame, x toward the tip, z up):
- The hook lands on the bar at 0.78 m/s (3.082 s). The load is on crown1 and crown2 alone. crown2's normal is
  (-0.38, -0.92) throughout; crown1's turns from (+0.41, -0.91) to (+1.00, 0.00) as the bar sinks past its end.
- The bar's center goes up into the crown, 4 mm toward the tip from its middle (x 0.0634-0.0641, crown center
  0.0595), not sideways and not out through the mouth. z rises from 0.4213 at first touch to 0.4339, past the seat
  (0.4227) at 0.6 mm per ms: 20 ms after touching, the tip still closes at 0.39 m/s.
- The penetration passes HOOK_RADIUS (5 mm) at 3.090 s and the bar's radius (6.35 mm) at 3.092 s. At 3.102 s it
  reaches 11.2 mm, the bar's radius plus the hook's (11.35 mm), so the bar's axis crosses crown2's. crown2's normal
  flips to (+0.38, +0.92), and its force drops from 1313 N to 0 in one step. That is the one jump, at the capsule's
  axis, not at a crown vertex. The bar leaves through the top of the hook (z 0.44 to 0.60), and the robot pitches
  about -3.6 deg and drops back to the carpet: "not hanging".
- On the same robot and script, a 1.66 in rigid bar at the same height sinks 11.3 mm too (crown0 to crown3, up to
  2.3 kN), short of its 26.1 mm. It holds, but the robot then bounces on its hooks for good: 15 mm, about 8 Hz, hanging
  a third of the time, and the run ends "not hanging" too. The 2022 MID rung sinks 7.9 mm in examples/climb/high.toml.
  With the 2022 rungs' 160 kN/m spring, both reach ONSTAGE: the 1/2 in bar hangs steadily, and the 1.66 in one
  hangs 99 % of the time.

The cause is the telescope, not the hook's shape. The extend motor's back-EMF damping (57 kN s/m for two Krakens at
1600 rad/m) is MuJoCo implicit dof damping on stages of 0.6 and 0.8 kg (b dt / m = 61 and 26 at 1 ms), with no
armature. The constraint solver sees that damping as an explicit force (bindings._coupled_dofs), so it counts on
braking that the step doesn't deliver, and the hook's contact pushes too little. When the robot lands, the sink
outgrows a thin bar. When it hangs, a rigid bar gives a self-excited bounce. The bounce goes on with the position
loop's voltage held fixed, stops when the damping is cut to a tenth after hanging, and stops with 2 kg or more of
armature on each stage.

The fix would be an armature of b dt on the telescope (the motors' rotors seen through the rope). It climbs this
bar and a rigid 1.66 in one, but it ends the bounce that tests/test_climb_run.py::
test_hooks_bouncing_while_let_down_is_no_failure needs (gaps >= 5 becomes 0), and it moves the climb's step times
off examples/climb/high_timed.toml (tests/test_climb_scenario.py::test_the_timed_climb_comes_from_the_climb_run).
Both tests fail at every armature tried, from 2 to 29 kg a stage, and 1 kg doesn't climb this bar. A backing
behind crown1 and crown2 doesn't do it either: it stops the bar going through, but the bounce stays. Stiffer hook
contacts (HOOK_SOLREF 0.005 or 0.002 s) change every 2022 hook contact, and 0.002 s still ends "not hanging" on the
chatter.
"""

from __future__ import annotations

from pathlib import Path

from scrimmage.testrange import check
from scrimmage.testrange.elements import THIN_BAR

THIN = Path(__file__).resolve().parent / "data" / "thin_bar"


def test_a_thin_bar_is_warned_about():
    rng = check.load(str(THIN / "range.toml"))
    names = ("chain_blue_center", "chain_blue_left", "chain_blue_right")
    assert list(rng.warnings) == [
        f"bar '{n}' is 0.5 in across: a stock climber's hook slips off a bar under 1 in (K5); hang a chain "
        f"(kind = \"chain\") or make the bar at least 1 in" for n in names]
    lines = check.summary("thin_bar", rng)
    assert len(lines) == 4 and all(n in line for n, line in zip(names, lines[1:]))   # `range check` shows them
    hangar = check.load("2022-hangar")
    assert hangar.warnings == ()
    assert min(e.diameter for e in hangar.elements if hasattr(e, "diameter")) >= THIN_BAR
