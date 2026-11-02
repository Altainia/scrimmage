"""The train-wheel ladder climber (examples/blocks/ladder_climber): a STEP file through the Part tab's import (edits,
build), onto the stand-in robot where the CAD has it ("Test it"), tested with blocks on the ladder, and the run kept for
the replay. What it gates (spec, "My calls"): the pipeline, and an outcome that is a sentence, not an exception. The
climb itself stops at rung1 for a physics reason (the example's README): reaching rung2 is marked as expected to
fail, so a change that makes it climb further says so here."""

from __future__ import annotations

import importlib.util
import csv
import json
import re
import shutil
import tomllib
from pathlib import Path

import mujoco
import pytest

from scrimmage.__main__ import main
from scrimmage.assembler import library
from scrimmage.blocks import store
from scrimmage.blocks.palette import problems
from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.blocks.sweep import run_sweep
from scrimmage.cad.settings import read_import
from scrimmage.parts.model import load_part

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "ladder_climber"
CAD = Path(__file__).resolve().parent / "data" / "cad"
STATUSES = ("finished", "check failed", "timed out", "tipped over", "walked off", "time limit", "repeat limit")


def _maker():
    spec = importlib.util.spec_from_file_location("make_climber", EXAMPLE / "make_climber.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def climb(tmp_path_factory):
    """The example made again from the fixture in a temp folder, and one run of its test there."""
    folder = tmp_path_factory.mktemp("ladder_climber")
    shutil.copy(CAD / "crank_climber.step", folder / "climber.step")
    messages = _maker().make(folder)
    shutil.copy(EXAMPLE / "climb.test.toml", folder / "climb.test.toml")
    program = load(folder / "climb.test.toml")
    world = world_for(program)
    result = TestRun(world, program).run()
    return {"folder": folder, "messages": messages, "program": program, "world": world, "result": result}


def test_the_example_s_cad_is_the_fixture():
    assert (EXAMPLE / "climber.step").read_bytes() == (CAD / "crank_climber.step").read_bytes()


def test_the_import_builds_the_climber(climb):
    folder = climb["folder"]
    assert climb["messages"] == []                      # nothing dropped by an edit, nothing the build couldn't do
    imp = read_import(folder / "parts" / "climber.import.toml")
    assert imp.frame.skip and imp.frame.confirmed and imp.source == "../climber.step"
    assert imp.written == {"climber": "climber"}
    part = load_part(folder / "parts" / "climber")
    joints = {b.name: b.joint.kind for b in part.bodies if b.joint is not None}
    assert joints == {"crank": "hinge", "rod_A": "hinge", "rod_B": "hinge", "arm_A": "slide", "arm_B": "slide",
                      "hook_A": "hinge", "hook_B": "hinge"}
    hooks = {b.name: b for b in part.bodies if b.name.startswith("hook")}
    for b in hooks.values():                            # finger, lip and back plate each touch as a box
        assert sum(g.collide for g in b.geoms) == 3 and b.joint.range == (0.0, 95.0) and b.joint.spring
    assert sorted((lp.body1, lp.body2) for lp in part.loops) == [("rod_A", "arm_A"), ("rod_B", "arm_B")]
    (motor,) = part.motors
    assert (motor.slot, motor.joint, motor.motor, motor.gearing) == ("crank_motor", "crank", "NEO", 200.0)


def test_the_stand_in_carries_it_where_the_cad_has_it(climb):
    folder, world = climb["folder"], climb["world"]
    raw = tomllib.loads((folder / "climber-standin.toml").read_text(encoding="utf-8"))
    (part,) = [p for p in raw["part"] if p.get("path")]
    assert part == {"name": "climber", "path": "parts/climber", "attach": "drive.chassis",
                    "ids": {"crank_motor": "PWM:2"}}
    assert raw["range"] == {"file": "ladder", "start": "below"}
    marker = library.cad_marker(folder / "parts" / "climber")
    m, d = world.model, mujoco.MjData(world.model)
    mujoco.mj_forward(m, d)
    R = d.xmat[world.robot_body].reshape(3, 3)
    for body, origin in marker["origins"].items():
        rel = R.T @ (d.xpos[m.body(f"climber.{body}").id] - d.xpos[world.robot_body])
        assert rel == pytest.approx(origin, abs=1e-6), body
    assert problems(climb["program"], world) == []


def test_the_committed_example_is_what_the_import_makes(climb):
    folder = climb["folder"]
    assert ((EXAMPLE / "climber-standin.toml").read_text(encoding="utf-8")
            == (folder / "climber-standin.toml").read_text(encoding="utf-8"))
    ours, made = load_part(EXAMPLE / "parts" / "climber"), load_part(folder / "parts" / "climber")
    assert [b.name for b in ours.bodies] == [b.name for b in made.bodies]
    for a, b in zip(ours.bodies, made.bodies):
        assert a.mass == pytest.approx(b.mass, rel=1e-6) and a.pos == pytest.approx(b.pos, abs=1e-9)
        assert (a.joint is None) == (b.joint is None)
        if a.joint is not None:
            assert (a.joint.kind, a.joint.range, a.joint.spring) == (b.joint.kind, b.joint.range, b.joint.spring)
            assert a.joint.axis == pytest.approx(b.joint.axis, abs=1e-9)
    assert ours.motors == made.motors and len(ours.loops) == len(made.loops)


def test_the_climb_ends_in_a_sentence(climb):
    r = climb["result"]
    assert r.status in STATUSES and isinstance(r.outcome, str) and r.outcome
    # what it does today (the README says why): onto rung1, then the higher hook can't reach rung2
    assert r.hung == ["rung1"]
    assert r.status == "timed out", r.outcome
    assert re.fullmatch(r"reached rung1, then waiting for a hook to touch rung2 timed out at \d+\.\d s "
                        r"\(block 2 inside block 3\)", r.outcome), r.outcome
    assert r.checks[0]["throughout"] and r.checks[0]["passed"]     # it stays under 30° of tilt the whole time
    assert 10.0 < max(r.signals["tilt"]) < 30.0
    assert max(r.signals["climber.crank"]) > 360.0                 # the crank went round, it didn't stall


@pytest.mark.xfail(strict=True, reason="the robot hangs from rung1 but tips up on its back bumper instead of "
                                       "rising, so the higher hook stays below and behind rung2 "
                                       "(examples/blocks/ladder_climber/README.md)")
def test_crank_climber_reaches_rung2(climb):
    assert "rung2" in climb["result"].hung


def test_the_run_is_kept_for_the_replay(climb):
    r, base = climb["result"], climb["folder"] / "climb.test-runs"
    folder = store.save_run(r, climb["program"], base)
    run = store.load_run(base, folder.name)
    assert run["outcome"] == r.outcome and run["hung"] == ["rung1"]
    assert {"climber.hook_A", "climber.hook_B", "climber.crank"} <= set(r.movers) and run["view"]["geoms"]
    n, times, poses = store.read_motion(run["motion"])
    assert n == len(r.movers) and len(times) == len(r.motion) and poses.shape[1:] == (n, 7)
    assert run["signals"]["tilt"] == pytest.approx(r.signals["tilt"], abs=1e-6)


def test_a_crank_volts_sweep_and_its_runs_for_compare(climb):
    """The spin of the crank (block 3's first block) at 6 V and 9 V: a row and a kept run each, and both runs load for
    the compare view."""
    base = climb["folder"] / "sweep-runs"
    out = run_sweep(climb["program"], {"block": [2, 0], "key": "volts"}, [6.0, 9.0], base)
    with open(out / "table.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [float(r["value"]) for r in rows] == [6.0, 9.0]
    assert all(r["status"] in STATUSES and r["outcome"] for r in rows), rows
    ids = sorted(e["id"] for e in store.list_runs(base))
    assert ids == ["sweep-001/001", "sweep-001/002"]
    runs = [store.load_run(base, i) for i in ids]
    assert [r["sweep"]["value"] for r in runs] == [6.0, 9.0]
    for r in runs:
        assert r["signals"]["t"] and r["view"]["geoms"] and r["motion"].is_file()
        assert "rung1" in r["hung"]


def test_scrimmage_test_run_on_the_example(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    with pytest.raises(SystemExit) as e:
        main(["test", "run", str(EXAMPLE / "climb.test.toml"), "--out", str(tmp_path / "runs")])
    out = capsys.readouterr().out
    assert e.value.code == 1, out                        # it didn't finish: it timed out waiting at rung2
    assert out.startswith("reached rung1, then ") and "passed: the robot tilts less than 30°" in out
    assert json.loads((tmp_path / "runs" / "001" / "result.json").read_text(encoding="utf-8"))["hung"] == ["rung1"]
