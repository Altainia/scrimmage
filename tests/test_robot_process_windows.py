"""The robot program's lifetime on Windows: scrimmage's job object (the robot program dies with scrimmage, as Linux's
PR_SET_PDEATHSIG does), kill_by_token finding a robot JVM by the token on its command line, and `./gradlew` run as
gradlew.bat. The Windows calls are faked here on every platform; the tests marked for Windows run the real ones (CI's
windows job)."""

import asyncio
import ctypes
import logging
import os
import subprocess
import sys
import time
import types
import uuid
from pathlib import Path

import pytest

from scrimmage import robot_process
from scrimmage.robot_process import RUN_TOKEN_ARG, RobotProcess

ROOT = Path(__file__).resolve().parents[1]
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows only (job objects, taskkill, CIM)")


class FakeKernel32:
    """kernel32's job object calls, recorded; `fail` names one that fails."""

    def __init__(self, fail=None):
        self.calls, self.fail, self.limits = [], fail, None

    def _call(self, name, ok, *args):
        self.calls.append((name, *args))
        return 0 if name == self.fail else ok

    def CreateJobObjectW(self, attrs, name):
        return self._call("CreateJobObjectW", 11, attrs, name)

    def SetInformationJobObject(self, job, info_class, info, size):
        self.limits = ctypes.cast(info, ctypes.POINTER(robot_process._JobLimits)).contents
        return self._call("SetInformationJobObject", 1, job, info_class, size)

    def OpenProcess(self, access, inherit, pid):
        return self._call("OpenProcess", 22, access, inherit, pid)

    def AssignProcessToJobObject(self, job, proc):
        return self._call("AssignProcessToJobObject", 1, job, proc)

    def CloseHandle(self, handle):
        return self._call("CloseHandle", 1, handle)


@pytest.fixture
def no_job(monkeypatch):
    monkeypatch.setattr(robot_process, "_job", None)


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="the 64-bit layout")
def test_the_job_limits_structure_has_windows_x64s_layout():
    assert ctypes.sizeof(robot_process._JobLimits) == 144   # JOBOBJECT_EXTENDED_LIMIT_INFORMATION on x64
    assert robot_process._JobLimits.IoInfo.offset == 64 and robot_process._BasicLimits.LimitFlags.offset == 16


def test_one_job_kills_on_close_and_takes_every_robot_program(no_job):
    k32 = FakeKernel32()
    assert robot_process.assign_to_job(1234, k32) and robot_process.assign_to_job(5678, k32)
    names = [c[0] for c in k32.calls]
    assert names == ["CreateJobObjectW", "SetInformationJobObject", "OpenProcess", "AssignProcessToJobObject",
                     "CloseHandle", "OpenProcess", "AssignProcessToJobObject", "CloseHandle"]
    assert k32.calls[0] == ("CreateJobObjectW", None, None)          # not inheritable: only scrimmage holds it
    assert k32.calls[1][2] == 9 and k32.limits.BasicLimitInformation.LimitFlags == 0x2000   # KILL_ON_JOB_CLOSE
    assert k32.calls[2] == ("OpenProcess", 0x0101, False, 1234) and k32.calls[3] == ("AssignProcessToJobObject", 11, 22)
    assert k32.calls[4] == ("CloseHandle", 22)                       # the process handle; the job's is kept


@pytest.mark.parametrize("fail", ["CreateJobObjectW", "SetInformationJobObject", "OpenProcess",
                                  "AssignProcessToJobObject"])
def test_a_job_that_cant_be_made_or_joined_is_one_line_and_no_error(no_job, caplog, fail):
    k32 = FakeKernel32(fail)
    with caplog.at_level(logging.WARNING, logger="scrimmage"):
        assert robot_process.assign_to_job(1234, k32) is False
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and lines[0].startswith("couldn't tie the robot program to scrimmage"), lines
    assert f"({fail} failed, error 0)" in lines[0] and "may keep running" in lines[0]
    if fail == "AssignProcessToJobObject":
        assert k32.calls[-1] == ("CloseHandle", 22)
    if fail == "SetInformationJobObject":
        assert k32.calls[-1] == ("CloseHandle", 11) and robot_process._job is None


def test_kill_by_token_on_windows_asks_cim_for_the_command_line_and_taskkills(monkeypatch, caplog):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(robot_process, "_warned_query", False)
    token, seen, killed = uuid.uuid4().hex, [], []

    def run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "123\r\n456\r\n", "")
    monkeypatch.setattr(robot_process.subprocess, "run", run)
    monkeypatch.setattr(robot_process, "kill_tree", killed.append)
    monkeypatch.setattr(robot_process.os, "kill", lambda *a: pytest.fail("signals on Windows"))
    robot_process.kill_by_token(token)
    assert killed == [123, 456]
    assert seen[0][:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    assert "Get-CimInstance Win32_Process" in seen[0][4] and f"'{RUN_TOKEN_ARG}{token}'" in seen[0][4]
    assert "$_.Name -ne 'powershell.exe'" in seen[0][4]   # not a query's PowerShell, whose command line has it too

    monkeypatch.setattr(robot_process.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "x"))
    killed.clear()
    with caplog.at_level(logging.WARNING, logger="scrimmage"):
        robot_process.kill_by_token(token)
    lines = [r.getMessage() for r in caplog.records]
    assert killed == [] and len(lines) == 1 and f"{RUN_TOKEN_ARG}{token}" in lines[0], lines
    assert "may still be running" in lines[0]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="scrimmage"):
        robot_process.kill_by_token(token)
    assert caplog.records == []   # once: where PowerShell is blocked, every stop would say it


def test_kill_by_token_on_windows_puts_only_its_own_tokens_in_the_query(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(robot_process.subprocess, "run", lambda *a, **k: pytest.fail("ran the query"))
    robot_process.kill_by_token("x') ; Stop-Computer ; ('")


def test_on_windows_stop_looks_for_leftovers_only_for_a_gradle_robot():
    assert robot_process.token_on_command_line(["C:/team/gradlew.bat", "-I", "x/scrimmage.init.gradle", "simulateJava"])
    assert robot_process.token_on_command_line(["./gradlew", "simulateJava"])
    assert not robot_process.token_on_command_line(["C:/py/python.exe", "-m", "robotpy", "sim"])


def test_gradlew_runs_as_gradlew_bat_on_windows(tmp_path):
    (tmp_path / "gradlew.bat").write_text("")
    args = ["-I", "x/scrimmage.init.gradle", "simulateJava"]
    assert robot_process.windows_command(["./gradlew", *args], tmp_path) == [str(tmp_path / "gradlew.bat"), *args]
    assert robot_process.windows_command(["gradlew", *args], tmp_path) == [str(tmp_path / "gradlew.bat"), *args]
    assert robot_process.windows_command(["python", "-m", "robotpy"], tmp_path) == ["python", "-m", "robotpy"]
    assert robot_process.windows_command(["./gradlew", *args], tmp_path / "none") == ["./gradlew", *args]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a stand-in gradlew.bat run as a script")
def test_start_on_windows_runs_gradlew_bat_and_joins_the_job(tmp_path, monkeypatch):
    """start() on Windows: `./gradlew` launched as gradlew.bat, then the robot program assigned to the job. Faked
    on Linux (the module sees win32; the stand-in gradlew.bat is a script)."""
    bat = tmp_path / "gradlew.bat"
    bat.write_text(f"#!{sys.executable}\nprint('bat ran')\n")
    bat.chmod(0o755)
    assigned = []
    monkeypatch.setattr(robot_process, "sys", types.SimpleNamespace(platform="win32", executable=sys.executable))
    monkeypatch.setattr(robot_process, "assign_to_job", assigned.append)
    monkeypatch.setattr(robot_process, "kill_tree", lambda pid: None)
    monkeypatch.setattr(robot_process, "kill_by_token", lambda token: None)

    async def main():
        p = RobotProcess(["./gradlew", "simulateJava"], tmp_path)
        await p.start(4321)
        await p.wait_exited(5)
        pid = p.proc.pid
        await p.stop()
        p.close()
        return pid, [l.text for l in p.since(0)]
    pid, lines = asyncio.run(main())
    assert lines == ["bat ran"] and assigned == [pid]


# ---------------------------------------------------------------------------------------------------- real Windows


def _exited(pid: int, wait: float) -> bool:
    """Whether process `pid` has exited (or never existed) within `wait` s."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x00100000, False, pid)   # SYNCHRONIZE
    if not h:
        return True
    try:
        return k32.WaitForSingleObject(h, int(wait * 1000)) == 0   # WAIT_OBJECT_0
    finally:
        k32.CloseHandle(h)


SCRIMMAGE_STAND_IN = """
import asyncio, sys
from scrimmage.robot_process import RobotProcess

ROBOT = ("import os, subprocess, sys, time\\n"
         "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\\n"
         "print(os.getpid(), child.pid, flush=True)\\n"
         "time.sleep(600)\\n")

async def main():
    p = RobotProcess([sys.executable, "-c", ROBOT], ".")
    await p.start(1)
    while not p.tail():
        await asyncio.sleep(0.05)
    print(p.tail()[0], flush=True)
    await asyncio.sleep(600)

asyncio.run(main())
"""


@windows
def test_the_robot_program_and_its_children_die_when_scrimmage_is_killed():
    """A stand-in scrimmage starts a robot program, which starts a child; scrimmage killed outright (TerminateProcess:
    no clean-up runs), Windows closes its job handle and ends both."""
    scrimmage = subprocess.Popen([sys.executable, "-c", SCRIMMAGE_STAND_IN], cwd=ROOT, stdout=subprocess.PIPE,
                                 text=True, env=dict(os.environ, PYTHONPATH=str(ROOT)))
    try:
        robot, child = map(int, scrimmage.stdout.readline().split())
        assert not _exited(robot, 0) and not _exited(child, 0)
    finally:
        scrimmage.kill()
        scrimmage.wait(10)
    assert _exited(robot, 10) and _exited(child, 10)


@windows
def test_kill_by_token_finds_a_process_by_the_token_on_its_command_line():
    token, other = uuid.uuid4().hex, uuid.uuid4().hex
    sleep = [sys.executable, "-c", "import time; time.sleep(600)"]
    mine, theirs = (subprocess.Popen([*sleep, f"{RUN_TOKEN_ARG}{t}"]) for t in (token, other))
    try:
        time.sleep(1)   # let both show up in CIM
        robot_process.kill_by_token(token)
        assert mine.wait(10) is not None
        assert theirs.poll() is None
    finally:
        for p in (mine, theirs):
            p.kill()
            p.wait(10)
