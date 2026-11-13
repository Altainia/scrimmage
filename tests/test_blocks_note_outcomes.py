"""A test's piece_in bodies are its hopper, and the run's piece table shows the feed outcome (K6-R12, K6-R13), on
examples/blocks/note-intake."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from scrimmage.blocks.conditions import Watch, _conds
from scrimmage.blocks.program import Block, Cond, load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.blocks.run_notes import piece_lines
from scrimmage.scenario_runner import _ball_result

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "note-intake"
HOPPER_LINE = 'hopper = ["intake.hopper"]\n'
TAIL = re.compile(r'\[\[block\]\]\ndo = "wait_until".*', re.S)
WAIT_4S = '[[block]]\ndo = "wait"\nseconds = 4.0\n'


def _copy(tmp_path: Path, edit) -> Path:
    folder = tmp_path / "note-intake"
    shutil.copytree(EXAMPLE, folder)
    test = folder / "intake.test.toml"
    test.write_text(edit(test.read_text(encoding="utf-8")), encoding="utf-8")
    return test


def _run(path: Path):
    prog = load(path)
    world = world_for(prog)
    watch = Watch(world, prog)
    return prog, watch, TestRun(world, prog).run()


def _table(result) -> list[str]:
    return [" ".join(line.split()) for line in piece_lines(result)]


def test_a_piece_in_body_is_the_hopper(tmp_path):
    prog, watch, result = _run(_copy(tmp_path, lambda s: s.replace(HOPPER_LINE, "")))
    assert prog.hopper is None and watch.hopper == ["intake.hopper"]
    assert result.passed and "1 of 1 made it in" in result.outcome
    row = result.pieces[0]["feed"]
    assert (row["outcome"], row["body"]) == ("in", "intake.hopper")
    assert _table(result)[1].endswith("in in intake.hopper")


def test_no_hopper_and_no_piece_in_is_unchanged(tmp_path):
    def edit(s: str) -> str:
        return TAIL.sub(WAIT_4S, s.replace(HOPPER_LINE, ""))
    prog, watch, result = _run(_copy(tmp_path, edit))
    assert watch.hopper == []
    assert result.outcome == "finished" and result.pieces[0]["feed"] is None


def test_a_cleared_hopper_stays_none(tmp_path):
    prog, watch, result = _run(_copy(tmp_path, lambda s: s.replace(HOPPER_LINE, "hopper = []\n")))
    assert prog.hopper == [] and watch.hopper == []
    assert "1 of 1 made it in" not in result.outcome and result.outcome == "finished"


@pytest.fixture(scope="module")
def saved():
    return _run(EXAMPLE / "intake.test.toml")[2]


def test_the_piece_table_says_in_for_a_note_in_the_bin(saved):
    line = _table(saved)[1]
    assert line.endswith("in in intake.hopper") and "on_field" not in line


def test_the_scenario_ledger_table_is_unchanged(saved):
    p = saved.pieces[0]
    assert p["outcome"] == "on_field"
    assert "rest at" in _ball_result({**p, "last_pos": tuple(p["last_pos"])})


def test_the_cli_run_says_the_note_made_it_in(tmp_path, capsys):
    """`scrimmage test run` builds a bare TestRun: the NOTE is tracked there too."""
    import argparse

    from scrimmage.__main__ import _test

    with pytest.raises(SystemExit) as e:
        _test(argparse.Namespace(test_command="run", file=EXAMPLE / "intake.test.toml", out=tmp_path / "runs"))
    out = capsys.readouterr().out
    assert e.value.code == 0
    assert "1 of 1 made it in" in out.splitlines()[0]
    assert re.search(r"\bin\s+in intake\.hopper", out) and "on_field" not in out


def test_the_cli_names_a_stuck_floor_note_once(tmp_path, capsys):
    """At 0 V the NOTE ends stuck: the piece table says where, and the stations' "stuck in the robot: ... (fed at
    ...)" line, which is for a fed piece, isn't printed for the floor piece."""
    import argparse

    from scrimmage.__main__ import _test

    path = _copy(tmp_path, lambda s: s.replace("volts = 8.0", "volts = 0.0"))
    with pytest.raises(SystemExit) as e:
        _test(argparse.Namespace(test_command="run", file=path, out=tmp_path / "runs"))
    out = capsys.readouterr().out
    assert e.value.code == 1 and "1 stuck in the robot" in out.splitlines()[0], out
    assert re.search(r"\bstuck\s+stuck on intake\.", out), out
    assert "stuck in the robot:" not in out and "fed at" not in out


def test_a_nested_piece_in_counts_as_a_hopper_body():
    inner = Cond("piece_in", {"body": "a.bin", "piece": "any"})
    outer = Cond("all", {"of": [inner, Cond("tilt", {"above": 1})]})
    blocks = [Block("repeat", {}, [Block("expect", {"that": outer})])]
    assert _conds(blocks, "piece_in") == [inner]
