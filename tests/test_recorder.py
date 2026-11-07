import pytest

from scrimmage.assembler.recorder import DriveRecorder, RecordingError
from scrimmage.scenario import load_scenario
from scrimmage.tracks import PortInputs

STEP = 0.02
CONFIG = None


def pi(ax=(), b=(), pov=()):
    return {0: PortInputs(list(ax), list(b), list(pov))}


def fixture_config():
    from pathlib import Path
    return Path(__file__).parent / "fake_robot.toml"


def record(rec, script):
    """script: list of (step index, mode, ports); sampled at every step boundary."""
    first = True
    n = script[-1][0]
    state = None
    for k in range(n + 1):
        for i, m, p in script:
            if i == k:
                state = (m, p)
        if first:
            rec.start(k * STEP, *state)
            first = False
        else:
            rec.sample(k * STEP, *state)
    rec.stop(n * STEP)


def make(tmp_path):
    rec = DriveRecorder(STEP)
    record(rec, [(0, "disabled", pi([0.0], [False])), (5, "teleop", pi([0.0], [False])),
                 (7, "teleop", pi([0.5], [False])), (9, "teleop", pi([0.5], [True])),
                 (12, "disabled", pi([0.0], [False])), (20, "disabled", pi([0.0], [False]))])
    return rec


def save(rec, tmp_path, **kw):
    return rec.save(tmp_path / "drive.toml", fixture_config(), (1.0, 2.0, 90.0), **kw)


def test_segments_and_replay(tmp_path):
    rec = make(tmp_path)
    r = save(rec, tmp_path)
    assert r["steps"] == 3 and r["pokes"] == 0 and r["path"].endswith("drive.toml")
    sc = load_scenario(tmp_path / "drive.toml")
    assert [s.mode for s in sc.steps] == ["disabled", "teleop", "disabled"]
    assert [s.name for s in sc.steps] == ["disabled 1", "teleop 2", "disabled 3"]
    d = [s.duration for s in sc.steps]
    assert d == pytest.approx([0.1, 0.14, 0.16])
    assert all(round(x / STEP) * STEP == pytest.approx(x) for x in d)
    tele = sc.steps[1].inputs.samples
    assert [s.t for s in tele] == pytest.approx([0.0, 0.04, 0.08])
    assert tele[0].ports[0].axes == [0.0] and tele[1].ports[0].axes == [0.5]
    assert tele[2].ports[0].buttons == [True]
    assert len(sc.steps[0].inputs.samples) == 1
    assert (tmp_path / "drive" / "step2.csv").exists()
    text = (tmp_path / "drive.toml").read_text()
    assert text.startswith("# Recorded in the Drive tab on ")
    assert "from start pose x=1.000, y=2.000, heading=90.0" in text
    assert 'description = "Recorded drive"' in text


def test_late_port_gets_zero_rows_before(tmp_path):
    rec = DriveRecorder(STEP)
    rec.start(0.0, "teleop", {})
    rec.sample(STEP, "teleop", {})
    rec.sample(2 * STEP, "teleop", {1: PortInputs([0.25], [True], [90])})
    rec.stop(3 * STEP)
    save(rec, tmp_path)
    s = load_scenario(tmp_path / "drive.toml").steps[0].inputs.samples
    assert len(s) == 2
    assert s[0].ports[1].axes == [0.0] and s[0].ports[1].buttons == [False] and s[0].ports[1].povs == [-1]
    assert s[1].ports[1].axes == [0.25] and s[1].ports[1].povs == [90]


def test_poke_count_and_zero_length_skipped(tmp_path):
    rec = DriveRecorder(STEP)
    rec.start(0.0, "disabled", {})
    rec.sample(0.0, "teleop", {})
    rec.poke(); rec.poke()
    rec.sample(STEP, "teleop", {})
    rec.stop(2 * STEP)
    assert rec.pokes == 2 and not rec.empty
    r = save(rec, tmp_path)
    assert r["pokes"] == 2 and r["steps"] == 1


def test_overwrite_refusals(tmp_path):
    rec = make(tmp_path)
    save(rec, tmp_path)
    with pytest.raises(RecordingError, match="drive.toml already exists"):
        save(rec, tmp_path)
    save(rec, tmp_path, overwrite=True)
    load_scenario(tmp_path / "drive.toml")
    # a step-CSV folder without its recording TOML isn't a recording: refused, with or without overwrite
    (tmp_path / "drive.toml").unlink()
    for kw in ({}, {"overwrite": True}):
        with pytest.raises(RecordingError, match="folder drive exists without a recording drive.toml") as e:
            save(rec, tmp_path, **kw)
        assert not str(e.value).endswith("already exists")
    assert (tmp_path / "drive" / "step1.csv").exists() and not (tmp_path / "drive.toml").exists()


def test_relative_config(tmp_path):
    rec = make(tmp_path)
    (tmp_path / "sub").mkdir()
    cfg = tmp_path / "robots" / "r.toml"
    cfg.parent.mkdir()
    cfg.write_text("")
    rec.save(tmp_path / "sub" / "d.toml", cfg, (0, 0, 0))
    assert 'config = "../robots/r.toml"' in (tmp_path / "sub" / "d.toml").read_text()


def test_errors(tmp_path):
    rec = DriveRecorder(STEP)
    assert rec.empty and not rec.recording
    with pytest.raises(RecordingError, match="nothing was recorded"):
        save(rec, tmp_path)
    rec = make(tmp_path)
    with pytest.raises(RecordingError, match=r"\.toml"):
        rec.save(tmp_path / "drive.txt", fixture_config(), (0, 0, 0))


def test_overwrite_never_removes_a_folder_it_did_not_make(tmp_path):
    rec = make(tmp_path)
    other = tmp_path / "scenarios"
    (other / "sub").mkdir(parents=True)
    (other / "keep.toml").write_text("x = 1\n")
    for kw in ({}, {"overwrite": True}):
        with pytest.raises(RecordingError, match="scenarios") as e:
            rec.save(tmp_path / "scenarios.toml", fixture_config(), (0, 0, 0), **kw)
        assert not str(e.value).endswith("already exists")   # no Overwrite? offer for a folder it won't remove
    assert (other / "keep.toml").read_text() == "x = 1\n" and (other / "sub").is_dir()
    assert not (tmp_path / "scenarios.toml").exists()
    # a folder of step CSVs with a subfolder isn't recorder-made either
    folder = tmp_path / "drive"
    (folder / "nested").mkdir(parents=True)
    (folder / "step1.csv").write_text("t\n0\n")
    with pytest.raises(RecordingError, match="drive"):
        save(rec, tmp_path, overwrite=True)
    assert (folder / "nested").is_dir() and (folder / "step1.csv").exists()


def test_overwrite_refuses_a_robot_config(tmp_path):
    rec = make(tmp_path)
    robot = tmp_path / "drive.toml"
    robot.write_text(fixture_config().read_text())
    with pytest.raises(RecordingError, match="not a recording") as e:
        save(rec, tmp_path, overwrite=True)
    assert not str(e.value).endswith("already exists")
    assert robot.read_text() == fixture_config().read_text()
    assert not (tmp_path / "drive").exists()


def test_resave_replaces_the_old_recording(tmp_path):
    rec = make(tmp_path)
    save(rec, tmp_path)
    (tmp_path / "drive" / "step9.csv").write_text("t\n0\n")   # an old step the new recording doesn't have
    (tmp_path / "drive" / "step1.trim1.trim2.csv").write_text("t\n0\n")   # Trim's copies are the recording's too
    r = save(rec, tmp_path, overwrite=True)
    assert r["steps"] == 3
    assert sorted(p.name for p in (tmp_path / "drive").iterdir()) == ["step1.csv", "step2.csv", "step3.csv"]
    load_scenario(tmp_path / "drive.toml")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["drive", "drive.toml"]   # nothing left aside


def test_failed_overwrite_keeps_the_old_recording(tmp_path, monkeypatch):
    import os
    rec = make(tmp_path)
    save(rec, tmp_path)
    (tmp_path / "drive" / "step1.csv").write_text("old\n")
    (tmp_path / "drive.toml").write_text((tmp_path / "drive.toml").read_text() + "# old\n")
    old_toml = (tmp_path / "drive.toml").read_text()
    old_csvs = {p.name: p.read_text() for p in (tmp_path / "drive").iterdir()}
    real = os.replace

    def failing(src, dst):
        if str(dst) == str(tmp_path / "drive.toml") and str(src).endswith("scenario.toml"):
            raise OSError(28, "No space left on device")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(OSError):
        save(rec, tmp_path, overwrite=True)
    monkeypatch.setattr(os, "replace", real)
    assert (tmp_path / "drive.toml").read_text() == old_toml
    assert {p.name: p.read_text() for p in (tmp_path / "drive").iterdir()} == old_csvs
    assert sorted(p.name for p in tmp_path.iterdir()) == ["drive", "drive.toml"]


def test_overwrite_refuses_a_hand_written_scenario(tmp_path):
    rec = make(tmp_path)
    mine = tmp_path / "drive.toml"
    text = f'description = "mine"\nconfig = "{fixture_config().as_posix()}"\n\n[[step]]\nmode = "auto"\nduration = 1.0\n'
    mine.write_text(text)
    load_scenario(mine)   # a real scenario, just not a recording
    for kw in ({}, {"overwrite": True}):
        with pytest.raises(RecordingError, match="drive.toml is not a recording made in the Drive tab") as e:
            save(rec, tmp_path, **kw)
        assert not str(e.value).endswith("already exists")
    assert mine.read_text() == text and not (tmp_path / "drive").exists()


def test_a_partly_failed_restore_names_the_piece_kept_in_the_staging_folder(tmp_path, monkeypatch):
    import os
    rec = make(tmp_path)
    save(rec, tmp_path)
    (tmp_path / "drive" / "step1.csv").write_text("old\n")
    (tmp_path / "drive.toml").write_text((tmp_path / "drive.toml").read_text() + "# old\n")
    old_toml = (tmp_path / "drive.toml").read_text()
    real = os.replace

    def failing(src, dst):
        if str(dst) == str(tmp_path / "drive.toml"):   # installing the new TOML, and putting the old one back
            raise OSError(28, "No space left on device")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(RecordingError) as e:
        save(rec, tmp_path, overwrite=True)
    monkeypatch.setattr(os, "replace", real)
    stages = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert len(stages) == 1
    # cleanup E6: the folder went back, so the message names only the TOML, and where it is kept
    assert str(e.value) == (f"can't write drive.toml ([Errno 28] No space left on device), and only part of the old "
                            f"recording could be put back: the old drive.toml is kept as {stages[0] / 'old.toml'}")
    assert (stages[0] / "old.toml").read_text() == old_toml   # nothing lost
    assert (tmp_path / "drive" / "step1.csv").read_text() == "old\n"   # the folder went back


def test_a_failed_restore_keeps_the_old_recording_in_the_staging_folder(tmp_path, monkeypatch):
    import os
    rec = make(tmp_path)
    save(rec, tmp_path)
    (tmp_path / "drive" / "step1.csv").write_text("old\n")
    real = os.replace

    def failing(src, dst):
        if str(dst) in (str(tmp_path / "drive.toml"), str(tmp_path / "drive")):   # no piece goes in, or back
            raise OSError(28, "No space left on device")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", failing)
    with pytest.raises(RecordingError, match="couldn't be put back: it is kept in") as e:
        save(rec, tmp_path, overwrite=True)
    monkeypatch.setattr(os, "replace", real)
    stages = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert len(stages) == 1 and str(stages[0]) in str(e.value)
    assert (stages[0] / "old-folder" / "step1.csv").read_text() == "old\n" and (stages[0] / "old.toml").exists()


def test_game_data_and_match_settings(tmp_path):
    """A step carries game_data only when what the robot had at its start differs from the step before (from ""
    for the first); [match] holds the settings that differ from the defaults."""
    rec = DriveRecorder(STEP)
    ports = pi([0.0], [False])
    rec.start(0.0, "auto", ports, game_data="", match={"station": "red2", "fms": False, "timer": False,
                                                       "auto_seconds": 20.0, "teleop_seconds": 140.0})
    rec.sample(5 * STEP, "teleop", ports, game_data="R")
    rec.sample(10 * STEP, "disabled", ports, game_data="R")
    rec.sample(12 * STEP, "teleop", ports, game_data="R")
    rec.sample(15 * STEP, "teleop", ports, game_data="B")   # edited during Teleop: a step of its own
    rec.stop(20 * STEP)
    save(rec, tmp_path)
    sc = load_scenario(tmp_path / "drive.toml")
    assert [s.mode for s in sc.steps] == ["auto", "teleop", "disabled", "teleop", "teleop"]
    text = (tmp_path / "drive.toml").read_text()
    assert [s.game_data for s in sc.steps] == [None, "R", None, None, "B"]
    assert text.count("game_data") == 2
    assert sc.match == {"station": "red2"}


def test_match_keys_that_differ_from_the_config_are_saved(tmp_path):
    """The tab put the station back to blue1 over a config's red3: the recording says so, or a replay would use
    the config's."""
    rec = DriveRecorder(STEP, config_match={"station": "red3", "fms": False, "timer": True, "auto_seconds": 20.0,
                                            "teleop_seconds": 140.0})
    rec.start(0.0, "teleop", pi([0.0]), match={"station": "blue1", "fms": False, "timer": True,
                                               "auto_seconds": 20.0, "teleop_seconds": 140.0})
    rec.stop(5 * STEP)
    save(rec, tmp_path)
    assert load_scenario(tmp_path / "drive.toml").match == {"station": "blue1", "timer": True}


def test_no_match_table_with_the_defaults(tmp_path):
    save(make(tmp_path), tmp_path)
    text = (tmp_path / "drive.toml").read_text()
    assert "[match]" not in text and "game_data" not in text
