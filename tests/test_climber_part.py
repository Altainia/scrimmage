"""The stock climber: a telescope with contact hooks at its tip, an optional spring latch (with a `release` motor
slot) and an optional pivot. Every climber geom collides; the hooks hold a bar by contact."""

from __future__ import annotations

import itertools
import math

import mujoco
import numpy as np
import pytest

from scrimmage.parts.assemble import ROBOT_BITS, assemble, part_spec
from scrimmage.parts.model import PartError
from scrimmage.parts.stock import build_stock, stock_params
from scrimmage.parts.stock.climber import HOOK_FRICTION, LATCH_RADIUS, hook_seat
from scrimmage.testrange.build_elements import add_element
from scrimmage.testrange.elements import IN, Bar

OD = 1.66 * IN            # the 2022 rungs (GE-22150)
DT = 0.001


def assembled(**params):
    cfg = {"part": [{"name": "drive", "stock": "chassis"},
                    {"name": "climb", "stock": "climber", "attach": "drive.top", **params}]}
    return assemble(cfg, ".")


@pytest.mark.parametrize("latch, pivot, hooks", list(itertools.product(("none", "passive", "released"),
                                                                       ("none", "articulated"), (1, 2))))
def test_climber_builds_with_slots(latch, pivot, hooks):
    params = {"latch": latch, "pivot": pivot, "hook_count": hooks}
    part = build_stock("climber", params, "t")
    want = {"extend": "motor", "extend_encoder": "encoder"}
    if latch == "released":
        want["release"] = "motor"
    if pivot == "articulated":
        want |= {"pivot": "motor", "pivot_encoder": "encoder"}
    assert part.slot_kinds() == want
    built = assembled(stages=3, **params)
    m = built.model
    latches = [m.geom(f"climb.hook{i}.latch") for i in range(hooks)] if latch != "none" else []
    assert len(latches) == (hooks if latch != "none" else 0)
    assert ("climb.pivot" in [m.joint(j).name for j in range(m.njnt)]) == (pivot == "articulated")


def test_hook_geoms_named_and_colliding():
    built = assembled(hook_count=2, hook_material="uhmw")
    m = built.model
    climber_bodies = {b for b in range(m.nbody) if m.body(b).name.startswith("climb.")}
    geoms = [g for g in range(m.ngeom) if m.geom_bodyid[g] in climber_bodies]
    assert geoms
    for g in geoms:                                       # every climber geom collides, with the robot's bits
        assert (m.geom_contype[g], m.geom_conaffinity[g]) == (ROBOT_BITS["contype"], ROBOT_BITS["conaffinity"])
    hooks = [g for g in geoms if m.geom(g).name.startswith("climb.hook")]
    for i in (0, 1):
        names = {m.geom(g).name for g in hooks if m.geom(g).name.startswith(f"climb.hook{i}.")}
        assert {f"climb.hook{i}.stem", f"climb.hook{i}.tip", f"climb.hook{i}.latch"} <= names
        assert any(".crown" in n for n in names)
    for g in hooks:                                       # condim 3, the material's friction, above the field's priority
        assert m.geom_condim[g] == 3
        assert m.geom_friction[g][0] == pytest.approx(HOOK_FRICTION["uhmw"])
        assert m.geom_priority[g] == 1
    assert HOOK_FRICTION == {"steel": 0.5, "polycarbonate": 0.35, "uhmw": 0.15, "nitrile": 1.0}
    # the existing mechanisms stay visual only
    elevator = build_stock("elevator", {}, "t")
    assert not any(g.collide for b in elevator.bodies for g in b.geoms)


def test_throat_is_bar_plus_6mm_and_seat_is_inside_the_hook():
    p = stock_params("climber", ONE, "t")
    assert p["hook_throat"] == pytest.approx(OD + 0.006)
    m, d = climber_world(ONE, bar=None)
    mujoco.mj_forward(m, d)
    stem, tip = (d.geom_xpos[m.geom(f"climb.hook0.{k}").id] for k in ("stem", "tip"))
    r = m.geom_size[m.geom("climb.hook0.stem").id][0]
    assert (tip[0] - stem[0]) - 2 * r == pytest.approx(p["hook_throat"], abs=1e-9)
    x, z = hook_seat(p)
    assert stem[0] < x < tip[0]


# ------------------------------------------------------------------ small worlds: one climber and one bar

def climber_world(params: dict, bar: Bar | None, base=(0.0, 0.0, 0.0)):
    """The climber on the world (no free joint), names prefixed "climb.", and a bar."""
    part = build_stock("climber", params, "t")
    spec = mujoco.MjSpec()
    spec.compiler.degree = False
    spec.option.timestep = DT
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
    spec.option.impratio = 10
    child = part_spec(part)
    spec.worldbody.add_frame(pos=list(base)).attach_body(child.body(part.bodies[0].name), "climb.", "")
    if bar is not None:
        add_element(spec, bar)
    m = spec.compile()
    return m, mujoco.MjData(m)


def dof(m, joint: str) -> int:
    return int(m.jnt_dofadr[m.joint(joint).id])


def qadr(m, joint: str) -> int:
    return int(m.jnt_qposadr[m.joint(joint).id])


ONE = {"stages": 1, "base_length": 0.6, "travel": 0.4, "hook_count": 1}


@pytest.mark.parametrize("params, per_stage", [
    # count x ROTOR_MOI (1e-4 kg m^2) x (motor rad per m of the first stage)^2, split equally over the moving stages
    ({"stages": 2, "gearing": 12.0, "count": 2}, 256.0),               # the stock K5 / 2022 climber: 12 x 2 / 0.015
    # = 1600 rad/m, 2e-4 x 1600^2 = 512 kg in all
    ({"stages": 1, "travel": 0.4, "gearing": 12.0, "count": 2}, 128.0),   # 800 rad/m: 128 kg, all on the tip
    ({"stages": 2, "gearing": 12.0, "count": 2, "rigging": "cascade"}, 64.0),   # 12 / 0.015 = 800 rad/m: a quarter
    ({"stages": 2, "gearing": 25.0, "count": 1}, 1e-4 * (25 * 2 / 0.015) ** 2 / 2),   # 3333 rad/m: 555.6 kg a stage
])
def test_the_telescope_carries_its_motors_rotor_inertia(params, per_stage):
    part = build_stock("climber", {"latch": "passive", "spool_radius": 0.015, **params}, "t")
    moving = [b for b in part.bodies if b.joint is not None and b.joint.name.startswith(("stage", "tip"))]
    assert len(moving) == params["stages"]
    assert [b.joint.armature for b in moving] == pytest.approx([per_stage] * params["stages"], rel=1e-9)
    latches = [b for b in part.bodies if b.name.startswith("latch")]
    assert latches and all(b.joint.armature == pytest.approx(2e-4) for b in latches)   # LATCH_ARMATURE, unchanged


def latch_world(latch: str):
    """A rigid rung across the hook's plane (along y), placed so the hook seats on it with the slide at SEAT_Q;
    the slide starts at START_Q, the hook's mouth above the rung."""
    p = stock_params("climber", {**ONE, "latch": latch}, "t")
    x, z = hook_seat(p)
    bar = Bar("rung", (x, 0.0), 90.0, 1.0, OD, z + SEAT_Q, posts=False)
    m, d = climber_world({**ONE, "latch": latch}, bar)
    d.qpos[qadr(m, "climb.tip")] = START_Q
    mujoco.mj_forward(m, d)
    return m, d, p


SEAT_Q, START_Q = 0.1, 0.25


def run_slide(m, d, target, seconds, cap=150.0, torque=None, record=None):
    """Servo the slide toward target(t) (a PD capped at `cap` N, plus the moving mass's weight); optional constant
    torque on the latch hinge; record(m, d) each step."""
    i, v = dof(m, "climb.tip"), qadr(m, "climb.tip")
    weight = float(m.body_subtreemass[m.body("climb.tip").id]) * 9.81
    t0 = d.time
    while d.time - t0 < seconds:
        f = 4e3 * (target(d.time - t0) - d.qpos[v]) - 200.0 * d.qvel[i]
        d.qfrc_applied[i] = weight + float(np.clip(f, -cap, cap))
        if torque is not None:
            d.qfrc_applied[dof(m, "climb.latch0")] = torque
        mujoco.mj_step(m, d)
        if record is not None:
            record(m, d)


def latch_deg(m, d) -> float:
    return math.degrees(d.qpos[qadr(m, "climb.latch0")])


def rung_over_hinge(m, d) -> float:
    """How far the rung's axis is above the latch hinge (the hook tip's end)."""
    tip = m.geom("climb.hook0.tip").id
    return float(d.geom_xpos[m.geom("element.rung.pipe").id][2] - (d.geom_xpos[tip][2] - m.geom_size[tip][1]))


def retract_onto_the_rung(m, d):
    """Lower the hook over the rung at 0.2 m/s; returns the latch's largest angle on the way (degrees)."""
    seen = []
    run_slide(m, d, lambda t: max(START_Q - 0.2 * t, SEAT_Q - 0.02), 1.2,
              record=lambda m, d: seen.append(latch_deg(m, d)))
    return max(seen)


def test_passive_latch_opens_for_the_bar_and_closes():
    m, d, p = latch_world("passive")
    widest = retract_onto_the_rung(m, d)
    assert widest > 80.0                                   # the rung pushed it open on its way into the throat
    assert latch_deg(m, d) == pytest.approx(0.0, abs=3.0)  # and it sprang shut behind the rung
    q = d.qpos[qadr(m, "climb.tip")]
    assert q == pytest.approx(SEAT_Q, abs=0.004)           # seated on the crown (a hard stop for the servo)
    # pushing the hook up again: the closed latch catches the rung, so it can't leave through the mouth
    run_slide(m, d, lambda t: SEAT_Q + 0.2, 1.0)
    assert rung_over_hinge(m, d) == pytest.approx(OD / 2 + LATCH_RADIUS, abs=0.002)   # resting on the shut latch
    assert latch_deg(m, d) == pytest.approx(0.0, abs=2.0)


def test_release_slot_opens_the_latch():
    m, d, p = latch_world("released")
    assert retract_onto_the_rung(m, d) > 80.0              # a released latch still lets the rung in
    assert latch_deg(m, d) == pytest.approx(0.0, abs=3.0)
    run_slide(m, d, lambda t: SEAT_Q, 0.3, torque=1.0)     # the release motor's torque opens it
    assert latch_deg(m, d) > 80.0
    run_slide(m, d, lambda t: SEAT_Q + 0.2 * min(t, 1.0), 1.2, torque=1.0)
    assert rung_over_hinge(m, d) < -OD / 2                 # the rung left through the mouth


def test_telescope_tube_collides_with_a_bar():
    params = {**ONE, "pivot": "articulated", "pivot_range": [-30.0, 30.0]}
    p = stock_params("climber", params, "t")
    half = p["tube"] / 2
    # a rung beside the base tube at mid-height, 3 cm off its +x face; the pivot leans the telescope into it
    bar = Bar("rung", (half + OD / 2 + 0.03, 0.0), 90.0, 1.0, OD, 0.3, posts=False)
    m, d = climber_world(params, bar)
    i, v = dof(m, "climb.pivot"), qadr(m, "climb.pivot")
    pipe = m.geom("element.rung.pipe").id
    climber = {b for b in range(m.nbody) if m.body(b).name.startswith("climb.")}
    touched = set()
    while d.time < 1.0:
        d.qfrc_applied[i] = float(np.clip(300.0 * (math.radians(20.0) - d.qpos[v]) - 20.0 * d.qvel[i], -40, 40))
        mujoco.mj_step(m, d)
        for c in d.contact[:d.ncon]:
            pair = {int(c.geom1), int(c.geom2)}
            if pipe in pair:
                other = (pair - {pipe}).pop()
                if m.geom_bodyid[other] in climber:
                    touched.add(other)
    assert touched, "the leaning telescope never touched the rung"
    assert any(not m.geom(g).name.startswith("climb.hook") for g in touched)   # a tube, not a hook
    assert d.qpos[v] < math.radians(15.0)                  # the rung stopped the lean


@pytest.mark.parametrize("given, message", [
    ({"hook_depth": 0.08}, r"hook_depth 0.08 m is too short for the latch to close under a seated bar: make it at "
                           r"least 0.09\d* m, or set latch = \"none\""),
    ({"hook_depth": 0.02, "latch": "none"}, r"hook_depth 0.02 m leaves no tip on the hook"),
    ({"hook_throat": 0.006}, r"hook_throat 0.006 m leaves no room for a bar: make it more than 0.006 m"),
    ({"hook_throat": 0.001, "latch": "none"}, r"hook_throat 0.001 m leaves no room for a bar"),
    ({"stages": 4, "tube": 0.03}, r"tube at least"),
    ({"stages": 1, "travel": 0.6}, r"travel must be at most 0.5"),
])
def test_climber_parameter_errors(given, message):
    with pytest.raises(PartError, match=message):
        build_stock("climber", given, "t")


def test_latch_closes_with_the_default_depth_and_none_needs_no_extra_depth():
    build_stock("climber", {"hook_depth": 0.08, "latch": "none"}, "t")
    build_stock("climber", {}, "t")
