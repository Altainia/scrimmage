import math

import pytest

from scrimmage.disc.inputs import DiscConfigError, DiscSetup, Layout, WheelSpot, load_setup, parse_setup
from scrimmage.disc.launcher import DiscLauncher, ExitedDisc
from scrimmage.launcher.shaft import VelocityController

INCH = 0.0254
RPM_6000 = 100.0  # wheel rotations per second

# A 2013 blue-roller shooter: 60A 4 in urethane rollers on a CIM geared up 4:3 (wheel free speed 7080 rpm), 1/4 in
# compression, a rubber-taped fence. Arc: one roller inside a curved fence (973's semicircle style). A group's shaft
# carries all its wheels, so `make` gives it ROLLER_INERTIA per wheel.
ROLLER_INERTIA = 2e-4  # estimate: docs/disc.md, Sources, one 4 in roller
ROLLER_SHAFT = {"wheel_inertia": ROLLER_INERTIA, "motor": "CIM", "gearing": 0.75}
PNEUMATIC_SHAFT = {"wheel_inertia": 0.00265, "motor": "CIM", "gearing": 0.75}  # source: CD-PHYS, AndyMark 8 in

BASE = {
    "disc": {"preset": "2013-frisbee"},
    "wheels": {"preset": "urethane-60a-4in"},
    "shaft": ROLLER_SHAFT,
    "control": {"target_rps": RPM_6000, "kP": 0.5},
    "layout": {
        "kind": "arc",
        "wheels": [{"x": 0.3, "side": "right", "group": 0}],
        "fence": "rubber-tape",
        "arc_deg": 180.0,
        "channel_length": 0.45,
        "compression": 0.25 * INCH,
    },
    "feed": {"entry_speed": 1.0},
}

LINEAR = {
    "kind": "linear",
    "wheels": [{"x": x, "side": "right", "group": 0} for x in (0.1, 0.25, 0.4)],
    "fence": "rubber-tape",
    "channel_length": 0.5,
    "compression": 0.25 * INCH,
}

PINCH = {
    "kind": "pinch",
    "wheels": [{"x": 0.1, "side": "right", "group": 0}, {"x": 0.1, "side": "left", "group": 1}],
    "channel_length": 0.3,
    "compression": 0.25 * INCH,
}


def pinch(pairs):
    """A pinch of `pairs` opposed wheel pairs 0.15 m apart, the right wheels group 0, the left group 1."""
    xs = [0.1 + 0.15 * i for i in range(pairs)]
    wheels = [{"x": x, "side": side, "group": g} for x in xs for side, g in (("right", 0), ("left", 1))]
    return {**PINCH, "wheels": wheels, "channel_length": xs[-1] + 0.1}


def linear(n, **changes):
    """A linear channel with n right-hand wheels 0.15 m apart."""
    xs = [0.1 + 0.15 * i for i in range(n)]
    return {**LINEAR, "wheels": [{"x": x, "side": "right", "group": 0} for x in xs], "channel_length": xs[-1] + 0.1,
            **changes}


def make(dt=None, **sections) -> DiscSetup:
    """BASE with changes. Unless [shaft] is given, each group's shaft gets ROLLER_INERTIA per wheel in it."""
    raw = {k: dict(v) for k, v in BASE.items()}
    for name, changes in sections.items():
        if name == "layout_replace":
            raw["layout"] = dict(changes)
        elif isinstance(changes, list):
            raw[name] = changes
        else:
            raw.setdefault(name, {}).update(changes)
    if "shaft" not in sections:
        wheels = raw["layout"]["wheels"]
        per_group = [sum(1 for w in wheels if w.get("group", 0) == g) for g in range(len(wheels))]
        raw["shaft"] = [{**ROLLER_SHAFT, "wheel_inertia": n * ROLLER_INERTIA} for n in per_group if n]
    if dt is not None:
        raw["dt"] = dt
    return parse_setup(raw)


def shoot(setup, voltage=None, external_bus=False, max_time=2.0):
    """Spin each shaft to its target, feed one disc at t = 0 and step until it leaves. With `voltage` set, every
    shaft gets that voltage instead of its velocity controller's. Returns the launcher, the disc and the shafts'
    speeds at the insert."""
    launcher = DiscLauncher(setup, external_bus=external_bus)
    ctrls = [VelocityController(c, s) for c, s in zip(setup.controls, setup.shafts)]
    for shaft, c in zip(launcher.shafts, setup.controls):
        shaft.speed = 2 * math.pi * c.target_rps
    before = [s.speed for s in launcher.shafts]
    assert launcher.insert(0.0)
    assert not launcher.insert(0.0)  # one disc in the channel at a time
    dt = launcher.default_dt()
    out: list[ExitedDisc] = []
    t = 0.0
    while not out and t < max_time:
        volts = [voltage if voltage is not None else c.voltage(s) for c, s in zip(ctrls, launcher.shafts)]
        out = launcher.step(dt, volts)
        t += dt
    assert len(out) == 1
    return launcher, out[0], before


def surface(setup, rps=RPM_6000):
    return 2 * math.pi * rps * setup.wheels.diameter / 2


def advance(setup, d):
    return setup.disc.radius * d.spin / d.speed


# --- setup ---------------------------------------------------------------------------------------------------------


def test_setup_parses_layout_and_groups():
    s = make()
    assert isinstance(s.layout, Layout)
    assert s.layout.wheels == (WheelSpot(x=0.3, side="right", group=0),)
    assert s.layout.fence_friction == pytest.approx(0.8)
    # The arc fence is concentric with the wheel: radius R_w + D - compression.
    assert s.layout.fence_radius == pytest.approx(0.0508 + 0.2794 - 0.25 * INCH)
    assert len(s.shafts) == 1 and len(s.controls) == 1
    p = make(layout_replace=PINCH, shaft=[ROLLER_SHAFT, ROLLER_SHAFT],
             control=[{"target_rps": 100.0, "kP": 0.5}, {"target_rps": 80.0, "kP": 0.5}])
    assert [c.target_rps for c in p.controls] == [100.0, 80.0]
    # One [shaft] table serves every group.
    q = make(layout_replace=PINCH)
    assert len(q.shafts) == 2 and q.shafts[0] == q.shafts[1]


def test_setup_errors_name_the_problem(tmp_path):
    with pytest.raises(DiscConfigError, match="kind"):
        make(layout={"kind": "belt"})
    with pytest.raises(DiscConfigError, match=r"\[\[shaft\]\].*2 wheel groups"):
        make(layout_replace=PINCH, shaft=[ROLLER_SHAFT])
    with pytest.raises(DiscConfigError, match="pinch.*both sides"):
        make(layout_replace={**PINCH, "wheels": [{"x": 0.1, "side": "right", "group": 0}]})
    with pytest.raises(DiscConfigError, match="enters clear"):
        make(layout_replace={**LINEAR, "wheels": [{"x": 0.01, "side": "right", "group": 0}]})
    with pytest.raises(DiscConfigError, match="unknown key 'speed'"):
        make(layout={"speed": 3})
    with pytest.raises(DiscConfigError, match="fence preset"):
        make(layout={"fence": "glass"})
    path = tmp_path / "s.toml"
    path.write_text('[layout]\nkind = "linear"\n', encoding="utf-8")
    with pytest.raises(DiscConfigError, match="missing"):
        load_setup(path)


# --- the shot ------------------------------------------------------------------------------------------------------


# The 2013 range (docs/disc.md, Sources): exit speed 0.35-0.48 of the wheels' no-load surface speed for single-side
# wheel, belt and arc shooters. With the presets' estimates a single 60A roller at 1/4 in compression falls short
# (0.266, arc of 180 deg): its grip, mu k_series c, is about 24 N, and the arc's centripetal load takes the squeeze
# off the wheel as the disc speeds up. The rim stiffness (k_rim 5000 N/m, an estimate) and the compression set it:
# k_rim 10000 gives 0.34, 1/2 in gives 0.34, and the roller's stored energy (its droop) caps it near 0.35 above that.
# Not tuned: k_rim waits for a measurement (docs: two plates and a scale). This flips to a pass when it is in range.
@pytest.mark.xfail(strict=True, reason="presets' k_rim estimate: single 60A roller at 1/4 in gives V/Vs 0.266")
def test_single_wheel_exit_ratio_in_2013_range():
    s = make()
    _, d, _ = shoot(s)
    assert d.status == "launched"
    assert 0.35 <= d.speed / surface(s) <= 0.48


def test_single_wheel_exit_ratio_with_presets():
    """Pins what the presets give for the single-roller arc today (see the xfail above): below the 2013 range,
    above the single-wheel linear case, and rising with the rim stiffness."""
    s = make()
    _, d, _ = shoot(s)
    assert 0.22 <= d.speed / surface(s) <= 0.29
    stiffer = make(disc={"rim_stiffness": 10000.0})
    _, e, _ = shoot(stiffer)
    assert e.speed > 1.2 * d.speed


def test_series_wheels_at_reported_compression_in_2013_range():
    # Four 60A rollers in series at 1/2 in compression (the "about 1/4 in to 1/2 in" T_2 reported for blue 60A
    # rollers, CD-BLUE; "about 1/2 in" standard for linear shooters, CD-FS), 6000 rpm, rubber-taped fence.
    s = make(layout_replace=linear(4, compression=0.5 * INCH))
    _, d, _ = shoot(s)
    assert d.status == "launched"
    assert 0.35 <= d.speed / surface(s) <= 0.48
    assert 0.8 <= advance(s, d) <= 1.1


def test_rolling_launch_advance_ratio_near_one():
    for layout in (BASE["layout"], LINEAR, linear(4, compression=0.5 * INCH)):
        s = make(layout_replace=layout)
        _, d, _ = shoot(s)
        assert d.status == "launched"
        assert 0.8 <= advance(s, d) <= 1.1


def test_slick_fence_does_not_reduce_spin():
    """The plan expected a slick fence (mu 0.15) to give an advance ratio under 0.4. The contact physics says
    otherwise, for any disc. A push F at the rim alone gives r Omega' / v' = m r^2 / I_zz (1.35 here, above 1 since
    I_zz < m r^2), so the far rim slides backwards and the fence's friction pushes it forward. Rolling needs only
    mu_f >= mu_w (m r^2 - I_zz) / (m r^2 + I_zz), 0.12 here. A slicker fence spins the disc faster than rolling and
    launches it slower."""
    no_fence_grip = {k: v for k, v in LINEAR.items() if k != "fence"}
    s = make(layout_replace={**LINEAR, "fence": "ptfe"})
    assert s.layout.fence_friction == pytest.approx(0.15)
    _, d, _ = shoot(s)
    assert d.status == "launched"
    assert 0.95 <= advance(s, d) <= 1.05
    bare = make(layout_replace={**no_fence_grip, "fence_friction": 0.0})
    _, e, _ = shoot(bare)
    disc = bare.disc
    assert advance(bare, e) == pytest.approx(disc.mass * disc.radius**2 / disc.izz, rel=0.1)
    assert e.speed < d.speed


@pytest.mark.parametrize("length", [0.50, 0.55, 0.60, 0.65, 0.70])
def test_exit_guide_removes_lateral_recoil(length):
    """The squeeze stored against the fence throws the disc sideways as the last wheel lets go. The guide walls are
    critically damped and hold the disc until it is clear, so it leaves along the channel at any channel length
    (before, the exit yaw was the phase of a ringing between walls and fence: -5.2 degrees at 0.50 m, +2.7 at 0.65)."""
    import tomllib
    from pathlib import Path
    raw = tomllib.loads((Path(__file__).resolve().parent.parent / "examples" / "disc" / "linear.toml").read_text())
    raw["layout"]["channel_length"] = length
    _, d, _ = shoot(parse_setup(raw))
    assert d.status == "launched" and abs(d.yaw_deg) < 1.0


def test_pinch_equal_speeds_near_zero_spin():
    s = make(layout_replace=PINCH)
    _, d, _ = shoot(s)
    assert d.status == "launched"
    assert abs(advance(s, d)) < 0.05
    # Unequal speeds spin the disc only once it has nearly caught the slower wheel: while both wheels slip forward
    # past it their forces are both mu N, equal, and their torques cancel. Three pairs at 26 and 20 rps get there.
    for right, left, sign in ((26.0, 20.0, 1.0), (20.0, 26.0, -1.0)):
        u = make(layout_replace=pinch(3), control=[{"target_rps": right, "kP": 0.5}, {"target_rps": left, "kP": 0.5}])
        _, e, _ = shoot(u)
        assert e.status == "launched"
        # The right wheel faster drives the right rim ahead: counter-clockwise from above (positive).
        assert sign * e.spin > 0.0
        # The no-slip spin (V_right - V_left) / (2 r), within 30%.
        predicted = (surface(u, right) - surface(u, left)) / u.disc.diameter
        assert 0.7 < e.spin / predicted < 1.1
    # Saturated slip (one pair at 130 and 100 rps): next to no spin despite the difference.
    v = make(layout_replace=PINCH, control=[{"target_rps": 130.0, "kP": 0.5}, {"target_rps": 100.0, "kP": 0.5}])
    _, f, _ = shoot(v)
    assert abs(advance(v, f)) < 0.05


def test_wheel_side_sets_spin_sign():
    for side, sign in (("right", 1.0), ("left", -1.0)):
        layout = {**LINEAR, "wheels": [{"x": x, "side": side, "group": 0} for x in (0.1, 0.25, 0.4)]}
        _, d, _ = shoot(make(layout_replace=layout))
        assert d.status == "launched"
        assert sign * d.spin > 0.0
        assert abs(d.yaw_deg) < 5.0  # it leaves along the channel
    arc_left = {**BASE["layout"], "wheels": [{"x": 0.3, "side": "left", "group": 0}]}
    _, d, _ = shoot(make(layout_replace=arc_left))
    assert d.status == "launched" and d.spin < 0.0


def metered(launcher):
    """Record each shaft's work on its load, from its own speeds (the trapezoid over each step): what the wheels
    gave up to the disc, measured on the shaft side."""
    work = [0.0] * len(launcher.shafts)
    for g, shaft in enumerate(launcher.shafts):
        def step(dt, voltage, load_torque, shaft=shaft, g=g, inner=shaft.step):
            before = shaft.speed
            inner(dt, voltage, load_torque)
            work[g] += load_torque * 0.5 * (before + shaft.speed) * dt
        shaft.step = step
    return work


def test_energy_balance():
    for layout in (BASE["layout"], LINEAR, PINCH):
        s = make(layout_replace=layout)
        launcher = DiscLauncher(s)
        work = metered(launcher)
        ctrls = [VelocityController(c, sh) for c, sh in zip(s.controls, s.shafts)]
        for shaft, c in zip(launcher.shafts, s.controls):
            shaft.speed = 2 * math.pi * c.target_rps
        launcher.insert(0.0)
        out = []
        while not out:
            out = launcher.step(launcher.default_dt(), [c.voltage(sh) for c, sh in zip(ctrls, launcher.shafts)])
        (d,) = out
        assert d.status == "launched"
        e = d.energy
        disc_ke = 0.5 * s.disc.mass * d.speed**2 + 0.5 * s.disc.izz * d.spin**2
        assert e.exit_linear + e.exit_spin == pytest.approx(disc_ke, rel=1e-9)
        gained = disc_ke - e.entry_kinetic
        # The books close: the wheels' work is the disc's gain plus slip, damping and rail losses.
        assert e.from_shaft == pytest.approx(gained + e.slip_loss + e.damping_loss + e.rail_loss, rel=1e-6)
        assert d.slip_energy == e.slip_loss
        for loss in (e.slip_loss, e.rail_loss):
            assert loss >= 0.0
        assert e.damping_loss >= -0.01 * e.from_shaft  # the springs' integration error is small
        # Nothing is created: the shafts' work on their loads (from their own speeds) is what the wheels gave the
        # disc, and it covers the disc's gain and the slip.
        assert sum(work) == pytest.approx(e.from_shaft, rel=1e-3)
        assert sum(work) >= (gained + e.slip_loss) * (1 - 1e-3)


def test_stopped_wheel_does_not_launch():
    s = make(layout_replace=LINEAR, control={"target_rps": 0.0, "kP": 0.0})
    _, d, _ = shoot(s, voltage=0.0)
    assert d.status != "launched"
    assert d.status == "jammed"
    e = d.energy
    assert math.isnan(d.speed) and math.isnan(d.spin)
    assert e.from_shaft <= 1e-6  # a stopped wheel gives nothing (it may take some back)
    assert e.slip_loss >= 0.0 and e.rail_loss >= 0.0


def test_no_compression_is_no_contact():
    s = make(layout_replace={**LINEAR, "compression": 0.0})
    launcher = DiscLauncher(s)
    assert launcher.insert(0.0)
    (d,) = launcher.step(launcher.default_dt(), [0.0])
    assert d.status == "no_contact"
    assert launcher.in_contact() == 0


def test_wheel_droop_reported():
    # Equal surface speed (6000 rpm on 4 in, 3000 rpm on 8 in) and equal compression and fence.
    roller = make(layout_replace=linear(1))
    pneumatic = make(layout_replace=linear(1), wheels={"preset": "pneumatic-8in"}, shaft=PNEUMATIC_SHAFT,
                     control={"target_rps": RPM_6000 / 2})
    assert surface(roller) == pytest.approx(surface(pneumatic, RPM_6000 / 2))
    _, small, _ = shoot(roller)
    _, big, _ = shoot(pneumatic)
    assert small.status == big.status == "launched"
    assert len(small.droop) == 1
    assert small.droop[0] > big.droop[0] > 0.0
    assert small.droop[0] > 0.1  # the light roller loses a good share of its speed
    assert big.droop[0] < 0.5 * small.droop[0]


def test_step_converges():
    # A shot takes 25-60 ms of wall time locally at the 1e-4 s substep (2026-10-29); not asserted, CI runners vary.
    s = make()
    _, d, _ = shoot(s)
    _, half, _ = shoot(make(dt=DiscLauncher(s).default_dt() / 2))
    assert half.speed == pytest.approx(d.speed, rel=0.01)
    assert half.spin == pytest.approx(d.spin, rel=0.01)
    assert d.t_exit - d.t_enter < 0.2


def test_arc_wraps_start_clear_and_create_no_energy():
    # Every wrap the parser accepts (up to a half turn) starts the disc touching only the wheel and the fence's
    # lead-in, unsqueezed, and the pass dissipates (no energy from the walls).
    for wrap in (30.0, 90.0, 150.0, 180.0):
        for side in ("right", "left"):
            s = make(layout={"arc_deg": wrap, "wheels": [{"x": 0.3, "side": side, "group": 0}]})
            launcher = DiscLauncher(s)
            launcher.insert(0.0)
            p = launcher._active
            assert launcher._stored(p.x, p.y) < 1e-12
            _, d, _ = shoot(s)
            assert d.status == "launched"
            assert d.energy.damping_loss >= 0.0
            assert d.t_exit - d.t_enter > 0.05  # it went round, not out early
    with pytest.raises(DiscConfigError, match="arc_deg must be above 0 and at most 180"):
        make(layout={"arc_deg": 181.0})


def test_insert_uses_the_launcher_clock():
    s = make()
    launcher = DiscLauncher(s)
    launcher.step(0.25, [0.0])
    with pytest.raises(ValueError, match="clock"):
        launcher.insert(0.0)
    assert launcher.insert(0.25)
    out = []
    while not out:
        out = launcher.step(launcher.default_dt(), [0.0])
    assert out[0].t_enter == pytest.approx(0.25)
    assert out[0].t_exit > out[0].t_enter


def test_single_group_unchanged_by_the_shared_battery():
    # The exits before the battery was shared (one Battery per ShaftModel), recorded at commit f763539 and re-recorded
    # when the exit guide was made critically damped and run to L + r (arc 8.123 -> 8.489 m/s, 58.05 -> 61.06 rad/s;
    # linear 6.128 -> 6.267 m/s, 43.79 -> 44.88 rad/s): the walls now take the recoil's energy.
    for layout, before in ((BASE["layout"], (8.48881845121076, 61.06169091465963, 38.49512036824422)),
                           (LINEAR, (6.267283298807661, 44.88191417187729, 33.405651102962146))):
        _, d, _ = shoot(make(layout_replace=layout))
        assert (d.speed, d.spin, d.energy.from_shaft) == pytest.approx(before, rel=1e-9)


def test_wheel_groups_share_one_battery():
    # Full command on a two-group pinch draws both groups' current through one battery: it sags more than one group.
    def sag(launcher, volts):
        launcher.step(0.02, volts)  # spin-up from rest: high current
        return launcher.shafts[0].bus_voltage
    s = make(layout_replace=PINCH)
    both = sag(DiscLauncher(s), [12.0, 12.0])
    one = sag(DiscLauncher(s), [12.0, 0.0])
    nominal = s.battery.nominal_voltage
    assert both < one < nominal
    assert nominal - both == pytest.approx(2 * (nominal - one), rel=0.05)


def test_external_bus_reads_the_world_voltage():
    s = make()
    launcher = DiscLauncher(s, external_bus=True)
    assert launcher.battery is None and all(sh.battery is None for sh in launcher.shafts)
    launcher.shafts[0].set_bus_voltage(6.0)
    launcher.step(0.5, [12.0])
    slow = launcher.shafts[0].speed
    full = DiscLauncher(s)
    full.step(0.5, [12.0])
    assert slow < 0.6 * full.shafts[0].speed
