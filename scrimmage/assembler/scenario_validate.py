"""Candidate scenario text -> the real loader, the robot config's world and `validate` -> errors keyed by GUI field.

Field ids (0-based indices; the runner's messages are 1-based): `scenario/<key>`, `scenario/match.<key>`,
`step<i>/<key>`, `step<i>/check<j>/<kind>` or `step<i>/check<j>/<kind>.<subkey>` when the message names one key
inside the check (`step1/check0/pose.tol_xy`, `step0/check2/always.signal`), `step<i>/shot<j>/<key>`,
`step<i>/sheet/<key>`; anything that can't be placed is `status`. A broken robot config or missing config file
sets `robot_problem` and gives a single `status` error "fix the robot first: ..." (code ROBOT_FIRST, spec L3: the
server words it again with its own robot problem). The loader's and validate's messages are placed on their fields
by the field or key each ScenarioError carries (spec L3), never by their words, so they are in the Language setting;
`detail` is a message without its "x.toml, step 2 (auto), check 1: " where, shown beside its field."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
import tomllib
from pathlib import Path

from ..checks import ScenarioError
from ..config import ConfigError, build_world, load_config
from ..scenario import config_for, load_scenario_raw, load_scenario_text, validate
from .. import i18n
from .validate import FieldError, Result as _BaseResult

ROBOT_FIRST = "robot_first"     # FieldError.code of the "fix the robot first" error


@dataclass
class Result(_BaseResult):
    scenario: object = None
    robot_problem: str | None = None


def _strip_path(message: str, path) -> str:
    for p in {str(path), str(Path(path).resolve())}:
        if message.startswith(p + ": "):
            return message[len(p) + 2:]
    return message


def _load_error(e: ScenarioError, path) -> FieldError:
    """A loader's error on the field it names (ScenarioError.field: the loader knows where it is, so nothing here
    reads its words, spec L3), else on the status line."""
    return FieldError(e.field or "status", _strip_path(str(e), path), detail=e.detail)


def _world_error(e: ScenarioError, step_i: int, ctx: tuple) -> FieldError:
    """An error from `validate` run on one isolated item, on that item's field; `ctx` says which: ('check', j,
    spec), ('shot', j) or ('sheet',). The key inside it is the one the error names (ScenarioError.key)."""
    sid = f"step{step_i}"
    if ctx[0] == "check":
        j, spec = ctx[1], ctx[2]
        kind = next(iter(spec), "check")
        fid = f"{sid}/check{j}/{kind}"
        if isinstance(spec.get(kind), dict) and e.key:
            fid += f".{e.key}"
        return FieldError(fid, str(e), detail=e.detail)
    if ctx[0] == "shot":
        return FieldError(f"{sid}/shot{ctx[1]}/{e.key or 'view'}", str(e), detail=e.detail)
    return FieldError(f"{sid}/sheet/view", str(e), detail=e.detail)


def _step_world_errors(scn, i: int, step, world) -> list[FieldError]:
    """Every name-vs-world problem in `step` (the file's step `i`, 0-based), each placed on its own field
    (validate stops at the first, so ask per item)."""
    errors: list[FieldError] = []
    items = [(dataclasses.replace(step, checks=[c], screenshots=[], contact_sheet=None), ("check", j, c))
             for j, c in enumerate(step.checks)]
    items += [(dataclasses.replace(step, checks=[], screenshots=[sh], contact_sheet=None), ("shot", j))
              for j, sh in enumerate(step.screenshots)]
    if step.contact_sheet is not None:
        items.append((dataclasses.replace(step, checks=[], screenshots=[]), ("sheet",)))
    for one, ctx in items:
        try:
            validate(dataclasses.replace(scn, steps=[one]), world, first_step=i + 1)
        except ScenarioError as e:
            errors.append(_world_error(e, i, ctx))
    return errors


_STAND_IN_STEP = {"mode": "disabled", "duration": 1.0}


def _load_errors(raw: dict, path: Path):
    """Every load error, not just the loader's first: the top level alone (with a stand-in step), then each
    [[step]] alone (with a stand-in top level). Returns (errors, base, steps): `base` is a one-step Scenario
    carrying the top level (None when `config` isn't a string) and `steps` is [(index, Step)] for the steps that
    loaded."""
    where = path.resolve().name
    errors: list[FieldError] = []
    top = {k: v for k, v in raw.items() if k != "step"}
    cfg = raw.get("config")
    try:
        load_scenario_raw({**top, "step": [_STAND_IN_STEP]}, path)
    except ScenarioError as e:
        errors.append(_load_error(e, path))
    steps_raw = raw.get("step")
    if not isinstance(steps_raw, list) or not steps_raw:
        errors.append(FieldError("status", i18n.t("{file}: needs at least one [[step]]", file=where),
                                 detail=i18n.t("needs at least one [[step]]")))
        steps_raw = []
    base = None
    if isinstance(cfg, str):
        try:
            base = load_scenario_raw({**top, "step": [_STAND_IN_STEP]}, path)
        except ScenarioError:
            base = load_scenario_raw({"config": cfg, "step": [_STAND_IN_STEP]}, path)
    loaded = []
    stand_in_top = {"config": cfg if isinstance(cfg, str) else "robot.toml"}
    for i, s in enumerate(steps_raw):
        try:
            one = load_scenario_raw({**stand_in_top, "step": [s]}, path, first_step=i + 1)
        except ScenarioError as e:
            errors.append(_load_error(e, path))
            continue
        loaded.append((i, one.steps[0]))
    return errors, base, loaded


def check(text: str, path) -> Result:
    """Never raises for bad scenario text, a bad robot config or a bad name. Lists every step's errors (a step's
    load errors stop at its first, as the loader does), so an edit that adds an error anywhere adds to the count."""
    path = Path(path)
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return Result(False, [FieldError("status", _strip_path(f"{path.resolve()}: {e}", path))], [], None)
    try:
        load_errors, base, loaded = _load_errors(raw, path)
        scn = load_scenario_text(text, path) if not load_errors else None
    except ScenarioError as e:   # a load error the per-part checks missed
        return Result(False, [_load_error(e, path)], [], None)
    except Exception as e:
        return Result(False, [FieldError("status", f"{type(e).__name__}: {e}")], [], None)
    if base is None:
        return Result(False, load_errors, [], None)
    try:
        config = load_config(base.config_path)
        try:
            config = config_for(base, config)     # the scenario's [range] over the robot's
        except ScenarioError as e:
            return Result(False, load_errors + [FieldError(e.field or "scenario/range", str(e), detail=e.detail)], [],
                          None)
        world = build_world(config)
    except Exception as e:
        if load_errors:   # the scenario's own errors first; the robot is looked at once they are fixed
            return Result(False, load_errors, [], None)
        problem = str(e) if isinstance(e, ConfigError) else f"{type(e).__name__}: {e}"
        return Result(False, [FieldError("status", i18n.t("fix the robot first: {why}", why=problem), ROBOT_FIRST)], [],
                      None, scn, problem)
    errors = list(load_errors)
    try:
        for i, step in loaded:
            errors += _step_world_errors(base, i, step, world)
        errors.sort(key=_order)
        if not errors:
            validate(scn, world)
    except ScenarioError as e:
        errors = [FieldError("status", str(e))]
    except Exception as e:
        errors = [FieldError("status", f"{type(e).__name__}: {e}")]
    return Result(not errors, errors, [FieldError("status", w) for w in world.warnings], world, scn, None)


def _order(e: FieldError):
    """Top-level errors, then step by step; sorted() keeps each step's own order."""
    m = re.match(r"step(\d+)/", e.field)
    return (1, int(m.group(1))) if m else ((0, 0) if e.field.startswith("scenario/") else (2, 0))
