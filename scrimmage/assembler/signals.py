"""The names a scenario can refer to, for the editor's pickers."""

from __future__ import annotations

import mujoco


def signals(world) -> dict[str, list[str]]:
    """Pickable names. `motors` are `motor.<name>` and `joints` `joint.<name>`; the scenario signal is that plus
    `.current`/`.command` or `.position`/`.velocity`. `contacts` are geom/body names for never_touch/must_touch and
    `cameras` the model's camera names (a view is `camera:<name>`). `fixed` has the robot's terrain signals
    (robot.*), less the wheel ones for a robot without tread wheels. On a test range, `range` lists its signals as
    they are (score, score.<goal> per goal, fired, held, on_field, element.<platform>.angle per platform,
    element.<name> per hinged or sliding element). The climb signals, for a robot with hooks (a stock climber) or on
    a range with a bar or a switch: climb.extension and climb.height in `fixed`, with climb.hook_load and
    climb.hook.<hook>.load per hook; climb.hanging, level, points and swing in `range`, with a bar or a switch. (A
    scenario may use extension and height on any robot.)"""
    from ..terrain.climb import robot_hooks
    from ..terrain.monitor import ROBOT_SIGNALS, WHEEL_SIGNALS
    from ..testrange.elements import Platform, moving_units
    m = world.model
    cams = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_CAMERA, i) for i in range(m.ncam)]
    parts = getattr(world, "parts", None)
    treads = bool(parts is not None and parts.tread_geoms)
    hooks = list(robot_hooks(m, world.robot_body))
    layout = parts.layout if parts is not None else None
    holds = layout is not None and bool(layout.bars or layout.switches)
    fixed = ["battery", "speed", *(f"robot.{f}" for f in ROBOT_SIGNALS if treads or f not in WHEEL_SIGNALS)]
    if hooks or holds:
        fixed += ["climb.extension", "climb.height"]
    if hooks:
        fixed += ["climb.hook_load", *(f"climb.hook.{h}.load" for h in hooks)]
    out = {"fixed": fixed,
           "motors": [f"motor.{b.name}" for b in world.motors],
           "joints": [f"joint.{j}" for j in sorted(world.joint_states())],
           "contacts": list(world.contact_names()),
           "cameras": [c for c in cams if c]}
    rng = getattr(world, "test_range", None)
    if rng is not None:
        out["range"] = ["score", *(f"score.{g.name}" for g in rng.range.goals), "fired", "held", "on_field",
                        *(f"element.{e.name}.angle" for e in rng.range.elements if isinstance(e, Platform)),
                        *(f"element.{n}" for n in moving_units(rng.range.elements))]
        if holds:
            out["range"] += ["climb.hanging", "climb.level", "climb.points", "climb.swing"]
    return out
