"""Every text file scrimmage reads or writes names its encoding. Windows' default is the locale's code page (cp1252
in the US), so an unnamed one crashes on the first non-ASCII character: a ❌ in a scenario report did, in Windows
CI (2026-11-14)."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [*sorted((ROOT / "scrimmage").rglob("*.py")), *sorted((ROOT / "lockstep" / "scrimmage_lockstep").rglob("*.py"))]


def _mode(call: ast.Call) -> str | None:
    """The literal mode of an open() call ("r" when not given), None when it isn't a literal."""
    arg = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
    if arg is None:
        return "r"
    return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None


def _unnamed(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or any(k.arg == "encoding" for k in node.keywords):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            found.append(f"{path.relative_to(ROOT)}:{node.lineno} {f.attr}")
        elif isinstance(f, ast.Name) and f.id == "open":
            mode = _mode(node)
            if mode is not None and "b" not in mode:
                found.append(f"{path.relative_to(ROOT)}:{node.lineno} open")
    return found


def test_every_text_file_read_or_write_names_its_encoding():
    found = [hit for p in SOURCES for hit in _unnamed(p)]
    assert not found, "add encoding=\"utf-8\" to:\n" + "\n".join(found)
