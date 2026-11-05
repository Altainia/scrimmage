"""Grip material on a part's bodies: what a grabber's pads are covered with, for testing the grip ("would grip tape
help?"). A part folder's own edits (scrimmage-edits.toml, parts/overlay.py) name it per body, written by the Part tab
(`set_grip`); a config's path part may override it:

    [body.pad_left]                       # scrimmage-edits.toml
    grip = "nitrile"                      # a GRIP_MATERIALS key, a number: my own (a measured) friction, or a
    #                                       friction measurement file, "pad.measure.toml" (relative to this file)

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

from ..measure.use import is_measurement
from ..i18n import N_, t, translated
from .model import Part, PartError, Where

GRIP_PRIORITY = 1                     # above a piece's (and every field geom's) 0, as the climber's hooks
GRIP_SOLREF = (0.004, 1.0)            # a rigid piece's firm contact (testrange/build.py, RIGID_SOLREF); a pad's own
#                                       solref governs at GRIP_PRIORITY, so without it the pad's contact would be
#                                       MuJoCo's soft default and a squeezed coral would creep down the pads
MUJOCO_FRICTION = 1.0                 # MuJoCo's default sliding friction: a geom's when neither a grip nor part.toml sets one
MY_OWN_MAX = 3.0                      # the largest measured friction taken (rubber on rubber is about 1 to 2)

_HERE = Path(__file__).resolve().parent.parent
_BENCH = tomllib.loads((_HERE / "bench" / "presets.toml").read_text(encoding="utf-8"))["material"]
_SURFACE = tomllib.loads((_HERE / "launcher" / "presets.toml").read_text(encoding="utf-8"))["surface"]

# key -> (label, sliding friction against a rigid piece); a label is N_(), shown with translated()
GRIP_MATERIALS: dict[str, tuple[str, float]] = {
    # estimate: rubber on a hard surface, the lower half of the published rubber-on-concrete and rubber-on-cardboard
    # ranges (0.5 to 0.85); the range's rubber surface (launcher/presets.toml [surface.rubber]), read from there
    "rubber": (N_("Rubber"), float(_SURFACE["rubber"]["friction"])),
    # estimate: rubber on a hard plastic piece, 0.7 to 1.0 typical; the bench's nitrile rigid_friction
    # (bench/presets.toml [material.nitrile]), read from there. As rubber: the estimates can't tell them apart
    "nitrile": (N_("Nitrile"), float(_BENCH["nitrile"]["rigid_friction"])),
    # estimate: a grit-coated tape grips a hard plastic piece above plain rubber (0.7 to 1.0 typical); taken past
    # the top of that range. No other table has it
    "grip-tape": (N_("Grip tape"), 1.1),
    # estimate: smooth plastic on a hard plastic piece, 0.3 to 0.5 typical; the bench's polycarbonate rigid_friction
    "polycarbonate": (N_("Polycarbonate"), float(_BENCH["polycarbonate"]["rigid_friction"])),
    # estimate: bare aluminum on a hard plastic piece, 0.3 to 0.4 typical; the bench's aluminum rigid_friction
    "aluminum": (N_("Aluminum"), float(_BENCH["aluminum"]["rigid_friction"])),
    # estimate: UHMW on a hard plastic piece, 0.15 to 0.25 typical (a low-friction guide material); the bench's
    # UHMW rigid_friction
    "hdpe": (N_("HDPE / UHMW"), float(_BENCH["UHMW"]["rigid_friction"])),
}
MEASURE = N_("measure yours with a sled pull, docs/test-tab.md")     # shown with translated()


def _number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def measured_grip(grip: str, where: str, base) -> tuple[float, object]:
    """The sliding friction a friction measurement file `grip` gives (found from `base`, the folder of the file that
    names it), and its Provenance (measure/file.py)."""
    from ..measure.file import MeasureError
    from ..measure.use import friction_value
    try:
        mu, prov = friction_value(grip, where, base=base, key="grip", needs="a grip")
    except MeasureError as e:
        raise PartError(str(e), where=where) from None
    if not (math.isfinite(mu) and 0 < mu <= MY_OWN_MAX):
        raise PartError(t("{where}: grip = \"{grip}\" measures a friction of {mu}, which isn't one to use: above 0 and "
                          "at most {most}; check its sled pulls", where=where, grip=grip, mu=f"{mu:g}",
                          most=f"{MY_OWN_MAX:g}"), where=where)
    return mu, prov


def grip_friction(grip: str | float, where: str, base=None) -> float:
    """The sliding friction of `grip`: a GRIP_MATERIALS key, a number above 0 and at most MY_OWN_MAX (my own), or a
    friction measurement file (`pad.measure.toml`, found from `base`: measured_grip)."""
    if is_measurement(grip):
        return measured_grip(grip, where, base if base is not None else Path("."))[0]
    if _number(grip):
        if not (math.isfinite(grip) and 0 < grip <= MY_OWN_MAX):
            raise PartError(t("{where}: grip {grip} isn't a friction to use; give a number above 0 and at most {most} "
                              "(a measured friction)", where=where, grip=repr(grip), most=f"{MY_OWN_MAX:g}"),
                            where=where)
        return float(grip)
    if isinstance(grip, str) and grip in GRIP_MATERIALS:
        return GRIP_MATERIALS[grip][1]
    shown = f'"{grip}"' if isinstance(grip, str) else repr(grip)
    raise PartError(t("{where}: grip {grip} isn't a grip material; pick one of {materials}, or give a number (a "
                      "measured friction)", where=where, grip=shown, materials=", ".join(GRIP_MATERIALS)), where=where)


def apply_grip(part: Part, grips: dict[str, str | float], where: str, base=None) -> tuple[Part, list[str]]:
    """The part with each body of `grips` (body -> grip) given its material, and warnings. An unknown body is a
    PartError; a body that touches nothing is a warning (its grip does nothing). A measurement file is found from
    `base`, the folder of the file that names it (default the part's folder), and its provenance kept in the part's
    grip_provenance."""
    if not isinstance(grips, dict):
        raise PartError(t("{where}: grip must be a table of body = material, not {grip}", where=where, grip=repr(grips)),
                        where=where)
    base = base if base is not None else part.base_dir if part.base_dir is not None else Path(".")
    names = [b.name for b in part.bodies]
    frictions, provenance = {}, {}
    for name, grip in grips.items():
        if name not in names:
            raise PartError(t("{where}: grip: no body {body} in the part; bodies: {bodies}", where=where,
                              body=repr(name), bodies=", ".join(names)), where=where)
        at = Where(f"{where}: body {name}", getattr(where, "part", None))
        if is_measurement(grip):
            frictions[name], provenance[name] = measured_grip(grip, at, base)
        else:
            frictions[name] = grip_friction(grip, at)
    warns: list[str] = []
    bodies = []
    for b in part.bodies:
        if b.name not in grips:
            bodies.append(b)
            continue
        mu = frictions[b.name]
        touch = [g.collide and not g.tread for g in b.geoms]
        if not any(touch):
            warns.append(f"{where}: body {b.name} has no geom that touches anything, so its grip does nothing")
        bodies.append(replace(b, geoms=[replace(g, friction=mu, priority=GRIP_PRIORITY, solref=GRIP_SOLREF) if t
                                        else g for g, t in zip(b.geoms, touch)]))
    kept = {k: p for k, p in part.grip_provenance.items() if k not in grips}
    out = replace(part, bodies=bodies, warnings=list(part.warnings) + warns,
                  grips={**part.grips, **copy.deepcopy(grips)}, grip_provenance={**kept, **provenance})
    return out, warns


def set_grip(folder: Path, body: str, grip: str | float | None) -> None:
    """Write `[body.<body>] grip` in the part folder's scrimmage-edits.toml (None removes it, and the body's table if
    nothing else is in it, and the file when nothing at all is), keeping the file's other keys and comments. The grip is checked first (a PartError)."""
    import tomlkit
    from ..cad.settings import replace_file
    from .overlay import OVERLAY
    if grip is not None:
        grip_friction(grip, f"body {body}", Path(folder))
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
    text = tomlkit.dumps(doc)
    if not text.strip():            # nothing left in it: no file (as the Part tab's plugin mode leaves it)
        path.unlink(missing_ok=True)
        return
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False,
                                         encoding="utf-8", newline="\n") as f:
            tmp = f.name
            f.write(text)
        replace_file(tmp, path)
    except OSError:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)
        raise


def measured_label(mu: float | None, prov) -> str:
    """A measured grip's words: its friction, the file it came from, and why it isn't good enough when it isn't."""
    from ..measure.labels import not_good, source
    if mu is not None:
        said = t("measured, friction {mu} ({source})", mu=f"{mu:g}", source=source(prov))
    else:
        said = t("measured ({source})", source=source(prov))
    return said if prov.good else t("{said}, {why} (UNCALIBRATED)", said=said, why=not_good(prov))


def _label(grip: str | float) -> str:
    if _number(grip):
        return t("my own number, friction {mu} (UNCALIBRATED unless you measured it)", mu=f"{float(grip):g}")
    label, mu = GRIP_MATERIALS[grip]
    label = translated(label)
    word = label[0].lower() + label[1:] if label[1:2].islower() else label     # "nitrile", "HDPE / UHMW"
    return t("{material}, friction {mu} (UNCALIBRATED estimate; {measure})", material=word, mu=f"{mu:g}",
             measure=translated(MEASURE))


def pad_frictions(world) -> dict[str, float | None]:
    """"<instance>.<body>" -> the sliding friction part.toml gives the body's touching geoms (treads aside; the largest
    when they differ, a geom with none counting MuJoCo's default), or None when none sets one (MuJoCo's default,
    MUJOCO_FRICTION), for each body of a path part with a geom that touches anything and no grip set."""
    built = getattr(world, "parts", None)
    if built is None:
        return {}
    out = {}
    for inst in built.instances:
        if inst.source.startswith("stock "):
            continue
        for b in inst.part.bodies:
            touch = [g for g in b.geoms if g.collide and not g.tread]
            if touch and b.name not in inst.part.grips:
                set_ = [g.friction for g in touch if g.friction is not None]
                out[f"{inst.name}.{b.name}"] = (max(g.friction if g.friction is not None else MUJOCO_FRICTION
                                                    for g in touch) if set_ else None)
    return dict(sorted(out.items()))


def grip_notes(world) -> list[str]:
    """One line per gripped body of the world's robot ([[part]] robots only), e.g. `claw.pad_left grip: nitrile,
    friction 0.8 (UNCALIBRATED estimate; ...)`."""
    built = getattr(world, "parts", None)
    if built is None:
        return []
    out = []
    for inst in built.instances:
        for body, grip in inst.part.grips.items():
            prov = inst.part.grip_provenance.get(body)
            if prov is None:
                out.append(t("{part}.{body} grip: {grip}", part=inst.name, body=body, grip=_label(grip)))
                continue
            mu = next((g.friction for b in inst.part.bodies if b.name == body for g in b.geoms
                       if g.priority == GRIP_PRIORITY), None)    # None: the body touches nothing
            out.append(t("{part}.{body} grip: {grip}", part=inst.name, body=body, grip=measured_label(mu, prov)))
    return out
