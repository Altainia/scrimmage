import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scrimmage.checks import CRASH_PATTERN, build_check, resolve_signal
from scrimmage.protocol import HalState
from scrimmage.scenario import ScenarioError, load_scenario, load_scenario_text, validate
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]

GOOD = """
config = "{config}"
description = "demo"
speed = "max"

[[step]]
mode = "disabled"
duration = 1.0

[[step]]
name = "auto"
mode = "auto"
duration = 2.0
axes = { 0 = [0.0, -0.5] }
  [[step.check]]
  pose = { x = 4.0, y = 5.0, heading = 90, tol_xy = 0.25 }
  [[step.check]]
  always = { signal = "battery", min = 7.0 }
  [[step.check]]
  never_touch = "obstacle"
  [[step.screenshot]]
  view = "top"
  at = 1.5
  [step.contact_sheet]
  frames = 4
"""


def write(tmp_path, text):
    p = tmp_path / "s.toml"
    p.write_text(text.replace("{config}", str(ROOT / "configs" / "kitbot.toml")))
    return p


@pytest.fixture
def world():
    return World(tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text()), ROOT / "configs")


def test_load_good_scenario(tmp_path, world):
    s = load_scenario(write(tmp_path, GOOD))
    assert s.speed is None and s.require_lockstep is True and s.config_path == ROOT / "configs" / "kitbot.toml"
    assert [st.name for st in s.steps] == ["step 1", "auto"]
    assert s.steps[1].axes == {0: [0.0, -0.5]} and s.steps[1].screenshots[0].at == 1.5
    assert s.steps[1].contact_sheet.frames == 4
    validate(s, world)


@pytest.mark.parametrize("bad, msg", [
    ('speeed = 1\n', "unknown key 'speeed'"),
    ('[[step]]\nmode = "autonomous"\nduration = 1\n', "mode must be one of"),
    ('[[step]]\nmode = "auto"\nduration = -1\n', "duration must be a positive number"),
    ('[[step]]\nmode = "auto"\nduration = 1\n[[step.check]]\npose = {x = 1}\nalways = {signal = "speed", max = 1}\n',
     "exactly one of"),
    ('[[step]]\nmode = "auto"\nduration = 1\n[[step.screenshot]]\nat = 5\n', "beyond the step's 1 s"),
])
def test_unknown_or_bad_values(tmp_path, bad, msg):
    with pytest.raises(ScenarioError, match=msg):
        load_scenario(write(tmp_path, 'config = "{config}"\n' + bad))


def test_unknown_signal_and_names_list_alternatives(world):
    with pytest.raises(ScenarioError, match=r"dt\.left"):
        build_check({"always": {"signal": "motor.lefty.current", "max": 1}}, world, "step 1")
    with pytest.raises(ScenarioError, match="obstacle"):
        build_check({"never_touch": "obstacel"}, world, "step 1")
    with pytest.raises(ScenarioError, match="battery, speed"):
        resolve_signal("voltage", world)


class StubRobot:
    def __init__(self):
        self.lines = []

    @property
    def next_index(self):
        return len(self.lines)

    def push(self, *texts):
        self.lines.extend(texts)

    def since(self, i):
        return [SimpleNamespace(index=n, t=0.0, text=t) for n, t in enumerate(self.lines) if n >= i]


def stub(world):
    return SimpleNamespace(world=world, bridge=SimpleNamespace(hal=HalState(), nt_link=None), robot=StubRobot(),
                           nt_value=lambda topic: None)


def test_pose_check_reports_measured_values(world, units):
    s = stub(world)
    c = build_check({"pose": {"x": 4.0, "y": 4.035, "tol_xy": 0.25}}, world, "auto")
    world.set_robot_pose(3.6, 4.035, 0.0)
    units("metric")
    r = c.end(s)
    assert not r.passed and "0.4 m from target" in r.shown() and "x=3.6 m" in r.shown()
    assert "1.31 ft from target" in r.message and "x=11.81 ft" in r.message   # report.json's: units.MACHINE
    world.set_robot_pose(4.1, 4.0, 0.0)
    assert c.end(s).passed


def test_always_check_reports_first_failure_time(world):
    s = stub(world)
    c = build_check({"always": {"signal": "battery", "min": 12.0}}, world, "auto")
    c.begin(s)
    world.vbatt = 12.5
    c.tick(s, 1.0)
    world.vbatt = 11.0
    c.tick(s, 1.25)
    world.vbatt = 10.0
    c.tick(s, 1.5)
    r = c.end(s)
    assert not r.passed and r.first_failure_t == 1.25 and r.measured == 10.0


def test_contact_checks(world):
    s = stub(world)
    never = build_check({"never_touch": "obstacle"}, world, "auto")
    must = build_check({"must_touch": "obstacle"}, world, "auto")
    for c in (never, must):
        c.begin(s)
    world.set_robot_pose(8.27 - 0.9, 4.035, 0.0)   # bumper against the obstacle
    for c in (never, must):
        c.tick(s, 2.0)
    assert not never.end(s).passed and must.end(s).passed


def test_console_check_and_crash_pattern(world):
    s = stub(world)
    c = build_check({"console": {"contains": r"step 3: stop", "not_contains": "Exception"}}, world, "auto")
    c.begin(s)
    s.robot.push("step 0: drive", "step 3: stop")
    assert c.end(s).passed
    assert CRASH_PATTERN.search("Traceback (most recent call last):")
    assert CRASH_PATTERN.search("Exception in thread \"main\" java.lang.NullPointerException")
    assert CRASH_PATTERN.search("\tat frc.robot.Robot.teleopPeriodic(Robot.java:42)")
    assert not CRASH_PATTERN.search("step 3: stop")


def test_at_end_on_nt_signal_needs_nt(world):
    c = build_check({"at_end": {"signal": "nt:/SmartDashboard/Gyro deg", "approx": 0, "tol": 5}}, world, "x")
    assert c.uses_nt
    r = c.end(stub(world))
    assert not r.passed and "no value" in r.message


@pytest.mark.parametrize("config_nt, session_nt, words", [
    (False, False, "[telemetry] nt = false"),
    (True, False, "nt=False"),
])
def test_nt_check_without_nt_says_why(world, config_nt, session_nt, words):
    import asyncio

    from scrimmage.scenario_runner import Report, _run_steps
    step = SimpleNamespace(name="s", checks=[{"at_end": {"signal": "nt:/SmartDashboard/x", "equals": 1}}])
    session = SimpleNamespace(lockstep=True, world=world, nt=session_nt, config=SimpleNamespace(nt=config_nt),
                              bridge=SimpleNamespace(nt_link=None, clock_freed=False))
    report = Report("x", "")
    asyncio.run(_run_steps(session, SimpleNamespace(require_lockstep=False, steps=[step]), report, Path("."), 0, None))
    assert words in report.error and "NetworkTables" in report.error
    assert ("Session" in report.error) is False


@pytest.mark.parametrize("extra, msg", [
    ('[[step.screenshot]]\nwidth = 8\n', "screenshot 1: width must be a whole number from 16 to 4096"),
    ('[[step.screenshot]]\nheight = 5000\n', "screenshot 1: height must be"),
    ('[step.contact_sheet]\nwidth = 99999\n', "contact_sheet: width must be"),
])
def test_render_sizes_validated(tmp_path, extra, msg):
    text = 'config = "{config}"\n[[step]]\nmode = "auto"\nduration = 1\n' + extra
    with pytest.raises(ScenarioError, match=msg):
        load_scenario(write(tmp_path, text))


@pytest.mark.parametrize("val", ["nan", "inf", "-inf"])
def test_check_values_must_be_finite(world, val):
    spec = {"always": {"signal": "battery", "min": float(val)}}
    with pytest.raises(ScenarioError, match="finite"):
        build_check(spec, world, "step 1")


def _step(extra, dur=1):
    return f'config = "{{config}}"\n[[step]]\nmode = "auto"\nduration = {dur}\n' + extra


@pytest.mark.parametrize("text, msg", [
    ('config = "{config}"\nstep = [1]\n', "step 1.*table"),
    (_step('contact_sheet = 3\n'), "contact_sheet must be a table"),
    (_step('screenshot = [3]\n'), "screenshot 1 must be a table"),
    (_step('[step.screenshot]\nview = "top"\n'), r"use \[\[step.screenshot\]\]"),
    (_step('check = [3]\n'), "check 1 must be a table"),
    (_step('', "inf"), "duration must be a positive"),
    (_step('', "nan"), "duration must be a positive"),
    (_step('[[step.screenshot]]\nat = nan\n'), "at must be"),
    (_step('axes = { 0 = [1.5] }\n'), "port 0, index 0.*-1 and 1"),
    (_step('axes = { 0 = ["x"] }\n'), "axes.*port 0, index 0"),
    (_step('buttons = { 0 = ["false"] }\n'), "buttons.*port 0, index 0.*true or false"),
    (_step('povs = { 0 = [90.0] }\n'), "povs.*port 0, index 0"),
    (_step('povs = { 0 = [360] }\n'), "povs.*port 0, index 0.*-1 or 0-359"),
    (_step('[step.contact_sheet]\nframes = 99\n'), "contact_sheet frames"),
    (_step('[step.contact_sheet]\nframes = 16\nwidth = 4096\nheight = 1024\n'), "67.1 megapixels"),
])
def test_more_bad_values(tmp_path, text, msg):
    with pytest.raises(ScenarioError, match=msg):
        load_scenario(write(tmp_path, text))


@pytest.mark.parametrize("extra, msg", [
    ('[[step.screenshot]]\nview = "camera:nope"\n', "nope"),
    ('[[step.screenshot]]\nview = "free"\n', "lookat"),
    ('[[step.screenshot]]\nview = "free"\ncamera = { lookat = [0, 0, 0], distanse = 3 }\n', "distanse"),
    ('[step.contact_sheet]\nview = "free"\n', "contact_sheet supports follow, top and camera:<name>"),
    ('[[step.check]]\nalways = { signal = "hal:PWM/0/speed", min = 0 }\n', "direction prefix"),
    ('[[step.check]]\nat_end = { signal = "nt:SmartDashboard/x", equals = 1 }\n', "start with /"),
])
def test_validate_views_and_signals(tmp_path, world, extra, msg):
    s = load_scenario(write(tmp_path, _step(extra)))
    with pytest.raises(ScenarioError, match=msg):
        validate(s, world)


def test_unknown_camera_lists_model_cameras(tmp_path, world):
    s = load_scenario(write(tmp_path, _step('[[step.screenshot]]\nview = "camera:nope"\n')))
    with pytest.raises(ScenarioError, match="cameras|no cameras"):
        validate(s, world)


def test_always_min_max_reports_worst(world):
    s = stub(world)
    c = build_check({"always": {"signal": "battery", "min": 7.0, "max": 13.0}}, world, "x")
    c.begin(s)
    for t, v in enumerate((3.0, 13.5, 6.9)):
        world.vbatt = v
        c.tick(s, float(t))
    r = c.end(s)
    assert not r.passed and r.measured == 3.0 and r.first_failure_t == 0.0


def test_always_pass_reports_extreme(world):
    s = stub(world)
    c = build_check({"always": {"signal": "battery", "min": 7.0}}, world, "x")
    c.begin(s)
    for t, v in enumerate((12.0, 9.5, 11.0)):
        world.vbatt = v
        c.tick(s, float(t))
    r = c.end(s)
    assert r.passed and r.measured == 9.5


def test_nan_signal_fails(world):
    s = stub(world)
    c = build_check({"always": {"signal": "battery", "min": 7.0}}, world, "x")
    c.begin(s)
    world.vbatt = float("nan")
    c.tick(s, 1.0)
    assert not c.end(s).passed
    e = build_check({"at_end": {"signal": "battery", "max": 20.0}}, world, "x")
    assert not e.end(s).passed


def test_equals_is_type_aware_and_exclusive(world):
    with pytest.raises(ScenarioError, match="can't be combined"):
        build_check({"at_end": {"signal": "battery", "equals": 1, "min": 0}}, world, "x")
    with pytest.raises(ScenarioError, match="min .* greater than max"):
        build_check({"at_end": {"signal": "battery", "min": 5, "max": 1}}, world, "x")
    with pytest.raises(ScenarioError, match="negative"):
        build_check({"at_end": {"signal": "battery", "approx": 5, "tol": -1}}, world, "x")
    c = build_check({"at_end": {"signal": "hal:DIO/0/<value", "equals": 1}}, world, "x")
    s = stub(world)
    s.bridge.hal.devices[("DIO", "0")] = {"<value": True}
    assert not c.end(s).passed


def test_missing_hal_value_lists_devices(world):
    s = stub(world)
    s.bridge.hal.devices[("PWM", "0")] = {"<speed": 0.5}
    c = build_check({"at_end": {"signal": "hal:PWM/9/<speed", "min": 0}}, world, "x")
    r = c.end(s)
    assert not r.passed and "no HAL value hal:PWM/9/<speed; devices seen: PWM/0" in r.message


def test_console_not_contains_across_ticks(world):
    s = stub(world)
    c = build_check({"console": {"not_contains": "Exception"}}, world, "x")
    assert c.ticks
    c.begin(s)
    s.robot.push("ok", "Exception: boom")
    c.tick(s, 1.0)
    s.robot.push("fine")
    c.tick(s, 2.0)
    r = c.end(s)
    assert not r.passed and "Exception: boom" in r.message


def test_pose_zero_tolerance_honored(world):
    c = build_check({"pose": {"x": 4.0, "tol_xy": 0}}, world, "x")
    world.set_robot_pose(4.001, 4.0, 0.0)
    assert not c.end(stub(world)).passed


def _run_always(world, readings):
    s = stub(world)
    c = build_check({"always": {"signal": "hal:PWM/0/<speed", "min": 0.0}}, world, "x")
    c.begin(s)
    for t, v in enumerate(readings):
        s.bridge.hal.devices.clear()
        if v is not None:
            s.bridge.hal.devices[("PWM", "0")] = {"<speed": v}
        c.tick(s, float(t))
    return c.end(s)


def test_always_missing_then_numeric_failure(world):
    r = _run_always(world, [None, -5.0])
    assert not r.passed and r.measured == -5.0 and r.first_failure_t == 0.0
    assert "was -5 at" in r.message and "no value at 1 ticks" in r.message


def test_always_numeric_then_missing_keeps_worst(world):
    r = _run_always(world, [-5.0, None])
    assert r.measured == -5.0 and "at t=0.0" in r.message and r.first_failure_t == 0.0


def test_always_only_missing(world):
    r = _run_always(world, [None, None])
    assert not r.passed and r.measured is None and "no HAL value hal:PWM/0/<speed" in r.message


def test_odometry_error_max_must_be_positive(world):
    with pytest.raises(ScenarioError, match="greater than 0"):
        build_check({"odometry_error": {"max": 0}}, world, "x")


def test_camera_distance_must_be_positive():
    from scrimmage.render import check_camera
    with pytest.raises(ValueError, match="distance must be greater than 0"):
        check_camera({"lookat": [0, 0, 0], "distance": 0})


def test_tol_without_approx_is_an_error(world):
    with pytest.raises(ScenarioError, match="tol needs approx"):
        build_check({"at_end": {"signal": "battery", "min": 12.0, "tol": 0.1}}, world, "step 1")


def test_dotted_joint_and_motor_names_resolve():
    w = SimpleNamespace(motors=[SimpleNamespace(name="elevator.lift", current=2.5, command=0.5)],
                        joint_states=lambda: {"elevator.stage1": (0.3, 0.1)})
    s = SimpleNamespace(world=w)
    assert resolve_signal("joint.elevator.stage1.position", w).read(s) == 0.3
    assert resolve_signal("joint.elevator.stage1.velocity", w).read(s) == 0.1
    assert resolve_signal("motor.elevator.lift.current", w).read(s) == 2.5
    for bad in ("joint.elevator.stage1.pos", "joint.elevator.position", "motor.elevator.lift.volts"):
        with pytest.raises(ScenarioError, match="unknown signal"):
            resolve_signal(bad, w)


def test_load_scenario_text_resolves_config_relative_to_path(tmp_path):
    text = 'config = "../fake_robot.toml"\n[[step]]\nmode = "auto"\nduration = 1\n'
    sc = load_scenario_text(text, tmp_path / "scenarios" / "unsaved.toml")  # the file need not exist
    assert sc.config_path == (tmp_path / "fake_robot.toml").resolve()
    assert sc.path == (tmp_path / "scenarios" / "unsaved.toml").resolve()


def test_load_scenario_text_reports_toml_errors_with_the_path(tmp_path):
    with pytest.raises(ScenarioError, match="x.toml"):
        load_scenario_text("config = ", tmp_path / "x.toml")


def test_signal_messages_show_plain_numbers_not_numpy_reprs(world):
    import numpy as np
    s = stub(world)
    world.vbatt = np.float64(12.0)
    r = build_check({"at_end": {"signal": "battery", "min": 5}}, world, "x").end(s)
    assert r.passed and r.message == "battery = 12 at the end; expected >= 5", r.message
    assert type(r.measured) is float
    c = build_check({"always": {"signal": "battery", "min": 7.0}}, world, "x")
    c.begin(s)
    world.vbatt = np.float64(6.5)
    c.tick(s, 1.0)
    r = c.end(s)
    assert not r.passed and "battery was 6.5 at t=1.0" in r.message and "np." not in r.message, r.message
    assert type(r.measured) is float
    # text and booleans keep their quotes / spelling
    world.vbatt = True
    assert "battery = True at the end" in build_check({"at_end": {"signal": "battery", "equals": True}}, world, "x").end(s).message


def test_array_signal_values_show_as_plain_lists(world):
    import numpy as np
    s = stub(world)
    world.vbatt = np.array([1.5, np.float64(2.0), 3.25])
    r = build_check({"at_end": {"signal": "battery", "equals": "x"}}, world, "x").end(s)
    assert r.message.startswith("battery = [1.5, 2, 3.25] at the end"), r.message
    assert "array" not in r.message and "np." not in r.message
    assert r.measured == [1.5, 2.0, 3.25] and type(r.measured) is list and type(r.measured[0]) is float


def test_signal_messages_never_round_a_failing_value_onto_its_bound(world):
    s = stub(world)
    world.vbatt = 4.9999996
    r = build_check({"at_end": {"signal": "battery", "min": 5}}, world, "x").end(s)
    assert not r.passed and r.message == "battery = 4.9999996 at the end; expected >= 5", r.message
    world.vbatt = 12345678.0
    r = build_check({"at_end": {"signal": "battery", "max": 12345677.5}}, world, "x").end(s)
    assert r.message == "battery = 12345678 at the end; expected <= 12345677.5", r.message
    world.vbatt = [0.1, 7]
    assert "battery = [0.1, 7] at the end" in build_check({"at_end": {"signal": "battery", "equals": "x"}},
                                                         world, "x").end(s).message
