"""The shooter shaft, its velocity controller and the battery it draws from (its own, or the world's bus).

All units are SI. `speed` is the wheel shaft's angular speed in rad/s; the controller's target and gains are in
wheel rotations per second, as Phoenix's velocity units are. Gearing is motor rotations per wheel rotation.
"""

import math

from ..motors import Battery, get_motor
from .inputs import BatteryIn, Control, Shaft


class ShaftModel:
    """The wheel shaft: a DC motor through the gearing, loaded by the balls, on a battery that sags.

    With `battery` None the shaft owns no battery: it runs on whatever bus voltage `set_bus_voltage` last set (12 V
    until then), and the caller charges `supply_current` to its own battery (the world's shared one)."""

    def __init__(self, shaft: Shaft, battery: BatteryIn | None):
        self.motor = get_motor(shaft.motor, shaft.motor_count)
        self.gearing = shaft.gearing
        # Rotor inertia is ignored: the motors module has no figure for it.
        self.inertia = shaft.wheel_inertia + shaft.added_inertia
        if battery is None:
            self.battery = None
            self.bus_voltage = 12.0
        else:
            self.battery = Battery(nominal_voltage=battery.nominal_voltage, resistance=battery.resistance)
            self.bus_voltage = battery.nominal_voltage
        self.speed = 0.0
        self.angle = 0.0  # the wheel's accumulated rotation (rad)
        self.stator_current = 0.0
        self.supply_current = 0.0

    def set_bus_voltage(self, v: float) -> None:
        """The bus voltage the next step runs on (for a shaft with no battery of its own)."""
        self.bus_voltage = v

    def torque_coefficients(self, voltage: float) -> tuple[float, float]:
        """(a, b) with the motor's torque at the wheel a - b * speed, for `voltage` clamped to the bus.

        Kt is WPILib's DCMotor constant, which is not exactly 1 / Kv (about 2% apart for a Kraken); kept so the
        model matches motors.py and WPILib's sims.
        """
        motor, g = self.motor, self.gearing
        bus = self.bus_voltage  # sag from the previous step's supply current (one-step lag)
        v = max(-bus, min(bus, voltage))
        return motor.Kt * g * v / motor.R, motor.Kt * g * g / (motor.R * motor.Kv)

    def step(self, dt: float, voltage: float, load_torque: float) -> None:
        """Advance dt seconds with `voltage` commanded; `load_torque` (N*m at the wheel) opposes forward spin.
        A step of no time (dt <= 0) changes nothing."""
        if dt <= 0.0:
            return
        motor, g = self.motor, self.gearing
        bus = self.bus_voltage
        v = max(-bus, min(bus, voltage))
        # Over one step the motor is linear in speed, so integrate it exactly (stable for any dt):
        # J dw/dt = a - b w, with a the stall-side drive less the load and b the back-EMF damping.
        a, b = self.torque_coefficients(voltage)
        a -= load_torque
        rest = a / b
        new_speed = rest + (self.speed - rest) * math.exp(-b * dt / self.inertia)
        # Report the step-average stator current from the momentum balance, J dw/dt = Kt g I - load, so it is
        # consistent with the energy the step delivered as back-EMF rose (or the load jumped).
        self.stator_current = (self.inertia * (new_speed - self.speed) / dt + load_torque) / (motor.Kt * g)
        self.supply_current = self.stator_current * (abs(v) / bus if bus > 0.0 else 0.0)
        self.angle += 0.5 * (self.speed + new_speed) * dt
        self.speed = new_speed
        if self.battery is not None:
            self.bus_voltage = self.battery.voltage(self.supply_current)


class VelocityController:
    """Stands in for the Talon's velocity loop: kS + kV feedforward plus kP on error, with current limits."""

    def __init__(self, control: Control, shaft: Shaft):
        self.control = control
        self.motor = get_motor(shaft.motor, shaft.motor_count)
        self.gearing = shaft.gearing
        self.motor_count = shaft.motor_count  # the current limits are per motor, as set on each Talon
        # Volts per wheel rotation per second that hold a speed with no load: 2 pi * gearing / Kv.
        self.kV = control.kV if control.kV is not None else 2.0 * math.pi * shaft.gearing / self.motor.Kv

    def voltage(self, shaft_model: ShaftModel) -> float:
        c, motor = self.control, self.motor
        error = c.target_rps - shaft_model.speed / (2.0 * math.pi)
        volts = c.kS * math.copysign(1.0, c.target_rps) + self.kV * c.target_rps + c.kP * error
        bus = shaft_model.bus_voltage
        emf = shaft_model.speed * self.gearing / motor.Kv  # back-EMF at the motor
        high, low = bus, -bus
        # `motor` is all the shaft's motors together (R is theirs in parallel), and each has its own Talon and limit,
        # so the shaft's total limit is the per-motor one times the motor count.
        if c.stator_limit is not None:
            # Stator current is (v - emf) / R, so the limit is a voltage window around the back-EMF.
            stator = c.stator_limit * self.motor_count
            high = min(high, emf + motor.R * stator)
            low = max(low, emf - motor.R * stator)
        if c.supply_limit is not None and bus > 0.0:
            # Supply current is stator current times duty v / bus: (v - emf) v / (R bus) <= limit.
            cap = motor.R * c.supply_limit * self.motor_count * bus
            high = min(high, (emf + math.sqrt(emf * emf + 4.0 * cap)) / 2.0)
            low = max(low, -(-emf + math.sqrt(emf * emf + 4.0 * cap)) / 2.0)
        return max(low, min(high, volts))
