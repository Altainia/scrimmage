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
SETTINGS = {"section": "inspection", "battery_setting": "battery_mass", "bumper_setting": "bumper_mass"}


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
    advice: Said | None = None   # when a line used an estimate: which settings to set; None otherwise

    @property
    def over(self) -> list[Line]:
        return [x for x in self.lines if x.over > 0]


def line_dict(line: Line, localized: bool) -> dict:
    """A line as a JSON-ready dict (numbers in SI): the Robot tab's and the MCP tool's one serializer. `localized` puts
    the text in the Units and Language settings; otherwise it is the machine text."""
    return {"key": line.key, "rule": line.rule, "measured": line.measured, "limit": line.limit, "over": line.over,
            "estimate": line.estimate, "text": shown(line.text) if localized else str(line.text)}


def _said(words: Callable[[], str]) -> Said:
    """The machine text (SI, American English) and the words for the settings."""
    with units.fixed("metric"):
        machine = words()
    return Said(machine, words)


def _length(v: float | None, fewer: int = 1) -> str:
    return units.fmt(v, "length", fewer=fewer)


def _start_length(v: float | None) -> str:
    """The reach at the start pose, at one precision on both of its lines (start reach and reach beyond the frame):
    the start reach is judged against a 1/4 in allowance, so it keeps the decimals."""
    return units.fmt(v, "length", fewer=0)


def _mass(v: float | None, fewer: int = 1) -> str:
    return units.fmt(v, "mass", fewer=fewer)


def _line(key: str, season: Season, what: Callable[[], str], measured: float, limit: Limit, show: Callable[[float], str],
          *, value: Callable[[], str] | None = None, rule: str | None = None, estimate: bool = False,
          extras: tuple[Callable[[], str], ...] = ()) -> Line:
    rule = rule or limit.rule
    if limit.value is None:
        words = lambda: t("{what}: no limit in {year}", what=what(), year=season.year)    # noqa: E731
        return Line(key, rule, None, None, 0.0, False, _said(words))
    over = measured - limit.value
    tail = list(extras)

    def words(warn: bool = False) -> str:
        shown_value = value() if value else show(measured)
        if over <= 0 and estimate:      # never read as a pass: it rests on an estimate
            s = t("{what}: {measured}, within the {limit} limit ({rule}), with estimates", what=what(),
                  measured=shown_value, limit=show(limit.value), rule=rule)
        elif over <= 0:
            s = t("{what}: {measured}, within the {limit} limit ({rule})", what=what(), measured=shown_value,
                  limit=show(limit.value), rule=rule)
        elif warn:
            s = t("{year} {rule}: {what} {measured}, over the {limit} limit by {by}", year=season.year, rule=rule,
                  what=what(), measured=shown_value, limit=show(limit.value), by=show(over))
        else:
            s = t("{what}: {measured}, over the {limit} limit ({rule}) by {by}", what=what(), measured=shown_value,
                  limit=show(limit.value), rule=rule, by=show(over))
        if over > 0 and limit.unless:
            s += t("; {exception}: not checked", exception=limit.unless)
        return s + "".join(extra() for extra in tail)

    return Line(key, rule, measured, limit.value, over, estimate, _said(words),
                _said(lambda: words(True)) if over > 0 else None)


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

    at_start = lambda: t("; at the start pose (a scenario run with [{section}] watches it during the run)",  # noqa: E731
                         **SETTINGS)
    lines = [_line("weight", season, lambda: t("weight without bumpers and battery"), weight, season.weight,
                   _mass, estimate=est_battery or est_bumpers,
                   value=_weight_sum([(modeled, False), (battery_part, est_battery), (bumpers_part, est_bumpers)],
                                     weight))]
    if season.weight_with_bumpers.value is not None:
        lines.append(_line("weight_with_bumpers", season, lambda: t("weight with bumpers, without the battery"),
                           with_bumpers, season.weight_with_bumpers, _mass, estimate=est_battery,
                           value=_weight_sum([(modeled, False), (battery_part, est_battery)], with_bumpers)))
    if season.bumper_set.value is not None:
        if est_bumpers:      # the estimate may equal the limit: comparing it would say nothing
            lines.append(_bumpers_unchecked(season, bumpers))
        else:
            lines.append(_line("bumper_set", season, lambda: t("one set of bumpers"), bumpers, season.bumper_set,
                               _mass))
    frame_note = () if m.frame_plate else (
        lambda: t("; the frame is the outline of all the chassis's geoms (no frame plate)"),)
    lines.append(_line("perimeter", season, lambda: t("frame perimeter"), m.perimeter, season.perimeter,
                       _length, extras=frame_note))
    lines.append(_line("start_height", season, lambda: t("starting height"), m.height, season.start_height,
                       _length))
    lines.append(_start_reach(season, m))
    if season.diameter.value is not None:
        lines.append(_line("diameter", season, lambda: t("diameter of the smallest circle holding the robot"),
                           m.diameter, season.diameter, _length))
    lines.append(_line("height", season, lambda: t("height"), m.height, season.height, _length,
                       extras=(at_start,)))
    lines.append(_line("reach", season, lambda: t("reach beyond the frame"), m.reach, season.reach,
                       _start_length, extras=(at_start,)))
    notes = []
    if weight < 0:
        notes.append(_said(lambda: t("the model weighs {modeled}, less than the {battery} battery and {bumpers} "
                                     "bumpers taken off it: if your model leaves them out, set {battery_setting} and "
                                     "{bumper_setting} to 0 under [{section}]", modeled=_mass(m.mass),
                                     battery=_mass(battery, 0), bumpers=_mass(bumpers, 0), **SETTINGS)))
    advice = None
    if any(x.estimate for x in lines):
        advice = _said(lambda: _advice(est_battery, est_bumpers))
    return Inspection(season, reason, lines, season.not_checked, tuple(notes), advice)


def _advice(battery: bool, bumpers: bool) -> str:
    """Which settings to set: only the unset ones, in the words too."""
    if battery and bumpers:
        return t("with estimates: weigh your battery and bumpers and set {battery_setting} and {bumper_setting} under "
                 "[{section}]", **SETTINGS)
    if battery:
        return t("with an estimate: weigh your battery and set {battery_setting} under [{section}]",
                 **SETTINGS)
    return t("with an estimate: weigh your bumpers and set {bumper_setting} under [{section}]", **SETTINGS)


def _bumpers_unchecked(season: Season, bumpers: float) -> Line:
    """The default bumper estimate isn't compared with the limit (in 2020 to 2024 it equals it): a not-checked line."""
    limit = season.bumper_set
    words = lambda: t("one set of bumpers: {mass}, estimate, not checked against the {limit} limit ({rule})",  # noqa: E731
                      mass=_mass(bumpers), limit=_mass(limit.value), rule=limit.rule)
    return Line("bumper_set", limit.rule, bumpers, limit.value, 0.0, True, _said(words))


def _start_reach(season: Season, m) -> Line:
    """The reach at the start must be 0, within the frame perimeter (the season's no-overhang rule, start_reach), with
    START_ALLOWANCE for minor protrusions."""
    rule = season.start_reach.rule
    allowed = (season.start_reach.value or 0.0) + START_ALLOWANCE
    over = m.reach - allowed

    def part() -> str:
        return f" ({m.reach_geom})" if m.reach_geom else ""

    def words(warn: bool = False) -> str:
        reach, allowance, by = _start_length(m.reach), _start_length(allowed), _start_length(over)
        if m.reach <= 0:
            return t("start reach: within the frame")
        if over > 0:
            if warn:
                return t("{year} {rule}: start reach {reach} past the frame{part}, over the {allowance} allowed for "
                         "minor protrusions by {by}; the rules want it within the frame", year=season.year,
                         rule=rule, reach=reach, part=part(), allowance=allowance, by=by)
            return t("start reach: {reach} past the frame{part}, over the {allowance} allowed for minor protrusions "
                     "({rule}) by {by}; the rules want it within the frame", reach=reach, part=part(),
                     allowance=allowance, rule=rule, by=by)
        return t("start reach: {reach} past the frame{part}, within the {allowance} allowed for minor protrusions "
                 "({rule})", reach=reach, part=part(), allowance=allowance, rule=rule)

    return Line("start_reach", rule, m.reach, allowed, over, False, _said(words),
                _said(lambda: words(True)) if over > 0 else None)


def warnings(inspection: Inspection, skip: tuple[str, ...] = ()) -> list[Said]:
    """One warning per over line ("2026 R104: frame perimeter 110.24 in, over the 110 in limit by 0.24 in"), then the
    report's other warnings. The lines whose keys are in `skip` are left out (a scenario's start notes leave height,
    reach and diameter to the monitor)."""
    over = [x for x in inspection.over if x.key not in skip]
    out = [x.warning for x in over if x.warning is not None] + list(inspection.notes)
    if inspection.advice is not None and any(x.estimate for x in over):
        out.append(inspection.advice)
    return out


def season_line(inspection: Inspection) -> Said:
    year, reason = inspection.season.year, inspection.reason

    def words() -> str:
        if reason == "the newest season":
            return t("{year} rules (the newest season; set [{section}] season to choose)", year=year,
                     section="inspection")
        return t("{year} rules (from {reason})", year=year, reason=reason)
    return _said(words)


def render(inspection: Inspection) -> list[str]:
    """What `scrimmage inspect` prints, in the Units and Language settings."""
    s = inspection.season
    out = [shown(season_line(inspection)), t("Measured at the model's start pose.")]
    out += [shown(x.text) for x in inspection.lines]
    out += [shown(n) for n in inspection.notes]
    if inspection.advice is not None:
        out.append(shown(inspection.advice))
    if inspection.not_checked:
        out.append(t("Not checked:"))
        out += [f"  - {item}" for item in inspection.not_checked]
    out.append(t("Limits from {manual} {url}", manual=s.manual, url=s.url))
    return out
