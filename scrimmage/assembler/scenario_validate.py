"""Candidate scenario text -> the real loader, the robot config's world and `validate` -> errors keyed by GUI field.

Field ids (0-based indices; the runner's messages are 1-based): `scenario/<key>`, `scenario/match.<key>`,
`step<i>/<key>`, `step<i>/check<j>/<kind>` or `step<i>/check<j>/<kind>.<subkey>` when the message names one key
inside the check (`step1/check0/pose.tol_xy`, `step0/check2/always.signal`), `step<i>/shot<j>/<key>`,
`step<i>/sheet/<key>`; anything that can't be placed is `status`. A broken robot config or missing config file
sets `robot_problem` and gives a single `status` error "fix the robot first: ..." (code ROBOT_FIRST, spec L3: the
server words it again with its own robot problem). The loader's and validate's messages stay American English: they
are placed on their fields by their words here, and the CLI prints them."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
import tomllib
from pathlib import Path

from ..checks import ScenarioError
from ..config import ConfigError, build_world, load_config
from ..driverstation import MATCH_KEYS
from ..scenario import (SHEET_KEYS, SHOT_KEYS, STEP_KEYS, TOP_KEYS, config_for, load_scenario_raw,
                        load_scenario_text, validate)
from .. import i18n
from .validate import FieldError, Result as _BaseResult

ROBOT_FIRST = "robot_first"     # FieldError.code of the "fix the robot first" error


@dataclass
class Result(_BaseResult):
    scenario: object = None
    robot_problem: str | None = None


_UNKNOWN = re.compile(r"unknown key '([^']*)'")
_WORD = re.compile(r"([A-Za-z_]+)")


def _strip_path(message: str, path) -> str:
    for p in {str(path), str(Path(path).resolve())}:
        if message.startswith(p + ": "):
            return message[len(p) + 2:]
    return message


def _key_in(rest: str, keys) -> str | None:
    """The key a message is about: `unknown key 'k'`, or a message that starts with one of `keys`."""
    m = _UNKNOWN.search(rest)
    if m:
        return m.group(1)
    m = _WORD.match(rest)
    return m.group(1) if m and m.group(1) in keys else None


def _prefix(where: str, i: int, s) -> str:
    """The `<file>, step N (name)` text the loader puts in front of most of a step's messages."""
    if isinstance(s, dict) and "name" in s:
        return f"{where}, step {i} ({str(s['name'])})"
    return f"{where}, step {i}"


def _shot_key(tail: str) -> str:
    key = _key_in(tail, SHOT_KEYS)
    if key:
        return key
    if tail.startswith(("unknown camera key", "the free view")):
        return "camera"
    return "view"


def _sheet_key(tail: str) -> str:
    key = _key_in(tail, SHEET_KEYS)
    if key:
        return key
    return "frames" if "pixels" in tail or "frames" in tail else "view"


def _map_load_error(message: str, path, raw: dict) -> list[FieldError]:
    where = Path(path).resolve().name
    message = _strip_path(message, path)
    if message.startswith(where + ": "):
        rest = message[len(where) + 2:]
        if rest.startswith("[match] "):
            key = _key_in(rest[len("[match] "):], MATCH_KEYS)
            return [FieldError(f"scenario/match.{key}" if key else "scenario/match", message)]
        key = _key_in(rest, TOP_KEYS)
        if key:
            return [FieldError(f"scenario/{key}", message)]
        if rest.startswith("needs at least one"):
            return [FieldError("status", message)]
        return [FieldError("scenario/speed", message)]      # the only other top-level message is parse_speed's
    steps = raw.get("step", []) if isinstance(raw.get("step"), list) else []
    m = re.match(re.escape(where) + r", step (\d+)\b", message)
    if m and 1 <= int(m.group(1)) <= len(steps):
        i = int(m.group(1))
        s = steps[i - 1]
        named = _prefix(where, i, s)     # the unknown-key check runs before the loader has read the step's name
        pre = named if message.startswith(named) else m.group(0)
        rest = message[len(pre):]
        sid = f"step{i - 1}"
        m = re.match(r", screenshot (\d+)(.*)", rest, re.S)
        if m:
            return [FieldError(f"{sid}/shot{int(m.group(1)) - 1}/{_shot_key(m.group(2).lstrip(': '))}", message)]
        m = re.match(r", contact_sheet(.*)", rest, re.S)
        if m:
            return [FieldError(f"{sid}/sheet/{_sheet_key(m.group(1).lstrip(': '))}", message)]
        m = re.match(r", check (\d+), (\w+): (.*)", rest, re.S)     # a check's own structure (by_time's t)
        if m:
            key = _key_in(m.group(3), {"t"})
            return [FieldError(f"{sid}/check{int(m.group(1)) - 1}/{m.group(2)}" + (f".{key}" if key else ""), message)]
        tail = rest.lstrip(": ")
        m = re.match(r"check (\d+) must be", tail)
        if m:
            return [FieldError(f"{sid}/check{int(m.group(1)) - 1}/check", message)]
        if "[[step.check]]" in tail or tail.startswith("checks are"):
            checks = s.get("check") if isinstance(s.get("check"), list) else []
            j = next((n for n, c in enumerate(checks) if isinstance(c, dict) and len(c) != 1), None)
            return [FieldError(f"{sid}/check{j}/check" if j is not None else f"{sid}/check", message)]
        if "[[step.screenshot]]" in tail or tail.startswith("screenshots are"):
            return [FieldError(f"{sid}/shot", message)]
        key = _key_in(tail, STEP_KEYS)
        return [FieldError(f"{sid}/{key}" if key else "status", message)]
    return [FieldError("status", message)]


def _map_world_error(message: str, step_i: int, ctx: tuple) -> FieldError:
    """`message` is from `validate` run on one isolated item; `ctx` says which: ('check', j, spec), ('shot', j)
    or ('sheet',)."""
    sid = f"step{step_i}"
    if ctx[0] == "check":
        j, spec = ctx[1], ctx[2]
        kind = next(iter(spec), "check")
        fid = f"{sid}/check{j}/{kind}"
        rest = message.split(f"{kind}: ", 1)[-1]
        val = spec.get(kind)
        if isinstance(val, dict):
            about_signal = rest.startswith(("unknown signal", "signal ")) or "needs a robot on a test range" in rest
            sub = "signal" if about_signal else _key_in(rest, val.keys())
            if sub:
                fid += f".{sub}"
        return FieldError(fid, message)
    if ctx[0] == "shot":
        j = ctx[1]
        tail = message.split("screenshot 1: ", 1)[-1]   # the isolated step has just this one shot
        return FieldError(f"{sid}/shot{j}/{_shot_key(tail)}", message)
    return FieldError(f"{sid}/sheet/view", message)


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
            validate(dataclasses.replace(scn, steps=[one]), world)
        except ScenarioError as e:
            msg = str(e).replace(f"step 1 ({one.name})", f"step {i + 1} ({step.name})", 1)
            errors.append(_map_world_error(msg, i, ctx))
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
        errors += _map_load_error(str(e), path, raw)
    steps_raw = raw.get("step")
    if not isinstance(steps_raw, list) or not steps_raw:
        errors.append(FieldError("status", f"{where}: needs at least one [[step]]"))
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
            one = load_scenario_raw({**stand_in_top, "step": [s]}, path)
        except ScenarioError as e:
            msg = str(e)
            if msg.startswith(f"{where}, step 1"):
                msg = f"{where}, step {i + 1}" + msg[len(f"{where}, step 1"):]
            errors += _map_load_error(msg, path, raw)
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
        return Result(False, _map_load_error(f"{path.resolve()}: {e}", path, {}), [], None)
    try:
        load_errors, base, loaded = _load_errors(raw, path)
        scn = load_scenario_text(text, path) if not load_errors else None
    except ScenarioError as e:   # a load error the per-part checks missed
        return Result(False, _map_load_error(str(e), path, raw), [], None)
    except Exception as e:
        return Result(False, [FieldError("status", f"{type(e).__name__}: {e}")], [], None)
    if base is None:
        return Result(False, load_errors, [], None)
    try:
        config = load_config(base.config_path)
        try:
            config = config_for(base, config)     # the scenario's [range] over the robot's
        except ScenarioError as e:
            key = "robot_env" if ": robot_env " in str(e) else "range"
            return Result(False, load_errors + [FieldError(f"scenario/{key}", str(e))], [], None)
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
