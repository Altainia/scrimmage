"""docs/match.md: every [match] key and game_data is in its tables with the code's defaults, the set_match tool and its
parameters are documented, the error messages are the code's, and the robot code uses the calls the live probes make.
The README, the Drive tab doc and the scenario editor doc point at it."""

from __future__ import annotations

import dataclasses
import inspect
import re
import tomllib
from pathlib import Path

import pytest

from scrimmage import mcp_server
from scrimmage.driverstation import MATCH_KEYS, STATIONS, MatchSettings, check_game_data

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "match.md"


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def table_rows(doc: str) -> dict[str, list[str]]:
    """The key tables' rows by their first column's key: | `station` | `"blue1"` | ..."""
    out = {}
    for line in doc.splitlines():
        if m := re.match(r"\| `(\w+)` \|", line):
            out[m.group(1)] = [c.strip() for c in line.split("|")[1:-1]]
    return out


def test_every_key_is_in_a_table_with_its_default():
    rows = table_rows(text())
    defaults = dataclasses.asdict(MatchSettings())
    for key in MATCH_KEYS:
        assert key in rows, f"docs/match.md's tables leave out the [match] key {key!r}"
        want = defaults[key]
        shown = rows[key][1]
        if isinstance(want, bool):
            assert shown == f"`{str(want).lower()}`", (key, shown)
        elif isinstance(want, str):
            assert shown == f'`"{want}"`', (key, shown)
        else:
            assert shown == f"`{want:g}`", (key, shown)
    assert "game_data" in rows
    assert ", ".join(f"`{s}`" for s in STATIONS[:-1]) + f" or `{STATIONS[-1]}`" in text()


def test_the_example_parses():
    blocks = re.findall(r"```toml\n(.*?)```", text(), re.S)
    assert blocks
    for b in blocks:
        raw = tomllib.loads(b)
        MatchSettings().merged(raw["match"], "[match]")
        for step in raw["step"]:
            if "game_data" in step:
                check_game_data(step["game_data"], "step")


def test_set_match_is_documented_with_its_parameters():
    doc = text()
    params = list(inspect.signature(mcp_server.set_match).parameters)
    assert params == ["station", "fms", "timer", "game_data", "auto_seconds", "teleop_seconds"]
    row = next(line for line in doc.splitlines() if line.startswith("| `set_match` |"))
    for p in params:
        assert f"`{p}`" in row, p
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_row = next(line for line in readme.splitlines() if line.startswith("| `set_match` |"))
    for p in params:
        assert f"`{p}`" in readme_row, p
    assert "docs/match.md" in readme_row


def test_the_readme_lists_every_scenario_key():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    block = readme.split("## Scenario files", 1)[1].split("```toml\n", 1)[1].split("```", 1)[0]
    raw = tomllib.loads(block)
    assert set(raw["match"]) == set(MATCH_KEYS)
    assert "game_data" in raw["step"][0]


@pytest.mark.parametrize("table", [
    {"station": "red4"}, {"alliance": "red"}, {"timer": "yes"}, {"auto_seconds": 0},
])
def test_the_error_messages_are_the_codes(table):
    with pytest.raises(ValueError) as e:
        MatchSettings().merged(table, "[match]")
    msg = str(e.value)
    doc = " ".join(text().split())   # the doc wraps long messages
    if "station" in table or "alliance" in table:
        assert msg in doc, msg                                    # in full
    else:
        assert msg.removeprefix("[match] ").split(", not")[0] in doc, msg   # without the value


def test_the_game_data_message_is_the_codes():
    with pytest.raises(ValueError) as e:
        check_game_data("x" * 65, "step 2 (shift):")
    assert str(e.value) in " ".join(text().split())


def test_the_robot_code_matches_the_live_probes():
    doc = text()
    py = (ROOT / "tests" / "match_probe_py" / "robot.py").read_text(encoding="utf-8")
    for call in ("getGameSpecificMessage()", "getAlliance()", "getLocation()", "isFMSAttached()", "getMatchTime()"):
        assert call in py and doc.count(f"DriverStation.{call}") >= 2, call   # once in Java, once in Python
    java = (ROOT / "tests" / "java_probe" / "src" / "main" / "java" / "frc" / "robot" / "Robot.java").read_text()
    for call in ("DriverStation.getGameSpecificMessage()", "DriverStation.getAlliance()"):
        assert call in java and call in doc, call


def test_the_other_docs_point_here():
    for name in ("drive.md", "scenario-editor.md"):
        assert "match.md" in (ROOT / "docs" / name).read_text(encoding="utf-8"), name
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/match.md" in readme and "game_data" in readme
    drive = (ROOT / "docs" / "drive.md").read_text(encoding="utf-8")
    for words in ("**Enter**", "aren't saved", "auto_seconds"):
        assert words in drive, words
    doc = text()
    assert "`scrimmage serve` ignores `[match]`" in doc
    flat = " ".join(doc.split())
    for words in ("press **Enter** or leave the box", "**aren't saved**", "no fields for the Auto and Teleop lengths"):
        assert words in flat, words
