"""`scrimmage range check` and `range view`: a range file checked and pictured on its own; and `scrimmage edit` on
a range file says what the file is instead of reading it as a robot config."""

from pathlib import Path

import pytest

from scrimmage.__main__ import main
from scrimmage.testrange.check import is_range_file
from scrimmage.testrange.spec import BUILTIN_RANGES

GOOD = '''
[range]
name = "kickoff"
ball = "2026-fuel"
field = { length = 16.541, width = 8.211 }
[[goal]]
name = "amp_blue"
pos = [1.84, 8.21]
rim_height = 0.889
tilt_deg = 90.0
heading_deg = 90.0
opening = { kind = "rect", width = 0.61, height = 0.457 }
[[floor_balls]]
at = [[2.9, 4.1], [2.9, 5.55]]
[start.sub]
pose = [1.3, 5.55, 0.0]
'''


def write(tmp_path, text, name="r.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_check_says_what_the_range_holds(tmp_path, capsys):
    main(["range", "check", str(write(tmp_path, GOOD))])
    out = capsys.readouterr().out
    assert "r.toml: ok" in out and "'kickoff'" in out
    assert "field 16.541 x 8.211 m" in out
    assert "1 goal (amp_blue)" in out and "2 floor pieces" in out and "1 start (sub)" in out


def test_check_prints_warnings(tmp_path, capsys):
    main(["range", "check", str(write(tmp_path, GOOD.replace("field = { length = 16.541, width = 8.211 }\n", "")))])
    out = capsys.readouterr().out
    assert "r.toml: ok" in out and "warning: goal 'amp_blue'" in out and "outside the field" in out


def test_check_fails_with_the_problem(tmp_path, capsys):
    bad = write(tmp_path, GOOD.replace("tilt_deg = 90.0", "tilt_deg = 194.8"))
    with pytest.raises(SystemExit) as e:
        main(["range", "check", str(bad), "2026"])
    assert e.value.code == 1
    out = capsys.readouterr().out
    assert "tilt_deg must be from 0 to 180" in out
    assert "2026: ok" in out            # every file is checked, a built-in by its name


def test_check_says_a_robot_config_is_not_a_range(tmp_path, capsys):
    cfg = write(tmp_path, '[range]\nfile = "2026"\n[[part]]\nname = "drive"\nstock = "chassis"\n', "robot.toml")
    with pytest.raises(SystemExit) as e:
        main(["range", "check", str(cfg)])
    assert e.value.code == 1
    assert "robot.toml is a robot config" in capsys.readouterr().out


def test_is_range_file():
    import tomllib
    for name, path in BUILTIN_RANGES.items():
        assert is_range_file(tomllib.loads(path.read_text())), name
    assert not is_range_file({"range": {"file": "2026"}, "part": []})
    assert not is_range_file({"robot": {}})
    assert not is_range_file({"range": "x"})


def test_edit_refuses_a_range_file_and_says_what_it_is(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["edit", str(write(tmp_path, GOOD)), "--no-browser"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "r.toml is a range file" in err and "scrimmage range check" in err and "[range] file" in err


def test_view_writes_a_picture(tmp_path, capsys):
    pytest.importorskip("PIL")
    out = tmp_path / "top.png"
    try:
        main(["range", "view", str(write(tmp_path, GOOD)), "--out", str(out)])
    except SystemExit as e:      # no headless GL here: said plainly
        assert "headless rendering" in capsys.readouterr().err
        pytest.skip("no headless GL")
    from PIL import Image
    img = Image.open(out)
    import numpy as np
    assert img.size == (1600, 800) and len(np.unique(np.asarray(img.convert("L")))) > 4


def test_view_needs_a_good_file(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["range", "view", str(write(tmp_path, GOOD.replace("ball =", "bal ="))), "--out",
              str(tmp_path / "x.png")])
    assert e.value.code == 1
    assert "unknown key 'bal'" in capsys.readouterr().err
    assert not (tmp_path / "x.png").exists()


def test_edit_help_names_range_files(capsys):
    with pytest.raises(SystemExit):
        main(["edit", "--help"])
    assert "range check" in capsys.readouterr().out
    assert Path  # noqa: B018
