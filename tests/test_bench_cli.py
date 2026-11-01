"""`scrimmage bench check|run|view`."""

import contextlib
import shutil
from pathlib import Path

import numpy as np
import pytest

from scrimmage.__main__ import main

ROOT = Path(__file__).resolve().parent.parent
PINCH = ROOT / "examples" / "bench" / "pinch_car.toml"


def test_check_prints_ok_and_counts(capsys):
    main(["bench", "check", str(PINCH)])
    out = capsys.readouterr().out
    assert out.startswith("ok: pinch car")
    assert "2 wheels" in out and "1 plate" in out and "1 inlet (3 pieces)" in out and "1 outlet" in out


def test_cli_run_writes_files_and_exit_codes(tmp_path, capsys):
    out = tmp_path / "o"
    main(["bench", "run", str(PINCH), "--out", str(out)])  # returns: exit 0
    printed = capsys.readouterr().out
    assert "3 of 3 left through `shot`" in printed and "UNCALIBRATED" in printed
    names = {p.name for p in out.iterdir()}
    assert {"summary.txt", "pieces.csv", "shafts.csv", "loads.csv", "path_side.png", "pass.png",
            "pass.gif"} <= names
    assert "path_top.png" not in names  # nothing moves across y
    assert "landings.csv" not in names  # no [flight]

    bad = tmp_path / "bad.toml"
    bad.write_text(PINCH.read_text().replace("[[outlet]]", "[[outlet]]\ncolor = 'red'"))
    with pytest.raises(SystemExit) as e:
        main(["bench", "run", str(bad), "--out", str(tmp_path / "b")])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("scrimmage: ") and "color" in err
    with pytest.raises(SystemExit) as e:
        main(["bench", "check", str(tmp_path / "missing.toml")])
    assert e.value.code == 2 and capsys.readouterr().err.startswith("scrimmage: ")


def test_default_results_folder(tmp_path, capsys):
    f = tmp_path / "car.toml"
    shutil.copy(PINCH, f)
    main(["bench", "run", str(f)])
    assert (tmp_path / "car-results" / "summary.txt").is_file()


class FakeViewer:
    def __init__(self, syncs):
        self.left, self.synced = syncs, []
        self.cam = type("Cam", (), {"lookat": np.zeros(3)})()

    def is_running(self):
        return self.left > 0

    def sync(self):
        self.left -= 1
        self.synced.append(True)

    def lock(self):
        return contextlib.nullcontext()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_view_replays_the_run(monkeypatch, capsys):
    from scrimmage.bench import view

    fake = FakeViewer(syncs=5)
    sleeps = []
    monkeypatch.setattr(view, "_launch", lambda model, data: fake)
    monkeypatch.setattr(view.time, "sleep", sleeps.append)
    main(["bench", "view", str(PINCH), "--slow", "0.5"])
    assert len(fake.synced) == 5
    assert sleeps and all(s >= 0 for s in sleeps)
    with pytest.raises(SystemExit) as e:
        main(["bench", "view", str(PINCH), "--slow", "0"])
    assert e.value.code == 2


def test_warnings_are_house_style(tmp_path, capsys):
    """A custom ball borrowing a preset's numbers: `scrimmage: warning: ...`, not a Python UserWarning."""
    import warnings
    hooded = ROOT / "examples" / "bench" / "hooded.toml"
    text = hooded.read_text().replace('preset = "2020-power-cell"',
                                      'kind = "squishy"\nshape = "sphere"\nsize = [0.1778]\nmass = 0.14')
    f = tmp_path / "ball.toml"
    f.write_text(text)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        main(["bench", "check", str(f)])
    err = capsys.readouterr().err
    assert err.startswith("scrimmage: warning: [piece] stiffness_n and restitution not given") and err.count("\n") == 1


def test_cli_run_logs_in_the_session_and_waits_for_the_lock(tmp_path, capsys):
    import threading
    import time

    from scrimmage.bench import session
    f = tmp_path / "car.toml"
    f.write_text(PINCH.read_text().replace("count = 3", "count = 1"))
    with session.run_lock(tmp_path, {"source": "agent", "file": "other.toml"}):
        t = threading.Thread(target=lambda: main(["bench", "run", str(f)]))
        t.start()
        time.sleep(0.5)
        assert session.read_events(tmp_path) == []
    t.join()
    assert "waiting: agent is running other.toml" in capsys.readouterr().err
    (e,) = session.read_events(tmp_path)
    assert (e["kind"], e["source"], e["ok"]) == ("run", "cli", True) and (tmp_path / "car-results" / "summary.txt").exists()
