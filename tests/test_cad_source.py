"""Reading CAD sources: STEP assemblies (names, nesting, positions) and STL/OBJ folders (units)."""

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from scrimmage.cad import CadError, missing_extra
from scrimmage.cad.mass import is_closed, mesh_inertial
from scrimmage.cad.source import UNITS, guess_units, load_source

DATA = Path(__file__).parent / "data" / "cad"


def piece(s, key):
    return next(p for p in s.pieces if p.key == key)


def test_step_names_nesting_positions():
    s = load_source(DATA / "climber.step")
    keys = [p.key for p in s.pieces]
    assert "Climber/base_plate" in keys and "Climber/hook_assy/hook" in keys and "Climber/hook_assy#2/hook" in keys
    arm = piece(s, "Climber/arm_A")
    assert arm.group == ("Climber",)
    assert piece(s, "Climber/hook_assy#2/hook").group == ("Climber", "Climber/hook_assy#2")
    assert arm.v.min(axis=0) == pytest.approx([0.05, 0.09, 0.01], abs=1e-6)     # meters, in CAD coords
    assert mesh_inertial(arm.v, arm.f, 1000).mass == pytest.approx(0.02 * 0.02 * 0.8 * 1000, rel=1e-6)
    assert is_closed(arm.f)
    assert s.kind == "step"


def test_repeated_bolts_get_numbered_keys():
    s = load_source(DATA / "climber.step")
    keys = [p.key for p in s.pieces]
    assert "Climber/bolt" in keys
    for i in range(2, 7):
        assert f"Climber/bolt#{i}" in keys
    assert "Climber/bolt#7" not in keys


def test_stl_folder_units_guessed_mm():
    s = load_source(DATA / "stl")
    assert s.units == "mm" and s.kind == "mesh"
    base = piece(s, "base_plate")
    assert np.ptp(base.v, axis=0).max() == pytest.approx(0.3, abs=1e-6)


def test_units_override():
    mm = piece(load_source(DATA / "stl" / "base_plate.stl", units="mm"), "base_plate")
    inch = piece(load_source(DATA / "stl" / "base_plate.stl", units="in"), "base_plate")
    assert np.ptp(inch.v, axis=0).max() == pytest.approx(300 * 0.0254, rel=1e-9)
    assert np.ptp(mm.v, axis=0).max() == pytest.approx(0.3, rel=1e-9)


def test_guess_units_thresholds():
    assert guess_units(0.8) == "m" and guess_units(30) == "in" and guess_units(800) == "mm"
    assert UNITS == {"m": 1.0, "mm": 0.001, "in": 0.0254}


def test_missing_extra_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "cascadio", None)
    with pytest.raises(CadError) as e:
        load_source(DATA / "climber.step")
    assert str(e.value) == missing_extra()


def test_unreadable_file_names_it(tmp_path):
    bad = tmp_path / "garbage.step"
    bad.write_text("this is not a STEP file")
    with pytest.raises(CadError, match="garbage.step"):
        load_source(bad)


def test_path_with_spaces_and_unicode(tmp_path):
    d = tmp_path / "my parts"
    d.mkdir()
    dst = d / "Klättrare.step"
    shutil.copy(DATA / "climber.step", dst)
    assert len(load_source(dst).pieces) > 5


def test_v2_and_yup_fixtures_load():
    v2 = load_source(DATA / "climber_v2.step")
    keys = [p.key for p in v2.pieces]
    assert "Climber/hook_assy_right/hook" in keys and "Climber/bolt#5" in keys and "Climber/bolt#6" not in keys
    assert np.ptp(piece(v2, "Climber/arm_B").v, axis=0)[2] == pytest.approx(0.9, abs=1e-6)
    yup = piece(load_source(DATA / "climber_yup.step"), "Climber/arm_A")
    assert np.ptp(yup.v, axis=0)[1] == pytest.approx(0.8, abs=1e-6)


def test_inside_out_stl_same_mass():
    a = load_source(DATA / "stl" / "hook.stl").pieces[0]
    b = load_source(DATA / "stl" / "hook_inside_out.stl").pieces[0]
    assert mesh_inertial(a.v, a.f, 1000).mass == pytest.approx(mesh_inertial(b.v, b.f, 1000).mass, rel=1e-6)
