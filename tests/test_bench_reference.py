"""The bench against the launcher model (spec checks 1, 3 and 6): examples/bench/hooded.toml is the launcher's
examples/launcher/hooded.toml rebuilt from plain shapes.

Like for like: one ball, no spread. Both spin the shaft up from rest and feed when it is within the same tolerance of
its target (the bench's SPIN_UP_TOLERANCE, given to the launcher's wait_for_speed feed). The launcher model's exit
speed is read at the end of the hood's release; the bench's as the ball rises into the outlet, just after it has left
the hood and the wheel (the outlet's comment in the file).
"""

import math
import os
import time
import tomllib
from pathlib import Path

import pytest

from scrimmage.bench import build
from scrimmage.bench.run import SPIN_UP_TOLERANCE, run_bench
from scrimmage.bench.spec import load_bench, parse_bench, with_overrides
from scrimmage.launcher.inputs import load_setup
from scrimmage.launcher.run import run_setup

ROOT = Path(__file__).resolve().parent.parent
HOODED = ROOT / "examples" / "bench" / "hooded.toml"
LAUNCHER = ROOT / "examples" / "launcher" / "hooded.toml"

D = 0.1778  # the Power Cell
R_WHEEL = 0.0508
FLARE_DEG, ENTRY_DEG, LEAD_DEG = 30.0, 240.0, 5.0
GRIP_RPS, SLIP_RPS = 33.3, 66.7  # 2000 and 4000 rpm
SQUISH = (0.15, 0.21, 0.28)  # of the diameter
NO_SPREAD = dict(diameter=0.0, mass=0.0, stiffness=0.0, feed_time=0.0, lane=0.0, entry_speed=0.0, yaw_deg=0.0)


def gap_for(squish: float) -> float:
    return round(D * (1.0 - squish), 4)


def hood(gap: float) -> dict:
    """The launcher model's hood at `gap`, as overrides of hooded.toml: a straight flare at FLARE_DEG from no squeeze
    at ENTRY_DEG (the squeeze rising at tan(flare) per meter along the ball's path, radius R + gap / 2) down to the gap,
    started LEAD_DEG early, then the arc at the gap."""
    squeeze = D - gap
    span = math.degrees(squeeze / math.tan(math.radians(FLARE_DEG)) / (R_WHEEL + gap / 2))
    end = ENTRY_DEG - span
    return {"curve.flare.radius_start": R_WHEEL + D + LEAD_DEG * squeeze / span,
            "curve.flare.radius_end": R_WHEEL + gap, "curve.flare.end_deg": end,
            "curve.hood.radius": R_WHEEL + gap, "curve.hood.start_deg": end}


def bench_shot(gap: float, rps: float):
    """One ball through hooded.toml with the hood at `gap` and the shaft at `rps`."""
    raw = with_overrides(tomllib.loads(HOODED.read_text()),
                         hood(gap) | {"shaft.shooter.target_rps": rps, "inlet.feed.count": 1})
    result = run_bench(parse_bench(raw, HOODED.parent))
    (piece,) = result.pieces
    assert piece.status == "out", piece.status
    return piece


def reference_shot(gap: float, rps: float):
    """The launcher model's exit for the same ball: one ball, no spread, fed at the bench's spin-up tolerance."""
    raw = tomllib.loads(LAUNCHER.read_text())
    raw["hood"]["gap"] = gap
    raw["control"]["target_rps"] = rps
    raw["feed"].update(count=1, tolerance=SPIN_UP_TOLERANCE)
    raw["spread"] = dict(NO_SPREAD)
    shot = run_setup(load_setup(raw)[0]).shots[0][0]
    assert shot.status == "launched", shot.status
    return shot


def compare(squish: float, rps: float) -> tuple[float, float, float, float]:
    """(bench speed, reference speed, bench launch angle, reference launch angle)."""
    gap = gap_for(squish)
    b, r = bench_shot(gap, rps), reference_shot(gap, rps)
    print(f"\nsquish {squish:.2f} D (gap {gap * 1000:.1f} mm) at {rps} rps: bench {b.speed:.3f} m/s "
          f"{b.elevation_deg:.1f} deg, launcher model {r.speed:.3f} m/s {r.launch_deg:.1f} deg, "
          f"difference {100 * (b.speed / r.speed - 1):+.2f}%")
    return b.speed, r.speed, b.elevation_deg, r.launch_deg


def test_hood_overrides_match_the_file():
    raw = tomllib.loads(HOODED.read_text())
    curves = {c["name"]: c for c in raw["curve"]}
    for dotted, value in hood(0.1397).items():  # the file's own gap, 5.5 in.
        _, name, key = dotted.split(".")
        assert curves[name][key] == pytest.approx(value, abs=1e-5), dotted
    assert curves["flare"]["start_deg"] == ENTRY_DEG + LEAD_DEG


@pytest.mark.parametrize("squish", SQUISH)
def test_reference_grip_regime(squish):
    """Check 1: at 2000 rpm the bench's exit speed is within 3% of the launcher model's at every squeeze. The launch
    angle is printed, not asserted: the launcher model's is the hood's exit angle by construction."""
    speed, ref, _, _ = compare(squish, GRIP_RPS)
    assert speed == pytest.approx(ref, rel=0.03)


def test_faceting_invariance(monkeypatch):
    """Check 3: cutting the curves into 2x and 4x as many pieces changes the exit speed by under 2%."""
    speeds = []
    for deg in (5.0, 2.5, 1.25):
        monkeypatch.setattr(build, "CURVE_MAX_DEG", deg)
        speeds.append(bench_shot(0.1397, SLIP_RPS).speed)
    print(f"\nexit speed at 5, 2.5 and 1.25 degree chords: {', '.join(f'{s:.3f}' for s in speeds)} m/s")
    for s in speeds[1:]:
        assert s == pytest.approx(speeds[0], rel=0.02)


@pytest.mark.skipif(os.environ.get("SCRIMMAGE_TIMING") != "1", reason="timed locally only: set SCRIMMAGE_TIMING=1")
def test_turnaround(tmp_path):
    """Check 6: hooded.toml as written (ten balls at 5 per second, about 3 s simulated, balls able to touch) inside
    about a minute, results and pictures included, as `bench run` makes them; the run alone inside half of it."""
    from scrimmage.bench.pictures import PICTURE_TRACE_HZ, write_pictures
    from scrimmage.bench.report import write_results

    t0 = time.perf_counter()
    result = run_bench(load_bench(HOODED), trace_hz=PICTURE_TRACE_HZ)
    run_wall = time.perf_counter() - t0
    write_pictures(result, write_results(result, tmp_path))
    wall = time.perf_counter() - t0
    print(f"\nhooded.toml, {len(result.pieces)} balls, {result.t_end:.2f} s simulated: run {run_wall:.1f} s, "
          f"with results and pictures {wall:.1f} s")
    assert len(result.pieces) == 10
    assert all(p.status == "out" for p in result.pieces)
    assert run_wall < 30.0
    assert wall < 60.0
