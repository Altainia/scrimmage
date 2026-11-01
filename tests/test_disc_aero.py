import math
from pathlib import Path

import numpy as np
import pytest

import scrimmage.disc
from scrimmage.disc.aero import G_HUMMEL, RHO, coefficients, forces, spline_slopes
from scrimmage.disc.inputs import PRESETS, Disc, DiscConfigError, aero_set, disc_preset

GRAVITY = 9.81
EYE = np.eye(3)


def velocity_at(alpha_deg, speed=12.0):
    """A velocity in the body x-z plane at angle of attack alpha (positive: air strikes the underside)."""
    a = math.radians(alpha_deg)
    return np.array([speed * math.cos(a), 0.0, -speed * math.sin(a)])


def test_preset_2013_frisbee_values():
    d = disc_preset("2013-frisbee")
    assert isinstance(d, Disc)
    assert d.diameter == pytest.approx(0.2794, abs=1e-6)
    assert d.mass == pytest.approx(0.180, abs=1e-6)
    assert d.height == pytest.approx(0.0356, abs=1e-6)
    assert d.izz == pytest.approx(0.0026, abs=1e-6)
    assert d.ixx == pytest.approx(0.00135, abs=1e-6)
    assert d.rim_stiffness == pytest.approx(5000.0, rel=1e-9)
    assert 0.0 < d.rim_damping_ratio < 1.0
    with pytest.raises(AttributeError):
        d.mass = 1.0  # frozen
    # The inertia estimate is the Ultra-Star's scaled by m d^2.
    u = disc_preset("ultra-star")
    scale = (d.mass / u.mass) * (d.diameter / u.diameter) ** 2
    assert d.izz == pytest.approx(u.izz * scale, rel=0.01)
    assert d.ixx == pytest.approx(u.ixx * scale, rel=0.01)


def test_ultra_star_is_thesis_table_3_1():
    u = disc_preset("ultra-star")
    assert (u.mass, u.diameter, u.izz, u.ixx) == pytest.approx((0.175, 0.269, 0.00235, 0.00122), rel=1e-9)


def test_every_preset_value_is_tagged_source_or_estimate():
    text = Path(scrimmage.disc.__file__).with_name("presets.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#") and not line.lstrip().startswith("["):
            assert "# source:" in line or "# estimate:" in line, line
    assert {"2013-frisbee", "ultra-star"} <= set(PRESETS["disc"])
    assert {"pc-long", "hummel-short", "hh02", "none"} <= set(PRESETS["aero"])


def test_unknown_preset_names_the_valid_ones():
    with pytest.raises(DiscConfigError, match="2013-frisbee"):
        disc_preset("frisbee")
    with pytest.raises(DiscConfigError, match="pc-long"):
        aero_set("pc")


def test_aero_sets_carry_the_thesis_damping():
    pc = aero_set("pc-long")
    assert (pc.cmq, pc.crp, pc.cnr) == pytest.approx((-0.0144, -0.0125, -3.41e-5), rel=1e-9)
    hs = aero_set("hummel-short")
    assert (hs.cl0, hs.cla, hs.cd0, hs.cda, hs.cm0, hs.cma, hs.crr) == pytest.approx(
        (0.3331, 1.9124, 0.1769, 0.685, -0.0821, 0.4338, 0.00171), rel=1e-9)
    assert (hs.cmq, hs.crp, hs.cnr) == pytest.approx((-0.005, -0.0055, -7.1e-6), rel=1e-9)
    hh = aero_set("hh02")
    assert (hh.cl0, hh.cla, hh.cd0, hh.cda, hh.cm0, hh.cma) == pytest.approx(
        (0.188, 2.37, 0.15, 1.24, -0.06, 0.38), rel=1e-9)


def test_spline_is_matlab_not_a_knot():
    # A not-a-knot spline reproduces any cubic exactly (MATLAB interp1 'spline').
    x = np.array([-1.0, -0.3, 0.0, 0.4, 1.1, 2.0])
    m = spline_slopes(x) @ (x**3 - 2 * x)
    assert m == pytest.approx(3 * x**2 - 2, abs=1e-9)


def test_pc_tables_reproduce_tabulated_points():
    pc = aero_set("pc-long")
    c = lambda deg, adv=0.0: coefficients(pc, math.radians(deg), adv)  # noqa: E731
    assert c(10)["CL"] == pytest.approx(0.725, abs=1e-3)
    assert c(-10)["CL"] == pytest.approx(-0.225, abs=1e-3)
    assert c(30)["CL"] == pytest.approx(1.675, abs=1e-3)
    assert c(0)["CD"] == pytest.approx(0.100, abs=1e-4)
    assert c(-3)["CD"] == pytest.approx(0.080, abs=1e-3)
    assert c(20)["CD"] == pytest.approx(0.570, abs=1e-3)
    assert c(9)["CM"] == pytest.approx(0.0, abs=1e-3)
    assert c(-10)["CM"] == pytest.approx(-0.038, abs=1e-3)
    assert c(30)["CM"] == pytest.approx(0.110, abs=1e-3)
    # C_Rr(alpha, AdvR) knots (thesis p. 83), at the code's radian grid.
    rr = lambda rad, adv: coefficients(pc, rad, adv)["CRr"]  # noqa: E731
    assert rr(0.0, 2.0) == pytest.approx(-0.0172, abs=1e-6)
    assert rr(0.0, 1.04) == pytest.approx(-0.0112, abs=1e-6)
    assert rr(0.0873, 2.0) == pytest.approx(-0.0136, abs=1e-6)
    assert rr(0.1745, 0.69) == pytest.approx(0.0, abs=1e-6)
    assert rr(0.2269, 0.35) == pytest.approx(0.0028, abs=1e-6)
    assert rr(0.5236, 0.17) == pytest.approx(-0.003, abs=1e-6)
    assert rr(-0.0873, 0.0) == pytest.approx(-0.0012, abs=1e-6)


def test_zero_lift_angle_near_minus_4_deg():
    # The P&C table puts zero lift at its -3 deg point (thesis p. 82); the linear fits put it at -3.9 (P&C) and
    # -4.5 (HH02) deg. The brief's [-4.5, -3.5] window is the linear-fit value; the table's own zero is -3.
    pc = aero_set("pc-long")
    lo, hi = math.radians(-6), math.radians(0)
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if coefficients(pc, mid, 0.0)["CL"] < 0 else (lo, mid)
    assert math.degrees(lo) == pytest.approx(-3.0, abs=0.05)
    hh = aero_set("hh02")
    a0 = -hh.cl0 / hh.cla
    assert coefficients(hh, a0, 0.0)["CL"] == pytest.approx(0.0, abs=1e-9)
    assert -4.6 < math.degrees(a0) < -4.4


def test_beyond_the_tables_stall_flat_plate_and_bounded_moment():
    for name in ("pc-long", "hummel-short", "hh02"):
        aero = aero_set(name)
        deg = np.arange(-90.0, 90.01, 0.5)
        cl = np.array([coefficients(aero, math.radians(a), 1.0)["CL"] for a in deg])
        cd = np.array([coefficients(aero, math.radians(a), 1.0)["CD"] for a in deg])
        cm = np.array([coefficients(aero, math.radians(a), 1.0)["CM"] for a in deg])
        crr = np.array([coefficients(aero, math.radians(a), 1.0)["CRr"] for a in deg])
        assert 40.0 <= deg[np.argmax(cl)] <= 50.0, name  # stall near 45 deg
        assert cd[-1] == pytest.approx(1.17, abs=1e-6) and cd[0] == pytest.approx(1.17, abs=1e-6)
        assert np.all(cd > 0.0)
        assert np.all(cd <= 1.17 + 1e-9), name  # drag rises to the flat plate without overshoot
        # The lift, pitching and roll coefficients vanish edge-on to the flow, where frame F flips.
        for c in (cl, cm, crr):
            assert c[0] == pytest.approx(0.0, abs=1e-9) and c[-1] == pytest.approx(0.0, abs=1e-9)
        assert np.max(np.abs(cm)) < 0.3, name
    # C_Rr stays within the table's own range everywhere, including the table's 15-30 deg gap.
    pc = aero_set("pc-long")
    for adv in (-2.5, -1.04, -0.3, 0.0, 0.17, 0.5, 1.04, 1.5, 2.0, 3.0):
        crr = [coefficients(pc, math.radians(a), adv)["CRr"] for a in np.arange(-90.0, 90.01, 0.5)]
        # The table's largest is 0.0192; a reversed spin mirrors about the zero-spin row (at most 0.0028 here).
        assert np.max(np.abs(crr)) <= 0.0192 + 2 * 0.0028, adv


def test_forces_finite_and_continuous_over_all_alpha():
    disc = disc_preset("2013-frisbee")
    omega = np.array([1.5, -2.0, 60.0])
    qad = 0.5 * RHO * 12.0**2 * math.pi * disc.diameter**2 / 4  # q A, about 5.4 N
    for name in ("pc-long", "hummel-short", "hh02"):
        aero = aero_set(name)
        # Sweep the velocity all the way round in the body x-z plane: alpha passes +-90 deg and frame F flips there.
        prev = None
        for th in np.arange(-180.0, 180.01, 0.5):
            t = math.radians(th)
            v = 12.0 * np.array([math.cos(t), 0.0, math.sin(t)])
            f, tau = forces(disc, aero, RHO, v, EYE, omega)
            assert np.all(np.isfinite(f)) and np.all(np.isfinite(tau))
            if prev is not None:
                assert np.linalg.norm(f - prev[0]) < 0.05 * qad, (name, th)
                assert np.linalg.norm(tau - prev[1]) < 0.01 * qad * disc.diameter, (name, th)
            prev = (f, tau)


def test_lift_is_perpendicular_to_velocity_and_drag_opposes_it():
    disc = disc_preset("2013-frisbee")
    aero = aero_set("pc-long")
    rng = np.random.default_rng(3)
    for _ in range(20):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        w, x, y, z = q
        rot = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])
        v = rng.normal(size=3) * 6.0
        f, _ = forces(disc, aero, RHO, v, rot, np.zeros(3))
        vhat = v / np.linalg.norm(v)
        z_disc = rot[:, 2]
        alpha = math.asin(-float(vhat @ z_disc))
        c = coefficients(aero, alpha, 0.0)
        qa = 0.5 * RHO * float(v @ v) * math.pi * disc.diameter**2 / 4
        assert float(f @ vhat) == pytest.approx(-c["CD"] * qa, rel=1e-9)  # drag opposes v
        lift = f - float(f @ vhat) * vhat
        assert np.linalg.norm(lift) == pytest.approx(abs(c["CL"]) * qa, rel=1e-9, abs=1e-12)
        # Lift lies in the plane of v and the disc axis, on the disc's top side when C_L > 0.
        assert float(lift @ np.cross(vhat, z_disc)) == pytest.approx(0.0, abs=1e-9)
        if c["CL"] > 0.01:
            assert float(lift @ z_disc) > 0.0


def test_frame_signs_alpha_lift_pitch_and_spin_down():
    disc = disc_preset("2013-frisbee")
    aero = aero_set("pc-long")
    # A flat disc moving forward and slightly down meets the air on its underside: positive alpha, lift up.
    f, tau = forces(disc, aero, RHO, velocity_at(5.0), EYE, np.zeros(3))
    assert f[2] > 0.0
    # At alpha 0 the P&C C_M is -0.006, nose-down: x (forward) turns toward -z, a positive torque about body y.
    _, tau = forces(disc, aero, RHO, velocity_at(0.0), EYE, np.zeros(3))
    assert tau[1] > 0.0
    # Spin-down opposes the spin (Hummel's N = C_Nr r, no q A d factor).
    _, tau = forces(disc, aero, RHO, velocity_at(0.0), EYE, np.array([0.0, 0.0, 50.0]))
    assert tau[2] == pytest.approx(-3.41e-5 * 50.0, rel=1e-9)


def test_roll_moment_from_spin_mirrors_with_the_spin():
    pc = aero_set("pc-long")
    for deg in (-5.0, 0.0, 7.0, 20.0):
        a = math.radians(deg)
        base = coefficients(pc, a, 0.0)["CRr"]
        up = coefficients(pc, a, 1.0)["CRr"] - base
        down = coefficients(pc, a, -1.0)["CRr"] - base
        assert down == pytest.approx(-up, abs=1e-12)
    # The linear sets: the roll term sqrt(d/g) C_Rr r is odd in the spin.
    disc = disc_preset("2013-frisbee")
    hs = aero_set("hummel-short")
    v = velocity_at(0.0)
    _, t1 = forces(disc, hs, RHO, v, EYE, np.array([0.0, 0.0, 40.0]))
    _, t2 = forces(disc, hs, RHO, v, EYE, np.array([0.0, 0.0, -40.0]))
    qad = 0.5 * RHO * 144.0 * math.pi * disc.diameter**2 / 4 * disc.diameter
    # Hummel's c3 is the disc's underside axis, so his spin is -omega_z; the roll moment is about x_F (+x here).
    assert t1[0] == pytest.approx(qad * math.sqrt(disc.diameter / G_HUMMEL) * 0.00171 * -40.0, rel=1e-9)
    assert t2[0] == pytest.approx(-t1[0], rel=1e-9)


def test_zero_spin_has_no_division_by_zero():
    disc = disc_preset("2013-frisbee")
    for name in ("pc-long", "hummel-short", "hh02", "none"):
        aero = aero_set(name)
        for v in (np.zeros(3), np.array([0.0, 0.0, -12.0]), np.array([0.0, 0.0, 1e-12]), velocity_at(3.0)):
            f, tau = forces(disc, aero, RHO, v, EYE, np.zeros(3))
            assert np.all(np.isfinite(f)) and np.all(np.isfinite(tau)), (name, v)
    f, tau = forces(disc, aero_set("pc-long"), RHO, np.zeros(3), EYE, np.array([0, 0, 50.0]))
    assert np.all(f == 0.0) and np.all(tau == 0.0)


def test_none_set_is_exactly_zero():
    disc = disc_preset("2013-frisbee")
    f, tau = forces(disc, aero_set("none"), RHO, velocity_at(37.0), EYE, np.array([3.0, -1.0, 50.0]))
    assert np.all(f == 0.0) and np.all(tau == 0.0)


def test_lift_at_12_mps_alpha0_is_about_half_weight():
    disc = disc_preset("2013-frisbee")
    f, _ = forces(disc, aero_set("pc-long"), RHO, velocity_at(0.0), EYE, np.zeros(3))
    assert 0.4 < f[2] / (disc.mass * GRAVITY) < 0.7


def _hummel_disc_flt_eom_aero(state, aero, d, area, rho):
    """A literal transcription of the aerodynamics in Hummel's discfltEOM.m (HUM03 pp. 88-90), in his frames: N is
    forward, right, down; C is the disc frame whose c3 points out of the underside. Returns (F in N, M in C)."""
    vel, f, th, fd, thd, gd = state
    st, ct, sf, cf = math.sin(th), math.cos(th), math.sin(f), math.cos(f)
    t_c_n = np.array([[ct, st * sf, -st * cf], [0, cf, sf], [st, -ct * sf, ct * cf]])
    c3 = t_c_n[2]
    vmag = np.linalg.norm(vel)
    vc3 = vel @ c3
    vp = vel - vc3 * c3
    alpha = math.atan(vc3 / np.linalg.norm(vp))
    adp = area * rho * vmag * vmag / 2
    uvel = vel / vmag
    uvp = vp / np.linalg.norm(vp)
    ulat = np.cross(c3, uvp)
    omega_c = np.array([fd * ct, thd, fd * st + gd])
    omega_n = t_c_n.T @ omega_c
    omegavp, omegalat, omegaspin = omega_n @ uvp, omega_n @ ulat, omega_n @ c3
    if aero.form == "linear":
        cl = aero.cl0 + aero.cla * alpha
        alphaeq = -aero.cl0 / aero.cla
        cd = aero.cd0 + aero.cda * (alpha - alphaeq) ** 2
        cm = aero.cm0 + aero.cma * alpha
        mvp = adp * d * (math.sqrt(d / G_HUMMEL) * aero.crr * omegaspin + aero.crp * omegavp) * uvp
    else:  # the table lookups are checked elsewhere; take them from coefficients()
        c = coefficients(aero, alpha, d * omegaspin / 2 / vmag)
        cl, cd, cm = c["CL"], c["CD"], c["CM"]
        mvp = adp * d * (c["CRr"] + aero.crp * omegavp) * uvp  # App. A form (p. 86)
    ulift = -np.cross(uvel, ulat)
    faero = cl * adp * ulift - cd * adp * uvel
    mlat = adp * d * (cm + aero.cmq * omegalat) * ulat
    mspin = np.array([0, 0, aero.cnr * omegaspin])
    return faero, t_c_n @ mvp + t_c_n @ mlat + mspin, t_c_n, omega_c


def test_matches_hummels_code_in_his_frames():
    # Hummel's N (forward, right, down) is the world (forward, left, up) under P = diag(1, -1, -1). His disc frame C
    # maps to the body as x = c1, y = -c2, z = -c3 (c3 out of the underside; body z out of the top).
    disc = disc_preset("ultra-star")
    p = np.diag([1.0, -1.0, -1.0])
    rng = np.random.default_rng(7)
    for name in ("hummel-short", "hh02", "pc-long"):
        aero = aero_set(name)
        for _ in range(25):
            vel = np.array([13.0, 0.0, 0.0]) + rng.uniform(-0.5, 0.5, size=3)
            f, th = rng.uniform(-0.08, 0.08, size=2)  # small angles: |alpha| < 9 deg, inside every core range
            fd, thd = rng.normal(size=2) * 5.0
            gd = rng.uniform(5.0, 54.0)  # Hummel's spin sense, AdvR below 1.04
            state = (vel, f, th, fd, thd, gd)
            f_n, m_c, t_c_n, omega_c = _hummel_disc_flt_eom_aero(state, aero, disc.diameter, disc.area, RHO)
            rot = np.column_stack([p @ t_c_n[0], -(p @ t_c_n[1]), -(p @ t_c_n[2])])
            omega_body = omega_c * [1.0, -1.0, -1.0]
            force, torque = forces(disc, aero, RHO, p @ vel, rot, omega_body)
            assert force == pytest.approx(p @ f_n, rel=1e-9, abs=1e-12), name
            assert torque == pytest.approx(m_c * [1.0, -1.0, -1.0], rel=1e-9, abs=1e-12), name
