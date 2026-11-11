"""Sensors in a World: each [[sensor]]'s beam or ray cast every physics step, after mj_step, and its reading kept.

World makes a SensorRuntime only for a robot with [[sensor]] tables (world.sensors is None otherwise, and stepping
costs nothing extra). Each sensor is a ray in its body's frame:

  - a beam break casts from `from` toward `to`, |to - from| long, plus END_SLOP at each end, and reads broken while a
    game piece is on it;
  - a distance or color sensor casts from `at` along `toward`, max_range long, and reads the first piece it hits
    (sees = "pieces") or the first thing that isn't the robot (sees = "anything").

Either way the field blocks it: a wall, a goal, an obstacle, a field element or the carpet in front of a piece hides
it, as it would from a real beam. A pieces-only sensor that hits the field first reads nothing (a beam clear, a
distance sensor its max_range).

An AprilTag's squares (geoms named `apriltag.<id>`, visual only) are stepped past like the robot's own, so tags
change no reading.

A camera (`kind = "camera"`) casts no ray of its own here: SensorRuntime hands the cameras to a CameraRuntime
(sensors/camera.py: which tags each one sees, at its own fps), made only when the robot has a camera, and its
readings come through `reading` and `state` as the others' do.

Pieces are the range's `ball.<i>` bodies: balls, discs and rigid pieces alike. A sensor never sees its own robot (the
robot body's whole tree).

When a reading is taken: after mj_step, from the poses mj_step computed at the start of the step (data.xpos, xmat and
geom_xpos are the kinematics of the state the step began from; mj_step integrates qpos afterwards). The sensor's body
and every geom it can hit come from that one pass, so the ray and what it hits agree exactly; the reading is of the
state 1 ms (one timestep) back. Recomputing the kinematics (mj_kinematics) would cost a pass per step and write into
data, and a 1 ms lag is far below what a beam break or a color sensor's own response time is (both are ideal here).

How the robot and the field are left out: MuJoCo's mj_ray excludes one body (`bodyexclude`) or whole geom groups,
not a body's tree, and the field, the robot and the pieces share geom group 0 (groups 1 and 2 mean structure and
ground to the terrain and climb monitors, and groups 3 to 5 aren't drawn by MuJoCo's renderer by default). So
`_cast` excludes the sensor's own body (the geom a beam is drawn through is most often its own) and, when a cast hits
the rest of the robot, casts again from just past that hit. A clear path, or a piece or the field first, is one
mj_ray a step; each robot surface in the way adds one (a box the ray passes through is two: in and out). MAX_CASTS
bounds a step's casts per sensor; a sensor that runs out of them is a warning in the world's warnings, once. Recasting
changes no model setting, so a robot's renders and every other monitor are as before, and it is exact for every
piece shape (spheres, cylinders, boxes and meshes).
"""

from __future__ import annotations

from dataclasses import dataclass

import mujoco
import numpy as np

from .. import i18n
from ..bindings import Update
from ..checks import Said
from ..protocol import HalState
from .colors import nearest_color
from .spec import COLOR_MAX_RANGE, I2C_PORTS, SensorError, SensorSpec, color_device

END_SLOP = 0.001    # m: a beam reaches this far past each end, so a piece resting exactly on an end reads broken on
                    # every step (a resting piece settles by far less than this) and never flickers
SKIP = 1e-6         # m: a recast starts this far past a hit it skipped
MAX_CASTS = 32      # a sensor's most casts in one step (robot surfaces in the way); past them it sees nothing
# A REV Color Sensor V3's values (spec C5, estimates): brightness 1 at 2 cm, 0.5 at 6 cm, nothing past 10 cm,
# straight lines between. With nothing in front it reads the dark carpet: DARK.
# estimate: FULL_SCALE, the count at full brightness, is our choice for every channel (the proximity's 11-bit 2047);
# the real sensor's raw color counts go far higher. The SimDevice gets these counts as they are (spec D8): REVLib's
# getColor() divides each by their sum, so it reads the piece's color whatever the scale.
FULL_SCALE = 2047
MAX_VOLTS = 5.0     # an AnalogInput reads 0 to 5 V
PORT_WORDS = {"onboard": "onboard", "mxp": "MXP"}


def _missing_color_words(name: str, port: str, after: float, other: str | None):
    """missing_color_sensors' warning for people, worded when it is shown (not on the bridge's thread)."""
    p = dict(sensor_table="[[sensor]]", name=name, device="REV Color Sensor V3", port=port, after=f"{after:g}",
             class_name="ColorSensorV3", i2c_key="i2c")
    if other is None:
        return lambda: i18n.t('{sensor_table} "{name}" is a {device} on the {port} I2C port, but the robot program '
                              "hasn't created one there {after} s after connecting; check the port the robot code "
                              "gives {class_name} and the sensor's {i2c_key}", **p)
    return lambda: i18n.t('{sensor_table} "{name}" is a {device} on the {port} I2C port, but the robot program '
                          "hasn't created one there {after} s after connecting (it has one on the {other} port); "
                          "check the port the robot code gives {class_name} and the sensor's {i2c_key}", other=other,
                          **p)
GRAY_REFLECTANCE = 0.3  # estimate: how much IR something that isn't a piece (the carpet, a wall) returns, of a piece's
BRIGHTNESS = ((0.0, 1.0), (0.02, 1.0), (0.06, 0.5), (COLOR_MAX_RANGE, 0.0))
DARK = {"Red": 12, "Green": 14, "Blue": 12, "IR": 8}    # estimate: a dark gray carpet, a few counts in each channel


@dataclass(frozen=True)
class Reading:
    on: bool                                        # broken (beam), closer than threshold (distance), sees a piece
    distance: float | None                          # m: a distance sensor's (max_range with nothing in range); a
                                                    # color sensor's to what it sees (None: nothing); None for a beam
    color: tuple[float, float, float] | None = None     # the piece's color (r, g, b, 0 to 1) a color sensor sees
    color_name: str | None = None                   # its nearest named color (colors.NAMED_COLORS)
    tags: tuple[int, ...] = ()                      # a camera's: the IDs of the tags it sees, in order
    tag_distances: tuple[float, ...] = ()           # m: how far each of those tags is, in the same order


def brightness(distance: float | None) -> float:
    """How bright a piece reads to a color sensor at `distance` (m): 1 up close, 0 past 10 cm (spec C5)."""
    if distance is None:
        return 0.0
    xs, ys = zip(*BRIGHTNESS)
    return float(np.interp(distance, xs, ys, right=0.0))


def color_values(reading: Reading) -> dict[str, int]:
    """A color sensor's Red, Green, Blue, IR and Proximity (0 to FULL_SCALE): a piece's color scaled by its
    brightness; IR the brightness of whatever it hits (a piece's in full, anything else's times GRAY_REFLECTANCE);
    proximity 2047 at contact falling to 0 at 10 cm along a straight line (an estimate of REV's curve), for whatever
    it hits. With no piece in front the colors are DARK's."""
    b = brightness(reading.distance)
    rgb = reading.color or (0.0, 0.0, 0.0)
    out = {ch: max(DARK[ch], round(FULL_SCALE * b * c)) for ch, c in zip(("Red", "Green", "Blue"), rgb)}
    out["IR"] = max(DARK["IR"], round(FULL_SCALE * b * (1.0 if reading.color is not None else GRAY_REFLECTANCE)))
    d = reading.distance
    out["Proximity"] = 0 if d is None else round(FULL_SCALE * max(0.0, 1.0 - d / COLOR_MAX_RANGE))
    return out


class SensorRuntime:
    def __init__(self, world, specs: list[SensorSpec]):
        m = world.model
        self.world, self.model, self.specs = world, m, list(specs)
        root = int(m.body_rootid[world.robot_body])
        robot = [b for b in range(m.nbody) if int(m.body_rootid[b]) == root]
        names = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) for b in robot]
        for s in self.specs:
            if mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, s.body) not in robot:
                raise SensorError(i18n.t("{sensor_table} \"{name}\": body \"{body}\" isn't one of the robot's "
                                         "bodies; its bodies: {bodies}", name=s.name, body=s.body,
                                         bodies=', '.join(n for n in names if n), sensor_table="[[sensor]]"))
        # The cameras: a CameraRuntime of their own, only when the robot has one (None otherwise).
        cams = [s for s in self.specs if s.kind == "camera"]
        self.cameras = None
        if cams:
            from .camera import CameraRuntime
            rng = world.parts.range if getattr(world, "parts", None) is not None else None
            self.cameras = CameraRuntime(world, cams, rng.apriltags if rng is not None else None)
        self._rays = [s for s in self.specs if s.kind != "camera"]     # the sensors this runtime casts for
        self._bodies = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, s.body) for s in self._rays]
        self._robot = (m.body_rootid[m.geom_bodyid] == root).tolist()      # per geom: part of the robot
        # per geom: an AprilTag's square, or a chain's bead or clearance probe (visual only: a ray steps past it as it
        # does past the robot's own geoms; a ray doesn't hit a chain's links, so it doesn't hit their beads either)
        self._tag = [(mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or "").startswith("apriltag.")
                     for g in range(m.ngeom)]
        layout = world.parts.layout if getattr(world, "parts", None) is not None else None
        for c in (layout.chains.values() if layout is not None else ()):
            for g in [c.probe, *c.beads]:
                self._tag[g] = True
        # Per geom: the piece (ball index) it belongs to, -1 for every other geom; and each piece's color.
        rng = world.test_range
        if rng is not None:
            from ..testrange.pieces import piece_color
            balls = rng.balls
            self._piece = balls.ball_of_geom(np.arange(m.ngeom)).tolist()
            self._colors = [piece_color(balls.kinds[k]) for k in balls.kind]
        else:
            self._piece, self._colors = [-1] * m.ngeom, []
        self._start = [np.asarray(s.start, dtype=float) - (END_SLOP * np.asarray(s.direction) if s.kind == "beam"
                                                           else 0.0) for s in self._rays]
        self._dir = [np.asarray(s.direction, dtype=float) for s in self._rays]
        self._reach = [s.length + (2 * END_SLOP if s.kind == "beam" else 0.0) for s in self._rays]
        self._gid = np.zeros(1, dtype=np.int32)
        self._pnt = np.zeros(3)
        self.casts = 0                                  # mj_ray calls so far (a cost check)
        self._out_of_casts: set[str] = set()            # the sensors already warned about running out of casts
        # Nothing seen until the first reading: World.reset reads them (data has no kinematics before mj_forward).
        self._readings = {s.name: self._read(s, None) for s in self._rays}

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.specs]

    def reading(self, name: str) -> Reading:
        if self.cameras is not None and name in self.cameras.names:
            return self.cameras.reading(name)
        if name not in self._readings:
            raise SensorError(i18n.t("the robot has no sensor \"{name}\"; its sensors: {names}",
                                     name=name, names=", ".join(self.names)))
        return self._readings[name]

    def reset(self) -> None:
        if self.cameras is not None:
            self.cameras.reset()
        self.update(self.world.data, force=True)

    def state(self) -> dict:
        """Each sensor's reading now, in SI, for MCP get_state: {name: {kind, on, distance_m (a distance sensor's, or
        a color sensor's to what it sees; None for a beam and while a color sensor sees nothing), color (the named
        color a color sensor sees, else None)}}; a camera's also has tags (the IDs it sees, in order)."""
        out = {}
        for s in self.specs:
            r = self.reading(s.name)
            out[s.name] = {"kind": s.kind, "on": r.on,
                           "distance_m": None if r.distance is None else round(float(r.distance), 4),
                           "color": r.color_name}
            if s.kind == "camera":
                out[s.name]["tags"] = list(r.tags)
        return out

    def update(self, data: mujoco.MjData, force: bool = False) -> None:
        """Read every sensor from `data`'s kinematics (after mj_step: the state the step began from). A camera reads
        at its own fps, or now with `force` (after a poke or a reset)."""
        if self.cameras is not None:
            self.cameras.update(data, force)
        for s, b, start, direction, reach in zip(self._rays, self._bodies, self._start, self._dir, self._reach):
            rot = data.xmat[b].reshape(3, 3)
            pnt = data.xpos[b] + rot @ start
            vec = rot @ direction
            hit = self._cast(data, pnt, vec, reach, s.sees == "pieces", b, s.name)
            self._readings[s.name] = self._read(s, hit)

    def _read(self, s: SensorSpec, hit) -> Reading:
        if s.kind == "beam":
            return Reading(on=hit is not None, distance=None)
        if s.kind == "distance":
            d = s.length if hit is None else min(hit[0], s.length)
            return Reading(on=d < s.threshold, distance=d)
        if hit is None:
            return Reading(on=False, distance=None)
        d, piece = hit
        if piece < 0:      # sees = "anything": something that isn't a piece is in front, and has no color to read
            return Reading(on=False, distance=d)
        color = self._colors[piece]
        return Reading(on=True, distance=d, color=color, color_name=nearest_color(color))

    def _cast(self, data, pnt, vec, reach: float, pieces_only: bool, own_body: int,
              name: str) -> tuple[float, int] | None:
        """The first thing the ray from `pnt` along unit `vec` hits within `reach` that the sensor sees: (distance,
        the piece's index or -1 for a non-piece), or None. See the module's note on recasting."""
        m, gid, robot, piece, tag = self.model, self._gid, self._robot, self._piece, self._tag
        start, gone = self._pnt, 0.0
        start[:] = pnt
        for _ in range(MAX_CASTS):
            self.casts += 1
            x = mujoco.mj_ray(m, data, start, vec, None, 1, own_body, gid)
            if x < 0.0 or gone + x > reach:
                return None
            gone += x
            g = int(gid[0])
            if piece[g] >= 0:
                return gone, piece[g]
            if not (robot[g] or tag[g]):    # the field: it blocks the view; only a sensor that sees anything reads it
                return None if pieces_only else (gone, -1)
            gone += SKIP
            start[:] = pnt + gone * vec
        self._warn_out_of_casts(name)
        return None

    def _warn_out_of_casts(self, name: str) -> None:
        if name in self._out_of_casts:
            return
        self._out_of_casts.add(name)
        # made on the simulation's thread: machine text (report.json, MCP state), worded for people when shown (Said)
        self.world.warnings.append(Said(
            f'[[sensor]] "{name}": the robot is in its way: its ray met {MAX_CASTS} of the robot\'s surfaces in one '
            "step, the most a sensor looks past, so it read nothing beyond them; move it or point it clear of the robot",
            lambda: i18n.t('{sensor_table} "{name}": the robot is in its way: its ray met {casts} of the robot\'s '
                           "surfaces in one step, the most a sensor looks past, so it read nothing beyond them; move "
                           "it or point it clear of the robot", sensor_table="[[sensor]]", name=name, casts=MAX_CASTS)))

    def sensor_updates(self) -> list[Update]:
        """The HAL inputs for the robot program: a DigitalInput per `dio` (on, inverted when active-low: a beam broken
        or a distance closer than its threshold), an AnalogInput per `analog` (volts_per_m times the distance, 0 to
        5 V) and a REV Color Sensor V3's SimDevice per `i2c` (color_values)."""
        ups: list[Update] = []
        for s in self._rays:        # a camera gives the robot program nothing (spec D7)
            got = self._readings[s.name]
            if s.analog is not None:
                ups.append(("AI", str(s.analog), {">voltage": min(MAX_VOLTS, max(0.0, s.volts_per_m * got.distance))}))
            if s.dio is not None:
                ups.append(("DIO", str(s.dio), {"<>value": (not got.on) if s.active_low else got.on}))
            if s.i2c is not None:
                ups.append((*color_device(s.i2c), {f">{k}": v for k, v in color_values(got).items()}))
        return ups

    def color_sensors(self) -> dict[tuple[str, str], SensorSpec]:
        """The color sensors the robot program reads, by their SimDevice's (type, device)."""
        return {color_device(s.i2c): s for s in self.specs if s.i2c is not None}

    def missing_color_sensors(self, hal: HalState, after: float) -> list[str]:
        """What to say, `after` s from connecting (time for the robot to create its devices), about each color sensor
        whose SimDevice the robot program hasn't created (put in the world's warnings too, once, so run reports show
        it)."""
        out = []
        for key, s in self.color_sensors().items():
            if key in hal.devices:
                continue
            others = [p for p in I2C_PORTS if p != s.i2c and color_device(p) in hal.devices]
            text = (f'[[sensor]] "{s.name}" is a REV Color Sensor V3 on the {PORT_WORDS[s.i2c]} I2C port, but the robot '
                    f"program hasn't created one there {after:g} s after connecting"
                    + (f" (it has one on the {PORT_WORDS[others[0]]} port)" if others else "")
                    + "; check the port the robot code gives ColorSensorV3 and the sensor's i2c")
            msg = Said(text, _missing_color_words(s.name, PORT_WORDS[s.i2c], after,
                                                  PORT_WORDS[others[0]] if others else None))
            out.append(msg)
            if msg not in self.world.warnings:
                self.world.warnings.append(msg)
        return out
