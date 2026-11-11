"""Robots on the test range for tests: a config builder and a PWM drive loop (no robot program)."""

from __future__ import annotations

import copy
from pathlib import Path

from scrimmage.protocol import HalState

# The kitbot of tests/test_part_physics.py: a chassis on a PWM differential drive (left PWM 0, right PWM 1, the
# right side inverted).
KITBOT = [{"name": "drive", "stock": "chassis", "length": 0.66, "width": 0.6, "mass": 47.0, "frame_height": 0.1},
          {"name": "dt", "stock": "differential", "attach": "drive.center", "ids": {"left": "PWM:0", "right": "PWM:1"}}]
# TEST_WORLD's stiff battery (tests/test_part_physics.py): the range turns the center obstacle off itself.
BATTERY = {"nominal_voltage": 12.0, "resistance": 0.0}
# A two-lane hooded launcher on the kitbot's top mount: the shooter on PWM 2, the hood on PWM 3. Wide enough for
# two 2026 FUEL lanes at +-0.08 m (each reaches 0.08 + 0.075 = 0.155 m from the center).
LAUNCHER = {"name": "launcher", "stock": "launcher", "attach": "drive.top", "pos": [-0.1, 0.0, 0.35], "width": 0.36,
            "ids": {"shooter": "PWM:2", "hood": "PWM:3"}}
# Its ball path: a hopper floor (PWM 5) and a feeder (PWM 6), rollers 0.05 m across geared 10:1 (a surface speed of
# about 1.6 m/s at full output), and a roller intake (PWM 7) whose zone reaches past the front bumper (the chassis's
# front bumper face is 0.41 m ahead of its center).
STAGE_PARTS = [{"name": "hopper_floor", "stock": "roller", "attach": "drive.top", "pos": [0.1, 0.0, 0.1],
                "gearing": 10.0, "ids": {"spin": "PWM:5"}},
               {"name": "feeder", "stock": "roller", "attach": "drive.top", "pos": [-0.05, 0.0, 0.2],
                "gearing": 10.0, "ids": {"spin": "PWM:6"}},
               {"name": "intake", "stock": "roller", "attach": "drive.center", "pos": [0.36, 0.0, 0.2],
                "gearing": 10.0, "ids": {"spin": "PWM:7"}}]
SHOOTER = {"part": "launcher", "lanes": [-0.08, 0.08],
           "wheels": {"durometer": 35.0, "friction": 1.0, "positions": [-0.08, 0.08], "width": 0.05},
           "hood": {"friction": 0.3}, "air": {"preset": "default"}, "hopper": {"capacity": 4},
           "stage": [{"name": "floor", "part": "hopper_floor", "path_length": 0.40},
                     {"name": "feeder", "part": "feeder", "path_length": 0.25}],
           "intake": {"part": "intake", "at": "drive.center", "pos": [0.45, 0.0, 0.08], "size": [0.15, 0.55, 0.20],
                      "rate": 8.0}}


def range_cfg(**overrides) -> dict:
    """A chassis robot with no shooter on the 2026 range, its floor balls off. Each override is a top-level
    section: a table is merged into the section of that name (a None value removes that key), anything else
    replaces it; `part=[...]` replaces the parts."""
    cfg = {"part": copy.deepcopy(KITBOT), "range": {"file": "2026", "floor_balls": False},
           "battery": dict(BATTERY)}
    for section, value in overrides.items():
        if isinstance(value, dict) and isinstance(cfg.get(section), dict):
            merged = {**cfg[section], **value}
            cfg[section] = {k: v for k, v in merged.items() if v is not None}
        elif value is None:
            cfg.pop(section, None)
        else:
            cfg[section] = copy.deepcopy(value)
    return cfg


def spares_world(cfg: dict):
    """A World on `cfg` built with its floor balls, then reset with them off: spare balls a test can place. A world
    built with floor_balls = false has no floor ball bodies."""
    from scrimmage.world import World
    w = World({**cfg, "range": {**cfg["range"], "floor_balls": True}}, None)
    w.test_range.reset(floor_balls=False)
    return w


def shooter_cfg(launcher: dict | None = None, **overrides) -> dict:
    """range_cfg with the kitbot carrying LAUNCHER (its keys updated by `launcher`) and STAGE_PARTS, and the [shooter]
    SHOOTER; the overrides as range_cfg's (`shooter={...}` merges into SHOOTER, a None value removing that key)."""
    part = {**copy.deepcopy(LAUNCHER), **(launcher or {})}
    shooter = {**copy.deepcopy(SHOOTER), **copy.deepcopy(overrides.pop("shooter", {}))}
    shooter = {k: v for k, v in shooter.items() if v is not None}
    return range_cfg(part=copy.deepcopy(KITBOT) + [part] + copy.deepcopy(STAGE_PARTS), shooter=shooter, **overrides)


def drive(w, seconds, **pwm):
    """Step `w` for `seconds` with PWM speeds given as p<port>=speed."""
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    for _ in range(int(seconds / w.model.opt.timestep)):
        w.step(hal)


def range_2024_with_tags(tmp_path) -> Path:
    """The built-in 2024 range, which has `apriltags = "2024-crescendo"` (K4); `tmp_path` is unused, kept for callers."""
    from scrimmage.testrange.spec import BUILTIN_RANGES
    return Path(BUILTIN_RANGES["2024"])
