from pathlib import Path

import pytest

from scrimmage.config import ConfigError, build_world, load_config
from scrimmage.parts.assemble import assemble
from scrimmage.parts.model import PartError
from scrimmage.protocol import HalState

TESTS = Path(__file__).parent


def robot(*parts, **extra):
    d = {"part": [{"name": "drive", "stock": "chassis", "ids": {"gyro": "Pigeon2:13"}}, *parts]}
    d.update(extra)
    return d


def intake(ids, **kw):
    return {"name": "intake", "path": "parts/intake", "attach": "drive.top", "ids": ids, **kw}


def test_short_forms_become_todays_bindings():
    b = assemble(robot(intake({"roller": "TalonFX:5"})), TESTS)
    (motor,) = b.bindings["motor"]
    assert motor == {"ctre": "TalonFX:5", "name": "intake.roller", "joint": "intake.roller", "motor": "KrakenX60",
                     "count": 1, "gear_ratio": 3.0, "efficiency": 1.0, "invert": False}
    (gyro,) = b.bindings["gyro"]
    assert gyro == {"ctre": "Pigeon2:13", "name": "drive.gyro", "body": "drive.chassis"}
    assert b.warnings == []


def test_pwm_and_table_forms_and_invert_flips():
    b = assemble(robot(intake({"roller": {"pwm": 2, "neutral": "coast", "invert": True}})), TESTS)
    (motor,) = b.bindings["motor"]
    assert motor["pwm"] == 2 and motor["neutral"] == "coast" and motor["invert"] is True
    b = assemble(robot(intake({"roller": "PWM:2"})), TESTS)
    assert b.bindings["motor"][0]["pwm"] == 2


def test_list_of_ids_makes_one_binding_per_controller(tmp_path):
    part = (TESTS / "parts" / "intake" / "part.toml").read_text().replace("gearing = 3", "gearing = 3\ncount = 2")
    (tmp_path / "two").mkdir()
    (tmp_path / "two" / "part.toml").write_text(part)
    d = robot({"name": "intake", "path": str(tmp_path / "two"), "attach": "drive.top",
               "ids": {"roller": ["TalonFX:5", "TalonFX:6"]}})
    ms = assemble(d, TESTS).bindings["motor"]
    assert [(m["name"], m["ctre"], m["count"]) for m in ms] == [("intake.roller[0]", "TalonFX:5", 1),
                                                                 ("intake.roller[1]", "TalonFX:6", 1)]
    d["part"][1]["ids"] = {"roller": "TalonFX:5"}
    assert assemble(d, TESTS).bindings["motor"][0]["count"] == 2
    d["part"][1]["ids"] = {"roller": ["TalonFX:5", "TalonFX:6", "TalonFX:7"]}
    with pytest.raises(PartError, match=r"slot 'roller' has 2 motors: give one ID or a list of 2"):
        assemble(d, TESTS)


@pytest.mark.parametrize("ids, message", [
    ({"rollr": "TalonFX:5"}, r"parts\[1\] 'intake': ids: no slot 'rollr'; the part's slots: roller"),
    ({"roller": "Pigeon2:5"}, r"slot 'roller' is a motor slot; Pigeon2:5 is a gyro"),
    ({"roller": "CANcoder:5"}, r"slot 'roller' is a motor slot; CANcoder:5 is an encoder"),
    ({"roller": "TalonFX"}, r"\"<Kind>:<number>\""),
    ({"roller": "Spark:3"}, r"Kind one of TalonFX, CANcoder, Pigeon2, PWM, DIO"),
    # SPARKs have short forms of their own: the table example is a device without one
    ({"roller": "Spark:3"}, r"other devices take a table of binding keys, e\.g\. \{ can = \"VictorSPX\[3\]\" \}\)$"),
    ({"roller": {"pwm": 1, "joint": "x"}}, r"'joint' comes from the part"),
    ({"roller": 5}, r"must be \"<Kind>:<number>\", a table of binding keys"),
])
def test_id_errors(ids, message):
    with pytest.raises(PartError, match=message):
        assemble(robot(intake(ids)), TESTS)


def test_kind_mismatch_uses_an_before_encoder():
    from scrimmage.parts.ids import _device
    with pytest.raises(PartError, match=r"slot 'enc' is an encoder slot; TalonFX:3 is a motor$"):
        _device("encoder", "enc", "TalonFX:3", "w")


def test_missing_ids_are_warnings():
    b = assemble(robot(intake({})), TESTS)
    assert b.bindings["motor"] == []
    assert b.warnings == ["intake.roller has no device ID, so it won't move"]


def write_cfg(tmp_path, text):
    p = tmp_path / "robot.toml"
    p.write_text(text)
    return load_config(p)


PARTS_CFG = f'''
[robot]
start_pose = [2.0, 4.035, 0.0]
[[part]]
name = "drive"
stock = "chassis"
ids = {{ gyro = "Pigeon2:13" }}
[[part]]
name = "intake"
path = "{TESTS / "parts" / "intake"}"
attach = "drive.top"
ids = {{ roller = "PWM:0" }}
'''


def test_world_runs_on_a_parts_robot(tmp_path):
    w = build_world(write_cfg(tmp_path, PARTS_CFG))
    assert w.robot_pose()[:2] == pytest.approx((2.0, 4.035))
    assert [m.name for m in w.motors] == ["intake.roller"]
    hal = HalState()
    hal.devices[("PWM", "0")] = {"<init": True, "<speed": 1.0}
    for _ in range(200):
        w.step(hal)
    assert w.joint_states()["intake.roller"][1] > 10      # spinning (rad/s)
    assert w.warnings == []


def test_duplicate_ids_across_parts_name_both(tmp_path):
    text = PARTS_CFG.replace('roller = "PWM:0"', 'roller = "TalonFX:13"').replace('gyro = "Pigeon2:13"', 'gyro = "Pigeon2:1"')
    text += f'''
[[part]]
name = "intake2"
path = "{TESTS / "parts" / "intake"}"
attach = "drive.center"
ids = {{ roller = "TalonFX:13" }}
'''
    with pytest.raises(ConfigError, match=r"is bound by motor intake.roller and motor intake2.roller"):
        build_world(write_cfg(tmp_path, text))


@pytest.mark.parametrize("extra, message", [
    ('[model]\nmjcf = "x.xml"\n', r"either \[model\] mjcf .* or \[\[part\]\] entries, not both"),
    ('[[motor]]\nname = "m"\npwm = 3\njoint = "intake.roller"\n', r"\[\[motor\]\] entries don't go with \[\[part\]\]"),
    ('[robot]\nbody = "x"\n', r"\[robot\] body is set by the root part"),
])
def test_parts_and_hand_written_dont_mix(tmp_path, extra, message):
    text = PARTS_CFG.replace("[robot]\nstart_pose", "[robotx]\nstart_pose") if "[robot]" in extra else PARTS_CFG
    with pytest.raises(ConfigError, match=message):
        build_world(write_cfg(tmp_path, text + extra))


def test_rev_short_forms():
    d = robot(intake({"roller": "SparkFlex:7"}))
    b = assemble(d, TESTS)
    assert b.bindings["motor"][0]["rev"] == "SparkFlex:7"
    assert "ctre" not in b.bindings["motor"][0]
    from scrimmage.parts.ids import _device
    assert _device("motor", "m", "SparkMax:3", "w") == {"rev": "SparkMax:3"}
    assert _device("encoder", "e", "SparkMaxAbs:3", "w") == {"rev": "SparkMaxAbs:3"}
    assert _device("encoder", "e", "SparkFlexAbs:4", "w") == {"rev": "SparkFlexAbs:4"}
    with pytest.raises(PartError, match=r"slot 'e' is an encoder slot; SparkMax:3 is a motor$"):
        _device("encoder", "e", "SparkMax:3", "w")
    with pytest.raises(PartError, match=r"slot 'm' is a motor slot; SparkMaxAbs:3 is an encoder$"):
        _device("motor", "m", "SparkMaxAbs:3", "w")


def test_sim_short_forms_take_a_name():
    from scrimmage.parts.ids import _device
    assert _device("motor", "m", "SimMotor:fl_drive", "w") == {"scrimmage": "fl_drive"}
    assert _device("encoder", "e", "SimEncoder:fl_turn", "w") == {"scrimmage": "fl_turn"}
    assert _device("gyro", "g", "SimGyro:imu", "w") == {"scrimmage": "imu"}
    with pytest.raises(PartError, match=r"SimMotor:.*needs a name"):
        _device("motor", "m", "SimMotor:", "w")
    with pytest.raises(PartError, match=r"no spaces"):
        _device("motor", "m", "SimMotor:a b", "w")
    with pytest.raises(PartError, match=r"slot 'e' is an encoder slot; SimMotor:x is a motor$"):
        _device("encoder", "e", "SimMotor:x", "w")
    b = assemble(robot(intake({"roller": "SimMotor:roller"})), TESTS)
    assert b.bindings["motor"][0]["scrimmage"] == "roller" and "ctre" not in b.bindings["motor"][0]
