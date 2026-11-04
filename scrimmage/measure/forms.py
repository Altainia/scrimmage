"""The printable forms, one per test, and the starter measurement files, both made from one list of each test's
readings so they say the same thing.

A form (`form_html(test)`) is a self-contained HTML page: the test's picture (scrimmage/measure/img/<test>.svg,
inline), what you need, the steps, the header fields, a setup box, 8 trial rows with each reading's units printed
and the value's formula, the summary boxes, and where to type the numbers in. It has no scripts, and it prints on US
Letter and on A4: the page is left to the printer's paper, and the layout fits the narrower width (A4's 210 mm) and
the shorter height (Letter's 11 in) of the two.

A starter file (`starter_toml(test, date)`) is a `<name>.measure.toml` with its [measure] header, and the test's
setup and one example trial written as comments, each reading's key with a unit.

The readings' names and quantities come from the modules that read them (tests_direct.py, fit_launcher.py,
fit_cylinder.py), so a form can't ask for a reading the file doesn't take; this module adds their words.
"""

import datetime
import html
import math
from dataclasses import dataclass
from importlib import resources

from . import fit_cylinder, fit_launcher, tests_direct
from .file import KIND_TESTS, TEST_KIND
from .stats import MAX_SPREAD, MIN_TRIALS, OUTLIER_DISTANCES
from .units import UNITS

TESTS = tuple(test for tests in KIND_TESTS.values() for test in tests)  # size, squeeze, drop, sled, shot, stroke
ROWS = 8  # trials a form has room for (spec C1)
RECOMMENDED = 5  # trials recommended (C1)
# A trial can be flagged as looking off only when one trial's distance can exceed OUTLIER_DISTANCES times the
# average distance, which needs more than 2 x OUTLIER_DISTANCES trials (stats.py).
OUTLIER_TRIALS = math.floor(2 * OUTLIER_DISTANCES) + 1
PICTURE_MM = 115  # the picture's printed width: a 15-unit label in a 480-unit-wide picture prints at 10 pt
UNIT_WORDS = {"n": "N"}  # how a unit is printed on a form, where it isn't its key's spelling
ABOUT = {"ball": "a FUEL ball from the practice bin", "friction": "40A tread on a FUEL ball",  # a starter's example
         "launcher": "the practice robot's shooter", "cylinder": "the intake's deploy cylinder"}


@dataclass(frozen=True, kw_only=True)
class Reading:
    """One thing written on a form and in a file."""
    name: str  # its key, without the unit: "force", "wheel_rps", "floor"
    quantity: str | None  # a units.UNITS quantity (the key takes a unit), or None: a plain number or text
    what: str  # its words on the form and in the starter file
    example: object = None  # the starter file's example value; None leaves it out of the starter
    unit: str = ""  # the example's unit (for a quantity)
    optional: bool = False


@dataclass(frozen=True, kw_only=True)
class Test:
    title: str
    gives: str  # what the test gives, in a sentence
    need: tuple[str, ...]
    steps: tuple[str, ...]
    setup: tuple[Reading, ...]  # once for all trials: [<test>_setup]
    trial: tuple[Reading, ...]  # the trial table's reading columns
    value: str  # the value column's name
    formula: str  # how a trial's value is worked out, printed above the value column
    summaries: tuple[str, ...]  # the values the summary boxes average; none for shots (they are fit, not averaged)
    note: str = ""  # printed under the trials table
    example_note: str = ""  # a comment under the starter's example trial


def _r(quantities: dict, name: str, what: str, example=None, unit: str = "", optional: bool = False) -> Reading:
    """A reading `name` whose quantity is found in `quantities` (a module's own table), so the form uses its key."""
    return Reading(name=name, quantity=quantities[name], what=what, example=example, unit=unit, optional=optional)


def _plain(names, name: str, what: str, example=None, optional: bool = False) -> Reading:
    """A plain number or text the module takes as `name` (checked against its list `names`)."""
    if name not in names:
        raise ValueError(f"{name} isn't one of {names}")
    return Reading(name=name, quantity=None, what=what, example=example, optional=optional)


_T, _S, _TEXT = tests_direct.TRIAL, tests_direct.SETTING, tests_direct.SETUP_TEXT
_SHOT, _CYL = fit_launcher.SHOT_READINGS, fit_cylinder.SETUP_READINGS
_SAVE = "Take at least {min} trials; {rec} is better."

TEST: dict[str, Test] = {
    "size": Test(
        title="Ball size and mass",
        gives="the ball's mass and diameter",
        need=("a kitchen scale (g or oz)", "a soft tape measure, or calipers"),
        steps=("Weigh the ball on the kitchen scale and write its mass.",
               "Wrap the tape around the ball's widest part, snug but not squeezing it, and read the "
               "circumference. Or read the diameter straight across with calipers.",
               "Turn the ball and measure again for the next trial.",
               "Work out each trial's diameter, then fill in the boxes at the bottom."),
        setup=(),
        trial=(_r(_T["size"], "mass", "the scale's reading", 142, "g"),
               _r(_T["size"], "circumference", "the tape around the widest part", 18.5, "in"),
               _r(_T["size"], "diameter", "or across, with calipers", optional=True)),
        value="diameter worked out", formula="circumference ÷ 3.1416, or the diameter read across",
        summaries=("mass", "diameter"),
        example_note="or diameter_in = 5.9, read across with calipers: a circumference or a diameter, not both"),
    "squeeze": Test(
        title="Ball squeeze",
        gives="the ball's stiffness: the force that squeezes it by 25% of its diameter",
        need=("a bathroom scale", "two flat boards, bigger than the ball", "a ruler, for the gap between the boards",
              "the ball's diameter (the size test)"),
        steps=("Put the bottom board on the scale and the ball on the board, then zero the scale. If it can't be "
               "zeroed, write its reading as the tare.",
               "Put the top board on the ball.",
               "Press the top board down, level, until the gap between the boards is 75% of the ball's diameter "
               "(diameter × 0.75: a 5.9 in ball, 4.4 in).",
               "Hold it there and read the scale: write the force.",
               "Let go, turn the ball, and repeat for the next trial."),
        setup=(_r(_S["squeeze"], "tare", "the scale's reading with the bottom board and the ball on it, if it "
                  "couldn't be zeroed", 1.2, "lb", optional=True),
               _r(_S["squeeze"], "squeezed_to", "the gap you pressed to, if it wasn't 75% of the diameter "
                  "(Scrimmage converts the force)", 4.4, "in", optional=True)),
        trial=(_r(_T["squeeze"], "force", "the scale's reading, pressed", 9.8, "lb"),),
        value="squeeze force", formula="force − tare (no tare if the scale was zeroed)",
        summaries=("squeeze force",)),
    "drop": Test(
        title="Ball drop",
        gives="the ball's bounce (its restitution)",
        need=("a ruler or tape measure, 18 in or more, upright against a wall",
              "a phone that films slow motion", "a hard floor: concrete, tile or wood, not carpet"),
        steps=("Tape the ruler upright against the wall, its zero on a hard floor (not carpet).",
               "Hold the ball with its bottom at 18 in on the ruler, and start filming against the ruler.",
               "Let go without pushing it.",
               "In the video, find the top of the first bounce and read the ruler at the bottom of the ball: "
               "write the bounce.",
               "Repeat for the next trial."),
        setup=(_r(_S["drop"], "drop", "the drop, floor to the bottom of the ball (18 in)", 18, "in"),
               _plain(_TEXT["drop"], "floor", "what the floor is", "concrete")),
        trial=(_r(_T["drop"], "bounce", "the top of the bounce, floor to the bottom of the ball", 7.0, "in"),),
        value="restitution", formula="√(bounce ÷ drop)",
        summaries=("restitution",)),
    "sled": Test(
        title="Sled pull",
        gives="the sliding friction of one material on another",
        need=("a spring scale (or a luggage scale)", "a sled: a board or box with one material on its bottom",
              "weights for the sled", "a scale to weigh the sled"),
        steps=("Put one material on the sled's bottom (a wheel's tread, a grip pad), and lay the other flat (what "
               "it slides on).",
               "Weigh the sled with its load: write it once, as the sled weight.",
               "Hook the spring scale to the sled and pull it level, slowly and steadily.",
               "Read the scale while the sled slides at a steady speed, not the first jerk that starts it moving: "
               "write the pull.",
               "Repeat for the next trial."),
        setup=(_r(_S["sled"], "sled", "the sled and its load, weighed", 5.0, "lb"),
               _plain(_TEXT["sled"], "sliding", "the material on the sled's bottom", "wheel tread: 40A"),
               _plain(_TEXT["sled"], "on", "what it slides on", "FUEL")),
        trial=(_r(_T["sled"], "pull", "the spring scale at a steady slide", 4.1, "lb"),),
        value="friction", formula="pull ÷ sled weight (both in the same unit)",
        summaries=("friction",)),
    "shot": Test(
        title="Launcher shot",
        gives="the ball's stiffness, or the wheel's friction (with the ball's squeeze measured), fit to the "
              "launcher model",
        need=("a phone that films slow motion, at its highest frame rate", "tape for two marks, 24 in or more apart",
              "a tachometer, or the robot's log, for the wheel speed", "the launcher's setup file"),
        steps=("Put two tape marks on the wall behind the ball's path, level with it and 24 in or more apart: "
               "12 in at 240 fps is too few frames for a good fit.",
               "Set the phone to its highest frame rate, square to the marks and far enough back to see both.",
               "Spin the wheel up and let it settle. Write the wheel speed you measured, not the setpoint.",
               "Feed ONE ball and film it.",
               "In the video, count the frames the ball takes from one mark to the next.",
               f"Shoot at two or more wheel speeds, {fit_launcher.SECOND_SPEED - 1:.0%} or more apart, a few shots "
               "at each."),
        setup=(_plain(fit_launcher.SETUP_TEXT, "setup", "the launcher setup file the shots were taken on",
                      "shooter.toml"),
               _plain(fit_launcher.SETUP_TEXT, "ball", "the ball's squeeze test file, to fit the wheel's friction",
                      "fuel.measure.toml", optional=True),
               _r(_SHOT, "marks", "the tape marks' spacing", 24, "in"),
               _plain(fit_launcher.SETUP_NUMBERS, "fps", "the camera's frame rate", 960),
               _plain(fit_launcher.SETUP_NUMBERS, "hood_deg", "the hood's exit angle, if not the setup's", 45,
                      optional=True)),
        trial=(_plain(fit_launcher.SHOT_NUMBERS, "wheel_rps", "the wheel speed you measured", 50),
               _plain(fit_launcher.SHOT_NUMBERS, "hood_deg", "only if not the setup box's", optional=True),
               _r(_SHOT, "frames", "frames from one mark to the next", 70)),
        value="speed", formula="marks × fps ÷ frames",
        summaries=(),
        note="Scrimmage fits the shots to the launcher model: they aren't averaged. Shots at two or more wheel "
             "speeds check the fit."),
    "stroke": Test(
        title="Cylinder stroke",
        gives="the valve's flow (its conductance), fit to the pneumatic model",
        need=("the cylinder's bore, rod and stroke (its label or datasheet)", "what the rod moves, weighed",
              "a phone that films slow motion (or a stopwatch, for a slow stroke)"),
        steps=("Write the cylinder's size, its load and the working pressure in the setup box.",
               "Start with the rod all the way in and the tanks full.",
               "Time the stroke from the valve opening (the click) to the rod at full stroke.",
               "A quick stroke (under 0.2 s) needs filming: count the frames from the click to the rod stopping. "
               "A stopwatch can't time it closely enough.",
               "Repeat for the next trial."),
        setup=(_r(_CYL, "bore", "the piston's diameter", 1.0625, "in"),
               _r(_CYL, "rod", "the rod's diameter", 0.3125, "in"),
               _r(_CYL, "stroke", "how far the rod travels", 6, "in"),
               _r(_CYL, "load_mass", "what the rod moves, besides itself", 2, "lb", optional=True),
               _r(_CYL, "rod_mass", "the rod's own mass (else 100 g)", 100, "g", optional=True),
               _r(_CYL, "friction", "the seals' friction (else 8 N)", 8, "n", optional=True),
               _plain(fit_cylinder.SETUP_ONLY, "vertical", "true if the rod lifts its load straight up", False),
               _plain(fit_cylinder.SETUP_ONLY, "working_psi", "the regulator's working pressure (else 60)", 60),
               Reading(name="fps", quantity=None, what="the camera's frame rate, if filmed", optional=True)),
        trial=(_r(fit_cylinder.STROKE_READINGS, "time", "valve opening to full stroke", 0.21, "s"),
               _r(fit_cylinder.STROKE_READINGS, "frames", "or frames, filmed", optional=True)),
        value="time", formula="frames ÷ fps, or the time you read",
        summaries=("time",),
        example_note="or frames = 50, with fps = 240 in [stroke_setup]: a time or frames, not both"),
}


def picture(test: str) -> str:
    """The test's SVG picture, as text to put inline in a page."""
    if test not in TEST:
        raise ValueError(f"no test {test!r}; one of {', '.join(TESTS)}")
    return resources.files(__package__).joinpath("img", f"{test}.svg").read_text(encoding="utf-8").strip()


def units(r: Reading) -> list[str]:
    """The units a reading may be written in, as printed on a form ("lb", "kg", "N"); [] for a plain number or text."""
    if r.quantity is None:
        return []
    return [UNIT_WORDS.get(u, u) for u in UNITS[r.quantity]]


def key(r: Reading, unit: str = "") -> str:
    """The reading's key in a file: `force_lb`; `frames`, whose unit is its name; a plain one's own name."""
    if r.quantity is None:
        return r.name
    unit = unit or r.unit or next(iter(UNITS[r.quantity]))
    if unit == r.name:
        return r.name
    return f"{r.name}_{unit}"


# ------------------------------------------------------------------------------------------------------- the form

_CSS = """
@page { margin: 11mm; }
* { box-sizing: border-box; }
html { background: white; color: black; }
body { margin: 0 auto; max-width: 186mm; font: 10pt/1.25 Helvetica, Arial, sans-serif; background: white;
       color: black; }
h1 { font-size: 16pt; margin: 0 0 1mm; }
h2 { font-size: 10.5pt; margin: 2mm 0 1mm; }
p { margin: 0; }
.top { display: flex; gap: 4mm; align-items: flex-start; margin-top: 1.5mm; }
.top figure { margin: 0; flex: 0 0 PICTURE_MM; width: PICTURE_MM; }
.top figure svg { width: 100%; height: auto; display: block; border: 0.3mm solid #999999; }
.top ol { margin: 0; padding-left: 5mm; }
.top li { margin-bottom: 0.8mm; }
.fields { display: grid; grid-template-columns: 1.3fr 0.8fr 1.4fr 1.8fr; gap: 0 4mm; margin-top: 1.5mm; }
.blank { border-bottom: 0.3mm solid black; height: 7mm; padding-top: 2.5mm; font-size: 9pt; }
table { border-collapse: collapse; width: 100%; break-inside: avoid; }
td, th { border: 0.3mm solid black; padding: 0.5mm 1.2mm; text-align: left; vertical-align: top; }
th { font-size: 9.5pt; }
.small { font-weight: normal; font-size: 8.5pt; }
.setup td { height: 8.5mm; font-size: 9.5pt; }
.setup { table-layout: fixed; }
.trials td { height: 6.4mm; }
.trials .n { width: 10mm; text-align: center; vertical-align: middle; }
.summary { display: flex; align-items: center; gap: 2mm; margin-top: 1.5mm; break-inside: avoid; }
.summary .name { font-weight: bold; width: 25mm; }
.box { border: 0.3mm solid black; width: 26mm; height: 10mm; padding: 0.4mm 1mm; font-size: 8.5pt; }
.tick { flex: 1; display: flex; align-items: center; gap: 1.5mm; font-size: 9pt; }
.tick span { flex: 0 0 5mm; height: 5mm; border: 0.3mm solid black; }
.note { font-size: 9pt; margin-top: 1.5mm; }
footer { margin-top: 2mm; padding-top: 1mm; border-top: 0.3mm solid black; font-size: 9pt; }
code { font-family: "Courier New", monospace; }
"""


def _e(text: str) -> str:
    return html.escape(text, quote=False)


def _label(r: Reading) -> str:
    """A reading's name, its units and words, for a setup cell or a column head."""
    extra = ["  ".join(units(r))] if r.quantity and units(r) != [r.name] else []
    if r.optional:
        extra.append("if needed")
    bits = f' <span class="small">{_e(", ".join(extra))}</span>' if extra else ""
    return f'<b>{_e(r.name)}</b>{bits}<br><span class="small">{_e(r.what)}</span>'


def _setup_table(test: str, t: Test) -> str:
    """The setup box: two readings to a row, each with a blank to write in."""
    cells = [f'<td class="label">{_label(r)}</td><td></td>' for r in t.setup]
    if test == "squeeze":
        cells.append('<td class="label"><b>the gap to press to</b><br><span class="small">the ball\'s diameter '
                     '× 0.75</span></td><td>______ × 0.75 = ______</td>')
    if not cells:
        return ""
    if len(cells) % 2:
        cells.append('<td class="label"></td><td></td>')
    rows = "".join(f"<tr>{a}{b}</tr>" for a, b in zip(cells[::2], cells[1::2]))
    cols = '<col style="width: 33%"><col style="width: 17%">' * 2
    return (f'<h2>Setup: once for all trials <span class="small">(circle the unit you used)</span></h2>'
            f'<table class="setup"><colgroup>{cols}</colgroup>{rows}</table>')


def _summary(t: Test) -> str:
    if not t.summaries:
        return ('<div class="summary"><div class="name">wheel speeds shot at</div>'
                + '<div class="box"></div>' * 3 + '<div class="tick">two or more, '
                f'{fit_launcher.SECOND_SPEED - 1:.0%} or more apart</div></div>')
    boxes = "".join(f'<div class="box">{label}</div>' for label in ("average", "lowest", "highest", "spread %"))
    tick = f'<div class="tick"><span></span>spread under {MAX_SPREAD:.0%}? If not, measure again.</div>'
    return "".join(f'<div class="summary"><div class="name">{_e(name)}</div>{boxes}{tick}</div>'
                   for name in t.summaries)


def form_html(test: str) -> str:
    """A printable blank form for one test: a self-contained page with no scripts."""
    if test not in TEST:
        raise ValueError(f"no test {test!r}; one of {', '.join(TESTS)}")
    t = TEST[test]
    steps = "".join(f"<li>{_e(s)}</li>" for s in t.steps)
    head = "".join(f"<th>{_label(r)}</th>" for r in t.trial)
    value_head = f'<th>{_e(t.value)}<br><span class="small">{_e(t.formula)}</span></th>'
    cells = "<td></td>" * (len(t.trial) + 1)
    body = "".join(f'<tr><td class="n">{i}</td>{cells}</tr>' for i in range(1, ROWS + 1))
    averaging = ("" if not t.summaries else
                 f"{_SAVE.format(min=MIN_TRIALS, rec=RECOMMENDED)} Spread = (highest − lowest) ÷ average. A trial "
                 f"that looks off can only be flagged with {OUTLIER_TRIALS} or more trials.")
    note = _e(" ".join(x for x in (t.note, averaging) if x))
    css = _CSS.replace("PICTURE_MM", f"{PICTURE_MM}mm")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_e(t.title)} form: Scrimmage</title>
<style>{css}</style>
</head>
<body>
<h1>{_e(t.title)}</h1>
<p>Gives {_e(t.gives)}. <b>You need:</b> {_e("; ".join(t.need))}.</p>
<div class="top"><figure>{picture(test)}</figure><ol>{steps}</ol></div>
<div class="fields"><div class="blank">Measured by:</div><div class="blank">Date:</div>
<div class="blank">Tools:</div><div class="blank">What you measured:</div></div>
{_setup_table(test, t)}
<h2>Trials <span class="small">(circle the unit you used)</span></h2>
<table class="trials">
<tr><th class="n">trial</th>{head}{value_head}</tr>
{body}
</table>
{_summary(t)}
<p class="note">{note}</p>
<footer>Type these into Scrimmage's Measure tab, or a <i>name</i>.measure.toml file: start one with
<code>scrimmage measure new {test} <i>name</i>.measure.toml</code>, then check it with
<code>scrimmage measure <i>name</i>.measure.toml</code>.</footer>
</body>
</html>
"""


# ------------------------------------------------------------------------------------------------ the starter file

def _toml(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return repr(v)


def _commented(text: str, comment: str) -> str:
    return f"{text:<34}# {comment}"


def _line(r: Reading) -> str:
    words = ("optional: " if r.optional else "") + r.what
    others = [k for u in UNITS[r.quantity] if (k := key(r, u)) != key(r)] if r.quantity else []
    if others:
        words += f" (or {', '.join(others)})"
    return _commented(f"# {key(r)} = {_toml(r.example)}", words)


def starter_toml(test: str, date: datetime.date | None = None) -> str:
    """A starter `<name>.measure.toml` for one test: its header to fill in, and its setup and one example trial as
    comments (uncommented, they make a file that reads)."""
    if test not in TEST:
        raise ValueError(f"no test {test!r}; one of {', '.join(TESTS)}")
    t = TEST[test]
    kind = TEST_KIND[test]
    date = date or datetime.date.today()
    lines = [f"# {t.title}: gives {t.gives}.",
             f"# Print a form to write the trials on: scrimmage measure form {test} -o {test}-form.html",
             f"# Then type them in here, one [[{test}]] table per trial, and check them: scrimmage measure <this file>",
             "# Every reading's key ends in the unit you read it in: force_lb, drop_mm.",
             "",
             "[measure]",
             f'kind = "{kind}"',
             _commented('about = ""', f'what you measured: "{ABOUT[kind]}"'),
             _commented('by = ""', "who measured it"),
             _commented(f"date = {date.isoformat()}", "when"),
             _commented('tools = ""', "what you measured it with")]
    if kind == "ball":
        lines.append(_commented('# base = "2026-fuel"', "the ball preset it measured"))
    if t.setup:
        lines += ["", _commented(f"[{test}_setup]", "once for all trials (or in a trial, for that trial alone)")]
        lines += [_line(r) for r in t.setup if r.example is not None]
    trial = [r for r in t.trial if r.example is not None]
    how_many = (f"take at least {MIN_TRIALS}, {RECOMMENDED} is better" if t.summaries else
                "at two or more wheel speeds")
    lines += ["", f"# One [[{test}]] table per trial: {how_many}.", f"# [[{test}]]", *(_line(r) for r in trial)]
    if t.example_note:
        lines.append(f"# {t.example_note}")
    return "\n".join(lines) + "\n"
