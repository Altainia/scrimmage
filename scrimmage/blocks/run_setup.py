"""A test's setup, before the run: the robot config with the test's `[setup]` applied (`setup_config`: the ball or
piece, the station settings, the seed, the partner), the World built from it (`world_for`), Setup's partner refusal in
words, and a sensor's chart signal (`sensor_value`). It never imports run.py; run.py re-exports every name here.
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

from .. import i18n, units
from ..parts.partner import PartnerOverlap
from ..sensors.colors import NAMED_COLORS
from .conditions import IN
from .program import Program, TestFileError, found_setup


COLOR_NAMES = list(NAMED_COLORS)     # a color sensor's signal is its color's index here (palette's sensor_colors)


def sensor_value(world, name: str) -> float:
    """Sensor `name`'s chart signal now: 0 or 1 for a beam break (1 broken), inches for a distance sensor, for a
    color sensor the index in NAMED_COLORS of the color it sees (NaN: none), and for a camera how many tags it sees."""
    kind = next(s.kind for s in world.sensors.specs if s.name == name)
    r = world.sensors.reading(name)
    if kind == "beam":
        return 1.0 if r.on else 0.0
    if kind == "distance":
        return r.distance / IN
    if kind == "camera":
        return float(len(r.tags))
    return float(COLOR_NAMES.index(r.color_name)) if r.on else math.nan


def setup_config(raw: dict, setup: dict, cfg_dir: Path | None = None) -> dict:
    """A copy of the config dict with the test's `[setup]` ball or piece, station settings and seed in its `[range]`
    (a station's settings add to, and replace, the config's own; a ball or piece replaces the config's own, whichever
    it gave), and its partner as a `[partner]` (setup_partner). Nothing to set leaves `raw` as it is. On the plain
    field (no `[range]`) the seed is dropped: it only picks the stations' draws, and there are none. `cfg_dir` is the
    config's folder, where a partner's robot start reads the config's own range file from."""
    raw = _range_setup(raw, setup)
    if "partner" in setup:
        raw = setup_partner(raw, setup["partner"], cfg_dir)
    return raw


def setup_partner(raw: dict, partner, cfg_dir: Path | None) -> dict:
    """A copy of the config dict with Setup's partner: false drops the config's `[partner]`; a table puts the
    partner's center `ahead_in` ahead of the robot's start along its heading, turned `heading_deg` from it, its brake
    as given (the config's `[partner]` keeps its other settings)."""
    if partner is False:
        return {k: v for k, v in raw.items() if k != "partner"}
    from ..parts.assemble import robot_start
    try:
        x, y, heading = robot_start(raw, cfg_dir)
    except ValueError as e:     # the config's own mistake (its [range], its start_pose), which building it says too
        raise TestFileError(i18n.t("Setup's partner needs the robot's start: {why}", why=e)) from None
    h, ahead = math.radians(heading), partner["ahead_in"] * IN
    have = raw.get("partner")
    out = {**(have if isinstance(have, dict) else {}),
           "start": [x + ahead * math.cos(h), y + ahead * math.sin(h), heading + partner.get("heading_deg", 0.0)]}
    if "brake" in partner:
        out["brake"] = partner["brake"]
    return {**raw, "partner": out}


def _range_setup(raw: dict, setup: dict) -> dict:
    """setup_config's `[range]` part: the ball or piece, the station settings and the seed."""
    values = {k: setup[k] for k in ("ball", "piece", "station", "seed") if k in setup}
    if not isinstance(raw.get("range"), dict):
        values.pop("seed", None)
        if values:          # palette.problems says so too, so Setup shows it before Run
            raise TestFileError(i18n.t("Setup's ball, piece and station settings need a field; this robot is on the "
                                       "plain field"))
    if not values:
        return raw
    rng = dict(raw["range"])
    if "ball" in values or "piece" in values:      # the test's one choice, never both (load_range refuses both)
        rng.pop("ball", None)
        rng.pop("piece", None)
    for key, v in values.items():
        if key == "station":
            have = rng.get("station")
            have = have if isinstance(have, dict) else {}
            rng[key] = {**have, **{n: {**(have[n] if isinstance(have.get(n), dict) else {}), **t}
                                   for n, t in v.items()}}
        else:
            rng[key] = v
    return {**raw, "range": rng}


def world_for(program: Program):
    """The World of the test's robot config (its `robot`, relative to the test file), with the test's setup applied.
    When Setup places the partner and it starts inside the robot or an element, the TestFileError says Setup's partner
    ("the partner robot (Setup) starts inside the robot (their outlines overlap by 7.87 in)", in the Units setting;
    machine text keeps the old 0.20 m), not the config's [partner]: the student only ticked Setup."""
    from ..config import ConfigError, build_world, load_config
    config = load_config(Path(program.path).parent / program.robot)
    try:
        return build_world(replace(config, raw=setup_config(config.raw, found_setup(program), config.dir)))
    except ConfigError as e:
        overlap = partner_overlap(e)
        if not isinstance(program.setup.get("partner"), dict) or overlap is None:
            raise
        raise TestFileError(setup_partner_words(overlap)) from None


def partner_overlap(e: BaseException) -> PartnerOverlap | None:
    """The PartnerOverlap a ConfigError came from (build_world re-raises partner.check_start's `from None`, so it is
    the error's context), found by its type, never by its words (L3); None for any other error."""
    seen = 0
    cause = e
    while cause is not None and seen < 8:
        if isinstance(cause, PartnerOverlap):
            return cause
        cause, seen = cause.__cause__ or cause.__context__, seen + 1
    return None


def setup_partner_words(overlap: PartnerOverlap) -> str:
    """Setup's partner refusal from the config's PartnerOverlap: in the Units setting ("... by 7.87 in)"), or in
    machine text in meters, as the config's own refusal was before the setting ("... by 0.20 m)")."""
    by = f"{overlap.depth:.2f} m" if units.is_machine() else units.fmt(overlap.depth, "length", fewer=1)
    return i18n.t("the partner robot (Setup) starts inside {what} (their outlines overlap by {by})",
                  what=overlap.what, by=by)
