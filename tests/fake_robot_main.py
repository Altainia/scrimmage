"""A robot-program stand-in for Session tests: connects like halsim_ws_client (optionally playing the lockstep
extension) and prints a little, so console capture has something to show.

    python fake_robot_main.py [--lockstep] [--stall-after N] [--exit-after N] [--release-after N] [--phoenix] [--foreign-after N] [--drive] [--climb FILE]
                              [--shoot [--shooter-pwm N] [--shooter-output X] [--feed-pwm N] [--feed-output X] [--feed-at S] [--step S]]

--shoot: a shooter's open-loop auto (configs/disc_robot.toml). While autonomous is enabled, PWM --shooter-pwm is held at
--shooter-output (a fraction of the battery voltage, as a PWM motor controller's speed), and from --feed-at seconds into
auto PWM --feed-pwm (the loader) runs at --feed-output, feeding the hopper's pieces. Auto time counts robot loops of
--step seconds (the config's [lockstep] step). Outside auto both are 0.

--climb FILE plays a timed setpoint climb (tests/climb_timed.py writes them, e.g. examples/climb/high_timed.toml):
from the first robot loop enabled in teleop (the endgame), each joint's setpoint at that time, held by a P loop on
the joint's quadrature encoder and driven on its PWM motor; relative paths are from the working directory.

Env SCRIMMAGE_FAKE_INPUT_LOG: a file that gets one JSON line of DS inputs per step request.
Env SCRIMMAGE_AUTO: printed as "SCRIMMAGE_AUTO=<value>" each time autonomous is enabled (as a robot picking its auto).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_robot import FakeRobot  # noqa: E402
from scrimmage.protocol import frame  # noqa: E402


class Robot(FakeRobot):
    in_auto = False
    shoot: argparse.Namespace | None = None   # the --shoot settings, or None
    auto_loops = 0                            # robot loops since autonomous was enabled
    feeding = False

    async def _loop_body(self, seq: int) -> None:
        auto = bool(self.ds.get(">enabled") and self.ds.get(">autonomous"))
        if auto and not self.in_auto:
            self.auto_loops = 0
            if "SCRIMMAGE_AUTO" in os.environ:
                print(f"SCRIMMAGE_AUTO={os.environ['SCRIMMAGE_AUTO']}", flush=True)
        self.in_auto = auto
        await super()._loop_body(seq)
        if self.shoot is not None:
            await self._shoot(auto)

    async def _shoot(self, auto: bool) -> None:
        """The shooter at a fixed output through auto; the loader on from --feed-at seconds in."""
        a = self.shoot
        t = self.auto_loops * a.step if auto else 0.0
        feed = auto and t >= a.feed_at - 1e-9
        if feed and not self.feeding:
            print(f"shoot: feeding at {t:.3f} s into auto", flush=True)
        self.feeding = feed
        for pwm, value in ((a.shooter_pwm, a.shooter_output if auto else 0.0),
                           (a.feed_pwm, a.feed_output if feed else 0.0)):
            await self.ws.send(frame("PWM", str(pwm), {"<init": True, "<speed": value}))
        if auto:
            self.auto_loops += 1


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lockstep", action="store_true")
    ap.add_argument("--stall-after", type=int)
    ap.add_argument("--exit-after", type=int)
    ap.add_argument("--release-after", type=int)
    ap.add_argument("--phoenix", action="store_true")
    ap.add_argument("--foreign-after", type=int)
    ap.add_argument("--drive", action="store_true")
    ap.add_argument("--shoot", action="store_true")
    ap.add_argument("--shooter-pwm", type=int, default=2)
    ap.add_argument("--shooter-output", type=float, default=1.0)
    ap.add_argument("--feed-pwm", type=int, default=3)
    ap.add_argument("--feed-output", type=float, default=1.0)
    ap.add_argument("--feed-at", type=float, default=1.5)
    ap.add_argument("--step", type=float, default=0.005)
    ap.add_argument("--climb", type=Path)
    args = ap.parse_args()
    robot = Robot(int(os.environ["HALSIMWS_PORT"]), lockstep=args.lockstep, stall_after=args.stall_after,
                      exit_after=args.exit_after, verbose=True,
                      release_after=args.release_after, phoenix=args.phoenix, foreign_after=args.foreign_after,
                      drive=args.drive, climb=args.climb,
                      input_log=Path(os.environ["SCRIMMAGE_FAKE_INPUT_LOG"]) if os.environ.get("SCRIMMAGE_FAKE_INPUT_LOG") else None)
    if args.shoot:
        robot.shoot = args
    async with robot:
        print("fake robot connected", flush=True)
        await robot.closed()


asyncio.run(main())
