import json
from pathlib import Path

import mujoco
import pytest

from scrimmage.__main__ import main
from scrimmage.config import build_world, load_config

ROOT = Path(__file__).resolve().parent.parent
FAKE = str(Path(__file__).resolve().parent / "fake_robot.toml")
KITBOT = str(ROOT / "configs" / "kitbot.toml")


def test_model_prints_xml(capsys):
    main(["model", KITBOT])
    out = capsys.readouterr().out
    assert "<mujoco" in out and "<body" in out


def test_model_writes_file(tmp_path, capsys):
    f = tmp_path / "m.xml"
    main(["model", KITBOT, "-o", str(f)])
    assert "<mujoco" in f.read_text()
    assert mujoco.MjModel.from_xml_path(str(f)).nbody > 1


def test_model_unknown_stock_part_exits_2(tmp_path, capsys):
    cfg = tmp_path / "bad.toml"
    cfg.write_text('[[part]]\nname = "x"\nstock = "no_such_part"\n')
    with pytest.raises(SystemExit) as e:
        main(["model", str(cfg)])
    assert e.value.code == 2
    assert "no_such_part" in capsys.readouterr().err


def test_model_hand_written_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        main(["model", FAKE])
    assert e.value.code == 2
    assert "this config uses a hand-written model:" in capsys.readouterr().err


def test_model_bad_config_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        main(["model", "/nonexistent.toml"])
    assert e.value.code == 2


def test_parts_lists_and_describes(capsys):
    main(["parts"])
    listing = capsys.readouterr().out
    assert listing.strip()
    assert "chassis" in listing
    main(["parts", "chassis"])
    assert "slots:" in capsys.readouterr().out


def test_parts_unknown_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        main(["parts", "no_such_part"])
    assert e.value.code == 2
    assert "scrimmage:" in capsys.readouterr().err


def test_part_tree():
    w = build_world(load_config(KITBOT))
    tree = w.part_tree()
    assert tree and {"name", "source", "attach", "slots", "mounts", "joints"} <= set(tree[0])
    assert all(isinstance(s["kind"], str) for t in tree for s in t["slots"].values())
    assert build_world(load_config(FAKE)).part_tree() is None


def test_log_warnings_logs_each_line(tmp_path, caplog):
    from parts_fake import WARNING, parts_config
    from scrimmage.__main__ import _log_warnings
    world = build_world(load_config(parts_config(tmp_path)))
    with caplog.at_level("WARNING", logger="scrimmage"):
        _log_warnings(world)
    assert any(WARNING in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)
