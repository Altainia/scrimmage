"""The what's-left checklist: `checklist(imp, src, result, guessed=(), volumes=None)` lists what the student still has to settle.

Each `Item` has a `level` ("todo" is work left, "check" is worth a look, "error" is a problem that makes the part
wrong), a plain-words `text`, and a `target` the GUI selects when the item is clicked: `{"kind": "piece" | "body" |
"frame", "keys": [...]}` where keys are piece keys or body names (none for the frame). Order: pieces still guessed as
aluminum (one item per body), pieces with no volume, guessed re-import matches, welded bodies (one item), jointed
bodies with no motor, an unconfirmed robot frame, then the build's own messages.

Left-out pieces are never listed. "Guessed as aluminum" means no material chosen yet (`material` is None; picking aluminum explicitly settles it), with no typed weight on the piece or
its body. `guessed` is the piece keys `reimport` matched by shape (its messages say "matched by shape; check it"); the
checklist can't see those messages, so the caller passes the keys. `volumes` (piece key -> volume in cubic meters)
is what the caller already worked out for each piece; without it the checklist works out each piece's volume itself,
which is slow on a big assembly.
"""

from dataclasses import dataclass
from typing import Iterable, Mapping

from .. import i18n
from .build import BuildResult
from .mass import mesh_inertial
from .settings import ImportFile
from .source import Source


@dataclass
class Item:
    level: str          # "todo" | "check" | "error"
    text: str
    target: dict        # {"kind": "piece" | "body" | "frame", "keys": [...]}


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else i18n.t("{names} and {last}", names=", ".join(names[:-1]), last=names[-1])


def _target(imp: ImportFile, text: str) -> dict:
    """What a build message is about: the body or piece it starts with, else the robot frame."""
    if text.startswith("The weights typed in "):
        name = text[len("The weights typed in "):].split(" ")[0]
        # the message names a body, or a part (then its root body)
        if name in imp.bodies:
            return {"kind": "body", "keys": [name]}
        roots = [n for n, b in imp.bodies.items() if b.part == name]
        if roots:
            return {"kind": "body", "keys": roots}
    for kind, names in (("body", imp.bodies), ("piece", imp.pieces)):
        for n in names:
            if text.startswith(n + " ") or text.startswith(n + ":") or text.startswith(n + "'"):
                return {"kind": kind, "keys": [n]}
    return {"kind": "frame", "keys": []}


def checklist(imp: ImportFile, src: Source, result: BuildResult, guessed: Iterable[str] = (),
              volumes: Mapping[str, float] | None = None) -> list[Item]:
    items: list[Item] = []
    by_key = {p.key: p for p in src.pieces}

    def volume(k: str) -> float:
        if volumes is not None and k in volumes:
            return volumes[k]
        return mesh_inertial(by_key[k].v, by_key[k].f, 1.0).mass
    live = [(k, ps) for k, ps in imp.pieces.items() if not ps.left_out and ps.body in imp.bodies]

    # still guessed as aluminum, per body (a typed weight on the piece or its body settles it)
    for name, bs in imp.bodies.items():
        if bs.mass:
            continue
        keys = [k for k, ps in live if ps.body == name and ps.material is None and not ps.mass]
        if keys:
            items.append(Item("todo", i18n.tn("{count} piece in {body} is still guessed as aluminum",
                                              "{count} pieces in {body} are still guessed as aluminum", len(keys), body=name),
                              {"kind": "piece", "keys": keys}))

    # no volume (open surfaces) and no typed weight to stand in for it
    for name, bs in imp.bodies.items():
        if bs.mass:
            continue
        keys = [k for k, ps in live if ps.body == name and not ps.mass and k in by_key
                and volume(k) <= 0]
        if keys:
            items.append(Item("error", i18n.tn("{count} piece in {body} has no volume: type a weight or leave it out",
                                               "{count} pieces in {body} have no volume: type a weight or leave them out",
                                               len(keys), body=name), {"kind": "piece", "keys": keys}))

    for k in guessed:
        items.append(Item("check", i18n.t("{piece} was matched by shape after re-importing: check it", piece=k),
                          {"kind": "piece", "keys": [k]}))

    welded = [n for n, b in imp.bodies.items() if b.parent is not None and b.joint is None]
    if welded:
        parents = {imp.bodies[n].parent for n in welded}
        if len(welded) == 1:
            text = i18n.t("{body} is welded to {parent}: give it a joint if it moves", body=welded[0], parent=parents.pop())
        elif len(parents) == 1:
            text = i18n.t("{bodies} are welded to {parent}: give them a joint if they move", bodies=_and(welded),
                          parent=parents.pop())
        else:
            text = i18n.t("{bodies} are welded to the body they hang on: give them a joint if they move", bodies=_and(welded))
        items.append(Item("check", text, {"kind": "body", "keys": welded}))

    for n, b in imp.bodies.items():
        if b.joint is not None and b.motor is None:
            items.append(Item("todo", i18n.t("{body} has a joint but no motor: add one, or leave it to move freely", body=n),
                              {"kind": "body", "keys": [n]}))

    if not imp.frame.confirmed:
        items.append(Item("todo", i18n.t("Check which way is up and forward for the robot"), {"kind": "frame", "keys": []}))

    for m in result.messages:
        items.append(Item("error" if "weighs nothing" in m else "check", m, _target(imp, m)))
    return items
