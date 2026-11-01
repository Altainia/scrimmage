"""RobotPy version of shims/java/WsCanShim.java: republish a vendor motor controller as the HALSim
WebSocket protocol's standard CANMotor / CANEncoder devices so scrimmage can drive it.

    from ws_can_shim import WsCanShim

    def robotInit(self):
        ...
        sim = self.arm_talon.sim_state          # phoenix6 TalonFX (create shims only in simulation:
                                                #  `if wpilib.RobotBase.isSimulation():`)
        self.shims = [WsCanShim("TalonFX[3]",
                                applied_volts=lambda: sim.motor_voltage,
                                set_supply_voltage=sim.set_supply_voltage,
                                set_rotor_rotations=sim.set_raw_rotor_position,
                                set_rotor_rps=sim.set_rotor_velocity)]

    def _simulationPeriodic(self):              # RobotPy spells WPILib's simulationPeriodic this way
        for s in self.shims:
            s.update()

The SimDevice plumbing below is exercised by the test suite; the phoenix6 lambdas above are an
untested example.
"""

from __future__ import annotations

from typing import Callable

import hal

_D = hal.SimDevice.Direction


class WsCanShim:
    def __init__(self, name: str, *, applied_volts: Callable[[], float],
                 set_supply_voltage: Callable[[float], object] = lambda v: None,
                 set_rotor_rotations: Callable[[float], object] = lambda r: None,
                 set_rotor_rps: Callable[[float], object] = lambda r: None,
                 is_brake: Callable[[], bool] = lambda: True):
        self._applied_volts = applied_volts
        self._set_supply_voltage = set_supply_voltage
        self._set_rotor_rotations = set_rotor_rotations
        self._set_rotor_rps = set_rotor_rps
        self._is_brake = is_brake
        # "CANMotor:<name>" shows up on the socket as {"type": "CANMotor", "device": "<name>"}
        self._motor = hal.SimDevice(f"CANMotor:{name}")
        self._encoder = hal.SimDevice(f"CANEncoder:{name}")
        self.percent_output = self._motor.createDouble("percentOutput", _D.kOutput, 0.0)
        self.brake_mode = self._motor.createBoolean("brakeMode", _D.kOutput, True)
        self.motor_current = self._motor.createDouble("motorCurrent", _D.kInput, 0.0)
        self.supply_current = self._motor.createDouble("supplyCurrent", _D.kInput, 0.0)
        self.bus_voltage = self._motor.createDouble("busVoltage", _D.kInput, 12.0)
        self.position = self._encoder.createDouble("position", _D.kInput, 0.0)
        self.velocity = self._encoder.createDouble("velocity", _D.kInput, 0.0)

    def update(self) -> None:
        vbus = self.bus_voltage.get()
        if vbus < 1.0:
            vbus = 12.0  # physics not connected yet
        self._set_supply_voltage(vbus)
        self.percent_output.set(max(-1.0, min(1.0, self._applied_volts() / vbus)))
        self.brake_mode.set(bool(self._is_brake()))
        self._set_rotor_rotations(self.position.get())
        self._set_rotor_rps(self.velocity.get())
