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
from scrimmage.blocks.conditions import Watch
from scrimmage.blocks.palette import ANY_BAR, ANY_HOOK
from scrimmage.blocks.program import Cond, Program
from scrimmage.climb.run import ClimbRun, run_climb
from scrimmage.climb.script import load_script
from scrimmage.config import build_world, load_config
from scrimmage.flexcontact import contact_ids, flex_id, id_body, n_ids
from scrimmage.movers import mover_poses, movers
from scrimmage.protocol import HalState
from scrimmage.testrange.chain import (CHAIN_EDGES, MAX_SAG_RATIO, MIN_SAG_RATIO, chain_points, lowest_vertex,
                                       rest_shape)
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


# ---- the world ---------------------------------------------------------------------------------------------------------

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


def test_a_bumper_on_the_chain_is_read_as_the_chain(tmp_path):
    """Review Focus 1. The fixture's chain is out of reach of a robot with its hooks down (their tops are at 0.57 m,
    the chain's bottom at 0.71), so this copy hangs it to the bumpers' height (its low point 9 cm up) and starts the
    robot 0.35 m further back. The robot drives its front bumper into it at 3 V."""
    for f in ("robot.toml", "climb.toml", "range.toml"):
        shutil.copy(CHAIN / f, tmp_path / f)
    rng = (tmp_path / "range.toml").read_text()
    rng, n = re.subn(r"(?m)^low = 0\.7176 .*$", "low = 0.09", rng)
    rng, k = re.subn(r"(?m)^pose = \[6\.05, ", "pose = [6.4, ", rng)
    assert n == k == 1
    (tmp_path / "range.toml").write_text(rng)
    w = build_world(load_config(tmp_path / "robot.toml"))
    m, d = w.model, w.data
    assert m.geom(m.ngeom - 1).name == "ball.0"     # what geom -1 would read
    assert w.test_range.balls.ball_of_geom(m.ngeom - 1) == 0
    fid = flex_id(m, flex_of(w))
    hal = HalState()
    volts = 3.0 / 12.5
    hal.devices[("PWM", "0")] = {"<init": True, "<speed": volts}
    hal.devices[("PWM", "1")] = {"<init": True, "<speed": -volts}     # the right side is mirrored
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


# ---- the cost -----------------------------------------------------------------------------------------------------------

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

def test_no_world_module_reads_contact_geom_directly():
    """R5: outside flexcontact.py (and the bench, which builds its own worlds), no module indexes contact.geom."""
    pkg = REPO / "scrimmage"
    bad = [str(p.relative_to(pkg)) for p in pkg.rglob("*.py")
           if p.name != "flexcontact.py" and "bench" not in p.relative_to(pkg).parts[:1]
           and "contact.geom[" in p.read_text(encoding="utf-8")]
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
