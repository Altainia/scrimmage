"""What a test run rests on and adds up to, in words: the run's notes (`run_notes`: the grips, the field pieces'
measured friction, the air's estimates, the clearance a stock part doesn't count) and the result's goal, clearance
and air lines. It never imports run.py (RunResult only for type checking); run.py re-exports every name here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import i18n, units
from ..checks import shown
from .clearance import NEVER_WITHIN_IN, uncounted_note
from .conditions import _amount, _conds
from .program import Program

if TYPE_CHECKING:
    from .run import RunResult


GRIP_CONDITIONS = ("held", "rests_on", "seated")     # a test of these rests on the grip's friction
DEFAULT_GRIP_NOTE = i18n.N_("grip: the pads have MuJoCo's default friction 1.0 — about grip tape's; the larger of "
                            "pad and piece applies — an UNCALIBRATED estimate; pick a grip material for them in the "
                            "Part tab, or measure yours with a sled pull (docs/test-tab.md)")


def default_grip_note(world) -> str:
    """The note for a test of held, rests on or seated with no grip anywhere: DEFAULT_GRIP_NOTE, or, when
    part.toml sets a pad's friction, the pads' own values (MuJoCo's default 1.0 for a pad it leaves alone)."""
    from ..parts.grip import pad_frictions
    pads = pad_frictions(world)
    if all(mu is None for mu in pads.values()):
        return i18n.translated(DEFAULT_GRIP_NOTE)
    mus = set(pads.values())
    if len(mus) == 1:
        return i18n.t("grip: the pads have friction {friction}, which part.toml gives them; the larger of pad and "
                      "piece applies — an UNCALIBRATED estimate; pick a grip material for them in the Part tab, or "
                      "measure yours with a sled pull (docs/test-tab.md)", friction=f"{mus.pop():g}")
    each = ", ".join(i18n.t("{body} {friction}", body=b, friction=f"{mu:g}") if mu is not None
                     else i18n.t("{body} MuJoCo's default 1.0", body=b) for b, mu in pads.items())
    return i18n.t("grip: the pads have the friction part.toml gives them ({pads}); the larger of pad and piece "
                  "applies — an UNCALIBRATED estimate; pick a grip material for them in the Part tab, or measure "
                  "yours with a sled pull (docs/test-tab.md)", pads=each)


PNEUMATIC_ESTIMATES = i18n.N_("pneumatic numbers are estimates")      # after measure/labels.py's mark


def pneumatic_note(cylinders) -> str:
    """What a robot's air rests on: each cylinder's valve conductance, measured (its CylinderSpec's provenance, from a
    stock cylinder's `measured` file) or still an estimate."""
    from ..measure.labels import line
    keys = tuple(f"{c.name} valve conductance" for c in cylinders)
    inputs = {k: c.provenance for k, c in zip(keys, cylinders)}
    return i18n.t("{line}; see docs/pneumatics.md.", line=line(inputs, keys, estimates=PNEUMATIC_ESTIMATES))


def piece_notes(world) -> list[str]:
    """One line per field piece whose friction a measurement file gives ([piece.<name>] friction = "<file>"), worded
    as a measured grip's: "crate piece: measured, friction 0.8 (tread.measure.toml, J. Smith, 2026-11-10, 3
    trials)"."""
    from ..parts.grip import measured_label
    tr = getattr(world, "test_range", None)
    kinds = getattr(getattr(tr, "range", None), "kinds", None) or {}
    return [f"{kind.name} piece: {measured_label(kind.friction, kind.provenance['friction'])}"
            for kind in kinds.values() if "friction" in kind.provenance]


def run_notes(world, program: Program) -> list[str]:
    """What the run's numbers rest on: a line per gripped body (parts/grip.py's grip_notes); a test of held, rests on
    or seated with no grip anywhere gets default_grip_note, so its grip isn't read as measured either. A field piece
    whose friction is measured gets piece_notes' line. A robot with cylinders gets pneumatic_note. A test that
    measures an element's clearance, on a robot with stock parts whose collide is off, gets clearance.uncounted_note:
    those parts aren't measured."""
    from ..parts.grip import grip_notes
    notes = grip_notes(world)
    if not notes and any(_conds(program.blocks, k) for k in GRIP_CONDITIONS):
        notes = [default_grip_note(world)]
    notes += piece_notes(world)
    if getattr(world, "pneumatics", None) is not None:
        notes.append(pneumatic_note(world.parts.cylinders))
    uncounted = uncounted_note(world, program)
    if uncounted is not None:
        notes.append(uncounted)
    rng = getattr(world, "parts", None) and world.parts.range
    if rng is not None:         # the range's warnings (a goal behind the walls), in the setting being worded
        notes += [str(w) if units.is_machine() else shown(w) for w in rng.warnings]
    return notes


def goal_lines(result: RunResult) -> list[str]:
    """The run's goal counts as lines, "scored 5 in hub_blue", one per goal with a ball in it (none without a
    [shooter])."""
    return [i18n.t("scored {count} in {goal}", count=n, goal=name) for name, n in (result.goals or {}).items() if n]


def piece_log(tr) -> list[dict]:
    """Each game piece that took part in the run on range runtime `tr` (testrange/balls.py took_part: a preload, a fed
    piece, a floor piece that moved): its ball log (log_dict: SI, None for unset), its kind, and its last position as
    a list (as result.json keeps it). Spares and untouched floor pieces are left out."""
    from ..testrange.balls import took_part
    starts, radius, kinds = tr.range.floor_balls, tr.balls.radius, tr.layout.kinds
    out = []
    for b in tr.ball_log():
        i = b["id"]
        start = starts[i] if b["origin"] == "floor" and i < len(starts) else None
        if not took_part(b, start, float(radius[i])):
            continue
        row = {**b, "kind": kinds[i] if i < len(kinds) else None}
        for k in ("last_pos", "robot_pose", "robot_velocity"):
            if row.get(k) is not None:
                row[k] = [float(v) for v in row[k]]
        out.append(row)
    return out


def piece_lines(result: RunResult) -> list[str]:
    """The run's pieces as a table (a header, then a line each): its number, kind, where it came from, when it was
    fired and its exit speed (blank if it never was), its outcome, and where it went (scenario_runner's ball table's
    last column: the goal and time it scored, where it rests or left the field, what it hit first). None off a
    range, or when no piece took part. Its numbers follow the Units setting, as the command line's do."""
    from ..scenario_runner import _ball_result
    if not result.pieces:
        return []
    speed = units.unit_name("speed")
    head = [i18n.t("#"), i18n.t("kind"), i18n.t("from"), i18n.t("fired s"), i18n.t("exit {unit}", unit=speed),
            i18n.t("outcome"), i18n.t("where")]
    rows = []
    for p in result.pieces:
        fired = "-" if p.get("fired_t") is None else f"{p['fired_t']:.2f}"
        exit_speed = "-" if p.get("exit_speed") is None else f"{units.to_display(p['exit_speed'], 'speed'):.2f}"
        rows.append([str(p["id"]), str(p.get("kind") or "-"), p["origin"], fired, exit_speed, p["outcome"],
                     _ball_result({**p, "last_pos": tuple(p["last_pos"]) if p.get("last_pos") else None})])
    widths = [max(len(r[k]) for r in [head, *rows]) for k in range(len(head) - 1)]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)) + "  " + r[-1] for r in [head, *rows]]


def clearance_lines(result: RunResult) -> list[str]:
    """The run's closest approaches as lines, one per element it measured: "closest to trench: 0.82 in at 3.1 s", or
    "trench: never within 20 in" (none for a run that measured none)."""
    if units.is_machine():                  # the old words, kept for machine text (units.machine_text)
        return [f"{e}: never within {NEVER_WITHIN_IN} in" if c["min_in"] is None else
                f"closest to {e}: {c['min_in']:.2f} in at {c['t']:.1f} s" for e, c in (result.clearance or {}).items()]
    return [i18n.t("{element}: never within {distance}", element=e, distance=_amount(NEVER_WITHIN_IN, "in"))
            if c["min_in"] is None else
            i18n.t("closest to {element}: {distance} at {t} s", element=e, distance=_amount(c["min_in"], "in", fewer=1),
                   t=f"{c['t']:.1f}")
            for e, c in (result.clearance or {}).items()]


def air_lines(result: RunResult) -> list[str]:
    """The run's air as lines (none without cylinders): "air: 120 psi at the start, 84 psi at the end, lowest 81 psi
    at 14.2 s; the compressor ran 9.0 s", and when the tanks fell below the working pressure, a warning (Review
    Focus 5)."""
    a = result.air
    if a is None:
        return []
    def psi(x: float) -> str:
        return f"{x:.0f} psi" if units.is_machine() else _amount(x, "psi", fewer=1)

    lines = [i18n.t("air: {start} at the start, {end} at the end, lowest {low} at {t} s; the compressor ran {ran} s",
                    start=psi(a["start_psi"]), end=psi(a["end_psi"]), low=psi(a["low_psi"]), t=f"{a['low_t']:.1f}",
                    ran=f"{a['compressor_s']:.1f}")]
    if a["below_working_t"] is not None:
        lines.append(i18n.t("the tanks fell below the {working} working pressure at {t} s: the cylinders pushed weaker "
                            "from then on", working=_amount(a["working_psi"], "psi"), t=f"{a['below_working_t']:.1f}"))
    return lines
