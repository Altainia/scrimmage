"""The Test tab's machine text never changes with the Units setting (spec Goal 5): MCP test_run's outcome, check
lines, air and clearance lines, a setup refusal, and a sweep's and a spread's table.csv are what they were before the
setting existed, byte for byte, under Imperial and under Metric.

MAIN holds the text main (before the setting) wrote for each case below, captured by running main's code on the same
files (sub-project 20a, Task 5; the two spread refusals in its fix round). It is pinned here so the check needs no
copy of main. The cases are the example block tests, changed so each reaches a line that names a unit: meters (a
drive, a move or joint check in "m", the partner's overlap), inches with their trailing zeros ("0.0 in", "11.0 in",
"120.0 psi") and psi."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "blocks"

MOVE = '[test]\nrobot = "trench-standin.toml"\n\n[[block]]\ndo = "move"\njoint = "elevator.stage1"\n{}\ntimeout = 0.1\n'

# name: (example folder, test file, how to change its text: (old, new) pairs, or the whole new text)
CASES = {
    "pressure": ("pneumatic", "cycles.test.toml", [("above = 60.0", "above = 125.0")]),
    "cylinder": ("pneumatic", "cycles.test.toml",
                 '[test]\nrobot = "arm-standin.toml"\n\n[[block]]\ndo = "expect"\n'
                 'that = { cylinder = { cylinder = "lift", is = "extended" } }\n'),
    "partner moved": ("partner", "push.test.toml", [("at_least_in = 60.0", "at_least_in = 200.0")]),
    "partner lifted": ("partner", "push.test.toml",
                       [("partner_moved = { at_least_in = 60.0 }", "partner_lifted = { at_least_in = 5.0 }")]),
    "partner overlap": ("partner", "push.test.toml", [("ahead_in = 50.0", "ahead_in = 5.0")]),
    "seated": ("reef", "place.test.toml", [("at_least_in = 3.0", "at_least_in = 20.0")]),
    "clears": ("trench", "raised.test.toml", []),
    "drive": ("trench", "clear.test.toml", [("distance = 4.0", "distance = 4.0\ntimeout = 0.5")]),
    "move in": ("trench", "clear.test.toml", MOVE.format('to = 30.0\nunit = "in"')),
    "move m": ("trench", "clear.test.toml", MOVE.format('to = 0.5\nunit = "m"')),
    "joint m": ("trench", "clear.test.toml",
                '[test]\nrobot = "trench-standin.toml"\n\n[[block]]\ndo = "expect"\n'
                'that = { joint = { name = "elevator.stage1", above = 0.5, unit = "m" } }\n'),
}

# the same, run through MCP test_spread: its refusals, made before any run, are machine text too
SPREADS = {
    "spread inside the robot": ("intake", "spread.test.toml",
                                [("floor_spread = { offset_in = [-6.0, 0.0, 6.0] }",
                                  "floor_spread = { distance_in = [1.0] }")]),
    "spread partner overlap": ("intake", "spread.test.toml",
                               [("floor_spread = { offset_in = [-6.0, 0.0, 6.0] }",
                                 "floor_spread = { offset_in = [0.0] }\npartner = { ahead_in = 5.0 }")]),
}


def case_file(tmp: Path, name: str) -> Path:
    """The case's example folder copied into `tmp`, with its test file changed; the test file's path."""
    folder, test, change = {**CASES, **SPREADS}[name]
    work = tmp / name.replace(" ", "_")
    shutil.copytree(EXAMPLES / folder, work, ignore=shutil.ignore_patterns("*-runs"))
    path = work / test
    text = path.read_text(encoding="utf-8")
    if isinstance(change, str):
        text = change
    for old, new in change if isinstance(change, list) else []:
        assert old in text, (name, old)
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    return path


def machine_text(tmp: Path) -> dict:
    """Each case's MCP test_run words (or its refusal), and a sweep's and a spread's table.csv."""
    from scrimmage import mcp_server
    out = {}
    for name in CASES:
        try:
            info = asyncio.run(mcp_server.test_run(str(case_file(tmp, name))))
        except mcp_server.ToolError as e:
            out[name] = {"error": str(e)}
            continue
        out[name] = {"outcome": info["outcome"], "checks": [c["line"] for c in info["checks"]],
                     "air_lines": info["air_lines"], "clearance_lines": info["clearance_lines"]}
    sweep = asyncio.run(mcp_server.test_sweep(str(case_file(tmp / "s", "drive")), {"block": [1], "key": "distance"},
                                              [1.0, 4.0]))
    out["sweep"] = (Path(sweep["sweep_folder"]) / "table.csv").read_text(encoding="utf-8")
    shutil.copytree(EXAMPLES / "intake", tmp / "intake", ignore=shutil.ignore_patterns("*-runs"))
    spread = asyncio.run(mcp_server.test_spread(str(tmp / "intake" / "spread.test.toml")))
    out["spread"] = [spread["sentence"], (Path(spread["spread_folder"]) / "table.csv").read_text(encoding="utf-8")]
    for name in SPREADS:
        try:
            spread = asyncio.run(mcp_server.test_spread(str(case_file(tmp, name))))
            out[name] = [spread["sentence"], (Path(spread["spread_folder"]) / "table.csv").read_text(encoding="utf-8")]
        except mcp_server.ToolError as e:
            out[name] = {"error": str(e)}
    return out


MAIN: dict = {
    "pressure":
        {"air_lines": ["air: 120 psi at the start, 120 psi at the end, lowest 120 psi at 0.0 s; the compressor ran 0.0 "
                       "s"],
         "checks": ["failed (block 1): expected the stored air pressure above 125 psi, but it was 120.0 psi at 0.3 s"],
         "clearance_lines": [],
         "outcome": "a check failed (block 1): expected the stored air pressure above 125 psi, but it was 120.0 psi at "
                    "0.3 s"},
    "cylinder":
        {"air_lines": ["air: 120 psi at the start, 120 psi at the end, lowest 120 psi at 0.0 s; the compressor ran 0.0 "
                       "s"],
         "checks": ["failed (block 1): expected lift extended, but its rod was out 0.0 in of 8 in at 0.3 s"],
         "clearance_lines": [],
         "outcome": "a check failed (block 1): expected lift extended, but its rod was out 0.0 in of 8 in at 0.3 s"},
    "partner moved":
        {"air_lines": [],
         "checks": ["failed (block 2): expected the partner to move at least 200 in, but it moved 63.4 in at 2.3 s"],
         "clearance_lines": [],
         "outcome": "a check failed (block 2): expected the partner to move at least 200 in, but it moved 63.4 in at "
                    "2.3 s"},
    "partner lifted":
        {"air_lines": [],
         "checks": ["failed (block 2): expected the partner lifted at least 5 in, but it was 0 in up, 0.2° from level "
                    "at 2.3 s"],
         "clearance_lines": [],
         "outcome": "a check failed (block 2): expected the partner lifted at least 5 in, but it was 0 in up, 0.2° "
                    "from level at 2.3 s"},
    "partner overlap":
        {"error": "the partner robot (Setup) starts inside the robot (their outlines overlap by 0.75 m)"},
    "seated":
        {"air_lines": [],
         "checks": ["passed: a coral is held by grabber from 1.4 s until it was set down on branch_l2 at 4.4 s (block "
                    "6)",
                    "failed (block 13): the coral is seated 9.2 in of 11.0 in on branch_l2, less than 20 in at 7.4 s"],
         "clearance_lines": [],
         "outcome": "a check failed (block 13): the coral is seated 9.2 in of 11.0 in on branch_l2, less than 20 in at "
                    "7.4 s; 1 placed on branch_l2"},
    "clears":
        {"air_lines": [],
         "checks": ["failed (block 2): expected the robot to clear trench_arm by at least 0.5 in, but it was 0.45 in "
                    "at 1.7 s"],
         "clearance_lines": ["closest to trench_arm: 0.45 in at 1.7 s"],
         "outcome": "a check failed (block 2): expected the robot to clear trench_arm by at least 0.5 in, but it was "
                    "0.45 in at 1.7 s"},
    "drive":
        {"air_lines": [],
         "checks": ["passed: the robot clears trench_arm by at least 0.5 in from 0.3 s on (block 1)"],
         "clearance_lines": ["trench_arm: never within 20 in"],
         "outcome": "drive (block 2) didn't reach 4 m in 0.5 s (went 0.43 m)"},
    "move in":
        {"air_lines": [],
         "checks": [],
         "clearance_lines": [],
         "outcome": "elevator.stage1 stopped at 1.9 in short of 30 in (block 1)"},
    "move m":
        {"air_lines": [],
         "checks": [],
         "clearance_lines": [],
         "outcome": "elevator.stage1 stopped at 0 m short of 0.5 m (block 1)"},
    "joint m":
        {"air_lines": [],
         "checks": ["failed (block 1): expected elevator.stage1 above 0.5 m, but it was -0 m at 0.3 s"],
         "clearance_lines": [],
         "outcome": "a check failed (block 1): expected elevator.stage1 above 0.5 m, but it was -0 m at 0.3 s"},
    "sweep":
        ("value,status,outcome,t_end,block 1: the robot clears trench_arm by at least 0.5 in\n"
         "1,timed out,drive (block 2) didn't reach 1 m in 0.5 s (went 0.43 m),0.800,passed\n"
         "4,timed out,drive (block 2) didn't reach 4 m in 0.5 s (went 0.43 m),0.800,passed\n"),
    "spread":
        ["picked up 1 of 3; missed at 6 in right and 6 in left",
         "placement,offset_in,distance_in,angle_deg,rotation_deg,tip_deg,status,picked up,outcome\n"
         "6 in right,-6,59.06,0,0,0,timed out,no,waiting for a ball to reach intake.hopper timed out at 7.3 s (block "
         "3); 0 of 1 made it in (0%); 1 stuck in the robot\n"
         "on the center line,0,59.06,0,0,0,finished,yes,1 of 1 made it in (100%)\n"
         "6 in left,6,59.06,0,0,0,timed out,no,waiting for a ball to reach intake.hopper timed out at 7.3 s (block 3); "
         "0 of 1 made it in (0%); 1 stuck in the robot\n"],
    "spread inside the robot":
        {"error": "the floor spread puts the ball inside the robot at 1 in ahead: start the distance at 36 in or more"},
    "spread partner overlap":
        {"error": "the partner robot (Setup) starts inside the robot (their outlines overlap by 0.75 m)"},
}


@pytest.mark.parametrize("system", ["imperial", "metric"])
def test_the_test_tabs_machine_text_is_mains_whatever_the_setting(tmp_path, units, system):
    pytest.importorskip("mcp")
    units(system)
    got = machine_text(tmp_path)
    for name in MAIN:
        assert got[name] == MAIN[name], name
    assert got.keys() == MAIN.keys()
