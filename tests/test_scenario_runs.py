import os
import time
from pathlib import Path

import pytest

import scrimmage.session as session_mod
from scrimmage.assembler.runs import RunBusy, RunManager
from scrimmage.scenario import ScenarioError

SCEN = Path(__file__).resolve().parent / "scenarios"


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)


def make(tmp_path, name="fake_pass.toml"):
    # a copy next to the real scenarios would pollute the repo; keep the real path, send runs to tmp
    changes = []
    mgr = RunManager(lambda: changes.append(1), runs_dir=tmp_path / "runs")
    return mgr, SCEN / name, (SCEN / name).read_text(), changes


def finish(mgr, timeout=60):
    assert mgr.join(timeout)
    return mgr.runs(0)["runs"][0]


def test_pass_run_has_report_progress_and_snapshot(tmp_path):
    mgr, path, text, changes = make(tmp_path)
    steps = []
    rid = mgr.start(path, text)
    while not mgr.join(0.02):
        steps.append(mgr.runs(0)["runs"][0]["step_name"])
    st = finish(mgr)
    assert st["id"] == rid and st["state"] == "passed" and st["report"]["passed"] is True
    assert "idle" in steps or st["step_name"] == "auto"
    assert st["step_name"] == "auto" and st["total_t"] == 1.5 and st["t"] > 0
    assert any("fake robot step" in l for l in st["console"]) and len(st["console"]) <= 200
    snap = Path(st["out_dir"]) / "scenario.toml"
    assert Path(__import__("tomllib").loads(snap.read_text())["config"]).is_absolute()
    assert changes and mgr.runs(0)["version"] >= len(set(steps))
    assert mgr.file(rid, "report.json").is_file()
    # the report names the student's scenario, not the snapshot file
    assert st["report"]["scenario"] == "fake_pass.toml"
    assert __import__("json").loads(mgr.file(rid, "report.json").read_text())["scenario"] == "fake_pass.toml"
    assert mgr.file(rid, "report.md").read_text().startswith("# PASS: fake_pass.toml\n")


@pytest.fixture
def robot_pids(monkeypatch):
    """The process-group ids of every robot program a Session starts."""
    pids = []
    orig = session_mod.Session.start

    async def start(self, *a, **kw):
        r = await orig(self, *a, **kw)
        pids.append(self.robot.proc.pid)
        return r
    monkeypatch.setattr(session_mod.Session, "start", start)
    return pids


def assert_robot_gone(pids):
    assert pids, "no robot program was started"
    with pytest.raises(ProcessLookupError):
        os.killpg(pids[-1], 0)


def test_fail_run(tmp_path, robot_pids):
    mgr, path, text, _ = make(tmp_path, "fake_fail.toml")
    mgr.start(path, text)
    st = finish(mgr)
    assert st["state"] == "failed" and st["report"]["passed"] is False
    assert_robot_gone(robot_pids)


def test_crashing_run_is_error_and_cleans_up(tmp_path, robot_pids, monkeypatch):
    import scrimmage.scenario_runner as runner_mod

    async def boom(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(runner_mod, "_run_steps", boom)
    mgr, path, text, _ = make(tmp_path)
    mgr.start(path, text)
    st = finish(mgr)
    assert st["state"] == "error" and any("boom" in line for line in st["console"])
    assert_robot_gone(robot_pids)
    assert not mgr.running


def test_report_error_run_is_error_and_cleans_up(tmp_path, robot_pids, monkeypatch):
    import scrimmage.scenario_runner as runner_mod

    async def gone(session, scenario, report, *a, **kw):
        report.error = "the robot program went away"
    monkeypatch.setattr(runner_mod, "_run_steps", gone)
    mgr, path, text, _ = make(tmp_path)
    mgr.start(path, text)
    st = finish(mgr)
    assert st["state"] == "error" and st["report"]["error"] == "the robot program went away", st
    assert_robot_gone(robot_pids)


def test_unreadable_report_settles_as_error(tmp_path, monkeypatch):
    import scrimmage.scenario_runner as runner_mod

    def bad(self):
        raise ValueError("no dict")
    monkeypatch.setattr(runner_mod.Report, "to_dict", bad)
    monkeypatch.setattr(runner_mod.Report, "write", lambda self, out_dir: None)   # only the manager calls to_dict
    mgr, path, text, _ = make(tmp_path)
    mgr.start(path, text)
    st = finish(mgr)
    assert st["state"] == "error" and st["report"] is None
    assert any("couldn't read the report" in line for line in st["console"])
    assert not mgr.running
    mgr.start(path, text)   # not stuck busy
    finish(mgr)


def test_not_busy_as_soon_as_the_final_state_is_visible(tmp_path, monkeypatch):
    """The busy check follows the run's final state, not whether the worker thread has exited yet."""
    seen = []
    mgr, path, text, _ = make(tmp_path)

    def on_change():
        if mgr.runs(0)["runs"] and mgr.runs(0)["runs"][0]["state"] != "running" and not seen:
            seen.append(mgr.running)
            try:
                mgr.start(path, text)   # from the worker thread, before it exits
                seen.append("started")
            except RunBusy:
                seen.append("busy")
    mgr._on_change = on_change
    mgr.start(path, text)
    deadline = time.monotonic() + 60
    while len(seen) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert seen == [False, "started"]
    mgr.stop()
    assert mgr.join(30)
    while mgr.running and time.monotonic() < deadline:
        time.sleep(0.05)


def test_start_failures_are_scenario_errors_and_leave_no_folder(tmp_path, monkeypatch):
    mgr, path, text, _ = make(tmp_path)
    (tmp_path / "runs").write_text("a file where the runs folder should be")
    with pytest.raises(ScenarioError, match="run folder"):
        mgr.start(path, text)
    (tmp_path / "runs").unlink()
    from pathlib import Path as P
    orig = P.write_text

    def fail_write(self, *a, **kw):
        if self.name == "scenario.toml":
            raise OSError(28, "No space left on device")
        return orig(self, *a, **kw)
    monkeypatch.setattr(P, "write_text", fail_write)
    with pytest.raises(ScenarioError, match="No space left"):
        mgr.start(path, text)
    assert list((tmp_path / "runs").iterdir()) == []
    assert mgr.runs(0)["runs"] == [] and not mgr.running


def test_old_runs_stop_being_served(tmp_path, monkeypatch):
    import scrimmage.assembler.runs as runs_mod
    monkeypatch.setattr(runs_mod, "KEEP_RUNS", 2)
    mgr, path, text, _ = make(tmp_path)
    ids = []
    for _ in range(3):
        ids.append(mgr.start(path, text))
        mgr.stop()
        finish(mgr)
        while mgr.running:
            time.sleep(0.01)
    assert [r["id"] for r in mgr.runs(0)["runs"]] == ids[::-1][:2]
    assert mgr.file(ids[0], "scenario.toml") is None and set(mgr._dirs) == set(ids[1:])
    assert mgr.file(ids[2], "scenario.toml").is_file()


def test_invalid_text_raises_and_starts_nothing(tmp_path):
    mgr, path, _, _ = make(tmp_path)
    with pytest.raises(ScenarioError):
        mgr.start(path, "config = 'x'\nnot valid [[")
    with pytest.raises(ScenarioError):
        mgr.start(path, "config = '../fake_robot.toml'\n")  # no steps
    assert mgr.runs(0)["runs"] == [] and not (tmp_path / "runs").exists()


def test_stop_midrun_kills_robot_and_second_start_is_busy(tmp_path, robot_pids):
    pids = robot_pids
    mgr, path, text, _ = make(tmp_path)
    mgr.start(path, text)
    deadline = time.monotonic() + 30
    while mgr.runs(0)["runs"][0]["step"] is None and time.monotonic() < deadline:
        time.sleep(0.02)
    with pytest.raises(RunBusy):
        mgr.start(path, text)
    mgr.stop()
    st = finish(mgr, 10)
    assert st["state"] == "stopped"
    assert pids, "no robot program was started"
    with pytest.raises(ProcessLookupError):
        os.killpg(pids[0], 0)
    mgr.start(path, text)  # a new run is allowed afterwards
    mgr.stop()
    finish(mgr, 10)


def test_editing_the_scenario_file_during_a_run_does_not_change_it(tmp_path):
    robot = SCEN.parent / "fake_robot.toml"
    f = tmp_path / "live.toml"
    good = (SCEN / "fake_pass.toml").read_text().replace('"../fake_robot.toml"', f'"{robot}"')
    f.write_text(good)
    mgr = RunManager(lambda: None, runs_dir=tmp_path / "runs")
    mgr.start(f, f.read_text())
    f.write_text("garbage [[")
    st = finish(mgr)
    assert st["state"] == "passed" and [s["name"] for s in st["report"]["steps"]] == ["idle", "auto"]
    assert Path(st["out_dir"], "scenario.toml").read_text() == good


def test_file_path_guard(tmp_path):
    mgr, path, text, _ = make(tmp_path)
    rid = mgr.start(path, text)
    finish(mgr)
    assert mgr.file(rid, "scenario.toml").is_file()
    assert mgr.file(rid, "../../x") is None
    assert mgr.file(rid, "/etc/passwd") is None
    assert mgr.file(rid, "nope.txt") is None
    assert mgr.file("nosuchrun", "scenario.toml") is None
    assert mgr.file(rid, "") is None


def test_a_real_error_is_not_hidden_as_stopped(tmp_path, monkeypatch):
    import scrimmage.assembler.runs as runs_mod
    from scrimmage.scenario_runner import STOPPED, Report

    outcome = {}

    async def fake_run(scenario, out_dir, *, progress=None, cancel=None, **kw):
        cancel.set()   # Stop was pressed, but the run ended for another reason
        if outcome["raise"]:
            raise RuntimeError("the simulator crashed")
        r = Report(scenario.path.name, "")
        r.error = outcome["error"]
        return r
    monkeypatch.setattr(runs_mod, "run_scenario", fake_run)
    mgr, path, text, _ = make(tmp_path)
    for raise_, error, state in ((False, "the robot program exited with code 1", "error"),
                                 (False, STOPPED, "stopped"),
                                 (True, None, "error")):
        outcome.update({"raise": raise_, "error": error})
        mgr.start(path, text)
        st = finish(mgr)
        assert st["state"] == state, (error, st)
        if raise_:
            assert any("the simulator crashed" in line for line in st["console"])
        else:
            assert st["report"]["error"] == error
        while mgr.running:
            time.sleep(0.01)


# ---- Stop during the robot program's start-up -------------------------------------------------------------

def _marked_pids(marker: str) -> list[int]:
    out = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                with open(f"/proc/{name}/cmdline", "rb") as f:
                    if marker.encode() in f.read():
                        out.append(int(name))
            except OSError:
                pass
    return [p for p in out if not _zombie(p)]


def _zombie(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return True


def slow_robot_scenario(tmp_path) -> tuple[Path, str, str]:
    """A scenario whose robot program never connects (it sleeps for 60 s; start_timeout 120 s). Returns
    (scenario path, its text, a marker found only in that robot program's command line)."""
    import uuid
    marker = f"scrimmage-slow-{uuid.uuid4().hex}"
    robot = (SCEN.parent / "fake_robot.toml").read_text()
    robot = robot.replace('mjcf = "models/kitbot.xml"', f'mjcf = "{SCEN.parent / "models" / "kitbot.xml"}"')
    robot = robot.replace('command = ["{python}", "fake_robot_main.py", "--lockstep", "--drive"]',
                          f'command = ["{{python}}", "-c", "import time; time.sleep(60)", "{marker}"]')
    robot = robot.replace("start_timeout = 15", "start_timeout = 120")
    assert marker in robot and "start_timeout = 120" in robot
    (tmp_path / "slow_robot.toml").write_text(robot)
    scn = tmp_path / "slow.toml"
    scn.write_text((SCEN / "fake_pass.toml").read_text().replace("../fake_robot.toml", "slow_robot.toml"))
    return scn, scn.read_text(), marker


def _wait_started(marker, timeout=30):
    deadline = time.monotonic() + timeout
    while not _marked_pids(marker):
        assert time.monotonic() < deadline, "the robot program didn't start"
        time.sleep(0.05)


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
def test_stop_interrupts_the_robot_start_up(tmp_path):
    path, text, marker = slow_robot_scenario(tmp_path)
    mgr = RunManager(lambda: None, runs_dir=tmp_path / "runs")
    mgr.start(path, text)
    _wait_started(marker)
    time.sleep(0.3)
    t0 = time.monotonic()
    mgr.stop()
    assert mgr.join(5), "Stop didn't end the run"
    assert time.monotonic() - t0 < 2.5
    st = mgr.runs(0)["runs"][0]
    assert st["state"] == "stopped" and st["report"]["error"] == "stopped by the user", st
    assert _marked_pids(marker) == []


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
def test_kill_robots_kills_a_run_s_robot_program_by_token(tmp_path):
    path, text, marker = slow_robot_scenario(tmp_path)
    mgr = RunManager(lambda: None, runs_dir=tmp_path / "runs")
    mgr.start(path, text)
    _wait_started(marker)
    mgr.kill_robots()
    assert _marked_pids(marker) == []
    mgr.stop()
    assert mgr.join(10)


def test_watch_sees_every_step_and_the_lock_is_released(tmp_path):
    from scrimmage.assembler.simlock import SimLock
    lock = SimLock()
    seen = []
    mgr = RunManager(lambda: None, runs_dir=tmp_path / "runs", simlock=lock,
                     watch=lambda session, t: seen.append((session.t, t)))
    path = SCEN / "fake_pass.toml"
    mgr.start(path, path.read_text())
    assert lock.holder == "run"
    st = finish(mgr)
    assert st["state"] == "passed"
    assert len(seen) == round(1.5 / 0.005) and all(a == b for a, b in seen)
    assert [t for _, t in seen] == sorted(t for _, t in seen)
    while mgr.running:
        time.sleep(0.01)
    assert lock.holder is None


def test_a_failed_start_releases_the_lock(tmp_path):
    from scrimmage.assembler.simlock import SimLock
    lock = SimLock()
    mgr = RunManager(lambda: None, runs_dir=tmp_path / "runs", simlock=lock)
    path = SCEN / "fake_pass.toml"
    with pytest.raises(ScenarioError):
        mgr.start(path, "config = 'x'\nnot valid [[")
    assert lock.holder is None
