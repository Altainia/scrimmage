"""[shooter] kind = "piece": a stock pinch_shooter part run by the pinch model (launcher/pinch.py), shooting the
range's rigid piece `piece = "<kind>"` (the 2024 NOTE). The part gives the wheels (their diameters, width, the gap
and where their axles stand), each wheel's shaft (bottom, top: its inertia, a flywheel, the motors and gearing) and
the channel's tilt; the range's kind gives the piece (its sides, mass, inertia and own `stiffness`). [shooter] gives
the tread ([shooter.wheels]: a [wheels.*] preset, or durometer and friction) and the air ([shooter.air], the
launcher's [air.*], for the range's flight). The piece takes the disc's one-lane path: the pinch holds one piece at
a time. There is no setup file: no stand-alone pinch run reads one.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import i18n, units
from ..launcher.inputs import LauncherConfigError, Shaft, _build_section
from ..launcher.pinch import PinchConfigError, PinchLauncher, PinchSetup, channel_turn, check_setup, piece_of
from .shooter import BeamBreak, Intake, Stage, _beam_breaks, _capacity, _err, _intake, _number, _stages
from .shooter_disc import DISC_LANE, DiscShooter


PIECE_SHOOTER_KEYS = ("kind", "part", "piece", "wheels", "air", "hopper", "stage", "intake", "beam_break")
PIECE_WHEEL_KEYS = ("preset", "durometer", "friction")


@dataclass(frozen=True)
class PieceShooterConfig:
    part: str                           # the stock pinch_shooter instance
    setup: PinchSetup                   # the pinch model's inputs: the part's values, the range's piece
    piece: str                          # the range kind it shoots
    turn: tuple[tuple[float, ...], ...]     # the piece's body frame to the channel's (pinch.channel_turn), rows
    air: object                         # the launcher's Air: the range's flight
    tilt: str                           # "articulated" or "fixed"
    tilt_range: tuple[float, float]     # exit angles (deg) at the articulated tilt's stops: joint 0 is the low one
    tilt_angle: float                   # the fixed channel's exit angle (deg)
    capacity: int                       # pieces the robot holds
    stages: tuple[Stage, ...] = ()
    intake: Intake | None = None
    beam_breaks: tuple[BeamBreak, ...] = ()
    lanes: tuple[float, ...] = (DISC_LANE,)     # one: the pinch takes one piece at a time
    kind: str = "piece"


def _piece_kind(sec: dict, rng):
    """[shooter] piece: the range's rigid kind the pinch shoots, refused in words when it isn't one."""
    kinds = rng.kinds
    names = ', '.join(kinds)
    name = sec.get("piece")
    if name is None:
        raise _err(i18n.t("{kind_setting} needs {piece_setting}, the range's rigid piece it shoots; this range's "
                          "pieces: {names}", kind_setting="kind = \"piece\"", piece_setting="piece = \"<kind name>\"",
                          names=names))
    if not isinstance(name, str) or name not in kinds:
        raise _err(i18n.t("piece {name}: no such piece on the range {range_name}; its pieces: {names}",
                          name=repr(name), range_name=repr(rng.name), names=names))
    kind = kinds[name]
    if kind.form == "ball":
        raise _err(i18n.t("piece {name} is a ball; a pinch shooter shoots a rigid piece. Shoot balls with a stock "
                          "launcher part: leave kind and piece out", name=repr(name)))
    if kind.form == "disc":
        raise _err(i18n.t("piece {name} is a disc; a pinch shooter shoots a rigid piece. Shoot discs with "
                          "{kind_setting} in a stock disc_shooter part", name=repr(name),
                          kind_setting="kind = \"disc\""))
    if name != rng.piece_kind:
        raise _err(i18n.t("piece {name}: the robot holds the range's {piece_kind} (its first ball, else its first "
                          "disc, else its default piece), so it can only shoot that; use a range whose only pieces "
                          "are {name}, or make it the default", name=repr(name), piece_kind=repr(rng.piece_kind)))
    return kind


def _pinch_wheels(sec: dict, name: str) -> tuple[float, float]:
    """[shooter.wheels]: the tread's durometer and friction, from a [wheels.*] preset and the keys given."""
    from ..launcher.inputs import PRESETS as LAUNCHER_PRESETS
    table = sec.get("wheels", {})
    if not isinstance(table, dict):
        raise _err(i18n.t("wheels must be a table, not {table}", table=repr(table)))
    for k in table:
        if k in ("diameter", "width", "positions", "roller"):
            raise _err(i18n.t("{shooter_wheels_table} {key} comes from the pinch_shooter part {name} (its "
                              "bottom_diameter, top_diameter and wheel_width); leave it out", key=k, name=repr(name),
                              shooter_wheels_table="[shooter.wheels]"))
        if k not in PIECE_WHEEL_KEYS:
            raise _err(i18n.t("wheels: unknown key {key}; allowed: {keys}", key=repr(k),
                              keys=', '.join(PIECE_WHEEL_KEYS)))
    values: dict = {}
    if "preset" in table:
        known = LAUNCHER_PRESETS.get("wheels", {})
        if table["preset"] not in known:
            raise _err(i18n.t("wheels: unknown preset {preset}; known wheels presets: {known}",
                              preset=repr(table["preset"]), known=', '.join(known)))
        values.update(known[table["preset"]])
    values.update({k: v for k, v in table.items() if k != "preset"})
    missing = [k for k in ("durometer", "friction") if k not in values]
    if missing:
        raise _err(i18n.t("{shooter_wheels_table} needs the tread: a preset (such as {preset_setting}) or durometer "
                          "and friction", shooter_wheels_table="[shooter.wheels]", preset_setting="preset = \"60A\""))
    durometer, friction = values["durometer"], values["friction"]
    if not (_number(durometer) and 0.0 < durometer < 100.0):
        raise _err(i18n.t("wheels: durometer must be above 0 and below 100, not {durometer}", durometer=durometer))
    if not (_number(friction) and friction > 0.0):
        raise _err(i18n.t("wheels: friction must be positive, not {friction}", friction=friction))
    return float(durometer), float(friction)


def _parse_piece(sec: dict, built) -> PieceShooterConfig:
    """[shooter] kind = "piece" (see the notes above), checked against the robot's parts and the range."""
    from .spec import piece_box
    rng = built.range
    for k in sec:
        if k == "lanes":
            raise _err(i18n.t("lanes: a pinch shooter feeds one piece at a time between its wheels; leave lanes out"))
        if k == "setup":
            raise _err(i18n.t("setup: a pinch shooter takes no setup file; the part, the range's piece and "
                              "{shooter_wheels_table} give its numbers", shooter_wheels_table="[shooter.wheels]"))
        if k not in PIECE_SHOOTER_KEYS:
            raise _err(i18n.t("unknown key {key} for a piece shooter; allowed: {keys}",
                              key=repr(k), keys=', '.join(PIECE_SHOOTER_KEYS)))
    name = sec.get("part")
    by_name = {i.name: i for i in built.instances}
    if not isinstance(name, str) or name not in by_name:
        raise _err(i18n.t("part {name}: no such part; parts: {parts}", name=repr(name), parts=', '.join(by_name)))
    inst = by_name[name]
    if inst.source != "stock pinch_shooter":
        raise _err(i18n.t("part {name} is a {source} part; a piece shooter ({kind_setting}) runs in a stock "
                          "pinch_shooter part", name=repr(name), source=inst.source, kind_setting="kind = \"piece\""))
    kind = _piece_kind(sec, rng)
    p = inst.stock_params
    box = piece_box(kind)
    piece = piece_of(box, kind.mass, kind.inertia, kind.stiffness)
    if p["gap"] >= piece.thickness:
        raise _err(i18n.t("part {name}: gap {gap} is no narrower than the piece {piece}'s thickness {thickness}, so "
                          "the wheels can't grip it; make the gap smaller than the thickness", name=repr(name),
                          gap=units.fmt(p["gap"], "length"), piece=repr(kind.name),
                          thickness=units.fmt(piece.thickness, "length")))
    durometer, friction = _pinch_wheels(sec, name)
    try:
        air = _build_section("air", sec.get("air", {}))
    except LauncherConfigError as e:
        raise _err(str(e)) from None
    shaft = Shaft(wheel_inertia=p["wheel_moi"], added_inertia=p["added_moi"], motor=p["motor"],
                  motor_count=p["count"], gearing=p["gearing"])
    setup = PinchSetup(piece=piece, bottom_diameter=p["bottom_diameter"], top_diameter=p["top_diameter"],
                       gap=p["gap"], wheel_x=p["wheel_x"], wheel_width=p["wheel_width"], durometer=durometer,
                       friction=friction, shafts=(shaft, shaft))
    try:
        check_setup(setup)
    except PinchConfigError as e:
        raise _err(i18n.t("part {name}: {error}", name=repr(name), error=e)) from None
    from .ballpath import PIECE_CAPACITY
    capacity = _capacity(sec, PIECE_CAPACITY)
    stages = _stages(sec.get("stage"), by_name, name)
    intake = _intake(sec.get("intake"), by_name)
    beams = _beam_breaks(sec.get("beam_break", []), stages, 1)
    turn = tuple(tuple(float(v) for v in row) for row in channel_turn(box))
    return PieceShooterConfig(part=name, setup=setup, piece=kind.name, turn=turn, air=air, tilt=p["tilt"],
                              tilt_range=tuple(p["tilt_range"]), tilt_angle=p["tilt_angle"], capacity=capacity,
                              stages=stages, intake=intake, beam_breaks=beams)


class PieceShooter(DiscShooter):
    """The pinch model running the pinch_shooter part's two wheel shafts (spin0 the bottom, spin1 the top) on the
    world's battery, exactly as DiscShooter runs a disc launcher's wheel groups: the same bindings, tilt, hand-off and
    mirroring, the model swapped."""

    def reset(self) -> None:
        """A fresh pinch model: its clock at 0, both shafts at rest, the channel empty."""
        self.launcher = PinchLauncher(self.config.setup, external_bus=True)
        self.exited = []
        self._fed = 0
