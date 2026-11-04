import math
import re

import pytest

from scrimmage.parts.model import PartError
from scrimmage.parts.stock import REGISTRY, Param, Stock, _check, _load_all, build_stock, describe

_load_all()


def test_chassis_defaults():
    p = build_stock("chassis", {}, "parts[0] 'drive'")
    assert p.bodies[0].name == "chassis"
    assert p.bodies[0].mass == pytest.approx(46.0)
    assert {m.name for m in p.mounts} == {"front_left", "front_right", "back_left", "back_right", "center", "top"}
    fl = next(m for m in p.mounts if m.name == "front_left")
    assert fl.pos == pytest.approx((0.35 - 0.0579, 0.35 - 0.0579, 0.0))
    assert p.slot_kinds() == {"gyro": "gyro"}


def test_chassis_parameters_move_the_corners():
    p = build_stock("chassis", {"length": 0.8, "width": 0.6, "module_inset": 0.1}, "w")
    br = next(m for m in p.mounts if m.name == "back_right")
    assert br.pos == pytest.approx((-0.3, -0.2, 0.0))


def _bumper_z_spans(p) -> list[tuple[float, float]]:
    """Each bumper box's (bottom, top) on the chassis: the blue boxes."""
    return [(round(g.pos[2] - g.size[2] / 2, 9), round(g.pos[2] + g.size[2] / 2, 9))
            for g in p.bodies[0].geoms if g.shape == "box" and g.friction == 0.3]


def test_chassis_bumper_clearance_defaults_to_the_old_bumpers():
    """bumper_clearance (the bumpers' bottom above the carpet) defaults to 0.02 m: the bumpers span z 0.02..0.14,
    as they did before the parameter existed, so existing robots don't change."""
    assert _bumper_z_spans(build_stock("chassis", {}, "w")) == [(0.02, 0.14)] * 4
    assert _bumper_z_spans(build_stock("chassis", {"bumper_clearance": 0.06}, "w")) == [(0.06, 0.18)] * 4


@pytest.mark.parametrize("given, message", [
    ({"lenght": 0.7}, r"parts\[0\] 'drive': unknown parameter 'lenght' for chassis; parameters: .*length"),
    ({"mass": -3}, r"mass must be greater than 0"),
    ({"mass": "heavy"}, r"mass must be a number"),
    ({"module_inset": 0.5}, r"module_inset must be at most"),
    ({"mass": float("nan")}, r"mass must be a finite number"),
])
def test_bad_parameters(given, message):
    with pytest.raises(PartError, match=message):
        build_stock("chassis", given, "parts[0] 'drive'")


def test_unknown_stock_lists_the_library():
    with pytest.raises(PartError, match=r"unknown stock part 'chasis'; stock parts: .*chassis"):
        build_stock("chasis", {}, "parts[0] 'drive'")


def test_describe_lists_parameters_with_units_defaults_and_constraints():
    text = describe("chassis")
    assert "length = 0.7 m (greater than 0)" in text
    assert "module_inset = 0.0579 m (at least 0.0, limited by other parameters)" in text
    assert "mounts: front_left, front_right, back_left, back_right, center, top" in text
    assert "slots: gyro" in text
    assert " :" not in text and "  (" not in text and " ()" not in text
    assert "chassis" in describe(None)
    assert set(REGISTRY) >= {"chassis"}


def test_describe_writes_defaults_as_toml():
    """Each default reads as a robot file writes it: true/false, double-quoted strings, lists, numbers."""
    import re
    _load_all()
    for name in REGISTRY:
        for line in describe(name).splitlines():
            m = re.match(r"  (\w+) = (.*?)(?: [^ ]+)?(?: \(.*\))?: ", line)
            if m is None:
                continue
            assert "False" not in m.group(2) and "True" not in m.group(2) and "'" not in m.group(2), line
    arm = describe("arm")
    assert '  motor = "KrakenX60" (one of ' in arm and "  collide = false: " in arm
    assert "  range = [-90.0, 90.0] deg " in arm
    assert '  push = "": ' in describe("cylinder")


def test_unknown_describe_name_lists_the_library():
    with pytest.raises(PartError, match=r"unknown stock part 'nope'; stock parts: .*chassis"):
        describe("nope")


@pytest.mark.parametrize("param, value, expected", [
    (Param("n", "m", 1.0, "d", positive=True), 2, 2.0),
    (Param("n", "m", 1.0, "d", min=0.0, max=1.0), 0.5, 0.5),
    (Param("c", "", 2, "d", kind="int", min=1, max=4), 3, 3),
    (Param("s", "", "a", "d", kind="str", choices=("a", "b")), "b", "b"),
    (Param("g", "", "low", "d", kind="ratio", choices=("low", "high")), "high", "high"),
    (Param("g", "", "low", "d", kind="ratio", choices=("low", "high")), 2, 2.0),
    (Param("r", "deg", (0, 1), "d", kind="pair"), (0, 1), (0.0, 1.0)),
    (Param("r", "deg", (0, 1), "d", kind="pair"), [1, 2], (1.0, 2.0)),
    (Param("b", "", False, "d", kind="bool"), True, True),
])
def test_check_accepts(param, value, expected):
    assert _check(param, value, "w", {}) == expected


@pytest.mark.parametrize("param, value, message", [
    (Param("n", "m", 1.0, "d", positive=True), 0, "n must be greater than 0, got 0"),
    (Param("n", "m", 1.0, "d"), float("nan"), r"n must be a finite number \(m\), not nan"),
    (Param("n", "m", 1.0, "d"), float("inf"), r"n must be a finite number \(m\), not inf"),
    (Param("n", "m", 1.0, "d"), True, r"n must be a number \(m\), not True"),
    (Param("c", "", 2, "d", kind="int", min=1, max=4), 0, "c must be at least 1, got 0"),
    (Param("c", "", 2, "d", kind="int", min=1, max=4), 5, "c must be at most 4, got 5"),
    (Param("c", "", 2, "d", kind="int", min=1, max=4), 2.5, "c must be a whole number, not 2.5"),
    (Param("n", "m", 1.0, "d"), 10 ** 400, r"n must be a finite number \(m\), not 1000"),
    (Param("c", "", 2, "d", kind="int", min=1, max=4), 10 ** 400, "c must be a finite number, not 1000"),
    (Param("g", "", "low", "d", kind="ratio", choices=("low", "high")), 10 ** 400,
     "g must be one of low, high or a number greater than 0, not 1000"),
    (Param("r", "deg", (0, 1), "d", kind="pair"), [0, 10 ** 400], r"r must be \[min, max\] with min < max \(deg\), not \[0, 1000"),
    (Param("s", "", "a", "d", kind="str", choices=("a", "b")), "c", "s must be one of a, b, not 'c'"),
    (Param("g", "", "low", "d", kind="ratio", choices=("low", "high")), "mid",
     "g must be one of low, high or a number greater than 0, not 'mid'"),
    (Param("g", "", "low", "d", kind="ratio", choices=("low", "high")), 0,
     "g must be one of low, high or a number greater than 0, not 0"),
    (Param("g", "", "low", "d", kind="ratio", choices=("low", "high")), float("nan"),
     r"g must be one of low, high or a number greater than 0, not nan"),
    (Param("r", "deg", (0, 1), "d", kind="pair"), [2, 1], r"r must be \[min, max\] with min < max \(deg\), not \[2, 1\]"),
    (Param("r", "deg", (0, 1), "d", kind="pair"), [1], r"r must be \[min, max\] with min < max \(deg\), not \[1\]"),
    (Param("r", "deg", (0, 1), "d", kind="pair"), [0, math.inf], r"r must be \[min, max\]"),
    (Param("b", "", False, "d", kind="bool"), 1, "b must be true or false, not 1"),
])
def test_check_rejects(param, value, message):
    with pytest.raises(PartError, match=message):
        _check(param, value, "w", {})


@pytest.mark.parametrize("kwargs", [
    dict(kind="str"),
    dict(kind="ratio"),
    dict(kind="str", choices=()),
    dict(kind="nonsense"),
])
def test_param_definition_errors(kwargs):
    with pytest.raises(ValueError):
        Param("x", "", "a", "d", **kwargs)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_stock_builds_with_defaults(name):
    stock = REGISTRY[name]
    part = build_stock(name, {}, "t")
    assert set(stock.slots) == set(part.slot_kinds())
    assert set(stock.mounts) == {m.name for m in part.mounts}


def test_registry_not_empty():
    assert REGISTRY


def test_callable_max_sees_only_earlier_parameters(monkeypatch):
    seen = []

    def limit(q):
        seen.append(sorted(q))
        return 1.0

    probe = Stock(
        "probe_limits", "test only",
        (Param("a", "m", 0.5, "d"), Param("b", "m", 0.5, "d", max=limit)),
        lambda q: {"part": {"name": "probe"},
                   "body": [{"name": "b", "mass": 1.0, "geom": [{"sphere": 0.1}]}]},
        slots=(), mounts=())
    monkeypatch.setitem(REGISTRY, "probe_limits", probe)   # removed again after the test
    build_stock("probe_limits", {"a": 0.2, "b": 0.5}, "w")
    assert seen == [["a"]]


def test_elevator_structure_and_gearing():
    p = build_stock("elevator", {"stages": 3, "rigging": "cascade", "travel": 1.2}, "e")
    assert [b.name for b in p.bodies] == ["base", "stage1", "stage2", "carriage"]
    assert [b.joint.range for b in p.bodies[1:]] == [(0.0, 0.4)] * 3
    assert len(p.couplings) == 2
    (lift,) = p.motors
    assert lift.joint == "stage1" and lift.gearing == pytest.approx(9 / 0.022)
    p = build_stock("elevator", {"stages": 3, "rigging": "continuous", "travel": 1.2}, "e")
    assert p.motors[0].gearing == pytest.approx(9 * 3 / 0.022)
    assert p.encoders[0].ratio == pytest.approx(3 / (2 * 3.141592653589793 * 0.022))


def test_arm_flywheel_roller_slots_and_mounts():
    arm = build_stock("arm", {"range": [-30, 120]}, "a")
    assert arm.bodies[1].joint.range == (-30.0, 120.0) and arm.bodies[1].com == (0.25, 0.0, 0.0)
    assert {m.name for m in arm.mounts} == {"base", "tip"}
    assert build_stock("flywheel", {}, "f").slot_kinds() == {"spin": "motor"}
    roller = build_stock("roller", {"length": 0.5}, "r")
    assert roller.bodies[1].geoms[0].size[1] == pytest.approx(0.5)


@pytest.mark.parametrize("name, given, message", [
    ("elevator", {"stages": 5}, r"stages must be at most 4"),
    ("elevator", {"rigging": "pulley"}, r"rigging must be one of continuous, cascade"),
    ("arm", {"range": [90, 0]}, r"range must be \[min, max\]"),
    ("arm", {"motor": "Flux"}, r"motor must be one of .*KrakenX60"),
])
def test_mechanism_parameter_errors(name, given, message):
    with pytest.raises(PartError, match=message):
        build_stock(name, given, "x")


def test_arm_com_and_spring_emission():
    assert build_stock("arm", {"length": 0.8}, "a").bodies[1].com == (0.4, 0.0, 0.0)
    assert build_stock("arm", {"length": 0.8, "com": 0.55}, "a").bodies[1].com == (0.55, 0.0, 0.0)
    assert build_stock("arm", {}, "a").bodies[1].joint.spring is None
    assert build_stock("arm", {"spring_stiffness": 3.0, "spring_rest": 20}, "a").bodies[1].joint.spring == (3.0, 20.0)
    # a rest angle alone still produces a spring (stiffness 0), so the two parameters never silently disagree
    assert build_stock("arm", {"spring_rest": 20}, "a").bodies[1].joint.spring == (0.0, 20.0)


def test_spinner_default_inertia_is_a_solid_disc():
    for name, given, r, m in [("flywheel", {}, 0.0508, 0.5), ("roller", {}, 0.025, 0.3)]:
        b = build_stock(name, given, "s").bodies[1]
        assert b.inertia == pytest.approx((m * r * r / 4, m * r * r / 2, m * r * r / 4))
    assert build_stock("flywheel", {"moi": 0.01}, "f").bodies[1].inertia == pytest.approx((0.005, 0.01, 0.005))


def test_elevator_couplings_ratios_and_friction():
    p = build_stock("elevator", {"stages": 3}, "e")
    assert [(c.joints, c.ratio) for c in p.couplings] == [(("stage1", "stage2"), 1.0), (("stage1", "carriage"), 1.0)]
    assert p.bodies[1].joint.frictionloss == pytest.approx(5.0 * 3)       # newtons at the carriage
    one = build_stock("elevator", {}, "e")
    assert one.couplings == [] and one.motors[0].joint == "carriage"
    assert one.bodies[1].joint.frictionloss == pytest.approx(5.0)


def test_elevator_encoder_ratio_by_rigging():
    r = 0.022
    cascade = build_stock("elevator", {"stages": 3, "rigging": "cascade"}, "e")
    assert cascade.encoders[0].joint == "stage1" and cascade.encoders[0].ratio == pytest.approx(1 / (2 * math.pi * r))
    cont = build_stock("elevator", {"stages": 3}, "e")
    assert cont.encoders[0].ratio == pytest.approx(3 / (2 * math.pi * r))


def test_mk4i_ratios_and_inversions():
    p = build_stock("swerve/MK4i", {"ratio": "L3"}, "m")
    drive_slot = next(s for s in p.motors if s.slot == "drive")
    steer_slot = next(s for s in p.motors if s.slot == "steer")
    assert drive_slot.gearing == pytest.approx(6.12) and drive_slot.invert is False
    assert steer_slot.gearing == pytest.approx(150 / 7) and steer_slot.invert is True
    assert build_stock("swerve/MK4i", {"ratio": 7.36}, "m").motors[0].gearing == pytest.approx(7.36)
    assert "REV" in describe("swerve/MAXSwerve")


def test_maxswerve_ratios_wheel_and_slots():
    for teeth in (12, 13, 14):      # REV: 45T bevel x 22T spur / (pinion x 15T bevel)
        gearing = build_stock("swerve/MAXSwerve", {"ratio": f"{teeth}T"}, "m").motors[0].gearing
        assert gearing == pytest.approx(45 * 22 / (teeth * 15), rel=1e-12)
    assert "12T = 5.5, 13T = 5.08, 14T = 4.71," in describe("swerve/MAXSwerve")    # labels: 2 decimals
    p = build_stock("swerve/MAXSwerve", {}, "m")
    drive = next(s for s in p.motors if s.slot == "drive")
    steer = next(s for s in p.motors if s.slot == "steer")
    assert drive.gearing == pytest.approx(45 * 22 / (14 * 15), rel=1e-12) and drive.motor == "NEO"
    assert steer.gearing == pytest.approx(9424 / 203) and steer.motor == "NEO550"
    wheel = next(b for b in p.bodies if b.name == "wheel")
    assert max(g.size[0] for g in wheel.geoms) == pytest.approx(0.0762 / 2)
    assert [e.slot for e in p.encoders] == ["encoder"] and p.encoders[0].joint == "steer" and p.encoders[0].ratio == 1.0
    assert p.encoders[0].invert is True                  # reads the module clockwise-positive: REV's template inverts it
    assert build_stock("swerve/MK4i", {}, "m").encoders[0].invert is False
    assert "REV MAXSwerve" in describe("swerve/MAXSwerve") and "can't bind" not in describe("swerve/MAXSwerve")


def test_differential_wheels_and_couplings():
    p = build_stock("differential", {"wheels_per_side": 3}, "d")
    assert [b.name for b in p.bodies] == ["rails", "left_1", "left_2", "left_3", "right_1", "right_2", "right_3"]
    assert {s.slot: s.joint for s in p.motors} == {"left": "left_2", "right": "right_2"}
    assert next(s for s in p.motors if s.slot == "right").invert is True
    assert len(p.couplings) == 4
