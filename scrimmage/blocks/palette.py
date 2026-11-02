"""What a robot and its field offer the block editor (the palette), and the problems a test's blocks have with them.

The palette lists every name a block can pick, as the world names them: the robot's motors and joints (a hinge reads
in degrees, a slide in inches, a drive wheel is a `wheel`), its bodies and parts, its hooks, the field's bars,
elements, game pieces and climb levels, whether it can drive (it has drive-wheel motors: joints whose body carries a
tread geom, as parts/assemble.py marks them) and the signals a chart can show. Every list is sorted by name except
`levels`, which keeps the range's climb order (lowest first), so a picker reads the way the robot climbs. `pieces`
are the field's game pieces (the floor balls), not the balls parked in the robot's slots.

`problems()` checks a test's names against that: one plain sentence per bad name, at the block's place (0-based
indices, through repeat bodies). A test written before Import again renamed a part shows its blocks' problems here
and doesn't run; nothing crashes.
"""

from __future__ import annotations

import mujoco

from ..hal_drive import driven_joints
from ..terrain.climb import robot_hooks

NEXT_BAR = "next bar"     # a hanging_from or touch target: the lowest bar above every bar hung from so far
ANY_HOOK = "hook"         # a touch's robot side: any of the robot's hooks
ANY_PIECE = "any"         # a piece_in's piece: any game piece
ALL_MOTORS = "all"        # a stop's motor: every motor


def _robot_bodies(world) -> list[int]:
    m = world.model
    root = m.body_rootid[world.robot_body]
    return [b for b in range(m.nbody) if m.body_rootid[b] == root and m.body(b).name]


def _joints(world) -> dict[str, str]:
    """Joint name -> kind ("hinge", "slide" or "wheel"), for every named hinge and slide."""
    m = world.model
    tread_bodies = {int(m.geom_bodyid[m.geom(g).id]) for g in (world.parts.tread_geoms if world.parts else ())}
    out = {}
    names = world.joint_states()
    for j in range(m.njnt):
        name = m.joint(j).name
        if name not in names:
            continue
        if int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_SLIDE):
            out[name] = "slide"
        else:
            out[name] = "wheel" if int(m.jnt_bodyid[j]) in tread_bodies else "hinge"
    return out


def _entry(name: str, kind: str) -> dict:
    return {"name": name, "unit": "in" if kind == "slide" else "deg", "kind": kind}


def hook_names(world, hooks: list[str]) -> list[str]:
    """The robot's hooks: the stock climber's (`<inst>.hook<i>`) and the bodies the test names as hooks."""
    return sorted(set(robot_hooks(world.model, world.robot_body)) | set(hooks))


def palette(world, hooks: list[str]) -> dict:
    m = world.model
    kinds = _joints(world)
    motors = sorted(n for n in driven_joints(world) if n in kinds)
    parts, layout, rng = world.parts, None, None
    if parts is not None:
        layout, rng = parts.layout, parts.range
    bars = sorted([*layout.bars, *layout.switches]) if layout is not None else []
    rule = rng.climb if rng is not None else None
    hook_list = hook_names(world, hooks)
    signals = ["tilt", *motors, *(f"{n}.current" for n in motors)]
    if hook_list:
        signals.append("hook_load")
    return {
        "motors": [_entry(n, kinds[n]) for n in motors],
        "joints": [_entry(n, k) for n, k in sorted(kinds.items())],
        "bodies": sorted(m.body(b).name for b in _robot_bodies(world)),
        "parts": sorted(i.name for i in parts.instances) if parts is not None else [],
        "hooks": hook_list,
        "bars": bars,
        "elements": sorted(e.name for e in rng.elements) if rng is not None else [],
        "pieces": sorted(layout.ball_names[:layout.n_floor]) if layout is not None else [],   # on the field
        "levels": list(rule.levels) if rule is not None else [],      # lowest first, as the range lists them
        "drive": any(kinds[n] == "wheel" for n in motors),
        "signals": signals,
    }


# ---- problems -------------------------------------------------------------------------------------------------------

class _Names:
    """The names a test can use on this world, for checking."""

    def __init__(self, world, hooks: list[str]):
        p = palette(world, hooks)
        self.p = p
        self.joints = {j["name"]: j["kind"] for j in p["joints"]}
        self.motors = {j["name"] for j in p["motors"]}
        self.bodies = set(p["bodies"])
        self.robot = self.bodies | set(p["parts"]) | set(p["hooks"])
        self.bars = set(p["bars"])
        self.pieces = set(p["pieces"])
        self.field = set(world.contact_names()) | set(p["elements"]) | self.bars


def _unit_problem(name: str, kind: str, unit: str) -> str | None:
    if kind == "slide" and unit == "deg":
        return f'"{name}" slides, so give it in inches or meters, not degrees'
    if kind != "slide" and unit != "deg":
        return f'"{name}" turns, so give it in degrees'
    return None


def _joint_problems(n: _Names, name: str, unit: str | None, motor: bool) -> list[str]:
    if name not in n.joints:
        return [f'no joint "{name}" on this robot any more']
    out = []
    if motor and name not in n.motors:
        out.append(f'"{name}" has no motor to move it')
    if unit is not None:
        u = _unit_problem(name, n.joints[name], unit)
        if u:
            out.append(u)
    return out


def _bar_problems(n: _Names, bar: str) -> list[str]:
    if bar == NEXT_BAR:
        return [] if n.bars else ['"next bar" needs a field with bars']
    return [] if bar in n.bars else [f'no bar "{bar}" on this field']


def _cond_problems(n: _Names, c) -> list[str]:
    a = c.args
    if c.kind == "joint":
        return _joint_problems(n, a["name"], a["unit"], motor=False)
    if c.kind == "touch":
        out = []
        if a["robot"] == ANY_HOOK:
            if not n.p["hooks"]:
                out.append("this robot has no hooks; say which bodies are hooks in the test's setup")
        elif a["robot"] not in n.robot:
            out.append(f'no part or body "{a["robot"]}" on this robot any more')
        if a["field"] == NEXT_BAR:
            out += _bar_problems(n, NEXT_BAR)
        elif a["field"] not in n.field and a["field"] not in n.pieces:
            out.append(f'nothing called "{a["field"]}" on this field')
        return out
    if c.kind == "piece_in":
        out = []
        if a["body"] not in n.bodies:
            out.append(f'no body "{a["body"]}" on this robot any more')
        if a["piece"] == ANY_PIECE:
            if not n.pieces:
                out.append("this field has no game pieces")
        elif a["piece"] not in n.pieces:
            out.append(f'no game piece "{a["piece"]}" on this field')
        return out
    if c.kind == "hanging_from":
        return _bar_problems(n, a["bar"])
    if c.kind == "level":
        if not n.p["levels"]:
            return ["levels need a field that scores climbs"]
        return [] if a["level"] in n.p["levels"] else [f'no level "{a["level"]}" on this field']
    return []       # tilt


def _block_problems(n: _Names, b) -> list[str]:
    a = b.args
    if b.do == "stop" and a["motor"] == ALL_MOTORS:
        return []
    if b.do in ("spin", "stop"):
        return _joint_problems(n, a["motor"], None, motor=True)
    if b.do == "move":
        return _joint_problems(n, a["joint"], a["unit"], motor=True)
    if b.do == "drive":
        return [] if n.p["drive"] else ["drive needs a drivetrain; this robot has none"]
    for key in ("until", "that"):
        if key in a:
            return _cond_problems(n, a[key])
    return []


def problems(program, world) -> list[dict]:
    """[{"place": [i, j, ...], "message": str}]: each name the program uses that this world doesn't have. A hook the
    test's setup names that isn't a body of the robot is at place []. Empty when the program can run."""
    n = _Names(world, list(program.hooks))
    out = [{"place": [], "message": f'no body "{h}" on this robot any more (the test lists it as a hook)'}
           for h in program.hooks if h not in n.bodies]

    def walk(blocks, trail):
        for i, b in enumerate(blocks):
            place = [*trail, i]
            out.extend({"place": place, "message": msg} for msg in _block_problems(n, b))
            walk(b.body, place)

    walk(program.blocks, [])
    return out
