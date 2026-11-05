"""Robot code projects: what a folder is (Java or RobotPy), what it needs before it can start, and how to fix that.

The start page's "point at the code and run" uses this. `detect` reads the folder, `checklist` says what is ready and
what is missing in plain words, and `run_fix` does the missing step (the same ones the install guides ask for).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import tomllib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from . import i18n, java_tools, teamfolders

JAVA_COMMAND = ["./gradlew", "-I", "{scrimmage_java}/scrimmage.init.gradle", "simulateJava"]
ROBOTPY_ARGS = ["-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
DETAIL_LINES = 20
DS_SOCKET_ARG = "--ds-socket"   # RobotPy sim's flag for WPILib's DS socket extension (the real Driver Station)
LOCKSTEP_DIR = Path(__file__).resolve().parent.parent / "lockstep"

# vendordep "name" -> the name teams use. Anything else is shown as written; WPILib's own and scrimmage's are hidden.
_VENDOR_NAMES = {"CTRE-Phoenix (v6)": "Phoenix 6", "CTRE-Phoenix": "Phoenix 5", "REVLib": "REVLib",
                 "photonlib": "PhotonVision", "PathplannerLib": "PathPlanner", "Studica": "Studica",
                 "AdvantageKit": "AdvantageKit"}
_HIDDEN_VENDORS = ("WPILib", "Scrimmage")
# pip package (in a RobotPy project's `requires`) -> team name
_PIP_VENDORS = {"phoenix6": "Phoenix 6", "robotpy-rev": "REVLib", "robotpy-navx": "NavX", "photonlibpy": "PhotonVision",
                "robotpy-commands-v2": "Commands v2", "pathplannerlib": "PathPlanner"}


@dataclass
class ProjectInfo:
    folder: Path
    kind: Literal["java", "robotpy"] | None
    vendors: list[str]
    tuner_swerve: bool
    python: Path | None
    summary: str


@dataclass
class Check:
    id: str
    ok: bool | None      # None: information only
    message: str
    fix: str | None = None
    detail: str | None = None


def _venv_python(folder: Path) -> Path | None:
    rel = Path(".venv") / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return folder / rel if (folder / rel).is_file() else None


def _java_vendors(folder: Path) -> list[str]:
    out: list[str] = []
    for f in sorted((folder / "vendordeps").glob("*.json")):
        try:
            name = json.loads(f.read_text(encoding="utf-8")).get("name") or f.stem
        except (OSError, ValueError, AttributeError):
            name = f.stem
        if name.startswith(_HIDDEN_VENDORS):
            continue
        name = _VENDOR_NAMES.get(name, name)
        if name not in out:
            out.append(name)
    return out


def _robotpy_vendors(folder: Path) -> list[str]:
    try:
        data = tomllib.loads((folder / "pyproject.toml").read_text(encoding="utf-8"))
        requires = data.get("tool", {}).get("robotpy", {}).get("requires", [])
    except (OSError, ValueError):
        return []
    out: list[str] = []
    for r in requires if isinstance(requires, list) else []:
        pkg = re.split(r"[<>=!~\[; ]", str(r).strip(), maxsplit=1)[0].lower()
        name = _PIP_VENDORS.get(pkg, pkg)
        if name and name not in out:
            out.append(name)
    return out


def _tuner_swerve(folder: Path) -> bool:
    for java in (folder / "src").rglob("*.java"):
        try:
            text = java.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "TunerConstants" in text or "com.ctre.phoenix6.swerve" in text:
            return True
    return False


def detect(folder: Path) -> ProjectInfo:
    folder = Path(folder)
    if is_java(folder):
        vendors = _java_vendors(folder)
        tuner = "Phoenix 6" in vendors and _tuner_swerve(folder)
        parts = ["Java (GradleRIO)", *vendors] + (["Tuner X swerve"] if tuner else [])
        return ProjectInfo(folder, "java", vendors, tuner, None, " · ".join(parts))
    if (folder / "robot.py").is_file():
        vendors = _robotpy_vendors(folder)
        return ProjectInfo(folder, "robotpy", vendors, False, _venv_python(folder), " · ".join(["Python (RobotPy)", *vendors]))
    return ProjectInfo(folder, None, [], False, None, "nothing here scrimmage can start: no build.gradle or robot.py")


def program_table(info: ProjectInfo) -> dict:
    """The `[robot_program]` values for this project."""
    if info.kind == "java":
        command = list(JAVA_COMMAND)
    elif info.kind == "robotpy":
        py = info.python.relative_to(info.folder).as_posix() if info.python else "{python}"
        command = [py, *ROBOTPY_ARGS]
    else:
        raise ValueError(info.summary)
    return {"command": command, "cwd": teamfolders.relativize(info.folder)}


def with_ds_socket(command: list[str]) -> list[str]:
    """A RobotPy command (`... -m robotpy ... sim ...`, as ROBOTPY_ARGS) with --ds-socket after `sim`, so the robot
    program listens for the real Driver Station; any other command, or one that has it already, unchanged."""
    if DS_SOCKET_ARG in command:
        return list(command)
    for i in range(len(command) - 1):
        if command[i] == "-m" and command[i + 1] == "robotpy" and "sim" in command[i + 2:]:
            at = command.index("sim", i + 2) + 1
            return [*command[:at], DS_SOCKET_ARG, *command[at:]]
    return list(command)


def _lockstep_state(python: Path | None) -> tuple[str | None, bool]:
    """(installed scrimmage-lockstep version, whether its extension imports and is built) in `python` (this
    program's Python when None). (None, False) when it isn't installed."""
    code = ("from importlib.metadata import version; print(version('scrimmage-lockstep'), flush=True)\n"
            "import os, scrimmage_lockstep; assert os.path.exists(str(scrimmage_lockstep.library_path()))")
    try:
        r = subprocess.run([str(python or sys.executable), "-c", code], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None, False
    lines = r.stdout.split()
    return (lines[0] if lines else None), r.returncode == 0


def _version_tuple(text: str) -> tuple[int, ...]:
    nums = [int(m.group()) if (m := re.match(r"\d+", p)) else 0 for p in text.split(".")[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def _check_jdk() -> Check:
    try:
        jdk = java_tools.find_jdk()
    except java_tools.JavaToolError:
        return Check("jdk", False, i18n.t("Java isn't installed: install WPILib (it brings the Java scrimmage needs), then check again."))
    return Check("jdk", True, i18n.t("Java found ({jdk})", jdk=jdk))


def _check_agent() -> Check:
    problem = java_tools.agent_missing_message("this robot", ["scrimmage.init.gradle"])
    if problem is None:
        return Check("agent", True, i18n.t("scrimmage's Java helper is built"))
    old = java_tools.agent_jar_path().exists()
    return Check("agent", False, "scrimmage's Java helper is out of date: build it again." if old
                 else "scrimmage's Java helper isn't built yet: build it (once per computer).", fix="build_agent")


def built_at(folder: Path) -> float | None:
    """When the robot code last built here (the newest jar in build/libs or compiled class under build/classes, as
    a file time), None when it never has. A build/ folder alone doesn't count: a failed build leaves one too."""
    build = Path(folder) / "build"
    times = [p.stat().st_mtime for p in (build / "libs").glob("*.jar") if p.is_file()]
    if not times and (build / "classes").is_dir():
        times = [p.stat().st_mtime for p in (build / "classes").rglob("*.class") if p.is_file()]
    return max(times) if times else None


def is_java(folder: Path) -> bool:
    return any((Path(folder) / f).is_file() for f in ("build.gradle", "build.gradle.kts"))


def _check_gradle_built(info: ProjectInfo) -> Check:
    if built_at(info.folder) is not None:
        return Check("gradle_built", True, i18n.t("The robot code has been built before"))
    return Check("gradle_built", False, i18n.t("The robot code hasn't been built on this computer yet. The first build downloads the libraries, so it needs the internet and takes a few minutes."), fix="gradle_build")


def _check_lockstep(info: ProjectInfo) -> Check:
    if sys.platform != "linux":
        return Check("lockstep", None, i18n.t("not available on this computer: the robot runs in real time"))
    have, imports = _lockstep_state(info.python)
    from .session import LOCKSTEP_VERSION
    need = ".".join(map(str, LOCKSTEP_VERSION))
    if have is None:
        return Check("lockstep", False, i18n.t("The fast-simulation add-on isn't installed for this robot's Python: install it to run "
                                               "faster than real time and to pause the robot.") if info.python
                     else i18n.t("The fast-simulation add-on isn't installed: install it to run faster than real time and to "
                                 "pause the robot."), fix="install_lockstep")
    if _version_tuple(have) < LOCKSTEP_VERSION:
        return Check("lockstep", False, i18n.t("The fast-simulation add-on is too old ({have}; this scrimmage needs {need} or newer): update it.", have=have, need=need), fix="install_lockstep")
    if not imports:
        return Check("lockstep", False, i18n.t("The fast-simulation add-on is installed for this robot's Python but doesn't load (its "
                                               "compiled part is missing): install it again.") if info.python
                     else i18n.t("The fast-simulation add-on is installed but doesn't load (its compiled part is missing): "
                                 "install it again."), fix="install_lockstep")
    return Check("lockstep", True, i18n.t("The fast-simulation add-on is installed ({have})", have=have))


def _check_realtime(info: ProjectInfo) -> Check:
    if sys.platform != "linux":
        return Check("realtime", None, i18n.t("On this computer the robot runs in real time (one second of robot is one second of waiting)."))
    if "Phoenix 6" in info.vendors:
        return Check("realtime", None, i18n.t("This robot uses CTRE Phoenix 6, so it runs in real time: it can't be paused or sped up."))
    return Check("realtime", None, i18n.t("This robot can be paused and run faster than real time."))


def _check_robotpy(info: ProjectInfo) -> Check:
    python = info.python or Path(sys.executable)
    own = "this project's Python environment (.venv)" if info.python else "scrimmage's Python"
    try:
        r = subprocess.run([str(python), "-c", "import robotpy"], capture_output=True, timeout=60,
                           stdin=subprocess.DEVNULL)
        ok = r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    if ok:
        return Check("robotpy", True, i18n.t("RobotPy is installed in {own}", own=own))
    return Check("robotpy", False, i18n.t("RobotPy isn't installed in {own}: run {python} -m pip install \"robotpy[sim]\"", own=own, python=python))


def checklist(info: ProjectInfo) -> list[Check]:
    """What is ready and what is missing before this project can start."""
    if info.kind == "java":
        return [_check_jdk(), _check_agent(), _check_gradle_built(info), _check_lockstep(info), _check_realtime(info)]
    if info.kind == "robotpy":
        return [_check_robotpy(info), _check_lockstep(info), _check_realtime(info)]
    return []


TIMED_OUT = -999
CANCELLED = -998
GRADLE_TIMEOUT, OTHER_TIMEOUT = 20 * 60, 5 * 60


def _stream(cmd: list[str], cwd: Path | None, env: dict | None, out: Callable[[str], None],
            timeout: float = OTHER_TIMEOUT, cancel: threading.Event | None = None) -> tuple[int, list[str]]:
    """Run `cmd` (it can't ask questions: stdin is empty), passing each output line to `out`. Returns the exit code
    (TIMED_OUT if it ran longer than `timeout` seconds and was stopped, CANCELLED if `cancel` was set first) and the
    last output lines."""
    tail: deque[str] = deque(maxlen=DETAIL_LINES)
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             start_new_session=sys.platform != "win32", stderr=subprocess.STDOUT, text=True, errors="replace")
    except OSError as e:
        line = f"can't run {cmd[0]}: {e.strerror or e}"
        out(line)
        return 127, [line]
    stopped = threading.Event()
    cancelled = threading.Event()
    finished = threading.Event()

    def stop() -> None:
        stopped.set()
        try:
            if sys.platform == "win32":
                p.kill()
            else:
                os.killpg(p.pid, signal.SIGKILL)  # the whole group: Gradle starts helper processes
        except OSError:
            pass
    timer = threading.Timer(timeout, stop)
    timer.start()
    if cancel is not None:
        def watch() -> None:
            while not finished.is_set():
                if cancel.wait(0.2):
                    cancelled.set()
                    stop()
                    return
        threading.Thread(target=watch, daemon=True, name="robotcode-cancel").start()
    assert p.stdout is not None
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            tail.append(line)
            out(line)
        code = p.wait()
    finally:
        finished.set()
        timer.cancel()
    if cancelled.is_set():
        line = "this was stopped (scrimmage edit was closed)"
        out(line)
        return CANCELLED, [*tail, line][-DETAIL_LINES:]
    if stopped.is_set():
        line = "this took too long and was stopped"
        out(line)
        return TIMED_OUT, [*tail, line][-DETAIL_LINES:]
    return code, list(tail)


def _failed(check_id: str, message: str, tail: list[str], fix: str, code: int = 1) -> Check:
    if code == TIMED_OUT:
        message = i18n.t("This took too long and was stopped. {advice}", advice=message.split(". ", 1)[-1])
    elif code == CANCELLED:
        message = i18n.t("This was stopped before it finished: scrimmage edit was closed. Try again.")
    return Check(check_id, False, message, fix=fix, detail="\n".join(tail[-DETAIL_LINES:]))


def run_fix(fix: str, info: ProjectInfo, out: Callable[[str], None], cancel: threading.Event | None = None) -> Check:
    """Do one missing step, streaming its output lines to `out`; returns the re-checked item. Setting `cancel`
    stops the step's program (and what it started)."""
    kw = {"cancel": cancel} if cancel is not None else {}
    if fix == "build_agent":
        code, tail = _stream([sys.executable, "-m", "scrimmage", "java", "build"], None, None, out, **kw)
        if code != 0:
            return _failed("agent", i18n.t("Building scrimmage's Java helper didn't work. It needs Java (WPILib) and the internet the first time. The last lines are below."), tail, fix, code)
        return _check_agent()
    if fix == "gradle_build":
        env = dict(os.environ)
        try:
            env["JAVA_HOME"] = str(java_tools.find_jdk())
        except java_tools.JavaToolError:
            pass
        wrapper = "gradlew.bat" if sys.platform == "win32" else "./gradlew"
        code, tail = _stream([wrapper, "build"], info.folder, env, out, GRADLE_TIMEOUT, **kw)
        if code != 0:
            return _failed("gradle_built", i18n.t("The robot code didn't build. The first build needs the internet to download libraries, and the right Java. The last lines are below."), tail, fix, code)
        return _check_gradle_built(info)
    if fix == "install_lockstep":
        python = str(info.python or sys.executable)
        by_hand = f'{python} -m pip install "{LOCKSTEP_DIR}"'
        if not (LOCKSTEP_DIR / "pyproject.toml").is_file():
            return Check("lockstep", False, i18n.t("scrimmage can't find its lockstep folder (it isn't running from its downloaded copy). From the scrimmage folder, run: {by_hand}", by_hand=by_hand), fix=fix)
        code, tail = _stream([python, "-m", "pip", "install", str(LOCKSTEP_DIR)], None, None, out, **kw)
        if code != 0:
            return _failed("lockstep", i18n.t("Installing the fast-simulation add-on didn't work. It needs the internet and a C++ compiler the first time. To try by hand, run: {by_hand}", by_hand=by_hand), tail, fix, code)
        return _check_lockstep(info)
    return Check(fix, False, i18n.t("scrimmage doesn't know how to fix \"{fix}\".", fix=fix))
