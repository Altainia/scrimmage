"""K5 D5 and D6: ONSTAGE off the carpet, HARMONY, their signals (`climb.hold`, `climb.hold.<hold>.robots`,
`climb.harmony`) and a partner robot hanging on a chain (`[partner] hang`, parts/partner.py, R8 and R9).

Every world here is a tmp copy of tests/data/chain with `[climb] rule = "off-carpet"`, `harmony = 2` and a short
`[game]` (1 s auto, 7 s teleop, so the match ends while the robot hangs), and without `stage_blue_leg2`: that truss
leg's estimated line passes through a partner hanging 0.9 m along chain_blue_center, which would then rest on the leg
instead of the chain (a placement problem of that spot, not of the chain).

The robot and partner are 60 kg each. A partner alone pulls the chain's 0.22 m of slack into a taut V, so the chain at
the robot's hooks rises from 0.72 m to about 0.99 m, out of the fixture climber's reach. The HARMONY climbs therefore
use a three-stage climber (travel 0.6 m) whose first reach puts its hooks over the taut chain; the climb they compare
with uses the same robot.
"""

from __future__ import annotations

import math
import re
import shutil
from pathlib import Path

import numpy as np
import pytest

from scrimmage.blocks.match import MatchState
from scrimmage.checks import ScenarioError, resolve_signal
from scrimmage.climb.run import ClimbRun
from scrimmage.climb.script import load_script
from scrimmage.config import build_world, load_config
from scrimmage.flexcontact import contact_ids
from scrimmage.parts.partner import HANG_GAP, IN
from scrimmage.protocol import HalState
from scrimmage.testrange.chain import chain_points, lowest_vertex, vertices
from scrimmage.testrange.elements import Bar, Chain, Switch

CHAIN = Path(__file__).resolve().parent / "data" / "chain"
NAME = "chain_blue_center"
LOW = 0.7176  # source: M 5.5: the chain's low point at rest, 2 ft 4 1/4 in (the fixture's)
ALONG = 0.9  # choice: R9's example: the partner one outline from the robot's hooks (m from the low point)
HANG = f'\n[partner]\nhang = {{ hold = "{NAME}", along = {ALONG} }}\n'
GAME = "\n[game]\nauto_seconds = 1\npause_seconds = 0\nteleop_seconds = 7\n"  # choice: it ends while the robot hangs
THREE = 0.17  # choice: the three-stage climber's first reach (m), its hooks over the taut chain
THREE_PLAIN = 0.0933  # choice: its reach (m) over the chain at rest: the fixture's 0.14 m times 2/3 stages


def fixture(folder: Path, partner: bool = False, three: float | None = None) -> Path:
    """The chain fixture in `folder`, made off-carpet (see the module), with a hanging partner and a three-stage
    climber reaching `three` if asked; its robot config's path."""
    folder.mkdir(parents=True, exist_ok=True)
    for f in CHAIN.iterdir():
        shutil.copy(f, folder)
    rng = (folder / "range.toml").read_text(encoding="utf-8")
    rng = re.sub(r'\[\[obstacle\]\]\nname = "stage_blue_leg2".*?\n\n', "", rng, flags=re.S)
    assert "stage_blue_leg2" not in rng
    rng = rng.replace('rule = "lowest-rung"', 'rule = "off-carpet"\nharmony = 2') + GAME
    (folder / "range.toml").write_text(rng, encoding="utf-8")
    robot = (folder / "robot.toml").read_text(encoding="utf-8")
    if three is not None:
        robot = robot.replace("stages = 2", "stages = 3").replace("travel = 0.45", "travel = 0.6")
        script = (folder / "climb.toml").read_text(encoding="utf-8")
        (folder / "climb.toml").write_text(script.replace("0.14", f"{three}"), encoding="utf-8")
    (folder / "robot.toml").write_text(robot + (HANG if partner else ""), encoding="utf-8")
    return folder / "robot.toml"


def climb(path: Path) -> dict:
    """The fixture's climb: the world, the result, every step's climb signals and robot center, and the hooks' mean
    height (the hook seat) at each step."""
    w = build_world(load_config(path))
    m, d = w.model, w.data
    hooks = [g for g in range(m.ngeom) if ".hook" in m.geom(g).name]
    samples, seats = [], []

    def each(t, v):
        samples.append((t, dict(v), tuple(float(c) for c in d.xpos[w.robot_body][:2])))
        seats.append(float(d.geom_xpos[hooks][:, 2].mean()))
    run = ClimbRun(w, load_script(path.parent / "climb.toml"))
    run.on_step = each
    r = run.run()
    return {"world": w, "result": r, "samples": samples, "seats": np.array(seats)}


def seat_at_end(c: dict) -> float:
    """The hook seat's mean height over the climb's last 2 s (it hangs still there)."""
    t = np.array([s[0] for s in c["samples"]])
    return float(c["seats"][t > t[-1] - 2.0].mean())


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    return climb(fixture(tmp_path_factory.mktemp("plain")))


@pytest.fixture(scope="module")
def harmony(tmp_path_factory):
    return climb(fixture(tmp_path_factory.mktemp("harmony"), partner=True, three=THREE))


@pytest.fixture(scope="module")
def alone(tmp_path_factory):
    return climb(fixture(tmp_path_factory.mktemp("alone"), three=THREE_PLAIN))


def hold_number(w, name: str) -> int:
    """R7, worked out here: the hold's rank among the range's bars, switches and chains, in element order."""
    holds = [e.name for e in w.parts.range.elements if isinstance(e, (Bar, Switch, Chain))]
    return holds.index(name) + 1


# ---- ONSTAGE ---------------------------------------------------------------------------------------------------------

def test_off_carpet_scores_the_holds_level(plain):
    w, r = plain["world"], plain["result"]
    assert r.status == "reached ONSTAGE", [(row.t, row.event) for row in r.rows if row.event]
    v = w.climb.sample()
    assert hold_number(w, NAME) == 1
    assert (v["climb.hanging"], v["climb.level"], v["climb.points"]) == (1, 1, 3.0)
    assert v["climb.hold"] == hold_number(w, NAME)
    assert v[f"climb.hold.{NAME}.robots"] == 1 and v["climb.hold.chain_blue_left.robots"] == 0
    assert v["climb.harmony"] == 0.0
    # back on the carpet, at its start (the hooks 0.31 m short of the chain): all of them 0
    w.set_robot_pose(*w.parts.range.starts["chain_center"])
    for _ in range(300):
        w.step(HalState())
    v = w.climb.sample()
    assert (v["climb.hanging"], v["climb.level"], v["climb.points"], v["climb.hold"], v["climb.harmony"],
            v[f"climb.hold.{NAME}.robots"]) == (0, 0, 0.0, 0, 0.0, 0)


def test_the_hold_signals_resolve_and_unknown_holds_are_listed(plain):
    w = plain["world"]
    assert resolve_signal(f"climb.hold.{NAME}.robots", w).name == f"climb.hold.{NAME}.robots"
    for name in ("climb.hold", "climb.harmony"):
        resolve_signal(name, w)
    with pytest.raises(ScenarioError) as e:
        resolve_signal("climb.hold.nope.robots", w)
    assert f"{NAME}, chain_blue_left, chain_blue_right" in str(e.value)


# ---- a hanging partner -----------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def hung(tmp_path_factory):
    """The robot standing at its start and the partner hanging at ALONG: at t = 0, then after 2 s."""
    w = build_world(load_config(fixture(tmp_path_factory.mktemp("hung"), partner=True)))
    m, d = w.model, w.data
    info = w.parts.partner
    e = next(e for e in w.parts.range.elements if e.name == NAME)
    pts = chain_points(e)
    axis = np.array([math.cos(math.radians(e.yaw_deg)), math.sin(math.radians(e.yaw_deg)), 0.0])
    s = (pts - np.array([*e.center, 0.0])) @ axis
    near = 1 + int(np.argmin(np.abs(s[1:-1] - ALONG)))           # the nearest interior vertex
    body = m.body(info.body).id
    root = m.body_rootid[body]
    mine = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == root
            and (m.geom_contype[g] or m.geom_conaffinity[g])]
    signs = np.array(list(np.ndindex(2, 2, 2)), dtype=float) * 2.0 - 1.0

    def top() -> float:
        return max(float((d.geom_xpos[g][2] + (m.geom_aabb[g][:3] + signs * m.geom_aabb[g][3:])
                          @ d.geom_xmat[g].reshape(3, 3)[2]).max()) for g in mine)
    start = {"top": top(), "vertex": pts[near].copy(), "z": float(d.xpos[body][2]),
             "heading": math.degrees(math.atan2(d.xmat[body][3], d.xmat[body][0])) % 360.0}
    flex = w.parts.layout.chains[NAME].flex
    fid = m.ngeom + flex
    touches = []                    # (t, a partner geom touches the chain)
    for _ in range(2000):
        w.step(HalState())
        touches.append((float(d.time), any((a == fid and b in mine) or (b == fid and a in mine)
                                           for a, b in contact_ids(m, d).tolist())))
    return {"world": w, "start": start, "near": near, "flex": flex, "body": body, "touches": touches}


def test_a_hanging_partner_hangs(hung):
    w, start = hung["world"], hung["start"]
    m, d = w.model, w.data
    info = w.parts.partner
    assert info.hang is not None and info.hang.hold == NAME and info.hang.along == ALONG
    # at t = 0: its top HANG_GAP under the nearest interior vertex, its x across the chain (yaw 90 + 90)
    assert start["vertex"][2] - start["top"] == pytest.approx(HANG_GAP, abs=0.002)
    assert start["heading"] == pytest.approx(180.0, abs=1e-6)
    # after 2 s: level, its weight carried by the chain, pulled into a V at its vertex (the low point rises: the
    # chain's slack is taken up), and its lift read from where it would stand on its wheels
    assert w.partner.tilt() < 0.5
    v = vertices(m, d, hung["flex"])
    assert int(np.argmin(v[:, 2])) == hung["near"]
    assert float(lowest_vertex(m, d, hung["flex"])[2]) > LOW + 0.05
    assert float(d.xpos[hung["body"]][2]) < start["z"] - 0.05
    r = d.xmat[hung["body"]].reshape(3, 3)
    low = float(np.min(d.xpos[hung["body"]][2] + info.corners @ r[2]))
    assert w.partner.height() == pytest.approx((low - info.stand_low) / IN, abs=0.01)
    assert w.partner.height() > 10.0
    # F12: no partner geom touches the chain after 1 s (and with the chain's bits, never)
    assert not any(touch for t, touch in hung["touches"] if t > 1.0)
    # it hangs off the carpet on its chain: one robot there, though the robot stands
    s = w.climb.sample()
    assert s[f"climb.hold.{NAME}.robots"] == 1 and s["climb.hanging"] == 0 and s["climb.harmony"] == 0.0


# ---- HARMONY ---------------------------------------------------------------------------------------------------------

def test_harmony_counts_the_robot_and_its_partner(harmony, alone):
    r = harmony["result"]
    assert r.status == "reached ONSTAGE", [(row.t, row.event) for row in r.rows if row.event]
    assert alone["result"].status == "reached ONSTAGE"
    v = harmony["world"].climb.sample()
    assert v[f"climb.hold.{NAME}.robots"] == 2
    assert v["climb.harmony"] == 2.0 and v["climb.points"] == 3.0 and v["climb.level"] == 1
    assert alone["world"].climb.sample()["climb.harmony"] == 0.0
    # the partner took up the chain's slack: the robot hangs higher than it does alone (spec D4: it stays where it
    # hooked, so it doesn't slide down to the partner)
    assert seat_at_end(harmony) >= seat_at_end(alone) + 0.01


def test_the_match_scores_onstage_and_harmony(harmony):
    w = harmony["world"]
    rng = w.parts.range
    assert rng.game.harmony == 2.0
    match = MatchState(rng.game, rng.zones, list(rng.climb.levels), start=0.0)
    for t, v, (x, y) in harmony["samples"]:
        match.see(t, x, y, int(v["climb.level"]), float(v["climb.harmony"]))
    assert harmony["samples"][-1][0] > match.end           # the match ended while the robot hung
    p = match.points([])
    assert p.endgame == 5.0
    assert [(i.source, i.count, i.points) for i in p.items] == [("climb", 1, 3.0), ("harmony", 1, 2.0)]


def test_the_docs_climb_example_parses():
    """docs/climbing.md's ONSTAGE and HARMONY [climb] example is a real [climb] table."""
    import tomllib
    from scrimmage.testrange.elements import parse_climb
    text = (Path(__file__).resolve().parent.parent / "docs" / "climbing.md").read_text(encoding="utf-8")
    (block,) = [b for b in re.findall(r"```toml\n(.*?)```", text, re.S) if 'rule = "off-carpet"' in b]
    rule, _ = parse_climb(Path("r.toml"), tomllib.loads(block)["climb"], ())
    assert (rule.rule, rule.levels, rule.harmony) == ("off-carpet", {"ONSTAGE": 3.0}, 2.0)
