"""Java robot programs under scrimmage, through the Gradle init script (real Gradle, real Phoenix 6)."""

import asyncio
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from fake_ds import FakeDS
from phoenix_runs import machine_too_slow
from scrimmage import java_tools
from scrimmage.driverstation import REAL_DS_WAITING
from scrimmage.session import Session

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
PROBE = TESTS / "java_probe"
pytestmark = pytest.mark.java


@pytest.fixture(scope="module", autouse=True)
def built_agent():
    java_tools.build_agent()


@pytest.fixture(scope="module", autouse=True)
def warm_gradle(built_agent):
    """Download dependencies and compile once, so the timed session starts don't race a cold Gradle cache."""
    r = subprocess.run(["./gradlew", "build"], cwd=PROBE, env=dict(os.environ, JAVA_HOME=str(java_tools.find_jdk())),
                       capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, "gradlew build failed:\n" + (r.stdout + r.stderr)[-3000:]


def test_java_session_runs_in_real_time_and_loads_agent():
    async def main():
        s = Session(PROBE / "scrimmage.toml", nt=False)
        try:
            st = await s.start()
            assert st["lockstep"] is False and st["unpausable"] is True   # Phoenix 6: real time
            for _ in range(100):  # Gradle forward the JVM's output a little behind the connection
                text = "\n".join(l["text"] for l in s.console()["lines"])
                if "scrimmage agent: loaded" in text:
                    break
                await asyncio.sleep(0.1)
            assert "scrimmage agent: loaded" in text
            assert "libhalsim_gui" not in text           # init script turned the template's GUI off
            assert "halsim_ds_socket" not in text        # ...and the probe's DS socket, without SCRIMMAGE_DS_SOCKET
            assert any(d.startswith("CANMotor/Talon FX (v6)[1]") for d in st["devices"])
            t0 = s.t
            r = await s.advance(0.5)
            assert t0 + 0.5 - 1e-6 <= r["t"] <= t0 + 0.75, (t0, r["t"])   # real time stops just past the target
        finally:
            await s.stop()
    asyncio.run(main())


def test_project_port_override_is_ignored_with_a_note(tmp_path):
    """A project that hard-codes wpi.sim.envVar('HALSIMWS_PORT', ...) still connects: the init script overrides it."""
    import shutil
    proj = tmp_path / "proj"
    shutil.copytree(PROBE, proj, ignore=shutil.ignore_patterns("build", ".gradle"))
    bg = proj / "build.gradle"
    bg.write_text(bg.read_text() + '\nwpi.sim.envVar("HALSIMWS_PORT", "3999")\n')
    cfg = proj / "scrimmage.toml"
    cfg.write_text(cfg.read_text().replace('"../models/', f'"{TESTS / "models"}/'))

    async def main():
        s = Session(cfg, nt=False)
        try:
            st = await s.start()
            assert st["realtime_reason"] is not None   # the probe has Phoenix 6 devices
            for _ in range(100):
                text = "\n".join(l["text"] for l in s.console()["lines"])
                if "ignoring the project's Gradle setting HALSIMWS_PORT=3999" in text:
                    break
                await asyncio.sleep(0.1)
            assert "ignoring the project's Gradle setting HALSIMWS_PORT=3999" in text
        finally:
            await s.stop()
    asyncio.run(main())


PROBE_LINE = re.compile(r"probe pos=([-\d.]+) inv=([-\d.]+) cc=([-\d.]+) yaw=([-\d.]+) loop=(\d+)")


async def probe_print_at(s: Session, loop: int, timeout: float = 10.0) -> tuple[float, float, float, float]:
    """The probe's first print at or after robot loop `loop` (Gradle forward the JVM's output a little behind)."""
    for _ in range(int(timeout / 0.1)):
        text = "\n".join(l["text"] for l in s.console()["lines"])
        for m in PROBE_LINE.finditer(text):
            if int(m.group(5)) >= loop:
                return tuple(map(float, m.groups()[:4]))
        await asyncio.sleep(0.1)
    raise AssertionError(f"no probe print at or after loop {loop} within {timeout} s; last output:\n{text[-2000:]}")


def test_phoenix_reads_scrimmage_physics():
    async def main():
        s = Session(PROBE / "scrimmage.toml", nt=False)
        try:
            await s.start()
            st0 = s.state()
            start_heading = st0["pose"]["heading_deg"]
            start_right = st0["joints"]["right_center"]["position"]
            # The probe applies DutyCycleOut(0.3) to TalonFX 1 and to TalonFX 4 (Inverted = Clockwise_Positive)
            s.set_mode("teleop")
            await s.advance(2.0)
            # Phoenix status signals refresh on the wall clock, so under lockstep they lag robot time while
            # the robot moves. Stop (disabled = 0 V, brake), let the signals catch up, then read the next print.
            s.set_mode("disabled")
            await s.advance(0.5)
            await asyncio.sleep(0.5)
            await s.advance(0.5)                # loops 126..150: the probe prints at loop 150
            st = s.state()
            pos, inv, cc, yaw = await probe_print_at(s, 150)
            joint_rev = st["joints"]["left_center"]["position"] / (2 * math.pi)
            right_rev = st["joints"]["right_center"]["position"] / (2 * math.pi)
            assert pos == pytest.approx(joint_rev * 10.0, rel=0.05, abs=0.2)   # rotor = joint x gear ratio
            assert inv == pytest.approx(-right_rev * 10.0, rel=0.05, abs=0.2)  # Phoenix applies Inverted to the raw rotor
            # Clockwise_Positive + positive duty cycle = negative rotor voltage; with invert = false the joint runs backward
            assert st["joints"]["right_center"]["position"] - start_right < -2.0
            assert inv > 1.0                                                    # ...which Phoenix reports as positive
            assert cc == pytest.approx(joint_rev + 0.25, abs=0.05)              # Phoenix adds MagnetOffset 0.25
            # The Pigeon boots at yaw 0, so compare with the heading change since start. Its yaw is unwrapped
            # (the robot spins in place here) and heading_deg is wrapped, so compare modulo 360.
            turned = st["pose"]["heading_deg"] - start_heading
            assert (yaw - turned + 180) % 360 - 180 == pytest.approx(0, abs=1.0)
            assert yaw < -90                                                    # spinning clockwise, unwrapped
            assert pos > 1.0                                                    # the Talon's voltage moved the joint
        finally:
            await s.stop()
    asyncio.run(main())


def probe_config(tmp_path, **env) -> Path:
    """tests/java_probe/scrimmage.toml, run from tmp_path with extra [robot_program] env."""
    text = (PROBE / "scrimmage.toml").read_text().replace('"../models/', f'"{TESTS / "models"}/')
    text = text.replace('cwd = "."', f'cwd = "{PROBE}"')
    table = ", ".join(f'{k} = "{v}"' for k, v in env.items())
    text = text.replace("start_timeout = 180\n", f"start_timeout = 180\nenv = {{ {table} }}\n")
    p = tmp_path / "probe.toml"
    p.write_text(text)
    return p


MATCH_LINE = re.compile(r"match data='([^']*)' alliance=(\w+)")


async def prints_until(s: Session, want: tuple, timeout: float = 10.0) -> list[tuple]:
    """PROBE_MODE=match's prints, once `want` (game data, alliance) is among them."""
    for _ in range(int(timeout / 0.1)):   # Gradle forward the JVM's output a little behind
        got = MATCH_LINE.findall("\n".join(l["text"] for l in s.console()["lines"]))
        if want in got:
            return got
        await s.advance(0.1)
    raise AssertionError(f"no {want} print within {timeout} s; got {got}")


def test_java_reads_the_match_data(tmp_path):
    """Spec M7 for Java: PROBE_MODE=match prints DriverStation.getGameSpecificMessage() and the alliance in teleop.
    The session's set_match(station="red2", game_data="R") reaches it, and a cleared game data does too."""
    cfg = probe_config(tmp_path, PROBE_MODE="match")

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            s.set_mode("teleop")
            await prints_until(s, ("", "Blue"))      # the defaults: no game data, blue1
            s.set_match(station="red2", game_data="R")
            await prints_until(s, ("R", "Red"))
            assert s.state()["match"]["game_data"] == "R"
            s.set_match(game_data="")
            got = await prints_until(s, ("", "Red"))
            assert got == [("", "Blue"), ("R", "Red"), ("", "Red")], got
        finally:
            await s.stop()
    asyncio.run(main())


async def follow_the_fake_ds(s: Session, ds: FakeDS, want: tuple | None) -> dict:
    """Start the session, then the fake DS (teleop enabled at red2, with the FMS, game data "B"); the state once the
    robot reports teleop (and PROBE_MODE=match has printed `want`, unless None)."""
    await s.start()
    assert s.state()["driver_station"] == REAL_DS_WAITING
    ds.set(True, "teleop", "red2", fms=True)
    ds.start()
    ds.send_game_data("B")
    if want is not None:
        await prints_until(s, want, timeout=20)
    for _ in range(200):
        if s.state()["mode"] == "teleop":
            break
        await s.advance(0.1)
    return s.state()


def test_java_follows_the_real_ds(tmp_path):
    """Sub-project 28: with real_ds the init script keeps the probe's wpi.sim.addDriverstation(). A fake DS enables
    teleop at red2 and sends game data "B": the probe prints them, and scrimmage shows the DS from the robot's reports."""
    cfg = probe_config(tmp_path, PROBE_MODE="match")

    async def main():
        s = Session(cfg, real_ds=True, nt=False)
        ds = FakeDS()
        try:
            st = await follow_the_fake_ds(s, ds, ("B", "Red"))
            assert st["real_ds"] is True and st["driver_station"] == "connected", st
            assert st["mode"] == "teleop" and st["match"]["station"] == "red2" and st["match"]["fms"] is True, st
            assert ds.replies() > 0
            text = "\n".join(l["text"] for l in s.console(0)["lines"])
            assert "halsim_ds_socket" in text and "libhalsim_gui" not in text
        finally:
            ds.stop()
            await s.stop()
    asyncio.run(main())


def test_the_init_script_adds_the_ds_socket_when_the_project_has_none(tmp_path):
    """A project without wpi.sim.addDriverstation(): with real_ds the init script adds it."""
    import shutil
    proj = tmp_path / "proj"
    shutil.copytree(PROBE, proj, ignore=shutil.ignore_patterns("build", ".gradle"))
    bg = proj / "build.gradle"
    assert "wpi.sim.addDriverstation()\n" in bg.read_text()
    bg.write_text(bg.read_text().replace("wpi.sim.addDriverstation()\n", ""))
    cfg = proj / "scrimmage.toml"
    cfg.write_text(cfg.read_text().replace('"../models/', f'"{TESTS / "models"}/'))

    async def main():
        s = Session(cfg, real_ds=True, nt=False)
        ds = FakeDS()
        try:
            st = await follow_the_fake_ds(s, ds, None)
            assert st["driver_station"] == "connected" and st["mode"] == "teleop", st
            assert ds.replies() > 0
        finally:
            ds.stop()
            await s.stop()
    asyncio.run(main())


def test_safety_net_catches_unguarded_writes(tmp_path):
    """With the agent disabled, the probe's setRawRotorPosition(5.0) reaches the device: scrimmage notices."""
    cfg = probe_config(tmp_path, PROBE_MODE="write", JAVA_TOOL_OPTIONS="-Dscrimmage.agent.disable=true")

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            st = s.status()
            assert st["realtime_reason"] == ("Phoenix 6 simulation runs on the wall clock, so this robot runs in real "
                                             "time (not lockstep)")
            for _ in range(20):   # 2 s of simulated time
                await s.advance(0.1)
                if any("Talon FX (v6)[1]" in f["device"] for f in s.status()["foreign_writes"]):
                    break
            fw = s.status()["foreign_writes"]
            assert any("Talon FX (v6)[1]" in f["device"] for f in fw), fw
            for _ in range(100):  # Gradle forward the JVM's output a little behind
                text = "\n".join(l["text"] for l in s.console()["lines"])
                if "scrimmage agent: disabled" in text:
                    break
                await asyncio.sleep(0.1)
            assert "scrimmage agent: disabled" in text   # the robot JVM picked up JAVA_TOOL_OPTIONS
            assert s.agent_state() == "disabled"           # so foreign-write messages give that cause
        finally:
            await s.stop()
    asyncio.run(main())


def test_agent_keeps_writes_out_and_reports_them(tmp_path):
    cfg = probe_config(tmp_path, PROBE_MODE="write")

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            s.set_mode("teleop")   # the motor moves, so echoes and Phoenix's own writes vary
            summary = None
            for _ in range(10):    # the agent prints a summary 100 ms after its first blocked write
                await s.advance(0.2)
                summary = s.status()["agent_summary"]
                if summary is not None:
                    break
            st = s.status()
            assert st["foreign_writes"] == []
            assert summary is not None and "TalonFXSimState.setRawRotorPosition" in summary, summary
        finally:
            await s.stop()
    asyncio.run(main())


def test_java_swerve_auto_scenario(tmp_path):
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(ROOT / "scenarios" / "swerve_auto.toml"),
                        "--out", str(out)], capture_output=True, text=True, timeout=600)
    data = json.loads((out / "report.json").read_text())
    # A machine too slow for real time may not finish the path in the 5 s step: short of the pose, no "auto: done"
    skip = machine_too_slow(data, lambda check: check == "pose" or check == "console contains /auto: done/")
    if skip:
        pytest.skip(skip)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert data["passed"] and any("runs in real time (not lockstep)" in n for n in data.get("notes", []))
    robot_log = (out / "robot.log").read_text()
    assert "scrimmage agent: ignored" in robot_log            # our example's sim thread was blocked


def test_java_maxswerve_auto_scenario(tmp_path):
    """REV's MAXSwerve template (SPARK MAX velocity and absolute-encoder position loops, an ADIS16470) runs its own
    trajectory auto under lockstep: the scenario's pose check is where the robot ends up on the field."""
    project = ROOT / "examples" / "java_maxswerve"
    r = subprocess.run(["./gradlew", "build"], cwd=project, env=dict(os.environ, JAVA_HOME=str(java_tools.find_jdk())),
                       capture_output=True, text=True, timeout=900)   # a cold Gradle cache: keep it out of start_timeout
    assert r.returncode == 0, "gradlew build failed:\n" + (r.stdout + r.stderr)[-3000:]
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(ROOT / "scenarios" / "maxswerve_auto.toml"),
                        "--out", str(out)], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    data = json.loads((out / "report.json").read_text())
    assert data["passed"], data
    checks = {c["check"]: c["passed"] for c in data["implicit"]}
    assert checks.get("REV simulation available") and checks.get("lockstep stayed engaged"), data["implicit"]
    assert not any("runs in real time (not lockstep)" in n for n in data.get("notes", []))  # REV follows sim time


# CTRE's Phoenix6-Examples has no license, so its Tuner X swerve project is downloaded at run time, never committed.
CTRE_COMMIT = "da029fc9676412dc9a24e3e2e3a3e4d6d71d5107"
CTRE_TARBALL = f"https://codeload.github.com/CrossTheRoadElec/Phoenix6-Examples/tar.gz/{CTRE_COMMIT}"
CTRE_PROJECT = f"Phoenix6-Examples-{CTRE_COMMIT}/java/SwerveWithPathPlanner/"

# From that commit's src/main/java/frc/robot/generated/TunerConstants.java, read once and hard-coded:
# corner -> (drive TalonFX id, steer TalonFX id, CANcoder id, CANcoder MagnetOffset in rotations, right side).
# All on CANBus "canivore", Pigeon 2 id 1. Phoenix 6 26.1.2 sim names CANivore devices like rio ones
# ("Talon FX (v6)[3]"), so the config needs no bus.
TUNER_MODULES = {"fl": (3, 2, 1, 0.15234375, False), "fr": (1, 0, 0, -0.4873046875, True),
                 "bl": (7, 6, 3, -0.219482421875, False), "br": (5, 4, 2, 0.17236328125, True)}
TUNER_PIGEON = 1
TUNER_DRIVE_RATIO = 7.363636363636365   # kDriveGearRatio
TUNER_STEER_RATIO = 15.42857142857143   # kSteerGearRatio


def tuner_x_config(project: Path, out: Path) -> Path:
    """A chassis and four stock swerve/MK4i parts, wired the way the Tuner X project's TunerConstants describes
    its robot. Every setting is copied from TunerConstants as-is; nothing is adjusted to fit the simulation:

    - Ratios: `ratio = kDriveGearRatio`, `steer_ratio = kSteerGearRatio`.
    - Inversions: steer motors are Inverted (kSteerMotorInverted = true), which is the MK4i part's default
      `steer_invert = true`. Right-side drive motors are Inverted (kInvertRightSide = true): `drive_invert` is
      the module's right-side flag. These are the real MK4i inversions, so the robot code's settings work unchanged.
    - IDs: `ids` take the module's drive/steer TalonFX and CANcoder; the Pigeon 2 is the chassis gyro.
    - The CANcoders are mounted with their magnets off by MagnetOffset: Phoenix adds the offset to the raw
      reading, so the raw magnet position with the wheel straight is -MagnetOffset (`offset`).
    - SwerveModule configures both motors NeutralMode Brake: `neutral = "brake"`. Kraken X60s (the part's
      default motors): kSpeedAt12Volts 4.54 m/s fits them.
    The coupling ratio (drive rotor turns per azimuth turn) isn't modeled: the part's joints are independent."""
    text = f'''[model]
timestep = 0.001
[robot]
start_pose = [2.0, 4.035, 0.0]
[lockstep]
step = 0.005
[robot_program]
command = ["./gradlew", "-I", "{{scrimmage_java}}/scrimmage.init.gradle", "simulateJava"]
cwd = "{project}"
start_timeout = 180

[[part]]
name = "drive"
stock = "chassis"
module_inset = 0.0579
ids = {{ gyro = "Pigeon2:{TUNER_PIGEON}" }}
'''
    mounts = {"fl": "front_left", "fr": "front_right", "bl": "back_left", "br": "back_right"}
    for corner, (drive, steer, cancoder, magnet_offset, right) in TUNER_MODULES.items():
        text += f'''
[[part]]
name = "{corner}"
stock = "swerve/MK4i"
attach = "drive.{mounts[corner]}"
ratio = {TUNER_DRIVE_RATIO}
steer_ratio = {TUNER_STEER_RATIO}
drive_invert = {str(right).lower()}
ids = {{ drive = {{ ctre = "TalonFX:{drive}", neutral = "brake" }}, steer = {{ ctre = "TalonFX:{steer}", neutral = "brake" }}, encoder = {{ ctre = "CANcoder:{cancoder}", offset = {-magnet_offset} }} }}
'''
    out.write_text(text)
    return out


def download_tuner_x_project(dest: Path) -> Path:
    """CTRE's java/SwerveWithPathPlanner at CTRE_COMMIT, extracted into dest (skips the test if unreachable)."""
    import http.client
    import io
    import tarfile
    import urllib.request
    try:
        with urllib.request.urlopen(CTRE_TARBALL, timeout=60) as r:
            data = r.read()
    except (OSError, http.client.HTTPException) as e:   # HTTPException covers IncompleteRead
        pytest.skip(f"couldn't download CTRE's Phoenix6-Examples at {CTRE_COMMIT[:12]} ({CTRE_TARBALL}): {e}")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            members = []
            for m in tar.getmembers():
                if m.name.startswith(CTRE_PROJECT) and (m.isfile() or m.isdir()):
                    m.name = m.name[len(CTRE_PROJECT):]
                    if m.name:
                        members.append(m)
            tar.extractall(dest, members=members, filter="data")
    except tarfile.TarError as e:   # a truncated or corrupt download
        pytest.skip(f"couldn't unpack CTRE's Phoenix6-Examples download: {e}")
    gradlew = dest / "gradlew"
    gradlew.chmod(0o755)   # the GitHub tarball doesn't keep the executable bit
    return dest


@pytest.mark.network
def test_tuner_x_project_follows_scrimmage_physics(tmp_path, caplog):
    """CTRE's own Tuner X swerve project (CANivore bus, PathPlanner, its startSimThread() sim model), unmodified."""
    import logging
    caplog.set_level(logging.WARNING, logger="scrimmage")
    project = download_tuner_x_project(tmp_path / "SwerveWithPathPlanner")
    r = subprocess.run(["./gradlew", "build"], cwd=project, env=dict(os.environ, JAVA_HOME=str(java_tools.find_jdk())),
                       capture_output=True, text=True, timeout=900)   # PathplannerLib: more vendordeps to fetch
    assert r.returncode == 0, "gradlew build failed:\n" + (r.stdout + r.stderr)[-3000:]
    cfg = tuner_x_config(project, tmp_path / "tuner_x.toml")

    async def main():
        s = Session(cfg, nt=False)
        try:
            st = await s.start()
            devices = [d for d in st["devices"] if "(v6)" in d]
            assert "CANMotor/Talon FX (v6)[3]" in devices and "CANGyro/Pigeon 2 (v6)[1]" in devices, devices
            x0 = s.state()["pose"]["x"]
            s.set_mode("teleop")
            s.set_joystick(0, axes=[0.0, -0.5, 0.0, 0.0, 0.0, 0.0])   # -getLeftY() is +x: push the left stick forward
            await s.advance(2.0)
            st = s.status()
            pose = s.state()["pose"]
            skipped = s.bridge.physics_skipped
            assert pose["x"] - x0 > 0.5, pose                  # ground truth: it drove forward
            assert st["foreign_writes"] == [], st["foreign_writes"]
            summary = st["agent_summary"]
            # the template's sim thread calls updateSimState on CTRE's SwerveDrivetrain: that call was skipped
            assert summary is not None and "SwerveDrivetrain.updateSimState from" in summary, summary
            assert "startSimThread" in summary
            straight = abs(pose["y"] - 4.035) < 0.3 and abs(pose["heading_deg"]) < 10
            if not straight and skipped > 0:
                # Phoenix 6 runs on the wall clock: physics that skipped ahead moves the robot without its heading
                # control seeing it (on a 0.5x CI runner it ended 11.7 and 19.7 deg off). The checks above still hold.
                pytest.skip(f"this machine couldn't run Phoenix 6 at real time: physics skipped ahead {skipped:.2f} s "
                            f"(failed: drove straight, {pose})")
            assert straight, pose                              # ...and straight
        finally:
            await s.stop()
    asyncio.run(main())
    assert not [r.getMessage() for r in caplog.records if "config expects" in r.getMessage()]


# The 30 s hold measured for Task 8 is in .superpowers/sdd/2026-10-11-java-ctre/task-8-report.md. The Talon's enable
# times out about 100 ms into a hold, so 5 s guards the same wind-up regression.
MM_HOLD = 5.0


async def motion_magic_run(cfg: Path, hold: float) -> dict:
    """PROBE_MODE=mm: Motion Magic on TalonFX 1 to 20 rotor rotations. Enable, advance 0.5 s (mid-move),
    optionally wait `hold` wall seconds, then advance 3 s. Rotor position is read from the joint (ground truth).
    "log" is the probe's last few cfg/mm console lines, for assertion messages."""
    s = Session(cfg, nt=False)
    trace: list[float] = []
    try:
        await s.start()
        await s.advance(0.5)                     # disabled: Phoenix finishes applying the config
        s.set_mode("teleop")
        record = lambda t: trace.append(s.state()["joints"]["left_center"]["position"] / (2 * math.pi) * 10.0)
        await s.advance(0.5, record)
        mid = trace[-1]
        if hold:
            await asyncio.sleep(hold)            # the robot and physics keep running in real time meanwhile
        await s.advance(3.0, record)
        await asyncio.sleep(0.5)                 # Gradle forward the JVM's output a little behind
        lines = [l["text"] for l in s.console()["lines"]]
        cfg_lines = [l for l in lines if l.startswith("cfg ")]
        mm_lines = [l for l in lines if l.startswith("mm pos=")]
        return {"mid": mid, "peak": max(trace), "final": trace[-1], "foreign_writes": s.status()["foreign_writes"],
                "cfg": cfg_lines, "log": "\n".join(cfg_lines + mm_lines[-(10 - len(cfg_lines)):])}
    finally:
        await s.stop()


def test_motion_magic_survives_a_held_session(tmp_path):
    """A caller that waits mid-move between advances (as an agent does between calls). A Phoenix robot runs in real
    time, so scrimmage can't hold it: the robot and physics carry on through the wait, and the move ends where one
    without a wait does, with no extra overshoot. (This once checked a held lockstep session; Phoenix robots no
    longer run in lockstep.)"""
    cfg = probe_config(tmp_path, PROBE_MODE="mm")
    base = asyncio.run(motion_magic_run(cfg, hold=0))
    held = asyncio.run(motion_magic_run(cfg, hold=MM_HOLD))
    for name, r in (("baseline", base), ("held", held)):
        why = f"{name}: {r}\nprobe output:\n{r['log']}"
        assert "cfg mm OK" in r["cfg"], why        # a failed apply leaves Motion Magic with zero gains: no move
        assert 0.5 < r["mid"] < 6.0, why           # mid-move when the hold starts
        assert r["foreign_writes"] == [], why
        assert r["final"] == pytest.approx(20.0, abs=0.5), why
    why = f"baseline: {base}\nheld: {held}\nheld probe output:\n{held['log']}"
    assert held["peak"] <= base["peak"] + 0.2, why  # no wind-up overshoot from the hold
    assert held["final"] == pytest.approx(base["final"], rel=0.01), why
