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
