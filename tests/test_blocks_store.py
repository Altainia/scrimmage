"""Kept runs of a block test and their self-contained replay files (scrimmage/blocks/store.py), and
`scrimmage test run --out`."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import numpy as np
import pytest

from scrimmage.blocks.program import loads
from scrimmage.blocks.run import MOTION_HZ, TestRun, world_for
from scrimmage.blocks.store import KEEP_RUNS, list_runs, load_run, read_motion, runs_dir, save_run
from test_assembler_core import stl_bytes
from test_blocks_run import MOVE_45, _cli, write_test

WAIT_1 = '[[block]]\ndo = "wait"\nseconds = 1.0\n'


def _run(path):
    program = loads(path.read_text(encoding="utf-8"), path)
    return program, TestRun(world_for(program), program).run()


def test_save_then_load(tmp_path):
    program, r = _run(write_test(tmp_path, MOVE_45))
    base = tmp_path / "t-runs"
    folder = save_run(r, program, base)
    assert folder == base / "001"
    assert sorted(p.name for p in folder.iterdir()) == ["motion.bin", "result.json", "signals.csv", "view.json"]
    got = load_run(base, "001")
    assert got["status"] == r.status == "finished"
    assert got["outcome"] == r.outcome
    assert got["t_end"] == pytest.approx(r.t_end)
    assert got["checks"][0]["passed"] is True and got["checks"][0]["line"] == r.checks[0]["line"]
    assert got["sweep"] is None and got["started"]
    assert loads(got["test"], program.path).blocks == program.blocks
    assert got["signals"]["t"] == pytest.approx(r.signals["t"])
    assert got["signals"]["arm.pivot"] == pytest.approx(r.signals["arm.pivot"], abs=1e-6)
    # the view holds the robot's geoms and the field's
    assert got["view"]["geoms"] and got["view"]["world_geoms"]
    assert got["view"] == r.view
    [entry] = list_runs(base)
    assert entry == {"id": "001", "status": "finished", "outcome": "finished", "started": got["started"],
                     "sweep": None}
    with pytest.raises(FileNotFoundError):
        load_run(base, "002")
    with pytest.raises(FileNotFoundError):
        load_run(base, "../t-runs/001")


def test_motion_file(tmp_path):
    program, r = _run(write_test(tmp_path, WAIT_1))
    folder = save_run(r, program, tmp_path / "runs")
    raw = (folder / "motion.bin").read_bytes()
    magic, version, frames, n = struct.unpack_from("<4sHII", raw)
    assert (magic, version, frames, n) == (b"SCRM", 1, len(r.motion), len(r.movers))
    assert len(raw) == struct.calcsize("<4sHII") + frames * (8 + n * 7 * 4)
    movers, times, poses = read_motion(folder / "motion.bin")
    assert movers == len(r.movers) > 1
    assert poses.shape == (len(r.motion), movers, 7) and poses.dtype == np.float32
    assert np.allclose(np.diff(times), 1.0 / MOTION_HZ, atol=2e-3)
    assert len(times) == pytest.approx(MOTION_HZ * (r.t_end - times[0]) + 1, abs=2)
    assert np.array_equal(poses[-1], r.motion[-1][1])
    with pytest.raises(ValueError, match="isn't a replay"):
        (tmp_path / "bad.bin").write_bytes(b"nope" + raw[4:])
        read_motion(tmp_path / "bad.bin")


def test_only_the_newest_runs_are_kept(tmp_path):
    program, r = _run(write_test(tmp_path, WAIT_1))
    base = tmp_path / "runs"
    for _ in range(KEEP_RUNS):
        save_run(r, program, base)
    assert sorted(p.name for p in base.iterdir()) == [f"{i:03d}" for i in range(1, KEEP_RUNS + 1)]
    save_run(r, program, base)                    # the 11th deletes the oldest
    assert sorted(p.name for p in base.iterdir()) == [f"{i:03d}" for i in range(2, KEEP_RUNS + 2)]
    assert [e["id"] for e in list_runs(base)] == [f"{i:03d}" for i in range(KEEP_RUNS + 1, 1, -1)]
    assert not [p for p in base.iterdir() if p.name.startswith(".")]     # no temp folder left behind


WEDGE_TOML = '[part]\nname = "wedge"\nformat = 1\n[[body]]\nname = "b"\nmass = 1\ngeom = [{ mesh = "wedge.stl" }]\n'


def _wedge(folder, size: float) -> None:
    a, b, c, d = (0, 0, 0), (size, 0, 0), (0, size, 0), (0, 0, size)
    (folder / "wedge.stl").write_bytes(stl_bytes([(a, c, b), (a, b, d), (a, d, c), (b, c, d)]))


def _verts(view) -> np.ndarray:
    import base64
    return np.frombuffer(base64.b64decode(view["meshes"][0]["vert"]), "<f4").reshape(-1, 3)


def test_an_old_run_keeps_its_own_meshes_after_the_part_is_imported_again(tmp_path):
    """Review Focus 5 (server half): the replay of a run from before Import again draws the old geometry."""
    part = tmp_path / "wedge"
    part.mkdir()
    (part / "part.toml").write_text(WEDGE_TOML, encoding="utf-8")
    _wedge(part, 0.1)
    path = write_test(tmp_path, WAIT_1)
    robot = tmp_path / "robot.toml"
    robot.write_text(robot.read_text(encoding="utf-8") + '\n[[part]]\nname = "w"\npath = "wedge"\n'
                     'attach = "drive.top"\n', encoding="utf-8")
    base = tmp_path / "t-runs"
    program, r = _run(path)
    save_run(r, program, base)
    _wedge(part, 0.2)                             # the part's meshes replaced (Import again), a while later
    st = (part / "wedge.stl").stat()              # (MuJoCo caches a mesh file by its name and time, to the second)
    os.utime(part / "wedge.stl", (st.st_atime + 5, st.st_mtime + 5))
    program, r2 = _run(path)
    save_run(r2, program, base)
    old, new = load_run(base, "001")["view"], load_run(base, "002")["view"]
    size = np.ptp(_verts(old), axis=0).max()       # MuJoCo recenters a mesh, so compare its extent
    assert np.ptp(_verts(new), axis=0).max() == pytest.approx(2 * size, rel=1e-4)


def test_runs_dir():
    assert runs_dir(Path("a") / "climb.test.toml") == Path("a") / "climb-runs"
    assert runs_dir(Path("a") / "odd.toml") == Path("a") / "odd-runs"


def test_cli_keeps_the_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    path = write_test(tmp_path, MOVE_45)
    assert _cli(["test", "run", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"kept in {tmp_path / 't-runs' / '001'}" in out
    assert load_run(tmp_path / "t-runs", "001")["status"] == "finished"

    there = tmp_path / "elsewhere"
    assert _cli(["test", "run", str(path), "--out", str(there)]) == 0
    assert f"kept in {there / '001'}" in capsys.readouterr().out
    assert [e["id"] for e in list_runs(there)] == ["001"]
    assert len(list_runs(tmp_path / "t-runs")) == 1


def _fake_run(base, run_id: str, started: str) -> None:
    import json
    folder = base / run_id
    folder.mkdir(parents=True)
    (folder / "result.json").write_text(json.dumps({"status": "finished", "outcome": "finished",
                                                    "started": started, "sweep": None}), encoding="utf-8")


def test_runs_sort_by_time_and_number(tmp_path):
    base = tmp_path / "runs"
    # across a fall-back DST change: 01:30-04:00 is earlier than 01:10-05:00 though its text sorts later
    _fake_run(base, "001", "2026-11-01T01:30:00.000-04:00")
    _fake_run(base, "002", "2026-11-01T01:10:00.000-05:00")
    # the same time: the higher number is newer, past 999 too, and a later sweep's run after a plain run
    same = "2026-11-02T10:00:00.000+00:00"
    _fake_run(base, "999", same)
    _fake_run(base, "1000", same)
    _fake_run(base, "sweep-002/001", same)
    _fake_run(base, "sweep-010/002", same)
    ids = [e["id"] for e in list_runs(base)]
    assert ids == ["sweep-010/002", "sweep-002/001", "1000", "999", "002", "001"]


def test_started_is_when_the_run_started(tmp_path):
    from datetime import datetime
    program, r = _run(write_test(tmp_path, WAIT_1))
    assert r.started and datetime.fromisoformat(r.started).tzinfo is not None
    save_run(r, program, tmp_path / "runs")
    assert load_run(tmp_path / "runs", "001")["started"] == r.started
