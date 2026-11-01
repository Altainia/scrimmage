r"""The launcher model's changes for running in the world: an external bus voltage, the hood angle at run time,
the shaft's angle, world-sized steps, and the balls' positions. The standalone runs must not change.

The golden files in tests/data/launcher_golden/ were written by the launcher code before these changes. To
rewrite them (only when a change to the standalone results is intended), run from the repo root:

    .venv/bin/python -c "import ast, pathlib; exec(ast.get_docstring(ast.parse(pathlib.Path(
        'tests/test_launcher_inworld.py').read_text())).split('# --- golden script ---')[-1])"

# --- golden script ---
import json, shutil, subprocess, sys, tempfile, tomllib
from pathlib import Path
from scrimmage.launcher.inputs import apply_overrides, parse_setup
from scrimmage.launcher.run import run_setup
from scrimmage.launcher.sweep import combinations, load_setup
golden = Path("tests/data/launcher_golden")
golden.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as out:
    subprocess.run([sys.executable, "-m", "scrimmage", "launcher", "run", "examples/launcher/hooded.toml",
                    "--out", out], check=True, capture_output=True)
    shutil.copyfile(Path(out) / "results.csv", golden / "hooded.csv")
path = Path("examples/launcher/hood_angle.toml")
_, sweep, _ = load_setup(path)
raw = tomllib.loads(path.read_text(encoding="utf-8"))
rows = []
for combo in combinations(sweep):
    result = run_setup(parse_setup(apply_overrides(raw, {**combo, "feed.count": 3})))
    rows.append({
        "combo": {k: repr(v) for k, v in combo.items()},
        "metrics": {k: repr(float(v)) for k, v in result.metrics.items()},
        "trace": [[repr(float(t)), repr(float(w))] for t, w in result.trace],
    })
(golden / "hood_angle.json").write_text(json.dumps(rows) + "\n", encoding="utf-8")
"""

import json
import math
import tomllib
from pathlib import Path

import pytest

from scrimmage.__main__ import main
from scrimmage.launcher.inputs import BatteryIn, LauncherConfigError, Shaft, apply_overrides, parse_setup
from scrimmage.launcher.model import FedBall, Launcher
from scrimmage.launcher.run import run_setup
from scrimmage.launcher.shaft import ShaftModel, VelocityController
from scrimmage.launcher.sweep import combinations, load_setup

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "data" / "launcher_golden"
POWER_CELL_D = 0.1778

BASE = {
    "ball": {"preset": "2020-power-cell"},
    "wheels": {"diameter": 0.1016, "width": 0.05, "preset": "30A", "positions": [0.0]},
    "shaft": {"wheel_inertia": 0.002},
    "control": {"target_rps": 60.0, "kP": 0.2},
    "hood": {"gap": 0.8 * POWER_CELL_D, "exit_angle_deg": 40.0, "friction": 0.5},
    "launcher": {"height": 0.5, "width": 0.3},
    "goal": {"distance": 3.0, "height": 2.64, "diameter": 1.2},
}


def make_setup():
    return parse_setup({k: dict(v) for k, v in BASE.items()})


def fed(s, lane=0.0):
    return FedBall(available_at=0.0, lane=lane, entry_speed=1.0, ball=s.ball)


def at_speed(s, **kw):
    """A launcher with its wheel at the target speed, and the controller's voltage there (held constant below, so
    runs stepped differently see the same input)."""
    launcher = Launcher(s, **kw)
    launcher.shaft.speed = 2 * math.pi * s.control.target_rps
    return launcher, VelocityController(s.control, s.shaft).voltage(launcher.shaft)


def run_out(launcher, volts, dt=None, before_step=None, max_time=1.0):
    """Step until no ball is in contact; returns the finished balls."""
    dt = dt or launcher.default_dt()
    out = []
    t_end = launcher.time + max_time
    while launcher.in_contact() and launcher.time < t_end:
        if before_step is not None:
            before_step(launcher)
        out.extend(launcher.step(dt, volts))
    return out


# --- the standalone runs are unchanged ------------------------------------------------------------------------------


def test_run_csv_unchanged(tmp_path, capsys):
    main(["launcher", "run", str(ROOT / "examples/launcher/hooded.toml"), "--out", str(tmp_path)])
    assert (tmp_path / "results.csv").read_bytes() == (GOLDEN / "hooded.csv").read_bytes()


def test_sweep_metrics_unchanged():
    golden = json.loads((GOLDEN / "hood_angle.json").read_text(encoding="utf-8"))
    path = ROOT / "examples/launcher/hood_angle.toml"
    _, sweep, _ = load_setup(path)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    combos = combinations(sweep)
    assert len(combos) == len(golden)
    for combo, want in zip(combos, golden):
        assert {k: repr(v) for k, v in combo.items()} == want["combo"]
        result = run_setup(parse_setup(apply_overrides(raw, {**combo, "feed.count": 3})))
        assert list(result.metrics) == list(want["metrics"]), combo
        for key, value in result.metrics.items():
            assert _same(value, want["metrics"][key]), (combo, key, repr(float(value)), want["metrics"][key])
        assert len(result.trace) == len(want["trace"]), combo
        for (t, w), (want_t, want_w) in zip(result.trace, want["trace"]):
            assert _same(t, want_t) and _same(w, want_w), (combo, repr(float(t)), repr(float(w)), want_t, want_w)


def _same(value, golden: str) -> bool:
    """The golden's value to within rounding. Not exact: the 3.11 CI job (numpy 2.4, another CPU) once differed from
    the 3.14 golden by one ulp in `spread_lateral_m`, the spread of landing points a few mm either side of the
    centerline, whose absolute rounding from the meter-scale flight is large relative to them. A model change moves
    these values by far more than 1e-9."""
    value, golden = float(value), float(golden)
    if math.isnan(golden):
        return math.isnan(value)
    return math.isclose(value, golden, rel_tol=1e-9, abs_tol=1e-15)


# --- the shaft ------------------------------------------------------------------------------------------------------


def test_external_bus_uses_set_voltage():
    shaft = Shaft(wheel_inertia=0.002)
    external = ShaftModel(shaft, None)
    assert external.bus_voltage == 12.0
    external.set_bus_voltage(10.0)
    own = ShaftModel(shaft, BatteryIn(nominal_voltage=10.0, resistance=0.0))
    external.step(0.001, 12.0, 0.0)
    own.step(0.001, 12.0, 0.0)
    assert external.stator_current > 0.0
    assert math.isclose(external.stator_current, own.stator_current, rel_tol=1e-12)
    assert external.bus_voltage == 10.0


def test_angle_integrates_speed():
    model = ShaftModel(Shaft(wheel_inertia=0.002), None)
    a, b = model.torque_coefficients(6.0)
    model.speed = a / b  # the free-running speed at 6 V: with no load the step holds it
    omega = model.speed
    assert model.angle == 0.0
    for _ in range(500):
        model.step(0.001, 6.0, 0.0)
    assert model.speed == omega
    assert math.isclose(model.angle, omega * 0.5, rel_tol=1e-9)


def test_external_bus_launcher():
    s = make_setup()
    assert Launcher(s, external_bus=True).shaft.battery is None
    assert Launcher(s).shaft.battery is not None


# --- the hood angle -------------------------------------------------------------------------------------------------


def test_same_hood_angle_is_noop():
    s = make_setup()
    plain, volts = at_speed(s)
    moved, _ = at_speed(s)
    for launcher in (plain, moved):
        assert launcher.insert(fed(s), 0.0) is None
    a = run_out(plain, volts)
    b = run_out(moved, volts, before_step=lambda ln: ln.set_hood_angle(s.hood.exit_angle_deg))
    assert len(a) == 1 and a[0].status == "launched"
    assert a == b


def mid_pass(s, fraction):
    """A launcher with one ball `fraction` of the way along the hood's arc."""
    launcher, volts = at_speed(s)
    launcher.insert(fed(s), 0.0)
    while launcher.positions()[0][2] < fraction * launcher.path:
        assert launcher.step(launcher.default_dt(), volts) == []
    return launcher, volts


def test_hood_raised_mid_pass():
    s = make_setup()
    unmoved, volts = mid_pass(s, 0.5)
    [base] = run_out(unmoved, volts)
    raised, _ = mid_pass(s, 0.5)
    new = s.hood.exit_angle_deg + 10.0
    raised.set_hood_angle(new)
    assert raised.exit_angle_deg == new
    [shot] = run_out(raised, volts)
    assert base.status == shot.status == "launched"
    assert base.launch_deg == s.hood.exit_angle_deg
    assert shot.launch_deg == new
    assert shot.t_exit - shot.t_enter < base.t_exit - base.t_enter


def test_hood_raised_past_ball_releases_it():
    s = make_setup()
    launcher, volts = mid_pass(s, 0.8)
    wrap = s.hood.entry_deg - (s.hood.exit_angle_deg + 90.0)
    new = s.hood.entry_deg - 90.0 - 0.2 * wrap  # the new hood ends 20% of the old wrap from the entry
    s_ball = launcher.positions()[0][2]
    launcher.set_hood_angle(new)
    d = s.ball.diameter
    assert launcher.path + math.sqrt(d * (d - s.hood.gap)) < s_ball  # the pass's end (hood end + release) is behind
    t_before = launcher.time
    done = launcher.step(launcher.default_dt(), volts)
    assert [d.status for d in done] == ["launched"]
    assert done[0].launch_deg == new
    assert math.isfinite(done[0].speed) and done[0].speed > 0.0
    assert done[0].t_exit == t_before  # it had already left: no step backwards to a landing on the end
    assert launcher.in_contact() == 0
    e = done[0].energy
    books = e.from_shaft + e.entry_kinetic - (e.exit_linear + e.exit_spin + e.potential + e.slip_loss
                                              + e.hysteresis_loss)
    assert abs(books) <= 1e-3 * (e.from_shaft + e.entry_kinetic)


def test_hood_angle_wrap_rejected():
    s = make_setup()
    launcher, _ = at_speed(s)
    path = launcher.path
    with pytest.raises(LauncherConfigError):
        launcher.set_hood_angle(s.hood.entry_deg - 90.0)  # no wrap
    with pytest.raises(LauncherConfigError):
        launcher.set_hood_angle(s.hood.entry_deg - 80.0)
    assert launcher.path == path
    assert launcher.exit_angle_deg == s.hood.exit_angle_deg


# --- stepping in the world ------------------------------------------------------------------------------------------


def test_world_sized_steps_match():
    s = make_setup()
    shots = []
    for dt, count in ((0.001, 60), (0.06, 1)):
        launcher, volts = at_speed(s)
        launcher.insert(fed(s), 0.0)
        out = []
        while launcher.in_contact() and launcher.time < 1.0:
            for _ in range(count):
                out.extend(launcher.step(dt, volts))
        assert [d.status for d in out] == ["launched"]
        shots.append(out[0])
    fine, coarse = shots
    assert math.isclose(fine.speed, coarse.speed, rel_tol=1e-6)
    assert math.isclose(fine.spin, coarse.spin, rel_tol=1e-6)


def test_positions_reports_balls_in_contact():
    s = make_setup()
    launcher, volts = at_speed(s)
    assert launcher.positions() == []
    assert launcher.insert(fed(s, lane=0.0), 0.0) is None
    assert launcher.insert(fed(s, lane=0.12), 0.0) is not None  # no wheel under it: finished at once, not listed
    assert launcher.positions() == [(0, 0.0, 0.0)]
    launcher.step(0.01, volts)
    [(index, lane, s_ball)] = launcher.positions()
    assert (index, lane) == (0, 0.0)
    assert 0.0 < s_ball < launcher.path
    run_out(launcher, volts)
    assert launcher.positions() == []
