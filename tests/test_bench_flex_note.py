"""The bench's flex NOTE (spec D9, R15): its mesh, its build against MuJoCo's flexcomp, and its fit to `stiffness` and
`restitution`, shipped and cached."""

from __future__ import annotations

import json
import math
from dataclasses import replace

import mujoco
import numpy as np
import pytest

from scrimmage.bench import flex_note as fn
from scrimmage.bench.spec import BenchError
from scrimmage.cad import fit as cad_fit
from scrimmage.testrange.pieces import kind_preset

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

    def squeeze(k, fit):        # stand-ins: force rises as E^0.8, e falls as beta rises
        calls.append("squeeze")
        return 12.7 * (fit.E / 12235.0) ** 0.8

    def drop(k, fit):
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
    monkeypatch.setattr(fn, "squeeze_force", lambda k, fit: fn.target_force(k))
    monkeypatch.setattr(fn, "drop_restitution", lambda k, fit: 0.5)       # no beta in the bracket bounces 0.95
    with pytest.raises(BenchError) as e:
        fn.fit_flex(kind)
    assert e.value.code == "flex_fit" and "'2024-note'" in str(e.value) and "0.95" in str(e.value)
    assert not list(tmp_path.rglob("*.json"))


def _stand_ins(monkeypatch, calls):
    """Cheap monotone scenes: force rises as E^0.8, e falls as beta rises."""
    monkeypatch.setattr(fn, "squeeze_force", lambda k, fit: (calls.append("squeeze"), fn.target_force(k)
                                                             * (fit.E / 12235.0) ** 0.8)[1])
    monkeypatch.setattr(fn, "drop_restitution", lambda k, fit: (calls.append("drop"), k.restitution
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
