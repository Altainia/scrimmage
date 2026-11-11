"""A range's AprilTag layout (spec D5): WPILib's `AprilTagFieldLayout` JSON, read, checked and drawn.

`[range] apriltags = "2024-crescendo"` names a layout scrimmage ships (BUILTIN_LAYOUTS, copied from WPILib at a pinned
tag: see apriltags/README.md), or a path to a layout JSON file. The layout is used as it is: scrimmage's field frame is
WPILib's (the origin at the blue alliance wall's right corner, x toward red, z up), and a tag's outward normal is its
pose's +x.

`parse_apriltags` reads and checks it (the file's field must match the range's within FIELD_MATCH; every tag's center
lies in the field or at most OUTSIDE_REACH outside it) and sets each tag's face point (R6): a wall-mounted tag's
center can sit a little inside the solid it hangs on (2024's SPEAKER tags are 3.8 cm inside the wall obstacle), so the
tag is drawn, and seen by a camera, at its center pushed out along its normal past any solid it lies at most
MOUNT_DEPTH inside. A tag deeper in a solid than that is warned about.

`add_apriltags` draws each tag as a black square with a white border: visual-only geoms that touch nothing.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import i18n
from ..teamfolders import TeamFolderError, expand

TAG_36H11_SIZE_IN = 6.5  # source: FRC 2024 and later 36h11 tags
FIELD_MATCH = 0.05  # source: spec D5
OUTSIDE_REACH = 0.3  # source: spec D5
MOUNT_DEPTH = 0.10  # choice: R6
FACE_CLEARANCE = 0.001  # m: a face point is this far out of the solid it was pushed out of
THICKNESS = 0.002  # m: a drawn tag's thickness (visual only); the white border sits 1 mm behind the black square
BORDER_RATIO = 10 / 8  # source: the tag36h11 family: the 8-cell tag has a 1-cell white margin on each side
BLACK = [0.02, 0.02, 0.02, 1.0]     # choice
WHITE = [0.95, 0.95, 0.95, 1.0]     # choice
IN = 0.0254

_DIR = Path(__file__).with_name("apriltags")
BUILTIN_LAYOUTS: dict[str, Path] = {name: _DIR / f"{name}.json"
                                    for name in ("2024-crescendo", "2026-rebuilt-welded", "2026-rebuilt-andymark")}


def _rotate(q: tuple[float, float, float, float], v: tuple[float, float, float]) -> np.ndarray:
    """The unit quaternion q = (w, x, y, z) turning vector v."""
    w, x, y, z = q
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                  [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                  [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
    return r @ np.asarray(v, float)


@dataclass(frozen=True)
class Tag:
    id: int
    pos: tuple[float, float, float]         # m, the tag's center in the field (WPILib's Pose3d translation)
    quat: tuple[float, float, float, float]  # (w, x, y, z), the Pose3d rotation
    face: tuple[float, float, float]        # m, where the tag is drawn and seen: pos pushed out of its mount (R6)

    @property
    def normal(self) -> np.ndarray:
        """The tag's outward normal: its +x, rotated."""
        return _rotate(self.quat, (1.0, 0.0, 0.0))


@dataclass(frozen=True)
class AprilTagLayout:
    name: str
    tags: tuple[Tag, ...]
    length: float   # m, the layout's field
    width: float
    size: float     # m, the tags' side (the black square)

    @property
    def ids(self) -> list[int]:
        return sorted(t.id for t in self.tags)

    def tag(self, id: int) -> Tag:
        for t in self.tags:
            if t.id == id:
                return t
        raise KeyError(id)


def _number(r, path: Path, who: str, table, key: str) -> float:
    v = table.get(key) if isinstance(table, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise r.err(i18n.t("apriltags file {path}: {who} has no number '{key}'", path=path, who=who, key=key))
    return float(v)


def _layout_path(r, ref: str, base_dir: Path) -> Path:
    if ref in BUILTIN_LAYOUTS:
        return BUILTIN_LAYOUTS[ref]
    try:
        path = Path(base_dir) / expand(ref)
    except TeamFolderError as e:
        raise r.err(i18n.t("apriltags '{ref}': {why}", ref=ref, why=e)) from None
    if not path.is_file():
        raise r.err(i18n.t("apriltags '{ref}' is not a layout scrimmage ships ({builtins}) and there is no file {path}",
                           ref=ref, builtins=", ".join(BUILTIN_LAYOUTS), path=path))
    return path


def _read_tags(r, path: Path) -> tuple[tuple[Tag, ...], float, float]:
    """The layout file's tags (face = pos until placed) and its field's length and width."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise r.err(i18n.t("apriltags file {path} can't be read as JSON: {why}", path=path, why=e)) from None
    if not isinstance(raw, dict) or not isinstance(raw.get("tags"), list):
        raise r.err(i18n.t("apriltags file {path} has no list '{key}'", path=path, key="tags"))
    field = raw.get("field")
    if not isinstance(field, dict):
        raise r.err(i18n.t("apriltags file {path} has no '{key}' with its length and width", path=path, key="field"))
    length = _number(r, path, i18n.t("the field"), field, "length")
    width = _number(r, path, i18n.t("the field"), field, "width")
    tags, seen = [], set()
    for i, t in enumerate(raw["tags"]):
        if not isinstance(t, dict) or isinstance(t.get("ID"), bool) or not isinstance(t.get("ID"), int):
            raise r.err(i18n.t("apriltags file {path}: tag number {index} has no whole number '{key}'", path=path,
                               index=i + 1, key="ID"))
        tid = t["ID"]
        who = i18n.t("tag {id}", id=tid)
        pose = t.get("pose")
        if not isinstance(pose, dict):
            raise r.err(i18n.t("apriltags file {path}: {who} has no '{key}'", path=path, who=who, key="pose"))
        rotation = pose.get("rotation")
        tr = pose.get("translation")
        q = rotation.get("quaternion") if isinstance(rotation, dict) else None
        if not isinstance(tr, dict) or not isinstance(q, dict):
            raise r.err(i18n.t("apriltags file {path}: {who} needs a pose with a translation and a rotation "
                               "quaternion", path=path, who=who))
        pos = tuple(_number(r, path, who, tr, k) for k in ("x", "y", "z"))
        quat = tuple(_number(r, path, who, q, k) for k in ("W", "X", "Y", "Z"))
        norm = math.sqrt(sum(c * c for c in quat))
        if norm < 1e-9:
            raise r.err(i18n.t("apriltags file {path}: {who} has a zero rotation quaternion", path=path, who=who))
        if tid in seen:
            raise r.err(i18n.t("apriltags file {path} gives tag {id} twice", path=path, id=tid))
        seen.add(tid)
        tags.append(Tag(tid, pos, tuple(c / norm for c in quat), pos))
    return tuple(tags), length, width


def _depth(local_p: np.ndarray, local_n: np.ndarray, half: np.ndarray) -> float:
    """How far along the normal a point inside a box (all in its frame) is from leaving it."""
    best = math.inf
    for p, n, h in zip(local_p, local_n, half):
        if abs(n) > 1e-9:
            best = min(best, (math.copysign(h, n) - p) / n)
    return best


def _face_point(tag: Tag, boxes) -> tuple[float, float, float]:
    """The tag's center pushed out along its normal past each box it lies at most MOUNT_DEPTH inside (a box it lies
    deeper in is left alone), plus FACE_CLEARANCE each."""
    n = tag.normal
    p = np.array(tag.pos, float)
    for _ in range(len(boxes) + 1):     # a push can land in the next box
        moved = False
        for _, c, rot, half in boxes:
            local = rot.T @ (p - c)
            if np.all(np.abs(local) <= half + 1e-9):
                d = _depth(local, rot.T @ n, half)
                if d <= MOUNT_DEPTH:
                    p = p + (d + FACE_CLEARANCE) * n
                    moved = True
        if not moved:
            break
    return float(p[0]), float(p[1]), float(p[2])


def _too_deep(tag: Tag, boxes) -> str | None:
    """The name of the first box the tag's center lies in deeper (along its normal) than MOUNT_DEPTH, else None."""
    n, p = tag.normal, np.array(tag.pos, float)
    for name, c, rot, half in boxes:
        local = rot.T @ (p - c)
        if np.all(np.abs(local) <= half + 1e-9) and _depth(local, rot.T @ n, half) > MOUNT_DEPTH:
            return name
    return None


def parse_apriltags(r, base_dir: Path, field: tuple[float, float], boxes, perimeter=()) -> tuple[
        AprilTagLayout | None, tuple[str, ...]]:
    """The range's AprilTag layout from its `[range]` reader `r` (keys `apriltags`, `apriltag_size_in`), checked
    against the range's `field` (length, width), and the warnings about it. `boxes` are the field's solids as
    (name, center, rotation, half sizes), the perimeter walls among them (their names in `perimeter`): a tag is pushed
    out of any of them it lies at most MOUNT_DEPTH inside, and is warned about when it lies deeper in any other.
    No layout (None, ()) when the range gives no `apriltags`."""
    from ..checks import worded

    size_in = r.num("apriltag_size_in", TAG_36H11_SIZE_IN, positive=True, nonnegative=True)    # nonnegative: finite
    if not r.has("apriltags"):
        if r.has("apriltag_size_in"):
            return None, (worded(lambda: i18n.t(
                "{where}: {size_key} is set but {layout_key} is not, so no tags are drawn; add {layout_key} or "
                "remove {size_key}", where=r.where, size_key="apriltag_size_in", layout_key="apriltags")),)
        return None, ()
    ref = r.text("apriltags")
    path = _layout_path(r, ref, base_dir)
    tags, length, width = _read_tags(r, path)
    if abs(length - field[0]) > FIELD_MATCH or abs(width - field[1]) > FIELD_MATCH:
        raise r.err(i18n.t("apriltags '{ref}' is for a {length} x {width} m field, but the range's field is "
                           "{field_length} x {field_width} m; use the layout for this field, or give the range the "
                           "layout's field size", ref=ref, length=f"{length:g}", width=f"{width:g}",
                           field_length=f"{field[0]:g}", field_width=f"{field[1]:g}"))
    for t in tags:
        x, y = t.pos[0], t.pos[1]
        if not (-OUTSIDE_REACH <= x <= field[0] + OUTSIDE_REACH and -OUTSIDE_REACH <= y <= field[1] + OUTSIDE_REACH):
            raise r.err(i18n.t("apriltags '{ref}': tag {id} at ({x}, {y}) m is outside the {length} x {width} m "
                               "field by more than {reach} m", ref=ref, id=t.id, x=f"{x:.4g}", y=f"{y:.4g}",
                               length=f"{field[0]:g}", width=f"{field[1]:g}", reach=f"{OUTSIDE_REACH:g}"))
    placed, warnings = [], []
    solid = [b for b in boxes if b[0] not in perimeter]
    for t in tags:
        placed.append(Tag(t.id, t.pos, t.quat, _face_point(t, boxes)))
        deep = _too_deep(t, solid)
        if deep is not None:
            warnings.append(worded(lambda t=t, deep=deep, ref=ref: i18n.t(
                "apriltags '{ref}': tag {id} is more than {depth} m inside '{solid}', so it is drawn inside it and "
                "no camera can see it. Move the tag, or the solid", ref=ref, id=t.id, depth=f"{MOUNT_DEPTH:g}",
                solid=deep)))
    name = ref if ref in BUILTIN_LAYOUTS else path.stem
    return AprilTagLayout(name, tuple(placed), length, width, size_in * IN), tuple(warnings)


def add_apriltags(spec, layout: AprilTagLayout) -> None:
    """Each tag as two thin boxes facing along its normal at its face point: `apriltag.<id>`, the black square, and
    `apriltag.<id>.border`, the white square behind it. They touch nothing (contype and conaffinity 0, group 1, as
    zone tape)."""
    import mujoco

    for t in layout.tags:
        n = t.normal
        face = np.array(t.face, float)
        for name, side, rgba, behind in ((f"apriltag.{t.id}", layout.size, BLACK, THICKNESS / 2.0),
                                         (f"apriltag.{t.id}.border", layout.size * BORDER_RATIO, WHITE,
                                          THICKNESS / 2.0 + 0.001)):
            spec.worldbody.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=list(face - behind * n),
                                    quat=list(t.quat), size=[THICKNESS / 2.0, side / 2.0, side / 2.0], rgba=rgba,
                                    contype=0, conaffinity=0, group=1)
