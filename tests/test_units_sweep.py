"""No hard-coded units left (spec Goal 2, U1): every length, speed, mass, force, torque, pressure, density and spring
rate the web app shows goes through the Units setting (static/units.js, scrimmage/units.py).

The sweep reads the string literals of the web app's JavaScript, its page (index.html) and the Python modules that
write its words and reports. It finds a unit after a number ("0.5 m", `${x} in`), a unit in brackets ("speed
(m/s)") and, in the JavaScript, a string that is just a unit ("kg"). Each hit must be in ALLOWED with its reason; a
new one fails, so a new display site names its kind instead (units.fmt(si, "length")). Angles, time, current and
voltage read the same in both systems, so they aren't looked for. An entry that no longer matches anything fails too,
to keep the list true.

Not swept: units.js and units.py (the table itself), and the Python modules that check files (blocks/program.py,
bench/spec.py, ...): their messages name the file's own keys and numbers, which stay in the file's units."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "scrimmage" / "assembler" / "static"
PY = ["blocks/conditions.py", "blocks/placed.py", "blocks/run.py", "blocks/run_words.py", "blocks/run_setup.py",
      "blocks/run_notes.py", "blocks/spread.py", "blocks/sweep.py",
      "checks.py", "scenario_runner.py", "bench/report.py", "bench/gallery.py", "bench/pictures.py",
      "bench/compare.py", "launcher/report.py", "measure/cli.py", "measure/fit_launcher.py",
      "measure/fit_cylinder.py", "parts/partner.py", "assembler/server.py", "assembler/runs.py",
      "assembler/server_common.py", "assembler/http_handler.py",
      "assembler/bench_session.py", "assembler/bench_jobs.py",
      "assembler/bench_results.py", "assembler/measure_session.py", "assembler/edits.py", "mcp_server.py"]

UNITS = (r"(?:mm|cm|m|in|ft|kg|lbs|lbf|lb|N|psi|kPa|m/s|ft/s|N·m|N\*m|in·lbf|lbf/in|N/m|g/cm³|lb/in³|kg/m³|kg/m\^3"
         r"|g|oz)")
END = r"(?![\w/·³^*])"
PROSE = r"(?<! in(?= (?:the|a|an|all|front|line|order|place|it|its|this|that|which|your|my|one|3D)\b))"
AFTER_NUMBER = re.compile(r"[\d}]\s?" + UNITS + END + PROSE)   # "0.5 m", `${x} in`, "4 in of", not "3 in the test"
IN_BRACKETS = re.compile(r"\(" + UNITS + r"\)")               # "exit speed (m/s)"
JUST_A_UNIT = re.compile(r"^\s*" + UNITS + r"\s*$")         # "kg", and " m" in `fmt(v) + " m"`
# Python's keys and unit names ("in", "m") are everywhere: there only a unit after a space, " m" in `f"{x:.2f}" + " m"`
SPACED_UNIT = re.compile(r"^\s+" + UNITS + r"\s*$")
FORMAT_SPEC = re.compile(r"\.\d+[gfe]\b")                    # ".3g" is a format, not 3 grams

KEY = "a file's, a key's or the wire's own unit (U3, U4; signals keep fixed units), not text in the setting"
WORD = "the word in, not inches"
MACHINE = "machine text: written as it was before the setting (docs/settings.md)"
CHOOSES = "chooses or names the unit by the setting"

# (file, a piece of the hit's source line, why it is allowed)
ALLOWED = [
    # JavaScript
    ("bench_fields.js", 'const KINDS = { m: "length", "m/s": "speed", kg: "mass", N: "force", "N/m": "spring" }',
     "fields.toml's unit names, mapped to their kinds"),
    ("bench_fields.js", 'return def.unit === "m" && FIELD_KEYS', "fields.toml's unit names, mapped to their kinds"),
    ("bench_fields.js", 'const siOf = (vt, v) => each(v, (x) => (vt.inches ? units.fromKey(x, "in")', KEY),
    ("bench_fields.js", 'const fileOf = (vt, si) => each(si, (x) => round6(vt.inches ? units.toKey(x, "in")', KEY),
    ("bench_fields.js", 'const unitOf = (vt, def) => (vt.kind ? units.name(vt.kind) : vt.inches ? "in"',
     KEY + " (an _in key's form)"),
    ("bench_fields.js", 'def.unit === "degrees" || def.unit === "m"', "fields.toml's unit names"),
    ("bench_stage.js", 'swept[c].inches ? units.fromKey(v, "in")', KEY + " (a swept _in key's values)"),
    ("bench_edit.js", '["m", "mm", "in"]', "a CAD file's own units, chosen when it is imported"),
    ("bench_edit.js", '$("#bench-cad-units").value = "mm"', "a CAD file's own units, chosen when it is imported"),
    ("blocks_defs.js", 'j.unit === "in"', KEY),
    ("blocks_defs.js", 'e.unit === "in"', KEY),
    ("blocks_defs.js", 'drive: { AMOUNT: { kind: "field", unit: "m"', KEY),
    ("blocks_defs.js", 'cond_seated: { DEPTH: { kind: "length", unit: "in"', KEY),
    ("blocks_defs.js", 'cond_pressure: { PSI: { kind: "pressure", unit: "psi"', KEY),
    ("blocks_defs.js", 'cond_element: { VALUE: { kind: "length", unit: "in"', KEY),
    ("blocks_defs.js", 'cond_clears: { CLEAR: { kind: "length", unit: "in"', KEY),
    ("blocks_defs.js", 'cond_partner_lifted: { LIFT: { kind: "length", unit: "in"', KEY),
    ("blocks_defs.js", 'cond_partner_moved: { MOVED: { kind: "length", unit: "in"', KEY),
    ("blocks_defs.js", 'const lengthUnit = (system) => (system === "metric" ? "m" : "in")',
     KEY + " (a new move's unit, by the setting)"),
    ("blocks_defs.js", 'message0: t("%1 in %2")', WORD + " (a piece in a body)"),
    ("app.js", 'return u === "in" ? t("inches") : u === "mm" ? t("millimeters") : u;', CHOOSES + " (the unit's words)"),
    ("fieldpick.js", '{ step: 5 * 0.3048, size: 5, unit: "ft" } : { step: 1, size: 1, unit: "m" }',
     CHOOSES + " (the field's grid)"),
    ("inspector.js", 'const PARAM_KIND = { m: "length", kg: "mass", N: "force", "N*m": "torque", "N/m": "spring"',
     "stock params' unit names, mapped to their kinds"),
    ("measure.js", 'const SYSTEM_UNITS = { imperial: { length: "in", mass: "lb", force: "lb", weight: "lb" }',
     "a new measure column's file unit, by the setting"),
    ("measure.js", 'metric: { length: "mm", mass: "kg"', "a new measure column's file unit, by the setting"),
    ("part.js", 'units.name("length") === "in" ? 3 : 1', CHOOSES + " (decimals)"),
    ("part_bodies.js", 'const v = measure(text, "kg", "mass")', "an edit's key name, not shown"),
    ("part_bodies.js", 'h("span", { class: "unit", text: units.name("mass") })), "kg",',
     "an edit's key name, not shown"),
    ("part_motion.js", 'units.name("length") === "mm" ? 0.025 : 0.0254', CHOOSES + " (a round example)"),
    ("test_run.js", 't("{count} in", { count: c.in })', "pieces in (a count), not inches"),
    ("test_run_chart.js", 'if (name === "hook_load") return "N"', KEY),
    ("test_run_chart.js", 'if (name === "air.psi" || name === "air.working_psi") return "psi"', KEY),
    ("test_run_chart.js", 'if (name.endsWith(".extension")) return "in"', KEY),
    ("test_run_chart.js", 'if (name.startsWith("seated.") || name.startsWith("clearance.")) return "in"', KEY),
    ("test_run_chart.js", 'if (name === "partner.height" || name === "partner.moved") return "in"', KEY),
    ("test_run_chart.js", 'if (kind === "distance") return "in"', KEY),
    ("test_run_sweep.js", 'join(" in ")', "a block in a repeat, not inches"),
    # Python
    ("blocks/placed.py", 'on = f"{depth / IN:.1f} in" if units.is_machine()', MACHINE),
    ("blocks/placed.py", 'depth=f"{depth / IN:.1f} in", could=f"{could / IN:.1f} in"', MACHINE),
    ("blocks/run_notes.py", 'f"{e}: never within {NEVER_WITHIN_IN} in"', MACHINE),
    ("blocks/run_notes.py", "f\"closest to {e}: {c['min_in']:.2f} in at {c['t']:.1f} s\" for e, c in", MACHINE),
    ("blocks/run_notes.py", '"scored {count} in {goal}"', "balls scored in a goal, not inches"),
    ("blocks/run_notes.py", 'return f"{x:.0f} psi" if units.is_machine()', MACHINE),
    ("blocks/run.py", "far, went = f\"{abs(a['distance']):g} m\", f\"{gone:.2f} m\"", MACHINE),
    ("blocks/run.py", "didn't reach {far} in {timeout} s", "reach a distance in a time, not inches"),
    ("blocks/run_words.py", 'out=f"{out:.1f} in", stroke=f"{round(stroke, 1):g} in"', MACHINE),
    ("blocks/run_words.py", 'p = dict(limit=f"{psi:g} psi", now=f"{now:.1f} psi")', MACHINE),
    ("blocks/run_setup.py", 'by = f"{overlap.depth:.2f} m" if units.is_machine()', MACHINE),
    ("blocks/run_words.py", "now = f\"{c / IN:.2f} in\" if math.isfinite(c) else f\">{NEVER_WITHIN_IN} in\"", MACHINE),
    ("blocks/run_words.py", 'up = f"{up:g} in"', MACHINE),
    ("blocks/run_words.py", "moved = (f\"{self.world.partner.moved():.1f} in\" if units.is_machine()", MACHINE),
    ("blocks/run_words.py", 'reading=f"{r.distance / IN:.1f} in", threshold=f"{spec.threshold / IN:.1f} in"', MACHINE),
    ("blocks/run_words.py", 'shown = f"{depth / IN:.1f} in" if units.is_machine()', MACHINE),
    ("blocks/run_words.py", "far = f\"{a['distance']:g} m\" if units.is_machine()", MACHINE),
    ("blocks/spread.py", 'return f"{_n(x)} in"', MACHINE),
    ("checks.py", 'msg = (f"pose within {dist:.2f} m and', MACHINE + " (report.json, MCP run_scenario)"),
    ("checks.py", 'f"pose: {dist:.2f} m from target', MACHINE + " (report.json, MCP run_scenario)"),
    ("checks.py", 'label = f"odometry_error {self.when} <= {self.max:g} m"',
     "the check's name in report.json and the scenario file's own number (meters)"),
    ("checks.py", 'f"odometry {e:.3f} m from ground truth', MACHINE + " (report.json, MCP run_scenario)"),
    ("checks.py", 'f"odometry error peaked at {worst:.3f} m', MACHINE + " (report.json, MCP run_scenario)"),
    ("bench/pictures.py", 'return m, f"{m * 100:g} cm" if m < 1.0 else f"{m:g} m"', CHOOSES + " (the scale bar)"),
    ("bench/pictures.py", 'return n * units.IN, f"{n:g} in"', CHOOSES + " (the scale bar)"),
    ("bench/pictures.py", 'return n * units.FT, f"{n:g} ft"', CHOOSES + " (the scale bar)"),
    ("measure/fit_cylinder.py", '{psi} psi: check the bore',
     MACHINE + " (also a robot file's load error)"),
    ("parts/partner.py", "must be at least {min_size} m (its drive's",
     "checks the partner's file numbers (meters), named by key"),
    ("parts/partner.py", "mass must be more than its drive's {drive_mass} kg,",
     "checks the partner's file numbers (kg), named by key"),
    ("parts/partner.py", "(their outlines overlap by {depth} m)",
     "a robot config's [partner] start, in meters as written (machine text); the Test tab words Setup's own"),
    ("assembler/edits.py", "is off the field ({length} m by {width} m)",
     "checks a robot file's start pose, written in meters (the page's pickers keep it on the field)"),
]


def js_strings(text: str):
    """(line, text) of each string literal in JavaScript `text`, comments and regex literals skipped; a template's
    ${...} reads as "}"."""
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            j = text.index("*/", i)
            line += text.count("\n", i, j)
            i = j + 2
        elif c == "/" and re.search(r"(^|[(,=:\[!&|?{};+\-*%<>~^]|\breturn)\s*$", text[max(0, i - 40):i]):
            j, in_class = i + 1, False          # a regex literal: its quotes aren't strings
            while j < n and text[j] != "\n" and (text[j] != "/" or in_class):
                if text[j] == "\\":
                    j += 2
                    continue
                in_class = (in_class or text[j] == "[") and text[j] != "]"
                j += 1
            i = j + 1
        elif c in "\"'`":
            j, buf, start = i + 1, [], line
            while j < n and text[j] != c:
                if text[j] == "\\":
                    buf.append(text[j:j + 2])
                    j += 2
                elif c == "`" and text.startswith("${", j):
                    k, depth = j + 2, 1
                    while k < n and depth:
                        depth += {"{": 1, "}": -1}.get(text[k], 0)
                        k += 1
                    line += text.count("\n", j, k)
                    buf.append("}")
                    j = k
                else:
                    line += text[j] == "\n"
                    buf.append(text[j])
                    j += 1
            yield start, "".join(buf)
            i = j + 1
        else:
            i += 1


def py_strings(text: str):
    """(line, text, source) of each string literal in Python `text` but docstrings; an f-string's {...} reads as "}",
    and the source is the literal as written (all its lines, joined)."""
    tree = ast.parse(text)
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            skip.update(id(v) for v in node.values)
            yield (node.lineno, "".join(v.value if isinstance(v, ast.Constant) else "}" for v in node.values),
                   " ".join(ast.get_source_segment(text, node).split()))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value, " ".join(ast.get_source_segment(text, node).split())


def hits():
    """[(file, line number, source line (in Python, then the whole literal), what matched)] for every unit found."""
    found = []
    for path in sorted(STATIC.glob("*.js")):
        if path.name == "units.js":
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for ln, s in js_strings(text):
            for m in [*AFTER_NUMBER.finditer(s), *IN_BRACKETS.finditer(s), *JUST_A_UNIT.finditer(s)]:
                found.append((path.name, ln, lines[ln - 1], m.group(0)))
    for ln, line in enumerate((STATIC / "index.html").read_text(encoding="utf-8").splitlines(), 1):
        for m in [*AFTER_NUMBER.finditer(line), *IN_BRACKETS.finditer(line)]:
            found.append(("index.html", ln, line, m.group(0)))
    for name in PY:
        text = (ROOT / "scrimmage" / name).read_text(encoding="utf-8")
        lines = text.splitlines()
        for ln, s, source in py_strings(text):
            s = FORMAT_SPEC.sub("", s)
            for m in [*AFTER_NUMBER.finditer(s), *IN_BRACKETS.finditer(s), *SPACED_UNIT.finditer(s)]:   # line + literal
                found.append((name, ln, f"{lines[ln - 1]} ‖ {source}", m.group(0)))
    return found


def _allowed(hit) -> tuple | None:
    """The ALLOWED entry for `hit`: its piece is on the hit's line and holds the unit found, so a new unit added on
    an allowed line isn't allowed with it."""
    name, _, line, what = hit
    unit = re.sub(r"^[\d}(]\s?|\)$", "", what.strip())
    return next((a for a in ALLOWED if a[0] == name and a[1] in line and re.search(
        r"(?<![\w/·])" + re.escape(unit) + r"(?![\w/·³^*])", a[1])), None)


def test_no_hard_coded_units_are_left():
    new = [f"{name}:{ln}: {what!r} in: {line.strip()}" for name, ln, line, what in hits() if not _allowed(
        (name, ln, line, what))]
    assert not new, ("a unit written into the text; show it with units.fmt / units.js fmt and its kind (or, if it "
                     "must stay, add it to ALLOWED with why):\n" + "\n".join(new))


def test_every_allowed_entry_still_matches():
    used = {_allowed(h) for h in hits()}
    assert not [a for a in ALLOWED if a not in used]


def test_the_sweep_finds_a_hard_coded_unit():
    """The scanners themselves: what a display site that skipped units.js would look like is found."""
    js = ('const a = `x ${v} m`; // "4 in" in a comment\nconst b = "speed (m/s)", re = /"/, c = "kg";\n'
          'const d = v.toFixed(2) + " m", e = fmt(v) + " in", f = "pieces in the robot";\n')
    got = [m.group(0) for _, s in js_strings(js)
           for m in [*AFTER_NUMBER.finditer(s), *IN_BRACKETS.finditer(s), *JUST_A_UNIT.finditer(s)]]
    assert got == ["} m", "(m/s)", "kg", " m", " in"]
    py = ('def f(x):\n    """4 in, a docstring"""\n    return f"{x:.2f} in", "%.3g" % x, "in the robot", '
          'f"{x:.2f}" + " m", str(x) + " in", d["in"]\n')
    got = [m.group(0) for _, s, _ in py_strings(py)
           for m in [*AFTER_NUMBER.finditer(FORMAT_SPEC.sub("", s)), *SPACED_UNIT.finditer(s)]]
    assert got == ["} in", " m", " in"]
