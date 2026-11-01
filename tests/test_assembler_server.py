import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scrimmage.assembler import templates, validate
from scrimmage.assembler.server import App

ROOT = Path(__file__).resolve().parent.parent


class Client:
    def __init__(self, app, url):
        self.app = app
        self.base = url.split("#")[0].rstrip("/")
        self.token = url.split("#token=")[1]

    def call(self, method, path, body=None, token="auto", timeout=40):
        headers = {}
        if token == "auto":
            token = self.token
        if token is not None:
            headers["X-Scrimmage-Token"] = token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, body=None, **kw):
        body = dict({} if body is None else body)
        if path not in ("/api/robot/folder", "/api/scenario/stop") and body.get("version", "auto") == "auto":
            # the version the page last saw
            body["version"] = (self.app.scenario.version if path.startswith("/api/scenario/")
                               else self.app.robot_version)
        elif body.get("version") is None:
            body.pop("version", None)
        return self.call("POST", path, body, **kw)


@pytest.fixture
def served(tmp_path):
    started = []

    def make(src="configs/kitbot.toml", name="robot.toml", text=None):
        cfg = tmp_path / name
        if text is None:
            shutil.copy(ROOT / src, cfg)
        else:
            cfg.write_text(text)
        app = App(cfg, open_browser=False)
        started.append(app)
        return Client(app, app.start()), cfg
    yield make
    for a in started:
        a.stop()


def test_old_routes_are_gone(served):
    c, _ = served()
    for path in ("/api/state", "/api/view", "/api/library"):
        assert c.get(path)[0] == 404, path
    for path in ("/api/edit", "/api/undo", "/api/redo", "/api/folder"):
        st, r = c.call("POST", path, {"version": 0, "op": "remove", "name": "dt", "path": "."})
        assert st == 404 and r["ok"] is False and r["errors"][0]["field"] == "status", path
    # a robot config has no scenario routes
    assert c.get("/api/scenario/state")[0] == 404
    assert c.call("POST", "/api/scenario/run", {"version": 0})[0] == 404


def test_cli_assemble_is_gone(tmp_path):
    r = subprocess.run([sys.executable, "-m", "scrimmage", "assemble", "r.toml"], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 2 and "invalid choice: 'assemble'" in r.stderr


def test_token_required(served):
    c, _ = served()
    assert c.get("/api/robot/state", token=None)[0] == 403
    assert c.get("/api/robot/state", token="nope")[0] == 403
    assert c.post("/api/robot/edit", {"op": "remove", "name": "dt"}, token=None)[0] == 403
    assert c.get("/api/robot/state")[0] == 200
    assert c.app._httpd.server_address[0] == "127.0.0.1"


def test_static_index_and_traversal(served):
    c, _ = served()
    st, body = c.get("/", token=None)
    assert st == 200 and b"<html" in body
    assert c.get("/../server.py", token=None)[0] == 404
    assert c.get("/%2e%2e/server.py", token=None)[0] == 404
    assert c.get("/nothing.js", token=None)[0] == 404


def test_vendored_three_is_served_and_pinned(served):
    """The 3D view loads three.js from static/vendor through an importmap: nothing comes from a CDN."""
    import re
    c, _ = served()
    st, index = c.get("/", token=None)
    imap = re.search(rb'<script type="importmap">(.*?)</script>', index, re.S)
    assert st == 200 and imap, "index.html needs an importmap"
    assert json.loads(imap.group(1))["imports"]["three"] == "./vendor/three.module.min.js"
    for f in ("vendor/three.module.min.js", "vendor/three.core.min.js", "vendor/OrbitControls.js", "view3d.js"):
        req = urllib.request.Request(c.base + "/" + f)
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200 and r.headers.get_content_type() == "text/javascript", f
    vendor = ROOT / "scrimmage/assembler/static/vendor"
    assert "MIT License" in (vendor / "LICENSE").read_text()
    readme = (vendor / "README").read_text()
    version = re.search(r"three (\d+)\.(\d+)\.(\d+)", readme)
    assert version and re.search(r"sha256:\s+[0-9a-f]{64}", readme)
    # the core build opens with its REVISION constant (minified: const t="185")
    assert re.match(r"/\*\*.*?\*/\s*const \w+=\"(\d+)\"", (vendor / "three.core.min.js").read_text(), re.S).group(1) == version.group(2)
    static = ROOT / "scrimmage/assembler/static"
    for f in [*static.glob("*.js"), *static.glob("*.html"), *static.glob("*.css")]:
        assert not re.search(r"https?://", f.read_text()), f"{f.name} must not load anything remote"


def test_state_content(served):
    c, cfg = served()
    st, s = c.get("/api/robot/state")
    assert st == 200 and s["version"] == 0 and s["path"] == str(cfg.resolve())
    assert not s["read_only"] and s["read_only_reason"] is None
    assert [p["name"] for p in s["parts"]] == ["drive", "dt"]
    drive, dt = s["parts"]
    assert drive["stock"] == "chassis" and drive["attach"] is None and drive["pos"] == [0, 0, 0]
    assert drive["params"]["length"] == 0.66 and "ids" not in drive["params"]
    assert dt["attach"] == "drive.center" and dt["ids"]["left"] == "PWM:0"
    assert "[robot_program]" in s["other_sections"] and "\n[[part]]" not in s["other_sections"]
    assert s["errors"] == [] and s["can_undo"] is False and s["can_redo"] is False
    assert s["mass_kg"] > 40 and s["bodies"] > 5 and s["joints"] > 0
    assert s["part_bodies"]["drive"] == ["chassis"]
    assert "left_2" in s["part_bodies"]["dt"] and set(s["part_bodies"]) == {"drive", "dt"}
    assert s["scenario_open"] is False   # the page shows no Scenario tab


def test_view_and_library(served):
    c, _ = served()
    st, v = c.get("/api/robot/view")
    assert st == 200 and v["instances"] == ["drive", "dt"] and v["geoms"]
    st, lib = c.get("/api/robot/library")
    assert st == 200 and any(s["name"] == "chassis" for s in lib["stock"]) and "folders" in lib


def test_add_edit_writes_file_and_bumps_version(served):
    c, cfg = served()
    st, r = c.post("/api/robot/edit", {"op": "add", "name": "arm", "stock": "chassis", "attach": "drive.nowhere"})
    assert r["ok"] is False and r["errors"][0]["field"] == "arm/attach"
    assert c.get("/api/robot/state")[1]["version"] == 0
    st, lib = c.get("/api/robot/library")
    stock = next(s for s in lib["stock"] if s["name"] == "differential")
    before = cfg.read_text()
    st, r = c.post("/api/robot/edit", {"op": "add", "name": "dt2", "stock": "differential", "attach": "drive.center",
                                 "pos": [0, 0, 0], "params": {}, "ids": {"left": "PWM:2", "right": "PWM:3"}})
    assert st == 200 and r["ok"] is True and r["version"] == 1
    after = cfg.read_text()
    assert after != before and 'name = "dt2"' in after
    st, s = c.get("/api/robot/state")
    assert [p["name"] for p in s["parts"]][-1] == "dt2" and s["can_undo"]
    assert stock["params"]  # library carries param metadata used for defaults


def test_remaining_ops(served):
    c, cfg = served()
    assert c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 40.0}})[1]["ok"]
    assert "mass = 40.0" in cfg.read_text()
    assert c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": None}})[1]["ok"]
    assert "mass" not in cfg.read_text().split("[[part]]", 1)[1].split("[[part]]")[0].replace("ids", "")
    assert c.post("/api/robot/edit", {"op": "attach", "name": "dt", "attach": "drive.center", "pos": [0, 0, 0.01],
                                "rot": [0, 0, 0]})[1]["ok"]
    assert c.post("/api/robot/edit", {"op": "rename", "name": "dt", "new_name": "wheels"})[1]["ok"]
    assert 'name = "wheels"' in cfg.read_text()
    assert c.post("/api/robot/edit", {"op": "ids", "name": "wheels", "ids": {"left": "PWM:4", "right": "PWM:1"}})[1]["ok"]
    assert "PWM:4" in cfg.read_text()
    assert c.post("/api/robot/edit", {"op": "remove", "name": "wheels"})[1]["ok"]
    assert 'name = "wheels"' not in cfg.read_text()
    st, r = c.post("/api/robot/edit", {"op": "bogus"})
    assert st == 400


def test_invalid_ids_edit_returns_field_errors_and_keeps_file(served):
    c, cfg = served()
    before = cfg.read_bytes()
    st, r = c.post("/api/robot/edit", {"op": "ids", "name": "dt", "ids": {"left": "PWM:0", "right": "PWM:0"}})
    assert st == 200 and r["ok"] is False
    assert {e["field"] for e in r["errors"]} >= {"dt/ids:left", "dt/ids:right"}
    assert all(set(e) == {"field", "message"} for e in r["errors"])
    st, r = c.post("/api/robot/edit", {"op": "ids", "name": "dt", "ids": {"nope": "PWM:5"}})
    assert r["ok"] is False and r["errors"][0]["field"] == "dt/ids:nope"
    st, r = c.post("/api/robot/edit", {"op": "rename", "name": "dt", "new_name": "bad name"})
    assert r["ok"] is False and r["errors"][0]["field"] == "dt/name"
    assert cfg.read_bytes() == before
    assert c.get("/api/robot/state")[1]["version"] == 0


def test_undo_redo(served):
    c, cfg = served()
    original = cfg.read_text()
    c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 40.0}})
    edited = cfg.read_text()
    st, r = c.post("/api/robot/undo")
    assert r["ok"] and cfg.read_text() == original
    s = c.get("/api/robot/state")[1]
    assert s["can_redo"] and not s["can_undo"]
    assert c.post("/api/robot/redo")[1]["ok"] and cfg.read_text() == edited
    assert c.post("/api/robot/redo")[1]["ok"] is False


def test_long_poll_returns_on_other_threads_edit(served):
    c, _ = served()
    out = {}

    def poll():
        t0 = time.monotonic()
        out["state"] = c.get("/api/robot/state?since=0")[1]
        out["dt"] = time.monotonic() - t0
    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.5)
    assert th.is_alive()
    c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 40.0}})
    th.join(10)
    assert out["state"]["version"] == 1 and out["dt"] < 5
    # a stale `since` returns at once
    t0 = time.monotonic()
    assert c.get("/api/robot/state?since=0")[1]["version"] == 1 and time.monotonic() - t0 < 1


def test_long_poll_picks_up_external_change(served):
    c, cfg = served()
    out = {}
    th = threading.Thread(target=lambda: out.update(s=c.get("/api/robot/state?since=0")[1]))
    th.start()
    time.sleep(0.3)
    cfg.write_text(cfg.read_text().replace("mass = 47.0", "mass = 48.0"))
    th.join(10)
    assert out["s"]["version"] == 1 and out["s"]["parts"][0]["params"]["mass"] == 48.0


def test_external_change_reloads_and_rejects_stale_edit(served):
    c, cfg = served()
    c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 40.0}})
    cfg.write_text(cfg.read_text().replace("mass = 40.0", "mass = 41.0"))
    st, r = c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 42.0}})
    assert st == 409 and "changed on disk" in r["error"]
    s = c.get("/api/robot/state")[1]
    assert s["parts"][0]["params"]["mass"] == 41.0 and not s["can_undo"] and s["version"] == 2
    assert "mass = 41.0" in cfg.read_text()
    assert c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 42.0}})[0] == 200


def test_stale_version_rejected(served):
    c, _ = served()
    c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 40.0}})
    st, r = c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 42.0}, "version": 0})
    assert st == 409 and "another tab" in r["error"]
    st, r = c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 42.0}, "version": None})
    assert st == 400 and r["ok"] is False and r["errors"][0]["field"] == "status"
    assert c.post("/api/robot/undo", {"version": None})[0] == 400


def test_mjcf_config_is_read_only(served):
    c, cfg = served(text=(ROOT / "tests" / "fake_robot.toml").read_text().replace(
        'mjcf = "models/kitbot.xml"', f'mjcf = "{ROOT / "tests" / "models" / "kitbot.xml"}"'))
    s = c.get("/api/robot/state")[1]
    assert s["read_only"] and "hand-written model" in s["read_only_reason"]
    assert s["part_bodies"] is None and s["com"] is None
    assert c.get("/api/robot/view")[1] == {}
    before = cfg.read_bytes()
    st, r = c.post("/api/robot/edit", {"op": "remove", "name": "x"})
    assert st == 409 and "hand-written" in r["error"]
    assert c.post("/api/robot/undo")[0] == 409
    assert cfg.read_bytes() == before


def test_invalid_config_is_read_only_with_errors(served):
    c, _ = served(text='[[part]]\nname = "drive"\nstock = "chassis"\nids = { gyro = "Pigeon2:1", extra = "TalonFX:1" }\n')
    s = c.get("/api/robot/state")[1]
    assert s["read_only"] and s["errors"] and "extra" in s["read_only_reason"]
    assert s["part_bodies"] is None and s["com"] is None
    assert c.get("/api/robot/view")[1] == {}
    assert [p["name"] for p in s["parts"]] == ["drive"]


def test_folder_endpoint(served, tmp_path):
    c, _ = served()
    parts = tmp_path / "myparts"
    (parts / "thing").mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "parts" / "intake" / "part.toml", parts / "thing" / "part.toml")
    assert c.post("/api/robot/folder", {"path": str(parts)})[1]["ok"]
    assert c.post("/api/robot/folder", {"path": "myparts"})[1]["ok"]   # relative to the config's directory
    assert len(c.app.extra_folders) == 1
    folders = c.get("/api/robot/library")[1]["folders"]
    assert [f["path"] for f in folders if f["path"].endswith("thing")] == ["myparts/thing"]
    assert c.post("/api/robot/folder", {"path": str(tmp_path / "missing")})[0] == 400


def test_host_guard(served):
    import http.client
    c, _ = served()
    conn = http.client.HTTPConnection("127.0.0.1", c.app.port, timeout=5)
    conn.request("GET", "/api/robot/state", headers={"X-Scrimmage-Token": c.token, "Host": "evil.example"})
    assert conn.getresponse().status == 403
    conn.close()


def test_unparsable_toml_is_read_only(served):
    c, cfg = served(text="[[part\n")
    s = c.get("/api/robot/state")[1]
    assert s["read_only"] and "not valid TOML" in s["read_only_reason"] and s["parts"] == []
    assert c.post("/api/robot/edit", {"op": "remove", "name": "x"})[0] == 409


def test_invalid_config_becomes_editable_after_fix_on_disk(served):
    bad = '[[part]]\nname = "drive"\nstock = "chassis"\nids = { gyro = "Pigeon2:1", extra = "TalonFX:1" }\n'
    c, cfg = served(text=bad)
    assert c.get("/api/robot/state")[1]["read_only"]
    cfg.write_text(bad.replace(', extra = "TalonFX:1"', ""))
    s = c.get("/api/robot/state?since=0")[1]
    assert not s["read_only"] and s["errors"] == [] and s["version"] == 1
    assert c.post("/api/robot/edit", {"op": "params", "name": "drive", "params": {"mass": 40.0}})[1]["ok"]


def test_undo_revalidates_before_writing(served, tmp_path):
    folder = tmp_path / "gadget"
    shutil.copytree(ROOT / "tests" / "parts" / "intake", folder)
    c, cfg = served()
    assert c.post("/api/robot/edit", {"op": "add", "name": "g", "path": "gadget", "attach": "drive.top"})[1]["ok"], \
        c.app.result.errors
    assert c.post("/api/robot/edit", {"op": "remove", "name": "g"})[1]["ok"]
    shutil.rmtree(folder)
    before = cfg.read_bytes()
    st, r = c.post("/api/robot/undo")
    assert st == 200 and r["ok"] is False and r["errors"]
    # the robot's own errors (the missing part folder), not the hand-written message
    assert all(e["message"].startswith("can't undo: ") for e in r["errors"]), r
    assert not any("hand-written" in e["message"] for e in r["errors"]), r
    assert any(e["field"].startswith("g/") or "gadget" in e["message"] for e in r["errors"]), r
    assert cfg.read_bytes() == before
    s = c.get("/api/robot/state")[1]
    assert not s["read_only"] and s["can_undo"]
    assert c.post("/api/robot/redo")[1] == {"ok": False, "errors": [{"field": "status", "message": "nothing to redo"}]}


def test_commit_aborts_if_file_changed_just_before_replace(served, tmp_path):
    from scrimmage.assembler.doc import ConfigDoc, FileChanged
    cfg = tmp_path / "r.toml"
    shutil.copy(ROOT / "configs" / "kitbot.toml", cfg)
    d = ConfigDoc(cfg)
    cfg.write_text(cfg.read_text() + "\n# outside edit\n")
    outside = cfg.read_bytes()
    with pytest.raises(FileChanged):
        d.commit(d.text.replace("mass = 47.0", "mass = 40.0"))
    assert cfg.read_bytes() == outside


def test_cli_port_errors(tmp_path):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    try:
        r = subprocess.run([sys.executable, "-m", "scrimmage", "edit", "--new", "swerve", "r.toml",
                            "--no-browser", "--port", str(port)], cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 2 and f"port {port} is in use" in r.stderr
    finally:
        s.close()
    r = subprocess.run([sys.executable, "-m", "scrimmage", "edit", "r2.toml", "--port", "70000"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 2 and "port must be 0-65535" in r.stderr


@pytest.mark.parametrize("kind", ["swerve", "swerve-rev", "differential"])
def test_new_templates_validate(kind, tmp_path):
    text = templates.new_config(kind)
    res = validate.check(text, tmp_path / "robot.toml")
    assert res.ok, res.errors
    assert "# [robot_program]" in text and "\n[robot_program]" not in text
    assert [p for p in ("swerve/MK4i",) if p in text] == (["swerve/MK4i"] if kind == "swerve" else [])
    assert ("swerve/MAXSwerve" in text) == (kind == "swerve-rev")
    with pytest.raises(ValueError):
        templates.new_config("tank")


def test_swerve_rev_template_binds_the_maxswerve_template_ids(tmp_path):
    from scrimmage.assembler import validate as v
    text = templates.new_config("swerve-rev")
    res = v.check(text, tmp_path / "robot.toml")
    assert res.ok, res.errors
    import tomllib
    parts = {p["name"]: p for p in tomllib.loads(text)["part"]}
    # CAN ids and the magnet position each kXxxChassisAngularOffset (-pi/2, 0, pi, pi/2) expects
    want = {"fl": (11, 10, 0.25), "bl": (13, 12, -0.5), "fr": (15, 14, 0.0), "br": (17, 16, -0.25)}
    for name, (drive, steer, offset) in want.items():
        assert parts[name]["stock"] == "swerve/MAXSwerve"
        ids = parts[name]["ids"]
        assert (ids["drive"], ids["steer"]) == (f"SparkMax:{drive}", f"SparkMax:{steer}")
        enc = ids["encoder"] if isinstance(ids["encoder"], dict) else {"rev": ids["encoder"]}
        assert enc["rev"] == f"SparkMaxAbs:{steer}" and enc.get("offset", 0.0) == offset
    # WPILib's ADIS16470_IMU: SimDevice "Gyro:ADIS16470[0]", yaw on Z, counter-clockwise positive
    assert parts["drive"]["ids"]["gyro"] == {"device": "ADIS16470[0]", "angle": "gyro_angle_z", "rate": "gyro_rate_z",
                                             "cw_positive": False}
    # configs/maxswerve.toml (examples/java_maxswerve's config) is this template plus the robot program
    example = tomllib.loads((ROOT / "configs" / "maxswerve.toml").read_text())
    assert example["part"] == tomllib.loads(text)["part"]
    chassis = parts["drive"]
    spacing = chassis.get("length", 0.7) - 2 * chassis.get("module_inset", 0.0579)
    assert spacing == pytest.approx(26.5 * 0.0254, abs=1e-6)
    assert chassis.get("width", 0.7) - 2 * chassis.get("module_inset", 0.0579) == pytest.approx(26.5 * 0.0254, abs=1e-6)


def _cli(*args, cwd):
    return subprocess.Popen([sys.executable, "-m", "scrimmage", "edit", *args], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, "BROWSER": "false"})


def test_cli_new_refuses_overwrite_and_missing(tmp_path):
    p = _cli("--new", "swerve", "r.toml", "--no-browser", cwd=tmp_path)
    try:
        url = None
        for line in p.stdout:
            if line.startswith("http://"):
                url = line.strip()
                break
        assert url and url.startswith("http://127.0.0.1:") and "/#token=" in url
        assert (tmp_path / "r.toml").read_text() == templates.new_config("swerve")
        req = urllib.request.Request(url.split("#")[0] + "api/robot/state", headers={"X-Scrimmage-Token": url.split("#token=")[1]})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert json.loads(r.read())["parts"][0]["name"] == "drive"
    finally:
        p.send_signal(signal.SIGINT)
        try:
            assert p.wait(15) == 0
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
            raise
    again = subprocess.run([sys.executable, "-m", "scrimmage", "edit", "--new", "swerve", "r.toml", "--no-browser"],
                           cwd=tmp_path, capture_output=True, text=True)
    assert again.returncode == 2 and "already exists" in again.stderr
    missing = subprocess.run([sys.executable, "-m", "scrimmage", "edit", "nope.toml", "--no-browser"],
                             cwd=tmp_path, capture_output=True, text=True)
    assert missing.returncode == 2 and "create one with --new swerve, --new swerve-rev, --new differential or --new-scenario --robot CONFIG" in missing.stderr


def test_library_finds_parts_folder_next_to_config(served, tmp_path):
    shutil.copytree(ROOT / "tests" / "parts" / "intake", tmp_path / "parts" / "intake")
    shutil.copytree(ROOT / "tests" / "parts" / "intake", tmp_path / "beside")
    c, _ = served()
    paths = [f["path"] for f in c.get("/api/robot/library")[1]["folders"]]
    assert sorted(paths) == ["beside", "parts/intake"]
    assert c.post("/api/robot/folder", {"path": "parts"})[1]["ok"]   # adding it as well still lists it once
    paths = [f["path"] for f in c.get("/api/robot/library")[1]["folders"]]
    assert sorted(paths) == ["beside", "parts/intake"]


def test_error_response_closes_connection(served):
    """An error answered before the body was read must not let the body be parsed as a second request."""
    import socket
    c, _ = served()
    smuggled = b"GET /api/robot/state HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Scrimmage-Token: " + c.token.encode() + b"\r\n\r\n"
    s = socket.create_connection(("127.0.0.1", c.app.port), timeout=5)
    try:
        s.sendall(b"POST /api/robot/edit HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                  b"Content-Length: " + str(len(smuggled)).encode() + b"\r\n\r\n" + smuggled)
        data = b""
        try:
            while chunk := s.recv(65536):
                data += chunk
        except socket.timeout:
            pytest.fail(f"the connection stayed open after a 403: {data[:300]!r}")
    finally:
        s.close()
    assert data.startswith(b"HTTP/1.1 403") and data.count(b"HTTP/1.1 ") == 1
    assert b"Connection: close" in data


def test_cli_stops_on_sigint_even_when_parent_ignores_it(tmp_path):
    p = subprocess.Popen([sys.executable, "-m", "scrimmage", "edit", "--new", "swerve", "r.toml", "--no-browser"],
                         cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
    try:
        for line in p.stdout:
            if line.startswith("http://"):
                break
        p.send_signal(signal.SIGINT)
        assert p.wait(15) == 0
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


@pytest.mark.parametrize("text", ["", 'title = "not a robot"\n'])
def test_empty_or_unrelated_toml_says_how_to_start(served, text):
    c, _ = served(text=text)
    s = c.get("/api/robot/state")[1]
    assert s["read_only"] and "--new-scenario --robot CONFIG" in s["read_only_reason"]
    assert "--new swerve|differential" in s["read_only_reason"]


# ---------------------------------------------------------------- the robot's CG and parts placed by their CAD

def test_state_and_view_have_the_robots_cg(served):
    c, _ = served()
    s = c.get("/api/robot/state")[1]
    assert set(s["com"]) == {"height", "forward", "left"}
    assert 0.05 < s["com"]["height"] < 0.4
    assert abs(s["com"]["forward"]) < 0.1 and abs(s["com"]["left"]) < 0.1   # the kitbot is near symmetric
    v = c.get("/api/robot/view")[1]
    # the view's com is the same point in the world: the kitbot starts at (2.0, 4.035) facing +x
    assert v["com"] == pytest.approx([2.0 + s["com"]["forward"], 4.035 + s["com"]["left"], s["com"]["height"]], abs=1e-6)


def _part_wait(c, pred, timeout=60.0):
    end = time.monotonic() + timeout
    st = c.get("/api/part/state")[1]
    while not pred(st):
        assert time.monotonic() < end, st
        st = c.get(f"/api/part/state?since={st['version']}")[1]
    return st


def _part_edit(c, op, **args):
    st = _part_wait(c, lambda st: st["build"]["state"] != "building")
    s, r = c.call("POST", "/api/part/edit", {"version": st["version"], "op": op, "args": args})
    assert s == 200, r
    return r


def _split_climber(c, cfg):
    """Import the climber for `cfg` (into parts/ next to it) and split it as the Part tab would: part `base` (the base
    plate, arm_B on a hinge) and part `right_arm` (the hook, welded to arm_B, then made its own part). Returns the
    import's state once built."""
    shutil.copy(ROOT / "tests" / "data" / "cad" / "climber.step", cfg.parent / "climber.step")
    s, r = c.call("POST", "/api/app/new", {"kind": "import", "path": str(cfg.parent / "climber.step"), "robot": str(cfg)})
    assert s == 200, r
    _part_edit(c, "set_part", body="base_plate", part="base")
    _part_edit(c, "set_joint", body="arm_B", parent="base_plate",
               joint={"kind": "hinge", "anchor": [0.24, 0.1, 0.01], "axis": [0, 1, 0]})
    _part_edit(c, "set_joint", body="hook_assy_2", parent="arm_B", joint=None)
    _part_edit(c, "set_part", body="hook_assy_2", part="right_arm")
    return _part_wait(c, lambda st: st["build"]["state"] == "idle" and len(st["parts"]) == 2
                      and all(p["com"] is not None for p in st["parts"]))


def test_parts_from_one_import_land_where_the_cad_has_them(served, tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    import mujoco
    import numpy as np
    from scrimmage.parts.assemble import assemble
    c, cfg = served()
    built = _split_climber(c, cfg)
    assert c.call("GET", "/api/app/state")[1]["back"]["path"] == str(cfg.resolve())   # opened from the robot
    assert c.call("POST", "/api/app/open", {"path": str(cfg)})[0] == 200
    lib = c.get("/api/robot/library")[1]
    folders = {f["name"]: f for f in lib["folders"]}
    iid = folders["base"]["cad"]["import_id"]
    assert folders["base"]["cad"] == {"import_id": iid, "part": "base", "attach": None}
    assert folders["right_arm"]["cad"] == {"import_id": iid, "part": "right_arm", "attach": "base.arm_B"}
    assert {s["name"]: s["root_body"] for s in lib["stock"]}["chassis"] == "chassis"
    for name, attach in (("base", "drive.chassis"), ("right_arm", "base.arm_B")):
        s, r = c.post("/api/robot/edit", {"op": "add", "name": name, "path": folders[name]["path"], "attach": attach})
        assert s == 200 and r["ok"], r
    marker = tomllib.loads((cfg.parent / folders["base"]["path"] / "import-origin.toml").read_text())
    arm_b = marker["origins"]["arm_B"]
    assert arm_b == pytest.approx([0.09, 0.0, 0.01], abs=1e-6)   # the hinge anchor, from the frame's center
    st = c.get("/api/robot/state")[1]
    parts = {p["name"]: p for p in st["parts"]}
    assert parts["base"]["pos"] == [0, 0, 0] and parts["base"]["cad_pos"] == [0.0, 0.0, 0.0]
    assert parts["right_arm"]["pos"] == pytest.approx([-x for x in arm_b])
    assert parts["right_arm"]["cad_pos"] == pytest.approx(parts["right_arm"]["pos"])
    assert parts["drive"]["cad_pos"] is None and parts["dt"]["cad_pos"] is None   # stock parts have no CAD
    assert parts["drive"]["cad"] is None
    assert parts["base"]["cad"] == {"import_id": iid, "part": "base", "hangs_on": None}
    assert parts["right_arm"]["cad"] == {"import_id": iid, "part": "right_arm", "hangs_on": "base.arm_B"}

    # every part's center of mass, in the world, is where the import put it in the robot frame
    w = assemble(tomllib.loads(cfg.read_text()), cfg.parent)
    m = w.model
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    mujoco.mj_comPos(m, d)
    chassis = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "drive.chassis")
    origin, rot = d.xpos[chassis], d.xmat[chassis].reshape(3, 3)
    for p in built["parts"]:
        ids = [b for b in range(m.nbody) if (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) or "").startswith(p["name"] + ".")]
        mass = sum(m.body_mass[b] for b in ids)
        world_com = sum(m.body_mass[b] * d.xipos[b] for b in ids) / mass
        assert mass == pytest.approx(p["mass"], rel=1e-6)
        assert world_com == pytest.approx(origin + rot @ np.array(p["com"]), abs=1e-6), p["name"]

    # moved away, "Reset to CAD" (an attach edit with cad_pos) puts it back; another target has no CAD placement
    c.post("/api/robot/edit", {"op": "attach", "name": "right_arm", "attach": "base.arm_B", "pos": [0, 0, 0.2], "rot": [0, 0, 0]})
    assert {p["name"]: p for p in c.get("/api/robot/state")[1]["parts"]}["right_arm"]["cad_pos"] == pytest.approx([-x for x in arm_b])
    c.post("/api/robot/edit", {"op": "attach", "name": "right_arm", "attach": "drive.top", "pos": [0, 0, 0], "rot": [0, 0, 0]})
    assert {p["name"]: p for p in c.get("/api/robot/state")[1]["parts"]}["right_arm"]["cad_pos"] is None
    # a part added with a pos of its own keeps it
    s, r = c.post("/api/robot/edit", {"op": "add", "name": "again", "path": folders["right_arm"]["path"], "attach": "base.arm_B",
                                      "pos": [0.1, 0, 0]})
    assert r["ok"] and {p["name"]: p for p in c.get("/api/robot/state")[1]["parts"]}["again"]["pos"] == [0.1, 0, 0]

    # an import that isn't made for this robot (its parts go elsewhere) has no way back to it
    (cfg.parent / "other").mkdir()
    shutil.copy(cfg.parent / "climber.step", cfg.parent / "other" / "climber.step")
    assert c.call("POST", "/api/app/open", {"path": str(cfg.parent / "other" / "climber.step")})[0] == 200
    assert c.call("GET", "/api/app/state")[1]["back"] is None


def _hand_part(folder, text, marker=None):
    folder.mkdir(parents=True)
    (folder / "part.toml").write_text(text)
    if marker is not None:
        (folder / "import-origin.toml").write_text(marker)


def test_a_mount_named_like_a_body_wins_over_the_cad_placement(served, tmp_path):
    """Attaching to "<part>.<name>" where the part has a mount of that name attaches to the mount, so there is no CAD
    placement; without the mount the same target is the body, at -origins[body]."""
    c, cfg = served()
    q = 'import = "x"\nimport_id = "same"\norigins = { base = [0.0, 0.0, 0.0], arm = [0.1, 0.0, 0.2] }\n'
    body = '[part]\nname = "{n}"\nformat = 1\n\n[[body]]\nname = "base"\nmass = 1.0\ngeom = [{{ box = [0.05, 0.05, 0.05] }}]\n'
    arm = '\n[[body]]\nname = "arm"\nparent = "base"\npos = [0.1, 0, 0.2]\nmass = 1.0\ngeom = [{ box = [0.02, 0.02, 0.02] }]\njoint = { name = "pivot", hinge = "y" }\n'
    _hand_part(tmp_path / "parts" / "q", body.format(n="q") + arm, q)
    _hand_part(tmp_path / "parts" / "qm", body.format(n="qm") + arm + '\n[[mount]]\nname = "arm"\nbody = "base"\n',
               q.replace('"same"', '"other"'))
    _hand_part(tmp_path / "parts" / "p", body.format(n="p"),
               'import = "x"\nimport_id = "same"\norigins = { base = [0.0, 0.0, 0.0] }\nattach = "q.arm"\n')
    _hand_part(tmp_path / "parts" / "pm", body.format(n="pm"),
               'import = "x"\nimport_id = "other"\norigins = { base = [0.0, 0.0, 0.0] }\nattach = "qm.arm"\n')
    for name, attach in (("q", "drive.chassis"), ("qm", "drive.chassis"), ("p", "q.arm"), ("pm", "qm.arm")):
        s, r = c.post("/api/robot/edit", {"op": "add", "name": name, "path": f"parts/{name}", "attach": attach})
        assert s == 200 and r["ok"], r
    parts = {p["name"]: p for p in c.get("/api/robot/state")[1]["parts"]}
    assert parts["p"]["pos"] == pytest.approx([-0.1, 0.0, -0.2]) and parts["p"]["cad_pos"] == pytest.approx([-0.1, 0.0, -0.2])
    assert parts["pm"]["pos"] == [0, 0, 0] and parts["pm"]["cad_pos"] is None     # the mount wins
    assert parts["p"]["cad"]["hangs_on"] == "q.arm" and parts["pm"]["cad"]["hangs_on"] == "qm.arm"
