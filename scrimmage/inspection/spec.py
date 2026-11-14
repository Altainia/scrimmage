"""The season table (seasons.toml), the [inspection] section of a robot config, and choosing a season (design Calls
I1 to I3). Limits are in SI: meters and kilograms."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError, _table
from ..i18n import t

LB, IN = 0.45359237, 0.0254
DEFAULT_BATTERY = 12.89 * LB     # AndyMark's listing for am-0844 (MK ES17-12); the rules allow 11.0 to 14.5 lb (R601)
DEFAULT_BUMPERS = 15 * LB        # an UNCALIBRATED estimate of a bumper set; no source
START_ALLOWANCE = 0.25 * IN      # the minor protrusions every season's perimeter rule allows (start_reach's allowance)


@dataclass(frozen=True)
class Limit:
    value: float | None          # m or kg; None = no limit
    rule: str
    unless: str = ""             # a zone or time exception, in words (not checked)


@dataclass(frozen=True)
class Season:
    year: int
    manual: str
    url: str
    weight: Limit
    weight_with_bumpers: Limit
    bumper_set: Limit
    perimeter: Limit
    start_reach: Limit           # no overhang at the start: 0 in every season, within START_ALLOWANCE
    start_height: Limit
    height: Limit
    reach: Limit
    diameter: Limit
    not_checked: tuple[str, ...]


_NONE = Limit(None, "")


def _limit(entry: dict | None, season: str = "?", name: str = "?") -> Limit:
    if entry is None:
        return _NONE
    if ("max_lb" not in entry and "max_in" not in entry) or "rule" not in entry:
        raise ValueError(t("seasons.toml: season {season}, limit {name}: needs max_lb or max_in, and rule",
                           season=season, name=name))
    if "max_lb" in entry:
        value = entry["max_lb"] * LB
    else:
        value = entry["max_in"] * IN
    return Limit(value, entry["rule"], entry.get("unless", ""))


def _parse(text: str) -> dict[int, Season]:
    seasons = {}
    for key, e in tomllib.loads(text).items():
        year = int(key)
        seasons[year] = Season(
            year, e["manual"], e["url"], *(_limit(e.get(n), key, n) for n in (
                "weight", "weight_with_bumpers", "bumper_set", "perimeter", "start_reach", "start_height", "height",
                "reach", "diameter")), tuple(e.get("not_checked", ())))
    return seasons


def _load() -> dict[int, Season]:
    return _parse((Path(__file__).with_name("seasons.toml")).read_text(encoding="utf-8"))


SEASONS: dict[int, Season] = _load()


@dataclass(frozen=True)
class InspectionSettings:
    season: int | None
    battery_mass: float | None   # kg, as the team weighed it; None = the estimate
    bumper_mass: float | None
    present: bool                # the config has an [inspection] section (even an empty one)


def _season_number(v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v not in SEASONS:
        raise ConfigError(t("[{section}] season must be one of {seasons}, not {value}",
                            section="inspection", seasons=", ".join(str(y) for y in sorted(SEASONS)), value=repr(v)))
    return v


def check_override(year: int, name: str) -> None:
    """A season asked for on the command line or in a tool call (`name`: "--season" or "season") that scrimmage doesn't
    know: raises ConfigError listing the seasons it knows."""
    if isinstance(year, bool) or year not in SEASONS:
        raise ConfigError(t("{name} {year}: scrimmage knows the seasons {seasons}", name=name, year=year,
                            seasons=", ".join(str(y) for y in sorted(SEASONS))))


def _mass(table: dict, key: str) -> float | None:
    v = table.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise ConfigError(t("[{section}] {key} must be a number of kilograms, 0 or more, not {value}",
                            section="inspection", key=key, value=repr(v)))
    return float(v)


def settings(raw: dict) -> InspectionSettings:
    present = "inspection" in raw
    table = _table(raw, "inspection")
    for key in table:
        if key not in ("season", "battery_mass", "bumper_mass"):
            raise ConfigError(t("[{section}] has no setting {key} (it has season, battery_mass and bumper_mass)",
                                section="inspection", key=key))
    season = _season_number(table["season"]) if "season" in table else None
    return InspectionSettings(season, _mass(table, "battery_mass"), _mass(table, "bumper_mass"), present)


def _range_season(raw: dict) -> tuple[int, str] | None:
    name = _table(raw, "range").get("file")
    if not isinstance(name, str):
        return None
    found = re.match(r"(\d{4})(?!\d)", Path(name).name)       # four digits, then the end or a non-digit
    if found and int(found.group(1)) in SEASONS:
        return int(found.group(1)), name
    return None


def choose_season(raw: dict, override: int | None = None) -> tuple[Season, str]:
    """The season to inspect against and why: --season, then [inspection] season, then the year a built-in range
    file's name starts with, else the newest season."""
    if override is not None:
        return SEASONS[_season_number(override)], "--season"
    chosen = settings(raw).season
    if chosen is not None:
        return SEASONS[chosen], "[inspection] season"
    in_range = _range_season(raw)
    if in_range is not None:
        return SEASONS[in_range[0]], t("the [{section}] file {file}", section="range", file=in_range[1])
    return SEASONS[max(SEASONS)], "the newest season"
