"""From `read.Extracted` to a body tree: rigid groups, tree joints, loops and couplings (plain Python, no FreeCAD).

Frames are `(pos_mm, quat_wxyz)` tuples in the Assembly's coordinates; a joint/body axis is the frame's +z.

Shapes:
- `TBody(name, comps, parent, joint, frame_world, limits)`: `name` is the body name (the group's first component's
  label sanitized to [A-Za-z0-9_], deduplicated with _2, _3...); `comps` the component names (internal `obj.Name`) in
  the body; `parent` the parent body's name (None for the root); `joint` the `read.J` hanging this body on its parent
  (None for the root); `frame_world` the body frame (the root's is the Assembly origin; a child's is its joint's frame
  on the child side, so the joint sits at the body origin along z); `limits` the joint range as seen along the child
  axis and relative to the saved pose (which is the exported joint's 0), `(min | None, max | None)` in degrees
  (Revolute) or mm (Slider), or None; `saved` FreeCAD's own value of the joint at the saved pose (degrees / mm,
  Reference2 relative to Reference1 along Reference1's z; FreeCAD's limits are absolute in this value), None for the
  root.
- `Tree(bodies, loops, couplings, warnings)`: `bodies` parent-before-child (the spanning tree takes Sliders first,
  Ruling R6, so loops close at Revolutes; a loop that can only close at a Slider is an error; among each kind, joints
  carrying a slot are taken first, so a slot lands on a tree joint whenever it can); `loops` a list of
  `(J, body1, body2, pin_pos_mm)` (pin at the joint's Reference1 frame origin, bodies of Reference1 / Reference2);
  `couplings` a list of `(J, a: J, b: J, ratio)` meaning `b = ratio * a` in the tree joints' coordinates — the ratio is
  dimensionless for Gears/Belt and **mm of rack per pinion radian** for RackPinion (a = the pinion's Revolute, b the
  rack's Slider); the two joints must move relative to the same part (Ruling R10); `warnings` a list of sentences.

A Revolute's saved value is wrapped by whole turns into its limits (FreeCAD reads the angle in (-180, 180]).
"""

import math
import re
from typing import NamedTuple, Optional

from .errors import ExportError

TREE_TYPES = ("Revolute", "Slider")
COUPLING_TYPES = ("Gears", "Belt", "RackPinion")
SUPPORTED = ("Fixed",) + TREE_TYPES + COUPLING_TYPES
SOLVED_MM = 0.01          # a joint's two frames further apart than this: the assembly wasn't solved/saved
LOOP_OPEN_MM = 1.0        # Ruling R3: a loop-closing joint whose frames are further apart than this is an error
PARALLEL = 1e-6
LIMIT_TOL = 1e-6          # degrees / mm: a joint saved this close to a limit is at it


class TBody(NamedTuple):
    name: str
    comps: list
    parent: Optional[str]
    joint: object
    frame_world: tuple
    limits: Optional[tuple]
    saved: Optional[float]


class Tree(NamedTuple):
    bodies: list
    loops: list
    couplings: list
    warnings: list


def z_axis(q):
    """The +z axis of a frame with rotation quaternion q = (w, x, y, z)."""
    w, x, y, z = q
    return (2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y))


def _dot(a, b):
    return sum(p * q for p, q in zip(a, b))


def x_axis(q):
    """The +x axis of a frame with rotation quaternion q = (w, x, y, z)."""
    w, x, y, z = q
    return (1 - 2 * (y * y + z * z), 2 * (x * y + w * z), 2 * (x * z - w * y))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def joint_value(j):
    """FreeCAD's value of a Revolute/Slider at the saved pose: the angle (degrees) from frame1's x to frame2's x about
    frame1's z, or the slide (mm) (o2 - o1) . z1."""
    z1 = z_axis(j.frame1_world[1])
    if j.type == "Slider":
        return _dot([b - a for a, b in zip(j.frame1_world[0], j.frame2_world[0])], z1)
    x1, x2 = x_axis(j.frame1_world[1]), x_axis(j.frame2_world[1])
    return math.degrees(math.atan2(_dot(_cross(x1, x2), z1), _dot(x1, x2)))


def _gap(j):
    """(how far apart the joint's two frame origins are in mm — across the axis only, for a Slider — and |cos| of the
    angle between their z axes)."""
    o1, o2 = j.frame1_world[0], j.frame2_world[0]
    z1 = z_axis(j.frame1_world[1])
    if j.type == "Slider":
        v = [b - a for a, b in zip(o1, o2)]
        t = _dot(v, z1)
        d = math.sqrt(max(_dot(v, v) - t * t, 0.0))
    else:
        d = math.dist(o1, o2)
    return d, abs(_dot(z1, z_axis(j.frame2_world[1])))


def _mm(d):
    return f"{round(d, 2):g}"


def sanitize(label):
    return re.sub(r"[^A-Za-z0-9_]", "_", label) or "body"


def _on_axis(f, frame):
    """Whether `frame` lies on the axis line of joint frame `f` (within SOLVED_MM) with a parallel axis."""
    z = z_axis(f[1])
    v = [b - a for a, b in zip(f[0], frame[0])]
    t = _dot(v, z)
    off = math.sqrt(max(_dot(v, v) - t * t, 0.0))
    return off <= SOLVED_MM and abs(_dot(z, z_axis(frame[1]))) >= 1 - PARALLEL


def wrap_saved(j, v0, lim):
    """(saved value, problem | None): a Revolute's saved angle shifted by whole turns into its limits `lim` (absolute,
    `(min | None, max | None)`), the nearest such shift when several fit; a Slider's is checked, never shifted."""
    lo, hi = lim
    if lo is not None and hi is not None and lo >= hi:
        return v0, (f"joint '{j.label}' has its minimum ({lo:g}) at or above its maximum ({hi:g}); set a minimum "
                    "below the maximum")
    if j.type == "Revolute":
        if lo is not None and hi is not None:
            first = math.ceil((lo - LIMIT_TOL - v0) / 360.0)
            last = math.floor((hi + LIMIT_TOL - v0) / 360.0)
            if first <= last:
                k = min(range(first, last + 1), key=abs)
                return v0 + 360.0 * k, None
        elif lo is not None:                        # one-sided: the turn starting at the limit
            return v0 + 360.0 * math.ceil((lo - LIMIT_TOL - v0) / 360.0) if v0 < lo - LIMIT_TOL else v0, None
        else:
            return v0 - 360.0 * math.ceil((v0 - hi - LIMIT_TOL) / 360.0) if v0 > hi + LIMIT_TOL else v0, None
    if (lo is None or v0 >= lo - LIMIT_TOL) and (hi is None or v0 <= hi + LIMIT_TOL):
        return v0, None
    return v0, f"joint '{j.label}' is saved outside its limits; move it inside and save"


def build(e, prefer=()):
    """Build the Tree from `read.Extracted` `e`; raises ExportError listing every problem found. `prefer` names
    (internal names) the joints carrying slots; they, and the joints couplings drive, become tree joints whenever the
    slider-first rule allows."""
    prefer = set(prefer)
    problems = list(e.problems)
    warnings = list(e.warnings)
    comps = e.components
    order = {n: i for i, n in enumerate(comps)}

    def label(n):
        return comps[n].label if n in comps else str(n)

    joints = []
    for j in e.joints:
        if j.suppressed:
            continue
        if j.comp1 not in comps or j.comp2 not in comps:
            problems.append(f"joint '{j.label}' is broken: one of its references is missing or isn't a part of the "
                            "Assembly; edit the joint and pick its two parts again")
        elif j.type not in SUPPORTED:
            problems.append(f"joint '{j.label}' is a {j.type} joint, which scrimmage can't export: use Revolute, "
                            "Slider or Fixed")
        else:
            joints.append(j)

    # rigid groups: union-find over Fixed joints
    up = {n: n for n in comps}

    def find(n):
        while up[n] != n:
            up[n] = up[up[n]]
            n = up[n]
        return n

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            if order[b] < order[a]:
                a, b = b, a
            up[b] = a

    for j in joints:
        if j.type == "Fixed":
            union(j.comp1, j.comp2)

    grounded = [g for g in e.grounded if g in comps]
    if not grounded:
        if comps:
            problems.append("nothing is grounded: ground one part (right-click it, Toggle grounded) of "
                            + ", ".join(label(n) for n in comps))
        else:
            problems.append("nothing is grounded: the Assembly has no parts; add parts to it and ground one")
        raise ExportError(problems)
    rigid = {n: find(n) for n in comps}                     # groups from Fixed joints alone
    root = find(grounded[0])
    root_group = {n for n in comps if find(n) == root}      # names the root body

    def absorb(n):                                          # merge n's group into the root, which stays the key
        r = find(n)
        if r != root:
            up[r] = root

    for g in grounded[1:]:
        absorb(g)

    edges = []
    for j in (j for j in joints if j.type in TREE_TYPES):
        if find(j.comp1) != find(j.comp2):
            edges.append(j)
        elif rigid[j.comp1] != rigid[j.comp2]:
            problems.append(f"joint '{j.label}' joins {label(j.comp1)} and {label(j.comp2)}, which are both "
                            "grounded: un-ground one of them")
        else:
            problems.append(f"joint '{j.label}' joins {label(j.comp1)} and {label(j.comp2)}, which are fixed "
                            "together; remove it or the Fixed joint")

    def driven(comp, frame):
        """The Revolute/Slider joints a coupling end on `comp` (its frame `frame`) could drive: those moving comp's
        group, narrowed to the one on the frame's axis when there are several."""
        g = find(comp)
        cands = [j for j in joints if j.type in TREE_TYPES and g in (find(j.comp1), find(j.comp2))
                 and find(j.comp1) != find(j.comp2)]
        if len(cands) > 1:
            cands = [j for j in cands if _on_axis(j.frame1_world if find(j.comp1) == g else j.frame2_world, frame)]
        return cands

    # joints that couplings drive must be tree joints too, like slotted ones (R13: preferring only slotted joints
    # could push a coupled joint onto a loop, so adding a motor broke a gear that exported before)
    for c in e.joints:
        if c.type in COUPLING_TYPES and not c.suppressed and c.comp1 in comps and c.comp2 in comps:
            for comp, frame in ((c.comp1, c.frame1_world), (c.comp2, c.frame2_world)):
                cands = driven(comp, frame)
                if len(cands) == 1:
                    prefer.add(cands[0].name)

    # spanning tree (Ruling R6): Sliders first, so a loop closes at a Revolute whenever its cycle has one. Groups
    # joined by Sliders form clusters (preferred Sliders first: slotted or coupled), then preferred Revolutes join
    # clusters, so a slot or coupling lands on a tree joint whenever it can; a BFS from the root over the remaining
    # Revolutes between clusters picks the rest.
    cl = {}

    def cfind(g):
        cl.setdefault(g, g)
        while cl[g] != g:
            cl[g] = cl[cl[g]]
            g = cl[g]
        return g

    tree_ids, slider_loops, loops_j = set(), [], []
    first = sorted((j for j in edges if j.type == "Slider"), key=lambda j: j.name not in prefer)
    first += [j for j in edges if j.type == "Revolute" and j.name in prefer]
    for j in first:
        a, b = cfind(find(j.comp1)), cfind(find(j.comp2))
        if a == b:
            (slider_loops if j.type == "Slider" else loops_j).append(j)
        else:
            cl[b] = a
            tree_ids.add(id(j))
    seen, queue = {cfind(root)}, [cfind(root)]
    i = 0
    while i < len(queue):
        c = queue[i]
        i += 1
        for j in edges:
            if j.type != "Revolute" or id(j) in tree_ids or any(id(j) == id(x) for x in loops_j):
                continue
            c1, c2 = cfind(find(j.comp1)), cfind(find(j.comp2))
            if c not in (c1, c2):
                continue
            other = c2 if c == c1 else c1
            if other in seen:
                loops_j.append(j)
            else:
                seen.add(other)
                queue.append(other)
                tree_ids.add(id(j))

    # parent-before-child order: BFS from the root over the tree joints
    reached, parent, via = [root], {}, {}
    i = 0
    while i < len(reached):
        g = reached[i]
        i += 1
        for j in edges:
            if id(j) not in tree_ids:
                continue
            g1, g2 = find(j.comp1), find(j.comp2)
            if g in (g1, g2):
                other = g2 if g == g1 else g1
                if other not in reached:
                    reached.append(other)
                    parent[other], via[other] = g, j

    loops_j = [j for j in loops_j if find(j.comp1) in reached]   # loops among unreached parts merge away below
    for j in slider_loops:
        if find(j.comp1) in reached:
            problems.append(f"slider '{j.label}' closes a loop of Sliders: a loop can only close at a Revolute")

    unreached = [n for n in comps if find(n) not in reached]
    if unreached:
        warnings.append("not connected to the grounded part by any joint, so merged into it: "
                        + ", ".join(label(n) for n in unreached))
        for n in unreached:
            absorb(n)

    # bodies, parent before child
    names, used_names = {}, set()
    members = {g: [n for n in comps if find(n) == g] for g in reached}
    for g in reached:
        first = next((n for n in members[g] if n in root_group), members[g][0]) if g == root else members[g][0]
        base = sanitize(label(first))
        name, k = base, 2
        while name in used_names:
            name, k = f"{base}_{k}", k + 1
        used_names.add(name)
        names[g] = name

    bodies = []
    for g in reached:
        if g == root:
            bodies.append(TBody(names[g], members[g], None, None, ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)), None, None))
            continue
        j = via[g]
        child_is_2 = find(j.comp2) == g
        frame = j.frame2_world if child_is_2 else j.frame1_world
        # FreeCAD measures a joint as Reference2's frame relative to Reference1's, along Reference1's z, and its
        # limits are absolute; the exported joint is 0 at the saved pose, along the child's z
        if child_is_2:
            k = 1 if _dot(z_axis(j.frame1_world[1]), z_axis(j.frame2_world[1])) >= 0 else -1
        else:
            k = -1
        v0 = joint_value(j)
        lim = j.limits
        if lim is not None:
            v0, bad = wrap_saved(j, v0, lim)
            if bad:
                problems.append(bad)
            lim = tuple(None if x is None else x - v0 for x in lim)
            if k < 0:
                lim = (None if lim[1] is None else -lim[1], None if lim[0] is None else -lim[0])
        bodies.append(TBody(names[g], members[g], names[parent[g]], j, frame, lim, v0))

    # the CAD must be solved; loops must close (Ruling R3)
    loop_ids = {id(j) for j in loops_j}
    for j in joints:
        if j.type in COUPLING_TYPES:
            continue
        d, c = _gap(j)
        if id(j) in loop_ids and d > LOOP_OPEN_MM:
            problems.append(f"loop at {j.label} isn't closed in the CAD (off by {_mm(d)} mm)")
        elif d > SOLVED_MM or c < 1 - PARALLEL:
            warnings.append(f"assembly not solved: joint '{j.label}' has its two ends {_mm(d)} mm apart"
                            + ("" if c >= 1 - PARALLEL else " and its axes not aligned")
                            + "; solve the Assembly in FreeCAD and save")

    loops = [(j, names[find(j.comp1)], names[find(j.comp2)], j.frame1_world[0])
             for j in loops_j if find(j.comp1) in names]

    # couplings: each end drives the tree joint that moves that end's part
    child_of = {id(b.joint): b for b in bodies if b.joint is not None}

    def coupled(c, comp, frame):
        g = find(comp)
        cands = driven(comp, frame)
        if len(cands) != 1:
            problems.append(f"coupling '{c.label}': can't tell which Revolute/Slider it drives on {label(comp)}; "
                            "pick the same frame as that joint's")
            return None
        j = cands[0]
        if id(j) not in child_of:
            problems.append(f"coupling '{c.label}' couples '{j.label}', which closes a loop: couple tree joints only")
            return None
        if g in names and child_of[id(j)].name != names[g]:
            problems.append(f"coupling '{c.label}' is on {label(comp)}, which '{j.label}' doesn't move (it moves "
                            f"{child_of[id(j)].name}): put the coupling on the moving part")
            return None
        return j

    def axis(j):
        return z_axis(child_of[id(j)].frame_world[1])

    def sign(frame, j):
        return 1 if _dot(z_axis(frame[1]), axis(j)) >= 0 else -1

    couplings = []
    for c in joints:
        if c.type not in COUPLING_TYPES:
            continue
        r1, r2 = c.radii or (0.0, 0.0)
        if r1 <= 0 or (c.type != "RackPinion" and r2 <= 0):
            problems.append(f"coupling '{c.label}' has a zero or negative radius: set its radi"
                            + ("us" if c.type == "RackPinion" else "i") + " to the pitch radius")
            continue
        a, b = coupled(c, c.comp1, c.frame1_world), coupled(c, c.comp2, c.frame2_world)
        if a is None or b is None:
            continue
        pa, pb = child_of[id(a)].parent, child_of[id(b)].parent
        if pa != pb:                                    # Ruling R10
            depth = {}
            for x in bodies:                            # parent before child
                depth[x.name] = 0 if x.parent is None else depth[x.parent] + 1
            moving = b if depth[pb] > depth[pa] else a
            problems.append(f"coupling '{c.label}' links '{a.label}' (moving relative to {pa}) and '{b.label}' (moving "
                            f"relative to {pb}): scrimmage couplings link two joints that move relative to the same "
                            "part, so a chain-driven wrist on a moving arm can't be expressed yet; drive "
                            f"'{moving.label}' directly with its own motor slot")
            continue
        if c.type == "RackPinion":
            rack_frame = c.frame1_world
            if a.type == "Slider" and b.type == "Revolute":
                a, b = b, a
            else:
                rack_frame = c.frame2_world
            if (a.type, b.type) != ("Revolute", "Slider"):
                problems.append(f"rack and pinion '{c.label}' needs a Slider (rack) and a Revolute (pinion), "
                                f"not {a.type} '{a.label}' and {b.type} '{b.label}'")
                continue
            # by geometry, as FreeCAD does: the pinion surface at the rack moves along w x d
            w, s_ax = axis(a), axis(b)
            center = child_of[id(a)].frame_world[0]
            d = [p - q for p, q in zip(rack_frame[0], center)]
            for u in (w, s_ax):
                t = _dot(d, u)
                d = [p - t * q for p, q in zip(d, u)]
            along = _dot(_cross(w, d), s_ax)
            if abs(along) < 1e-9:
                problems.append(f"rack and pinion '{c.label}': its rack frame is on the pinion axis, so the rack's "
                                "side can't be told; pick the rack's pitch line")
                continue
            ratio = r1 * (1 if along > 0 else -1)
        else:
            if a.type != "Revolute" or b.type != "Revolute":
                problems.append(f"{c.type.lower()} '{c.label}' needs two Revolute joints, not {a.type} '{a.label}' "
                                f"and {b.type} '{b.label}'")
                continue
            ratio = (r1 / r2 if c.type == "Belt" else -r1 / r2) * sign(c.frame1_world, a) * sign(c.frame2_world, b)
        couplings.append((c, a, b, ratio))

    if problems:
        raise ExportError(list(dict.fromkeys(problems)))
    return Tree(bodies, loops, couplings, warnings)


def to_data(t):
    """The Tree as JSON-able data (joints by internal name), for tests and summaries."""
    return dict(
        bodies=[dict(name=b.name, comps=list(b.comps), parent=b.parent,
                     joint=b.joint.name if b.joint else None, label=b.joint.label if b.joint else None,
                     type=b.joint.type if b.joint else None,
                     frame=[list(b.frame_world[0]), list(b.frame_world[1])],
                     limits=list(b.limits) if b.limits is not None else None, saved=b.saved) for b in t.bodies],
        loops=[[j.name, b1, b2, list(p)] for j, b1, b2, p in t.loops],
        couplings=[[c.name, a.name, b.name, r] for c, a, b, r in t.couplings],
        warnings=list(t.warnings))
