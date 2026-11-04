"""The editor server with a scenario open: classification, the scenario routes, the robot routes alongside, runs,
read-only cases and `scrimmage edit --new-scenario`."""
import shutil
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest

import scrimmage.session as session_mod
from scrimmage.assembler import scenario_validate, templates
from scrimmage.assembler.server import App, classify
from test_assembler_server import Client

ROOT = Path(__file__).resolve().parent.parent
FAKE_ROBOT = ROOT / "tests" / "fake_robot.toml"


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)


@pytest.fixture
def repo_copy(tmp_path):
    """tmp/configs and tmp/scenarios: copies of the shipped ones (scenarios name ../configs/<robot>.toml)."""
    shutil.copytree(ROOT / "configs", tmp_path / "configs")
    shutil.copytree(ROOT / "scenarios", tmp_path / "scenarios")
    return tmp_path


@pytest.fixture
def served(tmp_path):
    started = []

    def make(path):
        app = App(path, open_browser=False, runs_dir=tmp_path / "runs")
        started.append(app)
        return Client(app, app.start())
    yield make
    for a in started:
        a.stop()


def fake_scenario(tmp_path, name="fake_pass.toml") -> Path:
    """A copy of tests/scenarios/<name> in tmp_path whose config is the fake robot (absolute path)."""
    text = (ROOT / "tests" / "scenarios" / name).read_text().replace('"../fake_robot.toml"', f'"{FAKE_ROBOT}"')
    p = tmp_path / name
    p.write_text(text)
    return p


# ---- classification ---------------------------------------------------------------------------------------

def test_classify(tmp_path, repo_copy):
    for s in (repo_copy / "scenarios").glob("*.toml"):
        assert classify(s) == "scenario", s
    for c in (repo_copy / "configs").glob("*.toml"):
        assert classify(c) == "robot", c
    assert classify(FAKE_ROBOT) == "robot"
    cases = {
        'config = "r.toml"\n': "scenario",
        '[[step]]\nmode = "auto"\nduration = 1\n': "scenario",
        '[[step]]\nmode = "auto"\nduration = [\n': "scenario",            # broken TOML, but a [[step]] header
        'config = "r.toml"\n[[step\n': "scenario",                         # broken: config before any table
        '[robot_program]\nconfig = "x"\n[[part\n': "robot",               # broken; config only inside a table
        '[robot_program]\nconfig = "x"\n': "robot",
        "[[part\n": "robot",
        "": "robot",
    }
    for i, (text, kind) in enumerate(cases.items()):
        f = tmp_path / f"c{i}.toml"
        f.write_text(text)
        assert classify(f) == kind, text


def test_robot_config_opens_robot_only(served, repo_copy):
    c = served(repo_copy / "configs" / "kitbot.toml")
    assert c.app.scenario is None and c.get("/api/robot/state")[1]["parts"]
    for path in ("/api/scenario/state", "/api/scenario/signals", "/api/scenario/view", "/api/scenario/runs",
                 "/api/scenario/run/x/report.md"):
        assert c.get(path)[0] == 404, path


# ---- scenario state -----------------------------------------------------------------------------------------

def test_token_required_on_scenario_routes(served, repo_copy):
    c = served(repo_copy / "scenarios" / "kitbot_auto.toml")
    for path in ("/api/scenario/state", "/api/scenario/signals", "/api/scenario/view", "/api/scenario/runs",
                 "/api/scenario/run/x/report.md"):
        assert c.get(path, token=None)[0] == 403, path
    for path in ("/api/scenario/edit", "/api/scenario/undo", "/api/scenario/run", "/api/scenario/stop"):
        assert c.post(path, {"op": "delete_step", "step": 0}, token="nope")[0] == 403, path


@pytest.mark.parametrize("name", ["kitbot_auto.toml", "swerve_auto.toml", "elevator_arm.toml"])
def test_scenario_state_shape_on_shipped_scenarios(served, repo_copy, name):
    path = repo_copy / "scenarios" / name
    raw = tomllib.loads(path.read_text())
    c = served(path)
    st, s = c.get("/api/scenario/state")
    assert st == 200
    assert set(s) == {"version", "path", "display_path", "read_only", "read_only_reason", "robot_problem", "top",
                      "steps", "errors", "warnings", "can_undo", "can_redo"}
    assert s["version"] == 0 and s["path"] == str(path.resolve())
    assert not s["read_only"] and s["read_only_reason"] is None and s["robot_problem"] is None
    assert s["errors"] == [] and s["can_undo"] is False and s["can_redo"] is False
    assert s["top"] == {"config": raw["config"], "description": raw.get("description"), "speed": raw.get("speed"),
                        "require_lockstep": raw.get("require_lockstep"), "match": raw.get("match", {})}
    assert len(s["steps"]) == len(raw["step"])
    t = 0.0
    for got, want in zip(s["steps"], raw["step"]):
        assert set(got) == {"name", "mode", "duration", "game_data", "start_t", "inputs", "axes", "buttons", "povs",
                            "checks", "shots", "sheet"}
        assert got["game_data"] == want.get("game_data")
        assert (got["name"], got["mode"], got["duration"]) == (want.get("name"), want["mode"], want["duration"])
        assert got["start_t"] == pytest.approx(t)
        t += want["duration"]
        for k in ("axes", "buttons", "povs"):
            assert got[k] == want.get(k, {})
        assert got["checks"] == [{"kind": next(iter(ch)), "spec": next(iter(ch.values()))}
                                 for ch in want.get("check", [])]
        assert got["shots"] == want.get("screenshot", [])
        assert got["sheet"] == want.get("contact_sheet")
    # the robot tab shows the scenario's robot config
    r = c.get("/api/robot/state")[1]
    assert r["path"] == str((path.parent / raw["config"]).resolve()) and r["parts"]


def test_signals_and_view(served, repo_copy):
    c = served(repo_copy / "scenarios" / "kitbot_auto.toml")
    st, sig = c.get("/api/scenario/signals")
    assert st == 200 and sig["fixed"][:3] == ["battery", "speed", "robot.ground_gap"] and "joint.dt.left_2" in sig["joints"]
    assert sig["motors"] and sig["contacts"]
    st, v = c.get("/api/scenario/view")
    assert st == 200 and v["geoms"] and any(g["type"] == "plane" for g in v["world_geoms"])
    assert "world_geoms" not in c.get("/api/robot/view")[1]


def test_configs_lists_robot_configs_relative_to_the_scenario(served, repo_copy, monkeypatch):
    monkeypatch.chdir(repo_copy)   # configs/ is looked up in the working directory
    sdir = repo_copy / "scenarios"
    (sdir / "local_robot.toml").write_text((repo_copy / "configs" / "kitbot.toml").read_text())
    (sdir / "notes.txt").write_text("not a config")
    c = served(sdir / "kitbot_auto.toml")
    st, out = c.get("/api/scenario/configs")
    assert st == 200
    shipped = sorted(f"../configs/{p.name}" for p in (repo_copy / "configs").glob("*.toml"))
    assert out["configs"] == shipped + ["local_robot.toml"]    # configs/ first, then next to the scenario
    # other scenarios next to it are not robot configs
    assert not any(x.startswith("kitbot_") or x.endswith("_auto.toml") for x in out["configs"])
    assert c.get("/api/scenario/configs", token=None)[0] == 403


def test_configs_is_404_for_a_robot_config(served, repo_copy):
    c = served(repo_copy / "configs" / "kitbot.toml")
    assert c.get("/api/scenario/configs")[0] == 404


def test_robot_state_says_whether_a_scenario_is_open(served, repo_copy):
    assert served(repo_copy / "scenarios" / "kitbot_auto.toml").get("/api/robot/state")[1]["scenario_open"] is True
    assert served(repo_copy / "configs" / "kitbot.toml").get("/api/robot/state")[1]["scenario_open"] is False


def test_hand_written_robot_scenario_is_editable_and_has_a_view(served, tmp_path):
    c = served(fake_scenario(tmp_path))
    s = c.get("/api/scenario/state")[1]
    assert not s["read_only"] and s["errors"] == []
    r = c.get("/api/robot/state")[1]
    assert r["read_only"] and "hand-written" in r["read_only_reason"] and r["path"] == str(FAKE_ROBOT)
    assert c.get("/api/robot/view")[1] == {}
    v = c.get("/api/scenario/view")[1]
    assert v["geoms"] and v["world_geoms"] and v["instances"] == []
    assert "motor.left_drive" in c.get("/api/scenario/signals")[1]["motors"]
    assert c.post("/api/scenario/edit", {"op": "set_step", "step": 0, "fields": {"duration": 0.25}})[1]["ok"]


# ---- scenario edits -----------------------------------------------------------------------------------------

def test_edit_round_trip(served, repo_copy):
    path = repo_copy / "scenarios" / "kitbot_auto.toml"
    original = path.read_text()
    c = served(path)
    n = len(c.get("/api/scenario/state")[1]["steps"])

    def ok(op, **fields):
        st, r = c.post("/api/scenario/edit", {"op": op, **fields})
        assert st == 200 and r["ok"] is True, (op, r)
        return c.get("/api/scenario/state")[1]

    s = ok("add_step", name="wait", mode="disabled", duration=0.5)
    assert s["steps"][-1]["name"] == "wait" and len(s["steps"]) == n + 1 and s["version"] == 1 and s["can_undo"]
    s = ok("set_step", step=n, fields={"mode": "teleop", "duration": 2})
    assert (s["steps"][n]["mode"], s["steps"][n]["duration"]) == ("teleop", 2)
    s = ok("inputs", step=n, kind="axes", port=0, values=[0.0, 0.5])
    assert s["steps"][n]["axes"] == {"0": [0.0, 0.5]}
    s = ok("add_check", step=n, kind="at_end", spec={"signal": "battery", "min": 5})
    assert s["steps"][n]["checks"] == [{"kind": "at_end", "spec": {"signal": "battery", "min": 5}}]
    s = ok("set_check", step=n, check=0, spec={"console": {"contains": "hi"}})
    assert s["steps"][n]["checks"] == [{"kind": "console", "spec": {"contains": "hi"}}]
    s = ok("add_shot", step=n, spec={"view": "top", "at": "end"})
    assert s["steps"][n]["shots"] == [{"view": "top", "at": "end"}]
    s = ok("set_shot", step=n, shot=0, spec={"width": 640, "at": None})
    assert s["steps"][n]["shots"] == [{"view": "top", "width": 640}]
    s = ok("sheet", step=n, spec={"frames": 4})
    assert s["steps"][n]["sheet"] == {"frames": 4}
    s = ok("duplicate_step", step=n)
    assert len(s["steps"]) == n + 2 and s["steps"][n + 1]["checks"] == s["steps"][n]["checks"]
    s = ok("move_step", step=n + 1, to=0)
    assert s["steps"][0]["name"] == "wait" and s["steps"][1]["start_t"] == 2
    s = ok("delete_shot", step=0, shot=0)
    s = ok("delete_check", step=0, check=0)
    s = ok("sheet", step=0, spec=None)
    assert s["steps"][0]["shots"] == [] and s["steps"][0]["checks"] == [] and s["steps"][0]["sheet"] is None
    s = ok("delete_step", step=0)
    s = ok("delete_step", step=n)
    s = ok("top", key="description", value="changed")
    assert s["top"]["description"] == "changed" and 'description = "changed"' in path.read_text()
    s = ok("top", key="description", value=tomllib.loads(original)["description"])
    assert path.read_text() == original, "the edits undone by hand give back the file byte for byte"
    # undo and redo
    assert c.post("/api/scenario/undo")[1]["ok"]
    assert 'description = "changed"' in path.read_text()
    assert c.post("/api/scenario/redo")[1]["ok"] and path.read_text() == original
    assert c.post("/api/scenario/redo")[1] == {"ok": False, "errors": [{"field": "status", "message": "nothing to redo"}]}


def test_match_and_game_data_ops_and_state(served, tmp_path):
    path = fake_scenario(tmp_path)
    original = path.read_text()
    c = served(path)
    s = c.get("/api/scenario/state")[1]
    assert s["top"]["match"] == {} and [st["game_data"] for st in s["steps"]] == [None, None]

    def edit(op, **fields):
        return c.post("/api/scenario/edit", {"op": op, **fields})[1]

    assert edit("match", key="station", value="red1")["ok"]
    assert edit("match", key="timer", value=True)["ok"]
    assert edit("game_data", step=1, value="B")["ok"]
    s = c.get("/api/scenario/state")[1]
    assert s["top"]["match"] == {"station": "red1", "timer": True} and s["errors"] == []
    assert [st["game_data"] for st in s["steps"]] == [None, "B"]
    raw = tomllib.loads(path.read_text())
    assert raw["match"] == {"station": "red1", "timer": True} and raw["step"][1]["game_data"] == "B"
    # bad values are problems on their fields, and nothing is written
    before = path.read_text()
    r = edit("match", key="station", value="red4")
    assert r["ok"] is False and r["errors"][0]["field"] == "scenario/match.station"
    assert "red1, red2" in r["errors"][0]["message"]
    r = edit("game_data", step=0, value="x" * 65)
    assert r["ok"] is False and r["errors"][0]["field"] == "step0/game_data" and "64 bytes" in r["errors"][0]["message"]
    r = edit("match", key="fms", value="yes")
    assert r["ok"] is False and r["errors"][0]["field"] == "scenario/match.fms"
    assert path.read_text() == before
    # removing them gives back the file
    assert edit("game_data", step=1, value=None)["ok"]
    assert edit("match", key="station", value=None)["ok"] and edit("match", key="timer", value=None)["ok"]
    assert path.read_text() == original


def test_rejected_edits_give_field_errors_and_keep_the_file(served, repo_copy):
    path = repo_copy / "scenarios" / "kitbot_auto.toml"
    before = path.read_bytes()
    c = served(path)

    def err(op, **fields):
        st, r = c.post("/api/scenario/edit", {"op": op, **fields})
        assert st == 200 and r["ok"] is False and r["errors"], (op, r)
        assert all(set(e) == {"field", "message"} for e in r["errors"])
        return r["errors"]

    assert err("set_step", step=0, fields={"mode": "fly"})[0]["field"] == "step0/mode"
    assert err("set_step", step=0, fields={"duration": -1})[0]["field"] == "step0/duration"
    j = len(c.get("/api/scenario/state")[1]["steps"][1]["checks"])
    assert err("add_check", step=1, kind="at_end", spec={"signal": "motor.nope.current", "min": 0})[0]["field"] \
        == f"step1/check{j}/at_end.signal"
    assert err("add_check", step=0, kind="never_touch", spec="no_such_thing")[0]["field"] == "step0/check0/never_touch"
    assert err("add_shot", step=0, spec={"view": "sideways"})[0]["field"] == "step0/shot0/view"
    assert err("inputs", step=0, kind="axes", port=9, values=[0])[0]["field"] == "step0/axes"
    assert err("delete_step", step=99)[0]["field"] == "step99/step"
    assert err("top", key="config", value="../configs/nothing.toml")[0]["field"] == "status"
    assert err("set_check", step=0, check="x", spec=3)   # junk values from a client: an error, not a crash
    assert path.read_bytes() == before
    s = c.get("/api/scenario/state")[1]
    assert s["version"] == 0 and not s["can_undo"]
    assert c.post("/api/scenario/edit", {"op": "bogus"})[0] == 400
    st, r = c.post("/api/scenario/edit", {"op": "delete_step", "step": 0, "version": None})
    assert st == 400 and r["errors"][0]["field"] == "status"
    assert c.post("/api/scenario/undo", {"version": None})[0] == 400


def test_stale_version_and_outside_change(served, repo_copy):
    path = repo_copy / "scenarios" / "kitbot_auto.toml"
    c = served(path)
    assert c.post("/api/scenario/edit", {"op": "top", "key": "description", "value": "a"})[1]["ok"]
    st, r = c.post("/api/scenario/edit", {"op": "top", "key": "description", "value": "b", "version": 0})
    assert st == 409 and "another tab" in r["error"]
    path.write_text(path.read_text().replace('description = "a"', 'description = "outside"'))
    st, r = c.post("/api/scenario/edit", {"op": "top", "key": "description", "value": "c"})
    assert st == 409 and "changed on disk" in r["error"]
    s = c.get("/api/scenario/state")[1]
    assert s["top"]["description"] == "outside" and not s["can_undo"] and s["version"] == 2


def test_long_poll_scenario_state(served, repo_copy):
    path = repo_copy / "scenarios" / "kitbot_auto.toml"
    c = served(path)
    out = {}
    th = threading.Thread(target=lambda: out.update(s=c.get("/api/scenario/state?since=0")[1]))
    th.start()
    time.sleep(0.4)
    assert th.is_alive()
    path.write_text(path.read_text().replace("description =", "description = 'x'\n# was:"))
    th.join(10)
    assert out["s"]["version"] == 1 and out["s"]["top"]["description"] == "x"
    assert c.get("/api/scenario/state?since=x")[0] == 400


def test_switching_config_switches_the_robot(served, repo_copy):
    path = repo_copy / "scenarios" / "kitbot_auto.toml"
    c = served(path)
    r0 = c.get("/api/robot/state")[1]
    assert r0["path"].endswith("kitbot.toml")
    # the kitbot's checks name kitbot signals, so a fresh scenario on another robot: write one with only a step
    path.write_text('config = "../configs/kitbot.toml"\n\n[[step]]\nmode = "auto"\nduration = 1.0\n')
    assert c.get("/api/scenario/state?since=0")[1]["version"] == 1
    assert c.post("/api/scenario/edit", {"op": "top", "key": "config", "value": "../configs/swerve.toml"})[1]["ok"]
    r1 = c.get("/api/robot/state")[1]
    assert r1["path"] == str((repo_copy / "configs" / "swerve.toml").resolve()) and r1["version"] > r0["version"]
    assert any("MK4i" in str(p["stock"]) for p in r1["parts"])
    joints = c.get("/api/scenario/signals")[1]["joints"]
    assert joints and "joint.dt.left_2" not in joints and any(j.startswith("joint.fl.") for j in joints)
    # a robot edit goes to the new robot's file; undoing the scenario edit brings the kitbot back
    assert c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 40.0}})[1]["ok"]
    assert "mass = 40.0" in (repo_copy / "configs" / "swerve.toml").read_text()
    assert c.post("/api/scenario/undo")[1]["ok"]
    r2 = c.get("/api/robot/state")[1]
    assert r2["path"].endswith("kitbot.toml") and r2["version"] > r1["version"]
    # a stale robot version (from the swerve) can't edit the kitbot
    st, _ = c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 41.0},
                                       "version": r1["version"]})
    assert st == 409


# ---- read-only cases ----------------------------------------------------------------------------------------

def test_robot_breaking_makes_the_scenario_read_only(served, repo_copy):
    path = repo_copy / "scenarios" / "kitbot_auto.toml"
    robot = repo_copy / "configs" / "kitbot.toml"
    good = robot.read_text()
    c = served(path)
    assert not c.get("/api/scenario/state")[1]["read_only"]
    out = {}
    th = threading.Thread(target=lambda: out.update(s=c.get("/api/scenario/state?since=0")[1]))
    th.start()
    time.sleep(0.3)
    robot.write_text(good.replace('stock = "chassis"', 'stock = "no_such_stock"', 1))
    th.join(10)
    s = out["s"]
    assert s["read_only"] and s["robot_problem"] and s["read_only_reason"].startswith("fix the robot first")
    assert s["version"] == 1 and s["steps"], "the steps are still shown"
    before = path.read_bytes()
    st, r = c.post("/api/scenario/edit", {"op": "set_step", "step": 0, "fields": {"duration": 1.0}})
    assert st == 409 and "fix the robot first" in r["error"]
    st, r = c.post("/api/scenario/run", {})
    assert st == 200 and r["ok"] is False and "fix the robot first" in r["errors"][0]["message"]
    assert c.get("/api/scenario/signals")[1] == {} and c.get("/api/scenario/view")[1] == {}
    assert c.get("/api/robot/state")[1]["read_only"]
    assert path.read_bytes() == before
    robot.write_text(good)
    s = c.get("/api/scenario/state?since=1")[1]
    assert not s["read_only"] and s["robot_problem"] is None and s["errors"] == []
    assert c.post("/api/scenario/edit", {"op": "set_step", "step": 0, "fields": {"duration": 1.0}})[1]["ok"]


def test_robot_edit_that_breaks_a_check_shows_on_the_scenario(served, repo_copy):
    path = repo_copy / "scenarios" / "kitbot_auto.toml"
    c = served(path)
    sig = c.get("/api/scenario/signals")[1]
    assert c.post("/api/scenario/edit", {"op": "add_check", "step": 0, "kind": "at_end",
                                         "spec": {"signal": "joint.dt.left_2.velocity", "max": 100}})[1]["ok"]
    v = c.get("/api/scenario/state")[1]["version"]
    assert c.post("/api/robot/edit", {"op": "rename", "name": "dt", "new_name": "wheels"})[1]["ok"]
    s = c.get("/api/scenario/state")[1]
    assert s["version"] == v + 1 and not s["read_only"]
    assert any(e["field"].startswith("step0/check") for e in s["errors"]), s["errors"]
    assert c.get("/api/scenario/signals")[1] != sig


def test_missing_robot_config_on_open(served, tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('config = "nowhere.toml"\n\n[[step]]\nmode = "auto"\nduration = 1.0\n')
    c = served(p)
    s = c.get("/api/scenario/state")[1]
    assert s["read_only"] and "fix the robot first" in s["read_only_reason"] and "nowhere.toml" in s["robot_problem"]
    r = c.get("/api/robot/state")[1]
    assert r["read_only"] and r["path"] is None and r["parts"] == [] and "nowhere.toml" in r["read_only_reason"]
    assert c.post("/api/robot/edit", {"op": "remove", "name": "x"})[0] == 409
    assert c.get("/api/robot/view")[1] == {} and c.get("/api/robot/library")[0] == 200
    # the robot config appearing on disk makes both editable
    shutil.copy(ROOT / "configs" / "kitbot.toml", tmp_path / "nowhere.toml")
    s = c.get("/api/scenario/state?since=0")[1]
    assert not s["read_only"] and s["robot_problem"] is None
    r = c.get("/api/robot/state")[1]
    assert r["path"] == str(tmp_path / "nowhere.toml") and not r["read_only"] and r["version"] == 1


def test_unparsable_scenario_is_read_only(served, tmp_path):
    p = tmp_path / "s.toml"
    p.write_text(f'config = "{FAKE_ROBOT}"\n[[step]\n')
    c = served(p)
    s = c.get("/api/scenario/state")[1]
    assert s["read_only"] and "not valid TOML" in s["read_only_reason"] and s["steps"] == [] and s["errors"]
    assert c.post("/api/scenario/edit", {"op": "delete_step", "step": 0})[0] == 409


# ---- runs ---------------------------------------------------------------------------------------------------

def _wait_run(c, rid, timeout=60):
    deadline = time.monotonic() + timeout
    since = None
    while time.monotonic() < deadline:
        r = c.get("/api/scenario/runs" + (f"?since={since}" if since is not None else ""))[1]
        since = r["version"]
        run = next(x for x in r["runs"] if x["id"] == rid)
        if run["state"] != "running":
            return run
    raise AssertionError("the run didn't finish")


def test_run_stop_runs_and_run_files(served, tmp_path):
    path = fake_scenario(tmp_path)
    c = served(path)
    assert c.get("/api/scenario/runs")[1] == {"version": 0, "runs": []}
    st, r = c.post("/api/scenario/run", {"speed": "max"})
    assert st == 200 and r["ok"] is True
    rid = r["id"]
    # a second run while this one is going: 409
    st, r = c.post("/api/scenario/run", {})
    assert st == 409 and "already in progress" in r["error"]
    run = _wait_run(c, rid)
    assert run["state"] == "passed" and run["report"]["passed"] and run["out_dir"].startswith(str(tmp_path / "runs"))
    st, md = c.get(f"/api/scenario/run/{rid}/report.md")
    assert st == 200 and b"PASS" in md
    st, rep = c.get(f"/api/scenario/run/{rid}/report.json")
    assert st == 200 and rep["passed"] is True
    st, snap = c.get(f"/api/scenario/run/{rid}/scenario.toml")
    assert st == 200 and str(FAKE_ROBOT).encode() in snap
    for bad in (f"{rid}/../../x", f"{rid}/%2e%2e/%2e%2e/etc/passwd", f"{rid}/nope.png", "nosuchrun/report.md",
                f"{rid}/", rid):
        assert c.get(f"/api/scenario/run/{bad}")[0] == 404, bad
    assert c.get(f"/api/scenario/run/{rid}/report.md", token=None)[0] == 403

    # stop a run part way
    path.write_text(path.read_text().replace("duration = 1.0", "duration = 30.0"))
    v = c.get("/api/scenario/state?since=0")[1]["version"]
    st, r = c.post("/api/scenario/run", {"speed": 1, "version": v})
    rid2 = r["id"]
    deadline = time.monotonic() + 30
    while c.get("/api/scenario/runs")[1]["runs"][0]["step"] is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert c.post("/api/scenario/stop")[1] == {"ok": True}
    run = _wait_run(c, rid2, 20)
    assert run["state"] == "stopped"
    assert [x["id"] for x in c.get("/api/scenario/runs")[1]["runs"]] == [rid2, rid]


def test_run_refusals(served, tmp_path):
    path = fake_scenario(tmp_path)
    c = served(path)
    st, r = c.post("/api/scenario/run", {"version": 5})
    assert st == 409
    assert c.post("/api/scenario/run", {"version": None})[0] == 400
    for speed in ("fast", -1, True, [1]):
        st, r = c.post("/api/scenario/run", {"speed": speed})
        assert st == 200 and r["ok"] is False and "speed" in r["errors"][0]["message"], speed
    # a scenario with errors doesn't run
    path.write_text(path.read_text().replace('mode = "auto"', 'mode = "fly"'))
    c.get("/api/scenario/state?since=0")
    st, r = c.post("/api/scenario/run", {})
    assert st == 200 and r["ok"] is False and r["errors"][0]["field"] == "step1/mode"
    assert c.get("/api/scenario/runs")[1]["runs"] == [] and not (tmp_path / "runs").exists()


def test_a_run_uses_the_text_at_run_time(served, tmp_path):
    path = fake_scenario(tmp_path)
    c = served(path)
    rid = c.post("/api/scenario/run", {})[1]["id"]
    assert c.post("/api/scenario/edit", {"op": "delete_step", "step": 0})[1]["ok"]
    run = _wait_run(c, rid)
    assert [s["name"] for s in run["report"]["steps"]] == ["idle", "auto"]


# ---- scrimmage edit --new-scenario -------------------------------------------------------------------------

def _edit(*args, cwd):
    return subprocess.run([sys.executable, "-m", "scrimmage", "edit", *args], cwd=cwd, capture_output=True,
                          text=True, timeout=60)


def test_new_scenario_template_validates(tmp_path, repo_copy):
    text = templates.new_scenario("../configs/kitbot.toml")
    res = scenario_validate.check(text, repo_copy / "scenarios" / "new.toml")
    assert res.ok, res.errors
    raw = tomllib.loads(text)
    assert [(s["mode"], s["duration"]) for s in raw["step"]] == [("disabled", 1.0), ("auto", 5.0)]
    assert "check" not in raw["step"][1] and "# console = " in text


def test_cli_new_scenario(tmp_path, repo_copy):
    from test_assembler_server import _cli
    (repo_copy / "mine").mkdir()
    p = _cli("mine/new.toml", "--new-scenario", "--robot", "configs/kitbot.toml", "--no-browser", cwd=repo_copy)
    try:
        url = None
        for line in p.stdout:
            if line.startswith("http://"):
                url = line.strip()
                break
        assert url
        written = (repo_copy / "mine" / "new.toml").read_text()
        assert written == templates.new_scenario("../configs/kitbot.toml")
        import json
        import urllib.request
        req = urllib.request.Request(url.split("#")[0] + "api/scenario/state",
                                     headers={"X-Scrimmage-Token": url.split("#token=")[1]})
        with urllib.request.urlopen(req, timeout=10) as r:
            s = json.loads(r.read())
        assert s["errors"] == [] and not s["read_only"] and len(s["steps"]) == 2
    finally:
        p.terminate()
        p.wait(15)
    again = _edit("mine/new.toml", "--new-scenario", "--robot", "configs/kitbot.toml", "--no-browser", cwd=repo_copy)
    assert again.returncode == 2 and "already exists" in again.stderr
    assert (repo_copy / "mine" / "new.toml").read_text() == written
    r = _edit("x.toml", "--new-scenario", cwd=repo_copy)
    assert r.returncode == 2 and "needs --robot" in r.stderr
    r = _edit("x.toml", "--new-scenario", "--robot", "configs/none.toml", cwd=repo_copy)
    assert r.returncode == 2 and "not found" in r.stderr
    r = _edit("x.toml", "--robot", "configs/kitbot.toml", cwd=repo_copy)
    assert r.returncode == 2 and "--robot goes with --new-scenario" in r.stderr
    r = _edit("x.toml", "--new", "swerve", "--new-scenario", "--robot", "configs/kitbot.toml", cwd=repo_copy)
    assert r.returncode == 2 and "not both" in r.stderr
    r = _edit("--new", "swerve", cwd=repo_copy)   # with no FILE, edit opens the start page; --new still needs one
    assert r.returncode == 2 and "--new needs FILE" in r.stderr
    assert not (repo_copy / "x.toml").exists()


# ---- review fix round 1 -------------------------------------------------------------------------------------

def _kitbot_scenario(repo_copy, body: str) -> Path:
    p = repo_copy / "scenarios" / "broken.toml"
    p.write_text('config = "../configs/kitbot.toml"\n' + body)
    return p


TWO_BAD_SIGNALS = '''
[[step]]
name = "auto"
mode = "auto"
duration = 1.0
  [[step.check]]
  at_end = { signal = "motor.nope.current", max = 10 }
  [[step.check]]
  at_end = { signal = "joint.nope.position", max = 10 }
'''


def test_invalid_file_can_be_fixed_one_error_at_a_time(served, repo_copy):
    path = _kitbot_scenario(repo_copy, TWO_BAD_SIGNALS)
    c = served(path)
    s = c.get("/api/scenario/state")[1]
    assert not s["read_only"] and len(s["errors"]) == 2
    assert {e["field"] for e in s["errors"]} == {"step0/check0/at_end.signal", "step0/check1/at_end.signal"}
    st, r = c.post("/api/scenario/run", {})
    assert r["ok"] is False   # no runs until the file is valid
    r = c.post("/api/scenario/edit", {"op": "set_check", "step": 0, "check": 0,
                                      "spec": {"at_end": {"signal": "battery", "max": 20}}})[1]
    assert r["ok"], r
    s = c.get("/api/scenario/state")[1]
    assert [e["field"] for e in s["errors"]] == ["step0/check1/at_end.signal"] and "battery" in path.read_text()
    assert c.post("/api/scenario/edit", {"op": "set_check", "step": 0, "check": 1,
                                         "spec": {"at_end": {"signal": "joint.dt.left_2.position", "max": 1e6}}})[1]["ok"]
    s = c.get("/api/scenario/state")[1]
    assert s["errors"] == [] and s["can_undo"]
    # the file is valid again, so undo to an invalid version is refused
    st, r = c.post("/api/scenario/undo")
    assert r["ok"] is False and r["errors"][0]["message"].startswith("can't undo")


def test_fixing_one_bad_mode_reveals_the_next(served, repo_copy):
    # the loader stops at a step's first error, so fixing it can reveal the next one in that step
    path = _kitbot_scenario(repo_copy, '''
[[step]]
mode = "fly"
duration = -1.0

[[step]]
mode = "swim"
duration = 1.0
''')
    c = served(path)
    s = c.get("/api/scenario/state")[1]
    assert [e["field"] for e in s["errors"]] == ["step0/mode", "step1/mode"]   # every step's errors count
    assert c.post("/api/scenario/edit", {"op": "set_step", "step": 0, "fields": {"mode": "auto"}})[1]["ok"]
    s = c.get("/api/scenario/state")[1]
    assert [e["field"] for e in s["errors"]] == ["step0/duration", "step1/mode"]
    # undo and redo between two invalid versions with as many errors are written
    assert c.post("/api/scenario/undo")[1]["ok"] and 'mode = "fly"' in path.read_text()
    assert c.post("/api/scenario/redo")[1]["ok"] and 'mode = "fly"' not in path.read_text()
    assert c.post("/api/scenario/edit", {"op": "set_step", "step": 0, "fields": {"duration": 1.0}})[1]["ok"]
    assert c.post("/api/scenario/edit", {"op": "set_step", "step": 1, "fields": {"mode": "teleop"}})[1]["ok"]
    assert c.get("/api/scenario/state")[1]["errors"] == []
    assert c.post("/api/scenario/undo")[1]["ok"] is False   # valid now: undo to an invalid version is refused


def test_a_new_error_elsewhere_is_refused_even_when_it_is_a_load_error(served, tmp_path):
    """The reviewer's probe: one world error in step 3; a load error in another step must not mask it."""
    p = tmp_path / "s.toml"
    p.write_text(f'''config = "{FAKE_ROBOT}"
[[step]]
name = "a"
mode = "disabled"
duration = 0.5
[[step]]
name = "b"
mode = "auto"
duration = 1.0
[[step]]
name = "c"
mode = "auto"
duration = 1.0
  [[step.check]]
  at_end = {{ signal = "battery", min = 5, bogus = 1 }}
''')
    before = p.read_bytes()
    c = served(p)
    assert [e["field"] for e in c.get("/api/scenario/state")[1]["errors"]] == ["step2/check0/at_end.bogus"]
    for body in ({"op": "set_step", "step": 0, "fields": {"duration": -1}},
                 {"op": "set_step", "step": 1, "fields": {"mode": "bogus"}}):
        st, r = c.post("/api/scenario/edit", body)
        assert st == 200 and r["ok"] is False, (body, r)
        assert len(r["errors"]) == 2
        assert p.read_bytes() == before
    assert [e["field"] for e in c.get("/api/scenario/state")[1]["errors"]] == ["step2/check0/at_end.bogus"]


def test_invalid_file_refuses_a_candidate_with_more_errors(served, repo_copy):
    path = _kitbot_scenario(repo_copy, TWO_BAD_SIGNALS)
    before = path.read_bytes()
    c = served(path)
    st, r = c.post("/api/scenario/edit", {"op": "add_check", "step": 0, "kind": "at_end",
                                          "spec": {"signal": "motor.other.current", "max": 1}})
    assert st == 200 and r["ok"] is False and len(r["errors"]) == 3
    assert path.read_bytes() == before
    # an edit elsewhere that keeps the count is written
    assert c.post("/api/scenario/edit", {"op": "set_step", "step": 0, "fields": {"duration": 2.0}})[1]["ok"]
    assert len(c.get("/api/scenario/state")[1]["errors"]) == 2


def test_removing_config_detaches_the_robot(served, repo_copy):
    path = repo_copy / "scenarios" / "kitbot_auto.toml"
    c = served(path)
    v = c.get("/api/robot/state")[1]["version"]
    path.write_text(path.read_text().replace('config = "../configs/kitbot.toml"', ""))
    c.get("/api/scenario/state?since=0")
    r = c.get("/api/robot/state")[1]
    assert r["path"] is None and r["read_only"] and "names no robot config" in r["read_only_reason"]
    assert r["version"] > v
    s = c.get("/api/scenario/state")[1]
    assert s["errors"][0]["field"] == "scenario/config" and not s["read_only"]
    # an unparsable scenario: the robot tab says why
    path.write_text("config = [[\n")
    s = c.get(f"/api/scenario/state?since={s['version']}")[1]
    assert s["read_only"] and "TOML error" in s["robot_problem"]
    assert "TOML error" in c.get("/api/robot/state")[1]["read_only_reason"]


def test_robot_problem_paths_are_relative(served, repo_copy, monkeypatch):
    monkeypatch.chdir(repo_copy)
    robot = repo_copy / "configs" / "kitbot.toml"
    good = robot.read_text()
    robot.write_text(good + '\n[[part]]\nname = "x"\npath = "nowhere"\n')
    c = served(repo_copy / "scenarios" / "kitbot_auto.toml")
    s = c.get("/api/scenario/state")[1]
    assert s["read_only"] and s["robot_problem"]
    for text in (s["robot_problem"], s["read_only_reason"], s["errors"][0]["message"]):
        assert str(repo_copy) not in text, text


def test_deleted_robot_file_makes_the_scenario_read_only_until_it_is_back(served, repo_copy, monkeypatch):
    monkeypatch.chdir(repo_copy)
    robot = repo_copy / "configs" / "kitbot.toml"
    good = robot.read_text()
    c = served(repo_copy / "scenarios" / "kitbot_auto.toml")
    robot.unlink()
    s = c.get("/api/scenario/state?since=0")[1]
    assert s["read_only"] and s["robot_problem"] == "the robot config configs/kitbot.toml was deleted"
    assert s["read_only_reason"] == "fix the robot first: the robot config configs/kitbot.toml was deleted"
    r = c.get("/api/robot/state")[1]
    assert r["read_only"] and r["path"] is None and "was deleted" in r["read_only_reason"]
    time.sleep(1.2)   # a few polls: the reason stays put and the version doesn't creep
    v = c.get("/api/scenario/state")[1]["version"]
    assert c.get(f"/api/scenario/state?since={v - 1}")[1]["version"] == v
    robot.write_text(good)
    s = c.get(f"/api/scenario/state?since={v}")[1]
    assert not s["read_only"] and s["robot_problem"] is None and s["errors"] == []
    assert c.get("/api/robot/state")[1]["path"] == str(robot)


# ---- final review fixes ------------------------------------------------------------------------------------

def test_refused_robot_undo_with_a_scenario_open_gives_the_robot_errors(served, repo_copy):
    folder = repo_copy / "configs" / "gadget"
    shutil.copytree(ROOT / "tests" / "parts" / "intake", folder)
    path = _kitbot_scenario(repo_copy, TWO_BAD_SIGNALS)    # the scenario has errors of its own
    c = served(path)
    assert c.post("/api/robot/edit", {"op": "add", "name": "g", "path": "gadget", "attach": "drive.top"})[1]["ok"]
    assert c.post("/api/robot/edit", {"op": "remove", "name": "g"})[1]["ok"]
    shutil.rmtree(folder)
    st, r = c.post("/api/robot/undo")
    assert st == 200 and r["ok"] is False and r["errors"]
    assert all(e["message"].startswith("can't undo: ") for e in r["errors"]), r
    assert not any("hand-written" in e["message"] or "nope" in e["message"] for e in r["errors"]), r
    assert any(e["field"].startswith("g/") or "gadget" in e["message"] for e in r["errors"]), r


def test_closing_the_editor_during_robot_start_up_leaves_no_robot(tmp_path):
    from test_scenario_runs import _marked_pids, _wait_started, slow_robot_scenario
    path, _, marker = slow_robot_scenario(tmp_path)
    app = App(path, open_browser=False, runs_dir=tmp_path / "runs")
    c = Client(app, app.start())
    try:
        st, r = c.post("/api/scenario/run", {})
        assert r["ok"], r
        _wait_started(marker)
        t0 = time.monotonic()
        assert c.post("/api/scenario/stop")[1] == {"ok": True}
        run = _wait_run(c, r["id"], 5)
        assert run["state"] == "stopped" and time.monotonic() - t0 < 2.5
        assert _marked_pids(marker) == []
        # again, closing the editor (App.stop) instead of pressing Stop
        v = c.get("/api/scenario/state")[1]["version"]
        assert c.post("/api/scenario/run", {"version": v})[1]["ok"]
        _wait_started(marker)
    finally:
        t0 = time.monotonic()
        app.stop()
    assert time.monotonic() - t0 < 10 and not app.runs.running
    assert _marked_pids(marker) == []


def test_run_progress_does_not_wait_for_the_app_lock(tmp_path):
    """A slow edit validation (holding the app lock) must not stall the run's progress callbacks."""
    path = fake_scenario(tmp_path)
    app = App(path, open_browser=False, runs_dir=tmp_path / "runs")
    held, release = threading.Event(), threading.Event()

    def hold():
        with app._lock:
            held.set()
            release.wait(10)
    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(5)
        done = threading.Event()
        threading.Thread(target=lambda: (app._runs_changed(), done.set()), daemon=True).start()
        assert done.wait(2), "the progress callback waited for the app lock"
    finally:
        release.set()
        t.join()


# ---- input tracks -----------------------------------------------------------------------------------------

def _track_csv(rows=5):
    lines = ["t,p0.axis0,p0.button1,p0.pov0"]
    lines += [f"{i * 0.1:.1f},{i / 10},{i % 2},{-1 if i < 3 else 90}" for i in range(rows)]
    return "\n".join(lines) + "\n"


def _tracked_scenario(tmp_path):
    path = fake_scenario(tmp_path)
    (tmp_path / "drive.csv").write_text(_track_csv())
    return path


def test_inputs_file_op_and_state(served, tmp_path):
    path = _tracked_scenario(tmp_path)
    c = served(path)
    steps = c.get("/api/scenario/state")[1]["steps"]
    assert [s["inputs"] for s in steps] == [None, None]
    assert c.post("/api/scenario/edit", {"op": "inputs", "step": 1, "kind": "axes", "port": 0,
                                         "values": [0.5]})[1]["ok"]
    assert c.post("/api/scenario/edit", {"op": "inputs_file", "step": 1, "path": "drive.csv"})[1]["ok"]
    st = c.get("/api/scenario/state")[1]
    assert st["steps"][1]["inputs"] == "drive.csv" and st["steps"][1]["axes"] == {} and st["errors"] == []
    raw = tomllib.loads(path.read_text())["step"][1]
    assert raw["inputs"] == "drive.csv" and "axes" not in raw
    # a missing file is rejected, nothing written
    before = path.read_text()
    r = c.post("/api/scenario/edit", {"op": "inputs_file", "step": 0, "path": "missing.csv"})[1]
    assert r["ok"] is False and r["errors"][0]["field"] == "step0/inputs" and path.read_text() == before
    # removing it
    assert c.post("/api/scenario/edit", {"op": "inputs_file", "step": 1, "path": None})[1]["ok"]
    assert c.get("/api/scenario/state")[1]["steps"][1]["inputs"] is None


def test_bad_csv_is_a_step_inputs_error_in_state(served, tmp_path):
    path = _tracked_scenario(tmp_path)
    path.write_text(path.read_text().replace('duration = 1.0\n', 'duration = 1.0\ninputs = "drive.csv"\n'))
    (tmp_path / "drive.csv").write_text("t,p0.axis0\n0,5\n")
    c = served(path)
    st = c.get("/api/scenario/state")[1]
    assert st["steps"][1]["inputs"] == "drive.csv"
    assert [e["field"] for e in st["errors"]] == ["step1/inputs"]
    assert c.get("/api/scenario/track?step=1")[0] == 404


def test_track_route(served, tmp_path):
    path = _tracked_scenario(tmp_path)
    c = served(path)
    assert c.get("/api/scenario/track?step=1")[0] == 404   # no inputs
    assert c.post("/api/scenario/edit", {"op": "inputs_file", "step": 1, "path": "drive.csv"})[1]["ok"]
    st, r = c.get("/api/scenario/track?step=1")
    assert st == 200
    assert r["path"] == "drive.csv" and r["duration"] == 1.0 and r["t"] == [0.0, 0.1, 0.2, 0.3, 0.4]
    assert r["series"] == {"p0.axis0": [0.0, 0.1, 0.2, 0.3, 0.4], "p0.button1": [0, 1, 0, 1, 0],
                           "p0.pov0": [-1, -1, -1, 90, 90]}
    for q in ("step=9", "step=x", "", "step=-1"):
        assert c.get("/api/scenario/track?" + q)[0] in (400, 404), q
    assert c.get("/api/scenario/track?step=1", token=None)[0] == 403


def test_trim_track_op_undo_and_cleanup(served, tmp_path):
    path = _tracked_scenario(tmp_path)
    c = served(path)
    assert c.post("/api/scenario/edit", {"op": "inputs_file", "step": 1, "path": "drive.csv"})[1]["ok"]
    r = c.post("/api/scenario/edit", {"op": "trim_track", "step": 1, "start": 0.15, "end": 0.35})[1]
    assert r["ok"], r
    st = c.get("/api/scenario/state")[1]
    assert st["steps"][1]["inputs"] == "drive.trim1.csv" and st["steps"][1]["duration"] == 0.2
    assert (tmp_path / "drive.trim1.csv").exists()
    assert c.get("/api/scenario/track?step=1")[1]["t"] == [0.0, 0.05, 0.15]
    # out of range: error, no stray file
    r = c.post("/api/scenario/edit", {"op": "trim_track", "step": 1, "start": 0, "end": 5})[1]
    assert r["ok"] is False and r["errors"][0]["field"] == "step1/inputs"
    assert sorted(p.name for p in tmp_path.glob("drive*.csv")) == ["drive.csv", "drive.trim1.csv"]
    # undo restores the old track and keeps both files
    assert c.post("/api/scenario/undo")[1]["ok"]
    st = c.get("/api/scenario/state")[1]
    assert st["steps"][1]["inputs"] == "drive.csv" and st["steps"][1]["duration"] == 1.0
    assert (tmp_path / "drive.trim1.csv").exists()


def test_trim_track_rejected_edit_leaves_no_csv(served, tmp_path):
    path = _tracked_scenario(tmp_path)
    c = served(path)
    assert c.post("/api/scenario/edit", {"op": "inputs_file", "step": 1, "path": "drive.csv"})[1]["ok"]
    # a screenshot at 0.9 s is fine in a 1 s step but not in the 0.3 s one the trim makes
    assert c.post("/api/scenario/edit", {"op": "add_shot", "step": 1, "spec": {"at": 0.9}})[1]["ok"]
    before = path.read_text()
    r = c.post("/api/scenario/edit", {"op": "trim_track", "step": 1, "start": 0, "end": 0.3})[1]
    assert r["ok"] is False, "the shot past the new end must make validation refuse"
    assert path.read_text() == before
    assert sorted(p.name for p in tmp_path.glob("drive*.csv")) == ["drive.csv"]


def test_a_runs_tracks_are_read_when_it_starts(served, tmp_path):
    path = _tracked_scenario(tmp_path)
    path.write_text(path.read_text().replace('duration = 1.0\n', 'duration = 1.0\ninputs = "drive.csv"\n'))
    c = served(path)
    rid = c.post("/api/scenario/run", {"speed": 1})[1]["id"]
    (tmp_path / "drive.csv").write_text("garbage")   # rewritten after Run
    run = _wait_run(c, rid)
    assert run["state"] == "passed", run
