"""A bench plate can move its surface (K6-R14): `[[plate]] surface_speed` sets MuJoCo's geom `surfacevel` along the
plate's own x (K6-P10), so a NOTE dropped on it is carried as by a moving carpet. Checked through the bench's own
runner on the rigid NOTE and the flex NOTE, forward, backward and on a turned plate."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from scrimmage.bench.build import build_bench
from scrimmage.bench.run import run_bench
from scrimmage.bench.spec import BenchError, parse_bench

HERE = Path(__file__).resolve().parent
SPEED = 1.5           # source: K6-R14's example, `surface_speed = 1.5`
SPEED_TOL = 0.1       # source: the task's tolerance on the rigid NOTE, m/s
FLEX_SPEED_TOL = 0.15  # source: the task's tolerance on the flex NOTE, m/s
ANGLE_TOL_DEG = 3.0   # source: the task's tolerance on a turned plate's direction
WINDOW = 0.1          # choice: s, the speed is read over the last tenth of a second before its deadline
TRACE_HZ = 100.0      # choice: path samples every 10 ms, so the window holds ten


def _raw(speed: float = SPEED, rot_deg=(0.0, 0.0, 0.0), flex: bool = False, duration: float = 1.0) -> dict:
    """One long plate whose top is at z = 0, and the 2024 NOTE dropped from rest on its middle."""
    turned = any(rot_deg)
    piece = {"preset": "2024-note", **({"flex": True} if flex else {})}
    return {
        "bench": {"name": "moving carpet"},
        "piece": piece,
        # choice: 5 m long, so a NOTE carried at 1.5 m/s for 1.5 s stays on it
        "plate": [{"name": "carpet", "center": [0.0, 0.0, -0.05], "size": [5.0, 1.0, 0.1], "rot_deg": list(rot_deg),
                   "surface_speed": speed}],
        # choice: the NOTE is 2 in thick, so its middle 1 in up rests on the plate; a turned plate's edge rises
        # 0.18 m * sin 10 deg = 0.03 m under the NOTE's rim, so it starts higher there
        "inlet": [{"name": "drop", "center": [0.0, 0.0, 0.08 if turned else 0.03], "size": [0.001] * 3,
                   "count": 1}],
        "run": {"duration": duration},
    }


def _velocity(raw: dict, t_end: float) -> np.ndarray:
    """The NOTE's mean velocity over the WINDOW before t_end, from its sampled path."""
    result = run_bench(parse_bench(raw, HERE), trace_hz=TRACE_HZ)
    (piece,) = result.pieces
    path = np.array(piece.path)
    assert path[-1, 0] >= t_end - 1e-6, f"the NOTE left the run at {path[-1, 0]:.2f} s ({piece.status})"
    a = path[np.argmin(np.abs(path[:, 0] - (t_end - WINDOW)))]
    b = path[np.argmin(np.abs(path[:, 0] - t_end))]
    return (b[1:] - a[1:]) / (b[0] - a[0])


def test_a_moving_plate_carries_a_rigid_note():
    v = _velocity(_raw(), 1.0)
    assert v[0] == pytest.approx(SPEED, abs=SPEED_TOL)


def test_a_negative_surface_speed_carries_back():
    v = _velocity(_raw(speed=-SPEED), 1.0)
    assert v[0] == pytest.approx(-SPEED, abs=SPEED_TOL)


def test_a_turned_plate_carries_along_its_face():
    """rot_deg = [0, -10, 0] lifts the plate's +x end: the NOTE is carried up the ramp along the plate's own x."""
    v = _velocity(_raw(rot_deg=(0.0, -10.0, 0.0)), 1.0)
    along = np.array([math.cos(math.radians(10.0)), 0.0, math.sin(math.radians(10.0))])
    angle = math.degrees(math.acos(float(np.clip(v @ along / np.linalg.norm(v), -1.0, 1.0))))
    assert angle <= ANGLE_TOL_DEG
    assert float(np.linalg.norm(v)) == pytest.approx(SPEED, abs=SPEED_TOL)


def test_surface_speed_with_a_flex_note():
    """MuJoCo's flex contacts take the plate's surfacevel as its geom contacts do, so the flex NOTE is carried too
    and `surface_speed` with `flex = true` is not refused."""
    v = _velocity(_raw(flex=True, duration=1.5), 1.5)
    assert v[0] == pytest.approx(SPEED, abs=FLEX_SPEED_TOL)


def test_surface_speed_sets_the_plate_geoms_surfacevel():
    """Along the plate's own frame (MuJoCo's geom frame), and only on a moving plate: a still one leaves the model's
    flag off, so every bench without the key builds as before."""
    m = build_bench(parse_bench(_raw(speed=-0.75, rot_deg=(0.0, -10.0, 0.0)), HERE)).model
    assert list(m.geom_surfacevel[m.geom("plate:carpet").id]) == [-0.75, 0.0, 0.0, 0.0, 0.0, 0.0]
    assert m.flg_surfacevel
    still = build_bench(parse_bench(_raw(speed=0.0), HERE)).model
    assert not still.flg_surfacevel and not still.geom_surfacevel.any()


def test_surface_speed_is_a_number():
    raw = _raw()
    raw["plate"][0]["surface_speed"] = "fast"
    with pytest.raises(BenchError, match=r"\[\[plate\]\] 'carpet' surface_speed must be a number, not 'fast'"):
        parse_bench(raw, HERE)
    raw["plate"][0].pop("surface_speed")
    assert parse_bench(raw, HERE).plates[0].surface_speed == 0.0
