"""The Test tab's server side: a block test (`<name>.test.toml`, scrimmage/blocks/) behind /api/test/*.

A test opens in robot mode: the robot document is the test's `robot`, and the App keeps it in step with the file
(an assembler edit, an outside edit, Import again). The session holds the test's Program, the palette and problems of
the robot as it is now and its hints, which never stop a run (`refresh`, called by the App whenever it validates the
robot), and one run, sweep or floor spread at a time on a worker thread ("test-run", "test-sweep", "test-spread"),
which holds the SimLock as "test" while it runs.

Every change bumps one `version`, which the page long-polls. A save is refused (409) when the test changed after the
version the page sent (`_changed_at`): a run's progress doesn't make the page's version stale. An outside edit of the
test file is read on the App's next sync; one that can't be read keeps the last good test and says why (`file_error`)
until it is fixed.

Runs are kept where `scrimmage test run` keeps them (scrimmage/blocks/store.py: `<test stem>-runs/` next to the test).

A new test starts with blocks that do something on its robot (`starter_blocks`) and its hooks and hopper preselected
(`starter_hooks`, `starter_hopper`); the Part tab's "Test it" puts the built parts on a stand-in robot
(`standin_config`). The state carries every field Setup can pick (`field_choices`).
"""

from __future__ import annotations

import copy
import functools
import logging
import os
import re
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from types import SimpleNamespace

import tomlkit

from .. import i18n
from ..blocks import store
from ..blocks.palette import ANY_PIECE, hints, palette, problems
from ..blocks.program import Block, Cond, TestFileError, block_json, dumps, from_json, load, to_json
from ..blocks.run import TestRun, air_lines, clearance_lines, goal_lines, world_for
from ..blocks.spread import inside_robot, no_ball, no_spread, run_spread
from ..blocks.sweep import MAX_SWEEP, _kind as sweep_kind, run_sweep
from ..cad.settings import replace_file
from ..parts.model import PartError
from ..testrange.spec import BALL_CHOICE_RANGES, BUILTIN_RANGES, load_range
from .bench_session import SessionError
from .edits import add_part, cad_offset
from .simlock import SimBusy, SimLock
from .templates import new_config

log = logging.getLogger("scrimmage.assembler")
PROGRESS_PERIOD = 0.25    # s of wall time between progress updates while a run goes
POLL_SECONDS = 25.0


STARTER_DRIVE_M = 0.3    # a new test's drive up to the bars
STARTER_VOLTS = 6.0      # ... its spin of the first motor, for STARTER_WAIT_S
STARTER_WAIT_S = 2.0
STARTER_MOVE_DEG = 45.0  # ... its move of a lone hinge motor
STARTER_HOPPER_COUNT = 5        # ... on a field with a station: feed until this many are in the hopper
STARTER_FEED_TIMEOUT = 20.0     # s: ... giving up after this long
STANDIN_RANGE = {"file": "ladder", "start": "below"}   # the stand-in for a part with a hook: in front of the ladder
STANDIN_STATION = {"file": "station", "start": "at_station"}   # ... with a hopper-like body: at the station's chute
STANDIN_REEF = {"file": "reef", "start": "facing_coral"}       # ... with a grabber-like body: facing the floor coral
GRABBER_CLOSE_VOLTS = 4.0       # a grabber's starter closes its jaws at this (a NEO squeezes about 260 N on 300:1)
GRABBER_CLOSE_S = 1.0           # s: ... waits this long for them to close
GRABBER_LIFT_DEG = -30.0        # ... lifts with the part's lone arm hinge to this (the reef example's forward and up)
GRABBER_HOLD_TIMEOUT = 10.0     # s: ... then waits this long for a piece to be held
GRABBER_WAIT_S = 2.0            # ... then holds it this long, expected held throughout


def field_choices() -> list[dict]:
    """Every field the Test tab's Setup can put the robot on: the plain field (no [range]) first, then each built-in
    range in BUILTIN_RANGES order with its name, its starts (file order: Setup picks the first), whether its piece is
    a disc, whether Setup can change its ball, and its stations. A fresh copy each call (the ranges are read once)."""
    out = copy.deepcopy(_field_choices())
    out[0]["name"] = i18n.t("The plain field")
    return out


@functools.cache
def _field_choices() -> list[dict]:
    out: list[dict] = [{"file": None, "name": None, "starts": []}]       # its name: field_choices
    for name in BUILTIN_RANGES:
        rng = load_range(name, Path("."))
        out.append({"file": name, "name": rng.name, "starts": list(rng.starts), "disc": rng.disc is not None,
                    "ball_choice": name in BALL_CHOICE_RANGES,
                    "stations": sorted(st.name for st in rng.stations)})   # sorted, as the palette's
    return out


def station_starter(pal: dict | None) -> list[Block]:
    """The starter blocks for a field with a station: start feeding from the first, wait until 5 pieces are in the
    hopper (giving up after 20 s), stop feeding. With a grabber (grabber_part's), wait until the grabber holds a piece
    instead (ruling P31: a grabber holds one piece, never 5). Empty without a station."""
    stations = (pal or {}).get("stations") or []
    if not stations:
        return []
    first = stations[0]
    grabber = grabber_part(pal)
    until = Cond("held", {"piece": ANY_PIECE, "by": grabber}) if grabber \
        else Cond("in_hopper", {"at_least": STARTER_HOPPER_COUNT})
    return [Block("start_feeding", {"station": first}),
            Block("wait_until", {"until": until, "timeout": STARTER_FEED_TIMEOUT}),
            Block("stop_feeding", {"station": first})]


def grabber_part(pal: dict | None) -> str | None:
    """The robot's grabber: the part of its first body named like a grabber's, else its first part named like one
    (as standin_config sends both to the reef); None without one."""
    pal = pal or {}
    grabbers = [b.split(".", 1)[0] for b in pal.get("bodies") or [] if _grabby(b.rsplit(".", 1)[-1])]
    grabbers += [p for p in pal.get("parts") or [] if _grabby(p)]
    return grabbers[0] if grabbers else None


def grabber_starter(pal: dict | None) -> list[Block]:
    """The starter blocks for a robot with a grabber (grabber_part's) on a field with rods (the reef). Spin each of that part's motors whose joint is named like a jaw (CLOSE_WORDS) at 4 V, closing the grab (positive
    volts close, as the reef example's jaws do), and wait 1 s; when the part has one other hinge motor (an arm), move
    it to -30 degrees to lift the piece (held means off the carpet); wait until any piece is held by the part (giving
    up after 10 s), then expect it held throughout a 2 s wait. Empty without rods or such a part."""
    pal = pal or {}
    part = grabber_part(pal)
    if not pal.get("rods") or part is None:
        return []
    mine = [m for m in pal.get("motors") or [] if m["kind"] != "wheel" and m["name"].startswith(part + ".")]
    jaws = [m["name"] for m in mine if any(w in m["name"].rsplit(".", 1)[-1].lower() for w in CLOSE_WORDS)]
    arms = [m["name"] for m in mine if m["kind"] == "hinge" and m["name"] not in jaws]
    held = Cond("held", {"piece": ANY_PIECE, "by": part})
    close = [Block("spin", {"motor": j, "volts": GRABBER_CLOSE_VOLTS}) for j in jaws]
    close.append(Block("wait", {"seconds": GRABBER_CLOSE_S}))
    if len(arms) == 1:
        close.append(Block("move", {"joint": arms[0], "to": GRABBER_LIFT_DEG, "unit": "deg"}))
    return [*close,
            Block("wait_until", {"until": held, "timeout": GRABBER_HOLD_TIMEOUT}),
            Block("expect", {"that": copy.deepcopy(held), "throughout": True}),
            Block("wait", {"seconds": GRABBER_WAIT_S})]


def starter_blocks(pal: dict | None) -> list[Block]:
    """A new test's blocks for a robot with this palette: with a grabber on a field with rods, the grabber starter; on
    a field with a station, the station starter; else drive 0.3 m first when it can drive and the field has bars;
    then a robot whose only mechanism motor is a hinge moves it to 45 degrees, else the first mechanism motor spins at
    6 V for 2 s and everything stops; a robot with nothing to move waits 1 s."""
    feed = grabber_starter(pal) or station_starter(pal)
    if feed:
        return feed
    out = []
    pal = pal or {}
    if pal.get("drive") and pal.get("bars"):
        out.append(Block("drive", {"distance": STARTER_DRIVE_M}))
    mech = [m for m in pal.get("motors") or [] if m["kind"] != "wheel"]
    if len(mech) == 1 and mech[0]["kind"] == "hinge":
        out.append(Block("move", {"joint": mech[0]["name"], "to": STARTER_MOVE_DEG, "unit": "deg"}))
    elif mech:
        out += [Block("spin", {"motor": mech[0]["name"], "volts": STARTER_VOLTS}),
                Block("wait", {"seconds": STARTER_WAIT_S}), Block("stop", {"motor": "all"})]
    return out or [Block("wait", {"seconds": 1.0})]


def _hooky(name: str) -> bool:
    return "hook" in name.rsplit(".", 1)[-1].lower()


def starter_hooks(pal: dict | None) -> list[str]:
    """The robot's bodies whose names contain "hook", but the stock climber's (they count already)."""
    pal = pal or {}
    counted = set(pal.get("hooks") or [])
    return [b for b in pal.get("bodies") or [] if _hooky(b) and b not in counted]


GRABBER_WORDS = ("claw", "grabber", "gripper", "jaw")
CLOSE_WORDS = ("jaw", "claw", "grip", "finger")     # a grabber's joints that close the grab (ruling P24)
HOPPER_WORDS = ("hopper", "intake", "indexer", *GRABBER_WORDS)     # where a piece should end up: held counts


def _hoppery(name: str) -> bool:
    return any(w in name.lower() for w in HOPPER_WORDS)


def _grabby(name: str) -> bool:
    return any(w in name.lower() for w in GRABBER_WORDS)


def pick_hopper(bodies: list[str], roots: dict[str, str]) -> list[str]:
    """The hopper bodies among `bodies` ("<part>.<body>" names): those whose own name (after the last dot) contains
    one of HOPPER_WORDS ("hopper", "intake", "indexer", or a grabber's "claw", "grabber", "gripper", "jaw"); when
    none does, the root body of each part whose name contains one (`roots`: part -> its root body's "<part>.<body>"
    name), never its other bodies: an intake's roller counted as "in" would make the catch rate look better than it
    is."""
    own = [b for b in bodies if _hoppery(b.rsplit(".", 1)[-1])]
    return own or [r for part, r in roots.items() if _hoppery(part)]


def part_roots(world) -> dict[str, str]:
    """Each robot part instance's root body ("<instance>.<body>"): its first body whose parent body is another
    part's (or the world's), from the MuJoCo body tree."""
    m = world.model
    root = m.body_rootid[world.robot_body]
    out: dict[str, str] = {}
    for b in range(m.nbody):
        name = m.body(b).name
        if m.body_rootid[b] != root or "." not in name:
            continue
        inst = name.split(".", 1)[0]
        parent = m.body(int(m.body_parentid[b])).name
        if inst not in out and parent.split(".", 1)[0] != inst:
            out[inst] = name
    return out


def starter_hopper(pal: dict | None, roots: dict[str, str] | None = None) -> list[str]:
    """The robot's bodies a new test preselects as its hopper (pick_hopper over the palette's bodies; `roots`:
    part_roots of its world)."""
    return pick_hopper(list((pal or {}).get("bodies") or []), roots or {})


PWM_CHANNELS = range(20)          # a roboRIO's PWM outputs: 0-9 on the board, 10-19 on the MXP
CAN_IDS = range(1, 63)            # CAN IDs a motor controller can take (0 is a new controller's factory ID)
CAN_MOTORS = ("SparkMax", "SparkFlex", "TalonFX")      # the CAN motor controllers a config names in `ids`
_DEVICE = re.compile(r"^([A-Za-z][A-Za-z0-9]*):(\d+)$")


def _device_texts(v):
    """Every "<Kind>:<id>" text in one `ids` value (a short form, a table's ctre/rev/... entries, or a list of
    either: a slot bound to several motors)."""
    if isinstance(v, str):
        m = _DEVICE.match(v)
        if m:
            yield m.group(1), int(m.group(2))
    elif isinstance(v, dict):
        for x in v.values():
            yield from _device_texts(x)
    elif isinstance(v, (list, tuple)):
        for x in v:
            yield from _device_texts(x)


def free_motor_ids(doc, slots: list[str]) -> dict[str, str]:
    """A free device ID for each motor slot in `slots`, none of them one the robot document `doc` uses already: PWM
    channels, or CAN IDs of the robot's CAN motor controller when its motors are on CAN (more CAN motor controllers
    than PWM motors). ValueError when there are too few left."""
    pwm, can, kinds = set(), set(), []
    for p in doc.get("part", []):
        for v in (p.get("ids") or {}).values():
            for kind, n in _device_texts(v.unwrap() if hasattr(v, "unwrap") else v):
                if kind == "PWM":
                    pwm.add(n)
                elif kind not in ("DIO", "AIO", "Analog"):
                    can.add(n)                  # one CAN bus: an ID is taken whatever the device is
                    if kind in CAN_MOTORS:
                        kinds.append(kind)
    on_can = len(kinds) > len(pwm)
    kind = max(dict.fromkeys(kinds), key=kinds.count) if on_can else "PWM"
    free = [n for n in (CAN_IDS if on_can else PWM_CHANNELS) if n not in (can if on_can else pwm)]
    if len(free) < len(slots):
        raise ValueError(i18n.tn("the robot has no free CAN IDs left for {count} more motor",
                                 "the robot has no free CAN IDs left for {count} more motors", len(slots)) if on_can
                         else i18n.tn("the robot has no free PWM channels left for {count} more motor",
                                      "the robot has no free PWM channels left for {count} more motors", len(slots)))
    return {slot: f"{kind}:{n}" for slot, n in zip(slots, free)}


def _place(doc, parts: list[tuple[str, dict]], root_body: str, motor_slots: dict | None = None) -> list[str]:
    """Add to the robot document `doc` the part folders `parts` [(path as the config writes it, cad_marker)] of one
    CAD import that it doesn't have yet (a [[part]] with that path), where the CAD has them (edits.cad_offset): a part
    the robot carries on the stock chassis's root body at zero offset, a part the CAD hangs on another's body on that
    body (the other one first). Each is named after its part (made unique), and its motor slots (`motor_slots`: part
    name -> slot names) get device IDs the robot doesn't use yet (free_motor_ids), so they run at once. Returns the
    names added."""
    have = {str(p.get("path")).replace("\\", "/"): str(p["name"]) for p in doc.get("part", []) if p.get("path")}
    taken = {str(p["name"]) for p in doc.get("part", [])}
    names: dict = {}            # part name -> instance name
    for path, m in parts:
        if path in have:
            names[m["part"]] = have[path]
            continue
        base = re.sub(r"[^A-Za-z0-9_]", "_", m["part"] or "part") or "part"
        name, k = base, 2
        while name in taken:
            name, k = f"{base}_{k}", k + 1
        taken.add(name)
        names[m["part"]] = name
    by_part = {m["part"]: (path, m) for path, m in parts}
    done = {m["part"] for path, m in parts if path in have}
    added: list[str] = []

    def add(part, trail: tuple = ()) -> None:
        if part in done:
            return
        path, m = by_part[part]
        attach, pos = f"drive.{root_body}", [0.0, 0.0, 0.0]
        if m["attach"] is not None:
            target, body = m["attach"].split(".")
            if target in by_part and target not in trail:
                add(target, (*trail, part))
                attach = f"{names[target]}.{body}"
                pos = cad_offset(m, body, by_part[target][1], chassis_root=False) or pos
        done.add(part)
        ids = free_motor_ids(doc, list((motor_slots or {}).get(part) or []))
        add_part(doc, name=names[part], path=path, attach=attach, pos=pos, ids=ids or None)
        added.append(names[part])

    for _, m in parts:
        add(m["part"])
    return added


def standin_config(parts: list[tuple[str, dict]], root_body: str, motor_slots: dict | None = None,
                   roots: dict[str, str] | None = None) -> str:
    """The stand-in robot (templates' "standin") carrying the part folders `parts` [(path, cad_marker)] of one CAD
    import where the CAD has them, their motor slots on free IDs (see _place). With a body named like a hook, the robot
    starts in front of the ladder range; else with a hopper body (pick_hopper; `roots`: part name -> its root body's
    name, or None) named like a grabber's (in its own name or its part's), facing the reef field's floor coral; else
    with any other hopper body, at the station range's chute (the Test tab's Setup changes the field)."""
    doc = tomlkit.parse(new_config("standin"))
    _place(doc, parts, root_body, motor_slots)
    bodies = [f"{m['part']}.{b}" for _, m in parts for b in m["origins"]]
    named = {m["part"] for _, m in parts}
    hopper = pick_hopper(bodies, {p: f"{p}.{r}" for p, r in (roots or {}).items() if p in named and r})
    if any(_hooky(b) for b in bodies):
        field = STANDIN_RANGE
    elif any(_grabby(b) for b in hopper):      # a grabber's body, or the root of a part named like one
        field = STANDIN_REEF
    else:
        field = STANDIN_STATION if hopper else None
    if field is not None:
        rng = tomlkit.table()
        rng.update(field)
        doc.add(tomlkit.nl())
        doc["range"] = rng
    return tomlkit.dumps(doc)


def add_to_standin(text: str, parts: list[tuple[str, dict]], root_body: str,
                   motor_slots: dict | None = None) -> tuple[str, list[str]]:
    """An existing stand-in's text with the import's parts it doesn't have yet added where the CAD has them (their
    motor slots on IDs it doesn't use yet), and their names; nothing is removed or moved, and its field stays."""
    doc = tomlkit.parse(text)
    added = _place(doc, parts, root_body, motor_slots)
    return tomlkit.dumps(doc), added


def replace_text(path: Path, text: str) -> None:
    """Write `text` over the file at `path` in one step (a temp file beside it, then replaced)."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False,
                                         encoding="utf-8", newline="\n") as f:
            tmp = Path(f.name)
            f.write(text)
        replace_file(tmp, path)
    except BaseException:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise


def write_new(path: Path, text: str) -> bool:
    """Write a new file; False when it is there already (it is never replaced)."""
    try:
        with open(path, "x", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return True
    except FileExistsError:
        return False


def config_path(target: Path, base: Path) -> str:
    """`target` as a config writes it: relative to `base` ("/"-separated) when it can be, else absolute."""
    try:
        return Path(os.path.relpath(target, base)).as_posix()
    except ValueError:      # another drive (Windows)
        return target.as_posix()


def _sig(path: Path) -> bytes | None:
    """The file's bytes (a test file is small; a time stamp can miss a quick second edit on a coarse clock)."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _plain_error(e: BaseException) -> str:
    return i18n.t("the run stopped with an error ({kind}: {why})", kind=type(e).__name__, why=e)


def _busy() -> str:
    return i18n.t("a test run is in progress; wait for it to finish or stop it")


class TestSession:
    __test__ = False     # not a pytest class

    def __init__(self, path: Path, simlock: SimLock, version: int = 0):
        """TestFileError when the file can't be read as a test (OSError when it can't be read at all)."""
        self.path = Path(path).resolve()
        self.simlock = simlock
        self.program = load(self.path)
        self._sig = _sig(self.path)
        self.version = version
        self._changed_at = version
        self._cond = threading.Condition()
        self.palette: dict | None = None
        self.starter_hopper: list[str] = []      # what Setup ticks for a test with no hopper (starter_hopper)
        self.problems: list[dict] = []
        self.hints: list[dict] = []          # what the test can run with but should know (palette.hints)
        self.file_error: str | None = None   # the test file as it is on disk can't be read (an outside edit)
        self.run_error: str | None = None    # why the last run, sweep or spread couldn't run
        self.running: dict | None = None     # {"kind": "run" | "sweep" | "spread", "progress": 0..1}
        self.last: dict | None = None        # the last run, sweep or spread this session made: {"kind", "id"}
        self._world = None
        self._robot_problem: str | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._kept: tuple | None = None      # (the runs folder's look, list_runs, list_sweeps, list_spreads)
        self._lang: str | None = None        # the language the problems and hints were last made in

    # -- what it is -------------------------------------------------------------------------
    @property
    def robot_path(self) -> Path:
        """The robot config the test names (relative to the test file's folder)."""
        return (self.path.parent / self.program.robot).resolve()

    @property
    def runs_dir(self) -> Path:
        return store.runs_dir(self.path)

    def _bump(self, changed: bool = False) -> None:
        self.version += 1
        if changed:
            self._changed_at = self.version
        self._cond.notify_all()

    # -- the robot and the file -------------------------------------------------------------
    def _names(self) -> tuple[dict | None, list[dict], list[dict]]:
        """The palette, the problems and the hints."""
        if self._world is None:
            return None, [{"place": [], "message": self._robot_problem or i18n.t("the robot isn't loaded")}], []
        try:
            # the robot config's world, with the test's setup's partner (world_for builds a run's with it)
            pal = palette(self._world, list(self.program.hooks), self.program.setup)
            return pal, problems(self.program, self._world), hints(self.program, self._world, pal)
        except Exception as e:   # a robot the palette can't read must not take the Test tab down
            log.exception("test palette")
            return None, [{"place": [], "message": i18n.t("can't read the robot's motors and joints ({why})",
                                                          why=e)}], []

    def _recompute(self) -> None:
        self._lang = i18n.current_language()        # the problems' and hints' words are in it
        pal, probs, tips = self._names()
        hopper = starter_hopper(pal, part_roots(self._world)) if pal is not None else []
        if (pal, probs, tips, hopper) != (self.palette, self.problems, self.hints, self.starter_hopper):
            self.palette, self.problems, self.hints, self.starter_hopper = pal, probs, tips, hopper
            self._bump()

    def refresh(self, world, robot_problem: str | None) -> None:
        """The robot as it is now: its World (None when it doesn't load, `robot_problem` saying why)."""
        with self._cond:
            self._world, self._robot_problem = world, robot_problem
            self._recompute()

    def reload_if_changed(self) -> bool:
        """Read an outside edit of the test file; True when the test (and so maybe its robot) changed."""
        sig = _sig(self.path)
        with self._cond:
            if sig == self._sig or sig is None:
                return False
            self._sig = sig
            try:
                new = load(self.path)
            except (TestFileError, OSError, UnicodeDecodeError) as e:
                msg = str(e) if isinstance(e, TestFileError) else i18n.t("can't read {name}: {why}",
                                                                         name=self.path.name, why=e)
                if msg != self.file_error:
                    self.file_error = msg
                    self._bump(changed=True)
                return False
            self.program, self.file_error = new, None
            self._bump(changed=True)
            self._recompute()
            return True

    def save(self, body: dict) -> tuple[int, dict]:
        """Replace the test with body["test"] (the state's `test` shape) and write the file."""
        v = body.get("version")
        if not isinstance(v, int) or isinstance(v, bool):
            raise SessionError(400, i18n.t("version (the integer from the state) is required"))
        with self._cond:
            if self._closed:
                raise SessionError(409, i18n.t("the test was closed"))
            if v < self._changed_at:
                return 409, {"ok": False, "error": i18n.t("the test changed (another tab, or an outside edit); the "
                                                          "page has reloaded it"), "state": self.state()}
            try:
                new = from_json(body.get("test"), self.path)
            except TestFileError as e:      # a bad block's place goes as a code, so the page needn't read the words
                raise SessionError(400, str(e), {"place": e.place, "problem": e.problem} if e.place else None) from None
            text = dumps(new)
            tmp = None
            try:
                with tempfile.NamedTemporaryFile("w", dir=self.path.parent, prefix=f".{self.path.name}.",
                                                 suffix=".tmp", delete=False, encoding="utf-8", newline="\n") as f:
                    tmp = Path(f.name)
                    f.write(text)
                replace_file(tmp, self.path)
            except OSError as e:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
                raise SessionError(400, i18n.t("can't write {name}: {why}",
                                               name=self.path.name, why=e.strerror or e)) from None
            self._sig = _sig(self.path)
            self.program, self.file_error = new, None
            self._bump(changed=True)
            self._recompute()
            return 200, {"ok": True, "version": self.version}

    # -- the grip ---------------------------------------------------------------------------
    def grip(self, body: dict) -> tuple[int, dict]:
        """Setup's grip picker: body {body: "<instance>.<body>", grip: a GRIP_MATERIALS key, a number (my own
        friction) or null (the default)} written into that path part's folder, in its own edits (parts/grip.py
        set_grip), which the Part tab edits too. The App then sees the folder change and builds the robot again.
        400 for a grip, part or body it can't take (nothing is written); 409 while a run, sweep or spread goes."""
        from ..parts.grip import set_grip
        name, grip = body.get("body"), body.get("grip")
        with self._cond:
            if self._closed:
                raise SessionError(409, i18n.t("the test was closed"))
            if self.running is not None:
                raise SessionError(409, _busy())
            world = self._world
        if not isinstance(name, str) or name.count(".") != 1 or not all(name.split(".")):
            raise SessionError(400, i18n.t("body must be \"{part_body}\", such as \"{claw_pad_left}\", not {name}",
                                           name=repr(name), part_body="<part>.<body>", claw_pad_left="claw.pad_left"))
        if world is None or world.parts is None:
            raise SessionError(409, i18n.t("fix the robot first: {why}",
                                           why=self._robot_problem or i18n.t("it is not made of parts")))
        part, b = name.split(".")
        inst = next((i for i in world.parts.instances if i.name == part), None)
        if inst is None:
            raise SessionError(400, i18n.t('no part "{part}" on this robot', part=part))
        if inst.source.startswith("stock "):
            raise SessionError(400, i18n.t("{part}: grip is for a part folder's bodies; a stock part has its own "
                                           "materials", part=part))
        found = next((x for x in inst.part.bodies if x.name == b), None)
        if found is None:
            raise SessionError(400, i18n.t('no body "{body}" in {part}; its bodies: {names}',
                                           body=b, part=part, names=', '.join((x.name for x in inst.part.bodies))))
        if grip is not None and not any(g.collide and not g.tread for g in found.geoms):
            raise SessionError(400, i18n.t("{name} touches nothing (no geom collides), so a grip on it does nothing",
                                           name=name))
        own = self._config_grip(part)
        if grip is not None and b in own:       # clearing the folder's own grip is always fine
            raise SessionError(400, i18n.t("{file} sets {body}'s grip itself (its {part_table} {part}'s grip), and "
                                           "that wins over the part folder's; change it there",
                                           file=self.robot_path.name, body=name, part=part, part_table="[[part]]"))
        folder = Path(inst.source)
        try:
            set_grip(folder.parent if folder.is_file() else folder, b, grip)
        except PartError as e:
            raise SessionError(400, str(e)) from None
        except OSError as e:
            raise SessionError(400, i18n.t("can't write the grip in {folder}: {why}",
                                           folder=folder, why=e.strerror or e)) from None
        return 200, {"ok": True}

    def _config_grip(self, part: str) -> dict:
        """The robot config's own `grip` table of [[part]] `part` ({} without one)."""
        try:
            raw = tomllib.loads(self.robot_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return {}
        entry = next((e for e in raw.get("part") or [] if isinstance(e, dict) and e.get("name") == part), {})
        own = entry.get("grip")
        return own if isinstance(own, dict) else {}

    # -- runs -------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        with self._cond:
            return self.running is not None

    def start(self, kind: str, body: dict | None = None) -> tuple[int, dict]:
        """Start a run ("run"), a sweep ("sweep", body {target, values}) or the test's floor spread ("spread") on the
        worker thread."""
        body = body or {}
        target = values = None
        if kind == "sweep":
            target, values = body.get("target"), body.get("values")
            try:
                sweep_kind(target)
            except ValueError as e:
                raise SessionError(400, str(e)) from None
            names = isinstance(target, dict) and "grip" in target       # a grip sweep's values may be material names
            if not isinstance(values, list) or not values or len(values) > MAX_SWEEP or not all(
                    (isinstance(x, str) and names) or (isinstance(x, (int, float)) and not isinstance(x, bool))
                    for x in values):
                raise SessionError(400, i18n.t("values must be a list of 1 to {max} material names or numbers",
                                               max=MAX_SWEEP) if names
                                   else i18n.t("values must be a list of 1 to {max} numbers", max=MAX_SWEEP))
        with self._cond:
            if self._closed:
                raise SessionError(409, i18n.t("the test was closed"))
            if self.running is not None:
                raise SessionError(409, _busy())
            if self.file_error is not None:
                raise SessionError(409, i18n.t("fix the test file first: {file_error}", file_error=self.file_error))
            if kind == "spread" and not self.program.setup.get("floor_spread"):
                raise SessionError(409, no_spread())
            if kind == "spread" and self.palette is not None and not self.palette.get("floor_piece"):
                raise SessionError(409, no_ball())
            if kind == "spread" and self._world is not None:
                inside = inside_robot(self._world, self.program.setup["floor_spread"])
                if inside is not None:
                    raise SessionError(409, inside)
            if self.problems:
                return 409, {"ok": False, "error": i18n.t("Fix the problems first: the test can't run on this robot "
                                                          "yet."), "problems": self.problems}
            try:
                self.simlock.acquire("test")
            except SimBusy as e:
                raise SessionError(409, e.wait) from None
            program = self.program
            self._cancel = cancel = threading.Event()
            self.running = {"kind": kind, "progress": 0.0}
            self.run_error = None
            self._bump()
            if kind == "run":
                work, args = self._run, (program, cancel)
            elif kind == "spread":
                work, args = self._spread, (program, cancel)
            else:
                work, args = self._sweep, (program, target, values, cancel)
            self._thread = threading.Thread(target=work, args=args, daemon=True, name=f"test-{kind}")
            try:
                self._thread.start()
            except BaseException:
                self.simlock.release("test")
                self.running = None
                self._bump()
                raise
        return 200, {"ok": True}

    def cancel(self) -> dict:
        self._cancel.set()
        return {"ok": True}

    def _progress(self, p: float, last: list) -> None:
        now = time.monotonic()
        if now - last[0] < PROGRESS_PERIOD:
            return
        last[0] = now
        with self._cond:
            if self.running is not None:
                self.running = {**self.running, "progress": max(0.0, min(1.0, p))}
                self._bump()

    def _finish(self, last: dict | None, error: str | None) -> None:
        self.simlock.release("test")    # first: a page that sees the run finished may start a drive session
        with self._cond:
            self.running = None
            self._kept = None                # read the runs folder again, whatever its clock says
            if last is not None:
                self.last = last
            self.run_error = error
            self._bump()

    def _run(self, program, cancel: threading.Event) -> None:
        last = error = None
        clock = [time.monotonic()]
        try:
            limit = program.time_limit
            result = TestRun(world_for(program), program).run(
                on_sample=lambda t, row: self._progress(t / limit, clock), cancel=cancel)
            folder = store.save_run(result, program, self.runs_dir)
            last = {"kind": "run", "id": folder.name}
        except (ValueError, OSError) as e:      # TestFileError, ConfigError and PartError are ValueErrors
            error = str(e)
        except Exception as e:
            log.exception("test run")
            error = _plain_error(e)
        finally:
            self._finish(last, error)

    def _sweep(self, program, target: dict, values: list, cancel: threading.Event) -> None:
        last = error = None
        clock = [time.monotonic()]
        try:
            folder = run_sweep(program, target, values, self.runs_dir, cancel=cancel,
                               on_progress=lambda k, n, row: self._progress(k / n, clock))
            last = {"kind": "sweep", "id": folder.name}
        except (ValueError, OSError) as e:
            error = str(e)
        except Exception as e:
            log.exception("test sweep")
            error = _plain_error(e)
        finally:
            self._finish(last, error)

    def _spread(self, program, cancel: threading.Event) -> None:
        last = error = None
        clock = [time.monotonic()]
        try:
            folder = run_spread(program, self.runs_dir, cancel=cancel,
                                on_progress=lambda k, n, row: self._progress(k / n, clock))
            last = {"kind": "spread", "id": folder.name}
        except (ValueError, OSError) as e:
            error = str(e)
        except Exception as e:
            log.exception("test spread")
            error = _plain_error(e)
        finally:
            self._finish(last, error)

    # -- reading ----------------------------------------------------------------------------
    def _kept_runs(self) -> tuple[list, list, list]:
        """list_runs, list_sweeps and list_spreads, read again only when the runs folder changes: a run, a sweep or a
        spread is renamed into it whole and pruned whole, so its time and its names tell (the page polls four times a
        second while a run goes)."""
        base = self.runs_dir
        try:
            look = (base.stat().st_mtime_ns, tuple(sorted(p.name for p in base.iterdir())))
        except OSError:
            look = None
        kept = self._kept
        if kept is None or kept[0] != look:
            kept = self._kept = (look, store.list_runs(base), store.list_sweeps(base), store.list_spreads(base))
        return kept[1], kept[2], kept[3]

    def state(self) -> dict:
        with self._cond:
            if self._lang != i18n.current_language():
                self._recompute()           # the Language setting changed: the problems and hints in its words
            out = {"version": self.version, "path": str(self.path), "test": to_json(self.program),
                   "palette": self.palette, "problems": self.problems, "hints": self.hints, "running": self.running,
                   "file_error": self.file_error, "run_error": self.run_error, "last": self.last,
                   "starter_hopper": list(self.starter_hopper),
                   "station_starter": [block_json(b) for b in station_starter(self.palette)] or None}
        out["fields"] = field_choices()
        out["runs"], out["sweeps"], out["spreads"] = self._kept_runs()
        return out

    def wait(self, since: int | None, end: float) -> bool:
        """Wait (at most a second, and not past `end`) for the version to move from `since`; True when it has, or
        there is nothing to wait for."""
        with self._cond:
            if since is None or self.version != since or self._closed or time.monotonic() >= end:
                return True
            self._cond.wait(min(1.0, max(0.0, end - time.monotonic())))
            return self.version != since or self._closed

    def run_file(self, run_id: str, what: str) -> Path:
        """A kept run's view.json ("view") or motion.bin ("motion"); 404 for a run that isn't kept."""
        try:
            folder = store.run_folder(self.runs_dir, run_id)
        except FileNotFoundError:
            raise SessionError(404, i18n.t("no run {run_id} is kept for this test", run_id=run_id)) from None
        return folder / ("view.json" if what == "view" else "motion.bin")

    def run_result(self, run_id: str) -> dict:
        """A kept run's result.json fields plus id, signals and `lines`: its goal counts and air in the sentences
        `scrimmage test` prints (run.py goal_lines, air_lines and clearance_lines; none for a run without them)."""
        try:
            out = store.load_result(self.runs_dir, run_id)
        except FileNotFoundError:
            raise SessionError(404, i18n.t("no run {run_id} is kept for this test", run_id=run_id)) from None
        kept = SimpleNamespace(goals=out.get("goals"), air=out.get("air"), clearance=out.get("clearance"))
        out["lines"] = goal_lines(kept) + air_lines(kept) + clearance_lines(kept)
        return out

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cancel.set()
            self._cond.notify_all()
            t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=30)
