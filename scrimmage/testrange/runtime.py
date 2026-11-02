"""A range at run time: the World's hook for everything on the test range (its balls, the shooter, the ball path
inside the robot, and the balls the launcher shoots back into the world).

World.step calls before_physics after its motor loop (the supply current it returns goes on the shared battery,
which leaves the shooter's external bindings out of its own total, so the shooter is charged once), then mj_step,
then after_physics (where the intake collects). World.reset calls reset().

Stations: a range with [[station]]s has a Feeder (feeder.py), stepped in before_physics after the launches and before
the step's starting positions are recorded, so a fed ball's first step already moves. Its seed is a setting like
start and preload (reset(seed=...)).

Launches: before_physics steps the launcher model first, and every ball it launched in this step is placed in the
world (launch.py) before the step's starting positions are recorded and the flight forces applied, so its first
step already flies and can score. The launcher model's clock and the world's both start at 0 on reset, but the exit
is placed from the launcher's own time at the step's start (read before it steps), so the two never need to agree:
a ball that left at launcher time t_exit, f of the way through the step, is placed where it would have been at the
step's start, moved back along its velocity, and mj_step carries it to where it is at the step's end. A ball the
launcher finishes with (launched, or stuck) leaves the ball path and takes its new state in the same call, so the
ledger and the ball path's held() always agree.

Sleeping: a range world turns on MuJoCo's sleeping (mjENBL_SLEEP, MuJoCo 3.4 and later), so the balls resting on the
carpet cost almost nothing until something touches them. Only ball trees may sleep (the robot and anything else
never do). A sleeping ball wakes when an awake body touches it, when a force is applied to it, and when its qpos or
qvel is written (parking, placing); checked on MuJoCo 3.4.0 and 3.17.0, and two runs stay bit-identical.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from ..bindings import Update
from ..disc.inputs import DiscAir
from ..launcher.inputs import _build_section
from ..protocol import HalState
from .ballpath import BallPath
from .balls import BallState, Balls, log_dict
from .build import BALL_BITS, LAUNCHED_BITS
from .feeder import Feeder
from .launch import LAUNCH_CLEAR_S, exit_state, exit_state_disc
from .shooter import DiscShooter, Shooter, parse_shooter
from .station import DEFAULT_SEED, check_seed


class RangeRuntime:
    def __init__(self, world, built, cfg: dict, cfg_dir=None):
        self.world = world
        self.range = built.range
        self.layout = built.layout
        sec = cfg.get("range", {})
        self._config = {"start": sec.get("start"), "preload": sec.get("preload", 0),
                        "floor_balls": sec.get("floor_balls", True), "seed": sec.get("seed", DEFAULT_SEED)}
        self._overrides: dict = {}
        m = world.model
        m.opt.enableflags |= int(mujoco.mjtEnableBit.mjENBL_SLEEP)
        m.tree_sleep_policy[:] = int(mujoco.mjtSleepPolicy.mjSLEEP_NEVER)
        ball_trees = m.body_treeid[[m.body(name).id for name in self.layout.ball_names]]
        m.tree_sleep_policy[ball_trees] = int(mujoco.mjtSleepPolicy.mjSLEEP_ALLOWED)
        config = parse_shooter(cfg, built, cfg_dir) if "shooter" in cfg else None
        if config is not None:
            air = config.setup.air
        elif self.range.disc is not None:
            air = DiscAir()             # a disc range without a shooter: the disc model's sea-level air
        else:
            air = _build_section("air", {"preset": "default"})
        self.balls = Balls(m, world.data, self.layout, self.range, air)
        self.disc = config is not None and config.kind == "disc"
        if config is None:
            self.shooter = None
        else:
            self.shooter = DiscShooter(world, config) if self.disc else Shooter(world, config)
        self.ball_path = BallPath(config, self.balls, self.shooter) if config is not None else None
        if config is not None:
            # the frame the launcher model works in: the launcher's base (on its shaft), the disc shooter's channel
            self._base = m.body(f"{config.part}.{'channel' if self.disc else 'base'}").id
        # the stations' feeder, None on a range without stations
        self.feeder = Feeder(self.balls, self.layout, self.range, self._config["seed"]) if self.range.stations else None
        self._prev = np.zeros((self.balls.n, 3))
        self.events: list[tuple[int, str]] = []     # the last step's (ball, "scored" | "out")

    # ------------------------------------------------------------------------------------------------ setup

    def settings(self) -> dict:
        """start, preload, floor_balls and seed: the config's (seed: the [range] seed or DEFAULT_SEED), under the last
        overrides."""
        return {**self._config, **self._overrides}

    def clear_overrides(self) -> None:
        """Back to the config's start, preload, floor_balls and seed at the next reset."""
        self._overrides = {}

    def reset(self, *, start: str | None = None, preload: int | None = None, floor_balls: bool | None = None,
              seed: int | None = None) -> None:
        """Every ball back where the range starts it, the counts and logs cleared, the stations' pools unused and
        their draws restarted from the seed. A value given here overrides the config's [range] key (the seed:
        DEFAULT_SEED) and stays for later plain reset()s (the bridge resets the world when the robot connects) until
        clear_overrides()."""
        given = {"start": start, "preload": preload, "floor_balls": floor_balls, "seed": seed}
        new = {k: v for k, v in given.items() if v is not None}
        if start is not None and start not in self.range.starts:
            raise ValueError(f"start {start!r} isn't a start of range {self.range.name!r}; its starts: "
                             f"{', '.join(self.range.starts) or 'none'}")
        if seed is not None:
            check_seed(seed)
        n_slots = len(self.layout.robot_slots)
        if preload is not None and not 0 <= preload <= n_slots:
            raise ValueError(f"preload {preload}: the robot holds at most {n_slots} balls")
        if floor_balls and self.layout.n_floor == 0 and len(self.range.floor_balls):
            raise ValueError("floor_balls: this world was built with [range] floor_balls = false, so it has no floor "
                             "balls to put out; set floor_balls = true in the config (or the scenario's [range])")
        self._overrides.update(new)
        s = self.settings()
        balls = self.balls
        balls.clear()
        if s["floor_balls"]:
            for i, (x, y) in enumerate(self.range.floor_balls):
                balls.put(i, (float(x), float(y), balls.rest_z), BALL_BITS)
        if s["start"] is not None:
            self.world.set_robot_pose(*self.range.starts[s["start"]])
        if self.shooter is not None:     # its clock restarts with the world's
            self.shooter.reset()
            self.ball_path.reset()
            self.ball_path.preload(s["preload"])
        if self.feeder is not None:
            self.feeder.reset(s["seed"])
        mujoco.mj_forward(self.world.model, self.world.data)
        self.events = []

    def reset_robot(self) -> None:
        """With World.reset_robot (the robot's joints at rest, balls left where they are): the shooter shaft at rest
        in a fresh launcher model, whose balls go back to the front of their lanes' hoppers, so the ledger and the
        robot's count don't change."""
        if self.shooter is None:
            return
        self.ball_path.unload_launcher()
        self.shooter.reset()
        self.shooter.mirror()

    # ------------------------------------------------------------------------------------------------ each step

    def supply_at(self, bus: float) -> float:
        """The supply current (A) the shooter would draw on bus voltage `bus` (World's bus voltage solve)."""
        return self.shooter.supply_at(bus) if self.shooter is not None else 0.0

    def supply_and_slope(self, bus: float) -> tuple[float, float]:
        """supply_at(bus) and its derivative with respect to the bus voltage (A/V)."""
        return self.shooter.supply_and_slope(bus) if self.shooter is not None else (0.0, 0.0)

    def before_physics(self, hal: HalState, vbatt: float, dt: float) -> float:
        """Everything that sets forces before mj_step; the supply current (A) it draws from the battery."""
        current = 0.0
        if self.shooter is not None:
            self._shoot(dt, vbatt)
            current = self.shooter.supply_current()
        if self.feeder is not None:
            self.feeder.step(float(self.world.data.time))
        self._prev[:] = self.balls._qpos[:, :3]     # after the launches and feeds: a placed ball's first step moves
        self.balls.apply_flight_forces()
        self.balls.apply_rolling_resistance()
        return current

    def _shoot(self, dt: float, vbatt: float) -> None:
        """Step the launcher model and the ball path; every ball they finish with leaves the path here: a launched
        ball into the world, any other into `stuck`."""
        launcher = self.shooter.launcher
        t0 = launcher.time                          # the launcher's clock at this world step's start
        path = self.ball_path
        done = [(path.take(shot)[0], shot) for shot in self.shooter.step(dt, vbatt)]
        done += path.step(dt, launcher.time)        # turned away at the entry (no_contact, no_wheel)
        if not done:
            return
        motion = self._robot_motion()
        for i, shot in done:
            if shot.status == "launched":
                self._launch(i, shot, t0, motion)
            else:
                self._stuck(i, shot, motion)

    def _robot_motion(self) -> dict:
        """The robot and the launcher base's pose and velocity now (the kinematics refreshed from qpos and qvel:
        after mj_step data.xpos still holds the step's start; mj_step recomputes all of it, so this changes
        nothing else)."""
        m, d = self.world.model, self.world.data
        mujoco.mj_kinematics(m, d)
        mujoco.mj_comPos(m, d)
        mujoco.mj_comVel(m, d)
        base_vel, robot_vel = np.zeros(6), np.zeros(6)
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_XBODY, self._base, base_vel, 0)
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_XBODY, self.world.robot_body, robot_vel, 0)
        x, y, heading = self.world.robot_pose()
        return {"base_pos": d.xpos[self._base].copy(), "base_mat": d.xmat[self._base].reshape(3, 3).copy(),
                "base_vel": base_vel, "robot_pose": (x, y, math.degrees(heading)),
                "robot_velocity": (float(robot_vel[3]), float(robot_vel[4]), math.degrees(float(robot_vel[2])))}

    def _log_exit(self, i: int, shot, motion: dict) -> None:
        """The shot's firing record: the robot, hood and shooter in this world step, the launcher's result."""
        log = self.balls.log[i]
        log.robot_pose, log.robot_velocity = motion["robot_pose"], motion["robot_velocity"]
        log.hood_deg = self.shooter.angle_deg()
        log.shooter_rps = float(self.shooter.rps())
        log.launcher_status = shot.status

    def _launch(self, i: int, shot, t0: float, motion: dict) -> None:
        """A launched ball (or disc) into the world at the exit point, ignoring the robot for LAUNCH_CLEAR_S. A disc
        is turned to lie in the channel's plane and spins about its axis (launch.exit_state_disc); its log's
        exit_slip is left unset (the disc launcher reports slip energy, not a slip speed) and its spin_rpm is signed,
        positive counter-clockwise seen from above the channel."""
        balls = self.balls
        if self.disc:
            pos, vel, quat, ang_vel = exit_state_disc(shot, motion["base_pos"], motion["base_mat"],
                                                      motion["base_vel"], t0)
            balls.place(i, pos, vel, ang_vel, LAUNCHED_BITS, full_bits_after=LAUNCH_CLEAR_S, quat=quat)
        else:
            pos, vel, ang_vel = exit_state(shot, self.shooter.launcher.r_path, motion["base_pos"],
                                           motion["base_mat"], motion["base_vel"], t0)
            balls.place(i, pos, vel, ang_vel, LAUNCHED_BITS, full_bits_after=LAUNCH_CLEAR_S)
        self._log_exit(i, shot, motion)
        log = balls.log[i]
        log.fired_t = float(self.world.data.time + (shot.t_exit - t0))
        log.exit_speed, log.spin_rpm = float(shot.speed), float(shot.spin * 60.0 / (2.0 * math.pi))
        if not self.disc:
            log.exit_slip = float(shot.exit_slip)
        balls.fired += 1

    def _stuck(self, i: int, shot, motion: dict) -> None:
        """A ball the launcher model couldn't shoot (no_contact, no_wheel or jammed): out of play, `stuck`."""
        self.balls.set_state(i, BallState.STUCK)
        self.balls.park(i)
        self._log_exit(i, shot, motion)

    def after_physics(self) -> None:
        if self.shooter is not None:
            self.shooter.mirror()
        self.events = self.balls.after_step(self._prev)
        if self.ball_path is not None:
            self.ball_path.collect()

    def sensor_updates(self, hal: HalState) -> list[Update]:
        return self.ball_path.beam_updates() if self.ball_path is not None else []

    def devices(self) -> list:
        """The range's own devices, for World's duplicate check: the beam breaks (each has a device_key)."""
        return list(self.ball_path.config.beam_breaks) if self.ball_path is not None else []

    # ------------------------------------------------------------------------------------------------ reports

    def counts(self) -> dict:
        state = self.balls.state
        ledger = {s.value: int(np.count_nonzero(state == s)) for s in BallState}
        goals = dict(self.balls.goal_counts)
        return {"score": sum(goals.values()), "goals": goals, "fired": self.balls.fired, "held": ledger["robot"],
                "on_field": ledger["field"], "ledger": ledger}

    def ball_log(self) -> list[dict]:
        """Every ball's log (None for unset values), the in-play balls' last position and rest brought up to date."""
        self.balls.update_log()
        return [log_dict(b) for b in self.balls.log]

    def ball_poses(self) -> np.ndarray:
        """(k, 7) x, y, z and quaternion of the balls in play."""
        b = self.balls
        return self.world.data.qpos[b._q[b.field]]
