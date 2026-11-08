"""Floor zones a range declares (`[[zone]]`: 2024's wing, starting zone, stage zone) and the Test-tab condition
`in_zone` (the robot's center in or out of one): kickoff dry run gap 19."""

import tomllib
from pathlib import Path

import pytest

from range_fixtures import range_cfg
from scrimmage.blocks.conditions import Watch, describe
from scrimmage.blocks.program import Cond, Program, loads
from scrimmage.testrange.spec import RangeError, load_range

HEAD = '''
[range]
name = "t"
ball = "2026-fuel"
[[zone]]
name = "blue_start"
corners = [[0.0, 0.0], [1.93, 0.0], [1.93, 8.07], [0.0, 8.07]]
[[zone]]
name = "stage"
corners = [[3.0, 4.0], [5.0, 3.0], [5.0, 5.0]]
color = "red"
[start.home]
pose = [1.0, 4.0, 0.0]
'''


def load(tmp_path, text=HEAD):
    (tmp_path / "r.toml").write_text(text)
    return load_range("r.toml", tmp_path)


def test_zones_parse_and_contain_points(tmp_path):
    rng = load(tmp_path)
    start, stage = rng.zones
    assert start.name == "blue_start" and stage.name == "stage"
    assert start.contains(1.0, 4.0) and not start.contains(2.5, 4.0)
    assert stage.contains(4.5, 4.0) and not stage.contains(3.2, 4.8) and not stage.contains(6.0, 4.0)
    assert stage.color is not None


@pytest.mark.parametrize("zone, match", [
    ('name = "z"\ncorners = [[0.0, 0.0], [1.0, 0.0]]', "corners must be a list of at least 3"),
    ('name = "z"\ncorners = [[0.0, 0.0], [1.0, 0.0], [1.0, 9.5]]', "off the field"),
    ('name = "z"\ncorners = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]', "has no area"),
    ('name = "blue_start"\ncorners = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]', "two zones named 'blue_start'"),
    ('name = "z"\nwhere = 3\ncorners = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]', "unknown key 'where'"),
])
def test_bad_zones_are_refused(tmp_path, zone, match):
    with pytest.raises(RangeError, match=match):
        load(tmp_path, HEAD + f"[[zone]]\n{zone}\n")


def test_zones_are_drawn_as_tape_and_collide_with_nothing(tmp_path):
    from scrimmage.parts.assemble import assemble
    load(tmp_path)
    built = assemble(range_cfg(range={"file": "r.toml"}), tmp_path)
    m = built.model
    from scrimmage.testrange.zones import TAPE_HEIGHT
    tape = [g for g in range(m.ngeom) if int(m.geom_bodyid[g]) == 0 and not m.geom(g).name
            and m.geom_size[g][2] == pytest.approx(TAPE_HEIGHT / 2)]
    assert len(tape) == 4 + 3                                  # blue_start's 4 edges, stage's 3
    assert all(m.geom_contype[g] == 0 and m.geom_conaffinity[g] == 0 for g in tape)


def test_zone_tape_is_no_touch_target(tmp_path):
    # review I1: tape touches nothing, so a touch check on it could never pass; the Test tab mustn't offer it
    from scrimmage.blocks.palette import palette
    from scrimmage.world import World
    load(tmp_path)
    w = World(range_cfg(range={"file": "r.toml", "start": "home"}), tmp_path)
    assert not [n for n in palette(w, [])["targets"] if "zone" in n]
    assert not [n for n in w.contact_names() if "zone" in n]


def program(blocks=()) -> Program:
    return Program(Path("/tmp/x.test.toml"), Path("r.toml"), [], blocks=list(blocks))


def test_the_robot_in_a_zone(tmp_path):
    from scrimmage.world import World
    load(tmp_path)
    w = World(range_cfg(range={"file": "r.toml", "start": "home"}), tmp_path)
    watch = Watch(w, program())
    inside = Cond("in_zone", {"zone": "blue_start", "is": "in"})
    out = Cond("in_zone", {"zone": "blue_start", "is": "out"})
    assert watch.holds(inside) and not watch.holds(out)
    w.set_robot_pose(2.5, 4.0, 0.0)
    assert not watch.holds(inside) and watch.holds(out)
    assert watch.holds(Cond("in_zone", {"zone": "stage", "is": "out"}))
    w.set_robot_pose(4.6, 4.0, 90.0)
    assert watch.holds(Cond("in_zone", {"zone": "stage", "is": "in"}))


def test_in_zone_in_a_test_file_and_its_words(tmp_path):
    head = '[test]\nrobot = "r.toml"\n[[block]]\ndo = "expect"\n'
    p = loads(head + 'that = { in_zone = { zone = "blue_start", is = "out" } }\n', tmp_path / "x.test.toml")
    assert p.blocks[0].args["that"] == Cond("in_zone", {"zone": "blue_start", "is": "out"})
    assert loads(head + 'that = { in_zone = { zone = "w" } }\n', tmp_path / "y.test.toml").blocks[0].args["that"] == \
        Cond("in_zone", {"zone": "w", "is": "in"})
    from scrimmage.blocks.program import TestFileError
    with pytest.raises(TestFileError, match='is must be "in" or "out"'):
        loads(head + 'that = { in_zone = { zone = "w", is = "inside" } }\n', tmp_path / "z.test.toml")
    assert describe(Cond("in_zone", {"zone": "stage", "is": "in"})) == "the robot is in stage"
    assert describe(Cond("in_zone", {"zone": "blue_start", "is": "out"})) == "the robot is out of blue_start"


def test_a_zone_the_field_lacks_is_a_problem(tmp_path):
    from scrimmage.blocks.palette import palette, problems
    from scrimmage.world import World
    load(tmp_path)
    w = World(range_cfg(range={"file": "r.toml", "start": "home"}), tmp_path)
    assert palette(w, [])["zones"] == ["blue_start", "stage"]
    p = loads('[test]\nrobot = "r.toml"\n[[block]]\ndo = "expect"\nthat = { in_zone = { zone = "wing" } }\n',
              tmp_path / "x.test.toml")
    assert problems(p, w) == [{"place": [0], "message": "no zone 'wing' on this field; its zones: blue_start, stage"}]
    assert tomllib  # noqa: B018


def test_a_failed_zone_check_says_where_the_robot_was(tmp_path):
    from scrimmage.blocks.run import TestRun, world_for
    load(tmp_path)
    cfg = tmp_path / "robot.toml"
    from scrimmage.assembler.templates import new_config
    cfg.write_text(new_config("standin") + '\n[range]\nfile = "r.toml"\nstart = "home"\n')
    p = loads('[test]\nrobot = "robot.toml"\n[[block]]\ndo = "expect"\n'
              'that = { in_zone = { zone = "blue_start", is = "out" } }\n', tmp_path / "x.test.toml")
    r = TestRun(world_for(p), p).run()
    assert r.status == "check failed"
    assert "expected the robot out of blue_start, but its center was at (1.00 m, 4.00 m)" in r.checks[-1]["line"] \
        or "expected the robot out of blue_start, but its center was at" in r.outcome
