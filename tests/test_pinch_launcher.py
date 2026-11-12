"""The face-pinch shooter's analytic model (scrimmage/launcher/pinch.py) on its own, and against the bench.

The geometry is the kickoff dry run's bench file note_shooter.toml (scratchpad kickoff/work/bench): top and bottom
4 in wheels with a 1.25 in gap, 60A tread, a KrakenX60 on each at 60 rps, the 2024 NOTE at 2000 N/m, fed at 2 m/s."""

from __future__ import annotations

import dataclasses
import math
import re
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage import units
from scrimmage.launcher.inputs import Shaft
from scrimmage.launcher.pinch import (PinchConfigError, PinchLauncher, PinchSetup, channel_turn, check_setup,
                                      piece_of, single_shot, status_words)
from scrimmage.testrange.pieces import kind_preset
from scrimmage.testrange.spec import piece_box

IN = 0.0254
NOTE = kind_preset("2024-note")
SHAFT = Shaft(wheel_inertia=0.001, motor="KrakenX60", motor_count=1, gearing=1.0)


def setup(**overrides) -> PinchSetup:
    """note_shooter.toml's pinch: 4 in wheels, a 1.25 in gap, 14 in wide 60A tread, the NOTE at its preset 2000 N/m."""
    base = PinchSetup(piece=piece_of(piece_box(NOTE), NOTE.mass, NOTE.inertia, NOTE.stiffness),
                      bottom_diameter=4 * IN, top_diameter=4 * IN, gap=1.25 * IN, wheel_x=0.25, wheel_width=14 * IN,
                      durometer=60.0, friction=0.7, shafts=(SHAFT, SHAFT), entry_speed=2.0)
    return dataclasses.replace(base, **overrides)


def shot(s: PinchSetup | None = None, rps=(60.0, 60.0), kP: float = 0.5):
    out = single_shot(s or setup(), rps, kP=kP)
    assert out is not None
    return out


def closes(e) -> float:
    """The energy ledger's miss as a fraction of the shafts' work."""
    lhs = e.from_shaft + e.entry_kinetic
    rhs = e.exit_linear + e.exit_spin + e.slip_loss + e.damping_loss
    return abs(lhs - rhs) / e.from_shaft


def test_the_note_is_a_slab_lying_face_parallel():
    """The NOTE's slab: 2 in thick across the gap, 14 in along the path, its pitch inertia the one about a diameter;
    the preset's stiffness is the dry run's 2000 N/m guess."""
    p = setup().piece
    assert p.thickness == pytest.approx(2 * IN) and p.length == pytest.approx(14 * IN)
    assert p.width == pytest.approx(14 * IN)
    assert p.pitch_inertia == pytest.approx(NOTE.inertia[0])
    assert p.stiffness == 2000.0
    # a box 0.1 x 0.3 x 0.02 along body x, y, z: thinnest (z) across the gap, then x along the path, y across
    turn = channel_turn((0.1, 0.3, 0.02))
    assert np.linalg.det(turn) == pytest.approx(1.0)
    np.testing.assert_allclose(np.abs(turn @ [0.0, 0.0, 1.0]), [0.0, 0.0, 1.0])
    np.testing.assert_allclose(np.abs(turn @ [1.0, 0.0, 0.0]), [1.0, 0.0, 0.0])
    box = piece_of((0.1, 0.3, 0.02), 1.0, (1.0, 2.0, 3.0, 0.0, 0.0, 0.0), None)
    assert (box.thickness, box.length, box.width, box.pitch_inertia) == pytest.approx((0.02, 0.1, 0.3, 2.0))


def test_exit_speed_rises_with_wheel_speed():
    speeds = [shot(rps=(r, r)).speed for r in (20.0, 40.0, 60.0, 90.0)]
    assert all(s.status == "launched" for s in [shot(rps=(r, r)) for r in (20.0, 90.0)])
    assert speeds == sorted(speeds) and speeds[0] < 0.7 * speeds[-1]
    # slow wheels grip it out (the piece leaves near their surface speed), fast ones slip
    slow = shot(rps=(20.0, 20.0))
    assert slow.speed == pytest.approx(20.0 * math.pi * 4 * IN, rel=0.05) and slow.exit_slip < 0.05
    assert shot().exit_slip > 0.3


def test_equal_surface_speeds_give_no_spin_and_a_faster_wheel_spins_it():
    """Equal wheels at equal speeds leave no pitch spin; a faster top wheel drags the top face forward, topspin
    (negative backspin), and a faster bottom wheel gives backspin. The spin comes while the slower wheel grips: with
    both faces sliding, each wheel's friction is the full mu N, the two equal and opposite about the piece's middle,
    so 40 and 60 rps (both slipping) spin it hardly at all."""
    assert abs(shot().spin) < 1e-6
    top = shot(rps=(15.0, 25.0))
    bottom = shot(rps=(25.0, 15.0))
    assert top.spin < -5.0 and bottom.spin > 5.0
    assert top.spin == pytest.approx(-bottom.spin, rel=1e-6)     # mirror images
    assert top.speed == pytest.approx(bottom.speed, rel=1e-6)
    assert abs(shot(rps=(40.0, 60.0)).spin) < 0.1


def test_the_energy_ledger_closes():
    for rps in ((60.0, 60.0), (40.0, 70.0), (15.0, 15.0)):
        s = shot(rps=rps)
        assert s.status == "launched"
        assert closes(s.energy) < 0.01, (rps, s.energy)
        assert s.energy.exit_linear == pytest.approx(0.5 * NOTE.mass * s.speed ** 2)


def test_halving_the_substep_changes_little():
    coarse = shot()
    fine = shot(setup(dt=PinchLauncher(setup()).default_dt() / 2))
    assert fine.speed == pytest.approx(coarse.speed, rel=0.01)


def test_a_gap_no_narrower_than_the_piece_does_not_grip():
    s = setup(gap=2.25 * IN)
    out = shot(s)
    assert out.status == "no_contact" and math.isnan(out.speed)
    words = status_words(out, s)
    assert "can't grip" in words and f"gap ({units.fmt(0.05715, 'length')})" in words
    assert f"thickness ({units.fmt(0.0508, 'length')})" in words


def test_a_hard_piece_squeezed_too_tight_jams():
    """A NOTE with no give of its own, squeezed 0.75 in by the rubber alone: the wedge at its leading corners holds it
    off and the wheels stall against it."""
    hard = setup(piece=dataclasses.replace(setup().piece, stiffness=None))
    out = shot(hard)
    assert out.status == "jammed"
    assert "jammed" in status_words(out, hard)
    assert status_words(shot(), setup()) == ""


def test_the_setup_is_checked_in_words():
    s = setup()
    need = s.piece.length / 2.0 + max(s.reach(s.bottom_diameter), s.reach(s.top_diameter))   # 0.2074 m
    with pytest.raises(PinchConfigError, match=r"wheel_x = 0.15 puts the piece between the wheels .* at least "
                       + re.escape(units.fmt(need, "length")) + r" \(half the piece's length, "
                       + re.escape(units.fmt(s.piece.length / 2.0, "length"))):
        check_setup(setup(wheel_x=0.15))
    with pytest.raises(PinchConfigError, match="gap must be positive"):
        check_setup(setup(gap=0.0))


# ------------------------------------------------------------------------------------------------ against the bench

# note_shooter.toml, with the range's own NOTE (the ring preset, built by the range's piece builder) in place of the
# solid disc that stood in for it
BENCH = """
[bench]
name = "note pinch shooter"

[piece]
name = "note"
preset = "2024-note"
shape = "ring"
stiffness = {stiffness}

[shaft.bottom]
motor = "KrakenX60"
target_rps = 60
kP = 0.5
wheel_inertia = 0.001

[shaft.top]
motor = "KrakenX60"
target_rps = 60
kP = 0.5
wheel_inertia = 0.001

[[wheel]]
name = "bottom"
shaft = "bottom"
center_in = [0.0, 0.0, -2.625]
diameter_in = 4.0
width_in = 14.0
tread = "60A"

[[wheel]]
name = "top"
shaft = "top"
center_in = [0.0, 0.0, 2.625]
axis = [0.0, -1.0, 0.0]
diameter_in = 4.0
width_in = 14.0
tread = "60A"

[[plate]]
name = "feed_floor"
center_in = [-12.0, 0.0, -1.25]
size_in = [14.0, 16.0, 0.5]

[[inlet]]
name = "feed"
center_in = [-12.0, 0.0, 0.02]
size = [0.005, 0.005, 0.001]
count = {count}
rate = 2
velocity = [2.0, 0.0, 0.0]

[[outlet]]
name = "shot"
center = [0.6, 0.0, 0.0]
size = [0.2, 0.8, 0.8]

[run]
duration = {duration}
"""


def _bench(tmp_path, stiffness: float = 2000.0, count: int = 1, duration: float = 1.0):
    from scrimmage.bench.spec import load_bench
    path = tmp_path / "note_shooter.toml"
    path.write_text(BENCH.format(stiffness=stiffness, count=count, duration=duration), encoding="utf-8")
    return load_bench(path)


def test_the_benchs_squeeze_is_the_models(tmp_path):
    """The NOTE held still in the bench's pinch (wheels locked, no gravity): MuJoCo makes 16 contact points between a
    wheel and the ring's slices (the corners of a prism face's patch on each of the 4 slices under it), and the bench
    spreads the piece's stiffness over them, so together they carry the model's k c / 2, not 16 times it. The feed
    floor only grazes the ring (its top is the ring's bottom), so only the wheels' contacts are counted."""
    from scrimmage.bench.build import build_bench
    from scrimmage.bench.run import spread_stiffness
    built = build_bench(_bench(tmp_path))
    m, lay = built.model, built.layout
    geom_piece = np.full(m.ngeom, -1)
    geom_piece[list(lay.piece_geom_sets[0])] = 0
    soft = np.array(sorted(lay.contact_tc))
    tc1 = np.array([lay.contact_tc[g] for g in soft])
    m.opt.gravity[:] = 0.0
    d = mujoco.MjData(m)
    piece = m.body("piece:0").id
    qadr, vadr = m.jnt_qposadr[m.body_jntadr[piece]], m.jnt_dofadr[m.body_jntadr[piece]]
    for j in range(m.njnt):
        if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE:
            m.jnt_range[j], m.jnt_limited[j] = (0.0, 0.0), 1
    d.qpos[qadr:qadr + 7] = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
    for _ in range(3000):
        d.qvel[vadr:vadr + 6] *= 0.9
        spread_stiffness(m, d, geom_piece, 1, soft, tc1)
        mujoco.mj_step(m, d)
    per_wheel: dict[str, list[float]] = {}
    for i in range(d.ncon):
        c = d.contact[i]
        bodies = {int(m.geom_bodyid[c.geom1]), int(m.geom_bodyid[c.geom2])}
        if piece in bodies and any(m.body(b).name.startswith("shaft:") for b in bodies):
            f = np.zeros(6)
            mujoco.mj_contactForce(m, d, i, f)
            per_wheel.setdefault(m.body((bodies - {piece}).pop()).name, []).append(float(f[0]))
    launcher = PinchLauncher(setup())
    model_n = launcher.k * setup().compression / 2.0
    assert model_n == pytest.approx(19.05, abs=0.05)
    assert sorted(per_wheel) == ["shaft:bottom", "shaft:top"]
    for forces in per_wheel.values():
        assert len(forces) == 16
        assert sum(forces) == pytest.approx(model_n, rel=0.02)


@pytest.mark.xfail(strict=True, reason="a rigid NOTE on the bench is a rigid-piece limit (docs/bench.md, Known "
                   "limits): it leaves at 7.1 m/s, the model's at 9.6 m/s. Held still they squeeze alike "
                   "(test_the_benchs_squeeze_is_the_models: the bench spreads `stiffness` over its 8 contact points "
                   "per wheel), but the rigid ring's flat-sided slices strike the wheels going in (several hundred N "
                   "against the model's 19 N) and the NOTE rocks about 25 mm, so its wheels slow 46% against the "
                   "model's 4%. The flex NOTE is checked against the model on the spike's geometry in "
                   "test_flex_note_matches_pinch_py")
def test_exit_speed_against_the_bench(tmp_path):
    """The model's exit speed within 15% of the bench's (MuJoCo contact) on note_shooter.toml's geometry."""
    from scrimmage.bench.run import run_bench
    res = run_bench(_bench(tmp_path))
    bench = res.pieces[0].speed
    assert shot().speed == pytest.approx(bench, rel=0.15)


# ------------------------------------------------------------------------------------- the flex NOTE (spec D10)

FLEX_EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "bench" / "note_pinch_flex.toml"
SPIKE_SHAFT = Shaft(wheel_inertia=1.0, motor="KrakenX60", motor_count=1, gearing=1.0)  # source: the spike: held at speed


@pytest.mark.parametrize("rps", [50.0, 83.333], ids=["3000rpm", "5000rpm"])
def test_flex_note_matches_pinch_py(tmp_path, rps):
    """K2's pinch check, on the soft-note spike's geometry (note_pinch_flex.toml): the bench's flex NOTE leaves within
    15 % (spec D10's choice; the spike measured 4-13 %) of pinch.py fed the same squeeze, at 3000 and 5000 rpm. The
    model gives 7.95 m/s at both, the spike's flex 8.38 and 8.53."""
    from scrimmage.bench.run import run_bench
    from scrimmage.bench.spec import load_bench
    text = re.sub(r"(?m)^target_rps = 50\b", f"target_rps = {rps}", FLEX_EXAMPLE.read_text(encoding="utf-8"))
    assert text.count(f"target_rps = {rps}") == 2
    path = tmp_path / "note_pinch_flex.toml"
    path.write_text(text, encoding="utf-8")
    res = run_bench(load_bench(path))
    p = res.pieces[0]
    assert p.status == "out" and res.energy[0].ok
    model = single_shot(PinchSetup(piece=piece_of(piece_box(NOTE), NOTE.mass, NOTE.inertia, 2000.0),
                                   bottom_diameter=4 * IN, top_diameter=4 * IN, gap=1.5 * IN, wheel_x=0.25,
                                   wheel_width=14 * IN, durometer=60.0, friction=0.7,
                                   shafts=(SPIKE_SHAFT, SPIKE_SHAFT), entry_speed=2.0), (rps, rps))
    assert model.speed == pytest.approx(7.95, abs=0.01)
    assert p.speed == pytest.approx(model.speed, rel=0.15)
