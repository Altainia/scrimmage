"""The gate for unwrapped text (sub-project 20b, spec L1): every piece of text a student reads in the web app goes
through t() or tn() (or N_() and translated()), so a contributor can translate it. Like test_units_sweep.py, this
reads the code, not the running app, and fails on text that isn't wrapped, unless ALLOWED names it with a reason.

What it reads:
- **The page's JavaScript** (static/*.js but i18n.js and vendor/): every string literal that isn't an argument of
  t()/tn(), a context, a console call, a CSS class or selector, a font, an object key or a value compared with ===.
  It flags a literal holding a phrase (a word, a space, then more words) or one capitalized word ("Trim").
- **index.html:** text and title, placeholder, aria-label and alt attributes without their data-i18n* attribute.
- **The Python text sources** (scrimmage/**/*.py but NOT_SHOWN's modules): a literal string or f-string with a
  phrase, passed to an error or a notice (an exception, a Problem, warn()/warning()/error() on anything but a
  logger, err()/_err()/fail(), a list's append()) or returned in a dict as its "error", "message", "problem",
  "note", "warning", "why", "hint", "text", "label", "title" or "detail". said()/Said() take their machine text
  first (report.json, MCP), and their shown words in a function that calls t(), so those arguments are skipped.

A phrase is what's left once the {placeholders} (an f-string's {...}, a template's ${...}) are taken out, so a
locator join such as f"{where}: {e}" has no words and isn't flagged.

What it can't see: a lowercase single word ("to"), text built from variables, and text a Python function returns
outside a dict. The wrapping tasks found those by hand; this gate keeps the found ones from coming back.

ALLOWED: (file, a piece of the flagged text) -> why it stays as it is. A piece matches every flagged text in that
file that contains it. An entry that matches nothing fails, to keep the list true.
"""
from __future__ import annotations

import ast
import re
from html.parser import HTMLParser
from pathlib import Path

from scrimmage.i18n_tool import _js_string, _mask_js

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "scrimmage"
STATIC = PACKAGE / "assembler" / "static"

# Modules whose every word is the CLI's or MCP's: American English by the spec's non-goal and rulings (the CLI
# runs inside i18n.fixed("en-US"), every MCP tool too), never shown in the web app.
NOT_SHOWN = {
    "scrimmage/__main__.py": "the CLI's own messages and --help (American English, non-goal; the CLI ruling)",
    "scrimmage/mcp_server.py": "MCP tool results and refusals: machine text, en-US (L2)",
    "scrimmage/measure/cli.py": "`scrimmage measure` CLI output (American English, the CLI ruling)",
    "scrimmage/i18n_tool.py": "`scrimmage i18n` CLI, for contributors (American English)",
}

# -- the reasons, shared by many entries --------------------------------------------------------------------------
API = "API-contract refusal: the page never sends such a request, so no student sees it"
ASSERT = "internal assertion: a programming error, never a student's mistake"
CLI = "CLI-only text (American English, the CLI ruling)"
MCP = "MCP-only refusal or result: machine text, en-US (L2)"
THREAD = ("runtime warning built on the simulation's or bridge's thread: machine text in report notes and MCP "
          "state, and a new thread doesn't inherit the en-US guard (6d ruling)")
DEV = "developer-only text (the terminal, a stock part's definition, a tool's subprocess)"
MARKUP = "markup or code around values, no words of ours"
SETTINGS = ("the settings file's own problems and the --units/--language/--theme refusals: read before the "
            "language is known (i18n reads the settings), shown in the terminal and the CLI")
LOCATOR = "a locator join (a TOML table, a step or a file) around an already-translated message"
LOG = "the run's console log line (the robot program's log, shown as it is)"
PICKER = "a picker value (a key a test file holds); its words are t()"
FALLBACK = "settings.js's fallback before app.js hands it its words (useWords; i18n.js imports settings.js)"

ALLOWED = {
    # ---- Python -----------------------------------------------------------------------------------------------
    ("scrimmage/assembler/bench_session.py", "version (the integer from the state) is required"): API,
    ("scrimmage/assembler/bench_session.py", "values must be a table of dotted keys"): API,
    ("scrimmage/assembler/bench_session.py", "unknown op"): API,
    ("scrimmage/assembler/bench_session.py", "no such file"): API,
    ("scrimmage/assembler/bench_session.py", "no bench job"): API,
    ("scrimmage/assembler/bench_session.py", "a bench run is in progress"): API + " (the page disables Run during one)",
    ("scrimmage/assembler/bench_session.py", "no pictures: %s"): DEV + " (a warning() on bench_session's own log)",
    ("scrimmage/assembler/measure_session.py", "must be a number, text, or true or false"): API,
    ("scrimmage/assembler/measure_session.py", "must be an object {cells, extra}"): API,
    ("scrimmage/assembler/measure_session.py", "cells and extra must be objects"): API,
    ("scrimmage/assembler/measure_session.py", "file must be {measure, tests}"): API,
    ("scrimmage/assembler/measure_session.py", "measure must be"): API,
    ("scrimmage/assembler/measure_session.py", "no reading"): API,
    ("scrimmage/assembler/measure_session.py", "must be {unit, value}"): API,
    ("scrimmage/assembler/measure_session.py", "must be text"): API,
    ("scrimmage/assembler/measure_session.py", "test; it has"): API,
    ("scrimmage/assembler/measure_session.py", "must be {setup, trials: [...]}"): API,
    ("scrimmage/assembler/measure_session.py", "version (the integer from the state) is required"): API,
    ("scrimmage/assembler/measure_session.py", "origin must be a later trial's index"): API,
    ("scrimmage/assembler/measure_session.py", "is averaged, not fit"): API + " (no fit of an averaged test)",
    ("scrimmage/assembler/runs.py", "error: couldn't read the report"): LOG,
    ("scrimmage/assembler/server.py", "send units, language or theme to change"): API,
    ("scrimmage/assembler/server.py", 'speed must be a positive number or "max"'): API,
    ("scrimmage/assembler/server.py", "bad edit: "): API + " (malformed JSON values from a client)",
    ("scrimmage/assembler/simlock.py", "owner must be one of"): ASSERT,
    ("scrimmage/assembler/stream.py", "unknown message kind"): ASSERT,
    ("scrimmage/assembler/templates.py", "unknown template"): ASSERT + " (the page sends only known kinds)",
    ("scrimmage/bench/compare.py", "a [sweep] of"): "bench_compare's summary (MCP and CLI only): machine text",
    ("scrimmage/bench/fields.py", "unknown choices kind"): ASSERT,
    ("scrimmage/bench/gallery.py", "<img alt="): MARKUP,
    ("scrimmage/bench/gallery.py", '<ul class="head">'): MARKUP,
    ("scrimmage/bench/gallery.py", "<tr><td><a href="): MARKUP,
    ("scrimmage/bench/gallery.py", "compare_picture needs at least one result"): ASSERT,
    ("scrimmage/bench/gallery.py", "is not a folder of bench files"): MCP + " (bench_gallery and the CLI)",
    ("scrimmage/bench/gallery.py", "has no bench files"): MCP + " (bench_gallery and the CLI)",
    ("scrimmage/bench/pictures.py", 'view must be "side" or "top"'): ASSERT,
    ("scrimmage/bench/run.py", "trace_hz must be positive"): ASSERT,
    ("scrimmage/bench/session.py", "run_lock is already held"): ASSERT,
    ("scrimmage/bench/view.py", "--slow must be greater than 0"): CLI + " (`scrimmage bench view --slow`)",
    ("scrimmage/blocks/conditions.py", "unknown condition"): ASSERT + " (conditions come from the checked file)",
    ("scrimmage/blocks/program.py", "to TOML"): ASSERT + " (the TOML writer)",
    ("scrimmage/bridge.py", "config expects"): THREAD,
    ("scrimmage/bridge.py", "--speed {} with {} s steps"): CLI + " (`--speed`, the CLI and MCP)",
    ("scrimmage/climb/run.py", "| reached"): CLI + " (`scrimmage climb run`'s report)",
    ("scrimmage/climb/run.py", "over its {} in limit"): CLI + " (`scrimmage climb run`'s report)",
    ("scrimmage/disc/flight.py", "dt must be positive"): ASSERT,
    ("scrimmage/disc/launcher.py", "but the launcher's clock is at"): ASSERT,
    ("scrimmage/disc/launcher.py", "voltage(s) for"): ASSERT,
    ("scrimmage/freecad_tools.py", "FreeCAD"): DEV + " (`scrimmage freecad`, the CLI only)",
    ("scrimmage/freecad_tools.py", "isn't an executable"): DEV + " (`scrimmage freecad`, the CLI only)",
    ("scrimmage/i18n.py", "needs a value for"): ASSERT + " (a t() call missing a placeholder's value)",
    ("scrimmage/java_tools.py", "no JDK found"): CLI + " (`scrimmage java`; the page uses robotcode's words)",
    ("scrimmage/java_tools.py", "downloaded ASM has sha256"): CLI + " (`scrimmage java`)",
    ("scrimmage/java_tools.py", "the fast-mode library"): CLI + " (`scrimmage java`)",
    ("scrimmage/java_tools.py", "isn't a robot project folder"): CLI + " (`scrimmage java`)",
    ("scrimmage/java_tools.py", "isn't a GradleRIO robot project"): CLI + " (`scrimmage java`)",
    ("scrimmage/java_tools.py", "can't create the cache directory"): CLI + " (`scrimmage java`)",
    ("scrimmage/java_tools.py", "can't write"): CLI + " (`scrimmage java`)",
    ("scrimmage/java_tools.py", "could not download ASM"): CLI + " (`scrimmage java`)",
    ("scrimmage/java_tools.py", "gradlew publish wrote nothing"): CLI + " (`scrimmage java`)",
    ("scrimmage/java_tools.py", "can't run"): CLI + " (`scrimmage java`)",
    ("scrimmage/launcher/flight.py", "cannot fly a ball whose status is"): ASSERT,
    ("scrimmage/launcher/inputs.py", "unhandled type"): ASSERT,
    ("scrimmage/lockstep.py", 'speed must be a positive number or "max"'): "the lockstep protocol's own errors (CLI)",
    ("scrimmage/lockstep.py", "must be a whole multiple of the physics timestep"): "the lockstep protocol's own errors",
    ("scrimmage/measure/forms.py", "isn't one of"): ASSERT + " (form and test names come from code)",
    ("scrimmage/measure/forms.py", "no test {}; one of"): ASSERT + " (form and test names come from code)",
    ("scrimmage/measure/forms.py", '<td class="label">'): MARKUP + " (the printed form's blank)",
    ("scrimmage/measure/stats.py", "summary needs"): ASSERT,
    ("scrimmage/measure/units.py", "unknown quantity"): ASSERT,
    ("scrimmage/nt_reader.py", "no NetworkTables topic"): MCP + " (nt_get)",
    ("scrimmage/parts/overlay.py", "no part folder"): ASSERT,
    ("scrimmage/parts/stock/__init__.py", "a whole number"): CLI + " (`scrimmage parts describe`)",
    ("scrimmage/parts/stock/__init__.py", "or a number greater than 0"): CLI + " (`scrimmage parts describe`)",
    ("scrimmage/parts/stock/__init__.py", "[min, max] with min < max"): CLI + " (`scrimmage parts describe`)",
    ("scrimmage/parts/stock/__init__.py", "greater than 0"): CLI + " (`scrimmage parts describe`)",
    ("scrimmage/parts/stock/__init__.py", "at least {}"): CLI + " (`scrimmage parts describe`)",
    ("scrimmage/parts/stock/__init__.py", "at most {}"): CLI + " (`scrimmage parts describe`)",
    ("scrimmage/parts/stock/__init__.py", "one of {}"): CLI + " (`scrimmage parts describe`)",
    ("scrimmage/parts/stock/__init__.py", "limited by other parameters"): CLI + " (`scrimmage parts describe`)",
    ("scrimmage/parts/stock/__init__.py", "unknown kind"): DEV + " (a stock part definition's Param)",
    ("scrimmage/parts/stock/__init__.py", "needs choices"): DEV + " (a stock part definition's Param)",
    ("scrimmage/parts/toml_writer.py", "to part.toml"): ASSERT + " (the TOML writer)",
    ("scrimmage/pneumatics/runtime.py", "is on a REV Pneumatic Hub"): THREAD,
    ("scrimmage/pneumatics/runtime.py", "the robot never opened Pneumatic Hub"): THREAD,
    ("scrimmage/robot_process.py", "the robot program is already running"): ASSERT + " (started twice)",
    ("scrimmage/scenario_runner.py", "this session runs the range"): MCP + " (run_scenario on a live session)",
    ("scrimmage/scenario_runner.py", "but the scenario uses"): MCP + " (run_scenario on a live session)",
    ("scrimmage/scenario_runner.py", "robot_env can't reach the robot program"): (MCP + " (run_scenario on a "
                                                                                    "live session)"),
    ("scrimmage/sensors/runtime.py", "the robot is in its way"): THREAD,
    ("scrimmage/settings.py", 'units must be "imperial" or "metric"'): SETTINGS,
    ("scrimmage/settings.py", 'theme must be "system", "light" or "dark"'): SETTINGS,
    ("scrimmage/settings.py", "language must be one of"): SETTINGS,
    ("scrimmage/settings.py", "unknown setting"): SETTINGS,
    ("scrimmage/settings.py", "isn't imperial or metric; using imperial units"): SETTINGS,
    ("scrimmage/settings.py", "isn't one of Scrimmage's languages"): SETTINGS,
    ("scrimmage/settings.py", "isn't system, light or dark"): SETTINGS,
    ("scrimmage/testrange/ballpath.py", "rearmost ball"): ASSERT,
    ("scrimmage/testrange/build.py", "[range] "): LOCATOR,
    ("scrimmage/testrange/shooter.py", "fed a disc while the channel holds one"): ASSERT,
    ("scrimmage/testrange/spec.py", "[range]"): LOCATOR,
    ("scrimmage/units.py", "unknown unit system"): ASSERT,
    ("scrimmage/units.py", "unknown kind"): ASSERT,
    # ---- the page's JavaScript --------------------------------------------------------------------------------
    ("scrimmage/assembler/static/app.js", "Home"): "a key's name (KeyboardEvent.key), never shown",
    ("scrimmage/assembler/static/app.js", "End"): "a key's name (KeyboardEvent.key), never shown",
    ("scrimmage/assembler/static/bench.js", "vec vec-1"): "CSS classes",
    ("scrimmage/assembler/static/bench.js", "-dialog form"): "a CSS selector",
    ("scrimmage/assembler/static/bench_edit.js", "hood (curve)"): ("a bench file's section name and a curve's name, "
                                                                   "shown as the file's words beside them"),
    ("scrimmage/assembler/static/blocks_defs.js", "Condition"): "a Blockly style's name, never shown",
    ("scrimmage/assembler/static/blocks_defs.js", "next bar"): PICKER,
    ("scrimmage/assembler/static/blocks_defs.js", "any bar"): PICKER,
    ("scrimmage/assembler/static/gamepad.js", "Digit"): "a key's code (KeyboardEvent.code), never shown",
    ("scrimmage/assembler/static/library.js", "Chassis"): "a stock group's key, shown through groupWord's t()",
    ("scrimmage/assembler/static/library.js", "Drivetrain"): "a stock group's key, shown through groupWord's t()",
    ("scrimmage/assembler/static/library.js", "Mechanisms"): "a stock group's key, shown through groupWord's t()",
    ("scrimmage/assembler/static/measure.js", "#measure-trials tr[data-trial="): "a CSS selector",
    ("scrimmage/assembler/static/report.js", "rp-skip muted"): "CSS classes",
    ("scrimmage/assembler/static/scenario.js", "sc-field sc-desc"): "CSS classes",
    ("scrimmage/assembler/static/settings.js", "can't reach the editor"): FALLBACK,
    ("scrimmage/assembler/static/settings.js", "the server answered"): FALLBACK,
    ("scrimmage/assembler/static/step_editor.js", "<svg viewBox="): MARKUP + " (an icon)",
    ("scrimmage/assembler/static/step_editor.js", "step{} step{}/step"): "field ids (data-fields), never shown",
    ("scrimmage/assembler/static/step_editor.js", "step{}/axes step{}/buttons"): "field ids (data-fields), never shown",
    ("scrimmage/assembler/static/step_editor.js", "battery, motor.<name>.current, nt:/…"): ("a signal field's "
                                                                                             "placeholder: signal "
                                                                                             "names a student types"),
    ("scrimmage/assembler/static/test.js", "couldn't load"): "an Error a failed script load throws: the console only",
    ("scrimmage/assembler/static/test_run.js", "test-sweep-row test-sweep-norun"): "CSS classes",
    ("scrimmage/assembler/static/units.js", "unknown kind"): ASSERT,
    ("scrimmage/assembler/static/units.js", "could be {}: say which"): ASSERT + " (a key unit asked of code)",
    ("scrimmage/assembler/static/units.js", "isn't a key unit"): ASSERT + " (a key unit asked of code)",
    # ---- index.html -------------------------------------------------------------------------------------------
    ("scrimmage/assembler/static/index.html", "scrimmage"): "the product's name",
    ("scrimmage/assembler/static/index.html", "assembler"): "the product's name",
    ("scrimmage/assembler/static/index.html", "English (US)"): ("a language's own name, never translated; app.js "
                                                                "renderSettings builds the row from the catalogs"),
    ("scrimmage/assembler/static/index.html", "Theme"): "app.js renderSettings writes the Theme row's words with t()",
    ("scrimmage/assembler/static/index.html", "System"): "app.js renderSettings writes the Theme row's words with t()",
    ("scrimmage/assembler/static/index.html", "Light"): "app.js renderSettings writes the Theme row's words with t()",
    ("scrimmage/assembler/static/index.html", "Dark"): "app.js renderSettings writes the Theme row's words with t()",
}


# -- reading the code ---------------------------------------------------------------------------------------------
PHRASE = re.compile(r"[A-Za-z]{2,}\S*\s+[A-Za-z(]")
FIELD = re.compile(r"\{[^{}]*\}")


def _words(text: str) -> str:
    return FIELD.sub("", text)


def _is_phrase(text: str) -> bool:
    return bool(PHRASE.search(_words(text)))


# Python ----------------------------------------------------------------------------------------------------------
SINKS = {"append", "warn", "warning", "error", "fail", "_err", "err", "Problem", "said", "Said"}
LOGGERS = re.compile(r"^_?(?:log|logger|LOG|logging)$")
MACHINE_ARGS = {"said": {0, 2}, "Said": {0}}       # the check key and the machine text; the words are a t() function
DICT_KEYS = {"error", "message", "problem", "note", "warning", "why", "hint", "text", "label", "title", "detail"}
KW_ARGS = {"message", "msg", "problem", "note", "text", "detail", "hint"}


def _py_text(node) -> str | None:
    """A literal's text, an f-string's with {} for each value, a %- or +-built string's literal part; else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left, right = _py_text(node.left), _py_text(node.right)
        if left is None and right is None:
            return None
        return (left or "{}") + ("" if isinstance(node.op, ast.Mod) else (right or "{}"))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"
            and isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str)):
        return node.func.value.value
    return None


def python_hits(text: str) -> list[tuple[int, str]]:
    """(line, text) for each unwrapped phrase passed to an error or notice, or returned as a dict's message."""
    out = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name is None:
                continue
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and (
                    LOGGERS.match(f.value.id) or (f.value.id == "warnings" and name == "warn")):
                continue        # the terminal's log, not the page
            if not (name in SINKS or re.search(r"(?:Error|Exception|Warning)$", name)):
                continue
            skip = MACHINE_ARGS.get(name, set())
            args = [a for k, a in enumerate(node.args) if k not in skip]
            args += [kw.value for kw in node.keywords if kw.arg in KW_ARGS]
            for a in args:
                said = _py_text(a)
                if said is not None and _is_phrase(said):
                    out.append((a.lineno, said))
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in DICT_KEYS:
                    said = _py_text(v)
                    if said is not None and _is_phrase(said):
                        out.append((v.lineno, said))
    return sorted(out)


# JavaScript ------------------------------------------------------------------------------------------------------
WRAPPED = re.compile(r"(?<![\w$.])(?:i18n\s*\.\s*)?tn?\s*\(\s*$")              # t("…") or the first of tn("…", …)
SECOND_OF_TN = re.compile(r"(?<![\w$.])(?:i18n\s*\.\s*)?tn\s*\(\s*([\"'`])\s*\1\s*,\s*$")
NOT_TEXT = re.compile(r"(?:\bcontext\s*:|\bconsole\s*\.\s*\w+\s*\(|\b(?:class|className)\s*[:=]|"
                      r"\bclassList\s*\.\s*\w+\s*\(|\b(?:querySelector(?:All)?|closest|matches|\$\$?)\s*\(|"
                      r"\.font\s*=|\bmatchMedia\s*\(|\b(?:includes|has|get|delete|startsWith|endsWith)\s*\(|"
                      r"\bimport\b[^;]*\bfrom|\bimport\s*\()\s*$")
COMPARED = ("===", "!==", "==", "!=")
ONE_WORD = re.compile(r"^\s*[A-Z][a-z]+[a-z.…:!?]*\s*$")


def _js_literals(src: str, mask: str):
    """(offset, end, text) for each string literal: a template's ${...} is {} in its text, and the literals inside
    a ${...} are listed too."""
    out = []

    def scan(a: int, b: int) -> None:
        i = a
        while i < b:
            c = mask[i]
            if c in "\"'":
                j = mask.index(c, i + 1)
                value, _, _ = _js_string(src, i)
                out.append((i, j + 1, value if value is not None else src[i + 1:j]))
                i = j + 1
            elif c == "`":
                parts, j = [], i + 1
                while j < len(mask) and mask[j] != "`":
                    if mask.startswith("${", j):
                        depth, k = 1, j + 2
                        while k < len(mask) and depth:
                            depth += {"{": 1, "}": -1}.get(mask[k], 0)
                            k += 1
                        scan(j + 2, k - 1)
                        parts.append("{}")
                        j = k
                    else:
                        parts.append(src[j])
                        j += 1
                out.append((i, j + 1, "".join(parts)))
                i = j + 1
            else:
                i += 1

    scan(0, len(src))
    return out


def js_hits(src: str) -> list[tuple[int, str]]:
    """(line, text) for each string literal that reads as words a student would see, unwrapped."""
    mask = _mask_js(src)
    out = []
    for start, end, text in _js_literals(src, mask):
        before = mask[max(0, start - 300):start]
        after = mask[end:end + 40].lstrip()
        tight = before.rstrip()
        quoted = re.sub(r"([\"'`])\s*\1", r"\1\1", before)      # a blanked literal: its quotes together
        if (WRAPPED.search(before) or SECOND_OF_TN.search(quoted) or NOT_TEXT.search(before)
                or (tight.endswith(("{", ",")) and after.startswith(":"))          # an object's key
                or tight.endswith(COMPARED) or after.startswith(COMPARED)          # a key compared
                or (tight.endswith("[") and after.startswith("]"))                 # a key looked up
                or re.search(r"\bcase$", tight)):                                    # a switch's key
            continue
        words = _words(text)
        if PHRASE.search(words) or ONE_WORD.match(words):
            out.append((src.count("\n", 0, start) + 1, text))
    return out


# index.html ------------------------------------------------------------------------------------------------------
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
HTML_ATTRS = ("title", "placeholder", "aria-label", "alt")
LETTERS = re.compile(r"[A-Za-z]{2,}")


class _Unwrapped(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open: list[tuple[str, bool]] = []      # (tag, has data-i18n)
        self.hits: list[tuple[int, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for name in HTML_ATTRS:
            if a.get(name) and LETTERS.search(a[name]) and f"data-i18n-{name}" not in a:
                self.hits.append((self.getpos()[0], a[name]))
        if tag not in VOID:
            self.open.append((tag, "data-i18n" in a))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.open.pop()

    def handle_endtag(self, tag):
        while self.open and self.open.pop()[0] != tag:
            pass

    def handle_data(self, data):
        if any(tag in ("script", "style") for tag, _ in self.open) or (self.open and self.open[-1][1]):
            return
        if LETTERS.search(data):
            self.hits.append((self.getpos()[0], data.strip()))


def html_hits(text: str) -> list[tuple[int, str]]:
    p = _Unwrapped()
    p.feed(text)
    p.close()
    return p.hits


# -- the gate ------------------------------------------------------------------------------------------------------
def _files():
    for p in sorted(PACKAGE.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if "vendor" not in p.parts and rel not in NOT_SHOWN:
            yield rel, python_hits
    for p in sorted(STATIC.glob("*.js")):
        if p.name != "i18n.js":
            yield p.relative_to(ROOT).as_posix(), js_hits
    yield (STATIC / "index.html").relative_to(ROOT).as_posix(), html_hits


def _flagged() -> list[tuple[str, int, str]]:
    return [(rel, line, text) for rel, read in _files()
            for line, text in read((ROOT / rel).read_text(encoding="utf-8"))]


def _allowed(rel: str, text: str) -> bool:
    return any(f == rel and piece in text for f, piece in ALLOWED)


def test_no_unwrapped_text():
    bad = [f"{rel}:{line}: {text!r}" for rel, line, text in _flagged() if not _allowed(rel, text)]
    assert not bad, ("wrap these with t()/tn() (or N_() and translated()), or allow one in ALLOWED with why:\n"
                     + "\n".join(bad))


def test_every_allowed_entry_is_still_needed():
    flagged = _flagged()
    stale = [k for k in ALLOWED if not any(rel == k[0] and k[1] in text for rel, _, text in flagged)]
    assert not stale, stale
    assert all(why for why in ALLOWED.values()) and all(why for why in NOT_SHOWN.values())
    assert all((ROOT / f).is_file() for f in NOT_SHOWN)


def test_the_gate_catches_planted_text():
    """A self-test: an unwrapped string planted in JavaScript, Python and HTML is found; its wrapped twin isn't."""
    js = """import { t, tn } from "./i18n.js";
const ok = t("Saved the file"), n = tn("{count} part", "{count} parts", 2), cls = { class: "btn btn-small" };
const bad = h("p", { text: "Couldn't save the file" });
const also = `Saved ${n} parts`;
el.title = "Trim";
if (r.status === "timed out") console.warn("this is for the console");
const LABELS = { "check failed": () => t("check failed") };
"""
    assert [text for _, text in js_hits(js)] == ["Couldn't save the file", "Saved {} parts", "Trim"]
    py = '''from .i18n import t
def f(e, where):
    if e:
        raise ValueError("the robot can't start here")
    problems.append(f"{where}: {e}")                       # a locator join: no words
    log.warning("only the terminal reads this")
    raise ConfigError(t("the robot can't start here"))
    return {"ok": False, "error": f"no robot named {where}"}
    report.implicit.append(said("robot program running", True, "the machine words", lambda: t("the words")))
'''
    assert [text for _, text in python_hits(py)] == ["the robot can't start here", "no robot named {}"]
    html = ('<p data-i18n="Wrapped words">Wrapped words</p><p>Unwrapped words</p>'
            '<button title="Undo it" data-i18n-title="Undo it">x</button><button title="Redo it">y</button>')
    assert [text for _, text in html_hits(html)] == ["Unwrapped words", "Redo it"]
