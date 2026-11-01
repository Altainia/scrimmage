import ast
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scrimmage import freecad_tools
from scrimmage.__main__ import main
from scrimmage.motors import MOTORS

from freecad_harness import needs_freecad, run_in_freecad

ADDON = Path(__file__).resolve().parent.parent / "freecad" / "Scrimmage"
ALLOWED = {"FreeCAD", "FreeCADGui", "Part", "Mesh", "MeshPart", "JointObject", "UtilsAssembly", "Materials", "PySide"}


def test_addon_copies_in_sync():
    repo = ADDON.parent.parent
    assert (ADDON / "scrimmage_part" / "toml_writer.py").read_bytes() == (repo / "scrimmage/parts/toml_writer.py").read_bytes()
    ns = {}
    exec((ADDON / "scrimmage_part" / "motors.py").read_text(encoding="utf-8"), ns)
    assert list(ns["MOTOR_NAMES"]) == sorted(MOTORS)
    stdlib = set(sys.stdlib_module_names)
    for path in ADDON.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            for n in names:
                top = n.split(".")[0]
                # `import scrimmage_part` style absolute imports of the add-on's own package are fine
                assert top in stdlib or top in ALLOWED or top == "scrimmage_part", f"{path}: imports {n}"
                if top in ("FreeCADGui", "PySide"):
                    assert path.name in ("InitGui.py", "panel.py"), f"{path}: GUI import {n}"


def test_install_copies_addon(tmp_path):
    dest = freecad_tools.install(tmp_path)
    assert dest == tmp_path / "Scrimmage"
    assert (dest / "InitGui.py").is_file()
    (dest / "stale.txt").write_text("x")
    freecad_tools.install(tmp_path)
    assert (dest / "InitGui.py").is_file() and not (dest / "stale.txt").exists()
    assert not list(dest.rglob("__pycache__"))


def test_zip_has_workbench(tmp_path):
    out = freecad_tools.make_zip(tmp_path / "a.zip")
    names = zipfile.ZipFile(out).namelist()
    assert "Scrimmage/package.xml" in names
    assert "Scrimmage/LICENSE" in names     # BSD-3-Clause: the notice travels with the add-on
    assert all(n.startswith("Scrimmage/") for n in names)


def test_addon_license_matches_the_repo_license():
    repo = Path(__file__).resolve().parent.parent
    assert (repo / "freecad" / "Scrimmage" / "LICENSE").read_text() == (repo / "LICENSE").read_text()


def test_cli_install_and_zip(tmp_path, capsys):
    main(["freecad", "install", "--to", str(tmp_path)])
    assert str(tmp_path / "Scrimmage") in capsys.readouterr().out
    z = tmp_path / "out.zip"
    main(["freecad", "zip", "-o", str(z)])
    assert z.is_file()


def test_missing_addon_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(freecad_tools, "addon_dir", lambda: (_ for _ in ()).throw(freecad_tools.FreeCADError("ships with the scrimmage git checkout")))
    with pytest.raises(SystemExit) as e:
        main(["freecad", "zip"])
    assert e.value.code == 2
    assert "git checkout" in capsys.readouterr().err


@needs_freecad
def test_addon_loads_in_freecad():
    out = run_in_freecad("import scrimmage_part, json; print('SCRIMMAGE_JSON:' + json.dumps({'ok': True}))")
    assert out == {"ok": True}


@needs_freecad
def test_runner_files_and_failure():
    out = run_in_freecad("import json, os; print('SCRIMMAGE_JSON:' + json.dumps({'t': open(os.path.join(WORK, 'a.txt')).read()}))",
                         {"a.txt": "hi"})
    assert out == {"t": "hi"}
    with pytest.raises(Exception, match="boom"):
        run_in_freecad("raise RuntimeError('boom')")


@needs_freecad
def test_addon_dir_from_package():
    out = run_in_freecad("import scrimmage_part, os, json; d = scrimmage_part.addon_dir(); "
                         "print('SCRIMMAGE_JSON:' + json.dumps({'d': d, 'xml': os.path.isfile(os.path.join(d, 'package.xml')),"
                         " 'icon': os.path.isfile(os.path.join(d, 'icons', 'scrimmage.svg'))}))")
    assert out["d"] == str(ADDON) and out["xml"] and out["icon"]


def test_env_var_must_be_executable(monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_FREECAD_CMD", "/nonexistent/x")
    with pytest.raises(freecad_tools.FreeCADError, match="SCRIMMAGE_FREECAD_CMD=/nonexistent/x isn't an executable"):
        freecad_tools.find_freecad()
    monkeypatch.setenv("SCRIMMAGE_FREECAD_CMD", "/bin/true")
    assert freecad_tools.find_freecad() == "/bin/true"


def test_script_that_never_ran_is_failure(monkeypatch, tmp_path):
    fake = tmp_path / "fakecad"
    fake.write_text("#!/bin/sh\necho banner\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SCRIMMAGE_FREECAD_CMD", str(fake))
    with pytest.raises(freecad_tools.FreeCADError, match="never ran"):
        freecad_tools.run_freecad("print(1)", [], 20)


def test_timeout_kills_and_reports(monkeypatch, tmp_path):
    fake = tmp_path / "slowcad"
    fake.write_text("#!/bin/sh\necho partial\nsleep 30\n")
    fake.chmod(0o755)
    monkeypatch.setenv("SCRIMMAGE_FREECAD_CMD", str(fake))
    with pytest.raises(freecad_tools.FreeCADError, match="(?s)timed out.*partial"):
        freecad_tools.run_freecad("print(1)", [], 1)


@needs_freecad
def test_clean_sys_exit_counts_as_success():
    proc = freecad_tools.run_freecad("print('hello'); import sys; sys.exit(0)", [], 120)
    assert "hello" in proc.stdout and "SCRIMMAGE_DONE" not in proc.stdout
    with pytest.raises(freecad_tools.FreeCADError, match="exited with 4"):
        freecad_tools.run_freecad("import sys; sys.exit(4)", [], 120)


def test_install_failed_swap_restores_old(tmp_path, monkeypatch):
    dest = freecad_tools.install(tmp_path)
    (dest / "marker.txt").write_text("old")
    real = Path.rename
    calls = []

    def flaky(self, target):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("boom")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError, match="boom"):
        freecad_tools.install(tmp_path)
    monkeypatch.undo()
    assert (dest / "marker.txt").read_text() == "old" and (dest / "InitGui.py").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Scrimmage"]


def test_cli_export_without_freecad_exits_2_with_an_install_hint(tmp_path):
    fcstd = tmp_path / "a.FCStd"
    fcstd.write_bytes(b"not really")
    env = dict(os.environ, SCRIMMAGE_FREECAD_CMD="/nonexistent", PATH=str(Path(sys.executable).parent))
    r = subprocess.run([sys.executable, "-m", "scrimmage", "freecad", "export", str(fcstd), str(tmp_path / "out")],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2
    assert "install FreeCAD" in r.stderr and "SCRIMMAGE_FREECAD_CMD" in r.stderr
    assert not (tmp_path / "out").exists()


# ---------------------------------------------------------------- final review fixes

def _addon_module(name):
    sys.path.insert(0, str(ADDON))
    try:
        import importlib
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(ADDON))


def test_freecad_version_check_needs_1_1():
    sp = _addon_module("scrimmage_part")
    assert sp.version_problem(["1", "1", "1", "44227 +647 (Git)"]) is None
    assert sp.version_problem(["1", "2", "0"]) is None and sp.version_problem(["2", "0", "0dev"]) is None
    assert sp.version_problem(["1", "0", "2", "39319 (Git)"]) == \
        "the scrimmage add-on needs FreeCAD 1.1 or newer (this is 1.0.2)"
    assert sp.version_problem(["0", "21", "2"]) == "the scrimmage add-on needs FreeCAD 1.1 or newer (this is 0.21.2)"


def test_export_refuses_old_freecad(monkeypatch):
    sp = _addon_module("scrimmage_part")
    export = _addon_module("scrimmage_part.export")
    errors = _addon_module("scrimmage_part.errors")
    fake = type(sys)("FreeCAD")
    fake.Version = lambda: ["1", "0", "2", "x"]
    monkeypatch.setitem(sys.modules, "FreeCAD", fake)
    with pytest.raises(errors.ExportError) as e:
        export.export("/nowhere.FCStd", "/tmp/out")
    assert e.value.problems == ["the scrimmage add-on needs FreeCAD 1.1 or newer (this is 1.0.2)"]
    assert sp.require_freecad is not None


def test_a_failed_rename_into_place_is_an_export_error(tmp_path, monkeypatch):
    export = _addon_module("scrimmage_part.export")
    errors = _addon_module("scrimmage_part.errors")
    tmp, out = tmp_path / ".x.tmp", tmp_path / "x"
    tmp.mkdir()
    out.mkdir()
    (out / "part.toml").write_text("old")
    real = os.rename
    calls = []

    def rename(a, b):
        calls.append((a, b))
        if len(calls) == 2:                     # the new folder into place: Windows says the folder is in use
            raise PermissionError(13, "Access is denied", str(b))
        return real(a, b)

    monkeypatch.setattr(export.os, "rename", rename)
    with pytest.raises(errors.ExportError) as e:
        export._install(str(tmp), str(out))
    (p,) = e.value.problems
    assert f"close anything using {out} (Explorer, an editor) and export again" in p
    assert (out / "part.toml").read_text() == "old"          # the old part folder is put back


def test_package_xml_needs_freecad_1_1_and_names_the_license():
    text = (ADDON / "package.xml").read_text(encoding="utf-8")
    assert "<freecadmin>1.1.0</freecadmin>" in text and '<license file="LICENSE">BSD-3-Clause</license>' in text


def test_snap_reads_non_hidden_home_paths_in_place(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    can = freecad_tools._snap_can_read
    assert can(home / "cad" / "robot.FCStd") and can(home / "cad" / ".hidden" / "robot.FCStd")
    assert can(home / "snap" / "freecad" / "common" / "x.FCStd")
    assert not can(home / ".cad" / "robot.FCStd") and not can(home / "snap" / "other" / "x.FCStd")
    assert not can(tmp_path / "elsewhere.FCStd")
    (home / "x").mkdir(parents=True)
    f = home / "x" / "robot.FCStd"
    f.write_bytes(b"r")
    (home / "x" / "lib.FCStd").write_bytes(b"l")
    assert freecad_tools._export_inputs(f, "/snap/bin/freecad.cmd") == (str(f), {})
    out = tmp_path / "out"
    out.mkdir()
    g = out / "robot.FCStd"
    g.write_bytes(b"r")
    (out / "lib.fcstd").write_bytes(b"l")
    (out / "notes.txt").write_text("n")
    where, files = freecad_tools._export_inputs(g, "/snap/bin/freecad.cmd")
    assert where == "in/robot.FCStd" and files == {"in/robot.FCStd": b"r", "in/lib.fcstd": b"l"}
    assert freecad_tools._export_inputs(g, "/usr/bin/FreeCADCmd") == (str(g), {})
