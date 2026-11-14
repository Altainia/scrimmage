"""Comparing a measured robot with a season's limits: the inspection report (design Calls I2, I3, I5 and I6).

`inspect_world(world, raw, override)` measures the robot at the world's start pose (measure.py) and gives one `Line`
per limit. A line's `text` is worded twice, as scenario notes are (checks.Said): the str itself is the machine text
(American English, SI: millimeters and kilograms), and `text.words()` builds it in the Units and Language settings.
The scenario start notes, the assembler's warnings and MCP take `warnings(inspection)`."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .. import units
from ..checks import Said, shown
from ..i18n import t
from . import measure as measuring
from .spec import DEFAULT_BATTERY, DEFAULT_BUMPERS, START_ALLOWANCE, Limit, Season, choose_season, settings

KEYS = ("weight", "weight_with_bumpers", "bumper_set", "perimeter", "start_height", "start_reach", "diameter",
        "height", "reach")
SETTINGS = {"inspection_table": "[inspection]", "battery_setting": "battery_mass", "bumper_setting": "bumper_mass"}


@dataclass(frozen=True)
class Line:
    key: str
    rule: str
    measured: float | None      # m or kg; None with no limit in the season
    limit: float | None
    over: float                 # > 0: over by that much (m or kg)
    estimate: bool              # rests on an estimated battery or bumper mass
    text: Said
    warning: Said | None = None  # the line as a warning ("2026 R104: frame perimeter ..."); None when within


@dataclass(frozen=True)
class Inspection:
    season: Season
    reason: str
    lines: list[Line]
    not_checked: tuple[str, ...]
    notes: tuple[Said, ...] = field(default_factory=tuple)      # warnings that aren't a line's

    @property
    def over(self) -> list[Line]:
        return [x for x in self.lines if x.over > 0]


def _said(words: Callable[[], str]) -> Said:
    """The machine text (SI, American English) and the words for the settings."""
    with units.fixed("metric"):
        machine = words()
    return Said(machine, words)


def _length(v: float | None, fewer: int = 1) -> str:
    return units.fmt(v, "length", fewer=fewer)


def _mass(v: float | None, fewer: int = 1) -> str:
    return units.fmt(v, "mass", fewer=fewer)


def _estimate_extra() -> str:
    return t("; with estimates: weigh your battery and bumpers and set {battery_setting} and {bumper_setting} "
             "under {inspection_table}", **SETTINGS)


def _line(key: str, season: Season, what: Callable[[], str], measured: float, limit: Limit, show: Callable[[float], str],
          *, value: Callable[[], str] | None = None, rule: str | None = None, estimate: bool = False,
          extras: tuple[Callable[[], str], ...] = ()) -> Line:
    rule = rule or limit.rule
    if limit.value is None:
        words = lambda: t("{what}: no limit in {year}", what=what(), year=season.year)    # noqa: E731
        return Line(key, rule, None, None, 0.0, False, _said(words))
    over = measured - limit.value
    tail = [*extras, *([_estimate_extra] if estimate else [])]

    def words(warn: bool = False) -> str:
        shown_value = value() if value else show(measured)
        if over <= 0:
            s = t("{what}: {measured}, within the {limit} limit ({rule})", what=what(), measured=shown_value,
                  limit=show(limit.value), rule=rule)
        elif warn:
            s = t("{year} {rule}: {what} {measured}, over the {limit} limit by {by}", year=season.year, rule=rule,
                  what=what(), measured=shown_value, limit=show(limit.value), by=show(over, 1))
        else:
            s = t("{what}: {measured}, over the {limit} limit ({rule}) by {by}", what=what(), measured=shown_value,
                  limit=show(limit.value), rule=rule, by=show(over, 1))
        if over > 0 and limit.unless:
            s += t("; {exception}: not checked", exception=limit.unless)
        return s + "".join(extra() for extra in tail)

    return Line(key, rule, measured, limit.value, over, estimate, _said(words),
                _said(lambda: words(True)) if over > 0 else None)


def _show_length(v: float, fewer: int = 1) -> str:
    return _length(v, fewer)


def _show_mass(v: float, fewer: int = 1) -> str:
    return _mass(v, fewer)


def _weight_sum(parts: list[tuple[Callable[[], str], bool]], total: float) -> Callable[[], str]:
    def value() -> str:
        pieces = []
        for piece, estimated in parts:
            text = piece()
            pieces.append(t("{part}, estimate", part=text) if estimated else text)
        return f"{_mass(total)} ({' − '.join(pieces)})"
    return value


def inspect_world(world, raw: dict, override: int | None = None) -> Inspection:
    """The robot at the world's start pose against a season's limits. Raises ConfigError for a bad [inspection]."""
    chosen = settings(raw)
    season, reason = choose_season(raw, override)
    m = measuring.measure(world)
    battery = DEFAULT_BATTERY if chosen.battery_mass is None else chosen.battery_mass
    bumpers = DEFAULT_BUMPERS if chosen.bumper_mass is None else chosen.bumper_mass
    est_battery, est_bumpers = chosen.battery_mass is None, chosen.bumper_mass is None
    weight = m.mass - battery - bumpers
    with_bumpers = m.mass - battery

    def modeled() -> str:
        return t("{mass} modeled", mass=_mass(m.mass))

    def battery_part() -> str:
        return t("{mass} battery", mass=_mass(battery, 0))

    def bumpers_part() -> str:
        return t("{mass} bumpers", mass=_mass(bumpers, 0))

    lines = [_line("weight", season, lambda: t("weight without bumpers and battery"), weight, season.weight,
                   _show_mass, estimate=est_battery or est_bumpers,
                   value=_weight_sum([(modeled, False), (battery_part, est_battery), (bumpers_part, est_bumpers)],
                                     weight))]
    if season.weight_with_bumpers.value is not None:
        lines.append(_line("weight_with_bumpers", season, lambda: t("weight with bumpers, without the battery"),
                           with_bumpers, season.weight_with_bumpers, _show_mass, estimate=est_battery,
                           value=_weight_sum([(modeled, False), (battery_part, est_battery)], with_bumpers)))
    if season.bumper_set.value is not None:
        lines.append(_line("bumper_set", season, lambda: t("one set of bumpers"), bumpers, season.bumper_set,
                           _show_mass, estimate=est_bumpers,
                           value=(lambda: t("{part}, estimate", part=_mass(bumpers, 0))) if est_bumpers else None))
    frame_note = () if m.frame_plate else (
        lambda: t("; the frame is the outline of all the chassis's geoms (no frame plate)"),)
    lines.append(_line("perimeter", season, lambda: t("frame perimeter"), m.perimeter, season.perimeter,
                       _show_length, extras=frame_note))
    lines.append(_line("start_height", season, lambda: t("starting height"), m.height, season.start_height,
                       _show_length))
    lines.append(_start_reach(season, m))
    if season.diameter.value is not None:
        lines.append(_line("diameter", season, lambda: t("diameter of the smallest circle holding the robot"),
                           m.diameter, season.diameter, _show_length))
    lines.append(_line("height", season, lambda: t("height"), m.height, season.height, _show_length))
    lines.append(_line("reach", season, lambda: t("reach beyond the frame"), m.reach, season.reach,
                       lambda v, fewer=2: _length(v, fewer)))
    notes = []
    if weight < 0:
        notes.append(_said(lambda: t("the model weighs {modeled}, less than the {battery} battery and {bumpers} "
                                     "bumpers taken off it: if your model leaves them out, set {battery_setting} and "
                                     "{bumper_setting} to 0 under {inspection_table}", modeled=_mass(m.mass),
                                     battery=_mass(battery, 0), bumpers=_mass(bumpers, 0), **SETTINGS)))
    return Inspection(season, reason, lines, season.not_checked, tuple(notes))


def _start_reach(season: Season, m) -> Line:
    """The reach at the start must be 0 (within the frame perimeter), with START_ALLOWANCE for minor protrusions."""
    rule = season.perimeter.rule
    over = m.reach - START_ALLOWANCE

    def part() -> str:
        return f" ({m.reach_geom})" if m.reach_geom else ""

    def words(warn: bool = False) -> str:
        reach = _length(m.reach, 2)
        allowance = _length(START_ALLOWANCE, 0)
        if m.reach <= 0:
            return t("start reach: within the frame")
        if over > 0:
            if warn:
                return t("{year} {rule}: start reach {reach} past the frame{part}; the rules want it within the "
                         "frame ({allowance} for minor protrusions)", year=season.year, rule=rule, reach=reach,
                         part=part(), allowance=allowance)
            return t("start reach: {reach} past the frame{part}; the rules want it within the frame ({allowance} "
                     "for minor protrusions) ({rule})", reach=reach, part=part(), allowance=allowance, rule=rule)
        return t("start reach: {reach} past the frame{part}, within the {allowance} allowed for minor protrusions "
                 "({rule})", reach=reach, part=part(), allowance=allowance, rule=rule)

    return Line("start_reach", rule, m.reach, START_ALLOWANCE, over, False, _said(words),
                _said(lambda: words(True)) if over > 0 else None)


def warnings(inspection: Inspection) -> list[Said]:
    """One warning per over line ("2026 R104: frame perimeter 110.24 in, over the 110 in limit by 0.24 in"), then the
    report's other warnings."""
    return [x.warning for x in inspection.over if x.warning is not None] + list(inspection.notes)


def season_line(inspection: Inspection) -> Said:
    year, reason = inspection.season.year, inspection.reason

    def words() -> str:
        if reason == "the newest season":
            return t("{year} rules (the newest season; set {season_setting} to choose)", year=year,
                     season_setting="[inspection] season")
        return t("{year} rules (from {reason})", year=year, reason=reason)
    return _said(words)


def render(inspection: Inspection) -> list[str]:
    """What `scrimmage inspect` prints, in the Units and Language settings."""
    s = inspection.season
    out = [shown(season_line(inspection)), t("Measured at the model's start pose.")]
    out += [shown(x.text) for x in inspection.lines]
    out += [shown(n) for n in inspection.notes]
    if inspection.not_checked:
        out.append(t("Not checked:"))
        out += [f"  - {item}" for item in inspection.not_checked]
    out.append(t("Limits from {manual} {url}", manual=s.manual, url=s.url))
    return out
