"""Cylinders driven by a real RobotPy program under scrimmage: tests/pneumatics_probe_py (a single solenoid on a CTRE
PCM, a double solenoid on a REV Pneumatic Hub, a compressor on the PCM), through scrimmage's scenario runner.

halsim_ws_client relays a CTRE PCM and its solenoids itself. It relays nothing for a REV Pneumatic Hub, so the lockstep
extension (scrimmage-lockstep 0.7.0 or later) mirrors the hub into a "ScrimmagePH:<module>" SimDevice.

One robot's valves are all on one kind of module, so each test binds its own config's cylinder to one of the two."""

import asyncio
import re
from importlib import metadata
from pathlib import Path

import pytest

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")
pytest.importorskip("scrimmage_lockstep")

from scrimmage.scenario import load_scenario_text  # noqa: E402
from scrimmage.scenario_runner import run_scenario  # noqa: E402

TESTS = Path(__file__).resolve().parent
PROBE = TESTS / "pneumatics_probe_py"
PH_LOCKSTEP = (0, 7, 0)     # the first scrimmage-lockstep that relays the REV Pneumatic Hub
PRINT = re.compile(r"pneumatics pcm_switch=(\w+) ph_switch=(\w+) ph_on=(\w+) ph_amps=([\d.]+)")


def lockstep_version() -> tuple[int, ...]:
    return tuple(int(re.match(r"\d*", x)[0] or 0) for x in metadata.version("scrimmage-lockstep").split(".")[:3])


def run_probe(tmp: Path, valve: str, pneumatics: str = "") -> dict:
    """Run the probe with its cylinder's valve bound to `valve`: 0.5 s disabled, then 1.5 s of teleop. Returns the
    cylinder's extension (m) after each robot step, the steps' start times, the robot's print and the report."""
    cfg = tmp / "pneumatics_probe_py.toml"
    cfg.write_text(f"""
[model]
timestep = 0.001

[lockstep]
step = 0.005

[robot_program]
command = ["{{python}}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
cwd = "{PROBE}"

{pneumatics}

[[part]]
name = "drive"
stock = "chassis"

[[part]]
name = "cyl"
stock = "cylinder"
attach = "drive.top"
ids = {{ valve = "{valve}" }}
""")
    steps = ('[[step]]\nname = "disabled"\nmode = "disabled"\nduration = 0.5\n'
             '[[step]]\nname = "teleop"\nmode = "teleop"\nduration = 1.5\n')
    scenario = load_scenario_text(f'config = "{cfg}"\n' + steps, tmp / "probe_scenario.toml")
    rows: list[tuple[float, float]] = []
    out = tmp / "out"
    report = asyncio.run(run_scenario(scenario, out, watch=lambda s, t: rows.append(
        (t, s.world.pneumatics.extension("cyl")))))
    console = (out / "robot.log").read_text()
    m = PRINT.search(console)
    return {"rows": rows, "starts": {st.name: st.start_t for st in report.steps}, "report": report,
            "console": console, "print": m.groups() if m else None}


def check_extends(run: dict) -> None:
    rep = run["report"]
    assert rep.error is None and rep.passed, rep.to_dict()
    t_teleop = run["starts"]["teleop"]
    disabled = [x for t, x in run["rows"] if t <= t_teleop]
    assert disabled and max(disabled) < 0.001          # a disabled robot's valves are off
    assert run["rows"][-1][1] >= 0.15, run["rows"][::100]
    assert run["print"] is not None, run["console"][-2000:]


def test_pcm_solenoid_extends_a_cylinder(tmp_path):
    run = run_probe(tmp_path, "PCM:3")
    check_extends(run)
    assert run["print"][0] == "False"                  # 120 psi at the start: full, not calling for air


@pytest.mark.skipif(lockstep_version() < PH_LOCKSTEP,
                    reason="the installed scrimmage-lockstep is older than 0.7.0, which relays the REV Pneumatic Hub: "
                           "`pip install ./lockstep`")
def test_ph_double_solenoid_extends_a_cylinder(tmp_path):
    # 80 psi stored is below the switch's 95 psi: it calls for air, and the hub's closed loop runs the compressor
    run = run_probe(tmp_path, "PH:4,5", "[pneumatics]\nstart_psi = 80")
    check_extends(run)
    _, ph_switch, ph_on, ph_amps = run["print"]
    assert ph_switch == "True" and ph_on == "True" and float(ph_amps) > 1.0, run["print"]
    assert not any("REV Pneumatic Hub" in n for n in run["report"].notes)
