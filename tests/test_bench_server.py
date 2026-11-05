"""The Bench tab's server side: an idea session (a folder of bench files) behind /api/bench/*."""

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from scrimmage.assembler.server import App
from scrimmage.bench import session
from test_assembler_server import Client


def _summary_data(out, heads, energy_failed=False):
    """summary.json as bench/report.py's write_results writes it beside summary.txt (no words: how many headline
    lines summary.txt has, and the flags), for the Bench tab."""
    (out / "summary.json").write_text(json.dumps({"headlines": len(heads), "uncalibrated": True,
                                                  "energy_failed": energy_failed}))

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "examples" / "bench"


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRIMMAGE_RECENT", raising=False)
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))


@pytest.fixture
def bench(tmp_path, monkeypatch):
    """A served session folder holding copies of bounce_bar.toml and pinch_car.toml."""
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "kickoff"
    folder.mkdir()
    for n in ("bounce_bar.toml", "pinch_car.toml"):
        shutil.copy(BENCH / n, folder / n)
    started = []

    def make(path=folder):
        app = App(path, open_browser=False)
        started.append(app)
        return Client(app, app.start()), folder
    yield make
    for a in started:
        a.stop()
    assert not [t for t in threading.enumerate() if t.name.startswith("bench-")], "a bench thread outlived stop()"


def _state(c, since=None):
    s, b = c.get("/api/bench/state" + (f"?since={since}" if since is not None else ""))
    assert s == 200, b
    return b


def _idea(st, file):
    return next(i for i in st["ideas"] if i["file"] == file)


def _edit(c, body):
    st = _state(c)
    return c.call("POST", "/api/bench/edit", {"version": st["version"], **body})


def _wait_for(c, pred, timeout=3.0):
    end = time.monotonic() + timeout
    st = _state(c)
    while not pred(st):
        assert time.monotonic() < end, st
        st = _state(c, st["version"])
    return st


def test_state_lists_ideas_with_problems_and_focus(bench):
    c, folder = bench()
    st = _state(c)
    assert st["folder"] == str(folder) and st["display_folder"] == "kickoff" and st["lock"] is None
    i = _idea(st, "bounce_bar.toml")
    assert i["name"] == "bounce bar" and i["author"] == "Priya" and i["read_only_reason"] is None
    assert i["focus"] == [] and i["latest"] is None and not i["can_undo"]
    assert all(p["level"] == "warning" for p in i["problems"])
    s, f = c.get("/api/bench/fields")
    assert s == 200 and "wheel.diameter" in f["fields"]
    s, v = c.get("/api/bench/view?file=bounce_bar.toml")
    assert s == 200 and v["geoms"] and v["boxes"]
    s, d = c.get("/api/bench/idea?file=bounce_bar.toml")
    assert s == 200 and d["own"]["bar"][0]["spring"] == 3000.0 and d["included"] == {}
    assert d["merged"]["bench"]["name"] == "bounce bar"
    assert c.get("/api/bench/idea?file=nope.toml")[0] == 404


def test_new_session_and_new_idea(bench, tmp_path):
    c, _ = bench()
    s, body = c.call("POST", "/api/app/new", {"kind": "session", "path": "fresh"})
    assert s == 200 and body["ok"]
    st = _state(c)
    assert st["ideas"] == [] and st["stage"] is None
    s, body = c.call("POST", "/api/bench/new_idea", {"name": "Ava's flip", "author": "Ava",
                                                       "idea": "flip it over the bar"})
    assert s == 200 and body["ok"], body
    f = body["file"]
    assert (tmp_path / "fresh" / f).is_file() and f.endswith(".toml")
    st = _state(c)
    assert st["stage"] == f and _idea(st, f)["author"] == "Ava" and _idea(st, f)["idea"] == "flip it over the bar"
    assert [p for p in _idea(st, f)["problems"] if p["level"] == "error"] == []
    s, body2 = c.call("POST", "/api/bench/new_idea", {"name": "Ava's flip", "author": "Ava", "idea": "again",
                                                        "example": "bounce_bar"})
    assert s == 200 and body2["file"] != f
    st = _state(c)
    assert st["stage"] == body2["file"] and _idea(st, body2["file"])["idea"] == "again"
    assert "kicker" in (tmp_path / "fresh" / body2["file"]).read_text()
    s, body3 = c.call("POST", "/api/bench/new_idea", {"name": "x", "author": "", "idea": "", "example": "nope"})
    assert s == 200 and not body3["ok"]


def test_edit_set_writes_file_and_bumps_focus(bench):
    c, folder = bench()
    v0 = _state(c)["version"]
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "key": "bar.kicker.spring", "value": 4000})
    assert s == 200 and body["ok"], body
    text = (folder / "bounce_bar.toml").read_text()
    assert "spring = 4000" in text and "# N/m, up and down" in text
    st = _state(c)
    i = _idea(st, "bounce_bar.toml")
    assert i["focus"][0] == "bar.kicker.spring" and st["version"] == body["version"] > v0
    assert st["stage"] == "bounce_bar.toml" and i["can_undo"]
    s, body = c.call("POST", "/api/bench/undo", {"file": "bounce_bar.toml", "version": st["version"]})
    assert s == 200 and body["ok"]
    assert "spring = 3000.0" in (folder / "bounce_bar.toml").read_text()
    s, body = c.call("POST", "/api/bench/redo", {"file": "bounce_bar.toml", "version": body["version"]})
    assert s == 200 and body["ok"] and "spring = 4000" in (folder / "bounce_bar.toml").read_text()


def test_edit_ops_and_refusals(bench):
    c, folder = bench()
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "key": "bar.kicker.name", "value": "k2"})
    assert s == 200 and not body["ok"] and "rename" in body["errors"][0]["message"]
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "key": "wheel.top.diameter", "value": -1})
    assert s == 200 and not body["ok"] and body["errors"][0]["field"].startswith("wheel.top")
    assert "diameter = 0.1016" in (folder / "bounce_bar.toml").read_text()
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "rename", "section": "bar", "name": "kicker",
                        "new_name": "flipper"})
    assert s == 200 and body["ok"], body
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "add", "section": "plate", "name": "shelf",
                        "fields": {"center": [0.5, 0.0, -0.3]}})
    assert s == 200 and body["ok"], body
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "remove", "section": "plate", "name": "shelf"})
    assert s == 200 and body["ok"], body
    text = (folder / "bounce_bar.toml").read_text()
    assert 'name = "flipper"' in text and "shelf" not in text
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "explode"})
    assert s == 400
    s, body = c.call("POST", "/api/bench/edit", {"file": "bounce_bar.toml", "op": "set", "key": "run.duration",
                                                 "value": 2})
    assert s == 400   # no version


def test_include_shows_sources_and_override(bench, tmp_path):
    c, folder = bench()
    text = (folder / "bounce_bar.toml").read_text()
    head, tail = text.split("# 4 in.")
    (folder / "shafts.toml").write_text("[piece]" + head.split("[piece]")[1])   # the piece and the shafts
    own = 'include = ["shafts.toml"]\n\n[bench]\nname = "split"\nauthor = "Kai"\nidea = "shared shafts"\n\n# 4 in.'
    (folder / "split.toml").write_text(own + tail)
    st = _wait_for(c, lambda st: any(i["file"] == "split.toml" for i in st["ideas"]))
    assert "shafts.toml" not in [i["file"] for i in st["ideas"]]
    s, d = c.get("/api/bench/idea?file=split.toml")
    assert d["included"]["shaft.top"] == "shafts.toml" and d["included"]["piece"] == "shafts.toml"
    assert "shaft" not in d["own"] and d["merged"]["shaft"]["top"]["target_rps"] == 30
    s, body = _edit(c, {"file": "split.toml", "op": "set", "key": "shaft.top.target_rps", "value": 50})
    assert s == 200 and not body["ok"] and "override" in body["errors"][0]["message"]
    s, body = _edit(c, {"file": "split.toml", "op": "override", "section": "shaft", "name": "top"})
    assert s == 200 and body["ok"], body
    s, body = _edit(c, {"file": "split.toml", "op": "set", "key": "shaft.top.target_rps", "value": 50})
    assert s == 200 and body["ok"], body
    assert "target_rps = 30" in (folder / "shafts.toml").read_text()


def test_outside_edit_bumps_focus_and_stage(bench):
    c, folder = bench(None)   # start mode, then open a file of the folder
    assert c.call("POST", "/api/app/open", {"path": str(folder / "pinch_car.toml")})[0] == 200
    st = _state(c)
    assert st["stage"] == "pinch_car.toml"
    p = folder / "bounce_bar.toml"
    t0 = time.monotonic()
    p.write_text(p.read_text().replace("target_rps = 30", "target_rps = 35", 1))
    st = _state(c, st["version"])
    assert time.monotonic() - t0 < 1.0
    st = _wait_for(c, lambda st: _idea(st, "bounce_bar.toml")["focus"])
    assert _idea(st, "bounce_bar.toml")["focus"][0] == "shaft.bottom.target_rps"
    assert st["stage"] == "bounce_bar.toml" and st["ideas"][0]["file"] == "bounce_bar.toml"
    assert session.focus(folder, p)[0] == "shaft.bottom.target_rps"   # shared with the agent and the CLI


def test_outside_edit_during_page_edit_is_refused_and_reported(bench):
    c, folder = bench()
    st = _state(c)
    p = folder / "bounce_bar.toml"
    outside = p.read_text().replace("travel = 0.02", "travel = 0.03")
    p.write_text(outside)   # the agent writes while the page still shows the old version
    s, body = c.call("POST", "/api/bench/edit", {"file": "bounce_bar.toml", "version": st["version"], "op": "set",
                                                 "key": "bar.kicker.spring", "value": 4000})
    assert s == 200 and body["ok"] is False
    assert body["errors"][0]["field"] == "status"
    assert "the agent changed this idea" in body["errors"][0]["message"]
    assert "bar.kicker.spring" in body["errors"][0]["message"]
    assert p.read_text() == outside
    st2 = _state(c)
    assert st2["version"] > st["version"] and _idea(st2, "bounce_bar.toml")["focus"][0] == "bar.kicker.travel"
    # an edit to another idea with the old version is fine: only this idea changed
    s, body = c.call("POST", "/api/bench/edit", {"file": "pinch_car.toml", "version": st["version"], "op": "set",
                                                 "key": "run.duration", "value": 2.0})
    assert s == 200 and body["ok"], body


def test_broken_toml_idea_is_read_only_and_recovers(bench):
    c, folder = bench()
    p = folder / "bounce_bar.toml"
    good = p.read_text()
    p.write_text(good[: len(good) // 2] + '\nname = "half')   # half-written by the agent
    st = _wait_for(c, lambda st: _idea(st, "bounce_bar.toml")["read_only_reason"])
    i = _idea(st, "bounce_bar.toml")
    assert "not valid TOML" in i["read_only_reason"] and i["problems"][0]["level"] == "error"
    assert i["focus"] == []   # a half-written file doesn't flood the talked-about list
    assert _idea(st, "pinch_car.toml")["read_only_reason"] is None
    assert c.get("/api/bench/view?file=bounce_bar.toml") == (200, {})
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "key": "run.duration", "value": 2})
    assert s == 409
    s, body = _edit(c, {"file": "pinch_car.toml", "op": "set", "key": "run.duration", "value": 2.0})
    assert s == 200 and body["ok"]
    p.write_text(good.replace("travel = 0.02", "travel = 0.025"))
    st = _wait_for(c, lambda st: _idea(st, "bounce_bar.toml")["read_only_reason"] is None)
    assert _idea(st, "bounce_bar.toml")["focus"] == ["bar.kicker.travel"]
    assert c.get("/api/bench/view?file=bounce_bar.toml")[1]["geoms"]


def test_idea_deleted_outside_leaves_stage(bench):
    c, folder = bench()
    _edit(c, {"file": "pinch_car.toml", "op": "set", "key": "run.duration", "value": 2.0})
    _edit(c, {"file": "bounce_bar.toml", "op": "set", "key": "run.duration", "value": 2.0})
    assert _state(c)["stage"] == "bounce_bar.toml"
    (folder / "bounce_bar.toml").unlink()
    st = _wait_for(c, lambda st: all(i["file"] != "bounce_bar.toml" for i in st["ideas"]))
    assert st["stage"] == "pinch_car.toml"
    (folder / "pinch_car.toml").rename(folder / "pinch_car2.toml")   # renamed outside
    st = _wait_for(c, lambda st: [i["file"] for i in st["ideas"]] == ["pinch_car2.toml"])
    assert st["stage"] == "pinch_car2.toml"
    (folder / "pinch_car2.toml").unlink()
    st = _wait_for(c, lambda st: st["ideas"] == [])
    assert st["stage"] is None
    assert c.call("POST", "/api/bench/edit", {"file": "pinch_car2.toml", "version": st["version"], "op": "set",
                                              "key": "run.duration", "value": 1})[0] == 404


def test_latest_from_log_and_file_route(bench, tmp_path):
    c, folder = bench()
    out = tmp_path / "elsewhere" / "pinch_car-results"
    out.mkdir(parents=True)
    (out / "summary.txt").write_text('pinch car (Sam)\nidea: "x"\n\n3 of 3 got out through `goal`\n'
                                     "UNCALIBRATED: estimates from preset numbers, not measurements; measure.\n"
                                     "warning: w\n\nPieces\n")
    _summary_data(out, ["3 of 3 got out through `goal`",
                        "UNCALIBRATED: estimates from preset numbers, not measurements; measure."])
    (out / "path_side.png").write_bytes(b"\x89PNG fake")
    session.log_event(folder, "run", [folder / "pinch_car.toml"], out, "agent", True)
    st = _wait_for(c, lambda st: _idea(st, "pinch_car.toml")["latest"])
    lt = _idea(st, "pinch_car.toml")["latest"]
    assert st["stage"] == "pinch_car.toml"
    assert lt["ok"] and lt["source"] == "agent" and lt["kind"] == "run" and lt["out"] == str(out)
    assert lt["headlines"][0] == "3 of 3 got out through `goal`"
    assert lt["uncalibrated"].startswith("UNCALIBRATED")
    assert lt["pictures"] == {"path_side": str(out / "path_side.png"), "pass_gif": None}
    s, raw = c.get(f"/api/bench/file?path={out / 'path_side.png'}")
    assert s == 200 and raw == b"\x89PNG fake"
    session.log_event(folder, "run", [folder / "bounce_bar.toml"], tmp_path / "gone", "cli", False, "boom")
    st = _wait_for(c, lambda st: _idea(st, "bounce_bar.toml")["latest"])
    lt = _idea(st, "bounce_bar.toml")["latest"]
    assert not lt["ok"] and lt["error"] == "boom" and lt["uncalibrated"].startswith("UNCALIBRATED")
    assert lt["headlines"] == []


def test_file_route_refuses_outside_paths(bench, tmp_path):
    c, folder = bench()
    (tmp_path / "secret.txt").write_text("no")
    s, raw = c.get("/api/bench/file?path=bounce_bar.toml")
    assert s == 200 and b"bounce bar" in raw
    for bad in (str(tmp_path / "secret.txt"), "../secret.txt", str(folder / "missing.png"), "/etc/passwd"):
        s, body = c.get(f"/api/bench/file?path={bad}")
        assert s == 404, bad


def test_lock_holder_shows_in_state(bench):
    c, folder = bench()
    st = _state(c)
    with session.run_lock(folder, {"source": "agent", "file": "pinch_car.toml"}):
        st = _wait_for(c, lambda st: st["lock"])
        assert st["lock"]["source"] == "agent" and st["lock"]["file"] == "pinch_car.toml"
    st = _wait_for(c, lambda st: st["lock"] is None)


# ----------------------------------------------------------------------------------------------------- jobs (Task 7)

def _wait_job(c, job_id, timeout=60.0, states=("done", "failed")):
    end = time.monotonic() + timeout
    since = None
    while True:
        s, body = c.get("/api/bench/jobs" + (f"?since={since}" if since is not None else ""))
        assert s == 200, body
        job = next((j for j in body["jobs"] if j["id"] == job_id), None)
        if job is not None and job["state"] in states:
            return job
        assert time.monotonic() < end, body
        since = body["version"]


def test_run_from_page_updates_latest(bench):
    c, folder = bench()
    s, body = c.call("POST", "/api/bench/run", {"file": "pinch_car.toml"})
    assert s == 200 and body["ok"], body
    job = _wait_job(c, body["id"])
    assert job["state"] == "done" and job["error"] is None, job
    assert job["kind"] == "run" and job["files"] == ["pinch_car.toml"]
    assert job["out"] == str(folder / "pinch_car-results")
    st = _state(c)
    lt = _idea(st, "pinch_car.toml")["latest"]
    assert lt["ok"] and lt["source"] == "page" and lt["kind"] == "run" and lt["headlines"]
    assert lt["uncalibrated"].startswith("UNCALIBRATED")
    assert lt["pictures"]["path_side"] and Path(lt["pictures"]["path_side"]).is_file()
    assert st["stage"] == "pinch_car.toml"
    s, raw = c.get(f"/api/bench/file?path={lt['pictures']['path_side']}")
    assert s == 200 and raw[:4] == b"\x89PNG"
    events = session.read_events(folder)
    assert events[-1]["source"] == "page" and events[-1]["ok"]


def test_failed_run_shows_on_stage(bench, monkeypatch):
    from scrimmage.bench import run as bench_run

    def boom(*a, **k):
        raise bench_run.BenchRunError("the run blew up at t = 0.1000 s: test")
    monkeypatch.setattr(bench_run, "run_bench", boom)
    c, folder = bench()
    s, body = c.call("POST", "/api/bench/run", {"file": "pinch_car.toml"})
    assert s == 200 and body["ok"], body
    job = _wait_job(c, body["id"])
    assert job["state"] == "failed" and "blew up" in job["error"]
    lt = _idea(_state(c), "pinch_car.toml")["latest"]
    assert lt["ok"] is False and "blew up" in lt["error"]
    # the worker carries on with the next job
    monkeypatch.setattr(bench_run, "run_bench", lambda *a, **k: (_ for _ in ()).throw(ValueError("odd")))
    s, body = c.call("POST", "/api/bench/run", {"file": "bounce_bar.toml"})
    job = _wait_job(c, body["id"])
    assert job["state"] == "failed" and "odd" in job["error"]


def test_run_refused_for_bad_requests(bench):
    c, folder = bench()
    p = folder / "pinch_car.toml"
    p.write_text(p.read_text().replace("diameter = 0.06", "diameter = -1", 1))
    _wait_for(c, lambda st: any(q["level"] == "error" for q in _idea(st, "pinch_car.toml")["problems"]))
    s, body = c.call("POST", "/api/bench/run", {"file": "pinch_car.toml"})
    assert s == 200 and body["ok"] is False and body["errors"][0]["field"].startswith("wheel.")
    assert c.call("POST", "/api/bench/run", {"file": "nope.toml"})[0] == 404
    s, body = c.call("POST", "/api/bench/sweep", {"file": "bounce_bar.toml", "key": "run.duration", "values": []})
    assert s == 200 and body["ok"] is False and body["errors"][0]["field"] == "values"
    s, body = c.call("POST", "/api/bench/sweep", {"file": "bounce_bar.toml", "key": "wheel.nope.diameter",
                                                  "values": [0.1]})
    assert s == 200 and body["ok"] is False and body["errors"][0]["field"] == "key"
    s, body = c.call("POST", "/api/bench/compare", {"files": ["bounce_bar.toml"]})
    assert s == 200 and body["ok"] is False
    assert c.get("/api/bench/jobs")[1]["jobs"] == []


def test_page_run_waits_for_agent_lock(bench, tmp_path):
    import subprocess
    import sys
    c, folder = bench()
    code = ("import sys; from pathlib import Path; from scrimmage.bench import session\n"
            "with session.run_lock(Path(sys.argv[1]), {'source': 'agent', 'file': 'bounce_bar.toml'}):\n"
            "    print('held', flush=True); sys.stdin.read()\n")
    p = subprocess.Popen([sys.executable, "-c", code, str(folder)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, cwd=ROOT)
    try:
        assert p.stdout.readline().strip() == "held"
        st = _wait_for(c, lambda st: st["lock"])
        assert st["lock"]["source"] == "agent"
        s, body = c.call("POST", "/api/bench/run", {"file": "pinch_car.toml"})
        assert s == 200 and body["ok"]
        time.sleep(0.8)
        jobs = c.get("/api/bench/jobs")[1]["jobs"]
        assert jobs[0]["id"] == body["id"] and jobs[0]["state"] == "queued"
        assert c.call("POST", "/api/app/open", {"path": str(folder)})[0] == 409   # this App's own job is queued
    finally:
        p.stdin.close()
        p.wait(10)
    job = _wait_job(c, body["id"])
    assert job["state"] == "done", job


def test_open_refused_while_bench_run(bench, monkeypatch, tmp_path):
    from scrimmage.bench import run as bench_run
    real, go = bench_run.run_bench, threading.Event()

    def slow(*a, **k):
        assert go.wait(30)
        return real(*a, **k)
    monkeypatch.setattr(bench_run, "run_bench", slow)
    c, folder = bench()
    s, body = c.call("POST", "/api/bench/run", {"file": "pinch_car.toml"})
    _wait_job(c, body["id"], states=("running",))
    s, err = c.call("POST", "/api/app/open", {"path": str(folder)})
    assert s == 409 and "bench run" in err["error"]
    s, err = c.call("POST", "/api/app/new", {"kind": "session", "path": "other"})
    assert s == 409
    go.set()
    assert _wait_job(c, body["id"])["state"] == "done"
    # the agent (another holder of the session lock) never blocks opening
    with session.run_lock(folder, {"source": "agent", "file": "x.toml"}):
        assert c.call("POST", "/api/app/open", {"path": str(folder)})[0] == 200
    assert not [t for t in threading.enumerate() if t.name == "bench-jobs" and t.is_alive()]


def test_sweep_and_compare_and_gallery(bench):
    c, folder = bench()
    two = folder / "pinch_car2.toml"
    two.write_text((folder / "pinch_car.toml").read_text().replace('name = "pinch car"', 'name = "pinch two"')
                   .replace("target_rps = 40", "target_rps = 30"))
    _wait_for(c, lambda st: any(i["file"] == "pinch_car2.toml" for i in st["ideas"]))
    s, sw = c.call("POST", "/api/bench/sweep", {"file": "pinch_car.toml", "key": "shaft.top.target_rps",
                                                "values": [30, 40]})
    assert s == 200 and sw["ok"], sw
    s, cm = c.call("POST", "/api/bench/compare", {"files": ["pinch_car.toml", "pinch_car2.toml"]})
    assert s == 200 and cm["ok"], cm
    s, ga = c.call("POST", "/api/bench/gallery", {})
    assert s == 200 and ga["ok"], ga
    job = _wait_job(c, sw["id"])
    assert job["state"] == "done", job
    out = folder / "pinch_car-sweep"
    assert job["out"] == str(out) and (out / "results.csv").is_file()
    assert len((out / "results.csv").read_text().splitlines()) == 3
    job = _wait_job(c, cm["id"])
    assert job["state"] == "done" and job["files"] == ["pinch_car.toml", "pinch_car2.toml"], job
    assert (folder / "compare-results" / "compare.csv").is_file()
    assert (folder / "compare-results" / "compare.png").is_file()
    job = _wait_job(c, ga["id"])
    assert job["state"] == "done" and job["out"] == str(folder / "gallery.html"), job
    assert (folder / "gallery.html").is_file()
    s, raw = c.get(f"/api/bench/file?path={folder / 'compare-results' / 'compare.png'}")
    assert s == 200
    jobs = c.get("/api/bench/jobs")[1]["jobs"]
    assert [j["kind"] for j in jobs] == ["gallery", "compare", "sweep"]   # newest first
    st = _state(c)
    assert _idea(st, "pinch_car2.toml")["latest"] is None   # a compare is the session's result, not an idea's run
    r = st["result"]
    assert r["kind"] == "compare" and r["files"] == ["pinch_car.toml", "pinch_car2.toml"] and r["on_stage"]
    assert r["pictures"] == [str(folder / "compare-results" / "compare.png")]
    assert [row[0] for row in r["table"]["rows"]] == ["pinch car", "pinch two"]
    kinds = [e["kind"] for e in session.read_events(folder)]
    assert kinds == ["sweep", "compare", "gallery"]
    assert (folder / "pinch_car.toml").read_text().count("[sweep]") == 0   # the file is never touched


# ----------------------------------------------------------------------------------------------------- hardening

def test_file_route_serves_only_bench_outputs(bench, tmp_path):
    c, folder = bench()
    (tmp_path / "secret.txt").write_text("no")
    out = tmp_path / "res"
    (out / "deep").mkdir(parents=True)
    (out / "summary.txt").write_text("ok")
    (out / "notes.toml").write_text("x = 1")
    (out / "deep" / "pieces.csv").write_text("a")
    session.log_event(folder, "run", [folder / "pinch_car.toml"], out, "agent", True)
    for broad in (tmp_path, Path.home(), Path("/")):   # a broad out never opens a tree
        session.log_event(folder, "run", [folder / "pinch_car.toml"], broad, "agent", True)
    _wait_for(c, lambda st: _idea(st, "pinch_car.toml")["latest"])
    time.sleep(0.4)
    assert c.get(f"/api/bench/file?path={out / 'summary.txt'}")[0] == 200
    for bad in (out / "notes.toml", out / "deep" / "pieces.csv", tmp_path / "secret.txt"):
        assert c.get(f"/api/bench/file?path={bad}")[0] == 404, bad
    (folder / "link.txt").symlink_to(tmp_path / "secret.txt")
    assert c.get("/api/bench/file?path=link.txt")[0] == 404
    # a new log (deleted, started over) forgets the old results folders
    (folder / session.STATE_DIR / "log.jsonl").unlink()
    time.sleep(0.6)
    session.log_event(folder, "run", [folder / "bounce_bar.toml"], tmp_path / "other", "agent", True)
    _wait_for(c, lambda st: _idea(st, "bounce_bar.toml")["latest"])
    assert c.get(f"/api/bench/file?path={out / 'summary.txt'}")[0] == 404


def test_request_after_close_is_refused(bench, tmp_path):
    from scrimmage.assembler.bench_session import SessionError
    c, folder = bench()
    old = c.app.bench
    other = tmp_path / "other"
    other.mkdir()
    assert c.call("POST", "/api/app/open", {"path": str(other)})[0] == 200
    text = (folder / "bounce_bar.toml").read_text()
    for call in (lambda: old.edit({"file": "bounce_bar.toml", "version": old.version, "op": "set",
                                   "key": "run.duration", "value": 2.0}),
                 lambda: old.history("undo", {"file": "bounce_bar.toml", "version": old.version}),
                 lambda: old.new_idea({"name": "late"}),
                 lambda: old.view("bounce_bar.toml"),
                 lambda: old.file("bounce_bar.toml")):
        with pytest.raises(SessionError) as e:
            call()
        assert e.value.status == 409 and e.value.message == "the idea session was closed"
    assert (folder / "bounce_bar.toml").read_text() == text and not (folder / "late.toml").exists()


def test_meters_strips_only_length_fields():
    from scrimmage.assembler.bench_session import _meters
    assert _meters("wheel.top.diameter_in") == "wheel.top.diameter"
    assert _meters("inlet.feed_in") == "inlet.feed_in"
    assert _meters("inlet.feed_in.center_in") == "inlet.feed_in.center"
    assert _meters("shaft.spin_in") == "shaft.spin_in"
    assert _meters("run.jam_distance_in") == "run.jam_distance"
    assert _meters("run.duration_in") == "run.duration_in"
    assert _meters("sweep.wheel.top.diameter_in") == "sweep.wheel.top.diameter"
    assert _meters(None) is None


def test_set_bench_name_is_allowed(bench):
    c, folder = bench()
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "key": "bench.name", "value": "bouncy"})
    assert s == 200 and body["ok"], body
    assert _idea(_state(c), "bounce_bar.toml")["name"] == "bouncy"


def test_unreadable_idea_is_read_only(bench, caplog):
    import logging
    import os
    c, folder = bench()
    p = folder / "bounce_bar.toml"
    p.write_text(p.read_text().replace("travel = 0.02", "travel = 0.021"))
    os.chmod(p, 0)
    try:
        if os.access(p, os.R_OK):
            pytest.skip("running as a user that reads anything")
        with caplog.at_level(logging.ERROR, logger="scrimmage.assembler"):
            st = _wait_for(c, lambda st: _idea(st, "bounce_bar.toml")["read_only_reason"])
            assert "can't read" in _idea(st, "bounce_bar.toml")["read_only_reason"]
            s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "key": "run.duration", "value": 2.0})
            assert s == 409
            time.sleep(0.6)
        assert not [r for r in caplog.records if "bench session poll" in r.getMessage()]
    finally:
        os.chmod(p, 0o644)
    st = _wait_for(c, lambda st: _idea(st, "bounce_bar.toml")["read_only_reason"] is None)
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "key": "run.duration", "value": 2.0})
    assert s == 200 and body["ok"], body
    assert "travel = 0.021" in p.read_text()


def test_energy_failure_shows_in_latest_for_every_source(bench, monkeypatch, tmp_path):
    import dataclasses

    from scrimmage.bench import run as bench_run
    real = bench_run.run_bench

    def bad_energy(*a, **k):
        r = real(*a, **k)
        return dataclasses.replace(r, energy=[dataclasses.replace(e, ok=False) for e in r.energy])
    monkeypatch.setattr(bench_run, "run_bench", bad_energy)
    c, folder = bench()
    s, body = c.call("POST", "/api/bench/run", {"file": "pinch_car.toml"})
    job = _wait_job(c, body["id"])
    assert job["state"] == "done" and job["error"] is None, job   # it ran: failed is only for a run that raised
    lt = _idea(_state(c), "pinch_car.toml")["latest"]
    assert lt["ok"] is True and lt["energy_failed"] is True
    assert lt["headlines"][0].startswith("the energy check failed")
    # an agent's run reads the same way, from its results
    out = tmp_path / "agent-results"
    out.mkdir()
    (out / "summary.txt").write_text("b (A)\n\n3 of 3 out\nUNCALIBRATED: x\n\nEnergy check: pieces 0 gained energy "
                                     "from nowhere (a failure means the simulation is wrong, not the design)\n")
    _summary_data(out, ["3 of 3 out", "UNCALIBRATED: x"], energy_failed=True)
    session.log_event(folder, "run", [folder / "bounce_bar.toml"], out, "agent", True)
    st = _wait_for(c, lambda st: _idea(st, "bounce_bar.toml")["latest"])
    assert _idea(st, "bounce_bar.toml")["latest"]["energy_failed"] is True
    (out / "summary.txt").write_text("b (A)\n\n3 of 3 out\nUNCALIBRATED: x\n\nEnergy check: ok (a failure ...)\n")
    _summary_data(out, ["3 of 3 out", "UNCALIBRATED: x"])
    assert _idea(_state(c), "bounce_bar.toml")["latest"]["energy_failed"] is False


def test_stop_that_times_out_then_submit_works(bench, monkeypatch):
    from scrimmage.bench import run as bench_run
    real, go = bench_run.run_bench, threading.Event()

    def slow(*a, **k):
        assert go.wait(30)
        return real(*a, **k)
    monkeypatch.setattr(bench_run, "run_bench", slow)
    c, folder = bench()
    s, body = c.call("POST", "/api/bench/run", {"file": "pinch_car.toml"})
    _wait_job(c, body["id"], states=("running",))
    c.app.bench_jobs.stop(timeout=0.2)   # the job outlasts the stop
    assert c.app.bench_jobs.active
    go.set()
    assert _wait_job(c, body["id"])["state"] == "done"
    end = time.monotonic() + 5
    while [t for t in threading.enumerate() if t.name == "bench-jobs"]:
        assert time.monotonic() < end
        time.sleep(0.05)
    assert not c.app.bench_jobs.active
    s, body = c.call("POST", "/api/bench/run", {"file": "bounce_bar.toml"})
    assert s == 200 and body["ok"], body
    assert _wait_job(c, body["id"])["state"] == "done"


def test_finished_job_state_and_latest_agree(bench):
    """When the page sees a job finish, the idea's latest already shows it and open is no longer refused."""
    c, folder = bench()
    for _ in range(3):
        s, body = c.call("POST", "/api/bench/run", {"file": "pinch_car.toml"})
        job = _wait_job(c, body["id"])
        lt = _idea(c.app.bench.state(), "pinch_car.toml")["latest"]   # no poll in between
        assert job["state"] == "done" and lt["source"] == "page" and lt["t"] >= 0
        assert not c.app.bench_jobs.active
    assert c.call("POST", "/api/app/open", {"path": str(folder)})[0] == 200


def test_set_several_keys_is_one_edit(bench):
    """`set` with `values` writes every key in one edit (one write, one undo step); `field` names the row the page
    showed them under, in a refusal."""
    c, folder = bench()
    f = folder / "bounce_bar.toml"
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "field": "bar.kicker.center",
                        "values": {"bar.kicker.a": [0.25, -0.15, -0.048], "bar.kicker.b": [0.25, 0.15, -0.048]}})
    assert s == 200 and body["ok"], body
    text = f.read_text()
    assert "a = [0.25, -0.15, -0.048]" in text and "b = [0.25, 0.15, -0.048]" in text
    assert "# N/m, up and down" in text
    s, body = c.call("POST", "/api/bench/undo", {"file": "bounce_bar.toml", "version": _state(c)["version"]})
    assert s == 200 and body["ok"], body
    text = f.read_text()
    assert "a = [0.25, -0.15, -0.058]" in text and "b = [0.25, 0.15, -0.058]" in text   # both ends back in one undo
    # a refusal (here a bad key) is named by `field`, and nothing is written
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "field": "bar.kicker.center",
                        "values": {"bar.kicker.a": [0.25, -0.15, 0.0], "bar.kicker.bogus": 1}})
    assert s == 200 and not body["ok"] and body["errors"][0]["field"] == "bar.kicker.center", body
    assert f.read_text() == text
    # a name is still changed only by rename, bench.name excepted
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "values": {"bar.kicker.name": "x"}})
    assert not body["ok"] and "rename" in body["errors"][0]["message"]
    s, body = _edit(c, {"file": "bounce_bar.toml", "op": "set", "values": {"bench.name": "bouncy", "run.duration": 3}})
    assert body["ok"], body
    assert 'name = "bouncy"' in f.read_text() and "duration = 3.0" in f.read_text()
    # a stale group edit says which row wasn't saved
    v = _state(c)["version"]
    f.write_text(f.read_text().replace("spring = 3000.0", "spring = 5000.0"))
    _wait_for(c, lambda st: st["version"] > v)
    s, body = c.call("POST", "/api/bench/edit", {"file": "bounce_bar.toml", "version": v, "op": "set", "field": "bar.kicker.center",
                                                 "values": {"bar.kicker.a": [0, 0, 0], "bar.kicker.b": [0, 0.1, 0]}})
    assert not body["ok"] and "bar.kicker.center wasn't saved" in body["errors"][0]["message"]


def _fake_run(out, headline):
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.txt").write_text(f"pinch car (Sam)\n\n{headline}\nUNCALIBRATED: estimates; measure.\n\nPieces\n")
    _summary_data(out, [headline, "UNCALIBRATED: estimates; measure."])
    (out / "path_side.png").write_bytes(b"\x89PNG run")


def test_cli_sweep_and_compare_are_the_session_result(bench, tmp_path):
    """A sweep or compare logged by the CLI or the agent (even into the run's own results folder) never borrows the
    run's headlines: it is the session's `result`, with its chart and table, and each idea keeps its own run."""
    import os
    c, folder = bench()
    out = folder / "pinch_car-results"
    _fake_run(out, "3 of 3 got out through `goal`")
    session.log_event(folder, "run", [folder / "pinch_car.toml"], out, "cli", True)
    st = _wait_for(c, lambda st: _idea(st, "pinch_car.toml")["latest"])
    assert st["result"] is None
    # a CLI sweep into the same folder: an old sweep's chart is left there too
    stale = out / "line-exit_speed-vs-wheel.top.diameter.png"
    stale.write_bytes(b"\x89PNG old")
    os.utime(stale, (time.time() - 100, time.time() - 100))
    (out / "results.csv").write_text("shaft.top.target_rps,out,out_other,jammed,exit_speed,peak_load_n\n"
                                     "30,3,0,0,7.5,40\n40,3,0,0,9.25,52\n")
    chart = out / "line-exit_speed-vs-shaft.top.target_rps.png"
    chart.write_bytes(b"\x89PNG chart")
    session.log_event(folder, "sweep", [folder / "pinch_car.toml"], out, "cli", True)
    st = _wait_for(c, lambda st: st["result"])
    r = st["result"]
    assert r["kind"] == "sweep" and r["files"] == ["pinch_car.toml"] and r["source"] == "cli" and r["ok"]
    assert r["pictures"] == [str(chart)] and r["on_stage"] and st["stage"] == "pinch_car.toml"
    assert r["uncalibrated"].startswith("UNCALIBRATED")
    assert r["table"] == {"columns": ["shaft.top.target_rps", "out", "jammed", "exit_speed", "peak_load_n"],
                          "rows": [["30", "3", "0", "7.5", "40"], ["40", "3", "0", "9.25", "52"]]}
    lt = _idea(st, "pinch_car.toml")["latest"]   # the idea's own run, untouched
    assert lt["kind"] == "run" and lt["headlines"][0] == "3 of 3 got out through `goal`"
    assert c.get(f"/api/bench/file?path={chart}")[0] == 200
    # an agent compare: the result moves to it; both ideas keep their own runs
    cmp = folder / "compare-results"
    cmp.mkdir()
    (cmp / "compare.csv").write_text("idea,out,jammed,exit_speed,escaped\npinch car,3,0,9,0\nbounce bar,2,1,6,0\n")
    (cmp / "compare.png").write_bytes(b"\x89PNG cmp")
    session.log_event(folder, "compare", [folder / "pinch_car.toml", folder / "bounce_bar.toml"], cmp, "agent", True)
    st = _wait_for(c, lambda st: st["result"]["kind"] == "compare")
    r = st["result"]
    assert r["files"] == ["pinch_car.toml", "bounce_bar.toml"] and r["on_stage"] and r["source"] == "agent"
    assert r["pictures"] == [str(cmp / "compare.png")]
    assert r["table"]["columns"] == ["idea", "out", "jammed", "exit_speed"]
    assert r["table"]["rows"][1] == ["bounce bar", "2", "1", "6"]
    assert _idea(st, "pinch_car.toml")["latest"]["headlines"][0] == "3 of 3 got out through `goal`"
    assert _idea(st, "bounce_bar.toml")["latest"] is None
    # a restart keeps all of it, the compare still on stage
    c.app.stop()
    c2, _ = bench()
    st = _state(c2)
    assert st["result"]["kind"] == "compare" and st["result"]["on_stage"]
    assert st["result"]["table"]["rows"][0][0] == "pinch car"
    assert _idea(st, "pinch_car.toml")["latest"]["headlines"][0] == "3 of 3 got out through `goal`"
    # an edit moves the stage to that idea's own run; the result is kept for later
    time.sleep(0.05)
    p = folder / "bounce_bar.toml"
    p.write_text(p.read_text().replace('author = "Priya"', 'author = "Priya K"'))
    st = _wait_for(c2, lambda st: not st["result"]["on_stage"])
    assert st["stage"] == "bounce_bar.toml" and st["result"]["kind"] == "compare"
    c2.app.stop()
    c3, _ = bench()   # and after a restart it stays off the stage (the edit came after it)
    assert not _state(c3)["result"]["on_stage"]


def test_failed_sweep_shows_no_old_chart(bench):
    c, folder = bench()
    out = folder / "pinch_car-results"
    out.mkdir()
    (out / "results.csv").write_text("x,out\n1,3\n")
    (out / "line-exit_speed-vs-x.png").write_bytes(b"\x89PNG old")
    session.log_event(folder, "sweep", [folder / "pinch_car.toml"], out, "cli", False, "boom")
    r = _wait_for(c, lambda st: st["result"])["result"]
    assert not r["ok"] and r["error"] == "boom" and r["pictures"] == [] and r["table"] is None
    assert r["uncalibrated"].startswith("UNCALIBRATED")


def test_sweep_result_puts_the_exit_speed_chart_first(tmp_path):
    import os
    import time
    from scrimmage.assembler.bench_session import _result_files
    (tmp_path / "results.csv").write_text("x,exit_speed\n")
    later = time.time() + 5
    for name in ("line-elevation_deg-vs-x.png", "line-exit_speed-vs-x.png", "line-spin_rps-vs-x.png"):
        (tmp_path / name).write_bytes(b"png")
        os.utime(tmp_path / name, (later, later))
    pics, table = _result_files("sweep", tmp_path)
    assert Path(pics[0]).name == "line-exit_speed-vs-x.png" and len(pics) == 3 and table is not None
