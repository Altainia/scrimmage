"""A grip material on a part's bodies (scrimmage/parts/grip.py): the overlay key and the config's, the pad's friction
and firm contact governing a coral's, a rebuilt folder keeping the overlay, and the run's UNCALIBRATED notes."""

from __future__ import annotations

import tomllib
from pathlib import Path

import mujoco
import pytest
from pieces_helpers import blocks, grab_test, squeeze, wait

from scrimmage.blocks import store
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.parts.grip import (GRIP_MATERIALS, GRIP_PRIORITY, GRIP_SOLREF, apply_grip, grip_friction,
                                  grip_notes, set_grip)
from scrimmage.parts.model import PartError, load_part
from scrimmage.testrange.build import RIGID_SOLREF

PART = """
[part]
name = "claw"
format = 1

[[body]]
name = "base"
mass = 1.0
geom = [{ box = [0.1, 0.1, 0.1] }]

[[body]]
name = "pad"
parent = "base"
mass = 0.2
geom = [{ box = [0.02, 0.1, 0.05] }, { box = [0.01, 0.01, 0.01], collide = false }]

[[body]]
name = "label"
parent = "base"
mass = 0.1
geom = [{ box = [0.02, 0.02, 0.02], collide = false }]
"""


def claw(tmp_path: Path, overlay: str | None = None) -> Path:
    folder = tmp_path / "claw"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "part.toml").write_text(PART, encoding="utf-8")
    if overlay is not None:
        (folder / "scrimmage-edits.toml").write_text(overlay, encoding="utf-8")
    return folder


def colliding(part, body: str):
    (g,) = [g for b in part.bodies if b.name == body for g in b.geoms if g.collide]
    return g


def test_an_overlay_grip_sets_friction_and_priority(tmp_path):
    folder = claw(tmp_path)
    set_grip(folder, "pad", "nitrile")
    part = load_part(folder / "part.toml")
    g = colliding(part, "pad")
    assert g.friction == pytest.approx(GRIP_MATERIALS["nitrile"][1]) and g.priority == GRIP_PRIORITY
    assert g.solref == tuple(GRIP_SOLREF)
    looks = [g for b in part.bodies if b.name == "pad" for g in b.geoms if not g.collide]
    assert looks[0].friction is None and looks[0].priority == 0          # a geom that touches nothing is left
    assert colliding(part, "base").friction is None
    assert part.grips == {"pad": "nitrile"}


def test_my_own_number(tmp_path):
    folder = claw(tmp_path)
    set_grip(folder, "pad", 0.85)
    assert colliding(load_part(folder), "pad").friction == pytest.approx(0.85)
    set_grip(folder, "pad", None)                                       # removed: the table goes too
    assert colliding(load_part(folder), "pad").friction is None
    assert "pad" not in tomllib.loads((folder / "scrimmage-edits.toml").read_text()).get("body", {})


def test_set_grip_keeps_the_rest_of_the_overlay(tmp_path):
    folder = claw(tmp_path, "# my edits\ntotal_mass = 3.0\n\n[body.pad]\nmass = 0.4   # weighed it\n")
    set_grip(folder, "pad", "grip-tape")
    text = (folder / "scrimmage-edits.toml").read_text()
    assert "# my edits" in text and "# weighed it" in text
    raw = tomllib.loads(text)
    assert raw["total_mass"] == 3.0 and raw["body"]["pad"] == {"mass": 0.4, "grip": "grip-tape"}
    part = load_part(folder)
    assert colliding(part, "pad").friction == pytest.approx(GRIP_MATERIALS["grip-tape"][1])
    assert [b.mass for b in part.bodies if b.name == "pad"] == [pytest.approx(0.4)]


def test_one_grip_table():
    """The pads' materials are the bench's (scrimmage/bench/presets.toml, rigid_friction) and the range's rubber
    surface (launcher/presets.toml), so the tables can't drift apart; the contact is a rigid piece's."""
    bench = tomllib.loads((Path(__file__).resolve().parent.parent / "scrimmage" / "bench" / "presets.toml")
                          .read_text())["material"]
    for key, mat in (("polycarbonate", "polycarbonate"), ("aluminum", "aluminum"), ("hdpe", "UHMW"),
                     ("nitrile", "nitrile")):
        assert GRIP_MATERIALS[key][1] == bench[mat]["rigid_friction"]
    from scrimmage.testrange.pieces import surface_friction
    assert GRIP_MATERIALS["rubber"][1] == surface_friction("rubber")
    assert list(GRIP_MATERIALS) == ["rubber", "nitrile", "grip-tape", "polycarbonate", "aluminum", "hdpe"]
    assert tuple(GRIP_SOLREF) == tuple(RIGID_SOLREF)


def test_grip_friction():
    assert grip_friction("hdpe", "x") == GRIP_MATERIALS["hdpe"][1]
    assert grip_friction(1, "x") == 1.0 and grip_friction(3.0, "x") == 3.0
    with pytest.raises(PartError, match=r"^x: grip \"nitirle\" isn't a grip material; pick one of rubber, nitrile, "
                                        r"grip-tape, polycarbonate, aluminum, hdpe, or give a number \(a measured "
                                        r"friction\)$"):
        grip_friction("nitirle", "x")
    for bad in (0, -0.5, 3.5, True, float("nan")):
        with pytest.raises(PartError, match="x: grip"):
            grip_friction(bad, "x")


# ---- in the robot: the grabber on the stand-in (tests/pieces_helpers.py), its pads the jaws -------------------------

def grabber(folder: Path, text: str | None = None, grips: dict | None = None, config_grip: str = "") -> Path:
    """grab_test's files with `grips` (body -> grip) set in the grabber folder's overlay and `config_grip` (a TOML
    inline table) as the [[part]]'s grip; the test file."""
    path = grab_test(folder, text)
    for body, grip in (grips or {}).items():
        set_grip(folder / "parts" / "grabber", body, grip)
    if config_grip:
        robot = folder / "grab-standin.toml"
        robot.write_text(robot.read_text().replace('path = "parts/grabber"\n',
                                                   f'path = "parts/grabber"\ngrip = {config_grip}\n'))
    return path


def run(path: Path):
    program = load(path)
    world = world_for(program)
    return world, TestRun(world, program).run()


def pad_geom(model, body: str) -> int:
    b = model.body(f"grabber.{body}").id
    (g,) = [g for g in range(model.ngeom) if model.geom_bodyid[g] == b and model.geom_contype[g]]
    return g


def test_a_config_grip_overrides_the_folders(tmp_path):
    world = world_for(load(grabber(tmp_path, wait(0.1), {"jaw_b": "nitrile", "jaw_f": "nitrile"},
                                   '{ jaw_b = "hdpe" }')))
    m = world.model
    assert m.geom_friction[pad_geom(m, "jaw_b")][0] == pytest.approx(GRIP_MATERIALS["hdpe"][1])
    assert m.geom_friction[pad_geom(m, "jaw_f")][0] == pytest.approx(GRIP_MATERIALS["nitrile"][1])
    assert m.geom_priority[pad_geom(m, "jaw_b")] == GRIP_PRIORITY
    assert list(m.geom_solref[pad_geom(m, "jaw_b")]) == list(RIGID_SOLREF)
    assert grip_notes(world) == [
        "grabber.jaw_b grip: HDPE / UHMW, friction 0.2 (UNCALIBRATED estimate; measure yours with a sled pull, "
        "docs/test-tab.md)",
        "grabber.jaw_f grip: nitrile, friction 0.8 (UNCALIBRATED estimate; measure yours with a sled pull, "
        "docs/test-tab.md)"]


def test_the_pads_friction_governs_the_coral_contact(tmp_path):
    """A slick pad (HDPE, 0.2) against the coral's PVC (0.4): the contact is the pad's, not the larger of the two."""
    world, _ = run(grabber(tmp_path, squeeze(4.0) + wait(1.0), {"jaw_b": "hdpe", "jaw_f": "hdpe"}))
    m, d = world.model, world.data
    pads = {pad_geom(m, "jaw_b"), pad_geom(m, "jaw_f")}
    coral = m.body("ball.0").id
    seen = [c for c in d.contact[:d.ncon]
            if {c.geom1, c.geom2} & pads and coral in (m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2])]
    assert seen, "the jaws should be squeezing the coral"
    for c in seen:
        assert c.friction[0] == pytest.approx(0.2)
        assert list(c.solref) == pytest.approx(list(RIGID_SOLREF))


@pytest.fixture(scope="module")
def grab_run_nitrile(tmp_path_factory):
    folder = tmp_path_factory.mktemp("nitrile")
    program = load(grabber(folder, blocks(1.0), {"jaw_b": "nitrile", "jaw_f": "nitrile"}))
    return program, TestRun(world_for(program), program).run()


def test_a_run_with_a_grip_says_uncalibrated(grab_run_nitrile, tmp_path):
    program, r = grab_run_nitrile
    assert r.status == "finished" and r.checks[-1]["passed"], r.outcome      # nitrile holds at 1 V through the swing
    assert len(r.notes) == 2 and all("UNCALIBRATED" in n for n in r.notes)
    folder = store.save_run(r, program, tmp_path / "runs")
    assert store.load_result(tmp_path / "runs", folder.name)["notes"] == r.notes


def test_a_slick_pad_slips_where_rubber_holds(tmp_path):
    """At a 1 V squeeze rubber (0.8) holds the coral through the swing; HDPE (0.2) lets it slip out."""
    _, rubber = run(grabber(tmp_path / "rubber", blocks(1.0), {"jaw_b": "rubber", "jaw_f": "rubber"}))
    assert rubber.status == "finished" and rubber.checks[-1]["passed"], rubber.outcome
    _, hdpe = run(grabber(tmp_path / "hdpe", blocks(1.0), {"jaw_b": "hdpe", "jaw_f": "hdpe"}))
    assert hdpe.status == "check failed" and "slipped out of grabber" in hdpe.outcome, hdpe.outcome


def test_a_held_test_without_a_grip_still_says_uncalibrated(tmp_path):
    world = world_for(load(grabber(tmp_path, blocks())))
    assert grip_notes(world) == []
    program = load(tmp_path / "grab.test.toml")
    from scrimmage.blocks.run import run_notes
    (note,) = run_notes(world, program)
    assert "UNCALIBRATED" in note
    assert run_notes(world, load(grabber(tmp_path / "plain", wait(0.1)))) == []


@pytest.mark.parametrize("case, words", [("typo", "isn't a grip material"), ("no geoms", "does nothing"),
                                         ("stock", "a stock part has its own materials"),
                                         ("unknown body", "no body")])
def test_review_focus_4_grips_that_cant_apply(tmp_path, case, words):
    if case == "typo":
        folder = claw(tmp_path, '[body.pad]\ngrip = "nitirle"\n')
        with pytest.raises(PartError, match=words):
            load_part(folder)
    elif case == "no geoms":
        folder = claw(tmp_path, '[body.label]\ngrip = "rubber"\n')
        part = load_part(folder)
        assert any(words in w and "body label" in w for w in part.warnings), part.warnings
    elif case == "unknown body":
        with pytest.raises(PartError, match=words):
            apply_grip(load_part(claw(tmp_path)), {"paw": "rubber"}, "parts[1] 'claw'")
    else:
        path = grab_test(tmp_path, wait(0.1))
        robot = tmp_path / "grab-standin.toml"
        robot.write_text(robot.read_text().replace('stock = "differential"', 'stock = "differential"\n'
                                                   'grip = { left = "rubber" }'))
        with pytest.raises(ValueError, match=r"parts\[1\] 'dt': grip is for a part folder's bodies; " + words):
            world_for(load(path))


def test_a_grip_on_a_mujoco_model_is_its_own(tmp_path):
    """The assembled model really carries the grip (MjSpec), not only the Part."""
    folder = claw(tmp_path, '[body.pad]\ngrip = "aluminum"\n')
    from scrimmage.parts.assemble import part_spec
    m = part_spec(load_part(folder)).compile()
    g = [g for g in range(m.ngeom) if m.geom_bodyid[g] == m.body("pad").id and m.geom_contype[g]]
    assert m.geom_friction[g[0]][0] == pytest.approx(GRIP_MATERIALS["aluminum"][1])
    assert m.geom_priority[g[0]] == GRIP_PRIORITY
    assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "pad") >= 0
