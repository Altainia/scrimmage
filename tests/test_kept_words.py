"""A run kept in machine text (a sweep's, a spread's or MCP test_run's) keeps its words in every Language and Units
setting, and a sweep's or spread's table.json keeps its table's (cleanup A1, A2): the Test tab shows the viewer's.
Machine text itself (table.csv, sweep.json, spread.json, result.json's other fields, MCP results) doesn't change:
tests/test_units_machine_text.py and tests/test_mcp_language.py pin it."""
import json

from scrimmage import units as units_mod
from scrimmage.blocks import store
from scrimmage.blocks.program import loads
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.blocks.spread import run_spread
from scrimmage.blocks.sweep import run_sweep
from test_blocks_run import write_test
from test_blocks_spread import read_table, spread_test

DRIVE = '[[block]]\ndo = "drive"\ndistance = 5.0\ntimeout = 0.3\n'
TILT = '[[block]]\ndo = "expect"\nthat = { tilt = { above = 45.0 } }\n'
KEYS = {"en-US|imperial", "en-US|metric", "en-GB|imperial", "en-GB|metric"}


def _program(path):
    return loads(path.read_text(encoding="utf-8"), path)


def test_a_run_in_machine_text_keeps_its_words_in_every_setting(tmp_path):
    program = _program(write_test(tmp_path, DRIVE))
    with units_mod.machine_text():
        result = TestRun(world_for(program), program).run()
    assert type(result.outcome) is str      # plain text: MCP results and result.json's fields are as they were
    assert result.outcome.startswith("drive (block 1) didn't reach 5 m in 0.3 s (went ")
    assert set(result.words) == KEYS
    assert result.words["en-US|imperial"]["outcome"].startswith("drive (block 1) didn't reach 16.4 ft in 0.3 s")
    assert result.words["en-GB|metric"]["outcome"].startswith("drive (block 1) didn't reach 5 m in 0.3 s")


def test_a_failed_checks_words_and_its_line(tmp_path):
    program = _program(write_test(tmp_path, TILT))
    with units_mod.machine_text():
        result = TestRun(world_for(program), program).run()
    (check,) = result.checks
    assert type(check["line"]) is str and type(check["failure"]) is str
    (words,) = result.words["en-GB|metric"]["checks"]
    assert set(words) == {"text", "failure", "line"}
    assert words["line"] == check["line"]           # degrees read the same in every setting
    assert result.words["en-GB|metric"]["outcome"] == result.outcome


def test_a_run_outside_machine_text_has_no_words(tmp_path, units):
    units("metric")
    program = _program(write_test(tmp_path, DRIVE))
    result = TestRun(world_for(program), program).run()
    assert result.words is None
    assert result.outcome.startswith("drive (block 1) didn't reach 5 m in 0.3 s (went ")
    folder = store.save_run(result, program, tmp_path / "runs")
    assert "words" not in json.loads((folder / "result.json").read_text(encoding="utf-8"))


def test_a_sweeps_table_json_has_si_values_and_the_words(tmp_path, units):
    units("metric")
    program = _program(write_test(tmp_path, DRIVE))
    folder = run_sweep(program, {"block": [0], "key": "distance"}, [5.0, 6.0], tmp_path / "runs")
    rows = read_table(folder)
    assert rows[0]["outcome"].startswith("drive (block 1) didn't reach 5 m")     # table.csv: machine text
    table = json.loads((folder / "table.json").read_text(encoding="utf-8"))
    assert table["kind"] == "field" and [r["si"] for r in table["rows"]] == [5.0, 6.0]
    assert [r["run"] for r in table["rows"]] == ["001", "002"]
    assert set(table["words"]) == KEYS
    imperial = table["words"]["en-GB|imperial"]["outcomes"]
    assert imperial[0].startswith("drive (block 1) didn't reach 16.4 ft") and \
        imperial[1].startswith("drive (block 1) didn't reach 19.69 ft")
    # the state's sweep carries it, and a run's words are its result's
    (sweep,) = store.list_sweeps(tmp_path / "runs")
    assert sweep["table"] == table
    kept = store.load_result(tmp_path / "runs", "sweep-001/001")
    assert kept["words"]["en-GB|imperial"]["outcome"] == imperial[0]
    entry = next(r for r in store.list_runs(tmp_path / "runs") if r["id"] == "sweep-001/001")
    assert entry["words"]["en-GB|imperial"] == {"outcome": imperial[0]}


def test_a_sweeps_check_columns_are_worded_in_each_setting(tmp_path):
    program = _program(write_test(tmp_path, TILT))
    folder = run_sweep(program, {"robot": "mass"}, [30.0], tmp_path / "runs")
    table = json.loads((folder / "table.json").read_text(encoding="utf-8"))
    assert table["kind"] == "mass" and table["rows"][0]["si"] == 30.0
    columns = [c for c in read_table(folder)[0] if c not in ("value", "status", "outcome", "t_end")]
    assert table["words"]["en-GB|metric"]["columns"] == columns == ["block 1: the robot tilts more than 45°"]


def test_a_sweep_value_in_the_blocks_inches_is_kept_in_meters(tmp_path):
    move = '[[block]]\ndo = "move"\njoint = "arm.pivot"\nto = 10.0\nunit = "deg"\ntimeout = 0.2\n'
    program = _program(write_test(tmp_path, move))
    folder = run_sweep(program, {"block": [0], "key": "to"}, [10.0], tmp_path / "runs")
    assert json.loads((folder / "table.json").read_text(encoding="utf-8"))["kind"] is None   # degrees: as they are
    from scrimmage.blocks.sweep import value_kind
    assert value_kind({"block": [0], "key": "to"}, _program(write_test(tmp_path, move.replace('"deg"', '"in"')))) \
        == ("length", 0.0254)


def test_a_spreads_table_json_words_follow_each_setting(tmp_path, units):
    units("metric")
    path = spread_test(tmp_path, "offset_in = [0, 30]")
    folder = run_spread(_program(path), path.parent / "runs")
    assert [r["placement"] for r in read_table(folder)] == ["on the center line", "30 in left"]   # machine text
    table = json.loads((folder / "table.json").read_text(encoding="utf-8"))
    gb = table["words"]["en-GB|metric"]
    assert gb["placements"] == ["on the centre line", "762 mm left"]
    assert gb["sentence"] == "picked up 1 of 2; missed at 762 mm left"
    assert table["words"]["en-US|imperial"]["sentence"] == "picked up 1 of 2; missed at 30 in left"
    assert len(gb["outcomes"]) == 2 and all(gb["outcomes"])
    (spread,) = store.list_spreads(path.parent / "runs")
    assert spread["table"] == table


def test_an_older_sweep_without_table_json_is_shown_from_table_csv(tmp_path):
    program = _program(write_test(tmp_path, DRIVE))
    folder = run_sweep(program, {"block": [0], "key": "distance"}, [5.0], tmp_path / "runs")
    (folder / "table.json").unlink()
    (sweep,) = store.list_sweeps(tmp_path / "runs")
    assert sweep["table"] is None and sweep["rows"] == read_table(folder)
    (folder / "table.json").write_text('{"rows": []}', encoding="utf-8")      # one that doesn't match its rows
    assert store.list_sweeps(tmp_path / "runs")[0]["table"] is None


def test_a_new_shuffle_is_said_in_every_settings_words(tmp_path):
    from types import SimpleNamespace
    program = _program(write_test(tmp_path, DRIVE))
    with units_mod.machine_text():
        result = TestRun(world_for(program), program).run()
    base = tmp_path / "runs"
    first = SimpleNamespace(**{**vars(result), "seed": 1, "words": json.loads(json.dumps(result.words))})
    store.save_run(first, program, base)
    second = SimpleNamespace(**{**vars(result), "seed": 2, "words": json.loads(json.dumps(result.words))})
    store.save_run(second, program, base)
    kept = store.load_result(base, "002")
    assert kept["outcome"].endswith("; a new shuffle (seed 2)")
    assert all(w["outcome"].endswith("; a new shuffle (seed 2)") for w in kept["words"].values())
