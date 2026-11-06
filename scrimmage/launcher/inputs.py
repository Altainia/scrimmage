"""Launcher setup inputs: the dataclasses, loading a setup TOML, presets, and dotted-key overrides.

All units are SI; angles are degrees (keys end in `_deg`); `target_rps` is wheel-shaft rotations per second.

`[ball]` and `[wheels]` take `measured = "<file>.measure.toml"`, relative to the setup file: the numbers the file
measured replace the preset's (scrimmage/measure/use.py), and `Setup.provenance` says where each came from.
"""

import copy
import dataclasses
import tomllib
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path

from .. import i18n
from ..i18n import N_
from ..motors import MOTORS

PRESETS: dict[str, dict[str, dict]] = tomllib.loads(
    (Path(__file__).with_name("presets.toml")).read_text(encoding="utf-8")
)

# Air preset applied when [air] (or some of its keys) is omitted.
DEFAULT_PRESET = {"air": "default"}


FEED_RULES = ("schedule", "wait_for_speed")


class LauncherConfigError(ValueError):
    """A launcher setup problem; the message names the bad key and lists the valid ones."""


_frozen = dict(frozen=True, kw_only=True)  # kw_only so required fields may follow defaulted ones


@dataclass(**_frozen)
class Ball:
    diameter: float
    mass: float
    stiffness_n: float  # force (N) that compresses the ball by 25% of its diameter between flat plates
    stiffness_exponent: float = 1.5
    restitution: float  # bounce coefficient from a drop on a hard floor
    inertia_factor: float = 0.4  # moment of inertia / (mass * radius^2): 0.4 a uniform solid, 2/3 a thin shell


@dataclass(**_frozen)
class Wheels:
    diameter: float
    width: float
    durometer: float
    friction: float
    positions: tuple[float, ...] = (0.0,)  # wheel centers along the shaft (m)
    roller: bool = False  # one continuous roller of length `width` centered at 0


@dataclass(**_frozen)
class Shaft:
    wheel_inertia: float
    added_inertia: float = 0.0
    motor: str = "KrakenX60"
    motor_count: int = 1
    gearing: float = 1.0  # motor rotations per wheel rotation


@dataclass(**_frozen)
class Control:
    target_rps: float
    kS: float = 0.0
    kV: float | None = None  # volts per wheel rps; None: derived from the motor's 1/Kv scaled by gearing
    kP: float
    stator_limit: float | None = None
    supply_limit: float | None = None


@dataclass(**_frozen)
class BatteryIn:
    nominal_voltage: float = 12.5
    resistance: float = 0.02


@dataclass(**_frozen)
class Hood:
    gap: float
    entry_deg: float = 240.0
    exit_angle_deg: float
    friction: float
    # The hood's entry end is a straight flare (lead-in), opening away from the wheel at this angle to the
    # constant-gap arc, so a ball's squeeze ramps up as it is drawn in (see launcher/model.py). 30 is an estimate,
    # not sourced: a polycarbonate hood's end bent out by a third of a right angle. Measure your hood's.
    flare_deg: float = 30.0
    # The exit end. None (most FRC hoods): the hood ends at the arc's end and the squeeze releases as the ball rolls
    # off its edge. A number: a flared lip at that angle, releasing the squeeze as the entry flare loads it.
    exit_flare_deg: float | None = None


@dataclass(**_frozen)
class Launcher:
    height: float  # shaft height above the floor
    width: float  # inside width


@dataclass(**_frozen)
class Robot:
    velocity: tuple[float, float] = (0.0, 0.0)  # vx, vy (m/s)


@dataclass(**_frozen)
class Air:
    density: float = 1.2
    drag_coefficient: float
    lift_slope: float
    lift_max: float


@dataclass(**_frozen)
class Goal:
    distance: float
    height: float
    diameter: float


@dataclass(**_frozen)
class Feed:
    count: int = 10
    rate: float = 5.0
    rule: str = "wait_for_speed"
    tolerance: float = 0.05
    lanes: tuple[float, ...] = (0.0,)
    entry_speed: float = 1.0
    timeout: float = 10.0


@dataclass(**_frozen)
class Spread:
    """Random variation. Fractions are standard deviations relative to nominal; the others are in their units.
    The defaults are estimates (see docs/launcher.md)."""

    seed: int = 1
    diameter: float = 0.01
    mass: float = 0.02
    stiffness: float = 0.05
    feed_time: float = 0.01
    lane: float = 0.005
    entry_speed: float = 0.1
    yaw_deg: float = 0.5
    shooter_speed: float = 0.0


@dataclass(**_frozen)
class Results:
    recovery_tolerance: float = 0.02


@dataclass(**_frozen)
class Setup:
    ball: Ball
    wheels: Wheels
    shaft: Shaft
    control: Control
    battery: BatteryIn
    hood: Hood
    launcher: Launcher
    robot: Robot
    air: Air
    goal: Goal
    feed: Feed
    spread: Spread
    results: Results
    dt: float | None = None
    # "ball.stiffness_n" -> the measurement file a number came from (measure/file.py's Provenance): `measured` in
    # [ball] or [wheels]. Empty when no number was measured; not compared or shown, so such a setup is as before.
    provenance: dict = field(default_factory=dict, compare=False, repr=False)


# Ranges checked after parsing: (section, key, test, what the value must be, marked N_()). Optional keys left unset
# (None) are skipped. contact.py keeps its own physics-side guards as well.
_POSITIVE = (lambda v: v > 0.0, N_("positive"))
_NON_NEGATIVE = (lambda v: v >= 0.0, N_("zero or more"))
_RANGES: list[tuple[str, str, object, str]] = [
    *(("ball", k, *_POSITIVE) for k in ("diameter", "mass", "stiffness_n", "stiffness_exponent", "inertia_factor")),
    ("ball", "restitution", lambda v: 0.0 < v <= 1.0, N_("above 0 and at most 1")),
    *(("wheels", k, *_POSITIVE) for k in ("diameter", "width")),
    ("wheels", "durometer", lambda v: 0.0 < v < 100.0, N_("above 0 and below 100")),
    ("wheels", "friction", *_NON_NEGATIVE),
    *(("shaft", k, *_NON_NEGATIVE) for k in ("wheel_inertia", "added_inertia")),
    ("shaft", "motor_count", lambda v: v >= 1, N_("at least 1")),
    ("shaft", "gearing", *_POSITIVE),
    *(("control", k, *_POSITIVE) for k in ("stator_limit", "supply_limit")),
    ("battery", "nominal_voltage", *_POSITIVE),
    ("battery", "resistance", *_NON_NEGATIVE),
    ("hood", "gap", *_POSITIVE),
    ("hood", "friction", *_NON_NEGATIVE),
    ("launcher", "height", *_NON_NEGATIVE),
    ("launcher", "width", *_POSITIVE),
    *(("air", k, *_NON_NEGATIVE) for k in ("density", "drag_coefficient", "lift_slope", "lift_max")),
    ("goal", "diameter", *_POSITIVE),
    ("goal", "height", *_NON_NEGATIVE),
    *(("feed", k, *_POSITIVE) for k in ("tolerance", "timeout")),
    ("feed", "entry_speed", *_NON_NEGATIVE),
    *(("spread", f.name, *_NON_NEGATIVE) for f in dataclasses.fields(Spread) if f.name != "seed"),
    ("results", "recovery_tolerance", *_POSITIVE),
]


def check_lane(lane: float, diameter: float, width: float, where: str) -> None:
    """A ball fed at `lane` (m across the shaft) must lie between the side plates: |lane| + D / 2 <= width / 2."""
    if abs(lane) + diameter / 2.0 > width / 2.0 + 1e-12:
        raise LauncherConfigError(
            i18n.t("{where} {lane} does not fit: a ball of diameter {diameter} there reaches {reach} m from the "
                   "center, outside the launcher's width {width} (+-{half} m)", where=where, lane=lane,
                   diameter=diameter, reach=f"{abs(lane) + diameter / 2.0:.4g}", width=width,
                   half=f"{width / 2.0:.4g}")
        )


def _check_ranges(setup: Setup) -> None:
    for section, key, ok, what in _RANGES:
        value = getattr(getattr(setup, section), key)
        if value is not None and not ok(value):
            raise LauncherConfigError(i18n.t("[{section}] {key} must be {what}, not {value}",
                                             section=section, key=key, what=i18n.translated(what), value=value))
    shaft = setup.shaft
    if shaft.wheel_inertia + shaft.added_inertia <= 0.0:
        raise LauncherConfigError(
            i18n.t("{shaft_table} wheel_inertia + added_inertia must be positive, not {inertia}",
                   inertia=shaft.wheel_inertia + shaft.added_inertia, shaft_table="[shaft]")
        )


SECTIONS: dict[str, type] = {
    f.name: f.type for f in dataclasses.fields(Setup) if f.name not in ("dt", "provenance")
}
MEASURED_SECTIONS = ("ball", "wheels")  # the sections that take `measured = "<file>.measure.toml"` (measure/use.py)
_TOP_LEVEL = set(SECTIONS) | {"dt", "sweep", "chart"}


def _valid_keys(section: str) -> list[str]:
    keys = [f.name for f in dataclasses.fields(SECTIONS[section])]
    return keys + ["preset"] if section in PRESETS else keys


def _unknown_section(name: str) -> LauncherConfigError:
    return LauncherConfigError(
        i18n.t("unknown section [{name}]; valid sections: {sections}",
               name=name, sections=', '.join(sorted(_TOP_LEVEL)))
    )


def _unknown_key(section: str, key: str) -> LauncherConfigError:
    return LauncherConfigError(
        i18n.t("unknown key '{key}' in [{section}]; valid keys: {keys}",
               key=key, section=section, keys=', '.join(_valid_keys(section)))
    )


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _coerce(value, tp, section: str, key: str):
    where = f"[{section}] {key}"
    if tp is float:
        if not _is_number(value):
            raise LauncherConfigError(i18n.t("{where} must be a number, not {value}", where=where, value=repr(value)))
        return float(value)
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise LauncherConfigError(i18n.t("{where} must be a whole number, not {value}",
                                             where=where, value=repr(value)))
        return value
    if tp is str:
        if not isinstance(value, str):
            raise LauncherConfigError(i18n.t("{where} must be text, not {value}", where=where, value=repr(value)))
        return value
    if tp is bool:
        if not isinstance(value, bool):
            raise LauncherConfigError(i18n.t("{where} must be true or false, not {value}",
                                             where=where, value=repr(value)))
        return value
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (types.UnionType, typing.Union):  # `float | None`: TOML has no null, so None means absent
        inner = [a for a in args if a is not type(None)][0]
        return _coerce(value, inner, section, key)
    if origin is tuple:
        if not isinstance(value, (list, tuple)):
            raise LauncherConfigError(i18n.t("{where} must be a list of numbers, not {value}",
                                             where=where, value=repr(value)))
        if len(args) == 2 and args[1] is not Ellipsis and len(value) != len(args):
            raise LauncherConfigError(i18n.t("{where} must have {count} numbers, not {value}",
                                             where=where, count=len(args), value=repr(value)))
        if not value:
            raise LauncherConfigError(i18n.t("{where} must not be empty", where=where))
        return tuple(_coerce(v, float, section, key) for v in value)
    raise AssertionError(f"unhandled type {tp!r}")  # pragma: no cover


def _build_section(section: str, table):
    cls = SECTIONS[section]
    if not isinstance(table, dict):
        raise LauncherConfigError(i18n.t("[{section}] must be a table, not {table}",
                                         section=section, table=repr(table)))
    table = dict(table)
    if "measured" in table and section in MEASURED_SECTIONS:
        raise LauncherConfigError(i18n.t("[{section}] measured: a measurement file is read in a launcher setup "
                                         "file's [{section}]; here give the measured numbers themselves",
                                         section=section))
    valid = {f.name: f for f in dataclasses.fields(cls)}
    for key in table:
        if key not in valid and not (key == "preset" and section in PRESETS):
            raise _unknown_key(section, key)
    values: dict = {}
    name = table.pop("preset", DEFAULT_PRESET.get(section))
    if name is not None:
        known = PRESETS.get(section, {})
        if not isinstance(name, str) or name not in known:
            raise LauncherConfigError(
                i18n.t("[{section}] unknown preset {name}; known {section} presets: {known}",
                       section=section, name=repr(name), known=', '.join(known))
            )
        values.update(known[name])
    values.update(table)
    hints = typing.get_type_hints(cls)
    missing = [
        k for k, f in valid.items()
        if k not in values and f.default is dataclasses.MISSING
    ]
    if missing:
        raise LauncherConfigError(
            i18n.t("[{section}] missing required key(s): {missing}; valid keys: {keys}",
                   section=section, missing=', '.join(missing), keys=', '.join(_valid_keys(section)))
        )
    return cls(**{k: _coerce(v, hints[k], section, k) for k, v in values.items()})


def apply_measured(raw: dict, path: Path | None = None) -> tuple[dict, dict]:
    """`raw` with each `[ball]` and `[wheels]` `measured = "<file>"` replaced by the numbers the file measured, and
    their provenance ("ball.stiffness_n" -> Provenance). `path` is the setup file: the files are found from its
    folder (else the working folder) and its errors name it. No `measured`: `raw` itself and {}."""
    named = {s: raw[s]["measured"] for s in MEASURED_SECTIONS if isinstance(raw.get(s), dict) and "measured" in raw[s]}
    if not named:
        return raw, {}
    from ..measure.file import MeasureError
    from ..measure.use import apply_ball, apply_wheels

    base = Path(path).parent if path is not None else Path(".")
    for section, name in named.items():
        if not isinstance(name, str):
            raise LauncherConfigError(i18n.t("[{section}] measured must be a measurement file's path in quotes "
                                             "(\"{tread_measure_toml}\"), not {name}", section=section,
                                             name=repr(name), tread_measure_toml="tread.measure.toml"))
    shared = len(named) == 2 and (base / named["ball"]).resolve() == (base / named["wheels"]).resolve()
    out, provenance, fits = dict(raw), {}, {}  # fits: a launcher file named by both sections is fit once
    for section, name in named.items():
        where = f"{path}: [{section}]" if path is not None else f"[{section}]"
        fields = {k: v for k, v in raw[section].items() if k != "measured"}
        apply = apply_ball if section == "ball" else apply_wheels
        try:
            out[section], got = apply(fields, name, where, base=base, shared=shared, fits=fits)
        except MeasureError as e:
            raise LauncherConfigError(str(e)) from None
        provenance |= {f"{section}.{k}": p for k, p in got.items()}
    return out, provenance


def measured_raw(raw: dict, setup: Setup, path: Path | None = None) -> tuple[dict, dict[str, tuple[str, str]]]:
    """`raw` with each `measured` replaced by the numbers `setup` (parsed from `raw`) took from it, without reading
    the files' fits again: a sweep's rows reuse them. Also each section whose ball file has a base preset:
    {"ball": (the file as named, its base)}, for a swept preset to be checked against."""
    from ..measure.file import load
    base = Path(path).parent if path is not None else Path(".")
    out, bases = dict(raw), {}
    for section in MEASURED_SECTIONS:
        table = raw.get(section)
        if not (isinstance(table, dict) and "measured" in table):
            continue
        name = table["measured"]
        fields = {k: v for k, v in table.items() if k != "measured"}
        fields |= {k.split(".", 1)[1]: getattr(getattr(setup, section), k.split(".", 1)[1])
                   for k in setup.provenance if k.startswith(f"{section}.")}
        mf = load(base / name)  # read when `setup` was parsed, so it is there and well formed
        if section == "ball" and mf.kind == "launcher":  # its fit's ball file, if any, gave the ball's numbers
            from ..measure.use import launcher_fit
            ball_file = launcher_fit(mf, f"[{section}]", "measured", name).ball_file  # reused, not fit again
            mf = load(ball_file) if ball_file is not None else mf
        if section == "ball" and mf.kind == "ball" and mf.base is not None:
            fields.setdefault("preset", mf.base)
            bases[section] = (name, mf.base)
        out[section] = fields
    return out, bases


def parse_setup(raw: dict, path: Path | None = None) -> Setup:
    """Build a `Setup` from a raw dict (the parsed TOML), applying measurements (`path`, the setup file, finds them:
    apply_measured), presets and defaults."""
    for key in raw:
        if key not in _TOP_LEVEL:
            raise _unknown_section(key)
    raw, provenance = apply_measured(raw, path)
    parts = {name: _build_section(name, raw.get(name, {})) for name in SECTIONS}
    dt = raw.get("dt")
    if dt is not None:
        dt = _coerce(dt, float, "setup", "dt")
        if dt <= 0:
            raise LauncherConfigError(i18n.t("dt must be positive, not {dt}", dt=repr(dt)))
    setup = Setup(**parts, dt=dt, provenance=provenance)
    _check_ranges(setup)
    if setup.shaft.motor not in MOTORS:
        raise LauncherConfigError(
            i18n.t("{shaft_table} unknown motor {motor}; known motors: {motors}", motor=repr(setup.shaft.motor),
                   motors=', '.join(sorted(MOTORS)), shaft_table="[shaft]")
        )
    feed = setup.feed
    if feed.rule not in FEED_RULES:
        raise LauncherConfigError(i18n.t("{feed_table} unknown rule {rule}; valid rules: {rules}",
                                         rule=repr(feed.rule), rules=', '.join(FEED_RULES), feed_table="[feed]"))
    if feed.count < 1:
        raise LauncherConfigError(i18n.t("{feed_table} count must be at least 1, not {count}", count=feed.count,
                                         feed_table="[feed]"))
    if feed.rate <= 0.0:
        raise LauncherConfigError(i18n.t("{feed_table} rate must be positive, not {rate}", rate=feed.rate,
                                         feed_table="[feed]"))
    for lane in feed.lanes:
        check_lane(lane, setup.ball.diameter, setup.launcher.width, "[feed] lanes: lane")
    # The hood wraps from where the ball enters (entry_deg, fixed on the robot) round to where it leaves
    # (exit_angle_deg + 90, the hood turning with the exit angle). No wrap means the ball never touches it.
    wrap = setup.hood.entry_deg - (setup.hood.exit_angle_deg + 90.0)
    if wrap <= 0:
        raise LauncherConfigError(
            i18n.t("{hood_table} wrap must be positive: entry_deg {entry_deg} minus exit angle {exit_plus_90} "
                   "(exit_angle_deg + 90) is {wrap}; lower exit_angle_deg or raise entry_deg",
                   entry_deg=setup.hood.entry_deg, exit_plus_90=setup.hood.exit_angle_deg + 90.0, wrap=wrap,
                   hood_table="[hood]")
        )
    # The flares are the angles the hood's ends open at, not arcs of the wrap; a wrap shorter than the flares' ramps
    # is fine (the squeeze then peaks below the full delta, see launcher/model.py).
    flare = setup.hood.flare_deg
    if not 0.0 < flare < 90.0:
        raise LauncherConfigError(i18n.t("{hood_table} flare_deg must be above 0 and below 90, not {flare}",
                                         flare=flare, hood_table="[hood]"))
    exit_flare = setup.hood.exit_flare_deg
    if exit_flare is not None and not 0.0 < exit_flare < 90.0:
        raise LauncherConfigError(i18n.t("{hood_table} exit_flare_deg must be above 0 and below 90, not {exit_flare}",
                                         exit_flare=exit_flare, hood_table="[hood]"))
    return setup


def _check_key(dotted: str) -> tuple[str, str | None]:
    if dotted == "dt":
        return "dt", None
    section, _, key = dotted.partition(".")
    if section not in SECTIONS:
        raise _unknown_section(section)
    if not key or key not in _valid_keys(section):
        raise _unknown_key(section, key)
    return section, key


def apply_overrides(raw: dict, overrides: dict[str, object]) -> dict:
    """Return a deep copy of `raw` with dotted keys (`hood.gap`, or `dt`) set to the given values."""
    out = copy.deepcopy(raw)
    for dotted, value in overrides.items():
        section, key = _check_key(dotted)
        if key is None:
            out["dt"] = copy.deepcopy(value)
        else:
            out.setdefault(section, {})[key] = copy.deepcopy(value)
    return out


def load_setup(source: Path | dict) -> tuple[Setup, dict[str, list], list[dict]]:
    """Load a setup from a TOML path or a dict. Returns the setup, the raw `[sweep]` table and the `[[chart]]` list."""
    if isinstance(source, dict):
        raw, path = source, None
    else:
        path = Path(source)
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise LauncherConfigError(f"{source}: {e}") from None
    setup = parse_setup(raw, path)
    sweep = raw.get("sweep", {})
    if not isinstance(sweep, dict):
        raise LauncherConfigError(i18n.t("{sweep_table} must be a table, not {sweep}", sweep=repr(sweep),
                                         sweep_table="[sweep]"))
    for dotted, values in sweep.items():
        _check_key(dotted)
        if not isinstance(values, list) or not values:
            raise LauncherConfigError(i18n.t("{sweep_table} {dotted} must be a non-empty list of values, not {values}",
                                             dotted=dotted, values=repr(values), sweep_table="[sweep]"))
    charts = raw.get("chart", [])
    if not isinstance(charts, list) or not all(isinstance(c, dict) for c in charts):
        raise LauncherConfigError(i18n.t("{chart_table} must be a list of tables", chart_table="[[chart]]"))
    return setup, dict(sweep), list(charts)
