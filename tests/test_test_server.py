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


def test_sweep_values_may_be_material_names_for_a_grip(tmp_path, test_app):
    c = test_app(write_test(tmp_path))
    s, b = c.call("POST", "/api/test/sweep", {"target": {"block": [0], "key": "volts"}, "values": ["hdpe"]})
    assert s == 400 and "numbers" in b["error"], b
    s, b = c.call("POST", "/api/test/sweep", {"target": {"grip": "arm.pad"}, "values": ["hdpe", 0.8, None]})
    assert s == 400 and "material names or numbers" in b["error"], b
    s, b = c.call("POST", "/api/test/sweep", {"target": {"grip": "arm.pad"}, "values": ["hdpe", "nitrile"]})
    assert s == 200, b                # accepted; the worker finds arm is a stock part
    st = _wait_idle(c)
    assert "arm is a stock part" in (st["run_error"] or ""), st


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


def _part_edit(c, op, **args):
    v = c.get("/api/part/state")[1]["version"]
    s, b = c.call("POST", "/api/part/edit", {"version": v, "op": op, "args": args})
    assert s == 200 and b.get("ok", True), b


def test_test_it_gives_the_parts_motors_free_ids_so_the_starter_test_runs(tmp_path, test_app):
    """A part with a motor: Test it puts its slot on a PWM channel the stand-in's drive doesn't use, and the starter
    test (which moves that motor's joint) has no problems."""
    import shutil
    import tomllib
    step = tmp_path / "climber.step"
    shutil.copy(CAD / "climber.step", step)
    c = test_app(step)
    _built_part(c)
    _part_edit(c, "set_joint", body="arm_A", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.06, 0.1, 0.01], "axis": [0, 1, 0]})
    _part_edit(c, "set_motor", body="arm_A", settings={"motor": "NEO", "count": 1, "ratio": 50.0})
    end = time.monotonic() + 60
    while "arm_A_motor" not in (tmp_path / "parts" / "climber" / "part.toml").read_text(encoding="utf-8") \
            or c.get("/api/part/state")[1]["build"]["state"] != "idle":
        assert time.monotonic() < end
        time.sleep(0.05)
    s, b = c.call("POST", "/api/test/new", {"from_part": True})
    assert s == 200, b
    raw = tomllib.loads((tmp_path / "climber-standin.toml").read_text(encoding="utf-8"))
    parts = {p["name"]: p for p in raw["part"]}
    assert parts["climber"]["ids"] == {"arm_A_motor": "PWM:2"}           # the drive has PWM 0 and 1
    st = _state(c)
    assert st["problems"] == [], st["problems"]
    assert st["test"]["blocks"] == [{"do": "move", "joint": "climber.arm_A", "to": 45.0, "unit": "deg"}]


def test_free_motor_ids_never_reuse_one_the_robot_has():
    """Adding a part to an existing stand-in: its motors take the lowest PWM channels nothing uses (a hand-added part's
    included); a robot whose motors are on CAN gets CAN IDs of its controller, clear of every CAN device's ID."""
    import tomllib

    import tomlkit

    from scrimmage.assembler.test_session import add_to_standin, free_motor_ids, standin_config
    lift = {"import_id": "x", "part": "lift", "origins": {"frame": [0.0, 0.0, 0.0]}, "attach": None}
    roller = {"import_id": "x", "part": "roller", "origins": {"roll": [0.0, 0.0, 0.0]}, "attach": None}
    text = standin_config([("parts/lift", lift)], "chassis", {"lift": ["frame_motor"]})
    assert {p["name"]: p for p in tomllib.loads(text)["part"]}["lift"]["ids"] == {"frame_motor": "PWM:2"}
    text += '\n[[part]]\nname = "arm"\nstock = "arm"\nattach = "drive.top"\nids = { pivot = "PWM:3" }\n'
    new, added = add_to_standin(text, [("parts/lift", lift), ("parts/roller", roller)], "chassis",
                                {"lift": ["frame_motor"], "roller": ["roll_motor", "roll_2_motor"]})
    parts = {p["name"]: p for p in tomllib.loads(new)["part"]}
    assert added == ["roller"] and parts["lift"]["ids"] == {"frame_motor": "PWM:2"}
    assert parts["roller"]["ids"] == {"roll_motor": "PWM:4", "roll_2_motor": "PWM:5"}
    can = tomlkit.parse('[[part]]\nname = "a"\nids = { drive = "SparkMax:1", steer = { rev = "SparkMax:2" }, '
                        'encoder = "SparkMaxAbs:3" }\n[[part]]\nname = "g"\nids = { gyro = "Pigeon2:4" }\n')
    assert free_motor_ids(can, ["x_motor", "y_motor"]) == {"x_motor": "SparkMax:5", "y_motor": "SparkMax:6"}
    assert free_motor_ids(can, []) == {}
    multi = tomlkit.parse('[[part]]\nname = "a"\nids = { lift = [{ ctre = "TalonFX:1" }, { ctre = "TalonFX:2" }], '
                          'x = "TalonFX:3" }\n')
    assert free_motor_ids(multi, ["m1", "m2"]) == {"m1": "TalonFX:4", "m2": "TalonFX:5"}
    pwm = ", ".join(f'm{i} = "PWM:{i}"' for i in range(20))
    full = tomlkit.parse(f'[[part]]\nname = "a"\nids = {{ {pwm} }}\n')
    with pytest.raises(ValueError, match="no free PWM channels left for 1 more motor"):
        free_motor_ids(full, ["x_motor"])


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
    s, b = c.call("POST", "/api/robot/edit", {"version": c.get("/api/robot/state")[1]["version"], "op": "start",
                                              "range": "intake", "named": "facing_ball"})
    assert s == 200 and b["ok"], b
    assert c.get("/api/robot/state")[1]["range"] == "intake"


def test_setup_field_choice_moves_a_robot_with_its_own_start_pose_to_the_fields_start(tmp_path, test_app):
    """Setup's field picker sends the field's named start: a real robot placed at a 2026 match start
    ([robot] start_pose) moves to the ladder's start, in front of the rungs; the Robot tab's Undo puts the old field
    and pose back."""
    import tomllib

    import mujoco

    from scrimmage.testrange.spec import load_range
    from scrimmage.world import World
    t = write_test(tmp_path)
    robot = tmp_path / "robot.toml"
    match_start = [3.42, 5.56, 0.0]       # the 2026 field's blue_left start
    text = robot.read_text(encoding="utf-8").replace(
        "[robot]\n", f'[range]\nfile = "2026"\n\n[robot]\nstart_pose = {match_start}\n', 1)
    robot.write_text(text, encoding="utf-8")
    before = tomllib.loads(robot.read_text(encoding="utf-8"))
    assert before["robot"]["start_pose"] == match_start and before["range"] == {"file": "2026"}
    c = test_app(t)
    v = c.get("/api/robot/state")[1]["version"]
    # what static/test.js's field picker sends for "In front of a ladder (round rungs)"
    s, b = c.call("POST", "/api/robot/edit", {"version": v, "op": "start", "range": "ladder", "named": "below"})
    assert s == 200 and b["ok"], b
    raw = tomllib.loads(robot.read_text(encoding="utf-8"))
    assert raw["range"] == {"file": "ladder", "start": "below"} and "start_pose" not in raw.get("robot", {})
    assert _state(c)["palette"]["bars"] == ["rung1", "rung2", "rung3", "rung4"]
    # the robot is built at the ladder's start, 1.0 m before the rungs, not at the match start
    w = World(raw, tmp_path)
    mujoco.mj_forward(w.model, w.data)
    x, y, _ = load_range("ladder", ".").starts["below"]
    assert [float(v) for v in w.data.xpos[w.model.body("drive.chassis").id][:2]] == pytest.approx([x, y], abs=1e-6)
    # Undo puts the old field and the match start back
    s, b = c.call("POST", "/api/robot/undo", {"version": c.get("/api/robot/state")[1]["version"]})
    assert s == 200 and b["ok"], b
    after = tomllib.loads(robot.read_text(encoding="utf-8"))
    assert after["robot"]["start_pose"] == match_start and after["range"] == {"file": "2026"}


# ---- every field, station starters, floor spreads ------------------------------------------------------------------

STATION = Path(__file__).resolve().parents[1] / "examples" / "blocks" / "station"
REEF = STATION.parent / "reef"
ON_STATION = """
[range]
file = "station"
start = "{start}"
"""
SPREAD = """
[setup]
floor_spread = {{ offset_in = [-3.0, 3.0] }}

[[block]]
do = "wait"
seconds = 0.2
"""


def test_every_builtin_field_is_a_choice(tmp_path, test_app):
    from scrimmage.testrange.spec import BUILTIN_RANGES
    c = test_app(write_test(tmp_path))
    f = _state(c)["fields"]
    assert [x["file"] for x in f] == [None, *BUILTIN_RANGES] and len(f) == 14
    assert f[0] == {"file": None, "name": "The plain field", "starts": []}
    station = next(x for x in f if x["file"] == "station")
    assert station == {"file": "station", "name": "A human player station", "starts": ["at_station", "facing_ball"],
                       "disc": False, "ball_choice": True, "stations": ["feeder"]}
    assert next(x for x in f if x["file"] == "2013")["disc"] is True
    assert next(x for x in f if x["file"] == "2026")["ball_choice"] is False
    assert all(x["starts"] for x in f if x["file"])        # terrain-library has a start now


def test_set_start_takes_any_builtin_field():
    import tomlkit

    from scrimmage.assembler import edits
    doc = tomlkit.parse(new_config("standin"))
    edits.set_start(doc, {"range": "2022-hangar", "named": "under_mid"})
    assert doc["range"] == {"file": "2022-hangar", "start": "under_mid"}
    edits.set_start(doc, {"range": "2026"})
    assert doc["range"] == {"file": "2026"}
    with pytest.raises(edits.EditError):
        edits.set_start(doc, {"range": "somewhere"})


def _station_copy(tmp_path) -> Path:
    import shutil
    shutil.copytree(STATION / "parts", tmp_path / "parts")
    robot = tmp_path / "hopper-standin.toml"
    shutil.copy(STATION / "hopper-standin.toml", robot)
    return robot


def test_a_new_test_on_the_station_field_starts_feeding(tmp_path, test_app):
    """The station field: start feeding from its station, wait for 5 in the hopper, stop; the hopper is preselected."""
    c = test_app(_station_copy(tmp_path))
    s, b = c.call("POST", "/api/test/new", {})
    assert s == 200, b
    st = _state(c)
    t = st["test"]
    assert [b["do"] for b in t["blocks"]] == ["start_feeding", "wait_until", "stop_feeding"]
    assert t["hopper"] == ["hopper.bin"]
    assert t["blocks"] == [{"do": "start_feeding", "station": "feeder"},
                           {"do": "wait_until", "until": {"in_hopper": {"at_least": 5}}, "timeout": 20.0},
                           {"do": "stop_feeding", "station": "feeder"}]
    assert st["problems"] == [] and st["station_starter"] == t["blocks"]
    # a field with no station has no station starter
    (tmp_path / "plain").mkdir()
    assert _state(test_app(write_test(tmp_path / "plain")))["station_starter"] is None


def test_starter_hopper_picks_bodies_named_like_a_hopper():
    """A body's own name first; only when none matches, the root body of a part named like one, never its others."""
    from scrimmage.assembler.test_session import starter_hopper
    pal = {"bodies": ["drive.chassis", "hopper.bin", "arm.Intake_roller", "feed.indexer", "dt.left_1",
                      "hook.hopperish"]}
    roots = {"drive": "drive.chassis", "hopper": "hopper.bin", "arm": "arm.base"}
    assert starter_hopper(pal, roots) == ["arm.Intake_roller", "feed.indexer", "hook.hopperish"]
    intake = {"bodies": ["drive.chassis", "intake.arm", "intake.frame", "intake.roller"]}
    assert starter_hopper(intake, {"drive": "drive.chassis", "intake": "intake.frame"}) == ["intake.frame"]
    assert starter_hopper(intake) == [] and starter_hopper(None) == []


def test_the_station_examples_hopper_is_its_parts_root_body():
    from scrimmage.assembler.test_session import part_roots
    from scrimmage.config import build_world, load_config
    roots = part_roots(build_world(load_config(STATION / "hopper-standin.toml")))
    assert roots["hopper"] == "hopper.bin" and roots["drive"] == "drive.chassis"


def test_test_it_puts_a_hopper_part_on_the_station_field():
    """A part with a hopper-like body and no hook body: the stand-in starts at the station; a hook wins."""
    import tomllib

    from scrimmage.assembler.test_session import standin_config
    hopper = {"import_id": "x", "part": "hopper", "origins": {"bin": [0.0, 0.0, 0.0]}, "attach": None}
    raw = tomllib.loads(standin_config([("parts/hopper", hopper)], "chassis", roots={"hopper": "bin"}))
    assert raw["range"] == {"file": "station", "start": "at_station"}
    assert "range" not in tomllib.loads(standin_config([("parts/hopper", hopper)], "chassis"))   # no root known
    indexer = {"import_id": "x", "part": "front", "origins": {"Indexer_belt": [0.0, 0.0, 0.0]}, "attach": None}
    assert tomllib.loads(standin_config([("parts/front", indexer)], "chassis"))["range"]["file"] == "station"
    hook = {"import_id": "x", "part": "hook", "origins": {"hook_body": [0.1, 0.0, 0.9]}, "attach": "hopper.bin"}
    raw = tomllib.loads(standin_config([("parts/hopper", hopper), ("parts/hook", hook)], "chassis",
                                       roots={"hopper": "bin", "hook": "hook_body"}))
    assert raw["range"] == {"file": "ladder", "start": "below"}


def _spread_test(tmp_path, start: str) -> Path:
    path = write_test(tmp_path, blocks=SPREAD.format())
    robot = tmp_path / "robot.toml"
    robot.write_text(robot.read_text(encoding="utf-8") + ON_STATION.format(start=start), encoding="utf-8")
    return path


def test_a_spread_runs_and_is_listed(tmp_path, test_app):
    c = test_app(_spread_test(tmp_path, "facing_ball"))
    st = _state(c)
    assert st["problems"] == [] and st["palette"]["floor_piece"] and st["spreads"] == []
    s, b = c.call("POST", "/api/test/spread", {})
    assert s == 200, b
    st = _wait_idle(c)
    assert st["run_error"] is None, st
    assert st["spreads"][0]["id"].startswith("spread-") and len(st["spreads"][0]["rows"]) == 2
    assert st["last"] == {"kind": "spread", "id": st["spreads"][0]["id"]}
    run_id = next(r["id"] for r in st["runs"] if r["id"].startswith("spread-"))
    s, r = c.get(f"/api/test/run/{run_id}")
    assert s == 200 and r["id"] == run_id, r
    s, view = c.get(f"/api/test/run/{run_id}/view")
    assert s == 200, view
    s, body = c.get(f"/api/test/run/{run_id}/motion")
    assert s == 200 and body[:4] == b"SCRM"


def test_a_spread_without_a_floor_ball_is_refused(tmp_path, test_app):
    c = test_app(_spread_test(tmp_path, "at_station"))
    assert _state(c)["palette"]["floor_piece"] is False
    s, b = c.call("POST", "/api/test/spread", {})
    assert s == 409 and b["error"] == "the floor spread needs a ball on the floor in front of the robot's start", b
    assert _state(c)["running"] is None


def test_a_spread_on_a_test_without_one_is_refused(tmp_path, test_app):
    write_test(tmp_path)
    robot = tmp_path / "robot.toml"
    robot.write_text(robot.read_text(encoding="utf-8") + ON_STATION.format(start="facing_ball"), encoding="utf-8")
    c = test_app(tmp_path / "t.test.toml")
    s, b = c.call("POST", "/api/test/spread", {})
    assert s == 409 and "no floor spread" in b["error"], b


def test_the_state_names_the_hopper_a_test_without_one_would_preselect(tmp_path, test_app):
    """Setup ticks starter_hopper's bodies for a test with no hopper (R9: a body named like one, else a part's root
    body); the robot state names the field's start Setup's Start picker shows."""
    _station_copy(tmp_path)
    path = tmp_path / "t.test.toml"
    path.write_text('[test]\nrobot = "hopper-standin.toml"\n' + SPIN.replace("arm.pivot", "dt.left"), encoding="utf-8")
    c = test_app(path)
    assert _state(c)["starter_hopper"] == ["hopper.bin"]
    s, robot = c.get("/api/robot/state")
    assert s == 200 and robot["range"] == "station" and robot["start"] == "at_station"
    (tmp_path / "plain").mkdir()
    c = test_app(write_test(tmp_path / "plain"))
    assert _state(c)["starter_hopper"] == []
    assert c.get("/api/robot/state")[1]["start"] is None


def test_a_spread_that_puts_the_ball_inside_the_robot_is_refused(tmp_path, test_app):
    """The robot's front, the ball's radius and an inch: nearer, a placement's ball would start inside the robot."""
    path = _spread_test(tmp_path, "facing_ball")
    c = test_app(path)
    ball = _state(c)["palette"]["floor_ball"]
    assert ball["distance_in"] > ball["nearest_in"] > 17.7      # past the stand-in's half length
    path.write_text(path.read_text(encoding="utf-8").replace("offset_in = [-3.0, 3.0]", "distance_in = [12.0, 40.0]"),
                    encoding="utf-8")
    end = time.monotonic() + 15
    while _state(c)["test"]["setup"]["floor_spread"] != {"distance_in": [12.0, 40.0]}:
        assert time.monotonic() < end
        time.sleep(0.05)
    s, b = c.call("POST", "/api/test/spread", {})
    assert s == 409, b
    assert b["error"] == (f"the floor spread puts the ball inside the robot at 12 in ahead: start the distance at "
                          f"{ball['nearest_in']} in or more")
    from scrimmage.blocks.spread import inside_robot
    from scrimmage.config import build_world, load_config
    world = build_world(load_config(tmp_path / "robot.toml"))
    assert inside_robot(world, {"distance_in": [float(ball["nearest_in"])], "offset_in": [-6.0, 6.0]}) is None


def test_grabber_words_preselect():
    """A body named like a grabber's ("claw", "grabber", "gripper", "jaw") is where a piece should end up too."""
    from scrimmage.assembler.test_session import GRABBER_WORDS, HOPPER_WORDS, pick_hopper
    assert GRABBER_WORDS == ("claw", "grabber", "gripper", "jaw")
    assert HOPPER_WORDS == ("hopper", "intake", "indexer", "claw", "grabber", "gripper", "jaw")
    assert pick_hopper(["claw.jaw_left", "claw.arm"], {}) == ["claw.jaw_left"]
    assert pick_hopper(["grabber.mast", "grabber.arm", "grabber.jaw_left", "grabber.jaw_right"], {}) == [
        "grabber.jaw_left", "grabber.jaw_right"]
    assert pick_hopper(["Gripper.base", "x.Gripper_pad"], {}) == ["x.Gripper_pad"]
    assert pick_hopper(["claw.base", "claw.arm"], {"claw": "claw.base"}) == ["claw.base"]   # a part named like one


def test_test_it_puts_a_claw_on_the_reef_field():
    """A part with a grabber-like body starts facing the reef's floor coral; a hook still wins, and a hopper-like body
    with no grabber body still goes to the station."""
    import tomllib

    from scrimmage.assembler.test_session import STANDIN_REEF, standin_config
    assert STANDIN_REEF == {"file": "reef", "start": "facing_coral"}
    claw = {"import_id": "x", "part": "claw", "origins": {"arm": [0.0, 0.0, 0.0], "jaw": [0.1, 0.0, 0.2]},
            "attach": None}
    assert tomllib.loads(standin_config([("parts/claw", claw)], "chassis"))["range"] == STANDIN_REEF
    both = {"import_id": "x", "part": "front", "origins": {"intake": [0.0, 0.0, 0.0], "jaw": [0.1, 0.0, 0.2]},
            "attach": None}
    assert tomllib.loads(standin_config([("parts/front", both)], "chassis"))["range"]["file"] == "reef"
    hook = {"import_id": "x", "part": "hook", "origins": {"hook_body": [0.1, 0.0, 0.9]}, "attach": "claw.arm"}
    raw = tomllib.loads(standin_config([("parts/claw", claw), ("parts/hook", hook)], "chassis"))
    assert raw["range"] == {"file": "ladder", "start": "below"}
    indexer = {"import_id": "x", "part": "front", "origins": {"Indexer_belt": [0.0, 0.0, 0.0]}, "attach": None}
    assert tomllib.loads(standin_config([("parts/front", indexer)], "chassis"))["range"]["file"] == "station"


def test_a_grabber_on_a_field_with_rods_starts_by_waiting_for_a_hold():
    """On a field with rods (the reef, which has a station too), a robot with a body named like a grabber's starts
    by closing that part's jaw-like motors at 4 V (ruling P24), lifting with its lone arm hinge, waiting up to 10 s
    for any piece held by the part, expecting it held throughout a 2 s wait; without rods, or without a grabber body,
    the starters are as before."""
    from scrimmage.assembler.test_session import grabber_starter, starter_blocks, station_starter
    from scrimmage.blocks.program import block_json
    pal = {"bodies": ["drive.chassis", "grabber.arm", "grabber.jaw_left", "grabber.jaw_right"],
           "rods": ["branch_l2", "branch_l3", "branch_l4"], "stations": ["coral_station"],
           "motors": [{"name": "dt.left", "kind": "wheel"}, {"name": "grabber.arm", "kind": "hinge"},
                      {"name": "grabber.jaw_left", "kind": "slide"}, {"name": "grabber.Finger_r", "kind": "slide"},
                      {"name": "lift.jaw", "kind": "slide"}]}
    held = {"held": {"piece": "any", "by": "grabber"}}
    want = [{"do": "spin", "motor": "grabber.jaw_left", "volts": 4.0},
            {"do": "spin", "motor": "grabber.Finger_r", "volts": 4.0},
            {"do": "wait", "seconds": 1.0},
            {"do": "move", "joint": "grabber.arm", "to": -30.0, "unit": "deg"},
            {"do": "wait_until", "until": held, "timeout": 10.0},
            {"do": "expect", "that": held, "throughout": True},
            {"do": "wait", "seconds": 2.0}]
    assert [block_json(b) for b in grabber_starter(pal)] == want
    assert [block_json(b) for b in starter_blocks(pal)] == want
    no_rods = {**pal, "rods": []}
    assert grabber_starter(no_rods) == [] and starter_blocks(no_rods) == station_starter(no_rods)
    # a part named like a grabber, its bodies plainly named (standin_config sends it to the reef): that part, and the
    # 1 s wait though it has no jaw-like motor
    claw = {"bodies": ["drive.chassis", "claw.base", "claw.left"], "parts": ["claw", "drive"], "rods": ["branch_l2"],
            "stations": ["coral_station"], "motors": [{"name": "claw.lift", "kind": "hinge"}]}
    by_claw = {"held": {"piece": "any", "by": "claw"}}
    assert [block_json(b) for b in starter_blocks(claw)] == [
        {"do": "wait", "seconds": 1.0}, {"do": "move", "joint": "claw.lift", "to": -30.0, "unit": "deg"},
        {"do": "wait_until", "until": by_claw, "timeout": 10.0},
        {"do": "expect", "that": by_claw, "throughout": True}, {"do": "wait", "seconds": 2.0}]
    no_grabber = {**pal, "bodies": ["drive.chassis", "lift.arm"]}
    assert grabber_starter(no_grabber) == [] and starter_blocks(no_grabber) == station_starter(no_grabber)


def test_the_reef_palette_lists_its_rods():
    from scrimmage.blocks.palette import palette
    from scrimmage.config import build_world, load_config
    pal = palette(build_world(load_config(REEF / "grabber-standin.toml")), [])
    assert pal["rods"] == ["branch_l2", "branch_l3", "branch_l4"]
    assert "grabber.jaw_left" in pal["bodies"]


# ---- grips ----------------------------------------------------------------------------------------------------------

def _reef_test(tmp_path, blocks='[[block]]\ndo = "wait"\nseconds = 0.2\n') -> Path:
    import shutil
    shutil.copytree(REEF, tmp_path / "reef")
    t = tmp_path / "reef" / "g.test.toml"
    t.write_text(f'[test]\nrobot = "grabber-standin.toml"\n{blocks}', encoding="utf-8")
    return t


def _grip(c, body, grip):
    return c.call("POST", "/api/test/grip", {"body": body, "grip": grip})


def test_the_grip_endpoint_writes_the_overlay(test_app, tmp_path):
    """Setup's grip picker writes the grip into the part folder's own edits (scrimmage-edits.toml), and the robot is
    built again with it: the palette shows it. A material it doesn't know, a stock part, a body the part doesn't
    have and one that touches nothing are refused (nothing written); null clears it."""
    t = _reef_test(tmp_path)
    c = test_app(t)
    overlay = tmp_path / "reef" / "parts" / "grabber" / "scrimmage-edits.toml"
    assert _state(c)["palette"]["grips"] == {"grabber.jaw_left": None, "grabber.jaw_right": None}
    s, b = _grip(c, "grabber.jaw_left", "nitrile")
    assert s == 200 and b["ok"], b
    assert 'grip = "nitrile"' in overlay.read_text(encoding="utf-8")
    st = _state(c)
    assert st["palette"]["grips"]["grabber.jaw_left"] == "nitrile" and st["problems"] == []
    s, b = _grip(c, "grabber.jaw_right", 1.05)          # my own number
    assert s == 200 and _state(c)["palette"]["grips"]["grabber.jaw_right"] == 1.05
    before = overlay.read_text(encoding="utf-8")
    for body, grip, words in (("grabber.jaw_left", "velcro", "isn't a grip material"),
                              ("grabber.jaw_left", 9.0, "isn't a friction to use"),
                              ("drive.chassis", "nitrile", "a stock part has its own materials"),
                              ("grabber.nope", "nitrile", "no body \"nope\" in grabber"),
                              ("claw.jaw", "nitrile", "no part \"claw\" on this robot"),
                              ("grabber.arm", "nitrile", "touches nothing"),
                              ("jaw_left", "nitrile", "<part>.<body>")):
        s, b = _grip(c, body, grip)
        assert s == 400 and words in b["error"], (body, b)
    assert overlay.read_text(encoding="utf-8") == before
    s, b = _grip(c, "grabber.jaw_left", None)
    assert s == 200 and "jaw_left" not in overlay.read_text(encoding="utf-8")
    assert _state(c)["palette"]["grips"]["grabber.jaw_left"] is None


def test_the_grip_endpoint_waits_for_a_run(test_app, tmp_path):
    c = test_app(_reef_test(tmp_path, '[[block]]\ndo = "wait"\nseconds = 20\n'))
    assert c.call("POST", "/api/test/run", {})[0] == 200
    s, b = _grip(c, "grabber.jaw_left", "nitrile")
    assert s == 409 and "in progress" in b["error"], b
    c.call("POST", "/api/test/cancel", {})
    _wait_idle(c)
    assert not (tmp_path / "reef" / "parts" / "grabber" / "scrimmage-edits.toml").exists()


def test_the_grip_endpoint_wont_write_what_the_robot_config_overrides(test_app, tmp_path):
    """A path part's own `grip` in the robot config wins over the folder's: Setup says so instead of writing a grip
    that would do nothing."""
    t = _reef_test(tmp_path)
    robot = t.parent / "grabber-standin.toml"
    robot.write_text(robot.read_text(encoding="utf-8").replace(
        'ids = { arm = "PWM:2"', 'grip = { jaw_left = "hdpe" }\nids = { arm = "PWM:2"'), encoding="utf-8")
    c = test_app(t)
    assert _state(c)["palette"]["grips"]["grabber.jaw_left"] == "hdpe"
    s, b = _grip(c, "grabber.jaw_left", "nitrile")
    assert s == 400 and "grabber-standin.toml" in b["error"] and "change it there" in b["error"], b
    assert _grip(c, "grabber.jaw_left", None)[0] == 200     # clearing the folder's own grip is always fine
    assert _grip(c, "grabber.jaw_right", "nitrile")[0] == 200


def test_the_state_carries_the_new_palette_keys(test_app, tmp_path):
    st = _state(test_app(_reef_test(tmp_path)))
    pal = st["palette"]
    assert pal["piece"] == "2025-coral" and pal["kinds"]["2025-coral"]["label"] == "coral"
    assert pal["rods"] == ["branch_l2", "branch_l3", "branch_l4"] and pal["grip_materials"][0][0] == "rubber"


def test_a_config_grip_isnt_a_robot_tab_parameter(test_app, tmp_path):
    t = _reef_test(tmp_path)
    robot = t.parent / "grabber-standin.toml"
    robot.write_text(robot.read_text(encoding="utf-8").replace(
        'ids = { arm = "PWM:2"', 'grip = { jaw_left = "hdpe" }\nids = { arm = "PWM:2"'), encoding="utf-8")
    c = test_app(robot)
    s, rs = c.get("/api/robot/state")
    grabber = next(p for p in rs["parts"] if p["name"] == "grabber")
    assert "grip" not in grabber["params"], grabber
