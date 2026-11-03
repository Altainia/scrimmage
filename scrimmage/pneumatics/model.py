"""The robot's air system: tanks, compressor, pressure switch, regulator and double-acting cylinders.

Pure Python, no MuJoCo. The physics step hands in each cylinder's extension and gets back its force.

The model is isothermal (air at T_AIR throughout), so each chamber's state is its air mass m, and its absolute
pressure is p = m R T / V. A cylinder has two chambers:

    cap side   V = dead + A_cap x                          pushes to extend, area A_cap = pi bore^2 / 4
    rod side   V = dead + (A_cap - A_rod) (stroke - x)     pushes to retract, area A_cap - A_rod

with x the extension, clamped to [0, stroke]. Force is gauge pressure times area, positive to extend:

    F = p_cap A_cap - p_rod (A_cap - A_rod)

It carries no seal friction: MuJoCo's tendon frictionloss does that.

Flow through a valve follows ISO 6358, with sonic conductance C and critical ratio b:

    q = C p_up RHO_ANR phi(r),  r = p_down / p_up,  phi = 1 for r <= b, else sqrt(1 - ((r - b) / (1 - b))^2)

A valve is one of three states. "extend" feeds the cap side from the regulator and vents the rod side. "retract"
is the mirror. "hold" shuts both ports and traps the air (a detented double solenoid with both coils off).

Supply and exhaust. The regulator holds the working pressure, or the tanks' pressure if that is lower. Air drawn
from it leaves the tanks. A regulator does not relieve, so a chamber pushed above the regulator's pressure just
stays there. The exhaust is the atmosphere, and flows either way (a chamber pulled below atmosphere breathes in).

Stability. Each call splits into SUBSTEPS sub-steps. In each, a chamber's new mass is clamped so it never passes
the pressure it is flowing toward, so no step size can overshoot.

The compressor adds its free-air flow to the tanks while on. It is on when the robot is enabled, the program's
closed loop is on, and the pressure switch calls for air. The switch has hysteresis: it stops at switch_off_psi
and starts again below switch_on_psi. The VIAIR 90C curve is AndyMark's bench test. Cylinder size, conductance and
the switch's pressures are estimates (UNCALIBRATED).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

PSI = 6894.757            # Pa per psi
ATM = 101325.0            # Pa
T_AIR = 293.15            # K
R_AIR = 287.05            # J/(kg K)
RHO_ANR = 1.185           # kg/m^3, air at the standard reference condition (ISO 8778)
CFM = 4.719474e-4         # m^3/s per cubic foot per minute
MAX_WORKING_PSI = 60.0    # FRC rule: working side at most 60 psi
MAX_STORED_PSI = 120.0    # FRC rule: stored side at most 120 psi
DEFAULT_CONDUCTANCE = 8e-9   # m^3/(s Pa), 0.8 dm^3/(s bar): an estimate for FRC's small 5/2 valves
CRITICAL_RATIO = 0.3
SUBSTEPS = 10

_STATES = ("extend", "retract", "hold")


@dataclass(frozen=True)
class Compressor:
    """A compressor's free-air flow and current, as straight lines through points of (gauge psi, cfm, amps)."""
    name: str
    points: tuple[tuple[float, float, float], ...]

    def _line(self, psi: float, column: int) -> float:
        # Linear between the points, carried on past the last pair (and before the first pair).
        pts = self.points
        i = 0
        while i < len(pts) - 2 and psi > pts[i + 1][0]:
            i += 1
        (p0, *v0), (p1, *v1) = pts[i], pts[i + 1]
        return v0[column - 1] + (v1[column - 1] - v0[column - 1]) * (psi - p0) / (p1 - p0)

    def flow_cfm(self, psi: float) -> float:
        return max(0.0, self._line(psi, 1))

    def amps(self, psi: float) -> float:
        return self._line(psi, 2)


COMPRESSORS = {"VIAIR 90C": Compressor("VIAIR 90C", ((0.0, 0.88, 7.0), (100.0, 0.22, 11.0)))}


@dataclass(frozen=True)
class AirConfig:
    """The air system: tank count and size, compressor, and pressures (gauge psi)."""
    tanks: int = 2
    tank_ml: float = 574.0
    compressor: str = "VIAIR 90C"       # a COMPRESSORS key, or "none" (tanks only)
    working_psi: float = 60.0
    start_psi: float = 120.0
    switch_off_psi: float = 115.0
    switch_on_psi: float = 95.0

    def __post_init__(self):
        if self.tanks < 1:
            raise ValueError("The air system needs at least one tank.")
        if self.tank_ml <= 0:
            raise ValueError("A tank's volume must be more than zero.")
        if self.working_psi > MAX_WORKING_PSI:
            raise ValueError(f"The working pressure is {self.working_psi:g} psi; FRC allows at most "
                             f"{MAX_WORKING_PSI:g} psi on the working side (the regulator rule).")
        if self.start_psi > MAX_STORED_PSI:
            raise ValueError(f"The start pressure is {self.start_psi:g} psi; FRC allows at most "
                             f"{MAX_STORED_PSI:g} psi stored.")
        if self.switch_on_psi >= self.switch_off_psi:
            raise ValueError("The pressure switch must turn on below the pressure where it turns off.")
        if self.compressor != "none" and self.compressor not in COMPRESSORS:
            raise ValueError(f"Unknown compressor {self.compressor!r}; use one of "
                             f"{', '.join(COMPRESSORS)} or \"none\".")


@dataclass(frozen=True)
class CylinderSpec:
    """A double-acting cylinder. bore, rod and stroke in m; conductance in m^3/(s Pa).

    pin_a, pin_b (body name, local position) and length (pin to pin when retracted) place it in the robot; the
    assembler fills them. The air model does not use them.
    """
    name: str
    bore: float
    rod: float
    stroke: float
    friction: float                 # N of seal friction; applied by MuJoCo, not by this model
    conductance: float
    dead_volume: float = 1e-6       # m^3 at each port
    pin_a: tuple[str, tuple] | None = None
    pin_b: tuple[str, tuple] | None = None
    length: float | None = None


class _Chamber:
    """One side of a cylinder: its air mass, and the area its pressure pushes on."""

    def __init__(self, area: float, dead: float):
        self.area = area                # m^2 the gauge pressure acts on
        self.dead = dead
        self.mass = 0.0                 # kg

    def volume(self, travel: float) -> float:
        """travel is how far this chamber has opened, in m (x for the cap side, stroke - x for the rod side)."""
        return self.dead + self.area * travel

    def pressure(self, volume: float) -> float:
        return self.mass * R_AIR * T_AIR / volume


def _flow(p_up: float, p_down: float, conductance: float) -> float:
    """ISO 6358 mass flow (kg/s) from p_up to p_down (absolute); zero if there is no pressure drop."""
    if p_up <= p_down:
        return 0.0
    r = p_down / p_up
    if r <= CRITICAL_RATIO:
        phi = 1.0
    else:
        phi = math.sqrt(1.0 - ((r - CRITICAL_RATIO) / (1.0 - CRITICAL_RATIO)) ** 2)
    return conductance * p_up * RHO_ANR * phi


class AirSystem:
    def __init__(self, config: AirConfig, cylinders: list[CylinderSpec]):
        self.config = config
        self._specs = {c.name: c for c in cylinders}
        self._tank_volume = config.tanks * config.tank_ml * 1e-6
        self._compressor = COMPRESSORS.get(config.compressor)
        self._chambers: dict[str, tuple[_Chamber, _Chamber]] = {}
        for c in cylinders:
            a_cap = math.pi * c.bore ** 2 / 4
            a_ann = a_cap - math.pi * c.rod ** 2 / 4
            self._chambers[c.name] = (_Chamber(a_cap, c.dead_volume), _Chamber(a_ann, c.dead_volume))
        self.reset()

    def reset(self):
        self._tank_mass = self._mass_at(self.config.start_psi * PSI + ATM, self._tank_volume)
        # Same rule as the switch's start: it calls for air if the tanks start below where it stops.
        self._calling = self.config.start_psi < self.config.switch_off_psi
        self._on = False
        self._valves = {name: "retract" for name in self._specs}
        # The chambers' masses depend on where the rod is, which reset doesn't know: the first step fills them.
        self._primed = False
        self._x: dict[str, float] = {}

    @staticmethod
    def _mass_at(p_abs: float, volume: float) -> float:
        return p_abs * volume / (R_AIR * T_AIR)

    def set_valve(self, name: str, state: str):
        if state not in _STATES:
            raise ValueError(f"A valve state is one of {', '.join(_STATES)}, not {state!r}.")
        self._valves[name] = state

    # --- readings (gauge psi) ---------------------------------------------------------------------------------

    @property
    def stored_psi(self) -> float:
        return (self._stored_abs() - ATM) / PSI

    @property
    def working_psi(self) -> float:
        return (self._supply_abs() - ATM) / PSI

    @property
    def compressor_on(self) -> bool:
        return self._on

    @property
    def compressor_amps(self) -> float:
        return self._compressor.amps(self.stored_psi) if self._on else 0.0

    @property
    def switch_calls_for_air(self) -> bool:
        return self._calling

    def chamber_psi(self, name: str) -> tuple[float, float]:
        """(cap side, rod side) gauge psi at the last step's extension."""
        if not self._primed:
            return (0.0, 0.0)           # nothing has stepped yet; the first step fills the chambers
        cap, rod = self._chambers[name]
        x, s = self._x[name], self._specs[name].stroke
        return ((cap.pressure(cap.volume(x)) - ATM) / PSI,
                (rod.pressure(rod.volume(s - x)) - ATM) / PSI)

    def _stored_abs(self) -> float:
        return self._tank_mass * R_AIR * T_AIR / self._tank_volume

    def _supply_abs(self) -> float:
        return min(self.config.working_psi * PSI + ATM, self._stored_abs())

    # --- stepping ---------------------------------------------------------------------------------------------

    def _prime(self, extension: dict[str, float]):
        """Fill every chamber to the pressure its valve's state feeds, and the other to atmosphere."""
        supply = self._supply_abs()
        for name, spec in self._specs.items():
            x = min(max(extension.get(name, 0.0), 0.0), spec.stroke)
            cap, rod = self._chambers[name]
            feeds_cap = self._valves[name] == "extend"
            cap.mass = self._mass_at(supply if feeds_cap else ATM, cap.volume(x))
            rod.mass = self._mass_at(ATM if feeds_cap else supply, rod.volume(spec.stroke - x))
            self._x[name] = x
        self._primed = True

    def step(self, dt: float, extension: dict[str, float], enabled: bool, closed_loop: bool) -> dict[str, float]:
        """Advance the air by dt seconds with the cylinders at `extension` (m). Returns each force in N."""
        if not self._primed:
            self._prime(extension)
        h = dt / SUBSTEPS
        for _ in range(SUBSTEPS):
            self._update_switch()
            self._on = (self._compressor is not None and enabled and closed_loop and self._calling)
            if self._on:
                cfm = self._compressor.flow_cfm(self.stored_psi)
                self._tank_mass += cfm * CFM * RHO_ANR * h
            for name, spec in self._specs.items():
                x = min(max(extension.get(name, self._x[name]), 0.0), spec.stroke)
                self._x[name] = x
                self._step_cylinder(name, spec, x, h)
        forces = {}
        for name, spec in self._specs.items():
            cap, rod = self._chambers[name]
            x = self._x[name]
            p_cap = cap.pressure(cap.volume(x)) - ATM
            p_rod = rod.pressure(rod.volume(spec.stroke - x)) - ATM
            forces[name] = p_cap * cap.area - p_rod * rod.area
        return forces

    def _update_switch(self):
        p = self.stored_psi
        if p >= self.config.switch_off_psi:
            self._calling = False
        elif p < self.config.switch_on_psi:
            self._calling = True

    def _step_cylinder(self, name: str, spec: CylinderSpec, x: float, h: float):
        state = self._valves[name]
        if state == "hold":
            return
        cap, rod = self._chambers[name]
        fed, vented = (cap, rod) if state == "extend" else (rod, cap)
        v_fed = fed.volume(x if fed is cap else spec.stroke - x)
        v_vented = vented.volume(x if vented is cap else spec.stroke - x)

        # Fill the fed side from the regulator, up to the regulator's pressure and no further. The air comes
        # out of the tanks. The regulator doesn't relieve, so a side above it gets no flow either way.
        supply = self._supply_abs()
        p_fed = fed.pressure(v_fed)
        dm = _flow(supply, p_fed, spec.conductance) * h
        dm = min(dm, max(0.0, self._mass_at(supply, v_fed) - fed.mass), self._tank_mass)
        fed.mass += dm
        self._tank_mass -= dm

        # Vent the other side to the atmosphere, down to atmosphere and no further (or up, if it is below).
        p_vented = vented.pressure(v_vented)
        if p_vented > ATM:
            dm = min(_flow(p_vented, ATM, spec.conductance) * h, vented.mass - self._mass_at(ATM, v_vented))
            vented.mass -= max(dm, 0.0)
        elif p_vented < ATM:
            dm = min(_flow(ATM, p_vented, spec.conductance) * h, self._mass_at(ATM, v_vented) - vented.mass)
            vented.mass += max(dm, 0.0)
