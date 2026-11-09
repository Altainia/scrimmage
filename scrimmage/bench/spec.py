"""The bench file: dataclasses, loading a bench TOML, includes, materials and dotted-key overrides. Its [piece] is
parsed in spec_piece.py, its shafts, shapes, [flight] and [run] in spec_shapes.py.

The bench frame: x forward (the usual exit direction), z up, y across. SI units after parsing; angles are degrees
(keys end in `_deg`). Every length key also has an inch form, `<key>_in` (give one of the two), as the field
elements do. List sections (`[[wheel]]`, `[[plate]]`, ...) and `[shaft.<name>]` tables are addressed by their
`name`, which is how `[sweep]` and `with_overrides` reach them: `wheel.top.diameter`.
"""

import copy
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .. import i18n
from ..launcher.inputs import PRESETS, Ball
from ..launcher.rigid_aero import DRAG_COEFFICIENT, SPIN_DAMPING
from ..testrange.pieces import PieceKind
from ..testrange.spec import BUILTIN_RANGES, Goal
from .problems import Problem

IN = 0.0254  # m per inch
MATERIAL_DEFAULT = "polycarbonate"
# estimate: 1/8 in polycarbonate sheet, the usual FRC hood material
THICKNESS_DEFAULT = 0.125 * IN
UNITS = {"m": 1.0, "mm": 0.001, "in": IN}
# estimate: a piece has no material, so piece-on-piece friction is a middling plastic-on-plastic figure (build.py's
# contacts use it; a ring's kind carries it too)
PIECE_FRICTION = 0.5


class BenchError(ValueError):
    """A bench file problem; the message names the bad key and, where there is a short list, the valid ones. `key` is
    the dotted key of the field at fault (`wheel.top.diameter`, else the entry `wheel.top`), None when unknown.
    `code` says what the problem is for code that mustn't read its words ("no_coacd", "no_convex_pieces",
    "no_entry")."""

    def __init__(self, message: str, key: str | None = None, code: str | None = None):
        super().__init__(message)
        self.key, self.code = key, code


_frozen = dict(frozen=True, kw_only=True)
Vec = tuple[float, float, float]


@dataclass(**_frozen)
class Material:
    name: str
    foam_friction: float
    rigid_friction: float
    source: str


@dataclass(**_frozen)
class Piece:
    name: str
    kind: str  # "squishy" | "rigid"
    shape: str  # "sphere" | "box" | "cylinder" | "ring" | "mesh" ("disc" is read as a cylinder); a piece preset's own
    size: tuple[float, ...]  # sphere (diameter,); box (x, y, z); cylinder (diameter, length), axis along local z;
    #                          ring (diameter, inner diameter, length); mesh (); a piece preset its bounding box
    mass: float
    inertia: Vec  # principal moments about the piece's own axes (kg m^2)
    ball: Ball | None  # the launcher model's numbers; squishy pieces only
    stiffness: float | None  # N/m, a rigid piece's own contact stiffness in series with the tread
    mesh: Path | None
    units: str | None  # a mesh's length unit
    # a piece preset's or a ring's kind, built by the range's piece builder (testrange/pieces.py): the bench builds
    # its solids and flies it with its drag, so the bench and the range have one NOTE. None for the other shapes.
    rigid_kind: PieceKind | None = field(default=None, repr=False, compare=False)
    # a rigid piece's air in [flight] (launcher/rigid_aero.py): its drag coefficient on its shadow and spin damping
    drag_coefficient: float = DRAG_COEFFICIENT
    spin_damping: float = SPIN_DAMPING


@dataclass(**_frozen)
class Shaft:
    name: str
    motor: str | None
    count: int
    gearing: float  # motor rotations per shaft rotation
    wheel_inertia: float
    added_inertia: float
    control: str  # "speed" | "voltage" | "free"
    target_rps: float | None
    voltage: float | None
    kP: float
    kS: float
    kV: float | None  # None: derived from the motor's 1/Kv scaled by gearing, as in the launcher model
    stator_limit: float | None
    supply_limit: float | None
    coupled_to: str | None  # this shaft turns at `ratio` times that shaft's speed (a belt or gears)
    ratio: float


@dataclass(**_frozen)
class Wheel:
    name: str
    shaft: str
    center: Vec
    axis: Vec  # unit; the shaft's direction
    diameter: float
    width: float
    tread: str  # a launcher [wheels.*] preset name, also a material


@dataclass(**_frozen)
class Plate:
    name: str
    center: Vec
    size: Vec
    rot_deg: Vec  # rotations about x, then y, then z
    material: str


@dataclass(**_frozen)
class Bar:
    name: str
    a: Vec
    b: Vec
    diameter: float
    material: str
    spring: float | None  # N/m along spring_axis; None: rigid
    travel: float  # slide range, +- m (0 when rigid)
    spring_axis: Vec


@dataclass(**_frozen)
class Curve:
    name: str
    center: Vec
    axis: Vec  # unit; the curve's axis of revolution
    zero_dir: Vec  # unit, perpendicular to axis: where angle 0 sits
    radius_start: float
    radius_end: float
    start_deg: float
    end_deg: float
    width: float  # along the axis
    thickness: float
    material: str


@dataclass(**_frozen)
class Cad:
    name: str
    part: Path | None  # a part folder from the FreeCAD exporter
    mesh: Path | None  # a mesh file (STL or OBJ)
    units: str | None  # a mesh's length unit
    center: Vec
    rot_deg: Vec
    material: str  # every shape of the part but its wheels, or the mesh
    slots: dict[str, str]  # a part's motor slot -> the bench shaft that drives it
    tread: str | None = None  # a part's wheels' tread preset (None for a mesh)


@dataclass(**_frozen)
class Inlet:
    name: str
    center: Vec
    size: Vec
    count: int
    rate: float  # pieces per second
    velocity: Vec
    spin: Vec  # rad/s
    spread_speed: float  # standard deviations
    spread_position: float
    spread_time: float
    seed: int


@dataclass(**_frozen)
class Outlet:
    name: str
    center: Vec
    size: Vec


@dataclass(**_frozen)
class FlightGoal:
    """`[flight] goal = { range, name }`: a goal from a range file, with the bench placed on that field."""
    range: Path  # the range file
    name: str
    goal: Goal
    at: Vec  # the bench origin's field position (x, y, height), m
    heading_deg: float  # which way the bench's +x faces on the field, from field +x
    # the solid things a shot must miss on its way in: the range's obstacles and elements and every goal's plates,
    # each (name, center, axes as columns, half sizes) on the field
    boxes: tuple[tuple[str, np.ndarray, np.ndarray, np.ndarray], ...] = field(default=(), repr=False)


@dataclass(**_frozen)
class Flight:
    outlet: str
    height: float  # the bench origin's height above the floor (with a goal, its `at` height)
    target_distance: float | None  # None only with a goal
    target_height: float | None
    ring_diameter: float | None
    air: str  # a launcher [air.*] preset name
    goal: FlightGoal | None = None


@dataclass(**_frozen)
class RunSettings:
    duration: float = 5.0
    timestep: float | None = None  # None: the builder picks one per contact law
    jam_time: float = 1.0
    jam_distance: float = 0.01


@dataclass(**_frozen)
class Bench:
    name: str
    author: str
    idea: str
    notes: str
    piece: Piece
    shafts: dict[str, Shaft]
    wheels: tuple[Wheel, ...]
    plates: tuple[Plate, ...]
    bars: tuple[Bar, ...]
    curves: tuple[Curve, ...]
    cads: tuple[Cad, ...]
    inlets: tuple[Inlet, ...]
    outlets: tuple[Outlet, ...]
    flight: Flight | None
    run: RunSettings
    materials: dict[str, Material]
    sweep: dict[str, list]
    charts: list[dict]
    raw: dict  # the file with includes merged, ready for with_overrides and parse_bench
    base_dir: Path
    warnings: tuple[str, ...] = ()  # things the room should know that don't stop the run (a borrowed preset)


def _load_materials() -> dict[str, Material]:
    raw = tomllib.loads(Path(__file__).with_name("presets.toml").read_text(encoding="utf-8"))
    out = {n: Material(name=n, **v) for n, v in raw["material"].items()}
    for n, v in PRESETS["wheels"].items():  # a tread: the launcher's friction against foam; no rigid figure exists
        out[n] = Material(name=n, foam_friction=v["friction"], rigid_friction=v["friction"],
                          source="launcher tread preset (estimate; the same figure is used against rigid pieces)")
    return out


MATERIALS = _load_materials()

# Per section: keys, and which of them are lengths (each also readable as `<key>_in`).
_SECTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "bench": (("name", "author", "idea", "notes"), ()),
    "piece": (("name", "preset", "kind", "shape", "size", "mass", "inertia", "stiffness_n", "stiffness_exponent",
               "restitution", "inertia_factor", "stiffness", "mesh", "units", "drag_coefficient", "spin_damping"),
              ("size",)),
    "shaft": (("motor", "count", "gearing", "wheel_inertia", "added_inertia", "control", "target_rps", "voltage",
               "kP", "kS", "kV", "stator_limit", "supply_limit", "coupled_to", "ratio"), ()),
    "wheel": (("name", "shaft", "center", "axis", "diameter", "width", "tread"), ("center", "diameter", "width")),
    "plate": (("name", "center", "size", "rot_deg", "material"), ("center", "size")),
    "bar": (("name", "a", "b", "center", "axis", "length", "diameter", "material", "spring", "travel",
             "spring_axis"), ("a", "b", "center", "length", "diameter", "travel")),
    "curve": (("name", "center", "axis", "zero_dir", "radius", "radius_start", "radius_end", "start_deg", "end_deg",
               "width", "thickness", "material"),
              ("center", "radius", "radius_start", "radius_end", "width", "thickness")),
    "cad": (("name", "part", "mesh", "units", "center", "rot_deg", "material", "slots", "tread"), ("center",)),
    "inlet": (("name", "center", "size", "count", "rate", "velocity", "spin", "spread_speed", "spread_position",
               "spread_time", "seed"), ("center", "size", "spread_position")),
    "outlet": (("name", "center", "size"), ("center", "size")),
    "flight": (("outlet", "height", "target_distance", "target_height", "ring_diameter", "air", "goal", "at",
                "heading_deg"), ("height", "target_distance", "target_height", "ring_diameter", "at")),
    "run": (("duration", "timestep", "jam_time", "jam_distance"), ("jam_distance",)),
}
_LISTS = ("wheel", "plate", "bar", "curve", "cad", "inlet", "outlet")  # [[section]], entries named
_SINGLES = ("piece", "flight", "run")  # one table each; an include may supply them
_TOP = ("include", "bench", "shaft", "sweep", "chart", *_SINGLES, *_LISTS)
_MESH_PATHS = {"cad": ("part", "mesh"), "piece": ("mesh",)}  # keys holding paths, read relative to their file
_REQ = object()


def _valid_keys(section: str) -> tuple[str, ...]:
    keys, lengths = _SECTIONS[section]
    return keys + tuple(k + "_in" for k in lengths)


def _num_ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


class _Table:
    """Typed reads from one table, with errors that name the table and (for unknown keys) list the valid ones."""

    def __init__(self, section: str, label: str, table, name: str | None = None):
        self.section, self.label, self.t = section, label, table
        self.prefix = section if name is None else f"{section}.{name}"  # the dotted key of this table
        if not isinstance(table, dict):
            raise BenchError(i18n.t("{table} must be a table, not {value}", table=label, value=repr(table)),
                             self.prefix)
        valid = _valid_keys(section)
        for key in table:
            if key not in valid:
                raise BenchError(i18n.t("unknown key '{key}' in {table}; valid keys: {keys}", key=key, table=label,
                                        keys=", ".join(valid)), f"{self.prefix}.{key}")

    def err(self, message: str, key: str | None = None) -> BenchError:
        """`message` is the whole sentence, naming the table (`self.label`); `key` is the field at fault (an `_in`
        form counts as its meters key); without it, the whole table."""
        if key is not None and key.endswith("_in"):
            key = key[:-3]
        return BenchError(message, self.prefix if key is None else f"{self.prefix}.{key}")

    def has(self, key: str) -> bool:
        return key in self.t or key + "_in" in self.t

    def _raw(self, key: str, default, length: bool):
        """The stored value, scaled to meters for an inch form; `default` when absent."""
        if length and key in self.t and key + "_in" in self.t:
            raise self.err(i18n.t("{table} has both {key} and {key}_in; give one", table=self.label, key=key), key)
        scale = 1.0
        if length and key + "_in" in self.t:
            key, scale = key + "_in", IN
        if key not in self.t:
            if default is _REQ:
                keys = ", ".join(_valid_keys(self.section))
                if length:
                    raise self.err(i18n.t("{table} is missing required key '{key}' (or {key}_in); valid keys: {keys}",
                                          table=self.label, key=key, keys=keys), key)
                raise self.err(i18n.t("{table} is missing required key '{key}'; valid keys: {keys}",
                                      table=self.label, key=key, keys=keys), key)
            return default, key, scale
        return self.t[key], key, scale

    def num(self, key, default=_REQ, *, positive=False, nonneg=False, length=False):
        v, k, scale = self._raw(key, default, length)
        if k not in self.t:
            return v
        if not _num_ok(v):
            raise self.err(i18n.t("{table} {key} must be a number, not {value}", table=self.label, key=k,
                                  value=repr(v)), k)
        v = float(v) * scale
        if positive and not v > 0:
            raise self.err(i18n.t("{table} {key} must be positive, not {value}", table=self.label, key=k,
                                  value=repr(self.t[k])), k)
        if nonneg and v < 0:
            raise self.err(i18n.t("{table} {key} must be zero or more, not {value}", table=self.label, key=k,
                                  value=repr(self.t[k])), k)
        return v

    def whole(self, key, default=_REQ, *, minimum=None):
        v, k, _ = self._raw(key, default, False)
        if k not in self.t:
            return v
        if isinstance(v, bool) or not isinstance(v, int):
            raise self.err(i18n.t("{table} {key} must be a whole number, not {value}", table=self.label, key=k,
                                  value=repr(v)), k)
        if minimum is not None and v < minimum:
            raise self.err(i18n.t("{table} {key} must be at least {minimum}, not {value}", table=self.label, key=k,
                                  minimum=minimum, value=repr(v)), k)
        return v

    def vec(self, key, n=3, default=_REQ, *, positive=False, length=False):
        v, k, scale = self._raw(key, default, length)
        if k not in self.t:
            return v
        if (not isinstance(v, list) or len(v) != n or not all(_num_ok(x) for x in v)):
            raise self.err(i18n.t("{table} {key} must be a list of {n} numbers, not {value}", table=self.label,
                                  key=k, n=n, value=repr(v)), k)
        out = tuple(float(x) * scale for x in v)
        if positive and not all(x > 0 for x in out):
            raise self.err(i18n.t("{table} {key} must be positive, not {value}", table=self.label, key=k,
                                  value=repr(v)), k)
        return out

    def text(self, key, default=_REQ, *, one_of=None):
        v, k, _ = self._raw(key, default, False)
        if k not in self.t:
            return v
        if not isinstance(v, str):
            raise self.err(i18n.t("{table} {key} must be text, not {value}", table=self.label, key=k,
                                  value=repr(v)), k)
        if one_of is not None and v not in one_of:
            raise self.err(i18n.t("{table} {key} {value} is not one of: {choices}", table=self.label, key=k,
                                  value=repr(v), choices=", ".join(one_of)), k)
        return v


def _unit_vec(t: _Table, key: str, v: Vec) -> Vec:
    n = math.sqrt(sum(x * x for x in v))
    if n == 0.0:
        raise t.err(i18n.t("{table} {key} must not be all zeros", table=t.label, key=key), key)
    return tuple(x / n for x in v)


def _name_of(section: str, entry, index: int) -> str:
    if not isinstance(entry, dict):
        raise BenchError(i18n.t("[[{section}]] entry {n} must be a table, not {value}", section=section, n=index + 1,
                                value=repr(entry)))
    name = entry.get("name")
    if not isinstance(name, str) or not name or not all(c.isalnum() or c in "_-" for c in name):
        raise BenchError(i18n.t("[[{section}]] entry {n} needs a name of letters, digits, '_' or '-' (got {name}); "
                                "valid keys: {keys}", section=section, n=index + 1, name=repr(name),
                                keys=", ".join(_valid_keys(section))))
    return name


def _material(t: _Table) -> str:
    m = t.text("material", MATERIAL_DEFAULT)
    if m not in MATERIALS:
        raise t.err(i18n.t("{table} material {material} is not known; known materials: {known}", table=t.label,
                           material=repr(m), known=", ".join(MATERIALS)), "material")
    return m


def _resolve_path(t: _Table, key: str, base_dir: Path) -> Path | None:
    v = t.text(key, None)
    return None if v is None else base_dir / v


def _units(t: _Table, required: bool) -> str | None:
    if "units" not in t.t:
        if required:
            raise t.err(i18n.t("{table} needs units (\"{m}\", \"{mm}\" or \"{inch}\") for its mesh", table=t.label,
                               m="m", mm="mm", inch="in"))
        return None
    if not required:
        raise t.err(i18n.t("{table} has units, but only a mesh has units", table=t.label))
    return t.text("units", one_of=tuple(UNITS))


# --- includes -------------------------------------------------------------------------------------------------------

def _read_toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise BenchError(i18n.t("{path}: {error}", path=path, error=e)) from None


def _absolute_paths(raw: dict, base_dir: Path) -> dict:
    """Paths in an included file are read relative to that file; pin them before merging."""
    out = copy.deepcopy(raw)
    for section, keys in _MESH_PATHS.items():
        entries = out.get(section, [])
        for e in ([entries] if isinstance(entries, dict) else entries):
            for k in keys:
                if isinstance(e, dict) and isinstance(e.get(k), str):
                    e[k] = str(base_dir / e[k])
    goal = out.get("flight", {}).get("goal") if isinstance(out.get("flight"), dict) else None
    if isinstance(goal, dict) and isinstance(goal.get("range"), str) and goal["range"] not in BUILTIN_RANGES:
        goal["range"] = str(base_dir / goal["range"])   # a goal's range file, like a mesh, is the including file's
    return out


def _shaft_tables(value, where: str = "") -> dict:
    """A file's `shaft` value, checked to be one table per shaft ([shaft.top], not [[shaft]] or shaft = 5)."""
    if isinstance(value, dict):
        bad = next((f"shaft.{k} = {v!r}" for k, v in value.items() if not isinstance(v, dict)), None)
    else:
        bad = "[[shaft]]" if isinstance(value, list) else f"shaft = {value!r}"
    if bad:
        raise BenchError(i18n.t("{where}{shaft_name_table} must be a table: write {shaft_top_table}, not {bad}",
                                where=where, bad=bad, shaft_name_table="[shaft.<name>]",
                                shaft_top_table="[shaft.top]"))
    return value


def _resolve_includes(raw: dict, base_dir: Path, stack: tuple[Path, ...] = ()) -> dict:
    """`raw` with its `include` files merged in first (the file's own entries win by name), `include` dropped."""
    includes = raw.get("include", [])
    if not isinstance(includes, list) or not all(isinstance(i, str) for i in includes):
        raise BenchError(i18n.t("include must be a list of file names, not {value}", value=repr(includes)))
    own = {k: v for k, v in raw.items() if k != "include"}
    if not includes:
        return own
    merged: dict = {}
    origin: dict[tuple, Path] = {}

    def claim(key: tuple, path: Path, label: str):
        if key in origin and origin[key] != path:
            raise BenchError(i18n.t("{table} is defined in both {first} and {second}; define it in the including "
                                    "file to choose", table=label, first=origin[key], second=path))
        origin[key] = path

    for inc in includes:
        path = (base_dir / inc).resolve()
        if path in stack:
            raise BenchError(i18n.t("{path} includes itself (an include cycle)", path=path))
        sub = _read_toml(path)
        sub = _absolute_paths(_resolve_includes(sub, path.parent, stack + (path,)), path.parent)
        for section in _LISTS:
            entries = sub.get(section, [])
            if not isinstance(entries, list):
                raise BenchError(i18n.t("{path}: [[{section}]] must be a list of tables", path=path, section=section))
            for i, e in enumerate(entries):
                name = _name_of(section, e, i)
                claim((section, name), path, f"[[{section}]] '{name}'")
                merged.setdefault(section, []).append(e)
        for name, e in _shaft_tables(sub.get("shaft", {}), f"{path}: ").items():
            claim(("shaft", name), path, f"[shaft.{name}]")
            merged.setdefault("shaft", {})[name] = e
        for section in _SINGLES:
            if section in sub:
                claim((section,), path, f"[{section}]")
                merged[section] = sub[section]
    for section in _LISTS:  # the file's own entries replace included ones of the same name, in place
        entries = merged.get(section, [])
        own_entries = own.get(section, [])
        if not isinstance(own_entries, list):
            raise BenchError(i18n.t("[[{section}]] must be a list of tables", section=section))
        names = {_name_of(section, e, i): i for i, e in enumerate(entries)}
        for i, e in enumerate(own_entries):
            n = _name_of(section, e, i)
            if n in names:
                entries[names[n]] = e
            else:
                entries.append(e)
        if entries:
            merged[section] = entries
    own_shafts = _shaft_tables(own.get("shaft", {}))
    if "shaft" in merged or "shaft" in own:
        merged.setdefault("shaft", {}).update(own_shafts)
    for key, v in own.items():
        if key not in _LISTS and key != "shaft":
            merged[key] = v  # the file's own tables (bench, sweep, chart, and any single section) win whole
    return merged


# --- parsing --------------------------------------------------------------------------------------------------------

def _named(section: str, entries, problems: list[Problem] | None = None) -> list[tuple[str, dict]]:
    """The section's (name, table) pairs. With `problems`, a bad entry is reported there and left out."""
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        message = i18n.t("[[{section}]] must be a list of tables (write each as [[{section}]])", section=section)
        if problems is None:
            raise BenchError(message, section)
        problems.append(Problem("error", section, message))
        return []
    out: dict[str, dict] = {}
    for i, e in enumerate(entries):
        try:
            n = _name_of(section, e, i)
            if n in out:
                raise BenchError(i18n.t("[[{section}]] has two entries named '{name}' (twice); names must be unique",
                                        section=section, name=n), f"{section}.{n}")
        except BenchError as err:
            if problems is None:
                raise
            problems.append(Problem("error", err.key, str(err)))
            continue
        out[n] = e
    return list(out.items())


def parse_bench(raw: dict, base_dir: Path, problems: list[Problem] | None = None) -> Bench | None:
    """Build a `Bench` from a raw dict (the parsed TOML); file names in it are relative to `base_dir`.

    The first problem raises `BenchError`. With a `problems` list, every table and entry is parsed on its own: each
    error is appended (as a `Problem` with its key) and that entry left out, the cross-checks run on what parsed,
    and the result is None if there was any error."""
    from .spec_piece import _parse_piece
    from .spec_shapes import (_parse_bar, _parse_cad, _parse_curve, _parse_flight, _parse_inlet, _parse_outlet,
                              _parse_plate, _parse_run, _parse_shaft, _parse_wheel)
    base_dir = Path(base_dir)
    collect = problems is not None
    errors: list[Problem] = problems if collect else []
    start = len(errors)

    def attempt(fn, *args, key: str | None = None):
        """fn(*args), or None after recording its error (collecting mode); the error's own key wins over `key`."""
        try:
            return fn(*args)
        except BenchError as e:
            if not collect:
                raise
            errors.append(Problem("error", e.key or key, str(e)))
            return None

    for key in raw:
        if key not in _TOP:
            attempt(_raise, BenchError(i18n.t("unknown section [{section}]; valid sections: {sections}", section=key,
                                              sections=", ".join(_TOP)), key))
    try:
        raw = _resolve_includes(raw, base_dir)
    except BenchError as e:
        if not collect:
            raise
        errors.append(Problem("error", e.key, str(e)))
        return None
    bt = fields = None
    if "bench" not in raw:
        attempt(_raise, BenchError(i18n.t("{bench_table} is missing: give at least {name_setting} (valid keys: name, "
                                          "author, idea, notes)", bench_table="[bench]",
                                          name_setting="name = \"...\""), "bench"))
    else:
        bt = attempt(_Table, "bench", "[bench]", raw["bench"])
    warned: list[str] = []
    piece = None
    if "piece" not in raw:
        attempt(_raise, BenchError(i18n.t("{piece_table} is missing: give {preset_setting} or a custom piece (valid "
                                          "keys: {keys})", keys=", ".join(_valid_keys("piece")),
                                          piece_table="[piece]", preset_setting="preset = \"<ball preset>\""), "piece"))
    else:
        piece = attempt(_parse_piece, raw["piece"], base_dir, warned, key="piece")
    shafts_raw = attempt(_shaft_tables, raw.get("shaft", {}), key="shaft") or {}
    shafts = {n: sh for n, v in shafts_raw.items() if (sh := attempt(_parse_shaft, n, v, key=f"shaft.{n}"))}
    parsers = {
        "wheel": _parse_wheel, "plate": _parse_plate, "bar": _parse_bar, "curve": _parse_curve,
        "inlet": _parse_inlet, "outlet": _parse_outlet,
    }
    named = {s: _named(s, raw.get(s, []), errors if collect else None) for s in (*parsers, "cad")}
    parsed = {s: tuple(x for n, e in named[s] if (x := attempt(p, n, e, key=f"{s}.{n}")) is not None)
              for s, p in parsers.items()}
    cads = tuple(x for n, e in named["cad"] if (x := attempt(_parse_cad, n, e, base_dir, key=f"cad.{n}")) is not None)
    for s in shafts.values():
        if s.coupled_to is not None:
            attempt(_check_coupling, s, shafts, list(shafts_raw), key=f"shaft.{s.name}.coupled_to")
    names = ", ".join(shafts_raw) or i18n.t("(none)")
    for w in parsed["wheel"]:
        if w.shaft not in shafts_raw:
            attempt(_raise, BenchError(i18n.t("{wheel_table} '{name}' names shaft {shaft}, which is not defined; "
                                              "shafts: {shafts}", name=w.name, shaft=repr(w.shaft), shafts=names,
                                              wheel_table="[[wheel]]"),
                                       f"wheel.{w.name}.shaft"))
    for c in cads:
        for slot, shaft in c.slots.items():
            if shaft not in shafts_raw:
                attempt(_raise, BenchError(i18n.t("{cad_table} '{name}' slot {slot} names shaft {shaft}, which is "
                                                  "not defined; shafts: {shafts}", name=c.name, slot=repr(slot),
                                                  shaft=repr(shaft), shafts=names,
                                                  cad_table="[[cad]]"), f"cad.{c.name}.slots"))
    flight = None
    if "flight" in raw:
        flight = attempt(_parse_flight, raw["flight"], [n for n, _ in named["outlet"]], base_dir, key="flight")
        if flight is not None and flight.goal is not None and piece is not None and piece.kind != "rigid":
            attempt(_raise, BenchError(i18n.t("{flight_table} goal scores rigid pieces only for now; a ball's flight "
                                              "scores through {ring_setting} at its target", flight_table="[flight]",
                                              ring_setting="ring_diameter"), "flight.goal"))
            flight = None
    sweep = raw.get("sweep", {})
    if not isinstance(sweep, dict):
        attempt(_raise, BenchError(i18n.t("{sweep_table} must be a table, not {value}", value=repr(sweep),
                                          sweep_table="[sweep]"), "sweep"))
        sweep = {}
    for dotted, values in sweep.items():
        attempt(_check_path, raw, dotted, key=f"sweep.{dotted}")
        if not isinstance(values, list) or not values:
            attempt(_raise, BenchError(i18n.t("{sweep_table} {key} must be a non-empty list of values, not {value}",
                                              key=dotted, value=repr(values),
                                              sweep_table="[sweep]"), f"sweep.{dotted}"))
    charts = raw.get("chart", [])
    if not isinstance(charts, list) or not all(isinstance(c, dict) for c in charts):
        attempt(_raise, BenchError(i18n.t("{chart_table} must be a list of tables", chart_table="[[chart]]"), "chart"))
        charts = []
    if bt is not None:
        fields = attempt(lambda: (bt.text("name"), bt.text("author", ""), bt.text("idea", ""), bt.text("notes", "")))
    run = attempt(_parse_run, raw.get("run", {}), key="run")
    if len(errors) > start:
        return None
    return Bench(
        name=fields[0], author=fields[1], idea=fields[2], notes=fields[3],
        piece=piece, shafts=shafts, wheels=parsed["wheel"], plates=parsed["plate"], bars=parsed["bar"],
        curves=parsed["curve"], cads=cads, inlets=parsed["inlet"], outlets=parsed["outlet"], flight=flight,
        run=run, materials=dict(MATERIALS), sweep=dict(sweep), charts=list(charts),
        raw=copy.deepcopy(raw), base_dir=base_dir, warnings=tuple(warned))


def _raise(err: BenchError):
    raise err


def _check_coupling(shaft: Shaft, shafts: dict[str, Shaft], defined=None) -> None:
    """`defined`: every shaft name in the file, parsed or not (a shaft that failed to parse is not reported as
    missing, and its chain is not followed)."""
    defined = shafts if defined is None else defined
    chain, cur = [shaft.name], shaft
    while cur.coupled_to is not None:
        if cur.coupled_to not in defined:
            raise BenchError(i18n.t("{shaft_name_table} is coupled to {shaft}, which is not defined; shafts: {shafts}",
                                    shaft=repr(cur.coupled_to), shafts=", ".join(defined),
                                    shaft_name_table="[shaft.{}]".format(cur.name)))
        if cur.coupled_to in chain:
            if cur.coupled_to == cur.name:
                raise BenchError(i18n.t("{shaft_name_table} is coupled to itself",
                                        shaft_name_table="[shaft.{}]".format(cur.name)))
            raise BenchError(i18n.t("shafts are coupled in a loop: {chain}",
                                    chain=" -> ".join(chain + [cur.coupled_to])))
        if cur.coupled_to not in shafts:
            return
        cur = shafts[cur.coupled_to]
        chain.append(cur.name)


def load_bench(path: Path) -> Bench:
    """Load a bench file. Errors name the file."""
    path = Path(path)
    raw = _read_toml(path)
    base = path.resolve().parent
    try:
        return parse_bench(_resolve_includes(raw, base, (path.resolve(),)), base)
    except BenchError as e:
        msg = str(e)
        raise BenchError(msg if msg.startswith(str(path)) else i18n.t("{path}: {error}", path=path, error=msg),
                         e.key) from None


# --- overrides ------------------------------------------------------------------------------------------------------

def _split(dotted: str) -> tuple[str, str | None, str]:
    """(section, entry name or None, key) of a dotted key; errors for a key that cannot be one."""
    parts = dotted.split(".")
    section = parts[0]
    if section in _LISTS or section == "shaft":
        if len(parts) != 3:
            raise BenchError(i18n.t("'{dotted}' should read {section}.<name>.<key>, e.g. {section}.top.{key}",
                                    dotted=dotted, section=section, key=_SECTIONS[section][0][0]))
        return section, parts[1], parts[2]
    if section in _SINGLES or section == "bench":
        if len(parts) != 2:
            raise BenchError(i18n.t("'{dotted}' should read {section}.<key>", dotted=dotted, section=section))
        return section, None, parts[1]
    raise BenchError(i18n.t("unknown section '{section}' in '{dotted}'; valid sections: {sections}", section=section,
                            dotted=dotted, sections=", ".join(("bench", *_SINGLES, "shaft", *_LISTS))))


def _check_path(raw: dict, dotted: str) -> tuple[str, str | None, str]:
    section, name, key = _split(dotted)
    if key not in _valid_keys(section):
        raise BenchError(i18n.t("unknown key '{key}' in '{dotted}'; valid keys for {section}: {keys}", key=key,
                                dotted=dotted, section=section, keys=", ".join(_valid_keys(section))))
    if name is not None:
        known = list(raw.get("shaft", {})) if section == "shaft" else [e.get("name") for e in raw.get(section, [])]
        if name not in known:
            raise BenchError(i18n.t("no {section} named '{name}' in '{dotted}'; {section}s: {known}", section=section,
                                    name=name, dotted=dotted, known=", ".join(known) or i18n.t("(none)")),
                             code="no_entry")
    return section, name, key


def with_overrides(raw: dict, overrides: dict[str, object]) -> dict:
    """A deep copy of `raw` with dotted keys set: `wheel.top.diameter`, `curve.hood.radius_end`, `run.duration`.

    A list-section entry (and a shaft) is addressed by its name. Setting `diameter` replaces a stored `diameter_in`
    and the other way round."""
    out = copy.deepcopy(raw)
    for dotted, value in overrides.items():
        section, name, key = _check_path(out, dotted)
        if name is None:
            table = out.setdefault(section, {})
        elif section == "shaft":
            table = out["shaft"][name]
        else:
            table = next(e for e in out[section] if e.get("name") == name)
        table.pop(key[:-3] if key.endswith("_in") else key + "_in", None)
        table[key] = copy.deepcopy(value)
    return out
