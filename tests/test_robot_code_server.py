"""The Robot tab's robot code panel on the server: team folders, pointing at the robot code, its readiness fixes,
where the robot starts (and on which field), the battery form, and the page-only start gate."""

import json
import os
import threading
import time
import tomllib
from pathlib import Path

import pytest

from scrimmage import robotcode, teamfolders
from scrimmage.assembler import templates
from scrimmage.assembler.live import LiveError
from scrimmage.assembler.server import App
from scrimmage.robotcode import Check
from test_assembler_server import Client

ROOT = Path(__file__).resolve().parent.parent
UNPICKED = {"field": "start/unpicked", "message": "Pick where the robot starts first"}


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("SCRIMMAGE_RECENT", raising=False)
    monkeypatch.delenv("SCRIMMAGE_CAD", raising=False)
    monkeypatch.delenv("SCRIMMAGE_CODE", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def start():
    started = []

    def make(path=None):
        app = App(path, open_browser=False, runs_dir=Path.cwd() / "runs")
        started.append(app)
        return Client(app, app.start())
    yield make
    for a in started:
        a.stop()


def _robot(tmp_path, name="bot.toml", extra="") -> Path:
    p = tmp_path / name
    p.write_text(templates.new_config("swerve") + extra)
    return p


def _java_project(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "build.gradle").write_text("plugins { id 'java' }\n")
    (folder / "vendordeps").mkdir()
    (folder / "vendordeps" / "Phoenix6.json").write_text(json.dumps({"name": "CTRE-Phoenix (v6)"}))
    return folder


def _raw(p: Path) -> dict:
    return tomllib.loads(p.read_text())


def test_folders_roundtrip(tmp_path, start, monkeypatch):
    (tmp_path / "team" / "cad").mkdir(parents=True)
    c = start()
    s, b = c.get("/api/app/folders")
    assert s == 200 and b == {"cad": None, "code": None, "from_env": {"cad": False, "code": False}}
    s, b = c.call("POST", "/api/app/folders", {"name": "cad", "path": "team/cad"})
    assert s == 200 and b == {"ok": True}
    assert teamfolders.folders()["cad"] == tmp_path / "team" / "cad"
    s, b = c.get("/api/app/folders")
    assert b["cad"] == str(tmp_path / "team" / "cad") and b["code"] is None
    # a path that isn't an existing folder, or an unknown name, is refused and nothing is stored
    s, b = c.call("POST", "/api/app/folders", {"name": "code", "path": "nope"})
    assert s == 400 and "not a folder" in b["error"] and teamfolders.folders()["code"] is None
    s, b = c.call("POST", "/api/app/folders", {"name": "music", "path": "team"})
    assert s == 400
    # null forgets it
    assert c.call("POST", "/api/app/folders", {"name": "cad", "path": None}) == (200, {"ok": True})
    assert c.get("/api/app/folders")[1]["cad"] is None
    # the environment wins, and the page is told so
    monkeypatch.setenv("SCRIMMAGE_CODE", str(tmp_path / "team"))
    b = c.get("/api/app/folders")[1]
    assert b["code"] == str(tmp_path / "team") and b["from_env"] == {"cad": False, "code": True}


def test_code_detect_writes_program_undoable(tmp_path, start):
    proj = _java_project(tmp_path / "code" / "5098-2026")
    teamfolders.set_folder("code", tmp_path / "code")
    cfg = _robot(tmp_path)
    before = cfg.read_text()
    c = start(cfg)
    s, b = c.get("/api/robot/code")
    assert s == 200 and b["folder"] is None and b["info"] is None and b["checks"] == []
    s, b = c.call("POST", "/api/robot/code", {"folder": str(proj)})
    assert s == 200 and b["ok"] is True, b
    assert b["info"]["kind"] == "java" and b["info"]["summary"] == "Java (GradleRIO) · Phoenix 6"
    rp = _raw(cfg)["robot_program"]
    assert rp == {"command": robotcode.JAVA_COMMAND, "cwd": "{code}/5098-2026"}
    assert cfg.read_text().startswith(before.rstrip("\n"))   # comments and order kept: the table is added
    s, b = c.get("/api/robot/code")
    assert b["folder"] == str(proj) and b["cwd"] == "{code}/5098-2026"
    assert b["info"] == {"folder": str(proj), "kind": "java", "vendors": ["Phoenix 6"], "tuner_swerve": False,
                         "python": None, "summary": "Java (GradleRIO) · Phoenix 6"}
    ids = [ch["id"] for ch in b["checks"]]
    assert ids[:3] == ["jdk", "agent", "gradle_built"]
    assert all(set(ch) == {"id", "ok", "message", "fix", "detail"} for ch in b["checks"])
    built = next(ch for ch in b["checks"] if ch["id"] == "gradle_built")
    assert built["ok"] is False and built["fix"] == "gradle_build"
    # one Undo takes the whole [robot_program] away again
    s, b = c.post("/api/robot/undo")
    assert s == 200 and b["ok"] is True and cfg.read_text() == before
    # pointing again at a moved project (relative to the working directory) writes the new folder
    s, b = c.call("POST", "/api/robot/code", {"folder": "code/5098-2026"})
    assert s == 200 and _raw(cfg)["robot_program"]["cwd"] == "{code}/5098-2026"


def test_code_not_a_project_writes_nothing(tmp_path, start):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("hi")
    cfg = _robot(tmp_path)
    before = cfg.read_text()
    c = start(cfg)
    s, b = c.call("POST", "/api/robot/code", {"folder": "notes"})
    assert s == 400 and b["ok"] is False
    assert b["errors"] == [{"field": "status", "message": "nothing here scrimmage can start: no build.gradle or "
                                                          "robot.py"}]
    s, b = c.call("POST", "/api/robot/code", {"folder": "gone"})
    assert s == 400 and "not a folder" in b["error"]
    assert cfg.read_text() == before and c.get("/api/robot/state")[1]["can_undo"] is False
    # a config whose program folder was moved away says so; nothing is detected
    cfg.write_text(before + '\n[robot_program]\ncommand = ["./gradlew"]\ncwd = "moved"\n')
    b = c.get("/api/robot/code")[1]
    assert b["info"] is None and b["checks"] == [] and "moved" in b["problem"]


def test_fix_progress_and_failure(tmp_path, start, monkeypatch):
    proj = _java_project(tmp_path / "proj")
    cfg = _robot(tmp_path)
    go = threading.Event()
    seen = []

    def fake_fix(fix, info, out, cancel=None):
        seen.append((fix, info.folder))
        out("Downloading gradle")
        go.wait(10)
        out("BUILD FAILED")
        return Check("gradle_built", False, "The robot code didn't build.", fix="gradle_build", detail="BUILD FAILED")
    monkeypatch.setattr(robotcode, "run_fix", fake_fix)
    monkeypatch.setattr(robotcode, "checklist", lambda info: [])
    c = start(cfg)
    s, b = c.call("POST", "/api/robot/code/fix", {"fix": "gradle_build"})
    assert s == 400   # no robot code picked yet
    assert c.call("POST", "/api/robot/code", {"folder": str(proj)})[0] == 200
    s, b = c.call("POST", "/api/robot/code/fix", {"fix": "make_coffee"})
    assert s == 400
    s, b = c.call("POST", "/api/robot/code/fix", {"fix": "gradle_build"})
    assert s == 200 and b["ok"] is True and isinstance(b["id"], int)
    fid = b["id"]
    s, p = c.get("/api/robot/code/fix")
    for _ in range(50):
        if p["lines"]:
            break
        p = c.get(f"/api/robot/code/fix?since={p['version']}")[1]
    assert p["running"] is True and p["lines"] == ["Downloading gradle"] and p["result"] is None and p["id"] == fid
    # one at a time
    assert c.call("POST", "/api/robot/code/fix", {"fix": "build_agent"})[0] == 409
    go.set()
    t0 = time.monotonic()
    while p["running"] and time.monotonic() - t0 < 10:
        p = c.get(f"/api/robot/code/fix?since={p['version']}")[1]
    assert p["running"] is False and p["lines"] == ["Downloading gradle", "BUILD FAILED"]
    assert p["result"] == {"id": "gradle_built", "ok": False, "message": "The robot code didn't build.",
                           "fix": "gradle_build", "detail": "BUILD FAILED"}
    assert seen == [("gradle_build", proj)]
    # a long poll with the current version waits; a newer fix starts over
    v = p["version"]
    t0 = time.monotonic()
    threading.Timer(0.5, lambda: c.call("POST", "/api/robot/code/fix", {"fix": "gradle_build"})).start()
    q = c.get(f"/api/robot/code/fix?since={v}")[1]
    assert time.monotonic() - t0 >= 0.4 and q["version"] != v and q["id"] == fid + 1


def test_fix_stops_when_the_editor_closes(tmp_path, start, monkeypatch):
    proj = _java_project(tmp_path / "proj")
    gradlew = proj / "gradlew"
    gradlew.write_text("#!/bin/sh\necho started\nsleep 60\n")
    gradlew.chmod(0o755)
    cfg = _robot(tmp_path)
    monkeypatch.setattr(robotcode, "checklist", lambda info: [])
    c = start(cfg)
    assert c.call("POST", "/api/robot/code", {"folder": str(proj)})[0] == 200
    assert c.call("POST", "/api/robot/code/fix", {"fix": "gradle_build"})[0] == 200
    p = c.get("/api/robot/code/fix")[1]
    while not p["lines"]:
        p = c.get(f"/api/robot/code/fix?since={p['version']}")[1]
    t0 = time.monotonic()
    c.app.stop()
    assert time.monotonic() - t0 < 10
    st = c.app.fix_state()
    assert st["running"] is False and st["result"]["ok"] is False and "stopped" in st["result"]["message"]


def _built(proj: Path) -> None:
    (proj / "build" / "libs").mkdir(parents=True, exist_ok=True)
    (proj / "build" / "libs" / "robot.jar").write_bytes(b"")


def test_start_gate_refuses_drive_and_run(tmp_path, start):
    proj = _java_project(tmp_path / "proj")
    _built(proj)
    cfg = _robot(tmp_path)
    c = start(cfg)
    assert c.call("POST", "/api/robot/code", {"folder": str(proj)})[0] == 200
    b = c.get("/api/robot/code")[1]
    assert b["start"]["picked"] is False and b["start"]["pose"] is None and b["start"]["sentence"] == "Not picked yet"
    info = c.get("/api/live/info")[1]
    assert info["start_picked"] is False
    assert c.call("POST", "/api/live/start", {}) == (200, {"ok": False, "errors": [UNPICKED]})
    assert c.app.live.status()["state"] == "idle"
    # a scenario on the same robot: Run asks too
    s, body = c.call("POST", "/api/app/new", {"kind": "scenario", "path": "drive.toml", "robot": "bot.toml"})
    assert s == 200, body
    assert c.post("/api/scenario/run", {}) == (200, {"ok": False, "errors": [UNPICKED]})
    assert c.app.runs.runs()["runs"] == []
    # a scenario that places the robot itself ([range] start) counts as picked
    sc = tmp_path / "drive.toml"
    assert not c.app.robot.read_only_reason
    c.app.robot.path.write_text(cfg.read_text() + '\n[range]\nfile = "2026"\n')
    sc.write_text(sc.read_text() + '\n[range]\nstart = "blue_left"\n')
    c.get("/api/scenario/state")   # picks up the outside edits
    assert c.app._start_problem(for_run=True) is None
    # the CLI's loader keeps its default start for the same file
    from scrimmage.config import build_world, load_config
    assert build_world(load_config(cfg)).parts is not None


def test_unbuilt_java_code_is_refused_in_plain_words(tmp_path, start, monkeypatch):
    proj = _java_project(tmp_path / "proj")
    (proj / "build" / "tmp").mkdir(parents=True)        # what a failed first build leaves: no jar, no classes
    cfg = _robot(tmp_path)
    c = start(cfg)
    assert c.call("POST", "/api/robot/code", {"folder": str(proj)})[0] == 200
    built = next(ch for ch in c.get("/api/robot/code")[1]["checks"] if ch["id"] == "gradle_built")
    assert built["ok"] is False and built["fix"] == "gradle_build"
    # both page starts refuse before asking where the robot starts (no point picking a start for code that can't run)
    for route in ("/api/live/start", "/api/robot/check"):
        s, b = c.call("POST", route, {})
        assert s == 200 and b["ok"] is False and [e["field"] for e in b["errors"]] == ["start/not_built"], b
        assert "hasn't been built on this computer yet" in b["errors"][0]["message"]
        assert "Build now" in b["errors"][0]["message"]
    assert c.app.live.status()["state"] == "idle"
    # a Build now that fails: the checklist keeps saying so after a reload, even with an old jar from before, and
    # the starts say the last build failed
    _built(proj)
    old = time.time() - 60
    os.utime(proj / "build" / "libs" / "robot.jar", (old, old))
    go = threading.Event()

    def fake_fix(fix, info, out, cancel=None):
        go.wait(10)
        return Check("gradle_built", False, "The robot code didn't build.", fix="gradle_build", detail="BUILD FAILED")
    monkeypatch.setattr(robotcode, "run_fix", fake_fix)
    assert c.call("POST", "/api/robot/code/fix", {"fix": "gradle_build"})[0] == 200
    s, b = c.call("POST", "/api/live/start", {})     # while it builds
    assert b["errors"][0]["field"] == "start/not_built" and "still building" in b["errors"][0]["message"]
    go.set()
    p = c.get("/api/robot/code/fix")[1]
    while p["running"]:
        p = c.get(f"/api/robot/code/fix?since={p['version']}")[1]
    built = next(ch for ch in c.get("/api/robot/code")[1]["checks"] if ch["id"] == "gradle_built")
    assert built == {"id": "gradle_built", "ok": False, "message": "The robot code didn't build.",
                     "fix": "gradle_build", "detail": "BUILD FAILED"}
    s, b = c.call("POST", "/api/robot/check", {})
    assert b["errors"][0]["field"] == "start/not_built" and "didn't build the last time" in b["errors"][0]["message"]
    # it built since (in VS Code, say): ready again, and the start goes on to ask where the robot starts
    (proj / "build" / "libs" / "robot.jar").touch()
    assert next(ch for ch in c.get("/api/robot/code")[1]["checks"] if ch["id"] == "gradle_built")["ok"] is True
    assert c.call("POST", "/api/live/start", {}) == (200, {"ok": False, "errors": [UNPICKED]})


def test_scenario_run_refuses_unbuilt_java_code(tmp_path, start):
    proj = _java_project(tmp_path / "proj")
    cfg = _robot(tmp_path)
    c = start(cfg)
    assert c.call("POST", "/api/robot/code", {"folder": str(proj)})[0] == 200
    s, body = c.call("POST", "/api/app/new", {"kind": "scenario", "path": "drive.toml", "robot": "bot.toml"})
    assert s == 200, body
    # like the page's other starts, a Run refuses before asking where the robot starts
    s, b = c.post("/api/scenario/run", {})
    assert s == 200 and b["ok"] is False and [e["field"] for e in b["errors"]] == ["start/not_built"], b
    assert "Build now" in b["errors"][0]["message"]
    assert c.app.runs.runs()["runs"] == []
    _built(proj)
    assert c.post("/api/scenario/run", {}) == (200, {"ok": False, "errors": [UNPICKED]})


def test_existing_range_start_counts_as_picked(tmp_path, start):
    cfg = tmp_path / "climber.toml"
    cfg.write_text((ROOT / "configs" / "fastmode_range_phoenix.toml").read_text())
    c = start(cfg)
    st = c.get("/api/robot/code")[1]["start"]
    assert st["picked"] is True and st["range"] == "2026" and st["named"] == "blue_center"
    assert st["pose"] == [3.55, 4.035, 0.0] and "blue_center" in st["named_starts"]
    assert st["sentence"] == "Starts on the blue side, facing the red wall"
    assert c.get("/api/live/info")[1]["start_picked"] is True


def test_start_named_and_free_pose_exclusive(tmp_path, start):
    cfg = _robot(tmp_path)
    c = start(cfg)
    # the 2026 field and one of its named starts
    s, b = c.post("/api/robot/edit", {"op": "start", "named": "blue_left", "range": "2026"})
    assert s == 200 and b["ok"] is True, b
    raw = _raw(cfg)
    assert raw["range"] == {"file": "2026", "start": "blue_left"} and "start_pose" not in raw.get("robot", {})
    st = c.get("/api/robot/code")[1]["start"]
    assert st == {"picked": True, "pose": [3.42, 5.56, 0.0], "range": "2026", "named": "blue_left",
                  "named_starts": ["blue_left", "blue_center", "blue_right"], "floor_balls": True,
                  "fields": ["2026", None], "range_read_only": False,
                  "sentence": "Starts on the blue side, facing the red wall"}
    # a free placement replaces the named start; the field stays
    s, b = c.post("/api/robot/edit", {"op": "start", "pose": [12.0, 2.0, 180.0], "range": "2026",
                                      "floor_balls": False})
    assert s == 200 and b["ok"] is True, b
    raw = _raw(cfg)
    assert raw["robot"]["start_pose"] == [12.0, 2.0, 180.0]
    assert raw["range"] == {"file": "2026", "floor_balls": False}
    st = c.get("/api/robot/code")[1]["start"]
    assert st["named"] is None and st["pose"] == [12.0, 2.0, 180.0] and st["floor_balls"] is False
    assert st["sentence"] == "Starts on the red side, facing the blue wall"
    # the plain field: no [range]
    s, b = c.post("/api/robot/edit", {"op": "start", "pose": [4.0, 4.0, 90.0], "range": None})
    assert s == 200 and b["ok"] is True, b
    raw = _raw(cfg)
    assert "range" not in raw and raw["robot"]["start_pose"] == [4.0, 4.0, 90.0]
    st = c.get("/api/robot/code")[1]["start"]
    assert st["range"] is None and st["named_starts"] == [] and st["sentence"] == \
        "Starts on the blue side, facing a side wall"
    # a named start the field doesn't have, a field the picker doesn't offer, a spot off the field: refused
    for body, field in (({"named": "red_left", "range": "2026"}, "status"),
                        ({"pose": [1.0, 1.0, 0.0], "range": "2022-hangar"}, "start/range"),
                        ({"pose": [30.0, 1.0, 0.0], "range": None}, "start/pose"),
                        ({"named": "blue_left", "range": None}, "start/named")):
        s, b = c.post("/api/robot/edit", {"op": "start", **body})
        assert s == 200 and b["ok"] is False and b["errors"][0]["field"] == field, (body, b)
    # null clears the start
    s, b = c.post("/api/robot/edit", {"op": "start", "pose": None, "range": None})
    assert s == 200 and b["ok"] is True
    assert "start_pose" not in _raw(cfg).get("robot", {})
    assert c.get("/api/robot/code")[1]["start"]["picked"] is False


def test_changing_the_field_never_leaves_a_named_start_behind(tmp_path, start):
    """The picker's field choice is the op `start {range}` alone: a named start belongs to its range, so changing
    the field (2026 -> plain, plain -> 2026, another range -> 2026) takes it away and the start is unpicked."""
    cfg = _robot(tmp_path, extra='\n[range]\nfile = "2026"\nstart = "blue_center"\n')
    c = start(cfg)
    assert c.get("/api/robot/code")[1]["start"]["named"] == "blue_center"
    for rng in (None, "2026", None):
        s, b = c.post("/api/robot/edit", {"op": "start", "range": rng})
        assert s == 200 and b["ok"] is True, b
        raw = _raw(cfg)
        assert raw.get("range", {}).get("start") is None, (rng, raw.get("range"))
        assert raw.get("range") == ({"file": "2026"} if rng else None)
        st = c.get("/api/robot/code")[1]["start"]
        assert st["named"] is None and st["picked"] is False and st["sentence"] == "Not picked yet"
    # another range's named start goes when the picker moves the robot to 2026
    cfg2 = tmp_path / "climber.toml"
    cfg2.write_text((ROOT / "configs" / "climber_robot.toml").read_text())
    c2 = start(cfg2)
    s, b = c2.post("/api/robot/edit", {"op": "start", "range": "2026"})
    assert s == 200 and b["ok"] is True, b
    assert _raw(cfg2)["range"] == {"file": "2026", "floor_balls": False}
    # a free placement made on one field is kept when the field changes (it is a spot on the field, not a name)
    s, b = c.post("/api/robot/edit", {"op": "start", "pose": [3.0, 3.0, 0.0], "range": "2026"})
    assert s == 200 and b["ok"] is True, b
    s, b = c.post("/api/robot/edit", {"op": "start", "range": None})
    assert s == 200 and b["ok"] is True, b
    assert "range" not in _raw(cfg) and _raw(cfg)["robot"]["start_pose"] == [3.0, 3.0, 0.0]


def test_other_range_shown_read_only(tmp_path, start):
    cfg = _robot(tmp_path, extra='\n[range]\nfile = "2022-hangar"\n')
    c = start(cfg)
    st = c.get("/api/robot/code")[1]["start"]
    assert st["range"] == "2022-hangar" and st["range_read_only"] is True and st["picked"] is False
    # placing the robot without naming a field keeps that range
    s, b = c.post("/api/robot/edit", {"op": "start", "pose": [3.0, 3.0, 0.0]})
    assert s == 200 and b["ok"] is True, b
    assert _raw(cfg)["range"] == {"file": "2022-hangar"}


def test_templates_have_no_start_pose(tmp_path, start):
    for kind in templates.KINDS:
        raw = tomllib.loads(templates.new_config(kind))
        assert "start_pose" not in raw.get("robot", {}), kind
        assert "start" not in raw.get("range", {}), kind
    c = start()
    assert c.call("POST", "/api/app/new", {"kind": "robot", "path": "bot", "template": "swerve"})[1]["ok"]
    assert c.get("/api/robot/code")[1]["start"]["picked"] is False


def test_open_robot_with_unset_folder_asks(tmp_path, start):
    cfg = tmp_path / "bot.toml"
    cfg.write_text(templates.new_config("swerve") + '\n[robot_program]\ncommand = ["./gradlew"]\n'
                   'cwd = "{code}/5098-2026"\n')
    c = start()
    assert c.get("/api/app/state")[1]["needs_folder"] is None
    assert c.call("POST", "/api/app/open", {"path": "bot.toml"}) == (200, {"ok": True})
    st = c.get("/api/app/state")[1]
    assert st["needs_folder"] == "code"
    r = c.get("/api/robot/state")[1]
    assert r["read_only"] is True
    assert "code folder" in r["read_only_reason"] and "SCRIMMAGE_CODE" in r["read_only_reason"]
    assert "Traceback" not in r["read_only_reason"]
    # setting it on the start page clears the question and the robot can be edited again
    (tmp_path / "code").mkdir()
    v = r["version"]
    assert c.call("POST", "/api/app/folders", {"name": "code", "path": "code"}) == (200, {"ok": True})
    assert c.get("/api/app/state")[1]["needs_folder"] is None
    r = c.get(f"/api/robot/state?since={v}")[1]
    assert r["read_only"] is False and r["version"] > v
    assert c.get("/api/robot/code")[1]["cwd"] == "{code}/5098-2026"


def test_scenario_naming_unset_folder_asks(tmp_path, start):
    (tmp_path / "cad").mkdir()
    _robot(tmp_path / "cad")
    sc = tmp_path / "drive.toml"
    sc.write_text('config = "{cad}/bot.toml"\n[[step]]\nmode = "teleop"\nduration = 1\n')
    c = start(sc)
    assert c.get("/api/app/state")[1]["needs_folder"] == "cad"
    r = c.get("/api/robot/state")[1]
    assert r["read_only"] is True and "CAD folder" in r["read_only_reason"]
    assert c.call("POST", "/api/app/folders", {"name": "cad", "path": "cad"}) == (200, {"ok": True})
    r = c.get("/api/robot/state")[1]
    assert r["read_only"] is False and r["path"] == str(tmp_path / "cad" / "bot.toml")


def test_paths_written_through_the_page_use_team_folders(tmp_path, start):
    cad = tmp_path / "cad"
    (cad / "robots").mkdir(parents=True)
    teamfolders.set_folder("cad", cad)
    cfg = cad / "robots" / "bot.toml"
    cfg.write_text(templates.new_config("swerve"))
    c = start(cfg)
    # a new scenario elsewhere names its robot in the CAD folder
    s, b = c.call("POST", "/api/app/new", {"kind": "scenario", "path": "drive.toml", "robot": str(cfg)})
    assert s == 200, b
    assert _raw(tmp_path / "drive.toml")["config"] == "{cad}/robots/bot.toml"


def test_part_path_in_a_team_folder_is_written_with_it(tmp_path, start):
    import shutil
    cad = tmp_path / "cad"
    shutil.copytree(ROOT / "tests" / "parts" / "intake", cad / "intake")
    shutil.copytree(ROOT / "tests" / "parts" / "intake", tmp_path / "robots" / "parts" / "near")
    teamfolders.set_folder("cad", cad)
    cfg = _robot(tmp_path / "robots")
    c = start(cfg)
    s, b = c.post("/api/robot/edit", {"op": "add", "name": "intake", "path": str(cad / "intake"),
                                      "attach": "drive.center"})
    assert s == 200 and b["ok"] is True, b
    s, b = c.post("/api/robot/edit", {"op": "add", "name": "near", "path": "parts/near", "attach": "drive.center"})
    assert s == 200 and b["ok"] is True, b
    paths = {p["name"]: p.get("path") for p in _raw(cfg)["part"]}
    assert paths["intake"] == "{cad}/intake" and paths["near"] == "parts/near"


def test_battery_edit(tmp_path, start):
    cfg = _robot(tmp_path)
    c = start(cfg)
    b = c.get("/api/robot/code")[1]["battery"]
    assert b == {"nominal_voltage": 12.5, "resistance": 0.02,
                 "defaults": {"nominal_voltage": 12.0, "resistance": 0.02}}
    s, r = c.post("/api/robot/edit", {"op": "battery", "nominal_voltage": 12.8, "resistance": 0.015})
    assert s == 200 and r["ok"] is True, r
    text = cfg.read_text()
    assert "[battery]\nnominal_voltage = 12.8\nresistance = 0.015\n" in text
    assert c.get("/api/robot/code")[1]["battery"]["nominal_voltage"] == 12.8
    # null goes back to the default; a bad value is refused with its field
    s, r = c.post("/api/robot/edit", {"op": "battery", "resistance": None})
    assert s == 200 and r["ok"] is True and "resistance" not in _raw(cfg)["battery"]
    assert c.get("/api/robot/code")[1]["battery"]["resistance"] is None
    for body, field in (({"nominal_voltage": -1}, "battery/nominal_voltage"),
                        ({"resistance": "lots"}, "battery/resistance")):
        s, r = c.post("/api/robot/edit", {"op": "battery", **body})
        assert s == 200 and r["ok"] is False and r["errors"][0]["field"] == field
    assert c.post("/api/robot/undo")[1]["ok"] is True
    assert _raw(cfg)["battery"]["resistance"] == 0.015


# ---- the device check: rows from the running robot, applying matches, brake/coast and invert ----------------------

TESTS = ROOT / "tests"
PICKED = "[robot]\nstart_pose = [2.0, 4.0, 0.0]\n"


def _fake_program(tmp_path, *flags) -> Path:
    """The swerve template, started, run by tests/fake_robot_main.py (--phoenix: it makes Talon FX 1, nothing else)."""
    cmd = json.dumps(["{python}", "fake_robot_main.py", "--lockstep", *flags])
    cfg = _robot(tmp_path, extra=f"\n[robot_program]\ncommand = {cmd}\ncwd = {json.dumps(str(TESTS))}\n"
                                 "start_timeout = 15\n")
    cfg.write_text(cfg.read_text().replace("[robot]\n", PICKED, 1))
    return cfg


def _devices_until(c, pred, timeout=30.0, what="the device rows"):
    since, end = None, time.monotonic() + timeout
    while True:
        assert time.monotonic() < end, f"timed out waiting for {what}"
        path = "/api/robot/devices" + (f"?since={since}" if since is not None else "")
        s, b = c.get(path)
        assert s == 200 and set(b) == {"version", "live", "robot_time", "settled", "rows", "restart",
                                       "restart_problem"}, b
        if pred(b):
            return b
        since = b["version"]


def _row(b, slot):
    return next(r for r in b["rows"] if r["slot"] == slot)


@pytest.fixture
def quick_nt(monkeypatch):
    import scrimmage.session as session_mod
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)   # the fake robot serves no NetworkTables


def test_devices_rows_from_live_session(tmp_path, start, quick_nt):
    cfg = _fake_program(tmp_path, "--phoenix")
    c = start(cfg)
    b = c.get("/api/robot/devices")[1]
    assert b["live"] is False and b["rows"] == [] and b["settled"] is False
    assert c.call("POST", "/api/robot/check", {}) == (200, {"ok": True})
    # the check runs the robot disabled; the code made Talon FX 1 (fl.drive), so that row is ok at once, and the rest
    # wait until the robot has run MISSING_AFTER seconds before they count as missing
    b = _devices_until(c, lambda b: any(r["slot"] == "fl.drive" and r["status"] == "ok" for r in b["rows"]))
    assert b["live"] is True and set(_row(b, "fl.drive")) == {"slot", "kind", "expected", "made", "status",
                                                             "suggest", "note"}
    assert _row(b, "fl.drive")["expected"] == "TalonFX 1"
    assert c.app.live.status()["enabled"] is False and c.app.live.status()["mode"] == "disabled"
    b = _devices_until(c, lambda b: b["settled"])
    assert b["robot_time"] >= 3.0 and _row(b, "fl.steer")["status"] == "missing"
    assert _row(b, "fl.steer")["expected"] == "TalonFX 2"
    # it keeps running: a second check (or a Drive start) is refused while it's live
    assert c.call("POST", "/api/robot/check", {})[0] == 409
    assert c.call("POST", "/api/live/start", {})[0] == 409
    # applying matches restarts the running robot on the new IDs: fl.steer is now the Talon FX 1 the code made
    s, r = c.post("/api/robot/edit", {"op": "match_devices", "matches": {"fl.drive": "TalonFX 2",
                                                                         "fl.steer": "TalonFX 1"}})
    assert s == 200 and r["ok"] is True and r["restarting"] is True, r
    # the restart runs on its own thread: the devices say "restarting" until the new session has started
    b = _devices_until(c, lambda b: b["restart"] == "restarted", what="the restart")
    assert b["restart_problem"] is None
    b = _devices_until(c, lambda b: b["live"] and any(r["slot"] == "fl.steer" and r["status"] == "ok"
                                                      for r in b["rows"]), what="the restarted robot's rows")
    assert _row(b, "fl.drive")["status"] in ("waiting", "missing")
    c.call("POST", "/api/live/stop", {})
    b = _devices_until(c, lambda b: not b["live"], what="the session to end")
    assert b["rows"]    # the last rows stay for the page to show


def test_restart_that_fails_is_reported(tmp_path, start, quick_nt):
    cfg = _fake_program(tmp_path, "--phoenix")
    c = start(cfg)
    assert c.call("POST", "/api/robot/check", {}) == (200, {"ok": True})
    _devices_until(c, lambda b: b["live"] and b["rows"])
    real = c.app.live.start

    def refuse(*a, restarting=False, **k):
        if restarting:
            raise LiveError("a scenario run is in progress")
        return real(*a, **k)
    c.app.live.start = refuse
    s, r = c.post("/api/robot/edit", {"op": "match_devices", "matches": {"fl.drive": "TalonFX 2", "fl.steer": "TalonFX 1"}})
    assert s == 200 and r["restarting"] is True, r
    b = _devices_until(c, lambda b: b["restart"] == "failed", what="the failed restart")
    assert b["live"] is False and b["restart_problem"] == "a scenario run is in progress"
    # a start of its own forgets it
    c.app.live.start = real
    assert c.call("POST", "/api/robot/check", {}) == (200, {"ok": True})
    b = _devices_until(c, lambda b: b["live"])
    assert b["restart"] is None
    c.call("POST", "/api/live/stop", {})
    _devices_until(c, lambda b: not b["live"], what="the session to end")


def test_apply_swap_is_one_edit(tmp_path, start):
    cfg = _robot(tmp_path)
    before = cfg.read_text()
    c = start(cfg)
    # one ID at a time would pass through two motors on TalonFX 2: the loader refuses that
    s, r = c.post("/api/robot/edit", {"op": "match_devices", "matches": {"fl.drive": "TalonFX 2"}})
    assert s == 200 and r["ok"] is False and cfg.read_text() == before, r
    # both at once: one write, one undo step, no duplicate-ID error in between
    v = c.app.robot_version
    s, r = c.post("/api/robot/edit", {"op": "match_devices", "matches": {"fl.drive": "TalonFX 2",
                                                                         "fl.steer": "TalonFX 1"}})
    assert s == 200 and r["ok"] is True and r["restarting"] is False, r
    assert r["version"] == v + 1
    after = cfg.read_text()
    assert after == before.replace('drive = "TalonFX:1", steer = "TalonFX:2"',
                                   'drive = "TalonFX:2", steer = "TalonFX:1"')
    s, r = c.post("/api/robot/undo")
    assert s == 200 and r["ok"] is True and cfg.read_text() == before
    # other device forms: a fast-mode name, and "Kind:N"; a device of another type is refused with the slot's field
    s, r = c.post("/api/robot/edit", {"op": "match_devices", "matches": {"fl.encoder": "CANcoder:20"}})
    assert s == 200 and r["ok"] is True and _raw(cfg)["part"][1]["ids"]["encoder"] == "CANcoder:20"
    s, r = c.post("/api/robot/edit", {"op": "match_devices", "matches": {"fl.steer": "SPARK MAX 2"}})
    assert s == 200 and r["ok"] is False and r["errors"][0]["field"] == "devices/fl.steer", r
    s, r = c.post("/api/robot/edit", {"op": "match_devices", "matches": {"zz.drive": "TalonFX 30"}})
    assert s == 200 and r["ok"] is False and r["errors"][0]["field"] == "devices/zz.drive", r
    s, r = c.post("/api/robot/edit", {"op": "match_devices", "matches": {}})
    assert s == 200 and r["ok"] is False and r["errors"][0]["field"] == "devices/matches", r


def test_motor_neutral_and_invert_written(tmp_path, start):
    cfg = _robot(tmp_path)
    cfg.write_text(cfg.read_text().replace(
        'ids = { drive = "TalonFX:3", steer = "TalonFX:4", encoder = "CANcoder:10" }',
        'ids = { drive = { ctre = "TalonFX:3", neutral = "coast" }, steer = "TalonFX:4", encoder = "CANcoder:10" }'
        '   # the right front'))
    before = cfg.read_text()
    c = start(cfg)
    # a plain ID becomes a table with the settings, as hand-written configs write it
    s, r = c.post("/api/robot/edit", {"op": "motor", "slot": "fl.drive", "neutral": "brake", "invert": True})
    assert s == 200 and r["ok"] is True, r
    assert ('ids = { drive = { ctre = "TalonFX:1", neutral = "brake", invert = true }, steer = "TalonFX:2", '
            'encoder = "CANcoder:9" }') in cfg.read_text()
    # an existing table is edited in place; the comment after it stays
    s, r = c.post("/api/robot/edit", {"op": "motor", "slot": "fr.drive", "neutral": "brake"})
    assert s == 200 and r["ok"] is True, r
    assert ('ids = { drive = { ctre = "TalonFX:3", neutral = "brake" }, steer = "TalonFX:4", encoder = "CANcoder:10" }'
            '   # the right front') in cfg.read_text()
    world = c.app.result.world
    motors = {m.name: m for m in world.motors}
    assert motors["fl.drive"].brake is True and motors["fr.drive"].brake is True
    assert motors["fl.steer"].brake is False       # a TalonFX coasts by default
    # back to the defaults: the plain ID again, so the file reads as it did
    s, r = c.post("/api/robot/edit", {"op": "motor", "slot": "fl.drive", "neutral": None, "invert": False})
    assert s == 200 and r["ok"] is True, r
    s, r = c.post("/api/robot/edit", {"op": "motor", "slot": "fr.drive", "neutral": "coast"})
    assert s == 200 and r["ok"] is True and cfg.read_text() == before, r
    # refused: a bad neutral, a bad invert, an encoder slot, an unknown slot
    for body in ({"slot": "fl.drive", "neutral": "stop"}, {"slot": "fl.drive", "invert": "yes"},
                 {"slot": "fl.encoder", "neutral": "brake"}, {"slot": "fl.nope", "neutral": "brake"}):
        s, r = c.post("/api/robot/edit", {"op": "motor", **body})
        assert s == 200 and r["ok"] is False and r["errors"][0]["field"] == f"motor/{body['slot']}", (body, r)
    assert cfg.read_text() == before


def test_check_refused_while_unpicked(tmp_path, start):
    cfg = _fake_program(tmp_path, "--phoenix")
    cfg.write_text(cfg.read_text().replace(PICKED, "[robot]\n"))
    c = start(cfg)
    assert c.call("POST", "/api/robot/check", {}) == (200, {"ok": False, "errors": [UNPICKED]})
    assert c.app.live.status()["state"] == "idle" and c.get("/api/robot/devices")[1]["live"] is False
