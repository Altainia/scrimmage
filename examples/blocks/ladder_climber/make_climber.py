"""Makes this example's part from climber.step the way a student does it in the Part tab: each edit below is one
choice in the tab (scrimmage/cad/edits.py's apply_edit, the same call the tab makes), then the part folder is built
and the stand-in robot is made the way the tab's "Test it" makes it (the part on the stock chassis's root body, where
the CAD has it). The outputs are committed; re-run this only to change them:

    .venv/bin/python examples/blocks/ladder_climber/make_climber.py

climber.step is a copy of tests/data/cad/crank_climber.step (made by tests/make_cad_fixtures.py). It is drawn in
robot coordinates, so the import says "This isn't a whole robot" and uses the CAD's axes as they are. Points are
meters, in those coordinates.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

RATIO = 200.0                    # NEO turns per crank turn: 6 V spins a free NEO about 2840 rpm, the crank 0.24 rev/s
HOOK_SPRING = [3.0, -15.0]       # N*m/rad, and its rest (degrees): it holds the hook on its stop with 0.8 N*m
HOOK_FOLD = [0.0, 95.0]          # degrees: the stop at 0 holds the finger; it folds tip down to pass a rung
STROKE = 0.4                     # m each arm slides: twice the crank pins' 0.2 m radius
P = "CrankClimber"               # the STEP file's top assembly: piece keys are P/<sub-assembly>/<piece>


def _side_of_cylinder(piece) -> int:
    """A triangle on a cylinder's round side (the pins and the shaft turn about y): where a student clicks for "Pick
    the axis"."""
    import numpy as np
    tv = piece.v[piece.f]
    n = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
    area = np.linalg.norm(n, axis=1)
    side = np.abs(n[:, 1]) / np.maximum(area, 1e-30)
    return int(np.argmin(side - 1e-6 * area / area.max()))


def _top_face(piece) -> int:
    """A triangle on a bar's top end: its normal is the way the bar slides."""
    return int(piece.v[piece.f].mean(axis=1)[:, 2].argmax())


def edits(src) -> list[tuple[str, dict]]:
    """(op, args): the student's edits, in order. Axes and pivots come from clicks on the CAD, as "Pick the axis"
    takes them (scrimmage/cad/axis.py's fit_axis): a pin's round side for a turn, a bar's top face for a slide."""
    from scrimmage.cad.axis import fit_axis

    pieces = {p.key: p for p in src.pieces}

    def pin(key):               # (point, axis) of the pin or shaft `key`
        point, axis = fit_axis(pieces[f"{P}/{key}"], _side_of_cylinder(pieces[f"{P}/{key}"]), "round")
        return [float(x) for x in point], [float(x) for x in axis]

    def slide(key):             # the direction the bar `key` slides in
        _, axis = fit_axis(pieces[f"{P}/{key}"], _top_face(pieces[f"{P}/{key}"]), "flat")
        return [float(x) for x in axis]

    def hinge(body, parent, key, **more):
        point, axis = pin(key)
        return ("set_joint", {"body": body, "parent": parent,
                              "joint": {"kind": "hinge", "anchor": point, "axis": axis, **more}})

    out = [
        ("set_frame", {"skip": True, "confirmed": True}),            # "This isn't a whole robot"
        # every piece is aluminum but the discs (polycarbonate: lighter for their size)
        ("set_material", {"pieces": list(pieces), "material": "aluminum"}),
        ("set_material", {"pieces": [f"{P}/crank/disc_L", f"{P}/crank/disc_R"], "material": "polycarbonate"}),
        # each hook's lip and back plate are bodies of their own, welded to the hook, so each touches things as its
        # own box
        *[("new_body", {"pieces": [f"{P}/hook_{s}/{p}_{s}"], "name": f"{p}_{s}"})
          for s in "AB" for p in ("lip", "back")],
        # the crank turns on its shaft; each rod turns on its crank pin
        hinge("crank", "base", "crank/shaft"),
        hinge("rod_A", "crank", "crank/crank_pin_A"),
        hinge("rod_B", "crank", "crank/crank_pin_B"),
    ]
    for s, travel in (("A", [-STROKE - 0.02, 0.02]), ("B", [-0.02, STROKE + 0.02])):
        point, _ = pin(f"arm_{s}/arm_pin_{s}")
        out += [
            # each arm slides along its bar, A from the top of its stroke and B from the bottom
            ("set_joint", {"body": f"arm_{s}", "parent": "base",
                           "joint": {"kind": "slide", "anchor": point, "axis": slide(f"arm_{s}/arm_{s}_bar"),
                                     "range": travel}}),
            # the rod's lower end is pinned to its arm: that closes the slider-crank loop
            ("add_loop", {"body1": f"rod_{s}", "body2": f"arm_{s}", "point": point}),
            # the hook turns on its pin at the end of the arm's bracket, its spring holding it on its stop
            hinge(f"hook_{s}", f"arm_{s}", f"hook_{s}/hook_pin_{s}", range=HOOK_FOLD, spring=HOOK_SPRING,
                  damping=0.02),
        ]
    out += [
        ("set_motor", {"body": "crank", "settings": {"motor": "NEO", "count": 1, "ratio": RATIO}}),
        # the hooks touch the rungs; nothing else on the climber touches anything
        *[("set_collision", {"body": f"{p}_{s}", "mode": "simple"}) for s in "AB" for p in ("hook", "lip", "back")],
    ]
    return out


def import_climber(step: Path, parts: Path):
    """(the ImportFile after the edits, the Source, messages) for `step` with its import in the folder `parts`."""
    from scrimmage.assembler.part_session import source_text
    from scrimmage.cad.edits import apply_edit
    from scrimmage.cad.settings import new_import, read_import
    from scrimmage.cad.source import load_source

    src = load_source(step)
    imp_path = parts / "climber.import.toml"
    imp = new_import(src, "climber", Path(source_text(step, imp_path)), parts)
    if imp_path.is_file():          # made before: it stays the same import, so its part folder is rebuilt in place
        old = read_import(imp_path)
        imp.import_id, imp.written = old.import_id, old.written
    messages = []
    for op, args in edits(src):
        imp, msgs = apply_edit(imp, op, args)
        messages += msgs
    return imp, src, messages


def make(folder: Path, step: Path | None = None) -> list[str]:
    """Import `step` (default: `folder`/climber.step) into `folder`/parts, build the part, and write the stand-in
    robot `folder`/climber-standin.toml as "Test it" makes it: the part on the chassis's root body where the CAD has it,
    its crank motor on a free PWM channel. Returns the edits' and the build's messages."""
    from scrimmage.assembler import library
    from scrimmage.assembler.server import _chassis_root_body
    from scrimmage.assembler.test_session import config_path, replace_text, standin_config
    from scrimmage.cad.build import build_parts, part_folder, write_parts
    from scrimmage.cad.settings import write_import

    folder = Path(folder)
    step = Path(step) if step is not None else folder / "climber.step"
    parts = folder / "parts"
    imp, src, messages = import_climber(step, parts)
    imp_path = parts / "climber.import.toml"
    result = build_parts(imp, src)
    write_parts(result, imp, imp_path)       # it records the folders it wrote in imp.written
    write_import(imp, imp_path)
    placed = [(config_path(part_folder(imp, name, imp_path), folder),
               library.cad_marker(part_folder(imp, name, imp_path))) for name in result.parts]
    slots = {name: [m.slot for m in built.part.motors] for name, built in result.parts.items()}
    replace_text(folder / "climber-standin.toml", standin_config(placed, _chassis_root_body(), slots))
    return messages + result.messages


if __name__ == "__main__":
    for line in make(HERE):
        print(line)
    sys.exit(0)
