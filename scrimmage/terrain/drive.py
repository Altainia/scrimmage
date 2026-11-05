"""A built-in driver for `terrain sweep`: drives a robot straight ahead at a set speed with no robot program.

StraightDriver is a World.external hook. Every physics step, after the motors have driven (with no robot program
they get 0 V, and a braking motor adds back-EMF damping), it takes over the joints it drives:
- every swerve azimuth joint (a vertical hinge carrying a tread wheel) is held at 0 rad, the robot's +x;
- every tread wheel joint is servoed to `speed / r`, rolling the robot forward along its +x (a differential drive's
  wheels likewise: it has no azimuth joints);
- every joint in `holds` (an elevator up, an arm out) is held at its value.

The motor's torque and back-EMF damping on those joints are replaced, so coast and brake mode drive alike. The
servos are ideal: the wheel holds its speed (a robot's velocity loop, with no motor torque limit; traction limits it),
and each hold is a stiff PD. Their damping goes into MuJoCo's dof_damping, which the implicit integrator applies
implicitly, so they stay stable however stiff; only a hold's spring is explicit, at HOLD_OMEGA.
"""

from __future__ import annotations

import mujoco
import numpy as np

from .. import i18n

WHEEL_DAMPING = 50.0     # N m s/rad: the wheel servo's gain; 1 N m of torque costs it 0.02 rad/s of wheel speed
HOLD_OMEGA = 100.0       # rad/s: each hold's natural frequency on its joint's own inertia (0.1 rad per 1 ms step)
HOLD_ZETA = 1.0          # each hold's damping ratio
VERTICAL = 0.99          # a hinge axis this close to the parent's z is an azimuth (steer) joint


class StraightDriver:
    def __init__(self, world, speed: float, holds: dict[str, float]):
        m = world.model
        self.world, self.model = world, m
        self.speed = float(speed)
        if world.parts is None or not world.parts.tread_geoms:
            raise ValueError(i18n.t("the robot has no tread wheels (geoms with tread = true) to drive"))
        names = world.joint_states()
        unknown = [n for n in holds if n not in names]
        if unknown:
            raise ValueError(i18n.t("no joint {unknown} to hold; joints: {names}",
                                    unknown=repr(unknown[0]), names=', '.join(sorted(names))))
        # Each tread wheel: (dof, joint id, radius), and the azimuth joints above them.
        self.wheels: list[tuple[int, int, float]] = []
        azimuths: dict[int, None] = {}
        for name in world.parts.tread_geoms:
            g = m.geom(name).id
            b = int(m.geom_bodyid[g])
            j = int(m.body_jntadr[b])
            if j < 0 or int(m.jnt_type[j]) != int(mujoco.mjtJoint.mjJNT_HINGE):
                raise ValueError(i18n.t("tread geom {name} isn't on a wheel with a hinge joint", name=repr(name)))
            self.wheels.append((int(m.jnt_dofadr[j]), j, float(m.geom_size[g][0])))
            parent = int(m.body_parentid[b])
            for k in range(int(m.body_jntadr[parent]), int(m.body_jntadr[parent]) + int(m.body_jntnum[parent])):
                if (int(m.jnt_type[k]) == int(mujoco.mjtJoint.mjJNT_HINGE)
                        and abs(float(m.jnt_axis[k][2])) > VERTICAL):
                    azimuths[k] = None
        # Holds: (qpos address, dof, target, kp, kd), the gains on the joint's own inertia.
        d = world.data
        full = np.zeros((m.nv, m.nv))
        mujoco.mj_forward(m, d)
        mujoco.mj_fullM(m, d, full)
        targets = {j: 0.0 for j in azimuths}
        for name, value in holds.items():
            j = m.joint(name).id
            lo, hi = m.jnt_range[j]
            if m.jnt_limited[j] and not lo <= value <= hi:
                raise ValueError(i18n.t("can't hold joint {name} at {value}: its range is {lo} to {hi}",
                                        name=repr(name), value=f"{value:g}", lo=f"{lo:g}", hi=f"{hi:g}"))
            targets[j] = float(value)
        self.holds = []
        for j, q in targets.items():
            dof = int(m.jnt_dofadr[j])
            inertia = float(full[dof, dof])
            self.holds.append((int(m.jnt_qposadr[j]), dof, q, inertia * HOLD_OMEGA ** 2,
                               2.0 * HOLD_ZETA * inertia * HOLD_OMEGA))
        self._dofs = [w[0] for w in self.wheels] + [h[1] for h in self.holds]
        self._base = world._base_damping

    def _spin(self, data: mujoco.MjData, speed: float) -> list[float]:
        """Each wheel joint's velocity rolling the robot at `speed` along its +x: the chassis's y . the wheel's axis,
        over r (a wheel turning about the chassis's +y rolls it forward)."""
        side = data.xmat[self.world.robot_body].reshape(3, 3)[:, 1]
        return [float(side @ data.xaxis[j]) * speed / r for _, j, r in self.wheels]

    def __call__(self, data: mujoco.MjData) -> None:
        damping, qfrc = self.model.dof_damping, data.qfrc_applied
        for dof in self._dofs:              # the driver replaces the motors on its joints
            damping[dof] = self._base[dof]
            qfrc[dof] = 0.0
        for (dof, _, _), w in zip(self.wheels, self._spin(data, self.speed)):
            damping[dof] += WHEEL_DAMPING
            qfrc[dof] += WHEEL_DAMPING * w
        q = data.qpos
        for adr, dof, target, kp, kd in self.holds:
            damping[dof] += kd
            qfrc[dof] += kp * (target - float(q[adr]))

    def launch(self, data: mujoco.MjData) -> None:
        """Set the robot rolling at the driver's speed along its +x (kept level: no climb), every wheel spinning to
        match and every other joint at rest, so the run starts at speed rather than spinning its wheels up."""
        m, w = self.model, self.world
        mujoco.mj_forward(m, data)
        x = data.xmat[w.robot_body].reshape(3, 3)[:, 0].copy()
        x[2] = 0.0
        x /= np.linalg.norm(x)
        data.qvel[:] = 0.0
        data.qvel[w.free_dadr:w.free_dadr + 3] = self.speed * x
        for (dof, _, _), v in zip(self.wheels, self._spin(data, self.speed)):
            data.qvel[dof] = v
        mujoco.mj_forward(m, data)
