import os
import shutil
import stat
import sys
from pathlib import Path

from scrimmage import robotcode
from scrimmage.robotcode import Check, ProjectInfo, checklist, detect, program_table, run_fix

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_detect_java_phoenix_tuner():
    i = detect(EXAMPLES / "java_swerve")
    assert i.kind == "java" and "Phoenix 6" in i.vendors and "Java (GradleRIO)" in i.summary
    assert "WPILib-New-Commands" not in i.vendors


def test_detect_rev_and_robotpy():
    assert "REVLib" in detect(EXAMPLES / "java_maxswerve").vendors
    i = detect(EXAMPLES / "robotpy_kitbot")
    assert i.kind == "robotpy" and "RobotPy" in i.summary
    assert detect(EXAMPLES / "robotpy_maxswerve").kind == "robotpy"


def test_detect_not_a_project(tmp_path):
    i = detect(tmp_path)
    assert i.kind is None and "nothing here" in i.summary and "no build.gradle or robot.py" in i.summary
    assert detect(tmp_path / "gone").kind is None


def test_program_table_uses_code_folder(monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CODE", str(EXAMPLES))
    t = program_table(detect(EXAMPLES / "java_swerve"))
    assert t["cwd"] == "{code}/java_swerve"
    assert t["command"] == ["./gradlew", "-I", "{scrimmage_java}/scrimmage.init.gradle", "simulateJava"]


def test_program_table_robotpy_python(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRIMMAGE_CODE", raising=False)
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "none.json"))
    (tmp_path / "robot.py").write_text("")
    cmd = program_table(detect(tmp_path))["command"]
    assert cmd == ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
    py = tmp_path / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    py.parent.mkdir(parents=True)
    py.write_text("")
    i = detect(tmp_path)
    assert i.python == py
    assert program_table(i)["command"][0] == py.relative_to(tmp_path).as_posix()


def test_checklist_ids_and_realtime_note():
    c = {x.id: x for x in checklist(detect(EXAMPLES / "java_swerve"))}
    assert {"jdk", "agent", "gradle_built", "lockstep", "realtime"} <= set(c)
    assert c["realtime"].ok is None and "Phoenix" in c["realtime"].message
    assert "robotpy" not in c


def test_checklist_lockstep_not_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    c = {x.id: x for x in checklist(detect(EXAMPLES / "robotpy_kitbot"))}
    assert c["lockstep"].ok is None
    assert c["lockstep"].message == "not available on this computer: the robot runs in real time"
    assert "robotpy" in c


def _fake_project(tmp_path, body):
    (tmp_path / "build.gradle").write_text("")
    g = tmp_path / "gradlew"
    g.write_text("#!/bin/sh\n" + body)
    g.chmod(g.stat().st_mode | stat.S_IXUSR)
    return detect(tmp_path)


def test_gradle_build_failure_reported(tmp_path):
    info = _fake_project(tmp_path, "echo 'Could not resolve com.ctre:foo' >&2\nexit 1\n")
    lines = []
    r = run_fix("gradle_build", info, lines.append)
    assert isinstance(r, Check) and r.id == "gradle_built" and r.ok is False
    assert "Could not resolve" in r.detail and any("Could not resolve" in x for x in lines)
    assert "internet" in r.message.lower() and "Traceback" not in r.message


def test_gradle_build_success(tmp_path):
    info = _fake_project(tmp_path, "mkdir -p build/libs\ntouch build/libs/robot.jar\necho BUILD SUCCESSFUL\n")
    r = run_fix("gradle_build", info, lambda s: None)
    assert r.ok is True


def test_failed_build_does_not_read_as_built(tmp_path):
    # a failed build leaves a build/ folder (and Gradle's tmp files) but no jar and no compiled classes
    info = _fake_project(tmp_path, "mkdir -p build/tmp/compileJava build/classes/java/main\nexit 1\n")
    r = run_fix("gradle_build", info, lambda s: None)
    assert r.ok is False
    c = {x.id: x for x in checklist(detect(tmp_path))}
    assert c["gradle_built"].ok is False and c["gradle_built"].fix == "gradle_build"
    assert robotcode.built_at(tmp_path) is None


def test_built_reads_jar_or_classes(tmp_path):
    (tmp_path / "build.gradle").write_text("")
    assert robotcode.built_at(tmp_path) is None
    cls = tmp_path / "build" / "classes" / "java" / "main" / "frc" / "robot" / "Robot.class"
    cls.parent.mkdir(parents=True)
    cls.write_bytes(b"")
    assert robotcode.built_at(tmp_path) is not None
    assert {x.id: x for x in checklist(detect(tmp_path))}["gradle_built"].ok is True
    shutil.rmtree(tmp_path / "build")
    (tmp_path / "build" / "libs").mkdir(parents=True)
    (tmp_path / "build" / "libs" / "robot.jar").write_bytes(b"")
    assert robotcode.built_at(tmp_path) is not None


def test_detail_is_last_20_lines(tmp_path):
    info = _fake_project(tmp_path, "for i in $(seq 1 50); do echo line$i; done\nexit 1\n")
    r = run_fix("gradle_build", info, lambda s: None)
    assert r.detail.splitlines() == [f"line{n}" for n in range(31, 51)]


def test_build_agent_and_lockstep_fixes_use_commands(tmp_path, monkeypatch):
    seen = []

    def fake(cmd, cwd, env, out):
        seen.append(cmd)
        out("ok")
        return 0, ["ok"]
    monkeypatch.setattr(robotcode, "_stream", fake)
    info = ProjectInfo(tmp_path, "robotpy", [], False, Path("/x/python"), "")
    run_fix("install_lockstep", info, lambda s: None)
    assert seen[-1][:4] == ["/x/python", "-m", "pip", "install"]
    assert Path(seen[-1][-1]).name == "lockstep"
    r = run_fix("build_agent", info, lambda s: None)
    assert r.id == "agent" and seen[-1][-3:] == ["scrimmage", "java", "build"]


def test_unknown_fix_is_plain(tmp_path):
    r = run_fix("nope", detect(tmp_path), lambda s: None)
    assert r.ok is False


def test_robotpy_check_falls_back_to_scrimmage_python(tmp_path, monkeypatch):
    (tmp_path / "robot.py").write_text("")
    seen = []

    class R:
        returncode = 0
        stdout = ""
    monkeypatch.setattr(robotcode.subprocess, "run", lambda cmd, **k: seen.append(cmd) or R())
    c = [x for x in checklist(detect(tmp_path)) if x.id == "robotpy"][0]
    assert c.ok is True and seen[0][0] == sys.executable


def test_lockstep_installed_but_import_fails(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(robotcode, "_lockstep_state", lambda py: ("9.0.0", False))
    c = [x for x in checklist(detect(EXAMPLES / "java_swerve")) if x.id == "lockstep"][0]
    assert c.ok is False and c.fix == "install_lockstep" and "doesn't load" in c.message


def test_lockstep_state_runs_import_in_target_python(monkeypatch):
    seen = []

    class R:
        returncode = 1
        stdout = "0.6.0\n"
    monkeypatch.setattr(robotcode.subprocess, "run", lambda cmd, **k: seen.append(cmd) or R())
    assert robotcode._lockstep_state(Path("/p/python")) == ("0.6.0", False)
    assert seen[0][0] == "/p/python" and "scrimmage_lockstep" in seen[0][2] and "library_path" in seen[0][2]


def test_fix_stdin_is_empty(tmp_path):
    info = _fake_project(tmp_path, "if read x; then echo got-input; else echo eof; fi\nexit 1\n")
    r = run_fix("gradle_build", info, lambda s: None)
    assert r.detail == "eof"


def test_fix_timeout_is_plain(tmp_path, monkeypatch):
    info = _fake_project(tmp_path, "echo start\nsleep 30\n")
    monkeypatch.setattr(robotcode, "GRADLE_TIMEOUT", 0.5)
    r = run_fix("gradle_build", info, lambda s: None)
    assert r.ok is False and "took too long and was stopped" in r.message
    assert "took too long and was stopped" in r.detail


def test_install_lockstep_failure_gives_command(tmp_path, monkeypatch):
    monkeypatch.setattr(robotcode, "_stream", lambda *a, **k: (1, ["boom"]))
    info = ProjectInfo(tmp_path, "robotpy", [], False, Path("/x/python"), "")
    r = run_fix("install_lockstep", info, lambda s: None)
    assert f'/x/python -m pip install "{robotcode.LOCKSTEP_DIR}"' in r.message


def test_install_lockstep_missing_folder_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(robotcode, "LOCKSTEP_DIR", tmp_path / "nope")
    monkeypatch.setattr(robotcode, "_stream", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    info = ProjectInfo(tmp_path, "robotpy", [], False, Path("/x/python"), "")
    r = run_fix("install_lockstep", info, lambda s: None)
    assert r.ok is False and "pip install" in r.message and "lockstep folder" in r.message
