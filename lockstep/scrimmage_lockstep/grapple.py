"""scrimmage's Grapple LaserCAN helper for RobotPy robots (a port of the Java agent's ScrimmageGrapple).

libgrapplefrc-py's own `LaserCAN(id)` can't be built in a desktop simulation: on Linux its wheel bundles a private
copy of the WPILib HAL that nothing initializes, and the constructor crashes the interpreter (see
docs/superpowers/specs/2026-11-13-vendor-distance-sensors-checks.md, section 4). So `install()` puts a stand-in class
with the real one's methods (`get_measurement`, `set_range`, `set_roi`, `set_timing_budget`) in its place: as
`libgrapplefrc.LaserCAN`, and in every module that already holds the real class under some name (`robotpy sim`
imports robot.py, and with it a `from libgrapplefrc import LaserCAN`, before it loads the extension).

Every stand-in LaserCAN gets a HAL SimDevice "ScrimmageGrapple:LaserCAN[id]" (made on the robot loop) whose inputs
scrimmage writes:

- distance_mm: the distance to what the sensor sees, in mm, raw (not capped by the ranging mode);
- status: STATUS_VALID when it sees something, STATUS_NO_TARGET when not.

`get_measurement()` returns None (the real class's answer with no CAN frames) until scrimmage has written both and a
robot loop has run, then a measurement with the real one's fields (status, distance_mm, ambient, mode, budget, roi;
mode, budget and roi are libgrapplefrc's own types). The LaserCAN starts in short ranging, as the firmware does; past
its mode's reach it reads no target, with distance_mm the reach. The ranging mode, timing budget and region of
interest robot code sets are recorded and mirrored in the measurement, and each setter returns a success result (a
real LaserCAN would accept them).

Robots without libgrapplefrc pay nothing: `install()` does nothing. The helper says "loaded" on the first LaserCAN.
Nothing here raises into the robot program: a problem is said once ("scrimmage grapple helper: unavailable: ...") and
the helper stops; its LaserCANs then read None. Not covered: a LaserCAN built before the extension loads (at robot.py's
import; the real class crashes there on Linux anyway), and the real class kept somewhere other than a module's names
(a class attribute, say).
"""

from __future__ import annotations

import operator
import sys
import threading

HELPER_PREFIX = "scrimmage grapple helper: "
# The status values scrimmage writes: libgrapplefrc's LASERCAN_STATUS_VALID_MEASUREMENT (0) and
# LASERCAN_STATUS_WEAK_SIGNAL (2), the no-target status chosen in
# docs/superpowers/specs/2026-11-13-vendor-distance-sensors-checks.md (not seen on hardware). They mirror
# scrimmage/sensors/runtime.py's LASERCAN_STATUS_VALID and LASERCAN_STATUS_NO_TARGET (a test pins them equal).
STATUS_VALID = 0
STATUS_NO_TARGET = 2
# The ranging modes' reaches (mm): libgrapplefrc's RangingMode javadoc, "up to 4m" (LONG) and "1.3m and lower"
# (SHORT) (GrappleRobotics/libgrapplefrc LaserCanInterface.java at 36fd307).
LONG_REACH_MM = 4000
SHORT_REACH_MM = 1300
# The firmware's defaults (GrappleRobotics/LaserCAN lasercan-impl/src/lib.rs lines 39-41): short ranging, ROI x 8, y 8,
# w 16, h 16, a 33 ms timing budget. A unit set otherwise only in GrappleHook isn't seen here.
DEFAULT_BUDGET_MS = 33
DEFAULT_ROI = (8, 8, 16, 16)
_UNWRITTEN = -(2 ** 31)  # an input scrimmage hasn't written yet (the Java helper's Integer.MIN_VALUE)

_lock = threading.Lock()
_installed = False
_pending: list = []         # guarded by _lock
_started = False            # guarded by _lock
_unavailable = False        # written under _lock
_api: _Api | None = None    # set once the helper has started
_lib: _Lib | None = None    # libgrapplefrc's types, set by install()
_callback = None            # the SimPeriodicBefore registration, kept for the life of the program
_live: list = []            # robot thread only (the sim-periodic callback)


def _say(text: str) -> None:
    print(HELPER_PREFIX + text, flush=True)


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("scrimmage-lockstep")
    except Exception:
        return "unknown"


def _describe(e: BaseException) -> str:
    return type(e).__name__ + (f": {e}" if str(e) else "")


def install() -> None:
    """Put the stand-in LaserCAN in libgrapplefrc's place; a no-op without libgrapplefrc (or when called again)."""
    global _installed, _lib
    if _installed:
        return
    try:
        import libgrapplefrc
    except ModuleNotFoundError as e:
        if e.name == "libgrapplefrc":  # libgrapplefrc-py isn't installed: nothing to do
            return
        _installed = True
        _mark_unavailable(_describe(e))
        return
    except Exception as e:  # installed but broken: say so, like a missing API
        _installed = True
        _mark_unavailable(_describe(e))
        return
    _installed = True
    try:
        _lib = _Lib(libgrapplefrc)
        _rebind(libgrapplefrc.LaserCAN, LaserCAN)
    except _Missing as m:
        _mark_unavailable(str(m))
    except Exception as e:
        _mark_unavailable(_describe(e))


def _rebind(original, stand_in) -> None:
    """Every module name bound to `original` (libgrapplefrc's own, and a robot's `from libgrapplefrc import LaserCAN`
    made before install) now names `stand_in`."""
    for mod in list(sys.modules.values()):
        if mod is sys.modules.get(__name__):
            continue
        try:
            names = [k for k, v in vars(mod).items() if v is original]
        except Exception:  # None (a blocked import) or a module without a plain namespace
            continue
        for k in names:
            try:
                setattr(mod, k, stand_in)
            except Exception:
                pass


def _register(sensor: _Sensor) -> None:
    """Called from every stand-in LaserCAN's constructor."""
    global _started
    try:
        with _lock:
            if _unavailable:
                return
            _pending.append(sensor)
            if _started:
                return
            _started = True
        _start()
    except Exception as e:
        _mark_unavailable("registering a LaserCAN: " + _describe(e))


def _start() -> None:
    """Resolve the HAL and hook the robot loop; any failure makes the helper unavailable, said once."""
    global _api, _callback
    try:
        api = _Api()
        _callback = api.register_callback(_on_loop)
        _api = api
        _say(f"loaded (python {_version()})")
    except _Missing as m:
        _mark_unavailable(str(m))
    except Exception as e:
        _mark_unavailable(_describe(e))


def _mark_unavailable(reason: str) -> None:
    global _unavailable
    with _lock:
        if _unavailable:
            return
        _unavailable = True
        _pending.clear()
    _say("unavailable: " + reason)


# -------------------------------------------------------------------------------------------- the robot loop


def _on_loop() -> None:
    loop_once()


def loop_once() -> None:
    """Once per robot loop, before simulationPeriodic: make new LaserCANs' devices, then take scrimmage's values."""
    if _unavailable:
        return
    try:
        with _lock:
            fresh = list(_pending)
            _pending.clear()
        for s in fresh:
            if s.open():
                _live.append(s)
        for s in _live:
            s.read()
    except Exception as e:
        _mark_unavailable("the robot-loop step: " + _describe(e))


# -------------------------------------------------------------------------------------------- one LaserCAN


class _Sensor:
    def __init__(self, can_id: int):
        self.id = can_id
        self.distance_mm = self.status = None  # the SimInt inputs (robot thread)
        # what robot code set (any thread), and scrimmage's last values, taken on the robot loop (read on any thread)
        self.is_long = False                    # the firmware starts in short ranging
        self.budget_ms = DEFAULT_BUDGET_MS
        self.roi = DEFAULT_ROI
        self.reading: tuple[int, int] | None = None  # (status, distance_mm), one assignment: a matched pair

    def open(self) -> bool:
        """Make the SimDevice; False (said once) when it can't be made: two LaserCANs with that id."""
        name = f"ScrimmageGrapple:LaserCAN[{self.id}]"
        device = _api.SimDevice(name)
        if not device:
            _say(f"LaserCAN {self.id} is not simulated: can't create its scrimmage device {name} "
                 "(two LaserCANs with that id?)")
            return False
        self.device = device  # robotpy frees the SimDevice with its last reference
        self.distance_mm = device.createInt("distance_mm", _api.k_input, _UNWRITTEN)
        self.status = device.createInt("status", _api.k_input, _UNWRITTEN)
        return True

    def read(self) -> None:
        mm, st = int(self.distance_mm.get()), int(self.status.get())
        if mm != _UNWRITTEN and st != _UNWRITTEN:
            self.reading = (st, mm)

    def measurement(self):
        r = self.reading
        if r is None:
            return None
        st, mm = r
        lng = self.is_long
        reach = LONG_REACH_MM if lng else SHORT_REACH_MM
        if st == STATUS_NO_TARGET or mm > reach:  # nothing within the mode's reach: no target, at the reach
            st, mm = STATUS_NO_TARGET, reach
        lib = _lib
        return Measurement(st, mm, 0, lib.Long if lng else lib.Short, lib.budgets[self.budget_ms], lib.Roi(*self.roi))


class Measurement:
    """What the stand-in's get_measurement() returns: libgrapplefrc's LaserCanMeasurement's fields (that class can't
    be built from Python)."""

    __slots__ = ("status", "distance_mm", "ambient", "mode", "budget", "roi")

    def __init__(self, status, distance_mm, ambient, mode, budget, roi):
        self.status, self.distance_mm, self.ambient = status, distance_mm, ambient
        self.mode, self.budget, self.roi = mode, budget, roi

    def __repr__(self) -> str:
        return (f"Measurement(status={self.status}, distance_mm={self.distance_mm}, ambient={self.ambient}, "
                f"mode={self.mode!r}, budget={self.budget!r}, roi=({self.roi.x}, {self.roi.y}, {self.roi.w}, "
                f"{self.roi.h}))")


class GrappleResult:
    """What the stand-in's setters return: libgrapplefrc's GrappleResult's fields, a success (`error` None)."""

    __slots__ = ("error", "ok")

    def __init__(self):
        self.error, self.ok = None, ()

    def __repr__(self) -> str:
        return "GrappleResult(error=None, ok=())"


def _argument(value, name: str, cls):
    """libgrapplefrc's own TypeError for an argument of the wrong type (pyo3's wording)."""
    if not isinstance(value, cls):
        raise TypeError(f"argument '{name}': '{type(value).__name__}' object cannot be converted to "
                        f"'{cls.__name__}'")
    return value


class LaserCAN:
    """scrimmage's stand-in for libgrapplefrc.LaserCAN: the same methods, fed from scrimmage's physics (see the
    module's docstring)."""

    def __init__(self, can_id):
        can_id = operator.index(can_id)
        if not 0 <= can_id <= 255:  # the real constructor takes a u8
            raise OverflowError("can't convert negative int to unsigned" if can_id < 0
                                else "out of range integral type conversion attempted")
        self._scrimmage = _Sensor(can_id)
        _register(self._scrimmage)

    def __repr__(self) -> str:
        return f"<scrimmage LaserCAN {self._scrimmage.id}>"

    def get_measurement(self):
        """scrimmage's reading, or None (no reading yet, or the helper is unavailable)."""
        if _unavailable or _lib is None:
            return None
        try:
            return self._scrimmage.measurement()
        except Exception as e:
            _mark_unavailable("reading a LaserCAN: " + _describe(e))
            return None

    def set_range(self, mode) -> GrappleResult:
        """Record the ranging mode (libgrapplefrc.LaserCanRangingMode); always succeeds."""
        self._scrimmage.is_long = _argument(mode, "mode", _lib.RangingMode) == _lib.Long
        return GrappleResult()

    def set_timing_budget(self, budget) -> GrappleResult:
        """Record the timing budget (libgrapplefrc.LaserCanTimingBudget); always succeeds."""
        ms = int(_argument(budget, "budget", _lib.TimingBudget))
        if ms in _lib.budgets:
            self._scrimmage.budget_ms = ms
        return GrappleResult()

    def set_roi(self, roi) -> GrappleResult:
        """Record a copy of the region of interest (libgrapplefrc.LaserCanRoi); always succeeds."""
        _argument(roi, "roi", _lib.Roi)
        self._scrimmage.roi = (int(roi.x), int(roi.y), int(roi.w), int(roi.h))
        return GrappleResult()


# -------------------------------------------------------------------------------------------- libgrapplefrc / HAL


class _Missing(Exception):
    pass


class _Lib:
    """The libgrapplefrc types the stand-in uses, checked up front so a missing one is said plainly."""

    def __init__(self, g):
        _need(g, "libgrapplefrc", "LaserCAN", ["get_measurement", "set_range", "set_roi", "set_timing_budget"])
        self.RangingMode = _need(g, "libgrapplefrc", "LaserCanRangingMode", ["Long", "Short"])
        self.Long, self.Short = self.RangingMode.Long, self.RangingMode.Short
        names = ["TB20ms", "TB33ms", "TB50ms", "TB100ms"]
        self.TimingBudget = _need(g, "libgrapplefrc", "LaserCanTimingBudget", names)
        self.budgets = {int(getattr(self.TimingBudget, n)): getattr(self.TimingBudget, n) for n in names}
        if DEFAULT_BUDGET_MS not in self.budgets:
            raise _Missing(f"no libgrapplefrc.LaserCanTimingBudget of {DEFAULT_BUDGET_MS} ms")
        self.Roi = _need(g, "libgrapplefrc", "LaserCanRoi", [])
        roi = self.Roi(*DEFAULT_ROI)
        if (roi.x, roi.y, roi.w, roi.h) != DEFAULT_ROI:
            raise _Missing("libgrapplefrc.LaserCanRoi(x, y, w, h) doesn't keep its values")


class _Api:
    """The HAL pieces the helper uses, checked up front so a missing one is said plainly."""

    def __init__(self):
        try:
            import hal
            import hal.simulation
        except ImportError as e:
            raise _Missing(f"can't import {e.name or e}") from e
        self.SimDevice = _need(hal, "hal", "SimDevice", ["createInt", "Direction"])
        self.k_input = getattr(self.SimDevice.Direction, "kInput", None)
        if self.k_input is None:
            raise _Missing("no hal.SimDevice.Direction.kInput")
        self.register_callback = _need(hal.simulation, "hal.simulation", "registerSimPeriodicBeforeCallback", [])


def _need(module, module_name: str, name: str, attrs: list[str]):
    obj = getattr(module, name, None)
    if obj is None:
        raise _Missing(f"no {module_name}.{name}")
    for attr in attrs:
        if not hasattr(obj, attr):
            raise _Missing(f"no {module_name}.{name}.{attr}")
    return obj
