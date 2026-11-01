"""What the task panel shows, as plain data (no GUI imports; `overview` runs inside FreeCAD, the rest is plain Python).

`overview(doc)` reads the document's Assembly and returns a JSON-able dict:
    {"assembly": label,
     "components": [{"name", "label", "mass_kg"}],         # every component, document order; computed mass (no override)
     "bodies": [{"name", "comps": [component dicts]}],      # the exported bodies ([] when the tree can't be built)
     "joints": [{"name", "label", "type", "role", "limits"}],   # role: tree | loop | coupling | fixed | suppressed | broken
     "tree_joints": [{"name", "label", "type"}],            # Revolute/Slider joints a slot can go on
     "lcs_mounts": [{"name", "comp"}],                      # mount_* coordinate systems (name without the prefix)
     "problems": [...], "warnings": [...]}
`limits` is FreeCAD's (min, max) (degrees / mm; None for an open side) or None.
"""

import os
import re

from .errors import ExportError


def unique_name(base, used):
    """`base`, or `base_2`, `base_3`... — the first not in `used`."""
    name, k = base, 2
    while name in used:
        name, k = f"{base}_{k}", k + 1
    return name


def default_export_dir(file_name, assembly_label):
    """The suggested part folder: next to the saved document, named after the Assembly; "" for an unsaved one."""
    if not file_name:
        return ""
    return os.path.join(os.path.dirname(file_name), re.sub(r"[^A-Za-z0-9_]", "_", assembly_label) or "part")


def overview(doc):
    from . import mass, read, settings, tree
    asm = read.find_assembly(doc)
    e = read.read_assembly(asm)
    problems, warnings = list(e.problems), list(e.warnings)

    def comp_mass(c):
        try:
            return mass.body_inertial([c], {}, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)))[0]
        except ExportError:
            return 0.0

    comps = {n: {"name": n, "label": c.label, "mass_kg": comp_mass(c)} for n, c in e.components.items()}
    t = None
    try:
        st = settings.get(doc) or settings.ScrimmageSettings()
        t = tree.build(e, prefer=settings.slotted_joints(st))
        warnings += [w for w in t.warnings if w not in warnings]
    except ExportError as err:
        problems += [p for p in err.problems if p not in problems]

    roles = {}
    if t is not None:
        for b in t.bodies:
            if b.joint is not None:
                roles[b.joint.name] = "tree"
        for j, *_ in t.loops:
            roles[j.name] = "loop"
        for c, *_ in t.couplings:
            roles[c.name] = "coupling"
    joints = []
    for j in e.joints:
        if j.suppressed:
            role = "suppressed"
        elif j.comp1 is None or j.comp2 is None:
            role = "broken"
        elif j.type == "Fixed":
            role = "fixed"
        else:
            role = roles.get(j.name, "coupling" if j.type in tree.COUPLING_TYPES else "")
        joints.append({"name": j.name, "label": j.label, "type": j.type, "role": role,
                       "limits": list(j.limits) if j.limits is not None else None})
    tree_joints = [{"name": j["name"], "label": j["label"], "type": j["type"]} for j in joints
                   if j["role"] == "tree" or (t is None and j["type"] in tree.TREE_TYPES and j["role"] == "")]
    return {
        "assembly": asm.Label,
        "components": list(comps.values()),
        "bodies": [{"name": b.name, "comps": [comps[n] for n in b.comps]} for b in t.bodies] if t is not None else [],
        "joints": joints,
        "tree_joints": tree_joints,
        "lcs_mounts": [{"name": lbl[len("mount_"):], "comp": comp} for lbl, _, comp in e.lcs_mounts],
        "problems": problems,
        "warnings": warnings,
    }


def summary_text(s):
    """The export summary (export.export's dict) as text for the panel."""
    lines = [f"Exported '{s['part']}' to {s['out_dir']}"]

    def names(title, items):
        if items:
            lines.append(f"{title} ({len(items)}): {', '.join(items)}")

    names("Bodies", s.get("bodies", []))
    names("Joints", s.get("joints", []))
    for title, key in (("Loops", "loops"), ("Couplings", "couplings")):
        if s.get(key):
            lines.append(f"{title}: {s[key]}")
    names("Slots", s.get("slots", []))
    names("Mounts", s.get("mounts", []))
    lines.append(f"Total mass: {s.get('mass_kg', 0.0):.3f} kg")
    if s.get("warnings"):
        lines.append("Warnings:")
        lines += [f"- {w}" for w in s["warnings"]]
    return "\n".join(lines)


def problems_text(problems):
    """An ExportError's problems as text for the panel."""
    n = len(problems)
    head = f"Nothing was exported. Fix {'this problem' if n == 1 else f'these {n} problems'}:"
    return "\n".join([head] + [f"- {p}" for p in problems])
