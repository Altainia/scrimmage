"""scrimmage: run real WPILib robot code against a MuJoCo physics world via the HALSim WebSocket protocol."""

from ._gl import set_default

set_default()  # headless rendering needs a GL backend chosen before mujoco is imported

from .motors import MOTORS, Battery, DCMotor, get_motor
from .world import World

__all__ = ["MOTORS", "Battery", "DCMotor", "get_motor", "World"]
__version__ = "0.1.0"
