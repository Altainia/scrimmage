"""examples/blocks/note-intake/: the over-under floor intake for the soft 2024 NOTE (K6-R10), and its robustness grids
(tests/intake_grid.py, the intake spike's three grids on the example's own files, K6-R15)."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import mujoco
import pytest

sys.path.insert(0, str(Path(__file__).parent))

import intake_grid  # noqa: E402
from intake_grid import IN, Case, corner_cases, oat_cases, random_cases, variant  # noqa: E402
from scrimmage.blocks.conditions import Watch  # noqa: E402
from scrimmage.blocks.program import load, loads  # noqa: E402
from scrimmage.blocks.run import TestRun, world_for  # noqa: E402
from scrimmage.blocks.run_notes import piece_lines  # noqa: E402

EXAMPLE = intake_grid.EXAMPLE
TEST = EXAMPLE / "intake.test.toml"
PART = Path("parts") / "intake" / "part.toml"
ROBOT = Path("note-intake-standin.toml")


def _settings(c: Case) -> tuple:
    """A case's settings as the spike's case files hold them: (rollers_z m, bx_dx m, yaw, b_clear m, volts, speed,
    offset_in)."""
    return (c.axle_in * IN, c.lip_in * IN, c.yaw_deg, c.clear_mm / 1000, c.volts, c.speed, c.offset_in)


# the first and last case of each of the spike's k6/{oat,corner,random}_cases.json, read once and pasted in (its run
# defaults: 8 V, 1 m/s, offset 0; its design default b_clear 0.003 m)
SPIKE_ENDS = {
    "oat": [
        (0.0, 0.0, 0.0, 0.003, 8.0, 1.0, 0.0),                                 # {"f": "nominal", "x": 0}
        (0.0, 0.0, 0.0, 0.01, 8.0, 1.0, 0.0),                                  # {"geo": {"b_clear": 0.01}}
    ],
    "corners": [
        (-0.0127, -0.0254, -15, 0.003, 5, 0.5, 0.0),
        (0.0127, 0.0254, -15, 0.003, 11, 2.0, 0.0),
    ],
    "random": [
        (-0.004474647773237675, -0.017736861964635302, 1.076460129200676, 0.003, 8.91, 0.61, -1.0744886646993157),
        (0.008284343396106616, -0.014679049263730039, -7.783818223249963, 0.003, 6.51, 0.94, 0.6914973453276936),
    ],
}


def test_the_grids_are_the_spikes():
    oat, corners, rand = oat_cases(), corner_cases(), random_cases()
    assert (len(oat), len(corners), len(rand)) == (45, 16, 40)
    c0 = corners[0]
    assert (c0.axle_in, c0.lip_in, c0.volts, c0.speed, c0.yaw_deg) == (-0.5, -1, 5, 0.5, -15)
    assert [c.x for c in rand][0] == [-0.176, -0.698, 8.91, 0.61, 1.1, -1.1]
    assert oat[1].volts == 0 and [c.label for c in oat[:2]] == ["nominal", "control_0V"]
    for name, cases in (("oat", oat), ("corners", corners), ("random", rand)):
        first, last = SPIKE_ENDS[name]
        assert _settings(cases[0]) == pytest.approx(first, abs=1e-12), name
        assert _settings(cases[-1]) == pytest.approx(last, abs=1e-12), name


def _read(folder: Path) -> tuple[dict, dict]:
    return (tomllib.loads((folder / PART).read_text(encoding="utf-8")),
            tomllib.loads((folder / ROBOT).read_text(encoding="utf-8")))


def _body(part: dict, name: str) -> dict:
    return next(b for b in part["body"] if b["name"] == name)


def _mount(robot: dict) -> dict:
    return next(p for p in robot["part"] if p["name"] == "intake")


def _moved(case: Case, tmp_path: Path) -> tuple[dict, dict, dict, dict]:
    part0, robot0 = _read(EXAMPLE)
    part, robot = _read(variant(case, tmp_path / "v"))
    return part0, robot0, part, robot


def _undo(item0: dict, item: dict, key: str, i: int) -> None:
    """Put item[key][i] back as item0 has it (dropping the key if item0 has none)."""
    if key in item0:
        item[key][i] = item0[key][i]
    else:
        del item[key]


def test_a_variant_moves_the_settings_bodies(tmp_path):
    # the lip, as the spike's generator moved it: the bottom roller, the floor's, walls' and lid's front ends (their
    # backs stay) and the ceiling's back end (its front stays)
    part0, robot0, part, robot = _moved(Case("t", "lip", 1, lip_in=1.0), tmp_path / "lip")
    assert _body(part, "bottom")["pos"][0] - _body(part0, "bottom")["pos"][0] == pytest.approx(0.0254, abs=1e-9)
    _undo(_body(part0, "bottom"), _body(part, "bottom"), "pos", 0)
    for name, k, end in [("hopper", 1, 1), ("hopper", 2, 1), ("hopper", 3, 1), ("hopper", 4, 1), ("guide", 2, -1)]:
        g0, g = _body(part0, name)["geom"][k], _body(part, name)["geom"][k]
        ends0 = [g0["pos"][0] - g0["box"][0] / 2, g0["pos"][0] + g0["box"][0] / 2]
        ends = [g["pos"][0] - g["box"][0] / 2, g["pos"][0] + g["box"][0] / 2]
        moved, kept = (1, 0) if end == 1 else (0, 1)
        assert ends[moved] - ends0[moved] == pytest.approx(0.0254, abs=1e-6), (name, k)
        assert ends[kept] == pytest.approx(ends0[kept], abs=1e-6), (name, k)
        _undo(g0, g, "pos", 0)
        _undo(g0, g, "box", 0)
    assert part == part0
    assert robot == robot0

    part0, robot0, part, robot = _moved(Case("t", "axle", -0.5, axle_in=-0.5), tmp_path / "axle")
    for name in ("roller", "top0"):
        assert _body(part, name)["pos"][2] - _body(part0, name)["pos"][2] == pytest.approx(-0.0127, abs=1e-9)
    for name in ("roller", "top0"):
        _undo(_body(part0, name), _body(part, name), "pos", 2)
    assert part == part0
    assert robot == robot0

    part0, robot0, part, robot = _moved(Case("t", "clear", 10, clear_mm=10.0), tmp_path / "clear")
    assert _mount(robot)["pos"][2] - _mount(robot0)["pos"][2] == pytest.approx(0.007, abs=1e-9)
    assert _body(part, "roller")["pos"][2] - _body(part0, "roller")["pos"][2] == pytest.approx(-0.007, abs=1e-9)
    # the front roller kept 3.4 in from the bottom one (K6-P2's formula, +2.03 mm). It hangs on the arm, so its place
    # in the part is the arm's pos plus its own
    arm, back = _body(part0, "arm")["pos"], _body(part0, "bottom")["pos"]
    front = [a + b for a, b in zip(arm, _body(part0, "roller")["pos"])]
    h0 = front[2] - back[2]
    cd = math.hypot(front[0] - back[0], h0)
    assert cd == pytest.approx(0.08636, abs=1e-5) and h0 == pytest.approx(0.02748, abs=1e-6)
    dx = math.sqrt(cd ** 2 - (h0 - 0.007) ** 2) - math.sqrt(cd ** 2 - h0 ** 2)
    assert dx == pytest.approx(0.00203, abs=1e-5)
    assert _body(part, "roller")["pos"][0] - _body(part0, "roller")["pos"][0] == pytest.approx(dx, abs=1e-6)
    for i in (0, 2):
        _undo(_body(part0, "roller"), _body(part, "roller"), "pos", i)
    _undo(_mount(robot0), _mount(robot), "pos", 2)
    assert (part, robot) == (part0, robot0)

    part0, robot0, part, robot = _moved(Case("t", "yaw", 15, yaw_deg=15.0), tmp_path / "yaw")
    assert _mount(robot)["rot"] == [0.0, 0.0, 15.0]
    _undo(_mount(robot0), _mount(robot), "rot", 2)
    assert (part, robot) == (part0, robot0)


ARM_LIFT_DEG = 2.0  # choice: well off the stop; the saved run lifts it 7.3 degrees


def test_the_arm_is_a_sprung_hinge():
    """The front roller's arm, as shipped (Task 1's round 1): a hinge about -y (positive lifts the roller) limited to
    0..25 degrees, its spring at 14 N*m/rad pulling toward -5 degrees, so it rests on its stop at 0."""
    prog = load(TEST)
    m = world_for(prog).model
    j = m.joint("intake.arm")
    assert j.type[0] == mujoco.mjtJoint.mjJNT_HINGE
    assert list(m.jnt_axis[j.id]) == pytest.approx([0.0, -1.0, 0.0])
    assert bool(m.jnt_limited[j.id]) and list(m.jnt_range[j.id]) == pytest.approx([0.0, math.radians(25.0)])
    assert m.jnt_stiffness[j.id] == pytest.approx(14.0)
    assert m.qpos_spring[m.jnt_qposadr[j.id]] == pytest.approx(math.radians(-5.0))
    assert m.body(m.body_parentid[m.body("intake.roller").id]).name == "intake.arm"


def test_the_example_passes_as_saved():
    """It passes, the NOTE made it in (the outcome and the piece table say so), it lifts the front roller's arm off
    its stop on the way in, and the soft guard never fires."""
    prog = load(TEST)
    world = world_for(prog)
    q = world.model.jnt_qposadr[world.model.joint("intake.arm").id]
    lift = [0.0]

    def each(t, row):
        lift[0] = max(lift[0], float(world.data.qpos[q]))

    result = TestRun(world, prog).run(on_sample=each)
    assert result.passed and "1 of 1 made it in" in result.outcome
    row = " ".join(piece_lines(result)[1].split())
    assert row.endswith("in in intake.hopper"), row
    assert result.soft_resets == 0
    assert math.degrees(lift[0]) > ARM_LIFT_DEG


def test_the_example_fails_at_0_volts():
    """The spike's control: with the rollers off, the robot pushes the NOTE ahead of it, and it never gets in."""
    text = TEST.read_text(encoding="utf-8")
    assert text.count("volts = 8.0") == 1           # the spin block's
    prog = loads(text.replace("volts = 8.0", "volts = 0.0"), TEST)
    world = world_for(prog)
    result = TestRun(world, prog).run()
    assert not result.passed
    assert not Watch(world, prog).hopper_mask().any()


BENCH = EXAMPLE.parent.parent / "bench" / "note_intake_flex.toml"


def test_the_bench_flex_intake_passes():
    """The example's nip on the bench (examples/bench/note_intake_flex.toml): the flex NOTE, carried in by a moving
    carpet, goes through into the bin."""
    from scrimmage.bench.report import headlines
    from scrimmage.bench.run import run_bench
    from scrimmage.bench.spec import load_bench

    result = run_bench(load_bench(BENCH))
    assert [(p.status, p.outlet) for p in result.pieces] == [("out", "bin")]
    assert any(line.startswith("1 of 1 left through `bin`") for line in headlines(result)), headlines(result)


RUN_WALL_MAX = 10.0  # choice: s; the spike measured 4.1 s for this run


@pytest.mark.skipif(os.environ.get("SCRIMMAGE_TIMING") != "1", reason="timed locally only: set SCRIMMAGE_TIMING=1")
def test_the_example_runs_in_under_10_s(tmp_path):
    """`scrimmage test run` on the example, start to finish (K6-P6: timing never gates CI)."""
    t0 = time.perf_counter()
    done = subprocess.run([sys.executable, "-m", "scrimmage", "test", "run", str(TEST), "--out", str(tmp_path / "runs")],
                          capture_output=True, text=True, timeout=120)
    wall = time.perf_counter() - t0
    print(f"the CLI run took {wall:.1f} s")
    assert done.returncode == 0, done.stdout + done.stderr
    assert wall < RUN_WALL_MAX


# The grids as shipped (K6 Task 9, 2026-11-13; the README's tables): 44 of 45 one setting at a time (only the 0 V
# control fails), 16 of 16 corners and 40 of 40 random draws. The test gates on the target and on no soft resets, not
# on these counts: a case near the margin flips on a tiny change (Task 1's record had 39 of 40 random draws, before
# the NOTE's damping refit), and CI runs another Python on other CPUs. The counts, the failing cases and the fastest
# segments are printed (pytest -s) so any drift shows in the CI log.
GRID_NAMES = ("oat", "corners", "random")


@pytest.mark.skipif(os.environ.get("SCRIMMAGE_INTAKE_GRID") != "1",
                    reason="about 8 minutes: set SCRIMMAGE_INTAKE_GRID=1 (CI's intake-grid job does)")
def test_the_intake_grids():
    """The intake spike's three grids on the example's own files (K6-R10): the target is met and the soft guard never
    fires. Each grid's table is printed: its runs, its count, its failing cases and its fastest segment."""
    got = {name: intake_grid.run_grid(name) for name in GRID_NAMES}
    for name, rows in got.items():
        print(f"== {name}")
        print("\n".join(intake_grid.table(rows)))
        print("failing:", [(r["label"], r["x"]) for r in rows if not r["passed"]])
    assert intake_grid.meets_target(got["oat"], got["corners"])
    for name, rows in got.items():
        assert [r for r in rows if r["soft_resets"]] == [], name
