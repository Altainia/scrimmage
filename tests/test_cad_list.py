"""`scrimmage cad list`: a CAD file's pieces, grouped by name, filtered, and as JSON."""
import json
import time
from pathlib import Path

import pytest

from scrimmage.__main__ import main
from scrimmage.cad import listing
from scrimmage.cad.source import load_source
from tests.cad_helpers import FIELD_STEP

DATA = Path(__file__).parent / "data" / "cad"
CLIMBER = DATA / "climber.step"


def run(capsys, *argv):
    capsys.readouterr()
    try:
        main(["cad", "list", *map(str, argv)])
        code = 0
    except SystemExit as e:
        code = e.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_list_groups_repeated_names(capsys):
    code, out, _ = run(capsys, CLIMBER)
    assert code == 0
    lines = out.splitlines()
    assert sum(1 for ln in lines if ln.startswith("bolt ")) == 1
    assert next(ln for ln in lines if ln.startswith("bolt ")).split()[1] == "×6"
    assert next(ln for ln in lines if ln.startswith("hook ")).split()[1] == "×2"


def test_list_match_filters_by_name_or_key(capsys):
    _, out, _ = run(capsys, CLIMBER, "--match", "hook*")
    body = out.splitlines()[1:]
    assert body and all(ln.startswith("hook") for ln in body)
    _, out, _ = run(capsys, CLIMBER, "--match", "Climber/arm_*")
    body = out.splitlines()[1:]
    assert [ln.split()[0] for ln in body] == ["arm_A", "arm_B"]


def test_list_near_keeps_pieces_within(capsys):
    src = load_source(CLIMBER)
    arm = next(r for r in listing.rows(src) if r.name == "arm_A")
    x, y, z = arm.center_m
    code, out, _ = run(capsys, CLIMBER, "--near", f"{x},{y},{z}", "--within", "0.05")
    assert code == 0
    assert [ln.split()[0] for ln in out.splitlines()[1:]] == ["arm_A"]


def test_list_json_has_every_piece(capsys):
    code, out, _ = run(capsys, CLIMBER, "--json")
    assert code == 0
    doc = json.loads(out)
    assert doc["units"] == "mm" and doc["file"].endswith("climber.step")
    assert len(doc["pieces"]) == 11
    for p in doc["pieces"]:
        assert set(p) == {"key", "name", "center_m", "size_m", "center_file", "size_file", "triangles"}
        for m, f in zip(p["center_m"], p["center_file"]):
            assert f == pytest.approx(m * 1000)


def test_list_shows_file_units_and_meters(capsys):
    _, out, _ = run(capsys, CLIMBER)
    lines = out.splitlines()
    assert "mm" in lines[0]
    arm = next(ln for ln in lines if ln.startswith("arm_A "))
    assert "20 x 20 x 800" in arm and "0.02 x 0.02 x 0.8" in arm


def test_list_errors(capsys):
    code, _, err = run(capsys, CLIMBER, "--near", "0,0,0")
    assert code == 2 and err.startswith("scrimmage: ") and "--within" in err
    code, _, err = run(capsys, CLIMBER, "--within", "1")
    assert code == 2 and "--near" in err
    code, _, err = run(capsys, CLIMBER, "--near", "1,2", "--within", "1")
    assert code == 2 and "--near" in err
    code, _, err = run(capsys, CLIMBER, "--match", "bolts")
    assert code == 1 and "bolt" in err
    code, _, err = run(capsys, DATA / "nothing.step")
    assert code == 2 and err.startswith("scrimmage: ")


def test_list_an_stl_folder(capsys):
    code, out, _ = run(capsys, DATA / "stl")
    assert code == 0
    assert "mm" in out.splitlines()[0]
    assert "arm_A" in out and "hook" in out


def test_header_counts_pieces_not_lines(capsys):
    _, out, _ = run(capsys, CLIMBER)
    assert out.splitlines()[0].startswith("11 pieces in 5 lines")


def test_default_order_is_largest_first(capsys):
    _, out, _ = run(capsys, CLIMBER)
    names = [ln.split()[0] for ln in out.splitlines()[1:]]
    assert names[0] == "arm_A" or names[0] == "arm_B" or names[0] == "base_plate"
    assert names.index("bolt") > names.index("hook")        # bolts are the smallest
    _, out, _ = run(capsys, CLIMBER, "--sort", "name")
    names = [ln.split()[0] for ln in out.splitlines()[1:]]
    assert names == sorted(names)
    _, out, _ = run(capsys, CLIMBER, "--sort", "file")
    assert out.splitlines()[1].startswith("base_plate")


def test_a_guessed_unit_says_so(capsys):
    _, out, _ = run(capsys, DATA / "stl")
    assert "mm (guessed)" in out.splitlines()[0]
    _, out, _ = run(capsys, DATA / "stl", "--units", "mm")
    assert "guessed" not in out.splitlines()[0]
    _, out, _ = run(capsys, CLIMBER)
    assert "guessed" not in out.splitlines()[0]


def test_json_has_no_float32_noise(capsys):
    _, out, _ = run(capsys, CLIMBER, "--json")
    plate = next(p for p in json.loads(out)["pieces"] if p["name"] == "base_plate")
    assert plate["center_m"][0] == 0.15 and plate["size_file"] == [300.0, 200.0, 10.0]


def test_units_takes_every_step_unit(capsys):
    code, out, _ = run(capsys, CLIMBER, "--units", "cm")
    assert code == 0 and "30 x 20 x 1" in out


def test_a_glob_on_an_assembly_matches_its_descendants():
    src = load_source(CLIMBER)
    hooks = [r for r in listing.rows(src) if listing.matches(r, "*hook_assy*/*")]
    assert [r.name for r in hooks] == ["hook", "hook"]
    assert len([r for r in listing.rows(src) if listing.matches(r, "Climber/*")]) == 11


def test_list_units_flag(capsys):
    _, out, _ = run(capsys, CLIMBER, "--units", "in")
    assert "in" in out.splitlines()[0]
    assert "0.787" in next(ln for ln in out.splitlines() if ln.startswith("arm_A "))


def test_matches_name_or_key():
    src = load_source(CLIMBER)
    arm = next(p for p in src.pieces if p.name == "arm_A")
    assert listing.matches(arm, "arm_*") and listing.matches(arm, "Climber/arm_*")
    assert not listing.matches(arm, "ARM_*") and not listing.matches(arm, "hook*")


@pytest.mark.skipif(not FIELD_STEP.exists(), reason="FIRST's field CAD isn't committed")
def test_list_the_field_step_finds_the_stage(capsys):
    t0 = time.time()
    code, out, _ = run(capsys, FIELD_STEP, "--match", "GE-2420*")
    assert code == 0 and len(out.splitlines()) >= 2
    assert time.time() - t0 < 60
