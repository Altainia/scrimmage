import subprocess
import sys
from pathlib import Path

import pytest

from scrimmage.config import ConfigError, load_config
from scrimmage.scenario import load_scenario
from scrimmage.teamfolders import TeamFolderError, expand, folders, relativize, set_folder

TESTS = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "f.json"))
    monkeypatch.delenv("SCRIMMAGE_CAD", raising=False)
    monkeypatch.delenv("SCRIMMAGE_CODE", raising=False)


def test_expand_and_env_override(tmp_path, monkeypatch):
    set_folder("cad", tmp_path / "cad")
    assert expand("{cad}/arm") == str(tmp_path / "cad") + "/arm"
    monkeypatch.setenv("SCRIMMAGE_CAD", str(tmp_path / "other"))
    assert expand("{cad}/arm").startswith(str(tmp_path / "other"))
    assert expand("parts/arm") == "parts/arm"


def test_unset_folder_error_is_plain():
    with pytest.raises(TeamFolderError, match="code folder.*SCRIMMAGE_CODE"):
        expand("{code}/robot")
    with pytest.raises(TeamFolderError, match="CAD folder.*start page.*SCRIMMAGE_CAD"):
        expand("{cad}/arm")


def test_set_folder_can_forget(tmp_path):
    set_folder("code", tmp_path)
    assert folders() == {"cad": None, "code": tmp_path}
    set_folder("code", None)
    assert folders()["code"] is None


def test_relativize_longest_match(tmp_path):
    set_folder("cad", tmp_path)
    set_folder("code", tmp_path / "code")
    assert relativize(tmp_path / "code" / "r") == "{code}/r"
    assert relativize(tmp_path / "arm") == "{cad}/arm"
    assert relativize(tmp_path / "code") == "{code}"
    assert relativize(Path("/elsewhere/x")) == "/elsewhere/x"


ROBOT = '''
[robot_program]
command = ["{{python}}", "-m", "robotpy", "sim"]
cwd = "{code}/bot"
[[part]]
name = "drive"
stock = "chassis"
ids = {{ gyro = "Pigeon2:13" }}
[[part]]
name = "intake"
path = "{{cad}}/intake"
attach = "drive.top"
ids = {{ roller = "PWM:0" }}
'''.replace("{code}", "{{code}}")


def test_config_resolves_program_cwd_and_part_path(tmp_path):
    from scrimmage.world import World
    set_folder("code", tmp_path / "code")
    set_folder("cad", TESTS / "parts")
    p = tmp_path / "robot.toml"
    p.write_text(ROBOT.format())
    c = load_config(p)
    assert c.robot_program.cwd == (tmp_path / "code" / "bot").resolve()
    assert World(c.raw, c.dir).parts is not None


def test_config_unset_folder_is_a_config_error(tmp_path):
    p = tmp_path / "robot.toml"
    p.write_text(ROBOT.format())
    with pytest.raises(ConfigError, match="code folder.*SCRIMMAGE_CODE"):
        load_config(p)


def test_part_path_unset_folder_is_plain(tmp_path):
    from scrimmage.world import World
    set_folder("code", tmp_path)
    p = tmp_path / "robot.toml"
    p.write_text(ROBOT.format())
    c = load_config(p)
    with pytest.raises(ValueError, match="CAD folder.*SCRIMMAGE_CAD"):
        World(c.raw, c.dir)


def test_range_file_placeholder(tmp_path):
    from scrimmage.testrange.spec import BUILTIN_RANGES, load_range
    name, builtin = next(iter(BUILTIN_RANGES.items()))
    set_folder("cad", builtin.parent)
    assert load_range("{cad}/" + builtin.name, tmp_path).path == builtin
    set_folder("cad", None)
    with pytest.raises(ValueError, match="CAD folder.*SCRIMMAGE_CAD"):
        load_range("{cad}/" + builtin.name, tmp_path)


def test_scenario_config_placeholder(tmp_path):
    set_folder("cad", tmp_path / "cad")
    s = tmp_path / "s.toml"
    s.write_text('config = "{cad}/robot.toml"\n[[step]]\nmode = "disabled"\nduration = 1.0\n')
    assert load_scenario(s).config_path == (tmp_path / "cad" / "robot.toml").resolve()
    set_folder("cad", None)
    with pytest.raises(ValueError, match="CAD folder.*SCRIMMAGE_CAD"):
        load_scenario(s)


def test_cli_run_with_unset_folder_exits_2_plain(tmp_path):
    s = tmp_path / "s.toml"
    s.write_text('config = "{cad}/robot.toml"\n[[step]]\nmode = "disabled"\nduration = 1.0\n')
    r = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(s)], capture_output=True, text=True,
                       env={**__import__("os").environ, "SCRIMMAGE_FOLDERS": str(tmp_path / "none.json")})
    assert r.returncode == 2
    assert "CAD folder" in r.stderr and "SCRIMMAGE_CAD" in r.stderr
    assert "Traceback" not in r.stderr
