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

Language: the tests' words are marked with N_() where TEST defines them (data, read at import) and translated where
they are shown (form_html(), and the Measure tab's test_json()), in the Language setting of the moment. The few built
from numbers (f-strings) and the starter file's comments, which name the file's keys, stay American English.
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
from .. import i18n
from ..i18n import N_, t as _t, translated as _tr

TESTS = tuple(test for tests in KIND_TESTS.values() for test in tests)  # size, squeeze, drop, sled, shot, stroke
ROWS = 8  # trials a form has room for (spec C1)
RECOMMENDED = 5  # trials recommended (C1)
# A trial can be flagged as looking off only when one trial's distance can exceed OUTLIER_DISTANCES times the
# average distance, which needs more than 2 x OUTLIER_DISTANCES trials (stats.py).
OUTLIER_TRIALS = math.floor(2 * OUTLIER_DISTANCES) + 1
PICTURE_MM = 115  # the picture's printed width: a 15-unit label in a 480-unit-wide picture prints at 10 pt
SETUP_ACROSS = 3  # setup readings to a row
TYPICAL_SPEED = 9.0  # m/s: a typical shot's horizontal speed (the fit's test shots are 7 to 10 m/s across)
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


def spacing_in(fps: float) -> int:
    """The mark spacing (in, rounded up to 6 in) that a shot TYPICAL_SPEED across crosses in GOOD_FRAMES frames at
    `fps`: the shot form's example."""
    return math.ceil(fit_launcher.GOOD_FRAMES * TYPICAL_SPEED / fps / 0.0254 / 6) * 6


_T, _S, _TEXT = tests_direct.TRIAL, tests_direct.SETTING, tests_direct.SETUP_TEXT
_SHOT, _CYL = fit_launcher.SHOT_READINGS, fit_cylinder.SETUP_READINGS

TEST: dict[str, Test] = {
    "size": Test(
        title=N_("Ball size and mass"),
        gives=N_("the ball's mass and diameter"),
        need=(N_("a kitchen scale (g or oz)"), N_("a soft tape measure, or calipers")),
        steps=(N_("Weigh the ball on the kitchen scale and write its mass."),
               N_("Wrap the tape around the ball's widest part, snug but not squeezing it, and read the "
                  "circumference. Or read the diameter straight across with calipers."),
               N_("Turn the ball and measure again for the next trial."),
               N_("Work out each trial's diameter, then fill in the boxes at the bottom.")),
        setup=(),
        trial=(_r(_T["size"], "mass", N_("the scale's reading"), 142, "g"),
               _r(_T["size"], "circumference", N_("the tape around the widest part"), 18.5, "in"),
               _r(_T["size"], "diameter", N_("or across, with calipers"), optional=True)),
        value=N_("diameter worked out"), formula=N_("circumference ÷ 3.1416, or the diameter read across"),
        summaries=(N_("mass"), N_("diameter")),
        example_note=N_("or diameter_in = 5.9, read across with calipers: a circumference or a diameter, not both")),
    "squeeze": Test(
        title=N_("Ball squeeze"),
        gives=N_("the ball's stiffness: the force that squeezes it by 25% of its diameter"),
        need=(N_("a bathroom scale"), N_("two flat boards, bigger than the ball"),
              N_("a ruler, for the gap between the boards"), N_("the ball's diameter (the size test)")),
        steps=(N_("Put the bottom board on the scale and the ball on the board, then zero the scale. If it can't be "
                  "zeroed, write its reading as the tare."),
               N_("Put the top board on the ball."),
               N_("Press the top board down, level, until the gap between the boards is 75% of the ball's diameter "
                  "(diameter × 0.75: a 5.9 in ball, 4.4 in)."),
               N_("Hold it there and read the scale: write the force."),
               N_("Let go, turn the ball, and repeat for the next trial.")),
        setup=(_r(_S["squeeze"], "tare", N_("the scale's reading with the bottom board and the ball on it, if it "
                  "couldn't be zeroed"), 1.2, "lb", optional=True),
               _r(_S["squeeze"], "squeezed_to", N_("the gap you pressed to, if it wasn't 75% of the diameter "
                  "(Scrimmage converts the force)"), 4.4, "in", optional=True)),
        trial=(_r(_T["squeeze"], "force", N_("the scale's reading, pressed"), 9.8, "lb"),),
        value=N_("squeeze force"), formula=N_("force − tare (no tare if the scale was zeroed)"),
        summaries=(N_("squeeze force"),)),
    "drop": Test(
        title=N_("Ball drop"),
        gives=N_("the ball's bounce (its restitution)"),
        need=(N_("a ruler or tape measure, 18 in or more, upright against a wall"),
              N_("a phone that films slow motion"), N_("a hard floor: concrete, tile or wood, not carpet")),
        steps=(N_("Tape the ruler upright against the wall, its zero on a hard floor (not carpet)."),
               N_("Set the phone square-on to the ruler, level with where the top of the bounce will be (about 6.5 in "
                  "up), and start filming."),
               N_("Hold the ball against the ruler with its bottom at 18 in."),
               N_("Let go without pushing it."),
               N_("In the video, find the top of the first bounce and read the ruler at the bottom of the ball: "
                  "write the bounce."),
               N_("Repeat for the next trial.")),
        setup=(_r(_S["drop"], "drop", N_("the drop, floor to the bottom of the ball (18 in)"), 18, "in"),
               _plain(_TEXT["drop"], "floor", N_("what the floor is"), "concrete")),
        trial=(_r(_T["drop"], "bounce", N_("the top of the bounce, floor to the bottom of the ball"), 6.5, "in"),),
        value=N_("restitution"), formula=N_("√(bounce ÷ drop)"),
        summaries=(N_("restitution"),)),
    "sled": Test(
        title=N_("Sled pull"),
        gives=N_("the sliding friction of one material on another"),
        need=(N_("a spring scale (or a luggage scale)"), N_("a sled: a board or box with one material on its bottom"),
              N_("weights for the sled"), N_("a scale to weigh the sled")),
        steps=(N_("Put one material on the sled's bottom (a wheel's tread, a grip pad), and lay the other flat (what "
                  "it slides on)."),
               N_("Weigh the sled with its load: write it once, as the sled weight."),
               N_("Hook the spring scale to the sled and pull it level, slowly and steadily."),
               N_("Read the scale while the sled slides at a steady speed, not the first jerk that starts it moving: "
                  "write the pull."),
               N_("Repeat for the next trial.")),
        setup=(_r(_S["sled"], "sled", N_("the sled and its load, weighed"), 5.0, "lb"),
               _plain(_TEXT["sled"], "sliding", N_("the material on the sled's bottom"), "wheel tread: 40A"),
               _plain(_TEXT["sled"], "on", N_("what it slides on"), "FUEL")),
        trial=(_r(_T["sled"], "pull", N_("the spring scale at a steady slide"), 4.1, "lb"),),
        value=N_("friction"), formula=N_("pull ÷ sled weight (both in the same unit)"),
        summaries=(N_("friction"),)),
    "shot": Test(
        title=N_("Launcher shot"),
        gives=N_("the ball's stiffness, or the wheel's friction (with the ball's squeeze measured), fit to the "
                 "launcher model"),
        need=(N_("a phone that films slow motion"), N_("a board and tape"), N_("a tachometer or the robot's log"),
              N_("the launcher's setup file")),
        steps=(N_("Tape two vertical marks on a board a few inches behind the ball's path, the first near the exit."),
               f"Space them for {fit_launcher.GOOD_FRAMES} or more frames: for a shot about {TYPICAL_SPEED:g} m/s "
               f"across, {spacing_in(240)} in at 240 fps (24 in at 960 fps gives far more).",
               N_("Set the phone to its highest frame rate, square-on, 10 or more times as far in front of the path as "
                  "the board is behind it."),
               N_("Let the wheel settle; write the speed you measured, not the setpoint."),
               N_("Feed ONE ball and film it."),
               N_("Count the frames from mark to mark (its speed across, not up)."),
               f"Shoot at two or more wheel speeds, {fit_launcher.SECOND_SPEED - 1:.0%} or more apart."),
        setup=(_plain(fit_launcher.SETUP_TEXT, "setup", N_("the launcher setup file the shots were taken on"),
                      "shooter.toml"),
               _plain(fit_launcher.SETUP_TEXT, "ball", N_("the ball's file: its squeeze fits the wheel's friction"),
                      "fuel.measure.toml", optional=True),
               _r(_SHOT, "marks", N_("the tape marks' spacing"), 24, "in"),
               _plain(fit_launcher.SETUP_NUMBERS, "fps", N_("the camera's frame rate"), 960),
               _plain(fit_launcher.SETUP_NUMBERS, "hood_deg", N_("the hood's exit angle, if not the setup's"), 45,
                      optional=True)),
        trial=(_plain(fit_launcher.SHOT_NUMBERS, "wheel_rps", N_("the wheel speed you measured"), 50),
               _plain(fit_launcher.SHOT_NUMBERS, "hood_deg", N_("only if not the setup box's"), optional=True),
               _r(_SHOT, "frames", N_("frames from one mark to the next"), 70)),
        value=N_("speed across"),
        formula=f"marks × fps ÷ frames: the marks' unit ({', '.join(UNITS['length'])}) per second",
        summaries=(),
        note=N_("Scrimmage fits the shots to the launcher model: they aren't averaged. Shots at two or more wheel "
                "speeds check the fit.")),
    "stroke": Test(
        title=N_("Cylinder stroke"),
        gives=N_("the valve's flow (its conductance), fit to the pneumatic model"),
        need=(N_("the cylinder's bore, rod and stroke (its label or datasheet)"), N_("what the rod moves, weighed"),
              N_("a phone that films slow motion (or a stopwatch, for a slow stroke)")),
        steps=(N_("Write the cylinder's size, its load and the working pressure in the setup box."),
               N_("Start with the rod all the way in and the tanks full."),
               N_("Time the stroke from the valve's light (or the Pneumatic Hub channel's) coming on to the rod at "
                  "full stroke. Don't time it by the click: a slow-motion video's sound is unreliable."),
               N_("A quick stroke (under 0.2 s) needs filming: count the frames from the light coming on to the rod "
                  "stopping. A stopwatch can't time it closely enough."),
               N_("Repeat for the next trial.")),
        setup=(_r(_CYL, "bore", N_("the piston's diameter"), 1.0625, "in"),
               _r(_CYL, "rod", N_("the rod's diameter"), 0.3125, "in"),
               _r(_CYL, "stroke", N_("how far the rod travels"), 6, "in"),
               _r(_CYL, "load_mass", N_("what the rod moves, besides itself"), 2, "lb", optional=True),
               _r(_CYL, "rod_mass", N_("the rod's own mass (else 100 g)"), 100, "g", optional=True),
               _r(_CYL, "friction", N_("the seals' friction (else 8 N)"), 8, "n", optional=True),
               _plain(fit_cylinder.SETUP_ONLY, "vertical", N_("true if the rod lifts its load straight up (else "
                      "false)"), False, optional=True),
               _plain(fit_cylinder.SETUP_ONLY, "working_psi", N_("the regulator's working pressure (else 60)"), 60,
                      optional=True),
               _plain(fit_cylinder.STROKE_NUMBERS, "fps", N_("the camera's frame rate, if filmed"), optional=True)),
        trial=(_r(fit_cylinder.STROKE_READINGS, "time", N_("light on to full stroke"), 0.21, "s"),
               _r(fit_cylinder.STROKE_READINGS, "frames", N_("or frames, filmed"), optional=True)),
        value=N_("time"), formula=N_("frames ÷ fps, or the time you read"),
        summaries=(N_("time"),),
        example_note=N_("or frames = 50, with fps = 240 in [stroke_setup]: a time or frames, not both")),
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
.trials td { height: 6mm; }
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
        extra.append(_t("if needed"))
    bits = f' <span class="small">{_e(", ".join(extra))}</span>' if extra else ""
    return f'<b>{_e(r.name)}</b>{bits}<br><span class="small">{_e(_tr(r.what))}</span>'


def _setup_table(test: str, t: Test) -> str:
    """The setup box: three readings to a row, each with a blank to write in."""
    cells = [f'<td class="label">{_label(r)}</td><td></td>' for r in t.setup]
    if test == "squeeze":
        gap, times = _e(_t("the gap to press to")), _e(_t("the ball's diameter × 0.75"))
        cells.append(f'<td class="label"><b>{gap}</b><br><span class="small">{times}</span></td>'
                     '<td>______ × 0.75 = ______</td>')
    if not cells:
        return ""
    cells += ['<td class="label"></td><td></td>'] * (-len(cells) % SETUP_ACROSS)
    rows = "".join(f"<tr>{''.join(cells[i:i + SETUP_ACROSS])}</tr>" for i in range(0, len(cells), SETUP_ACROSS))
    cols = '<col style="width: 22%"><col style="width: 11.3%">' * SETUP_ACROSS
    title, circle = _e(_t("Setup: once for all trials")), _e(_t("(circle the unit you used)"))
    return (f'<h2>{title} <span class="small">{circle}</span></h2>'
            f'<table class="setup"><colgroup>{cols}</colgroup>{rows}</table>')


def _summary(t: Test) -> str:
    if not t.summaries:
        return (f'<div class="summary"><div class="name">{_e(_t("wheel speeds shot at"))}</div>'
                + '<div class="box"></div>' * 3 + '<div class="tick">'
                + _e(_t("two or more, {apart} or more apart", apart=f"{fit_launcher.SECOND_SPEED - 1:.0%}"))
                + '</div></div>')
    boxes = "".join(f'<div class="box">{_e(label)}</div>' for label in (_t("average"), _t("lowest"), _t("highest"),
                                                                         _t("spread %")))
    tick = ('<div class="tick"><span></span>'
            + _e(_t("spread under {spread}? If not, measure again.", spread=f"{MAX_SPREAD:.0%}")) + "</div>")
    return "".join(f'<div class="summary"><div class="name">{_e(_tr(name))}</div>{boxes}{tick}</div>'
                   for name in t.summaries)


def form_html(test: str) -> str:
    """A printable blank form for one test: a self-contained page with no scripts."""
    if test not in TEST:
        raise ValueError(f"no test {test!r}; one of {', '.join(TESTS)}")
    t = TEST[test]
    # the form is for people: in the Language setting (_tr: TEST's words, marked with N_())
    steps = "".join(f"<li>{_e(_tr(s))}</li>" for s in t.steps)
    head = "".join(f"<th>{_label(r)}</th>" for r in t.trial)
    value_head = f'<th>{_e(_tr(t.value))}<br><span class="small">{_e(_tr(t.formula))}</span></th>'
    cells = "<td></td>" * (len(t.trial) + 1)
    body = "".join(f'<tr><td class="n">{i}</td>{cells}</tr>' for i in range(1, ROWS + 1))
    averaging = ("" if not t.summaries else
                 _t("Take at least {min} trials; {rec} is better. Spread = (highest − lowest) ÷ average. A trial "
                    "that looks off can only be flagged with {flagged} or more trials.", min=MIN_TRIALS,
                    rec=RECOMMENDED, flagged=OUTLIER_TRIALS))
    note = _e(" ".join(x for x in (_tr(t.note) if t.note else "", averaging) if x))
    css = _CSS.replace("PICTURE_MM", f"{PICTURE_MM}mm")
    title = _tr(t.title)
    gives = _e(_t("Gives {what}.", what=_tr(t.gives)))
    by, when, tools, what = (_e(x) for x in (_t("Measured by:"), _t("Date:"), _t("Tools:"), _t("What you measured:")))
    footer = _t("Type these into Scrimmage's Measure tab, or a <i>name</i>.measure.toml file: start one with\n"
                "<code>scrimmage measure new {test} <i>name</i>.measure.toml</code>, then check it with\n"
                "<code>scrimmage measure <i>name</i>.measure.toml</code>.", test=test)
    return f"""<!doctype html>
<html lang="{i18n.current_language()}">
<head>
<meta charset="utf-8">
<title>{_e(_t("{test} form: Scrimmage", test=title))}</title>
<style>{css}</style>
</head>
<body>
<h1>{_e(title)}</h1>
<p>{gives} <b>{_e(_t("You need:"))}</b> {_e("; ".join(_tr(x) for x in t.need))}.</p>
<div class="top"><figure>{picture(test)}</figure><ol>{steps}</ol></div>
<div class="fields"><div class="blank">{by}</div><div class="blank">{when}</div>
<div class="blank">{tools}</div><div class="blank">{what}</div></div>
{_setup_table(test, t)}
<h2>{_e(_t("Trials"))} <span class="small">{_e(_t("(circle the unit you used)"))}</span></h2>
<table class="trials">
<tr><th class="n">{_e(_t("trial"))}</th>{head}{value_head}</tr>
{body}
</table>
{_summary(t)}
<p class="note">{note}</p>
<footer>{footer}</footer>
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
