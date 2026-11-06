"""Every t()/tn() call (and translated() of a literal or an N_() constant, and a stock part's keyed doc) names exactly
the placeholders its source has: a {name} in the source with no value for it is a
KeyError when the text is shown (a 500 instead of a refusal), and a value with no {name} is a word lost from the
sentence. Read from the code, as `scrimmage i18n template` reads it: Python by its AST, the web app's JavaScript by
i18n_tool's mask (a call whose params aren't an object literal, or spread one in, isn't checked)."""
import ast
import re
from pathlib import Path

from scrimmage import i18n_tool

ROOT = Path(__file__).resolve().parent.parent / "scrimmage"
FIELD = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
NOT_PARAMS = {"lang", "context"}


def _names(*sources: str) -> set[str]:
    return {m for s in sources for m in FIELD.findall(s) if m}


def _python_mismatches() -> list[str]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if name not in ("t", "tn"):
                continue
            want = 1 if name == "t" else 2
            sources = [a.value for a in node.args[:want] if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if len(sources) != want or any(kw.arg is None for kw in node.keywords):     # not literal, or **params
                continue
            given = {kw.arg for kw in node.keywords} - NOT_PARAMS
            need = _names(*sources)
            if name == "tn":
                given |= {"count"}
                need |= {"count"}
            if need != given:
                out.append(f"{path.relative_to(ROOT)}:{node.lineno}: {sources[0][:70]!r} needs {sorted(need)}, "
                           f"given {sorted(given)}")
    return out


def _object_keys(mask: str, start: int, end: int) -> set[str] | None:
    """The keys of the object literal mask[start:end] (inside its braces); None when it spreads another in."""
    keys, depth, part = set(), 0, ""
    for c in mask[start:end] + ",":
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            part = part.strip()
            if part.startswith("..."):
                return None
            if part:
                keys.add(re.match(r"[\"']?([\w$]+)", part).group(1))
            part = ""
        else:
            part += c
    return keys


def _js_mismatches() -> list[str]:
    out = []
    for path in sorted((ROOT / "assembler" / "static").glob("*.js")):
        src = path.read_text(encoding="utf-8")
        funcs = {}
        for m in i18n_tool._IMPORT.finditer(src):
            braces = re.search(r"\{([^}]*)\}", m.group(1))
            for part in (braces.group(1).split(",") if braces else []):
                name, _, alias = (w.strip() for w in part.partition(" as "))
                if name in ("t", "tn"):
                    funcs[alias or name] = name
        if not funcs:
            continue
        mask = i18n_tool._mask_js(src)
        names = "|".join(re.escape(n) for n in funcs)
        for m in re.finditer(rf"(?<![\w$.])(?:{names}|i18n\s*\.\s*(?:tn?))\s*\(", mask):
            word = re.sub(r"[\s(]", "", mask[m.start():m.end()]).replace("i18n.", "")
            kind = funcs.get(word, word)
            i, sources = m.end(), []
            for _ in range(1 if kind == "t" else 2):
                i = i18n_tool._skip(mask, i)
                value, end, _why = i18n_tool._js_string(src, i)
                if value is None:
                    break
                sources.append(value)
                i = i18n_tool._skip(mask, end) + 1        # past its "," or ")"
            if len(sources) != (1 if kind == "t" else 2):
                continue
            if kind == "tn":            # the count, then the params
                depth, j = 0, i
                while j < len(mask) and not (depth == 0 and mask[j] in ",)"):
                    depth += mask[j] in "([{"
                    depth -= mask[j] in ")]}"
                    j += 1
                if mask[j:j + 1] != ",":
                    given, i = set(), None
                else:
                    i = j + 1
            if i is None:
                keys = set()
            else:
                i = i18n_tool._skip(mask, i)
                if mask[i - 1:i] == ")" or mask[i:i + 1] == ")":
                    keys = set()
                elif mask[i:i + 1] == "{":
                    keys = _object_keys(mask, i + 1, i18n_tool._close(mask, i + 1))
                    if keys is None:
                        continue
                else:
                    continue                # params given some other way: not checked
            given = keys - NOT_PARAMS
            need = _names(*sources)
            if kind == "tn":
                given |= {"count"}
                need |= {"count"}
            if need != given:
                line = src.count("\n", 0, m.start()) + 1
                out.append(f"{path.name}:{line}: {sources[0][:70]!r} needs {sorted(need)}, given {sorted(given)}")
    return out


def _literal_keys(node) -> set[str] | None:
    """The names a literal gives as params: a dict's string keys, or the first items of a tuple of (name, value)
    pairs (a stock part's doc_keys); None for anything else."""
    if isinstance(node, ast.Dict) and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in node.keys):
        return {k.value for k in node.keys}
    if isinstance(node, ast.Tuple) and all(isinstance(e, ast.Tuple) and e.elts and isinstance(e.elts[0], ast.Constant)
                                           for e in node.elts):
        return {e.elts[0].value for e in node.elts}
    return None


# The N_() fields filled from a keys field beside them: (the call's name, the text's position or keyword, the keys)
KEYED_FIELDS = (("Param", 3, "doc", "doc_keys"), ("Stock", 1, "doc", "doc_keys"), ("Stock", None, "note", "note_keys"),
                ("Test", None, "example_note", "example_keys"))


def _translated_mismatches() -> list[str]:
    """translated(source, **params) must fill the placeholders of its source: a literal, or a module-level N_()
    constant of the same module (a ** of a module-level dict literal counts its keys). An N_() text filled from a
    keys field (a stock part's doc and doc_keys) must have exactly those keys. Without params, translated() of a
    constant with placeholders would show the {braces}."""
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts, dicts = {}, {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                v = node.value
                if isinstance(v, ast.Call) and getattr(v.func, "id", None) == "N_" and v.args and \
                        isinstance(v.args[0], ast.Constant):
                    consts[node.targets[0].id] = v.args[0].value
                elif _literal_keys(v) is not None and isinstance(v, ast.Dict):
                    dicts[node.targets[0].id] = _literal_keys(v)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            where = f"{path.relative_to(ROOT)}:{node.lineno}"
            if name in ("translated", "_tr") and node.args:
                a = node.args[0]
                source = a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else \
                    consts.get(a.id) if isinstance(a, ast.Name) else None
                if source is None:
                    continue
                given = set()
                for kw in node.keywords:
                    if kw.arg is None:
                        keys = dicts.get(kw.value.id) if isinstance(kw.value, ast.Name) else None
                        if keys is None:
                            given = None
                            break
                        given |= keys
                    elif kw.arg not in NOT_PARAMS:
                        given.add(kw.arg)
                if given is not None and _names(source) != given:
                    out.append(f"{where}: {source[:70]!r} needs {sorted(_names(source))}, given {sorted(given)}")
            for call, pos, field, keys_field in KEYED_FIELDS:
                if name != call:
                    continue
                kws = {kw.arg: kw.value for kw in node.keywords}
                text = kws.get(field) if pos is None or len(node.args) <= pos else node.args[pos]
                if not (isinstance(text, ast.Call) and getattr(text.func, "id", None) == "N_" and text.args):
                    continue
                source = text.args[0].value
                given = _literal_keys(kws[keys_field]) if keys_field in kws else set()
                if given is not None and _names(source) != given:
                    out.append(f"{where}: {call} {field} {source[:60]!r} needs {sorted(_names(source))}, "
                               f"{keys_field} gives {sorted(given)}")
    return out


def test_every_python_call_gives_its_placeholders():
    assert _python_mismatches() == []


def test_every_translated_call_and_keyed_doc_gives_its_placeholders():
    assert _translated_mismatches() == []


def test_every_javascript_call_gives_its_placeholders():
    assert _js_mismatches() == []


def test_the_checks_find_a_mismatch(tmp_path, monkeypatch):
    (tmp_path / "assembler" / "static").mkdir(parents=True)
    (tmp_path / "m.py").write_text('from scrimmage import i18n\ni18n.t("in {folder}: {value}", folder=1, why=2)\n'
                                   'i18n.tn("{count} part", "{count} parts", 2)\n')
    (tmp_path / "assembler" / "static" / "a.js").write_text(
        'import { t, tn } from "./i18n.js";\nt("in {folder}", { folder, why });\nt("ok {a}", { a: f(1, 2) });\n'
        'tn("{count} run", "{count} runs", n);\nt("{x}", p);\n')
    monkeypatch.setattr(__import__(__name__), "ROOT", tmp_path)
    assert len(_python_mismatches()) == 1 and "m.py:2" in _python_mismatches()[0]
    assert len(_js_mismatches()) == 1 and "a.js:2" in _js_mismatches()[0]
    (tmp_path / "m.py").write_text('from scrimmage.i18n import N_, translated\nNOTE = N_("set {table}")\n'
                                   'KEYS = {"table": "[x]"}\ntranslated(NOTE)\ntranslated(NOTE, **KEYS)\n'
                                   'translated("{a} b", a=1, c=2)\n'
                                   'Param("n", "", 0, N_("see {form}"), doc_keys=(("form", "x"),))\n'
                                   'Param("n", "", 0, N_("see {form}"))\n')
    found = _translated_mismatches()
    assert [f.split(":")[1] for f in found] == ["4", "6", "8"], found
