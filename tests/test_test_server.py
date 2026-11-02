"""The Test tab's server side: a block test (`<name>.test.toml`) behind /api/test/*."""

import threading
import time
from pathlib import Path

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
    # the arm's one hinge motor: move it; no bars on this field, so no drive first
    assert st["test"]["blocks"] == [{"do": "move", "joint": "arm.pivot", "to": 45.0, "unit": "deg"}]
    assert st["problems"] == [] and st["test"]["hooks"] == []
    s, b = c.call("POST", "/api/test/new", {"robot": str(robot)})
    assert s == 200, b
    assert (tmp_path / "robot-2.test.toml").is_file()
    assert c.get("/api/app/state")[1]["test"] == str((tmp_path / "robot-2.test.toml").resolve())


LADDER = """
[range]
file = "ladder"
start = "below"
"""
ROLLER = """
[[part]]
name = "roller"
stock = "roller"
attach = "drive.top"
ids = { spin = "PWM:3" }
"""
HOOK_PART = """\
[part]
name = "grabber"
format = 1

[[body]]
name = "base"
mass = 0.5
geom = [{ box = [0.05, 0.05, 0.05] }]

[[body]]
name = "hook_A"
parent = "base"
pos = [0.0, 0.0, 0.2]
mass = 0.2
geom = [{ box = [0.02, 0.02, 0.05] }]
"""
GRABBER = """
[[part]]
name = "grab"
path = "grabber"
attach = "drive.top"
"""


def _new_blocks(tmp_path, test_app, extra: str) -> dict:
    robot = tmp_path / "robot.toml"
    robot.write_text(new_config("standin") + extra, encoding="utf-8")
    c = test_app(robot)
    s, b = c.call("POST", "/api/test/new", {})
    assert s == 200, b
    st = _state(c)
    assert st["problems"] == [], st["problems"]
    return st["test"]


def test_a_new_test_on_the_stand_in_by_bars_drives_first(tmp_path, test_app):
    """A drivetrain and bars on the field: drive up to them first, then the mechanism."""
    test = _new_blocks(tmp_path, test_app, ARM + LADDER)
    assert test["blocks"] == [{"do": "drive", "distance": 0.3},
                              {"do": "move", "joint": "arm.pivot", "to": 45.0, "unit": "deg"}]


def test_a_new_test_spins_the_first_motor_when_there_are_several(tmp_path, test_app):
    test = _new_blocks(tmp_path, test_app, ARM + ROLLER + LADDER)
    assert test["blocks"] == [{"do": "drive", "distance": 0.3}, {"do": "spin", "motor": "arm.pivot", "volts": 6.0},
                              {"do": "wait", "seconds": 2.0}, {"do": "stop", "motor": "all"}]


def test_a_new_test_without_bars_has_the_motor_lines_only(tmp_path, test_app):
    test = _new_blocks(tmp_path, test_app, ARM + ROLLER)
    assert test["blocks"] == [{"do": "spin", "motor": "arm.pivot", "volts": 6.0}, {"do": "wait", "seconds": 2.0},
                              {"do": "stop", "motor": "all"}]


def test_a_new_test_preselects_the_bodies_named_hook(tmp_path, test_app):
    (tmp_path / "grabber").mkdir()
    (tmp_path / "grabber" / "part.toml").write_text(HOOK_PART, encoding="utf-8")
    test = _new_blocks(tmp_path, test_app, GRABBER)
    assert test["hooks"] == ["grab.hook_A"]
    assert test["blocks"] == [{"do": "wait", "seconds": 1.0}]      # nothing to move: the stand-in has no bars


def test_the_stand_in_for_parts_from_one_import_puts_them_where_the_cad_has_them():
    """Two parts from one import: the robot carries the first (on the chassis's root body at zero offset), the second
    hangs on the first's body `arm` at minus that body's origin; a hook-named body puts the stand-in by the ladder."""
    from scrimmage.assembler.test_session import standin_config
    base = {"import_id": "x", "part": "lift", "origins": {"frame": [0.0, 0.0, 0.0], "arm": [0.1, 0.0, 0.5]},
            "attach": None}
    hook = {"import_id": "x", "part": "hook", "origins": {"hook_body": [0.1, 0.0, 0.9]}, "attach": "lift.arm"}
    text = standin_config([("parts/hook", hook), ("parts/lift", base)], "chassis")
    import tomllib
    raw = tomllib.loads(text)
    parts = {p["name"]: p for p in raw["part"]}
    assert [p["name"] for p in raw["part"]] == ["drive", "dt", "lift", "hook"]
    assert parts["lift"] == {"name": "lift", "path": "parts/lift", "attach": "drive.chassis"}
    assert parts["hook"]["attach"] == "lift.arm" and parts["hook"]["pos"] == [-0.1, 0.0, -0.5]
    assert raw["range"] == {"file": "ladder", "start": "below"}
    no_hooks = tomllib.loads(standin_config([("parts/lift", base)], "chassis"))
    assert "range" not in no_hooks and no_hooks["part"][0]["mass"] == 45.0


def test_test_it_again_adds_the_imports_new_parts_to_the_stand_in_and_never_removes_one():
    """The import now makes a second part that hangs on the first: it is added on the first's instance (whatever the
    student renamed it), where the CAD has it; the student's own edits and parts stay."""
    import tomllib

    from scrimmage.assembler.test_session import add_to_standin, standin_config
    base = {"import_id": "x", "part": "lift", "origins": {"frame": [0.0, 0.0, 0.0], "arm": [0.1, 0.0, 0.5]},
            "attach": None}
    hook = {"import_id": "x", "part": "hook", "origins": {"tip": [0.1, 0.0, 0.9]}, "attach": "lift.arm"}
    text = standin_config([("parts/lift", base)], "chassis").replace('name = "lift"', 'name = "my_lift"')
    text = text.replace("mass = 45.0", "mass = 30.0")
    text += '\n[[part]]\nname = "extra"\nstock = "arm"\nattach = "drive.top"\n'
    new, added = add_to_standin(text, [("parts/lift", base), ("parts/hook", hook)], "chassis")
    assert added == ["hook"]
    parts = {p["name"]: p for p in tomllib.loads(new)["part"]}
    assert set(parts) == {"drive", "dt", "my_lift", "extra", "hook"}
    assert parts["hook"]["attach"] == "my_lift.arm" and parts["hook"]["pos"] == [-0.1, 0.0, -0.5]
    assert parts["drive"]["mass"] == 30.0
    assert add_to_standin(new, [("parts/lift", base), ("parts/hook", hook)], "chassis") == (new, [])


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


def test_state_reads_the_kept_runs_only_when_they_change(tmp_path, test_app, monkeypatch):
    """The page polls the state four times a second while a run goes: the kept runs' files are read again only when
    the runs folder changes (a run kept, one pruned), not on every poll."""
    from scrimmage.blocks import store
    t = write_test(tmp_path)
    c = test_app(t)
    reads = []
    real = store.list_runs
    monkeypatch.setattr(store, "list_runs", lambda base: reads.append(base) or real(base))
    for _ in range(5):
        _state(c)
    assert len(reads) <= 1, reads
    s, b = c.call("POST", "/api/test/run", {})
    assert s == 200, b
    st = _wait_idle(c)
    assert len(st["runs"]) == 1
    n = len(reads)
    for _ in range(5):
        assert len(_state(c)["runs"]) == 1
    assert len(reads) == n
    # a run kept from outside (`scrimmage test run`, the MCP tool) shows
    (store.runs_dir(t) / "002").mkdir()
    (store.runs_dir(t) / "002" / "result.json").write_text('{"status": "finished", "started": ""}', encoding="utf-8")
    assert {r["id"] for r in _state(c)["runs"]} == {"001", "002"}


LONG = """
[[block]]
do = "wait"
seconds = 30.0
"""


def test_a_stopped_run_is_kept_marked_and_doesnt_push_runs_out(tmp_path, test_app):
    t = write_test(tmp_path, LONG)
    c = test_app(t)
    assert c.call("POST", "/api/test/run", {})[0] == 200
    end = time.monotonic() + 30
    while not (_state(c)["running"] or {}).get("progress"):
        assert time.monotonic() < end
        time.sleep(0.05)
    assert c.call("POST", "/api/test/cancel", {})[0] == 200
    st = _wait_idle(c)
    [run] = st["runs"]
    assert run["status"] == "cancelled" and run["cancelled"] and run["outcome"].startswith("you stopped the run")


WEDGE = '[part]\nname = "wedge"\nformat = 1\n[[body]]\nname = "b"\nmass = 1\ngeom = [{ mesh = "wedge.stl" }]\n'


def write_wedge(folder, size: float, mass: float = 1.0) -> None:
    from test_assembler_core import stl_bytes
    a, b, c, d = (0, 0, 0), (size, 0, 0), (0, size, 0), (0, 0, size)
    folder.mkdir(exist_ok=True)
    (folder / "part.toml").write_text(WEDGE.replace("mass = 1", f"mass = {mass}"), encoding="utf-8")
    (folder / "wedge.stl").write_bytes(stl_bytes([(a, c, b), (a, b, d), (a, d, c), (b, c, d)]))


def mesh_extent(view) -> float:
    import base64

    import numpy as np
    sizes = [np.ptp(np.frombuffer(base64.b64decode(m["vert"]), "<f4").reshape(-1, 3), axis=0).max()
             for m in view["meshes"]]
    return float(min(sizes))


def test_a_part_folder_changed_within_the_second_rebuilds_the_robot(tmp_path, test_app):
    """Import again rewrites a part's folder, not the robot config: the server sees the folder change, so the next run
    (and the palette) use the new part. MuJoCo caches a mesh file by its name and time to the second, so the folder
    here changes within the same second as before, and the run must still draw the new mesh."""
    import os
    t = write_test(tmp_path)
    robot = tmp_path / "robot.toml"
    robot.write_text(robot.read_text(encoding="utf-8")
                     + '\n[[part]]\nname = "w"\npath = "wedge"\nattach = "drive.top"\n', encoding="utf-8")
    part = tmp_path / "wedge"
    write_wedge(part, 0.1)
    c = test_app(t)
    assert _state(c)["problems"] == []
    assert c.call("POST", "/api/test/run", {})[0] == 200
    _wait_idle(c)
    stl = part / "wedge.stl"
    old = stl.stat().st_mtime_ns
    write_wedge(part, 0.2, mass=2.0)
    sec = old - old % 1_000_000_000
    same = sec + (old - sec + 500_000_000) % 1_000_000_000      # the same second, another moment in it
    os.utime(stl, ns=(same, same))
    assert c.call("POST", "/api/test/run", {})[0] == 200
    st = _wait_idle(c)
    assert st["run_error"] is None, st
    first, second = (c.get(f"/api/test/run/{i}/view")[1] for i in ("001", "002"))
    assert mesh_extent(second) == pytest.approx(2 * mesh_extent(first), rel=1e-3)


def test_a_part_given_as_its_file_next_to_the_robot_watches_only_what_it_reads(tmp_path, test_app):
    """A part can be its part.toml file (docs/parts.md), here next to the robot config, the test and its runs folder.
    Runs write only to the runs folder, so they aren't mistaken for Import again; the part's own mesh changing is."""
    t = write_test(tmp_path)
    robot = tmp_path / "robot.toml"
    robot.write_text(robot.read_text(encoding="utf-8")
                     + '\n[[part]]\nname = "w"\npath = "part.toml"\nattach = "drive.top"\n', encoding="utf-8")
    write_wedge(tmp_path, 0.1)
    c = test_app(t)
    assert _state(c)["problems"] == []
    version = c.get("/api/robot/state")[1]["version"]
    for _ in range(2):
        assert c.call("POST", "/api/test/run", {})[0] == 200
        assert _wait_idle(c)["run_error"] is None
        assert c.get("/api/robot/state")[1]["version"] == version
    (tmp_path / "notes.txt").write_text("not the part's", encoding="utf-8")
    assert c.get("/api/robot/state")[1]["version"] == version
    write_wedge(tmp_path, 0.2)                    # the part's mesh replaced: that is the robot changing
    assert c.get("/api/robot/state")[1]["version"] > version


CAD = Path(__file__).resolve().parent / "data" / "cad"


def _built_part(c, timeout=60.0):
    end = time.monotonic() + timeout
    s, st = c.get("/api/part/state")
    while not (st["build"]["state"] == "idle" and st["parts"] and st["parts"][0]["mass"]):
        assert time.monotonic() < end, st
        s, st = c.get(f"/api/part/state?since={st['version']}")
        assert s == 200, st
    return st


def test_test_it_from_the_part_tab_puts_the_part_on_a_stand_in_where_the_cad_has_it(tmp_path, test_app):
    import shutil
    import tomllib

    import mujoco
    from scrimmage.assembler import library
    from scrimmage.config import build_world, load_config
    step = tmp_path / "climber.step"
    shutil.copy(CAD / "climber.step", step)
    c = test_app(step)
    assert c.get("/api/app/state")[1]["mode"] == "part"
    _built_part(c)
    s, b = c.call("POST", "/api/test/new", {})       # an empty body never means Test it
    assert s == 400, b
    assert not (tmp_path / "climber-standin.toml").exists()
    s, b = c.call("POST", "/api/test/new", {"from_part": True})
    assert s == 200 and b["added"] == [], b
    standin, test = tmp_path / "climber-standin.toml", tmp_path / "climber.test.toml"
    app = c.get("/api/app/state")[1]
    assert app["mode"] == "robot" and app["test"] == str(test.resolve())     # the page opens on the Test tab
    raw = tomllib.loads(standin.read_text(encoding="utf-8"))
    [part] = [p for p in raw["part"] if p.get("path")]
    assert part == {"name": "climber", "path": "parts/climber", "attach": "drive.chassis"}
    assert classify(standin) == "robot" and classify(test) == "test"
    st = _state(c)
    assert st["problems"] == [] and st["test"]["robot"] == "climber-standin.toml" and st["test"]["blocks"]
    # each of the part's bodies sits where the CAD has it: its origin in the robot frame is the import's
    marker = library.cad_marker(tmp_path / "parts" / "climber")
    w = build_world(load_config(standin))
    mujoco.mj_forward(w.model, w.data)
    root = w.robot_body
    R = w.data.xmat[root].reshape(3, 3)
    for body, origin in marker["origins"].items():
        rel = R.T @ (w.data.xpos[w.model.body(f"climber.{body}").id] - w.data.xpos[root])
        assert rel == pytest.approx(origin, abs=1e-6), body
    # Test it again (back in the Part tab) opens the same test: nothing made twice, the student's edits kept
    standin.write_text(standin.read_text(encoding="utf-8").replace("mass = 45.0", "mass = 30.0"), encoding="utf-8")
    assert c.call("POST", "/api/app/open", {"path": str(step)})[0] == 200
    _built_part(c)
    s, b = c.call("POST", "/api/test/new", {"from_part": True})
    assert s == 200 and b["added"] == [], b
    assert "mass = 30.0" in standin.read_text(encoding="utf-8")
    assert not (tmp_path / "climber-standin-2.toml").exists() and not (tmp_path / "climber-2.test.toml").exists()
    assert c.get("/api/app/state")[1]["test"] == str(test.resolve())


def test_a_robot_edit_puts_the_stand_in_by_the_ladder_and_the_test_sees_its_bars(tmp_path, test_app):
    """Setup's field choice is the Robot tab's start edit with a ladder (undo works); the test's palette follows."""
    import tomllib
    t = write_test(tmp_path)
    c = test_app(t)
    assert _state(c)["palette"]["bars"] == []
    v = c.get("/api/robot/state")[1]["version"]
    s, b = c.call("POST", "/api/robot/edit", {"version": v, "op": "start", "range": "ladder", "named": "below"})
    assert s == 200 and b["ok"], b
    raw = tomllib.loads((tmp_path / "robot.toml").read_text(encoding="utf-8"))
    assert raw["range"] == {"file": "ladder", "start": "below"}
    assert c.get("/api/robot/state")[1]["range"] == "ladder"
    assert _state(c)["palette"]["bars"] == ["rung1", "rung2", "rung3", "rung4"]
    s, b = c.call("POST", "/api/robot/undo", {"version": c.get("/api/robot/state")[1]["version"]})
    assert s == 200 and b["ok"], b
    assert "range" not in tomllib.loads((tmp_path / "robot.toml").read_text(encoding="utf-8"))
    assert _state(c)["palette"]["bars"] == []
    s, b = c.call("POST", "/api/robot/edit", {"version": c.get("/api/robot/state")[1]["version"], "op": "start",
                                              "range": "somewhere"})
    assert s == 200 and not b["ok"] and b["errors"][0]["field"] == "start/range", b
