"""What a robot and its field offer the block editor (the palette), and the problems a test's blocks have with them.

The palette lists every name a block can pick, as the world names them: the robot's motors and joints (a hinge reads
in degrees, a slide in inches, a drive wheel is a `wheel`), its bodies and parts, its hooks, the field's bars,
elements, rods, goals and game pieces, its stock turrets (`turrets`, what an aim block turns), other surfaces a
touch can name (`targets`: the carpet, the walls) and climb levels, whether it can drive and how (`drive_kind`: a tank
or swerve drive, as drive.py finds them) and the signals a chart can show (`seated.<rod>` for each rod, in inches, when
a piece kind of the field has a hole).
The field's pieces: `piece` (its default kind's preset), `kinds` (each kind's form, whether it is hollow or flat and the
plain name a student reads, e.g. "coral"), `floor_kinds` (each floor piece's kind), `piece_labels` (what a student
reads for each floor piece, "coral 2", as the run's sentences name it) and `station_kinds` (each station's), and every
preset Setup's piece picker offers (`piece_presets`: balls and pieces). The grips: `grips`, the grip
material (parts/grip.py) of each body of a path part that touches anything ("<instance>.<body>" -> a material, a
number or None for the default friction), and `grip_materials`, the materials a picker offers ([key, label,
friction estimate]).
Every list is sorted by name except `levels`, which keeps the range's climb order (lowest first), so a picker reads the
way the robot climbs. `pieces` are the field's game pieces (the floor balls), not the balls parked in the robot's
slots.

`problems()` checks a test's names against that: one plain sentence per bad name, at the block's place (0-based
indices, through repeat bodies). A test written before Import again renamed a part shows its blocks' problems here
and doesn't run; nothing crashes.
"""

from __future__ import annotations

import mujoco

from ..hal_drive import driven_joints
from ..launcher.inputs import PRESETS
from ..parts.grip import GRIP_MATERIALS
from ..terrain.climb import robot_hooks
from ..testrange.spec import BALL_CHOICE_RANGES, RangeError, load_range
from ..testrange.station import OVERRIDE_KEYS, with_overrides
from .aim import turrets
from .drive import drivetrain
from .placed import ON_PIECE
from .spread import NO_BALL, floor_ball, spread_ball

NEXT_BAR = "next bar"     # a hanging_from or touch target: the lowest bar above every bar hung from so far
ANY_BAR = "any bar"       # a touch target: any of the field's bars
ANY_HOOK = "hook"         # a touch's robot side: any of the robot's hooks
ANY_PIECE = "any"         # a piece_in's, held's, rests_on's or seated's piece: any game piece
ANY_GOAL = "any"          # a scored's goal: every goal of the range together
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


def _targets(world, named: set[str]) -> list[str]:
    """The field's other surfaces a touch can name, as a student knows them: each field body by its own name (a hub,
    never its numbered geoms: a touch on a body counts all of its geoms) and the named geoms of the fixed field (the
    carpet, the walls). Not `world` (every fixed thing at once), not `element.<name>...` (an element goes by its own
    name), not a ball (the floor balls are `pieces`; the robot's slot balls are no surface of the field), and not what
    bars, elements and pieces list already."""
    m = world.model
    root = m.body_rootid[world.robot_body]
    layout = world.parts.layout if world.parts is not None else None
    skip = set(named) | set(layout.ball_names if layout is not None else ())
    out = {m.body(b).name for b in range(1, m.nbody) if m.body_rootid[b] != root and m.body(b).name}
    out |= {m.geom(g).name for g in range(m.ngeom) if int(m.geom_bodyid[g]) == 0 and m.geom(g).name}
    return sorted(n for n in out if n not in skip and not n.startswith("element."))


def _station_settings(st) -> dict:
    """A station's OVERRIDE_KEYS values, as Setup's boxes show them (no stock is 0; lists, not tuples)."""
    out = {}
    for key in OVERRIDE_KEYS:
        v = getattr(st, key)
        out[key] = 0 if key == "stock" and v is None else list(v) if isinstance(v, tuple) else v
    return out


def _kinds(rng) -> dict:
    """Each piece kind of the field: its form ("ball", "disc" or "rigid"), whether it is hollow (a hole a rod goes
    through) or flat, and its plain name (`label`)."""
    if rng is None:
        return {}
    return {name: {"form": k.form, "hollow": k.hole is not None, "flat": bool(k.flat), "label": k.label}
            for name, k in rng.kinds.items()}


def _grips(world) -> dict:
    """"<instance>.<body>" -> its grip material (None: the default friction) for each body of a path part (a part
    folder's, not a stock part's) with a geom that touches anything, a tread's aside: what a grip can be set on."""
    parts = world.parts
    if parts is None:
        return {}
    out = {}
    for inst in parts.instances:
        if inst.source.startswith("stock "):
            continue
        for b in inst.part.bodies:
            if any(g.collide and not g.tread for g in b.geoms):
                out[f"{inst.name}.{b.name}"] = inst.part.grips.get(b.name)
    return dict(sorted(out.items()))


def _spread_kind_name(world) -> str:
    """The kind name of the floor spread's piece (the one spread_ball picks)."""
    i, _ = spread_ball(world)
    return str(world.test_range.balls.kind[i])


def _piece_labels(world, floor_names: list[str]) -> dict:
    """Each floor piece's body name -> what a student reads for it ("ball 10", "coral 2"), as every sentence names it
    (balls.piece_name), so a picker never numbers pieces by its sorted list."""
    tr = getattr(world, "test_range", None)
    if tr is None:
        return {}
    return {name: tr.balls.piece_name(i) for i, name in enumerate(floor_names)}


def hook_names(world, hooks: list[str]) -> list[str]:
    """The robot's hooks: the stock climber's (`<inst>.hook<i>`) and the bodies the test names as hooks."""
    return sorted(set(robot_hooks(world.model, world.robot_body)) | set(hooks))


def palette(world, hooks: list[str]) -> dict:
    m = world.model
    kinds = _joints(world)
    driven = driven_joints(world)
    motors = sorted(n for n in driven if n in kinds)
    parts, layout, rng = world.parts, None, None
    if parts is not None:
        layout, rng = parts.layout, parts.range
    bars = sorted([*layout.bars, *layout.switches]) if layout is not None else []
    rule = rng.climb if rng is not None else None
    hook_list = hook_names(world, hooks)
    bar_set = set(bars)
    elements = sorted(e.name for e in rng.elements) if rng is not None else []
    pieces = sorted(layout.ball_names[:layout.n_floor]) if layout is not None else []   # on the field
    signals = ["tilt", *motors, *(f"{n}.current" for n in motors)]
    if hook_list:
        signals.append("hook_load")
    rods = sorted(layout.rods) if layout is not None else []
    if rng is not None and any(k.hole is not None for k in rng.kinds.values()):
        signals += [f"seated.{r}" for r in rods]                   # inches, empty while no piece is on it
    dt = drivetrain(world, driven)
    stations = sorted(rng.stations, key=lambda st: st.name) if rng is not None else []
    ball = floor_ball(world)
    floor_names = layout.ball_names[:layout.n_floor] if layout is not None else []
    return {
        "motors": [_entry(n, kinds[n]) for n in motors],
        "joints": [_entry(n, k) for n, k in sorted(kinds.items())],
        "bodies": sorted(m.body(b).name for b in _robot_bodies(world)),
        "parts": sorted(i.name for i in parts.instances) if parts is not None else [],
        "hooks": hook_list,
        "bars": bars,
        "elements": elements,
        "goals": sorted(g.name for g in rng.goals) if rng is not None else [],
        "turrets": turrets(world),          # stock turrets' instances: each turns `<inst>.turn`
        "rods": rods,                       # the field's rods (a reef's branches)
        "pieces": pieces,
        "targets": _targets(world, bar_set | set(elements) | set(pieces)),
        "levels": list(rule.levels) if rule is not None else [],      # lowest first, as the range lists them
        "drive": dt is not None,
        "drive_kind": dt.kind if dt is not None else None,
        "signals": signals,
        "stations": [st.name for st in stations],
        "station_settings": {st.name: _station_settings(st) for st in stations},
        "ball": rng.ball_preset if rng is not None else None,
        "balls": sorted(PRESETS["ball"]),
        "ball_choice": rng is not None and rng.path.stem in BALL_CHOICE_RANGES,
        "floor_piece": ball is not None,
        "piece": rng.kinds[rng.default_kind].preset if rng is not None else None,     # the default kind's
        "piece_presets": {"balls": sorted(PRESETS["ball"]), "pieces": sorted(PRESETS["piece"])},
        "kinds": _kinds(rng),
        "floor_kinds": dict(zip(floor_names, layout.kinds[:layout.n_floor])) if layout is not None else {},
        "piece_labels": _piece_labels(world, floor_names),
        "station_kinds": {st.name: st.kind or rng.default_kind for st in stations},
        "grips": _grips(world),
        "grip_materials": [[key, label, mu] for key, (label, mu) in GRIP_MATERIALS.items()],
        # where the floor spread's piece lies (in) and the nearest distance ahead Setup offers: {offset_in,
        # distance_in, nearest_in, kind (its kind's name), and a piece other than a ball's tip_deg and rotation_deg},
        # or null
        "floor_ball": None if ball is None else {**{k: round(v, 2) for k, v in ball.items()},
                                                 "kind": _spread_kind_name(world)},
    }


# ---- problems -------------------------------------------------------------------------------------------------------

class _Names:
    """The names a test can use on this world, for checking."""

    def __init__(self, world, hooks: list[str], program=None):
        p = palette(world, hooks)
        self.p = p
        self.joints = {j["name"]: j["kind"] for j in p["joints"]}
        self.motors = {j["name"] for j in p["motors"]}
        self.bodies = set(p["bodies"])
        self.robot = self.bodies | set(p["parts"]) | set(p["hooks"])
        self.bars = set(p["bars"])
        self.pieces = set(p["pieces"])
        self.elements = set(p["elements"])
        self.rods = set(p["rods"])
        self.goals = list(p["goals"])
        self.turrets = list(p["turrets"])
        rng = world.parts.range if world.parts is not None else None
        self.hollow = rng is not None and any(k.hole is not None for k in rng.kinds.values())
        self.field = set(world.contact_names()) | set(p["elements"]) | self.bars
        self.stations = set(p["stations"])
        self.hopper = list(program.hopper or []) if program is not None else []
        self.stock = {}                     # station -> its stock after the test's setup (None: no stock)
        if rng is not None:
            # only the field's own stations: a name it doesn't have is reported by problems() and leaves the others'
            # settings in force
            known = {st.name for st in rng.stations}
            asked = program.setup.get("station") or {} if program is not None else {}
            settings = {k: v for k, v in asked.items() if k in known}
            try:
                if settings:
                    rng = with_overrides(rng, settings)
            except RangeError:
                pass                        # reported at place [] by setup_problems
            self.stock = {st.name: st.stock for st in rng.stations}


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


def _no_goal(n: _Names, goal: str) -> str:
    return f"no goal '{goal}' on this field; its goals: {', '.join(n.goals)}"


def _aim_problems(n: _Names, turret: str, goal: str) -> list[str]:
    """An aim block's or aimed condition's turret and goal: a stock turret of the robot, a goal of the field."""
    out = []
    if not n.turrets:
        out.append("this robot has no turret to aim")
    elif turret not in n.turrets:
        out.append(f"{turret} isn't a turret; the robot's turrets: {', '.join(n.turrets)}")
    if not n.goals:
        out.append("this field has no goals to aim at")
    elif goal not in n.goals:
        out.append(_no_goal(n, goal))
    return out


def _cond_problems(n: _Names, c) -> list[str]:
    a = c.args
    if c.kind == "joint":
        return _joint_problems(n, a["name"], a["unit"], motor=False)
    if c.kind == "touch":
        out = []
        if a["field"] == ANY_BAR:
            out += [] if n.bars else ['"any bar" needs a field with bars']
        if a["robot"] == ANY_HOOK:
            if not n.p["hooks"]:
                out.append("this robot has no hooks; say which bodies are hooks in the test's setup")
        elif a["robot"] not in n.robot:
            out.append(f'no part or body "{a["robot"]}" on this robot any more')
        if a["field"] == NEXT_BAR:
            out += _bar_problems(n, NEXT_BAR)
        elif a["field"] != ANY_BAR and a["field"] not in n.field and a["field"] not in n.pieces:
            out.append(f'nothing called "{a["field"]}" on this field')
        return out
    if c.kind in ("piece_in", "held", "rests_on", "seated"):
        out = []
        if c.kind == "rests_on" and a["on"] != ON_PIECE and a["on"] not in n.elements:
            out.append(f'no element "{a["on"]}" on this field')
        if c.kind == "seated" and a["on"] not in n.rods:
            out.append(f'no rod "{a["on"]}" on this field')
        elif c.kind == "seated" and not n.hollow:
            out.append("this field's pieces have no hole to go on a rod")
        if c.kind == "piece_in" and a["body"] not in n.bodies:
            out.append(f'no body "{a["body"]}" on this robot any more')
        if c.kind == "held" and a["by"] not in n.robot:
            out.append(f'no body or part "{a["by"]}" on this robot (held by)')
        if a["piece"] == ANY_PIECE:
            # a piece a station feeds can be placed too (rests_on, seated)
            if not n.pieces and not (c.kind in ("rests_on", "seated") and n.stations):
                out.append("this field has no game pieces")
        elif a["piece"] not in n.pieces:
            out.append(f'no game piece "{a["piece"]}" on this field')
        return out
    if c.kind == "hanging_from":
        return _bar_problems(n, a["bar"])
    if c.kind in ("in_hopper", "made_it_in") and not n.hopper:
        return ["tick where a piece should end up in Setup: the bodies a piece lands in or is held by"]
    if c.kind == "scored":
        if not n.goals:
            return ["this field has no goals to score in"]
        if a["goal"] != ANY_GOAL and a["goal"] not in n.goals:
            return [_no_goal(n, a["goal"])]
        return []
    if c.kind == "aimed":
        return _aim_problems(n, a["turret"], a["at"])
    if c.kind == "station_empty":
        name = a["station"]
        if name not in n.stations:
            return [f'no station "{name}" on this field']
        if n.stock.get(name) is None:
            return [f'"{name}" never runs out (it has no stock); give it a stock in Setup']
        return []
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
    if b.do in ("start_feeding", "stop_feeding", "feed_one"):
        return [] if a["station"] in n.stations else [f'no station "{a["station"]}" on this field']
    if b.do == "turn":
        return [] if n.p["drive_kind"] else ["turn needs a drivetrain; this robot has none"]
    if b.do == "aim":
        out = _aim_problems(n, a["turret"], a["at"])
        if a["turret"] in n.turrets:
            out += _joint_problems(n, f"{a['turret']}.turn", None, motor=True)
        return out
    if b.do == "drive":
        if n.p["drive"]:
            if n.p["drive_kind"] == "tank" and a.get("direction") in ("left", "right"):
                return ["a tank drive can't drive sideways; turn, then drive forward"]
            return []
        if any(j["kind"] == "wheel" for j in n.p["motors"]):
            return ["drive works on a tank or swerve drive; spin the drive motors instead"]
        return ["drive needs a drivetrain; this robot has none"]
    for key in ("until", "that"):
        if key in a:
            return _cond_problems(n, a[key])
    return []


def setup_problems(program, rng) -> list[str]:
    """What the test's setup asks of the field that the field won't do: the messages of the RangeErrors from applying
    its ball or piece and station settings to `rng` (the file path dropped), e.g. a piece too big for a station's
    chute. Names the field doesn't have are left to problems(), which says them in its own words."""
    out: list[str] = []

    def say(e: RangeError) -> str:
        return str(e).removeprefix(f"{rng.path}: ")

    ball, piece = program.setup.get("ball"), program.setup.get("piece")
    if (ball is not None or piece is not None) and rng.path.stem in BALL_CHOICE_RANGES:
        try:
            rng = load_range(rng.path.as_posix(), rng.path.parent, ball=ball, piece=piece)
        except RangeError as e:
            out.append(say(e))
    known = {st.name for st in rng.stations}
    settings = {k: v for k, v in program.setup.get("station", {}).items() if k in known}
    if settings:
        try:
            with_overrides(rng, settings)
        except RangeError as e:
            out.append(say(e))
    return out


def _setup_level_problems(program, world, n: _Names) -> list[str]:
    tr = world.test_range
    rng = world.parts.range if world.parts is not None else None
    out = []
    setup = program.setup
    if "ball" in setup and rng is None:
        out.append("the plain field has no ball to pick; clear the ball in Setup")
    elif "ball" in setup and not n.p["ball_choice"]:
        if n.p["ball"]:
            out.append("this field keeps its own ball; pick a ball on the intake or station field")
        else:
            out.append("this field has no ball to pick; clear the ball in Setup")
    if "piece" in setup and rng is None:
        out.append("the plain field has no piece to pick; clear the piece in Setup")
    elif "piece" in setup and not n.p["ball_choice"]:
        out.append("this field keeps its own pieces; pick a piece on the intake or station field")
    out += [f'Setup names a station this field doesn\'t have: "{name}"'
            for name in setup.get("station", {}) if name not in n.stations]
    if setup.get("floor_spread") and spread_ball(world) is None:
        out.append(NO_BALL)
    if rng is not None and tr is not None:
        out += setup_problems(program, rng)
    return out


def problems(program, world) -> list[dict]:
    """[{"place": [i, j, ...], "message": str}]: each name the program uses that this world doesn't have. A hook or
    hopper body the test's setup names that isn't a body of the robot, and what the setup asks of the field, are at
    place []. Empty when the program can run."""
    n = _Names(world, list(program.hooks), program)
    out = [{"place": [], "message": f'no body "{h}" on this robot any more (the test lists it as a hook)'}
           for h in program.hooks if h not in n.bodies]
    out += [{"place": [], "message": f'no body "{h}" on this robot any more (the test lists it as the hopper)'}
            for h in program.hopper or [] if h not in n.bodies]
    out += [{"place": [], "message": msg} for msg in _setup_level_problems(program, world, n)]

    def walk(blocks, trail):
        for i, b in enumerate(blocks):
            place = [*trail, i]
            out.extend({"place": place, "message": msg} for msg in _block_problems(n, b))
            walk(b.body, place)

    walk(program.blocks, [])
    return out
