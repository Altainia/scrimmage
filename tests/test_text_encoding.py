"""Every text file scrimmage reads or writes names its encoding. Windows' default is the locale's code page (cp1252
in the US), so an unnamed one crashes on the first non-ASCII character: a ❌ in a scenario report did, in Windows
CI (2026-11-14)."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [*sorted((ROOT / "scrimmage").rglob("*.py")), *sorted((ROOT / "lockstep" / "scrimmage_lockstep").rglob("*.py"))]


MODES = set("rwxa+tb")


def _mode(call: ast.Call, index: int, default: str | None) -> str | None:
    """The literal mode of an open-like call: its `index`th argument or mode=, else `default`; None when it isn't a
    literal file mode."""
    arg = call.args[index] if len(call.args) > index else next((k.value for k in call.keywords if k.arg == "mode"), None)
    if arg is None:
        return default
    ok = isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value and set(arg.value) <= MODES
    return arg.value if ok else None


def _text_mode(call: ast.Call) -> bool:
    """open(), io.open() and os.fdopen() take the mode second; Path.open() first (a bare .open() could be anything,
    so only one with a mode counts); the tempfile ones are binary unless given a mode."""
    f = call.func
    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
    if name == "open" and isinstance(f, ast.Attribute) and not (isinstance(f.value, ast.Name) and f.value.id == "io"):
        mode = _mode(call, 0, None)
    elif name in ("open", "fdopen"):
        mode = _mode(call, 1, "r")
    elif name in ("NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile"):
        mode = _mode(call, 0, None)
    else:
        return False
    return mode is not None and "b" not in mode


def _unnamed(path: Path, root: Path = ROOT) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or any(k.arg == "encoding" for k in node.keywords):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            found.append(f"{path.relative_to(root)}:{node.lineno} {f.attr}")
        elif _text_mode(node):
            found.append(f"{path.relative_to(root)}:{node.lineno} {ast.unparse(node.func)}")
    return found


def test_the_check_sees_each_kind_of_text_open(tmp_path):
    src = tmp_path / "x.py"
    src.write_text("open(p)\nopen(p, 'rb')\np.open('w')\np.open()\nos.fdopen(fd, 'w')\nos.fdopen(fd, 'wb')\n"
                   "tempfile.NamedTemporaryFile('w')\ntempfile.NamedTemporaryFile(mode='w')\n"
                   "tempfile.NamedTemporaryFile()\nwebbrowser.open(url)\nio.open(p)\nopen(p, encoding='utf-8')\n",
                   encoding="utf-8")
    hits = sorted(int(h.split(":")[1].split()[0]) for h in _unnamed(src, tmp_path))
    assert hits == [1, 3, 5, 7, 8, 11], hits


def test_every_text_file_read_or_write_names_its_encoding():
    found = [hit for p in SOURCES for hit in _unnamed(p)]
    assert not found, "add encoding=\"utf-8\" to:\n" + "\n".join(found)
