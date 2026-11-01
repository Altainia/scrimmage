"""Stock parts against closed-form results, driven through World with PWM IDs (no robot program)."""

import math

import numpy as np

import pytest

from scrimmage.motors import get_motor
from scrimmage.protocol import HalState
from scrimmage.world import World

BASE = {"name": "base", "stock": "chassis", "mass": 200.0}      # heavy, so mechanisms don't rock it
# A stiff battery: a stalled Kraken draws ~366 A, and the default 0.02 ohm would sag the voltage the tests use.
# No obstacle: a robot driven flat out for seconds would hit it.
TEST_WORLD = {"battery": {"nominal_voltage": 12.0, "resistance": 0.0}, "field": {"obstacle": False}}


def world(*parts, start=(2.0, 4.0)):
    return World({"part": list(parts), "robot": {"start_pose": [*start, 0.0]}, **TEST_WORLD}, None)


def drive(w, seconds, **pwm):
    hal = HalState()
    for port, speed in pwm.items():
        hal.devices[("PWM", port.removeprefix("p"))] = {"<init": True, "<speed": speed}
    for _ in range(int(seconds / w.model.opt.timestep)):
        w.step(hal)


@pytest.mark.parametrize("stock, given, moi, gearing", [
    ("flywheel", {"moi": 0.018}, 0.018, 3.0),           # gearing 3: the motor sees the wheel's inertia / 9
    ("flywheel", {}, 0.5 * 0.5 * 0.0508 ** 2, 2.0),     # default solid disc, 1/2 m r^2
    ("roller", {"diameter": 0.1, "mass": 1.0}, 0.5 * 1.0 * 0.05 ** 2, 2.0),
])
def test_spinner_spin_up_matches_the_dc_motor_time_constant(stock, given, moi, gearing):
    w = world(BASE, {"name": "fw", "stock": stock, "attach": "base.top", "gearing": gearing, **given,
                     "ids": {"spin": "PWM:0"}})
    m = get_motor("KrakenX60")
    # wheel: J dw/dt = g Kt (V - g w / Kv) / R   ->   tau = J R Kv / (g^2 Kt), w_free = Kv V / g
    tau = moi * m.R * m.Kv / (gearing ** 2 * m.Kt)
    drive(w, tau, p0=1.0)
    speed = w.joint_states()["fw.spin"][1]
    free = m.Kv * w.vbatt / gearing
    assert speed == pytest.approx(free * (1 - math.exp(-1)), rel=0.08)


def q_set(w, joint, value):
    w.data.qpos[w.model.jnt_qposadr[w.model.joint(joint).id]] = value


def carriage_height(w, name="e"):
    """Absolute carriage height above the elevator base, from the body positions."""
    return float(w.data.xpos[w.model.body(f"{name}.carriage").id][2] - w.data.xpos[w.model.body(f"{name}.base").id][2])


STAGE_MASS = 1.5            # the stock default


def lift_world(rigging, load, stages=3, binding=None):
    ids = {"lift": "PWM:0"}
    if binding:
        ids["encoder"] = binding
    return world(BASE, {"name": "e", "stock": "elevator", "attach": "base.top", "stages": stages, "rigging": rigging,
                        "travel": 1.2, "count": 1, "gearing": 20.0, "spool_radius": 0.03, "carriage_mass": load,
                        "stage_mass": STAGE_MASS, "friction": 0.0, "ids": ids})


def stall_load(rigging, stages=3):
    """Carriage mass at the stall boundary: virtual work on the first stage's travel q. The stages sit at
    q, 2q, ... (n-1)q and the carriage at nq; the rope force on q is F0 (cascade) or n F0 (continuous), with
    F0 = stall torque * gearing / spool radius, so m_carriage = F0 / (g n or g) - stage_mass (n-1) / 2."""
    m = get_motor("KrakenX60")
    f0 = m.stall_torque * 1 * 20 / 0.03
    return f0 / 9.81 / (stages if rigging == "cascade" else 1) - STAGE_MASS * (stages - 1) / 2


def mid_height_run(rigging, load, duty=1.0, seconds=0.5):
    """Start every stage mid-travel, drive, and return (start, end) of the carriage height."""
    w = lift_world(rigging, load)
    for j in ("stage1", "stage2", "carriage"):
        q_set(w, f"e.{j}", 0.2)
    mujoco_forward(w)
    start = carriage_height(w)
    drive(w, seconds, p0=duty)
    return start, carriage_height(w)


def mujoco_forward(w):
    import mujoco
    mujoco.mj_forward(w.model, w.data)


@pytest.mark.parametrize("rigging", ["continuous", "cascade"])
def test_default_elevator_is_numerically_stable(rigging):
    import mujoco
    for duty in (1.0, 0.0):                        # full power into the end stop, then neutral in brake mode
        w = world(BASE, {"name": "e", "stock": "elevator", "attach": "base.top", "stages": 3, "rigging": rigging,
                         "carriage_mass": 1.0, "ids": {"lift": "PWM:0"}})
        drive(w, 2.0, p0=duty)
        assert np.isfinite(w.data.qpos).all() and np.isfinite(w.data.qvel).all()
        assert w.data.warning[mujoco.mjtWarning.mjWARN_BADQACC].number == 0


def test_implicit_back_emf_matches_the_explicit_integration():
    """The motor's back-EMF is integrated implicitly (as joint damping); against hand-integrating the same
    first-order motor+wheel equation explicitly at the sim timestep, the wheel speed agrees within 1%."""
    moi = 0.00337                                  # tau = J R Kv / Kt ~ 0.3 s: a heavy wheel
    w = world(BASE, {"name": "fw", "stock": "flywheel", "attach": "base.top", "moi": moi, "ids": {"spin": "PWM:0"}})
    m = get_motor("KrakenX60")
    dt, v, omega = w.model.opt.timestep, w.vbatt, 0.0
    steps = int(0.3 / dt)
    for _ in range(steps):
        omega += dt * m.Kt * (v - omega / m.Kv) / m.R / moi
    drive(w, 0.3, p0=1.0)
    assert w.joint_states()["fw.spin"][1] == pytest.approx(omega, rel=0.01)
    # and returns to the base damping when the world resets
    w.reset()
    assert (w.model.dof_damping == w._base_damping).all()


@pytest.mark.parametrize("rigging", ["continuous", "cascade"])
def test_three_stage_elevator_stall_boundary(rigging):
    stall = stall_load(rigging)
    start, end = mid_height_run(rigging, 0.95 * stall)
    assert end > start + 0.005                      # 5% under stall: lifts
    start, end = mid_height_run(rigging, 1.05 * stall)
    assert end < start - 0.005                      # 5% over stall: sinks even at full power


def test_cascade_falls_under_a_load_continuous_holds():
    load = 0.95 * stall_load("continuous")          # continuous lifts this at full power
    start, end = mid_height_run("continuous", load)
    assert end > start + 0.005
    start, end = mid_height_run("cascade", load)   # cascade only has 1/3 of the force at the carriage
    assert end < start - 0.005


@pytest.mark.parametrize("rigging", ["continuous", "cascade"])
def test_three_stage_elevator_stages_move_together_and_encoder_counts_spool_turns(rigging):
    w = lift_world(rigging, 1.0, binding="CANcoder:1")
    drive(w, 0.3, p0=1.0)
    js = w.joint_states()
    q1 = js["e.stage1"][0]
    assert q1 > 0.02                               # it moved
    assert js["e.stage2"][0] == pytest.approx(q1, rel=0.02)
    assert js["e.carriage"][0] == pytest.approx(q1, rel=0.02)
    assert carriage_height(w) == pytest.approx(3 * q1, rel=0.02)       # carriage = 3 x stage 1 travel
    (turns,) = [u[2][">rawPositionInput"] for u in w.sensor_updates(HalState()) if ">rawPositionInput" in u[2]]
    spool_turns_per_meter = (1 / 3 if rigging == "cascade" else 1.0) / (2 * math.pi * 0.03)   # per carriage meter
    assert turns == pytest.approx(carriage_height(w) * spool_turns_per_meter, rel=0.02)


import mujoco

SWERVE = [{"name": "drive", "stock": "chassis", "ids": {"gyro": "Pigeon2:13"}}] + [
    {"name": c, "stock": "swerve/MK4i", "attach": f"drive.{m}",
     "ids": {"drive": f"PWM:{i}", "steer": f"PWM:{i + 4}", "encoder": f"CANcoder:{i + 20}"}}
    for i, (c, m) in enumerate((("fl", "front_left"), ("fr", "front_right"), ("bl", "back_left"),
                                ("br", "back_right")))]


def settle_contacts(w, steps=1000):
    hal = HalState()
    for _ in range(steps):
        w.step(hal)
    m, d = w.model, w.data
    carpet = m.geom("carpet").id
    return {mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, int(m.geom_bodyid[c.geom1 if c.geom2 == carpet else c.geom2]))
            for c in d.contact[:d.ncon] if carpet in (c.geom1, c.geom2)}


def test_swerve_robot_rests_on_its_four_wheels():
    w = world(*SWERVE)
    z0 = w.data.xpos[w.robot_body][2]
    assert settle_contacts(w) == {"fl.wheel", "fr.wheel", "bl.wheel", "br.wheel"}
    assert abs(w.data.xpos[w.robot_body][2] - z0) < 0.005


def test_swerve_free_speed_matches_the_ratio():
    w = world(*SWERVE, start=(1.0, 4.0))             # ~12 m of room before the red wall
    drive(w, 3.0, p0=1.0, p1=1.0, p2=1.0, p3=1.0)
    m = get_motor("KrakenX60")
    free = m.Kv * w.vbatt / 6.75 * 0.0508            # m/s at the wheel, no load
    assert w.robot_speed() == pytest.approx(free, rel=0.12)


KITBOT = [{"name": "drive", "stock": "chassis", "length": 0.66, "width": 0.6, "mass": 47.0, "frame_height": 0.1},
          {"name": "dt", "stock": "differential", "attach": "drive.center", "ids": {"left": "PWM:0", "right": "PWM:1"}}]


def test_differential_robot_rests_on_its_wheels():
    wheels = {f"dt.{s}_{i}" for s in ("left", "right") for i in (1, 2, 3)}
    flat = [KITBOT[0], {**KITBOT[1], "drop_center": 0.0}]
    assert settle_contacts(world(*flat)) == wheels                 # no drop center: all six wheels touch
    # A rigid chassis on a 3 mm drop center touches with the middle wheels plus at most one end pair (it rocks).
    # The stock chassis is balanced front to back and the braked motors hold it, so here it rests on the middle pair.
    assert {"dt.left_2", "dt.right_2"} <= settle_contacts(world(*KITBOT)) <= wheels


def test_differential_robot_drives_straight_and_turns():
    w = world(*KITBOT)
    drive(w, 1.0, p0=0.5, p1=-0.5)                    # the right side is inverted: opposite commands go straight
    x, y, h = w.robot_pose()
    assert x > 2.2 and abs(h) < 0.05 and abs(y - 4.0) < 0.05
    w = world(*KITBOT)
    drive(w, 0.5, p0=0.5)                             # left side only, forward: pivots clockwise
    _, _, h = w.robot_pose()
    assert h < -0.1
    w = world(*KITBOT)
    drive(w, 0.5, p0=0.5, p1=0.5)                     # the right controller is un-inverted: drives backward
    x, y, h = w.robot_pose()
    assert abs(x - 2.0) < 0.1 and h < -math.radians(20)   # so it spins in place, clockwise (~5 rad/s: 0.5 s keeps
                                                          # heading from wrapping past 180 deg)


def arm_angle_after(factor, seconds):
    mass, length, gearing = 3.0, 0.6, 100.0
    m = get_motor("KrakenX60")
    volts = mass * 9.81 * length / 2 / gearing / m.Kt * m.R
    w = world(BASE, {"name": "a", "stock": "arm", "attach": "base.top", "mass": mass, "length": length,
                     "gearing": gearing, "range": [-180, 180], "ids": {"pivot": "PWM:0"}})
    drive(w, seconds, p0=factor * volts / w.vbatt)
    q, qd = w.joint_states()["a.pivot"][:2]
    return math.degrees(q), qd


def test_arm_holding_voltage_boundary():
    assert arm_angle_after(0.9, 1.0)[0] < -0.2      # 10% under the m*g*r holding voltage: sinks
    assert arm_angle_after(1.1, 1.0)[0] > 0.2       # 10% over: rises
    assert abs(arm_angle_after(1.0, 1.0)[1]) < 1e-3  # at the holding voltage: stays put


def rack_and_pinion_world(tmp_path, radius, driven="rack"):
    """A rail with a rack (slide) and a pinion of `radius` m (hinge), coupled rack = -radius * pinion; the motor slot
    drives `driven`."""
    from scrimmage.parts.toml_writer import dumps
    pm = 0.068 * (radius / 0.02) ** 2                       # a 20 mm-thick steel-ish disc scaled with its radius
    pinion_i = 0.5 * pm * radius ** 2
    (tmp_path / "part.toml").write_text(dumps({
        "part": {"name": "rp", "format": 1},
        "body": [{"name": "rail", "mass": 5.0, "geom": []},
                 {"name": "rack", "parent": "rail", "mass": 0.65, "geom": [],
                  "joint": {"name": "rack", "slide": "z", "range": [0.0, 0.5]}},
                 {"name": "pinion", "parent": "rail", "pos": [-radius, 0.0, 0.1], "rot": [-90, 0, 0], "mass": pm,
                  "com": [0, 0, 0], "inertia": [pinion_i / 2, pinion_i / 2, pinion_i], "geom": [],
                  "joint": {"name": "pinion", "hinge": "z"}}],
        "coupling": [{"joints": ["pinion", "rack"], "ratio": -radius}],
        "motor": [{"slot": "lift", "joint": driven, "motor": "KrakenX60",
                   "gearing": 500.0 if driven == "rack" else 10.0}]}))
    return World({"part": [BASE, {"name": "rp", "path": ".", "attach": "base.top", "ids": {"lift": "PWM:0"}}],
                  "robot": {"start_pose": [2.0, 4.0, 0.0]}, **TEST_WORLD}, tmp_path)


@pytest.mark.parametrize("radius", [0.020, 0.010, 0.005, 0.003])
def test_rack_and_pinion_coupling_holds_with_a_small_pinion(tmp_path, radius):
    """A slide coupled to a light hinge at `radius` meters per radian. MuJoCo regularizes a joint equality with both
    joints' inverse inertias unscaled by the ratio, so a coupling written with |ratio| < 1 onto the light pinion was
    far too soft (the pinion barely turned). The assembler writes couplings with |ratio| >= 1."""
    w = rack_and_pinion_world(tmp_path, radius)
    drive(w, 0.05, p0=0.3)
    rack, pinion = w.joint_states()["rp.rack"][0], w.joint_states()["rp.pinion"][0]
    drive(w, 0.05, p0=0.3)
    d_rack, d_pinion = w.joint_states()["rp.rack"][0] - rack, w.joint_states()["rp.pinion"][0] - pinion
    assert d_rack > 0.01 and d_rack / d_pinion == pytest.approx(-radius, rel=0.01)


@pytest.mark.parametrize("driven", ["rack", "pinion"])
def test_back_emf_damping_split_keeps_the_dissipation_whichever_way_a_coupling_is_written(tmp_path, driven):
    """The coupling is stored as pinion = rack / -r (|ratio| >= 1); the damping split must still give
    sum b_k c_k^2 = b, with c_k = d q_k / d q_motor."""
    from scrimmage.bindings import _coupled_dofs
    r = 0.01
    w = rack_and_pinion_world(tmp_path, r, driven)
    m = w.model
    (e,) = [e for e in range(m.neq) if int(m.eq_type[e]) == int(mujoco.mjtEq.mjEQ_JOINT)]
    assert m.joint(int(m.eq_obj1id[e])).name == "rp.pinion" and m.eq_data[e][1] == pytest.approx(-1 / r)
    shares = dict(_coupled_dofs(m, f"rp.{driven}"))
    dof = {j: int(m.jnt_dofadr[m.joint(f"rp.{j}").id]) for j in ("rack", "pinion")}
    c = {"rack": 1.0, "pinion": -1 / r} if driven == "rack" else {"rack": -r, "pinion": 1.0}
    assert sum(shares[dof[j]] * c[j] ** 2 for j in c) == pytest.approx(1.0)
    # equal b_k / M_kk on both dofs (what keeps the implicit correction uniform)
    ratios = [shares[dof[j]] / m.dof_M0[dof[j]] for j in c]
    assert ratios[0] == pytest.approx(ratios[1])
