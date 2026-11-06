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

The words are in the Language setting, the label's own word ("UNCALIBRATED") too: nothing finds a line by it (the
Bench tab reads a run's headlines from summary.json, spec L3). A caller's own `estimates` wording is N_() text
without it.
"""

from __future__ import annotations

from ..i18n import N_, t, tn, translated
from .file import Provenance

ESTIMATES = N_("estimates from preset material numbers, not fitted to measurements")


def marked(text: str) -> str:
    """`text` (in the user's language) after the label's mark: "UNCALIBRATED: <text>"."""
    return t("UNCALIBRATED: {text}", text=text)

# what matters for each result (spec goal 5); a grip's pads and a cylinder's valve are named by their callers
LAUNCHER = ("ball.stiffness_n", "ball.restitution", "wheels.friction")
DISC = ("disc.inertia", "disc.rim_stiffness", "tread.friction")      # docs/disc.md's "Measure these first"
SQUISHY_BENCH = ("ball.stiffness_n", "tread.friction")              # a bench: its piece's stiffness and friction
RIGID_BENCH = ("piece.stiffness", "tread.friction")
NAMES = {   # an input's key -> (what it belongs to, its words, its words after its owner's) (N_(): translated())
    "ball.stiffness_n": ("ball", N_("ball stiffness"), N_("stiffness")),
    "ball.restitution": ("ball", N_("ball bounce"), N_("bounce")),
    "wheels.friction": ("wheel", N_("wheel friction"), N_("friction")),
    "piece.stiffness": ("piece", N_("piece stiffness"), N_("stiffness")),
    "tread.friction": ("tread", N_("tread friction"), N_("friction")),
    "disc.inertia": ("disc", N_("disc inertia"), N_("inertia")),
    "disc.rim_stiffness": ("disc", N_("disc rim stiffness"), N_("rim stiffness")),
}


def names(keys) -> list[str]:
    """Each key's words, the owner said once for a run of keys that share it: "ball stiffness", "bounce". A key
    NAMES doesn't know is its own words ("lift valve conductance")."""
    out, last = [], None
    for k in keys:
        owner, full, short = NAMES.get(k, (None, k, k))
        out.append(translated(short if owner is not None and owner == last else full))
        last = owner
    return out


def _and(words: list[str]) -> str:
    return words[0] if len(words) == 1 else t("{names} and {last}", names=", ".join(words[:-1]), last=words[-1])


def source(p: Provenance) -> str:
    """Where a measurement came from: "fuel.measure.toml, J. Smith, 2026-11-10, 5 trials"; who and when are left
    out when the file doesn't give them ("fuel.measure.toml, 5 trials")."""
    trials = tn("{count} trial", "{count} trials", p.trials)
    return ", ".join([p.file.name, *(x for x in (p.by, p.date) if x), trials])


def not_good(p: Provenance) -> str:
    """Why a measurement counts as an estimate: "not good enough: 2 trials; take at least 3" (one colon)."""
    return t("not good enough: {why}", why=p.why.replace(": ", "; ")) if p.why else t("not good enough")


def _grouped(keys, inputs) -> list[tuple[Provenance | None, list[str]]]:
    """The keys measured by each file (None: not measured), in the order of `keys`, each file once."""
    groups: dict[Provenance | None, list[str]] = {}
    for k in keys:
        groups.setdefault(inputs[k], []).append(k)
    return list(groups.items())


def line(inputs: dict[str, Provenance | None], matters: tuple[str, ...], *, estimates: str = ESTIMATES) -> str:
    """The label for a result resting on `matters`, each measured as `inputs` says (a key missing or None: an
    estimate). `estimates` is the caller's own wording (N_() text, after the mark) for when nothing is measured."""
    prov = {k: inputs.get(k) for k in matters}
    if all(p is None for p in prov.values()):
        return marked(t("{estimates} ({inputs})", estimates=translated(estimates), inputs=", ".join(names(matters))))
    good = [k for k in matters if prov[k] is not None and prov[k].good]
    measured = "; ".join(f"{_and(names(ks))} ({source(p)})" for p, ks in _grouped(good, prov))
    if len(good) == len(matters):
        return t("Calibrated from measurements: {measured}", measured=measured)
    # the plain estimates together, and the not-good measurements by their file, saying why
    still = [", ".join(names(ks)) if p is None else f"{_and(names(ks))} ({p.file.name}, {not_good(p)})"
             for p, ks in _grouped([k for k in matters if k not in good], prov)]
    text = marked(t("still estimates: {still}", still=", ".join(still)))
    return t("{text}. Measured: {measured}", text=text, measured=measured) if measured else text


def common(provenances: list[dict]) -> dict[str, Provenance]:
    """The provenance every one of several runs shares, key by key: a key measured differently (or not at all) by
    some run, as a swept number is, is left out, so it reads as an estimate."""
    if not provenances:
        return {}
    first, *rest = provenances
    return {k: p for k, p in first.items() if all(r.get(k) == p for r in rest)}
