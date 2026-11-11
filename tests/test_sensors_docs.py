"""docs/sensors.md: every [[sensor]] key is in its tables, its TOML examples parse, its complete example builds a World
with the sensors, and its numbers and names match the code. The Test tab's and the README's sensor rows are there."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from scrimmage.sensors.colors import NAMED_COLORS
from scrimmage.sensors.runtime import BRIGHTNESS, DARK, FULL_SCALE, GRAY_REFLECTANCE
from scrimmage.sensors.spec import COLOR_DEVICE, DEFAULT_MAX_RANGE, DEFAULT_VOLTS_PER_M, KEYS, parse_sensors
from scrimmage.world import World

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "sensors.md"
FENCE = re.compile(r"(<!-- complete -->\n)?```toml\n(.*?)```", re.S)


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def blocks() -> list[tuple[bool, str]]:
    """(marked complete, text) of every toml block in the doc."""
    return [(m.group(1) is not None, m.group(2)) for m in FENCE.finditer(text())]


def table_keys(doc: str) -> set[str]:
    """The keys in the first column of the doc's key tables: | `from` (or `from_in`) | ..."""
    out = set()
    for line in doc.splitlines():
        if line.startswith("| `"):
            out.update(re.findall(r"`(\w+)`", line.split("|")[1]))
    return out


def test_every_key_is_in_a_table():
    have = table_keys(text())
    for kind, keys in KEYS.items():
        missing = [k for k in keys if k not in have]
        assert not missing, f"docs/sensors.md's tables leave out the {kind} keys {missing}"


def test_the_examples_parse_and_the_complete_one_builds_a_world():
    toml = blocks()
    sensors = [t for _, t in toml if "[[sensor]]" in t]
    assert sensors
    for t in sensors:
        parse_sensors(tomllib.loads(t)["sensor"])
    complete = [t for done, t in toml if done]
    assert len(complete) == 1, "mark exactly one example <!-- complete -->"
    w = World(tomllib.loads(complete[0]), ROOT)
    assert w.sensors is not None and w.sensors.names == ["bin", "range", "eye"]


def test_the_numbers_and_names_match_the_code():
    doc = text()
    assert f"{DEFAULT_VOLTS_PER_M:g}" in doc and f"{DEFAULT_MAX_RANGE['distance']:.1f} m" in doc
    assert f"{DEFAULT_MAX_RANGE['color']:.2f} m" in doc
    assert str(FULL_SCALE) in doc and f"{GRAY_REFLECTANCE:g}" in doc
    assert f"red {DARK['Red']}, green {DARK['Green']}, blue {DARK['Blue']}, IR {DARK['IR']}" in doc
    assert BRIGHTNESS == ((0.0, 1.0), (0.02, 1.0), (0.06, 0.5), (0.10, 0.0))      # the doc's "brightness" row
    assert "full up to 2 cm, half at 6 cm, nothing at 10 cm" in doc
    assert COLOR_DEVICE.format(port=1) in doc and "[0,82]" in doc
    for name, rgb in NAMED_COLORS.items():
        assert f"| `{name}` | {', '.join(f'{c:g}' if c % 1 else f'{c:.1f}' for c in rgb)} |" in doc, name


def test_the_robot_code_matches_the_e2e_probe():
    """The Python snippet uses the calls tests/sensors_probe_py makes (checked live by test_sensors_robot_e2e)."""
    doc = text()
    probe = (ROOT / "tests" / "sensors_probe_py" / "robot.py").read_text(encoding="utf-8")
    for call in ("wpilib.DigitalInput(", "wpilib.AnalogInput(", "rev.ColorSensorV3(wpilib.I2C.Port.kMXP)",
                 ".getColor()", ".getProximity()", ".getVoltage()"):
        assert call in probe and call in doc, call


def test_the_test_tab_and_the_readme_name_the_sensors():
    tab = (ROOT / "docs" / "test-tab.md").read_text(encoding="utf-8")
    assert "## Sensors" in tab and "[Sensors](#sensors)" in tab and "examples/blocks/beam" in tab
    assert "`{ sensor = { sensor = \"bin\", is = \"on\" } }`" in tab
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '`[[sensor]] kind = "beam"`' in readme and "docs/sensors.md" in readme
    assert "sensors.md" in (ROOT / "docs" / "parts.md").read_text(encoding="utf-8")


def test_vision_in_robot_code_names_the_topic_and_says_it_isnt_run_here():
    """Spec D7: the section feeds PhotonLib's VisionSystemSim the true pose's Pose2d topic (tests/test_nt_and_recorder.py
    pins its name and type), and says the snippet isn't checked here."""
    doc = text()
    section = doc.split("## Vision in robot code", 1)[1].split("\n## ", 1)[0]
    for name in ("/MuJoCo/GroundTruthPose", "Pose2d", "VisionSystemSim", "```java", "```python"):
        assert name in section, name
    assert "isn't run in this repo's tests" in section
    assert "[Vision in robot code](#vision-in-robot-code)" in doc


def test_the_camera_keys_and_estimates_are_in_the_doc():
    from scrimmage.sensors.spec import DEFAULT_FPS, DEFAULT_HFOV_DEG, DEFAULT_MAX_ANGLE_DEG, DEFAULT_VFOV_DEG
    have = table_keys(text())
    assert set(KEYS["camera"]) <= have
    doc = text()
    for key, value in (("hfov_deg", DEFAULT_HFOV_DEG), ("vfov_deg", DEFAULT_VFOV_DEG),
                       ("max_angle_deg", DEFAULT_MAX_ANGLE_DEG), ("fps", DEFAULT_FPS)):
        assert f"| `{key}` | {value} |" in doc, key
    assert f"| {DEFAULT_MAX_RANGE['camera']:.1f} m |" in doc
    assert "Cameras and vision" not in doc and "/MuJoCo/Camera/<name>/TagIds" in doc
