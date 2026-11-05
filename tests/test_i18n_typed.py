"""The gate for what a student types (sub-project 20b, Task 6d review): a translatable source (t(), tn(), N_(), a
data-i18n attribute) must not hold a TOML key, value or table a student types into a file, or a command they run. A
translator would translate it, and the student would then type something the loader refuses ("colour" for "color").
Those go in as {placeholders}: `kind = "{kind}"` or {kind_setting}, {range_table}, `{command}`.

The scan reads every source as `scrimmage i18n template` finds it, and flags a quoted token ("…" or `…`) or a
[table] / [[table]] header that isn't a single {placeholder}. ALLOWED lists the sources that keep one, each with why.
"""
import re

from scrimmage import i18n
from scrimmage.i18n_tool import scan

FIELD = re.compile(r"\{[A-Za-z_]\w*\}")
TABLE = re.compile(r"\[\[?([A-Za-z_<{][\w.<>{}\-]*)\]\]?")

# source (as the catalog keys it, without a context) -> why it may keep a quoted or bracketed token
ALLOWED = {
    # UI labels, quoted in the words: they are translated with the label they name, and nobody types them in a file
    'Plug a check into "wait until" (from Check).': "a block's label, translated with the block",
    'Plug a check into "repeat until" (from Check).': "a block's label, translated with the block",
    'Plug a check into "expect" (from Check).': "a block's label, translated with the block",
    "The parts go in the parts folder next to {robot}. When you're done, click \"Back to the robot\".":
        "a button's label, translated with the button",
    # N_() constants pinned as the shown text by tests that predate this gate (cad, real DS): a placeholder there
    # needs those tests' constants to change
    'Reading CAD files needs the cad extra: pip install "scrimmage[cad]"':
        "MISSING_EXTRA: tests compare the shown message to the constant (test_cad_mass, test_cad_source, test_part_server)",
    "the config's [driver_station] real = true is ignored here: in a scenario run, scrimmage is the Driver Station":
        "CONFIG_REAL_DS_NOTE: test_real_ds compares the run's note to the constant",
}
# Stock part and bench docs: N_() data shown through a generic translated(p.doc), written as documentation of
# the part's own keys and slots (many identifiers, not only these tokens); the translating guide (6b) says to keep
# what is in quotes and brackets. Listed by the start of the source.
ALLOWED_PREFIXES = {
    "Slots: ": "a stock part's slots doc (N_() data, shown with translated())",
    "a cylinder measurement file (": "a stock part's parameter doc (N_() data)",
    '"<part>.<body>" the rod': "a stock part's parameter doc (N_() data)",
    "hooded shooter: ": "a stock part's doc (N_() data)",
    "planar disc shooter: ": "a stock part's doc (N_() data)",
    "The Part tab opens on it. When its parts are up to date, click \"Test it\"": "a button's label, translated "
                                                                                  "with the button",
    "Robot files say \"in the CAD folder\" or \"in the code folder\"": "the folder picker's labels, translated with them",
    "Exit numbers are for each file's first outlet. \"Worst shaft drop\"": "the gallery table's column names, "
                                                                           "translated with them",
    "measure the ball's stiffness (a squeeze test)": "the bench report's UNCALIBRATED words name a docs heading "
                                                      "(\"Measure these first\"), translated with the docs",
}


def _tokens(text: str) -> list[str]:
    """The quoted, backticked and bracketed tokens of `text` that aren't one {placeholder}, quotes paired in order."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if text.startswith("{{", i) or text.startswith("}}", i):
            i += 2
            continue
        if c in '"`':
            j = text.find(c, i + 1)
            if j < 0:
                break
            if not FIELD.fullmatch(text[i + 1:j]):
                out.append(text[i:j + 1])
            i = j + 1
            continue
        if c == "[":
            m = TABLE.match(text, i)
            if m and not FIELD.fullmatch(m.group(1)):
                out.append(m.group(0))
                i = m.end()
                continue
        i += 1
    return out


def _flagged() -> dict[str, tuple[list[str], str]]:
    sources, _ = scan()
    out = {}
    for s in sources:
        key = s.key.split(i18n.CONTEXT)[-1]
        for text in key.split(i18n.PLURAL) if s.plural else [key]:
            found = _tokens(text)
            if found:
                out[key] = (found, s.where[0])
    return out


def _allowed(key: str) -> bool:
    return key in ALLOWED or any(key.startswith(p) for p in ALLOWED_PREFIXES)


def test_no_source_holds_what_a_student_types():
    bad = {k: v for k, v in _flagged().items() if not _allowed(k)}
    assert not bad, "\n".join(f"{where}: {found} in {key!r}" for key, (found, where) in bad.items())


def test_every_allowed_entry_is_still_needed():
    flagged = _flagged()
    stale = [k for k in ALLOWED if k not in flagged]
    stale += [p for p in ALLOWED_PREFIXES if not any(k.startswith(p) for k in flagged)]
    assert not stale, stale


def test_the_scan_finds_the_tokens_it_should():
    assert _tokens('needs kind = "beam", "distance" or "color"') == ['"beam"', '"distance"', '"color"']
    assert _tokens('kind must be "{ball}" or "{disc}", not {kind}') == []
    assert _tokens("{where}: [shooter] must be a table; see [[goal]] and [{section}]") == ["[shooter]", "[[goal]]"]
    assert _tokens("run `scrimmage java build`, then `{command}`") == ["`scrimmage java build`"]
    assert _tokens("give [x, y, z] or {{ sides, across_flats }}") == []
