"""A parts robot (chassis + differential + an intake with no device ID) driven by tests/fake_robot_main.py."""
from pathlib import Path

TESTS = Path(__file__).resolve().parent
WARNING = "intake.roller has no device ID, so it won't move"


def parts_config(tmp_path) -> Path:
    p = tmp_path / "parts_robot.toml"
    p.write_text(f'''[model]
timestep = 0.001
[robot]
start_pose = [2.0, 4.035, 0.0]
[lockstep]
step = 0.005
[robot_program]
command = ["{{python}}", "fake_robot_main.py", "--lockstep"]
cwd = "{TESTS}"
start_timeout = 15

[[part]]
name = "drive"
stock = "chassis"
ids = {{ gyro = "Pigeon2:13" }}

[[part]]
name = "dt"
stock = "differential"
attach = "drive.center"
ids = {{ left = "PWM:0", right = "PWM:1", left_encoder = {{ channel_a = 0, counts_per_rev = 2048 }}, right_encoder = {{ channel_a = 2, counts_per_rev = 2048 }} }}

[[part]]
name = "intake"
path = "{TESTS / "parts" / "intake"}"
attach = "drive.top"
''')
    return p
