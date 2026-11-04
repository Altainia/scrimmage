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
the friction likewise. It may be named by both, and each takes the number it gives. Named by `[ball] measured` it
also gives the measured numbers of the ball file its shots were fit with (`[shot_setup] ball`, or its setup's own),
so the run's ball is the fit's: a fitted number means the same in a run as in the fit. A fit is run again only when
one of the files it read changes.

A file whose trials or fit aren't good enough is still used, its provenance marked not good with why; the labels
keep UNCALIBRATED for it. Each function here only reads: it returns the new values and their provenance.
"""

import math
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


def launcher_fit(mf: MeasureFile, where: str, key: str, name: str, *, fits: dict | None = None):
    """A launcher file's fit (fit_launcher.fit), reused while none of the files it read has changed: a robot's
    [shooter] and a sweep's rows load the same setup many times. `fits` (resolved path -> LauncherFit) holds fits
    already run in one load, so a file named twice is fit once even if it changes in between."""
    from . import fit_launcher  # runs the launcher model

    known = mf.path.resolve()
    if fits is not None and known in fits:
        return fits[known]
    stamped = _FITS.get(known)
    if stamped is not None and stamped[0] == _stamps(stamped[1].inputs):
        result = stamped[1]
    else:
        result = _fitted(fit_launcher.fit, mf, where, key, name)
        _FITS[known] = (_stamps(result.inputs), result)
    if fits is not None:
        fits[known] = result
    return result


_FITS: dict = {}  # resolved launcher file -> (its inputs' stamps, its fit)


def _stamps(paths) -> tuple:
    """Each file's modification time and size (None when it's gone), to tell when a fit's inputs change."""
    out = []
    for p in paths:
        try:
            st = Path(p).stat()
            out.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((p, None, None))
    return tuple(out)


def _launcher(mf: MeasureFile, field: str, where: str, key: str, name: str, *, shared: bool,
              fits: dict | None) -> tuple[dict, MeasureFile | None]:
    """{field: (value, provenance)} from a launcher file's fit, and the ball file the shots were fit with (None if
    none, or for the wheels). The ball file comes with the ball's number so a run's ball is the one the fit used.
    The number is left out when the fit gave the other one and the file is `shared` (named by the other section
    too, which takes that number); otherwise that is an error."""
    result = launcher_fit(mf, where, key, name, fits=fits)
    ball = load(result.ball_file) if field == "stiffness_n" and result.ball_file is not None else None
    if field in result.fitted:
        return {field: (getattr(result, field), _fit_provenance(mf, "shot", result))}, ball
    if shared:
        return {}, ball
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
               launcher: bool = True, fits: dict | None = None) -> tuple[dict, dict[str, Provenance]]:
    """A `[ball]` table's keys (`preset` and its own numbers, without `measured`) with the measured fields of the
    file `path` in place, and each measured field's provenance. A ball file's `base` must be the table's preset when
    both are given; with no preset, its base becomes the preset. `shared`: the wheels name the same launcher file.
    `launcher`: a launcher file is taken too (a range's ball isn't on a launcher, so it takes a ball file only).
    `fits`: the fits already run (see _launcher)."""
    mf = open_file(path, base, where, key, ("ball", "launcher") if launcher else ("ball",), "a ball")
    fields = dict(fields)
    got, ball_file = {}, None
    if mf.kind == "launcher":
        got, mf = _launcher(mf, "stiffness_n", where, key, path, shared=shared, fits=fits)
        if mf is None:
            return _replace(fields, got, where, key, path)
        ball_file = mf.path.name
    preset = fields.get("preset")
    if mf.base is not None and preset is not None and preset != mf.base:
        what = f"measured a {mf.base} ball" if ball_file is None else f"was fit with a {mf.base} ball ({ball_file})"
        raise MeasureError(f"{where}: {key} = \"{path}\" {what}, but the preset is {preset}: make them the same, or "
                           "leave the preset out to use the file's")
    if preset is None and mf.base is not None:
        fields["preset"] = mf.base
    return _replace(fields, {**_direct(mf, BALL_FIELDS, where, key, path), **got}, where, key, path)


def apply_wheels(fields: dict, path: str, where: str, *, base=".", key: str = "measured", shared: bool = False,
                 fits: dict | None = None) -> tuple[dict, dict[str, Provenance]]:
    """A `[wheels]` table's keys (without `measured`) with the friction of the file `path` in place, and its
    provenance. `shared`: the ball names the same launcher file. `fits`: the fits already run (see _launcher)."""
    mf = open_file(path, base, where, key, ("friction", "launcher"), "a wheel")
    got = (_launcher(mf, "friction", where, key, path, shared=shared, fits=fits)[0] if mf.kind == "launcher"
           else _direct(mf, ("friction",), where, key, path))
    return _replace(dict(fields), got, where, key, path)


def friction_value(path: str, where: str, *, base=".", key: str = "friction",
                   needs: str = "a surface") -> tuple[float, Provenance]:
    """The sliding friction a friction file's sled pulls measured, and its provenance: for a grip or a piece."""
    mf = open_file(path, base, where, key, ("friction",), needs)
    (value, prov), = _direct(mf, ("friction",), where, key, path).values()
    return value, prov


CYLINDER_UNITS = {"bore": "m", "rod": "m", "stroke": "m", "friction": "N", "rod_mass": "kg"}


def apply_cylinder(params: dict, path: str, where: str, *, base=".", key: str = "measured",
                   cylinder: dict | None = None) -> tuple[dict, Provenance, list[str]]:
    """A stock cylinder's parameters with the conductance a cylinder file's strokes fit, its provenance, and a
    warning for each of the cylinder's numbers (`cylinder`: its checked parameters, the defaults filled in) that
    isn't the one the strokes were timed on: the valve fit there may not fit here."""
    from .fit_cylinder import cylinder_of, fit

    mf = open_file(path, base, where, key, ("cylinder",), "a cylinder")
    if "conductance" in params:
        raise MeasureError(f"{where}: conductance is given and also measured by {key} = \"{path}\": give one (leave "
                           "conductance out to use the measurement)")
    result = _fitted(fit, mf, where, key, path)
    warnings = []
    if cylinder is not None:
        timed = cylinder_of(mf)
        for name, unit in CYLINDER_UNITS.items():
            if name in cylinder and not math.isclose(timed[name], cylinder[name], rel_tol=1e-6):
                warnings.append(f"{key} = \"{path}\": the strokes were fit with {name} {timed[name]:.4g} {unit}, but "
                                f"this cylinder has {cylinder[name]:.4g} {unit}: the valve's fit may not fit it (give "
                                f"it the strokes' {name}, or time this one)")
    return {**params, "conductance": result.conductance}, _fit_provenance(mf, "stroke", result), warnings
