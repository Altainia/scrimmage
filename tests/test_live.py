"""LiveManager with the fake robot (tests/fake_robot.toml, 5 ms steps): a live drive session on a worker thread."""

import asyncio
import json
import os
import struct
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

import scrimmage.session as session_mod
from scrimmage.assembler.live import LiveError, LiveManager
from scrimmage.assembler.runs import RunBusy, RunManager
from scrimmage.assembler.simlock import SimBusy, SimLock
from scrimmage.scenario import load_scenario
from scrimmage.scenario_runner import run_scenario

TESTS = Path(__file__).resolve().parent
ROBOT = TESTS / "fake_robot.toml"
SCEN = TESTS / "scenarios"
STEP = 0.005
HEAD = struct.Struct("<IBdH")


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)  # the fake robot serves no NetworkTables


def wait_for(pred, timeout=20.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def frames(pub):
    out = []
    for kind, msg in list(pub):
        if kind == "frame":
            seq, source, t, n = HEAD.unpack_from(msg)
            poses = np.frombuffer(msg, dtype="<f4", offset=HEAD.size).reshape(n, 7)
            out.append((seq, source, t, poses))
    return out


def states(pub):
    return [m for k, m in list(pub) if k == "state"]


def notices(pub):
    return [m for k, m in list(pub) if k == "notice"]


def readouts(pub):
    return states(pub)[-1]["readouts"]


def fresh_state(pub, mgr, pred=lambda s: True, timeout=5.0):
    """The first state message published after now that satisfies pred."""
    n = len(states(pub))
    found = []

    def ok():
        for s in states(pub)[n:]:
            if pred(s):
                found.append(s)
                return True
        return False
    wait_for(ok, timeout, "a state message")
    return found[0]


def config_copy(tmp_path, command_extra=(), command=None, name="robot.toml", start_timeout=None):
    text = ROBOT.read_text().replace('mjcf = "models/kitbot.xml"', f'mjcf = "{TESTS / "models" / "kitbot.xml"}"')
    text = text.replace('cwd = "."', f'cwd = "{TESTS}"')
    old = 'command = ["{python}", "fake_robot_main.py", "--lockstep", "--drive"]'
    if command is None:
        command = ["{python}", "fake_robot_main.py", "--lockstep", "--drive", *command_extra]
    text = text.replace(old, "command = " + json.dumps(command))
    if start_timeout is not None:
        text = text.replace("start_timeout = 15", f"start_timeout = {start_timeout}")
    p = tmp_path / name
    p.write_text(text)
    return p


# ---- one shared live session for the command tests ---------------------------------------------------------

@pytest.fixture(scope="module")
def live():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)
        lock = SimLock()
        pub = []
        mgr = LiveManager(lock, lambda kind, msg: pub.append((kind, msg)))
        mgr.start(ROBOT)
        wait_for(lambda: mgr.status()["state"] in ("running", "error"), 30, "the session to run")
        assert mgr.status()["state"] == "running", mgr.status()
        yield mgr, pub, lock
        mgr.stop()
        assert mgr.join(10)
        assert mgr.status()["state"] == "stopped" and lock.holder is None


@pytest.fixture
def running(live):
    """The shared session, running (not paused) and disabled, with no stale notices."""
    mgr, pub, lock = live
    mgr.command({"cmd": "resume"})
    mgr.command({"cmd": "disable"})
    wait_for(lambda: not mgr.status()["paused"] and not mgr.status()["enabled"])
    return mgr, pub, lock


def test_start_runs_and_frames_decode(running):
    mgr, pub, lock = running
    assert lock.holder == "live"
    with pytest.raises(LiveError, match="a drive session is already live"):
        mgr.start(ROBOT)
    wait_for(lambda: len(frames(pub)) >= 5, 10, "frames")
    fs = frames(pub)
    n = len(mgr._session.movers())
    assert n > 0 and all(f[3].shape == (n, 7) and f[1] == 0 for f in fs)
    ts = [f[2] for f in fs]
    seqs = [f[0] for f in fs]
    assert ts == sorted(ts) and ts[-1] > ts[0] and seqs == sorted(set(seqs))
    st = states(pub)
    assert [s["state"] for s in st][:1] == ["starting"] and any(s["state"] == "running" for s in st)
    s = st[-1]
    assert s["source"] == "live" and "pose" in s["readouts"] and isinstance(s["console_seq"], int)
    assert s["mode"] == "disabled" and s["enabled"] is False and s["speed"] == 1.0 and s["speed_locked"] is None


def test_pause_holds_time_and_step_advances_exactly(running):
    mgr, pub, _ = running
    mgr.command({"cmd": "pause"})
    wait_for(lambda: mgr.status()["state"] == "paused")
    t0 = mgr.status()["t"]
    time.sleep(0.3)
    assert mgr.status()["t"] == t0
    mgr.command({"cmd": "step", "n": 4})
    wait_for(lambda: mgr.status()["t"] >= round(t0 + 4 * STEP, 4) - 1e-9)
    time.sleep(0.2)
    st = mgr.status()
    assert st["t"] == round(t0 + 4 * STEP, 4) and st["paused"] and st["state"] == "paused"
    mgr.command({"cmd": "resume"})
    wait_for(lambda: mgr.status()["t"] > t0 + 0.05)
    assert mgr.status()["state"] == "running"


def test_speed(running):
    mgr, pub, _ = running
    mgr.command({"cmd": "speed", "speed": 2})
    wait_for(lambda: mgr.status()["speed"] == 2.0)
    mgr.command({"cmd": "speed", "speed": "max"})
    wait_for(lambda: mgr.status()["speed"] is None)
    for bad in (0.005, 0, -1, "fast", True, float("nan")):
        with pytest.raises(LiveError):
            mgr.command({"cmd": "speed", "speed": bad})
    mgr.command({"cmd": "speed", "speed": 1})
    wait_for(lambda: mgr.status()["speed"] == 1.0)


def test_command_validation(running):
    mgr, _, _ = running
    for bad in ({"cmd": "fly"}, {}, "pause", {"cmd": "mode", "mode": "disabled"}, {"cmd": "step", "n": 0},
                {"cmd": "step", "n": 501}, {"cmd": "step", "n": 2.5}, {"cmd": "set_pose", "x": 1, "y": "a",
                                                                         "heading_deg": 0},
                {"cmd": "push", "force_n": 10, "direction_deg": 0, "duration_s": 0},
                {"cmd": "move_body", "name": 3, "x": 0, "y": 0, "yaw_deg": 0},
                {"cmd": "signals", "names": ["battery"] * 5}, {"cmd": "signals", "names": [1]}):
        with pytest.raises(LiveError):
            mgr.command(bad)


def test_pokes(running):
    mgr, pub, _ = running
    mgr.command({"cmd": "pause"})
    mgr.command({"cmd": "set_pose", "x": 3.0, "y": 2.0, "heading_deg": 90})
    wait_for(lambda: readouts(pub)["pose"]["x"] == 3.0)
    assert readouts(pub)["pose"] == {"x": 3.0, "y": 2.0, "heading_deg": 90.0}
    mgr.command({"cmd": "resume"})
    time.sleep(0.2)
    y0 = readouts(pub)["pose"]["y"]
    mgr.command({"cmd": "push", "force_n": 300, "direction_deg": 90, "duration_s": 0.2})   # along the robot
    world = mgr._session.world
    wait_for(lambda: world.data.xfrc_applied[world.robot_body, 0] != 0, 5, "the push force")
    wait_for(lambda: world.data.xfrc_applied[world.robot_body, 0] == 0, 5, "the push force to clear")
    time.sleep(0.3)
    assert readouts(pub)["pose"]["y"] > y0 + 0.01
    n = len(notices(pub))
    mgr.command({"cmd": "move_body", "name": "no_such_body", "x": 1, "y": 1, "yaw_deg": 0})
    wait_for(lambda: len(notices(pub)) > n)
    note = notices(pub)[-1]
    assert note["kind"] == "error" and "no_such_body" in note["text"]
    mgr.command({"cmd": "reset"})
    wait_for(lambda: abs(readouts(pub)["pose"]["x"] - 2.0) < 0.01 and abs(readouts(pub)["pose"]["y"] - 4.035) < 0.01)
    assert mgr.status()["state"] == "running"


def test_reset_ends_a_push(running):
    """Reset puts the robot back at rest: a push still running stops there, not pushing it off again."""
    mgr, pub, _ = running
    mgr.command({"cmd": "push", "force_n": 400, "direction_deg": 90, "duration_s": 30})
    world = mgr._session.world
    wait_for(lambda: world.data.xfrc_applied[world.robot_body, 1] != 0, 5, "the push force")
    mgr.command({"cmd": "reset"})
    wait_for(lambda: world.data.xfrc_applied[world.robot_body, 1] == 0, 5, "the push force to clear")
    t = mgr.status()["t"]
    wait_for(lambda: mgr.status()["t"] > t + 0.3)
    pose = readouts(pub)["pose"]
    assert abs(pose["x"] - 2.0) < 0.01 and abs(pose["y"] - 4.035) < 0.01


def test_signals(running):
    mgr, pub, _ = running
    n = len(notices(pub))
    mgr.command({"cmd": "signals", "names": ["battery", "nope.thing"]})
    wait_for(lambda: len(notices(pub)) > n)
    assert "nope.thing" in notices(pub)[-1]["text"]
    s = fresh_state(pub, mgr, lambda s: "battery" in s["signals"])
    assert isinstance(s["signals"]["battery"], float) and set(s["signals"]) == {"battery"}


def test_inputs_are_rounded_and_the_watchdog_zeroes_a_quiet_port(running):
    mgr, pub, _ = running
    ds = mgr._session.bridge.ds
    with pytest.raises(LiveError):
        mgr.input({"6": {"axes": [0.0]}})
    with pytest.raises(LiveError):
        mgr.input({"0": {"axes": [2.0]}})
    with pytest.raises(LiveError):
        mgr.input({"0": {"povs": [360]}})
    with pytest.raises(LiveError):
        mgr.input({"0": {"buttons": [2]}})
    mgr.input({"0": {"axes": [0.123456, -0.5], "buttons": [True, False], "povs": [90]}})
    wait_for(lambda: 0 in ds.joysticks and ds.joysticks[0].axes == [0.1235, -0.5], 2, "the input")
    assert ds.joysticks[0].buttons == [True, False] and ds.joysticks[0].povs == [90]
    t0 = time.monotonic()
    wait_for(lambda: ds.joysticks[0].axes == [0.0, 0.0], 3, "the watchdog")
    assert time.monotonic() - t0 > 0.35
    assert ds.joysticks[0].buttons == [False, False] and ds.joysticks[0].povs == [-1]


def test_client_gone_disables_and_pauses(running):
    mgr, pub, _ = running
    ds = mgr._session.bridge.ds
    mgr.command({"cmd": "mode", "mode": "teleop"})
    mgr.command({"cmd": "enable"})
    wait_for(lambda: mgr.status()["enabled"] and ds.mode == "teleop")
    assert mgr.status()["mode"] == "teleop"
    mgr.input({"1": {"axes": [0.5, 0.5]}})
    wait_for(lambda: 1 in ds.joysticks and ds.joysticks[1].axes == [0.5, 0.5], 2)
    mgr.client_gone()
    wait_for(lambda: mgr.status()["paused"] and not mgr.status()["enabled"])
    assert ds.mode == "disabled" and ds.joysticks[1].axes == [0.0, 0.0] and mgr.status()["mode"] == "disabled"


def test_live_blocks_a_scenario_run(running, tmp_path):
    mgr, _, lock = running
    runs = RunManager(lambda: None, runs_dir=tmp_path / "runs", simlock=lock)
    path = SCEN / "fake_pass.toml"
    with pytest.raises(RunBusy, match="a drive session is live"):
        runs.start(path, path.read_text())
    assert not runs.running and lock.holder == "live"


def test_nothing_recorded_cannot_be_saved(running, tmp_path):
    mgr, _, _ = running
    assert mgr.status()["can_save"] is False
    with pytest.raises(LiveError, match="nothing has been recorded"):
        mgr.save_recording(tmp_path / "rec.toml", False)


# ---- separate sessions ----------------------------------------------------------------------------------------

def test_a_run_blocks_live(tmp_path):
    lock = SimLock()
    runs = RunManager(lambda: None, runs_dir=tmp_path / "runs", simlock=lock)
    path = SCEN / "fake_pass.toml"
    runs.start(path, path.read_text())
    mgr = LiveManager(lock, lambda *a: None)
    with pytest.raises(SimBusy, match="a scenario run is in progress"):
        mgr.start(ROBOT)
    assert mgr.status()["state"] == "idle"
    runs.stop()
    assert runs.join(30)
    wait_for(lambda: lock.holder is None, 5)


def test_simlock():
    lock = SimLock()
    lock.acquire("run")
    with pytest.raises(SimBusy, match="a scenario run is in progress"):
        lock.acquire("live")
    lock.release("live")  # not the holder: no-op
    assert lock.holder == "run"
    lock.release("run")
    lock.acquire("live")
    with pytest.raises(SimBusy, match="a drive session is live"):
        lock.acquire("run")
    lock.release("live")
    assert lock.holder is None


def test_run_and_live_race_for_the_lock(tmp_path):
    path = SCEN / "fake_pass.toml"
    text = path.read_text()
    for i in range(10):
        lock = SimLock()
        runs = RunManager(lambda: None, runs_dir=tmp_path / f"runs{i}", simlock=lock)
        mgr = LiveManager(lock, lambda *a: None)
        barrier = threading.Barrier(2)
        outcome = {}

        def go(name, fn):
            barrier.wait()
            try:
                fn()
                outcome[name] = "won"
            except (SimBusy, RunBusy) as e:
                outcome[name] = str(e)

        threads = [threading.Thread(target=go, args=("live", lambda: mgr.start(ROBOT))),
                   threading.Thread(target=go, args=("run", lambda: runs.start(path, text)))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert sorted(outcome.values()).count("won") == 1, outcome
        loser = "run" if outcome["live"] == "won" else "live"
        assert outcome[loser] == ("a drive session is live" if loser == "run" else "a scenario run is in progress")
        mgr.stop()
        runs.stop()
        assert mgr.join(30) and runs.join(30)
        wait_for(lambda: lock.holder is None, 5)


def test_a_robot_crash_is_an_error_with_the_traceback_and_keeps_the_recording(tmp_path):
    cfg = config_copy(tmp_path, ["--exit-after", "200"])
    lock = SimLock()
    pub = []
    mgr = LiveManager(lock, lambda kind, msg: pub.append((kind, msg)))
    mgr.start(cfg)
    wait_for(lambda: mgr.status()["state"] in ("running", "error"), 30)
    mgr.input({"0": {"axes": [0.0, 0.0]}})
    mgr.command({"cmd": "record_start"})
    assert mgr.join(30)
    st = mgr.status()
    assert st["state"] == "error" and "exited" in st["error"], st
    assert lock.holder is None
    last = states(pub)[-1]
    assert last["state"] == "error" and any("Traceback" in line for line in last["console"])
    assert st["can_save"] and not st["recording"]
    res = mgr.save_recording(tmp_path / "crash.toml", False)
    assert res["steps"] == 1 and (tmp_path / "crash.toml").is_file()


UNSAVED = "the last drive's recording hasn't been saved: save it, or start again to discard it"


def test_an_unsaved_recording_is_discarded_only_when_asked(tmp_path):
    cfg = config_copy(tmp_path, ["--exit-after", "200"])   # each session ends (error) after 1 s of sim time
    lock = SimLock()
    mgr = LiveManager(lock, lambda *a: None)

    def drive(record):
        wait_for(lambda: mgr.status()["state"] in ("running", "error"), 30)
        mgr.input({"0": {"axes": [0.0, 0.0]}})
        if record:
            mgr.command({"cmd": "record_start"})
        assert mgr.join(30)
        assert mgr.status()["can_save"] is record

    mgr.start(cfg)
    drive(record=True)
    with pytest.raises(LiveError) as e:
        mgr.start(cfg)
    assert str(e.value) == UNSAVED
    assert lock.holder is None and mgr.status()["state"] == "error" and mgr.status()["can_save"]   # kept
    lock.acquire("run")   # a busy simulation says so first: no point asking about the recording
    with pytest.raises(SimBusy, match="a scenario run is in progress"):
        mgr.start(cfg)
    lock.release("run")
    with pytest.raises(LiveError, match="hasn't been saved"):
        mgr.start(cfg)
    assert lock.holder is None   # the refusal let the lock go again

    mgr.save_recording(tmp_path / "one.toml", False)   # saved: Start goes ahead
    mgr.start(cfg)
    drive(record=True)
    with pytest.raises(LiveError, match="hasn't been saved"):
        mgr.start(cfg)
    mgr.start(cfg, discard_recording=True)              # asked: the recording goes
    drive(record=False)
    mgr.start(cfg)                                      # nothing recorded: nothing to lose
    drive(record=False)

    # saved while live, then recording again: the new recording isn't saved
    mgr.start(config_copy(tmp_path, name="long.toml"))   # no --exit-after
    wait_for(lambda: mgr.status()["state"] == "running", 30)
    mgr.command({"cmd": "record_start"})
    wait_for(lambda: mgr.status()["recording"], 10)
    time.sleep(0.2)
    mgr.save_recording(tmp_path / "two.toml", False)
    mgr.command({"cmd": "record_start"})
    wait_for(lambda: mgr.status()["recording"], 10)
    time.sleep(0.2)
    mgr.stop()
    assert mgr.join(30)
    with pytest.raises(LiveError, match="hasn't been saved"):
        mgr.start(cfg)


def _marked_pids(marker):
    out = []
    for name in os.listdir("/proc"):
        if name.isdigit():
            try:
                with open(f"/proc/{name}/cmdline", "rb") as f:
                    if marker.encode() in f.read():
                        with open(f"/proc/{name}/stat") as s:
                            if s.read().rsplit(")", 1)[1].split()[0] != "Z":
                                out.append(int(name))
            except (OSError, IndexError):
                pass
    return out


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
def test_stop_interrupts_the_robot_start_up(tmp_path):
    marker = f"scrimmage-slow-{uuid.uuid4().hex}"
    cfg = config_copy(tmp_path, command=["{python}", "-c", "import time; time.sleep(60)", marker], start_timeout=120)
    lock = SimLock()
    mgr = LiveManager(lock, lambda *a: None)
    mgr.start(cfg)
    wait_for(lambda: _marked_pids(marker), 30, "the robot program to start")
    assert mgr.status()["state"] == "starting"
    t0 = time.monotonic()
    mgr.stop()
    assert mgr.join(5), "Stop didn't end the start-up"
    assert time.monotonic() - t0 < 2.5
    assert mgr.status()["state"] == "stopped" and lock.holder is None
    assert _marked_pids(marker) == []


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
def test_kill_robots_kills_the_live_robot_program_by_token(tmp_path):
    marker = f"scrimmage-slow-{uuid.uuid4().hex}"
    cfg = config_copy(tmp_path, command=["{python}", "-c", "import time; time.sleep(60)", marker], start_timeout=120)
    mgr = LiveManager(SimLock(), lambda *a: None)
    mgr.start(cfg)
    wait_for(lambda: _marked_pids(marker), 30, "the robot program to start")
    mgr.kill_robots()
    assert _marked_pids(marker) == []
    mgr.stop()
    assert mgr.join(10)


# ---- recording round trip --------------------------------------------------------------------------------------

def _log(path):
    return {e["seq"]: e for e in map(json.loads, path.read_text().splitlines())}


def _view(e):
    return {"enabled": e["enabled"], "auto": e["auto"], "test": e["test"], "joysticks": e["joysticks"]}


def test_a_recorded_drive_replays_the_same_inputs(tmp_path, monkeypatch):
    log_a, log_b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    monkeypatch.setenv("SCRIMMAGE_FAKE_INPUT_LOG", str(log_a))
    pub = []
    mgr = LiveManager(SimLock(), lambda kind, msg: pub.append((kind, msg)))
    mgr.start(ROBOT)
    wait_for(lambda: mgr.status()["state"] in ("running", "error"), 30)
    assert mgr.status()["state"] == "running"

    def feed(port0, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            mgr.input({"0": port0})
            time.sleep(0.02)

    neutral = {"axes": [0.0, 0.0], "buttons": [False, False], "povs": [-1]}
    feed(neutral, 0.1)
    mgr.command({"cmd": "record_start"})
    wait_for(lambda: mgr.status()["recording"])
    feed({"axes": [0.0, -0.5], "buttons": [False, False], "povs": [-1]}, 0.2)
    feed({"axes": [0.25, -0.5], "buttons": [True, False], "povs": [-1]}, 0.2)
    feed({"axes": [0.25, -0.5], "buttons": [True, False], "povs": [90]}, 0.2)
    mgr.command({"cmd": "mode", "mode": "teleop"})
    mgr.command({"cmd": "enable"})
    feed({"axes": [0.0, -1.0], "buttons": [False, True], "povs": [90]}, 0.3)
    mgr.command({"cmd": "set_pose", "x": 2.5, "y": 4.0, "heading_deg": 0})   # a poke
    feed({"axes": [0.0, -0.3333333], "buttons": [False, True], "povs": [180]}, 0.2)
    mgr.command({"cmd": "record_stop"})
    wait_for(lambda: not mgr.status()["recording"])
    t0 = mgr._recorder._segments[0].start
    res = mgr.save_recording(tmp_path / "rec.toml", False)
    assert res["pokes"] == 1 and res["steps"] >= 2, res
    with pytest.raises(LiveError, match="already exists"):
        mgr.save_recording(tmp_path / "rec.toml", False)
    mgr.stop()
    assert mgr.join(10)

    monkeypatch.setenv("SCRIMMAGE_FAKE_INPUT_LOG", str(log_b))
    sc = load_scenario(tmp_path / "rec.toml")
    rep = asyncio.run(run_scenario(sc, tmp_path / "out"))
    assert rep.error is None, rep.to_dict()
    n = round(sum(s.duration for s in sc.steps) / STEP)
    a, b = _log(log_a), _log(log_b)
    first = round(t0 / STEP) + 1
    assert n > 100
    seen = set()
    for k in range(n):
        va, vb = _view(a[first + k]), _view(b[1 + k])
        assert va == vb, (k, va, vb)
        seen.add(json.dumps(va, sort_keys=True))
    assert len(seen) >= 5   # every change made it into the comparison


def test_shutdown_refuses_later_starts():
    lock = SimLock()
    mgr = LiveManager(lock, lambda *a: None)
    mgr.shutdown()
    with pytest.raises(LiveError, match="shutting down"):
        mgr.start(ROBOT)
    assert lock.holder is None and mgr.status()["state"] == "idle" and mgr.join(1)


def test_status_reports_the_chosen_mode_while_disabled(running):
    mgr, _, _ = running
    mgr.command({"cmd": "mode", "mode": "auto"})
    wait_for(lambda: mgr.status().get("chosen_mode") == "auto", 5, "the chosen mode")
    s = mgr.status()
    assert s["mode"] == "disabled" and not s["enabled"]
    mgr.command({"cmd": "mode", "mode": "teleop"})
    wait_for(lambda: mgr.status()["chosen_mode"] == "teleop", 5, "teleop again")


def test_the_fake_robot_drives_straight_on_the_shared_config(running):
    """tests/fake_robot.toml inverts right_drive (PWM 1); the fake robot's --drive negates PWM 1 to match, so pushing
    port 0's axis 1 forward drives the robot straight ahead."""
    mgr, pub, _ = running
    mgr.command({"cmd": "pause"})
    mgr.command({"cmd": "set_pose", "x": 3.0, "y": 4.0, "heading_deg": 0})
    wait_for(lambda: readouts(pub)["pose"]["x"] == 3.0, 5, "the pose")
    mgr.command({"cmd": "resume"})
    mgr.command({"cmd": "mode", "mode": "teleop"})
    mgr.command({"cmd": "enable"})
    wait_for(lambda: mgr.status()["enabled"], 5, "enabled")
    end = time.monotonic() + 1.0
    while time.monotonic() < end:
        mgr.input({"0": {"axes": [0.0, -1.0]}})
        time.sleep(0.02)
    mgr.command({"cmd": "disable"})
    mgr.input({"0": {"axes": [0.0, 0.0]}})
    wait_for(lambda: not mgr.status()["enabled"], 5, "disabled")
    p = readouts(pub)["pose"]
    assert p["x"] - 3.0 > 0.1 and abs(p["y"] - 4.0) < 0.05 and abs(p["heading_deg"]) < 5, p


def test_rev_problem_is_published_as_an_error_notice_once():
    pub = []
    mgr = LiveManager(SimLock(), lambda kind, msg: pub.append((kind, msg)))
    problem = None
    session = type("S", (), {"rev_problem": lambda self: problem})()
    mgr._check_rev(session)
    assert notices(pub) == []
    problem = "REV simulation unavailable (no class X): update REVLib to 2026.0.5 or later (vendordeps/REVLib.json)"
    mgr._check_rev(session)
    mgr._check_rev(session)
    assert notices(pub) == [{"kind": "error", "text": problem}]


def test_the_game_data_box_is_sent_on_a_teleop_enable(running):
    """M5: the box's value reaches the robot when it is enabled in Teleop, and at once when edited there; edited
    while enabled in Auto, it waits for Teleop."""
    mgr, _, _ = running
    ds = mgr._session.bridge.ds
    mgr.command({"cmd": "mode", "mode": "teleop"})
    mgr.command({"cmd": "match", "game_data": "R"})
    wait_for(lambda: mgr.status()["match"]["game_data"] == "R", 5, "the box")
    assert ds.game_data == ""   # disabled: the box waits
    mgr.command({"cmd": "enable"})
    wait_for(lambda: ds.game_data == "R", 5, "R on the enable")
    mgr.command({"cmd": "match", "game_data": "B"})
    wait_for(lambda: ds.game_data == "B", 5, "B at once")
    mgr.command({"cmd": "mode", "mode": "auto"})
    mgr.command({"cmd": "match", "game_data": "R"})
    wait_for(lambda: mgr.status()["match"]["game_data"] == "R" and mgr.status()["mode"] == "auto", 5, "auto")
    time.sleep(0.3)
    assert ds.game_data == "B"   # enabled in Auto: the change waits for Teleop
    mgr.command({"cmd": "mode", "mode": "teleop"})
    wait_for(lambda: ds.game_data == "R", 5, "R in Teleop")
    # clean up while enabled in Teleop, so the robot's game data clears too (a box cleared while disabled would leave
    # the robot with "R" for the later tests on this shared session)
    mgr.command({"cmd": "match", "game_data": ""})
    wait_for(lambda: mgr.status()["match"]["game_data"] == "" and ds.game_data == "", 5, "the game data cleared")
    mgr.command({"cmd": "disable"})
    wait_for(lambda: not mgr.status()["enabled"], 5, "disabled")


def test_the_match_command_sets_the_station_fms_and_timer(running):
    mgr, _, _ = running
    ds = mgr._session.bridge.ds
    assert mgr.status()["match"] == {"station": "blue1", "fms": False, "timer": False, "auto_seconds": 20.0,
                                     "teleop_seconds": 140.0, "game_data": ""}
    assert mgr.status()["stations"] == ["red1", "red2", "red3", "blue1", "blue2", "blue3"]
    for bad in ({"cmd": "match", "station": "green1"}, {"cmd": "match", "fms": "yes"},
                {"cmd": "match", "auto_seconds": 0}, {"cmd": "match", "game_data": "x" * 65},
                {"cmd": "match", "period": 3}):
        with pytest.raises(LiveError):
            mgr.command(bad)
    mgr.command({"cmd": "match", "station": "red2", "fms": True, "timer": True})
    wait_for(lambda: mgr.status()["match"]["station"] == "red2", 5, "the station")
    assert ds.match.station == "red2" and ds.match.fms and ds.match.timer
    assert mgr.status()["match"]["fms"] and mgr.status()["match"]["timer"]
    mgr.command({"cmd": "match", "station": "blue1", "fms": False, "timer": False})
    wait_for(lambda: ds.match.station == "blue1" and not ds.match.fms and not ds.match.timer, 5, "back")


def test_a_restart_keeps_the_match_settings_and_the_game_data(tmp_path, monkeypatch):
    """A Drive tab restart (the robot's IDs changed) is the same match, as restart_robot is: the station, FMS and
    timer settings, the game data box and the robot's game data all stay. A new Start goes back to the config's."""
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)
    mgr = LiveManager(SimLock(), lambda kind, msg: None)
    mgr.start(config_copy(tmp_path))
    try:
        wait_for(lambda: mgr.status()["state"] in ("running", "error"), 30, "the session to run")
        assert mgr.status()["state"] == "running", mgr.status()
        mgr.command({"cmd": "match", "station": "red2", "fms": True, "timer": True, "auto_seconds": 15,
                     "game_data": "R"})
        mgr.command({"cmd": "mode", "mode": "teleop"})
        mgr.command({"cmd": "enable"})
        wait_for(lambda: mgr._session is not None and mgr._session.bridge.ds.game_data == "R", 5, "R sent")
        kept = {"station": "red2", "fms": True, "timer": True, "auto_seconds": 15.0, "teleop_seconds": 140.0,
                "game_data": "R"}
        assert mgr.status()["match"] == kept
        old = mgr._session
        mgr.restart()
        wait_for(lambda: mgr.devices()["restart"] == "restarted" and mgr.status()["state"] == "running"
                 and mgr._session is not None and mgr._session is not old, 30, "the restart")
        assert mgr.status()["match"] == kept
        ds = mgr._session.bridge.ds
        assert (ds.match.station, ds.match.fms, ds.match.timer, ds.match.auto_seconds) == ("red2", True, True, 15.0)
        assert ds.game_data == "R" and not mgr.status()["enabled"]   # the robot has it from its first frame
        mgr.stop()
        assert mgr.join(10)
        mgr.start(config_copy(tmp_path))   # a Start of its own: the config's [match] and an empty box
        wait_for(lambda: mgr.status()["state"] == "running" and mgr._session is not None, 30, "a new start")
        assert mgr.status()["match"] == {"station": "blue1", "fms": False, "timer": False, "auto_seconds": 20.0,
                                         "teleop_seconds": 140.0, "game_data": ""}
        assert mgr._session.bridge.ds.game_data == ""
    finally:
        mgr.stop()
        assert mgr.join(10)


# ---- the FRC Driver Station app drives (sub-project 28) -------------------------------------------------------

def test_a_real_ds_session_refuses_the_ds_commands_and_shows_the_ds(tmp_path, monkeypatch):
    """With real_ds the page can't drive: mode, enable, disable, match, the joystick feed, pause, step and record are
    refused with the reason; the state says the real DS drives, waiting for it (the fake robot has no DS socket),
    and the speed is locked with the real DS's real-time reason. Leaving the page doesn't disable or pause it."""
    from scrimmage.driverstation import REAL_DS_DRIVING, REAL_DS_WAITING
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)
    pub = []
    mgr = LiveManager(SimLock(), lambda kind, msg: pub.append((kind, msg)))
    mgr.start(config_copy(tmp_path), real_ds=True)
    try:
        wait_for(lambda: mgr.status()["state"] in ("running", "error"), 30, "the session to run")
        st = mgr.status()
        assert st["state"] == "running" and st["real_ds"] is True, st
        assert st["driver_station"] == REAL_DS_WAITING and st["mode"] == "waiting" and st["enabled"] is False
        assert set(st["match"]) >= {"connected", "station", "fms", "match_time"}
        for c in ({"cmd": "mode", "mode": "auto"}, {"cmd": "enable"}, {"cmd": "disable"},
                  {"cmd": "match", "station": "red1"}, {"cmd": "pause"}, {"cmd": "resume"}, {"cmd": "step", "n": 1},
                  {"cmd": "record_start"}):
            with pytest.raises(LiveError, match=REAL_DS_DRIVING):
                mgr.command(c)
        with pytest.raises(LiveError, match=REAL_DS_DRIVING):
            mgr.input({"0": {"axes": [0.5]}})
        s = fresh_state(pub, mgr, lambda s: s["readouts"] is not None and s["speed_locked"])
        assert s["real_ds"] is True and s["readouts"]["real_ds"] is True and "real Driver Station" in s["speed_locked"]
        assert s["driver_station"] == REAL_DS_WAITING
        mgr.client_gone()
        t0 = mgr.status()["t"]
        wait_for(lambda: mgr.status()["t"] > t0 + 0.2, 5, "the clock to keep running")
        assert mgr.status()["paused"] is False and mgr.status()["state"] == "running"
        assert not [n for n in notices(pub) if n["kind"] == "error"], notices(pub)
    finally:
        mgr.stop()
        assert mgr.join(10)
    assert mgr.status()["real_ds"] is True   # the last session's, until the next Start


def test_the_real_ds_choice_starts_from_the_config(tmp_path, monkeypatch):
    """real_ds=None takes the config's [driver_station] real; True or False overrides it (D3)."""
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)
    cfg = config_copy(tmp_path)
    cfg.write_text(cfg.read_text() + "\n[driver_station]\nreal = true\n")
    mgr = LiveManager(SimLock(), lambda kind, msg: None)
    for choice, expected in ((None, True), (False, False)):
        mgr.start(cfg, real_ds=choice)
        try:
            wait_for(lambda: mgr.status()["state"] in ("running", "error"), 30, "the session to run")
            assert mgr.status()["state"] == "running" and mgr.status()["real_ds"] is expected, mgr.status()
        finally:
            mgr.stop()
            assert mgr.join(10)
