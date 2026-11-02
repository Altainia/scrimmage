"""Driving a robot's motors without a robot program: the climb runner and the block-test runner both write the motor
controllers' outputs here, so every drive goes through the config's own motor, gearing and battery."""

from __future__ import annotations

from .protocol import HalState

KP_SLIDE = 400.0          # V/m: a slide's position loop (12 V at 3 cm of error; estimate, a team's climber kP)
KP_HINGE = 60.0           # V/rad: a hinge's (12 V at 11.5 deg of error; estimate)


class MotorDrive:
    """A motor binding being driven, writing its HAL outputs like the robot program would."""

    def __init__(self, binding):
        self.b = binding

    def write(self, hal: HalState, volts: float, bus: float) -> None:
        b = self.b
        if b.kind == "PWM":
            hal.devices[b.device_key] = {"<init": True, "<speed": b.sign * volts / max(bus, 1e-6)}
        elif b.kind == "CANMotor":
            hal.devices[b.device_key] = {"<percentOutput": b.sign * volts / max(bus, 1e-6)}
        else:                           # Phoenix 6, the REV helper and fast mode take a voltage
            hal.devices[b.device_key] = {"<motorVoltage": b.sign * volts}


def position_volts(target: float, q: float, hinge: bool, cap: float) -> float:
    """A proportional position loop: volts for a joint at q heading to target, clipped to +-cap."""
    kp = KP_HINGE if hinge else KP_SLIDE
    return max(-cap, min(cap, kp * (target - q)))


def driven_joints(world) -> dict:
    """Joint name -> the motor binding that moves it (found by the joint's qpos address)."""
    m = world.model
    names = world.joint_states()
    by_qadr = {mb.qadr: mb for mb in world.motors}
    motors = {}
    for j in range(m.njnt):
        name = m.joint(j).name
        if name in names and int(m.jnt_qposadr[j]) in by_qadr:
            motors[name] = by_qadr[int(m.jnt_qposadr[j])]
    return motors
