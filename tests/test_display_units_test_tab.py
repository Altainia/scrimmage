"""The Test tab's server-built words in the Units setting (spec U2/U4): failure text, check sentences, run lines and
floor-spread labels, each in both systems. The run's numbers (signals, the test file) don't change with it."""
from types import SimpleNamespace

from scrimmage.blocks.conditions import _amount, describe
from scrimmage.blocks.placed import seated_words
from scrimmage.blocks.program import Cond
from scrimmage.blocks.run import air_lines, clearance_lines
from scrimmage.blocks.spread import label

IN = 0.0254


def test_a_seated_depth_in_both_systems(units):
    assert seated_words(1.5 * IN, 2 * IN) == "seated 1.5 in of 2 in"
    assert seated_words(0.5 * IN, 2 * IN) == "only 0.5 in on: could be knocked off"
    units("metric")
    assert seated_words(1.5 * IN, 2 * IN) == "seated 38 mm of 51 mm"
    assert seated_words(0.5 * IN, 2 * IN) == "only 13 mm on: could be knocked off"


def test_a_pressure_check_in_both_systems(units):
    cond = Cond("pressure", {"side": "stored", "above": 60})
    assert describe(cond) == "the stored air pressure is above 60 psi"
    units("metric")
    assert describe(cond) == "the stored air pressure is above 413.7 kPa"


def test_file_units_in_words(units):
    # a block's "deg", "in" and "m", and psi: degrees read the same in both systems; a joint's "m" is a robot length
    assert [_amount(90, "deg"), _amount(3, "in"), _amount(0.1016, "m"), _amount(60, "psi")] == \
        ["90°", "3 in", "4 in", "60 psi"]
    units("metric")
    assert [_amount(90, "deg"), _amount(3, "in"), _amount(0.1016, "m"), _amount(60, "psi")] == \
        ["90°", "76.2 mm", "101.6 mm", "413.7 kPa"]


def test_check_sentences_in_both_systems(units):
    conds = [Cond("clears", {"element": "trench", "at_least_in": 2}), Cond("partner_moved", {"at_least_in": 6}),
             Cond("seated", {"piece": "coral_1", "on": "peg", "at_least_in": 3}),
             Cond("joint", {"name": "lift", "above": 0.5, "unit": "m"})]
    assert [describe(c) for c in conds] == [
        "the robot clears trench by at least 2 in", "the partner moved at least 6 in",
        "coral_1 is seated at least 3 in on peg", "lift passes 19.685 in"]
    units("metric")
    assert [describe(c) for c in conds] == [
        "the robot clears trench by at least 50.8 mm", "the partner moved at least 152.4 mm",
        "coral_1 is seated at least 76.2 mm on peg", "lift passes 500 mm"]


def test_run_lines_in_both_systems(units):
    result = SimpleNamespace(clearance={"trench": {"min_in": 0.82, "t": 3.1}, "gate": {"min_in": None, "t": None}},
                             air={"start_psi": 120.0, "end_psi": 84.2, "low_psi": 81.0, "low_t": 2.0,
                                  "compressor_s": 1.5, "below_working_t": None, "working_psi": 60.0})
    assert clearance_lines(result) == ["closest to trench: 0.82 in at 3.1 s", "gate: never within 20 in"]
    assert air_lines(result) == ["air: 120 psi at the start, 84 psi at the end, lowest 81 psi at 2.0 s; "
                                 "the compressor ran 1.5 s"]
    units("metric")
    assert clearance_lines(result) == ["closest to trench: 21 mm at 3.1 s", "gate: never within 508 mm"]
    assert air_lines(result) == ["air: 827 kPa at the start, 581 kPa at the end, lowest 558 kPa at 2.0 s; "
                                 "the compressor ran 1.5 s"]


def test_a_placement_label_in_both_systems(units):
    p = {"offset_in": 6, "distance_in": 12, "angle_deg": 30, "rotation_deg": 0, "tip_deg": 0}
    axes = ["offset_in", "distance_in", "angle_deg"]
    assert label(p, axes) == "6 in left 12 in ahead at 30°"
    units("metric")
    assert label(p, axes) == "152 mm left 305 mm ahead at 30°"


# ---- machine text: a sweep's or spread's table.csv and MCP's test_run are in one fixed system (Imperial) ------------
DRIVE = '[[block]]\ndo = "drive"\ndistance = 5.0\ntimeout = 0.3\n'


def _sweep_rows(tmp_path, name):
    import csv
    from scrimmage.blocks.program import loads
    from scrimmage.blocks.sweep import run_sweep
    from test_blocks_run import write_test
    folder = tmp_path / name
    folder.mkdir()
    path = write_test(folder, DRIVE)
    out = run_sweep(loads(path.read_text(encoding="utf-8"), path), {"block": [0], "key": "distance"}, [5.0],
                    folder / "runs")
    with open(out / "table.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_a_sweeps_table_is_written_as_it_always_was_whatever_the_setting(tmp_path, units):
    imperial = _sweep_rows(tmp_path, "a")      # a drive's words were meters before the setting, and still are
    assert imperial[0]["outcome"].startswith("drive (block 1) didn't reach 5 m in 0.3 s (went "), imperial
    units("metric")
    assert _sweep_rows(tmp_path, "b") == imperial


def test_a_spreads_table_and_sentence_are_written_in_imperial(tmp_path, units):
    import csv
    import json
    from scrimmage.blocks.program import loads
    from scrimmage.blocks.spread import run_spread
    from test_blocks_spread import spread_test
    units("metric")
    path = spread_test(tmp_path, "offset_in = [0, 30]")
    out = run_spread(loads(path.read_text(encoding="utf-8"), path), path.parent / "runs")
    assert json.loads((out / "spread.json").read_text(encoding="utf-8"))["sentence"] == \
        "picked up 1 of 2; missed at 30 in left"
    with open(out / "table.csv", newline="", encoding="utf-8") as f:
        assert [r["placement"] for r in csv.DictReader(f)] == ["on the center line", "30 in left"]


def test_mcp_test_run_answers_as_it_always_did_whatever_the_setting(tmp_path, units):
    import asyncio
    import pytest
    pytest.importorskip("mcp")
    from scrimmage import mcp_server
    from test_blocks_run import write_test
    for system, name in (("imperial", "a"), ("metric", "b")):
        units(system)
        (tmp_path / name).mkdir()
        info = asyncio.run(mcp_server.test_run(str(write_test(tmp_path / name, DRIVE))))
        assert info["outcome"].startswith("drive (block 1) didn't reach 5 m in 0.3 s (went "), (system, info["outcome"])
