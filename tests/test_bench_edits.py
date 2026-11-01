"""Pure edit operations on bench files (comments and order kept)."""

from pathlib import Path

import pytest
import tomlkit

from scrimmage.assembler.bench_edits import (
    DEFAULTS, add_entry, new_bench_text, override_included, remove_entry, rename_entry, set_value)
from scrimmage.assembler.edits import EditError
from scrimmage.bench.problems import collect_problems
from scrimmage.bench.spec import _read_toml, _resolve_includes

EX = Path(__file__).resolve().parent.parent / "examples" / "bench"
BOUNCE = (EX / "bounce_bar.toml").read_text(encoding="utf-8")
PINCH = (EX / "pinch_car.toml").read_text(encoding="utf-8")


def write(tmp_path, text, name="b.toml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def errors(path):
    _, probs = collect_problems(path)
    return [p for p in probs if p.level == "error"]


def test_set_keeps_comments_and_order():
    doc = tomlkit.parse(BOUNCE)
    set_value(doc, "bar.kicker.spring", 4000.0)
    out = tomlkit.dumps(doc)
    assert "spring = 4000.0" in out and "# N/m, up and down" in out
    assert out.replace("4000.0", "3000.0") == BOUNCE


def test_set_inch_form_replaces_metric():
    doc = tomlkit.parse(PINCH)
    set_value(doc, "wheel.top.diameter_in", 2.5)
    top = [w for w in doc["wheel"] if w["name"] == "top"][0]
    assert top["diameter_in"] == 2.5 and "diameter" not in top
    set_value(doc, "wheel.top.diameter", 0.07)
    assert top["diameter"] == 0.07 and "diameter_in" not in top


def test_set_none_removes_and_single_table_created():
    doc = tomlkit.parse(PINCH)
    set_value(doc, "wheel.top.tread", None)
    assert "tread" not in [w for w in doc["wheel"] if w["name"] == "top"][0]
    set_value(doc, "flight.height", 0.5)
    set_value(doc, "run.duration", 3.0)
    assert doc["flight"]["height"] == 0.5 and doc["run"]["duration"] == 3.0
    set_value(doc, "shaft.top.target_rps", 55)
    assert doc["shaft"]["top"]["target_rps"] == 55
    set_value(doc, "wheel.top.center", [0.0, 0.0, 0.05])
    assert tomlkit.parse(tomlkit.dumps(doc))["wheel"][1]["center"] == [0.0, 0.0, 0.05]


def test_set_errors_name_the_key():
    doc = tomlkit.parse(PINCH)
    for key in ("wheel.nope.diameter", "wheel.top.bogus", "bogus.x", "wheel.top"):
        with pytest.raises(EditError) as e:
            set_value(doc, key, 1)
        assert e.value.field == key


@pytest.mark.parametrize("section", ["wheel", "plate", "bar", "curve", "inlet", "outlet", "shaft"])
def test_added_entry_loads(section, tmp_path):
    doc = tomlkit.parse(PINCH)
    name = add_entry(doc, section)
    assert name == f"{section}1"
    assert not errors(write(tmp_path, tomlkit.dumps(doc)))
    assert add_entry(doc, section) == f"{section}2"
    assert not errors(write(tmp_path, tomlkit.dumps(doc)))


def test_add_wheel_without_shafts_adds_one(tmp_path):
    doc = tomlkit.parse('[bench]\nname = "x"\n\n[piece]\npreset = "2026-fuel"\n\n'
                        '[[inlet]]\nname = "in"\ncenter = [-1, 0, 0]\nsize = [0.01, 0.01, 0.01]\n\n'
                        '[[outlet]]\nname = "out"\ncenter = [1, 0, 0]\nsize = [1, 1, 1]\n')
    name = add_entry(doc, "wheel")
    assert name in doc["shaft"] and doc["wheel"][0]["shaft"] == name
    assert not errors(write(tmp_path, tomlkit.dumps(doc)))


def test_add_with_name_fields_and_bad_input():
    doc = tomlkit.parse(PINCH)
    assert add_entry(doc, "plate", "ramp", {"size_in": [4, 4, 1]}) == "ramp"
    ramp = [p for p in doc["plate"] if p["name"] == "ramp"][0]
    assert ramp["size_in"] == [4, 4, 1] and "size" not in ramp
    with pytest.raises(EditError):
        add_entry(doc, "plate", "ramp")
    with pytest.raises(EditError):
        add_entry(doc, "plate", "bad name")
    with pytest.raises(EditError):
        add_entry(doc, "gizmo")
    with pytest.raises(EditError):
        add_entry(doc, "plate", "q", {"bogus": 1})


def test_added_entry_keeps_comments():
    doc = tomlkit.parse(BOUNCE)
    add_entry(doc, "bar")
    out = tomlkit.dumps(doc)
    assert "# 4 in. wheels 0.12 m apart" in out and "# N/m, up and down" in out


def test_rename_shaft_rewrites_wheels():
    doc = tomlkit.parse(PINCH + '\n[sweep]\n"shaft.top.target_rps" = [30, 40]\n"wheel.top.diameter" = [0.05]\n')
    rename_entry(doc, "shaft", "top", "upper")
    assert "upper" in doc["shaft"] and "top" not in doc["shaft"]
    assert [w for w in doc["wheel"] if w["name"] == "top"][0]["shaft"] == "upper"
    assert list(doc["sweep"]) == ["shaft.upper.target_rps", "wheel.top.diameter"]
    rename_entry(doc, "wheel", "top", "roof")
    assert list(doc["sweep"]) == ["shaft.upper.target_rps", "wheel.roof.diameter"]
    text = tomlkit.dumps(doc)
    assert "[shaft.upper]" in text and "# turned the other way" in text
    assert tomlkit.parse(text)["shaft"]["upper"]["target_rps"] == 40


def test_rename_rewrites_coupling_slots_flight_and_checks(tmp_path):
    doc = tomlkit.parse(
        '[shaft.a]\nmotor = "KrakenX60"\n\n[shaft.b]\nmotor = "KrakenX60"\ncoupled_to = "a"\nratio = 2\n\n'
        '[[cad]]\nname = "c"\nmesh = "m.stl"\n\n[cad.slots]\nm1 = "a"\n\n'
        '[[outlet]]\nname = "o"\n\n[flight]\noutlet = "o"\n')
    rename_entry(doc, "shaft", "a", "z")
    assert doc["shaft"]["b"]["coupled_to"] == "z" and doc["cad"][0]["slots"]["m1"] == "z"
    rename_entry(doc, "outlet", "o", "goal")
    assert doc["flight"]["outlet"] == "goal" and doc["outlet"][0]["name"] == "goal"
    with pytest.raises(EditError):
        rename_entry(doc, "shaft", "z", "b")
    with pytest.raises(EditError):
        rename_entry(doc, "shaft", "nope", "q")
    with pytest.raises(EditError):
        rename_entry(doc, "shaft", "z", "has space")


def test_remove_used_shaft_is_refused():
    doc = tomlkit.parse(PINCH)
    with pytest.raises(EditError) as e:
        remove_entry(doc, "shaft", "top")
    assert e.value.field == "shaft.top" and "top" in e.value.message and "wheel" in e.value.message
    remove_entry(doc, "wheel", "top")
    remove_entry(doc, "shaft", "top")
    assert "top" not in doc["shaft"] and "# a toy car" not in tomlkit.dumps(doc).replace("# A toy", "")
    assert [w["name"] for w in doc["wheel"]] == ["bottom"]


def test_remove_drops_sweep_keys_and_last_entry(tmp_path):
    doc = tomlkit.parse(BOUNCE + '\n[sweep]\n"bar.kicker.spring" = [1000.0]\n"run.duration" = [1.0]\n')
    remove_entry(doc, "bar", "kicker")
    assert "bar" not in doc and list(doc["sweep"]) == ["run.duration"]
    with pytest.raises(EditError):
        remove_entry(doc, "bar", "kicker")


def test_override_included_copies_entry(tmp_path):
    write(tmp_path, PINCH, "base.toml")
    top = write(tmp_path, 'include = ["base.toml"]\n\n[bench]\nname = "mine"\n', "mine.toml")
    doc = tomlkit.parse(top.read_text())
    merged = _resolve_includes(_read_toml(top), tmp_path, (top.resolve(),))
    with pytest.raises(EditError):
        set_value(doc, "wheel.top.diameter", 0.07)
    override_included(doc, merged, "wheel", "top")
    set_value(doc, "wheel.top.diameter", 0.07)
    top.write_text(tomlkit.dumps(doc))
    bench, probs = collect_problems(top)
    assert not [p for p in probs if p.level == "error"]
    assert [w.diameter for w in bench.wheels if w.name == "top"] == [0.07]
    assert len(bench.wheels) == 2
    with pytest.raises(EditError):
        override_included(doc, merged, "wheel", "top")   # already in the file
    override_included(doc, merged, "shaft", "top")
    assert doc["shaft"]["top"]["target_rps"] == 40
    override_included(doc, merged, "run", "run")
    assert doc["run"]["duration"] == 2.0
    top.write_text(tomlkit.dumps(doc))
    assert not errors(top)


def test_new_bench_blank_and_from_example_load(tmp_path):
    blank = new_bench_text("my idea", "Sam", "squeeze it", None)
    doc = tomlkit.parse(blank)
    assert doc["bench"]["name"] == "my idea" and doc["bench"]["author"] == "Sam"
    assert len(doc["wheel"]) == 2 and len(doc["inlet"]) == 1 and len(doc["outlet"]) == 1
    assert doc["piece"]["preset"] == "2026-fuel" and len(doc["shaft"]) == 2
    assert not errors(write(tmp_path, blank))
    ex = new_bench_text("copy", "Ravi", "like the car", EX / "pinch_car.toml")
    d2 = tomlkit.parse(ex)
    assert d2["bench"]["name"] == "copy" and d2["bench"]["author"] == "Ravi" and d2["bench"]["idea"] == "like the car"
    assert d2["piece"]["name"] == "car" and len(d2["wheel"]) == 2 and "# turned the other way" in ex
    assert not errors(write(tmp_path, ex, "c.toml"))
    odd = new_bench_text('say "hi"', "A\\B", "line", None)
    assert tomlkit.parse(odd)["bench"]["name"] == 'say "hi"'


def test_defaults_cover_every_section():
    assert set(DEFAULTS) == {"bench", "piece", "shaft", "wheel", "plate", "bar", "curve", "cad", "inlet", "outlet",
                             "flight", "run"}


def test_add_cad_with_a_part_folder_drops_the_default_mesh():
    doc = tomlkit.parse(PINCH)
    assert add_entry(doc, "cad", "arm", {"part": "parts/arm"}) == "arm"
    arm = [c for c in doc["cad"] if c["name"] == "arm"][0]
    assert arm["part"] == "parts/arm" and "mesh" not in arm and "units" not in arm
    add_entry(doc, "cad", "block", {"mesh": "block.stl", "units": "in"})
    block = [c for c in doc["cad"] if c["name"] == "block"][0]
    assert block["mesh"] == "block.stl" and block["units"] == "in" and "part" not in block


def test_set_keeps_floats_floats():
    """The page sends JSON, where 0.0 arrives as 0: a whole number replacing a float stays written as a float."""
    doc = tomlkit.parse(BOUNCE)
    set_value(doc, "plate.feeder.center", [-0.21, 0, -0.08])
    set_value(doc, "run.duration", 3)
    set_value(doc, "shaft.top.target_rps", 40)   # written as a whole number: stays one
    text = tomlkit.dumps(doc)
    assert "center = [-0.21, 0.0, -0.08]" in text and "duration = 3.0" in text and "target_rps = 40\n" in text
