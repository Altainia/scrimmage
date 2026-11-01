"""Climb signals: hanging, level and points by the range's [climb] rule, the swing about the bar, the whole-hook load,
and the robot's extension beyond its frame perimeter and its height (scrimmage/terrain/climb.py).

The robots are a stock chassis and climber, 60 kg in all, hung by their hooks the way tests/test_climb_physics.py
hangs them, from any bar or switch: placed with the hook's seat on the bar's axis and the CoM straight under it."""

from __future__ import annotations

import asyncio
import copy
import math
import tomllib
from pathlib import Path
from types import SimpleNamespace

import mujoco
import numpy as np
import pytest

import scrimmage.scenario_runner as runner_mod
from scrimmage.checks import ScenarioError, resolve_signal, tick_checks
from scrimmage.protocol import HalState
from scrimmage.scenario import load_scenario_raw
from scrimmage.terrain.climb import ClimbMonitor
from scrimmage.testrange.elements import IN, Bar
from scrimmage.testrange.spec import load_range
from scrimmage.world import World
from test_climb_physics import (G, MASS, climber, hang, mid_rung, one_rung, params, qadr, robot, seat_point, step,
                                swing_angle)

ROOT = Path(__file__).resolve().parents[1]
TICK = 0.005                    # s: a lockstep step
PIPE = 1.66 * IN                # m: the 2022 rung's OD
LEVELS_2022 = {"LOW": 4, "MID": 6, "HIGH": 10, "TRAVERSAL": 15}
CHASSIS = {"name": "drive", "stock": "chassis", "mass": 50.0}
SWERVE = [{"name": c, "stock": "swerve/MK4i", "attach": f"drive.{m}",
           "ids": {"drive": f"PWM:{i}", "steer": f"PWM:{i + 4}"}}
          for i, (c, m) in enumerate((("fl", "front_left"), ("fr", "front_right"), ("bl", "back_left"),
                                      ("br", "back_right")))]


# ------------------------------------------------------------------ helpers


def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    return repr(float(v))


def write_range(tmp_path, name: str, rule: str, levels: dict, elements: list[dict]) -> str:
    lines = ["[range]", f'name = "{name}"', 'ball = "2026-fuel"', "[climb]", f'rule = "{rule}"',
             "levels = { " + ", ".join(f"{k} = {v}" for k, v in levels.items()) + " }"]
    for e in elements:
        lines.append("[[element]]")
        lines += [f"{k} = {_toml(v)}" for k, v in e.items()]
    path = tmp_path / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def bar(name: str, x: float, y: float, height: float, level: str | None = None, diameter: float = PIPE,
        length: float = 2.0) -> dict:
    """A rigid bar without posts along y."""
    e = {"name": name, "kind": "bar", "center": [x, y], "yaw_deg": 90.0, "length": length, "diameter": diameter,
         "height": height, "posts": False}
    if level is not None:
        e["level"] = level
    return e


def world(rng: str, start, climb: dict | None = None, drive: list[dict] = ()) -> World:
    """A chassis (with `drive` parts on it) and `climb`, MASS kg in all, on range `rng` at `start`."""
    parts = [dict(CHASSIS), *copy.deepcopy(list(drive)), climb or climber()]
    cfg = {"part": parts, "range": {"file": rng, "floor_balls": False},
           "battery": {"nominal_voltage": 12.0, "resistance": 0.0}, "robot": {"start_pose": list(start)}}
    w = World(cfg, Path("."))
    parts[0]["mass"] = 50.0 + MASS - float(w.model.body_subtreemass[w.robot_body])
    assert parts[0]["mass"] > 0.0
    w = World(cfg, Path("."))
    assert w.model.body_subtreemass[w.robot_body] == pytest.approx(MASS)
    return w


def hang_on(w: World, geom: str, settle: float = 0.0) -> np.ndarray:
    """Hang the robot by its hooks from bar geom `geom` (any direction; the robot's y must run along it), at full
    extension and at rest, its CoM straight under the bar's axis; then step `settle` s. Returns the pivot."""
    m, d = w.model, w.data
    w.reset()
    d.qpos[qadr(m, "climb.tip")] = params(w)["travel"]
    mujoco.mj_forward(m, d)
    g = m.geom(geom).id
    p, a = d.geom_xpos[g].copy(), d.geom_xmat[g].reshape(3, 3)[:, 2].copy()
    seat = seat_point(w)
    pivot = p + float((seat - p) @ a) * a
    q = w.free_qadr
    d.qpos[q:q + 3] += pivot - seat
    mujoco.mj_forward(m, d)
    r = d.subtree_com[w.robot_body] - pivot
    r -= float(r @ a) * a
    down = np.array([0.0, 0.0, -1.0]) - a[2] * a
    phi = math.atan2(float(np.cross(r, down) @ a), float(r @ down))
    turn = np.zeros(9)
    rq = np.array([math.cos(phi / 2), *(math.sin(phi / 2) * a)])
    mujoco.mju_quat2Mat(turn, rq)
    d.qpos[q:q + 3] = pivot + turn.reshape(3, 3) @ (d.qpos[q:q + 3] - pivot)
    quat = np.zeros(4)
    mujoco.mju_mulQuat(quat, rq, d.qpos[q + 3:q + 7].copy())
    d.qpos[q + 3:q + 7] = quat
    d.qvel[:] = 0.0
    mujoco.mj_forward(m, d)
    step(w, settle)
    return pivot


def run(w: World, seconds: float) -> list[dict]:
    """Step in TICK ticks; each tick's climb sample, consumed as the scenario runner does."""
    hal = HalState()
    per_tick = round(TICK / w.model.opt.timestep)
    out = []
    for _ in range(round(seconds / TICK)):
        for _ in range(per_tick):
            w.step(hal)
        out.append({**w.climb.sample(), **(w.terrain.sample() if w.terrain is not None else {})})
        tick_checks([], SimpleNamespace(world=w), w.data.time)
    return out


def read(w: World, name: str):
    return resolve_signal(name, w).read(SimpleNamespace(world=w))


def robot_geoms(w: World) -> list[int]:
    m = w.model
    root = m.body_rootid[w.robot_body]
    return [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == root
            and (m.geom_contype[g] or m.geom_conaffinity[g])]


def bottom(w: World, g: int) -> float:
    """Geom g's lowest point (boxes and capsules exactly)."""
    m, d = w.model, w.data
    R = d.geom_xmat[g].reshape(3, 3)
    s = m.geom_size[g]
    if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX:
        return float(d.geom_xpos[g][2] - np.abs(R[2]) @ s)
    assert m.geom_type[g] == mujoco.mjtGeom.mjGEOM_CAPSULE
    return float(d.geom_xpos[g][2] - abs(R[2, 2]) * s[1] - s[0])


def top(w: World, g: int) -> float:
    """Geom g's highest point (boxes and capsules exactly)."""
    return 2 * float(w.data.geom_xpos[g][2]) - bottom(w, g)


def lowest(w: World) -> float:
    return min(bottom(w, g) for g in robot_geoms(w))


def touching(w: World, name: str) -> bool:
    m, d = w.model, w.data
    root = m.body_rootid[w.robot_body]
    other = m.geom(name).id
    return any((c.geom1 == other and m.body_rootid[m.geom_bodyid[c.geom2]] == root)
               or (c.geom2 == other and m.body_rootid[m.geom_bodyid[c.geom1]] == root) for c in d.contact[:d.ncon])


def high_rung() -> Bar:
    return {e.name: e for e in load_range("2022-hangar", ".").elements if isinstance(e, Bar)}["high"]


def hangar_copy(tmp_path, name: str, under: list[dict]) -> str:
    """The 2022 HIGH rung alone (rigid, no posts) with the 2022 levels, and `under`."""
    hi = high_rung()
    return write_range(tmp_path, name, "lowest-rung", LEVELS_2022,
                       [bar("high", hi.center[0], hi.center[1], hi.height, "HIGH"), *under])


def bumpers(w: World) -> list[tuple[float, float]]:
    """The front and back bumpers' (lowest point, center x), the lower first."""
    m, d = w.model, w.data
    out = [(bottom(w, g), float(d.geom_xpos[g][0])) for g in range(m.ngeom)
           if m.geom_bodyid[g] == w.robot_body and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX
           and m.geom_size[g][0] == pytest.approx(0.04) and m.geom_size[g][1] > 0.4]
    assert len(out) == 2
    return sorted(out)


def supported(tmp_path, under: str) -> tuple[World, dict]:
    """Hung from a copy of the 2022 HIGH rung with `under` the lower bumper, 5 mm into its underside (the chassis
    hangs tilted a few degrees): a MID rung ("mid"), a truss box ("truss", not ground) or a floor box ("floor").
    Pushed up at that end, the robot turns a little about the rung and leans on it. Returns the world and a tick's
    sample after 0.3 s."""
    hi = high_rung()
    start = (hi.center[0] + 0.6, hi.center[1], 0.0)
    w = world(hangar_copy(tmp_path, "high-only", []), start)
    hang_on(w, "element.high.pipe")
    (z, x), _ = bumpers(w)
    y = float(w.data.xpos[w.robot_body][1])
    if under == "mid":
        e = bar("mid", x, y, z + 0.005 - PIPE / 2, "MID", length=1.5)
    else:
        e = {"name": under, "kind": "box", "center": [x, y], "yaw_deg": 0.0, "size": [0.1, 1.5, 0.1],
             "base_height": z + 0.005 - 0.1, "ground": under == "floor"}
    w = world(hangar_copy(tmp_path, f"under-{under}", [e]), start)
    hang_on(w, "element.high.pipe", settle=0.3)
    return w, run(w, 0.05)[-1]


# ------------------------------------------------------------------ hanging, level and points


def test_hanging_and_level_lowest_rung(tmp_path):
    hi = high_rung()
    w = world(hangar_copy(tmp_path, "high-only", []), (hi.center[0] + 0.6, hi.center[1], 0.0))
    assert w.climb is not None                   # a range with bars has one from the start
    assert read(w, "climb.hanging") == 0 and read(w, "climb.level") == 0 and read(w, "climb.points") == 0
    hang_on(w, "element.high.pipe", settle=0.3)
    s = run(w, 0.05)[-1]
    assert (s["climb.hanging"], s["climb.level"], s["climb.points"]) == (1, 3, 10)   # HIGH: the third level

    w, s = supported(tmp_path, "mid")
    assert touching(w, "element.high.pipe") and touching(w, "element.mid.pipe")
    assert (s["climb.hanging"], s["climb.level"], s["climb.points"]) == (1, 2, 6)    # the lowest rung touched: MID
    w, s = supported(tmp_path, "truss")
    assert touching(w, "element.high.pipe") and touching(w, "element.truss.box")
    assert (s["climb.hanging"], s["climb.level"], s["climb.points"]) == (1, 3, 10)   # a truss isn't ground


def test_touching_ground_scores_nothing(tmp_path):
    w, s = supported(tmp_path, "floor")
    assert touching(w, "element.high.pipe") and touching(w, "element.floor.box")
    assert (s["climb.hanging"], s["climb.level"], s["climb.points"]) == (0, 0, 0)
    w, _ = robot(climber())                      # standing on the HANGAR's floor protection, touching no rung
    assert run(w, 0.05)[-1]["climb.hanging"] == 0


def test_hang_rule_with_level_bonus():
    sw = next(e for e in load_range("2020-switch", ".").elements if e.name == "generator_switch")
    tilt = lambda w: math.degrees(float(w.data.qpos[w.parts.layout.switches["generator_switch"]]))
    short = climber(base_length=0.6)         # the chassis 0.5 m up: clear of the carpet at the switch's stop
    w = world("2020-switch", (sw.center[0], sw.center[1] - 1.0, 90.0), short)    # under the switch's hinge
    hang_on(w, "element.generator_switch.bar", settle=3.0)
    s = run(w, 0.05)[-1]
    assert abs(tilt(w)) < sw.level_deg
    assert (s["climb.hanging"], s["climb.level"], s["climb.points"]) == (1, 1, 25 + 15)
    w = world("2020-switch", (sw.center[0] - 0.837, sw.center[1] - 1.0, 90.0), short)   # on one exposed section
    hang_on(w, "element.generator_switch.bar", settle=3.0)
    s = run(w, 0.05)[-1]
    assert abs(tilt(w)) > sw.level_deg                                       # at its stop: not level
    assert (s["climb.hanging"], s["climb.level"], s["climb.points"]) == (1, 1, 25)


def test_lowest_point_rule(tmp_path):
    rungs = {e.name: e for e in load_range("2013-pyramid", ".").elements if isinstance(e, Bar)}
    l1, l2, l3 = rungs["l1_s"], rungs["l2_s"], rungs["l3_s"]
    rng = write_range(tmp_path, "pyramid-rungs", "lowest-point", {"LEVEL1": 10, "LEVEL2": 20, "LEVEL3": 30},
                      [bar("l1", 1.0, 4.0, l1.height, "LEVEL1", l1.diameter),
                       bar("l2", 3.0, 4.0, l2.height, "LEVEL2", l2.diameter),
                       bar("l3", 5.0, 4.0, l3.height, "LEVEL3", l3.diameter)])
    top = lambda b: b.height + b.diameter / 2
    w = world(rng, (5.5, 4.0, 0.0))
    hang_on(w, "element.l3.pipe", settle=0.3)
    s = run(w, 0.05)[-1]
    assert top(l1) < lowest(w) < top(l2)          # above the 30 in rung, below the 60 in one: LEVEL2
    assert (s["climb.hanging"], s["climb.level"], s["climb.points"]) == (1, 2, 20)
    hang_on(w, "element.l2.pipe", settle=0.3)
    s = run(w, 0.05)[-1]
    assert lowest(w) < top(l1)                     # held below the 30 in rung: LEVEL1
    assert (s["climb.hanging"], s["climb.level"], s["climb.points"]) == (1, 1, 10)


# ------------------------------------------------------------------ swing and hook load


def test_hook_load_sums_the_whole_hook(tmp_path):
    w, mid = robot(climber(hook_count=2), one_rung(tmp_path, rigid=True))    # a rigid rung: no bounce on its spring
    hang_on(w, "element.mid.pipe", settle=1.0)
    s = run(w, 0.05)[-1]
    share = MASS * G / 2
    loads = [s["climb.hook.climb.hook0.load"], s["climb.hook.climb.hook1.load"]]
    assert loads == pytest.approx([share, share], rel=0.05)
    assert s["climb.hook_load"] == max(loads)
    assert read(w, "climb.hook.climb.hook1.load") == pytest.approx(share, rel=0.05)
    with pytest.raises(ScenarioError, match="climb.hook0, climb.hook1"):
        resolve_signal("climb.hook.climb.hook2.load", w)


def test_swing_about_the_bar_hung_from():
    w, mid = robot(climber())
    axis = hang(w, mid, swing_deg=10.0)
    step(w, 0.01)                               # the hook's crown sinks into its contact
    w.climb.consume()
    w.step(HalState())
    s = w.climb.sample()
    assert s["climb.hanging"] == 1
    assert s["climb.swing"] == pytest.approx(math.degrees(swing_angle(w, axis)), abs=0.05)
    assert s["climb.swing"] == pytest.approx(10.0, abs=0.5)      # positive toward +x, as the helper's
    per_tick = round(TICK / w.model.opt.timestep)
    held, worst = [], []
    for _ in range(round(1.0 / TICK)):         # through a half swing: each tick holds its largest magnitude
        angles = []
        for _ in range(per_tick):
            w.step(HalState())
            angles.append(math.degrees(swing_angle(w, axis)))
        held.append(w.climb.sample()["climb.swing"])
        worst.append(max(angles, key=abs))
        tick_checks([], SimpleNamespace(world=w), w.data.time)
    assert held == pytest.approx(worst, abs=0.1)    # the helper's pivot is the rung at rest, the monitor's the
                                                    # sprung rung where it is
    assert min(held) < -8.0                     # it swings through to the other side, a little lower


def test_a_brushed_bar_stops_being_the_swings_bar(tmp_path):
    mid = mid_rung()
    rng = write_range(tmp_path, "two-rungs", "lowest-rung", LEVELS_2022,
                      [bar("a", mid.center[0], mid.center[1], mid.height, "MID"),
                       # b on a stiff slide: a jointed body moves with its body_pos, a static one doesn't
                       {**bar("b", mid.center[0] + 3.0, mid.center[1], mid.height, "HIGH"), "stiffness": 1e6,
                        "damping": 2000.0}])
    w = world(rng, (mid.center[0] - 0.1, mid.center[1], 0.0))
    axis_a = hang_on(w, "element.a.pipe", settle=0.3)
    m, d = w.model, w.data
    b, tip = m.body("element.b").id, m.geom("climb.hook0.tip").id
    away = m.body_pos[b].copy()

    def one_step() -> dict:
        w.climb.consume()
        w.step(HalState())
        return w.climb.sample()

    at = d.geom_xpos[tip].copy()                # bar b against the outside of hook 0's tip, 1 mm into it
    at[0] += m.geom_size[tip][0] + PIPE / 2 - 0.001
    m.body_pos[b] = at
    step(w, 0.005)
    assert touching(w, "element.b.pipe") and touching(w, "element.a.pipe")
    s = one_step()
    axis_b = d.geom_xpos[m.geom("element.b.pipe").id].copy()
    about_a, about_b = (math.degrees(swing_angle(w, x)) for x in (axis_a, axis_b))
    assert abs(about_a - about_b) > 1.5                                 # the two bars give different swings
    assert s["climb.swing"] == pytest.approx(about_b, abs=0.3)          # hooked last: b
    m.body_pos[b] = away                        # b leaves; the hook still holds a
    step(w, 0.005)
    s = one_step()
    assert not touching(w, "element.b.pipe")
    assert s["climb.swing"] == pytest.approx(math.degrees(swing_angle(w, axis_a)), abs=0.3)


def test_hook_load_flips_the_force_when_the_hook_is_geom2():
    """A bar of two pieces of one hook, at rest on a plane (a plane sorts before a capsule: the hook is geom2) and on
    a box (a box sorts after: the hook is geom1). The whole hook carries the bar's weight only if the plane contact's
    force is flipped; unflipped, the two would nearly cancel."""
    m = mujoco.MjModel.from_xml_string("""
<mujoco><option timestep="0.001"/>
  <worldbody>
    <geom name="floor" type="plane" size="1 1 0.1"/>
    <geom name="block" type="box" size="0.03 0.03 0.025" pos="0.1 0 0.025"/>
    <body name="r" pos="0 0 0.06"><freejoint/>
      <geom name="r.hook0.a" type="capsule" fromto="-0.15 0 -0.05 -0.05 0 -0.05" size="0.01" mass="1"/>
      <geom name="r.hook0.b" type="capsule" fromto="0.05 0 0 0.15 0 0" size="0.01" mass="1"/>
    </body>
  </worldbody>
</mujoco>""")
    d = mujoco.MjData(m)
    for _ in range(300):
        mujoco.mj_step(m, d)
    hook = {m.geom("r.hook0.a").id, m.geom("r.hook0.b").id}
    sides = {("geom1" if c.geom1 in hook else "geom2") for c in d.contact[:d.ncon]}
    assert sides == {"geom1", "geom2"}
    mon = ClimbMonitor(SimpleNamespace(model=m, data=d, robot_body=m.body("r").id, parts=None))
    assert mon.hooks == ["r.hook0"]
    s = mon.sample()
    assert s["climb.hook.r.hook0.load"] == pytest.approx(2.0 * 9.81, rel=0.01)


class FakeSession:
    """Enough of a Session for _run_step: the world stepped in 5 ms ticks, no robot program."""

    def __init__(self, world: World):
        self.world, self.robot = world, None
        self.bridge = SimpleNamespace(ds=SimpleNamespace(clear_joysticks=lambda: None), hal=HalState())

    @property
    def t(self) -> float:
        return self.world.time

    def set_joystick(self, port, **kw) -> None:
        pass

    def set_mode(self, mode) -> None:
        pass

    async def advance(self, dt: float, on_step=None, **kw) -> None:
        per_tick = round(TICK / self.world.model.opt.timestep)
        for _ in range(round(dt / TICK)):
            for _ in range(per_tick):
                self.world.step(HalState())
            if on_step is not None:
                on_step(self.t)


def test_step_start_consumes_the_climb_peaks(tmp_path, monkeypatch):
    """A re-hook impact before a step isn't the step's: its `always` check reads the peaks from its own start."""
    def hold_step(consume: bool) -> runner_mod.StepReport:
        w, _ = robot(climber())
        hang_on(w, "element.mid.pipe")
        w.data.qpos[w.free_qadr + 2] += 0.02    # dropped 2 cm onto the rung
        mujoco.mj_forward(w.model, w.data)
        step(w, 2.5)                            # settled; nothing consumed, so the impact is still held
        assert w.climb.sample()["climb.hook_load"] > 2.0 * MASS * G
        if not consume:
            monkeypatch.setattr(runner_mod, "consume_peaks", lambda world: None)
        scenario = load_scenario_raw({"config": "robot.toml", "step": [
            {"name": "hold", "mode": "disabled", "duration": 0.1,
             "check": [{"always": {"signal": "climb.hook_load", "max": 1.3 * MASS * G}}]}]}, tmp_path / "s.toml")
        rep = runner_mod.StepReport("hold", "disabled", 0.0)
        asyncio.run(runner_mod._run_step(FakeSession(w), scenario.steps[0], 0, tmp_path, rep,
                                         runner_mod._Hooks(None, None)))
        return rep
    assert hold_step(consume=True).passed
    assert not hold_step(consume=False).passed     # without it the first tick reads the impact


def test_hanging_robot_is_not_stranded():
    mid = {e.name: e for e in load_range("2022-hangar", ".").elements if isinstance(e, Bar)}["mid"]
    w = world("2022-hangar", (mid.center[0] - 0.1, mid.center[1], 0.0), climber(ids={"extend": "PWM:8"}),
              drive=SWERVE)
    hang_on(w, "element.mid.pipe", settle=2.5)    # placed on an unloaded rung, it bounces on the rung's spring first
    samples = run(w, 1.0)
    assert all(x["climb.hanging"] == 1 for x in samples)
    assert all(x["robot.wheels_down"] == 0 for x in samples)
    assert not any(x["robot.stranded"] for x in samples)
    assert w.terrain.first_stranded is None


# ------------------------------------------------------------------ extension and height


def beyond_frame(w: World, half: float = 0.35) -> float:
    """The farthest any non-chassis colliding geom reaches beyond the chassis's 0.7 m square frame, in the chassis's
    own x-y plane: boxes by their corners, capsules by their end caps."""
    m, d = w.model, w.data
    b = w.robot_body
    Rc, pc = d.xmat[b].reshape(3, 3), d.xpos[b]
    far = 0.0
    for g in robot_geoms(w):
        if m.geom_bodyid[g] == b:
            continue
        R, p, s = d.geom_xmat[g].reshape(3, 3), d.geom_xpos[g], m.geom_size[g]
        if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX:
            pts = [p + R @ ((np.array(c) * 2 - 1) * s) for c in np.ndindex(2, 2, 2)]
            rad = 0.0
        else:
            pts = [p + R[:, 2] * s[1], p - R[:, 2] * s[1]]
            rad = s[0]
        for x in pts:
            lx, ly, _ = Rc.T @ (x - pc)
            dx, dy = max(0.0, abs(lx) + rad - half), max(0.0, abs(ly) + rad - half)
            far = max(far, math.hypot(dx, dy))
    return far


def test_extension_and_height_signals():
    cfg = {"part": [{"name": "drive", "stock": "chassis"},
                    {"name": "climb", "stock": "climber", "attach": "drive.top", "pivot": "articulated",
                     "ids": {"extend": "PWM:2", "pivot": "PWM:3"}}],
           "field": {"obstacle": False}, "robot": {"start_pose": [2.0, 4.0, 0.0]}}
    w = World(cfg, None)
    assert w.climb is None
    m, d = w.model, w.data
    h0 = read(w, "climb.height")
    assert w.climb is not None                   # made on demand
    assert read(w, "climb.extension") == 0.0     # upright in the middle of the frame
    assert h0 == pytest.approx(max(top(w, g) for g in robot_geoms(w)), abs=0.003)   # bounding boxes: a turned
                                                                                    # capsule's reads a little high
    d.qpos[qadr(m, "climb.tip")] = 0.25
    mujoco.mj_forward(m, d)
    assert read(w, "climb.height") - h0 == pytest.approx(0.25, abs=1e-6)
    d.qpos[qadr(m, "climb.pivot")] = math.radians(28.0)
    mujoco.mj_forward(m, d)
    ext = read(w, "climb.extension")
    assert ext > 0.1
    assert ext == pytest.approx(beyond_frame(w), abs=0.005)
    q = w.free_qadr
    d.qpos[q + 3:q + 7] = [math.cos(math.radians(37) / 2), 0.0, 0.0, math.sin(math.radians(37) / 2)]
    mujoco.mj_forward(m, d)
    assert read(w, "climb.extension") == pytest.approx(ext, abs=1e-9)     # the robot's own frame, any heading
    w.reset()
    s = run(w, 0.3)[-1]                         # settled on the carpet: the tick's peak is where it is
    assert s["climb.extension"] == 0.0
    assert s["climb.height"] == pytest.approx(max(top(w, g) for g in robot_geoms(w)), abs=0.003)   # settled


# ------------------------------------------------------------------ cost and names


def test_no_monitor_without_climbing_elements():
    path = ROOT / "configs" / "fastmode.toml"
    w = World(tomllib.loads(path.read_text()), path.parent)
    for _ in range(100):
        w.step(HalState())
    w.reset()
    w.reset_robot()
    assert w.climb is None
    with pytest.raises(ScenarioError, match="climb.hanging"):
        resolve_signal("climb.altitude", w)
    assert w.climb is None
    station = World({"part": [{"name": "drive", "stock": "chassis"}],
                     "range": {"file": "2023-charge-station", "start": "approach", "floor_balls": False}}, None)
    assert station.terrain is not None and station.climb is None     # field elements, but nothing to hang from
    resolve_signal("climb.hanging", station)
    assert station.climb is not None


def test_picker_lists_the_climb_signals():
    from scrimmage.assembler.signals import signals
    w, _ = robot(climber(hook_count=2))
    out = signals(w)
    assert {"climb.extension", "climb.height", "climb.hook_load", "climb.hook.climb.hook0.load",
            "climb.hook.climb.hook1.load"} <= set(out["fixed"])
    assert {"climb.hanging", "climb.level", "climb.points", "climb.swing"} <= set(out["range"])
    plain = signals(World({"part": [{"name": "drive", "stock": "chassis"}], "field": {"obstacle": False}}, None))
    assert not any(n.startswith("climb.") for n in plain["fixed"])      # no climber, nothing to hang from
