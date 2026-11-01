"""Test helpers: a fake robot program (halsim_ws_client, optionally with the lockstep extension) and Bridge runners."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import time
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

import websockets

from scrimmage.bridge import Bridge
from scrimmage.lockstep import LOCKSTEP_DEVICE
from scrimmage.protocol import frame
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]
_ports = itertools.count(3330)


def next_port() -> int:
    return next(_ports)


def make_bridge(port: int, config: Path | None = None, **kw) -> Bridge:
    config = config or ROOT / "configs" / "kitbot.toml"
    cfg = tomllib.loads(config.read_text())
    return Bridge(World(cfg, config.parent), "localhost", port, nt=False, **kw)


PHOENIX_CONFIG = ROOT / "tests" / "java_probe" / "scrimmage.toml"  # Phoenix 6 bindings: TalonFX 1 and 4, CANcoder 2, Pigeon 3
ROTOR = ("CANEncoder", "Talon FX (v6)[1]/Rotor Sensor")


async def wait_until(pred, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.01)


@asynccontextmanager
async def running(bridge: Bridge):
    task = asyncio.create_task(bridge.run())
    await asyncio.sleep(0.2)  # let it bind the port
    try:
        yield bridge
    finally:
        bridge.done.set()
        await asyncio.wait_for(task, 5)


class TimedClimb:
    """A timed setpoint climb (tests/climb_timed.py writes them, e.g. examples/climb/high_timed.toml): `loop`, the
    robot loop in s; `[[joint]]`s, each a position loop (`pwm` motor, `inverted`, `encoder` DIO channel_a,
    `counts_per_unit`, `kp` in V per unit); `[[setpoint]]`s at `t` s, each `to` a position (optionally
    `max_voltage`) or an open-loop `voltage`, held until the joint's next setpoint."""

    def __init__(self, loop: float, joints: list[dict], setpoints: list[dict]):
        self.loop, self.joints = loop, joints
        self.setpoints = sorted(setpoints, key=lambda sp: sp["t"])

    @classmethod
    def load(cls, path: Path) -> "TimedClimb":
        raw = tomllib.loads(Path(path).read_text())
        joints = raw.get("joint", [])
        names = {j["name"] for j in joints}
        for j in joints:
            missing = {"name", "pwm", "encoder", "counts_per_unit", "kp"} - set(j)
            if missing:
                raise ValueError(f"{path}: joint {j.get('name', '?')!r} needs {', '.join(sorted(missing))}")
        for sp in raw.get("setpoint", []):
            if sp.get("joint") not in names or "t" not in sp or ("to" in sp) == ("voltage" in sp):
                raise ValueError(f"{path}: a setpoint needs t, a joint of the [[joint]]s ({', '.join(sorted(names))}) "
                                 f"and one of to or voltage; got {sp}")
        return cls(float(raw["loop"]), joints, list(raw.get("setpoint", [])))

    def at(self, t: float) -> dict[str, dict]:
        """Each joint's setpoint in force at t (joints with none yet are left out)."""
        out = {}
        for sp in self.setpoints:
            if sp["t"] > t + 1e-9:
                break
            out[sp["joint"]] = sp
        return out

    def volts(self, sp: dict, position: float, bus: float) -> float:
        """A setpoint's voltage: open loop, or the P loop on `position`, clipped to the bus (or its max_voltage)."""
        if "voltage" in sp:
            return float(sp["voltage"])
        kp = next(j["kp"] for j in self.joints if j["name"] == sp["joint"])
        cap = min(bus, float(sp.get("max_voltage", bus)))
        return max(-cap, min(cap, kp * (float(sp["to"]) - position)))


class FakeRobot:
    """Connects like a robot program's halsim_ws_client and records every frame the bridge sends.

    With lockstep=True it also plays the scrimmage_lockstep extension: it acknowledges engage, and acks each
    step request (or holds the acks when auto_ack=False, until ack_held()). release_after=N reports a watchdog
    release (<engaged false) after the Nth keep-alive. phoenix=True announces a Phoenix 6 device
    (CANMotor/Talon FX (v6)[1] and its rotor sensor) on connecting; foreign_after=N then writes its rotor position (5.0, a value
    scrimmage never sends) after step N, like robot code calling TalonFXSimState.setRawRotorPosition. announce_delay holds back the
    ScrimmageLockstep device after connecting, so the bridge runs in real time for that long first. Like the extension,
    >engage false frees the clock (<engaged false): the robot loop then runs on its own every 5 ms of wall time,
    counting on from the last step.
    """

    def __init__(self, port: int, *, lockstep: bool = False, done_seq: int = 0, auto_ack: bool = True,
                 announce_delay: float = 0.0, ignore_engages: int = 0,
                 stall_after: int | None = None, exit_after: int | None = None, verbose: bool = False,
                 release_after: int | None = None, phoenix: bool = False, foreign_after: int | None = None,
                 drive: bool = False, input_log: Path | None = None, climb: Path | None = None):
        self.phoenix, self.foreign_after = phoenix, foreign_after
        self.climb = None if climb is None else TimedClimb.load(climb)
        self.climb_loops = 0      # robot loops enabled in teleop so far: the timed climb's clock
        self.hal_in: dict[tuple[str, str], dict] = {}   # Encoder and RoboRIO values the bridge sends
        self.drive, self.input_log = drive, input_log
        self.ds: dict = {}         # latest DriverStation data, merged (the bridge only sends what changed)
        self.joysticks: dict[str, dict] = {}
        self.stall_after, self.exit_after, self.verbose = stall_after, exit_after, verbose
        self.release_after = release_after  # report <engaged false (the 2 s watchdog fired) after N keep-alives
        self.ignore_engages = ignore_engages
        self.port, self.lockstep, self.done_seq, self.auto_ack = port, lockstep, done_seq, auto_ack
        self.announce_delay = announce_delay
        self.frames: list[dict] = []
        self.held: list[int] = []
        self.ws = None
        self._task: asyncio.Task | None = None
        self._free: asyncio.Task | None = None   # the robot loop running on the wall clock after >engage false

    async def __aenter__(self) -> "FakeRobot":
        self.ws = await websockets.connect(f"ws://localhost:{self.port}/wpilibws")
        if self.phoenix:
            await self.ws.send(frame("CANMotor", "Talon FX (v6)[1]", {"<motorVoltage": 0.0}))
            await self.ws.send(frame(*ROTOR, {">rawPositionInput": 0.0, ">velocity": 0.0}))   # its rotor sensor
        if self.lockstep:
            await asyncio.sleep(self.announce_delay)
            await self._send({"<engaged": False, "<done_seq": self.done_seq})
        self._task = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._free is not None:
            self._free.cancel()
        if self._task is not None:
            self._task.cancel()
        await self.ws.close()

    async def _send(self, data: dict) -> None:
        await self.ws.send(frame(*LOCKSTEP_DEVICE, data))

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                self.frames.append(msg)
                if msg["type"] == "DriverStation":
                    self.ds.update(msg["data"])
                elif msg["type"] == "Joystick":
                    self.joysticks.setdefault(msg["device"], {}).update(msg["data"])
                elif msg["type"] in ("Encoder", "RoboRIO"):
                    self.hal_in.setdefault((msg["type"], msg["device"]), {}).update(msg["data"])
                if not self.lockstep or (msg["type"], msg["device"]) != LOCKSTEP_DEVICE:
                    continue
                data = msg["data"]
                if data.get(">engage") is True:
                    if self.ignore_engages > 0:
                        self.ignore_engages -= 1
                    else:
                        await self._send({"<engaged": True})
                if data.get(">engage") is False and self._free is None:
                    await self._send({"<engaged": False})
                    self._free = asyncio.create_task(self._run_free())
                if ">keepalive" in data and self.release_after is not None and data[">keepalive"] >= self.release_after:
                    self.release_after = None
                    await self._send({"<engaged": False})
                if ">step_seq" in data:
                    seq = data[">step_seq"]
                    if self.exit_after is not None and seq >= self.exit_after:
                        print("Traceback (most recent call last):\n  File \"robot.py\", line 1, in teleopPeriodic\n"
                              "RuntimeError: fake robot crashed on purpose", flush=True)
                        os._exit(3)
                    if self.stall_after is not None and seq > self.stall_after:
                        continue  # stop acking: a hung robot program
                    await self._loop_body(seq)
                    if self.auto_ack:
                        self.done_seq = seq
                        await self._send({"<done_seq": seq})
                    else:
                        self.held.append(seq)
        except websockets.ConnectionClosed:
            pass

    async def _loop_body(self, seq: int) -> None:
        """One robot loop: what robot code does each step (or each 5 ms once its clock runs free)."""
        if self.verbose and seq % 100 == 0:
            print(f"fake robot step {seq}", flush=True)
        if self.foreign_after is not None and seq >= self.foreign_after:
            self.foreign_after = None
            await self.ws.send(frame(*ROTOR, {">rawPositionInput": 5.0}))
        if self.input_log is not None:
            self._log_inputs(seq)
        if self.drive:
            await self._drive()
        if self.climb is not None:
            await self._climb()

    async def _run_free(self) -> None:
        seq = self.done_seq
        try:
            while True:
                await asyncio.sleep(0.005)
                seq += 1
                await self._loop_body(seq)
        except websockets.ConnectionClosed:
            pass

    def _log_inputs(self, seq: int) -> None:
        entry = {"seq": seq, "enabled": bool(self.ds.get(">enabled")), "auto": bool(self.ds.get(">autonomous")),
                 "test": bool(self.ds.get(">test")),
                 "joysticks": {p: {"axes": j.get(">axes", []), "buttons": j.get(">buttons", []),
                                   "povs": j.get(">povs", [])} for p, j in self.joysticks.items()}}
        with self.input_log.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    async def _drive(self) -> None:
        """Arcade-less tank drive: both sides follow port 0's axis 1 (pushed forward = positive speed). PWM 1 gets
        the negated speed, as a robot program inverts its right side: tests/fake_robot.toml's right_drive has
        invert = true, so the two together drive straight."""
        speed = 0.0
        if self.ds.get(">enabled"):
            axes = self.joysticks.get("0", {}).get(">axes", [])
            speed = -float(axes[1]) if len(axes) > 1 else 0.0
        for pwm, value in (("0", speed), ("1", -speed if speed else 0.0)):
            await self.ws.send(frame("PWM", pwm, {"<init": True, "<speed": value}))

    async def _climb(self) -> None:
        """Play the timed climb as a team's endgame code would: from the first loop enabled in teleop, each joint's
        setpoint in force at (loops so far) x loop, held by a P loop on its encoder, in volts over the battery's
        voltage; disabled, or before teleop, every motor is neutral. The first call creates the encoders."""
        c = self.climb
        if self.climb_loops == 0 and not getattr(self, "_encoders_made", False):
            self._encoders_made = True
            for i, j in enumerate(c.joints):
                await self.ws.send(frame("Encoder", str(i), {"<init": True, "<channel_a": j["encoder"],
                                                             "<channel_b": j["encoder"] + 1}))
        teleop = self.ds.get(">enabled") and not self.ds.get(">autonomous") and not self.ds.get(">test")
        sps = c.at(self.climb_loops * c.loop) if teleop else {}
        if teleop:
            self.climb_loops += 1
        bus = float(self.hal_in.get(("RoboRIO", ""), {}).get(">vin_voltage", 12.0)) or 12.0
        for i, j in enumerate(c.joints):
            out = 0.0
            sp = sps.get(j["name"])
            if sp is not None:
                pos = self.hal_in.get(("Encoder", str(i)), {}).get(">count", 0) / j["counts_per_unit"]
                out = c.volts(sp, pos, bus) / bus * (-1.0 if j.get("inverted") else 1.0)
            await self.ws.send(frame("PWM", str(j["pwm"]), {"<init": True, "<speed": out}))

    async def closed(self) -> None:
        """Until the bridge closes the connection."""
        if self._task is not None:
            await self._task

    async def ack_held(self) -> None:
        for seq in self.held:
            self.done_seq = seq
            await self._send({"<done_seq": seq})
        self.held.clear()

    def step_seqs(self) -> list[int]:
        return [m["data"][">step_seq"] for m in self.frames
                if (m["type"], m["device"]) == LOCKSTEP_DEVICE and ">step_seq" in m["data"]]

    def engage_values(self) -> list[bool]:
        return [m["data"][">engage"] for m in self.frames
                if (m["type"], m["device"]) == LOCKSTEP_DEVICE and ">engage" in m["data"]]

    def keepalives(self) -> list[int]:
        return [m["data"][">keepalive"] for m in self.frames
                if (m["type"], m["device"]) == LOCKSTEP_DEVICE and ">keepalive" in m["data"]]

    def ds_enabled(self) -> list[bool]:
        return [m["data"][">enabled"] for m in self.frames if m["type"] == "DriverStation"]
