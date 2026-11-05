"""Run a launcher setup, or every combination of its `[sweep]` table."""

import dataclasses
import itertools
import tomllib
from pathlib import Path

from .. import i18n
from .inputs import LauncherConfigError, Setup, apply_overrides, load_setup, measured_raw, parse_setup
from .report import label
from .run import RunResult, run_setup


def combinations(sweep: dict[str, list]) -> list[dict[str, object]]:
    """The cartesian product of the swept values, the first key varying slowest, in file order."""
    keys = list(sweep)
    return [dict(zip(keys, values)) for values in itertools.product(*(sweep[k] for k in keys))]


def run_one(path: Path, load=load_setup, run=run_setup) -> tuple[list[tuple[dict, RunResult]], list[Setup]]:
    """Run the file's setup once, ignoring any `[sweep]`. `load` and `run` default to the ball launcher's; the disc
    launcher passes its own."""
    setup, _, _ = load(path)
    return [({}, run(setup))], [setup]


def setups_for(path: Path, load=load_setup, parse=parse_setup, overrides=apply_overrides,
               error=LauncherConfigError, measured=measured_raw) -> list[tuple[dict, Setup]]:
    """Parse every combination of the file's `[sweep]`, before any runs: a bad one raises `error` (the launcher's
    LauncherConfigError) prefixed with that combination (`hood.exit_angle_deg=160: ...`). The disc launcher passes
    its own load, parse, overrides and error class, and measured=None. The rows reuse the numbers the setup `load`
    read from its measurement files (no fit runs again); a swept number replaces a measured one, and its provenance
    goes. A swept ball preset must be the one the ball file measured (its base)."""
    base, sweep, _ = load(path)  # validates the setup and the sweep keys
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    provenance, bases = {}, {}
    if measured is not None and getattr(base, "provenance", None):
        raw, bases = measured(raw, base, Path(path))
        provenance = base.provenance
    out = []
    for combo in combinations(sweep):
        for section, (name, measured_base) in bases.items():
            swept = combo.get(f"{section}.preset", measured_base)
            if swept != measured_base:
                raise error(i18n.t("{combo}: [{section}] preset {swept} isn't the {base} ball that measured = "
                                   "\"{name}\" measured: sweep the preset without measured, or measure each ball",
                                   combo=label(combo), section=section, swept=swept, base=measured_base, name=name))
        try:
            setup = parse(overrides(raw, combo))
        except error as e:
            raise error(f"{label(combo)}: {e}") from None
        if provenance:
            setup = dataclasses.replace(setup, provenance={k: p for k, p in provenance.items() if k not in combo})
        out.append((combo, setup))
    return out


def run_all(path: Path, run=run_setup, **kwargs) -> tuple[list[tuple[dict, RunResult]], list[Setup]]:
    """Run every combination; returns the rows and the setups they ran (aligned). Every combination is parsed
    before the first runs. `kwargs` go to `setups_for`."""
    parsed = setups_for(path, **kwargs)
    return [(combo, run(s)) for combo, s in parsed], [s for _, s in parsed]


def run_sweep(path: Path) -> list[tuple[dict, RunResult]]:
    return run_all(path)[0]


__all__ = ["LauncherConfigError", "combinations", "run_all", "run_one", "run_sweep", "setups_for"]
