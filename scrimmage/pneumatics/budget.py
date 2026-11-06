"""An air budget: how many strokes a robot's tanks and compressor can pay for in a match.

Estimates (UNCALIBRATED; its last line names what it rests on, BUDGET_MATTERS). Each stroke fills one side of a
cylinder from the regulator at the working pressure. The swept volume starts empty, so it fills at absolute pressure;
the dead volume (the port and fitting space) starts at one atmosphere, so only the gauge pressure adds air. All
counted as free air (air at one atmosphere).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .. import i18n
from ..measure.labels import line
from .model import ATM, CFM, COMPRESSORS, PSI, AirConfig, CylinderSpec


# What a budget rests on. A valve's measured conductance doesn't enter it (a budget counts air, not how fast it
# flows), and none of these takes a measurement file yet, so a budget is always UNCALIBRATED.
BUDGET_MATTERS = ("compressor flow", "pressure switch points", "tank volume", "dead volume")
def budget_line() -> str:
    """A budget's UNCALIBRATED line, in the language now (American English in the CLI)."""
    return i18n.t("{line}; see docs/pneumatics.md.", line=line({}, BUDGET_MATTERS, estimates=i18n.N_("estimates")))



@dataclass(frozen=True)
class Budget:
    per_cycle_l: dict[str, float]       # free-air liters for an extend plus a retract, at the working pressure
    stored_l: float                     # free air between the switch's top and the working pressure
    cycles_no_compressor: float
    compressor_l_per_match: float       # the mean flow between the switch points, times the match length
    cycles_per_match: float
    config: AirConfig
    match_s: float


def _cycle_l(config: AirConfig, c: CylinderSpec) -> float:
    gauge = config.working_psi * PSI
    a_cap = math.pi * c.bore ** 2 / 4
    a_ann = a_cap - math.pi * c.rod ** 2 / 4
    total = 0.0
    for area in (a_cap, a_ann):         # extend fills the cap side, retract fills the rod side
        total += area * c.stroke * (gauge + ATM) / ATM + c.dead_volume * gauge / ATM
    return total * 1000.0


def budget(config: AirConfig, cylinders: list[CylinderSpec], match_s: float = 150.0) -> Budget:
    per_cycle = {c.name: _cycle_l(config, c) for c in cylinders}
    stored = config.tanks * config.tank_ml / 1000.0 * (config.switch_off_psi - config.working_psi) * PSI / ATM
    total = sum(per_cycle.values())
    comp = COMPRESSORS.get(config.compressor)
    if comp is None:
        compressor = 0.0
    else:
        mid = (config.switch_on_psi + config.switch_off_psi) / 2
        compressor = comp.flow_cfm(mid) * CFM * match_s * 1000.0
    return Budget(per_cycle, stored, stored / total if total else math.inf, compressor,
                  (stored + compressor) / total if total else math.inf, config, match_s)


def full_cycles(cycles: float) -> str:
    """Whole cycles that each reach the working pressure: 4.8 is 4 (a fifth extend would start below it and push
    weaker), not 5 as rounding would say. A hair under a whole number (float error) counts as it."""
    return "unlimited" if math.isinf(cycles) else f"{math.floor(cycles + 1e-9):d}"


def format_budget(b: Budget) -> str:
    c = b.config
    lines = [f"Working pressure {c.working_psi:g} psi; the switch stops the compressor at {c.switch_off_psi:g} psi.",
             "Free air for one cycle (extend and retract):"]
    lines += [f"  {name}: {liters:.3f} L" for name, liters in b.per_cycle_l.items()]
    lines += [f"Stored air above the working pressure: {b.stored_l:.2f} L",
              f"Cycles from the tanks alone: {full_cycles(b.cycles_no_compressor)}",
              f"Compressor air in a {b.match_s:g} s match: {b.compressor_l_per_match:.1f} L"
              + (" (no compressor)" if c.compressor == "none" else ""),
              f"Cycles per match, tanks and compressor: {full_cycles(b.cycles_per_match)}",
              budget_line()]
    return "\n".join(lines)
