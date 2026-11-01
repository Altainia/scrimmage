"""Bench sessions: the run log, talked-about keys and the cross-process run lock."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from scrimmage.bench.session import (bump, changed_keys, focus, lock_holder, log_event, read_events, run_lock)

ROOT = Path(__file__).resolve().parent.parent


def _hold(folder: Path, seconds: float = 30) -> subprocess.Popen:
    code = ("import sys,time\nfrom pathlib import Path\nfrom scrimmage.bench.session import run_lock\n"
            "with run_lock(Path(sys.argv[1]), {'source': 'agent', 'file': 'x.toml'}):\n"
            "    print('held', flush=True)\n    time.sleep(float(sys.argv[2]))\n")
    p = subprocess.Popen([sys.executable, "-c", code, str(folder), str(seconds)], stdout=subprocess.PIPE, text=True,
                         cwd=ROOT)
    assert p.stdout.readline().strip() == "held"
    return p


def test_log_roundtrip_and_since(tmp_path):
    log_event(tmp_path, "run", [tmp_path / "a.toml"], tmp_path / "a-results", "cli", True)
    log_event(tmp_path, "run", [tmp_path / "b.toml"], tmp_path / "b-results", "agent", False, "boom")
    assert [e["seq"] for e in read_events(tmp_path)] == [0, 1]
    assert read_events(tmp_path, 1)[0]["error"] == "boom"
    e = read_events(tmp_path)[0]
    assert e["files"] == [str((tmp_path / "a.toml").resolve())] and e["source"] == "cli" and e["ok"] is True


def test_torn_last_line_is_ignored(tmp_path):
    log_event(tmp_path, "run", [tmp_path / "a.toml"], tmp_path / "r", "cli", True)
    with open(tmp_path / ".scrimmage-bench" / "log.jsonl", "a") as f:
        f.write('{"seq": 1, "t": 5, "ki')
    assert [e["seq"] for e in read_events(tmp_path)] == [0]
    log_event(tmp_path, "run", [tmp_path / "a.toml"], tmp_path / "r", "cli", True)
    assert [e["seq"] for e in read_events(tmp_path)] == [0, 1]


def test_bump_moves_to_front_and_caps(tmp_path):
    f = tmp_path / "a.toml"
    bump(tmp_path, f, ["wheel.top.diameter", "run.duration"])
    bump(tmp_path, f, ["run.duration"])
    assert focus(tmp_path, f) == ["run.duration", "wheel.top.diameter"]
    bump(tmp_path, f, [f"run.k{i}" for i in range(20)])
    assert len(focus(tmp_path, f)) == 12
    assert focus(tmp_path, tmp_path / "other.toml") == []


def test_changed_keys():
    a = {"wheel": [{"name": "top", "diameter": 0.1}], "run": {"duration": 2}}
    b = {"wheel": [{"name": "top", "diameter": 0.12}], "run": {"duration": 2}, "bar": [{"name": "k", "spring": 3}]}
    assert set(changed_keys(a, b)) == {"wheel.top.diameter", "bar.k", "bar.k.spring"}
    # removed entry, shafts, singles
    assert set(changed_keys(b, a)) == {"wheel.top.diameter", "bar.k", "bar.k.spring"}
    c = {"shaft": {"s": {"motor": "kraken", "count": 1}}, "run": {"duration": 3}}
    d = {"shaft": {"s": {"motor": "kraken", "count": 2}, "t": {"motor": "neo"}}, "run": {"duration": 3}}
    assert set(changed_keys(c, d)) == {"shaft.s.count", "shaft.t", "shaft.t.motor"}


def test_second_process_waits_and_sees_holder(tmp_path):
    p = _hold(tmp_path, 1.5)
    try:
        h = lock_holder(tmp_path)
        assert h and h["source"] == "agent" and h["pid"] == p.pid
        seen = []
        t0 = time.time()
        with run_lock(tmp_path, {"source": "cli", "file": "y.toml"}, wait=seen.append):
            assert lock_holder(tmp_path)["source"] == "cli"
        assert time.time() - t0 > 0.5
        assert len(seen) == 1 and seen[0]["file"] == "x.toml"
    finally:
        p.kill()
        p.wait()
    assert lock_holder(tmp_path) is None


def test_lock_from_killed_process_is_free(tmp_path):
    p = _hold(tmp_path)
    assert lock_holder(tmp_path) is not None
    os.kill(p.pid, signal.SIGKILL)
    p.wait()
    assert (tmp_path / ".scrimmage-bench" / "holder.json").exists()  # stale
    assert lock_holder(tmp_path) is None
    t0 = time.time()
    with run_lock(tmp_path, {"source": "cli", "file": "y.toml"}, wait=lambda h: (_ for _ in ()).throw(AssertionError)):
        pass
    assert time.time() - t0 < 1


def test_reentry_in_one_thread_raises(tmp_path):
    import pytest
    with run_lock(tmp_path, {"source": "cli", "file": "a"}):
        with pytest.raises(RuntimeError, match="already held"):
            with run_lock(tmp_path, {"source": "cli", "file": "a"}):
                pass
    with run_lock(tmp_path, {"source": "cli", "file": "a"}):  # released cleanly
        pass


def test_logging_failure_does_not_hide_the_bench_error(tmp_path, monkeypatch, capsys):
    import pytest

    from scrimmage.bench import session

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(session, "log_event", boom)
    with pytest.raises(ValueError, match="real"):
        with session.recorded(tmp_path, "run", [tmp_path / "a.toml"], tmp_path / "r", "cli"):
            raise ValueError("real")
    with session.recorded(tmp_path, "run", [tmp_path / "a.toml"], tmp_path / "r", "cli"):
        pass
    assert "could not write the session log" in capsys.readouterr().err


def test_unwritable_folder_runs_without_lock_and_log(tmp_path, capsys):
    import pytest

    from scrimmage.bench import session
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("needs POSIX permissions as a non-root user")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o555)
    try:
        ran = []
        with session.recorded(ro, "run", [ro / "a.toml"], tmp_path / "r", "cli"):
            ran.append(1)
        assert ran and "not shared with the Bench tab" in capsys.readouterr().err
        assert not (ro / ".scrimmage-bench").exists()
    finally:
        ro.chmod(0o755)
