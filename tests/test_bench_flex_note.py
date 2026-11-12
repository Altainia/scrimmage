"""The bench's flex NOTE (spec D9, R15): its mesh, its build against MuJoCo's flexcomp, its fit to `stiffness` and
`restitution`, shipped and cached, and on the bench with `flex = true`: opt-in, its errors, the golden shot and the
report's "experimental" marking and [flight] hand-off."""

from __future__ import annotations

import json
import math
import tomllib
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage import units
from scrimmage.bench import flex_note as fn
from scrimmage.bench import report
from scrimmage.bench.build import PIECE_BITS, TIMESTEP, build_bench
from scrimmage.bench.problems import collect_problems
from scrimmage.bench import run as run_mod
from scrimmage.bench.run import FlexPieces, run_bench
from scrimmage.bench.spec import BenchError, load_bench, parse_bench
from scrimmage.cad import fit as cad_fit
from scrimmage.testrange.pieces import kind_preset
from scrimmage.testrange.spec import passing_width, piece_box
from scrimmage.launcher import flight6, rigid_aero
from scrimmage.launcher.inputs import PRESETS, Air

IN = 0.0254


@pytest.fixture(scope="module")
def note():
    return kind_preset("2024-note")


def test_the_torus_mesh(note):
    big_r, r = fn.ring_radii(note)
    assert big_r == pytest.approx(6 * IN) and r == pytest.approx(1 * IN)
    v, t = fn.torus_tets(*fn.MESH, fn.FLEX_RADIUS, big_r, r)
    assert v.shape == (112, 3) and t.shape == (288, 4)
    vol = fn.tet_volumes(v, t)
    assert (vol > 0).all()
    tube = 2 * math.pi ** 2 * big_r * r ** 2
    assert vol.sum() == pytest.approx(tube, rel=0.05)
    assert np.ptp(v[:, 2]) + 2 * fn.FLEX_RADIUS == pytest.approx(2 * IN, abs=1e-9)


def test_the_flex_is_flexcomps(note):
    """add_flex_note compiles to the same bodies, joints and flex as the spike's <flexcomp> of the same mesh."""
    fit = fn.FlexFit(E=12235.0, beta=0.0025)
    big_r, r = fn.ring_radii(note)
    v, t = fn.torus_tets(*fn.MESH, fn.FLEX_RADIUS, big_r, r)
    f = " ".join
    xml = (f'<mujoco><worldbody><flexcomp name="n" type="direct" dim="3" dof="full" mass="{note.mass}" '
           f'radius="{fn.FLEX_RADIUS}" point="{f(repr(float(x)) for x in v.ravel())}" '
           f'element="{f(str(x) for x in t.ravel())}">'
           f'<contact condim="3" friction="{note.friction} 0.005 0.0001" solref="0.002 1" '
           f'solmix="{fn.FLEX_CONTACT["solmix"]:g}" selfcollide="none"/>'
           f'<elasticity young="{fit.E}" poisson="0.3" damping="{fit.beta}"/></flexcomp></worldbody></mujoco>')
    want = mujoco.MjModel.from_xml_string(xml)
    spec = mujoco.MjSpec()
    assert fn.add_flex_note(spec, "n", (0.0, 0.0, 0.0), note, fit) == "n"
    got = spec.compile()
    assert (got.nbody, got.nv, got.nflex) == (want.nbody, want.nv, want.nflex) == (113, 336, 1)
    names = [a for a in dir(want) if a.startswith(("flex_", "body_", "jnt_", "dof_")) and a != "flex_rgba"]
    for a in names:
        x, y = getattr(want, a), getattr(got, a)
        if isinstance(x, np.ndarray) and x.dtype.kind in "fiub":
            assert np.allclose(x, y, atol=1e-12), a


def test_the_shipped_fit_reproduces_its_squeeze_and_drop(note, monkeypatch):
    def no_scene(*_):
        raise AssertionError("the shipped fit ran a scene")

    monkeypatch.setattr(fn, "squeeze_force", no_scene)
    monkeypatch.setattr(fn, "drop_restitution", no_scene)
    fit = fn.fit_flex(note)
    monkeypatch.undo()
    assert fn.target_force(note) == pytest.approx(12.70, abs=0.005)
    assert fn.squeeze_force(note, fit) == pytest.approx(12.70, rel=0.02)
    assert fn.drop_restitution(note, fit) == pytest.approx(0.3, abs=0.02)


def test_a_new_pair_is_fitted_once_and_cached(note, tmp_path, monkeypatch):
    monkeypatch.setattr(cad_fit, "SHAPED_CACHE", tmp_path)
    kind = replace(note, stiffness=4000.0, restitution=0.4)
    calls = []

    def squeeze(k, fit, **_):        # stand-ins: force rises as E^0.8, e falls as beta rises
        calls.append("squeeze")
        return 12.7 * (fit.E / 12235.0) ** 0.8

    def drop(k, fit, **_):
        calls.append("drop")
        return 0.3 * (0.0025 / fit.beta) ** 0.3

    monkeypatch.setattr(fn, "squeeze_force", squeeze)
    monkeypatch.setattr(fn, "drop_restitution", drop)
    fit = fn.fit_flex(kind)
    assert "squeeze" in calls and "drop" in calls
    assert squeeze(kind, fit) == pytest.approx(fn.target_force(kind), rel=0.02)
    assert abs(drop(kind, fit) - 0.4) <= 0.02
    files = list(tmp_path.rglob("*.json"))
    assert files == [fn.cache_path(kind)]
    assert json.loads(files[0].read_text())["E"] == fit.E

    def no_scene(*_):
        raise AssertionError("the cached fit ran a scene")

    monkeypatch.setattr(fn, "squeeze_force", no_scene)
    monkeypatch.setattr(fn, "drop_restitution", no_scene)
    assert fn.fit_flex(kind) == fit


def test_a_bounce_out_of_reach_is_an_error_not_a_cache(note, tmp_path, monkeypatch):
    monkeypatch.setattr(cad_fit, "SHAPED_CACHE", tmp_path)
    kind = replace(note, stiffness=4000.0, restitution=0.95)
    monkeypatch.setattr(fn, "squeeze_force", lambda k, fit, **_: fn.target_force(k))
    monkeypatch.setattr(fn, "drop_restitution", lambda k, fit, **_: 0.5)       # no beta in the bracket bounces 0.95
    with pytest.raises(BenchError) as e:
        fn.fit_flex(kind)
    assert e.value.code == "flex_fit" and "'2024-note'" in str(e.value) and "0.95" in str(e.value)
    assert not list(tmp_path.rglob("*.json"))


def _stand_ins(monkeypatch, calls):
    """Cheap monotone scenes: force rises as E^0.8, e falls as beta rises."""
    monkeypatch.setattr(fn, "squeeze_force", lambda k, fit, **_: (calls.append("squeeze"), fn.target_force(k)
                                                             * (fit.E / 12235.0) ** 0.8)[1])
    monkeypatch.setattr(fn, "drop_restitution", lambda k, fit, **_: (calls.append("drop"), k.restitution
                                                                * (0.0025 / fit.beta) ** 0.3)[1])


@pytest.mark.parametrize("bad", ['{"E": -1, "beta": NaN}', '{"E": Infinity, "beta": 0.002}',
                                 '{"E": 12000.0, "beta": 0}'])
def test_a_bad_cache_entry_is_fitted_again(note, tmp_path, monkeypatch, bad):
    monkeypatch.setattr(cad_fit, "SHAPED_CACHE", tmp_path)
    kind = replace(note, stiffness=4000.0)
    path = fn.cache_path(kind)
    path.parent.mkdir(parents=True)
    path.write_text(bad)
    calls = []
    _stand_ins(monkeypatch, calls)
    fit = fn.fit_flex(kind)
    assert calls and fit.E > 0 and fit.beta > 0 and math.isfinite(fit.E)
    assert json.loads(path.read_text())["E"] == fit.E


def test_the_shipped_table_is_read_exactly(note, tmp_path, monkeypatch):
    """A bad shipped entry, a near miss of the preset's numbers, or a table for another mesh or timestep is not
    used: each is fitted."""
    monkeypatch.setattr(cad_fit, "SHAPED_CACHE", tmp_path / "cache")
    table = json.loads(fn.SHIPPED_FITS.read_text())
    assert "2000.0_0.3" in table
    calls = []
    _stand_ins(monkeypatch, calls)
    assert fn.fit_flex(replace(note, stiffness=2000.00001)) and calls
    for change in ({"2000.0_0.3": {"E": float("nan"), "beta": 0.0025}}, {"mesh": [24, 8]}, {"timestep": 0.001}):
        shipped = tmp_path / f"fits{len(calls)}.json"
        shipped.write_text(json.dumps({**table, **change}))
        monkeypatch.setattr(fn, "SHIPPED_FITS", shipped)
        calls.clear()
        for f in (tmp_path / "cache").rglob("*.json"):
            f.unlink()
        assert fn.fit_flex(note) and calls, change


def test_a_mujoco_warning_is_a_fit_error_with_no_log_file(note, tmp_path, monkeypatch):
    """At a huge E MuJoCo warns that the flex is ill-conditioned (the spike's warning). The scene raises `flex_fit`
    with MuJoCo's words, MuJoCo's own handler (which writes MUJOCO_LOG.TXT into the working directory) is kept off
    during it and put back after."""
    monkeypatch.chdir(tmp_path)
    before = mujoco.get_mju_user_warning()
    with pytest.raises(BenchError) as e:
        fn.squeeze_force(note, fn.FlexFit(E=1e14, beta=0.0025))
    assert e.value.code == "flex_fit" and "ill-conditioned" in str(e.value)
    assert mujoco.get_mju_user_warning() is before
    assert not (tmp_path / "MUJOCO_LOG.TXT").exists()


def test_a_real_fit_converges(note, tmp_path, monkeypatch):
    """A new pair through the real scenes: a secant step and a few bisection steps (about 6 s), from a scratch
    working directory that is left with no MUJOCO_LOG.TXT."""
    monkeypatch.setattr(cad_fit, "SHAPED_CACHE", tmp_path / "cache")
    monkeypatch.chdir(tmp_path)
    kind = replace(note, stiffness=1200.0, restitution=0.3)
    fit = fn.fit_flex(kind)
    assert fn.cache_path(kind).is_file()
    assert fn.squeeze_force(kind, fit) == pytest.approx(fn.target_force(kind), rel=0.02)
    assert abs(fn.drop_restitution(kind, fit) - 0.3) <= 0.02
    assert not (tmp_path / "MUJOCO_LOG.TXT").exists()


# ---- on the bench: `flex = true` (spec D9, Task 8) ------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
EXAMPLE = REPO / "examples" / "bench" / "note_pinch_flex.toml"
GOLDEN = Path(__file__).resolve().parent / "data" / "bench_flex_golden.json"
RANGE_DIR = Path(__file__).resolve().parent / "data" / "range_2024"
GOLDEN_SPEED = 0.02       # choice: R15, platforms differ in the last digits
GOLDEN_ANGLE_DEG = 0.5    # choice: an exit angle near level, so absolute (2 % of a few degrees is under 0.1)
GOLDEN_SPIN = 0.5         # choice: rad/s, absolute: the spin is a few rad/s of contact noise about zero


def _raw(**overrides) -> dict:
    """note_pinch_flex.toml's tables, with dotted-key overrides (None drops a key)."""
    raw = tomllib.loads(EXAMPLE.read_text(encoding="utf-8"))
    for key, value in overrides.items():
        *path, last = key.split(".")
        table = raw
        for k in path:
            table = table[k] if isinstance(table, dict) else next(e for e in table if e["name"] == k)
        if value is None:
            table.pop(last, None)
        else:
            table[last] = value
    return raw


def test_flex_is_opt_in():
    """Without `flex`, note_pinch_flex.toml is the rigid NOTE on `implicitfast`, with no flex in the model; every
    other example builds with none."""
    rigid = build_bench(parse_bench(_raw(**{"piece.flex": None}), EXAMPLE.parent))
    assert rigid.model.nflex == 0 and rigid.layout.flex_pieces == []
    assert rigid.model.opt.integrator == mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    assert rigid.model.opt.timestep == TIMESTEP["rigid"] and len(rigid.layout.piece_bodies) == 1
    others = [p for p in sorted(EXAMPLE.parent.rglob("*.toml")) if p != EXAMPLE]
    assert len(others) >= 7
    for path in others:
        assert build_bench(load_bench(path)).model.nflex == 0, path


def test_the_flex_takes_the_rigid_pieces_place():
    """With `flex = true`: `discrete` with Newton at FLEX_TIMESTEP, one flex per piece with the rigid piece's
    collision bits, parked below the bounds, and spawned as the rigid piece is (its rest shape in the kind's frame,
    unturned): read back, it is at the spawn point, moving with the spawn's velocity and spin, unturned."""
    built = build_bench(parse_bench(_raw(), EXAMPLE.parent))
    m, lay = built.model, built.layout
    assert m.opt.integrator == mujoco.mjtIntegrator.mjINT_DISCRETE and m.opt.solver == mujoco.mjtSolver.mjSOL_NEWTON
    assert m.opt.timestep == fn.FLEX_TIMESTEP and m.nflex == 1 and lay.flex_pieces == [0]
    assert lay.piece_bodies == [] and lay.contact_tc == {}
    assert (m.flex_contype[0], m.flex_conaffinity[0]) == (PIECE_BITS["rigid"]["contype"],
                                                         PIECE_BITS["rigid"]["conaffinity"])
    pool = FlexPieces(m, lay)
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    assert d.xpos[pool.bodies[0], 2].max() < lay.bounds[0][2]          # parked
    pos, vel, spin = np.array([0.1, 0.02, 0.3]), np.array([2.0, 0.0, 0.5]), np.array([0.0, 0.0, 3.0])
    pool.spawn(d, 0, pos, vel, spin)
    c, v, w, rot = pool.state(d, 0)
    np.testing.assert_allclose(c, pos, atol=1e-12)
    np.testing.assert_allclose(v, vel, atol=1e-12)
    np.testing.assert_allclose(w, spin, atol=1e-9)
    np.testing.assert_allclose(rot, np.eye(3), atol=1e-9)
    # the rest shape spans the rigid NOTE's own box, in the same frame
    kind = kind_preset("2024-note")
    x = pool.base[0] + d.qpos[pool.qadr[0]] - pos
    lo, hi = x.min(axis=0) - fn.FLEX_RADIUS, x.max(axis=0) + fn.FLEX_RADIUS
    np.testing.assert_allclose(hi - lo, piece_box(kind), rtol=0.03)


def test_flex_errors(tmp_path):
    """`flex = true` on a sphere names "ring"; 4 flex pieces in the inlets name the most, 3."""
    raw = _raw(**{"piece.preset": "2022-cargo"})
    with pytest.raises(BenchError, match="ring") as e:
        parse_bench(raw, EXAMPLE.parent)
    assert e.value.key == "piece.flex" and "flex" in str(e.value)
    with pytest.raises(BenchError, match=r"\b4\b.*at most 3\b") as e:
        build_bench(parse_bench(_raw(**{"inlet.feed.count": 4}), EXAMPLE.parent))
    assert e.value.code == "flex_pool"


@pytest.mark.parametrize("overrides, key, words", [
    ({"piece.preset": "2019-hatch-panel"}, "piece.flex", r"round tube bent into a ring .*\(165\.1 mm\), not 4\.8 mm"),
    ({"piece.flex": None, "piece.restitution": 0.5}, "piece.restitution", "read only for a flex piece"),
    ({"piece.restitution": 1.0}, "piece.restitution", "above 0 and below 1"),
    ({"piece.restitution": 0.0}, "piece.restitution", "must be positive"),
    ({"piece.flex": "yes"}, "piece.flex", "must be true or false"),
], ids=["hatch-panel", "restitution-without-flex", "restitution-1", "restitution-0", "flex-not-a-bool"])
def test_flex_parse_errors(overrides, key, words):
    """The flex keys' other refusals: a ring that isn't a round tube (the 2019 HATCH PANEL), `restitution` without
    `flex`, a restitution of 1 or 0, and a `flex` that isn't true or false. Each names its key."""
    with pytest.raises(BenchError, match=words) as e:
        parse_bench(_raw(**overrides), EXAMPLE.parent)
    assert e.value.key == key


def test_the_flex_is_fitted_at_the_runs_timestep(tmp_path, monkeypatch):
    """`[run] timestep` on a flex bench: the flex is fitted at that step (R15: the fit runs at the run's own step) and
    cached under it, not given the shipped fit made at 0.5 ms. The default step reads the shipped fit, and a rigid
    build after a flex one is still `implicitfast` (the two paths keep nothing between them)."""
    monkeypatch.setattr(cad_fit, "SHAPED_CACHE", tmp_path)
    steps = []

    def squeeze(k, fit, timestep):
        steps.append(timestep)
        return fn.target_force(k) * (fit.E / 12235.0) ** 0.8

    monkeypatch.setattr(fn, "squeeze_force", squeeze)
    monkeypatch.setattr(fn, "drop_restitution", lambda k, fit, timestep: k.restitution * (0.0025 / fit.beta) ** 0.3)
    built = build_bench(parse_bench(_raw(**{"run.timestep": 2.5e-4}), EXAMPLE.parent))
    assert built.model.opt.timestep == 2.5e-4 and steps and set(steps) == {2.5e-4}
    kind = replace(kind_preset("2024-note"), stiffness=2000.0, restitution=0.3)
    assert fn.cache_path(kind, 2.5e-4).is_file() and not fn.cache_path(kind).exists()
    steps.clear()
    assert build_bench(parse_bench(_raw(), EXAMPLE.parent)).model.opt.timestep == fn.FLEX_TIMESTEP and not steps
    rigid = build_bench(parse_bench(_raw(**{"piece.flex": None}), EXAMPLE.parent)).model
    assert rigid.opt.integrator == mujoco.mjtIntegrator.mjINT_IMPLICITFAST and rigid.nflex == 0


def test_an_outlet_within_the_flexs_reach_is_warned_about(tmp_path):
    """The exit is read as the flex's center enters the outlet: an outlet within its reach of a wheel would read it
    still squeezed, so `bench check` warns. The example's outlet, 10 in past the axles, is clear."""
    assert not [p for p in collect_problems(EXAMPLE)[1] if p.key == "outlet.shot"]
    text = EXAMPLE.read_text(encoding="utf-8").replace("center_in = [16.0, 0.0, 0.0]", "center_in = [8.0, 0.0, 0.0]")
    text = text.replace("size_in = [12.0, 24.0, 24.0]", "size_in = [4.0, 24.0, 24.0]")
    bench, problems = collect_problems(EXAMPLE, text)
    warned = [p for p in problems if p.key == "outlet.shot"]
    assert bench is not None and len(warned) == 1 and warned[0].level == "warning"
    assert "closer than the flex piece's reach" in warned[0].message


def _shot(result):
    p = result.pieces[0]
    assert p.status == "out" and result.energy[0].ok, (p.status, result.energy)
    return p


def test_the_flex_shot_golden():
    """note_pinch_flex.toml's shot against bench_flex_golden.json (recorded once, R15): speed within 2 %, the exit
    angles (elevation and yaw) within GOLDEN_ANGLE_DEG, spin within GOLDEN_SPIN. A change in MuJoCo's `discrete` integrator shows here.
    Measured when recorded: a 1e-7 m/s change in the feed moves nothing; 1e-5 m/s moves the speed 0.3 %, the angle
    0.04 degrees and the spin 0.3 rad/s, so platforms' last digits sit well inside these."""
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    p = _shot(run_bench(load_bench(EXAMPLE)))
    assert p.speed == pytest.approx(want["speed_m_s"], rel=GOLDEN_SPEED)
    assert p.elevation_deg == pytest.approx(want["elevation_deg"], abs=GOLDEN_ANGLE_DEG)
    assert p.yaw_deg == pytest.approx(want["yaw_deg"], abs=GOLDEN_ANGLE_DEG)
    np.testing.assert_allclose(p.spin, want["spin_rad_s"], atol=GOLDEN_SPIN)


def test_the_report_marks_flex_experimental(tmp_path, monkeypatch):
    """The headlines and the pieces table say "experimental (MuJoCo flex)", the flex line says UNCALIBRATED; in
    `[flight]` to the 2024 SPEAKER the shot is the rigid NOTE's flight from the flex's exit state."""
    raw = _raw()
    raw["flight"] = {"outlet": "shot", "goal": {"range": "2024.toml", "name": "speaker_blue"},
                     "at": [2.5, 5.5446, 0.5], "heading_deg": 180.0}
    result = run_bench(parse_bench(raw, RANGE_DIR))
    p = _shot(result)
    lands = report.landings(result)
    with units.fixed("metric"):
        heads = report.headlines(result, lands)
    flex = [h for h in heads if "experimental (MuJoCo flex)" in h]
    assert len(flex) == 1 and "UNCALIBRATED" in flex[0] and "(2000 N/m)" in flex[0] and "(0.3)" in flex[0]
    # the flex line comes first and takes no outcome's place (M6): the rest are the rigid-labelled report's own six
    # lines, made a full report here by standing in a load line (out, goal, load, shaft, time, UNCALIBRATED)
    rigid = replace(result, bench=replace(result.bench, piece=replace(result.bench.piece, flex=False)))
    with monkeypatch.context() as mp, units.fixed("metric"):
        mp.setattr(report, "_load_line", lambda r: "the `top` takes the hardest hits")
        plain = report.headlines(rigid, lands)
        full = report.headlines(result, lands)
    assert len(plain) == report.MAX_LINES and full == flex + plain and plain[-2].startswith("a piece takes")
    assert any("shots score in `speaker_blue`" in h for h in heads)
    out = report.write_results(result, tmp_path / "out")
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "Pieces: experimental (MuJoCo flex)" in summary and "flown as the rigid piece" in summary
    data = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert data["flex"] is True and data["headlines"] == len(heads)
    rows = (out / "pieces.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].split(",")[-1] == "model" and rows[1].split(",")[-1] == "flex"
    # the hand-off: the exit pose is the flex's center and Kabsch rotation (a few degrees off level as it leaves),
    # and the flight is the rigid NOTE's from that state, to 1e-9
    q = np.array(p.poses[-1][1][3:7])
    assert np.allclose(p.poses[-1][1][:3], p.path[-1][1:]) and abs(q[0]) < 0.9999
    rot = np.zeros(9)
    mujoco.mju_quat2Mat(rot, q / np.linalg.norm(q))
    assert math.degrees(math.acos(rot[8])) == pytest.approx(p.tilt_deg, abs=1e-6)
    # the golden's recorded exit state, as the outcome of this run, lands where the rigid NOTE flown directly from
    # that state (flight6, the kind's own air) lands, to 1e-9
    want = json.loads(GOLDEN.read_text(encoding="utf-8"))
    pos, quat, vel, spin = (np.array(want[k]) for k in ("exit_pos_m", "exit_quat", "exit_velocity_m_s", "spin_rad_s"))
    t_out = want["t_out_s"]
    recorded = replace(p, path=[*p.path[:-1], (t_out, *pos)], speed=want["speed_m_s"],
                       elevation_deg=want["elevation_deg"], yaw_deg=want["yaw_deg"], spin=tuple(spin),
                       poses=[(t_out, (*pos, *quat))])
    (_, flown), = report.landings(replace(result, pieces=[recorded]))
    floor, apex, flight_time = _fly(result.bench, pos, quat, vel, spin)
    assert flown.goal in ("scored", "missed")
    np.testing.assert_allclose(flown.floor, floor, rtol=0, atol=1e-9)
    np.testing.assert_allclose((flown.apex, flown.flight_time), (apex, flight_time), rtol=0, atol=1e-9)


def _fly(bench, pos, quat, vel, spin):
    """The rigid NOTE flown from an exit state with flight6 and the kind's own air, the bench standing at the goal's
    `at`, facing `heading_deg`: (where it lands on the floor, its apex, its flight time), in the bench's floor frame."""
    fl, kind = bench.flight, bench.piece.rigid_kind
    xx, yy, zz, xy, xz, yz = kind.inertia
    inertia = np.array([[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]])
    air = SimpleNamespace(shadow_areas=kind.shadow_areas, drag_coefficient=kind.drag_coefficient,
                          spin_damping=kind.spin_damping)
    force = rigid_aero.force_fn(air, Air(**PRESETS["air"][fl.air]).density)
    h = math.radians(fl.goal.heading_deg)
    turn = np.array([[math.cos(h), -math.sin(h), 0.0], [math.sin(h), math.cos(h), 0.0], [0.0, 0.0, 1.0]])
    origin = np.array([fl.goal.at[0], fl.goal.at[1], 0.0])
    q = quat / np.linalg.norm(quat)
    rot = flight6.matrix_from_quat(q)
    q_field = flight6.quat_mul(np.array([math.cos(h / 2), 0.0, 0.0, math.sin(h / 2)]), q)
    res = flight6.fly(origin + turn @ (pos + [0.0, 0.0, fl.height]), turn @ vel, q_field, rot.T @ spin, kind.mass,
                      inertia, force, floor=passing_width(kind) / 2.0,
                      goals=((fl.goal.name, fl.goal.goal.scoring_surface()),))
    path = (res.samples[:, flight6.POS] - origin) @ turn
    return tuple(path[-1, :2]), float(path[:, 2].max()), res.flight_time


TWO = """
[bench]
name = "two notes"
author = "Jordan"
idea = "drop one soft NOTE onto another"
[piece]
preset = "2024-note"
flex = true
[[plate]]
name = "floor"
center = [0.0, 0.0, -0.01]
size = [1.0, 1.0, 0.02]
[[inlet]]
name = "below"
center = [0.0, 0.0, 0.0265]
size = [0.001, 0.001, 0.001]
count = 1
[[inlet]]
name = "above"
center = [0.25, 0.0, 0.39]
size = [0.001, 0.001, 0.001]
count = 1
[run]
duration = 1.0
jam_time = 0.35
"""


def test_two_flex_notes_touching(monkeypatch):
    """One flex NOTE dropped flat onto another's rim (0.25 m off its center, so it slides off: a flat stack makes
    hundreds of contacts a step and takes about 20 s): no NaN and no MuJoCo warning, both come to rest (jammed;
    `jam_time` outlasts the 0.25 s fall, so the lower one is still there when the upper lands), neither gains energy, and their contact reads the other flex's velocity (rigid_contacts' flex side)."""
    seen = []
    real = run_mod._Run._flex_velocity

    def spy(self, c, side, f):
        v = real(self, c, side, f)
        seen.append((f, v.copy()))
        return v

    monkeypatch.setattr(run_mod._Run, "_flex_velocity", spy)
    r = run_mod._Run(parse_bench(tomllib.loads(TWO), Path(".")), 60.0)
    res = r.run()
    assert [p.status for p in res.pieces] == ["jammed", "jammed"]
    assert sum(w.number for w in r.d.warning) == 0
    assert all(e.ok for e in res.energy), res.energy
    assert {f for f, _ in seen} == {0, 1}
    assert all(np.isfinite(v).all() for _, v in seen) and max(np.linalg.norm(v) for _, v in seen) > 0.5
