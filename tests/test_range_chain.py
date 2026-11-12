"""K5 D1 and D3: the `kind = "chain"` range element, a hanging 1D flex with visual beads (scrimmage/testrange/chain.py),
and the contact readers that read a flex's contacts by id (scrimmage/flexcontact.py).

tests/data/chain is Task 1's thin-bar robot and script with chain_blue_center a chain (spec D1's example) and one floor
ball far from the robot, so the model's last geom is a ball's: a flex contact read by its geom (-1) would read that
ball (R5).
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

import mujoco
import numpy as np
import pytest

from range_fixtures import range_cfg
from scrimmage.assembler.view import model_view
from scrimmage.blocks.clearance import Closest, clearance
from scrimmage.blocks.conditions import Watch
from scrimmage.blocks.palette import ANY_BAR, ANY_HOOK
from scrimmage.blocks.program import Cond, Program
from scrimmage.climb.run import ClimbRun, run_climb
from scrimmage.climb.script import load_script
from scrimmage.config import build_world, load_config
from scrimmage.flexcontact import contact_ids, flex_id, id_body, n_ids
from scrimmage.movers import mover_poses, movers
from scrimmage.protocol import HalState
from scrimmage.testrange.chain import (CHAIN_EDGES, MAX_SAG_RATIO, MIN_SAG_RATIO, PROBE_GROUP, chain_points,
                                       lowest_vertex, rest_shape)
from scrimmage.testrange.elements import Chain, parse_climb, parse_elements
from scrimmage.testrange.spec import BUILTIN_RANGES, RangeError
from scrimmage.world import World

CHAIN = Path(__file__).resolve().parent / "data" / "chain"
REPO = Path(__file__).resolve().parent.parent
EXAMPLE = {"name": "chain_blue_center", "kind": "chain", "center": [5.7441, 4.1051], "yaw_deg": 90.0, "span": 2.42,
           "top": 1.18, "low": 0.7176, "diameter_in": 0.5, "level": "ONSTAGE"}      # spec D1's example
LOW = 0.7176
NAME = "chain_blue_center"


def element(**changes) -> dict:
    t = {**EXAMPLE, "name": "c", **changes}
    return {k: v for k, v in t.items() if v is not None}


# ---- parsing ---------------------------------------------------------------------------------------------------------

def test_a_chain_parses():
    (e,) = parse_elements(Path("r.toml"), [dict(EXAMPLE)])
    assert e.diameter == pytest.approx(0.0127, abs=1e-12)
    assert e == Chain(name=NAME, center=(5.7441, 4.1051), yaw_deg=90.0, span=2.42, top=1.18, low=0.7176,
                      diameter=e.diameter, level="ONSTAGE")
    _, (scored,) = parse_climb(Path("r.toml"), {"rule": "lowest-rung", "levels": {"ONSTAGE": 3}}, (e,))
    assert scored.points == 3.0


def test_the_docs_chain_example_parses():
    """docs/terrain.md's Chains example is spec D1's."""
    import tomllib
    text = (REPO / "docs" / "terrain.md").read_text(encoding="utf-8")
    (block,) = [b for b in re.findall(r"```toml\n(.*?)```", text, re.S) if 'kind = "chain"' in b]
    assert tomllib.loads(block)["element"] == [EXAMPLE]


SAG_PAST = MAX_SAG_RATIO * 1.01 * 0.001     # m: just past the deepest catenary over a 1 mm span (F1)


@pytest.mark.parametrize("changes, words", [
    ({"low": 1.2}, "must be below top"),
    ({"span": 0}, "span must be positive"),
    ({"low": 0.005}, "above the carpet"),
    ({"friction": 0.5}, "unknown key 'friction'"),
    ({"edges": 20}, "unknown key 'edges'"),
    ({"center": [0.5, 4.1], "yaw_deg": 0.0}, "outside the field"),
    ({"span": 0.001, "top": LOW + SAG_PAST}, "no catenary"),
])
def test_chain_checks(changes, words):
    with pytest.raises(RangeError) as err:
        parse_elements(Path("r.toml"), [element(**changes)])
    assert "element 'c'" in str(err.value) and words in str(err.value)


def test_the_no_catenary_limit_is_the_solvers():
    """F1: MAX_SAG_RATIO is the real limit. Just under it a 1 mm span parses, and so does span 0.01 with 3 m of sag
    (ratio 300); a chain pulled tauter than MIN_SAG_RATIO has none either."""
    assert 2000.0 < MAX_SAG_RATIO < 2100.0 and MIN_SAG_RATIO < 1e-4
    parse_elements(Path("r.toml"), [element(span=0.001, top=LOW + MAX_SAG_RATIO * 0.99 * 0.001)])
    parse_elements(Path("r.toml"), [element(span=0.01, top=LOW + 3.0)])
    with pytest.raises(ValueError):
        rest_shape(2.0, LOW + MIN_SAG_RATIO * 2.0 * 0.5, LOW)


def test_the_rest_shape():
    pts, link = rest_shape(2.42, 1.18, 0.7176)
    assert pts.shape == (CHAIN_EDGES + 1, 2) == (41, 2)
    assert int(np.argmin(pts[:, 1])) == 20 and pts[20, 1] == pytest.approx(0.7176, abs=1e-9)
    assert pts[0, 0] == pytest.approx(-1.21, abs=1e-9) and pts[-1, 0] == pytest.approx(1.21, abs=1e-9)
    assert pts[0, 1] == pytest.approx(1.18, abs=1e-9) and pts[-1, 1] == pytest.approx(1.18, abs=1e-9)
    links = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    assert np.ptp(links) < 1e-12 and links[0] == pytest.approx(link, abs=1e-12)
    assert 40 * link == pytest.approx(2.642, rel=0.01)      # the continuous catenary through 2.42, 1.18 and 0.7176


# ---- the world -------------------------------------------------------------------------------------------------------

def chain_world() -> World:
    return build_world(load_config(CHAIN / "robot.toml"))


def flex_of(w: World) -> int:
    return w.parts.layout.chains[NAME].flex


def test_a_chain_hangs_still():
    w = chain_world()
    m, d = w.model, w.data
    f = flex_of(w)
    hal = HalState()
    lows = []
    for _ in range(10000):          # 10 s
        w.step(hal)
        lows.append(float(lowest_vertex(m, d, f)[2]))
    assert abs(lows[-1] - LOW) < 0.001            # spike: 0.15 mm
    assert np.std(lows[5000:]) < 0.0001
    assert np.isfinite(d.qpos).all()


@pytest.fixture(scope="module")
def climbed():
    """The fixture's climb, once: the world, the result, a trace (t, climb.hanging, climb.swing, the lowest vertex's
    height), the Test tab's watch updated every step, and the middle bead's start."""
    w = chain_world()
    m, d = w.model, w.data
    f = flex_of(w)
    view = model_view(w, include_world=True)
    watch = Watch(w, Program(Path("/tmp/x.test.toml"), Path("r.toml"), []))
    trace = []

    def each(t, v):
        trace.append((t, v["climb.hanging"], v["climb.swing"], float(lowest_vertex(m, d, f)[2])))
        watch.update()
    run = ClimbRun(w, load_script(CHAIN / "climb.toml"))
    run.on_step = each
    r = run.run()
    return w, r, np.array(trace), watch, view


def test_the_robot_climbs_the_chain(climbed):
    _, r, trace, _, _ = climbed
    assert r.status == "reached ONSTAGE", [(row.t, row.event) for row in r.rows if row.event]
    assert not any("walked off" in row.event for row in r.rows)
    last = trace[trace[:, 0] > r.duration - 3.0]
    assert last[:, 1].min() == 1
    sag = LOW - last[:, 3]
    assert 0.04 <= sag.min() and sag.max() <= 0.08          # spike: 6 cm
    assert np.abs(last[:, 2]).max() < 1.5                   # deg; spike: +-0.4


def test_climb_run_reads_the_chain():
    """`scrimmage climb run`'s entry point on the fixture."""
    r = run_climb(CHAIN / "robot.toml", load_script(CHAIN / "climb.toml"))
    assert r.status == "reached ONSTAGE"


def test_the_beads_show_the_sag(climbed):
    w, _, _, _, view = climbed
    beads = {g["name"]: g for g in view["world_geoms"] if g["name"].startswith(f"element.{NAME}.bead")}
    assert len(beads) == 41 and all(g["type"] == "sphere" for g in beads.values())
    inner = [beads[f"element.{NAME}.bead{i}"] for i in range(1, 40)]
    assert all(g["mover"] >= 0 for g in inner)
    assert beads[f"element.{NAME}.bead0"]["mover"] == -1 and beads[f"element.{NAME}.bead40"]["mover"] == -1
    assert np.allclose([beads[f"element.{NAME}.bead{i}"]["pos"] for i in range(41)], chain_points(
        next(e for e in w.parts.range.elements if e.name == NAME)), atol=1e-9)
    mid = beads[f"element.{NAME}.bead20"]
    now = mover_poses(w, movers(w))[mid["mover"]]
    assert now[2] < mid["pos"][2] - 0.04
    assert f"element.{NAME}.bead0" not in w.movable_objects()


def test_the_swing_axis_is_the_chains_line(climbed):
    w, _, _, _, _ = climbed
    a, b = w.parts.layout.chains[NAME].ends
    s = w.climb.swing_direction()
    assert s is not None
    assert abs(s[2]) < 1e-6 and abs(float(s @ (b - a))) < 1e-6 and float(np.linalg.norm(s)) == pytest.approx(1.0)


def test_the_watch_and_terrain_monitor_read_the_chain(climbed):
    """F3: the Test tab's watch and the terrain monitor build on a chain range and read its flex as the hold."""
    w, r, _, watch, _ = climbed
    m = w.model
    fid = flex_id(m, flex_of(w))
    mon = w.terrain_monitor()
    assert len(mon._hold_l) == n_ids(m) and mon._hold_l[fid]
    assert watch._holds[NAME] == [fid]
    assert watch.holds(Cond("touch", {"robot": ANY_HOOK, "field": NAME}))
    assert watch.holds(Cond("touch", {"robot": ANY_HOOK, "field": ANY_BAR}))
    assert watch.load[NAME] > 0.5 * watch.weight
    assert NAME in watch.hung
    top = watch._top(NAME)
    assert top == pytest.approx(float(lowest_vertex(m, w.data, flex_of(w))[2]) + 0.0127 / 2.0, abs=1e-12)
    assert top < LOW - 0.03


LOW_CHAIN_LOW = 0.09    # m: the lowered copy's low point, at the front bumper's height
LOW_CHAIN_START = 6.4   # m: the lowered copy's start x, 0.35 m further back than the fixture's
SENSOR = """
[[sensor]]
name = "range"
kind = "distance"
body = "drive.chassis"
at = [0.38, 0.0, 0.09]            # inside the front bumper, at the lowered chain's low point (bead 20)
toward = [1, 0, 0]
max_range = 2.0
sees = "anything"                 # the field too: a chain in the way would read
analog = 0
"""


def lowered(tmp_path: Path, sensor: bool = False) -> World:
    """The fixture's chain is out of reach of a robot with its hooks down (their tops are at 0.57 m, the chain's
    bottom at 0.71), so this copy hangs it to the bumpers' height (its low point 9 cm up) and starts the robot 0.35 m
    further back. With `sensor`, a distance sensor looks ahead from the front bumper straight at bead 20."""
    for f in ("robot.toml", "climb.toml", "range.toml"):
        shutil.copy(CHAIN / f, tmp_path / f)
    rng = (tmp_path / "range.toml").read_text()
    rng, n = re.subn(r"(?m)^low = 0\.7176 .*$", f"low = {LOW_CHAIN_LOW}", rng)
    rng, k = re.subn(r"(?m)^pose = \[6\.05, ", f"pose = [{LOW_CHAIN_START}, ", rng)
    assert n == k == 1
    (tmp_path / "range.toml").write_text(rng)
    if sensor:
        (tmp_path / "robot.toml").write_text((tmp_path / "robot.toml").read_text() + SENSOR)
    return build_world(load_config(tmp_path / "robot.toml"))


def forward(volts: float) -> HalState:
    hal = HalState()
    hal.devices[("PWM", "0")] = {"<init": True, "<speed": volts / 12.5}
    hal.devices[("PWM", "1")] = {"<init": True, "<speed": -volts / 12.5}     # the right side is mirrored
    return hal


def front_face(w: World) -> float:
    """The world x of the robot's front bumper face (the robot faces -x): the smallest x of its box geoms."""
    m, d = w.model, w.data
    root = m.body_rootid[w.robot_body]
    boxes = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == root
             and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX and m.geom_contype[g]]
    return min(float(d.geom_xpos[g][0] - np.abs(d.geom_xmat[g].reshape(3, 3))[0] @ m.geom_size[g]) for g in boxes)


def test_a_bumper_on_the_chain_is_read_as_the_chain(tmp_path):
    """Review Focus 1, on the lowered copy (lowered()). The robot drives its front bumper into the chain at 3 V."""
    w = lowered(tmp_path)
    m, d = w.model, w.data
    assert m.geom(m.ngeom - 1).name == "ball.0"     # what geom -1 would read
    assert w.test_range.balls.ball_of_geom(m.ngeom - 1) == 0
    fid = flex_id(m, flex_of(w))
    hal = forward(3.0)
    mon = w.terrain_monitor()
    seen = 0
    for _ in range(1000):           # 1 s
        w.step(hal)
        ids = contact_ids(m, d)
        on = np.flatnonzero((ids == fid).any(axis=1))
        if not len(on):
            continue
        seen += 1
        assert fid == m.ngeom and ids.min() >= 0        # one flex: its id is the first past the geoms
        on_ground, touched, hooked, others = w.climb.contact_state()
        assert NAME in touched and not hooked and others
        assert w.climb.sample()["climb.hanging"] == 0
        _, _, (mine, other) = mon._read()
        assert any(o == fid for o in other) and not any(mon._ground_l[o] for o in other if o == fid)
        balls = w.test_range.balls.ball_of_geom(ids)
        flex_side = ids[on] == fid
        assert np.all(balls[on][flex_side] == -1)
        robot = m.body_rootid[id_body(m)[ids[on][~flex_side]]] == m.body_rootid[w.robot_body]
        assert robot.all()
        assert f"element.{NAME}" in w.robot_contacts()
        assert f"element.{NAME}" in w.contact_names()       # a scenario's contact check can name it
    assert seen > 0


# ---- clearance, rays and the probe -----------------------------------------------------------------------------------

def test_clearance_to_a_chain_is_the_distance_to_its_links(tmp_path):
    """Review finding 1. At the start of the lowered copy the chain's low point is at the front bumper's height, so the
    nearest robot geom is that face, and the distance is the face to the chain's axis (x 5.7441) less its radius."""
    w = lowered(tmp_path)
    m, d = w.model, w.data
    mujoco.mj_forward(m, d)
    probe = w.parts.layout.chains[NAME].probe
    pose = (d.geom_xpos[probe].copy(), d.geom_xmat[probe].copy(), m.geom_size[probe].copy())
    want = front_face(w) - 5.7441 - 0.0127 / 2.0
    assert 0.2 < want < 0.3
    assert clearance(w, NAME) == pytest.approx(want, abs=1e-6)
    assert np.array_equal(d.geom_xpos[probe], pose[0]) and np.array_equal(d.geom_xmat[probe], pose[1])
    assert np.array_equal(m.geom_size[probe], pose[2])


def test_a_clears_condition_fails_when_the_robot_touches_the_chain(tmp_path):
    """Review finding 1: `clears chain_blue_center by 1 in` holds at the start (about 9 in away) and fails once the
    bumper touches the chain (clearance 0)."""
    w = lowered(tmp_path)
    m, d = w.model, w.data
    watch = Watch(w, Program(Path("/tmp/x.test.toml"), Path("r.toml"), []))
    watch.closest = Closest(w, [NAME])
    clears = Cond("clears", {"element": NAME, "at_least_in": 1.0})
    mujoco.mj_forward(m, d)
    watch.closest.update(0.0)
    assert watch.holds(clears)
    fid = flex_id(m, flex_of(w))
    hal = forward(3.0)
    for _ in range(1000):
        w.step(hal)
        if (contact_ids(m, d) == fid).any():
            break
    else:
        pytest.fail("the bumper never touched the chain")
    watch.closest.update(float(d.time))
    assert watch.closest.now[NAME] == 0.0
    assert not watch.holds(clears)


def test_a_distance_sensor_sees_through_a_chain(tmp_path):
    """Review finding 4: a ray doesn't hit a flex's links, so it doesn't hit their beads either: the sensor looking
    straight at bead 20 reads nothing. A chain blocks no sensor, camera or ray (docs/terrain.md)."""
    w = lowered(tmp_path, sensor=True)
    m, d = w.model, w.data
    c = w.parts.layout.chains[NAME]
    mujoco.mj_forward(m, d)
    gid = np.zeros(1, np.int32)
    b = m.body("drive.chassis").id
    start = d.xpos[b] + d.xmat[b].reshape(3, 3) @ np.array([0.38, 0.0, 0.09])
    assert mujoco.mj_ray(m, d, start, np.array([-1.0, 0.0, 0.0]), None, 1, b, gid) > 0
    assert int(gid[0]) == c.beads[20]               # the bead is in the beam's way
    w.step(HalState())
    r = w.sensors.reading("range")
    assert r.distance > 0.5         # past the chain (0.27 m away): it reads the field beyond, or nothing


def test_the_probe_changes_nothing_else():
    """The probe touches nothing, isn't drawn, isn't a mover or a movable object, and adds one geom (no body)."""
    w = chain_world()
    m = w.model
    c = w.parts.layout.chains[NAME]
    p = c.probe
    assert m.geom(p).name == f"element.{NAME}.probe" and m.geom_bodyid[p] == m.body(f"element.{NAME}").id
    assert m.geom_contype[p] == 0 and m.geom_conaffinity[p] == 0 and m.geom_group[p] == PROBE_GROUP
    assert m.geom_rgba[p][3] == 0.0
    view = model_view(w, include_world=True)
    assert not any(g["name"] == f"element.{NAME}.probe" for g in view["world_geoms"])
    assert all(mv.id != p for mv in movers(w) if mv.kind == "geom")
    assert f"element.{NAME}.probe" not in w.movable_objects()


# ---- the cost --------------------------------------------------------------------------------------------------------

def ranges_2024(tmp_path: Path) -> tuple[Path, Path]:
    """The speaker example's robot on the 2024 range as is, and on a copy with its six chain_* bars as chains with D1's
    numbers: (bars config, chains config)."""
    src = Path(BUILTIN_RANGES["2024"]).read_text()
    blocks = re.findall(r'(?ms)^\[\[element\]\]\nname = "chain_[a-z_]+"\nkind = "bar"\n.*?^level = "ONSTAGE"\n', src)
    assert len(blocks) == 6
    chains = src
    for b in blocks:
        keep = [ln for ln in b.splitlines() if ln.startswith(("[[element]]", "name", "center", "yaw_deg",
                                                                "diameter_in", "level"))]
        new = "\n".join(keep[:4] + ['kind = "chain"', "span = 2.42", "top = 1.18", "low = 0.7176"] + keep[4:]) + "\n"
        chains = chains.replace(b, new)
    robot = (REPO / "examples/blocks/speaker/note-shooter-standin.toml").read_text()
    assert robot.count('file = "2024"') == 1
    out = []
    for name, text in (("bars", src), ("chains", chains)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "range.toml").write_text(text)
        (tmp_path / name / "robot.toml").write_text(robot.replace('file = "2024"', 'file = "range.toml"'))
        out.append(tmp_path / name / "robot.toml")
    return out[0], out[1]


def idle(w: World, seconds: float) -> float:
    hal = HalState()
    t0 = time.perf_counter()
    for _ in range(round(seconds / w.model.opt.timestep)):
        w.step(hal)
    return time.perf_counter() - t0


def test_six_chains_add_only_their_vertices(tmp_path):
    """F13, the cost through structure: six chains add their 39 vertex bodies each (an end-bead body each in place
    of a bar's body), 3 slides a vertex, and one edge equality row an edge; nothing else."""
    bars, chains = (build_world(load_config(p)) for p in ranges_2024(tmp_path))
    assert chains.model.nflex == 6 and bars.model.nflex == 0
    assert chains.model.nbody - bars.model.nbody == 6 * 39
    assert chains.model.nv - bars.model.nv == 6 * 39 * 3
    idle(bars, 2.0)
    idle(chains, 2.0)
    assert chains.data.nefc - bars.data.nefc == 6 * 40
    for w in (bars, chains):
        assert np.isfinite(w.data.qpos).all()


@pytest.mark.skipif(bool(os.environ.get("CI")), reason="timing is only meaningful on a quiet machine")
def test_six_chains_cost_at_most_6x_six_bars(tmp_path):
    """F13: the wall time of 2 s idle, the least of 3 runs each, with six chains over six bars, at most 6x (the spike's
    2.1x, 149 to 315 ms per simulated second, with 2x headroom). Skipped on CI: timing never gates CI."""
    bars, chains = (build_world(load_config(p)) for p in ranges_2024(tmp_path))
    best = {}
    for name, w in (("bars", bars), ("chains", chains)):
        best[name] = min(idle(w, 2.0) for _ in range(3))
    assert best["chains"] / best["bars"] <= 6.0, best


# ---- every contact reader goes through flexcontact -------------------------------------------------------------------

# Any direct read of a contact's geoms: `contact.geom` (with or without an index), `.geom1` / `.geom2`, a contact by
# index, or a loop over the contacts (review finding 2).
BAD = re.compile(r"contact\.geom\b|\.geom[12]\b|\.contact\[|\bin\s+\w+\.contact\b")


def test_the_contact_pattern_catches_each_form():
    """The pattern can't silently weaken: it matches every form a contact's geoms have been read in, including
    World.robot_contacts' before this task and the bench's."""
    for text in ("for c in d.contact[:d.ncon]:\n    b1 = m.geom_bodyid[c.geom1]",
                 "g = d.contact.geom[:d.ncon]", "geom = d.contact.geom", "con = d.contact[k]; g = con.geom1",
                 "x = c.geom2", "for c in data.contact:"):
        assert BAD.search(text), text
    for text in ("ids = contact_ids(m, d)", "m.geom_bodyid[g]", "d.geom_xpos[g]", "d.contact.frame[i]"):
        assert not BAD.search(text), text


def test_no_world_module_reads_contact_geom_directly():
    """R5: outside flexcontact.py, no module reads a contact's geoms (BAD), the bench's included: a bench with
    `flex = true` has a flex NOTE on it (Task 8)."""
    pkg = REPO / "scrimmage"
    files = [p for p in pkg.rglob("*.py") if p.name != "flexcontact.py"]
    assert any(p.relative_to(pkg).parts[0] == "bench" for p in files)
    bad = [f"{p.relative_to(pkg)}:{k}" for p in files
           for k, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if BAD.search(line)]
    assert bad == []


def test_no_flex_no_copy():
    w = World(range_cfg(), None)
    m, d = w.model, w.data
    hal = HalState()
    for _ in range(50):
        w.step(hal)
    assert m.nflex == 0 and d.ncon > 0
    ids = contact_ids(m, d)
    assert np.shares_memory(ids, d.contact.geom)
    assert id_body(m) is m.geom_bodyid and n_ids(m) == m.ngeom
