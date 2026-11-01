"""FreeCAD add-on support: locate the add-on and FreeCAD, install/zip the add-on, run scripts in headless FreeCAD.

The add-on (freecad/Scrimmage) ships from the scrimmage git checkout only (install with `pip install -e`)."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path

ADDON_NAME = "Scrimmage"
_NOT_FOUND = ("the FreeCAD add-on ships with the scrimmage git checkout; "
              "install scrimmage with pip install -e <checkout>")


class FreeCADError(RuntimeError):
    pass


def addon_dir() -> Path:
    d = Path(__file__).resolve().parent.parent / "freecad" / ADDON_NAME
    if not (d / "package.xml").is_file():
        raise FreeCADError(_NOT_FOUND)
    return d


def find_freecad() -> str | None:
    """Headless FreeCAD binary: $SCRIMMAGE_FREECAD_CMD, then freecad.cmd, FreeCADCmd, freecadcmd on PATH.
    A set but unusable $SCRIMMAGE_FREECAD_CMD raises FreeCADError naming the variable."""
    env = os.environ.get("SCRIMMAGE_FREECAD_CMD")
    if env:
        found = shutil.which(env)
        if found is None:
            raise FreeCADError(f"SCRIMMAGE_FREECAD_CMD={env} isn't an executable")
        return found
    for name in ("freecad.cmd", "FreeCADCmd", "freecadcmd"):
        found = shutil.which(name)
        if found:
            return found
    return None


def mod_dirs() -> list[Path]:
    home = Path.home()
    return [home / "snap/freecad/common/Mod",
            home / ".var/app/org.freecad.FreeCAD/data/FreeCAD/Mod",
            home / ".local/share/FreeCAD/Mod"]


def _default_mod_dir() -> Path:
    dirs = mod_dirs()
    return next((d for d in dirs if d.exists()), dirs[-1])


def _addon_files(src: Path):
    for p in sorted(src.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            yield p


def install(to: Path | None = None) -> Path:
    """Copy the add-on to <to>/Scrimmage (default: the FreeCAD user Mod folder), replacing any previous copy."""
    src = addon_dir()
    dest = Path(to if to is not None else _default_mod_dir()) / ADDON_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{ADDON_NAME}.new-{uuid.uuid4().hex[:8]}"
    old = dest.parent / f".{ADDON_NAME}.old-{uuid.uuid4().hex[:8]}"
    try:
        shutil.copytree(src, tmp, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        moved = False
        if dest.exists():
            dest.rename(old)
            moved = True
        try:
            tmp.rename(dest)
        except BaseException:
            if moved:
                old.rename(dest)
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    return dest


def make_zip(out: Path) -> Path:
    """Zip the add-on with Scrimmage/ as the top folder (for the Addon Manager or a manual unzip into Mod)."""
    src = addon_dir()
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _addon_files(src):
            z.write(p, f"{ADDON_NAME}/{p.relative_to(src).as_posix()}")
    return out


DONE = "SCRIMMAGE_DONE"
_NOISE = re.compile(r"^(Adding snap-specific PYTHONPATH|FreeCAD \d+\.\d+.*Libs:|\(C\) \d{4}|"
                    r"FreeCAD is free and open-source)")

_PRELUDE = '''\
# (the leading newline before the marker below: FreeCAD 1.1.3 prints progress text such as "(100 %)" without a
# trailing newline, which would otherwise swallow the marker)
import json as _json, os as _os, sys as _sys, traceback as _tb
_sys.path.insert(0, {addon!r})
WORK = {work!r}
ARGS = _json.loads(_os.environ.get("SCRIMMAGE_FREECAD_ARGS", "[]"))
_sys.argv = [{name!r}] + ARGS
try:
    exec(compile(open({body!r}, encoding="utf-8").read(), {body!r}, "exec"), globals())
except SystemExit as _e:
    if _e.code is None or _e.code == 0:
        print("\\n" + {done!r}, flush=True)
    raise
except BaseException:
    _tb.print_exc()
    _sys.stdout.flush(); _sys.stderr.flush()
    _os._exit(3)
print("\\n" + {done!r}, flush=True)
'''


def _clean(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not _NOISE.match(l)).strip("\n")


def _snap_confined(binary: str) -> bool:
    try:
        return str(Path(binary).resolve()).startswith("/snap/") or Path(binary).parent == Path("/snap/bin")
    except OSError:
        return False


def _work_root(binary: str) -> Path:
    # A snap's FreeCAD can't read scripts from /tmp (private mount) or hidden dirs; ~/snap/freecad/common works
    if _snap_confined(binary):
        return Path.home() / "snap/freecad/common/scrimmage-work"
    return Path(tempfile.gettempdir()) / "scrimmage-work"


def run_freecad(script: str, args: list[str], timeout: float, files: dict | None = None,
                collect=None) -> subprocess.CompletedProcess:
    """Run `script` in headless FreeCAD with the add-on on sys.path. The script sees ARGS (and sys.argv[1:]) and WORK
    (a scratch dir holding `files`, name -> str/bytes). stdout comes back without FreeCAD's banner. `collect(work)`,
    if given, is called with WORK after a successful run, before WORK is deleted (to take files the script wrote).
    Raises FreeCADError on a non-zero exit, a timeout, or no FreeCAD."""
    binary = find_freecad()
    if binary is None:
        raise FreeCADError("FreeCAD not found: install FreeCAD 1.1+ or set SCRIMMAGE_FREECAD_CMD to its freecad.cmd/FreeCADCmd")
    addon = addon_dir()
    root = _work_root(binary)
    work = root / uuid.uuid4().hex
    work.mkdir(parents=True)
    try:
        for name, data in (files or {}).items():
            p = work / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data if isinstance(data, bytes) else data.encode())
        body = work / "_body.py"
        body.write_text(script, encoding="utf-8")
        runner = work / "_run.py"
        runner.write_text(encoding="utf-8", data=_PRELUDE.format(done=DONE, addon=str(addon), work=str(work), name=str(body), body=str(body)))
        env = dict(os.environ, SCRIMMAGE_FREECAD_ARGS=json.dumps(list(args)))
        popen = subprocess.Popen([binary, str(runner)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 encoding="utf-8", errors="replace", env=env, start_new_session=True)
        try:
            out, err = popen.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(popen.pid, signal.SIGKILL)
            except OSError:
                popen.kill()
            out, err = popen.communicate()
            raise FreeCADError(f"FreeCAD timed out after {timeout:g}s:\n{_clean(out)}\n{_clean(err)}") from None
        lines = _clean(out).splitlines()
        finished = DONE in lines
        stdout = "\n".join(l for l in lines if l != DONE) + "\n"
        stderr = _clean(err)
        if popen.returncode != 0 or not finished:
            why = f"exited with {popen.returncode}" if popen.returncode != 0 else "never ran the script (no completion marker)"
            raise FreeCADError(f"FreeCAD {why}:\n{stdout}{stderr}")
        if collect is not None:
            collect(work)
        return subprocess.CompletedProcess(popen.args, popen.returncode, stdout, stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            root.rmdir()          # only if empty
        except OSError:
            pass


class ExportProblems(Exception):
    """The Assembly can't be exported; `problems` lists why (the add-on's ExportError, carried out of FreeCAD)."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


_EXPORT_SCRIPT = """\
import json, os
from scrimmage_part import export
from scrimmage_part.errors import ExportError
try:
    out = export.export(ARGS[0] if os.path.isabs(ARGS[0]) else os.path.join(WORK, ARGS[0]), os.path.join(WORK, "out"))
except ExportError as e:
    out = {"problems": e.problems}
with open(os.path.join(WORK, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(out, f)
"""


def _snap_can_read(path: Path) -> bool:
    """Whether a snap's FreeCAD can read `path` where it is: the snap `home` interface allows $HOME's non-hidden
    entries (any depth below them), not ~/snap/<other snaps>, and nothing outside $HOME (/tmp is private)."""
    try:
        rel = path.resolve().relative_to(Path.home().resolve())
    except ValueError:
        return False
    parts = rel.parts
    if not parts or parts[0].startswith("."):
        return False
    return parts[0] != "snap" or (len(parts) > 1 and parts[1] == "freecad")


def _export_inputs(fcstd: Path, binary: str) -> tuple[str, dict]:
    """(the path FreeCAD opens, files to copy into WORK). In place when FreeCAD can read the file there, so the
    documents it links to are found next to it; otherwise the file and its sibling .FCStd documents are copied."""
    fcstd = fcstd.resolve()
    if not _snap_confined(binary) or _snap_can_read(fcstd):
        return str(fcstd), {}
    files = {f"in/{p.name}": p.read_bytes() for p in sorted(fcstd.parent.iterdir())
             if p.is_file() and p.suffix.lower() == ".fcstd" and p != fcstd}
    files[f"in/{fcstd.name}"] = fcstd.read_bytes()
    return f"in/{fcstd.name}", files


def _not_a_part_folder(out_dir: Path) -> str | None:
    if out_dir.exists() and not (out_dir.is_dir() and (out_dir / "part.toml").is_file()):
        return (f"the export folder {out_dir} exists and isn't a part folder (it has no part.toml); pick an empty "
                "or new folder")
    return None


def _move_into_place(src: Path, out_dir: Path) -> None:
    """Copy the exported folder `src` next to `out_dir` under a temporary name, then rename it into place (replacing
    an existing part folder)."""
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_dir.parent / f".{out_dir.name}.tmp-{uuid.uuid4().hex[:8]}"
    old = out_dir.parent / f".{out_dir.name}.old-{uuid.uuid4().hex[:8]}"
    try:
        shutil.copytree(src, tmp)
        moved = False
        if out_dir.exists():
            out_dir.rename(old)
            moved = True
        try:
            tmp.rename(out_dir)
        except BaseException:
            if moved:
                old.rename(out_dir)
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)


def export_part(fcstd: Path, out_dir: Path, timeout: float = 600) -> dict:
    """Export the Assembly in `fcstd` to the part folder `out_dir` with headless FreeCAD; returns the add-on's summary.
    Raises ExportProblems (nothing written) or FreeCADError (no FreeCAD, or it failed)."""
    fcstd, out_dir = Path(fcstd), Path(out_dir).resolve()
    if not fcstd.is_file():
        raise ExportProblems([f"no such FreeCAD file: {fcstd}"])
    bad = _not_a_part_folder(out_dir)
    if bad:
        raise ExportProblems([bad])
    hint = ("install FreeCAD 1.1+ (https://www.freecad.org/downloads.php, or `snap install freecad` on Linux) or set "
            "SCRIMMAGE_FREECAD_CMD to its freecad.cmd/FreeCADCmd")
    try:
        found = find_freecad()
    except FreeCADError as e:
        raise FreeCADError(f"{e}: {hint}") from None
    if found is None:
        raise FreeCADError(f"FreeCAD not found: {hint}")
    result: dict = {}

    def take(work: Path) -> None:       # before WORK is deleted: read the summary and move the folder out
        result.update(json.loads((work / "summary.json").read_text(encoding="utf-8")))
        if "problems" not in result:
            _move_into_place(work / "out", out_dir)

    where, files = _export_inputs(fcstd, found)
    run_freecad(_EXPORT_SCRIPT, [where], timeout, files=files, collect=take)
    if "problems" in result:
        raise ExportProblems(result["problems"])
    summary = dict(result)
    summary["out_dir"] = str(out_dir)
    return summary
