"""Contacts with a flex, read by id (K5 R5).

In a MuJoCo contact a flex's side has geom -1 and its flex id in `contact.flex`. A per-geom Python list or array
indexed with -1 reads its last entry, which is the model's last geom (a range's last ball): a silent misread. So
every world-side contact reader takes its ids from contact_ids(), where a flex's side is `ngeom + flex id`, and keeps
its per-geom arrays n_ids() long, one entry more per flex.

A model without a flex pays nothing: contact_ids() is MuJoCo's own array, not a copy, and n_ids() is ngeom.
"""

from __future__ import annotations

import re

import mujoco
import numpy as np

PAIR_SUFFIX = "#soft-pair-"     # an unnamed geom paired with a soft ring is named "<body>#soft-pair-<k>" (K6-P11)
_PAIRED = re.compile(re.escape(PAIR_SUFFIX) + r"\d+$")


def n_ids(model: mujoco.MjModel) -> int:
    """How many contact ids the model has: its geoms, then its flexes."""
    return int(model.ngeom) + int(model.nflex)


def flex_id(model: mujoco.MjModel, flex: int) -> int:
    """Flex `flex`'s contact id: past the geoms."""
    return int(model.ngeom) + int(flex)


def contact_ids(model: mujoco.MjModel, data: mujoco.MjData) -> np.ndarray:
    """(ncon, 2): each contact's two sides as contact ids. Without a flex this is `data.contact.geom[:ncon]` itself;
    with one, a copy with each -1 (a flex's side) replaced by flex_id(model, data.contact.flex[i, side])."""
    geom = data.contact.geom[:data.ncon]
    if model.nflex == 0:
        return geom
    out = geom.copy()
    side = out < 0
    if side.any():
        out[side] = model.ngeom + data.contact.flex[:data.ncon][side]
    return out


def id_body(model: mujoco.MjModel) -> np.ndarray:
    """(n_ids,): each contact id's body, `geom_bodyid` with 0 (the world) for each flex."""
    if model.nflex == 0:
        return model.geom_bodyid
    return np.concatenate([model.geom_bodyid, np.zeros(model.nflex, dtype=model.geom_bodyid.dtype)])


def id_name(model: mujoco.MjModel, i: int) -> str:
    """Contact id `i`'s name: a geom's, or a flex's (a chain's flex is `element.<name>`), or its body's for an
    unnamed geom; "geom <i>" or "flex <f>" with none."""
    if i >= model.ngeom:
        f = i - model.ngeom
        return mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_FLEX, f) or f"flex {f}"
    name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, i)
    if name:        # a name given only for a soft pair (K6-P11) reads as its body's, as before
        name = _PAIRED.sub("", name)
    return name or mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, int(model.geom_bodyid[i])) or f"geom {i}"
