"""The example launcher setups in examples/launcher/ load, run, and give sane results."""

import tomllib
from pathlib import Path

import pytest

from scrimmage.launcher.inputs import apply_overrides, load_setup, parse_setup
from scrimmage.launcher.run import run_setup
from scrimmage.launcher.sweep import combinations

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "launcher"
NAMES = ("hooded.toml", "flywheel_squish.toml", "hood_angle.toml")


def raw(name: str) -> dict:
    return tomllib.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def hooded(**overrides):
    return parse_setup(apply_overrides(raw("hooded.toml"), overrides))


@pytest.mark.parametrize("name", NAMES)
def test_examples_load_and_run(name):
    # Each example runs: a plain setup through run_setup, a sweep through every combination (as `run_sweep` does),
    # with feed.count cut to 2 to keep the test quick.
    _, sweep, _ = load_setup(EXAMPLES / name)
    combos = combinations(sweep)
    if name == "hooded.toml":
        assert not sweep
    else:
        assert len(combos) > 1
    for combo in combos:
        s = parse_setup(apply_overrides(raw(name), {**combo, "feed.count": 2}))
        r = run_setup(s)
        assert r.metrics["fired"] == 2, (name, combo)
        assert r.metrics["launched"] == 2, (name, combo, [x.status for x, _ in r.shots])


@pytest.mark.xfail(
    strict=True,
    reason="hooded.toml at 66.7 rps launches at 7.48 m/s with exit_slip_pct 25.7 (grip-limited above a knee near "
    "50 rps) on the Power Cell preset's estimated stiffness_n = 60 N; below the 8 m/s sanity floor. Not tuned: "
    "stiffness_n is unmeasured (it gives 9.7 m/s at 120 N). Controller ruling R24: report to the user.",
)
def test_hooded_sanity_range():
    """A sanity range only, not a target: at 4000 rpm (66.7 rps) a 4 in. wheel's surface moves at 21 m/s, so a
    gripping wheel would launch near half that, about 10 m/s. 8 to 20 m/s is a wide range around that. (2020 teams'
    long Power Cell shots likely needed similar speeds, but we have no measured figure to cite.) The model is
    uncalibrated, so this only catches a result that is wildly off. It fails today with the estimated 60 N ball
    (docs/launcher.md, "A known shortfall"): either that estimate is low or the model under-predicts grip, and
    measuring stiffness_n decides which."""
    r = run_setup(hooded(**{"control.target_rps": 66.7, "feed.count": 2}))
    assert r.metrics["launched"] == 2
    assert 8.0 <= r.metrics["exit_speed"] <= 20.0, r.metrics


def test_hooded_rpm_regime():
    # The report says which regime a shot is in (exit_slip_pct): below the grip limit ("knee") the wheel grips the
    # ball out (slip near 0) and more wheel speed gives proportionally more exit speed; above it the slip is large.
    speeds = {}
    for rps in (25.0, 35.0):
        r = run_setup(hooded(**{"control.target_rps": rps, "feed.count": 1}))
        assert r.metrics["exit_slip_pct"] < 5.0, (rps, r.metrics)
        speeds[rps] = r.metrics["exit_speed"]
    assert speeds[35.0] == pytest.approx(speeds[25.0] * 35.0 / 25.0, rel=0.05), speeds
    fast = run_setup(hooded(**{"control.target_rps": 90.0, "feed.count": 1}))
    assert fast.metrics["exit_slip_pct"] > 20.0, fast.metrics
