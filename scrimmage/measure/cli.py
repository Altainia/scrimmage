"""`scrimmage measure`: read a measurement file, print a blank form, or start a file.

    scrimmage measure FILE                    each test's trials, averages and warnings, and a launcher's or a
                                              cylinder's fit; exit 0 when good enough to use, 1 when not, 2 when
                                              the file can't be read
    scrimmage measure form TEST [-o PATH]     a printable blank form (HTML), to PATH or printed
    scrimmage measure new TEST PATH           a starter <name>.measure.toml for one test

A file named `form` or `new` is read as ./form or ./new.
"""

import argparse
import math
import sys
from pathlib import Path

from . import fit_cylinder, fit_launcher, forms, tests_direct
from .file import KIND_TESTS, MeasureError, MeasureFile, load
from .stats import summary, why
from .use import SUFFIX

USAGE = "scrimmage measure FILE | form TEST [-o PATH] | new TEST PATH"

# what each measured field is, and how its value is shown
FIELD = {
    "mass": ("mass", lambda v: f"{v * 1000:.1f} g"),
    "diameter": ("diameter", lambda v: f"{v * 1000:.1f} mm ({v / 0.0254:.2f} in)"),
    "stiffness_n": ("stiffness_n, the force that squeezes the ball by 25%", lambda v: f"{v:.1f} N"),
    "restitution": ("restitution, the bounce", lambda v: f"{v:.3f}"),
    "friction": ("friction", lambda v: f"{v:.3f}"),
    "time": ("stroke time", lambda v: f"{v:.3f} s"),
}
# each direct test's fields (the trials each value came from are its summary's `trials`: a size trial may only weigh)
GIVES = {"size": ("mass", "diameter"), "squeeze": ("stiffness_n",), "drop": ("restitution",), "sled": ("friction",)}


def shot_words(result) -> tuple[str, str]:
    """What a shot fit fits ("the wheel's friction"), and what it holds and at what, in words."""
    if "friction" in result.fitted:
        return ("the wheel's friction",
                f"the ball's stiffness held at {result.stiffness_n:.0f} N, from its squeeze test")
    return "the ball's stiffness", f"the wheel friction held at {result.friction:.2f}"


def _cell(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    return ["  " + "  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in [head, *rows]]


def _readings(rows: list[dict]) -> list[str]:
    keys: list[str] = []
    for row in rows:
        keys += [k for k in row if k not in keys]
    return keys


def _summary_line(field: str, s) -> str:
    show = FIELD[field][1]
    trials = f"{s.n} trial{'s' if s.n != 1 else ''}"
    return (f"  {field}: average {show(s.mean)}, lowest {show(s.low)}, highest {show(s.high)}, "
            f"spread {s.spread:.0%} ({trials})")


def _verdict(good: bool, reason: str) -> str:
    return "  good enough to use" if good else f"  not good enough yet: {reason}"


def _direct(mf: MeasureFile, test: str, measured: dict) -> tuple[list[str], list[str]]:
    """A direct test's lines, and why each field that isn't good isn't."""
    rows = mf.trials[test]
    gives = [f for f in GIVES[test] if f in measured]  # a size test may weigh only
    readings = _readings(rows)
    columns: dict[str, list[str]] = {}
    for field in gives:
        s, values = measured[field]
        shown = dict(zip(s.trials, values))   # the trials that gave this field (a size trial may only weigh)
        columns[field] = [FIELD[field][1](shown[i]) if i in shown else "" for i in range(len(rows))]
    table = [[str(i + 1), *(_cell(row.get(k, "")) for k in readings), *(columns[f][i] for f in gives)]
             for i, row in enumerate(rows)]
    head = ["trial", *readings, *gives]
    lines = [f"{test}: gives {', '.join(FIELD[f][0] for f in gives)}", *_table(head, table)]
    problems = []
    for field in gives:
        s = measured[field][0]
        lines.append(_summary_line(field, s))
        lines += [f"  warning: {w}" for w in s.warnings]
        if not s.good:
            problems.append(f"{field} ({why(s)})")
    lines.append(_verdict(not problems, "; ".join(problems)))
    return lines, problems


def _shot(mf: MeasureFile) -> tuple[list[str], list[str]]:
    result = fit_launcher.fit(mf)
    rows = mf.trials["shot"]
    readings = _readings(rows)
    table = []
    for i, (row, (measured, model)) in enumerate(zip(rows, result.per_shot)):
        model_text = "not launched" if math.isnan(model) else f"{model:.2f} m/s"
        off = "" if math.isnan(model) else f"{(model - measured) / measured:+.1%}"
        table.append([str(i + 1), *(_cell(row.get(k, "")) for k in readings), f"{measured:.2f} m/s", model_text,
                      off])
    what, held = shot_words(result)
    lines = [f"shot: fits {what} to your shots ({held})",
             *_table(["shot", *readings, "speed across", "model", "model off by"], table),
             f"  fit: {result.band_text}",
             f"  the model misses your shots by {result.rms_error:.1%} (root mean square)",
             *(f"  warning: {w}" for w in result.warnings),
             _verdict(result.good, result.why)]
    return lines, ([] if result.good else [f"the shot fit ({result.why})"])


def _stroke(mf: MeasureFile) -> tuple[list[str], list[str]]:
    result = fit_cylinder.fit(mf)
    rows = mf.trials["stroke"]
    times, _ = fit_cylinder.trial_times(mf, mf.settings.get("stroke_setup", {}))
    readings = _readings(rows)
    table = [[str(i + 1), *(_cell(row.get(k, "")) for k in readings), FIELD["time"][1](t)]
             for i, (row, t) in enumerate(zip(rows, times))]
    lines = ["stroke: fits the valve's conductance to your strokes",
             *_table(["stroke", *readings, "time"], table),
             _summary_line("time", summary(times)),
             f"  fit: {result.band_text}",
             f"  the model's stroke takes {result.model_time:.3f} s, off by {result.error:+.1%}",
             *(f"  warning: {w}" for w in result.warnings),
             _verdict(result.good, result.why)]
    return lines, ([] if result.good else [f"the stroke fit ({result.why})"])


def report(mf: MeasureFile) -> tuple[str, bool]:
    """The printed report of a measurement file, and whether all of it is good enough to use."""
    about = f": {mf.about}" if mf.about else ""
    head = [f"{mf.path}: a {mf.kind} measurement{about}",
            f"measured by {mf.by or '(no name)'} on {mf.date}" + (f" with {mf.tools}" if mf.tools else "")
            + (f" (the {mf.base} preset)" if mf.base else "")]
    tests = [t for t in KIND_TESTS[mf.kind] if t in mf.trials]
    if not tests:
        listed = " or ".join(f"[[{t}]]" for t in KIND_TESTS[mf.kind])
        return "\n".join([*head, "", f"no trials yet: write each trial as a {listed} table "
                                     f"(scrimmage measure form {KIND_TESTS[mf.kind][0]} prints a form for them)",
                          "", "Not good enough yet: no trials"]), False
    measured = tests_direct.values(mf)
    sections, problems = [], []
    for test in tests:
        if test == "shot":
            lines, bad = _shot(mf)
        elif test == "stroke":
            lines, bad = _stroke(mf)
        else:
            lines, bad = _direct(mf, test, measured)
        sections.append("\n".join(lines))
        problems += bad
    if problems:
        end = (f"Not good enough yet: {'; '.join(problems)}. A run that names this file uses it, but its result "
               "stays UNCALIBRATED until then.")
    else:
        end = "Good enough to use: a run that names this file shows these numbers as measured."
    return "\n\n".join(["\n".join(head), *sections, end]), not problems


def _tests() -> str:
    return ", ".join(forms.TESTS)


def _test(text: str) -> str:
    if text not in forms.TEST:
        raise argparse.ArgumentTypeError(f"no test {text!r}; one of {_tests()}")
    return text


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # a bad command line exits 2, as argparse does, with this command's usage
        self.print_usage(sys.stderr)
        print(f"scrimmage measure: {message}", file=sys.stderr)
        sys.exit(2)


def main(argv: list[str]) -> int:
    """Run `scrimmage measure` with its arguments; the exit code."""
    if argv and argv[0] == "form":
        ap = _Parser(prog="scrimmage measure form", description="write a printable blank form for one test")
        ap.add_argument("test", type=_test, help=f"one of {_tests()}")
        ap.add_argument("-o", "--output", type=Path, help="write the form to this HTML file (else print it)")
        args = ap.parse_args(argv[1:])
        page = forms.form_html(args.test)
        if args.output is None:
            sys.stdout.write(page)
            return 0
        try:
            args.output.write_text(page, encoding="utf-8")
        except OSError as e:
            print(f"scrimmage: {args.output}: {e.strerror or e}", file=sys.stderr)
            return 2
        print(args.output)
        return 0
    if argv and argv[0] == "new":
        ap = _Parser(prog="scrimmage measure new", description="write a starter measurement file for one test")
        ap.add_argument("test", type=_test, help=f"one of {_tests()}")
        ap.add_argument("path", type=Path, help=f"the new file, its name ending in {SUFFIX}")
        args = ap.parse_args(argv[1:])
        if not args.path.name.endswith(SUFFIX):
            print(f"scrimmage: {args.path}: a measurement file's name ends in {SUFFIX} (fuel{SUFFIX})",
                  file=sys.stderr)
            return 2
        try:
            with args.path.open("x", encoding="utf-8") as f:
                f.write(forms.starter_toml(args.test))
        except FileExistsError:
            print(f"scrimmage: {args.path} already exists: pick another name, or edit it", file=sys.stderr)
            return 2
        except OSError as e:
            print(f"scrimmage: {args.path}: {e.strerror or e}", file=sys.stderr)
            return 2
        print(f"wrote {args.path}: fill in [measure] and the trials, then check it with "
              f"scrimmage measure {args.path}")
        return 0
    ap = _Parser(prog="scrimmage measure", usage=USAGE,
                 description="read a measurement file: each test's trials, averages and warnings, and a launcher's "
                             "or cylinder's fit (exit 0 good enough to use, 1 not yet, 2 can't read it)",
                 epilog=f"scrimmage measure form TEST [-o PATH] writes a printable blank form; scrimmage measure new "
                        f"TEST PATH starts a file. The tests: {_tests()}.")
    ap.add_argument("file", type=Path, help=f"a <name>{SUFFIX} file")
    args = ap.parse_args(argv)
    try:
        text, good = report(load(args.file))
    except MeasureError as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"scrimmage: {args.file}: {e.strerror or e}", file=sys.stderr)
        return 2
    print(text)
    return 0 if good else 1
