"""`scrimmage i18n template` and `scrimmage i18n check`: the source strings the code wraps, and each catalog against them.

The sources are found by reading the code, not by running it: every t()/tn() call whose function came from i18n
(Python: `from .i18n import t`, `i18n.t(...)`; JavaScript: `import { t } from "./i18n.js"`, `i18n.t(...)`) must
pass literal strings first. An HTML page's static text is its data-i18n, data-i18n-title, -placeholder and
-aria-label attributes (with data-i18n-context), which static/i18n.js fills in. A variable, an f-string, a template with ${...} or text joined with + is an error here,
since a translator could never see the whole sentence; its dynamic parts are {placeholders} instead.

    template   lists each source with every file:line using it; --out FILE also writes a starter catalog, every
               entry "" (untranslated), to fill in as a new <lang>.json
    check      for each catalog (or the one named): entries that are broken (they show in English, L6), whose
               placeholders don't match the source's, that no source uses any more, and sources it doesn't
               translate. It exits 1 for a broken or mismatched entry or a non-literal call, else 0: missing and
               unused entries are only listed, since a catalog holds just what differs from American English.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import i18n

PACKAGE = Path(__file__).resolve().parent
SKIP_DIRS = {"vendor", "node_modules", "__pycache__"}
SKIP_FILES = {"i18n.py", "i18n_tool.py", "i18n.js"}      # the calls' own definitions


@dataclass
class Source:
    key: str                 # the catalog key: "<singular>|<plural>" for tn, "<context>\u0004..." with a context
    plural: bool
    where: list[str] = field(default_factory=list)      # "pkg/mod.py:12", in the order found


@dataclass
class Report:
    lang: str
    name: str
    entries: int
    broken: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    unused: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


# -- finding the sources -----------------------------------------------------------------------------------------
def scan(root: Path = PACKAGE) -> tuple[list[Source], list[str]]:
    """Every wrapped source under `root` (its .py and .js files), and the calls that don't pass literals."""
    found: dict[str, Source] = {}
    errors: list[str] = []
    base = root.resolve().parent
    files = sorted(p for p in root.rglob("*") if p.suffix in (".py", ".js", ".html") and p.name not in SKIP_FILES
                   and not SKIP_DIRS & set(p.relative_to(root).parts[:-1]))
    for path in files:
        shown = path.resolve().relative_to(base).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            calls = {".py": _python_calls, ".js": _js_calls, ".html": _html_calls}[path.suffix](text)
        except SyntaxError as e:     # a file being edited: say so, and scan the rest
            errors.append(f"{shown}:{e.lineno or 1}: isn't valid Python ({e.msg})")
            continue
        for line, kind, args, context, error in calls:
            where = f"{shown}:{line}"
            if error:
                errors.append(f"{where}: {kind}() needs {'a literal string' if kind == 't' else 'literal strings'} "
                              f"first, not {error}")
                continue
            if kind == "tn" and i18n.PLURAL in args[0]:
                errors.append(f"{where}: tn()'s singular can't contain {i18n.PLURAL!r} (it joins the plural's key)")
                continue
            key = i18n.PLURAL.join(args)
            key = f"{context}{i18n.CONTEXT}{key}" if context else key
            found.setdefault(key, Source(key, kind == "tn")).where.append(where)
    return list(found.values()), errors


def _python_calls(text: str):
    """(line, "t" | "tn", [literal args], context, error) for each i18n call in a Python module."""
    tree = ast.parse(text)
    funcs: dict[str, str] = {}      # local name -> t | tn
    modules: set[str] = set()       # local (dotted) names of the i18n module: i18n, loc, scrimmage.i18n
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[-1] == "i18n":
                funcs.update({a.asname or a.name: a.name for a in node.names if a.name in ("t", "tn", "N_")})
            else:
                modules.update(a.asname or a.name for a in node.names if a.name == "i18n")
        elif isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name.split(".")[-1] == "i18n")
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in funcs:
            kind = funcs[f.id]
        elif isinstance(f, ast.Attribute) and f.attr in ("t", "tn", "N_") and _dotted(f.value) in modules:
            kind = f.attr
        else:
            continue
        if kind == "N_":     # data marked where it is defined: a plain source, as t()'s
            kind = "t"
        want = 1 if kind == "t" else 2
        args, error = [], None
        for a in node.args[:want]:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                args.append(a.value)
            else:
                error = error or ("an f-string" if isinstance(a, ast.JoinedStr) else ast.unparse(a))
        if len(node.args) < want and not error:
            error = "nothing"
        context = None
        for kw in node.keywords:
            if kw.arg == "context":
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    context = kw.value.value
                else:
                    error = error or f"a context of {ast.unparse(kw.value)}"
        calls.append((node.lineno, kind, args, context, error))
    return sorted(calls, key=lambda c: c[0])


def _dotted(node: ast.expr) -> str | None:
    """"scrimmage.i18n" for the expression scrimmage.i18n (a name or a chain of attributes), else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


HTML_ATTRS = ("data-i18n", "data-i18n-title", "data-i18n-placeholder", "data-i18n-aria-label")


class _HtmlSources(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.calls = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for name in HTML_ATTRS:
            if name in a:
                value = a[name]
                error = None if value else "an empty attribute"
                self.calls.append((self.getpos()[0], "t", [value] if value else [], a.get("data-i18n-context"), error))

    handle_startendtag = handle_starttag


def _html_calls(text: str):
    """(line, "t", [source], context, error) for each data-i18n* attribute in an HTML page."""
    p = _HtmlSources()
    p.feed(text)
    p.close()
    return p.calls


# JavaScript has no parser here: the code is masked (comments and the insides of strings and regular expressions
# blanked, a template's ${...} kept as code, every position and newline unchanged), the calls are found in the mask,
# and their literal arguments are read from the text.
_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}
_REGEX_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "yield",
                "await"}
_IMPORT = re.compile(r"""\bimport\s+([^;"']*?)\s+from\s+["'][^"']*\bi18n\.js["']""", re.S)


def _mask_js(src: str) -> str:
    out = list(src)
    n = len(src)
    stack: list[int] = []      # one brace depth per open ${ ... }
    i = 0

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "

    def template(i: int) -> int:
        """From inside a template at i: the index after its closing backtick, or after a "${" (pushed)."""
        start = i
        while i < n:
            c = src[i]
            if c == "\\":
                i += 2
            elif c == "`":
                blank(start, i)
                return i + 1
            elif src.startswith("${", i):
                blank(start, i)
                stack.append(0)
                return i + 2
            else:
                i += 1
        blank(start, n)
        return n

    def prev_token(i: int) -> str:
        j = i - 1
        while j >= 0 and out[j].isspace():
            j -= 1
        if j < 0:
            return ""
        if out[j].isalnum() or out[j] in "_$":
            k = j
            while k >= 0 and (out[k].isalnum() or out[k] in "_$"):
                k -= 1
            return "".join(out[k + 1:j + 1])
        return out[j]

    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            blank(i + 1, min(j, n))
            i = j + 1
        elif c == "`":
            i = template(i + 1)
        elif c == "/" and (prev_token(i) in _REGEX_AFTER or prev_token(i) in _REGEX_WORDS):
            j, in_class = i + 1, False
            while j < n and src[j] != "\n" and (in_class or src[j] != "/"):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                j += 1
            blank(i + 1, min(j, n))
            i = j + 1
        elif c == "{" and stack:
            stack[-1] += 1
            i += 1
        elif c == "}" and stack:
            if stack[-1] == 0:
                stack.pop()
                i = template(i + 1)
            else:
                stack[-1] -= 1
                i += 1
        else:
            i += 1
    return "".join(out)


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _js_string(src: str, i: int) -> tuple[str | None, int, str | None]:
    """The literal string starting at i: (its value, the index after it, None), or (None, i, why it isn't one)."""
    q = src[i] if i < len(src) else ""
    if q not in ("\"", "'", "`"):
        return None, i, None
    out, j = [], i + 1
    while j < len(src):
        c = src[j]
        if c == "\\":
            e = src[j + 1] if j + 1 < len(src) else ""
            if e == "u" and src.startswith("{", j + 2):
                end = src.index("}", j + 2)
                out.append(chr(int(src[j + 3:end], 16)))
                j = end + 1
            elif e == "u":
                out.append(chr(int(src[j + 2:j + 6], 16)))
                j += 6
            elif e == "x":
                out.append(chr(int(src[j + 2:j + 4], 16)))
                j += 4
            elif e == "\n":
                j += 2
            else:
                out.append(_ESCAPES.get(e, e))
                j += 2
        elif c == q:
            return "".join(out), j + 1, None
        elif q == "`" and src.startswith("${", j):
            return None, i, "a template with ${...}"
        elif c == "\n" and q != "`":
            break
        else:
            out.append(c)
            j += 1
    return None, i, "an unfinished string"


def _skip(mask: str, i: int) -> int:
    while i < len(mask) and mask[i].isspace():
        i += 1
    return i


def _close(mask: str, i: int) -> int:
    """The index of the bracket closing the one at i - 1 ("(" just before i)."""
    depth = 1
    while i < len(mask):
        if mask[i] in "([{":
            depth += 1
        elif mask[i] in ")]}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(mask)


def _js_calls(src: str):
    funcs: dict[str, str] = {}
    objects: set[str] = set()
    for m in _IMPORT.finditer(src):
        clause = m.group(1)
        braces = re.search(r"\{([^}]*)\}", clause)
        for part in (braces.group(1).split(",") if braces else []):
            name, _, alias = (w.strip() for w in part.partition(" as "))
            if name in ("t", "tn"):
                funcs[alias or name] = name
            elif name == "i18n":
                objects.add(alias or name)
        rest = re.sub(r"\{[^}]*\}", "", clause)
        star = re.search(r"\*\s*as\s+([\w$]+)", rest)
        if star:
            objects.add(star.group(1))
        default = re.match(r"\s*([\w$]+)", rest)
        if default:
            objects.add(default.group(1))
    if not funcs and not objects:
        return []
    mask = _mask_js(src)
    names = "|".join(re.escape(n) for n in funcs)
    objs = "|".join(re.escape(n) for n in objects)
    pattern = "|".join(p for p in (f"(?P<f>{names})" if names else "",
                                   rf"(?:{objs})\s*\.\s*(?P<m>tn?)" if objs else "") if p)
    calls = []
    for m in re.finditer(rf"(?<![\w$.])(?:{pattern})\s*\(", mask):
        if re.search(r"\bfunction\s*$", mask[max(0, m.start() - 30):m.start()]):
            continue
        kind = funcs[m.group("f")] if m.groupdict().get("f") else m.group("m")
        line = src.count("\n", 0, m.start()) + 1
        i, args, error = m.end(), [], None
        for k in range(1 if kind == "t" else 2):
            i = _skip(mask, i)
            value, end, why = _js_string(src, i)
            if value is None:
                error = why or ("nothing" if mask[i:i + 1] == ")" else src[i:].split(",")[0].split(")")[0].strip())
                break
            after = _skip(mask, end)
            if mask[after:after + 1] not in (",", ")"):
                error = f"one joined with {mask[after]}" if mask[after:after + 1] == "+" else "a string with more after it"
                break
            args.append(value)
            i = after + 1
        context = None
        if not error:
            body = slice(m.end(), _close(mask, m.end()))
            c = re.search(r"(?<![\w$])context\s*:\s*", mask[body])
            if c:
                at = body.start + c.end()
                context, _, why = _js_string(src, at)
                if context is None:
                    error = f"a context of {src[at:].split('}')[0].split(',')[0].strip()}"
        calls.append((line, kind, args, context, error))
    return calls


# -- the catalogs against them ------------------------------------------------------------------------------------
def starter(sources: list[Source]) -> dict:
    """A new catalog: every source, untranslated."""
    out: dict = {"_language": ""}
    for s in sources:
        out[s.key] = {form: "" for form in i18n.plural_categories(i18n.SOURCE)} if s.plural else ""
    return out


def check(lang: str, sources: list[Source], locales: Path | None = None) -> Report:
    locales = locales or i18n.LOCALES
    catalog, problem = i18n.read_catalog(locales / f"{lang}.json")
    name = catalog.get("_language") if isinstance(catalog.get("_language"), str) else ""
    entries = {k: v for k, v in catalog.items() if not k.startswith("_")}
    report = Report(lang, name or lang, len(entries))
    if problem:
        report.broken.append(problem)
    by_key = {s.key: s for s in sources}
    for key, entry in entries.items():
        s = by_key.get(key)
        if s is None:
            report.unused.append(key)
            continue
        if entry == "" or (isinstance(entry, dict) and all(v == "" for v in entry.values())):
            report.missing.append(key)
            continue
        why = i18n.entry_problem(key, entry, lang, s.plural)
        if why:
            report.broken.append(f"{key}: {why}")
            continue
        source = key.rpartition(i18n.CONTEXT)[2]
        if s.plural:
            singular, _, plural = source.partition(i18n.PLURAL)
            want = (i18n.names(singular) | i18n.names(plural)) - {"count"}
            texts = [entry[f] for f in i18n.plural_categories(lang)]
        else:
            want, texts = i18n.names(source), [entry]
        dropped = want - set().union(*(i18n.names(x) for x in texts))
        if dropped:
            report.mismatched.append(f"{key}: the translation drops {i18n._listed(dropped)}")
    report.missing += [s.key for s in sources if s.key not in entries]
    return report


def _print_report(r: Report) -> None:
    print(f"{r.lang} ({r.name}): {r.entries} entries")
    for title, items in (("broken (shown in English)", r.broken), ("placeholders don't match", r.mismatched),
                         ("unused (no source has this text)", r.unused),
                         ("missing (shown in American English)", r.missing)):
        if items:
            print(f"  {title}: {len(items)}")
            for item in items:
                print(f"    {json.dumps(item, ensure_ascii=False)}")


def main(args) -> int:
    root = Path(args.root) if args.root else PACKAGE
    sources, errors = scan(root)
    for e in errors:
        print(f"scrimmage: {e}", file=sys.stderr)
    if args.i18n_command == "template":
        for s in sources:
            for where in s.where:
                print(f"{where}: {json.dumps(s.key, ensure_ascii=False)}")
        if args.out:
            Path(args.out).write_text(json.dumps(starter(sources), ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
            print(f"wrote {len(sources)} sources to {args.out}")
        return 1 if errors else 0
    locales = Path(args.locales) if args.locales else i18n.LOCALES
    known = sorted(p.stem for p in locales.glob("*.json") if i18n.TAG.match(p.stem) and p.stem != i18n.SOURCE)
    if args.lang and args.lang not in known:
        print(f"scrimmage: there's no {args.lang}.json in {locales} (the catalogs: {', '.join(known) or 'none'})",
              file=sys.stderr)
        return 2
    failed = bool(errors)
    for lang in [args.lang] if args.lang else known:
        r = check(lang, sources, locales)
        _print_report(r)
        failed = failed or bool(r.broken or r.mismatched)
    return 1 if failed else 0
