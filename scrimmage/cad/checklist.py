"""The what's-left checklist: `checklist(imp, src, result, guessed=())` lists what the student still has to settle.

Each `Item` has a `level` ("todo" is work left, "check" is worth a look, "error" is a problem that makes the part
wrong), a plain-words `text`, and a `target` the GUI selects when the item is clicked: `{"kind": "piece" | "body" |
"frame", "keys": [...]}` where keys are piece keys or body names (none for the frame). Order: pieces still guessed as
aluminum (one item per body), pieces with no volume, guessed re-import matches, welded bodies (one item), jointed
bodies with no motor, an unconfirmed robot frame, then the build's own messages.

Left-out pieces are never listed. "Guessed as aluminum" means material aluminum with no typed weight on the piece or
its body. `guessed` is the piece keys `reimport` matched by shape (its messages say "matched by shape; check it"); the
checklist can't see those messages, so the caller passes the keys.
"""

from dataclasses import dataclass
from typing import Iterable

from .build import BuildResult
from .mass import mesh_inertial
from .settings import ImportFile
from .source import Source


@dataclass
class Item:
    level: str          # "todo" | "check" | "error"
    text: str
    target: dict        # {"kind": "piece" | "body" | "frame", "keys": [...]}


def _pieces(n: int) -> str:
    return f"{n} piece" if n == 1 else f"{n} pieces"


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _target(imp: ImportFile, text: str) -> dict:
    """What a build message is about: the body or piece it starts with, else the robot frame."""
    for kind, names in (("body", imp.bodies), ("piece", imp.pieces)):
        for n in names:
            if text.startswith(n + " ") or text.startswith(n + ":") or text.startswith(n + "'"):
                return {"kind": kind, "keys": [n]}
    return {"kind": "frame", "keys": []}


def checklist(imp: ImportFile, src: Source, result: BuildResult, guessed: Iterable[str] = ()) -> list[Item]:
    items: list[Item] = []
    by_key = {p.key: p for p in src.pieces}
    live = [(k, ps) for k, ps in imp.pieces.items() if not ps.left_out and ps.body in imp.bodies]

    # still guessed as aluminum, per body (a typed weight on the piece or its body settles it)
    for name, bs in imp.bodies.items():
        if bs.mass:
            continue
        keys = [k for k, ps in live if ps.body == name and ps.material == "aluminum" and not ps.mass]
        if keys:
            items.append(Item("todo", f"{_pieces(len(keys))} in {name} {'is' if len(keys) == 1 else 'are'} still "
                              "guessed as aluminum", {"kind": "piece", "keys": keys}))

    # no volume (open surfaces) and no typed weight to stand in for it
    for name, bs in imp.bodies.items():
        if bs.mass:
            continue
        keys = [k for k, ps in live if ps.body == name and not ps.mass and k in by_key
                and mesh_inertial(by_key[k].v, by_key[k].f, 2700.0).mass <= 0]
        if keys:
            one = len(keys) == 1
            items.append(Item("error", f"{_pieces(len(keys))} in {name} {'has' if one else 'have'} no volume: type a "
                              f"weight or leave {'it' if one else 'them'} out", {"kind": "piece", "keys": keys}))

    for k in guessed:
        items.append(Item("check", f"{k} was matched by shape after re-importing: check it",
                          {"kind": "piece", "keys": [k]}))

    welded = [n for n, b in imp.bodies.items() if b.parent is not None and b.joint is None]
    if welded:
        parents = {imp.bodies[n].parent for n in welded}
        to = parents.pop() if len(parents) == 1 else "the body they hang on"
        one = len(welded) == 1
        items.append(Item("check", f"{_and(welded)} {'is' if one else 'are'} welded to {to}: give "
                          f"{'it' if one else 'them'} a joint if {'it moves' if one else 'they move'}",
                          {"kind": "body", "keys": welded}))

    for n, b in imp.bodies.items():
        if b.joint is not None and b.motor is None:
            items.append(Item("todo", f"{n} has a joint but no motor: add one, or leave it to move freely",
                              {"kind": "body", "keys": [n]}))

    if not imp.frame.confirmed:
        items.append(Item("todo", "Check which way is up and forward for the robot", {"kind": "frame", "keys": []}))

    for m in result.messages:
        items.append(Item("error" if "weighs nothing" in m else "check", m, _target(imp, m)))
    return items
