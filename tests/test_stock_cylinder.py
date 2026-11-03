"""The stock pneumatic cylinder: a tendon between two pins, free (its own rod) or pushing another part, and its
valve slot's device IDs."""

from __future__ import annotations

import mujoco
import pytest

from scrimmage.parts.assemble import assemble
from scrimmage.parts.model import PartError, load_part

STROKE = 0.1524


def free_robot(**params):
    return {"part": [{"name": "drive", "stock": "chassis"},
                     {"name": "cyl", "stock": "cylinder", "attach": "drive.top", **params}]}


def arm_robot(**params):
    params = {"push": "arm.arm", "push_pos": [0.2, 0, 0], **params}
    return {"part": [{"name": "drive", "stock": "chassis"},
                     {"name": "arm", "stock": "arm", "attach": "drive.top"},
                     {"name": "cyl", "stock": "cylinder", "attach": "drive.top", "pos": [0, 0, -0.1], **params}]}


def test_free_cylinder():
    plain = assemble({"part": [{"name": "drive", "stock": "chassis"}]}, ".")
    built = assemble(free_robot(), ".")
    m = built.model
    L0 = 0.3 * STROKE + 0.05
    ten = m.tendon("cyl.cylinder")
    assert bool(m.tendon_limited[ten.id])
    assert m.tendon_range[ten.id] == pytest.approx([L0, L0 + STROKE])
    assert m.tendon_frictionloss[ten.id] == pytest.approx(8.0)
    assert m.nu == plain.model.nu + 1
    act = m.actuator("cyl.cylinder")
    assert act.trntype[0] == mujoco.mjtTrn.mjTRN_TENDON and act.trnid[0] == ten.id
    assert act.gainprm[0] == 1 and not any(act.biasprm) and not act.ctrllimited[0]
    j = m.joint("cyl.extend")
    assert j.range == pytest.approx([0, STROKE])

    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    before = float(d.ten_length[ten.id])
    assert before == pytest.approx(L0, abs=1e-9)
    d.qpos[j.qposadr[0]] = 0.1
    mujoco.mj_forward(m, d)
    assert float(d.ten_length[ten.id]) - before == pytest.approx(0.1, abs=1e-9)

    (spec,) = built.cylinders
    assert spec.name == "cyl" and spec.length == pytest.approx(L0)
    assert spec.pin_a[0] == "cyl.barrel" and spec.pin_b[0] == "cyl.rod"
    assert (spec.bore, spec.rod, spec.stroke) == (0.0269875, 0.0079375, STROKE)
    assert spec.conductance == 8e-9 and spec.friction == 8.0
    assert {mt.name for mt in built.instances[1].part.mounts} == {"base", "rod"}


def test_free_cylinder_given_length():
    built = assemble(free_robot(length=0.3), ".")
    assert built.model.tendon_range[0] == pytest.approx([0.3, 0.3 + STROKE])
    assert built.cylinders[0].length == pytest.approx(0.3)


def test_robot_without_cylinder_has_no_valves():
    built = assemble({"part": [{"name": "drive", "stock": "chassis"}]}, ".")
    assert built.cylinders == [] and built.bindings["valve"] == []
    assert built.model.ntendon == 0 and built.model.nu == 0


def test_push_cylinder_on_an_arm():
    built = assemble(arm_robot(), ".")
    m = built.model
    (spec,) = built.cylinders
    assert spec.pin_a[0] == "cyl.barrel" and spec.pin_b == ("arm.arm", (0.2, 0.0, 0.0))
    L0 = (0.2 ** 2 + 0.1 ** 2) ** 0.5
    assert spec.length == pytest.approx(L0, abs=1e-9)
    assert m.tendon_range[m.tendon("cyl.cylinder").id] == pytest.approx([L0, L0 + STROKE], abs=1e-9)
    assert [b.name for b in built.instances[2].part.bodies] == ["barrel"]
    assert {mt.name for mt in built.instances[2].part.mounts} == {"base"}

    pivot = m.joint("arm.pivot").dofadr[0]
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    assert d.qacc[pivot] < 0          # unpushed, the arm falls
    d.ctrl[m.actuator("cyl.cylinder").id] = 100.0
    mujoco.mj_forward(m, d)
    assert d.qacc[pivot] > 0          # 100 N on the cylinder pushes the tip up


def test_push_length_outside_stroke():
    with pytest.raises(PartError) as e:
        assemble(arm_robot(length=0.01), ".")
    msg = str(e.value)
    assert "cyl" in msg and "0.2236 m" in msg and "0.01 m" in msg


def test_push_pins_rigid():
    with pytest.raises(PartError) as e:
        assemble(arm_robot(push="drive.chassis"), ".")
    assert str(e.value) == ("cyl: the cylinder's two ends are on bodies that can't move relative to each other "
                            "(nothing between drive.chassis and drive.chassis turns or slides)")


def test_push_unknown_body():
    with pytest.raises(PartError, match="no body 'nothing' on 'arm'; its bodies: base, arm"):
        assemble(arm_robot(push="arm.nothing"), ".")
    with pytest.raises(PartError, match="no part 'nobody'"):
        assemble(arm_robot(push="nobody.arm"), ".")
    with pytest.raises(PartError, match="<part>.<body>"):
        assemble(arm_robot(push="arm"), ".")


def valve(*ids):
    """Robot with one free cylinder per valve ID; the valve bindings."""
    cfg = {"part": [{"name": "drive", "stock": "chassis"}]}
    for i, v in enumerate(ids):
        cfg["part"].append({"name": f"c{i}", "stock": "cylinder", "attach": "drive.top", "pos": [0, 0.05 * i, 0],
                            "ids": {"valve": v}})
    return assemble(cfg, ".").bindings["valve"]


def test_valve_ids():
    (b,) = valve("PCM:3")
    assert b == {"name": "c0.valve", "cylinder": "c0", "module": "PCM", "id": 0, "forward": 3, "reverse": None,
                 "invert": False}
    (b,) = valve("PH:3,4")
    assert (b["module"], b["id"], b["forward"], b["reverse"]) == ("PH", 1, 3, 4)
    (b,) = valve({"pcm": 1, "channel": 3})
    assert (b["module"], b["id"], b["forward"], b["reverse"]) == ("PCM", 1, 3, None)
    (b,) = valve({"ph": 2, "forward": 3, "reverse": 4, "invert": True})
    assert (b["module"], b["id"], b["forward"], b["reverse"], b["invert"]) == ("PH", 2, 3, 4, True)
    with pytest.raises(PartError, match="0 to 7"):
        valve("PCM:9")
    with pytest.raises(PartError, match="0 to 15"):
        valve({"ph": 1, "channel": 16})
    with pytest.raises(PartError, match="c0.valve.*PCM.*c1.valve.*PH"):
        valve("PCM:1", "PH:2")
    with pytest.raises(PartError, match="slot 'valve' is a valve slot; TalonFX:3 is a motor"):
        valve("TalonFX:3")
    with pytest.raises(PartError, match="different channels"):
        valve("PH:3,3")


def test_valve_in_a_motor_slot():
    cfg = {"part": [{"name": "drive", "stock": "chassis"},
                    {"name": "arm", "stock": "arm", "attach": "drive.top", "ids": {"pivot": "PCM:3"}}]}
    with pytest.raises(PartError, match="slot 'pivot' is a motor slot; PCM:3 is a valve"):
        assemble(cfg, ".")


def test_unbound_valve_warns():
    built = assemble(free_robot(), ".")
    assert "cyl.valve has no device ID, so it won't move" in built.warnings
    assert built.bindings["valve"] == []


def test_push_pos_needs_push():
    with pytest.raises(PartError, match="push_pos"):
        assemble(free_robot(push_pos=[0.1, 0, 0]), ".")


def test_part_toml_rejects_cylinder(tmp_path):
    (tmp_path / "part.toml").write_text(
        '[part]\nname = "p"\nformat = 1\n\n[[body]]\nname = "a"\nmass = 1.0\ngeom = [{ box = [0.1, 0.1, 0.1] }]\n\n'
        '[[cylinder]]\nbore = 0.02\n')
    with pytest.raises(PartError, match="unknown key 'cylinder'"):
        load_part(tmp_path)
