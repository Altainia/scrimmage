"""Fitting an axis from a clicked triangle: round faces give a hinge axis, flat faces a slide direction."""

import numpy as np
import pytest

from scrimmage.cad import CadError
from scrimmage.cad.axis import fit_axis
from scrimmage.cad.source import Piece

N = 48


def piece(v, f):
    return Piece("p", "p", (), np.asarray(v, float), np.asarray(f, np.int32))


def ring(r, x, c=(0.0, 0.0)):
    a = np.arange(N) * 2 * np.pi / N
    return np.stack([np.full(N, x), c[0] + r * np.cos(a), c[1] + r * np.sin(a)], 1)


def cylinder(r=0.008, length=0.04, c=(0.01, 0.02)):
    """Closed cylinder along x; side triangles are 0..2N-1, then the two caps."""
    v = np.vstack([ring(r, 0.0, c), ring(r, length, c), [[0, *c], [length, *c]]])
    f = []
    for i in range(N):
        j = (i + 1) % N
        f += [[i, j, N + j], [i, N + j, N + i]]
    for i in range(N):
        j = (i + 1) % N
        f += [[2 * N, j, i], [2 * N + 1, N + i, N + j]]
    return piece(v, f)


def washer(ri=0.008, ro=0.03, t=0.01, c=(0.1, 0.05)):
    """A disc with a through-hole along x; the hole wall is triangles 0..2N-1 (normals point to the axis)."""
    v = np.vstack([ring(ri, 0.0, c), ring(ri, t, c), ring(ro, 0.0, c), ring(ro, t, c)])
    f = []
    for i in range(N):
        j = (i + 1) % N
        f += [[i, N + j, j], [i, N + i, N + j]]                      # hole wall
    for i in range(N):
        j = (i + 1) % N
        f += [[i, j, 2 * N + j], [i, 2 * N + j, 2 * N + i]]            # face at x=0
        f += [[N + i, 3 * N + j, N + j], [N + i, 3 * N + i, 3 * N + j]]  # face at x=t
        f += [[2 * N + i, 2 * N + j, 3 * N + j], [2 * N + i, 3 * N + j, 3 * N + i]]  # outside
    return piece(v, f)


def test_cylinder_side_gives_axis_and_center():
    p = cylinder()
    point, d = fit_axis(p, 5, "round")
    assert abs(abs(d[0]) - 1) < 1e-6 and np.linalg.norm(d) == pytest.approx(1)
    assert np.allclose(point, [0.02, 0.01, 0.02], atol=1e-4)


def test_hole_in_plate_gives_hole_axis():
    p = washer()
    point, d = fit_axis(p, 10, "round")
    assert abs(abs(d[0]) - 1) < 1e-6
    assert np.allclose(point, [0.005, 0.1, 0.05], atol=1e-4)


def test_flat_face_for_round_is_an_error():
    p = cylinder()
    with pytest.raises(CadError, match="isn't round"):
        fit_axis(p, 2 * N + 3, "round")      # a cap


def test_flat_face_direction_and_centroid():
    p = cylinder()
    point, d = fit_axis(p, 2 * N + 3, "flat")
    assert np.allclose(np.abs(d), [1, 0, 0], atol=1e-6)
    assert np.allclose(point, [0.04, 0.01, 0.02], atol=1e-4)


def test_degenerate_triangle_and_bad_index():
    p = piece([[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0]], [[0, 1, 2], [0, 1, 3]])
    for kind in ("round", "flat"):
        with pytest.raises(CadError):
            fit_axis(p, 0, kind)
    with pytest.raises(CadError):
        fit_axis(p, 7, "flat")
    with pytest.raises(CadError):
        fit_axis(p, -1, "round")


def test_single_triangle_for_round_is_an_error_and_never_nan():
    p = piece([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    with pytest.raises(CadError):
        fit_axis(p, 0, "round")
    point, d = fit_axis(p, 0, "flat")
    assert np.all(np.isfinite(point)) and np.all(np.isfinite(d))


def bore(k=6, ri=0.0127 / np.sqrt(3), ro=0.03, t=0.01, c=(0.1, 0.05), turn=0.3):
    """A k-sided plate with a k-sided bore along x (k=6: a 1/2" hex bore, 12.7 mm across its flats). The bore's
    walls are triangles 0..2k-1."""
    def poly(r, x):
        a = turn + np.arange(k) * 2 * np.pi / k
        return np.stack([np.full(k, x), c[0] + r * np.cos(a), c[1] + r * np.sin(a)], 1)
    v = np.vstack([poly(ri, 0.0), poly(ri, t), poly(ro, 0.0), poly(ro, t)])
    f = []
    for i in range(k):
        j = (i + 1) % k
        f += [[i, k + j, j], [i, k + i, k + j]]                      # bore wall
    for i in range(k):
        j = (i + 1) % k
        f += [[i, j, 2 * k + j], [i, 2 * k + j, 2 * k + i]]
        f += [[k + i, 3 * k + j, k + j], [k + i, 3 * k + i, 3 * k + j]]
        f += [[2 * k + i, 2 * k + j, 3 * k + j], [2 * k + i, 3 * k + j, 3 * k + i]]
    return piece(v, f)


@pytest.mark.parametrize("k", [6, 8])
def test_hex_and_octagon_bores_give_their_center(k):
    p = bore(k)
    for tri in range(2 * k):
        point, d = fit_axis(p, tri, "round")
        assert np.allclose(d, [1, 0, 0], atol=1e-6)
        assert np.allclose(point, [0.005, 0.1, 0.05], atol=1e-9)


def test_hex_outside_is_still_not_round_on_one_flat():
    # a hex plate's outside walls are round enough too (they go all the way round), but its end face is flat
    p = bore(6)
    with pytest.raises(CadError, match="isn't round"):
        fit_axis(p, 2 * 6 + 1, "round")      # the face at x=0


def test_a_chamfered_bar_is_not_a_bore():
    """A bar's top with 45-degree chamfers along both edges: its normals all lie across the bar, but they don't go
    round, so it isn't taken for a bore."""
    w, c, L = 0.02, 0.003, 0.1
    prof = [(-w / 2, 0), (-w / 2, c * 2), (-w / 2 + c, c * 3), (w / 2 - c, c * 3), (w / 2, c * 2), (w / 2, 0)]
    v = [[x, y, z] for x in (0.0, L) for (y, z) in prof]
    m = len(prof)
    f = []
    for i in range(m - 1):
        f += [[i, i + 1, m + i + 1], [i, m + i + 1, m + i]]
    p = piece(v, f)
    with pytest.raises(CadError, match="isn't round"):
        fit_axis(p, 4, "round")        # the flat top, between the chamfers
