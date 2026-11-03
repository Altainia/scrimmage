"""HALSim WebSocket server that runs the MuJoCo world alongside a robot program."""

from __future__ import annotations

import asyncio
import csv
import logging
import math
import re
import time
from pathlib import Path
from typing import Any, Callable

import websockets
from websockets.protocol import State as WsState

from .bindings import CTRE_DEVICES, REV_DEVICES, resolve_device_key
from .config import DEFAULT_ODOMETRY
from . import foreign_writes as fwn
from .driverstation import DriverStationState
from .lockstep import DEFAULT_STEP, LOCKSTEP_DEVICE, LockstepDriver, physics_steps_per_step
from .nt_reader import NetworkTablesLink, NTUnavailable
from .pneumatics.runtime import PH_TYPE
from .protocol import DeviceKey, HalState, OutboundCache, frame
from .recorder import Recorder
from .world import World

log = logging.getLogger("scrimmage")

PHYSICS_TICK = 0.002     # wall-clock seconds between physics catch-up passes (real-time mode)
SENSOR_PERIOD = 0.005    # how often sensor values are pushed to the robot program (real-time mode)
FULL_REFRESH = 0.5       # resend every input, even unchanged ones, this often (covers devices created late)
MAX_LAG = 0.25           # if physics falls this far behind wall time, drop the backlog
DS_PERIOD = 0.02         # simulated seconds between Driver Station frames
LOG_PERIOD = 0.02        # simulated seconds between CSV rows and NT ground-truth updates
VIEWER_PERIOD = 1 / 60   # wall-clock seconds between viewer refreshes
DETECT_WINDOW = 2.0      # wall seconds after connecting to wait for the lockstep extension
PHOENIX_MARK = " (v6)["  # Phoenix 6 SimDevice names: "Talon FX (v6)[1]", "CANcoder (v6)[2]", ...
REALTIME_REASON = "Phoenix 6 simulation runs on the wall clock, so this robot runs in real time (not lockstep)"
PHOENIX_BOOT = 1.0       # simulated seconds a Phoenix device may take to boot and re-assert its initial values
MISSING_AFTER = 2.0      # simulated seconds after connecting before a configured Phoenix device counts as missing
# "<Kind> (v6)[<id>]" anywhere in a device name, not followed by a sub-device ("/Rotor Sensor")
PHOENIX_NAME = re.compile("(?P<kind>" + "|".join(map(re.escape, CTRE_DEVICES.values())) + r")\[(?P<id>\d+)\](?!/)")
SCRIMMAGE_TYPES = ("ScrimmageMotor", "ScrimmageEncoder", "ScrimmageGyro")   # fast-mode devices: "ScrimmageMotor:<name>" ...
REV_TYPE = "ScrimmageREV"   # the agent's helper SimDevice per SPARK: "ScrimmageREV:SparkMax[<id>]"
REV_NAME = re.compile("(?P<kind>" + "|".join(map(re.escape, REV_DEVICES.values())) + r")\[(?P<id>\d+)\]")
REV_LABEL = {"SparkMax": "SPARK MAX", "SparkFlex": "SPARK Flex"}
REV_OWN_NAME = re.compile(r"(?P<label>SPARK MAX|SPARK Flex) \[(?P<id>\d+)\]")  # REVLib's own SimDevice per SPARK
REV_AGENT_FIX = ("run the robot program through scrimmage or its Gradle init script (see `scrimmage java path`), "
                 "after `scrimmage java build`")
REVLIB_NEEDED = "2026.0.5"
ROBOTPY_REV_FIX = ("add `--scrimmage-lockstep` to `[robot_program] command`, or update scrimmage-lockstep "
                   "(`pip install ./lockstep`)")


# Message types halsim_ws_client's built-in providers use (WPILib 2026); every other message comes from a SimDevice.
# "Accel" is also a SimDevice type ("Accel:ADXL345_I2C[...]"): only its device "BuiltInAccel" is built in.
BUILTIN_WS_TYPES = frozenset({"AI", "AO", "AddressableLED", "CTREPCM", "DIO", "DriverStation", "Encoder", "HAL",
                              "Joystick", "PWM", "Relay", "RoboRIO", "Solenoid", "dPWM"})


def from_sim_device(key: DeviceKey) -> bool:
    """Whether halsim_ws_client relays this (type, device) for a SimDevice, not a built-in provider."""
    typ, dev = key
    return not (typ in BUILTIN_WS_TYPES or (typ == "Accel" and dev == "BuiltInAccel"))


def robot_language(command: list[str]) -> str | None:
    """"python" when the robot program command runs `robotpy`, "java" when it runs Gradle, else None."""
    names = [Path(c).name.lower() for c in command]
    if "robotpy" in names:
        return "python"
    if any(n.startswith("gradle") for n in names):
        return "java"
    return None


def phoenix_inputs(world: World) -> set[DeviceKey]:
    """The Phoenix 6 devices scrimmage simulates, whose `>` inputs robot code must leave alone."""
    keys: set[DeviceKey] = set()
    for m in world.motors:
        if m.kind == "CTRE":
            keys |= {m.device_key, ("CANEncoder", m.device + "/Rotor Sensor")}
    keys |= {e.device_key for e in world.encoders if e.kind == "CTRE"}
    keys |= {g.device_key for g in world.gyros if g.ctre}
    return keys


def phoenix_devices(world: World) -> list[DeviceKey]:
    """The Phoenix 6 devices the config binds, in config order (motors, encoders, gyros)."""
    keys = [m.device_key for m in world.motors if m.kind == "CTRE"]
    keys += [e.device_key for e in world.encoders if e.kind == "CTRE"]
    keys += [g.device_key for g in world.gyros if g.ctre]
    return list(dict.fromkeys(keys))


def missing_device_warnings(hal: HalState, expected: list[DeviceKey]) -> list[str]:
    """What to say about configured Phoenix devices the robot program hasn't created. A device of the same
    kind and id under another name (on another bus, say) gets a hint naming it; the rest share one message
    listing the Phoenix devices the robot did create."""
    def kind_id(name: str):
        m = PHOENIX_NAME.search(name)
        return (m["kind"], int(m["id"])) if m else None

    present = sorted(d for _, d in hal.devices if kind_id(d) is not None)
    out, missing = [], []
    for typ, name in expected:
        if resolve_device_key(hal, (typ, name)) is not None:
            continue
        want = kind_id(name)
        similar = sorted(d for t, d in hal.devices if t == typ and d != name and want and kind_id(d) == want)
        if similar:
            out.append(f'config expects {name}, robot has {similar[0]}: set device = "{similar[0]}" in that binding')
        else:
            missing.append(name)
    if missing:
        have = f"the robot has {', '.join(present)}" if present else "the robot has no Phoenix 6 devices"
        out.append(f"config expects {', '.join(missing)}, but the robot program hasn't created "
                   f"{'it' if len(missing) == 1 else 'them'} {MISSING_AFTER:g} s after connecting; "
                   f"check the CAN ids in the config ({have})")
    return out


def fast_phoenix_message(names: list[str]) -> str:
    """What to say when a fast-mode run's robot program creates Phoenix 6 devices anyway."""
    return (f"fast mode, but the robot program created Phoenix 6 devices ({', '.join(names)}): they force real time; "
            "use the fast-mode IO for them")


def scrimmage_devices(world: World) -> list[DeviceKey]:
    """The fast-mode (Scrimmage) devices the config binds, in config order (motors, encoders, gyros)."""
    keys = [m.device_key for m in world.motors if m.kind == "Scrimmage"]
    keys += [e.device_key for e in world.encoders if e.kind == "Scrimmage"]
    keys += [g.device_key for g in world.gyros if g.scrimmage]
    return list(dict.fromkeys(keys))


def missing_scrimmage_warnings(hal: HalState, expected: list[DeviceKey], sim_mode: str | None = None) -> list[str]:
    """One warning per bound fast-mode device the robot program hasn't created; when it created other fast-mode
    devices, they are named (a typo in a device name shows up there). In Phoenix mode (sim_mode "phoenix") that's
    the likely cause, so one warning says so instead."""
    missing = [f"{typ}:{name}" for typ, name in expected if (typ, name) not in hal.devices]
    if missing and sim_mode == "phoenix":
        return [f"the config binds fast-mode devices ({_some(missing)}), but this run isn't in fast mode, so robot "
                "code that checks Scrimmage.fastMode() uses its Phoenix IO: set [robot_program] sim_mode = \"fast\" "
                "or run with --sim-mode fast"]
    present = sorted(f"{t}:{d}" for t, d in hal.devices if t in SCRIMMAGE_TYPES)
    have = f" (it created {', '.join(present)})" if present else ""
    return [f"fast mode: the robot program never created {key}; does it pick its fast-mode IO when "
            f"Scrimmage.fastMode() is true?{have}" for key in missing]


def _some(names: list[str], most: int = 3) -> str:
    return ", ".join(names) if len(names) <= most else f"{', '.join(names[:most])} and {len(names) - most} more"


def rev_devices(world: World) -> list[DeviceKey]:
    """The REV SPARK helper devices the config binds, in config order (motors, then absolute encoders)."""
    keys = [m.device_key for m in world.motors if m.kind == "REV"]
    keys += [e.device_key for e in world.encoders if e.kind == "REV"]
    return list(dict.fromkeys(keys))


def rev_unhelped(hal: HalState, missing: list[DeviceKey], with_helpers: bool = False) -> list[DeviceKey]:
    """The `missing` helper keys whose SPARK the robot program did create: REVLib's own SimDevice for it is there
    ("SPARK MAX [3]" for "SparkMax[3]"). None when any ScrimmageREV helper is present, since the agent is driving REV
    then (a SPARK still missing is a CAN id or a SPARK still to come), unless `with_helpers`."""
    if not with_helpers and any(t == REV_TYPE for t, _ in hal.devices):
        return []
    own = {(m["label"], m["id"]) for _, d in hal.devices if (m := REV_OWN_NAME.fullmatch(d))}
    out = []
    for key in missing:
        m = REV_NAME.fullmatch(key[1])
        if m and (REV_LABEL[m["kind"]], m["id"]) in own:
            out.append(key)
    return out


def rev_agent_reason(agent: str | None, language: str | None = None) -> str:
    """Why the agent made no helpers, worded from its console state. The console can lag a fast lockstep run, so
    "missing" (no agent line yet) only words the reason; it never decides that the agent isn't driving REV."""
    if language == "python":
        return ("an out-of-date scrimmage-lockstep?" if agent == "loaded"
                else "the scrimmage REV helper isn't loaded or is out of date")
    if agent == "disabled":
        return "the scrimmage Java agent was disabled (-Dscrimmage.agent.disable)"
    if agent == "loaded":
        return "an out-of-date agent jar?"
    return "the scrimmage Java agent isn't loaded or is out of date"


def missing_rev_warnings(hal: HalState, expected: list[DeviceKey], motors=None, encoders=(),
                         agent: str | None = None, language: str | None = None) -> list[str]:
    """What to say about bound REV SPARKs the robot program hasn't created, worded like the Phoenix check: a SPARK
    of the other kind with the same id gets a hint (in the short form of the binding: "SparkMax:5" for a motor,
    "SparkMaxAbs:5" for an absolute encoder); the rest share one message listing the SPARKs the robot has.
    `motors` / `encoders`: the keys bound by motor / encoder bindings (motors default to all of `expected`).
    SPARKs the robot did create with no helper at all (see rev_unhelped) share one message saying the scrimmage agent
    isn't driving them instead, its reason worded from `agent`, the agent's state per the console. `language`
    ("python" for a RobotPy robot) words the fix for that robot; for RobotPy, a SPARK with REVLib's SimDevice but no
    helper (other helpers exist, or the console says the helper loaded) was created before the helper loaded (import
    time of robot.py)."""
    motors = set(expected) if motors is None else set(motors)
    out = []
    python = language == "python"
    unhelped = rev_unhelped(hal, [key for key in expected if key not in hal.devices], with_helpers=python)
    if unhelped:
        sparks = ", ".join(f"{REV_LABEL[m['kind']]} {m['id']}" for k in unhelped if (m := REV_NAME.fullmatch(k[1])))
        names = ", ".join(k[1] for k in unhelped)
        if python and (agent == "loaded" or any(t == REV_TYPE for t, _ in hal.devices)):
            out.append(f"config expects {names}, but the robot program created {sparks} before scrimmage's REV "
                       "helper loaded (for example at import time of robot.py): create SPARKs in robotInit or a "
                       "subsystem constructor")
        elif python:
            out.append(f"config expects {names}, but the scrimmage REV helper isn't driving the robot's REV "
                       f"SPARKs: the robot program created {sparks} with no scrimmage helper "
                       f"({rev_agent_reason(agent, language)}); {ROBOTPY_REV_FIX}")
        else:
            out.append(f"config expects {names}, but the scrimmage agent isn't driving the "
                       f"robot's REV SPARKs: the robot program created {sparks} with no scrimmage helper "
                       f"({rev_agent_reason(agent)}); {REV_AGENT_FIX}")
        expected = [key for key in expected if key not in unhelped]
    present = sorted((d for t, d in hal.devices if t == REV_TYPE), key=_rev_order)
    missing = []
    for key in expected:
        if key in hal.devices:
            continue
        want = REV_NAME.fullmatch(key[1])
        other = [d for d in present if want and (m := REV_NAME.fullmatch(d)) and m["id"] == want["id"]]
        if other:
            kind = REV_NAME.fullmatch(other[0])["kind"]
            forms = [f'"{kind}{suffix}:{want["id"]}"' for suffix, keys in (("", motors), ("Abs", encoders))
                     if key in keys]
            out.append(f"config expects {key[1]}, robot has {other[0]}: use {kind} in that binding's rev "
                       f"({' / '.join(forms)})")
        else:
            missing.append(key[1])
    if missing:
        have = f"the robot has {', '.join(present)}" if present else "the robot has no REV SPARKs"
        out.append(f"config expects {', '.join(missing)}, but the robot program hasn't created "
                   f"{'it' if len(missing) == 1 else 'them'} {MISSING_AFTER:g} s after connecting; "
                   f"check the CAN ids in the config ({have})")
    return out


def _rev_order(name: str):
    m = REV_NAME.fullmatch(name)
    return (int(m["id"]), m["kind"]) if m else (1 << 30, name)


def unbound_rev_message(name: str) -> str:
    m = REV_NAME.fullmatch(name)
    if m is None:
        return f"the robot program created {name}, but nothing in the config binds it"
    return (f"the robot program created {REV_LABEL[m['kind']]} {m['id']}, but nothing in the config binds it: it runs "
            f'in REV\'s simulation with no physics (bind it with rev = "{m["kind"]}:{m["id"]}")')


def rev_unavailable_message(state: str, language: str | None = None) -> str:
    """The agent's "unavailable: <reason>" as a scrimmage error (bridge log, scenario check, Drive notice)."""
    reason = state.removeprefix("unavailable:").strip()
    if language == "python":
        return (f"REV simulation unavailable in the robot program ({reason}): "
                "update robotpy-rev (`pip install \"robotpy-rev>=2026.0.4,<2027\"`)")
    return (f"REV simulation unavailable in the robot program ({reason}): update REVLib to {REVLIB_NEEDED} or later "
            "(vendordeps/REVLib.json), then rebuild (WPILib VS Code: Manage Vendor Libraries, Check for Updates)")


class _Ticks:
    """When each periodic extra last ran, in simulated seconds since the world was reset."""

    def __init__(self) -> None:
        self.refresh = self.ds = self.log = -math.inf


class Bridge:
    def __init__(self, world: World, host: str = "localhost", port: int = 3300, *,
                 log_path: Path | None = None, viewer: bool = False, nt: bool = True,
                 speed: float | None = 1.0, step: float = DEFAULT_STEP, held: bool = False,
                 odometry_topic: str | None = DEFAULT_ODOMETRY, nt_port: int = 5810, sim_mode: str | None = None):
        self.world = world
        self.sim_mode = sim_mode         # the robot program's [robot_program] sim_mode, when scrimmage launched it
        self.speed = speed               # real-time factor; None = as fast as possible (lockstep only)
        self.step = step
        self.physics_per_step = physics_steps_per_step(step, float(world.model.opt.timestep))
        if speed is not None and step / speed >= 1.0:
            raise ValueError(f"--speed {speed:g} with {step:g} s steps: each step would take {step / speed:g} s "
                             "of wall time or more, and the robot's 2 s watchdog would release the clock")
        self.held = held                 # step only when asked (Session), keeping the robot paused in between
        self.listening = asyncio.Event()
        self.bound_port: int | None = None
        self.mode: str | None = None     # "lockstep" or "realtime" once decided for the current connection
        self.mode_changed = asyncio.Event()
        self.driver: LockstepDriver | None = None
        self.disconnected = asyncio.Event()      # set while no robot program is connected (wakes the held keep-alive)
        self.lockstep_changed = asyncio.Event()   # set whenever the ScrimmageLockstep device reports
        self.lockstep_released_at: float | None = None  # sim time the robot's watchdog freed its clock while held
        self.host, self.port = host, port
        self.ds = DriverStationState()
        self.log_path = log_path
        self.want_viewer = viewer
        self.want_nt = nt
        self.odometry_topic, self.nt_port = odometry_topic, nt_port
        self.recorder = Recorder()
        self.nt_link: NetworkTablesLink | None = None
        self.hal = HalState()
        self.cache = OutboundCache()
        self.phoenix = False             # a Phoenix 6 device appeared on the current connection
        self.phoenix_released = False    # ... so scrimmage freed the robot's clock: this connection runs in real time
        self.robot_behind = 0.0          # ... and the longest the robot program has been behind real time since (s)
        self.physics_skips = 0           # times real-time physics fell MAX_LAG behind and skipped ahead (connection)
        self.physics_skipped = 0.0       # ... and the simulated time it skipped (s)
        self._phoenix_seen_event = asyncio.Event()  # wakes a held driver to release the clock
        self._phoenix_released_event = asyncio.Event()  # wakes a held driver whose in-flight step released it
        self._fast_phoenix_warned = False
        self.foreign_writes: dict[str, dict] = {}  # "type/device key" -> first sighting and count
        self._watched = phoenix_inputs(world)
        self._expected = phoenix_devices(world)
        self._devices_checked = False    # missing Phoenix devices reported for the current connection
        self._rev_expected = rev_devices(world)
        self._scrimmage_expected = scrimmage_devices(world)
        self._scrimmage_present: set[DeviceKey] = set()   # fast-mode devices seen this connection
        self._scrimmage_unbound: set[DeviceKey] = set()   # ... that nothing in the config binds, reported once
        self._rev_motor_keys = {m.device_key for m in world.motors if m.kind == "REV"}
        self._rev_encoder_keys = {e.device_key for e in world.encoders if e.kind == "REV"}
        self._rev_unbound: set[DeviceKey] = set()   # SPARKs nothing binds, reported this connection
        self._rev_present: set[DeviceKey] = set()   # ScrimmageREV helpers seen this connection
        self._ph_present: set[DeviceKey] = set()    # ScrimmagePH devices (REV Pneumatic Hubs) seen this connection
        self._rev_checked = False        # REV sim availability reported for the current connection
        self.rev_state: Callable[[], str | None] = lambda: None  # "unavailable: <reason>" per the agent (console)
        # (device, key) -> pending suspect: first value and time, latest value, how many
        self._suspects: dict[tuple[DeviceKey, str], dict] = {}
        self._initial: dict[tuple[DeviceKey, str], Any] = {}  # each watched input's value before scrimmage sent one
        self._heard: set[DeviceKey] = set()  # watched devices the robot program has announced (this connection)
        self._warned: set[str] = set()   # devices warned about this run
        self.robot_language: str | None = None  # "python" / "java" per the robot program command (Session)
        self.agent_state: Callable[[], str | None] = lambda: None  # the Java agent, per the console (Session)
        self.ws: Any = None
        self.conn_id = 0                 # bumped on every connection; drivers stop when it changes
        self.exit_code = 0
        self.done = asyncio.Event()
        self._ticks = _Ticks()
        self._viewer = self._logf = self._writer = None
        self._log_mark = 0               # log file position when the current connection began
        self._last_view = -math.inf

    # ---------------------------------------------------------------- websocket side
    async def _handler(self, ws) -> None:
        path = getattr(getattr(ws, "request", None), "path", "/wpilibws")
        if not path.startswith("/wpilibws") or self.ws is not None:
            await ws.close(1008, "only one robot program at /wpilibws")
            return
        log.info("robot program connected")
        self.ws = ws
        self.conn_id += 1
        self._set_mode(None)
        self.disconnected.clear()
        self.hal = HalState()
        self.sim_values_seen: set[tuple[str, str, str]] = set()  # distinct SimDevice values heard of (connection)
        self.phoenix = self.phoenix_released = False
        self._initial = {}
        self._heard = set()
        self._phoenix_seen_event.clear()
        self._phoenix_released_event.clear()
        self._scrimmage_present.clear()
        self._restart_clock()
        if self._logf:
            self._log_mark = self._logf.tell()
        try:
            async for raw in ws:
                got = self.hal.apply(raw)
                if got is None:
                    continue
                if from_sim_device(got[0]):
                    before = len(self.sim_values_seen)
                    self.sim_values_seen.update((*got[0], k) for k in got[1])
                    if len(self.sim_values_seen) != before:
                        self.lockstep_changed.set()  # a driver may be waiting for these (LockstepDriver.step_once)
                # Initializing a device in robot code resets its sim values, so resend ours.
                if got[1].get("<init") is True:
                    self.cache.forget(*got[0])
                if not self.phoenix and PHOENIX_MARK in got[0][1]:
                    self._phoenix_seen()
                if got[0][0] in SCRIMMAGE_TYPES:
                    self._scrimmage_seen(got[0])
                if got[0][0] == REV_TYPE:
                    self._rev_seen(got[0])
                if got[0][0] == PH_TYPE and got[0] not in self._ph_present:
                    # The extension creates it when the robot opens the hub, with no <init: resend the hub's inputs
                    self._ph_present.add(got[0])
                    self.cache.forget(*got[0])
                self._check_inputs(*got)
                if got[0] == LOCKSTEP_DEVICE:
                    if got[1].get("<engaged") is False:
                        self._lockstep_released()
                    self.lockstep_changed.set()
        except websockets.ConnectionClosed:
            pass
        finally:
            log.info("robot program disconnected")
            self.ws = None
            self._set_mode(None)
            self.disconnected.set()
            self.lockstep_changed.set()  # wake a driver waiting on this connection

    # ---------------------------------------------------------------- Phoenix 6: real time, foreign writes
    @property
    def effective_speed(self) -> float | None:
        """The speed the run goes at: the configured one, or 1x (real time) once Phoenix 6 devices are present."""
        return 1.0 if self.phoenix else self.speed

    @property
    def realtime_reason(self) -> str | None:
        return REALTIME_REASON if self.phoenix else None

    def phoenix_names(self) -> list[str]:
        """The Phoenix 6 devices the robot program has created ("Talon FX (v6)[1]", ...), sorted."""
        return sorted({m.group(0) for _, d in self.hal.devices if (m := PHOENIX_NAME.search(d))})

    @property
    def fast_phoenix_message(self) -> str | None:
        """Set when a fast-mode robot program created Phoenix 6 devices, which force real time."""
        if self.sim_mode != "fast" or not self.phoenix:
            return None
        return fast_phoenix_message(self.phoenix_names())

    def _phoenix_seen(self) -> None:
        self.phoenix = True
        self._phoenix_seen_event.set()

    async def release_for_phoenix(self) -> None:
        """Phoenix 6 simulation follows the wall clock, so free the robot's clock (>engage false) and run physics in
        real time for the rest of this connection. Callers make sure no lockstep step is in flight."""
        if self.phoenix_released:
            return
        self._mark_released()
        self._phoenix_released_event.set()
        await self._send([frame(*LOCKSTEP_DEVICE, {">engage": False})])
        if not self.held and self.speed != 1.0:
            log.warning("%s", REALTIME_REASON)
        else:
            log.info("%s", REALTIME_REASON)
        self._set_mode("realtime")

    def _scrimmage_seen(self, dev: DeviceKey) -> None:
        """A fast-mode device the robot program created. SimDevices send no <init and halsim_ws_client drops values
        for one that doesn't exist yet, so on first sight resend all its inputs. Say once if nothing binds it."""
        if dev not in self._scrimmage_present:
            self._scrimmage_present.add(dev)
            self.cache.forget(*dev)
        if dev not in self._scrimmage_expected and dev not in self._scrimmage_unbound:
            self._scrimmage_unbound.add(dev)
            log.warning("fast mode: the robot program created %s:%s, but nothing in the config binds it: it gets no "
                        "physics (bind it with scrimmage = \"%s\")", dev[0], dev[1], dev[1])

    def _rev_seen(self, dev: DeviceKey) -> None:
        """A SPARK the robot program created. The first time its helper shows up, resend all its inputs: the agent
        creates the helper a robot loop after the SPARK (and sends no <init), and halsim_ws_client drops values for a
        SimDevice that doesn't exist yet, so inputs that don't change (at rest, the motor code) would otherwise wait
        for the next full refresh. Say once if nothing in the config binds it."""
        if dev not in self._rev_present:
            self._rev_present.add(dev)
            self.cache.forget(*dev)
        if dev not in self._rev_expected and dev not in self._rev_unbound:
            self._rev_unbound.add(dev)
            log.warning("%s", unbound_rev_message(dev[1]))

    def _check_rev_available(self) -> bool:
        """Report (once per connection) the agent saying REV can't be simulated, when the config binds SPARKs.
        Returns False once it has."""
        if not self._rev_expected:
            return True
        if not self._rev_checked:
            state = self.rev_state()
            if state and state.startswith("unavailable"):
                self._rev_checked = True
                log.error("%s", rev_unavailable_message(state, self.robot_language))
        return not self._rev_checked

    def _check_inputs(self, dev: DeviceKey, delta: dict[str, Any]) -> None:
        """Note `>` values relayed back for a simulated Phoenix device that scrimmage didn't send. They're
        judged after the next sensor push (settle_foreign_writes), so an update already on its way isn't counted.
        Every frame from the robot program comes through here."""
        self._behind(self.cache.heard())
        if dev not in self._watched:
            return
        if dev not in self._heard:
            # Its first frame announces the values it was created with. Anything we sent before then was dropped
            # (halsim_ws_client drops values for a device that doesn't exist yet), so resend all of ours.
            self._heard.add(dev)
            for key, value in delta.items():
                if key.startswith(">"):
                    self._initial.setdefault((dev, key), value)
            self.cache.forget(*dev)
            return
        for key, value in delta.items():
            if not key.startswith(">"):
                continue
            sent = self.cache.sent_history(*dev, key)
            if not sent:   # never sent (yet): the robot program announcing its initial values, not a write
                self._initial.setdefault((dev, key), value)
                continue
            i = fwn.echo_index(dev[1], key, value, sent)
            if i is not None:
                self._behind(self.cache.relayed(*dev, key, i))
                continue
            initial = self._initial.get((dev, key))
            if self.world.time < PHOENIX_BOOT and initial is not None and fwn.is_echo(dev[1], key, value, [initial]):
                # The device booting (real time, slow computer) reports its own values until ours land, compared as
                # ours are (a Talon models a little supply drop on its own 12 V too)
                continue
            s = self._suspects.get((dev, key))
            if s is None:
                self._suspects[(dev, key)] = {"value": value, "t": self.world.time, "last": value, "count": 1}
            else:
                s["last"] = value
                s["count"] += 1

    def _mark_released(self) -> None:
        """The robot's clock runs free from here: count how far behind it falls from its next frame on (the hold
        before this isn't a stall)."""
        self.phoenix_released = True
        self.cache.track_lag()

    def _behind(self, seconds: float) -> None:
        """The robot program was `seconds` behind (silent that long, or relaying a value that long superseded): kept
        while it runs in real time, where a robot that falls behind runs differently from one that keeps up."""
        if self.phoenix_released:
            self.robot_behind = max(self.robot_behind, seconds)

    def settle_foreign_writes(self) -> None:
        """Record the suspects that the values sent since still don't explain as foreign writes."""
        suspects, self._suspects = self._suspects, {}
        new = []
        for (dev, key), s in suspects.items():
            sent = self.cache.sent_history(*dev, key)
            if fwn.is_echo(dev[1], key, s["value"], sent) and fwn.is_echo(dev[1], key, s["last"], sent):
                continue
            name = f"{dev[0]}/{dev[1]} {key}"
            fw = self.foreign_writes.get(name)
            if fw is not None:
                fw["count"] += s["count"]
                continue
            fw = self.foreign_writes[name] = {"device": dev[1], "key": key, "first_t": round(s["t"], 4),
                                              "value": s["value"], "count": s["count"]}
            new.append(fw)
        for device, writes in fwn.by_device(new).items():
            if device not in self._warned:
                self._warned.add(device)
                log.warning("%s", fwn.message(device, writes, self.agent_state(), self.robot_language))

    def _set_mode(self, mode: str | None) -> None:
        self.mode = mode
        self.mode_changed.set()

    def _lockstep_engaged(self, driver: LockstepDriver) -> None:
        self.driver = driver
        self._set_mode("lockstep")

    def _lockstep_released(self) -> None:
        """The extension let the robot's clock run free on its own (its 2 s watchdog): scrimmage stalled. Not when
        scrimmage freed it on purpose for Phoenix 6 devices."""
        if self.phoenix:
            return
        if self.held and self.driver is not None and not self.done.is_set() and self.lockstep_released_at is None:
            self.lockstep_released_at = self.world.time
            log.warning("the robot program's clock ran free at t=%.3f (scrimmage stalled for more than 2 s); "
                        "results aren't repeatable", self.world.time)

    def _lockstep_ended(self, driver: LockstepDriver) -> None:
        if self.driver is driver:
            self.driver = None

    async def _send(self, frames: list[str]) -> None:
        ws = self.ws
        if ws is None or not frames:
            return
        proto, transport = getattr(ws, "protocol", None), getattr(ws, "transport", None)
        if (len(frames) > 1 and transport is not None and getattr(proto, "state", None) is WsState.OPEN
                and hasattr(proto, "send_text")):
            # websockets' Sans-I/O protocol frames them all, and one write sends them: the robot program reads a
            # step's sensors in one go rather than waking for each of 20 frames (on a 2-vCPU machine each wakeup
            # costs both programs time). Nothing awaits in between, so no other send can come between the frames.
            for f in frames:
                proto.send_text(f.encode())
            transport.write(b"".join(proto.data_to_send()))     # (open: no end-of-stream marker among them)
            return
        try:
            for f in frames:
                await ws.send(f)
        except websockets.ConnectionClosed:
            pass

    def _connected(self, conn: int) -> bool:
        """Is connection number `conn` still the live one (and are we still running)?"""
        return not self.done.is_set() and self.ws is not None and self.conn_id == conn

    def _restart_clock(self) -> None:
        """Simulated time back to 0: reset the world and resend every input."""
        self.world.reset()
        self.lockstep_released_at = None
        self.cache.reset()
        if self.phoenix_released:        # still in real time (attach mode's reset): keep counting from here
            self.cache.track_lag()
        self.foreign_writes = {}
        self._suspects = {}
        self.robot_behind = 0.0
        self.physics_skips, self.physics_skipped = 0, 0.0
        self._warned = set()
        self._devices_checked = False
        self._rev_unbound = set()
        self._rev_present = set()
        self._ph_present = set()
        self._rev_checked = False
        self._ticks = _Ticks()
        self.recorder.reset()
        if self.nt_link is not None:
            self.nt_link.reset_balls()   # the world was reset: send the balls' poses again

    def _rewind_log(self) -> None:
        """Drop the log rows written since the current connection began."""
        if self._logf:
            self._logf.seek(self._log_mark)
            self._logf.truncate()
            self._logf.flush()

    # ---------------------------------------------------------------- optional extras
    def _setup_nt(self) -> NetworkTablesLink | None:
        if not self.want_nt:
            return None
        try:
            return NetworkTablesLink(self.odometry_topic, port=self.nt_port)
        except NTUnavailable as e:
            log.info("%s; skipping NetworkTables", e)
            return None

    def _setup_viewer(self):
        if not self.want_viewer:
            return None
        import mujoco.viewer
        return mujoco.viewer.launch_passive(self.world.model, self.world.data)

    def _open_log(self) -> None:
        if not self.log_path:
            return
        w = self.world
        self._logf = open(self.log_path, "w", newline="")
        self._writer = csv.writer(self._logf)
        self._writer.writerow(["t", "mode", "x", "y", "heading_deg", "speed", "vbatt", "odo_x", "odo_y", "odo_deg"]
                              + [f"{m.name}_cmd" for m in w.motors] + [f"{m.name}_amps" for m in w.motors])

    def _log_row(self, now: float, mode: str) -> None:
        w = self.world
        x, y, yaw = w.robot_pose()
        odo = None
        if self.nt_link is not None:
            self.nt_link.publish_truth(x, y, yaw)
            if w.test_range is not None:
                self.nt_link.publish_balls(w.test_range.ball_poses())
            odo = self.nt_link.odometry()
        self.recorder.sample(now, x, y, odo)
        if self._writer:
            vals = list(odo) if odo is not None else [math.nan] * 3
            self._writer.writerow([f"{now:.3f}", mode, f"{x:.4f}", f"{y:.4f}", f"{math.degrees(yaw):.2f}",
                                   f"{w.robot_speed():.3f}", f"{w.vbatt:.2f}",
                                   *[f"{v:.4f}" for v in vals],
                                   *[f"{m.command:.3f}" for m in w.motors],
                                   *[f"{m.current:.1f}" for m in w.motors]])

    def _sync_viewer(self) -> None:
        """Refresh the viewer at most VIEWER_PERIOD of wall time apart, so fast runs aren't held back by it."""
        if self._viewer is None:
            return
        wall = time.monotonic()
        if wall - self._last_view < VIEWER_PERIOD:
            return
        self._last_view = wall
        if not self._viewer.is_running():
            self.done.set()
            return
        self._viewer.sync()

    # ---------------------------------------------------------------- per-tick work, shared by both drivers
    async def _push_sensors(self) -> None:
        await self._send(self.cache.frames(self.world.sensor_updates(self.hal)))
        self.settle_foreign_writes()

    async def _on_tick(self, now: float) -> None:
        """Periodic extras, on simulated time since the robot program connected."""
        t = self._ticks
        rev_ok = self._check_rev_available()
        if not self._devices_checked and now >= MISSING_AFTER - 1e-6:   # summed timesteps fall just short
            self._devices_checked = True
            msgs = missing_device_warnings(self.hal, self._expected)
            if rev_ok:  # otherwise the REVLib error above already says why no SPARK showed up
                msgs += missing_rev_warnings(self.hal, self._rev_expected, self._rev_motor_keys,
                                             self._rev_encoder_keys, self.agent_state(),
                                             self.robot_language)
            msgs += missing_scrimmage_warnings(self.hal, self._scrimmage_expected, self.sim_mode)
            if self.world.pneumatics is not None:
                msgs += self.world.pneumatics.warn_missing_hubs(self.hal)   # in the run's notes too
            for msg in msgs:
                log.warning("%s", msg)
        if (not self._fast_phoenix_warned and now >= MISSING_AFTER - 1e-6
                and (msg := self.fast_phoenix_message) is not None):   # by then the robot has created its devices
            self._fast_phoenix_warned = True
            log.warning("%s", msg)
        if now - t.refresh >= FULL_REFRESH:
            t.refresh = now
            self.cache.clear()
        mode = self.ds.mode if self.ds.active else "external"
        if self.ds.active and (self.ds.changed or now - t.ds >= DS_PERIOD):
            t.ds = now
            self.ds.changed = False
            await self._send(self.ds.frames())
        if now - t.log >= LOG_PERIOD:
            t.log = now
            self._log_row(now, mode)
        self._sync_viewer()

    # ---------------------------------------------------------------- drivers
    async def _run_realtime(self, conn: int) -> str:
        """Step physics to keep pace with the wall clock. Returns "lockstep" as soon as the robot program
        shows the lockstep extension (unless Phoenix 6 devices keep it in real time), "done" if it must stop, or
        "disconnected"."""
        w = self.world
        wall0, sim0 = time.monotonic(), w.time
        last_sensor = -math.inf
        decided = False
        while self._connected(conn):
            if LOCKSTEP_DEVICE in self.hal.devices and not self.phoenix_released:
                if not self.phoenix:
                    return "lockstep"
                await self.release_for_phoenix()   # Phoenix 6 devices appeared before lockstep engaged
            if self.phoenix_released:
                decided = True                     # released on purpose: real time whatever the speed
            if not decided and time.monotonic() - wall0 > DETECT_WINDOW:
                decided = True
                if self.speed != 1.0 and not self.held:
                    log.error("--speed %s needs the scrimmage_lockstep extension, but the robot program didn't "
                              "load it (RobotPy: add --scrimmage-lockstep)", "max" if self.speed is None else self.speed)
                    self.exit_code = 2
                    self.done.set()
                    return "done"
                log.info("lockstep extension not loaded; running in real time")
                self._set_mode("realtime")
            target = time.monotonic() - wall0
            lag = target - (w.time - sim0)
            if lag > MAX_LAG:
                log.warning("physics fell %.0f ms behind real time; skipping ahead", 1000 * lag)
                self.physics_skips += 1
                self.physics_skipped += lag
                sim0 = w.time - target
            # One pass simulates at most SENSOR_PERIOD: behind real time, the passes come back to back, each taking
            # the robot program's latest commands and pushing the sensors. Catching up the whole lag in one pass
            # (up to MAX_LAG) would leave the robot's control loops blind that long and its commands that stale.
            stop = min(target, w.time - sim0 + SENSOR_PERIOD)
            while (w.time - sim0) < stop:
                w.step(self.hal)
            now = w.time
            await self._on_tick(now)
            if now - last_sensor >= SENSOR_PERIOD - 1e-9:     # (5 steps of 1 ms may sum to just under it)
                last_sensor = now
                await self._push_sensors()
            await asyncio.sleep(PHYSICS_TICK if w.time - sim0 >= target else 0)
        return "disconnected"

    async def _physics(self) -> None:
        try:
            self.nt_link = self._setup_nt()
            self._viewer = self._setup_viewer()
            self._open_log()
            while not self.done.is_set():
                if self.ws is None:
                    await asyncio.sleep(0.05)   # idle until a robot program connects
                    self._sync_viewer()
                    continue
                conn = self.conn_id
                if await self._run_realtime(conn) == "lockstep":
                    await LockstepDriver(self, conn, MAX_LAG).run()
        finally:
            if self.nt_link is not None:
                self.nt_link.close()
            if self._logf:
                self._logf.close()
            if self._viewer is not None:
                self._viewer.close()

    async def run(self) -> None:
        # halsim_ws_client never answers a close frame, so don't wait the default 10 s for it
        async with websockets.serve(self._handler, self.host, self.port, max_size=None, close_timeout=1) as server:
            self.bound_port = server.sockets[0].getsockname()[1]
            self.listening.set()
            log.info("HALSim WebSocket server listening on ws://%s:%d/wpilibws", self.host, self.bound_port)
            physics = asyncio.create_task(self._physics())
            await self.done.wait()
            physics.cancel()
            try:
                await physics
            except asyncio.CancelledError:
                pass
            ws = self.ws  # the robot program may disconnect while we release its clock
            if ws is not None:
                if LOCKSTEP_DEVICE in self.hal.devices:
                    await self._send([frame(*LOCKSTEP_DEVICE, {">engage": False})])  # release the robot's clock
                await ws.close()
