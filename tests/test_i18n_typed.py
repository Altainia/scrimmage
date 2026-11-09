"""The gate for what a student types (sub-project 20b, Task 6d review): a translatable source (t(), tn(), N_(), a
data-i18n attribute) must not hold a TOML key, value or table a student types into a file, or a command they run. A
translator would translate it, and the student would then type something the loader refuses ("colour" for "color").
Those go in as {placeholders}: `kind = "{kind}"` or {kind_setting}, {range_table}, `{command}`.

The scan reads every source as `scrimmage i18n template` finds it, and flags a quoted token ("…" or `…`) or a
[table] / [[table]] header that isn't a single {placeholder}, a key = value (or key=value) setting with its key
written out, and a {{literal brace}} group. ALLOWED lists, per file, the tokens a source may keep, each with why.
"""
from i18n_helpers import tokens as _tokens
from scrimmage import i18n
from scrimmage.i18n_tool import scan

# (file, token) -> why that source may keep that token. Each entry is one token in one file's source, exactly; only UI
# labels and headings (translated with what they name) and prose formulas (words around "=", translated as words).
LABEL = "a UI label quoted in the words, translated with the label it names; nobody types it into a file"
FORMULA = "prose: a formula or a form described in words around \"=\", translated as words, never typed as written"
ALLOWED = {
    ("scrimmage/assembler/static/blocks_defs.js", '"wait until"'): LABEL + " (the block)",
    ("scrimmage/assembler/static/blocks_defs.js", '"repeat until"'): LABEL + " (the block)",
    ("scrimmage/assembler/static/blocks_defs.js", '"expect"'): LABEL + " (the block)",
    ("scrimmage/assembler/static/start.js", '"Back to the robot"'): LABEL + " (the button)",
    ("scrimmage/assembler/static/start.js", '"Test it"'): LABEL + " (the button)",
    ("scrimmage/assembler/static/start.js", '"in the CAD folder"'): LABEL + " (the folder picker's)",
    ("scrimmage/assembler/static/start.js", '"in the code folder"'): LABEL + " (the folder picker's)",
    ("scrimmage/bench/gallery.py", '"Worst shaft drop"'): LABEL + " (the gallery table's column)",
    ("scrimmage/bench/gallery.py", '"hardest hit"'): LABEL + " (a headline's words)",
    ("scrimmage/bench/report.py", '"Measure these first"'): "a heading of docs/launcher.md, translated with the docs",
    ("scrimmage/assembler/static/app.js", "token=…"): "what the printed link carries (#token=…), shown, never typed",
    ("scrimmage/assembler/static/index.html", "direction = heading"): FORMULA,
    ("scrimmage/bench/spec_shapes.py", "name = shaft"): FORMULA + " (slot name = shaft name)",
    ("scrimmage/testrange/elements.py", "name = points"): FORMULA + " (level name = points)",
    ("scrimmage/disc/inputs.py", "fence_radius = R_wheel"): FORMULA + " (fence_radius = R_wheel + D - compression)",
    ("scrimmage/parts/model.py", "b = ratio"): FORMULA + " (b = ratio * a)",
    ("scrimmage/measure/forms.py", "Spread = (highest"): FORMULA + " (Spread = (highest − lowest) ÷ average)",
    ("scrimmage/parts/stock/mechanisms.py", "height = spool"): FORMULA + " (a stock part's doc)",
    ("scrimmage/parts/stock/mechanisms.py", "angle = hood_range[0]"): FORMULA + " (exit angle = hood_range[0] + …)",
    ("scrimmage/parts/stock/mechanisms.py", "angle = tilt_range[0]"): FORMULA + " (launch angle = tilt_range[0] + …)",
}




def _flagged() -> list[tuple[str, str, str, str]]:
    """(file, line, token, source) for each token a source keeps."""
    sources, _ = scan()
    out = []
    for s in sources:
        key = s.key.split(i18n.CONTEXT)[-1]
        for text in key.split(i18n.PLURAL) if s.plural else [key]:
            for token in _tokens(text):
                where = s.where[0]
                out.append((where.rsplit(":", 1)[0], where, token, key))
    return out


def test_no_source_holds_what_a_student_types():
    bad = [f for f in _flagged() if (f[0], f[2]) not in ALLOWED]
    assert not bad, "\n".join(f"{where}: {token} in {key!r}" for _, where, token, key in bad)


def test_every_allowed_entry_is_still_needed():
    found = {(f[0], f[2]) for f in _flagged()}
    assert not [k for k in ALLOWED if k not in found]


def test_the_scan_finds_the_tokens_it_should():
    assert _tokens('needs kind = "beam", "distance" or "color"') == ['kind = "beam"', '"distance"', '"color"']
    assert _tokens('kind must be "{ball}" or "{disc}", not {kind}') == []
    assert _tokens("{where}: [shooter] must be a table; see [[goal]] and [{section}]") == ["[shooter]", "[[goal]]"]
    assert _tokens("run `scrimmage java build`, then `{command}`") == ["`scrimmage java build`"]
    assert _tokens("give [x, y, z] or {sides_form}") == []
    assert _tokens("points into the team's CAD folder ({{cad}})") == ["{{cad}}"]
    assert _tokens("don't start the session with nt=False; or set limits = true") == ["nt=False", "limits = true"]
    assert _tokens("{key} = {value}: expected {form}") == []
    assert _tokens("{name} was {value} at t={t} (x={x}, heading={heading}°, t={start}–{end} s)") == []
