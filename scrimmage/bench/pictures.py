"""Pictures of a bench run for a room on a projector: the mechanism from the side (and from above) with every piece's
path drawn through it, colored by outcome, and frames of one pass, as a contact sheet and a GIF.

The shapes are an orthographic MuJoCo render (no perspective, so sizes read true and a scale bar holds everywhere) on
a white background; paths, outlets, inlets, labels, a title, a legend and a scale bar are drawn over it with Pillow.
The pool's parked pieces are hidden (geom group 3, which MuJoCo doesn't draw by default).
"""

import copy
import math
from pathlib import Path

import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..render import new_renderer
from .report import STATUS_COLORS, STATUS_WORDS
from .run import BenchResult

# view -> (azimuth, elevation, the world axes across and up the picture)
VIEWS = {"side": (90.0, 0.0, (0, 2)), "top": (90.0, -90.0, (0, 1))}
HIDDEN_GROUP = 3        # not drawn by MuJoCo's default scene options
BACKGROUND = (255, 255, 255)
INK = (25, 25, 35)
OUTLET_INK = (0, 110, 45)
INLET_INK = (40, 90, 200)
SHAPE_RGBA = {"wheel": (0.18, 0.38, 0.82, 1.0), "bar": (0.62, 0.45, 0.22, 1.0), "other": (0.42, 0.46, 0.52, 1.0)}
PIECE_RGBA = (0.6, 0.2, 0.75, 1.0)
PATH_ORDER = ("timed_out", "out", "escaped", "jammed")  # later ones drawn on top: the rare ones matter
MOVES_ACROSS = 0.01     # m: a top view is drawn when the paths span more than this across y
PICTURE_TRACE_HZ = 240.0  # what `bench run` samples paths and the trace at, for smooth paths and a smooth GIF
UNCALIBRATED_INK = (170, 40, 40)  # dark red, as the launcher model's charts' footer; not the jammed paths' red
PICTURE_UNCALIBRATED = "UNCALIBRATED: estimates from preset numbers, not measurements"
OUTLINE_APART = 0.6     # a piece's outlines are at least this many piece sizes apart (the later pose wins)


def _font(px: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=px)
    except (TypeError, OSError):  # an old Pillow, or one without FreeType: the small bitmap font
        return ImageFont.load_default()


class _Frame:
    """An orthographic camera framing the shapes, inlets and paths into the picture's middle band, and the mapping
    from world points to pixels."""

    def __init__(self, result: BenchResult, view: str, width: int, height: int, top: int, bottom: int):
        if view not in VIEWS:
            raise ValueError(f'view must be "side" or "top", not {view!r}')
        az, el, (ia, ib) = VIEWS[view]
        self.ia, self.ib, self.w, self.h = ia, ib, width, height
        m, lay = result.built.model, result.built.layout
        d = mujoco.MjData(m)
        mujoco.mj_kinematics(m, d)
        pts = []
        for g in lay.shape_of_geom:
            R = d.geom_xmat[g].reshape(3, 3)
            c, h = d.geom_xpos[g] + R @ m.geom_aabb[g, :3], np.abs(R) @ m.geom_aabb[g, 3:]
            pts += [c - h, c + h]
        for lo, hi in lay.inlet_boxes.values():
            pts += [lo, hi]
        for p in result.pieces:
            pts += [np.array(q[1:]) for q in p.path]
        pts = np.array(pts) if pts else np.zeros((2, 3))
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        r = result.built.model.geom_rbound[lay.piece_geoms[0]] if lay.piece_geoms else 0.0
        lo, hi = lo - r, hi + r  # paths are centers: leave room for the piece
        span = np.maximum(hi - lo, 0.05)
        band = height - top - bottom
        margin = 0.06 * min(width, band)
        self.scale = min((width - 2 * margin) / span[ia], (band - 2 * margin) / span[ib])  # pixels per meter
        mid = (lo + hi) / 2
        # the camera's center is the picture's; the content's center goes to the band's
        self.center = mid.copy()
        self.center[ib] += ((top + band / 2) - height / 2) / self.scale
        self.cam = mujoco.MjvCamera()
        self.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.cam.lookat[:] = self.center
        self.cam.azimuth, self.cam.elevation = az, el
        self.cam.distance = float(np.linalg.norm(hi - lo)) + 1.0
        self.fovy = height / self.scale  # orthographic: the picture's height in meters

    def px(self, p) -> tuple[float, float]:
        return (self.w / 2 + (p[self.ia] - self.center[self.ia]) * self.scale,
                self.h / 2 - (p[self.ib] - self.center[self.ib]) * self.scale)


class _Painter:
    """A copy of the bench's model set up for pictures (orthographic, shapes colored, bright flat light) and one
    renderer per size."""

    def __init__(self, result: BenchResult):
        self.result = result
        m = self.model = copy.deepcopy(result.built.model)
        lay = result.built.layout
        m.vis.global_.orthographic = 1
        m.vis.headlight.ambient[:] = 0.5
        m.vis.headlight.diffuse[:] = 0.55
        m.vis.headlight.specular[:] = 0.05
        wheels = set(lay.wheel_geoms.values())
        bars = {m.geom(f"bar:{b.name}").id for b in result.bench.bars}
        for g in lay.shape_of_geom:
            m.geom_rgba[g] = SHAPE_RGBA["wheel" if g in wheels else "bar" if g in bars else "other"]
        for g in lay.piece_geoms:
            m.geom_rgba[g] = PIECE_RGBA
            m.geom_group[g] = HIDDEN_GROUP
        self.data = mujoco.MjData(m)
        self._rs: dict[tuple[int, int], mujoco.Renderer] = {}

    def close(self) -> None:
        for r in self._rs.values():
            r.close()
        self._rs.clear()

    def render(self, frame: _Frame, qpos: np.ndarray | None = None, show: tuple[int, ...] = (),
               pieces: list | None = None) -> np.ndarray:
        """The shapes (and pool pieces `show`) at qpos (default: the model's start), on white. `pieces`, if given, gets
        the mask of the pieces' pixels, so an overlay drawn later can be kept off them."""
        m, d, lay = self.model, self.data, self.result.built.layout
        d.qpos[:] = m.qpos0 if qpos is None else qpos
        mujoco.mj_forward(m, d)
        for i, g in enumerate(lay.piece_geoms):
            m.geom_group[g] = 0 if i in show else HIDDEN_GROUP
        m.vis.global_.fovy = frame.fovy
        key = (frame.w, frame.h)
        if key not in self._rs:
            self._rs[key] = new_renderer(m, frame.w, frame.h)
        r = self._rs[key]
        r.update_scene(d, camera=frame.cam)
        rgb = r.render().copy()
        r.enable_segmentation_rendering()
        try:
            r.update_scene(d, camera=frame.cam)
            seg = r.render()
        finally:
            r.disable_segmentation_rendering()
        rgb[seg[..., 0] < 0] = BACKGROUND
        if pieces is not None:
            pieces.append((seg[..., 1] == int(mujoco.mjtObj.mjOBJ_GEOM)) & np.isin(seg[..., 0], lay.piece_geoms))
        return rgb


def _dashed_rect(draw: ImageDraw.ImageDraw, box, fill, width: int, dash: int) -> None:
    (x0, y0), (x1, y1) = box
    for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        n = max(1, int(math.hypot(bx - ax, by - ay) // (2 * dash)))
        for k in range(n):
            f0, f1 = k / n, (k + 0.5) / n
            draw.line([(ax + (bx - ax) * f0, ay + (by - ay) * f0), (ax + (bx - ax) * f1, ay + (by - ay) * f1)],
                      fill=fill, width=width)


def _label(draw: ImageDraw.ImageDraw, xy, text: str, font, fill=INK, anchor="mm") -> None:
    box = draw.textbbox(xy, text, font=font, anchor=anchor)
    draw.rectangle([box[0] - 5, box[1] - 3, box[2] + 5, box[3] + 3], fill=(255, 255, 255))
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def _nice_length(meters: float) -> float:
    k = 10.0 ** math.floor(math.log10(meters))
    return max(f * k for f in (1, 2, 5) if f * k <= meters)


def _length_text(meters: float) -> str:
    return f"{meters * 100:g} cm" if meters < 1.0 else f"{meters:g} m"


def _box_px(frame: _Frame, lo, hi):
    (ax, ay), (bx, by) = frame.px(lo), frame.px(hi)
    return (min(ax, bx), min(ay, by)), (max(ax, bx), max(ay, by))


def _overlay_shapes(draw: ImageDraw.ImageDraw, result: BenchResult, frame: _Frame, u: float,
                    qpos: np.ndarray | None = None) -> None:
    """Thin shapes drawn again at a size a projector shows: bars (often seen end on) and curves (a few mm thick), at
    qpos (default: the model's start)."""
    m = result.built.model
    d = mujoco.MjData(m)
    if qpos is not None:
        d.qpos[:] = qpos
    mujoco.mj_kinematics(m, d)
    rgb = lambda k: tuple(int(255 * c) for c in SHAPE_RGBA[k][:3])  # noqa: E731
    for bar in result.bench.bars:
        g = m.geom(f"bar:{bar.name}").id
        c, axis, half = d.geom_xpos[g], d.geom_xmat[g].reshape(3, 3)[:, 2], m.geom_size[g, 1]
        a, b = frame.px(c - half * axis), frame.px(c + half * axis)
        w = max(bar.diameter * frame.scale, 14 * u)
        draw.line([a, b], fill=rgb("bar"), width=int(w))
        for x, y in (a, b):
            draw.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=rgb("bar"))
    for curve in result.bench.curves:
        pts = [frame.px(d.geom_xpos[m.geom(f"curve:{curve.name}:{i}").id]) for i in range(_chords(m, curve.name))]
        draw.line(pts, fill=rgb("other"), width=int(max(curve.thickness * frame.scale, 7 * u)), joint="curve")


def _chords(m: mujoco.MjModel, name: str) -> int:
    n = 0
    while mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, f"curve:{name}:{n}") >= 0:
        n += 1
    return n


def _convex_hull(pts: np.ndarray) -> list[tuple[float, float]]:
    """The 2D convex hull of points (Andrew's monotone chain), counter-clockwise."""
    pts = sorted(set(map(tuple, np.round(pts, 2))))
    if len(pts) < 3:
        return pts

    def half(points):
        out: list = []
        for p in points:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out[:-1]
    return half(pts) + half(pts[::-1])


def _piece_outline(result: BenchResult, frame: _Frame, qpos) -> tuple[str, object]:
    """The piece's true-size outline at a pose (x y z qw qx qy qz) on the picture: ("circle", (x, y, r)) for a ball,
    else ("polygon", points), the hull of its corners (box), rims (cylinder) or vertices (mesh)."""
    m, g = result.built.model, result.built.layout.piece_geoms[0]
    R = np.zeros(9)
    mujoco.mju_quat2Mat(R, np.asarray(qpos[3:7], dtype=float))
    R = R.reshape(3, 3)
    Rg = np.zeros(9)
    mujoco.mju_quat2Mat(Rg, m.geom_quat[g])
    Rw = R @ Rg.reshape(3, 3)
    c = np.asarray(qpos[:3], dtype=float) + R @ m.geom_pos[g]
    size, kind = m.geom_size[g], m.geom_type[g]
    if kind == mujoco.mjtGeom.mjGEOM_SPHERE:
        x, y = frame.px(c)
        return "circle", (x, y, size[0] * frame.scale)
    if kind == mujoco.mjtGeom.mjGEOM_BOX:
        local = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * size[:3]
    elif kind == mujoco.mjtGeom.mjGEOM_CYLINDER:
        a = np.linspace(0.0, 2 * math.pi, 32, endpoint=False)
        rim = np.c_[size[0] * np.cos(a), size[0] * np.sin(a)]
        local = np.r_[np.c_[rim, np.full(32, -size[1])], np.c_[rim, np.full(32, size[1])]]
    elif kind == mujoco.mjtGeom.mjGEOM_MESH:
        k = m.geom_dataid[g]
        local = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]]
    else:  # anything else: its bounding box
        local = (np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
                 * m.geom_aabb[g, 3:] + m.geom_aabb[g, :3])
    world = c + local @ Rw.T
    return "polygon", _convex_hull(np.array([frame.px(p) for p in world]))


def _draw_outlines(draw: ImageDraw.ImageDraw, result: BenchResult, frame: _Frame, u: float) -> None:
    """Each piece's true-size outline, in its path's color, where it began each pass against a part and where it
    ended (skipping one that would sit on a later one): a path is only the center, so this shows what touched."""
    if not result.built.layout.piece_geoms:
        return
    reach = float(result.built.model.geom_rbound[result.built.layout.piece_geoms[0]])
    width = max(2, int(3 * u))
    for status in PATH_ORDER + ("stuck_at_inlet",):
        for p in result.pieces:
            if p.status != status:
                continue
            drawn: list[np.ndarray] = []
            for _, q in reversed(p.poses):  # the later of two close poses wins: the nip, not the roll up to it
                c = np.asarray(q[:3])
                if any(np.linalg.norm(c - o) < OUTLINE_APART * 2 * reach for o in drawn):
                    continue
                drawn.append(c)
                kind, shape = _piece_outline(result, frame, q)
                if kind == "circle":
                    x, y, r = shape
                    draw.ellipse([x - r, y - r, x + r, y + r], outline=STATUS_COLORS[status], width=width)
                elif len(shape) >= 3:
                    draw.polygon(shape, outline=STATUS_COLORS[status], width=width)


def _uncalibrated(draw: ImageDraw.ImageDraw, xy, font, anchor: str = "lm") -> None:
    draw.text(xy, PICTURE_UNCALIBRATED, font=font, fill=UNCALIBRATED_INK, anchor=anchor)


def _shape_labels(result: BenchResult, frame: _Frame, u: float) -> dict[str, tuple[tuple[float, float], str]]:
    """Where to write each shape's name, beside it rather than over it: a curve's outside its middle chord; a shape
    big on the picture in its middle; anything else just below it."""
    m, lay = result.built.model, result.built.layout
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    geoms: dict[str, list[int]] = {}
    for g, s in lay.shape_of_geom.items():
        geoms.setdefault(s, []).append(g)
    curves = {c.name: c for c in result.bench.curves}
    out = {}
    for s, gs in geoms.items():
        if s in curves:
            mid = d.geom_xpos[gs[len(gs) // 2]]
            x, y = frame.px(mid)
            cx, cy = frame.px(np.array(curves[s].center))
            r = math.hypot(x - cx, y - cy)
            if r > 1:
                out[s] = ((x + (x - cx) / r * 34 * u, y + (y - cy) / r * 34 * u), "mm")
            else:
                out[s] = ((x, y + 26 * u), "mt")
            continue
        boxes = []
        for g in gs:
            R = d.geom_xmat[g].reshape(3, 3)
            c, h = d.geom_xpos[g] + R @ m.geom_aabb[g, :3], np.abs(R) @ m.geom_aabb[g, 3:]
            boxes.append(_box_px(frame, c - h, c + h))
        x0, y0 = min(b[0][0] for b in boxes), min(b[0][1] for b in boxes)
        x1, y1 = max(b[1][0] for b in boxes), max(b[1][1] for b in boxes)
        if min(x1 - x0, y1 - y0) > 90 * u:
            out[s] = (((x0 + x1) / 2, (y0 + y1) / 2), "mm")
        else:
            out[s] = (((x0 + x1) / 2, y1 + 12 * u), "mt")
    return out


def path_picture(result: BenchResult, view: str = "side", width: int = 1280, height: int = 720) -> np.ndarray:
    """The mechanism with every piece's path, colored by outcome, and the outlets outlined (RGB, uint8)."""
    u = height / 720.0
    top, bottom = int(100 * u), int(104 * u)
    frame = _Frame(result, view, width, height, top, bottom)
    painter = _Painter(result)
    try:
        img = Image.fromarray(painter.render(frame))
    finally:
        painter.close()
    draw = ImageDraw.Draw(img)
    small, mid, big = _font(int(24 * u)), _font(int(28 * u)), _font(int(38 * u))
    lay, b = result.built.layout, result.bench
    _overlay_shapes(draw, result, frame, u)

    for name, (lo, hi) in lay.outlet_boxes.items():
        (x0, y0), (x1, y1) = _box_px(frame, lo, hi)
        _dashed_rect(draw, ((x0, y0), (x1, y1)), OUTLET_INK, max(2, int(4 * u)), int(14 * u))
        cx = min(max((x0 + x1) / 2, 80 * u), width - 80 * u)
        cy = min(max(y0 + 22 * u, top + 20 * u), height - bottom - 20 * u)
        _label(draw, (cx, cy), f"outlet: {name}", small, OUTLET_INK)
    inlet_labels = []
    for inlet in b.inlets:
        (x0, y0), (x1, y1) = _box_px(frame, *lay.inlet_boxes[inlet.name])
        r = 9 * u
        draw.rectangle([x0 - r, y0 - r, x1 + r, y1 + r], outline=INLET_INK, width=max(2, int(4 * u)))
        # the label goes behind the inlet, where pieces come from, so the paths don't cross it
        v = np.array(inlet.velocity, dtype=float)
        dx, dy = v[frame.ia], -v[frame.ib]
        n = math.hypot(dx, dy)
        dx, dy = (dx / n, dy / n) if n > 1e-9 else (0.0, -1.0)
        cx, cy = (x0 + x1) / 2 - dx * 30 * u, (y0 + y1) / 2 - dy * 30 * u
        anchor = ("r" if dx > 0.4 else "l" if dx < -0.4 else "m") + ("b" if dy > 0.4 else "t" if dy < -0.4 else "m")
        inlet_labels.append((inlet.name, (cx, cy), anchor))

    width_px = max(3, int(7 * u))
    counts: dict[str, int] = {}
    for status in PATH_ORDER + ("stuck_at_inlet",):
        for p in result.pieces:
            if p.status != status:
                continue
            counts[status] = counts.get(status, 0) + 1
            pts = [frame.px(q[1:]) for q in p.path]
            color = STATUS_COLORS[status]
            if len(pts) > 1:
                draw.line(pts, fill=color, width=width_px, joint="curve")
            if pts:
                x, y = pts[-1]
                r = width_px * 1.1
                draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(255, 255, 255), width=2)
    _draw_outlines(draw, result, frame, u)

    placed: list[list] = []  # shapes in line with each other (two wheels seen from above) share one label
    for shape, ((x, y), anchor) in _shape_labels(result, frame, u).items():
        same = next((q for q in placed if math.hypot(q[1] - x, q[2] - y) < 40 * u), None)
        if same:
            same[0] += f" / {shape}"
        else:
            placed.append([shape, x, y, anchor])
    for text, x, y, anchor in placed:
        if top < y < height - bottom:
            _label(draw, (x, y), text, small, anchor=anchor)
    for name, (x, y), anchor in inlet_labels:
        _label(draw, (x, y), f"in: {name}", small, INLET_INK, anchor=anchor)

    # title band
    draw.rectangle([0, 0, width, top], fill=(240, 242, 246))
    draw.text((20 * u, 12 * u), f"{b.name}: {'side view' if view == 'side' else 'from above'}", font=big, fill=INK)
    if b.idea or b.author:
        idea = (f"{b.author}: " if b.author else "") + (f'"{b.idea}"' if b.idea else "")
        draw.text((20 * u, 58 * u), idea, font=mid, fill=(70, 70, 85))
    # legend band: a line sample per outcome, the outlet box, and a scale bar; UNCALIBRATED under them
    y = height - bottom + 32 * u
    draw.rectangle([0, height - bottom, width, height], fill=(240, 242, 246))
    _uncalibrated(draw, (20 * u, height - 22 * u), small)
    x = 20 * u
    for status in PATH_ORDER + ("stuck_at_inlet",):
        if status not in counts:
            continue
        draw.line([(x, y), (x + 44 * u, y)], fill=STATUS_COLORS[status], width=width_px)
        text = f"{STATUS_WORDS[status]}: {counts[status]}"
        draw.text((x + 54 * u, y), text, font=mid, fill=INK, anchor="lm")
        x += 54 * u + draw.textlength(text, font=mid) + 30 * u
    meters = _nice_length(0.2 * width / frame.scale)
    bar = meters * frame.scale
    x1 = width - 20 * u
    draw.line([(x1 - bar, y + 8 * u), (x1, y + 8 * u)], fill=INK, width=max(2, int(5 * u)))
    for xe in (x1 - bar, x1):
        draw.line([(xe, y), (xe, y + 16 * u)], fill=INK, width=max(2, int(3 * u)))
    draw.text((x1 - bar / 2, y - 4 * u), _length_text(meters), font=small, fill=INK, anchor="mb")
    return np.asarray(img)


def moves_across(result: BenchResult) -> bool:
    ys = [q[2] for p in result.pieces for q in p.path]
    return bool(ys) and max(ys) - min(ys) > MOVES_ACROSS


def _pass_piece(result: BenchResult):
    return (next((p for p in result.pieces if p.status == "out"), None)
            or next((p for p in result.pieces if p.t_in is not None), None))


def pass_frames(result: BenchResult, frames: int = 8, width: int = 640,
                height: int = 360) -> list[tuple[float, np.ndarray]]:
    """Side views of the first `out` piece's pass (any spawned piece's if none got out), at trace samples spread
    evenly across its time inside; fewer when the trace has fewer samples in that time. Empty if nothing spawned."""
    p = _pass_piece(result)
    if p is None or not result.trace:
        return []
    times = np.array([t for t, _ in result.trace])
    inside = np.flatnonzero((times >= p.t_in - 1e-9) & (times <= p.t_out + 1e-9))
    if not len(inside):
        inside = np.array([int(np.argmin(np.abs(times - p.t_in)))])
    pick = sorted(set(inside[np.linspace(0, len(inside) - 1, min(frames, len(inside))).round().astype(int)]))
    frame = _Frame(result, "side", width, height, 0, 0)
    painter = _Painter(result)
    lay = result.built.layout
    floor = lay.bounds[0][2]
    u = max(height / 360.0, 0.5)  # thin shapes as thick as in a 720-high path picture, on a frame half its size
    out = []
    try:
        for k in pick:
            t, qpos = result.trace[k]
            show = tuple(i for i, a in enumerate(lay.piece_qpos) if qpos[a + 2] > floor)
            mask: list = []
            rgb = painter.render(frame, qpos, show, mask)
            im = Image.fromarray(rgb)
            _overlay_shapes(ImageDraw.Draw(im), result, frame, u, qpos)
            img = np.array(im)
            img[mask[0]] = rgb[mask[0]]  # the pieces stay on top of the thickened shapes
            out.append((float(t), img))
    finally:
        painter.close()
    return out


def _time_label(draw: ImageDraw.ImageDraw, t: float, height: int, x: float = 0.0, y: float = 0.0) -> None:
    """The simulated time, big enough for a projector: a tenth of the frame's height."""
    _label(draw, (x + height / 30, y + height / 40), f"t = {t:.3f} s", _font(max(12, height // 10)), anchor="la")


def pass_sheet(frames: list[tuple[float, np.ndarray]], columns: int = 4) -> np.ndarray:
    """The frames in a grid, left to right, each with its time in large type, and UNCALIBRATED under them."""
    cols = min(columns, len(frames))
    rows = math.ceil(len(frames) / cols)
    h, w = frames[0][1].shape[:2]
    band = max(28, h // 7)
    sheet = Image.new("RGB", (cols * w, rows * h + band), (240, 242, 246))
    draw = ImageDraw.Draw(sheet)
    for i, (t, img) in enumerate(frames):
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(Image.fromarray(img), (x, y))
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=(200, 204, 212), width=2)
        _time_label(draw, t, h, x, y)
    _uncalibrated(draw, (band / 2, rows * h + band / 2), _font(max(12, int(band * 0.55))))
    return np.asarray(sheet)


def write_gif(frames: list[tuple[float, np.ndarray]], path: Path, fps: float = 15) -> Path:
    """An endless GIF of the frames, each stamped with its simulated time and UNCALIBRATED."""
    path = Path(path)
    images = []
    for t, img in frames:
        im = Image.fromarray(img)
        draw = ImageDraw.Draw(im)
        _time_label(draw, t, im.height)
        font = _font(max(10, im.height // 22))
        box = draw.textbbox((im.width / 2, im.height - 6), PICTURE_UNCALIBRATED, font=font, anchor="mb")
        draw.rectangle([box[0] - 6, box[1] - 4, box[2] + 6, box[3] + 4], fill=(255, 255, 255))
        _uncalibrated(draw, (im.width / 2, im.height - 6), font, anchor="mb")
        images.append(im.convert("P", palette=Image.Palette.ADAPTIVE))
    images[0].save(path, save_all=True, append_images=images[1:], duration=int(round(1000 / fps)), loop=0)
    return path


def write_pictures(result: BenchResult, out_dir: Path) -> list[Path]:
    """path_side.png, path_top.png (when anything moves across y), pass.png and pass.gif. RenderUnavailable when
    there is no GL."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for view in ("side", "top") if moves_across(result) else ("side",):
        p = out_dir / f"path_{view}.png"
        Image.fromarray(path_picture(result, view)).save(p)
        written.append(p)
    sheet = pass_frames(result, frames=8)
    if sheet:
        p = out_dir / "pass.png"
        Image.fromarray(pass_sheet(sheet, columns=4)).save(p)
        written.append(p)
        written.append(write_gif(pass_frames(result, frames=40), out_dir / "pass.gif"))
    return written
