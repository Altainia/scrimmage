"""docs/driver-station.md: every switch that turns the FRC Driver Station app on (the Drive tab's choice, serve's
--real-ds, MCP's real_ds, the config's [driver_station] real) and every port is documented, the refusal, waiting and
note texts are the code's, and the README, the Drive tab doc and the match doc point at it."""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from scrimmage import mcp_server
from scrimmage.assembler.live import REAL_DS_NO_PAUSE, REAL_DS_NO_RECORD
from scrimmage.config import load_config_text
from scrimmage.driverstation import REAL_DS_DRIVING, REAL_DS_WAITING, real_ds_line
from scrimmage.scenario_runner import REAL_DS_REFUSED
from scrimmage.session import DS_TCP_PORT, DS_UDP_PORT, NOT_LISTENING, REAL_DS_INPUTS, REAL_DS_UNPAUSABLE

from fake_ds import TCP_ROBOT, UDP_DS, UDP_ROBOT

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "driver-station.md"
STATIC = ROOT / "scrimmage" / "assembler" / "static"


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def flat(s: str) -> str:
    """The doc with its line wrapping undone (long messages wrap)."""
    return " ".join(s.split())


def test_the_drive_tabs_choice_is_the_pages():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    choice = html.split('<select id="drive-ds-source">', 1)[1].split("</select>", 1)[0]
    labels = re.findall(r"<option[^>]*>([^<]+)</option>", choice)
    assert labels == ["Scrimmage (this page)", "The FRC Driver Station app"]
    doc = text()
    for label in labels:
        assert f"**{label}**" in doc, label
    assert "**Driver Station**" in doc and "**Start and check devices**" in doc


def test_serves_switch_is_documented():
    out = subprocess.run([sys.executable, "-m", "scrimmage", "serve", "--help"], capture_output=True, text=True,
                         check=True).stdout
    assert "--real-ds" in out and "--no-real-ds" in out
    doc = text()
    for words in ("--real-ds --launch", "`--no-real-ds`", "`scrimmage serve --real-ds` without `--launch`",
                  "scrimmage run --real-ds"):
        assert words in flat(doc), words


def test_the_mcp_switch_is_documented():
    for tool in (mcp_server.start_session, mcp_server.run_scenario):
        assert inspect.signature(tool).parameters["real_ds"].default is None, tool
    doc = text()
    for words in ("start_session(config=..., real_ds=true)", "`real_ds=false`", "run_scenario(real_ds=true)",
                  "start_session(attach=true)"):
        assert words in doc, words


def test_the_config_switch_is_documented_and_parses(tmp_path):
    doc = text()
    blocks = [b for b in re.findall(r"```toml\n(.*?)```", doc, re.S) if "[driver_station]" in b]
    assert blocks
    for b in blocks:
        assert tomllib.loads(b)["driver_station"]["real"] is True
        assert load_config_text(b, tmp_path / "a.toml").real_ds is True
    assert "`[driver_station] real = true`" in doc


def test_every_port_is_documented():
    assert (DS_UDP_PORT, DS_TCP_PORT) == (UDP_ROBOT, TCP_ROBOT)
    doc = text()
    table = {m[0]: m[1] for m in re.findall(r"^\| ((?:UDP|TCP) \d+) \| ([^|]+?) \|", doc, re.M)}
    assert table == {f"UDP {UDP_ROBOT}": "Driver Station to robot", f"UDP {UDP_DS}": "robot to Driver Station",
                     f"TCP {TCP_ROBOT}": "Driver Station to robot (the app connects)"}   # the firewall table
    rule = flat(doc).split("The simulation computer must accept ", 1)[1].split(".", 1)[0]
    assert rule == f"UDP {UDP_ROBOT} and TCP {TCP_ROBOT}, and the Driver Station laptop must accept UDP {UDP_DS}"
    assert f"sudo ufw allow {UDP_ROBOT}/udp" in doc and f"sudo ufw allow {TCP_ROBOT}/tcp" in doc


def test_the_refusal_and_waiting_texts_are_the_codes():
    doc = flat(text())
    for msg in (REAL_DS_DRIVING, REAL_DS_WAITING, REAL_DS_INPUTS, REAL_DS_NO_PAUSE, REAL_DS_NO_RECORD,
                REAL_DS_UNPAUSABLE):
        assert msg in doc, msg
    # the not-listening note and the port refusal, up to their first colon
    assert NOT_LISTENING.split(":")[0].lower() in doc.lower().replace("**", "")
    assert "UDP port 1110 is already in use" in doc
    # the scenario refusal's reason
    assert "the scenario is the Driver Station" in REAL_DS_REFUSED and "a scenario is the Driver Station" in doc


def test_the_shown_texts_are_the_codes():
    doc = text()
    for mode, estop in (("teleop", False), ("disabled", False), ("disabled", True)):
        line = real_ds_line({"connected": True, "mode": mode, "estop": estop})
        assert f"`{line}`" in doc, line
    assert real_ds_line({"connected": True, "mode": "auto", "estop": False}) == "Driver Station: auto enabled"
    assert "(or `auto enabled`, `test enabled`)" in flat(doc)
    assert real_ds_line({"connected": False}) == REAL_DS_WAITING
    js = (STATIC / "drive.js").read_text(encoding="utf-8")
    chips = re.search(r"REAL_DS_MODE_TEXT = \{(.*?)\}", js).group(1)
    for chip in re.findall(r'"([^"]+)"', chips) + ["Waiting for the Driver Station", "Disabled (E-stopped)"]:
        assert f'"{chip}"' in doc, chip
    assert "Enter disables" in doc and "Space bar is the E-stop" in doc
    assert "There, Enter disables and Space is the E-stop." in js


def test_the_timeouts_are_the_configs(tmp_path):
    py = load_config_text('[robot_program]\ncommand = ["python"]\n', tmp_path / "a.toml")
    java = load_config_text('[robot_program]\ncommand = ["./gradlew", "simulateJava"]\n', tmp_path / "a.toml")
    doc = flat(text())
    assert f"{py.robot_program.start_timeout:g} s, or {java.robot_program.start_timeout:g} s for a Gradle project" in doc


def test_the_java_switch_is_the_init_scripts():
    gradle = (ROOT / "scrimmage" / "java" / "scrimmage.init.gradle").read_text(encoding="utf-8")
    assert "SCRIMMAGE_DS_SOCKET" in gradle and "addDriverstation" in gradle
    doc = text()
    assert "SCRIMMAGE_DS_SOCKET=1 ./gradlew -I" in doc and "wpi.sim.addDriverstation()" in doc
    assert "--ds-socket" in doc and "Use Real DriverStation" in doc


def test_the_other_docs_point_here():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    row = next(line for line in readme.splitlines() if line.startswith("| `start_session` |"))
    assert "`real_ds`" in row and "docs/driver-station.md" in row and "[driver_station] real" in row
    block = readme.split("```toml\n[robot_program]", 1)[1].split("```", 1)[0]
    assert tomllib.loads("[robot_program]" + block)["driver_station"] == {"real": False}
    assert "--real-ds" in readme and "docs/driver-station.md  " in readme
    for name in ("drive.md", "match.md"):
        assert "driver-station.md" in (ROOT / "docs" / name).read_text(encoding="utf-8"), name
    assert "ignores `[match]` too" in flat((ROOT / "docs" / "match.md").read_text(encoding="utf-8"))
