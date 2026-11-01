"""Run a disc setup, or every combination of its `[sweep]` table: the ball launcher's sweep (launcher/sweep.py),
given the disc's loading, parsing, overrides and runner.

A dotted key `control.target_rps` sets that key in every table of a per-group `[[control]]` list.
"""

import copy
from functools import partial
from pathlib import Path

from ..launcher import sweep as ball
from .inputs import _SECTIONS, DiscConfigError, DiscSetup, load_setup, parse_setup
from .run import RunResult, run_setup


def apply_overrides(raw: dict, overrides: dict[str, object]) -> dict:
    """A deep copy of `raw` with dotted keys (`layout.compression`, or `dt`) set to the given values."""
    out = copy.deepcopy(raw)
    for dotted, value in overrides.items():
        if dotted == "dt":
            out["dt"] = copy.deepcopy(value)
            continue
        section, _, key = dotted.partition(".")
        if section not in _SECTIONS or not key:
            raise DiscConfigError(f"[sweep] {dotted!r} is not section.key; sections: {', '.join(_SECTIONS)}, or dt")
        tables = out.setdefault(section, {})
        for table in tables if isinstance(tables, list) else [tables]:
            table[key] = copy.deepcopy(value)
    return out


_disc = dict(load=load_setup, parse=parse_setup, overrides=apply_overrides, error=DiscConfigError)


def setups_for(path: Path) -> list[tuple[dict, DiscSetup]]:
    """Every combination of the file's `[sweep]`, parsed before any runs."""
    _, sweep, _ = load_setup(path)
    for dotted, values in sweep.items():
        if not isinstance(values, list) or not values:
            raise DiscConfigError(f"[sweep] {dotted} must be a non-empty list of values, not {values!r}")
    return ball.setups_for(path, **_disc)


def run_all(path: Path) -> tuple[list[tuple[dict, RunResult]], list[DiscSetup]]:
    return ball.run_all(path, run=run_setup, **_disc)


run_one = partial(ball.run_one, load=load_setup, run=run_setup)
