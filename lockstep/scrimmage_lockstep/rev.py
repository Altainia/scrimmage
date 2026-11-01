"""scrimmage's REV SPARK helper for RobotPy robots (a port of the Java agent's ScrimmageRev).

Every SPARK the robot program creates is registered here (`install()` wraps `rev.SparkMax.__init__` and
`rev.SparkFlex.__init__` in place, so `import rev` and `from rev import SparkMax` both see it); once per robot loop,
before simulationPeriodic, the helper drives REV's own SparkSim for each SPARK with scrimmage's physics values,
exchanged through a HAL SimDevice "ScrimmageREV:SparkMax[id]" / "ScrimmageREV:SparkFlex[id]" per SPARK:

- inputs (written by scrimmage): position (motor shaft rotations since the start, physical frame; NaN until
  scrimmage writes it), velocity (motor shaft rpm, physical frame), vbus (V), current (A), absRotations
  (absolute-encoder magnet rotations, unwrapped), fwdLimit, revLimit, motor (model code: 1 NEO, 2 NEO 550,
  3 NEO Vortex, else the SPARK's usual motor);
- output (read by scrimmage): motorVoltage, the voltage across the physical motor (after the SPARK's inversion and
  follower mirroring).

`SparkMaxSim.iterate` / `SparkFlexSim.iterate` called by the robot program are ignored and counted (scrimmage's
physics owns the simulation), unless REV is unavailable. Nothing here raises into the robot program: a problem is
said once on the console and the helper stops (or drops the one SPARK).
"""

from __future__ import annotations

import functools
import math
import sys
import threading
import time
import weakref

HELPER_PREFIX = "scrimmage rev helper: "
REPORT_FIRST_DELAY = 0.1  # s after the first ignored write, so even short runs have a summary
REPORT_PERIOD = 5.0
_GUARD_KEY = "SparkSim.iterate"  # the same key as the Java agent's

_lock = threading.Lock()
_inside = threading.local()      # .on: the helper itself is driving REV's simulation (this thread)
_installed = False
_pending: list = []              # guarded by _lock
_started = False                 # guarded by _lock
_unavailable = False             # written under _lock
_api: _Api | None = None
_known = weakref.WeakSet()       # every SPARK registered (guarded by _lock)
_driven = weakref.WeakSet()      # the SPARKs the helper drives or is about to (guarded by _lock)
_sim_spark = weakref.WeakKeyDictionary()  # a SparkSim the robot built (after install) -> its SPARK
_callback = None                 # the SimPeriodicBefore registration, kept for the life of the program
# robot thread only (the sim-periodic callback)
_sparks: list[_Spark] = []
_last_time: float | None = None
_last_failure: str | None = None
# the iterate guard's summary
_counts: dict[str, int] = {}
_first_caller: dict[str, str] = {}
_last_printed = -1
_reporter_started = False


def _say(text: str) -> None:
    print(HELPER_PREFIX + text, flush=True)


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("scrimmage-lockstep")
    except Exception:
        return "unknown"


def install() -> None:
    """Wrap the SPARK constructors and guard SparkSim.iterate; a no-op without robotpy-rev (or when called again)."""
    global _installed
    if _installed:
        return
    broken = None
    try:
        import rev
    except ModuleNotFoundError as e:
        if e.name == "rev":  # robotpy-rev isn't installed: nothing to do
            return
        broken = e
    except Exception as e:  # installed but broken: say so, like a missing API
        broken = e
    _installed = True
    _say(f"loaded (python {_version()})")
    if broken is not None:
        _mark_unavailable(_describe(broken))
        return
    try:
        for name in ("SparkMax", "SparkFlex"):
            cls = getattr(rev, name, None)
            if cls is not None:
                _wrap_init(cls, _spark_created)
        # the base class first: a subclass that inherits its (now wrapped) methods is left alone
        for name in ("SparkSim", "SparkMaxSim", "SparkFlexSim"):
            cls = getattr(rev, name, None)
            if cls is not None:
                _wrap_init(cls, _sim_created)
                if hasattr(cls, "iterate"):
                    _guard_iterate(cls)
    except Exception as e:
        _mark_unavailable(_describe(e))


def _wrap_init(cls, after) -> None:
    """After the class's own constructor, call `after(self, *args, **kwargs)` (never raising into the robot)."""
    original = cls.__init__
    if getattr(original, "_scrimmage_wrapped", False):
        return

    @functools.wraps(original)
    def __init__(self, *args, **kwargs):
        original(self, *args, **kwargs)
        after(self, *args, **kwargs)

    __init__._scrimmage_wrapped = True
    cls.__init__ = __init__


def _spark_created(spark, *args, **kwargs) -> None:
    _register(spark)


def _sim_created(sim, *args, **kwargs) -> None:
    """A SparkSim was built: note its SPARK, and register a SPARK made before install() (robot.py imported before
    loadExtension) so the helper drives it."""
    try:
        spark = args[0] if args else next((kwargs[k] for k in ("spark", "sparkMax", "sparkFlex") if k in kwargs), None)
        if spark is None:
            return
        _sim_spark[sim] = spark
        with _lock:
            known = spark in _known
        if not known:
            _register(spark)
    except Exception as e:
        _fail("noting a SparkSim", e)


def _drives(sim) -> bool:
    """Does the helper drive this SparkSim's SPARK? (A sim built before install() isn't known: no.)"""
    spark = _sim_spark.get(sim)
    with _lock:
        return spark is not None and spark in _driven


def _guard_iterate(cls) -> None:
    original = cls.iterate
    if getattr(original, "_scrimmage_wrapped", False):
        return

    @functools.wraps(original)
    def iterate(self, *args, **kwargs):
        try:
            through = _unavailable or getattr(_inside, "on", False) or not _drives(self)
        except Exception:
            through = True
        if through:
            return original(self, *args, **kwargs)
        try:
            _blocked(_GUARD_KEY)
        except Exception as e:
            _fail("counting a robot-side SparkSim.iterate", e)
        return None

    iterate._scrimmage_wrapped = True
    cls.iterate = iterate


def _register(spark) -> None:
    """Called at the end of every SPARK constructor, with the new SPARK."""
    global _started
    try:
        with _lock:
            if _unavailable or spark in _known:
                return
            _known.add(spark)
            _driven.add(spark)
            _pending.append(spark)
            if _started:
                return
            _started = True
        _start()
    except Exception as e:
        _fail("registering a SPARK", e)


def _start() -> None:
    """Resolve robotpy-rev/WPILib and hook the robot loop; any failure makes REV unavailable, said once."""
    global _api, _callback
    try:
        _api = _Api()
        _callback = _api.register_callback(_on_loop)
    except _Missing as m:
        _mark_unavailable(str(m))
    except Exception as e:
        _mark_unavailable(_describe(e))


def _mark_unavailable(reason: str) -> None:
    global _unavailable
    with _lock:
        _unavailable = True
        _pending.clear()
    _say("REV sim unavailable: " + reason)


# -------------------------------------------------------------------------------------------- the robot loop


def _on_loop() -> None:
    try:
        now = _api.fpga_time()
    except Exception as e:
        _fail("the REV robot-loop step", e)
        return
    loop_once(now)


def loop_once(now: float) -> None:
    """One robot loop: drive every registered SPARK's SparkSim for the time since the previous loop."""
    global _last_time
    _inside.on = True
    try:
        dt = 0.02 if _last_time is None else now - _last_time
        _last_time = now
        _add_pending()
        by_id: dict[int, _Spark] = {}
        for s in list(_sparks):
            try:
                s.prepare(dt)
                by_id.setdefault(s.id, s)
            except Exception as e:
                _drop(s, e)
        for s in list(_sparks):
            try:
                if s.leader_id == 0:
                    s.drive(dt)
            except Exception as e:
                _drop(s, e)
                if by_id.get(s.id) is s:
                    del by_id[s.id]
        for s in list(_sparks):
            try:
                if s.leader_id != 0:
                    s.follow(by_id)
                s.publish()
            except Exception as e:
                _drop(s, e)
    except Exception as e:
        _fail("the REV robot-loop step", e)
    finally:
        _inside.on = False


def _add_pending() -> None:
    with _lock:
        if not _pending:
            return
        fresh = list(_pending)
        _pending.clear()
    for base in fresh:
        try:
            s = _Spark(base)
            if s.device:
                _sparks.append(s)
                continue
        except Exception as e:
            _fail("setting up a SPARK", e)
        _undriven(base)


def _drop(s: _Spark, e: BaseException) -> None:
    _say(f"{s.label} is no longer simulated: {_describe(e)}")
    if s in _sparks:
        _sparks.remove(s)
    _undriven(s.base)
    s.device = None  # robotpy frees the SimDevice with its last reference


def _undriven(spark) -> None:
    """The helper won't drive this SPARK: the robot's own SparkSim.iterate on it goes through again."""
    with _lock:
        _driven.discard(spark)


def _fail(what: str, e: BaseException) -> None:
    global _last_failure
    msg = _describe(e)
    if msg != _last_failure:  # once per distinct problem, not 50 times a second
        _last_failure = msg
        _say(f"REV helper failed in {what}: {msg}")


def _describe(e: BaseException) -> str:
    return type(e).__name__ + (f": {e}" if str(e) else "")


def _mod1(x: float) -> float:
    return x - math.floor(x)


def _round(x: float) -> int:
    """Java's Math.round: half up, NaN -> 0 (and +-inf to no motor code)."""
    return math.floor(x + 0.5) if math.isfinite(x) else 0


def _factor(f: float) -> float:
    return 1.0 if f == 0.0 else f  # REV treats a zero conversion factor as 1


# -------------------------------------------------------------------------------------------- one SPARK


class _Spark:
    def __init__(self, base):
        api = _api
        self.base = base
        self.flex = isinstance(base, api.SparkFlex)
        self.id = int(base.getDeviceId())
        self.label = ("SPARK Flex " if self.flex else "SPARK MAX ") + str(self.id)
        self.config = base.configAccessor
        name = "ScrimmageREV:" + ("SparkFlex[" if self.flex else "SparkMax[") + f"{self.id}]"
        device = api.SimDevice(name)
        self.device = device if device else None
        if not self.device:
            _say(f"{self.label} is not simulated: can't create its scrimmage device {name} (two SPARKs with that id?)")
            return
        k_in, k_out = api.SimDevice.Direction.kInput, api.SimDevice.Direction.kOutput
        self.position = device.createDouble("position", k_in, math.nan)
        self.velocity = device.createDouble("velocity", k_in, 0.0)
        self.vbus = device.createDouble("vbus", k_in, 12.0)
        self.current = device.createDouble("current", k_in, 0.0)
        self.abs_rotations = device.createDouble("absRotations", k_in, 0.0)
        self.motor = device.createDouble("motor", k_in, 0.0)
        self.fwd_limit = device.createBoolean("fwdLimit", k_in, False)
        self.rev_limit = device.createBoolean("revLimit", k_in, False)
        self.motor_voltage = device.createDouble("motorVoltage", k_out, 0.0)
        self.sim = self.abs_sim = self.fwd_sim = self.rev_sim = self.rel_sim = None
        self.motor_model: str | None = None  # the DCMotor factory the SparkSim was built with
        self.leader_id = 0                   # 0: not a follower
        self.inverted = self.follow_inverted = self.warned_leader = False
        self.abs_pos = self.abs_vel = 0.0
        self.prev_abs = math.nan
        self.applied, self.phys_sign = 0.0, 1.0
        # the built-in encoder, in the robot's units: set from the motor's real rotation (not integrated), plus the
        # offset the robot program's own setPosition calls make
        self.rel_pos = self.rel_offset = self.last_rel = math.nan
        self.rel_vel = 0.0
        self.enc_pos_f = self.enc_vel_f = 1.0
        self.rel_from_iterate = False

    def prepare(self, dt: float) -> None:
        """Read the config and inputs; (re)build the SparkSim; set the limit switches and absolute encoder."""
        api, config = _api, self.config
        self.leader_id = int(config.getFollowerModeLeaderId())
        self.follow_inverted = bool(config.getFollowerModeInverted())
        self.inverted = bool(config.getInverted())
        code = _round(self.motor.get())
        model = {1: "NEO", 2: "NEO550", 3: "neoVortex"}.get(code, "neoVortex" if self.flex else "NEO")
        if model != self.motor_model:  # only a different motor: a new SparkSim starts REV's filters over
            sim_cls = api.SparkFlexSim if self.flex else api.SparkMaxSim
            self.sim = sim_cls(self.base, getattr(api.DCMotor, model)(1))
            self.motor_model = model
            self.abs_sim = self.sim.getAbsoluteEncoderSim()
            self.fwd_sim = self.sim.getForwardLimitSwitchSim()
            self.rev_sim = self.sim.getReverseLimitSwitchSim()
            self.rel_sim = self.sim.getRelativeEncoderSim()
        self.fwd_sim.setPressed(bool(self.fwd_limit.get()))
        self.rev_sim.setPressed(bool(self.rev_limit.get()))

        abs_cfg = config.absoluteEncoder
        abs_inverted = bool(abs_cfg.getInverted())
        r = self.abs_rotations.get()
        # REVLib: the zero offset is the reading at the zero position as if not inverted (and factor 1)
        from_zero = r - abs_cfg.getZeroOffset()
        self.abs_pos = _mod1(-from_zero if abs_inverted else from_zero) * _factor(abs_cfg.getPositionConversionFactor())
        if dt > 0:
            rpm = 0.0 if math.isnan(self.prev_abs) else (r - self.prev_abs) / dt * 60.0 * (-1.0 if abs_inverted else 1.0)
            self.abs_vel = rpm * _factor(abs_cfg.getVelocityConversionFactor())
            self.prev_abs = r
        self.abs_sim.setPosition(self.abs_pos)
        self.abs_sim.setVelocity(self.abs_vel)

        # the built-in encoder counts the motor's rotation in the SPARK's frame (a follower: its own inverted setting)
        enc = config.encoder
        self.enc_pos_f = _factor(enc.getPositionConversionFactor())
        self.enc_vel_f = _factor(enc.getVelocityConversionFactor())
        sign = -1.0 if self.inverted else 1.0
        rot = self.position.get()
        self.rel_vel = self.velocity.get() * sign * self.enc_vel_f
        self.rel_from_iterate = False
        cur = self.rel_sim.getPosition()
        if math.isnan(self.rel_offset):
            self.rel_offset = cur                      # counts from the start (power-on), keeping a value the robot set
        elif not math.isnan(self.last_rel) and cur != self.last_rel:
            self.rel_offset += cur - self.last_rel     # the robot program set the encoder's position
        if math.isnan(rot):  # scrimmage hasn't sent it (yet, or the SPARK is unbound): leave the encoder to REV
            self.rel_pos = math.nan
            return
        self.rel_pos = rot * sign * self.enc_pos_f + self.rel_offset

    def drive(self, dt: float) -> None:
        """A leader (or a SPARK on its own): run REV's simulated controller for dt."""
        self.phys_sign = -1.0 if self.inverted else 1.0
        if dt > 0:
            sensor = getattr(self.config.closedLoop.getFeedbackSensor(), "name", None)
            on_abs, on_primary = sensor == "kAbsoluteEncoder", sensor == "kPrimaryEncoder"
            # iterate integrates its position from the velocity: start it so that it lands on the real position,
            # which its closed loop then uses (an integral of sampled velocities drifts, e.g. against a hard stop)
            if on_abs:
                abs_cfg = self.config.absoluteEncoder
                abs_pos_f = _factor(abs_cfg.getPositionConversionFactor())
                abs_vel_f = _factor(abs_cfg.getVelocityConversionFactor())
                v = self.abs_vel
                self.sim.setPosition(self.abs_pos - v / abs_vel_f / 60.0 * dt * abs_pos_f)
            else:
                v = self.velocity.get() * self.phys_sign * self.enc_vel_f
                if on_primary and not math.isnan(self.rel_pos):
                    self.sim.setPosition(self.rel_pos - v / self.enc_vel_f / 60.0 * dt * self.enc_pos_f)
                    self.rel_from_iterate = True  # iterate sets the encoder's (filtered, noisy) velocity
            self.sim.iterate(v, max(self.vbus.get(), 0.1), dt)
            if on_abs:  # iterate mirrors its integrated position into the absolute encoder: put the real one back
                self.abs_sim.setPosition(self.abs_pos)
                self.abs_sim.setVelocity(self.abs_vel)
        self.applied = self.sim.getAppliedOutput()

    def follow(self, by_id: dict[int, _Spark]) -> None:
        """A follower: mirror its leader's output (negated if it follows inverted)."""
        top: _Spark | None = self
        sign = 1.0
        depth = 0
        while top is not None and top.leader_id != 0 and depth < 8:
            sign *= -1.0 if top.follow_inverted else 1.0
            nxt = by_id.get(top.leader_id)
            if nxt is None or nxt is top:
                if not self.warned_leader:
                    self.warned_leader = True
                    _say(f"{self.label} follows CAN id {top.leader_id}, but the robot program has no SPARK with that "
                         "id; its output stays 0")
                top = None
                break
            top = nxt
            depth += 1
        if top is None or top.leader_id != 0:  # missing leader, or a follower loop
            self.applied, self.phys_sign = 0.0, 1.0
        else:
            self.applied = top.applied * sign
            self.phys_sign = top.phys_sign  # a follower mirrors its leader's physical output
        self.sim.setAppliedOutput(self.applied)
        self.sim.setBusVoltage(max(self.vbus.get(), 0.1))

    def publish(self) -> None:
        """The physics current into REV, and the motor voltage out to scrimmage."""
        if not math.isnan(self.rel_pos):
            self.rel_sim.setPosition(self.rel_pos)
            if not self.rel_from_iterate:
                self.rel_sim.setVelocity(self.rel_vel)
        self.last_rel = self.rel_sim.getPosition()
        self.sim.setMotorCurrent(self.current.get())
        self.motor_voltage.set(self.applied * self.vbus.get() * self.phys_sign)


# -------------------------------------------------------------------------------------------- the iterate guard


def _blocked(key: str) -> None:
    """A robot-side sim write was ignored: count it, note its first caller, and make sure summaries get printed."""
    global _reporter_started
    with _lock:
        _counts[key] = _counts.get(key, 0) + 1
        start = not _reporter_started
        _reporter_started = True
    if key not in _first_caller:
        _first_caller[key] = _caller()
    if start:
        _start_reporter()


def _caller() -> str:
    """The first frame outside robotpy-rev and this helper: `module.qualname` (lambdas as `outer (lambda)`)."""
    f = sys._getframe(1)
    while f is not None:
        mod = f.f_globals.get("__name__", "?")
        if not (mod == __name__ or mod == "rev" or mod.startswith("rev.") or mod.startswith("scrimmage_lockstep")):
            qual = getattr(f.f_code, "co_qualname", f.f_code.co_name).replace(".<locals>", "")
            if qual.endswith(".<lambda>"):  # like the Java agent's lambda$startSimThread$5 -> startSimThread (lambda)
                qual = qual[: -len(".<lambda>")] + " (lambda)"
            return f"{mod}.{qual}"
        f = f.f_back
    return "?"


def _start_reporter() -> None:
    def run():
        time.sleep(REPORT_FIRST_DELAY)
        while True:
            report()
            time.sleep(REPORT_PERIOD)

    threading.Thread(target=run, name="scrimmage-rev-report", daemon=True).start()


def report() -> None:
    """Print the ignored-writes summary if it changed since the last one."""
    global _last_printed
    with _lock:
        total = sum(_counts.values())
        if total == 0 or total == _last_printed:
            return
        _last_printed = total
        top = sorted(_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    listed = "; ".join(f"{k} from {_first_caller.get(k, '?')}" for k, _ in top)
    _say(f"ignored {total} robot-side sim writes ({listed})")


# -------------------------------------------------------------------------------------------- robotpy-rev / WPILib


class _Missing(Exception):
    pass


class _Api:
    """The robotpy-rev, HAL and WPILib pieces the helper uses, checked up front so a missing one is said plainly."""

    def __init__(self):
        try:
            import hal
            import hal.simulation
            import rev
            import wpilib
            from wpimath.system.plant import DCMotor
        except ImportError as e:
            raise _Missing(f"can't import {e.name or e}") from e
        self.SparkMax = _need(rev, "rev", "SparkMax", ["getDeviceId", "configAccessor"])
        self.SparkFlex = _need(rev, "rev", "SparkFlex", ["getDeviceId", "configAccessor"])
        sim_methods = ["iterate", "setPosition", "setAppliedOutput", "getAppliedOutput", "setMotorCurrent",
                       "setBusVoltage", "getAbsoluteEncoderSim", "getForwardLimitSwitchSim",
                       "getReverseLimitSwitchSim", "getRelativeEncoderSim"]
        self.SparkMaxSim = _need(rev, "rev", "SparkMaxSim", sim_methods)
        self.SparkFlexSim = _need(rev, "rev", "SparkFlexSim", sim_methods)
        _need(rev, "rev", "SparkBaseConfigAccessor", ["getInverted", "getFollowerModeLeaderId",
                                                      "getFollowerModeInverted", "encoder", "absoluteEncoder",
                                                      "closedLoop"])
        _need(rev, "rev", "EncoderConfigAccessor", ["getPositionConversionFactor", "getVelocityConversionFactor"])
        _need(rev, "rev", "AbsoluteEncoderConfigAccessor", ["getInverted", "getZeroOffset",
                                                            "getPositionConversionFactor",
                                                            "getVelocityConversionFactor"])
        _need(rev, "rev", "ClosedLoopConfigAccessor", ["getFeedbackSensor"])
        _need(rev, "rev", "SparkAbsoluteEncoderSim", ["setPosition", "setVelocity"])
        _need(rev, "rev", "SparkLimitSwitchSim", ["setPressed"])
        _need(rev, "rev", "SparkRelativeEncoderSim", ["getPosition", "setPosition", "setVelocity"])
        self.DCMotor = DCMotor
        for name in ("NEO", "NEO550", "neoVortex"):
            if not hasattr(DCMotor, name):
                raise _Missing(f"no wpimath.system.plant.DCMotor.{name}")
        self.SimDevice = _need(hal, "hal", "SimDevice", ["createDouble", "createBoolean", "Direction"])
        for name in ("kInput", "kOutput"):
            if not hasattr(self.SimDevice.Direction, name):
                raise _Missing(f"no hal.SimDevice.Direction.{name}")
        self.register_callback = _need(hal.simulation, "hal.simulation", "registerSimPeriodicBeforeCallback", [])
        self.fpga_time = getattr(_need(wpilib, "wpilib", "Timer", ["getFPGATimestamp"]), "getFPGATimestamp")


def _need(module, module_name: str, name: str, attrs: list[str]):
    obj = getattr(module, name, None)
    if obj is None:
        raise _Missing(f"no {module_name}.{name}")
    for attr in attrs:
        if not hasattr(obj, attr):
            raise _Missing(f"no {module_name}.{name}.{attr}")
    return obj
