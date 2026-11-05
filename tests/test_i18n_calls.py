"""Every t()/tn() call names exactly the placeholders its source has: a {name} in the source with no value for it is a
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


def test_every_python_call_gives_its_placeholders():
    assert _python_mismatches() == []


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
