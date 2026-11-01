"""The editor with nothing open, opening any file or idea folder, New, Browse and the recent list (/api/app/*)."""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scrimmage.assembler import recent
from scrimmage.assembler.server import App, classify
from test_assembler_server import Client

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "examples" / "bench"


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRIMMAGE_RECENT", raising=False)
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))   # never the user's own recent list


@pytest.fixture
def start(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    started = []

    def make(path=None):
        app = App(path, open_browser=False)
        started.append(app)
        return Client(app, app.start())
    yield make
    for a in started:
        a.stop()


def _session(tmp_path, *names) -> Path:
    folder = tmp_path / "kickoff"
    folder.mkdir()
    for n in names:
        shutil.copy(BENCH / n, folder / n)
    return folder


def test_start_mode_then_open_session(tmp_path, start):
    folder = _session(tmp_path, "bounce_bar.toml", "pinch_car.toml")
    c = start()
    s, st = c.get("/api/app/state")
    assert s == 200 and st["mode"] == "start" and st["path"] is None
    assert "bounce_bar" in st["examples"] and "pinch_car" in st["examples"]
    s, body = c.get("/api/robot/state")
    assert s == 404 and body["error"] == "no robot is open"
    s, body = c.get("/api/bench/state")
    assert s == 404 and body["error"] == "no idea session is open"
    s, body = c.call("POST", "/api/app/open", {"path": "kickoff"})
    assert s == 200 and body == {"ok": True}
    s, st = c.get("/api/app/state")
    assert st["mode"] == "bench" and st["path"] == str(folder) and st["display_path"] == "kickoff"
    s, b = c.get("/api/bench/state")
    assert s == 200 and sorted(i["file"] for i in b["ideas"]) == ["bounce_bar.toml", "pinch_car.toml"]
    assert c.get("/api/robot/state")[0] == 404
    assert [r["path"] for r in st["recent"]] == [str(folder)] and st["recent"][0]["kind"] == "session"


def test_open_robot_then_bench_file_then_robot(tmp_path, start):
    folder = _session(tmp_path, "bounce_bar.toml")
    cfg = tmp_path / "robot.toml"
    shutil.copy(ROOT / "configs" / "kitbot.toml", cfg)
    c = start(cfg)
    v0 = c.get("/api/robot/state")[1]["version"]
    assert c.get("/api/app/state")[1]["mode"] == "robot"
    assert c.get("/api/bench/state")[0] == 404
    assert c.call("POST", "/api/app/open", {"path": str(folder / "bounce_bar.toml")}) == (200, {"ok": True})
    s, b = c.get("/api/bench/state")
    assert b["stage"] == "bounce_bar.toml" and b["folder"] == str(folder) and b["version"] > v0
    assert c.call("POST", "/api/app/open", {"path": "robot.toml"}) == (200, {"ok": True})
    s, r = c.get("/api/robot/state")
    assert s == 200 and r["path"] == str(cfg) and r["version"] > b["version"] and not r["read_only"]
    assert c.get("/api/app/state")[1]["mode"] == "robot"


def test_open_refuses_what_it_cant_open(tmp_path, start):
    (tmp_path / "notes.txt").write_text("hi")
    c = start()
    for p in ("missing.toml", "notes.txt", ""):
        s, body = c.call("POST", "/api/app/open", {"path": p})
        assert s == 400, (p, body)
    assert c.get("/api/app/state")[1]["mode"] == "start"


def test_open_refused_while_drive_live(tmp_path, start, monkeypatch):
    folder = _session(tmp_path, "bounce_bar.toml")
    cfg = tmp_path / "robot.toml"
    shutil.copy(ROOT / "configs" / "kitbot.toml", cfg)
    c = start(cfg)
    st = c.app.live.status()
    monkeypatch.setattr(c.app.live, "status", lambda: {**st, "state": "running", "config": str(cfg)})
    s, body = c.call("POST", "/api/app/open", {"path": str(folder)})
    assert s == 409 and "drive session" in body["error"]
    assert c.get("/api/app/state")[1]["mode"] == "robot"


def test_edit_without_file_starts(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "SCRIMMAGE_RECENT"} | {"SCRIMMAGE_CACHE": str(tmp_path / "cache")}
    p = subprocess.Popen([sys.executable, "-m", "scrimmage", "edit", "--no-browser"], cwd=tmp_path, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = p.stdout.readline()
        assert line.startswith("http://127.0.0.1:") and "#token=" in line, p.stderr.read() if p.poll() else line
    finally:
        p.terminate()
        p.wait(10)
    r = subprocess.run([sys.executable, "-m", "scrimmage", "edit", "--new", "swerve"], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 2 and "--new" in r.stderr


def test_classify_bench_and_folder(tmp_path):
    assert classify(BENCH / "bounce_bar.toml") == "bench"
    assert classify(BENCH) == "session"
    assert classify(ROOT / "configs" / "kitbot.toml") == "robot"
    broken = tmp_path / "half.toml"
    broken.write_text('[bench]\nname = "half\n')
    assert classify(broken) == "bench"
    odd = tmp_path / "odd.toml"
    odd.write_text('config = "r.toml"\n[bench]\nname = "x"\n')
    assert classify(odd) == "scenario"


def test_new_session_and_new_robot(tmp_path, start):
    c = start()
    s, body = c.call("POST", "/api/app/new", {"kind": "session", "path": "kickoff 2027"})
    assert s == 200 and body["ok"], body
    assert (tmp_path / "kickoff 2027").is_dir()
    st = c.get("/api/app/state")[1]
    assert st["mode"] == "bench" and c.get("/api/bench/state")[1]["ideas"] == []
    s, body = c.call("POST", "/api/app/new", {"kind": "session", "path": "kickoff 2027"})
    assert s == 400 and "already exists" in body["error"]
    s, body = c.call("POST", "/api/app/new", {"kind": "robot", "path": "bot", "template": "swerve"})
    assert s == 200 and body["ok"], body
    assert (tmp_path / "bot.toml").is_file()
    r = c.get("/api/robot/state")[1]
    assert r["path"] == str(tmp_path / "bot.toml") and len(r["parts"]) == 5
    s, body = c.call("POST", "/api/app/new", {"kind": "scenario", "path": "drive.toml", "robot": "bot.toml"})
    assert s == 200 and body["ok"], body
    assert c.get("/api/scenario/state")[1]["top"]["config"] == "bot.toml"
    s, body = c.call("POST", "/api/app/new", {"kind": "robot", "path": "bot.toml", "template": "swerve"})
    assert s == 400 and "already exists" in body["error"]
    s, body = c.call("POST", "/api/app/new", {"kind": "robot", "path": "x.toml", "template": "hovercraft"})
    assert s == 400


def test_browse_lists_folders_and_kinds(tmp_path, start):
    _session(tmp_path, "bounce_bar.toml")
    shutil.copy(ROOT / "configs" / "kitbot.toml", tmp_path / "robot.toml")
    (tmp_path / "drive.toml").write_text('config = "robot.toml"\n[[step]]\nmode = "teleop"\nduration = 1\n')
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "readme.txt").write_text("x")
    c = start()
    s, b = c.get("/api/app/browse")
    assert s == 200 and b["dir"] == str(tmp_path) and b["parent"] == str(tmp_path.parent)
    # folders first, then the TOML files, each sorted; hidden entries and other files left out
    assert [(e["name"], e["kind"]) for e in b["entries"]] == [
        ("kickoff", "folder"), ("drive.toml", "scenario"), ("robot.toml", "robot")]
    assert b["entries"][0]["path"] == str(tmp_path / "kickoff")
    s, b = c.get(f"/api/app/browse?dir={tmp_path / 'kickoff'}")
    assert [(e["name"], e["kind"]) for e in b["entries"]] == [("bounce_bar.toml", "bench")]
    assert c.get(f"/api/app/browse?dir={tmp_path / 'nope'}")[0] == 400


def test_recent_is_remembered_and_drops_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "c"))
    a, b = tmp_path / "a.toml", tmp_path / "b"
    a.write_text("")
    b.mkdir()
    recent.remember(a, "robot")
    time.sleep(0.01)
    recent.remember(b, "session")
    recent.remember(a, "robot")
    assert [(r["path"], r["kind"]) for r in recent.recent()] == [(str(a), "robot"), (str(b), "session")]
    assert (tmp_path / "c" / "recent.json").is_file()
    a.unlink()
    assert [r["path"] for r in recent.recent()] == [str(b)]
    for i in range(20):
        (tmp_path / f"f{i}.toml").write_text("")
        recent.remember(tmp_path / f"f{i}.toml", "robot")
    got = recent.recent()
    assert len(got) == 12 and got[0]["path"] == str(tmp_path / "f19.toml")
    monkeypatch.delenv("SCRIMMAGE_CACHE")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    recent.remember(b, "session")
    assert (tmp_path / "home" / ".cache" / "scrimmage" / "recent.json").is_file()
