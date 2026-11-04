"""Using a measurement file wherever its number is used (spec goal 4, C11 and C12).

A setting names a file by its path, relative to the file that names it (C12):

    [ball]                          # a launcher setup
    preset = "2026-fuel"
    measured = "fuel.measure.toml"  # a ball file (its measured fields replace the preset's) or a launcher file

    [wheels]
    preset = "35A"
    measured = "tread.measure.toml" # a friction file (its sled pull) or a launcher file

A range's `[range] ball` (and a `[piece.<name>] ball`) and a test's Setup ball take a ball file in place of a ball
preset; its `base` is the preset. A grip and a piece's `friction` take a friction file in place of a material or a
number. A stock cylinder's `measured` takes a cylinder file, whose fit gives the valve's conductance.

Which file goes where (C11): a ball setting takes a ball or launcher file, the wheels a friction or launcher file, a
grip or a piece's friction a friction file, a cylinder a cylinder file. Another kind is an error naming both the
setting and the file's kind.

A launcher file fits one number (fit_launcher.py): the ball's stiffness, or, with the ball's squeeze measured, the
wheel's friction. Named by `[ball] measured` it gives the stiffness when that is what it fit; by `[wheels] measured`
the friction likewise. It may be named by both, and each takes the number it gives.

A file whose trials or fit aren't good enough is still used, its provenance marked not good with why; the labels
keep UNCALIBRATED for it. Each function here only reads: it returns the new values and their provenance.
"""

from pathlib import Path

from .file import MeasureError, MeasureFile, Provenance, load, provenance
from .tests_direct import values

SUFFIX = ".measure.toml"
BALL_FIELDS = ("diameter", "mass", "stiffness_n", "restitution")  # what a ball file can measure, as [ball] keys


def is_measurement(value) -> bool:
    """Whether a setting's value names a measurement file rather than a preset, a material or a number."""
    return isinstance(value, str) and value.endswith(SUFFIX)


def find(name: str, base, where: str, key: str) -> Path:
    """The path of the measurement file `<key> = "<name>"` names, found from `base`, the folder of the file that names
    it (C12); an error naming the place, the key and where it looked when it isn't there."""
    path = Path(base) / name
    if not path.is_file():
        raise MeasureError(f"{where}: {key} = \"{name}\": no such file (looked in {path.resolve()})")
    return path


def open_file(name: str, base, where: str, key: str, kinds: tuple[str, ...], needs: str) -> MeasureFile:
    """The measurement file `<key> = "<name>"` names, found from `base` (the folder of the file that names it), and
    checked to be one of `kinds`. `needs` says what the setting is: "a ball", "a grip"."""
    path = find(name, base, where, key)
    try:
        mf = load(path)
    except MeasureError as e:
        raise MeasureError(f"{where}: {key} = \"{name}\": {e}") from None
    if mf.kind not in kinds:
        raise MeasureError(f"{where}: {key} = \"{name}\" is a {mf.kind} measurement; {needs} needs a "
                           f"{' or '.join(kinds)} measurement")
    return mf


def _fitted(fit, mf: MeasureFile, where: str, key: str, name: str):
    try:
        return fit(mf)
    except MeasureError as e:
        raise MeasureError(f"{where}: {key} = \"{name}\": {e}") from None


def _fit_provenance(mf: MeasureFile, test: str, result) -> Provenance:
    return Provenance(file=mf.path, by=mf.by, date=mf.date, trials=len(mf.trials.get(test, ())), good=result.good,
                      why=result.why)


def _launcher(mf: MeasureFile, field: str, where: str, key: str, name: str, *, shared: bool) -> dict:
    """{field: (value, provenance)} from a launcher file's fit, empty when it fit the other number and the file is
    `shared` (named by the other section too, which takes that number); otherwise that is an error."""
    from .fit_launcher import fit  # runs the launcher model

    result = _fitted(fit, mf, where, key, name)
    if field in result.fitted:
        return {field: (getattr(result, field), _fit_provenance(mf, "shot", result))}
    if shared:
        return {}
    if field == "stiffness_n":
        raise MeasureError(f"{where}: {key} = \"{name}\" fits the wheel's friction, not the ball's stiffness (its "
                           "shots' ball squeeze is measured): name it in [wheels] measured, and the ball file in "
                           "[ball] measured")
    raise MeasureError(f"{where}: {key} = \"{name}\" fits the ball's stiffness, not the wheel's friction (its shots' "
                       "ball squeeze isn't measured): name it in [ball] measured, or measure the squeeze to fit the "
                       "wheel's friction")


def _direct(mf: MeasureFile, fields: tuple[str, ...], where: str, key: str, name: str) -> dict:
    """{field: (mean, provenance)} of a ball or friction file's averaged trials."""
    got = {k: (s.mean, provenance(mf, s)) for k, (s, _) in values(mf).items() if k in fields}
    if not got:
        tests = " or ".join(f"[[{t}]]" for t in mf.trials) or "trials"
        raise MeasureError(f"{where}: {key} = \"{name}\" has no {tests} that measure {' or '.join(fields)}")
    return got


def _replace(fields: dict, got: dict, where: str, key: str, name: str) -> tuple[dict, dict[str, Provenance]]:
    clash = [k for k in got if k in fields]
    if clash:
        raise MeasureError(f"{where}: {clash[0]} is given and also measured by {key} = \"{name}\": give one (leave "
                           f"{clash[0]} out to use the measurement)")
    return {**fields, **{k: v for k, (v, _) in got.items()}}, {k: p for k, (_, p) in got.items()}


def apply_ball(fields: dict, path: str, where: str, *, base=".", key: str = "measured", shared: bool = False,
               launcher: bool = True) -> tuple[dict, dict[str, Provenance]]:
    """A `[ball]` table's keys (`preset` and its own numbers, without `measured`) with the measured fields of the
    file `path` in place, and each measured field's provenance. A ball file's `base` must be the table's preset when
    both are given; with no preset, its base becomes the preset. `shared`: the wheels name the same launcher file.
    `launcher`: a launcher file is taken too (a range's ball isn't on a launcher, so it takes a ball file only)."""
    mf = open_file(path, base, where, key, ("ball", "launcher") if launcher else ("ball",), "a ball")
    fields = dict(fields)
    if mf.kind == "launcher":
        return _replace(fields, _launcher(mf, "stiffness_n", where, key, path, shared=shared), where, key, path)
    preset = fields.get("preset")
    if mf.base is not None and preset is not None and preset != mf.base:
        raise MeasureError(f"{where}: {key} = \"{path}\" measured a {mf.base} ball, but the preset is {preset}: make "
                           "them the same, or leave the preset out to use the file's")
    if preset is None and mf.base is not None:
        fields["preset"] = mf.base
    return _replace(fields, _direct(mf, BALL_FIELDS, where, key, path), where, key, path)


def apply_wheels(fields: dict, path: str, where: str, *, base=".", key: str = "measured",
                 shared: bool = False) -> tuple[dict, dict[str, Provenance]]:
    """A `[wheels]` table's keys (without `measured`) with the friction of the file `path` in place, and its
    provenance. `shared`: the ball names the same launcher file."""
    mf = open_file(path, base, where, key, ("friction", "launcher"), "a wheel")
    got = (_launcher(mf, "friction", where, key, path, shared=shared) if mf.kind == "launcher"
           else _direct(mf, ("friction",), where, key, path))
    return _replace(dict(fields), got, where, key, path)


def friction_value(path: str, where: str, *, base=".", key: str = "friction",
                   needs: str = "a surface") -> tuple[float, Provenance]:
    """The sliding friction a friction file's sled pulls measured, and its provenance: for a grip or a piece."""
    mf = open_file(path, base, where, key, ("friction",), needs)
    (value, prov), = _direct(mf, ("friction",), where, key, path).values()
    return value, prov


def apply_cylinder(params: dict, path: str, where: str, *, base=".",
                   key: str = "measured") -> tuple[dict, Provenance]:
    """A stock cylinder's parameters with the conductance a cylinder file's strokes fit, and its provenance."""
    from .fit_cylinder import fit

    mf = open_file(path, base, where, key, ("cylinder",), "a cylinder")
    if "conductance" in params:
        raise MeasureError(f"{where}: conductance is given and also measured by {key} = \"{path}\": give one (leave "
                           "conductance out to use the measurement)")
    result = _fitted(fit, mf, where, key, path)
    return {**params, "conductance": result.conductance}, _fit_provenance(mf, "stroke", result)
