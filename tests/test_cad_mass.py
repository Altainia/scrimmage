"""scrimmage.cad names, materials and mesh mass properties."""

import sys
from pathlib import Path

import numpy as np
import pytest

from scrimmage.cad import CadError, MISSING_EXTRA
from scrimmage.cad.mass import (Inertial, combine, density, is_closed, mesh_inertial, principal, rpy_deg)
from scrimmage.cad.names import sanitize, unique

ADDON = Path(__file__).resolve().parent.parent / "freecad" / "Scrimmage"

# outward-facing triangles of a unit cube's 8 corners (index = x + 2y + 4z)
_FACES = [(0, 2, 3), (0, 3, 1), (4, 5, 7), (4, 7, 6), (0, 1, 5), (0, 5, 4),
          (2, 6, 7), (2, 7, 3), (0, 4, 6), (0, 6, 2), (1, 3, 7), (1, 7, 5)]


def box_mesh(size, center=(0, 0, 0)):
    corners = np.array([[x, y, z] for z in (0, 1) for y in (0, 1) for x in (0, 1)], float)
    v = (corners - 0.5) * np.array(size) + np.array(center)
    return v, np.array(_FACES)


def test_box_volume_com_inertia():
    inert = mesh_inertial(*box_mesh((0.3, 0.2, 0.1), center=(1, 2, 3)), 2700.0)
    assert inert.mass == pytest.approx(0.006 * 2700)
    assert inert.com == pytest.approx([1, 2, 3])
    assert inert.tensor[0, 0] == pytest.approx(inert.mass * (0.2**2 + 0.1**2) / 12)
    assert abs(inert.tensor[0, 1]) < 1e-12


def test_inside_out_box_weighs_the_same():
    v, f = box_mesh((0.3, 0.2, 0.1), center=(1, 2, 3))
    good = mesh_inertial(v, f, 2700.0)
    flipped = mesh_inertial(v, f[:, ::-1], 2700.0)
    assert flipped.mass == pytest.approx(good.mass)
    assert flipped.com == pytest.approx(good.com)
    assert flipped.tensor == pytest.approx(good.tensor, abs=1e-9)


def test_open_mesh_has_no_mass():
    v, f = box_mesh((1, 1, 1))
    f = f[:-1]
    assert not is_closed(f)
    assert is_closed(box_mesh((1, 1, 1))[1])
    assert mesh_inertial(v, f, 2700.0).mass == 0


def test_combine_two_boxes_parallel_axis():
    cube = lambda c: mesh_inertial(*box_mesh((1, 1, 1), center=c), 1.0)
    one = cube((0, 0, 0))
    both = combine([one, cube((1, 0, 0))])
    assert both.mass == pytest.approx(2)
    assert both.com == pytest.approx([0.5, 0, 0])
    assert both.tensor[1, 1] == pytest.approx(2 * one.tensor[1, 1] + 2 * 0.5**2)
    assert both.tensor[0, 0] == pytest.approx(2 * one.tensor[0, 0])


def test_principal_and_rpy_match_freecad_addon():
    sys.path.insert(0, str(ADDON))
    try:
        from scrimmage_part import mass as addon_mass
    finally:
        sys.path.remove(str(ADDON))
    rng = np.random.default_rng(7)
    for _ in range(20):
        m = rng.normal(size=(3, 3))
        t = m @ m.T + 0.1 * np.eye(3)
        w, r = principal(t)
        aw, ar = addon_mass.principal(t.tolist())
        assert w == pytest.approx(aw)
        assert np.linalg.det(r) == pytest.approx(1)
        assert r @ np.diag(w) @ r.T == pytest.approx(t)
        # eigenvector signs are free, so compare rpy_deg on the same rotation (the add-on's) and each R on its own
        assert rpy_deg(ar) == pytest.approx(addon_mass.rpy_deg(ar), abs=1e-6)
        assert np.array(ar) @ np.diag(aw) @ np.array(ar).T == pytest.approx(t)


def test_printed_density():
    assert density("pla", 0.4) == pytest.approx(1240 * (0.25 + 0.3))
    assert density("petg") == pytest.approx(1270 * (0.25 + 0.75 * 0.4))
    assert density("aluminum") == 2700
    assert density("custom", custom=1000) == 1000


def test_custom_needs_positive_density():
    with pytest.raises(CadError, match="density"):
        density("custom")
    with pytest.raises(CadError):
        density("unobtainium")


def test_sanitize_and_unique():
    assert sanitize("Hook (Left) Ø8") == "Hook_Left_8"
    assert sanitize("8020") == "p_8020"
    assert sanitize("") == "piece"
    used = set()
    assert [unique("a", used) for _ in range(3)] == ["a", "a_2", "a_3"]


def test_missing_extra_message():
    assert MISSING_EXTRA == 'Reading CAD files needs the cad extra: pip install "scrimmage[cad]"'
