"""The scrimmage_lockstep HALSim extension against a real RobotPy program, with this test playing scrimmage.

Needs `pip install ./lockstep` (plus robotpy[sim] and robotpy-halsim-ws). Each test starts a robot process (~3 s).
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import websockets

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")
pytest.importorskip("scrimmage_lockstep")

from scrimmage.protocol import HalState, frame  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PORT = 3318
DEV = ("SimDevice", "ScrimmageLockstep")
PROBE = ("SimDevice", "Probe")


class FakeSim:
    """A minimal stand-in for scrimmage: a WebSocket server that records what the robot program reports."""

    def __init__(self):
        self.hal = HalState()
        self.ws = None
        self.changed = asyncio.Event()
        self.connected = asyncio.Event()

    async def handler(self, ws):
        self.ws = ws
        self.connected.set()
        try:
            async for raw in ws:
                self.hal.apply(raw)
                self.changed.set()
        except websockets.ConnectionClosed:
            pass

    def get(self, key, name, default=None):
        return self.hal.get(*key, name, default)

    async def until(self, pred, timeout):
        deadline = time.monotonic() + timeout
        while not pred():
            self.changed.clear()
            if pred():
                break
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"timed out; devices seen: {sorted(self.hal.devices)}")
            try:
                await asyncio.wait_for(self.changed.wait(), left)
            except TimeoutError:
                pass

    async def send(self, key, data):
        await self.ws.send(frame(*key, data))

    async def engage(self, step_us=5000):
        await self.send(DEV, {">step_us": step_us, ">engage": True})
        await self.until(lambda: self.get(DEV, "<engaged") is True, 5)

    async def step(self, seq):
        await self.send(DEV, {">step_seq": seq})
        await self.until(lambda: (self.get(DEV, "<done_seq") or 0) >= seq, 5)


async def with_robot(body, env_extra=None, wait_for_probe=True) -> str:
    """Run `body(sim)` with a probe robot connected; returns the robot program's output."""
    sim = FakeSim()
    env = dict(os.environ, HALSIMWS_PORT=str(PORT), HALSIMWS_HOST="localhost", **(env_extra or {}))
    async with websockets.serve(sim.handler, "localhost", PORT):
        robot = subprocess.Popen(
            [sys.executable, "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"],
            cwd=ROOT / "tests" / "lockstep_probe", env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            await asyncio.wait_for(sim.connected.wait(), 30)
            if wait_for_probe:
                await sim.until(lambda: DEV in sim.hal.devices and sim.get(PROBE, "<init_us") is not None, 30)
            await body(sim)
        finally:
            robot.kill()
            out = robot.communicate()[0]
    return out


def test_steps_robot_clock_and_orders_outputs():
    async def body(sim):
        await asyncio.sleep(1.0)
        assert (sim.get(PROBE, "<loops") or 0) == 0          # paused from load: no loops before engage
        await sim.engage()
        init_us = sim.get(PROBE, "<init_us")
        prev = sim.get(PROBE, "<loops") or 0
        for n in range(1, 41):                                # 40 x 5 ms = 200 ms of robot time
            await sim.step(n)
            loops = sim.get(PROBE, "<loops") or 0
            if loops != prev:
                # outputs of the loop that ran during step n arrived before done_seq = n
                elapsed = sim.get(PROBE, "<loop_us") - init_us
                assert (n - 1) * 5000 < elapsed <= n * 5000, (n, elapsed)
            prev = loops
        assert prev == pytest.approx(10, abs=1)              # 20 ms TimedRobot loop
        assert (sim.get(PROBE, "<fast") or 0) == pytest.approx(40, abs=2)   # 5 ms addPeriodic keeps up
        await asyncio.sleep(0.5)
        assert sim.get(PROBE, "<loops") == prev               # no steps, no robot time
    asyncio.run(with_robot(body))


def test_watchdog_releases_clock_and_steps_reengage():
    async def body(sim):
        await sim.engage()
        for n in range(1, 5):
            await sim.step(n)
        loops = sim.get(PROBE, "<loops")
        await sim.until(lambda: sim.get(DEV, "<engaged") is False, 4)          # 2 s watchdog
        await sim.until(lambda: sim.get(PROBE, "<loops") > loops + 5, 3)       # real time: 50 loops/s
        await sim.step(5)                                                      # a step takes the clock back
        assert sim.get(DEV, "<engaged") is True
        frozen = sim.get(PROBE, "<loops")
        await asyncio.sleep(0.5)
        assert sim.get(PROBE, "<loops") == frozen
    asyncio.run(with_robot(body))


def test_grace_period_releases_clock_without_scrimmage():
    async def body(sim):
        # connected, but never engages: like running the robot without scrimmage
        await sim.until(lambda: (sim.get(PROBE, "<loops") or 0) > 5, 5)
    out = asyncio.run(with_robot(body, {"SCRIMMAGE_LOCKSTEP_GRACE_S": "0.5"}))
    assert "running in real time" in out


def test_waits_for_program_start_before_stepping():
    async def body(sim):
        # engage and step as soon as the device appears, while the robot constructor is still sleeping
        await sim.until(lambda: DEV in sim.hal.devices, 30)
        await sim.send(DEV, {">step_us": 5000, ">engage": True})
        await sim.send(DEV, {">step_seq": 1})
        await sim.until(lambda: (sim.get(DEV, "<done_seq") or 0) >= 1, 10)
        n, deadline = 1, time.monotonic() + 20
        while not sim.get(PROBE, "<loops"):
            assert time.monotonic() < deadline
            n += 1
            await sim.step(n)
        assert sim.get(PROBE, "<loop_us") - sim.get(PROBE, "<init_us") == 20000
        # no robot time passed before the program started: the first 20 ms loop came with step 4
        assert n == 4, n
    asyncio.run(with_robot(body, {"PROBE_INIT_DELAY": "0.5"}, wait_for_probe=False))


def test_engage_false_releases_clock():
    async def body(sim):
        await sim.engage()
        for n in range(1, 5):
            await sim.step(n)
        loops = sim.get(PROBE, "<loops")
        t0 = time.monotonic()
        await sim.send(DEV, {">engage": False})
        await sim.until(lambda: sim.get(DEV, "<engaged") is False, 0.5)
        assert time.monotonic() - t0 < 0.5
        await sim.until(lambda: sim.get(PROBE, "<loops") > loops + 10, 1)        # real time: 50 loops/s
    out = asyncio.run(with_robot(body))
    assert "disengaged" in out


def test_keepalive_holds_clock_past_watchdog():
    async def body(sim):
        await sim.engage()
        for n in range(1, 5):
            await sim.step(n)
        loops = sim.get(PROBE, "<loops")
        for k in range(1, 9):                       # 4 s of keep-alives: twice the 2 s watchdog
            await sim.send(DEV, {">keepalive": k})
            await asyncio.sleep(0.5)
        assert sim.get(DEV, "<engaged") is True
        assert sim.get(PROBE, "<loops") == loops    # still paused
    asyncio.run(with_robot(body))


def test_engage_after_watchdog_release_engages_again():
    async def body(sim):
        await sim.engage()
        await sim.step(1)
        await sim.until(lambda: sim.get(DEV, "<engaged") is False, 4)   # watchdog released (engage set false)
        await sim.engage()                                               # a fresh engage must not be swallowed
        assert sim.get(DEV, "<engaged") is True
    asyncio.run(with_robot(body))
