"""Headless pictures of the world: a follow camera, a top-down field view, model cameras, free cameras, and
contact sheets. Paths (ground truth, odometry) are drawn into the 3D scene, so they show in every view."""

from __future__ import annotations

import io
import math
import os

import mujoco
import numpy as np
from PIL import Image, ImageDraw

from .i18n import N_, t, translated

PATH_COLORS = {"truth": (0.15, 0.85, 0.25, 1.0), "odometry": (0.95, 0.35, 0.2, 1.0)}
PATH_WIDTH = 0.03        # capsule radius, m
PATH_Z = 0.02            # just above the carpet
MAX_SEGMENTS = 500       # per path
VIEWS_HELP = N_('view must be "follow", "top", "free" or "camera:<name>"')   # said with translated()


CAMERA_KEYS = ("lookat", "distance", "azimuth", "elevation")
MIN_SIZE, MAX_SIZE = 16, 4096
MAX_SHEET_PIXELS = 64_000_000  # frames x width x height of one contact sheet


class RenderUnavailable(RuntimeError):
    pass


def check_size(name: str, value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_SIZE <= value <= MAX_SIZE:
        raise ValueError(t("{name} must be a whole number from {least} to {most}, not {value}", name=name,
                           least=MIN_SIZE, most=MAX_SIZE, value=repr(value)))
    return value


def check_sheet_size(frames: int, width: int, height: int) -> None:
    px = frames * width * height
    if px > MAX_SHEET_PIXELS:
        raise ValueError(t("a contact sheet of {frames} frames at {width}x{height} is {megapixels} megapixels; the "
                           "limit is {limit} (use fewer or smaller frames)", frames=frames, width=width, height=height,
                           megapixels=f"{px / 1e6:.1f}", limit=f"{MAX_SHEET_PIXELS / 1e6:g}"))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def check_camera(camera: dict | None) -> dict:
    if camera is None:
        return {}
    if not isinstance(camera, dict):
        raise ValueError(t("camera must be an object with any of {keys}, not {value}", keys=", ".join(CAMERA_KEYS),
                           value=repr(camera)))
    bad = [k for k in camera if k not in CAMERA_KEYS]
    if bad:
        raise ValueError(t("unknown camera key(s) {keys}; allowed: {allowed}", keys=", ".join(map(repr, bad)),
                           allowed=", ".join(CAMERA_KEYS)))
    if "lookat" in camera:
        la = camera["lookat"]
        if not isinstance(la, (list, tuple)) or len(la) != 3 or not all(_num(v) for v in la):
            raise ValueError(t("camera lookat must be a list of 3 numbers [x, y, z], not {value}", value=repr(la)))
    for k in ("distance", "azimuth", "elevation"):
        if k in camera and not _num(camera[k]):
            raise ValueError(t("camera {key} must be a number, not {value}", key=k, value=repr(camera[k])))
    if "distance" in camera and camera["distance"] <= 0:
        raise ValueError(t("camera distance must be greater than 0, not {value}", value=repr(camera["distance"])))
    return camera


def new_renderer(model: mujoco.MjModel, width: int, height: int) -> mujoco.Renderer:
    """A MuJoCo offscreen renderer for `model` (its offscreen buffer grown to fit); RenderUnavailable if GL fails."""
    vis = model.vis.global_
    vis.offwidth, vis.offheight = max(vis.offwidth, width), max(vis.offheight, height)
    try:
        return mujoco.Renderer(model, height=height, width=width)
    except Exception as e:  # noqa: BLE001 - any GL failure means the same thing to the caller
        raise RenderUnavailable(
            f"headless rendering isn't available with MUJOCO_GL={os.environ.get('MUJOCO_GL', '(unset)')} ({e}). "
            "On Linux use MUJOCO_GL=egl, or MUJOCO_GL=osmesa with libosmesa6 installed") from None


class Renderer:
    """Draws pictures of a world. Keeps one MuJoCo renderer (GL context) per picture size: building one holds Python's
    GIL for its whole duration (seconds with slow software GL), so sizes are not rebuilt on every switch, and
    prepare() can build them before anything is waiting on the event loop."""

    KEEP = 4   # sizes kept at once

    def __init__(self, world):
        self.world = world
        self._rs: dict[tuple[int, int], mujoco.Renderer] = {}

    def close(self) -> None:
        for r in self._rs.values():
            r.close()
        self._rs.clear()

    def prepare(self, sizes) -> None:
        """Build the renderers for these (width, height) sizes now; a GL failure is left for render() to report."""
        for w, h in sizes:
            try:
                self._renderer(check_size("width", w), check_size("height", h))
            except (RenderUnavailable, ValueError):
                return

    def _renderer(self, width: int, height: int) -> mujoco.Renderer:
        r = self._rs.pop((width, height), None)
        if r is None:
            if len(self._rs) >= self.KEEP:
                self._rs.pop(next(iter(self._rs))).close()   # the least recently used
            r = new_renderer(self.world.model, width, height)
        self._rs[(width, height)] = r   # most recently used last
        return r

    def _field_bounds(self, d) -> tuple[float, float, float, float]:
        """(cx, cy, half_x, half_y) of the static, non-plane geometry: the field and its walls."""
        m = self.world.model
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for g in range(m.ngeom):
            if m.geom_bodyid[g] != 0 or int(m.geom_type[g]) == int(mujoco.mjtGeom.mjGEOM_PLANE):
                continue
            rot = d.geom_xmat[g].reshape(3, 3)
            c = d.geom_xpos[g] + rot @ m.geom_aabb[g][:3]
            h = np.abs(rot) @ m.geom_aabb[g][3:]
            lo, hi = np.minimum(lo, c - h), np.maximum(hi, c + h)
        if not np.isfinite(lo).all():
            c, e = m.stat.center, m.stat.extent
            return float(c[0]), float(c[1]), float(e), float(e)
        return float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2), float((hi[0] - lo[0]) / 2), float((hi[1] - lo[1]) / 2)

    def _camera(self, view: str, camera: dict | None, aspect: float, data=None):
        w, c = self.world, check_camera(camera)
        cam = mujoco.MjvCamera()
        if view == "follow":
            cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING
            cam.trackbodyid = w.robot_body
            cam.distance = float(c.get("distance", 3.0))
            cam.azimuth = float(c.get("azimuth", -135.0))
            cam.elevation = float(c.get("elevation", -30.0))
        elif view == "top":
            cx, cy, hx, hy = self._field_bounds(self.world.data if data is None else data)
            fovy = math.radians(w.model.vis.global_.fovy)
            half = max(hy, hx / aspect)  # fovy is vertical; the image is aspect times wider
            cam.type = mujoco.mjtCamera.mjCAMERA_FREE
            cam.lookat[:] = [cx, cy, 0.0]
            cam.azimuth, cam.elevation = 90.0, -90.0
            cam.distance = 1.08 * half / math.tan(fovy / 2)
        elif view == "free":
            if "lookat" not in c:
                raise ValueError("the free view needs camera = {lookat: [x, y, z]} (and optionally distance, "
                                 "azimuth, elevation)")
            cam.type = mujoco.mjtCamera.mjCAMERA_FREE
            cam.lookat[:] = [float(v) for v in c["lookat"]]
            cam.distance = float(c.get("distance", 3.0))
            cam.azimuth = float(c.get("azimuth", -135.0))
            cam.elevation = float(c.get("elevation", -30.0))
        elif view.startswith("camera:"):
            name = view.split(":", 1)[1]
            if mujoco.mj_name2id(w.model, mujoco.mjtObj.mjOBJ_CAMERA, name) < 0:
                names = [mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_CAMERA, i) for i in range(w.model.ncam)]
                have = ", ".join(n for n in names if n) or None
                if have:
                    raise ValueError(t("no camera {name}; the model's cameras: {cameras}", name=repr(name),
                                       cameras=have))
                raise ValueError(t("no camera {name}; the model has no cameras", name=repr(name)))
            return name
        else:
            raise ValueError(t("{help}, not {view}", help=translated(VIEWS_HELP), view=repr(view)))
        return cam

    @staticmethod
    def _draw_paths(scene, paths: dict[str, list[tuple[float, float]]]) -> None:
        for kind, pts in paths.items():
            if len(pts) < 2 or kind not in PATH_COLORS:
                continue
            stride = max(1, math.ceil((len(pts) - 1) / MAX_SEGMENTS))
            keep = pts[::stride]
            if keep[-1] != pts[-1]:
                keep.append(pts[-1])
            rgba = np.array(PATH_COLORS[kind], dtype=np.float32)
            for (x0, y0), (x1, y1) in zip(keep, keep[1:]):
                if math.hypot(x1 - x0, y1 - y0) < 1e-6:
                    continue
                if scene.ngeom >= scene.maxgeom:
                    return
                g = scene.geoms[scene.ngeom]
                mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3), np.eye(3).ravel(), rgba)
                mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_CAPSULE, PATH_WIDTH,
                                     np.array([x0, y0, PATH_Z]), np.array([x1, y1, PATH_Z]))
                scene.ngeom += 1

    def render(self, view: str = "follow", *, width: int = 960, height: int = 600,
               paths: dict[str, list[tuple[float, float]]] | None = None, camera: dict | None = None,
               data=None) -> np.ndarray:
        """`data`: an MjData snapshot to draw instead of the world's live one (so another thread can keep stepping)."""
        data = self.world.data if data is None else data
        width, height = check_size("width", width), check_size("height", height)
        cam = self._camera(view, camera, width / height, data)  # validate before creating a GL context
        r = self._renderer(width, height)
        r.update_scene(data, camera=cam)
        if paths:
            self._draw_paths(r.scene, paths)
        return r.render().copy()


def contact_sheet(frames: list[tuple[float, np.ndarray]], columns: int = 4) -> np.ndarray:
    """Tile frames (t, image) left to right, top to bottom, each labeled with its simulated time."""
    cols = min(columns, len(frames))
    rows = math.ceil(len(frames) / cols)
    h, w = frames[0][1].shape[:2]
    sheet = Image.new("RGB", (cols * w, rows * h))
    draw = ImageDraw.Draw(sheet)
    for i, (t, img) in enumerate(frames):
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(Image.fromarray(img), (x, y))
        draw.rectangle([x, y, x + 84, y + 20], fill=(0, 0, 0))
        draw.text((x + 6, y + 4), f"t={t:.2f} s", fill=(255, 255, 255))
    return np.asarray(sheet)


def to_png(img: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()
