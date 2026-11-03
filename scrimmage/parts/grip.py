"""Grip material on a part's bodies: what a grabber's pads are covered with, for testing the grip ("would grip tape
help?"). A part folder's own edits (scrimmage-edits.toml, parts/overlay.py) name it per body, written by the Part tab
(`set_grip`); a config's path part may override it:

    [body.pad_left]                       # scrimmage-edits.toml
    grip = "nitrile"                      # a GRIP_MATERIALS key, or a number: my own (a measured) friction

    [[part]]                              # the robot config
    path = "parts/claw"
    grip = { pad_left = "grip-tape", pad_right = 1.05 }

Each colliding geom of a gripped body (treads excepted) gets the material's sliding friction, a firm contact
(GRIP_SOLREF, a rigid piece's) and contact priority GRIP_PRIORITY. A piece's geoms have priority 0, so the pad's
friction and contact govern their contacts: at equal priority MuJoCo would take the larger of the two frictions (a
slick pad on a PVC coral would grip like PVC) and the piece's contact (its solmix outweighs every robot geom's).

The frictions are not measurements: friction against FRC pieces isn't published. They are the same estimates the
bench uses for a shape against a rigid piece (scrimmage/bench/presets.toml, `rigid_friction`) and the test range's
rubber surface (scrimmage/launcher/presets.toml), read from those files so the tables can't disagree; a run says so
(`grip_notes`, UNCALIBRATED). Measure your own pad with a sled pull and give the number.
"""

from __future__ import annotations

import copy
import math
import os
import tempfile
import tomllib
from dataclasses import replace
from pathlib import Path

from .model import Part, PartError

GRIP_PRIORITY = 1                     # above a piece's (and every field geom's) 0, as the climber's hooks
GRIP_SOLREF = (0.004, 1.0)            # a rigid piece's firm contact (testrange/build.py, RIGID_SOLREF); a pad's own
#                                       solref governs at GRIP_PRIORITY, so without it the pad's contact would be
#                                       MuJoCo's soft default and a squeezed coral would creep down the pads
MY_OWN_MAX = 3.0                      # the largest measured friction taken (rubber on rubber is about 1 to 2)

_HERE = Path(__file__).resolve().parent.parent
_BENCH = tomllib.loads((_HERE / "bench" / "presets.toml").read_text(encoding="utf-8"))["material"]
_SURFACE = tomllib.loads((_HERE / "launcher" / "presets.toml").read_text(encoding="utf-8"))["surface"]

# key -> (label, sliding friction against a rigid piece)
GRIP_MATERIALS: dict[str, tuple[str, float]] = {
    # estimate: rubber on a hard surface, the lower half of the published rubber-on-concrete and rubber-on-cardboard
    # ranges (0.5 to 0.85); the range's rubber surface (launcher/presets.toml [surface.rubber]), read from there
    "rubber": ("Rubber", float(_SURFACE["rubber"]["friction"])),
    # estimate: rubber on a hard plastic piece, 0.7 to 1.0 typical; the bench's nitrile rigid_friction
    # (bench/presets.toml [material.nitrile]), read from there. As rubber: the estimates can't tell them apart
    "nitrile": ("Nitrile", float(_BENCH["nitrile"]["rigid_friction"])),
    # estimate: a grit-coated tape grips a hard plastic piece above plain rubber (0.7 to 1.0 typical); taken past
    # the top of that range. No other table has it
    "grip-tape": ("Grip tape", 1.1),
    # estimate: smooth plastic on a hard plastic piece, 0.3 to 0.5 typical; the bench's polycarbonate rigid_friction
    "polycarbonate": ("Polycarbonate", float(_BENCH["polycarbonate"]["rigid_friction"])),
    # estimate: bare aluminum on a hard plastic piece, 0.3 to 0.4 typical; the bench's aluminum rigid_friction
    "aluminum": ("Aluminum", float(_BENCH["aluminum"]["rigid_friction"])),
    # estimate: UHMW on a hard plastic piece, 0.15 to 0.25 typical (a low-friction guide material); the bench's
    # UHMW rigid_friction
    "hdpe": ("HDPE / UHMW", float(_BENCH["UHMW"]["rigid_friction"])),
}
MEASURE = "measure yours with a sled pull, docs/test-tab.md"


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def grip_friction(grip: str | float, where: str) -> float:
    """The sliding friction of `grip`: a GRIP_MATERIALS key, or a number above 0 and at most MY_OWN_MAX (my own)."""
    if _number(grip):
        if not (math.isfinite(grip) and 0 < grip <= MY_OWN_MAX):
            raise PartError(f"{where}: grip {grip!r} isn't a friction to use; give a number above 0 and at most "
                            f"{MY_OWN_MAX:g} (a measured friction)")
        return float(grip)
    if isinstance(grip, str) and grip in GRIP_MATERIALS:
        return GRIP_MATERIALS[grip][1]
    shown = f'"{grip}"' if isinstance(grip, str) else repr(grip)
    raise PartError(f"{where}: grip {shown} isn't a grip material; pick one of {', '.join(GRIP_MATERIALS)}, or give "
                    "a number (a measured friction)")


def apply_grip(part: Part, grips: dict[str, str | float], where: str) -> tuple[Part, list[str]]:
    """The part with each body of `grips` (body -> grip) given its material, and warnings. An unknown body is a
    PartError; a body that touches nothing is a warning (its grip does nothing)."""
    if not isinstance(grips, dict):
        raise PartError(f"{where}: grip must be a table of body = material, not {grips!r}")
    names = [b.name for b in part.bodies]
    for name, grip in grips.items():
        if name not in names:
            raise PartError(f"{where}: grip: no body {name!r} in the part; bodies: {', '.join(names)}")
        grip_friction(grip, f"{where}: body {name}")
    warns: list[str] = []
    bodies = []
    for b in part.bodies:
        if b.name not in grips:
            bodies.append(b)
            continue
        mu = grip_friction(grips[b.name], f"{where}: body {b.name}")
        touch = [g.collide and not g.tread for g in b.geoms]
        if not any(touch):
            warns.append(f"{where}: body {b.name} has no geom that touches anything, so its grip does nothing")
        bodies.append(replace(b, geoms=[replace(g, friction=mu, priority=GRIP_PRIORITY, solref=GRIP_SOLREF) if t
                                        else g for g, t in zip(b.geoms, touch)]))
    out = replace(part, bodies=bodies, warnings=list(part.warnings) + warns,
                  grips={**part.grips, **copy.deepcopy(grips)})
    return out, warns


def set_grip(folder: Path, body: str, grip: str | float | None) -> None:
    """Write `[body.<body>] grip` in the part folder's scrimmage-edits.toml (None removes it, and the body's table if
    nothing else is in it), keeping the file's other keys and comments. The grip is checked first (a PartError)."""
    import tomlkit
    from ..cad.settings import replace_file
    from .overlay import OVERLAY
    if grip is not None:
        grip_friction(grip, f"body {body}")
        if _number(grip):
            grip = float(grip)
    path = Path(folder) / OVERLAY
    doc = tomlkit.parse(path.read_text(encoding="utf-8")) if path.is_file() else tomlkit.document()
    bodies = doc.get("body")
    if grip is None:
        if bodies is None or body not in bodies:
            return
        bodies[body].pop("grip", None)
        if not bodies[body]:
            del bodies[body]
        if not bodies:
            del doc["body"]
    else:
        if bodies is None:
            doc["body"] = bodies = tomlkit.table(is_super_table=True)
        if body not in bodies:
            bodies[body] = tomlkit.table()
        bodies[body]["grip"] = grip
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False,
                                         encoding="utf-8", newline="\n") as f:
            tmp = f.name
            f.write(tomlkit.dumps(doc))
        replace_file(tmp, path)
    except OSError:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _label(grip: str | float) -> str:
    if _number(grip):
        return f"my own number, friction {float(grip):g} (UNCALIBRATED unless you measured it)"
    label, mu = GRIP_MATERIALS[grip]
    word = label[0].lower() + label[1:] if label[1:2].islower() else label     # "nitrile", "HDPE / UHMW"
    return f"{word}, friction {mu:g} (UNCALIBRATED estimate; {MEASURE})"


def grip_notes(world) -> list[str]:
    """One line per gripped body of the world's robot ([[part]] robots only), e.g. `claw.pad_left grip: nitrile,
    friction 0.8 (UNCALIBRATED estimate; ...)`."""
    built = getattr(world, "parts", None)
    if built is None:
        return []
    return [f"{inst.name}.{body} grip: {_label(grip)}" for inst in built.instances
            for body, grip in inst.part.grips.items()]
