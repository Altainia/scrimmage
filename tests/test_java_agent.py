"""The sim-write guard agent against the real Phoenix 6 jar (via tests/java_probe)."""

import math
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest

from scrimmage import java_tools
from scrimmage.robot_process import kill_by_token

PROBE = Path(__file__).resolve().parent / "java_probe"
pytestmark = pytest.mark.java


def probe_env(mode: str = "read") -> dict:
    return dict(os.environ, JAVA_HOME=str(java_tools.find_jdk()), PROBE_MODE=mode)


def run_probe(tmp_path: Path, mode: str, agent: bool, extra_jvm: str = "", ceiling: float = 120.0) -> str:
    """Run the probe with plain ./gradlew (no scrimmage); return its output once it has printed enough."""
    init = tmp_path / "agent-only.init.gradle"
    agent_arg = f't.jvmArgs("-javaagent:{java_tools.agent_jar_path()}")' if agent else ""
    extra_arg = f't.jvmArgs("{extra_jvm}")' if extra_jvm else ""
    init.write_text(
        "allprojects { p -> p.plugins.withId('edu.wpi.first.GradleRIO') { p.afterEvaluate {\n"
        "  p.extensions.getByName('wpi').sim.halExtensions.all { it.defaultEnabled.set(false) }\n"
        "  p.tasks.matching { it.name in ['simulateJavaRelease','simulateJavaDebug'] }.configureEach { t ->\n"
        f"    {agent_arg}\n"
        f"    {extra_arg}\n"
        "  } } } }\n")
    token = uuid.uuid4().hex
    env = dict(probe_env(mode), SCRIMMAGE_RUN_TOKEN=token)
    p = subprocess.Popen(["./gradlew", "-I", str(init), "simulateJava"], cwd=PROBE, env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    lines: list[str] = []
    threading.Thread(target=lambda: lines.extend(iter(p.stdout.readline, "")), daemon=True).start()

    def enough() -> bool:
        text = "".join(lines)
        if text.count("probe pos=") < 4:
            return False
        return not (agent and mode == "write" and not extra_jvm) or "robot-side sim writes" in text

    try:
        deadline = time.monotonic() + ceiling
        while time.monotonic() < deadline and not enough() and p.poll() is None:
            time.sleep(0.2)
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        kill_by_token(token)
    time.sleep(0.2)
    return "".join(lines)


@pytest.fixture(scope="module", autouse=True)
def built_agent():
    java_tools.build_agent()


@pytest.fixture(scope="module", autouse=True)
def warm_gradle(built_agent):
    """Download dependencies and compile once, so the timed runs below don't race a cold Gradle cache."""
    r = subprocess.run(["./gradlew", "build"], cwd=PROBE, env=probe_env(), capture_output=True, text=True,
                       timeout=900)
    assert r.returncode == 0, "gradlew build failed:\n" + (r.stdout + r.stderr)[-3000:]


def last_probe(out: str) -> dict:
    m = re.findall(r"probe pos=([-\d.]+) inv=([-\d.]+) cc=([-\d.]+) yaw=([-\d.]+)", out)
    assert m, out[-3000:]
    return dict(zip(["pos", "inv", "cc", "yaw"], map(float, m[-1])))


def test_without_agent_robot_side_writes_take_effect(tmp_path):
    p = last_probe(run_probe(tmp_path, "write", agent=False))
    assert p["pos"] == pytest.approx(5.0) and p["yaw"] == pytest.approx(33.0)


def test_agent_blocks_robot_side_writes_and_reports_them(tmp_path):
    out = run_probe(tmp_path, "write", agent=True)
    p = last_probe(out)
    assert p["pos"] == pytest.approx(0.0) and p["yaw"] == pytest.approx(0.0)
    assert re.search(r"scrimmage agent: ignored \d+ robot-side sim writes \(.*?TalonFXSimState\.setRawRotorPosition "
                     r"from frc\.robot\.Robot\.robotPeriodic", out), out[-3000:]
    assert "Limit" not in "".join(re.findall(r"scrimmage agent: ignored.*", out))  # limit setters aren't blocked


def test_agent_can_be_disabled(tmp_path):
    p = last_probe(run_probe(tmp_path, "write", agent=True, extra_jvm="-Dscrimmage.agent.disable=true"))
    assert p["pos"] == pytest.approx(5.0)


def gradle_init_output(tmp_path: Path, cache: Path, **env_extra) -> str:
    env = {k: v for k, v in probe_env().items() if k not in ("SCRIMMAGE_AGENT_JAR", "SCRIMMAGE_CACHE")}
    env.update(SCRIMMAGE_CACHE=str(cache), **env_extra)
    r = subprocess.run(["./gradlew", "-I", str(java_tools.init_script_path()), "help"], cwd=PROBE, env=env,
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    return r.stdout + r.stderr


def test_init_script_warns_without_an_agent_jar_and_finds_it_in_the_cache(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = gradle_init_output(tmp_path, empty)
    assert "scrimmage: WARNING" in out and "scrimmage java build" in out
    full = tmp_path / "full"
    full.mkdir()
    (full / "scrimmage-agent.jar").write_bytes(java_tools.agent_jar_path().read_bytes())
    assert "scrimmage: WARNING" not in gradle_init_output(tmp_path, full)


FAKE_REV = {
    "com/revrobotics/spark/SparkBase.java": """package com.revrobotics.spark;
public abstract class SparkBase {
  public SparkBase(int id) {
    if (id < 0) return;               // a second RETURN in the constructor: the hook runs before each
    System.out.println("fake SparkBase " + id);
  }
}""",
    "com/revrobotics/spark/SparkMax.java": """package com.revrobotics.spark;
public class SparkMax extends SparkBase { public SparkMax(int id) { super(id); } }""",
    "com/revrobotics/spark/SparkSim.java": """package com.revrobotics.spark;
public class SparkSim {
  public static int iterations;
  public void iterate(double velocity, double vbus, double dt) {
    if (vbus == 0) return;
    iterations++;
  }
}""",
    "com/revrobotics/sim/NoiseGenerator.java": """package com.revrobotics.sim;
public class NoiseGenerator {
  static java.util.Random m_rand = new java.util.Random();
  public static double next() { return m_rand.nextGaussian(); }
}""",
    "Main.java": """public class Main {
  public static void main(String[] a) throws Exception {
    new com.revrobotics.spark.SparkSim().iterate(100, 12, 0.02);  // before the helper: scrimmage's to drive
    new com.revrobotics.spark.SparkMax(-1);
    new com.revrobotics.spark.SparkMax(4);
    new com.revrobotics.spark.SparkSim().iterate(100, 12, 0.02);  // REV unavailable: the robot's own sim runs
    System.out.println("iterations=" + com.revrobotics.spark.SparkSim.iterations);
    System.out.println("noise=" + com.revrobotics.sim.NoiseGenerator.next()
        + " expected=" + new java.util.Random(0).nextGaussian());
    Thread.sleep(500);
  }
}""",
}


def run_fake_rev(tmp_path, files: dict) -> str:
    """Compile stand-in REV/WPILib classes and Main, run Main with the agent; return its output."""
    src = tmp_path / "src"
    for name, text in files.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(text)
    jdk = java_tools.find_jdk()
    classes = tmp_path / "classes"
    subprocess.run([str(jdk / "bin" / "javac"), "-d", str(classes), *map(str, src.rglob("*.java"))], check=True)
    r = subprocess.run([str(jdk / "bin" / "java"), f"-javaagent:{java_tools.agent_jar_path()}", "-cp", str(classes),
                        "Main"], capture_output=True, text=True, timeout=60)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    return out


FAKE_CTRE = {
    "com/ctre/phoenix6/StatusCode.java": "package com.ctre.phoenix6;\npublic enum StatusCode { OK, NotImplemented }",
    # The real setters' result is NotImplemented here, so an OK can only come from the agent.
    "com/ctre/phoenix6/sim/TalonFXSimState.java": """package com.ctre.phoenix6.sim;
import com.ctre.phoenix6.StatusCode;
public final class TalonFXSimState {
  public static double pos;
  public static boolean fwd;
  public StatusCode setRawRotorPosition(double v) { pos = v; return StatusCode.NotImplemented; }
  public StatusCode setForwardLimit(boolean v) { fwd = v; return StatusCode.NotImplemented; }
}""",
    "com/ctre/phoenix6/swerve/SwerveDrivetrain.java": """package com.ctre.phoenix6.swerve;
import com.ctre.phoenix6.sim.TalonFXSimState;
public class SwerveDrivetrain {
  final TalonFXSimState sim = new TalonFXSimState();
  public void updateSimState(double dt, double volts) { sim.setRawRotorPosition(42.0); }
  public void internalWrite() { sim.setRawRotorPosition(7.0); }  // CTRE's own code: never rewritten
}""",
    "Drivetrain.java": """public class Drivetrain extends com.ctre.phoenix6.swerve.SwerveDrivetrain {
  void tick() { super.updateSimState(0.02, 12.0); }
}""",
    "Other.java": """public class Other {
  public void updateSimState(double dt, double volts) { System.out.println("other ran " + dt + " " + volts); }
}""",
    "Throws.java": """public class Throws {
  public void updateSimState(double dt, double volts) { throw new IllegalStateException("boom"); }
}""",
    "Main.java": """import com.ctre.phoenix6.sim.TalonFXSimState;
public class Main {
  public static void main(String[] a) throws Exception {
    TalonFXSimState s = new TalonFXSimState();
    System.out.println("set=" + s.setRawRotorPosition(5.0) + " pos=" + TalonFXSimState.pos);
    s.setForwardLimit(true);
    System.out.println("fwd=" + TalonFXSimState.fwd);
    Drivetrain d = new Drivetrain();
    d.updateSimState(0.02, 12.0);
    Runnable r = () -> d.updateSimState(0.02, 12.0);
    r.run();
    d.tick();
    System.out.println("after swerve pos=" + TalonFXSimState.pos);
    d.internalWrite();
    System.out.println("after internal pos=" + TalonFXSimState.pos);
    new Other().updateSimState(0.5, 11.0);
    try { new Throws().updateSimState(0.02, 12.0); } catch (IllegalStateException e) {
      System.out.println("caught " + e.getMessage());
    }
    Thread.sleep(500);
  }
}""",
}


def test_agent_rewrites_robot_calls_and_leaves_phoenix_classes_alone(tmp_path):
    """Against stand-in Phoenix classes: the robot's calls to *SimState setters are counted no-ops giving OK (limit
    setters run), its calls to a CTRE SwerveDrivetrain's updateSimState (direct, in a lambda, or super.) are skipped,
    another class's updateSimState runs (exceptions and all), and Phoenix's own code, never modified, still writes."""
    out = run_fake_rev(tmp_path, FAKE_CTRE)
    assert "set=OK pos=0.0" in out, out
    assert "fwd=true" in out, out
    assert "after swerve pos=0.0" in out, out
    assert "after internal pos=7.0" in out, out
    assert "other ran 0.5 11.0" in out and "caught boom" in out, out
    m = re.search(r"scrimmage agent: ignored (\d+) robot-side sim writes \((.*)\)", out)
    assert m and m.group(1) == "4", out
    assert "SwerveDrivetrain.updateSimState from Main.main" in m.group(2), out
    assert "TalonFXSimState.setRawRotorPosition from Main.main" in m.group(2), out


def test_agent_hooks_rev_classes(tmp_path):
    """Against stand-in REV classes: the SparkBase constructor hook runs (and, with no REVLib sim classes, the
    helper says REV is unavailable, once), a robot-side SparkSim.iterate is a counted no-op until then and runs
    once the helper can't (the robot's own physics keeps working), and REV's noise source is seeded with 0."""
    out = run_fake_rev(tmp_path, FAKE_REV)
    unavailable = re.findall(r"scrimmage agent: REV sim unavailable: (.+)", out)
    assert len(unavailable) == 1 and ("com.revrobotics" in unavailable[0] or "edu.wpi" in unavailable[0]), out
    assert out.index("REV sim unavailable") < out.index("fake SparkBase 4"), out   # hooked at the early return
    assert "iterations=1" in out, out
    noise, expected = re.search(r"noise=(\S+) expected=(\S+)", out).groups()
    assert noise == expected, out
    assert re.search(r"scrimmage agent: ignored 1 robot-side sim writes \(SparkSim\.iterate from Main\.main\)",
                     out), out


def test_noise_generator_without_the_expected_field_is_left_alone(tmp_path):
    """A REVLib whose NoiseGenerator has no static Random m_rand: the agent doesn't patch it (a PUTSTATIC to a missing
    field would break the class for good), and says the noise isn't seeded."""
    files = dict(FAKE_REV)
    files["com/revrobotics/sim/NoiseGenerator.java"] = """package com.revrobotics.sim;
public class NoiseGenerator {
  static java.util.Random rand = new java.util.Random(7);
  public static double next() { return rand.nextGaussian(); }
}"""
    out = run_fake_rev(tmp_path, files)
    assert "NoSuchFieldError" not in out and "ExceptionInInitializerError" not in out, out
    noise = float(re.search(r"noise=(\S+)", out).group(1))
    assert re.search(r"scrimmage agent: REV sim noise isn't seeded \(.*m_rand.*\)", out), out
    assert math.isfinite(noise)


FULL_REV = {
    **FAKE_REV,
    "com/revrobotics/spark/SparkBase.java": """package com.revrobotics.spark;
public abstract class SparkBase {
  final int id;
  public SparkBase(int id) { this.id = id; System.out.println("fake SparkBase " + id); }
  public int getDeviceId() { return id; }
}""",
    "com/revrobotics/spark/SparkMax.java": """package com.revrobotics.spark;
public class SparkMax extends SparkBase { public Object configAccessor; public SparkMax(int id) { super(id); } }""",
    "com/revrobotics/spark/SparkFlex.java": """package com.revrobotics.spark;
public class SparkFlex extends SparkBase { public Object configAccessor; public SparkFlex(int id) { super(id); } }""",
    "com/revrobotics/spark/config/SparkBaseConfigAccessor.java": """package com.revrobotics.spark.config;
public class SparkBaseConfigAccessor {
  public Object encoder, absoluteEncoder, closedLoop;
  public boolean getInverted() { return false; }
  public int getFollowerModeLeaderId() { return 0; }
  public boolean getFollowerModeInverted() { return false; }
}""",
    "com/revrobotics/spark/config/EncoderConfigAccessor.java": """package com.revrobotics.spark.config;
public class EncoderConfigAccessor {
  public double getPositionConversionFactor() { return 1; }
  public double getVelocityConversionFactor() { return 1; }
}""",
    "com/revrobotics/spark/config/AbsoluteEncoderConfigAccessor.java": """package com.revrobotics.spark.config;
public class AbsoluteEncoderConfigAccessor {
  public boolean getInverted() { return false; }
  public double getZeroOffset() { return 0; }
  public double getPositionConversionFactor() { return 1; }
  public double getVelocityConversionFactor() { return 1; }
}""",
    "com/revrobotics/spark/config/ClosedLoopConfigAccessor.java": """package com.revrobotics.spark.config;
public class ClosedLoopConfigAccessor { public Object getFeedbackSensor() { return null; } }""",
    "edu/wpi/first/math/system/plant/DCMotor.java": """package edu.wpi.first.math.system.plant;
public class DCMotor {
  public static DCMotor getNEO(int n) { return new DCMotor(); }
  public static DCMotor getNeo550(int n) { return new DCMotor(); }
  public static DCMotor getNeoVortex(int n) { return new DCMotor(); }
}""",
    "com/revrobotics/spark/SparkSim.java": """package com.revrobotics.spark;
public class SparkSim {
  public static int iterations;
  public void iterate(double velocity, double vbus, double dt) { iterations++; }
  public void setAppliedOutput(double v) {}
  public double getAppliedOutput() { return 0; }
  public void setMotorCurrent(double v) {}
  public void setBusVoltage(double v) {}
  public void setPosition(double v) {}
  public Object getAbsoluteEncoderSim() { return null; }
  public Object getRelativeEncoderSim() { return null; }
  public Object getForwardLimitSwitchSim() { return null; }
  public Object getReverseLimitSwitchSim() { return null; }
}""",
    "com/revrobotics/sim/SparkMaxSim.java": """package com.revrobotics.sim;
public class SparkMaxSim extends com.revrobotics.spark.SparkSim {
  public SparkMaxSim(com.revrobotics.spark.SparkMax s, edu.wpi.first.math.system.plant.DCMotor m) {}
}""",
    "com/revrobotics/sim/SparkFlexSim.java": """package com.revrobotics.sim;
public class SparkFlexSim extends com.revrobotics.spark.SparkSim {
  public SparkFlexSim(com.revrobotics.spark.SparkFlex s, edu.wpi.first.math.system.plant.DCMotor m) {}
}""",
    "com/revrobotics/sim/SparkAbsoluteEncoderSim.java": """package com.revrobotics.sim;
public class SparkAbsoluteEncoderSim { public void setPosition(double v) {} public void setVelocity(double v) {} }""",
    "com/revrobotics/sim/SparkRelativeEncoderSim.java": """package com.revrobotics.sim;
public class SparkRelativeEncoderSim {
  public double getPosition() { return 0; }
  public void setPosition(double v) {}
  public void setVelocity(double v) {}
}""",
    "com/revrobotics/sim/SparkLimitSwitchSim.java": """package com.revrobotics.sim;
public class SparkLimitSwitchSim { public void setPressed(boolean b) {} }""",
    "edu/wpi/first/hal/SimDevice.java": """package edu.wpi.first.hal;
public class SimDevice {
  public enum Direction { kInput, kOutput }
  public static SimDevice create(String name) { return new SimDevice(); }
  public SimDouble createDouble(String n, Direction d, double v) { return new SimDouble(); }
  public SimBoolean createBoolean(String n, Direction d, boolean v) { return new SimBoolean(); }
  public void close() {}
}""",
    "edu/wpi/first/hal/SimDouble.java": """package edu.wpi.first.hal;
public class SimDouble { public double get() { return 0; } public void set(double v) {} }""",
    "edu/wpi/first/hal/SimBoolean.java": """package edu.wpi.first.hal;
public class SimBoolean { public boolean get() { return false; } }""",
    "edu/wpi/first/wpilibj/Timer.java": """package edu.wpi.first.wpilibj;
public class Timer { public static double getFPGATimestamp() { return 0; } }""",
    "edu/wpi/first/hal/HAL.java": """package edu.wpi.first.hal;
public class HAL {
  public static Object registerSimPeriodicBeforeCallback(Runnable r) { throw new IllegalStateException("boom"); }
}""",
    "Main.java": """public class Main {
  public static void main(String[] a) throws Exception {
    new com.revrobotics.spark.SparkMax(1);
    new com.revrobotics.spark.SparkMax(2);
    System.out.println("constructed");
    new com.revrobotics.spark.SparkSim().iterate(1, 12, 0.02);
    System.out.println("iterations=" + com.revrobotics.spark.SparkSim.iterations);
  }
}""",
}


def test_a_failure_while_starting_the_rev_helper_is_reported_as_unavailable(tmp_path):
    """Every REV/WPILib member resolves, but registering the robot-loop callback throws: one REV-unavailable line
    giving the cause (so scrimmage reports it), nothing more for later SPARKs, and the robot carries on, its own
    SparkSim.iterate included."""
    out = run_fake_rev(tmp_path, FULL_REV)
    lines = re.findall(r"scrimmage agent: REV.*", out)
    assert lines == ["scrimmage agent: REV sim unavailable: IllegalStateException: boom"], out
    assert "constructed" in out and "fake SparkBase 2" in out
    assert "iterations=1" in out and "ignored" not in out, out    # the robot's own SparkSim physics still runs


# FULL_REV with a working robot loop: HAL keeps the callback for Main to run, SimDouble inputs read SimDouble.VALUES
# (by name, else their initial value), and SparkMaxSim counts how often the helper builds it, with which motor.
LOOP_REV = {
    **FULL_REV,
    "com/revrobotics/spark/SparkMax.java": """package com.revrobotics.spark;
public class SparkMax extends SparkBase {
  public Object configAccessor = new com.revrobotics.spark.config.SparkBaseConfigAccessor();
  public SparkMax(int id) { super(id); }
}""",
    "com/revrobotics/spark/config/SparkBaseConfigAccessor.java": """package com.revrobotics.spark.config;
public class SparkBaseConfigAccessor {
  public Object encoder = new EncoderConfigAccessor(), absoluteEncoder = new AbsoluteEncoderConfigAccessor(),
      closedLoop = new ClosedLoopConfigAccessor();
  public boolean getInverted() { return false; }
  public int getFollowerModeLeaderId() { return 0; }
  public boolean getFollowerModeInverted() { return false; }
}""",
    "edu/wpi/first/math/system/plant/DCMotor.java": """package edu.wpi.first.math.system.plant;
public class DCMotor {
  public final String name;
  DCMotor(String name) { this.name = name; }
  public static DCMotor getNEO(int n) { return new DCMotor("NEO"); }
  public static DCMotor getNeo550(int n) { return new DCMotor("NEO550"); }
  public static DCMotor getNeoVortex(int n) { return new DCMotor("NeoVortex"); }
}""",
    "com/revrobotics/spark/SparkSim.java": """package com.revrobotics.spark;
public class SparkSim {
  public static int iterations;
  public void iterate(double velocity, double vbus, double dt) { iterations++; }
  public void setAppliedOutput(double v) {}
  public double getAppliedOutput() { return 0; }
  public void setMotorCurrent(double v) {}
  public void setBusVoltage(double v) {}
  public void setPosition(double v) {}
  public Object getAbsoluteEncoderSim() { return new com.revrobotics.sim.SparkAbsoluteEncoderSim(); }
  public Object getRelativeEncoderSim() { return new com.revrobotics.sim.SparkRelativeEncoderSim(); }
  public Object getForwardLimitSwitchSim() { return new com.revrobotics.sim.SparkLimitSwitchSim(); }
  public Object getReverseLimitSwitchSim() { return new com.revrobotics.sim.SparkLimitSwitchSim(); }
}""",
    "com/revrobotics/sim/SparkMaxSim.java": """package com.revrobotics.sim;
public class SparkMaxSim extends com.revrobotics.spark.SparkSim {
  public static int built;
  public SparkMaxSim(com.revrobotics.spark.SparkMax s, edu.wpi.first.math.system.plant.DCMotor m) {
    built++;
    System.out.println("built SparkMaxSim " + built + " " + m.name);
  }
}""",
    "edu/wpi/first/hal/SimDevice.java": """package edu.wpi.first.hal;
public class SimDevice {
  public enum Direction { kInput, kOutput }
  public static SimDevice create(String name) { return new SimDevice(); }
  public SimDouble createDouble(String n, Direction d, double v) { return new SimDouble(n, v); }
  public SimBoolean createBoolean(String n, Direction d, boolean v) { return new SimBoolean(); }
  public void close() {}
}""",
    "edu/wpi/first/hal/SimDouble.java": """package edu.wpi.first.hal;
public class SimDouble {
  public static final java.util.Map<String, Double> VALUES = new java.util.HashMap<>();
  final String name;
  final double initial;
  SimDouble(String name, double initial) { this.name = name; this.initial = initial; }
  public double get() { return VALUES.getOrDefault(name, initial); }
  public void set(double v) {}
}""",
    "edu/wpi/first/hal/HAL.java": """package edu.wpi.first.hal;
public class HAL {
  public static Runnable loop;
  public static Object registerSimPeriodicBeforeCallback(Runnable r) { loop = r; return new Object(); }
}""",
}


def test_rev_helper_rebuilds_the_spark_sim_only_when_its_motor_changes(tmp_path):
    """scrimmage's motor code arrives after the helper's first loop (0 = the SPARK's usual motor): a NEO code for a
    SPARK MAX keeps the SparkSim it built (rebuilding it mid-run resets REV's velocity filter); a NEO 550 rebuilds it."""
    files = dict(LOOP_REV)
    files["Main.java"] = """import edu.wpi.first.hal.HAL;
import edu.wpi.first.hal.SimDouble;
public class Main {
  public static void main(String[] a) throws Exception {
    new com.revrobotics.spark.SparkMax(1);
    HAL.loop.run();
    SimDouble.VALUES.put("motor", 1.0);
    HAL.loop.run();
    HAL.loop.run();
    System.out.println("after NEO built=" + com.revrobotics.sim.SparkMaxSim.built);
    SimDouble.VALUES.put("motor", 2.0);
    HAL.loop.run();
    HAL.loop.run();
    System.out.println("after NEO550 built=" + com.revrobotics.sim.SparkMaxSim.built);
  }
}"""
    out = run_fake_rev(tmp_path, files)
    assert "REV helper failed" not in out and "unavailable" not in out, out
    assert "built SparkMaxSim 1 NEO" in out and "after NEO built=1" in out, out
    assert "built SparkMaxSim 2 NEO550" in out and "after NEO550 built=2" in out, out


# Stand-in libgrapplefrc, shaped as 2026.0.0's: LaserCan's setters fail as the real ones do in simulation (a CAN
# request that times out), and getMeasurement() has no frames to give. HAL keeps the callback for Main to run; a
# SimInt input reads SimInt.VALUES["<device>.<name>"], else its initial value; a second SimDevice of one name is null.
FAKE_GRAPPLE = {
    "au/grapplerobotics/ConfigurationFailedException.java": """package au.grapplerobotics;
public class ConfigurationFailedException extends Exception {
  public ConfigurationFailedException(String m) { super(m); }
}""",
    "au/grapplerobotics/interfaces/LaserCanInterface.java": """package au.grapplerobotics.interfaces;
import au.grapplerobotics.ConfigurationFailedException;
public interface LaserCanInterface {
  class RegionOfInterest {
    public int x, y, w, h;
    public RegionOfInterest(int x, int y, int w, int h) { this.x = x; this.y = y; this.w = w; this.h = h; }
  }
  class Measurement {
    public int status, distance_mm, ambient, budget_ms;
    public boolean is_long;
    public RegionOfInterest roi;
    public Measurement(int status, int distance_mm, int ambient, boolean is_long, int budget_ms, RegionOfInterest roi) {
      this.status = status; this.distance_mm = distance_mm; this.ambient = ambient; this.is_long = is_long;
      this.budget_ms = budget_ms; this.roi = roi;
    }
    public String toString() {
      return "status=" + status + " mm=" + distance_mm + " long=" + is_long + " budget=" + budget_ms
          + " roi=" + roi.x + "," + roi.y + "," + roi.w + "," + roi.h;
    }
  }
  enum RangingMode { LONG, SHORT }
  enum TimingBudget {
    TIMING_BUDGET_20MS, TIMING_BUDGET_33MS, TIMING_BUDGET_50MS, TIMING_BUDGET_100MS;
    public int asMilliseconds() { return new int[] {20, 33, 50, 100}[ordinal()]; }
  }
  Measurement getMeasurement();
  void setRangingMode(RangingMode mode) throws ConfigurationFailedException;
  void setTimingBudget(TimingBudget budget) throws ConfigurationFailedException;
  void setRegionOfInterest(RegionOfInterest roi) throws ConfigurationFailedException;
}""",
    "au/grapplerobotics/LaserCan.java": """package au.grapplerobotics;
import au.grapplerobotics.interfaces.LaserCanInterface;
public class LaserCan implements LaserCanInterface {
  public LaserCan(int can_id) { System.out.println("fake LaserCan " + can_id); }
  public Measurement getMeasurement() { return null; }
  public void setRangingMode(RangingMode mode) throws ConfigurationFailedException { timedOut(); }
  public void setTimingBudget(TimingBudget budget) throws ConfigurationFailedException { timedOut(); }
  public void setRegionOfInterest(RegionOfInterest roi) throws ConfigurationFailedException { timedOut(); }
  void timedOut() throws ConfigurationFailedException { throw new ConfigurationFailedException("timed out"); }
}""",
    "edu/wpi/first/hal/SimDevice.java": """package edu.wpi.first.hal;
public class SimDevice {
  public enum Direction { kInput, kOutput }
  static final java.util.Set<String> NAMES = new java.util.HashSet<>();
  final String name;
  SimDevice(String name) { this.name = name; }
  public static SimDevice create(String name) { return NAMES.add(name) ? new SimDevice(name) : null; }
  public SimInt createInt(String n, Direction d, int v) { return new SimInt(name + "." + n, v); }
}""",
    "edu/wpi/first/hal/SimInt.java": """package edu.wpi.first.hal;
public class SimInt {
  public static final java.util.Map<String, Integer> VALUES = new java.util.HashMap<>();
  final String key;
  final int initial;
  SimInt(String key, int initial) { this.key = key; this.initial = initial; }
  public int get() { return VALUES.getOrDefault(key, initial); }
}""",
    "edu/wpi/first/hal/HAL.java": """package edu.wpi.first.hal;
public class HAL {
  public static Runnable loop;
  public static Object registerSimPeriodicBeforeCallback(Runnable r) { loop = r; return new Object(); }
}""",
    "Early.java": """public class Early {
  static final au.grapplerobotics.LaserCan LASER = new au.grapplerobotics.LaserCan(9);  // a static initializer
}""",
    "Main.java": """import au.grapplerobotics.LaserCan;
import au.grapplerobotics.interfaces.LaserCanInterface.*;
import edu.wpi.first.hal.HAL;
import edu.wpi.first.hal.SimInt;
public class Main {
  static void put(int id, int mm, int status) {
    SimInt.VALUES.put("ScrimmageGrapple:LaserCAN[" + id + "].distance_mm", mm);
    SimInt.VALUES.put("ScrimmageGrapple:LaserCAN[" + id + "].status", status);
  }
  static void config(String what, LaserCan c, Object arg) {
    try {
      if (arg instanceof RangingMode) c.setRangingMode((RangingMode) arg);
      if (arg instanceof TimingBudget) c.setTimingBudget((TimingBudget) arg);
      if (arg instanceof RegionOfInterest) c.setRegionOfInterest((RegionOfInterest) arg);
      System.out.println(what + " ok");
    } catch (Exception e) {
      System.out.println(what + " threw " + e.getMessage());
    }
  }
  public static void main(String[] a) throws Exception {
    LaserCan seven = new LaserCan(7);
    LaserCan eight = new LaserCan(8);
    LaserCan twin = new LaserCan(7);
    System.out.println("before loop: " + seven.getMeasurement());
    HAL.loop.run();
    SimInt.VALUES.put("ScrimmageGrapple:LaserCAN[7].distance_mm", 400);
    HAL.loop.run();
    System.out.println("one written: " + seven.getMeasurement());
    put(7, 400, 0);
    put(8, 2000, 0);
    put(9, 4000, 2);
    System.out.println("not yet read: " + seven.getMeasurement());
    HAL.loop.run();
    System.out.println("seven: " + seven.getMeasurement());
    System.out.println("eight short: " + eight.getMeasurement());
    System.out.println("nine: " + Early.LASER.getMeasurement());
    HAL.loop.run();
    System.out.println("nine after a loop: " + Early.LASER.getMeasurement());
    System.out.println("twin: " + twin.getMeasurement());
    config("long", eight, RangingMode.LONG);
    config("budget", eight, TimingBudget.TIMING_BUDGET_50MS);
    config("roi", eight, new RegionOfInterest(4, 4, 8, 8));
    System.out.println("eight long: " + eight.getMeasurement());
    put(8, 4200, 0);
    HAL.loop.run();
    System.out.println("eight past long: " + eight.getMeasurement());
    config("short", eight, RangingMode.SHORT);
    put(8, 1000, 0);
    HAL.loop.run();
    System.out.println("eight short again: " + eight.getMeasurement());
  }
}""",
}
LASERCAN_DEFAULTS = "long=false budget=33 roi=8,8,16,16"


def test_grapple_helper_feeds_lasercans(tmp_path):
    """Against stand-in Grapple and WPILib classes: each LaserCan (one made in a static initializer too) reads its own
    device's values once scrimmage has written both and a robot loop has taken them; short ranging (the firmware's
    default) turns a target past 1.3 m into no target at 1300 mm, long ranging reaches 4 m; the setters don't throw and
    their values show in the Measurement; a second LaserCan with one id isn't fed."""
    out = run_fake_rev(tmp_path, FAKE_GRAPPLE)
    assert out.count("scrimmage grapple helper: loaded") == 1, out
    assert "unavailable" not in out, out
    assert out.index("fake LaserCan 7") < out.index("scrimmage grapple helper: loaded"), out
    assert "before loop: null" in out and "one written: null" in out and "not yet read: null" in out, out
    assert f"seven: status=0 mm=400 {LASERCAN_DEFAULTS}" in out, out
    assert f"eight short: status=2 mm=1300 {LASERCAN_DEFAULTS}" in out, out
    # LaserCAN 9's device appears on the first loop after its static initializer ran: fed from the next one
    assert "nine: null" in out and f"nine after a loop: status=2 mm=1300 {LASERCAN_DEFAULTS}" in out, out
    assert "LaserCAN 7 is not simulated: can't create its scrimmage device ScrimmageGrapple:LaserCAN[7]" in out, out
    assert "twin: null" in out, out
    assert "long ok" in out and "budget ok" in out and "roi ok" in out, out
    assert "eight long: status=0 mm=2000 long=true budget=50 roi=4,4,8,8" in out, out
    assert "eight past long: status=2 mm=4000 long=true" in out, out
    assert "short ok" in out and "eight short again: status=0 mm=1000 long=false" in out, out


def test_grapple_helper_unavailable_leaves_lasercan_alone(tmp_path):
    """No WPILib HAL classes: one unavailable line naming what is missing, and every LaserCan keeps its original
    behavior (its setters throw, getMeasurement() gives null)."""
    files = {k: v for k, v in FAKE_GRAPPLE.items() if not k.startswith("edu/")}
    files["Main.java"] = """import au.grapplerobotics.LaserCan;
import au.grapplerobotics.interfaces.LaserCanInterface.RangingMode;
public class Main {
  public static void main(String[] a) throws Exception {
    LaserCan one = new LaserCan(1);
    new LaserCan(2);
    try { one.setRangingMode(RangingMode.LONG); } catch (Exception e) { System.out.println("threw " + e.getMessage()); }
    System.out.println("measurement=" + one.getMeasurement());
  }
}"""
    out = run_fake_rev(tmp_path, files)
    lines = re.findall(r"scrimmage grapple helper: .*", out)
    assert lines == ["scrimmage grapple helper: unavailable: no class edu.wpi.first.hal.SimDevice"], out
    assert "threw timed out" in out and "measurement=null" in out, out

