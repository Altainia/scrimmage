"""The RobotPy REV helper (lockstep/scrimmage_lockstep/rev.py) against fake `rev`, `hal`, `wpilib` and `wpimath` modules.

The helper is a port of the Java agent's ScrimmageRev: these tests check the same rules (units, signs, zero offset,
followers, the position preset, dt, the vbus floor, rebuilding only on a motor change, the iterate guard). The fakes
model just enough of REV's SparkSim: `iterate` integrates its position from the velocity it's given and mirrors it
into the selected feedback sensor, the way REVLib does; the sensor sims belong to the SPARK, as REV's sim state does.
"""

import importlib.util
import math
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "lockstep" / "scrimmage_lockstep"
P = "scrimmage rev helper: "


# ---------------------------------------------------------------------------------------------------- fakes


class Value:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


class Sensor:
    """A SparkAbsoluteEncoderSim / SparkRelativeEncoderSim / SparkLimitSwitchSim stand-in."""

    def __init__(self):
        self.position = 0.0
        self.velocity = 0.0
        self.pressed = False

    def setPosition(self, p):
        self.position = p

    def getPosition(self):
        return self.position

    def setVelocity(self, v):
        self.velocity = v

    def getVelocity(self):
        return self.velocity

    def setPressed(self, p):
        self.pressed = p

    def getPressed(self):
        return self.pressed


class Enum:
    def __init__(self, name):
        self.name = name


def make_rev(missing=()):
    rev = types.ModuleType("rev")

    class FeedbackSensor:
        kPrimaryEncoder = Enum("kPrimaryEncoder")
        kAbsoluteEncoder = Enum("kAbsoluteEncoder")
        kAlternateOrExternalEncoder = Enum("kAlternateOrExternalEncoder")

    class EncoderConfigAccessor:
        def __init__(self):
            self.pos_f, self.vel_f = 1.0, 1.0

        def getPositionConversionFactor(self):
            return self.pos_f

        def getVelocityConversionFactor(self):
            return self.vel_f

    class AbsoluteEncoderConfigAccessor(EncoderConfigAccessor):
        def __init__(self):
            super().__init__()
            self.inverted, self.zero = False, 0.0

        def getInverted(self):
            return self.inverted

        def getZeroOffset(self):
            return self.zero

    class ClosedLoopConfigAccessor:
        def __init__(self):
            self.sensor = FeedbackSensor.kPrimaryEncoder

        def getFeedbackSensor(self):
            return self.sensor

    class SparkBaseConfigAccessor:
        # properties, as in robotpy-rev (the helper checks the API on the classes)
        encoder = property(lambda self: self._encoder)
        absoluteEncoder = property(lambda self: self._abs)
        closedLoop = property(lambda self: self._closed_loop)

        def __init__(self):
            self.inverted, self.leader, self.follow_inverted = False, 0, False
            self._encoder = EncoderConfigAccessor()
            self._abs = AbsoluteEncoderConfigAccessor()
            self._closed_loop = ClosedLoopConfigAccessor()

        def getInverted(self):
            if isinstance(self.inverted, Exception):
                raise self.inverted
            return self.inverted

        def getFollowerModeLeaderId(self):
            return self.leader

        def getFollowerModeInverted(self):
            return self.follow_inverted

    class SparkBase:
        class MotorType:
            kBrushless = "kBrushless"
            kBrushed = "kBrushed"

        configAccessor = property(lambda self: self._config)

        def __init__(self, deviceID, type):
            self.device_id, self.motor_type = deviceID, type
            self._config = SparkBaseConfigAccessor()
            self.duty = 0.0
            # REV's sim state belongs to the device: every SparkSim of this SPARK sees the same sensors
            self.abs, self.fwd, self.rev, self.rel = Sensor(), Sensor(), Sensor(), Sensor()

        def getDeviceId(self):
            return self.device_id

        def set(self, duty):
            self.duty = duty

    class SparkMax(SparkBase):
        pass

    class SparkFlex(SparkBase):
        pass

    class SparkSim:
        created = []

        def __init__(self, spark, motor):
            self.spark, self.motor = spark, motor
            self.position = 0.0
            self.applied = 0.0
            self.bus = None
            self.current = None
            self.iterations = []    # (velocity, vbus, dt)
            self.applied_sets = []
            self.fail = None
            SparkSim.created.append(self)

        def iterate(self, velocity, vbus, dt):
            if self.fail:
                raise self.fail
            self.iterations.append((velocity, vbus, dt))
            cfg = self.spark.configAccessor
            if cfg.closedLoop.sensor.name == "kAbsoluteEncoder":
                f = cfg.absoluteEncoder
                self.position += velocity / (f.vel_f or 1.0) / 60.0 * dt * (f.pos_f or 1.0)
                self.spark.abs.position, self.spark.abs.velocity = self.position, velocity
            elif cfg.closedLoop.sensor.name == "kPrimaryEncoder":
                f = cfg.encoder
                self.position += velocity / (f.vel_f or 1.0) / 60.0 * dt * (f.pos_f or 1.0)
                # REV low-pass filters the velocity it reports
                self.spark.rel.position, self.spark.rel.velocity = self.position, 0.9 * velocity
            self.applied = self.spark.duty  # "the controller": the duty cycle the robot asked for

        def setPosition(self, p):
            self.position = p

        def getAppliedOutput(self):
            return self.applied

        def setAppliedOutput(self, a):
            self.applied = a
            self.applied_sets.append(a)

        def setBusVoltage(self, v):
            self.bus = v

        def setMotorCurrent(self, a):
            self.current = a

        def getAbsoluteEncoderSim(self):
            return self.spark.abs

        def getForwardLimitSwitchSim(self):
            return self.spark.fwd

        def getReverseLimitSwitchSim(self):
            return self.spark.rev

        def getRelativeEncoderSim(self):
            return self.spark.rel

    class SparkMaxSim(SparkSim):
        def iterate(self, velocity, vbus, dt):  # robotpy-rev: each sim class may define its own iterate
            super().iterate(velocity, vbus, dt)

    class SparkFlexSim(SparkSim):
        pass

    for cls in (FeedbackSensor, EncoderConfigAccessor, AbsoluteEncoderConfigAccessor, ClosedLoopConfigAccessor,
                SparkBaseConfigAccessor, SparkBase, SparkMax, SparkFlex, SparkSim, SparkMaxSim, SparkFlexSim):
        setattr(rev, cls.__name__, cls)
    rev.SparkAbsoluteEncoderSim = rev.SparkRelativeEncoderSim = rev.SparkLimitSwitchSim = Sensor
    for name in missing:
        delattr(rev, name)
    return rev


class Fakes:
    def __init__(self, rev_missing=()):
        self.rev = make_rev(rev_missing)
        self.devices = {}
        self.callbacks = []
        self.now = 0.0
        fakes = self

        class SimDevice:
            class Direction:
                kInput, kOutput, kBidir = 0, 1, 2

            def __init__(self, name):
                self.name = name
                self.ok = name not in fakes.devices
                self.values = {}
                if self.ok:
                    fakes.devices[name] = self

            def __bool__(self):
                return self.ok

            def createDouble(self, name, direction, initial):
                v = self.values[name] = Value(initial)
                return v

            createBoolean = createDouble

        self.hal = types.ModuleType("hal")
        self.hal.SimDevice = SimDevice
        self.hal_sim = types.ModuleType("hal.simulation")
        self.hal_sim.registerSimPeriodicBeforeCallback = lambda cb: self.callbacks.append(cb) or ("SimCB", cb)
        self.hal.simulation = self.hal_sim
        self.wpilib = types.ModuleType("wpilib")
        self.wpilib.Timer = types.SimpleNamespace(getFPGATimestamp=lambda: fakes.now)

        class DCMotor:
            def __init__(self, name, n):
                self.name, self.n = name, n

            @staticmethod
            def NEO(n=1):
                return DCMotor("NEO", n)

            @staticmethod
            def NEO550(n=1):
                return DCMotor("NEO550", n)

            @staticmethod
            def neoVortex(n=1):
                return DCMotor("neoVortex", n)

        self.plant = types.ModuleType("wpimath.system.plant")
        self.plant.DCMotor = DCMotor
        self.wpimath = types.ModuleType("wpimath")
        self.wpimath.system = types.ModuleType("wpimath.system")
        self.wpimath.system.plant = self.plant

    def modules(self):
        return {"rev": self.rev, "hal": self.hal, "hal.simulation": self.hal_sim, "wpilib": self.wpilib,
                "wpimath": self.wpimath, "wpimath.system": self.wpimath.system, "wpimath.system.plant": self.plant}

    def device(self, spark):
        kind = "SparkFlex" if isinstance(spark, self.rev.SparkFlex) else "SparkMax"
        return self.devices[f"ScrimmageREV:{kind}[{spark.getDeviceId()}]"].values

    def sim(self, spark):
        """The helper's (latest) SparkSim for this SPARK."""
        return [s for s in self.rev.SparkSim.created if s.spark is spark][-1]


def load_package(monkeypatch):
    """Import scrimmage_lockstep (and so scrimmage_lockstep.rev) from the source tree, fresh, as `scrimmage_lockstep`."""
    for name in ("scrimmage_lockstep", "scrimmage_lockstep.rev"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location("scrimmage_lockstep", PKG_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PKG_DIR)])
    pkg = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "scrimmage_lockstep", pkg)
    spec.loader.exec_module(pkg)
    return pkg


@pytest.fixture
def env(monkeypatch, capsys):
    """Fake modules installed; the helper loaded from source and installed (its "loaded" line already read)."""
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.rev")
    monkeypatch.setattr(helper, "_start_reporter", lambda: None)  # tests call report() themselves
    helper.install()
    capsys.readouterr()
    fakes.helper = helper
    return fakes


def spark(env, id_, flex=False):
    cls = env.rev.SparkFlex if flex else env.rev.SparkMax
    return cls(id_, cls.MotorType.kBrushless)


def step(env, dt=0.02):
    env.now += dt
    env.helper.loop_once(env.now)


# ---------------------------------------------------------------------------------------------------- install


def test_install_is_a_no_op_without_rev(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "rev", None)  # `import rev` raises ImportError
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.rev")
    helper.install()
    assert capsys.readouterr().out == ""
    assert helper.HELPER_PREFIX == P


def test_install_says_loaded_with_the_package_version(monkeypatch, capsys):
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.rev")
    monkeypatch.setattr(helper, "_version", lambda: "9.8.7")
    helper.install()
    helper.install()  # a second call changes nothing
    assert capsys.readouterr().out == P + "loaded (python 9.8.7)\n"


def test_version_is_the_installed_distribution_version(monkeypatch):
    import importlib.metadata
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.rev")
    try:
        expected = importlib.metadata.version("scrimmage-lockstep")
    except importlib.metadata.PackageNotFoundError:
        expected = "unknown"
    assert helper._version() == expected


def test_load_extension_installs_the_rev_helper(monkeypatch, capsys, tmp_path):
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    fakes.hal.loadOneExtension = lambda path: 0
    pkg = load_package(monkeypatch)
    lib = tmp_path / "lib.so"
    lib.write_bytes(b"")
    monkeypatch.setattr(pkg, "library_path", lambda: lib)
    pkg.loadExtension()
    assert capsys.readouterr().out.startswith(P + "loaded (python ")
    spark(fakes, 1)
    assert len(fakes.callbacks) == 1


def test_load_extension_survives_a_failing_rev_helper(monkeypatch, tmp_path, caplog):
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    fakes.hal.loadOneExtension = lambda path: 0
    pkg = load_package(monkeypatch)
    lib = tmp_path / "lib.so"
    lib.write_bytes(b"")
    monkeypatch.setattr(pkg, "library_path", lambda: lib)
    helper = importlib.import_module("scrimmage_lockstep.rev")

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(helper, "install", boom)
    pkg.loadExtension()  # doesn't raise
    assert "boom" in caplog.text


# ---------------------------------------------------------------------------------------------------- registering


def test_every_import_style_and_construction_registers(env):
    from rev import SparkMax  # noqa: the fake, imported after install()

    class MySpark(env.rev.SparkMax):
        def __init__(self, id_):
            super().__init__(id_, env.rev.SparkMax.MotorType.kBrushless)
            self.extra = True

    a = SparkMax(1, SparkMax.MotorType.kBrushless)
    b = env.rev.SparkMax(deviceID=2, type=SparkMax.MotorType.kBrushed)
    c = MySpark(3)
    d = env.rev.SparkFlex(4, env.rev.SparkFlex.MotorType.kBrushless)
    assert (a.device_id, a.motor_type) == (1, "kBrushless")
    assert (b.device_id, b.motor_type) == (2, "kBrushed")  # keyword arguments pass through unchanged
    assert c.extra and c.device_id == 3
    assert len(env.callbacks) == 1  # the first SPARK hooks the robot loop, once
    step(env)
    assert sorted(env.devices) == ["ScrimmageREV:SparkFlex[4]", "ScrimmageREV:SparkMax[1]", "ScrimmageREV:SparkMax[2]",
                                   "ScrimmageREV:SparkMax[3]"]
    assert env.sim(d).motor.name == "neoVortex" and env.sim(a).motor.name == "NEO"
    assert isinstance(env.sim(d), env.rev.SparkFlexSim) and isinstance(env.sim(a), env.rev.SparkMaxSim)


def test_the_callback_steps_with_the_fpga_clock(env):
    s = spark(env, 1)
    env.now = 1.0
    env.callbacks[0]()
    env.now = 1.05
    env.callbacks[0]()
    assert [it[2] for it in env.sim(s).iterations] == pytest.approx([0.02, 0.05])


def test_a_duplicate_device_name_is_not_simulated(env, capsys):
    spark(env, 5)
    spark(env, 5)
    step(env)
    assert capsys.readouterr().out == (P + "SPARK MAX 5 is not simulated: can't create its scrimmage device "
                                       "ScrimmageREV:SparkMax[5] (two SPARKs with that id?)\n")


def test_missing_rev_api_is_unavailable_once_and_robot_iterate_passes_through(monkeypatch, capsys):
    fakes = Fakes(rev_missing=("SparkFlexSim",))
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.rev")
    helper.install()
    capsys.readouterr()
    a = spark(fakes, 1)
    spark(fakes, 2)
    assert capsys.readouterr().out == P + "REV sim unavailable: no rev.SparkFlexSim\n"
    assert fakes.callbacks == []
    robot_sim = fakes.rev.SparkMaxSim(a, fakes.plant.DCMotor.NEO(1))
    robot_sim.iterate(1.0, 12.0, 0.02)  # the robot's own sim physics keeps working
    assert robot_sim.iterations == [(1.0, 12.0, 0.02)]


# ---------------------------------------------------------------------------------------------------- the loop


def test_velocity_dt_and_vbus_floor(env):
    s = spark(env, 1)
    s.configAccessor.encoder.vel_f = 0.5
    step(env)
    dev = env.device(s)
    dev["velocity"].set(600.0)
    dev["vbus"].set(0.0)
    step(env, 0.03)
    assert env.sim(s).iterations[-1] == pytest.approx((300.0, 0.1, 0.03))
    s.configAccessor.inverted = True  # the SPARK's frame is the physical one negated
    dev["vbus"].set(11.0)
    step(env)
    assert env.sim(s).iterations[-1] == pytest.approx((-300.0, 11.0, 0.02))


def test_first_loop_dt_is_0_02_and_dt_le_0_skips_iterate(env):
    s = spark(env, 1)
    s.duty = 0.5
    env.helper.loop_once(10.0)
    assert env.sim(s).iterations[-1][2] == pytest.approx(0.02)
    env.helper.loop_once(10.0)  # dt = 0
    env.helper.loop_once(9.0)   # dt < 0
    assert len(env.sim(s).iterations) == 1
    assert env.device(s)["motorVoltage"].get() == pytest.approx(6.0)  # still published


def test_zero_conversion_factor_counts_as_one(env):
    s = spark(env, 1)
    s.configAccessor.encoder.vel_f = 0.0
    step(env)
    env.device(s)["velocity"].set(100.0)
    step(env)
    assert env.sim(s).iterations[-1][0] == pytest.approx(100.0)


def test_motor_voltage_and_current(env):
    s = spark(env, 1)
    s.duty = 0.25
    s.configAccessor.inverted = True
    step(env)
    dev = env.device(s)
    dev["vbus"].set(12.0)
    dev["current"].set(7.5)
    step(env)
    assert dev["motorVoltage"].get() == pytest.approx(-3.0)
    assert env.sim(s).current == pytest.approx(7.5)


def test_limit_switches(env):
    s = spark(env, 1)
    step(env)
    env.device(s)["fwdLimit"].set(True)
    step(env)
    assert s.fwd.pressed is True and s.rev.pressed is False
    env.device(s)["fwdLimit"].set(False)
    env.device(s)["revLimit"].set(True)
    step(env)
    assert s.fwd.pressed is False and s.rev.pressed is True


def test_absolute_position_offset_inversion_and_velocity(env):
    s = spark(env, 1)
    abs_cfg = s.configAccessor.absoluteEncoder
    abs_cfg.zero, abs_cfg.inverted, abs_cfg.pos_f, abs_cfg.vel_f = 0.1, True, 2.0, 3.0
    step(env)
    dev = env.device(s)
    dev["absRotations"].set(5.30)
    step(env)
    # REVLib: the offset is the reading at zero as if not inverted; inverted reads backwards from there
    assert s.abs.position == pytest.approx((1.0 - 0.2) * 2.0)
    dev["absRotations"].set(5.32)
    step(env, 0.04)
    assert s.abs.position == pytest.approx((1.0 - 0.22) * 2.0)
    assert s.abs.velocity == pytest.approx(-0.02 / 0.04 * 60.0 * 3.0)


def test_absolute_feedback_drives_iterate_and_keeps_the_real_position(env):
    s = spark(env, 1)
    cfg = s.configAccessor
    cfg.closedLoop.sensor = env.rev.FeedbackSensor.kAbsoluteEncoder
    cfg.absoluteEncoder.pos_f, cfg.absoluteEncoder.vel_f = 360.0, 6.0
    step(env)
    dev = env.device(s)
    dev["absRotations"].set(0.25)
    step(env)
    dev["absRotations"].set(0.26)
    dev["velocity"].set(999.0)  # the motor's speed: not what an absolute-encoder closed loop sees
    step(env)
    v_expected = 0.01 / 0.02 * 60.0 * 6.0
    assert env.sim(s).iterations[-1][0] == pytest.approx(v_expected)
    # the preset made iterate's integrated position land on the real one, and the real one is put back after
    assert env.sim(s).position == pytest.approx(0.26 * 360.0)
    assert s.abs.position == pytest.approx(0.26 * 360.0)
    assert s.abs.velocity == pytest.approx(v_expected)


def test_primary_feedback_position_preset_lands_on_the_real_position(env):
    s = spark(env, 1)
    enc = s.configAccessor.encoder
    enc.pos_f, enc.vel_f = 2.0, 0.5
    step(env)
    dev = env.device(s)
    dev["position"].set(10.0)
    dev["velocity"].set(120.0)
    step(env)
    assert env.sim(s).iterations[-1][0] == pytest.approx(60.0)
    assert env.sim(s).position == pytest.approx(20.0)  # iterate's integral ended on rotations * factor
    assert s.rel.position == pytest.approx(20.0)
    assert s.rel.velocity == pytest.approx(54.0)       # iterate's own (filtered) velocity, not overwritten


def test_robot_set_position_is_preserved(env):
    s = spark(env, 1)
    s.rel.position = 5.0  # the robot set the encoder before the first loop
    env.helper.loop_once(0.0)
    dev = env.device(s)
    dev["position"].set(1.0)
    step(env)
    assert s.rel.position == pytest.approx(6.0)
    s.rel.position = 0.0  # robot: encoder.setPosition(0)
    dev["position"].set(1.5)
    step(env)
    assert s.rel.position == pytest.approx(0.5)


def test_unbound_position_leaves_the_encoder_to_rev(env):
    s = spark(env, 1)
    s.configAccessor.closedLoop.sensor = env.rev.FeedbackSensor.kAlternateOrExternalEncoder
    step(env)
    env.device(s)["velocity"].set(60.0)
    s.rel.position = 3.0
    step(env)
    assert s.rel.position == pytest.approx(3.0)  # NaN position: the helper doesn't touch it


def test_not_primary_feedback_sets_velocity_itself(env):
    s = spark(env, 1)
    s.configAccessor.closedLoop.sensor = env.rev.FeedbackSensor.kAlternateOrExternalEncoder
    s.configAccessor.encoder.pos_f = 4.0
    step(env)
    dev = env.device(s)
    dev["position"].set(2.0)
    dev["velocity"].set(30.0)
    step(env)
    assert s.rel.position == pytest.approx(8.0)
    assert s.rel.velocity == pytest.approx(30.0)


def test_followers_mirror_the_physical_output(env):
    leader = spark(env, 1)
    leader.configAccessor.inverted = True
    leader.duty = 0.5
    follower = spark(env, 2)
    follower.configAccessor.leader = 1
    follower.configAccessor.follow_inverted = True
    plain = spark(env, 3)
    plain.configAccessor.leader = 1
    step(env)
    assert env.device(leader)["motorVoltage"].get() == pytest.approx(-6.0)
    assert env.device(follower)["motorVoltage"].get() == pytest.approx(6.0)
    assert env.device(plain)["motorVoltage"].get() == pytest.approx(-6.0)
    assert env.sim(follower).applied_sets == [-0.5]
    assert env.sim(follower).iterations == []  # followers aren't iterated
    assert env.sim(follower).bus == pytest.approx(12.0)


def test_a_follower_of_a_missing_leader_outputs_0_and_says_so_once(env, capsys):
    f = spark(env, 4)
    f.configAccessor.leader = 9
    step(env)
    step(env)
    assert capsys.readouterr().out == (P + "SPARK MAX 4 follows CAN id 9, but the robot program has no SPARK with "
                                       "that id; its output stays 0\n")
    assert env.device(f)["motorVoltage"].get() == 0.0


def test_rebuild_only_when_the_motor_changes(env):
    s = spark(env, 1)
    step(env)
    step(env)
    sims = lambda: [x for x in env.rev.SparkSim.created if x.spark is s]  # noqa: E731
    assert len(sims()) == 1
    env.device(s)["motor"].set(1.0)  # NEO: a SPARK MAX's usual motor already
    step(env)
    assert len(sims()) == 1
    env.device(s)["motor"].set(2.0)
    step(env)
    step(env)
    assert [x.motor.name for x in sims()] == ["NEO", "NEO550"]
    f = spark(env, 2, flex=True)
    step(env)
    env.device(f)["motor"].set(3.0)
    step(env)
    assert len([x for x in env.rev.SparkSim.created if x.spark is f]) == 1


def test_a_throwing_spark_is_dropped_and_the_others_continue(env, capsys):
    bad = spark(env, 7)
    good = spark(env, 8)
    good.duty = 1.0
    step(env)
    env.sim(bad).fail = RuntimeError("boom")
    step(env)  # doesn't raise
    out = capsys.readouterr().out
    assert out == P + "SPARK MAX 7 is no longer simulated: RuntimeError: boom\n"
    n = len(env.sim(good).iterations)
    step(env)
    assert len(env.sim(good).iterations) == n + 1
    assert env.device(good)["motorVoltage"].get() == pytest.approx(12.0)
    assert capsys.readouterr().out == ""


def test_a_spark_failing_in_prepare_is_dropped(env, capsys):
    bad = spark(env, 7)
    good = spark(env, 8)
    bad.configAccessor.inverted = ValueError("bad config")
    step(env)
    assert capsys.readouterr().out == P + "SPARK MAX 7 is no longer simulated: ValueError: bad config\n"
    assert len(env.sim(good).iterations) == 1


def test_a_failing_loop_never_raises_and_says_once(env, capsys, monkeypatch):
    spark(env, 1)

    def boom():
        raise RuntimeError("loop broke")

    monkeypatch.setattr(env.helper, "_add_pending", boom)
    step(env)
    step(env)
    assert capsys.readouterr().out == P + "REV helper failed in the REV robot-loop step: RuntimeError: loop broke\n"


# ---------------------------------------------------------------------------------------------------- iterate guard


def robot_simulation_periodic(sim):
    sim.iterate(100.0, 12.0, 0.02)


def test_robot_iterate_is_ignored_and_counted(env, capsys):
    s = spark(env, 1)
    robot_sim = env.rev.SparkMaxSim(s, env.plant.DCMotor.NEO(1))
    robot_simulation_periodic(robot_sim)
    robot_simulation_periodic(robot_sim)
    flex_sim = env.rev.SparkFlexSim(spark(env, 2, flex=True), env.plant.DCMotor.neoVortex(1))
    flex_sim.iterate(1.0, 12.0, 0.02)
    assert robot_sim.iterations == [] and flex_sim.iterations == []
    env.helper.report()
    assert capsys.readouterr().out == (P + "ignored 3 robot-side sim writes (SparkSim.iterate from "
                                       "test_rev_py_helper.robot_simulation_periodic)\n")
    env.helper.report()  # nothing new: no line
    assert capsys.readouterr().out == ""
    step(env)
    assert len(env.sim(s).iterations) == 1  # the helper's own calls go through


def test_lambda_callers_are_named_like_the_java_agent(env, capsys):
    s = spark(env, 1)
    robot_sim = env.rev.SparkMaxSim(s, env.plant.DCMotor.NEO(1))

    def start_sim_thread():
        f = lambda: robot_sim.iterate(1.0, 12.0, 0.02)  # noqa: E731
        f()

    start_sim_thread()
    env.helper.report()
    out = capsys.readouterr().out
    assert out.endswith("start_sim_thread (lambda))\n"), out


def test_the_summary_lists_the_top_three(env, capsys):
    for i in range(5):
        env.helper._blocked("b")
    for i in range(5):
        env.helper._blocked("a")
    env.helper._blocked("c")
    env.helper._blocked("c")
    env.helper._blocked("d")
    env.helper.report()
    out = capsys.readouterr().out
    assert out.startswith(P + "ignored 13 robot-side sim writes (a from ")
    assert "; b from " in out and "; c from " in out and "d from" not in out


def test_the_first_blocked_write_starts_the_reporter_once(monkeypatch):
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.rev")
    started = []
    monkeypatch.setattr(helper, "_start_reporter", lambda: started.append(1))
    helper._blocked("x")
    helper._blocked("x")
    assert started == [1]


def test_math_helpers():
    # a sanity check on the module's tiny helpers (loaded standalone, no fakes needed)
    spec = importlib.util.spec_from_file_location("_rev_helper_standalone", PKG_DIR / "rev.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._mod1(-0.25) == pytest.approx(0.75) and mod._mod1(2.5) == pytest.approx(0.5)
    assert mod._factor(0.0) == 1.0 and mod._factor(2.0) == 2.0


# ---------------------------------------------------------------------------------------------------- fix round 1


def test_robot_iterate_on_the_base_sparksim_is_ignored(env, capsys):
    s = spark(env, 1)
    robot_sim = env.rev.SparkSim(s, env.plant.DCMotor.NEO(1))  # robotpy-rev's base class can be built directly
    robot_sim.iterate(100.0, 12.0, 0.02)
    assert robot_sim.iterations == []
    env.helper.report()
    assert capsys.readouterr().out.startswith(P + "ignored 1 robot-side sim writes (SparkSim.iterate from ")


def install_after(monkeypatch, capsys, build):
    """Fakes in place, `build(fakes)` run before install() (robot.py imported before loadExtension), then install."""
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.rev")
    monkeypatch.setattr(helper, "_start_reporter", lambda: None)
    fakes.helper = helper
    built = build(fakes)
    helper.install()
    capsys.readouterr()
    return fakes, built


def test_a_spark_built_before_install_registers_when_a_sim_is_built_for_it(monkeypatch, capsys):
    env, s = install_after(monkeypatch, capsys, lambda f: spark(f, 3))
    assert env.callbacks == []  # not known yet
    robot_sim = env.rev.SparkMaxSim(s, env.plant.DCMotor.NEO(1))
    assert len(env.callbacks) == 1
    s.duty = 0.5
    step(env)
    assert env.device(s)["motorVoltage"].get() == pytest.approx(6.0)  # driven by the helper
    assert env.sim(s) is not robot_sim and len(env.sim(s).iterations) == 1
    robot_sim.iterate(1.0, 12.0, 0.02)  # now the helper drives it: the robot's own physics is ignored
    assert robot_sim.iterations == []
    env.helper.report()
    assert capsys.readouterr().out.startswith(P + "ignored 1 robot-side sim writes")


def test_a_sim_built_before_install_passes_through(monkeypatch, capsys):
    def build(f):
        s = spark(f, 3)
        return f.rev.SparkSim(s, f.plant.DCMotor.NEO(1))

    env, robot_sim = install_after(monkeypatch, capsys, build)
    robot_sim.iterate(1.0, 12.0, 0.02)  # the helper doesn't drive this SPARK: the robot's own physics keeps working
    assert robot_sim.iterations == [(1.0, 12.0, 0.02)]
    env.helper.report()
    assert capsys.readouterr().out == ""


def test_a_spark_the_helper_does_not_drive_passes_through(env, capsys):
    spark(env, 5)
    dup = spark(env, 5)
    bad = spark(env, 6)
    step(env)
    env.sim(bad).fail = RuntimeError("boom")
    step(env)
    capsys.readouterr()
    for s in (dup, bad):
        robot_sim = env.rev.SparkMaxSim(s, env.plant.DCMotor.NEO(1))
        robot_sim.fail = None
        robot_sim.iterate(1.0, 12.0, 0.02)
        assert robot_sim.iterations == [(1.0, 12.0, 0.02)]
    assert env.callbacks and len(env.callbacks) == 1
    env.helper.report()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("code, model", [(math.nan, "NEO"), (2.5, "neoVortex"), (1.5, "NEO550"), (math.inf, "NEO")])
def test_the_motor_code_rounds_like_java(env, capsys, code, model):
    s = spark(env, 1)
    step(env)
    env.device(s)["motor"].set(code)
    step(env)
    assert env.sim(s).motor.name == model
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("body, reason", [
    ("raise RuntimeError('broken build')", "RuntimeError: broken build"),
    ("import zzz_no_such_module", "ModuleNotFoundError: No module named 'zzz_no_such_module'"),
])
def test_a_broken_robotpy_rev_is_unavailable(monkeypatch, capsys, tmp_path, body, reason):
    (tmp_path / "rev").mkdir()
    (tmp_path / "rev" / "__init__.py").write_text(body + "\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "rev", raising=False)
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.rev")
    monkeypatch.setattr(helper, "_version", lambda: "0.2.0")
    helper.install()  # doesn't raise
    assert capsys.readouterr().out == P + "loaded (python 0.2.0)\n" + P + "REV sim unavailable: " + reason + "\n"
    monkeypatch.delitem(sys.modules, "rev", raising=False)
