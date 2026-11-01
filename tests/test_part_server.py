"""The Part tab's server side: a CAD import (or a plugin part folder) behind /api/part/*."""

import base64
import json
import os
import shutil
import struct
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest

from scrimmage.assembler import part_session, recent
from scrimmage.assembler.server import App, classify
from scrimmage.bench.cad import write_obj
from scrimmage.cad import MISSING_EXTRA, CadError
from scrimmage.cad.frame import to_robot
from scrimmage.cad.settings import read_import
from scrimmage.cad.source import load_source
from scrimmage.parts.model import load_part
from test_assembler_server import Client

DATA = Path(__file__).resolve().parent / "data" / "cad"
HOOK = "Climber/hook_assy/hook"


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRIMMAGE_RECENT", raising=False)
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def part_app(tmp_path):
    """make(path=None) -> (Client, the climber.step copy). With no path, opens the copied climber.step."""
    step = tmp_path / "climber.step"
    shutil.copy(DATA / "climber.step", step)
    started = []

    def make(path="step"):
        app = App(step if path == "step" else path, open_browser=False)
        started.append(app)
        return Client(app, app.start()), step
    yield make
    for a in started:
        a.stop()
    assert not [t for t in threading.enumerate() if t.name.startswith("part-")], "a part thread outlived stop()"


def _state(c, since=None):
    s, b = c.get("/api/part/state" + (f"?since={since}" if since is not None else ""))
    assert s == 200, b
    return b


def _wait_for(c, pred, timeout=20.0):
    end = time.monotonic() + timeout
    st = _state(c)
    while not pred(st):
        assert time.monotonic() < end, st
        st = _state(c, st["version"])
    return st


def _built(c):
    return _wait_for(c, lambda st: st["build"]["state"] != "building")


def _edit(c, op, **args):
    st = _state(c)
    return c.call("POST", "/api/part/edit", {"version": st["version"], "op": op, "args": args})


def _post(c, path, body=None):
    return c.call("POST", path, body or {})


def _decode(piece):
    v = np.frombuffer(base64.b64decode(piece["vert"]), dtype="<f4").reshape(-1, 3)
    f = np.frombuffer(base64.b64decode(piece["face"]), dtype="<i4").reshape(-1, 3)
    return v, f


def test_open_step_creates_import_and_builds(part_app, tmp_path):
    c, step = part_app()
    s, app = c.get("/api/app/state")
    assert s == 200 and app["mode"] == "part"
    imp_path = tmp_path / "parts" / "climber.import.toml"
    assert app["path"] == str(imp_path) and imp_path.is_file()
    assert classify(imp_path) == "import" and classify(step) == "import"
    st = _built(c)
    assert st["mode"] == "import" and st["name"] == "climber" and st["read_only_reason"] is None
    assert len(st["bodies"]) == 5 and st["build"] == {"state": "idle", "message": None}
    assert st["source"] == "../climber.step" and st["units"] == "m"
    hook = next(p for p in st["pieces"] if p["key"] == HOOK)
    assert hook["material"] is None and hook["body"] == "hook_assy" and hook["volume"] > 0 and hook["mass"] > 0
    assert any(p["hardware"] for p in st["pieces"]) and not hook["hardware"]
    assert st["parts"][0]["name"] == "climber" and st["parts"][0]["mass"] > 0
    assert any(i["text"].startswith("Check which way is up") for i in st["checklist"])
    assert not st["can_undo"] and not st["can_redo"]
    assert (tmp_path / "parts" / "climber" / "part.toml").is_file()
    part = load_part(tmp_path / "parts" / "climber")
    assert part.name == "climber"
    # the build's `written` was saved, and that save isn't an outside edit
    assert read_import(imp_path).written == {"climber": "climber"}
    time.sleep(0.6)
    assert _state(c)["version"] == st["version"]
    s, b = c.get("/api/app/browse")
    assert {"name": "climber.step", "path": str(step), "kind": "cad"} in b["entries"]
    s, b = c.get(f"/api/app/browse?dir={tmp_path / 'parts'}")
    assert {e["name"]: e["kind"] for e in b["entries"]} == {"climber": "folder", "climber.import.toml": "import"}
    s, m = c.get("/api/part/materials")
    assert s == 200 and m["materials"]["aluminum"]["density"] == 2700.0 and "KrakenX60" in m["motors"]


def test_reopen_step_keeps_settings(part_app, tmp_path):
    c, step = part_app()
    _built(c)
    assert _edit(c, "set_material", pieces=[HOOK], material="steel")[0] == 200
    s, _ = _post(c, "/api/app/open", {"path": "climber.step"})
    assert s == 200
    e = recent.recent()[0]
    assert e["path"] == str(tmp_path / "parts" / "climber.import.toml") and e["kind"] == "import"
    st = _built(c)
    assert next(p for p in st["pieces"] if p["key"] == HOOK)["material"] == "steel"


def test_edit_material_saves_and_rebuilds(part_app, tmp_path):
    c, _ = part_app()
    before = _built(c)
    m0 = before["parts"][0]["mass"]
    s, st = _edit(c, "set_material", pieces=[HOOK], material="steel")
    assert s == 200 and st["version"] > before["version"] and st["can_undo"]
    assert next(p for p in st["pieces"] if p["key"] == HOOK)["material"] == "steel"
    assert read_import(tmp_path / "parts" / "climber.import.toml").pieces[HOOK].material == "steel"
    st = _wait_for(c, lambda st: st["build"]["state"] == "idle" and st["parts"][0]["mass"] > m0)
    part = load_part(tmp_path / "parts" / "climber")
    assert sum(b.mass for b in part.bodies) == pytest.approx(st["parts"][0]["mass"])


def test_edit_messages_are_shown(part_app):
    c, _ = part_app()
    _built(c)
    s, st = _edit(c, "merge_bodies", into="arm_A", other="arm_B")
    assert s == 200 and len(st["bodies"]) == 4
    assert isinstance(st["messages"], list)


def test_state_has_couplings_and_loops(part_app):
    c, _ = part_app()
    st = _built(c)
    assert st["couplings"] == [] and st["loops"] == []
    hinge = {"kind": "hinge", "anchor": [0.06, 0.1, 0.818], "axis": [1, 0, 0]}
    for body in ("hook_assy", "hook_assy_2"):
        assert _edit(c, "set_joint", body=body, parent="base_plate", joint=hinge)[0] == 200
    s, st = _edit(c, "add_coupling", a="hook_assy", b="hook_assy_2", ratio=-1)
    assert s == 200 and st["couplings"] == [{"a": "hook_assy", "b": "hook_assy_2", "ratio": -1.0}]
    s, st = _edit(c, "add_loop", body1="arm_A", body2="arm_B", point=[0.1, 0.1, 0.5])
    assert s == 200 and st["loops"] == [{"body1": "arm_A", "body2": "arm_B", "point": [0.1, 0.1, 0.5]}]


def test_stale_version_409(part_app):
    c, _ = part_app()
    st = _built(c)
    v = st["version"]
    assert c.call("POST", "/api/part/edit", {"version": v, "op": "set_material",
                                             "args": {"pieces": [HOOK], "material": "steel"}})[0] == 200
    s, b = c.call("POST", "/api/part/edit", {"version": v, "op": "set_material",
                                             "args": {"pieces": [HOOK], "material": "nylon"}})
    assert s == 409, b
    assert c.call("POST", "/api/part/edit", {"op": "set_material", "args": {}})[0] == 400


def test_bad_edit_400_with_field(part_app):
    c, _ = part_app()
    _built(c)
    s, b = _edit(c, "set_material", pieces=[HOOK], material="unobtainium")
    assert s == 400 and b["field"] == "material" and "Unknown material" in b["message"]
    s, b = _edit(c, "no_such_op")
    assert s == 400 and b["field"] == "op"


def test_axis_route(part_app):
    c, _ = part_app()
    _built(c)
    src = load_source(DATA / "climber.step")
    p = next(p for p in src.pieces if p.key == HOOK)
    tv = p.v[p.f]
    n = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None]
    tri = int(np.argmin(np.abs(n[:, 0])))
    s, b = _post(c, "/api/part/axis", {"piece": HOOK, "triangle": tri, "kind": "round"})
    assert s == 200, b
    assert len(b["point"]) == 3 and abs(abs(b["axis"][0]) - 1) < 1e-3
    s, b = _post(c, "/api/part/axis", {"piece": HOOK, "triangle": 10 ** 6, "kind": "round"})
    assert s == 400 and "outside the piece" in b["error"]
    s, b = _post(c, "/api/part/axis", {"piece": "nope", "triangle": 0, "kind": "round"})
    assert s == 400
    s, b = _post(c, "/api/part/axis", {"piece": HOOK, "triangle": 0, "kind": "wobbly"})
    assert s == 400


def test_undo_redo(part_app, tmp_path):
    c, _ = part_app()
    _built(c)
    imp = tmp_path / "parts" / "climber.import.toml"
    _edit(c, "set_material", pieces=[HOOK], material="steel")
    st = _state(c)
    s, st = _post(c, "/api/part/undo", {"version": st["version"]})
    assert s == 200 and next(p for p in st["pieces"] if p["key"] == HOOK)["material"] is None
    assert st["can_redo"] and not st["can_undo"] and read_import(imp).pieces[HOOK].material is None
    s, st = _post(c, "/api/part/redo", {"version": st["version"]})
    assert s == 200 and next(p for p in st["pieces"] if p["key"] == HOOK)["material"] == "steel"
    assert read_import(imp).pieces[HOOK].material == "steel"
    # an undo never brings back an old `written` (the build's record of its folders)
    st = _built(c)
    assert read_import(imp).written == {"climber": "climber"}


def test_outside_edit_shows(part_app, tmp_path):
    c, _ = part_app()
    st = _built(c)
    imp = tmp_path / "parts" / "climber.import.toml"
    text = imp.read_text().replace('name = "climber"', 'name = "climber"', 1)
    data = read_import(imp)
    data.pieces[HOOK].material = "nylon"
    from scrimmage.cad.settings import write_import
    write_import(data, imp)
    t0 = time.monotonic()
    st = _wait_for(c, lambda st: next(p for p in st["pieces"] if p["key"] == HOOK)["material"] == "nylon", 2.0)
    assert time.monotonic() - t0 < 1.5
    assert st["can_undo"]
    imp.write_text("not [valid")
    st = _wait_for(c, lambda st: st["read_only_reason"] is not None, 2.0)
    assert _edit(c, "set_material", pieces=[HOOK], material="steel")[0] == 409
    imp.write_text(text)
    _wait_for(c, lambda st: st["read_only_reason"] is None, 2.0)


def test_reimport_keeps_settings_and_reports(part_app, tmp_path):
    c, step = part_app()
    _built(c)
    _edit(c, "set_material", pieces=[HOOK], material="steel")
    shutil.copy(DATA / "climber_v2.step", step)
    s, st = _post(c, "/api/part/reimport", {"version": _state(c)["version"]})
    assert s == 200, st
    keys = {p["key"] for p in st["pieces"]}
    v2 = {p.key for p in load_source(DATA / "climber_v2.step").pieces}
    assert keys == v2 and st["messages"]
    hook = next((p for p in st["pieces"] if p["key"] == HOOK), None)
    if hook is not None:
        assert hook["material"] == "steel"
    _built(c)


def test_upload_creates_source_copy(part_app, tmp_path):
    c, _ = part_app()
    _built(c)
    data = (DATA / "climber_v2.step").read_bytes()
    req = urllib.request.Request(c.base + "/api/part/upload", data=data, method="POST",
                                 headers={"X-Scrimmage-Token": c.token, "X-Filename": "..%2F..%2Fevil%20v2.step"})
    with urllib.request.urlopen(req, timeout=40) as r:
        assert r.status == 200
    dest = tmp_path / "parts" / "climber.source" / "evil v2.step"
    assert dest.read_bytes() == data
    assert read_import(tmp_path / "parts" / "climber.import.toml").source == "climber.source/evil v2.step"
    assert not (tmp_path.parent / "evil v2.step").exists()
    st = _built(c)
    assert {p["key"] for p in st["pieces"]} == {p.key for p in load_source(dest).pieces}
    # a file that isn't CAD is refused
    req = urllib.request.Request(c.base + "/api/part/upload", data=b"x", method="POST",
                                 headers={"X-Scrimmage-Token": c.token, "X-Filename": "notes.txt"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=40)
    assert e.value.code == 400
    # no token, no upload
    req = urllib.request.Request(c.base + "/api/part/upload", data=data, method="POST",
                                 headers={"X-Filename": "a.step"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=40)
    assert e.value.code == 403


def test_upload_from_start_page_creates_import(part_app, tmp_path):
    c, _ = part_app(None)
    req = urllib.request.Request(c.base + "/api/part/upload", data=(DATA / "climber.step").read_bytes(),
                                 method="POST", headers={"X-Scrimmage-Token": c.token, "X-Filename": "lift.step"})
    with urllib.request.urlopen(req, timeout=40) as r:
        assert r.status == 200
    assert (tmp_path / "parts" / "lift.source" / "lift.step").is_file()
    imp = read_import(tmp_path / "parts" / "lift.import.toml")
    assert imp.source == "lift.source/lift.step" and imp.name == "lift"
    assert c.get("/api/app/state")[1]["mode"] == "part"
    _built(c)
    assert (tmp_path / "parts" / "lift" / "part.toml").is_file()


def test_app_new_import(part_app, tmp_path):
    c, step = part_app(None)
    robot_dir = tmp_path / "robot"
    robot_dir.mkdir()
    shutil.copy(Path(__file__).resolve().parent.parent / "configs" / "kitbot.toml", robot_dir / "bot.toml")
    s, b = _post(c, "/api/app/new", {"kind": "import", "path": "climber.step", "robot": "robot/bot.toml"})
    assert s == 200, b
    assert (robot_dir / "parts" / "climber.import.toml").is_file()
    assert read_import(robot_dir / "parts" / "climber.import.toml").source == "../../climber.step"
    _built(c)
    assert (robot_dir / "parts" / "climber" / "part.toml").is_file()
    assert _post(c, "/api/app/new", {"kind": "import", "path": "nope.step"})[0] == 400


def test_new_import_skips_a_part_folder_that_is_already_there(part_app, tmp_path):
    """parts/climber/ made by the FreeCAD add-on: a new import of climber.step is climber_2, so its first build works."""
    plugin = tmp_path / "parts" / "climber"
    plugin.mkdir(parents=True)
    (plugin / "part.toml").write_text("[part]\nname = \"climber\"\n")
    c, _ = part_app()
    st = _wait_for(c, lambda st: (tmp_path / "parts" / "climber_2" / "part.toml").is_file()
                   or st["build"]["state"] == "failed")
    assert st["build"]["state"] != "failed", st["build"]
    assert (tmp_path / "parts" / "climber_2.import.toml").is_file()
    assert not (tmp_path / "parts" / "climber.import.toml").exists()
    assert (tmp_path / "parts" / "climber_2" / "part.toml").is_file()
    assert (plugin / "part.toml").read_text() == "[part]\nname = \"climber\"\n"
    assert part_session.free_import(tmp_path / "parts", "climber").name == "climber_3.import.toml"


PLUGIN = """
[part]
name = "arm"
format = 1

[[body]]
name = "base"
mass = 2.0
geom = [{ box = [0.2, 0.2, 0.1] }]

[[body]]
name = "arm"
parent = "base"
pos = [0, 0, 0.1]
mass = 1.0
com = [0.1, 0, 0]
inertia = [0.01, 0.02, 0.03]
geom = [{ mesh = "meshes/arm.obj", collide = false }]
joint = { name = "pivot", hinge = "y", range = [-90, 90] }
"""


def _plugin_folder(tmp_path) -> Path:
    d = tmp_path / "arm"
    (d / "meshes").mkdir(parents=True)
    (d / "part.toml").write_text(PLUGIN)
    v = np.array([[x, y, z] for x in (0, 0.4) for y in (0, 0.1) for z in (0, 0.05)], dtype=float)
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    write_obj(d / "meshes" / "arm.obj", v, f)
    return d


def test_plugin_folder_mode_edits_overlay(part_app, tmp_path):
    d = _plugin_folder(tmp_path)
    assert classify(d) == "import" and classify(tmp_path) == "session"
    c, _ = part_app(d)
    st = _state(c)
    assert st["mode"] == "plugin" and st["name"] == "arm" and st["pieces"] == []
    assert [b["name"] for b in st["bodies"]] == ["base", "arm"]
    s, st = _edit(c, "set_mass", body="arm", kg=2.5)
    assert s == 200, st
    assert tomllib.loads((d / "scrimmage-edits.toml").read_text()) == {"body": {"arm": {"mass": 2.5}}}
    arm = next(b for b in st["bodies"] if b["name"] == "arm")
    assert arm["mass"] == pytest.approx(2.5) and arm["typed_mass"] == 2.5 and arm["changed"] == ["mass"]
    base = next(b for b in st["bodies"] if b["name"] == "base")
    assert base["typed_mass"] is None and base["changed"] == []
    assert load_part(d).bodies[1].mass == pytest.approx(2.5)
    s, st = _edit(c, "set_joint", body="arm", joint={"range": [-45, 45]})
    assert s == 200, st
    assert load_part(d).bodies[1].joint.range == (-45, 45)
    s, b = _edit(c, "set_material", pieces=["x"], material="steel")
    assert s == 400 and b["message"] == "per-piece materials need the STEP: import it instead"
    s, b = _edit(c, "set_mass", piece="x", kg=1.0)
    assert s == 400 and b["message"] == "per-piece materials need the STEP: import it instead"
    s, b = _edit(c, "set_mass", body="nope", kg=1.0)
    assert s == 400 and b["field"] == "body"
    st = _state(c)
    s, st = _post(c, "/api/part/undo", {"version": st["version"]})
    assert s == 200 and load_part(d).bodies[1].joint.range == (-90, 90)
    s, v = c.get("/api/part/view")
    assert s == 200 and [p["key"] for p in v["pieces"]] == ["arm"]
    vert, _ = _decode(v["pieces"][0])
    assert vert[:, 2].min() == pytest.approx(0.1)        # the arm body sits 0.1 m up
    assert _post(c, "/api/part/reimport", {"version": st["version"]})[0] == 400


def test_import_made_folder_opens_its_import(part_app, tmp_path):
    c, _ = part_app()
    _built(c)
    s, _ = _post(c, "/api/app/open", {"path": "parts/climber"})
    assert s == 200
    st = _state(c)
    assert st["mode"] == "import" and c.get("/api/app/state")[1]["path"] == str(tmp_path / "parts" /
                                                                                 "climber.import.toml")


def test_missing_extra_reported_not_crash(part_app, monkeypatch, tmp_path):
    def missing(*a, **k):
        raise CadError(MISSING_EXTRA)
    monkeypatch.setattr(part_session, "load_source", missing)
    c, _ = part_app()
    st = _state(c)
    assert st["read_only_reason"] == MISSING_EXTRA and st["build"]["state"] == "failed"
    assert st["build"]["message"] == MISSING_EXTRA and st["pieces"] == []
    assert _edit(c, "set_material", pieces=[HOOK], material="steel")[0] == 409
    assert c.get("/api/part/view") == (200, {"pieces": []})
    monkeypatch.undo()
    monkeypatch.chdir(tmp_path)
    s, st = _post(c, "/api/part/reimport", {"version": st["version"]})
    assert s == 200 and st["read_only_reason"] is None and len(st["bodies"]) == 5


def test_part_threads_stop(tmp_path):
    shutil.copy(DATA / "climber.step", tmp_path / "climber.step")
    app = App(tmp_path / "climber.step", open_browser=False)
    app.start()
    assert [t for t in threading.enumerate() if t.name.startswith("part-")]
    app.stop()
    assert not [t for t in threading.enumerate() if t.name.startswith("part-")]


def test_frame_stays_unchecked_until_confirmed(part_app):
    """Changing the frame doesn't check it: the checklist keeps asking, after a reopen too, until confirmed = true."""
    c, _ = part_app()
    _built(c)
    ask = "Check which way is up and forward for the robot"
    s, st = _edit(c, "set_frame", up="+y")
    assert s == 200 and st["frame"]["confirmed"] is False
    assert any(i["text"] == ask for i in st["checklist"])
    assert _post(c, "/api/app/open", {"path": "climber.step"})[0] == 200
    st = _built(c)
    assert st["frame"]["up"] == "+y" and st["frame"]["confirmed"] is False
    s, st = _edit(c, "set_frame", confirmed=True)
    assert s == 200 and st["frame"]["confirmed"] is True and st["frame"]["up"] == "+y"
    assert not any(i["text"] == ask for i in st["checklist"])
    assert _post(c, "/api/app/open", {"path": "climber.step"})[0] == 200
    assert _built(c)["frame"]["confirmed"] is True


def test_view_is_robot_coordinates_after_frame_change(part_app):
    c, _ = part_app()
    _built(c)
    src = load_source(DATA / "climber.step")
    hook = next(p for p in src.pieces if p.key == HOOK)
    s, b = _edit(c, "set_frame", up="+y", front="+x", floor=0.0, center=[0.0, 0.0])
    assert s == 200, b
    s, v = c.get("/api/part/view")
    assert s == 200
    got = next(p for p in v["pieces"] if p["key"] == HOOK)
    vert, face = _decode(got)
    from scrimmage.cad.frame import RobotFrame
    R, t = to_robot(RobotFrame("+y", "+x", 0.0, (0.0, 0.0)))
    assert np.allclose(vert, hook.v @ R.T + t, atol=1e-5) and np.array_equal(face, hook.f)
    assert c.get("/api/part/view")[1] is v or c.get("/api/part/view")[1] == v


def _binary_stl(path: Path, lo, size) -> None:
    v = np.array([[x, y, z] for x in (0, size) for y in (0, size) for z in (0, size)], dtype=float) + lo
    f = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
         [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    out = bytearray(80) + struct.pack("<I", len(f))
    for tri in f:
        out += struct.pack("<3f", 0, 0, 0)
        for i in tri:
            out += struct.pack("<3f", *v[i])
        out += b"\0\0"
    path.write_bytes(bytes(out))


def test_large_assembly_stays_quick(tmp_path):
    folder = tmp_path / "big"
    folder.mkdir()
    for i in range(400):
        _binary_stl(folder / f"plate_{i:03d}.stl", np.array([i % 20, i // 20, 0]) * 0.05, 0.04)
    app = App(None, open_browser=False)
    c = Client(app, app.start())
    try:
        t0 = time.monotonic()
        s, b = c.call("POST", "/api/app/new", {"kind": "import", "path": "big"})
        assert s == 200, b
        st = _state(c)
        s, v = c.get("/api/part/view")
        took = time.monotonic() - t0
        assert s == 200 and len(st["pieces"]) == 400 and len(v["pieces"]) == 400
        if not os.environ.get("CI"):
            assert took < 5.0, took
        _built(c)
    finally:
        app.stop()


def test_rapid_edits_latest_wins(part_app, tmp_path):
    c, _ = part_app()
    _built(c)
    for mat in ("steel", "nylon", "pla", "hdpe", "steel"):
        assert _edit(c, "set_material", pieces=[HOOK], material=mat)[0] == 200
    st = _wait_for(c, lambda st: st["build"]["state"] == "idle")
    imp = read_import(tmp_path / "parts" / "climber.import.toml")
    assert imp.pieces[HOOK].material == "steel" and imp.written == {"climber": "climber"}
    assert sum(b.mass for b in load_part(tmp_path / "parts" / "climber").bodies) == pytest.approx(
        st["parts"][0]["mass"])


def _upload(c, files, again=False):
    """POST /api/part/upload with [(name, bytes)]: one file as X-Filename, several as X-Files. (status, body)."""
    headers = {"X-Scrimmage-Token": c.token}
    if len(files) == 1:
        headers["X-Filename"] = quote(files[0][0])
    else:
        headers["X-Files"] = quote(json.dumps([{"name": n, "size": len(b)} for n, b in files]))
    if again:
        headers["X-Import-Again"] = "1"
    req = urllib.request.Request(c.base + "/api/part/upload", data=b"".join(b for _, b in files), method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


STL = DATA / "stl"


def _stls(*names):
    return [(f"{n}.stl", (STL / f"{n}.stl").read_bytes()) for n in names]


def test_several_stls_from_the_start_page_are_one_part(part_app, tmp_path):
    c, _ = part_app(None)
    s, st = _upload(c, _stls("base_plate", "arm_A", "hook"))
    assert s == 200, st
    folder = tmp_path / "parts" / "stl_parts.source" / part_session.MESH_FOLDER
    assert sorted(p.name for p in folder.iterdir()) == ["arm_A.stl", "base_plate.stl", "hook.stl"]
    assert read_import(tmp_path / "parts" / "stl_parts.import.toml").source == "stl_parts.source/meshes"
    assert {p["key"] for p in st["pieces"]} == {"base_plate", "arm_A", "hook"}
    assert st["source_copied"] is True


def test_several_stls_dropped_on_an_open_part_replace_its_source(part_app, tmp_path):
    c, _ = part_app(None)
    s, st = _upload(c, _stls("base_plate", "arm_A"))
    assert s == 200, st
    s, st = _edit(c, "set_units", units="in")
    assert s == 200, st
    # the next drop holds a different set: the old files go, and the units stay (the same folder)
    s, st = _upload(c, _stls("arm_A", "hook"))
    assert s == 200, st
    folder = tmp_path / "parts" / "stl_parts.source" / part_session.MESH_FOLDER
    assert sorted(p.name for p in folder.iterdir()) == ["arm_A.stl", "hook.stl"]
    assert {p["key"] for p in st["pieces"]} == {"arm_A", "hook"} and st["units"] == "in"
    assert not [p for p in folder.parent.iterdir() if p.name.startswith(".")]


@pytest.mark.parametrize("files, says", [
    ([("a.step", b"x"), ("b.step", b"y")], "Use one STEP file at a time"),
    ([("a.step", b"x"), ("b.stl", b"y")], "Use a STEP file on its own, or only STL and OBJ files"),
    ([("a.stl", b"x"), ("notes.txt", b"y")], "notes.txt is not a STEP, STL or OBJ file"),
    ([("a.stl", b"x"), ("A.STL", b"y")], "Two of the files are called"),
])
def test_several_files_that_dont_go_together_are_refused(part_app, tmp_path, files, says):
    for path in (None, "step"):
        c, _ = part_app(path)
        s, b = _upload(c, files)
        assert s == 400 and says in b["error"], b
    assert not (tmp_path / "parts" / "a.source").exists()
    assert not (tmp_path / "parts" / "climber.source").exists()


def test_import_again_on_a_copied_source_keeps_the_settings(part_app, tmp_path):
    """A file sent from the chooser is copied in: Import again sends the new export (X-Import-Again) in its place and
    reconciles the settings as an ordinary re-import does. An import opened from a path is read from that path."""
    c, _ = part_app(None)
    s, st = _upload(c, [("climber.step", (DATA / "climber.step").read_bytes())])
    assert s == 200 and st["source_copied"] is True
    _edit(c, "set_material", pieces=[HOOK], material="steel")
    s, st = _upload(c, [("climber.step", (DATA / "climber_v2.step").read_bytes())], again=True)
    assert s == 200, st
    copy = tmp_path / "parts" / "climber.source" / "climber.step"
    assert copy.read_bytes() == (DATA / "climber_v2.step").read_bytes()
    assert {p["key"] for p in st["pieces"]} == {p.key for p in load_source(DATA / "climber_v2.step").pieces}
    assert st["messages"]
    hook = next((p for p in st["pieces"] if p["key"] == HOOK), None)
    assert hook is None or hook["material"] == "steel"
    assert st["source_copied"] is True


def test_import_again_on_copied_stls_keeps_the_typed_units(part_app, tmp_path):
    c, _ = part_app(None)
    assert _upload(c, [("arm_A.stl", (STL / "arm_A.stl").read_bytes())])[0] == 200
    s, st = _edit(c, "set_units", units="in")
    assert s == 200, st
    # the new export has another name: as Import again, its units are still the ones typed
    s, st = _upload(c, [("arm_A v2.stl", (STL / "arm_A.stl").read_bytes())], again=True)
    assert s == 200 and st["units"] == "in", st


def test_a_source_opened_from_a_path_is_not_copied(part_app):
    c, _ = part_app()
    assert _state(c)["source_copied"] is False
