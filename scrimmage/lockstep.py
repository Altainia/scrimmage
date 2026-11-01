"""Lockstep stepping: scrimmage drives the robot program's clock through the scrimmage_lockstep extension."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import TYPE_CHECKING, Callable

from .protocol import frame

if TYPE_CHECKING:
    from .bridge import Bridge

log = logging.getLogger("scrimmage")

# The extension's SimDevice as halsim_ws_client relays it (verified by tests/test_lockstep_ext.py).
LOCKSTEP_DEVICE = ("SimDevice", "ScrimmageLockstep")
DEFAULT_STEP = 0.005  # seconds; fine enough for 5 ms addPeriodic loops
STALL_FIRST_WARNING = 2.0  # wall seconds waiting on the robot program before the first warning
STALL_REPEAT = 10.0        # then warn this often
KEEPALIVE_PERIOD = 0.5     # wall seconds between keep-alives while held
ENGAGE_RESEND = 2.0        # re-send engage this often until the extension acknowledges it
VALUES_WAIT = 1.0          # wall seconds to wait for a step's new SimDevice values before carrying on


def parse_speed(text: str) -> float | None:
    """"max" -> None (as fast as possible), otherwise a positive real-time factor."""
    if text.strip().lower() == "max":
        return None
    try:
        v = float(text)
    except ValueError:
        raise ValueError(f'speed must be a positive number or "max", not {text!r}') from None
    if not (math.isfinite(v) and v > 0):
        raise ValueError(f'speed must be a positive number or "max", not {text!r}')
    return v


def physics_steps_per_step(step: float, timestep: float) -> int:
    """How many physics timesteps make one lockstep step; the step must be a whole multiple."""
    n = round(step / timestep)
    if n < 1 or abs(n * timestep - step) > 1e-9:
        raise ValueError(f"lockstep step {step:g} s must be a whole multiple of the physics timestep {timestep:g} s")
    return n


class Pacer:
    """Keeps simulated time from running ahead of wall time x speed (speed None: never waits)."""

    def __init__(self, speed: float | None, clock: Callable[[], float] = time.monotonic):
        self.speed = speed
        self.clock = clock
        self.wall0 = self.sim0 = 0.0

    def start(self, sim_time: float) -> None:
        self.wall0, self.sim0 = self.clock(), sim_time

    def delay(self, sim_time: float) -> float:
        if self.speed is None:
            return 0.0
        return max(0.0, self.wall0 + (sim_time - self.sim0) / self.speed - self.clock())

    def lag(self, sim_time: float) -> float:
        """Wall time minus the wall time sim_time was due at; positive when behind."""
        if self.speed is None:
            return 0.0
        return self.clock() - (self.wall0 + (sim_time - self.sim0) / self.speed)


class LockstepDriver:
    """Alternates physics steps with robot program steps until the connection ends.

    Each cycle: step physics by one lockstep step, run the per-tick extras, push sensors, then ask the
    extension to step the robot clock (>step_seq = n) and wait for <done_seq >= n. Everything travels on
    the one WebSocket in order, so once done_seq arrives, that step's motor outputs are already in hal.

    Phoenix 6 devices end it: their simulation follows the wall clock, so between steps the driver frees the robot's
    clock (Bridge.release_for_phoenix) and the bridge carries on in real time.
    """

    def __init__(self, bridge: Bridge, conn: int, max_lag: float):
        self.b = bridge
        self.conn = conn
        self.seq = 0
        self.max_lag = max_lag  # if pacing falls this far behind (robot stalled), start pacing afresh
        self.values_slack = 0   # SimDevice values the extension counts that never arrived (see _wait_for_values)
        self._stepping = False  # a step_once is in flight (only it may release the clock then)

    def _get(self, key: str, default=None):
        return self.b.hal.get(*LOCKSTEP_DEVICE, key, default)

    async def _wait_for(self, pred: Callable[[], bool], what: str, timeout: float | None = None) -> str:
        """Wait until pred() holds. Returns "ok", "disconnected", or "timeout" (after `timeout` wall seconds).
        Warns while the robot program is stalled."""
        b = self.b
        start = time.monotonic()
        next_warn = start + STALL_FIRST_WARNING
        while not pred():
            if not b._connected(self.conn):
                return "disconnected"
            now = time.monotonic()
            if timeout is not None and now - start >= timeout:
                return "timeout"
            wake = next_warn if timeout is None else min(next_warn, start + timeout)
            b.lockstep_changed.clear()
            try:
                await asyncio.wait_for(b.lockstep_changed.wait(), max(0.0, wake - now))
            except TimeoutError:
                if time.monotonic() >= next_warn:
                    log.warning("waiting on robot program (%s)", what)
                    next_warn = time.monotonic() + STALL_REPEAT
        return "ok"

    async def _engage(self) -> bool:
        step_us = round(self.b.step * 1e6)
        attempts = 0
        while True:
            attempts += 1
            if attempts > 1:
                log.warning("waiting on robot program (engage); asking again")
            # step_us goes with engage (not with each step_seq) so the extension never reads a stale value
            await self.b._send([frame(*LOCKSTEP_DEVICE, {">step_us": step_us, ">engage": True})])
            r = await self._wait_for(lambda: self._get("<engaged") is True, "engage", timeout=ENGAGE_RESEND)
            if r != "timeout":
                return r == "ok"

    async def run(self) -> None:
        b, w = self.b, self.b.world
        if not await self._engage():
            return
        if not b._connected(self.conn):
            return  # the robot program went away right after acknowledging
        log.info("lockstep engaged: %g ms steps, %s", b.step * 1000,
                 "held" if b.held else ("speed max" if b.speed is None else f"speed {b.speed:g}x"))
        b._restart_clock()
        b._rewind_log()  # drop rows logged in real time before the hand-off
        self.seq = int(self._get("<done_seq", 0) or 0)  # the extension keeps counting across reconnects
        if await self._release_for_phoenix():  # Phoenix 6 devices appeared while it engaged (robot constructor)
            return
        b._lockstep_engaged(self)
        wall0, sim0 = time.monotonic(), w.time
        try:
            if b.held:
                await self._hold()
            else:
                pacer = Pacer(b.speed)
                pacer.start(w.time)
                await self._loop(pacer)
        finally:
            b._lockstep_ended(self)
            wall, sim = time.monotonic() - wall0, w.time - sim0
            log.info("lockstep: simulated %.1f s in %.1f s (%.1fx real time)", sim, wall,
                     sim / wall if wall > 0 else 0.0)

    async def step_once(self, timeout: float | None = None) -> str:
        """One lockstep step: physics, per-tick extras, sensors, then the robot program.
        Returns "ok", "done", "disconnected", "timeout" (no ack within `timeout` wall seconds), or "released"
        (Phoenix 6 devices are present, so the robot's clock now runs free: no step was taken, or it was the last)."""
        if not self.b._connected(self.conn):
            return "disconnected"
        if await self._release_for_phoenix():
            return "released"
        self._stepping = True
        try:
            r = await self._step(timeout)
        finally:
            self._stepping = False
        if r == "ok" and await self._release_for_phoenix():
            return "released"
        return r

    async def _release_for_phoenix(self) -> bool:
        """Free the robot's clock if Phoenix 6 devices have appeared; True once it is free."""
        b = self.b
        if b.phoenix and b._connected(self.conn):
            await b.release_for_phoenix()
        return b.phoenix_released

    async def _step(self, timeout: float | None) -> str:
        b, w = self.b, self.b.world
        if int(self._get("<done_seq", 0) or 0) < self.seq:
            # A previous step is still unacknowledged (the extension keeps only the latest step_seq,
            # so sending another would let the robot clock fall behind physics): wait for it first.
            r = await self._wait_for(lambda: int(self._get("<done_seq", 0) or 0) >= self.seq,
                                     f"step {self.seq}", timeout)
            if r != "ok":
                return r
        for _ in range(b.physics_per_step):
            w.step(b.hal)
        await b._on_tick(w.time)
        if b.done.is_set():
            return "done"
        await b._push_sensors()
        self.seq += 1
        n = self.seq
        await b._send([frame(*LOCKSTEP_DEVICE, {">step_seq": n})])
        r = await self._wait_for(lambda: int(self._get("<done_seq", 0) or 0) >= n, f"step {n}", timeout)
        return r if r != "ok" else await self._wait_for_values()

    async def _wait_for_values(self) -> str:
        """Wait until every SimDevice value the robot program has created has reached scrimmage. halsim_ws_client
        starts relaying a device created mid-step from its own thread, so it can arrive after the step's done_seq;
        the extension (0.5.0 and later) reports how many there are with each done_seq. Older extensions: no wait."""
        need = self._get("<sim_values")
        if need is None:
            return "ok"

        def heard() -> bool:
            return len(self.b.sim_values_seen) + self.values_slack >= int(need)
        if heard():
            return "ok"
        r = await self._wait_for(heard, "new devices", timeout=VALUES_WAIT)
        if r == "timeout":
            missing = int(need) - len(self.b.sim_values_seen) - self.values_slack
            self.values_slack += missing
            log.warning("%d simulated device value(s) the robot program created never reached scrimmage (after %g s); "
                        "carrying on without waiting for them", missing, VALUES_WAIT)
            return "ok"
        return r

    async def _loop(self, pacer: Pacer) -> None:
        b, w = self.b, self.b.world
        while b._connected(self.conn):
            if await self.step_once() != "ok":
                return
            now = w.time
            if pacer.lag(now) > self.max_lag:
                pacer.start(now)  # don't race to make up for a stall (breakpoint, slow code)
            delay = pacer.delay(now)
            if delay > 0:
                await asyncio.sleep(delay)

    async def _hold(self) -> None:
        """Held: steps happen only through step_once (Session.advance). Meanwhile keep the robot's
        2 s watchdog fed, so an agent can take as long as it likes between calls. Phoenix 6 devices appearing
        end the hold: the clock is freed at once, or by the step in flight when they appear."""
        b = self.b
        n = 0
        while b._connected(self.conn):
            if b.phoenix_released or (not self._stepping and await self._release_for_phoenix()):
                return
            n += 1
            await b._send([frame(*LOCKSTEP_DEVICE, {">keepalive": n})])
            # a disconnect wakes this at once (b.disconnected is set by the handler), as do Phoenix 6 devices, and
            # the step in flight when they appeared freeing the clock
            wake = [asyncio.ensure_future(b.disconnected.wait()),
                    asyncio.ensure_future(b._phoenix_released_event.wait())]
            if not b.phoenix:
                wake.append(asyncio.ensure_future(b._phoenix_seen_event.wait()))
            try:
                await asyncio.wait(wake, timeout=KEEPALIVE_PERIOD, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for f in wake:
                    f.cancel()
