"""The Test tab's server side: a block test (`<name>.test.toml`) behind /api/test/*."""

import threading
import time

import pytest

from scrimmage.assembler import recent
from scrimmage.assembler.server import App, classify
from scrimmage.assembler.templates import new_config
from test_assembler_server import Client

ARM = """
[[part]]
name = "arm"
stock = "arm"
attach = "drive.top"
ids = { pivot = "PWM:2" }
"""
SPIN = """
[[block]]
do = "spin"
motor = "arm.pivot"
volts = 2.0

[[block]]
do = "wait"
seconds = 0.5
"""


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_SKIP_JAVA", "1")
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)


def write_robot(folder, arm=True):
    path = folder / "robot.toml"
    path.write_text(new_config("standin") + (ARM if arm else ""), encoding="utf-8")
    return path


def write_test(folder, blocks=SPIN, name="t.test.toml"):
    write_robot(folder)
    path = folder / name
    path.write_text(f'[test]\nrobot = "robot.toml"\n{blocks}', encoding="utf-8")
    return path


@pytest.fixture
def test_app(tmp_path):
    started = []

    def make(path):
        app = App(path, open_browser=False)
        started.append(app)
        return Client(app, app.start())
    yield make
    for a in started:
        a.stop()
    assert not [t for t in threading.enumerate() if t.name.startswith("test-")], "a test thread outlived stop()"


def _state(c, since=None):
    s, b = c.get("/api/test/state" + (f"?since={since}" if since is not None else ""))
    assert s == 200, b
    return b


def _wait_idle(c, timeout=60.0):
    end = time.monotonic() + timeout
    st = _state(c)
    while st["running"] is not None:
        assert time.monotonic() < end, st
        st = _state(c, st["version"])
    return st


def test_classify_test_and_scenario(tmp_path):
    t = write_test(tmp_path)
    assert classify(t) == "test"
    sc = tmp_path / "s.toml"
    sc.write_text('config = "robot.toml"\n[[step]]\nduration = 1.0\n', encoding="utf-8")
    assert classify(sc) == "scenario"
    # not valid TOML: judged by its [test] header line
    bad = tmp_path / "bad.test.toml"
    bad.write_text('[test]\nrobot = "robot.toml\n', encoding="utf-8")
    assert classify(bad) == "test"
    assert classify(tmp_path / "robot.toml") == "robot"


def test_open_test_state_and_recent(tmp_path, test_app):
    t = write_test(tmp_path)
    c = test_app(t)
    s, app = c.get("/api/app/state")
    assert s == 200 and app["mode"] == "robot" and app["test"] == str(t.resolve())
    st = _state(c)
    assert st["problems"] == [] and st["running"] is None and st["runs"] == [] and st["sweeps"] == []
    assert "arm.pivot" in [m["name"] for m in st["palette"]["motors"]]
    assert st["test"]["blocks"][0] == {"do": "spin", "motor": "arm.pivot", "volts": 2.0}
    # the robot document is the test's robot
    s, rs = c.get("/api/robot/state")
    assert s == 200 and rs["path"] == str((tmp_path / "robot.toml").resolve())
    # opening it remembers it as a test; opening a robot closes the test
    s, b = c.post("/api/app/open", {"path": str(t)})
    assert s == 200, b
    assert recent.recent()[0] == {**recent.recent()[0], "path": str(t.resolve()), "kind": "test"}
    s, b = c.post("/api/app/open", {"path": str(tmp_path / "robot.toml")})
    assert s == 200, b
    assert c.get("/api/app/state")[1]["test"] is None
    assert c.get("/api/test/state")[0] == 404


def test_save_bad_shape_and_stale(tmp_path, test_app):
    t = write_test(tmp_path)
    c = test_app(t)
    st = _state(c)
    test = st["test"]
    bad = {**test, "blocks": [test["blocks"][0], {"do": "wait", "seconds": -1}]}
    s, b = c.call("POST", "/api/test/save", {"test": bad, "version": st["version"]})
    assert s == 400 and "block 2" in b["error"] and "negative" in b["error"], b
    good = {**test, "blocks": [*test["blocks"], {"do": "wait", "seconds": 0.25}]}
    s, b = c.call("POST", "/api/test/save", {"test": good, "version": st["version"]})
    assert s == 200, b
    assert "seconds = 0.25" in t.read_text(encoding="utf-8")
    new = _state(c)
    assert new["version"] > st["version"] and len(new["test"]["blocks"]) == 3
    # the page's old version: refused, with the state as it is now
    s, b = c.call("POST", "/api/test/save", {"test": test, "version": st["version"]})
    assert s == 409 and b["state"]["test"]["blocks"] == new["test"]["blocks"], b
    assert len(_state(c)["test"]["blocks"]) == 3


def test_run_waits_for_the_drive_session(tmp_path, test_app):
    """Review Focus 3: Run while another simulation holds the lock is a plain busy message; once it's free the test
    runs and its run is kept."""
    t = write_test(tmp_path)
    c = test_app(t)
    c.app.simlock.acquire("live")
    try:
        s, b = c.call("POST", "/api/test/run", {})
        assert s == 409 and b["error"] == "Wait for the drive session to stop first.", b
        assert _state(c)["running"] is None
    finally:
        c.app.simlock.release("live")
    s, b = c.call("POST", "/api/test/run", {})
    assert s == 200 and b["ok"], b
    st = _wait_idle(c)
    assert st["run_error"] is None, st
    assert len(st["runs"]) == 1 and st["runs"][0]["status"] == "finished", st["runs"]
    assert c.app.simlock.holder is None
    run_id = st["runs"][0]["id"]
    s, body = c.get(f"/api/test/run/{run_id}/motion")
    assert s == 200 and body[:4] == b"SCRM"
    s, view = c.get(f"/api/test/run/{run_id}/view")
    assert s == 200 and view["geoms"], view.keys()
    s, res = c.get(f"/api/test/run/{run_id}")
    assert s == 200 and res["status"] == "finished" and res["signals"]["t"] and "view" not in res
    assert c.get("/api/test/run/nope/view")[0] == 404


def test_run_refused_with_problems_and_robot_change(tmp_path, test_app):
    """The robot losing its arm on disk (an Import again, an outside edit): the next state names the problem and Run
    is refused until it's fixed."""
    t = write_test(tmp_path)
    c = test_app(t)
    assert _state(c)["problems"] == []
    write_robot(tmp_path, arm=False)
    st = _state(c)
    assert st["problems"] and st["problems"][0]["place"] == [0], st["problems"]
    assert "arm.pivot" in st["problems"][0]["message"]
    s, b = c.call("POST", "/api/test/run", {})
    assert s == 409 and b["problems"] == st["problems"], b
    write_robot(tmp_path)
    assert _state(c)["problems"] == []


def test_sweep_and_cancel(tmp_path, test_app):
    t = write_test(tmp_path)
    c = test_app(t)
    s, b = c.call("POST", "/api/test/sweep", {"target": {"block": [0], "key": "volts"}, "values": [1.0, 2.0]})
    assert s == 200, b
    st = _wait_idle(c)
    assert st["run_error"] is None, st
    assert len(st["sweeps"]) == 1 and [r["value"] for r in st["sweeps"][0]["rows"]] == ["1", "2"]
    assert {r["sweep"]["value"] for r in st["runs"] if r["sweep"]} == {1.0, 2.0}
    s, b = c.call("POST", "/api/test/sweep", {"target": {"robot": "wings"}, "values": [1.0]})
    assert s == 400, b
    s, b = c.call("POST", "/api/test/cancel", {})
    assert s == 200 and b["ok"]


def test_new_test(tmp_path, test_app):
    robot = write_robot(tmp_path)
    c = test_app(robot)
    s, b = c.call("POST", "/api/test/new", {})
    assert s == 200, b
    made = tmp_path / "robot.test.toml"
    assert made.is_file() and classify(made) == "test"
    st = _state(c)
    assert st["test"]["blocks"] == [{"do": "wait", "seconds": 1.0}] and st["problems"] == []
    s, b = c.call("POST", "/api/test/new", {"robot": str(robot)})
    assert s == 200, b
    assert (tmp_path / "robot-2.test.toml").is_file()
    assert c.get("/api/app/state")[1]["test"] == str((tmp_path / "robot-2.test.toml").resolve())


def test_missing_robot_and_bad_outside_edit(tmp_path, test_app):
    """A test naming a robot file that isn't there opens with a plain problem; an outside edit that breaks the file
    keeps the last good test and says why until it's fixed."""
    t = write_test(tmp_path)
    t.write_text('[test]\nrobot = "gone.toml"\n' + SPIN, encoding="utf-8")
    c = test_app(t)
    st = _state(c)
    assert st["palette"] is None and "gone.toml" in st["problems"][0]["message"], st["problems"]
    assert "doesn't exist" in st["problems"][0]["message"]
    assert c.call("POST", "/api/test/run", {})[0] == 409
    t.write_text('[test]\nrobot = "robot.toml"\n' + SPIN, encoding="utf-8")
    assert _state(c)["problems"] == []
    t.write_text('[test]\nrobot = "robot.toml"\n[[block]]\ndo = "fly"\n', encoding="utf-8")
    st = _state(c)
    assert st["file_error"] and "block 1" in st["file_error"] and st["test"]["blocks"][0]["do"] == "spin"
    s, b = c.call("POST", "/api/test/run", {})
    assert s == 409 and "fix the test file first" in b["error"], b
