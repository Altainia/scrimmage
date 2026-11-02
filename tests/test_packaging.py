"""pyproject.toml lists every subpackage by hand; a new folder with an __init__.py must be added to it, or an
installed (not editable) scrimmage would be missing it."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_package_folder_is_listed_in_pyproject():
    listed = set(tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["packages"])
    found = {".".join(p.parent.relative_to(ROOT).parts) for p in (ROOT / "scrimmage").rglob("__init__.py")
             if "__pycache__" not in p.parts and "java" not in p.relative_to(ROOT / "scrimmage").parts[:1]}
    assert found == listed, f"missing: {sorted(found - listed)}; not folders: {sorted(listed - found)}"


def test_package_data_covers_the_static_and_range_files():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]["scrimmage"]
    assert "assembler/static/**/*" in data and "testrange/ranges/*.toml" in data
    assert (ROOT / "scrimmage/assembler/static/vendor/blockly.min.js").is_file()
    assert (ROOT / "scrimmage/assembler/static/vendor/blockly-media").is_dir()
