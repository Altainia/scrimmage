"""Hanging physics, the guard for sub-project 12: a 60 kg robot hung by the stock climber's contact hooks holds,
swings with the compound pendulum's period, loses amplitude to hook friction at about the Coulomb rate, lets go by
geometry (or by its latch's release motor) and re-hooks with plausible loads. The expectations are the research
notes' 4.1 to 4.4 (docs/superpowers/specs/2026-10-29-climbing-design.md cites them).

The robot is a stock chassis topped up to 60 kg with one climber on its top mount, in a World, so its motors run
through their real bindings (PWM, brake or coast at neutral). It hangs from the 2022 MID rung: the real HANGAR's for
holding and swinging, and a range of that rung alone (or a rigid copy) where a big swing or a fall must not meet the
other rungs. The swing is about the rung's axis (along y): the hooks open toward +x, the robot faces +x."""

from __future__ import annotations

import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.parts.stock.climber import HOOK_FRICTION, HOOK_RADIUS, LATCH_RADIUS, hook_seat
from scrimmage.protocol import HalState
from scrimmage.testrange.elements import IN, Bar
from scrimmage.testrange.spec import load_range
from scrimmage.world import World

MASS = 60.0               # kg: the hung robot
G = 9.81
PIPE_R = 1.66 * IN / 2    # m: the 2022 rung's radius, 21.1 mm (manual 5.4.2)
HANG_TRAVEL = 0.25        # m: hung at full extension, the chassis about 13 cm off the carpet under the MID rung
CLIMB_LENGTH = 1.2        # m: the climbing robot's tubes; retracted and hung from MID its chassis is about 7 cm up


def mid_rung() -> Bar:
    return {e.name: e for e in load_range("2022-hangar", ".").elements if isinstance(e, Bar)}["mid"]


def one_rung(tmp_path, rigid: bool = False) -> str:
    """A range of the 2022 MID rung alone (its sprung slide too, unless rigid): a big swing or a fall meets nothing
    else."""
    mid = mid_rung()
    lines = ['[range]', 'name = "2022 MID rung"', 'ball = "2026-fuel"', '[[element]]', 'name = "mid"', 'kind = "bar"',
             f'center = [{mid.center[0]!r}, {mid.center[1]!r}]', f'yaw_deg = {mid.yaw_deg!r}',
             f'length = {mid.length!r}', f'diameter = {mid.diameter!r}', f'height = {mid.height!r}', 'posts = false',
             f'friction = {mid.friction!r}']
    if not rigid:
        lines += [f'stiffness = {mid.stiffness!r}', f'damping = {mid.damping!r}']
    path = tmp_path / ("rigid.toml" if rigid else "rung.toml")
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def climber(**params) -> dict:
    return {"name": "climb", "stock": "climber", "attach": "drive.top", "stages": 1, "base_length": 0.9,
            "travel": HANG_TRAVEL, "latch": "none", "ids": {"extend": "PWM:2"}, **params}


def robot(climb: dict, rng: str = "2022-hangar") -> tuple[World, Bar]:
    """A World with the chassis and `climb`, MASS kg in all, standing on the range just short of the MID rung."""
    mid = mid_rung()
    cfg = {"part": [{"name": "drive", "stock": "chassis", "mass": 50.0}, climb],
           "range": {"file": rng, "floor_balls": False}, "battery": {"nominal_voltage": 12.0, "resistance": 0.0},
           "robot": {"start_pose": [mid.center[0] - 0.1, mid.center[1], 0.0]}}
    w = World(cfg, Path("."))
    cfg["part"][0]["mass"] = 50.0 + MASS - float(w.model.body_subtreemass[w.robot_body])
    w = World(cfg, Path("."))
    assert w.model.body_subtreemass[w.robot_body] == pytest.approx(MASS)
    return w, mid


def qadr(m, joint: str) -> int:
    return int(m.jnt_qposadr[m.joint(joint).id])


def params(w: World) -> dict:
    return next(i for i in w.parts.instances if i.name == "climb").stock_params


def seat_point(w: World) -> np.ndarray:
    """Where hook 0 holds the bar's axis, in world coordinates."""
    m, d = w.model, w.data
    p = params(w)
    x, z = hook_seat(p)
    y = 0.0 if p["hook_count"] == 1 else p["hook_spacing"] / 2
    tip = m.body("climb.tip").id
    return d.xpos[tip] + d.xmat[tip].reshape(3, 3) @ np.array([x, y, z])


def bar_axis(w: World, bar: Bar) -> np.ndarray:
    return w.data.geom_xpos[w.model.geom(f"element.{bar.name}.pipe").id].copy()


def bar_in_tip(w: World, bar: Bar) -> np.ndarray:
    """The bar's axis in the climber tip's frame (x toward the hooks' opening, z up the climber)."""
    m, d = w.model, w.data
    tip = m.body("climb.tip").id
    return d.xmat[tip].reshape(3, 3).T @ (bar_axis(w, bar) - d.xpos[tip])


def _place(w: World, bar: Bar, turn_deg: float, extension: float) -> np.ndarray:
    m, d = w.model, w.data
    w.reset()
    d.qpos[qadr(m, "climb.tip")] = extension
    mujoco.mj_forward(m, d)
    seat = seat_point(w)
    axis = bar_axis(w, bar)
    axis[1] = seat[1]
    th = math.radians(turn_deg)
    rot = np.array([[math.cos(th), 0.0, math.sin(th)], [0.0, 1.0, 0.0], [-math.sin(th), 0.0, math.cos(th)]])
    q = w.free_qadr
    d.qpos[q:q + 3] = axis + rot @ (d.qpos[q:q + 3] - seat)
    quat = np.zeros(4)
    mujoco.mju_mulQuat(quat, np.array([math.cos(th / 2), 0.0, math.sin(th / 2), 0.0]), d.qpos[q + 3:q + 7].copy())
    d.qpos[q + 3:q + 7] = quat
    d.qvel[:] = 0.0
    mujoco.mj_forward(m, d)
    return axis


def hang(w: World, bar: Bar, swing_deg: float = 0.0, settle: float = 0.0) -> np.ndarray:
    """Hang the robot on `bar` by its hooks (seated on the crown, the climber at full extension), at rest with its
    CoM `swing_deg` from under the bar's axis (positive toward +x), then step `settle` seconds. Returns a point on
    the bar's axis, the pivot."""
    ext = params(w)["travel"]
    axis = _place(w, bar, 0.0, ext)
    lean = math.degrees(swing_angle(w, axis))       # where the CoM hangs with the climber straight up
    axis = _place(w, bar, lean - swing_deg, ext)
    step(w, settle)
    return axis


def swing_angle(w: World, axis: np.ndarray) -> float:
    """The CoM's angle from straight under the pivot (rad), positive toward +x."""
    c = w.data.subtree_com[w.robot_body]
    return math.atan2(c[0] - axis[0], axis[2] - c[2])


def pivot_inertia(w: World, axis: np.ndarray) -> tuple[float, float]:
    """(d, k): the pivot-to-CoM distance and the robot's radius of gyration about its CoM, about the bar's axis (y),
    from the model's bodies as they are now."""
    m, d = w.model, w.data
    root = m.body_rootid[w.robot_body]
    i_pivot = 0.0
    for b in range(m.nbody):
        if m.body_rootid[b] != root:
            continue
        rb = d.ximat[b].reshape(3, 3)
        r = d.xipos[b] - axis
        i_pivot += (rb @ np.diag(m.body_inertia[b]) @ rb.T)[1, 1] + m.body_mass[b] * (r[0] ** 2 + r[2] ** 2)
    c = d.subtree_com[w.robot_body] - axis
    dist = math.hypot(c[0], c[2])
    return dist, math.sqrt(i_pivot / MASS - dist ** 2)


def step(w: World, seconds: float, hal: HalState | None = None, each=None) -> None:
    hal = hal or HalState()
    for _ in range(round(seconds / w.model.opt.timestep)):
        w.step(hal)
        if each is not None:
            each()


def swing(w: World, axis: np.ndarray, seconds: float) -> tuple[np.ndarray, np.ndarray]:
    ts, th = [], []
    step(w, seconds, each=lambda: (ts.append(w.data.time), th.append(swing_angle(w, axis))))
    return np.array(ts), np.array(th)


def zero_crossings(ts: np.ndarray, th: np.ndarray) -> np.ndarray:
    i = np.where(np.sign(th[:-1]) * np.sign(th[1:]) < 0)[0]
    return ts[i] + (ts[i + 1] - ts[i]) * th[i] / (th[i] - th[i + 1])


def half_swing_amplitudes(th: np.ndarray) -> np.ndarray:
    """The largest |angle| of each half swing (between zero crossings), from the start."""
    cuts = [0, *(np.where(np.sign(th[:-1]) * np.sign(th[1:]) < 0)[0] + 1), len(th)]
    return np.array([np.abs(th[a:b]).max() for a, b in zip(cuts, cuts[1:]) if b > a])


def hook_geoms(m, hook: int | None = None) -> set[int]:
    prefix = "climb.hook" if hook is None else f"climb.hook{hook}."
    return {g for g in range(m.ngeom) if m.geom(g).name.startswith(prefix)}


def touching(w: World, geoms: set[int], other: int) -> bool:
    return any((c.geom1 in geoms and c.geom2 == other) or (c.geom2 in geoms and c.geom1 == other)
               for c in w.data.contact[:w.data.ncon])


def on_carpet(w: World) -> bool:
    m = w.model
    root = m.body_rootid[w.robot_body]
    carpet = m.geom("carpet").id
    mine = {g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == root}
    return touching(w, mine, carpet)


# ------------------------------------------------------------------ holding and swinging


def test_sixty_kg_robot_holds_thirty_seconds():
    w, mid = robot(climber(hook_count=2))
    m, d = w.model, w.data
    axis = hang(w, mid, settle=2.0)
    pipe = m.geom("element.mid.pipe").id
    hooks = hook_geoms(m)
    stem = m.geom("climb.hook0.stem").id
    com0 = d.subtree_com[w.robot_body].copy()
    hook0 = d.geom_xpos[stem] - d.geom_xpos[pipe]
    deepest = [0.0]

    def sink():
        for c in d.contact[:d.ncon]:
            if pipe in (c.geom1, c.geom2) and (c.geom1 in hooks or c.geom2 in hooks):
                deepest[0] = max(deepest[0], -c.dist)

    step(w, 30.0, each=sink)
    assert not on_carpet(w)
    assert touching(w, hooks, pipe)
    creep = np.linalg.norm(d.geom_xpos[stem] - d.geom_xpos[pipe] - hook0)     # the hook along or round the rung
    assert creep < 0.01
    assert np.linalg.norm(d.subtree_com[w.robot_body] - com0) < 0.01
    assert 0.0 < deepest[0] < 0.002                         # the hook-rung contact sinks under 2 mm
    assert abs(swing_angle(w, axis)) < math.radians(1.0)


def test_telescope_stop_holds_a_hanging_robot_within_2mm():
    """Hung at full extension, the robot's weight pulls the telescope against its travel stop; the stop's stiff
    limit_solref holds 60 kg within 2 mm (MuJoCo's default stop sagged about 15 mm), and the robot settles: no
    bounce kept going between the stop and the rung's spring."""
    w, mid = robot(climber(hook_count=2))
    m, d = w.model, w.data
    hang(w, mid, settle=5.0)
    over = d.qpos[qadr(m, "climb.tip")] - m.jnt_range[m.joint("climb.tip").id][1]
    assert not on_carpet(w)
    assert -0.0005 < over < 0.002
    zs = []
    step(w, 1.0, each=lambda: zs.append(d.subtree_com[w.robot_body][2]))
    assert max(zs) - min(zs) < 0.001


def test_every_telescope_stage_has_the_stiff_stop():
    w, _ = robot(climber(stages=3, travel=0.6))
    m = w.model
    slides = [j for j in range(m.njnt) if m.joint(j).name.startswith("climb.")
              and m.jnt_type[j] == mujoco.mjtJoint.mjJNT_SLIDE]
    assert len(slides) == 3
    for j in slides:
        assert m.jnt_solref[j][0] < 0.01


def test_free_swing_period_matches_compound_pendulum():
    w, mid = robot(climber(hook_material="uhmw"))     # low friction: the swing barely decays
    axis = hang(w, mid, settle=1.0)
    d_cm, k = pivot_inertia(w, axis)
    want = 2 * math.pi * math.sqrt((d_cm ** 2 + k ** 2) / (G * d_cm))     # research notes 4.1
    assert 1.0 < d_cm < 1.3 and 0.15 < k < 0.35
    axis = hang(w, mid, swing_deg=8.0)
    ts, th = swing(w, axis, 3.5 * want)
    up = zero_crossings(ts, th)[::2]                  # every other zero crossing: one full period apart
    assert len(up) >= 3
    assert np.mean(np.diff(up)) == pytest.approx(want, rel=0.05)


@pytest.mark.parametrize("material", ["steel", "uhmw"])
def test_hook_friction_decay_matches_coulomb_estimate(material):
    w, mid = robot(climber(hook_material=material))
    axis = hang(w, mid, settle=1.0)
    d_cm, _ = pivot_inertia(w, axis)
    want = 4 * HOOK_FRICTION[material] * PIPE_R / d_cm     # research notes 4.2: rad lost per full cycle
    axis = hang(w, mid, swing_deg=25.0)
    _, th = swing(w, axis, 9.5)
    amp = half_swing_amplitudes(th)
    # same-side swings one cycle apart, from the first swing back (the release's first half swing settles the hook)
    same_side = amp[1:9:2]
    assert len(same_side) == 4 and same_side[0] > math.radians(20.0)
    per_cycle = (same_side[0] - same_side[-1]) / (len(same_side) - 1)
    assert want / 1.5 < per_cycle < want * 1.5
    # Coulomb friction loses amplitude linearly: each cycle's loss about the same (viscous loss would shrink with it)
    losses = -np.diff(same_side)
    assert losses.max() < 1.3 * losses.min()


@pytest.mark.parametrize("latch", ["none", "passive"])
def test_big_swing_keeps_the_bar_in_the_hook_and_only_a_latch_holds_an_unloaded_hook(latch, tmp_path):
    """Released from rest 50 deg out (as from MID to HIGH), the bar stays on the crown, latch or not: while the CoM
    is below the bar the hook is pressed onto it (g cos(theta) plus the centripetal term stays positive). The swing
    alone can't walk a J hook off, so the unlatched hook lets go only when the load reverses: released 120 deg out,
    above the bar, the robot drops through its own hook. A latch catches it."""
    rng = one_rung(tmp_path)
    w, mid = robot(climber(latch=latch), rng)
    p = params(w)
    seat = np.array(hook_seat(p))
    tip_end = p["base_length"]                               # the hook's tip ends level with the tip tube's top

    axis = hang(w, mid, swing_deg=50.0)
    worst = [0.0]
    step(w, 4.0, each=lambda: worst.__setitem__(0, max(worst[0], np.hypot(*(bar_in_tip(w, mid)[[0, 2]] - seat)))))
    assert worst[0] < 0.005                                  # seated on the crown throughout
    assert abs(swing_angle(w, axis)) < math.radians(50.0)

    axis = hang(w, mid, swing_deg=120.0)
    lowest = [1.0]
    step(w, 4.0, each=lambda: lowest.__setitem__(0, min(lowest[0], bar_in_tip(w, mid)[2] - tip_end)))
    if latch == "none":
        assert lowest[0] < -0.5                              # the bar left through the mouth; the robot fell away
        assert not touching(w, hook_geoms(w.model), w.model.geom("element.mid.pipe").id)
    else:
        assert lowest[0] > 0.005                             # the shut latch held the bar above its hinge
        assert np.hypot(*(bar_in_tip(w, mid)[[0, 2]] - seat)) < 0.005    # and the robot swung back onto the crown
        assert not on_carpet(w)


# ------------------------------------------------------------------ catching and letting go, through the motors

SEAT_CLEARANCE = 0.13     # m: the crown starts this far above the bar's axis, the whole hook above the bar
HANG_EXT = 0.02           # m: the extension the robot hangs at (just off the retracted stop)
# Duty cycle cap while setting down. Stalled on the shut latches the extend motor then pushes about 1.4 kN; it takes
# about 3.5 kN to push the bar through both (measured), as a full-duty push (about 9 kN) does.
SET_DOWN_CAP = 0.15


class Program:
    """A scripted robot program: each step it writes PWM outputs: the extension (PWM 2) and the pivot (PWM 4) on
    position loops, the release motor (PWM 3) open loop."""

    def __init__(self, w: World):
        self.w = w
        self.qe, self.qp = qadr(w.model, "climb.tip"), qadr(w.model, "climb.pivot")

    def run(self, seconds: float, extend, pivot_deg: float = 0.0, release: float = 0.0, cap: float = 1.0,
            each=None) -> None:
        d = self.w.data
        t0 = d.time
        for _ in range(round(seconds / self.w.model.opt.timestep)):
            target = extend(d.time - t0) if callable(extend) else extend
            out = {"2": float(np.clip(20.0 * (target - d.qpos[self.qe]), -cap, cap)),
                   "4": float(np.clip(8.0 * (math.radians(pivot_deg) - d.qpos[self.qp]), -1.0, 1.0)),
                   "3": release}
            hal = HalState()
            for port, speed in out.items():
                hal.devices[("PWM", port)] = {"<init": True, "<speed": speed}
            self.w.step(hal)
            if each is not None:
                each()


def stand_under(w: World, bar: Bar) -> float:
    """The robot standing, its climber extended so the crown is SEAT_CLEARANCE above the bar's axis and right over
    it. Returns that extension."""
    m, d = w.model, w.data
    mujoco.mj_forward(m, d)
    seat, axis = seat_point(w), bar_axis(w, bar)
    ext = axis[2] + SEAT_CLEARANCE - seat[2]
    d.qpos[qadr(m, "climb.tip")] = ext
    d.qpos[w.free_qadr] += axis[0] - seat[0]
    mujoco.mj_forward(m, d)
    return ext


def climb_robot(tmp_path, latch: str, release_ids=None) -> tuple[World, Bar]:
    ids = {"extend": "PWM:2", "pivot": "PWM:4"}
    if release_ids is not None:
        ids["release"] = release_ids
    return robot(climber(base_length=CLIMB_LENGTH, travel=0.5, latch=latch, hook_count=2, pivot="articulated",
                         ids=ids), one_rung(tmp_path))


def catch(w: World, bar: Bar, prog: Program, start: float, each=None) -> None:
    """Retract at about 0.25 m/s: the hooks come down over the bar and lift the robot; then hang a second."""
    prog.run(1.5, lambda t: max(start - 0.25 * t, HANG_EXT), each=each)
    prog.run(1.0, HANG_EXT)
    assert not on_carpet(w)
    assert np.hypot(*(bar_in_tip(w, bar)[[0, 2]] - np.array(hook_seat(params(w))))) < 0.005


def test_release_by_geometry_without_a_latch_motor(tmp_path):
    """Hung by unlatched hooks, the robot extends: it sets down on the carpet (the hooks unload), the hooks rise
    clear of the bar, the pivot leans them away and retracting brings them down beside it: the hooks left the bar."""
    w, mid = climb_robot(tmp_path, "none")
    m = w.model
    p = params(w)
    start = stand_under(w, mid)
    prog = Program(w)
    catch(w, mid, prog, start)
    hooks, pipe = hook_geoms(m), m.geom("element.mid.pipe").id
    up = start + 0.06
    prog.run(4.0, up, cap=SET_DOWN_CAP)
    assert on_carpet(w)
    assert not touching(w, hooks, pipe)
    assert bar_in_tip(w, mid)[2] + PIPE_R < p["base_length"]       # the whole bar is below the hook tip's end
    prog.run(1.5, up, pivot_deg=-10.0)
    touched = []
    prog.run(2.0, HANG_EXT, pivot_deg=-10.0, each=lambda: touched.append(touching(w, hooks, pipe)))
    assert not any(touched)
    assert on_carpet(w)
    clear = HOOK_RADIUS + p["hook_throat"] / 2 + HOOK_RADIUS + PIPE_R       # throat center to the tip leg's far side
    assert abs(bar_in_tip(w, mid)[0] - hook_seat(p)[0]) > clear + 0.05


def test_release_slot_on_a_coast_motor_catches_holds_and_lets_go(tmp_path):
    """The `released` latch on two hooks (latch1 coupled to latch0) with its release motor bound to PWM 3 in coast
    neutral. In a real catch both latches are pushed open by the bar and spring shut behind it before the robot
    leaves the carpet; set down, the shut latches hold the bar in; the release motor opens both and the hooks let go."""
    w, mid = climb_robot(tmp_path, "released", {"pwm": 3, "neutral": "coast"})
    m, d = w.model, w.data
    p = params(w)
    hinge = p["base_length"]                                  # the latch hinges at the hook tip's end
    latches = [qadr(m, "climb.latch0"), qadr(m, "climb.latch1")]
    fingers = {m.geom("climb.hook0.latch").id, m.geom("climb.hook1.latch").id}
    pipe, hooks = m.geom("element.mid.pipe").id, hook_geoms(m)

    def angles() -> list[float]:
        return [math.degrees(d.qpos[q]) for q in latches]

    seen = {"widest": 0.0, "apart": 0.0, "left": None, "shut": None, "lift": None}
    start = stand_under(w, mid)
    floor_z = None

    def watch():
        nonlocal floor_z
        a = angles()
        seen["widest"] = max(seen["widest"], min(a))
        seen["apart"] = max(seen["apart"], abs(a[0] - a[1]))
        if touching(w, fingers, pipe):
            seen["left"], seen["shut"] = d.time, None
        elif seen["left"] is not None and seen["shut"] is None and max(a) < 3.0:
            seen["shut"] = d.time
        z = d.xpos[w.robot_body][2]
        if d.time > 0.05 and floor_z is None:
            floor_z = z                                       # settled on the carpet
        if floor_z is not None and seen["lift"] is None and z > floor_z + 0.005:
            seen["lift"] = d.time

    prog = Program(w)
    catch(w, mid, prog, start, each=watch)
    assert seen["widest"] > 80.0                              # the bar pushed both latches open
    assert seen["apart"] < 1.0                                # one linkage
    assert seen["shut"] - seen["left"] < 0.1                  # sprang shut within 0.1 s of the bar passing
    assert seen["shut"] < seen["lift"]                        # before the hooks took the robot's weight
    assert max(angles()) < 3.0

    up = start + 0.06
    prog.run(4.0, up, cap=SET_DOWN_CAP)                       # set down, then push the hooks up against the latches
    assert on_carpet(w)
    # the bar rests on the shut latches, pressed into them by the extend motor (about 1.4 kN at this cap: the soft
    # stop gives about 1 cm), and stays above their hinge: the hooks can't rise off it
    rest = bar_in_tip(w, mid)[2] - hinge
    assert 0.005 < rest < PIPE_R + LATCH_RADIUS + 0.003
    assert touching(w, fingers, pipe)
    assert max(angles()) < 10.0

    # Hung again, the bar on the crown clear of the latches' swing, the release motor opens both (a latch pressed
    # by the bar, or one whose swing the bar is in, won't turn); extending then sets the robot down and lifts the
    # hooks off the bar through their open mouths.
    prog.run(2.0, HANG_EXT)
    assert not on_carpet(w)
    prog.run(0.3, HANG_EXT, release=0.4)
    assert min(angles()) > 85.0
    assert not on_carpet(w)                                   # the crowns carry the robot
    prog.run(4.0, up, release=0.4, cap=SET_DOWN_CAP)
    assert on_carpet(w)
    assert min(angles()) > 85.0
    assert bar_in_tip(w, mid)[2] + PIPE_R < hinge             # the hooks rose off the bar
    prog.run(1.5, up, pivot_deg=-10.0, release=0.4)
    shut = []
    prog.run(0.1, up, pivot_deg=-10.0, each=lambda: shut.append(max(angles())))
    assert shut[-1] < 3.0                                     # coast: the springs shut them again within 0.1 s
    touched = []
    prog.run(2.0, HANG_EXT, pivot_deg=-10.0, each=lambda: touched.append(touching(w, hooks, pipe)))
    assert not any(touched)
    assert on_carpet(w)


# ------------------------------------------------------------------ re-hook loads


def peak_hook_force(w: World, seconds: float) -> float:
    """The largest total contact force on hook 0 (its geoms' contact forces summed as vectors), N."""
    m, d = w.model, w.data
    hook = hook_geoms(m, 0)
    f6 = np.zeros(6)
    peak = 0.0
    for _ in range(round(seconds / m.opt.timestep)):
        w.step(HalState())
        total = np.zeros(3)
        for i, c in enumerate(d.contact[:d.ncon]):
            if c.geom1 in hook or c.geom2 in hook:
                mujoco.mj_contactForce(m, d, i, f6)
                total += c.frame.reshape(3, 3).T @ f6[:3]
        peak = max(peak, float(np.linalg.norm(total)))
    return peak


def drop_onto_the_rung(rng: str, drop: float) -> float:
    w, mid = robot(climber(), rng)
    hang(w, mid)
    w.data.qpos[w.free_qadr + 2] += drop                     # the bar in the throat, the crown `drop` above it
    mujoco.mj_forward(w.model, w.data)
    return peak_hook_force(w, 0.5)


def test_rehook_impact_below_4kN_compliant_higher_rigid(tmp_path):
    """A 60 kg robot drops 3 cm onto its hook (research notes 4.4: F about m g (1 + sqrt(1 + 2 h / delta)), 2.7 kN
    for 2 cm onto 5 mm of give). The 2022 rung's spring (160 kN/m) keeps the peak under 4 kN; a rigid rung, with only
    the contact's and the climber's give, peaks measurably higher."""
    compliant = drop_onto_the_rung(one_rung(tmp_path), 0.03)
    rigid = drop_onto_the_rung(one_rung(tmp_path, rigid=True), 0.03)
    assert 3 * MASS * G < compliant < 4000.0
    assert rigid > 1.15 * compliant
