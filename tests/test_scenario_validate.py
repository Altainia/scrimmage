from pathlib import Path

import mujoco
import pytest

from scrimmage.assembler import scenario_validate as sv
from scrimmage.assembler.signals import signals
from scrimmage.assembler.view import model_view

ROOT = Path(__file__).resolve().parent.parent
PATH = ROOT / "scenarios" / "x.toml"
HEAD = 'config = "../configs/kitbot.toml"\n'
STEP = '\n[[step]]\nname = "A"\nmode = "auto"\nduration = 1.0\n'


def fields(text, path=PATH):
    r = sv.check(text, path)
    return r, [e.field for e in r.errors]


def test_real_scenarios_are_ok():
    for f in (ROOT / "scenarios").glob("*.toml"):
        r = sv.check(f.read_text(), f)
        assert r.ok, (f, r.errors)
        assert r.scenario is not None and r.world is not None and r.robot_problem is None


@pytest.mark.parametrize("text,field", [
    (HEAD + STEP.replace('"auto"', '"bogus"'), "step0/mode"),
    (HEAD + STEP.replace("1.0", "-1"), "step0/duration"),
    (HEAD + STEP + "foo = 1\n", "step0/foo"),
    (HEAD + "bar = 1\n" + STEP, "scenario/bar"),
    (HEAD + 'speed = "fast"\n' + STEP, "scenario/speed"),
    ('description = "x"\n' + STEP, "scenario/config"),
    (HEAD + STEP + "axes = { 0 = [2.0] }\n", "step0/axes"),
    (HEAD + STEP + '  [[step.screenshot]]\n  at = 5.0\n', "step0/shot0/at"),
    (HEAD + STEP + '  [[step.screenshot]]\n  width = 3\n', "step0/shot0/width"),
    (HEAD + STEP + '  [[step.screenshot]]\n  at = "end"\n  [[step.screenshot]]\n  at = -1\n', "step0/shot1/at"),
    (HEAD + STEP + '  [step.contact_sheet]\n  frames = 99\n', "step0/sheet/frames"),
    (HEAD + STEP + '  [step.contact_sheet]\n  view = "free"\n', "step0/sheet/view"),
    (HEAD + STEP + '  [[step.check]]\n  pose = { x = 1 }\n  at_end = { signal = "battery", min = 1 }\n',
     "step0/check0/check"),
    (HEAD, "status"),
    ("this is = not toml [", "status"),
    (HEAD + '[match]\nstation = "red4"\n' + STEP, "scenario/match.station"),
    (HEAD + '[match]\nauto_seconds = -1\n' + STEP, "scenario/match.auto_seconds"),
    (HEAD + '[match]\nalliance = "red"\n' + STEP, "scenario/match"),
    (HEAD + STEP + "game_data = 3\n", "step0/game_data"),
    (HEAD + STEP + 'game_data = "' + "x" * 65 + '"\n', "step0/game_data"),
])
def test_load_errors_map_to_fields(text, field):
    r, fs = fields(text)
    assert not r.ok and fs == [field], (fs, [e.message for e in r.errors])


def test_second_step_index_is_zero_based():
    text = HEAD + STEP + STEP.replace('"A"', '"B"').replace('"auto"', '"nope"')
    assert fields(text)[1] == ["step1/mode"]


def test_unnamed_step():
    text = HEAD + '\n[[step]]\nmode = "x"\nduration = 1\n'
    assert fields(text)[1] == ["step0/mode"]


def test_unknown_signal_and_pose():
    text = HEAD + STEP + ('  [[step.check]]\n  pose = { x = 1.0 }\n'
                          '  [[step.check]]\n  always = { signal = "motor.zzz.current", min = 0 }\n'
                          '  [[step.check]]\n  pose = { tol_xy = -1, x = 1 }\n'
                          '  [[step.check]]\n  pose = { y = "a" }\n'
                          '  [[step.check]]\n  pose = { }\n')
    r, fs = fields(text)
    assert fs == ["step0/check1/always.signal", "step0/check2/pose.tol_xy", "step0/check3/pose.y",
                  "step0/check4/pose"]


def test_unknown_contact_and_bad_values():
    text = HEAD + STEP + ('  [[step.check]]\n  never_touch = "nothing"\n'
                          '  [[step.check]]\n  at_end = { signal = "battery", min = 5, max = 1 }\n'
                          '  [[step.check]]\n  console = { contains = "a", bogus = 1 }\n')
    r, fs = fields(text)
    assert fs[0] == "step0/check0/never_touch" and fs[1] == "step0/check1/at_end.min"
    assert fs[2].startswith("step0/check2/console")


def test_bad_screenshot_view_and_camera():
    text = HEAD + STEP + ('  [[step.screenshot]]\n  view = "camera:nope"\n'
                          '  [[step.screenshot]]\n  view = "free"\n'
                          '  [[step.screenshot]]\n  view = "sideways"\n'
                          '  [[step.screenshot]]\n  view = "top"\n  camera = { zoom = 1 }\n'
                          '  [step.contact_sheet]\n  view = "camera:nope"\n')
    r, fs = fields(text)
    assert fs == ["step0/shot0/view", "step0/shot1/camera", "step0/shot2/view", "step0/shot3/camera",
                  "step0/sheet/view"]


def test_all_errors_reported_not_just_first():
    text = HEAD + STEP + '  [[step.check]]\n  never_touch = "a"\n  [[step.check]]\n  never_touch = "b"\n'
    assert len(fields(text)[1]) == 2


def test_missing_config_file():
    r, fs = fields('config = "../configs/nope.toml"\n' + STEP)
    assert not r.ok and fs == ["status"] and r.robot_problem
    assert r.errors[0].message.startswith("fix the robot first: ")


def test_invalid_robot_config(tmp_path):
    (tmp_path / "robot.toml").write_text("this is [ not toml")
    r, fs = fields('config = "robot.toml"\n' + STEP, tmp_path / "s.toml")
    assert not r.ok and fs == ["status"] and "fix the robot first" in r.errors[0].message
    assert r.robot_problem and r.world is None


def test_signals_kitbot():
    r = sv.check((ROOT / "scenarios" / "kitbot_auto.toml").read_text(), ROOT / "scenarios" / "kitbot_auto.toml")
    s = signals(r.world)
    assert s["fixed"] == ["battery", "speed", "robot.ground_gap", "robot.wheels_down", "robot.air_time", "robot.pitch",
                          "robot.roll", "robot.tilt", "robot.z", "robot.stranded", "robot.tipped"]
    assert "motor.dt.left" in s["motors"] and "joint.dt.left_2" in s["joints"]
    assert "obstacle" in s["contacts"] and isinstance(s["cameras"], list)


def test_world_geoms():
    r = sv.check((ROOT / "scenarios" / "kitbot_auto.toml").read_text(), ROOT / "scenarios" / "kitbot_auto.toml")
    base = model_view(r.world)
    assert "world_geoms" not in base
    v = model_view(r.world, include_world=True)
    assert {k: v[k] for k in base} == base
    by = {g["name"]: g for g in v["world_geoms"]}
    assert "carpet" in by and "obstacle" in by
    assert by["carpet"]["type"] == "plane" and len(by["carpet"]["size"]) == 2
    assert by["obstacle"]["type"] == "box" and len(by["obstacle"]["pos"]) == 3
    assert not any(g["name"] == "" for g in v["world_geoms"])


def test_step_ten_does_not_match_step_one():
    text = HEAD + STEP * 9 + STEP.replace('"A"', '"J"').replace('"auto"', '"nope"')
    assert fields(text)[1] == ["step9/mode"]
    text = HEAD + STEP * 9 + STEP.replace('"A"', '"J"') + '  [[step.screenshot]]\n  at = 9\n'
    assert fields(text)[1] == ["step9/shot0/at"]
    text = HEAD + STEP * 10 + "foo = 1\n"
    assert fields(text)[1] == ["step9/foo"]


# ---- final review: every load error counts (R4) ------------------------------------------------------------

def test_load_errors_in_every_step_are_listed():
    text = (HEAD + 'speed = "fast"\n' + STEP.replace('"auto"', '"bogus"') + STEP.replace("1.0", "-1")
            + STEP + '  [[step.check]]\n  at_end = { signal = "battery", min = 5, bogus = 1 }\n'
            + STEP + "foo = 1\n")
    r, fs = fields(text)
    assert not r.ok and r.scenario is None
    assert fs == ["scenario/speed", "step0/mode", "step1/duration", "step2/check0/at_end.bogus", "step3/foo"], \
        [e.message for e in r.errors]
    # messages keep the real 1-based step numbers
    assert "step 2 (A)" in r.errors[2].message and "step 4:" in r.errors[4].message


def test_a_new_load_error_does_not_hide_a_world_error():
    one = HEAD + STEP + STEP + STEP + '  [[step.check]]\n  at_end = { signal = "battery", min = 5, bogus = 1 }\n'
    assert fields(one)[1] == ["step2/check0/at_end.bogus"]
    two = one.replace("duration = 1.0", "duration = -1", 1)
    assert fields(two)[1] == ["step0/duration", "step2/check0/at_end.bogus"]


def test_no_steps_and_a_top_error_are_both_listed():
    assert fields('speed = "fast"\n')[1] == ["scenario/config", "status"]


def test_a_bad_robot_with_load_errors_still_lists_the_load_errors(tmp_path):
    (tmp_path / "robot.toml").write_text("this is [ not toml")
    r, fs = fields('config = "robot.toml"\n' + STEP.replace('"auto"', '"x"') + STEP.replace("1.0", "0"),
                   tmp_path / "s.toml")
    assert fs == ["step0/mode", "step1/duration"] and r.robot_problem is None


def test_missing_inputs_csv_maps_to_the_step_field():
    r, fs = fields(HEAD + STEP + 'inputs = "gone.csv"\n')
    assert fs == ["step0/inputs"] and "file not found" in r.errors[0].message
    r, fs = fields(HEAD + STEP + 'inputs = "gone.csv"\naxes = { 0 = [1.0] }\n')
    assert fs == ["step0/inputs"]


def test_an_unnamed_steps_world_error_names_the_step_once():
    """Cleanup D7: an unnamed step's world error says "step 3", not "step 3 (step 3)"; a named step keeps its name."""
    from scrimmage.config import build_world, load_config
    from scrimmage.scenario import ScenarioError, load_scenario_text, validate
    world = build_world(load_config(ROOT / "configs" / "kitbot.toml"))
    unnamed = STEP.replace('name = "A"\n', "")
    bad = '  [[step.screenshot]]\n  view = "nope"\n'
    for text, where in ((HEAD + STEP + unnamed + unnamed + bad, "x.toml, step 3, screenshot 1: "),
                        (HEAD + STEP + unnamed + STEP + bad, "x.toml, step 3 (A), screenshot 1: ")):
        with pytest.raises(ScenarioError) as e:
            validate(load_scenario_text(text, PATH), world)
        assert str(e.value).startswith(where + "view must be"), str(e.value)
