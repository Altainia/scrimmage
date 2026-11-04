"""Labels that say which numbers a result rests on are measured and which are still estimates (spec goal 5, C7).

`line(inputs, matters)` words the line from each input that matters and its Provenance (None: an estimate):

- **Nothing measured:** the caller's existing UNCALIBRATED wording, ending with what matters: "UNCALIBRATED:
  estimates from preset material numbers, not fitted to measurements (ball stiffness, bounce, wheel friction)".
- **Some measured:** "UNCALIBRATED: still estimates: wheel friction. Measured: ball stiffness and bounce
  (fuel.measure.toml, J. Smith, 2026-11-10, 5 trials)".
- **All measured and good:** "Calibrated from measurements: ball stiffness and bounce (fuel.measure.toml, ...);
  wheel friction (tread.measure.toml, ...)".

A measurement that isn't good counts as an estimate and says why: "ball stiffness (fuel.measure.toml, not good
enough: 2 trials; take at least 3)". The line has no closing period, so a caller can add its own ending.
"""

from __future__ import annotations

from .file import Provenance

ESTIMATES = "UNCALIBRATED: estimates from preset material numbers, not fitted to measurements"

# what matters for each result (spec goal 5); a grip's pads and a cylinder's valve are named by their callers
LAUNCHER = ("ball.stiffness_n", "ball.restitution", "wheels.friction")
DISC = ("disc.inertia", "disc.rim_stiffness", "tread.friction")      # docs/disc.md's "Measure these first"
SQUISHY_BENCH = ("ball.stiffness_n", "tread.friction")              # a bench: its piece's stiffness and friction
RIGID_BENCH = ("piece.stiffness", "tread.friction")
NAMES = {   # an input's key -> its words: (what it belongs to, what it is)
    "ball.stiffness_n": ("ball", "stiffness"),
    "ball.restitution": ("ball", "bounce"),
    "wheels.friction": ("wheel", "friction"),
    "piece.stiffness": ("piece", "stiffness"),
    "tread.friction": ("tread", "friction"),
    "disc.inertia": ("disc", "inertia"),
    "disc.rim_stiffness": ("disc", "rim stiffness"),
}


def names(keys) -> list[str]:
    """Each key's words, the owner said once for a run of keys that share it: "ball stiffness", "bounce". A key
    NAMES doesn't know is its own words ("lift valve conductance")."""
    out, last = [], None
    for k in keys:
        owner, what = NAMES.get(k, (None, k))
        out.append(what if owner is not None and owner == last else f"{owner} {what}" if owner else what)
        last = owner
    return out


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def source(p: Provenance) -> str:
    """Where a measurement came from: "fuel.measure.toml, J. Smith, 2026-11-10, 5 trials"; who and when are left
    out when the file doesn't give them ("fuel.measure.toml, 5 trials")."""
    trials = f"{p.trials} trial{'' if p.trials == 1 else 's'}"
    return ", ".join([p.file.name, *(x for x in (p.by, p.date) if x), trials])


def not_good(p: Provenance) -> str:
    """Why a measurement counts as an estimate: "not good enough: 2 trials; take at least 3" (one colon)."""
    return f"not good enough: {p.why.replace(': ', '; ')}" if p.why else "not good enough"


def _grouped(keys, inputs) -> list[tuple[Provenance | None, list[str]]]:
    """The keys measured by each file (None: not measured), in the order of `keys`, each file once."""
    groups: dict[Provenance | None, list[str]] = {}
    for k in keys:
        groups.setdefault(inputs[k], []).append(k)
    return list(groups.items())


def line(inputs: dict[str, Provenance | None], matters: tuple[str, ...], *, estimates: str = ESTIMATES) -> str:
    """The label for a result resting on `matters`, each measured as `inputs` says (a key missing or None: an
    estimate). `estimates` is the caller's own UNCALIBRATED wording for when nothing is measured."""
    prov = {k: inputs.get(k) for k in matters}
    if all(p is None for p in prov.values()):
        return f"{estimates} ({', '.join(names(matters))})"
    good = [k for k in matters if prov[k] is not None and prov[k].good]
    measured = "; ".join(f"{_and(names(ks))} ({source(p)})" for p, ks in _grouped(good, prov))
    if len(good) == len(matters):
        return f"Calibrated from measurements: {measured}"
    # the plain estimates together, and the not-good measurements by their file, saying why
    still = [", ".join(names(ks)) if p is None else f"{_and(names(ks))} ({p.file.name}, {not_good(p)})"
             for p, ks in _grouped([k for k in matters if k not in good], prov)]
    text = f"UNCALIBRATED: still estimates: {', '.join(still)}"
    return f"{text}. Measured: {measured}" if measured else text


def common(provenances: list[dict]) -> dict[str, Provenance]:
    """The provenance every one of several runs shares, key by key: a key measured differently (or not at all) by
    some run, as a swept number is, is left out, so it reads as an estimate."""
    if not provenances:
        return {}
    first, *rest = provenances
    return {k: p for k, p in first.items() if all(r.get(k) == p for r in rest)}
