import sys
from pathlib import Path

import pytest

from scrimmage.config import DEFAULT_ODOMETRY, ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path, text):
    p = tmp_path / "robot.toml"
    p.write_text(text)
    return p


def test_kitbot_defaults():
    c = load_config(ROOT / "configs" / "kitbot.toml")
    assert c.step == 0.005
    assert c.dir == ROOT / "configs"
    assert c.odometry_topic == DEFAULT_ODOMETRY
    assert c.nt_port == 5810 and c.step_timeout == 10.0


def test_robot_program_and_python_placeholder(tmp_path):
    c = load_config(write(tmp_path, '[robot_program]\ncommand = ["{python}", "robot.py"]\ncwd = "sub"\n'
                                    'env = { A = 1 }\nstart_timeout = 5\n'))
    rp = c.robot_program
    assert rp.command == [sys.executable, "robot.py"]
    assert rp.cwd == (tmp_path / "sub").resolve()
    assert rp.env == {"A": "1"} and rp.start_timeout == 5.0


@pytest.mark.parametrize("text, msg", [
    ("lockstep = 5\n", "[lockstep] must be a table"),
    ('[lockstep]\nstep = "fast"\n', "[lockstep] step must be a number"),
    ('[robot_program]\ncommand = "python robot.py"\n', "[robot_program] command must be a list of strings"),
    ("[telemetry]\nodometry = 3\n", "[telemetry] odometry must be"),
    ('[session]\nstep_timeout = "x"\n', "[session] step_timeout must be a number"),
])
def test_bad_values(tmp_path, text, msg):
    with pytest.raises(ConfigError, match=msg.replace("[", r"\[").replace("]", r"\]")):
        load_config(write(tmp_path, text))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_gradle_command_defaults_to_a_long_start_timeout(tmp_path):
    c = load_config(write(tmp_path, '[robot_program]\ncommand = ["./gradlew", "simulateJava"]\n'))
    assert c.robot_program.start_timeout == 180.0
    c = load_config(write(tmp_path, '[robot_program]\ncommand = ["./gradlew", "simulateJava"]\nstart_timeout = 20\n'))
    assert c.robot_program.start_timeout == 20.0
    c = load_config(write(tmp_path, '[robot_program]\ncommand = ["{python}", "robot.py"]\n'))
    assert c.robot_program.start_timeout == 30.0


def _config_with(tmp_path, extra: str):
    from pathlib import Path
    base = (Path(__file__).resolve().parent / "fake_robot.toml").read_text()
    base = base.replace('"models/', f'"{Path(__file__).resolve().parent / "models"}/')
    p = tmp_path / "dup.toml"
    p.write_text(base + "\n" + extra)
    return p


DUP_MOTOR = """
[[motor]]
name = "{name}"
ctre = "TalonFX:3"
joint = "left_center"
motor = "KrakenX60"
gear_ratio = 10.0
"""


def test_two_bindings_on_one_device_are_rejected(tmp_path):
    import pytest
    from scrimmage.config import ConfigError, build_world, load_config
    cfg = load_config(_config_with(tmp_path, DUP_MOTOR.format(name="fl_drive") + DUP_MOTOR.format(name="fr_drive")))
    with pytest.raises(ConfigError, match=r"CANMotor Talon FX \(v6\)\[3\] is bound by motor fl_drive and motor fr_drive"):
        build_world(cfg)


def test_a_motor_and_its_rotor_sensor_share_a_name_without_conflict(tmp_path):
    from scrimmage.config import build_world, load_config
    build_world(load_config(_config_with(tmp_path, DUP_MOTOR.format(name="drive"))))  # one binding, two HAL devices


def test_load_config_text_resolves_relative_paths(tmp_path):
    from scrimmage.config import load_config_text, build_world
    (tmp_path / "parts").mkdir()
    import shutil
    shutil.copytree(Path(__file__).parent / "parts" / "intake", tmp_path / "parts" / "intake")
    text = '[[part]]\nname = "drive"\nstock = "chassis"\n[[part]]\nname = "i"\npath = "parts/intake"\nattach = "drive.top"\n'
    cfg = load_config_text(text, tmp_path / "robot.toml")     # the file need not exist
    assert cfg.path == (tmp_path / "robot.toml").resolve()
    assert build_world(cfg).parts.instances[1].name == "i"


def test_load_config_text_reports_toml_errors_with_the_path(tmp_path):
    from scrimmage.config import ConfigError, load_config_text
    with pytest.raises(ConfigError, match=r"robot\.toml: .*"):
        load_config_text("[[part]\n", tmp_path / "robot.toml")


def test_sim_mode_fast_sets_the_robots_env(tmp_path):
    base = '[robot_program]\ncommand = ["{python}", "robot.py"]\n'
    c = load_config(write(tmp_path, base))
    assert c.robot_program.sim_mode == "phoenix" and "SCRIMMAGE_MODE" not in c.robot_program.env
    c = load_config(write(tmp_path, base + 'sim_mode = "fast"\nenv = { A = 1 }\n'))
    assert c.robot_program.sim_mode == "fast" and c.robot_program.env == {"A": "1", "SCRIMMAGE_MODE": "fast"}
    with pytest.raises(ConfigError, match=r"sim_mode.*fast.*phoenix.*'x'"):
        load_config(write(tmp_path, base + 'sim_mode = "x"\n'))


def test_sim_mode_unused_says_why(tmp_path):
    from scrimmage.config import sim_mode_unused
    base = '[robot_program]\ncommand = ["{python}", "robot.py"]\n'
    plain = load_config(write(tmp_path, base))
    assert sim_mode_unused(plain, None, launching=True) is None
    assert sim_mode_unused(plain, "fast", launching=True) is None
    assert sim_mode_unused(plain, None, launching=False) is None       # nothing asked for
    msg = sim_mode_unused(plain, "fast", launching=False)
    assert msg and "has no effect" in msg and "doesn't launch the robot program" in msg and "SCRIMMAGE_MODE" in msg
    fast = load_config(write(tmp_path, base + 'sim_mode = "fast"\n'))
    assert "has no effect" in sim_mode_unused(fast, None, launching=False)
    none = load_config(write(tmp_path, ""))
    msg = sim_mode_unused(none, "fast", launching=True)
    assert msg and "no [robot_program]" in msg
    assert sim_mode_unused(none, None, launching=False) is None


def test_set_sim_mode_overrides_the_config(tmp_path):
    from scrimmage.config import set_sim_mode
    base = '[robot_program]\ncommand = ["{python}", "robot.py"]\n'
    c = load_config(write(tmp_path, base))
    set_sim_mode(c, "fast")
    assert c.robot_program.sim_mode == "fast" and c.robot_program.env["SCRIMMAGE_MODE"] == "fast"
    set_sim_mode(c, "phoenix")
    assert c.robot_program.sim_mode == "phoenix" and "SCRIMMAGE_MODE" not in c.robot_program.env
    set_sim_mode(c, None)                           # no override
    with pytest.raises(ConfigError, match="sim_mode"):
        set_sim_mode(c, "x")
    set_sim_mode(load_config(write(tmp_path, '[telemetry]\n')), "fast")   # no robot program: nothing to set


def test_telemetry_nt_switch(tmp_path):
    """[telemetry] nt = false: the robot program serves no NetworkTables, and a session doesn't wait for it."""
    from scrimmage.config import load_config_text
    from scrimmage.session import Session
    assert load_config_text("", tmp_path / "r.toml").nt is True
    off = tmp_path / "r.toml"
    off.write_text((Path(__file__).parent / "fake_robot.toml").read_text().replace('cwd = "."', f'cwd = "{Path(__file__).parent}"')
                   + "\n[telemetry]\nnt = false\n")
    assert load_config(off).nt is False
    assert Session(off).nt is False
    with pytest.raises(ConfigError, match="nt must be true or false"):
        load_config_text("[telemetry]\nnt = 0\n", tmp_path / "r.toml")
