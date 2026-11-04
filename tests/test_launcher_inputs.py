import dataclasses
import re
from pathlib import Path

import pytest

from scrimmage.launcher import inputs
from scrimmage.launcher.inputs import (
    PRESETS, LauncherConfigError, Setup, _build_section, apply_overrides, load_setup, parse_setup,
)

BASE = {
    "ball": {"preset": "2020-power-cell"},
    "wheels": {"diameter": 0.1016, "width": 0.025, "preset": "30A", "positions": [-0.05, 0.05]},
    "shaft": {"wheel_inertia": 0.002},
    "control": {"target_rps": 60.0, "kP": 0.2},
    "hood": {"gap": 0.15, "exit_angle_deg": 40.0, "friction": 0.5},
    "launcher": {"height": 0.5, "width": 0.3},
    "goal": {"distance": 3.0, "height": 2.64, "diameter": 1.2},
}


def raw(**changes):
    import copy
    r = copy.deepcopy(BASE)
    for k, v in changes.items():
        r[k] = v
    return r


def test_loads_and_fills_defaults():
    s, sweep, charts = load_setup(raw())
    assert isinstance(s, Setup)
    assert s.air.density == 1.2 and s.air.drag_coefficient == PRESETS["air"]["default"]["drag_coefficient"]
    assert s.control.kV is None and s.control.kS == 0.0
    assert s.wheels.positions == (-0.05, 0.05)
    assert s.battery.nominal_voltage == 12.5 and s.robot.velocity == (0.0, 0.0)
    assert s.dt is None and sweep == {} and charts == []


def test_load_from_path_with_sweep_and_chart(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(
        '[ball]\npreset="2022-cargo"\n[wheels]\ndiameter=0.1\nwidth=0.03\npreset="40A"\n'
        '[shaft]\nwheel_inertia=0.001\n[control]\ntarget_rps=50\nkP=0.1\n'
        '[hood]\ngap=0.2\nexit_angle_deg=30\nfriction=0.4\n[launcher]\nheight=0.5\nwidth=0.3\n'
        '[goal]\ndistance=3\nheight=2.6\ndiameter=1\n'
        '[sweep]\n"hood.gap"=[0.1,0.2]\n[[chart]]\ny="exit_speed"\n'
    )
    s, sweep, charts = load_setup(p)
    assert s.ball.diameter == pytest.approx(0.2413)
    assert sweep == {"hood.gap": [0.1, 0.2]}
    assert charts == [{"y": "exit_speed"}]


def test_preset_fills_and_override_wins():
    r = raw(ball={"preset": "2020-power-cell", "mass": 0.2})
    s = parse_setup(r)
    assert s.ball.diameter == PRESETS["ball"]["2020-power-cell"]["diameter"]
    assert s.ball.mass == 0.2


def test_unknown_preset_names_known_ones():
    with pytest.raises(LauncherConfigError) as e:
        parse_setup(raw(ball={"preset": "nope"}))
    assert "nope" in str(e.value) and "2020-power-cell" in str(e.value)


def test_unknown_key_lists_valid_keys():
    r = raw()
    r["hood"]["gpa"] = 1.0
    with pytest.raises(LauncherConfigError) as e:
        parse_setup(r)
    assert "gpa" in str(e.value) and "gap" in str(e.value)


def test_unknown_section():
    r = raw()
    r["hoodd"] = {}
    with pytest.raises(LauncherConfigError, match="hoodd"):
        parse_setup(r)


def test_missing_required_names_field():
    r = raw()
    del r["hood"]["gap"]
    with pytest.raises(LauncherConfigError, match=r"hood.*gap|gap.*hood"):
        parse_setup(r)
    r = raw()
    del r["goal"]
    with pytest.raises(LauncherConfigError, match="goal"):
        parse_setup(r)


def test_wrong_type_names_key():
    r = raw()
    r["hood"]["gap"] = "wide"
    with pytest.raises(LauncherConfigError, match="gap"):
        parse_setup(r)


def test_unknown_motor_lists_known():
    r = raw(shaft={"wheel_inertia": 0.002, "motor": "Nope"})
    with pytest.raises(LauncherConfigError, match="KrakenX60"):
        parse_setup(r)


def test_wrap_must_be_positive():
    r = raw()
    r["hood"].update(entry_deg=120.0, exit_angle_deg=60.0)  # phi_exit = 150 > 120
    with pytest.raises(LauncherConfigError, match="wrap"):
        parse_setup(r)
    r["hood"].update(entry_deg=240.0)
    parse_setup(r)


def test_apply_overrides_deep_copies():
    r = raw()
    out = apply_overrides(r, {"hood.gap": 0.3, "dt": 0.001, "robot.velocity": [1.0, 0.0]})
    assert r["hood"]["gap"] == 0.15 and "dt" not in r and "robot" not in r
    assert out["hood"]["gap"] == 0.3
    out["hood"]["gap"] = 9
    assert r["hood"]["gap"] == 0.15
    s = parse_setup(out)
    assert s.dt == 0.001 and s.robot.velocity == (1.0, 0.0)


def test_apply_overrides_rejects_unknown():
    with pytest.raises(LauncherConfigError, match="gpa"):
        apply_overrides(raw(), {"hood.gpa": 1})
    with pytest.raises(LauncherConfigError, match="nosuch"):
        apply_overrides(raw(), {"nosuch.x": 1})


def test_every_input_is_sweepable():
    base = parse_setup(raw())
    for sec in dataclasses.fields(Setup):
        if sec.name in ("dt", "provenance"):  # provenance: where measured numbers came from, not an input
            continue
        obj = getattr(base, sec.name)
        for f in dataclasses.fields(obj):
            cur = getattr(obj, f.name)
            if cur is None:
                cur = 0.1  # kV, stator_limit, supply_limit
            if isinstance(cur, tuple):
                cur = list(cur)
            r = apply_overrides(raw(), {f"{sec.name}.{f.name}": cur})
            parse_setup(r)  # accepted: no error
    parse_setup(apply_overrides(raw(), {"dt": 0.002}))


def test_presets_carry_sources():
    text = (Path(inputs.__file__).with_name("presets.toml")).read_text()
    assert "# source:" in text and "# estimate:" in text
    for kind in ("ball", "wheels"):
        assert PRESETS[kind]
    for d in ("30A", "35A", "40A", "45A", "60A"):
        assert d in PRESETS["wheels"]


def test_flare_and_inertia_defaults():
    s = parse_setup(raw())
    assert s.hood.flare_deg == 30.0
    assert s.ball.inertia_factor == pytest.approx(0.4)  # the Power Cell preset: a foam solid
    assert parse_setup(raw(ball={"preset": "2022-cargo"})).ball.inertia_factor == pytest.approx(0.667)
    assert parse_setup(raw(ball={"diameter": 0.2, "mass": 0.2, "stiffness_n": 50.0, "restitution": 0.5})
                       ).ball.inertia_factor == 0.4  # no preset: the dataclass default


@pytest.mark.parametrize("flare", [0.0, -5.0, 90.0])
def test_flare_must_be_between_0_and_90(flare):
    with pytest.raises(LauncherConfigError, match="flare_deg must be above 0 and below 90"):
        parse_setup(raw(hood={**BASE["hood"], "flare_deg": flare}))


def test_flare_wider_than_the_wrap_is_fine():
    # Exit angle 125 with the default entry (240): a 25 degree wrap, less than the default 30 degree flare.
    s = parse_setup(raw(hood={**BASE["hood"], "exit_angle_deg": 125.0}))
    assert s.hood.flare_deg == 30.0


def test_exit_flare_defaults_to_an_abrupt_release():
    s = parse_setup(raw())
    assert s.hood.exit_flare_deg is None
    assert parse_setup(raw(hood={**BASE["hood"], "exit_flare_deg": 45.0})).hood.exit_flare_deg == 45.0


@pytest.mark.parametrize("flare", [0.0, -5.0, 90.0, 120.0])
def test_exit_flare_must_be_between_0_and_90(flare):
    with pytest.raises(LauncherConfigError, match="exit_flare_deg must be above 0 and below 90"):
        parse_setup(raw(hood={**BASE["hood"], "exit_flare_deg": flare}))


@pytest.mark.parametrize("section, changes, key", [
    ("shaft", {"wheel_inertia": 0.0, "added_inertia": 0.0}, "wheel_inertia + added_inertia"),
    ("shaft", {"motor_count": 0}, "motor_count"),
    ("shaft", {"gearing": 0.0}, "gearing"),
    ("shaft", {"wheel_inertia": -0.001}, "wheel_inertia"),
    ("ball", {"preset": "2020-power-cell", "stiffness_exponent": 0.0}, "stiffness_exponent"),
    ("ball", {"preset": "2020-power-cell", "restitution": 1.5}, "restitution"),
    ("ball", {"preset": "2020-power-cell", "mass": 0.0}, "mass"),
    ("hood", {"gap": -0.01}, "gap"),
    ("hood", {"friction": -0.1}, "friction"),
    ("wheels", {"durometer": 100.0}, "durometer"),
    ("wheels", {"diameter": 0.0}, "diameter"),
    ("control", {"stator_limit": 0.0}, "stator_limit"),
    ("battery", {"nominal_voltage": 0.0}, "nominal_voltage"),
    ("battery", {"resistance": -0.01}, "resistance"),
    ("launcher", {"width": 0.0}, "width"),
    ("launcher", {"height": -0.1}, "height"),
    ("air", {"density": -1.0}, "density"),
    ("goal", {"diameter": 0.0}, "diameter"),
    ("goal", {"height": -1.0}, "height"),
    ("feed", {"tolerance": 0.0}, "tolerance"),
    ("feed", {"entry_speed": -1.0}, "entry_speed"),
    ("feed", {"timeout": 0.0}, "timeout"),
    ("spread", {"yaw_deg": -0.5}, "yaw_deg"),
    ("results", {"recovery_tolerance": 0.0}, "recovery_tolerance"),
])
def test_out_of_range_inputs_name_the_key(section, changes, key):
    r = raw()
    r[section] = {**r.get(section, {}), **changes}
    with pytest.raises(LauncherConfigError, match=re.escape(f"[{section}] {key}")):
        parse_setup(r)


def test_examples_and_presets_are_in_range():
    for p in sorted(Path(__file__).parents[1].joinpath("examples", "launcher").glob("*.toml")):
        load_setup(p)


def test_lane_that_fits_the_launcher_width():
    # Power Cell 0.1778 m: lanes at +-0.06 need |lane| + D/2 = 0.1489 <= width/2 = 0.15.
    s = parse_setup(raw(feed={"lanes": [-0.06, 0.06]}))
    assert s.feed.lanes == (-0.06, 0.06)


def test_lane_outside_the_launcher_width_names_lane_width_and_ball():
    with pytest.raises(LauncherConfigError) as e:
        parse_setup(raw(feed={"lanes": [0.0, 0.08]}))
    msg = str(e.value)
    assert "[feed] lanes" in msg and "0.08" in msg and "0.3" in msg and "0.1778" in msg


def test_new_ball_presets_are_there_with_their_sizes():
    P = inputs.PRESETS["ball"]
    NEW = ["2016-boulder", "2017-fuel", "2019-cargo", "2025-algae", "2014-ball", "2010-soccer-ball", "2012-basketball"]
    assert P["2017-fuel"]["diameter"] == pytest.approx(0.127) and P["2017-fuel"]["mass"] == pytest.approx(0.0737)
    assert P["2016-boulder"]["diameter"] == pytest.approx(0.254)
    assert P["2019-cargo"]["diameter"] == pytest.approx(0.3302)
    assert P["2025-algae"]["diameter"] == pytest.approx(0.4064)
    assert P["2014-ball"]["diameter"] == pytest.approx(0.635)
    assert P["2010-soccer-ball"]["diameter"] == pytest.approx(0.2223, abs=5e-4)
    assert P["2012-basketball"]["diameter"] == pytest.approx(0.2021, abs=5e-4)
    for name in NEW:
        assert 0 < P[name]["restitution"] <= 0.95
        _build_section("ball", {"preset": name})       # complete


def test_every_preset_value_is_tagged_source_or_estimate():
    text = (Path(inputs.__file__).with_name("presets.toml")).read_text()
    values = [ln for ln in text.splitlines() if "=" in ln and not ln.lstrip().startswith(("#", "["))]
    assert values
    for ln in values:
        assert "# source:" in ln or "# estimate:" in ln, ln
