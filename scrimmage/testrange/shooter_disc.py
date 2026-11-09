"""[shooter] kind = "disc": a stock disc_shooter part run by the disc launcher model (scrimmage/disc/launcher.py).
The part gives the channel (the layout, its wheels and their groups, the compression, the channel's length and an
arc's wrap), the wheels' diameter and tread width and each group's shaft (its wheels' inertia, a flywheel, the
motors and gearing); the range gives the disc and its aerodynamics. [shooter] gives the rest of the model's inputs,
inline or from a disc setup file (`scrimmage disc run`'s): [wheels] (a preset, durometer, friction), [layout]
(the fence or fence_friction, rail_friction, wheel_stiffness), [air] (density) and dt. A setup file's values for
what the part or the range gives must agree with them; its sections a world doesn't use (control, feed, launcher,
goal, robot, battery, results, sweep, chart) are ignored.
"""

from __future__ import annotations

import dataclasses
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .. import i18n
from ..disc import inputs as disc_inputs
from ..disc.launcher import DiscLauncher, ExitedDisc
from ..launcher.inputs import LauncherConfigError
from ..motors import get_motor
from ..parts.stock.mechanisms import disc_wheel_spots
from .shooter import (BeamBreak, CONTROL_PLACEHOLDER, Intake, Stage, _beam_breaks, _capacity, _err, _intake,
                      _mean_voltage, _mount_height, _same, _share_currents, _stages, _supply_and_slope)


DISC_SHOOTER_KEYS = ("kind", "part", "setup", "wheels", "layout", "air", "hopper", "stage", "intake", "beam_break")
DISC_INLINE_SECTIONS = ("wheels", "layout", "air")
DISC_IGNORED_SECTIONS = ("control", "feed", "launcher", "goal", "robot", "battery", "results", "sweep", "chart")
DISC_FILE_SECTIONS = ("disc", "wheels", "layout", "shaft", "air", "dt")
# The disc setup's keys the disc_shooter part gives, and its parameters for each.
DISC_PART_KEYS = {("wheels", "diameter"): "wheel_diameter", ("wheels", "width"): "wheel_width",
                  ("layout", "kind"): "layout", ("layout", "compression"): "compression",
                  ("layout", "channel_length"): "channel_length", ("layout", "arc_deg"): "arc_deg",
                  ("layout", "fence_radius"): "compression",
                  ("layout", "wheels"): "wheel_count, wheel_start, wheel_pitch, wheel_side and groups"}
DISC_LANE = 0.0                 # the channel's one lane (BallPath's lanes)
TILT_TOLERANCE_DEG = 1e-6


@dataclass(frozen=True)
class DiscShooterConfig:
    part: str                           # the stock disc_shooter instance
    setup: disc_inputs.DiscSetup        # the disc launcher model's inputs: the part's values, the range's disc
    tilt: str                           # "articulated" or "fixed"
    tilt_range: tuple[float, float]     # launch angles (deg) at the articulated tilt's stops: joint 0 is the low one
    tilt_angle: float                   # the fixed channel's launch angle (deg)
    capacity: int                       # discs the robot holds
    stages: tuple[Stage, ...] = ()
    intake: Intake | None = None
    beam_breaks: tuple[BeamBreak, ...] = ()
    lanes: tuple[float, ...] = (DISC_LANE,)     # one: the channel takes one disc at a time
    kind: str = "disc"


def _disc_shafts(p: dict, spots: list[dict]) -> list[dict]:
    """Each wheel group's [[shaft]]: its wheels' inertia together, the flywheel, the motors and gearing."""
    groups = max(s["group"] for s in spots) + 1
    return [{"wheel_inertia": p["wheel_moi"] * sum(1 for s in spots if s["group"] == g),
             "added_inertia": p["added_moi"], "motor": p["motor"], "motor_count": p["count"],
             "gearing": p["gearing"]} for g in range(groups)]


def _check_disc_file(raw: dict, where: str, name: str, given: dict, shafts: list[dict], rng) -> None:
    """A disc setup file's values for what the part or the range gives must agree with them."""
    if "disc" in raw:
        try:
            disc = disc_inputs._preset_table("disc", disc_inputs.Disc, dict(raw["disc"]), disc_inputs.DEFAULT_DISC)
        except (LauncherConfigError, TypeError) as e:
            raise _err(f"{where}: {e}") from None
        if disc != rng.disc:
            raise _err(i18n.t("{where}: its {disc_table} is not the range's disc {disc_preset}: the shooter shoots "
                              "the range's discs; remove {disc_table} from the setup file or make it the range's",
                              where=where, disc_preset=repr(rng.disc_preset), disc_table="[disc]"))
    air = raw.get("air", {})
    if isinstance(air, dict) and "aero" in air and air["aero"] != rng.aero.name:
        raise _err(i18n.t("{where}: {air_table} {aero_setting} but the range {name} flies its discs with {aero}; "
                          "leave aero out of the setup file", where=where, name=repr(rng.name),
                          aero=repr(rng.aero.name), air_table="[air]",
                          aero_setting="aero = {}".format(repr(air['aero']))))
    for (section, key), value in given.items():
        table = raw.get(section)
        if not isinstance(table, dict) or key not in table:
            continue
        mine = table[key]
        if key == "wheels":
            try:
                agree = len(mine) == len(value) and all(
                    math.isclose(float(a["x"]), b["x"], abs_tol=1e-9) and a["side"] == b["side"]
                    and a.get("group", 0) == b["group"] for a, b in zip(mine, value))
            except (TypeError, KeyError, ValueError):
                agree = False
        else:
            agree = _same(mine, value)
        if not agree:
            raise _err(i18n.t("{where}: [{section}] {key} = {mine} but the disc_shooter part {name} gives {value} "
                              "(from its {params}); make them agree or leave the key out of the setup file",
                              where=where, section=section, key=key, mine=mine, name=repr(name), value=value,
                              params=DISC_PART_KEYS[(section, key)]))
    if "shaft" in raw:
        tables = raw["shaft"] if isinstance(raw["shaft"], list) else [raw["shaft"]] * len(shafts)
        if len(tables) != len(shafts):
            raise _err(i18n.t("{where}: {shaft_tables} has {tables} table(s) but the disc_shooter part {name} has "
                              "{groups} wheel group(s); leave {shaft_table} out of the setup file", where=where,
                              tables=len(tables), name=repr(name), groups=len(shafts), shaft_tables="[[shaft]]",
                              shaft_table="[shaft]"))
        for g, (table, mine) in enumerate(zip(tables, shafts)):
            for key, value in (table.items() if isinstance(table, dict) else ()):
                if key in mine and not _same(value, mine[key]):
                    raise _err(i18n.t("{where}: {shaft_table} {key} = {value} but the disc_shooter part {name}'s "
                                      "wheel group {group} has {mine} (from its wheel_moi, added_moi, motor, count "
                                      "and gearing); make them agree or leave {shaft_table} out of the setup file",
                                      where=where, key=key, value=value, name=repr(name), group=g, mine=mine[key],
                                      shaft_table="[shaft]"))


def _parse_disc(sec: dict, built, cfg_dir: Path | None) -> DiscShooterConfig:
    """[shooter] kind = "disc" (see the notes above), checked against the robot's parts and the range."""
    rng = built.range
    for k in sec:
        if k == "lanes":
            raise _err(i18n.t("lanes: a disc shooter feeds one disc at a time down one channel; leave lanes out"))
        if k == "disc":
            raise _err(i18n.t("disc comes from the range ({disc_preset}); leave it out",
                              disc_preset=repr(rng.disc_preset)))
        if k in ("shaft", "launcher"):
            raise _err(i18n.t("{key} comes from the disc_shooter part (its parameters wheel_moi, added_moi, "
                              "motor, count, gearing and tilt, and its mount); leave it out", key=k))
        if k not in DISC_SHOOTER_KEYS:
            raise _err(i18n.t("unknown key {key} for a disc shooter; allowed: {keys}",
                              key=repr(k), keys=', '.join(DISC_SHOOTER_KEYS)))
    name = sec.get("part")
    by_name = {i.name: i for i in built.instances}
    if not isinstance(name, str) or name not in by_name:
        raise _err(i18n.t("part {name}: no such part; parts: {parts}", name=repr(name), parts=', '.join(by_name)))
    inst = by_name[name]
    if inst.source != "stock disc_shooter":
        raise _err(i18n.t("part {name} is a {source} part; a disc shooter ({kind_setting}) runs in a stock "
                          "disc_shooter part", name=repr(name), source=inst.source, kind_setting="kind = \"disc\""))
    p = inst.stock_params
    if not math.isclose(p["channel_width"], rng.disc.diameter, abs_tol=1e-9):
        raise _err(i18n.t("part {name}: channel_width {channel_width} isn't the diameter of the range's disc "
                          "{disc_preset} ({diameter}): the guide walls fit the disc; set {channel_width_setting}",
                          name=repr(name), channel_width=p['channel_width'], disc_preset=repr(rng.disc_preset),
                          diameter=rng.disc.diameter,
                          channel_width_setting="channel_width = {}".format(rng.disc.diameter)))
    articulated = p["tilt"] == "articulated"
    tilt0 = p["tilt_range"][0] if articulated else p["tilt_angle"]
    spots = disc_wheel_spots(p)
    shafts = _disc_shafts(p, spots)
    given = {(s, k): p[param] for (s, k), param in DISC_PART_KEYS.items()
             if param in p and k not in ("arc_deg", "fence_radius")}
    given[("layout", "wheels")] = spots
    if p["layout"] == "arc":
        given[("layout", "arc_deg")] = p["arc_deg"]
        given[("layout", "fence_radius")] = p["wheel_diameter"] / 2 + rng.disc.diameter - p["compression"]

    for section in DISC_INLINE_SECTIONS:
        table = sec.get(section, {})
        if not isinstance(table, dict):
            raise _err(i18n.t("{section} must be a table, not {table}", section=section, table=repr(table)))
        for k in table:
            if (section, k) in DISC_PART_KEYS:
                param = DISC_PART_KEYS[(section, k)]
                raise ValueError(i18n.t("{shooter_section_table} {key} comes from the disc_shooter part {name} "
                                        "({param}); leave it out", key=k, name=repr(name), param=param,
                                        shooter_section_table="[shooter.{}]".format(section)))
            if (section, k) == ("air", "aero"):
                raise ValueError(i18n.t("{shooter_air_table} aero comes from the range ({name}: {aero_setting}); "
                                        "leave it out", name=repr(rng.name), shooter_air_table="[shooter.air]",
                                        aero_setting="aero = {}".format(repr(rng.aero.name))))

    raw: dict = {}
    if "setup" in sec:
        ref = sec["setup"]
        if not isinstance(ref, str):
            raise _err(i18n.t("setup must be a path to a disc setup file, not {ref}", ref=repr(ref)))
        path = Path(cfg_dir if cfg_dir is not None else ".") / ref
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise _err(i18n.t("setup: can't read {path}: {error}", path=path, error=e.strerror)) from None
        try:
            whole = tomllib.loads(text)
        except tomllib.TOMLDecodeError as e:
            raise _err(f"setup {path}: {e}") from None
        _check_disc_file(whole, f"setup {path}", name, given, shafts, rng)
        raw = {k: v for k, v in whole.items() if k in DISC_FILE_SECTIONS and k not in ("disc", "shaft")}
        unknown = sorted(set(whole) - set(DISC_FILE_SECTIONS) - set(DISC_IGNORED_SECTIONS))
        if unknown:
            raise _err(i18n.t("setup {path}: unknown section(s) {unknown}", path=path, unknown=unknown))
    for section in DISC_INLINE_SECTIONS:
        if section in sec:
            base = raw.get(section, {})
            raw[section] = {**(base if isinstance(base, dict) else {}), **sec[section]}

    capacity = _capacity(sec, _disc_capacity())
    stages = _stages(sec.get("stage"), by_name, name)
    intake = _intake(sec.get("intake"), by_name)
    beams = _beam_breaks(sec.get("beam_break", []), stages, 1)

    layout = {k: v for k, v in raw.get("layout", {}).items() if k != "fence_radius"}
    layout.update({"kind": p["layout"], "wheels": spots, "compression": p["compression"],
                   "channel_length": p["channel_length"]})
    if p["layout"] == "arc":
        layout["arc_deg"] = p["arc_deg"]
    raw["layout"] = layout
    raw["wheels"] = {**raw.get("wheels", {}), "diameter": p["wheel_diameter"], "width": p["wheel_width"]}
    raw["disc"] = dataclasses.asdict(rng.disc)
    raw["shaft"] = shafts
    raw["control"] = [dict(CONTROL_PLACEHOLDER) for _ in shafts]
    # the setup's mount is a record (the world places the disc from the channel body): the channel origin's start
    # height (above 0, as the model requires) and the launch angle at the start
    raw["launcher"] = {"height": max(_mount_height(built, name, "channel"), 1e-3), "tilt_deg": tilt0}
    raw["air"] = {**raw.get("air", {}), "aero": rng.aero.name}
    try:
        setup = disc_inputs.parse_setup(raw)
    except LauncherConfigError as e:
        raise _err(i18n.t("(the disc launcher model's inputs): {error}", error=e)) from None
    return DiscShooterConfig(part=name, setup=setup, tilt=p["tilt"], tilt_range=tuple(p["tilt_range"]),
                             tilt_angle=p["tilt_angle"], capacity=capacity, stages=stages, intake=intake,
                             beam_breaks=beams)


def _disc_capacity() -> int:
    from .ballpath import DISC_CAPACITY
    return DISC_CAPACITY


class DiscShooter:
    """The disc launcher model running the disc_shooter part's wheel groups, each its own shaft on the world's
    battery, as Shooter runs the ball launcher's one: per group, external bindings on the group's spin joint, the
    model stepped at their mean voltage, the currents shared back and the spin joint mirroring the shaft.

    The model is stepped every physics step with the world's dt (it takes its own substeps inside, at its
    default_dt, while a disc is in the channel), so its clock is the world's and the ball path feeds it at its own
    time (DiscLauncher.insert raises otherwise)."""

    def __init__(self, world, config: DiscShooterConfig):
        self.world = world
        self.config = config
        m = world.model
        self._joints: list[tuple[int, int, float]] = []     # per group: qpos and dof address, the joint's qpos0
        self._groups: list[list] = []                       # per group: its motors' bindings
        for g in range(len(config.setup.shafts)):
            j = m.joint(f"{config.part}.spin{g}").id
            qadr, dadr = int(m.jnt_qposadr[j]), int(m.jnt_dofadr[j])
            self._joints.append((qadr, dadr, float(m.qpos0[qadr])))
            bindings = [mb for mb in world.motors if mb.dadr == dadr]
            for mb in bindings:
                mb.external = True
            self._groups.append(bindings)
        self.bindings = [mb for group in self._groups for mb in group]
        self._tilt_qadr = (int(m.jnt_qposadr[m.joint(f"{config.part}.tilt").id])
                           if config.tilt == "articulated" else None)
        self._emf = [s.gearing / get_motor(s.motor, s.motor_count).Kv for s in config.setup.shafts]
        self.reset()

    def reset(self) -> None:
        """A fresh disc launcher model: its clock at 0, every shaft at rest, the channel empty."""
        self.launcher = DiscLauncher(self.config.setup, external_bus=True)
        self.exited: list[ExitedDisc] = []
        self._fed = 0                   # discs fed: the next one's index (DiscLauncher numbers them in order)

    def tilt_deg(self) -> float:
        """The channel's launch angle now (deg above horizontal): tilt_range[0] + the tilt joint's angle (kept
        between the stops), or the fixed tilt_angle."""
        if self._tilt_qadr is None:
            return self.config.tilt_angle
        lo, hi = self.config.tilt_range
        return min(hi, max(lo, lo + math.degrees(float(self.world.data.qpos[self._tilt_qadr]))))

    def angle_deg(self) -> float:
        """The launch angle for the shot's log: the channel's tilt."""
        return float(self.tilt_deg())

    def rps(self) -> float:
        """Wheel group 0's speed (rotations per second)."""
        return self.launcher.shafts[0].speed / (2.0 * math.pi)

    def step(self, dt: float, vbatt: float) -> list[ExitedDisc]:
        """Advance the disc launcher model dt seconds on bus voltage vbatt; the discs that left it this step."""
        volts = []
        for shaft, bindings, emf in zip(self.launcher.shafts, self._groups, self._emf):
            shaft.set_bus_voltage(vbatt)
            volts.append(_mean_voltage(shaft.speed * emf, bindings))
        self.exited = self.launcher.step(dt, volts)
        for shaft, bindings, v in zip(self.launcher.shafts, self._groups, volts):
            _share_currents(shaft, bindings, v, vbatt)
        return self.exited

    def supply_at(self, bus: float) -> float:
        return self.supply_and_slope(bus)[0]

    def supply_and_slope(self, bus: float) -> tuple[float, float]:
        """Every group's supply current on bus voltage `bus` and its derivative (A/V), summed (Shooter's, per
        shaft)."""
        total = slope = 0.0
        for shaft, bindings in zip(self.launcher.shafts, self._groups):
            i, di = _supply_and_slope(shaft, bindings, bus)
            total += i
            slope += di
        return total, slope

    def supply_current(self) -> float:
        return sum(mb.supply_current for mb in self.bindings)

    def mirror(self) -> None:
        """Each spin joint takes its group's shaft angle and speed (after mj_step)."""
        d = self.world.data
        for (qadr, dadr, q0), shaft in zip(self._joints, self.launcher.shafts):
            d.qpos[qadr] = q0 + shaft.angle
            d.qvel[dadr] = shaft.speed

    # ---- the ball path's hand-off (ballpath.BallPath): one lane, the channel

    def positions(self) -> list[tuple[int, float, float]]:
        """The disc in the channel: (index, the lane, how far along the channel its center has come)."""
        return [(index, DISC_LANE, s) for index, s in self.launcher.positions()]

    def busy_lanes(self, d: float) -> set[float]:
        """The channel's lane while a disc is in it: it takes the next one only when clear."""
        return {DISC_LANE} if self.launcher.in_contact() else set()

    def feed(self, lane: float, entry_speed: float, t: float) -> tuple[int | None, ExitedDisc | None]:
        """Feed a disc into the channel at the launcher's time t, moving at `entry_speed`: (its index, None). One
        that nothing touches finishes no_contact and comes back from the next step."""
        if not self.launcher.insert(t, entry_speed):
            raise RuntimeError("disc shooter: fed a disc while the channel holds one (the ball path checks it's clear)")
        index = self._fed
        self._fed += 1
        return index, None
