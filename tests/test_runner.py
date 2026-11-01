import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import scrimmage.session as session_mod
from scrimmage.render import RenderUnavailable
from scrimmage.scenario import load_scenario
from scrimmage.scenario_runner import PHOENIX_SLOW, run_scenario
from scrimmage.config import load_config
from scrimmage.session import Session

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)  # the fake robot serves no NetworkTables


def scenario(tmp_path, body: str) -> Path:
    p = tmp_path / "s.toml"
    p.write_text(f'config = "{TESTS / "fake_robot.toml"}"\n' + body)
    return p


SIMPLE = """
[[step]]
mode = "auto"
duration = 0.5
  [[step.check]]
  pose = { x = 2.0, y = 4.035, tol_xy = 0.05 }
  [[step.check]]
  always = { signal = "battery", min = 12.0 }
  [[step.screenshot]]
  view = "top"
  at = 0.25
  width = 320
  height = 200
"""


def test_runner_passes_and_writes_report(tmp_path):
    out = tmp_path / "out"
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), out))
    assert rep.exit_code == 0, rep.to_dict()
    data = json.loads((out / "report.json").read_text())
    assert data["passed"] is True and len(data["steps"][0]["checks"]) == 2
    shot = out / data["steps"][0]["screenshots"][0]
    assert shot.read_bytes()[:4] == b"\x89PNG"
    md = (out / "report.md").read_text()
    assert "PASS" in md and shot.name in md
    assert (out / "run.csv").exists() and (out / "robot.log").exists()
    assert set(data["files"]) == {"csv", "robot_log", "report", "report_md"}
    assert all((out / f).exists() for f in data["files"].values())


def test_runner_failure_has_measured_values(tmp_path):
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE.replace("x = 2.0", "x = 3.0"))),
                                   tmp_path / "out"))
    assert rep.exit_code == 1
    pose = rep.steps[0].checks[0]
    assert not pose.passed and "1.00 m from target" in pose.message


def test_runner_crash_is_a_failed_check(tmp_path):
    body = SIMPLE
    p = scenario(tmp_path, body)
    cfg = tmp_path / "crash.toml"
    cfg.write_text((TESTS / "fake_robot.toml").read_text()
                   .replace('"models/', f'"{TESTS / "models"}/')
                   .replace('"--lockstep", "--drive"]', '"--lockstep", "--exit-after", "20"]')
                   .replace('cwd = "."', f'cwd = "{TESTS}"'))
    p.write_text(p.read_text().replace(str(TESTS / "fake_robot.toml"), str(cfg)))
    rep = asyncio.run(run_scenario(load_scenario(p), tmp_path / "out"))
    assert rep.exit_code == 1
    crash = [c for c in rep.implicit if not c.passed]
    assert crash and "exited" in crash[0].message and "(t=None)" not in crash[0].message
    assert not any(c.check == "robot program running" and c.passed for c in rep.implicit)
    assert len(rep.steps) == 1 and rep.steps[0].interrupted.startswith("step interrupted at t=")
    assert rep.steps[0].screenshots == [] and not rep.to_dict()["steps"][0]["passed"]


def test_runner_marks_screenshots_skipped_when_render_unavailable(tmp_path, monkeypatch):
    def unavailable(self, *a, **k):
        raise session_mod.SessionError("headless rendering isn't available (no GL). On Linux set MUJOCO_GL=egl")
    monkeypatch.setattr(session_mod.Session, "_render_job", unavailable)   # every picture goes through it
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), tmp_path / "out"))
    assert rep.exit_code == 0
    assert rep.steps[0].skipped and "MUJOCO_GL" in rep.steps[0].skipped[0]


def test_lockstep_required_but_missing_is_exit_2(tmp_path, monkeypatch):
    import scrimmage.bridge as bridge_mod
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)
    cfg = tmp_path / "rt.toml"
    cfg.write_text((TESTS / "fake_robot.toml").read_text()
                   .replace('"models/', f'"{TESTS / "models"}/')
                   .replace(', "--lockstep", "--drive"]', "]").replace('cwd = "."', f'cwd = "{TESTS}"'))
    p = scenario(tmp_path, SIMPLE)
    p.write_text(p.read_text().replace(str(TESTS / "fake_robot.toml"), str(cfg)))
    rep = asyncio.run(run_scenario(load_scenario(p), tmp_path / "out"))
    assert rep.exit_code == 2 and "lockstep" in rep.error


def test_cli_run_rejects_invalid_scenario(tmp_path):
    p = scenario(tmp_path, '[[step]]\nmode = "auto"\nduration = 1\n[[step.check]]\nalways = { signal = "volts", min = 1 }\n')
    out = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(p), "--out", str(tmp_path / "o")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 2 and "battery, speed" in out.stderr


def test_cli_run_fake_robot(tmp_path):
    env = {**os.environ, "SCRIMMAGE_NT_CONNECT_TIMEOUT": "0.5"}  # the fake robot serves no NetworkTables
    out = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(scenario(tmp_path, SIMPLE)),
                          "--out", str(tmp_path / "o")], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PASS" in out.stdout


def test_nt_checks_without_networktables_are_exit_2(tmp_path, monkeypatch):
    import scrimmage.bridge as bridge_mod
    from scrimmage.nt_reader import NTUnavailable

    def unavailable(*a, **k):
        raise NTUnavailable("NetworkTables needs pyntcore: pip install 'scrimmage[nt]'")
    monkeypatch.setattr(bridge_mod, "NetworkTablesLink", unavailable)
    body = '[[step]]\nmode = "auto"\nduration = 0.5\n[[step.check]]\nalways = { signal = "nt:/x", min = 1 }\n'
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, body)), tmp_path / "out"))
    assert rep.exit_code == 2 and "scrimmage[nt]" in rep.error


def test_nt_always_check_says_how_it_was_sampled(tmp_path):
    pytest.importorskip("ntcore")
    body = '[[step]]\nmode = "auto"\nduration = 0.5\n[[step.check]]\nalways = { signal = "nt:/x", min = 1 }\n'
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, body)), tmp_path / "out"))
    check = rep.steps[0].checks[0]
    assert not check.passed and "sampled at 0.25 s hold points" in check.message, check.message


def test_cli_run_reports_unexpected_errors_without_a_traceback(tmp_path):
    out_file = tmp_path / "taken"
    out_file.write_text("not a directory")
    out = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(scenario(tmp_path, SIMPLE)),
                          "--out", str(out_file)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "scrimmage: " in out.stderr and "File exists" in out.stderr and "Traceback" not in out.stderr


def test_unexpected_error_mid_run_is_reported_as_an_error(tmp_path, monkeypatch, capsys):
    from scrimmage.__main__ import main

    async def disk_full(self, *a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(session_mod.Session, "screenshot_png", disk_full)
    out = tmp_path / "out"
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), out))
    data = json.loads((out / "report.json").read_text())
    assert data["passed"] is False and data["exit_code"] == 2 and "disk full" in data["error"]
    assert data["steps"][0]["interrupted"].startswith("step interrupted at t=")
    assert "ERROR" in (out / "report.md").read_text()

    with pytest.raises(SystemExit) as e:  # the CLI path
        main(["run", str(scenario(tmp_path, SIMPLE)), "--out", str(tmp_path / "out2")])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "scrimmage: disk full" in err and "Traceback" not in err


def test_nt_checks_on_a_session_without_nt_say_so(tmp_path):
    body = '[[step]]\nmode = "auto"\nduration = 0.5\n[[step.check]]\nalways = { signal = "nt:/x", min = 1 }\n'

    async def main():
        s = session_mod.Session(TESTS / "fake_robot.toml", nt=False)
        try:
            await s.start()
            return await run_scenario(load_scenario(scenario(tmp_path, body)), tmp_path / "out", session=s)
        finally:
            await s.stop()
    rep = asyncio.run(main())
    assert rep.exit_code == 2 and "started without NetworkTables (nt=False)" in rep.error


def test_joystick_axes_are_zeroed_in_a_step_that_sets_none(tmp_path, monkeypatch):
    import scrimmage.bridge as bridge_mod
    sent = []
    orig = bridge_mod.Bridge._send

    async def recording_send(self, frames):
        sent.extend(json.loads(f) for f in frames)
        await orig(self, frames)
    monkeypatch.setattr(bridge_mod.Bridge, "_send", recording_send)
    body = ('[[step]]\nmode = "teleop"\nduration = 0.3\naxes = { 0 = [0.0, 1.0] }\n'
            '[[step]]\nmode = "teleop"\nduration = 0.3\n')
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, body)), tmp_path / "out"))
    assert rep.error is None, rep.to_dict()
    axes = [f["data"][">axes"] for f in sent if f["type"] == "Joystick" and f["device"] == "0"]
    assert [0.0, 1.0] in axes  # step 1's input went out
    assert axes[-1] == [0.0, 0.0], axes[-3:]  # and step 2 neutralized it


def test_lockstep_release_fails_the_run(tmp_path, monkeypatch):
    orig = session_mod.Session._advance

    async def releasing(self, seconds, on_step):
        r = await orig(self, seconds, on_step)
        if self.bridge.lockstep_released_at is None:
            self.bridge.lockstep_released_at = self.t
        return r
    monkeypatch.setattr(session_mod.Session, "_advance", releasing)
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), tmp_path / "out"))
    assert rep.exit_code == 1
    bad = [c for c in rep.implicit if not c.passed]
    assert [c.check for c in bad] == ["lockstep stayed engaged"]
    assert "clock ran free at t=0.250" in bad[0].message and "aren't repeatable" in bad[0].message


def test_lockstep_engaged_check_passes_normally(tmp_path):
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), tmp_path / "out"))
    assert any(c.check == "lockstep stayed engaged" and c.passed for c in rep.implicit)


def test_cli_run_config_missing_a_key_is_exit_2(tmp_path):
    cfg = tmp_path / "bad.toml"
    cfg.write_text((TESTS / "fake_robot.toml").read_text().replace('"models/', f'"{TESTS / "models"}/')
                   .replace('joint = "left_center"\n', "", 1))
    p = scenario(tmp_path, SIMPLE)
    p.write_text(p.read_text().replace(str(TESTS / "fake_robot.toml"), str(cfg)))
    out = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(p), "--out", str(tmp_path / "o")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "missing key 'joint'" in out.stderr and "[[motor]]" in out.stderr and "Traceback" not in out.stderr


@pytest.mark.parametrize("which", ["scenario", "config"])
def test_cli_run_directory_path_is_exit_2(tmp_path, which):
    p = scenario(tmp_path, SIMPLE)
    if which == "scenario":
        p = tmp_path
    else:
        p.write_text(p.read_text().replace(str(TESTS / "fake_robot.toml"), str(tmp_path)))
    out = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(p), "--out", str(tmp_path / "o")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert str(tmp_path) in out.stderr and "Traceback" not in out.stderr


def test_foreign_sim_writes_fail_the_run_and_phoenix_notes_the_speed(tmp_path):
    cfg = (TESTS / "fake_robot.toml").read_text().replace('"models/', f'"{TESTS / "models"}/')
    cfg = cfg.replace('"--lockstep", "--drive"]', '"--lockstep", "--phoenix", "--foreign-after", "5"]')
    cfg = cfg.replace('cwd = "."', f'cwd = "{TESTS}"')
    cfg += '\n[[motor]]\nname = "phoenix"\nctre = "TalonFX:1"\njoint = "left_center"\nmotor = "KrakenX60"\n'
    (tmp_path / "robot.toml").write_text(cfg)
    sc = tmp_path / "s.toml"
    sc.write_text(f'config = "{tmp_path / "robot.toml"}"\n' + SIMPLE)
    out = tmp_path / "out"
    rep = asyncio.run(run_scenario(load_scenario(sc), out))
    assert rep.exit_code == 1, rep.to_dict()
    bad = [c for c in rep.implicit if not c.passed]
    assert [c.check for c in bad] == ["no foreign sim writes"]
    msg = bad[0].message
    assert msg.startswith("something in the robot program writes simulated sensor values for Talon FX (v6)[1] "
                          "(rotor position, first at t="), msg
    assert "hasn't said the scrimmage Java agent is loaded" in msg and "scrimmage java path" in msg   # no agent line
    assert "remove or guard the robot-side sim model" in msg and "Rotor Sensor" not in msg
    assert rep.effective_speed == 1.0
    note = ("Phoenix 6 simulation runs on the wall clock, so this robot runs in real time (not lockstep); "
            "results are close but not byte-identical")
    assert rep.notes == [note]
    (lock,) = [c for c in rep.implicit if c.check == "lockstep stayed engaged"]
    assert lock.passed and lock.message == "Phoenix 6 devices: this robot runs in real time, not lockstep"
    assert rep.lockstep is False
    data = json.loads((out / "report.json").read_text())
    assert data["notes"] == [note] and data["effective_speed"] == 1.0
    assert note in (out / "report.md").read_text() and note in rep.summary()


def test_a_phoenix_robot_program_that_falls_behind_real_time_says_so(tmp_path, monkeypatch):
    """Physics keeping up isn't enough: a Phoenix 6 robot program starved of CPU (a 2-vCPU CI runner) falls behind the
    wall clock. Here every frame from it arrives as if it had been silent 0.4 s: the report says it fell behind."""
    from scrimmage.protocol import OutboundCache
    real_heard = OutboundCache.heard
    monkeypatch.setattr(OutboundCache, "heard", lambda self: max(real_heard(self), 0.4))
    cfg = (TESTS / "fake_robot.toml").read_text().replace('"models/', f'"{TESTS / "models"}/')
    cfg = cfg.replace('"--lockstep", "--drive"]', '"--lockstep", "--phoenix"]')
    cfg = cfg.replace('cwd = "."', f'cwd = "{TESTS}"')
    cfg += '\n[[motor]]\nname = "phoenix"\nctre = "TalonFX:1"\njoint = "left_center"\nmotor = "KrakenX60"\n'
    (tmp_path / "robot.toml").write_text(cfg)
    sc = tmp_path / "s.toml"
    sc.write_text(f'config = "{tmp_path / "robot.toml"}"\n[[step]]\nmode = "auto"\nduration = 0.5\n')
    rep = asyncio.run(run_scenario(load_scenario(sc), tmp_path / "out"))
    behind = [n for n in rep.notes if "behind real time" in n]
    assert behind == ["the robot program fell 0.40 s behind real time (silent, or relaying values that long out of "
                      "date): Phoenix 6 runs on the wall clock, so its timing may differ from a run that keeps up"], \
        rep.notes
    assert not [n for n in rep.notes if "kept up with only" in n]


def test_a_phoenix_run_that_falls_behind_real_time_says_so(tmp_path, monkeypatch):
    """A Phoenix 6 robot runs in real time; when physics can't keep up (here each 1 ms physics step takes 2 ms of
    wall time, so about 0.4x), it skips ahead and the report says physics fell behind real time."""
    import scrimmage.world as world_mod
    real_step = world_mod.World.step

    def slow_step(self, hal):
        time.sleep(0.002)
        return real_step(self, hal)
    monkeypatch.setattr(world_mod.World, "step", slow_step)
    cfg = (TESTS / "fake_robot.toml").read_text().replace('"models/', f'"{TESTS / "models"}/')
    cfg = cfg.replace('"--lockstep", "--drive"]', '"--lockstep", "--phoenix"]')
    cfg = cfg.replace('cwd = "."', f'cwd = "{TESTS}"')
    cfg += '\n[[motor]]\nname = "phoenix"\nctre = "TalonFX:1"\njoint = "left_center"\nmotor = "KrakenX60"\n'
    (tmp_path / "robot.toml").write_text(cfg)
    sc = tmp_path / "s.toml"
    sc.write_text(f'config = "{tmp_path / "robot.toml"}"\n[[step]]\nmode = "auto"\nduration = 1.5\n')
    rep = asyncio.run(run_scenario(load_scenario(sc), tmp_path / "out"))
    slow = [n for n in rep.notes if "kept up with only" in n]
    assert len(slow) == 1 and "physics fell behind real time" in slow[0], rep.notes
    assert 0.15 < float(slow[0].split("only ")[1].split("x")[0]) < 0.75, slow

    async def short_advances():   # as the scenario runner makes when it samples NetworkTables: 0.2 s at a time
        s = Session(load_config(tmp_path / "robot.toml"), nt=False)
        try:
            await s.start()
            for _ in range(8):
                await s.advance(0.2)
            return s.phoenix_speed
        finally:
            await s.stop()
    speed = asyncio.run(short_advances())
    assert speed is not None and 0.15 < speed < 0.75, speed


def test_a_phoenix_run_whose_physics_skips_ahead_once_says_so(tmp_path, monkeypatch):
    """One stall (a step taking 0.5 s of wall time) makes physics skip ahead; averaged over the run it still keeps
    up with more than PHOENIX_SLOW of real time, and the report says it fell behind, by how much."""
    import scrimmage.world as world_mod
    real_step = world_mod.World.step
    stalled = []

    def stall_once(self, hal):
        if self.time >= 1.0 and not stalled:
            stalled.append(self.time)
            time.sleep(0.5)
        return real_step(self, hal)
    monkeypatch.setattr(world_mod.World, "step", stall_once)
    cfg = (TESTS / "fake_robot.toml").read_text().replace('"models/', f'"{TESTS / "models"}/')
    cfg = cfg.replace('"--lockstep", "--drive"]', '"--lockstep", "--phoenix"]')
    cfg = cfg.replace('cwd = "."', f'cwd = "{TESTS}"')
    cfg += '\n[[motor]]\nname = "phoenix"\nctre = "TalonFX:1"\njoint = "left_center"\nmotor = "KrakenX60"\n'
    (tmp_path / "robot.toml").write_text(cfg)
    sc = tmp_path / "s.toml"
    sc.write_text(f'config = "{tmp_path / "robot.toml"}"\n[[step]]\nmode = "auto"\nduration = 4.0\n')
    rep = asyncio.run(run_scenario(load_scenario(sc), tmp_path / "out"))
    slow = [n for n in rep.notes if "kept up with only" in n]
    assert len(slow) == 1 and "physics fell behind real time and skipped ahead 0." in slow[0], rep.notes
    assert "(1 time)" in slow[0] and float(slow[0].split("only ")[1].split("x")[0]) >= PHOENIX_SLOW, slow


def test_a_slow_phoenix_run_gets_the_time_physics_skipped_but_a_hard_limit(tmp_path, monkeypatch):
    """A real-time advance's wall-time budget is credited with the time physics skipped ahead, so a slow machine's run
    finishes (its report says physics fell behind) instead of being cut off; ADVANCE_MAX_FACTOR still bounds it. Here
    each 1 ms step takes 2 ms (about 0.4x) against a budget of 1.2x the step's time."""
    import scrimmage.session as session_mod
    import scrimmage.world as world_mod
    real_step = world_mod.World.step

    def slow_step(self, hal):
        time.sleep(0.002)
        return real_step(self, hal)
    monkeypatch.setattr(world_mod.World, "step", slow_step)
    monkeypatch.setattr(session_mod, "ADVANCE_FACTOR", 1.2)
    monkeypatch.setattr(session_mod, "ADVANCE_EXTRA", 0.0)
    cfg = (TESTS / "fake_robot.toml").read_text().replace('"models/', f'"{TESTS / "models"}/')
    cfg = cfg.replace('"--lockstep", "--drive"]', '"--lockstep", "--phoenix"]')
    cfg = cfg.replace('cwd = "."', f'cwd = "{TESTS}"')
    cfg += '\n[[motor]]\nname = "phoenix"\nctre = "TalonFX:1"\njoint = "left_center"\nmotor = "KrakenX60"\n'
    (tmp_path / "robot.toml").write_text(cfg)
    sc = tmp_path / "s.toml"
    sc.write_text(f'config = "{tmp_path / "robot.toml"}"\n[[step]]\nmode = "auto"\nduration = 1.5\n')
    rep = asyncio.run(run_scenario(load_scenario(sc), tmp_path / "out"))
    assert rep.steps[-1].interrupted is None and not rep.error, (rep.steps[-1].interrupted, rep.error)
    assert [n for n in rep.notes if "skipped ahead" in n], rep.notes
    monkeypatch.setattr(session_mod, "ADVANCE_MAX_FACTOR", 1.2)          # the hard limit, below what it needs
    rep = asyncio.run(run_scenario(load_scenario(sc), tmp_path / "out2"))
    assert rep.steps[-1].interrupted is not None, rep.notes
    assert any("physics reached" in (c.message or "") for c in rep.implicit), rep.implicit


def test_a_fast_mode_run_with_phoenix_devices_fails_lockstep(tmp_path):
    cfg = (TESTS / "fake_robot.toml").read_text().replace('"models/', f'"{TESTS / "models"}/')
    cfg = cfg.replace('"--lockstep", "--drive"]', '"--lockstep", "--phoenix"]')
    cfg = cfg.replace('cwd = "."', f'cwd = "{TESTS}"')
    cfg += '\n[[motor]]\nname = "phoenix"\nctre = "TalonFX:1"\njoint = "left_center"\nmotor = "KrakenX60"\n'
    (tmp_path / "robot.toml").write_text(cfg)
    sc = tmp_path / "s.toml"
    sc.write_text(f'config = "{tmp_path / "robot.toml"}"\n[[step]]\nmode = "auto"\nduration = 0.3\n')
    rep = asyncio.run(run_scenario(load_scenario(sc), tmp_path / "out", sim_mode="fast"))
    msg = ("fast mode, but the robot program created Phoenix 6 devices (Talon FX (v6)[1]): they force real time; "
           "use the fast-mode IO for them")
    assert msg in rep.notes, rep.notes
    # Unlike Phoenix mode, where the release is on purpose, in fast mode it fails "lockstep stayed engaged".
    assert rep.exit_code == 1, rep.to_dict()
    assert [(c.check, c.message) for c in rep.implicit if not c.passed] == [("lockstep stayed engaged", msg)]
    assert rep.lockstep is False


def test_no_foreign_writes_check_passes_normally(tmp_path):
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), tmp_path / "out"))
    assert any(c.check == "no foreign sim writes" and c.passed for c in rep.implicit)
    assert rep.notes == [] and rep.effective_speed is None


def test_agent_summary_becomes_a_note_when_nothing_got_through(tmp_path, monkeypatch):
    line = ("scrimmage agent: ignored 42 robot-side sim writes "
            "(TalonFXSimState.setRawRotorPosition from frc.robot.Robot.simulationPeriodic)")
    orig = session_mod.Session._advance

    async def advance(self, seconds, on_step):
        self.robot._add(line.encode())
        return await orig(self, seconds, on_step)
    monkeypatch.setattr(session_mod.Session, "_advance", advance)
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), tmp_path / "out"))
    assert rep.exit_code == 0, rep.to_dict()
    assert rep.notes == ["the scrimmage agent neutralized 42 robot-side sim writes "
                         "(TalonFXSimState.setRawRotorPosition from frc.robot.Robot.simulationPeriodic)"]


def test_rev_helper_summary_becomes_a_note_when_nothing_got_through(tmp_path, monkeypatch):
    """A RobotPy robot's summary comes from scrimmage_lockstep's REV helper, with its own prefix."""
    line = ("scrimmage rev helper: ignored 7 robot-side sim writes "
            "(SparkSim.iterate from robot.MyRobot._simulationPeriodic)")
    orig = session_mod.Session._advance

    async def advance(self, seconds, on_step):
        self.robot._add(line.encode())
        return await orig(self, seconds, on_step)
    monkeypatch.setattr(session_mod.Session, "_advance", advance)
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), tmp_path / "out"))
    assert rep.exit_code == 0, rep.to_dict()
    assert rep.notes == ["the scrimmage rev helper neutralized 7 robot-side sim writes "
                         "(SparkSim.iterate from robot.MyRobot._simulationPeriodic)"]


def test_report_notes_carry_slot_warnings(tmp_path):
    from parts_fake import WARNING, parts_config
    p = tmp_path / "s.toml"
    p.write_text(f'config = "{parts_config(tmp_path)}"\n[[step]]\nmode = "auto"\nduration = 0.2\n')
    report = asyncio.run(run_scenario(load_scenario(p), tmp_path / "out", speed=None))
    assert any(WARNING in n for n in report.notes)


FAKE_PASS = TESTS / "scenarios" / "fake_pass.toml"
FAKE_FAIL = TESTS / "scenarios" / "fake_fail.toml"


def test_progress_events_in_order_with_spaced_ticks(tmp_path):
    events = []
    rep = asyncio.run(run_scenario(load_scenario(FAKE_PASS), tmp_path / "o", progress=events.append))
    assert rep.exit_code == 0, rep.to_dict()
    kinds = [e["type"] for e in events]
    assert kinds[0] == "step_start" and kinds[-1] == "done" and kinds.count("done") == 1
    assert events[-1] == {"type": "done", "passed": True}
    starts = [e for e in events if e["type"] == "step_start"]
    assert starts == [{"type": "step_start", "index": 0, "name": "idle", "mode": "disabled", "duration": 0.5},
                      {"type": "step_start", "index": 1, "name": "auto", "mode": "auto", "duration": 1.0}]
    assert kinds.index("step_start") < kinds.index("tick")
    ticks = [e["t"] for e in events if e["type"] == "tick"]
    assert len(ticks) >= 5 and ticks == sorted(ticks)
    assert all(b - a >= 0.25 - 1e-6 for a, b in zip(ticks, ticks[1:]))
    lines = [e["line"] for e in events if e["type"] == "console"]
    assert "fake robot connected" in lines and any(l.startswith("fake robot step") for l in lines)


def test_failing_scenario_reports_done_not_passed(tmp_path):
    events = []
    rep = asyncio.run(run_scenario(load_scenario(FAKE_FAIL), tmp_path / "o", progress=events.append))
    assert rep.exit_code == 1
    assert events[-1] == {"type": "done", "passed": False}


def _run_with_own_session(scen, out, **kw):
    """run_scenario on a session the test started, so the test can see the robot's pid."""
    from scrimmage.config import load_config
    from scrimmage.session import Session

    async def go():
        session = Session(load_config(scen.config_path), speed=scen.speed)
        await session.start()
        return await run_scenario(scen, out, session=session, **kw)
    return asyncio.run(go())


def test_cancel_after_first_step_start_stops_and_kills_the_robot(tmp_path):
    cancel = threading.Event()
    pids = []
    scen = load_scenario(FAKE_PASS)
    from scrimmage.config import load_config
    from scrimmage.session import Session

    async def go():
        session = Session(load_config(scen.config_path), speed=scen.speed)
        await session.start()

        def progress(e):
            if e["type"] == "step_start" and e["index"] == 0:
                pids.append(session.robot.proc.pid)
                cancel.set()
        return await run_scenario(scen, tmp_path / "o", session=session, progress=progress, cancel=cancel)

    t0 = time.monotonic()
    rep = asyncio.run(go())
    assert time.monotonic() - t0 < 5
    assert rep.error == "stopped by the user" and rep.exit_code == 2
    assert len(rep.steps) == 1
    assert json.loads((tmp_path / "o" / "report.json").read_text())["error"] == "stopped by the user"
    with pytest.raises(ProcessLookupError):
        os.killpg(pids[0], 0)


def test_cancel_mid_step_from_a_tick_is_prompt(tmp_path):
    cancel = threading.Event()
    events = []

    def progress(e):
        events.append(e)
        if e["type"] == "tick" and e["t"] >= 0.5:
            cancel.set()
    scen = load_scenario(FAKE_PASS)
    t0 = time.monotonic()
    rep = asyncio.run(run_scenario(scen, tmp_path / "o", progress=progress, cancel=cancel))
    assert time.monotonic() - t0 < 2 + 3  # run time before the cancel + the 2 s budget after it
    assert rep.error == "stopped by the user"
    assert len(rep.steps) == 2 and rep.steps[1].end_t < 1.5  # stopped inside the second step
    assert events[-1] == {"type": "done", "passed": False, "error": "stopped by the user"}


def test_cancel_before_start_returns_immediately(tmp_path):
    cancel = threading.Event()
    cancel.set()
    rep = asyncio.run(run_scenario(load_scenario(FAKE_PASS), tmp_path / "o", cancel=cancel))
    assert rep.error == "stopped by the user" and rep.steps == []


def test_done_is_emitted_when_the_run_raises(tmp_path, monkeypatch):
    events = []

    async def boom(*a, **k):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(session_mod.Session, "advance", boom)
    with pytest.raises(RuntimeError):
        asyncio.run(run_scenario(load_scenario(FAKE_PASS), tmp_path / "o", progress=events.append))
    assert events[-1]["type"] == "done" and events[-1]["passed"] is False and "kaboom" in events[-1]["error"]


def test_hooks_do_not_change_results_with_a_step_that_does_not_divide_a_tick(tmp_path):
    body = (TESTS / "scenarios" / "fake_pass.toml").read_text().replace("../fake_robot.toml", str(TESTS / "fake_robot.toml"))
    cfg = (TESTS / "fake_robot.toml").read_text().replace("step = 0.005", "step = 0.02").replace("models/kitbot.xml", str(TESTS / "models" / "kitbot.xml"))
    (tmp_path / "cfg.toml").write_text(cfg.replace('cwd = "."', f'cwd = "{TESTS}"'))
    (tmp_path / "s.toml").write_text(body.replace(str(TESTS / "fake_robot.toml"), str(tmp_path / "cfg.toml"))
                                     .replace("duration = 1.0", "duration = 0.25"))
    sc = load_scenario(tmp_path / "s.toml")

    def run(name, **kw):
        out = tmp_path / name
        rep = asyncio.run(run_scenario(sc, out, **kw))
        assert rep.exit_code in (0, 1), rep.to_dict()  # ran to the end (the console check may not see a line)
        return json.loads((out / "report.json").read_text()), (out / "run.csv").read_text()
    plain = run("plain")
    hooked = run("hooked", progress=lambda e: None, cancel=threading.Event())
    assert plain[0]["steps"][1]["end_t"] == hooked[0]["steps"][1]["end_t"]
    assert plain == hooked


def test_cli_runs_fake_scenarios_with_unchanged_output(tmp_path):
    env = {**os.environ, "SCRIMMAGE_NT_CONNECT_TIMEOUT": "0.5"}
    def run(path):
        return subprocess.run([sys.executable, "-m", "scrimmage", "run", str(path), "--out", str(tmp_path / path.stem)],
                              capture_output=True, text=True, timeout=120, env=env, cwd=ROOT)
    ok = run(FAKE_PASS)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert ok.stdout.startswith("PASS  fake_pass.toml\n") and "ok   [auto] console" in ok.stdout
    bad = run(FAKE_FAIL)
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert bad.stdout.startswith("FAIL  fake_fail.toml\n") and "FAIL [auto] " in bad.stdout


def test_picture_sizes_are_built_before_the_robot_program_starts(monkeypatch):
    """The runner tells the Session which picture sizes it will use; their renderers exist before the robot program
    is launched, so building them can't stall a held robot (seen on CI's software GL)."""
    from scrimmage.scenario_runner import picture_sizes
    sc = load_scenario(TESTS.parent / "scenarios" / "kitbot_auto.toml")
    assert picture_sizes(sc) == [(960, 600), (480, 300)]

    seen = {}
    orig = session_mod.Session._launch

    async def launch(self):
        seen["ready"] = sorted(self._renderer._rs) if self._renderer is not None else []
        return await orig(self)
    monkeypatch.setattr(session_mod.Session, "_launch", launch)

    async def main():
        s = session_mod.Session(TESTS / "fake_robot.toml", nt=False, picture_sizes=[(160, 100), (80, 50)])
        try:
            await s.start()
        finally:
            await s.stop()
    asyncio.run(main())
    assert seen["ready"] == [(80, 50), (160, 100)]
