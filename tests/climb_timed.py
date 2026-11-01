"""Turn a climb script and its `climb run` into a timed setpoint script for the fake robot's --climb mode
(tests/fake_robot_main.py).

    .venv/bin/python tests/climb_timed.py configs/climber_robot.toml examples/climb/high.toml examples/climb/high_timed.toml

A robot program sees only HAL, not contacts, so it can't wait for "the hooks touch HIGH" as a climb script does. It
can do what a team's endgame code does: at fixed times, set each mechanism's setpoint and hold it with a position
loop on the mechanism's own encoder. This writes that program's data:
- `[[joint]]`: each joint the script drives, with its motor (`pwm`, `inverted` as the config's binding), its encoder
  (`encoder`, the DIO channel_a of a quadrature Encoder; `counts_per_unit`, counts per m or rad, the encoder's sign
  included) and the loop's gain `kp` in volts per m or rad: the runner's own (KP_SLIDE, KP_HINGE), so the robot's
  loops are the runner's;
- `[[setpoint]]`: from each script step's drives, at the time the step started in the `climb run`, i.e. when its
  wait fired: `to` (a position, with `max_voltage` if the step capped it) or `voltage` (open loop).
The timings are the climb run's; everything else is closed loop, so a small change in the physics or a few ms of
timing doesn't change the climb (tests/test_climb_scenario.py shifts every time by +-20 ms).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from scrimmage.climb.run import KP_HINGE, KP_SLIDE, ClimbRun, run_climb
from scrimmage.climb.script import ScriptError, load_script
from scrimmage.config import build_world, load_config


def joints_table(config: Path, script) -> list[dict]:
    """The [[joint]] entries: each driven joint's PWM motor, quadrature encoder and gain."""
    world = build_world(load_config(config))
    run = ClimbRun(world, script)
    encoders = {int(e.qadr): e for e in world.encoders if e.kind == "Encoder"}
    out = []
    for name in dict.fromkeys(dr.joint for st in script.steps for dr in st.drives):
        motor = run.motors[name]
        enc = encoders.get(int(motor.qadr))
        if motor.kind != "PWM" or enc is None:
            raise ScriptError(f"{config}: joint {name!r} needs a PWM motor and a quadrature encoder (channel_a) on "
                              "the same joint for the fake robot to drive it")
        per = 1.0 if enc.is_slide else 2.0 * math.pi
        out.append({"name": name, "pwm": int(motor.device_key[1]), "inverted": motor.sign < 0,
                    "encoder": enc.channel_a, "counts_per_unit": enc.cpr * enc.ratio * enc.sign / per,
                    "kp": KP_SLIDE if enc.is_slide else KP_HINGE, "unit": "m" if enc.is_slide else "rad"})
    return out


def timed_text(config: Path, script_path: Path, cmd: str) -> tuple[str, object]:
    script = load_script(script_path)
    if any(st.release for st in script.steps):
        raise ScriptError(f"{script_path}: the fake robot's --climb mode has no latch release; drop the release")
    joints = joints_table(config, script)
    result = run_climb(config, script)
    if not result.reached_goal:
        raise ScriptError(f"{script_path}: the climb run ended {result.status!r}; fix the climb before timing it")
    starts = {s.index: s for s in result.steps}
    levels = ", ".join(f"{k} held at {v:.2f} s" for k, v in result.level_times.items())
    lines = [
        "# A timed setpoint climb for the fake robot's --climb mode (tests/fake_robot_main.py): at each setpoint's t",
        "# (s from the first robot loop enabled in teleop) a joint's position loop, on its own encoder, takes a new",
        f"# target. Written by tests/climb_timed.py (its docstring explains it) from {script_path.as_posix()} and the",
        "# times its steps started in `climb run`. Regenerate it after a change to the climb, the robot or the physics:",
        f"#   {cmd}",
        "#",
        f"# The climb run ended \"{result.status}\" at {result.duration:.2f} s; {levels}; peak hook load "
        f"{result.peak_hook_load:.0f} N.",
        "",
        f"loop = {load_config(config).step:g}          # s: the robot loop (the config's [lockstep] step)",
        "",
    ]
    for j in joints:
        lines += ["[[joint]]",
                  f'name = "{j["name"]}"',
                  f'pwm = {j["pwm"]}' + ("\ninverted = true" if j["inverted"] else ""),
                  f'encoder = {j["encoder"]}            # the quadrature Encoder\'s DIO channel_a',
                  f'counts_per_unit = {j["counts_per_unit"]:.6g}   # counts per {j["unit"]}',
                  f'kp = {j["kp"]:g}               # V per {j["unit"]} of error, clipped to the battery',
                  ""]
    for st in script.steps:
        s = starts.get(st.index)
        if s is None or not st.drives:
            continue
        lines.append(f"# step {st.index}: {st.name} (ended by {s.how} at {s.end:.3f} s)")
        for dr in st.drives:
            lines += ["[[setpoint]]", f"t = {s.start:.3f}", f'joint = "{dr.joint}"']
            if dr.to is not None:
                lines.append(f"to = {dr.to:.6g}")
                if dr.max_voltage is not None:
                    lines.append(f"max_voltage = {dr.max_voltage:g}")
            else:
                lines.append(f"voltage = {dr.voltage:g}")
        lines.append("")
    return "\n".join(lines), result


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    config, script, out = (Path(a) for a in argv)
    text, result = timed_text(config, script, f".venv/bin/python tests/climb_timed.py {' '.join(argv)}")
    out.write_text(text)
    print(f"{out}: {result.status} at {result.duration:.2f} s; levels {result.level_times}; "
          f"peak hook load {result.peak_hook_load:.0f} N")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
