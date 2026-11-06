"""Stock climber: a telescoping arm with J hooks at its tip that hold a bar by contact, an optional spring latch
across each hook's mouth and an optional pivot at its base. Unlike the other mechanisms, every climber geom collides:
the tubes, the hooks and the latches touch the field's bars, so a missed or walking hook is physical.

The telescope is the elevator's slide chain (mechanisms.slide_chain): base, stage1 .. stage<n-1> and the tip, every
tube `base_length` long and standing up from the base mount, nested `NEST` smaller each stage. The hooks sit on a
bracket at the tip tube's top, opening toward +x, in the x-z plane (the bar runs along y). Each hook i is capsules:

- `hook<i>.stem`, rising just outside the base tube's +x face (so a bar in the throat clears every tube);
- `hook<i>.crown0` .. `crown3`, a half circle over the top, the bar's seat;
- `hook<i>.tip`, coming down `hook_depth` below the throat's top: the hook's open side, the mouth, is below, between
  the tip's end and the stem, `hook_throat` wide;
- with a latch, `hook<i>.latch`: a finger hinged at the tip's end (body and joint `latch<i>`), lying across the mouth.
  A bar coming up into the mouth pushes it up into the throat (it opens inwards only: its closed stop is a joint
  limit) and its spring snaps it shut behind the bar, so the bar can't drop out through the mouth. A `released`
  latch also has a motor slot `release` that turns it open (with two hooks, latch1 is coupled to latch0, one
  linkage).

A hook holds the robot by its weight on the crown; it lets go by geometry: unload it, lift it clear of the bar (the
tip's end above the bar) and move it off sideways, by extending or by the pivot.
"""

from __future__ import annotations

import math

from ...motors import MOTORS
from ..model import PartError
from . import Param, Stock, register
from .mechanisms import slide_chain
from ...i18n import N_, t

IN = 0.0254
STEEL = [0.55, 0.55, 0.6, 1.0]
ALUMINUM = [0.75, 0.76, 0.78, 1.0]
HOOK_RGBA = [0.95, 0.5, 0.1, 1.0]
LATCH_RGBA = [0.2, 0.6, 0.9, 1.0]

# Sliding friction of the hook against a powder-coated steel rung (research notes 4.2, CD and Engineering Toolbox).
HOOK_FRICTION = {"steel": 0.5,            # estimate: steel on steel 0.42 kinetic to 0.5-0.8 static, dry
                 "polycarbonate": 0.35,   # estimate: 0.3-0.5, no authoritative datasheet
                 "uhmw": 0.15,            # estimate: UHMW-PE on steel 0.10-0.22 (Curbell, Proto Labs datasheets)
                 "nitrile": 1.0}          # estimate: elastomer tread on metal 0.8-1.2
HOOK_PRIORITY = 1         # above the field's 0: the hook's friction, condim and softness govern a hook-rung contact
HOOK_CONDIM = 3           # sliding friction only; torsion and rolling would double-count the pipe's real radius
# Hook-rung contact softness. Estimate, chosen so a 60 kg robot hanging on two hooks sinks well under 2 mm: at rest
# MuJoCo's soft contact sinks (1 - d) T^2 zeta^2 f / m_c (parts/assemble.py, tread_solref), d about 0.9 to 0.95
# (default solimp), f / m_c the contact's force over the mass the solver sees there. Hanging at a hook far from the
# CoM, or on a light compliant rung (a 2022 rung moves about 9 kg), m_c can drop to about 8 kg with f about 300 N
# a hook: f / m_c about 40 m/s^2, so T = 0.01 s sinks at most 0.1 x 1e-4 x 40 = 0.4 mm, and 0.05 mm for m_c = 60 kg.
# 0.01 s is 10 timesteps at 1 ms, well clear of MuJoCo's 2-step floor. Critically damped: a hook doesn't bounce.
# Measured: a 60 kg body on two hooks (6 contacts) of a 1-stage climber sinks 0.64 mm into a rigid 2022 rung and
# 0.73 mm into the 160 kN/m one, its CoM under the hooks or 0.3 m off; at the default 0.02 s it was 1.5 and 1.9 mm.
HOOK_SOLREF = [0.01, 1.0]
HOOK_RADIUS = 0.005       # m: each hook capsule's radius (estimate: a 1/4 to 1/2 in thick plate hook)
THROAT_CLEARANCE = 0.006  # m: the default throat is the bar's OD plus this (3 mm a side)
RUNG_OD = 1.66 * IN       # m: the 2022 rungs' OD (manual 5.4.2, GE-22150), the default throat's bar
CROWN_SEGMENTS = 4        # capsules in the crown's half circle (even, so its top is a vertex over the seat)
BRACKET_HEIGHT = 0.02     # m: the bracket carrying the hooks on the tip tube's top (estimate)
NEST = 0.25 * IN          # m: each stage's tube this much smaller than the one below (estimate: 1/8 in wall gap a side)
MIN_OVERLAP = 0.1         # m: how much of each tube stays inside the one below at full extension (estimate)
LATCH_RADIUS = 0.004      # m: the latch finger's capsule radius (estimate)
LATCH_MASS = 0.03         # kg: the latch finger (estimate: a small polycarbonate or aluminum flap)
LATCH_OPEN_DEG = 95.0     # deg: how far the latch turns open (inward); past 90 it lies along the hook's tip
LATCH_PRELOAD_DEG = 15.0  # deg: the spring's rest is this far past the closed stop, so it holds the latch shut
LATCH_DAMPING = 0.002     # N m s/rad: the latch hinge's damping (estimate: settles the flap within a few swings)
# The latch's stop and its inertia. MuJoCo softens a constraint by the inverse inertia it acts on, so a bare 30 g flap
# (2e-5 kg m^2) on a default stop (solref 0.02) sags about 4 deg under its own spring's preload and blows open under
# a bar. An armature (a rotor-like added inertia, about a NEO 550 rotor geared 3:1) and a stiff stop hold it: a bar
# pressing with 3.75 N m (150 N at 25 mm) sags it about 1 deg, and the spring still shuts it in about 30 ms.
LATCH_ARMATURE = 2e-4     # kg m^2 (estimate)
LATCH_STOP_SOLREF = [0.004, 1.0]   # s, damping ratio: 4 steps at 1 ms, critically damped (estimate: a hard stop)
# The telescope's travel stops. A robot hangs on them (hung at full extension, its weight pulls the tip against the
# upper stop), and MuJoCo's default stop (solref 0.02 s) let 60 kg sag about 15 mm through it and a full-duty motor
# push 10 cm through. 3 ms (3 steps at 1 ms), overdamped, holds 60 kg within 1.3 mm (tests/test_climb_physics.py).
# Overdamped, not critically: at [0.004, 1] the stop and a 2022 rung's spring held a 12 mm (peak to peak) bounce
# going for good (measured), at [0.003, 2] it dies within 5 s.
TELESCOPE_STOP_SOLREF = [0.003, 2.0]   # s, damping ratio (estimate: a hard stop)
LATCH_MARGIN = 0.002      # m: the latch's swing clears a seated bar by at least this
PIVOT_BRACKET = 0.03      # m: the pivot bracket's height under the base tube (estimate)
PIVOT_BRACKET_MASS = 0.3  # kg: the pivot bracket and its bearings (estimate)

_MOTOR_NAMES = tuple(sorted(MOTORS))


def _capsule(a, b, radius: float, y: float = 0.0, **extra) -> dict:
    """A capsule from (x, z) point a to point b, at y."""
    dx, dz = b[0] - a[0], b[1] - a[1]
    return {"capsule": [radius, math.hypot(dx, dz)], "pos": [(a[0] + b[0]) / 2, y, (a[1] + b[1]) / 2],
            "rot": [0.0, math.degrees(math.atan2(dx, dz)), 0.0], **extra}


def _hook_frame(p: dict) -> dict:
    """The hook's key points in the tip body's frame (x, z): the stem's x, the crown's center, the tip's x and end."""
    w, r = p["hook_throat"], HOOK_RADIUS
    rho = w / 2 + r                                     # the crown's centerline radius
    x_stem = p["tube"] / 2 + r                          # the stem just outside the base tube's +x face
    zc = p["base_length"] + p["hook_depth"] - w / 2     # so the tip's end is level with the tip tube's top
    return {"stem": x_stem, "rho": rho, "center": (x_stem + rho, zc), "tip": x_stem + 2 * rho,
            "tip_end": zc + w / 2 - p["hook_depth"]}


def hook_seat(p: dict) -> tuple[float, float]:
    """Where a bar of OD hook_throat - THROAT_CLEARANCE sits on the crown: its center's (x, z) in the tip body's frame
    (checked climber parameters). It rests on the two crown capsules either side of the top vertex."""
    f = _hook_frame(p)
    bar_r = (p["hook_throat"] - THROAT_CLEARANCE) / 2
    cx, zc = f["center"]
    return cx, zc + f["rho"] - (bar_r + HOOK_RADIUS) / math.cos(math.pi / (2 * CROWN_SEGMENTS))


def _check_hook(p: dict, where: str = "climber") -> None:
    w, depth = p["hook_throat"], p["hook_depth"]
    if w <= THROAT_CLEARANCE:                           # the seat and latch geometry assume a bar of OD w - clearance
        raise PartError(t("{where}: hook_throat {throat} m leaves no room for a bar: make it more than {least} m (the "
                          "bar's OD plus {mm} mm)", where=where, throat=w, least=THROAT_CLEARANCE,
                          mm=f"{THROAT_CLEARANCE * 1000:g}"), where=where, field="param:hook_throat")
    if depth <= w / 2 + HOOK_RADIUS:
        raise PartError(t("{where}: hook_depth {depth} m leaves no tip on the hook: make it more than {least} m (half "
                          "the throat plus the hook's radius)", where=where, depth=depth,
                          least=f"{w / 2 + HOOK_RADIUS:.4f}"), where=where, field="param:hook_depth")
    if p["latch"] == "none":
        return
    f = _hook_frame(p)
    bar_r = (w - THROAT_CLEARANCE) / 2
    reach = w + bar_r + LATCH_RADIUS + LATCH_MARGIN     # the latch's length plus the bar's and its own radius
    need = math.sqrt(reach ** 2 - f["rho"] ** 2) + w / 2 - (hook_seat(p)[1] - f["center"][1])
    if depth < need:
        raise PartError(t("{where}: hook_depth {depth} m is too short for the latch to close under a seated bar: "
                          "make it at least {least} m, or set {latch_setting}", where=where, depth=depth,
                          least=math.ceil(need * 1000) / 1000,
                          latch_setting="latch = \"none\""), where=where, field="param:hook_depth")


def _hook(p: dict, i: int, y: float) -> tuple[list[dict], dict | None]:
    """Hook i's geoms on the tip body, and its latch body (or None)."""
    f = _hook_frame(p)
    r = HOOK_RADIUS
    tag = {"priority": HOOK_PRIORITY, "condim": HOOK_CONDIM,
           "friction": HOOK_FRICTION[p["hook_material"]], "solref": list(HOOK_SOLREF), "color": HOOK_RGBA}
    cx, zc = f["center"]
    base = p["base_length"] - BRACKET_HEIGHT / 2
    geoms = [_capsule((f["stem"], base), (f["stem"], zc), r, y, **{**tag, "name": f"hook{i}.stem"})]
    pts = [(cx + f["rho"] * math.cos(math.pi * (1 - k / CROWN_SEGMENTS)),
            zc + f["rho"] * math.sin(math.pi * (1 - k / CROWN_SEGMENTS))) for k in range(CROWN_SEGMENTS + 1)]
    geoms += [_capsule(a, b, r, y, **{**tag, "name": f"hook{i}.crown{k}"})
              for k, (a, b) in enumerate(zip(pts, pts[1:]))]
    geoms.append(_capsule((f["tip"], zc), (f["tip"], f["tip_end"]), r, y, **{**tag, "name": f"hook{i}.tip"}))
    if p["latch"] == "none":
        return geoms, None
    w = p["hook_throat"]
    finger = _capsule((0.0, 0.0), (-w, 0.0), LATCH_RADIUS, **{**tag, "name": f"hook{i}.latch",
                                                               "color": LATCH_RGBA})
    latch = {"name": f"latch{i}", "parent": "tip", "mass": LATCH_MASS, "pos": [f["tip"], y, f["tip_end"]],
             "joint": {"name": f"latch{i}", "hinge": "y", "range": [0.0, LATCH_OPEN_DEG], "damping": LATCH_DAMPING,
                       "armature": LATCH_ARMATURE, "limit_solref": list(LATCH_STOP_SOLREF),
                       "spring": {"stiffness": p["latch_spring"], "rest": -LATCH_PRELOAD_DEG}},
             "geom": [finger]}
    return geoms, latch


def _climber(p: dict) -> dict:
    n, L = p["stages"], p["base_length"]
    if p["tube"] - n * NEST < 0.01:
        raise PartError(t("climber: a {tube} m base tube leaves the tip tube under 1 cm with {stages} stages nested "
                          "{nest} m smaller each: make tube at least {least} m", tube=p["tube"], stages=n,
                          nest=f"{NEST:.4f}", least=f"{n * NEST + 0.01:.4f}"))
    _check_hook(p)
    f = _hook_frame(p)
    ys = [0.0] if p["hook_count"] == 1 else [p["hook_spacing"] / 2, -p["hook_spacing"] / 2]
    hooks, latches = [], []
    for i, y in enumerate(ys):
        g, latch = _hook(p, i, y)
        hooks += g
        if latch is not None:
            latches.append(latch)
    reach = f["stem"] + HOOK_RADIUS
    span = abs(ys[0] - ys[-1]) + 2 * HOOK_RADIUS
    bracket = {"box": [reach, max(span, p["tube"] - n * NEST), BRACKET_HEIGHT],
               "pos": [reach / 2, 0.0, L - BRACKET_HEIGHT / 2], "color": ALUMINUM, "name": "bracket"}

    def tubes(i: int, h: float) -> list[dict]:
        t = p["tube"] - i * NEST
        out = [{"box": [t, t, L], "pos": [0.0, 0.0, L / 2], "color": ALUMINUM}]
        return out + [bracket] + hooks if i == n else out

    chain = slide_chain(p, tubes, "tip", p["tip_mass"], "extend", "extend_encoder", TELESCOPE_STOP_SOLREF)
    bodies = chain["body"] + latches
    couplings = chain["coupling"]
    motors, encoders = chain["motor"], chain["encoder"]
    if p["latch"] == "released":
        motors.append({"slot": "release", "joint": "latch0", "motor": p["release_motor"],
                       "gearing": p["release_gearing"]})
        couplings += [{"joints": ["latch0", lt["name"]], "ratio": 1.0} for lt in latches[1:]]
    root = "base"
    if p["pivot"] == "articulated":
        root = "bracket"
        lo, hi = p["pivot_range"]
        bodies[0] = {**bodies[0], "parent": "bracket", "joint": {"name": "pivot", "hinge": "y", "range": [lo, hi]}}
        bodies.insert(0, {"name": "bracket", "mass": PIVOT_BRACKET_MASS,
                          "geom": [{"box": [p["tube"], p["tube"], PIVOT_BRACKET], "pos": [0, 0, -PIVOT_BRACKET / 2],
                                    "color": STEEL}]})
        motors.append({"slot": "pivot", "joint": "pivot", "motor": p["pivot_motor"], "count": p["pivot_count"],
                       "gearing": p["pivot_gearing"]})
        encoders.append({"slot": "pivot_encoder", "joint": "pivot", "ratio": 1.0})
    return {
        "part": {"name": "climber", "format": 1},
        "body": bodies,
        "coupling": couplings,
        "motor": motors,
        "encoder": encoders,
        "mount": [{"name": "base", "body": root},
                  {"name": "tip", "body": "tip", "pos": [0.0, 0.0, L]}],
    }


register(Stock(
    "climber", N_("telescoping climber with J hooks at its tip that hold a bar by contact; optional spring latch "
    "(passive or released by a motor) and pivot. Every geom collides"),
    (Param("stages", "", 2, N_("moving tubes including the tip (the tip is stage 'tip', the others stage1, stage2, ...)"),
           kind="int", min=1, max=4),
     Param("base_length", "m", 0.6, N_("length of every tube (estimate)"), positive=True),
     Param("travel", "m", 0.6, "total extension of the tip from fully retracted; each tube keeps "
           f"{MIN_OVERLAP} m inside the one below",
           positive=True, max=lambda q: q["stages"] * (q["base_length"] - MIN_OVERLAP)),
     Param("rigging", "", "continuous", N_("continuous or cascade, as the elevator's"), kind="str",
           choices=("continuous", "cascade")),
     Param("tube", "m", 2 * IN, N_("outer size of the square base tube; each stage is 1/4 in smaller"), positive=True),
     Param("stage_mass", "kg", 0.6, N_("mass of the base tube and of each intermediate tube (estimate: 2 in 1/16 wall "
           "aluminum, 0.86 kg/m, plus hardware)"), positive=True),
     Param("tip_mass", "kg", 0.8, N_("mass of the tip tube with its hooks and bracket (estimate)"), positive=True),
     Param("motor", "", "KrakenX60", N_("extend motor model"), kind="str", choices=_MOTOR_NAMES),
     Param("count", "", 1, N_("motors driving the telescope"), kind="int", min=1),
     Param("gearing", "", 20.0, N_("motor turns per spool turn (estimate)"), positive=True),
     Param("spool_radius", "m", 0.015, N_("radius the rope winds on (estimate)"), positive=True),
     Param("friction", "N", 5.0, N_("constant sliding friction, in newtons felt at the tip"), min=0.0),
     Param("hook_throat", "m", round(RUNG_OD + THROAT_CLEARANCE, 6), N_("the throat's clear width between the stem and "
           "the tip; default the 2022 rung's 1.66 in OD plus 6 mm"), positive=True),
     Param("hook_depth", "m", 0.1, N_("how far the hook's tip reaches below the throat's top (estimate)"), positive=True),
     Param("hook_material", "", "steel", N_("sets the hook's friction on the bar: steel 0.5, polycarbonate 0.35, "
           "uhmw 0.15, nitrile 1.0 (estimates)"), kind="str", choices=tuple(HOOK_FRICTION)),
     Param("hook_count", "", 1, N_("hooks across the arm (1 or 2)"), kind="int", min=1, max=2),
     Param("hook_spacing", "m", 0.2, N_("distance between two hooks along the bar (estimate)"), positive=True),
     Param("latch", "", "passive", N_("none; passive: a spring latch across each hook's mouth that the bar pushes "
           "open and that snaps shut behind it; released: the same, plus a motor slot release that opens it"),
           kind="str", choices=("none", "passive", "released")),
     Param("latch_spring", "N*m/rad", 0.3, N_("the latch's torsion spring (estimate)"), positive=True),
     Param("release_motor", "", "NEO550", N_("release motor model"), kind="str", choices=_MOTOR_NAMES),
     Param("release_gearing", "", 5.0, N_("release motor turns per latch turn (estimate)"), positive=True),
     Param("pivot", "", "none", N_("none, or articulated: a hinge at the base on a motor (slots pivot, pivot_encoder)"),
           kind="str", choices=("none", "articulated")),
     Param("pivot_range", "deg", [-30.0, 30.0], N_("the pivot's limits; 0 is upright, positive leans the tip toward "
           "+x, the side the hooks open to (estimate)"), kind="pair"),
     Param("pivot_motor", "", "KrakenX60", N_("pivot motor model"), kind="str", choices=_MOTOR_NAMES),
     Param("pivot_count", "", 1, N_("motors driving the pivot"), kind="int", min=1),
     Param("pivot_gearing", "", 100.0, N_("pivot motor turns per climber turn (estimate)"), positive=True)),
    _climber, slots=("extend", "extend_encoder"), mounts=("base", "tip"),
    note=N_("Slots: extend (motor) and extend_encoder (spool turns); release (motor) with {latch_setting}; pivot "
         "(motor) and pivot_encoder (climber turns) with {pivot_setting}. Hook geoms are named "
         "<part>.hook<i>.stem, .crown<k>, .tip and .latch; the bracket carrying them <part>.bracket. The climber "
         "stands up from its base mount, retracted; the hooks open toward +x and hold a bar running along y."),
    note_keys=(("latch_setting", 'latch = "released"'), ("pivot_setting", 'pivot = "articulated"'))))
