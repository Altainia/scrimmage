"""The ledger on ball-only fields steps as it did before a field could hold other kinds of piece.

tests/data/ledger_golden.json holds every piece's final qpos (rounded to 1e-9) and the counts of these runs, written
by the code before non-ball pieces were in the world:
    .venv/bin/python tests/test_range_ledger_golden.py
- "2026": test_range_discs.py's kitbot run behind range_ball_golden.npz (three spare balls: a lob at the blue hub, a
  ball rolling on the carpet and one dropped on the rim).
- "2026_shooter": the range fixtures' shooter robot on the 2026 range with its floor balls, two balls preloaded,
  driving forward with its intake, stages and launcher running, 3 s: launches, flight, scoring, the intake's pickups
  and the carpet's rolling resistance.
- "2013": test_range_disc_shooter.py's disc shooter on the 2013 range, four discs preloaded, shooting while it
  creeps forward, 3 s: disc launches, flight under the disc aerodynamics, and the discs landing.
- "intake": the intake example (examples/blocks/intake), its roller on, driving at the ball, 3 s.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from range_fixtures import drive, shooter_cfg  # noqa: E402
from scrimmage.world import World  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "data" / "ledger_golden.json"
INTAKE = ROOT / "examples" / "blocks" / "intake"


def _record(w) -> dict:
    """Every piece's qpos, rounded to 1e-9, and the range's counts."""
    b = w.test_range.balls
    qpos = [[round(float(x), 9) for x in w.data.qpos[b._q[i]]] for i in range(b.n)]
    return {"qpos": qpos, "counts": w.test_range.counts()}


def run_2026() -> dict:
    from test_range_discs import _ball_range_run
    return _record(_ball_range_run())


def run_2026_shooter() -> dict:
    w = World(shooter_cfg(range={"preload": 2, "floor_balls": True}, robot={"start_pose": [6.4, 2.6, 0.0]}), None)
    drive(w, 3.0, p0=0.4, p1=-0.4, p2=0.8, p3=0.0, p5=1.0, p6=1.0, p7=1.0)
    return _record(w)


def run_2013() -> dict:
    from test_range_disc_shooter import disc_cfg
    w = World(disc_cfg(), None)
    drive(w, 3.0, p0=0.2, p1=-0.2, p2=0.45, p5=1.0)
    return _record(w)


def run_intake() -> dict:
    from scrimmage.blocks.program import load
    from scrimmage.blocks.run import world_for
    w = world_for(load(INTAKE / "intake.test.toml"))
    w.reset()
    drive(w, 3.0, p0=0.33, p1=-0.33, p2=0.5)
    return _record(w)


RUNS = {"2026": run_2026, "2026_shooter": run_2026_shooter, "2013": run_2013, "intake": run_intake}


def test_the_ledger_steps_as_before():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    for name, run in RUNS.items():
        assert run() == golden[name], name


if __name__ == "__main__":
    GOLDEN.write_text(json.dumps({name: run() for name, run in RUNS.items()}, indent=1) + "\n", encoding="utf-8")
