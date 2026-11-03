"""What a robot offers the block editor, and the problems a test's blocks have with it (scrimmage/blocks/palette.py)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from range_fixtures import range_cfg, shooter_cfg, spares_world
from scrimmage.assembler.templates import new_config
from scrimmage.blocks.palette import palette, problems
from scrimmage.blocks.program import loads
from scrimmage.world import World

HEAD = '[test]\nrobot = "r.toml"\n'
PATH = Path("/tmp/x.test.toml")
CLIMBER = {"name": "climber", "stock": "climber", "attach": "drive.top", "ids": {"extend": "PWM:2"}}


def standin(*parts) -> World:
    cfg = tomllib.loads(new_config("standin"))
    cfg["part"] += [dict(p) for p in parts]
    return World(cfg, None)


@pytest.fixture(scope="module")
def climber_world():
    return standin(CLIMBER)


def test_palette_of_the_standin_with_a_climber(climber_world):
    p = palette(climber_world, [])
    assert p["motors"] == [{"name": "climber.stage1", "unit": "in", "kind": "slide"},
                           {"name": "dt.left_2", "unit": "deg", "kind": "wheel"},
                           {"name": "dt.right_2", "unit": "deg", "kind": "wheel"}]
    assert p["drive"] is True
    joints = {j["name"]: j for j in p["joints"]}
    assert joints["climber.tip"] == {"name": "climber.tip", "unit": "in", "kind": "slide"}
    assert joints["climber.latch0"]["kind"] == "hinge" and joints["dt.left_1"]["kind"] == "wheel"
    assert [j["name"] for j in p["joints"]] == sorted(joints)
    assert p["parts"] == ["climber", "drive", "dt"]
    assert "drive.chassis" in p["bodies"] and "climber.tip" in p["bodies"] and p["bodies"] == sorted(p["bodies"])
    assert p["hooks"] == ["climber.hook0"]
    assert p["bars"] == p["elements"] == p["pieces"] == p["levels"] == []
    assert p["signals"] == ["tilt", "climber.stage1", "dt.left_2", "dt.right_2", "climber.stage1.current",
                            "dt.left_2.current", "dt.right_2.current", "hook_load"]


def test_touch_targets_are_the_fields_other_surfaces(climber_world):
    """A touch can name any field geom or body (the carpet, a wall): the palette lists them as targets, without
    `world` and without what bars, elements and pieces already list."""
    p = palette(climber_world, [])
    assert "carpet" in p["targets"] and "wall_left" in p["targets"] and "world" not in p["targets"]
    assert p["targets"] == sorted(p["targets"])
    w = World(range_cfg(range={"file": "ladder", "start": "below"}), None)
    p = palette(w, [])
    assert "carpet" in p["targets"] and "rail_left" in p["elements"]
    assert not [t for t in p["targets"] if t.startswith("element.") or t in p["bars"] or t in p["elements"]]
    # every target is a name problems() accepts on the field side
    prog = loads(HEAD + "".join(f'[[block]]\ndo = "wait_until"\nuntil = {{ touch = {{ robot = "drive.chassis", field = "{t}" }} }}\n'
                                for t in p["targets"]), PATH)
    assert problems(prog, w) == []


def test_touch_targets_on_a_game_piece_field_are_surfaces_a_student_knows():
    """On the 2026 range with a shooter robot (its hopper's slot balls) and floor balls: a hub is one target by its own
    name, never its numbered geoms, and no ball is a target (the floor balls are pieces)."""
    for w in (World(shooter_cfg(), None), spares_world(range_cfg())):
        p = palette(w, [])
        assert "hub_blue" in p["targets"] and "hub_red" in p["targets"] and "carpet" in p["targets"]
        assert not [t for t in p["targets"] if "." in t], p["targets"]
        balls = set(w.parts.layout.ball_names)
        assert balls and not balls & set(p["targets"])


def test_any_bar_is_a_touch_target_on_a_field_with_bars(climber_world):
    ladder = World(range_cfg(range={"file": "ladder", "start": "below"}), None)
    prog = loads(HEAD + '[[block]]\ndo = "wait_until"\nuntil = { touch = { robot = "drive.chassis", field = "any bar" } }\n',
                 PATH)
    assert problems(prog, ladder) == []
    assert [p["message"] for p in problems(prog, climber_world)] == ['"any bar" needs a field with bars']


def test_palette_of_a_robot_without_a_drivetrain_or_hooks(tmp_path):
    w = World({"part": [{"name": "drive", "stock": "chassis"}]}, tmp_path)
    p = palette(w, [])
    assert p["motors"] == [] and p["drive"] is False and p["hooks"] == []
    assert p["signals"] == ["tilt"]


def test_palette_of_the_ladder():
    w = World(range_cfg(range={"file": "ladder", "start": "below"}), None)
    p = palette(w, ["drive.chassis"])
    assert p["bars"] == ["rung1", "rung2", "rung3", "rung4"]
    assert p["elements"] == ["rail_left", "rail_right", "rung1", "rung2", "rung3", "rung4"]
    assert p["hooks"] == ["drive.chassis"] and p["signals"][-1] == "hook_load"
    assert p["levels"] == []


def test_pieces_are_the_field_balls_not_the_robots_slots():
    w = World(shooter_cfg(), None)
    assert w.parts.layout.ball_names and palette(w, [])["pieces"] == []      # four hopper slots, no floor balls
    w = spares_world(range_cfg())
    layout = w.parts.layout
    assert palette(w, [])["pieces"] == sorted(layout.ball_names[:layout.n_floor]) and layout.n_floor > 0


def test_palette_levels_on_a_range_that_scores_climbs():
    w = World(range_cfg(range={"file": "2022-hangar", "start": None}), None)
    assert palette(w, [])["levels"] == list(w.parts.range.climb.levels)
    assert palette(w, [])["levels"]


RENAMED = HEAD + '''
[[block]]
do = "spin"
motor = "climber.stage1"
volts = 6.0

[[block]]
do = "wait"
seconds = 1.0

[[block]]
do = "repeat"
times = 2

  [[block.body]]
  do = "spin"
  motor = "climber.crank"
  volts = 6.0

  [[block.body]]
  do = "stop"
  motor = "all"
'''


def test_a_renamed_joint_is_one_problem_at_its_place(climber_world):
    """Review Focus 1: a test written before the robot changed."""
    assert problems(loads(RENAMED, PATH), climber_world) == [
        {"place": [2, 0], "message": 'no joint "climber.crank" on this robot any more'}]


def test_a_program_that_fits_has_no_problems(climber_world):
    text = RENAMED.replace("climber.crank", "climber.stage1") + '''
[[block]]
do = "move"
joint = "climber.stage1"
to = 10.0
unit = "in"

[[block]]
do = "drive"
seconds = 1.0

[[block]]
do = "expect"
that = { tilt = { below = 20.0 } }
throughout = true
'''
    assert problems(loads(text, PATH), climber_world) == []


def _one(text, world):
    return [p["message"] for p in problems(loads(HEAD + text, PATH), world)]


def test_problem_messages(climber_world, tmp_path):
    w = climber_world
    assert _one('[[block]]\ndo = "spin"\nmotor = "climber.tip"\nvolts = 3.0\n', w) == [
        '"climber.tip" has no motor to move it']
    assert _one('[[block]]\ndo = "move"\njoint = "climber.stage1"\nto = 3.0\nunit = "deg"\n', w) == [
        '"climber.stage1" slides, so give it in inches or meters, not degrees']
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { joint = { name = "climber.latch0", above = 1.0, '
                'unit = "in" } }\n', w) == ['"climber.latch0" turns, so give it in degrees']
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { hanging_from = "rung5" }\n', w) == [
        'no bar "rung5" on this field']
    assert _one('[[block]]\ndo = "expect"\nthat = { level = "HIGH" }\n', w) == [
        "levels need a field that scores climbs"]
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { touch = { robot = "arm", field = "nowhere" } }\n', w) == [
        'no part or body "arm" on this robot any more', 'nothing called "nowhere" on this field']
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { piece_in = { body = "intake.hopper" } }\n', w) == [
        'no body "intake.hopper" on this robot any more', "this field has no game pieces"]
    bare = World({"part": [{"name": "drive", "stock": "chassis"}]}, tmp_path)
    assert _one('[[block]]\ndo = "drive"\ndistance = 1.0\n', bare) == [
        "drive needs a drivetrain; this robot has none"]
    assert _one('[[block]]\ndo = "wait_until"\nuntil = { touch = { robot = "hook", field = "next bar" } }\n',
                bare) == ["this robot has no hooks; say which bodies are hooks in the test's setup",
                          '"next bar" needs a field with bars']


def test_a_hook_that_is_no_body_is_a_setup_problem(climber_world):
    text = HEAD.replace('robot = "r.toml"\n', 'robot = "r.toml"\nhooks = ["arm.hook_A"]\n')
    assert problems(loads(text, PATH), climber_world) == [
        {"place": [], "message": 'no body "arm.hook_A" on this robot any more (the test lists it as a hook)'}]


# ---- stations, the hopper and the ball ----------------------------------------------------------------------------

def station_world(file="station", start="at_station", **range_extra) -> World:
    cfg = tomllib.loads(new_config("standin"))
    cfg["range"] = {"file": file, "start": start, **range_extra}
    return World(cfg, None)


def sprog(blocks="", hopper=(), setup="") -> object:
    head = HEAD + (f"hopper = {list(hopper)!r}\n".replace("'", '"') if hopper else "")
    return loads(head + blocks + setup, PATH)


def msgs(prog, w) -> list[str]:
    return [p["message"] for p in problems(prog, w)]


FEED = '[[block]]\ndo = "feed_one"\nstation = "{}"\n'
WAIT_HOPPER = '[[block]]\ndo = "wait_until"\nuntil = {{ in_hopper = {{ at_least = 1 }} }}\n'
WAIT_EMPTY = '[[block]]\ndo = "wait_until"\nuntil = {{ station_empty = "feeder" }}\n'
WAIT_MADE_IT = '[[block]]\ndo = "wait_until"\nuntil = {{ made_it_in = {{ at_least = 50 }} }}\n'


def test_palette_lists_stations_ball_choice_and_floor_piece():
    pal = palette(station_world(), [])
    assert pal["stations"] == ["feeder"] and pal["ball_choice"] and pal["ball"] == "2026-fuel"
    assert pal["station_settings"]["feeder"]["interval"] == 1.0 and not pal["floor_piece"]
    assert pal["station_settings"]["feeder"]["stock"] == 0 and "2014-ball" in pal["balls"]
    assert pal["drive_kind"] == "tank" and pal["drive"] is True
    on_intake = palette(station_world("intake", "facing_ball"), [])
    assert on_intake["floor_piece"] is True and on_intake["ball_choice"] and on_intake["stations"] == []
    assert palette(station_world("station", "facing_ball"), [])["floor_piece"] is True
    on_2026 = palette(World(range_cfg(), None), [])
    assert on_2026["ball_choice"] is False and on_2026["floor_piece"] is False
    assert on_2026["stations"] == ["outpost_blue", "outpost_red"]     # its OUTPOSTS


def test_feed_problems():
    w = station_world()
    assert msgs(sprog(FEED.format("nope"), ["drive.chassis"]), w) == ['no station "nope" on this field']
    assert msgs(sprog(FEED.format("feeder")), w) == []
    assert msgs(sprog(WAIT_HOPPER.format()), w) == ["pick the hopper in Setup: the bodies a piece lands in"]
    assert msgs(sprog(WAIT_HOPPER.format(), ["drive.chassis"]), w) == []
    assert msgs(sprog(WAIT_MADE_IT.format()), w) == ["pick the hopper in Setup: the bodies a piece lands in"]
    assert msgs(sprog(WAIT_MADE_IT.format(), ["drive.chassis"]), w) == []
    assert '"feeder" never runs out' in msgs(sprog(WAIT_EMPTY.format(), ["drive.chassis"]), w)[0]
    stocked = '\n[setup.station.feeder]\nstock = 3\n'
    assert msgs(sprog(WAIT_EMPTY.format(), ["drive.chassis"], stocked), w) == []
    left = '[[block]]\ndo = "drive"\nseconds = 1\ndirection = "left"\n'
    assert msgs(sprog(left), w) == ["a tank drive can't drive sideways; turn, then drive forward"]
    turn = '[[block]]\ndo = "turn"\ndegrees = 90\n'
    assert msgs(sprog(turn), w) == []


def test_turn_needs_a_drivetrain():
    from types import SimpleNamespace
    from scrimmage.blocks.palette import _block_problems
    block = sprog('[[block]]\ndo = "turn"\ndegrees = 90\n').blocks[0]
    assert _block_problems(SimpleNamespace(p={"drive_kind": None}), block) == [
        "turn needs a drivetrain; this robot has none"]


def test_setup_problems_at_place_nothing():
    w = station_world()
    assert msgs(sprog(hopper=["nope.body"]), w) == ['no body "nope.body" on this robot any more (the test lists it as the hopper)']
    assert msgs(sprog(setup='\n[setup.station.nope]\nstock = 1\n'), w) == [
        'Setup names a station this field doesn\'t have: "nope"']
    assert msgs(sprog(setup='\n[setup.floor_spread]\noffset_in = [-3, 3]\n'), w) == [
        "the floor spread needs a ball on the floor in front of the robot's start"]
    assert msgs(sprog(setup='\n[setup.floor_spread]\noffset_in = [-3, 3]\n'), station_world("station", "facing_ball")) == []
    on_2026 = World(range_cfg(), None)
    assert msgs(sprog(setup='\n[setup]\nball = "2014-ball"\n'), on_2026) == [
        "this field keeps its own ball; pick a ball on the intake or station field"]
    assert msgs(sprog(setup='\n[setup]\nball = "2014-ball"\n'), station_world("2013", "auto_line_left")) == [
        "this field has no ball to pick; clear the ball in Setup"]


def test_an_unknown_station_in_setup_leaves_the_others_settings_alone():
    """One station the field doesn't have is reported on its own: the field's station keeps the test's stock."""
    setup = '\n[setup.station.feeder]\nstock = 3\n\n[setup.station.nope]\nstock = 1\n'
    assert msgs(sprog(WAIT_EMPTY.format(), ["drive.chassis"], setup), station_world()) == [
        'Setup names a station this field doesn\'t have: "nope"']


def test_setup_on_the_plain_field_says_what_to_clear():
    plain = World(tomllib.loads(new_config("standin")), None)
    assert msgs(sprog(setup='\n[setup]\nseed = 4\n'), plain) == []
    assert msgs(sprog(setup='\n[setup]\nball = "2014-ball"\n'), plain) == [
        "the plain field has no ball to pick; clear the ball in Setup"]
    assert msgs(sprog(setup='\n[setup.station.feeder]\nstock = 1\n'), plain) == [
        'Setup names a station this field doesn\'t have: "feeder"']


def test_review_focus_1_a_ball_that_doesnt_fit_is_a_setup_problem(tmp_path):
    text = (Path(__file__).resolve().parent.parent / "scrimmage/testrange/ranges/station.toml").read_text()
    text = text.replace("chute = { length = 0.5, slope_deg = 30.0 }", "chute = { length = 0.5, slope_deg = 30.0, width = 0.25 }")
    text = text.replace("release = [0.3, 4.0, 1.1]", "release = [1.0, 4.0, 1.1]")    # clear of the wall for a big ball
    text = text.replace("pose = [1.183, 4.0, 180.0]", "pose = [1.883, 4.0, 180.0]")      # moved with the release
    (tmp_path / "station.toml").write_text(text)
    w = station_world(str(tmp_path / "station.toml"))
    p = sprog(setup='\n[setup]\nball = "2014-ball"\n')
    found = problems(p, w)
    assert found[0]["place"] == [] and "no wider than the ball" in found[0]["message"]
    assert str(tmp_path) not in found[0]["message"]
    p.setup["ball"] = "2026-fuel"
    assert problems(p, w) == []


# ---- piece kinds, rods and grips ------------------------------------------------------------------------------------

REEF = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "reef"


def test_palette_lists_kinds_presets_rods_and_grips():
    """The reef field with the grabber: the default kind's preset, every ball and piece preset, the field's kinds
    (with the plain name a student reads), what each floor piece and station is, the rods, and a grip per body of a
    path part that touches anything (the grabber's two jaws; the mast and arm touch nothing), with the materials and
    their friction estimates."""
    from scrimmage.config import build_world, load_config
    from scrimmage.parts.grip import GRIP_MATERIALS
    pal = palette(build_world(load_config(REEF / "grabber-standin.toml")), [])
    assert pal["piece"] == "2025-coral" and "2015-tote" in pal["piece_presets"]["pieces"] and pal["rods"]
    assert pal["piece_presets"]["pieces"] == sorted(pal["piece_presets"]["pieces"])
    assert "2014-ball" in pal["piece_presets"]["balls"] and "2014-ball" not in pal["piece_presets"]["pieces"]
    assert pal["kinds"] == {"2025-coral": {"form": "rigid", "hollow": True, "flat": False, "label": "coral"}}
    assert pal["floor_kinds"] == {"ball.0": "2025-coral"} and pal["station_kinds"] == {"coral_station": "2025-coral"}
    assert pal["grips"] == {"grabber.jaw_left": None, "grabber.jaw_right": None}
    assert pal["grip_materials"] == [[k, label, mu] for k, (label, mu) in GRIP_MATERIALS.items()]
    assert ["rubber", "Rubber", 0.8] in pal["grip_materials"]
    # the old keys stay
    assert pal["ball"] is None and "2014-ball" in pal["balls"] and pal["ball_choice"] is False


def test_the_grips_show_a_parts_own_material_and_skip_stock_parts(tmp_path):
    import shutil

    from scrimmage.config import build_world, load_config
    from scrimmage.parts.grip import set_grip
    shutil.copytree(REEF, tmp_path / "reef")
    set_grip(tmp_path / "reef" / "parts" / "grabber", "jaw_left", "nitrile")
    pal = palette(build_world(load_config(tmp_path / "reef" / "grabber-standin.toml")), [])
    assert pal["grips"] == {"grabber.jaw_left": "nitrile", "grabber.jaw_right": None}
    assert palette(station_world(), [])["grips"] == {}       # the stand-in's chassis and drive are stock parts


def test_a_ball_fields_kinds_and_the_plain_field():
    pal = palette(station_world("intake", "facing_ball"), [])
    assert pal["piece"] == pal["ball"] == "2026-fuel"
    (name, kind), = pal["kinds"].items()
    assert kind["form"] == "ball" and not kind["hollow"] and pal["floor_kinds"] == {"ball.0": name}
    plain = palette(World(tomllib.loads(new_config("standin")), None), [])
    assert plain["piece"] is None and plain["kinds"] == plain["floor_kinds"] == plain["station_kinds"] == {}
    assert plain["piece_presets"]["pieces"] and plain["grip_materials"]


def test_review_focus_2_a_piece_that_doesnt_fit_is_a_setup_problem():
    """A tote fed from the station field's chute: its release point is too near the wall for the tote's size (a
    setup problem at place [], the file's path left out); a coral fits."""
    w = station_world()
    p = sprog(setup='\n[setup]\npiece = "2015-tote"\n')
    found = problems(p, w)
    assert found[0]["place"] == [] and found[0]["message"].startswith(
        "[[station]] 'feeder' release point (0.3, 4) lies off the field"), found
    assert "station.toml" not in found[0]["message"]
    p.setup["piece"] = "2025-coral"
    assert problems(p, w) == []
    p.setup.pop("piece")
    assert problems(p, w) == []


def test_a_piece_in_setup_on_a_field_that_keeps_its_own():
    assert msgs(sprog(setup='\n[setup]\npiece = "2025-coral"\n'), World(range_cfg(), None)) == [
        "this field keeps its own pieces; pick a piece on the intake or station field"]
    plain = World(tomllib.loads(new_config("standin")), None)
    assert msgs(sprog(setup='\n[setup]\npiece = "2025-coral"\n'), plain) == [
        "the plain field has no piece to pick; clear the piece in Setup"]
    assert msgs(sprog(setup='\n[setup]\npiece = "2025-coral"\n'), station_world("intake", "facing_ball")) == []


def test_a_setup_piece_reaches_the_world():
    """[setup] piece goes in the config's [range] piece: the intake field's floor piece is a coral."""
    from dataclasses import replace

    from scrimmage.blocks.run import setup_config
    from scrimmage.config import build_world, load_config
    config = load_config(Path(__file__).resolve().parent.parent / "examples/blocks/intake/intake-standin.toml")
    raw = setup_config(config.raw, {"piece": "2025-coral"})
    assert raw["range"]["piece"] == "2025-coral"
    pal = palette(build_world(replace(config, raw=raw)), [])
    assert pal["piece"] == "2025-coral" and list(pal["floor_kinds"].values()) == list(pal["kinds"])
    assert next(iter(pal["kinds"].values()))["label"] == "coral"
