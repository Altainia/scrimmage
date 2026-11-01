"""The import file (`<name>.import.toml`): what the student chose for one CAD source, kept so a re-import can
reconcile it with a changed CAD file. Geometric choices (anchors, axes, mount points) are in CAD coordinates."""

from __future__ import annotations

import copy
import os
import re
import tempfile
import tomllib
import uuid
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np

from .. import teamfolders
from ..parts.toml_writer import _toml_value
from . import CadError
from .bodies import default_bodies
from .frame import RobotFrame, default_frame
from .matching import fingerprint, match
from .names import sanitize, unique
from .source import Source

COLLISIONS = ("none", "wheel", "shaped", "simple")


@dataclass
class PieceSettings:
    body: str
    material: str = "aluminum"
    infill: float | None = None
    custom_density: float | None = None
    mass: float | None = None
    left_out: bool = False
    fp: dict = field(default_factory=dict)


@dataclass
class JointSettings:
    kind: str                               # "hinge" | "slide"
    anchor: list[float]                     # CAD meters
    axis: list[float]                       # CAD
    range: tuple[float, float] | None = None
    spring: tuple[float, float] | None = None       # (stiffness, rest)
    damping: float = 0.0
    frictionloss: float = 0.0

    def __post_init__(self):
        if self.kind not in ("hinge", "slide"):
            raise CadError(f"A joint is a hinge or a slide, not {self.kind!r}.")
        for name in ("range", "spring"):
            v = getattr(self, name)
            if v is not None:
                setattr(self, name, (float(v[0]), float(v[1])))


@dataclass
class MotorSettings:
    motor: str
    count: int = 1
    ratio: float = 1.0
    spool_diameter: float | None = None
    invert: bool = False
    efficiency: float = 1.0


@dataclass
class EncoderSettings:
    on: str = "joint"
    invert: bool = False

    def __post_init__(self):
        if self.on not in ("joint", "motor"):
            raise CadError(f"An encoder is on the joint or the motor, not {self.on!r}.")


@dataclass
class MountSettings:
    name: str
    point: list[float]
    normal: list[float]


@dataclass
class BodySettings:
    parent: str | None = None
    joint: JointSettings | None = None
    mass: float | None = None
    collision: str = "none"
    part: str | None = None                 # set on the root body of a part
    motor: MotorSettings | None = None
    encoder: EncoderSettings | None = None
    gyro: bool = False
    mounts: list[MountSettings] = field(default_factory=list)

    def __post_init__(self):
        if self.collision not in COLLISIONS:
            raise CadError(f"Collision is one of {', '.join(COLLISIONS)}, not {self.collision!r}.")


@dataclass
class PartSettings:
    folder: str
    total_mass: float | None = None


@dataclass
class ImportFile:
    name: str
    import_id: str
    source: str
    units: str
    frame: RobotFrame
    pieces: dict[str, PieceSettings] = field(default_factory=dict)
    bodies: dict[str, BodySettings] = field(default_factory=dict)
    couplings: list[dict] = field(default_factory=list)
    loops: list[dict] = field(default_factory=list)
    parts: dict[str, PartSettings] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Plain data in the file's layout (no None values; tuples as lists)."""
        top = _plain(self)
        head = {k: top.pop(k) for k in ("name", "import_id", "source", "units", "frame")}
        return {"import": head, **top}

    @classmethod
    def from_dict(cls, d: dict) -> "ImportFile":
        """Build from `to_dict`'s layout. A wrong or unknown key raises CadError naming its path."""
        _expect_dict(d, "")
        _no_unknown(d, {"import", "pieces", "bodies", "couplings", "loops", "parts"}, "")
        head = d.get("import")
        _expect_dict(head, "import")
        _no_unknown(head, {"name", "import_id", "source", "units", "frame"}, "import")
        flat = {k: head[k] for k in head if k != "frame"}
        for k in ("pieces", "bodies", "parts"):
            _expect_dict(d.get(k, {}), k)
        kw = dict(
            frame=_build(RobotFrame, head.get("frame", {}), "import.frame"),
            pieces={k: _build(PieceSettings, v, f"pieces.{k}") for k, v in d.get("pieces", {}).items()},
            bodies={k: _build_body(v, f"bodies.{k}") for k, v in d.get("bodies", {}).items()},
            parts={k: _build(PartSettings, v, f"parts.{k}") for k, v in d.get("parts", {}).items()},
            couplings=_list_of_dicts(d.get("couplings", []), "couplings"),
            loops=_list_of_dicts(d.get("loops", []), "loops"),
        )
        for k in ("name", "import_id", "source", "units"):
            if k not in flat:
                raise CadError(f"The import file is missing import.{k}.")
            if not isinstance(flat[k], str):
                raise CadError(f"import.{k} must be text.")
        return cls(**flat, **kw)


def _plain(x):
    if hasattr(x, "__dataclass_fields__"):
        return {f.name: _plain(getattr(x, f.name)) for f in fields(x) if getattr(x, f.name) is not None}
    if isinstance(x, dict):
        return {k: _plain(v) for k, v in x.items() if v is not None}
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    return x


def _expect_dict(v, path):
    if not isinstance(v, dict):
        raise CadError(f"{path or 'The import file'} must be a table.")


def _no_unknown(d, known, path):
    for k in d:
        if k not in known:
            raise CadError(f"{path + '.' if path else ''}{k} is not a setting the import file knows.")


def _list_of_dicts(v, path):
    if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
        raise CadError(f"{path} must be a list of tables.")
    return copy.deepcopy(v)


def _build(cls, d, path):
    _expect_dict(d, path)
    _no_unknown(d, {f.name for f in fields(cls)}, path)
    try:
        return cls(**d)
    except CadError as e:
        raise CadError(f"{path}: {e}") from None
    except (TypeError, ValueError, IndexError) as e:
        raise CadError(f"{path}: not valid ({e}).") from None


def _build_body(d, path):
    _expect_dict(d, path)
    _no_unknown(d, {f.name for f in fields(BodySettings)}, path)
    d = dict(d)
    for key, cls in (("joint", JointSettings), ("motor", MotorSettings), ("encoder", EncoderSettings)):
        if key in d:
            d[key] = _build(cls, d[key], f"{path}.{key}")
    if "mounts" in d:
        if not isinstance(d["mounts"], list):
            raise CadError(f"{path}.mounts must be a list of tables.")
        d["mounts"] = [_build(MountSettings, m, f"{path}.mounts") for m in d["mounts"]]
    try:
        return BodySettings(**d)
    except CadError as e:
        raise CadError(f"{path}.collision: {e}") from None


def _portable(source_path: Path, base: Path) -> str:
    text = str(source_path).replace("\\", "/")
    if text.startswith("{") or not Path(text).is_absolute():
        return text
    rel = teamfolders.relativize(Path(text))
    if rel.startswith("{"):
        return rel
    try:
        return Path(text).relative_to(base).as_posix()
    except ValueError:
        return text


def new_import(src: Source, name: str, source_path: Path, base: Path) -> ImportFile:
    """The defaults for a first import: the default bodies, all welded to the first (the root of part `name`)."""
    bodies = default_bodies(src)
    first = next(iter(bodies))
    fps = {p.key: fingerprint(p) for p in src.pieces}
    pieces = {k: PieceSettings(body=b, fp=fps[k]) for b, keys in bodies.items() for k in keys}
    order = {p.key: i for i, p in enumerate(src.pieces)}
    pieces = dict(sorted(pieces.items(), key=lambda kv: order[kv[0]]))
    settings = {b: BodySettings(parent=None if b == first else first, part=name if b == first else None)
                for b in bodies}
    folder = teamfolders.relativize(Path(base) / name)
    return ImportFile(name=name, import_id=uuid.uuid4().hex, source=_portable(Path(source_path), Path(base)),
                      units=src.units, frame=default_frame(src), pieces=pieces, bodies=settings,
                      parts={name: PartSettings(folder=folder if folder.startswith("{") else name)})


# ---------------------------------------------------------------- the file

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE.match(k) else _toml_value(k)


def _is_tables(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _emit(lines: list[str], d: dict, path: list[str], array: bool = False) -> None:
    head = ".".join(_key(p) for p in path)
    if path:
        lines += ["", f"[[{head}]]" if array else f"[{head}]"]
    for k, v in d.items():
        if not isinstance(v, dict) and not _is_tables(v):
            lines.append(f"{_key(k)} = {_toml_value(v)}")
    for k, v in d.items():
        if isinstance(v, dict):
            _emit(lines, v, path + [k])
        elif _is_tables(v):
            for item in v:
                _emit(lines, item, path + [k], array=True)


def dumps_import(imp: ImportFile) -> str:
    lines: list[str] = []
    _emit(lines, imp.to_dict(), [])
    return "\n".join(lines).lstrip("\n") + "\n"


def write_import(imp: ImportFile, path: Path) -> None:
    """Write the file in one step (a temporary file, then a rename)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_import(imp)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False,
                                         encoding="utf-8", newline="\n") as f:
            tmp = f.name
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def read_import(path: Path) -> ImportFile:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise CadError(f"{Path(path).name} isn't valid TOML: {e}") from None
    except OSError as e:
        raise CadError(f"Can't read {Path(path).name}: {e.strerror or e}") from None
    return ImportFile.from_dict(data)


# ---------------------------------------------------------------- reconciling

def _mentions(x, name: str) -> bool:
    if isinstance(x, str):
        return x == name
    if isinstance(x, dict):
        return any(_mentions(v, name) for v in x.values())
    if isinstance(x, (list, tuple)):
        return any(_mentions(v, name) for v in x)
    return False


def remove_empty_bodies(imp: ImportFile) -> tuple[ImportFile, list[str]]:
    """A copy of `imp` without the bodies that hold no pieces, and one message per removed body.

    A removed body takes its joint, motor, encoder, gyro, mounts, and any coupling or loop that names it. Its
    children re-parent to its parent. If it was the root (no parent), its first child becomes the new root and
    takes over the part it rooted (a root has no joint, so the heir's joint, motor and encoder are dropped). Used by reimport and by edits that move pieces out of a body."""
    imp = copy.deepcopy(imp)
    messages: list[str] = []
    used = {p.body for p in imp.pieces.values()}
    for name in [n for n in imp.bodies if n not in used]:
        b = imp.bodies.pop(name)
        kids = [n for n, c in imp.bodies.items() if c.parent == name]
        heir = b.parent if b.parent is not None else (kids[0] if kids else None)
        for n in kids:
            imp.bodies[n].parent = b.parent if b.parent is not None else (None if n == heir else heir)
        if b.part is not None:
            if heir is not None and imp.bodies[heir].part is None:
                imp.bodies[heir].part = b.part
            else:
                imp.parts.pop(b.part, None)
        gone = [w for w, has in (("joint", b.joint), ("motor", b.motor), ("encoder", b.encoder), ("gyro", b.gyro),
                                 ("mounts", b.mounts)) if has]
        msg = f"{name} is no longer in the CAD"
        if gone:
            what = gone[0] if len(gone) == 1 else ", ".join(gone[:-1]) + " and " + gone[-1]
            msg += f"; its {what} {'was' if len(gone) == 1 else 'were'} dropped"
        if b.parent is None and heir is not None and imp.bodies[heir].joint is not None:
            hb = imp.bodies[heir]
            hb.joint = hb.motor = hb.encoder = None
            msg += f"; {heir} is now the root, so its joint was dropped"
        messages.append(msg)
        for attr in ("couplings", "loops"):
            keep = [c for c in getattr(imp, attr) if not _mentions(c, name)]
            if len(keep) != len(getattr(imp, attr)):
                messages.append(f"A {attr[:-1]} that used {name} was dropped")
            setattr(imp, attr, keep)
    return imp, messages


def reimport(imp: ImportFile, src: Source) -> tuple[ImportFile, list[str]]:
    """Reconcile `imp` with a (possibly changed) `src`: pieces keep their settings through `match`, new pieces go to
    their default body, and bodies left empty are removed. Returns a new ImportFile and the messages to show."""
    out = copy.deepcopy(imp)
    messages: list[str] = []
    m = match([{"key": k, "fp": p.fp} for k, p in imp.pieces.items()], src.pieces)
    fps = {p.key: fingerprint(p) for p in src.pieces}
    out.pieces = {}
    for old, new in m.pairs.items():
        ps = copy.deepcopy(imp.pieces[old])
        ps.fp = fps[new]
        out.pieces[new] = ps
        if old in m.guessed:
            messages.append(f"{old}: matched by shape; check it")
    for key in m.missing:
        messages.append(f"{key} is no longer in the CAD")
    default = {k: b for b, keys in default_bodies(src).items() for k in keys}
    siblings: dict[str, list[str]] = {}
    for k, b in default.items():
        siblings.setdefault(b, []).append(k)
    for key in m.added:
        want = default[key]
        body = next((out.pieces[k].body for k in siblings[want] if k in out.pieces), None)
        if body is None and want in out.bodies:
            body = want
        if body is None:
            body = unique(sanitize(want), set(out.bodies))
            root = next((n for n, b in out.bodies.items() if b.parent is None), None)
            out.bodies[body] = BodySettings(parent=root)
        out.pieces[key] = PieceSettings(body=body, fp=fps[key])
    order = {p.key: i for i, p in enumerate(src.pieces)}
    out.pieces = dict(sorted(out.pieces.items(), key=lambda kv: order[kv[0]]))
    out, more = remove_empty_bodies(out)
    return out, messages + more
