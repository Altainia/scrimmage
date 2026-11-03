"""Climbing elements: bars (optionally on a sprung slide), boxes and a tilting switch, the climbing ranges built from
them, and their [climb] scoring rule."""

from __future__ import annotations

import math

import mujoco
import numpy as np
import pytest

from range_fixtures import range_cfg
from scrimmage.parts.field import FIELD_BITS
from scrimmage.testrange.build_elements import ELEMENT_GROUP, NON_GROUND_GROUP, add_element
from scrimmage.testrange.elements import IN, Bar, Box, ClimbRule, Switch
from scrimmage.testrange.spec import RangeError, load_range, parse_range
from scrimmage.world import World

FLOOR_PROTECTION = 0.375 * IN   # 2022 manual 5.4.3: the MID, HIGH and TRAVERSAL heights are above it
SWERVE = [{"name": "drive", "stock": "chassis"}] + [
    {"name": c, "stock": "swerve/MK4i", "attach": f"drive.{m}",
     "ids": {"drive": f"PWM:{i}", "steer": f"PWM:{i + 4}"}}
    for i, (c, m) in enumerate((("fl", "front_left"), ("fr", "front_right"), ("bl", "back_left"),
                                ("br", "back_right")))]


def groups(*gs) -> np.ndarray:
    return np.array([1 if g in gs else 0 for g in range(6)], dtype=np.uint8)


def ray_down(m, d, x, y, group, top=4.0) -> float | None:
    """The height of the first geom in `group` under (x, y), or None."""
    gid = np.array([-1], dtype=np.int32)
    dist = mujoco.mj_ray(m, d, np.array([x, y, top]), np.array([0.0, 0.0, -1.0]), groups(group), 1, -1, gid)
    return None if dist < 0.0 else top - dist


def element_world(*elements):
    spec = mujoco.MjSpec()
    spec.option.timestep = 0.001
    spec.worldbody.add_geom(name="carpet", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[10, 10, 0.1], group=ELEMENT_GROUP,
                            **FIELD_BITS)
    for e in elements:
        add_element(spec, e)
    m = spec.compile()
    return m, mujoco.MjData(m)


def hangar_bars() -> dict[str, Bar]:
    return {e.level: e for e in load_range("2022-hangar", ".").elements if isinstance(e, Bar)}


def test_hangar_rung_heights_and_spacing():
    rng = load_range("2022-hangar", ".")
    bars = hangar_bars()
    assert set(bars) == {"LOW", "MID", "HIGH", "TRAVERSAL"}
    m, d = element_world(*rng.elements)
    mujoco.mj_forward(m, d)
    tops = {"LOW": 48.72 * IN, "MID": 60.26 * IN + FLOOR_PROTECTION, "HIGH": 75.59 * IN + FLOOR_PROTECTION,
            "TRAVERSAL": 90.94 * IN + FLOOR_PROTECTION}
    center = {}
    for level, b in bars.items():
        assert b.diameter == pytest.approx(1.66 * IN)
        assert ray_down(m, d, *b.center, NON_GROUND_GROUP) == pytest.approx(tops[level], abs=0.0005)
        center[level] = d.geom_xpos[m.geom(f"element.{b.name}.pipe").id].copy()
    for lo, hi in (("MID", "HIGH"), ("HIGH", "TRAVERSAL")):
        step = center[hi] - center[lo]
        assert np.hypot(*step[:2]) == pytest.approx(24.00 * IN, abs=0.01 * IN)
        assert step[2] == pytest.approx(15.34 * IN, abs=0.02 * IN)
    assert rng.climb == ClimbRule(rule="lowest-rung", levels={"LOW": 4.0, "MID": 6.0, "HIGH": 10.0, "TRAVERSAL": 15.0})
    assert {lv: b.points for lv, b in bars.items()} == {"LOW": 4.0, "MID": 6.0, "HIGH": 10.0, "TRAVERSAL": 15.0}
    assert all(b.stiffness == pytest.approx(160e3) and b.damping > 0.0 for b in bars.values())
    protection = [e for e in rng.elements if isinstance(e, Box) and e.ground]
    assert len(protection) == 1 and protection[0].size[2] == pytest.approx(FLOOR_PROTECTION)
    assert any(isinstance(e, Box) and not e.ground for e in rng.elements)          # the truss
    assert "under_mid" in rng.starts


def test_bars_are_not_ground():
    rng = load_range("2022-hangar", ".")
    low = hangar_bars()["LOW"]
    w = World(range_cfg(part=SWERVE, range={"file": "2022-hangar"},
                        robot={"start_pose": [low.center[0] + 0.35, low.center[1], 0.0]}), None)
    m, d = w.model, w.data
    for e in rng.elements:
        gids = [g for g in range(m.ngeom) if m.body(m.geom_bodyid[g]).name.startswith(f"element.{e.name}")]
        assert gids
        want = ELEMENT_GROUP if isinstance(e, Box) and e.ground else NON_GROUND_GROUP
        assert all(m.geom_group[g] == want for g in gids), e.name
        assert all(m.geom_contype[g] == FIELD_BITS["contype"] and m.geom_conaffinity[g] == FIELD_BITS["conaffinity"]
                   for g in gids)
    mujoco.mj_forward(m, d)
    assert ray_down(m, d, *low.center, ELEMENT_GROUP) == pytest.approx(0.0, abs=1e-9)   # through the bar: carpet
    mon = w.terrain_monitor()
    for _ in range(300):
        w.step(_idle())
    s = mon.sample()
    assert s["robot.ground_gap"] == 0.0 and s["robot.wheels_down"] == 4


def _idle():
    from scrimmage.protocol import HalState
    return HalState()


def test_compliant_bar_deflects_by_load_over_stiffness():
    bar = hangar_bars()["MID"]
    m, d = element_world(bar)
    flex = m.joint(f"element.{bar.name}.flex")
    q, v = int(flex.qposadr[0]), int(flex.dofadr[0])
    body = m.body(f"element.{bar.name}").id
    for _ in range(500):                  # unloaded, its own weight: rests at 0 (the spring's rest takes it)
        mujoco.mj_step(m, d)
    assert d.qpos[q] == pytest.approx(0.0, abs=2e-5)
    assert m.jnt_range[flex.id] == pytest.approx([-0.05, 0.05])
    for _ in range(1500):
        d.qfrc_applied[:] = 0.0
        mujoco.mj_applyFT(m, d, np.array([0.0, 0.0, -600.0]), np.zeros(3), d.xpos[body].copy(), body,
                          d.qfrc_applied)
        mujoco.mj_step(m, d)
    assert -d.qpos[q] == pytest.approx(600.0 / bar.stiffness, rel=0.2)
    assert abs(d.qvel[v]) < 1e-3


def switch() -> Switch:
    return next(e for e in load_range("2020-switch", ".").elements if isinstance(e, Switch))


def test_switch_tilts_to_stop_and_levels():
    s = switch()
    assert s.stop_deg == pytest.approx(14.5) and s.level_deg == pytest.approx(8.0)
    assert s.mass == pytest.approx(42.2, abs=0.1) and s.com_drop == pytest.approx(0.66, abs=0.005)
    m, d = element_world(s)
    j = m.joint(f"element.{s.name}.tilt")
    q = int(j.qposadr[0])
    body = m.body(f"element.{s.name}").id
    c, sn = math.cos(math.radians(s.yaw_deg)), math.sin(math.radians(s.yaw_deg))
    for _ in range(2000):                 # 600 N hanging 1.2 m off center along the bar
        d.qfrc_applied[:] = 0.0
        at = np.array([s.center[0] + 1.2 * c, s.center[1] + 1.2 * sn, 1.5])
        mujoco.mj_applyFT(m, d, np.array([0.0, 0.0, -600.0]), np.zeros(3), at, body, d.qfrc_applied)
        mujoco.mj_step(m, d)
    assert abs(math.degrees(d.qpos[q])) == pytest.approx(s.stop_deg, abs=0.5)
    d.qfrc_applied[:] = 0.0
    swing = []
    for _ in range(9000):                 # let go: it swings back through level, the swing dying away
        mujoco.mj_step(m, d)
        swing.append(abs(math.degrees(d.qpos[q])))
    assert max(swing[:3000]) > s.level_deg        # not overdamped: the first swing passes the level band
    assert max(swing[6000:]) < s.level_deg        # a full period (about 3 s) within it after 6 s
    m2, d2 = element_world(s)             # unloaded, it hangs level, the bar top at its height
    for _ in range(500):
        mujoco.mj_step(m2, d2)
    assert abs(math.degrees(d2.qpos[q])) < 1e-6
    assert ray_down(m2, d2, *s.center, NON_GROUND_GROUP) == pytest.approx(63.0 * IN, abs=0.002)


CLIMB_RANGE = """
[range]
name = "t"
ball = "2026-fuel"

[[element]]
name = "a"
kind = "bar"
center = [3.0, 2.0]
length = 2.0
diameter = 0.04
height = 1.2
level = "ONE"

[[element]]
name = "sw"
kind = "switch"
center = [8.0, 4.0]
hinge_height = 3.0
bar_length = 3.0
bar_drop = 1.3
mass = 40.0
com_drop = 0.6
stop_deg = 14.5
damping = 20.0
level_deg = 8.0
"""


def _parse(climb: str, body: str = CLIMB_RANGE):
    import tomllib
    from pathlib import Path
    return parse_range(tomllib.loads(body + climb), Path("t.toml"))


def test_climb_rule_parses():
    r = _parse('[climb]\nrule = "lowest-rung"\nlevels = { ONE = 4, TWO = 6 }\n')
    assert r.climb == ClimbRule(rule="lowest-rung", levels={"ONE": 4.0, "TWO": 6.0})
    assert next(e for e in r.elements if e.name == "a").points == 4.0
    r = _parse('[climb]\nrule = "hang"\nlevels = { ONE = 25 }\nlevel_bonus = 15\nswitch = "sw"\n')
    assert r.climb == ClimbRule(rule="hang", levels={"ONE": 25.0}, level_bonus=15.0, switch="sw")
    r = _parse('[climb]\nrule = "lowest-point"\nlevels = { ONE = 10 }\n')
    assert r.climb.rule == "lowest-point"
    assert _parse("", CLIMB_RANGE.replace('level = "ONE"\n', "")).climb is None
    for bad, words in (('rule = "highest"\nlevels = { ONE = 4 }', ("unknown rule 'highest'", "lowest-rung")),
                       ('rule = "lowest-rung"\nlevels = { TWO = 4 }', ("level 'ONE'", "TWO")),
                       ('rule = "lowest-rung"\nlevels = { ONE = 6, TWO = 4 }', ("lowest first",)),
                       ('rule = "hang"\nlevels = { ONE = 25 }\nlevel_bonus = 15', ("switch",)),
                       ('rule = "hang"\nlevels = { ONE = 25 }\nlevel_bonus = 15\nswitch = "a"', ("isn't a switch",)),
                       ('rule = "lowest-rung"\nlevels = { ONE = 4 }\nswitch = "sw"', ("only", "hang"))):
        with pytest.raises(RangeError) as e:
            _parse("[climb]\n" + bad + "\n")
        assert all(w in str(e.value) for w in words), str(e.value)
    with pytest.raises(RangeError, match="no \\[climb\\]"):
        _parse("")


def test_bar_box_switch_keys_checked():
    with pytest.raises(RangeError, match="damping.*stiffness"):
        _parse("", CLIMB_RANGE.replace('level = "ONE"\n', "damping = 50.0\n"))
    with pytest.raises(RangeError, match="ground must be true or false"):
        _parse("", CLIMB_RANGE.replace('level = "ONE"\n', "")
               + '[[element]]\nname = "b"\nkind = "box"\ncenter = [5.0, 5.0]\nsize = [1.0, 1.0, 0.1]\nground = 1\n')
    r = _parse("", CLIMB_RANGE.replace('level = "ONE"\n', "")
               + '[[element]]\nname = "b"\nkind = "box"\ncenter = [5.0, 5.0]\nsize_in = [10.0, 20.0, 1.0]\n'
               'base_height = 1.5\n')
    b = next(e for e in r.elements if e.name == "b")
    assert b.size == pytest.approx((10 * IN, 20 * IN, IN)) and b.base_height == 1.5 and not b.ground


# 2026: 48 more since its OUTPOSTS became stations, one parked ball body, geom and joint for each of their 2 x 24 FUEL.
# terrain-library: one more body, geom and joint since it gained the 2020 CONTROL PANEL (ADDED, left out of the sums).
EXISTING = {"2026": (423, 473, 415), "2026-bump": (16, 31, 13), "2023-charge-station": (11, 22, 9),
            "terrain-library": (15, 30, 10)}
ADDED = {"element.control_panel"}
# The sums of geom_size, geom_pos and geom_friction over each range's element.* geoms, recorded from the code before
# the climbing kinds (commit d1bdb90).
ELEMENT_SUMS = {
    "2026": (26.107300760181097, 1.524520960533307, 14.522399999999998),
    "2026-bump": (6.526825190045274, 0.38113024013332686, 3.6306),
    "2023-charge-station": (4.753975920069263, 0.12857173133132072, 1.8153),
    "terrain-library": (8.73109475780278, 0.5322307354439942, 6.050999999999998),
}


@pytest.mark.parametrize("name", EXISTING)
def test_existing_ranges_unchanged(name):
    """The ranges before climbing compile to the same model: the body, geom and joint counts, and the element geoms'
    sizes, positions and frictions (checked by hand against the full model, every array and the XML, when the
    climbing kinds were added), every element geom in the ground group."""
    w = World(range_cfg(range={"file": name, "floor_balls": True}), None)
    m = w.model
    assert (m.nbody, m.ngeom, m.njnt) == EXISTING[name]
    gids = [g for g in range(m.ngeom) if m.body(m.geom_bodyid[g]).name.startswith("element.")
            and m.body(m.geom_bodyid[g]).name not in ADDED]
    assert all(m.geom_group[g] == ELEMENT_GROUP for g in gids)
    sums = tuple(float(getattr(m, a)[gids].sum()) for a in ("geom_size", "geom_pos", "geom_friction"))
    assert sums == pytest.approx(ELEMENT_SUMS[name], rel=1e-12)
    assert not w.parts.layout.bars and not w.parts.layout.switches


@pytest.mark.parametrize("name,start", [("2022-hangar", "under_mid"), ("2020-switch", "approach"),
                                        ("2013-pyramid", "approach")])
def test_climbing_ranges_load_and_stand_still(name, start):
    """Each climbing range builds with its start (the hangar's on the floor protection) and sits still."""
    w = World(range_cfg(part=SWERVE, range={"file": name, "start": start}), None)
    rng = w.parts.range
    assert rng.climb is not None
    assert set(w.parts.layout.bars) == {e.name for e in rng.elements if isinstance(e, Bar)}
    assert set(w.parts.layout.switches) == {e.name for e in rng.elements if isinstance(e, Switch)}
    mon = w.terrain_monitor()
    for _ in range(500):
        w.step(_idle())
    s = mon.sample()
    assert np.isfinite(w.data.qpos).all()
    assert s["robot.wheels_down"] == 4 and s["robot.tilt"] < 1.0


def test_starts_may_stand_under_a_bar_but_not_on_its_posts():
    bar = CLIMB_RANGE.replace('level = "ONE"\n', "")
    r = _parse("[start.under]\npose = [3.0, 2.0, 90.0]\n", bar)     # under the bar's middle
    assert "under" in r.starts
    with pytest.raises(RangeError, match="start 'post'.*overlaps element 'a'"):
        _parse("[start.post]\npose = [3.95, 2.0, 0.0]\n", bar)        # on its +x post
    with pytest.raises(RangeError, match="overlaps element 'a'"):
        _parse("[start.no]\npose = [3.0, 2.0, 0.0]\n", bar.replace("height = 1.2\n", "height = 1.2\nposts = true\n")
               .replace("length = 2.0", "length = 0.6"))               # a short bar: its posts under the robot


def test_bar_section_defaults_to_round_and_takes_square():
    bar = CLIMB_RANGE.replace('level = "ONE"\n', "")
    assert _parse("", bar).elements[0].section == "round"
    assert _parse("", bar.replace("height = 1.2\n", 'height = 1.2\nsection = "square"\n')).elements[0].section == "square"
    with pytest.raises(RangeError, match="section must be"):
        _parse("", bar.replace("height = 1.2\n", 'height = 1.2\nsection = "hex"\n'))
