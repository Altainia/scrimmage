"""Sweeps of a block test: one setting tried at several values (scrimmage/blocks/sweep.py)."""

from __future__ import annotations

import csv
import json
import threading

import pytest

from scrimmage.blocks.program import TestFileError, loads
from scrimmage.blocks.store import KEEP_SWEEPS, list_runs, load_run
from scrimmage.blocks.sweep import MAX_SWEEP, run_sweep
from scrimmage.config import load_config
from test_blocks_run import MOVE_45, write_test

WAIT = '[[block]]\ndo = "wait"\nseconds = 0.2\n'


def _program(path):
    return loads(path.read_text(encoding="utf-8"), path)


def _table(folder):
    with open(folder / "table.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _left_behind(folder):
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("."))


def test_sweep_chassis_mass(tmp_path):
    path = write_test(tmp_path, MOVE_45)
    original = (tmp_path / "robot.toml").read_text(encoding="utf-8")
    seen = []
    out = run_sweep(_program(path), {"robot": "mass"}, [30, 45, 60], tmp_path / "t-runs",
                    on_progress=lambda k, n, row: seen.append((k, n, row["value"])))
    assert out == tmp_path / "t-runs" / "sweep-001"
    assert seen == [(1, 3, 30.0), (2, 3, 45.0), (3, 3, 60.0)]
    rows = _table(out)
    assert [float(r["value"]) for r in rows] == [30, 45, 60]
    assert all(r["status"] == "finished" and r["outcome"] == "finished" for r in rows)
    assert [k for k in rows[0]] == ["value", "status", "outcome", "t_end", "block 2: arm.pivot passes 40°"]
    assert all(r["block 2: arm.pivot passes 40°"] == "passed" for r in rows)
    meta = json.loads((out / "sweep.json").read_text(encoding="utf-8"))
    assert meta == {"target": {"robot": "mass"}, "values": [30.0, 45.0, 60.0]}
    # each value's run, kept with what was swept
    runs = list_runs(tmp_path / "t-runs")
    assert sorted(e["id"] for e in runs) == ["sweep-001/001", "sweep-001/002", "sweep-001/003"]
    assert load_run(tmp_path / "t-runs", "sweep-001/002")["sweep"] == {"target": {"robot": "mass"}, "value": 45.0,
                                                                        "index": 1}
    # the robot file is untouched and no temp config is left next to it
    assert (tmp_path / "robot.toml").read_text(encoding="utf-8") == original
    assert _left_behind(tmp_path) == [] and _left_behind(tmp_path / "t-runs") == []


def test_sweep_a_move_blocks_to(tmp_path):
    path = write_test(tmp_path, MOVE_45)
    out = run_sweep(_program(path), {"block": [0], "key": "to"}, [30.0, 50.0], tmp_path / "runs")
    rows = _table(out)
    assert [r["status"] for r in rows] == ["check failed", "finished"]
    assert [r["block 2: arm.pivot passes 40°"] for r in rows] == ["failed", "passed"]
    for k, want in ((1, 30.0), (2, 50.0)):
        test = loads(load_run(tmp_path / "runs", f"sweep-001/{k:03d}")["test"], path)
        assert test.blocks[0].args["to"] == want
        assert test.blocks[1] == _program(path).blocks[1]


def test_a_value_that_cant_build_is_a_row(tmp_path):
    path = write_test(tmp_path, WAIT)
    out = run_sweep(_program(path), {"robot": "mass"}, [-5, 40], tmp_path / "runs")
    bad, good = _table(out)
    assert bad["status"] == "couldn't run"
    assert bad["outcome"] == "robot.toml: parts[0] 'drive': mass must be greater than 0, got -5.0"
    assert bad["t_end"] == ""
    assert good["status"] == "finished"
    assert [e["id"] for e in list_runs(tmp_path / "runs")] == ["sweep-001/002"]
    assert _left_behind(tmp_path) == []


def test_a_bad_block_value_is_a_row(tmp_path):
    path = write_test(tmp_path, WAIT)
    out = run_sweep(_program(path), {"block": [0], "key": "seconds"}, [-1, 0.1], tmp_path / "runs")
    bad, good = _table(out)
    assert bad["status"] == "couldn't run" and "seconds can't be negative" in bad["outcome"]
    assert good["status"] == "finished"


def test_sweep_com_and_mount(tmp_path):
    path = write_test(tmp_path, WAIT)
    seen = []

    def spy(k, n, row):
        seen.append(row)

    run_sweep(_program(path), {"robot": "com_up"}, [0.1], tmp_path / "runs", on_progress=spy)
    run_sweep(_program(path), {"mount": "arm", "axis": "yaw"}, [90.0], tmp_path / "runs", on_progress=spy)
    run_sweep(_program(path), {"mount": "arm", "axis": "z"}, [0.05], tmp_path / "runs", on_progress=spy)
    assert [r["status"] for r in seen] == ["finished"] * 3


def test_modified_config_text(tmp_path):
    """The edits land on the right keys, comments kept (checked through the config module's own loader)."""
    from scrimmage.blocks.sweep import _edit_config
    write_test(tmp_path, WAIT)
    text = (tmp_path / "robot.toml").read_text(encoding="utf-8")
    out = _edit_config(text, {"robot": "com_left"}, 0.02)
    assert "# Attach your part to drive.top" in out
    p = tmp_path / "check.toml"
    p.write_text(_edit_config(_edit_config(out, {"robot": "mass"}, 50), {"mount": "arm", "axis": "x"}, 0.1),
                 encoding="utf-8")
    raw = load_config(p).raw
    drive = next(t for t in raw["part"] if t["name"] == "drive")
    arm = next(t for t in raw["part"] if t["name"] == "arm")
    assert drive["mass"] == 50 and drive["com_offset"] == [0.0, 0.02, 0.0]
    assert arm["pos"] == [0.1, 0.0, 0.0]


def test_bad_sweeps_are_refused(tmp_path):
    path = write_test(tmp_path, MOVE_45)
    p = _program(path)
    base = tmp_path / "runs"
    with pytest.raises(ValueError, match=f"at most {MAX_SWEEP} values"):
        run_sweep(p, {"robot": "mass"}, list(range(1, MAX_SWEEP + 2)), base)
    with pytest.raises(ValueError, match="at least one value"):
        run_sweep(p, {"robot": "mass"}, [], base)
    with pytest.raises(TestFileError, match="block 1 has no seconds"):
        run_sweep(p, {"block": [0], "key": "seconds"}, [1.0], base)
    with pytest.raises(TestFileError, match="there's no block 5"):
        run_sweep(p, {"block": [4], "key": "to"}, [1.0], base)
    with pytest.raises(ValueError, match="no part named 'claw'"):
        run_sweep(p, {"mount": "claw", "axis": "x"}, [1.0], base)
    with pytest.raises(ValueError, match="can't sweep"):
        run_sweep(p, {"robot": "height"}, [1.0], base)
    assert not base.exists() or list(base.iterdir()) == []


def test_no_stand_in_chassis(tmp_path):
    from scrimmage.blocks.sweep import _edit_config
    write_test(tmp_path, WAIT)
    text = (tmp_path / "robot.toml").read_text(encoding="utf-8").replace('stock = "chassis"', 'stock = "kitbot"')
    with pytest.raises(ValueError, match="no stand-in chassis"):
        _edit_config(text, {"robot": "mass"}, 40)


def test_only_the_newest_sweeps_are_kept_and_cancel(tmp_path):
    path = write_test(tmp_path, WAIT)
    base = tmp_path / "runs"
    for _ in range(KEEP_SWEEPS + 1):
        run_sweep(_program(path), {"block": [0], "key": "seconds"}, [0.1], base)
    assert sorted(p.name for p in base.iterdir()) == [f"sweep-{i:03d}" for i in range(2, KEEP_SWEEPS + 2)]

    cancel = threading.Event()
    cancel.set()
    out = run_sweep(_program(path), {"block": [0], "key": "seconds"}, [0.1, 0.2], base, cancel=cancel)
    assert [r["status"] for r in _table(out)] == ["cancelled"]
    assert out.name == f"sweep-{KEEP_SWEEPS + 2:03d}"


def test_a_cancelled_sweep_doesnt_push_out_a_complete_one(tmp_path):
    path = write_test(tmp_path, WAIT)
    base = tmp_path / "runs"
    for _ in range(KEEP_SWEEPS):
        run_sweep(_program(path), {"block": [0], "key": "seconds"}, [0.1], base)
    cancel = threading.Event()
    cancel.set()
    out = run_sweep(_program(path), {"block": [0], "key": "seconds"}, [0.1], base, cancel=cancel)
    assert json.loads((out / "sweep.json").read_text(encoding="utf-8"))["cancelled"] is True
    names = sorted(p.name for p in base.iterdir())
    assert names == [f"sweep-{i:03d}" for i in range(1, KEEP_SWEEPS + 2)]       # nothing pruned
    run_sweep(_program(path), {"block": [0], "key": "seconds"}, [0.1], base)   # a complete one prunes the oldest
    assert sorted(p.name for p in base.iterdir()) == [f"sweep-{i:03d}" for i in range(2, KEEP_SWEEPS + 3)]


def test_the_temp_config_is_deleted_while_briefly_held(tmp_path, monkeypatch):
    import scrimmage.blocks.sweep as sweep
    from pathlib import Path
    monkeypatch.setattr(sweep, "REPLACE_WAITS", (0.0, 0.0, 0.0))
    real = Path.unlink
    held = {"n": 2}

    def unlink(self, *a, **kw):
        if ".sweep-" in self.name and held["n"] > 0:
            held["n"] -= 1
            raise PermissionError(13, "held by another program")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink)
    path = write_test(tmp_path, WAIT)
    out = run_sweep(_program(path), {"robot": "mass"}, [40.0], tmp_path / "runs")
    assert held["n"] == 0 and _left_behind(tmp_path) == []
    assert "problems" not in json.loads((out / "sweep.json").read_text(encoding="utf-8"))


def test_a_temp_config_still_held_is_named(tmp_path, monkeypatch):
    import scrimmage.blocks.sweep as sweep
    from pathlib import Path
    monkeypatch.setattr(sweep, "REPLACE_WAITS", (0.0,))
    real = Path.unlink

    def unlink(self, *a, **kw):
        if ".sweep-" in self.name and self.suffix == ".toml":
            raise PermissionError(13, "held by another program")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", unlink)
    path = write_test(tmp_path, WAIT)
    out = run_sweep(_program(path), {"robot": "mass"}, [40.0], tmp_path / "runs")
    [left] = _left_behind(tmp_path)
    assert left.startswith(".robot.sweep-")
    [problem] = json.loads((out / "sweep.json").read_text(encoding="utf-8"))["problems"]
    assert left in problem and "Delete it by hand" in problem
    assert _table(out)[0]["status"] == "finished"


def test_any_error_in_one_value_is_its_row(tmp_path, monkeypatch):
    import scrimmage.blocks.sweep as sweep
    real = sweep.TestRun.run
    calls = {"n": 0}

    def run(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("the physics blew up")
        return real(self, *a, **kw)

    monkeypatch.setattr(sweep.TestRun, "run", run)
    path = write_test(tmp_path, WAIT)
    out = run_sweep(_program(path), {"robot": "mass"}, [30.0, 40.0, 50.0], tmp_path / "runs")
    rows = _table(out)
    assert [r["status"] for r in rows] == ["finished", "couldn't run", "finished"]
    assert rows[1]["outcome"] == "something went wrong (RuntimeError: the physics blew up)"
    assert sorted(e["id"] for e in list_runs(tmp_path / "runs")) == ["sweep-001/001", "sweep-001/003"]
    assert _left_behind(tmp_path) == []


def test_an_interrupted_sweep_keeps_the_runs_done(tmp_path, monkeypatch):
    import scrimmage.blocks.sweep as sweep
    real = sweep.TestRun.run
    calls = {"n": 0}

    def run(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(self, *a, **kw)

    monkeypatch.setattr(sweep.TestRun, "run", run)
    path = write_test(tmp_path, WAIT)
    with pytest.raises(KeyboardInterrupt):
        run_sweep(_program(path), {"robot": "mass"}, [30.0, 40.0], tmp_path / "runs")
    out = tmp_path / "runs" / "sweep-001"
    assert json.loads((out / "sweep.json").read_text(encoding="utf-8"))["cancelled"] is True
    assert [r["status"] for r in _table(out)] == ["finished"]
    assert _left_behind(tmp_path) == [] and _left_behind(tmp_path / "runs") == []


def test_leftovers_of_a_killed_sweep_are_removed(tmp_path):
    import os
    import time
    path = write_test(tmp_path, WAIT)
    base = tmp_path / "runs"
    base.mkdir()
    old = time.time() - 2 * 3600
    stale = [tmp_path / ".robot.sweep-abc.toml", base / ".run-x", base / ".sweep-y"]
    fresh = [tmp_path / ".robot.sweep-new.toml", base / ".run-new"]
    for p in stale + fresh:
        if p.suffix == ".toml":
            p.write_text("", encoding="utf-8")
        else:
            p.mkdir()
            (p / "result.json").write_text("{}", encoding="utf-8")
    for p in stale:
        os.utime(p, (old, old))
    (tmp_path / ".other.toml").write_text("", encoding="utf-8")
    os.utime(tmp_path / ".other.toml", (old, old))
    run_sweep(_program(path), {"robot": "mass"}, [40.0], base)
    assert not any(p.exists() for p in stale)
    assert all(p.exists() for p in fresh) and (tmp_path / ".other.toml").exists()


def test_com_target_has_its_own_message(tmp_path):
    from scrimmage.blocks.sweep import _edit_config
    write_test(tmp_path, WAIT)
    text = (tmp_path / "robot.toml").read_text(encoding="utf-8").replace('stock = "chassis"', 'stock = "kitbot"')
    with pytest.raises(ValueError, match="to move the center of gravity of"):
        _edit_config(text, {"robot": "com_up"}, 0.1)
