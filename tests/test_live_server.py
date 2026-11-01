"""The editor server's live side: the token-guarded WebSocket hub, the /api/live/* routes, scenario runs streaming
frames, saving a recording and opening it as the scenario document."""

import json
import os
import socket
import struct
import time
import types
import uuid
from pathlib import Path

import numpy as np
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

import scrimmage.assembler.stream as stream_mod
import scrimmage.session as session_mod
from scrimmage.assembler.server import App
from test_assembler_server import Client
from test_live import UNSAVED, _marked_pids, config_copy, wait_for

TESTS = Path(__file__).resolve().parent
HEAD = struct.Struct("<IBdH")


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)  # the fake robot serves no NetworkTables


@pytest.fixture
def work(tmp_path, monkeypatch):
    """tmp/work is the server's working directory; tmp/robot holds the robot config."""
    (tmp_path / "work").mkdir()
    (tmp_path / "robot").mkdir()
    monkeypatch.chdir(tmp_path / "work")
    return tmp_path


@pytest.fixture
def served(work):
    started = []

    def make(path):
        app = App(path, open_browser=False, runs_dir=work / "runs")
        started.append(app)
        return Client(app, app.start())
    yield make
    for a in started:
        a.stop()


@pytest.fixture
def robot(work):
    return config_copy(work / "robot")


def ws_url(c):
    _, info = c.get("/api/live/info")
    return f"ws://127.0.0.1:{info['port']}", info["port"]


def ws_open(c, token="auto", origin="auto", host=None):
    url, port = ws_url(c)
    http_port = c.app.port
    if origin == "auto":
        origin = f"http://127.0.0.1:{http_port}"
    kw = {"proxy": None, "open_timeout": 5, "legacy": True}
    if host is not None:
        kw["sock"] = socket.create_connection(("127.0.0.1", port))
        url = f"ws://{host}:{port}"
    ws = connect(url, origin=origin, **kw)
    if token is not None:
        try:
            ws.send(json.dumps({"token": c.token if token == "auto" else token}))
        except ConnectionClosed:
            pass   # a refused connection may already be closed
    return ws


def collect(ws, pred, timeout=10.0, what="a message"):
    """Messages until one satisfies pred: (all received, the match)."""
    got = []
    end = time.monotonic() + timeout
    while True:
        left = end - time.monotonic()
        assert left > 0, f"timed out waiting for {what}"
        try:
            m = ws.recv(timeout=left)
        except TimeoutError:
            continue
        m = m if isinstance(m, bytes) else json.loads(m)
        got.append(m)
        if pred(m):
            return got, m


def decode(frame):
    seq, source, t, n = HEAD.unpack_from(frame)
    return seq, source, t, np.frombuffer(frame, dtype="<f4", offset=HEAD.size).reshape(n, 7)


def status(c):
    code, info = c.get("/api/live/info")
    assert code == 200
    return info["status"]


def start_live(c, body=None):
    code, r = c.call("POST", "/api/live/start", body or {})
    assert code == 200 and r == {"ok": True}, r
    wait_for(lambda: status(c)["state"] in ("running", "error"), 30, "the session to run")
    assert status(c)["state"] == "running", status(c)


def assert_rejected(ws):
    with pytest.raises(ConnectionClosed) as e:
        ws.recv(timeout=5)   # nothing arrives before the close
    assert e.value.rcvd is not None and e.value.rcvd.code == 1008
    ws.close()


def test_live_info_needs_the_token(served, robot):
    c = served(robot)
    assert c.get("/api/live/info", token=None)[0] == 403
    assert c.get("/api/live/info", token="nope")[0] == 403
    code, info = c.get("/api/live/info")
    assert code == 200 and isinstance(info["port"], int) and info["port"] > 0
    assert info["status"]["state"] == "idle" and info["run_active"] is False
    code, view = c.get("/api/live/view")
    assert code == 200 and len(view["movers"]) > 0 and "movable" in view
    code, sig = c.get("/api/live/signals")
    assert code == 200 and "motor.left_drive" in sig["motors"]


def test_socket_auth_and_driving(served, robot):
    c = served(robot)
    start_live(c)
    bad = [ws_open(c, token="wrong"), ws_open(c, origin="http://evil.example"),
           ws_open(c, origin=f"http://127.0.0.1:{c.app.port + 1}"), ws_open(c, host="evil.example")]
    for ws in bad:
        try:
            ws.send(json.dumps({"type": "cmd", "cmd": "pause"}))
        except ConnectionClosed:
            pass
        assert_rejected(ws)
    time.sleep(0.3)
    assert status(c)["paused"] is False   # none of the pauses got through

    _, view = c.get("/api/live/view")
    ws = ws_open(c)
    frames, states = [], []
    end = time.monotonic() + 10
    while (len(frames) < 5 or not states) and time.monotonic() < end:
        m = ws.recv(timeout=5)
        (frames if isinstance(m, bytes) else states).append(m if isinstance(m, bytes) else json.loads(m))
    assert len(frames) >= 5 and states
    fs = [decode(f) for f in frames]
    assert all(f[1] == 0 and f[3].shape == (len(view["movers"]), 7) for f in fs)
    ts = [f[2] for f in fs]
    assert ts == sorted(ts) and ts[-1] > ts[0]
    assert states[0]["type"] == "state" and states[0]["source"] == "live"

    # a pause holds sim time
    ws.send(json.dumps({"type": "cmd", "cmd": "pause"}))
    wait_for(lambda: status(c)["paused"], 5, "the pause")
    t0 = status(c)["t"]
    time.sleep(0.3)
    assert status(c)["t"] == t0
    # a bad command comes back as a notice; malformed messages are ignored
    ws.send("not json")
    ws.send(json.dumps([1, 2]))
    ws.send(json.dumps({"type": "cmd", "cmd": "fly"}))
    _, notice = collect(ws, lambda m: isinstance(m, dict) and m.get("type") == "notice", 5, "a notice")
    assert "fly" in notice["text"]
    # inputs reach the session
    ws.send(json.dumps({"type": "input", "ports": {"0": {"axes": [0.123456]}}}))
    for cmd in ({"cmd": "mode", "mode": "teleop"}, {"cmd": "enable"}, {"cmd": "resume"}):
        ws.send(json.dumps({"type": "cmd", **cmd}))
    wait_for(lambda: status(c)["mode"] == "teleop" and not status(c)["paused"], 5, "teleop, running")

    # one simulation at a time
    code, r = c.call("POST", "/api/live/start", {})
    assert code == 409 and "already live" in r["error"]

    # the last page going away disables and pauses the robot within a second
    ws.close()
    t0 = time.monotonic()
    wait_for(lambda: status(c)["mode"] == "disabled" and status(c)["paused"], 1.0, "the robot disabled")
    assert time.monotonic() - t0 < 1.0

    code, r = c.call("POST", "/api/live/stop", {})
    assert code == 200 and r == {"ok": True}
    wait_for(lambda: status(c)["state"] == "stopped", 10, "the session to stop")


def test_start_refusals(served, robot, work):
    c = served(robot)
    for speed in (0, -1, "fast", True):
        code, r = c.call("POST", "/api/live/start", {"speed": speed})
        assert code == 200 and r["ok"] is False and r["errors"][0]["field"] == "status", (speed, r)
    assert status(c)["state"] == "idle"

    text = robot.read_text()
    head, _, tail = text.partition("[robot_program]")
    no_program = work / "robot" / "noprog.toml"
    no_program.write_text(head + "[[motor]]" + tail.partition("[[motor]]")[2])
    c2 = served(no_program)
    code, r = c2.call("POST", "/api/live/start", {})
    assert code == 200 and r["errors"] == [{"field": "status", "message": "this robot config has no [robot_program] "
                                            "section: add one to drive it"}]
    broken = work / "robot" / "broken.toml"
    broken.write_text("[[part\n")
    c3 = served(broken)
    code, r = c3.call("POST", "/api/live/start", {})
    assert code == 200 and r["ok"] is False
    assert c3.get("/api/live/view") == (200, {}) and c3.get("/api/live/signals") == (200, {})


def test_live_info_says_whether_start_can_work(served, robot, work):
    _, info = served(robot).get("/api/live/info")
    assert info["can_start"] is True and info["start_problem"] is None

    text = robot.read_text()
    head, _, tail = text.partition("[robot_program]")
    no_program = work / "robot" / "noprog.toml"
    no_program.write_text(head + "[[motor]]" + tail.partition("[[motor]]")[2])
    c = served(no_program)
    _, info = c.get("/api/live/info")
    assert info["can_start"] is False and info["start_problem"] == ("this robot config has no [robot_program] "
                                                                     "section: add one to drive it")
    # the same words as Start's refusal
    _, r = c.call("POST", "/api/live/start", {})
    assert r["errors"][0]["message"] == info["start_problem"]
    # fixed on disk: the next info says it can start
    no_program.write_text(text)
    _, info = c.get("/api/live/info")
    assert info["can_start"] is True and info["start_problem"] is None

    broken = work / "robot" / "broken.toml"
    broken.write_text("[[part\n")
    _, info = served(broken).get("/api/live/info")
    assert info["can_start"] is False and info["start_problem"]


def fake_scenario(work):
    text = (TESTS / "scenarios" / "fake_pass.toml").read_text().replace(
        '"../fake_robot.toml"', json.dumps(str(TESTS / "fake_robot.toml")))
    p = work / "work" / "fake_pass.toml"
    p.write_text(text)
    return p


def test_a_run_blocks_live_and_streams_run_frames(served, work):
    c = served(fake_scenario(work))
    ws = ws_open(c)
    time.sleep(0.2)   # authed before the run starts
    code, r = c.post("/api/scenario/run", {"speed": 1})   # real time: frames are throttled by wall time
    assert code == 200 and r["ok"], r
    code, r = c.call("POST", "/api/live/start", {})
    assert code == 409 and r["error"] == "a scenario run is in progress"
    code, info = c.get("/api/live/info")
    assert info["run_active"] is True
    code, r = c.call("POST", "/api/scenario/open", {"path": "fake_pass.toml"})
    assert code == 409
    _, view = c.get("/api/live/view")
    frames, states = [], []
    end = time.monotonic() + 30
    while (len(frames) < 5 or not any(s.get("step_name") == "auto" for s in states)) and time.monotonic() < end:
        m = ws.recv(timeout=10)
        (frames if isinstance(m, bytes) else states).append(m if isinstance(m, bytes) else json.loads(m))
    fs = [decode(f) for f in frames]
    assert len(fs) >= 5 and all(f[1] == 1 and f[3].shape == (len(view["movers"]), 7) for f in fs)
    ts = [f[2] for f in fs]
    assert ts == sorted(ts)
    s = [s for s in states if s["type"] == "state"][-1]
    assert s["source"] == "run" and s["step_name"] == "auto" and s["mode"] == "auto" and s["t"] > 0
    ws.close()
    c.post("/api/scenario/stop", {})
    assert c.app.runs.join(30)


def test_save_recording_and_open_it(served, robot, work):
    c = served(robot)
    assert c.get("/api/scenario/state")[0] == 404
    start_live(c)
    ws = ws_open(c)
    for cmd in ({"cmd": "record_start"}, {"cmd": "mode", "mode": "teleop"}, {"cmd": "enable"}):
        ws.send(json.dumps({"type": "cmd", **cmd}))
    wait_for(lambda: status(c)["recording"] and status(c)["t"] > 0.5, 10, "some recording")
    ws.send(json.dumps({"type": "cmd", "cmd": "record_stop"}))
    wait_for(lambda: not status(c)["recording"], 5, "the recording to stop")

    other = work / "elsewhere"
    other.mkdir()
    for p in (str(other / "rec.toml"), "../elsewhere/rec.toml"):
        code, r = c.call("POST", "/api/live/save_recording", {"path": p})
        assert code == 200 and r["ok"] is False and r["errors"][0]["field"] == "status", r
    assert not (other / "rec.toml").exists()
    assert c.call("POST", "/api/live/save_recording", {"path": 3})[0] == 400

    code, r = c.call("POST", "/api/live/save_recording", {"path": "drives/rec.toml"})
    assert code == 200 and r["ok"] is True, r
    assert r["path"] == str(work / "work" / "drives" / "rec.toml") and r["display_path"] == "drives/rec.toml"
    assert r["steps"] >= 1 and r["pokes"] == 0
    assert (work / "work" / "drives" / "rec.toml").is_file() and (work / "work" / "drives" / "rec").is_dir()
    code, r = c.call("POST", "/api/live/save_recording", {"path": "drives/rec.toml"})
    assert code == 200 and r["ok"] is False and r["errors"][0]["field"] == "save/exists", r
    code, r = c.call("POST", "/api/live/save_recording", {"path": "drives/rec.toml", "overwrite": True})
    assert code == 200 and r["ok"] is True, r
    # an overwrite never replaces a robot config or a folder the recorder didn't make
    robot_text = robot.read_text()
    code, r = c.call("POST", "/api/live/save_recording", {"path": str(robot), "overwrite": True})
    assert code == 200 and r["ok"] is False and r["errors"][0]["field"] == "status", r
    assert "not a recording" in r["errors"][0]["message"] and robot.read_text() == robot_text
    (work / "work" / "scenarios").mkdir()
    (work / "work" / "scenarios" / "mine.toml").write_text("x = 1\n")
    code, r = c.call("POST", "/api/live/save_recording", {"path": "scenarios.toml", "overwrite": True})
    assert code == 200 and r["ok"] is False and r["errors"][0]["field"] == "status", r
    assert "scenarios" in r["errors"][0]["message"]
    assert (work / "work" / "scenarios" / "mine.toml").read_text() == "x = 1\n"
    # the robot config's folder is allowed too
    code, r = c.call("POST", "/api/live/save_recording", {"path": str(work / "robot" / "rec2.toml")})
    assert code == 200 and r["ok"] is True, r
    ws.close()
    c.call("POST", "/api/live/stop", {})
    wait_for(lambda: status(c)["state"] == "stopped", 10, "the session to stop")

    v = c.app.robot_version
    assert c.call("POST", "/api/scenario/open", {"path": str(robot)})[0] == 400
    assert c.call("POST", "/api/scenario/open", {"path": "nope.toml"})[0] == 400
    code, r = c.call("POST", "/api/scenario/open", {"path": "drives/rec.toml"})
    assert code == 200 and r == {"ok": True}
    code, st = c.get("/api/scenario/state")
    assert code == 200 and st["display_path"] == "drives/rec.toml" and st["errors"] == [], st
    assert st["steps"] and st["read_only"] is False
    code, rs = c.get("/api/robot/state")
    assert rs["scenario_open"] is True and rs["path"] == str(robot) and rs["version"] > v


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
def test_stopping_the_app_during_start_up_leaves_no_robot(work):
    marker = f"scrimmage-slow-{uuid.uuid4().hex}"
    cfg = config_copy(work / "robot", command=["{python}", "-c", "import time; time.sleep(60)", marker],
                      start_timeout=120)
    app = App(cfg, open_browser=False, runs_dir=work / "runs")
    c = Client(app, app.start())
    code, r = c.call("POST", "/api/live/start", {})
    assert code == 200 and r["ok"], r
    wait_for(lambda: _marked_pids(marker), 30, "the robot program to start")
    app.stop()
    assert _marked_pids(marker) == []
    assert app.live.status()["state"] == "stopped"


def welded_robot(folder: Path) -> Path:
    """The fake robot with its chassis welded to the world (kitbot.xml without the <freejoint>)."""
    xml = (TESTS / "models" / "kitbot.xml").read_text()
    assert '<freejoint name="chassis_free"/>' in xml
    (folder / "welded.xml").write_text(xml.replace('<freejoint name="chassis_free"/>', ""))
    cfg = config_copy(folder)
    cfg.write_text(cfg.read_text().replace(f'mjcf = "{TESTS / "models" / "kitbot.xml"}"', 'mjcf = "welded.xml"'))
    return cfg


def test_view_of_a_robot_welded_to_the_world(served, work):
    import tomllib
    from scrimmage.assembler.view import model_view
    from scrimmage.world import World
    cfg = welded_robot(work / "robot")
    w = World(tomllib.loads(cfg.read_text()), cfg.parent)
    v = model_view(w, include_world=True)
    assert v["root_mover"] == -1
    m, chassis = w.model, w.model.body("chassis").id
    robot_geoms = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == chassis]
    assert len(robot_geoms) == len(v["geoms"])
    fixed = [gd for g, gd in zip(robot_geoms, v["geoms"]) if m.geom_bodyid[g] == chassis]
    assert fixed and all(gd["mover"] == -1 and gd["lpos"] == [0.0, 0.0, 0.0] and gd["lquat"] == [1.0, 0.0, 0.0, 0.0]
                         for gd in fixed)
    c = served(cfg)
    code, rv = c.get("/api/live/view")
    assert code == 200 and rv["root_mover"] == -1 and rv["geoms"], rv


def test_opening_a_scenario_is_refused_while_a_drive_session_is_live(served, robot, work):
    c = served(robot)
    scen = fake_scenario(work)
    start_live(c)
    code, r = c.call("POST", "/api/scenario/open", {"path": scen.name})
    assert code == 409 and r["error"] == "a drive session is live", r
    ws = ws_open(c)
    ws.send(json.dumps({"type": "cmd", "cmd": "pause"}))
    wait_for(lambda: status(c)["state"] == "paused", 5, "the pause")
    code, r = c.call("POST", "/api/scenario/open", {"path": scen.name})
    assert code == 409 and r["error"] == "a drive session is live", r
    ws.close()
    c.call("POST", "/api/live/stop", {})
    wait_for(lambda: status(c)["state"] == "stopped", 10, "the session to stop")
    code, r = c.call("POST", "/api/scenario/open", {"path": scen.name})
    assert code == 200 and r == {"ok": True}, r


def test_opening_a_scenario_is_refused_while_a_drive_session_starts(served, work):
    slow = config_copy(work / "robot", command=["{python}", "-c", "import time; time.sleep(60)"], start_timeout=120)
    c = served(slow)
    code, r = c.call("POST", "/api/live/start", {})
    assert code == 200 and r["ok"], r
    assert status(c)["state"] == "starting"
    code, r = c.call("POST", "/api/scenario/open", {"path": fake_scenario(work).name})
    assert code == 409 and r["error"] == "a drive session is live", r
    c.call("POST", "/api/live/stop", {})
    wait_for(lambda: status(c)["state"] == "stopped", 10, "the session to stop")


DEEP = "[[[[ nested too deeply ]]]]"   # the stand-in: the hub's JSON parser raises RecursionError on it


@pytest.fixture
def deep_json(monkeypatch):
    """The stream hub's json.loads raises RecursionError on DEEP, as json does on input nested deeper than the
    interpreter can recurse (how deep that is depends on the Python version and the thread's stack)."""
    real = json.loads

    def loads(s, *a, **kw):
        if s == DEEP:
            raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")
        return real(s, *a, **kw)
    monkeypatch.setattr(stream_mod, "json", types.SimpleNamespace(loads=loads, dumps=json.dumps))


def test_deeply_nested_json_before_auth_is_refused(served, robot, deep_json):
    c = served(robot)
    ws = ws_open(c, token=None)
    ws.send(DEEP)
    assert_rejected(ws)


def test_deeply_nested_json_after_auth_is_ignored(served, robot, monkeypatch, deep_json):
    c = served(robot)
    gone = []
    monkeypatch.setattr(c.app.live, "client_gone", lambda: gone.append(1))
    ws = ws_open(c)
    time.sleep(0.2)   # authed
    ws.send(DEEP)
    ws.send(json.dumps({"type": "cmd", "cmd": "fly"}))   # the connection is still open: this comes back as a notice
    _, notice = collect(ws, lambda m: isinstance(m, dict) and m.get("type") == "notice", 5, "a notice")
    assert "fly" in notice["text"] and gone == []
    ws.close()


def test_start_asks_before_discarding_an_unsaved_recording(served, robot):
    c = served(robot)
    start_live(c)
    ws = ws_open(c)
    ws.send(json.dumps({"type": "cmd", "cmd": "record_start"}))
    wait_for(lambda: status(c)["recording"] and status(c)["t"] > 0.2, 10, "some recording")
    ws.close()
    c.call("POST", "/api/live/stop", {})
    wait_for(lambda: status(c)["state"] == "stopped", 10, "the session to stop")
    assert status(c)["can_save"]

    code, r = c.call("POST", "/api/live/start", {})
    assert code == 200 and r == {"ok": False, "errors": [{"field": "live/unsaved", "message": UNSAVED}]}, r
    assert status(c)["state"] == "stopped" and status(c)["can_save"]   # the recording is still there
    assert c.call("POST", "/api/live/start", {"discard_recording": "yes"})[0] == 400
    start_live(c, {"discard_recording": True})
    assert not status(c)["can_save"]
    c.call("POST", "/api/live/stop", {})
    wait_for(lambda: status(c)["state"] == "stopped", 10, "the session to stop")


def test_a_recording_saved_while_live_opens_when_it_drives_the_same_robot(served, robot, work):
    c = served(robot)
    start_live(c)
    ws = ws_open(c)
    for cmd in ({"cmd": "record_start"}, {"cmd": "mode", "mode": "teleop"}, {"cmd": "enable"}):
        ws.send(json.dumps({"type": "cmd", **cmd}))
    wait_for(lambda: status(c)["recording"] and status(c)["t"] > 0.3, 10, "some recording")
    code, r = c.call("POST", "/api/live/save_recording", {"path": "drives/rec.toml"})
    assert code == 200 and r["ok"], r
    code, r = c.call("POST", "/api/scenario/open", {"path": "drives/rec.toml"})   # the page's openSaved
    assert code == 200 and r == {"ok": True}, r
    code, rs = c.get("/api/robot/state")
    assert rs["scenario_open"] is True and rs["path"] == str(robot)
    assert status(c)["state"] in ("running", "paused")

    # the same robot reached through symlinks: the scenario's own path, and its config
    rec = work / "work" / "drives" / "rec.toml"
    (work / "work" / "link.toml").symlink_to(rec)
    code, r = c.call("POST", "/api/scenario/open", {"path": "link.toml"})
    assert code == 200 and r == {"ok": True}, r
    (work / "robot" / "alias.toml").symlink_to(robot)
    via = work / "work" / "via_alias.toml"
    via.write_text(f'config = {json.dumps(str(work / "robot" / "alias.toml"))}\n'
                   + rec.read_text().split("\n", 1)[1].replace("config =", "# config =", 1))
    assert classify_config(via) == str(work / "robot" / "alias.toml")
    code, r = c.call("POST", "/api/scenario/open", {"path": "via_alias.toml"})
    assert code == 200 and r == {"ok": True}, r
    assert c.get("/api/robot/state")[1]["path"] == str(robot)

    # a scenario whose robot can't be told while live: refused
    bad = {"toml_error.toml": "config = \"x.toml\"\n[[step]\nname = 1\n",
           "no_config.toml": '[[step]]\nname = "a"\nmode = "teleop"\nduration = 1.0\n',
           "missing.toml": 'config = "nope/robot.toml"\n[[step]]\nname = "a"\nmode = "teleop"\nduration = 1.0\n'}
    for name, text in bad.items():
        (work / "work" / name).write_text(text)
        code, r = c.call("POST", "/api/scenario/open", {"path": name})
        assert code == 409 and r["error"] == "a drive session is live", (name, r)
    assert c.get("/api/robot/state")[1]["path"] == str(robot)
    ws.close()
    c.call("POST", "/api/live/stop", {})
    wait_for(lambda: status(c)["state"] == "stopped", 10, "the session to stop")


def classify_config(path):
    import tomllib
    return tomllib.loads(Path(path).read_text())["config"]


def test_a_scenario_edit_cant_switch_the_robot_of_a_live_session(served, robot, work):
    c = served(fake_scenario(work))   # its config: tests/fake_robot.toml
    code, r = c.post("/api/scenario/edit", {"op": "top", "key": "config", "value": str(robot)})
    assert code == 200 and r["ok"], r   # not live: the robot document follows
    assert c.get("/api/robot/state")[1]["path"] == str(robot)
    start_live(c)
    code, r = c.post("/api/scenario/edit", {"op": "top", "key": "config", "value": str(TESTS / "fake_robot.toml")})
    assert code == 409 and r["error"] == "a drive session is live", r
    code, r = c.post("/api/scenario/undo", {})   # back to tests/fake_robot.toml: refused too
    assert code == 409 and r["error"] == "a drive session is live", r
    assert c.get("/api/robot/state")[1]["path"] == str(robot)
    code, r = c.post("/api/scenario/edit", {"op": "top", "key": "description", "value": "still driving"})
    assert code == 200 and r["ok"], r   # an edit that keeps the robot is fine
    c.call("POST", "/api/live/stop", {})
    wait_for(lambda: status(c)["state"] == "stopped", 10, "the session to stop")
    code, r = c.post("/api/scenario/edit", {"op": "top", "key": "config", "value": str(TESTS / "fake_robot.toml")})
    assert code == 200 and r["ok"], r


def range_scenario(work):
    from range_fixtures import shooter_cfg
    from test_range_checks import write_config
    # a page run needs a picked start (here the range's own named one)
    cfg = write_config(work / "work", shooter_cfg(range={"preload": 2, "start": "blue_center"}))
    scn = work / "work" / "range_run.toml"
    scn.write_text(f'config = "{cfg.name}"\n\n[[step]]\nname = "wait"\nmode = "disabled"\nduration = 1.0\n')
    return scn, cfg


def test_range_world_view_has_hub_and_balls(served, work):
    from range_fixtures import shooter_cfg
    from test_range_checks import write_config
    c = served(write_config(work / "robot", shooter_cfg(range={"preload": 2, "floor_balls": True})))
    code, v = c.get("/api/live/view")
    assert code == 200
    assert any(g["name"].startswith("hub_blue") for g in v["world_geoms"])
    balls = [i for i, m in enumerate(v["movers"]) if m["kind"] == "body" and m["name"].startswith("ball.")]
    assert len(balls) > 100
    assert all(any(g["mover"] == i and g["type"] == "sphere" for g in v["world_geoms"]) for i in balls)


def test_watch_state_carries_range(served, work):
    scn, _ = range_scenario(work)
    c = served(scn)
    ws = ws_open(c)
    time.sleep(0.2)
    code, r = c.post("/api/scenario/run", {"speed": 1})
    assert code == 200 and r["ok"], r
    _, s = collect(ws, lambda m: not isinstance(m, bytes) and m.get("source") == "run" and m["type"] == "state",
                   30, "a run state message")
    assert s["range"]["held"] == 2 and s["range"]["goals"]["hub_blue"] == 0 and s["range"]["fired"] == 0, s
    ws.close()
    c.post("/api/scenario/stop", {})
    assert c.app.runs.join(30)
