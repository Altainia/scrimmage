import asyncio
import os
import sys
import time

import pytest

import subprocess
import uuid

from scrimmage.robot_process import RobotProcess, kill_by_token


def proc(code: str, **kw) -> RobotProcess:
    return RobotProcess([sys.executable, "-c", code], ".", **kw)


def alive(pid: int) -> bool:
    """False for a missing process or a zombie (exited, not yet reaped)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError, IndexError):
        return False
    return state != "Z"


def test_captures_lines_with_clock_and_env(tmp_path):
    async def main():
        t = [1.5]
        log = tmp_path / "robot.log"
        p = proc("import os; print('hello'); print(os.environ['HALSIMWS_PORT'])", clock=lambda: t[0], log_path=log)
        await p.start(4321)
        for _ in range(100):
            if p.returncode is not None and p.next_index >= 2:
                break
            await asyncio.sleep(0.02)
        await p.stop()
        p.close()
        assert [(l.t, l.text) for l in p.since(0)] == [(1.5, "hello"), (1.5, "4321")]
        assert p.returncode == 0 and not p.running
        assert "[    1.500] hello" in log.read_text()
    asyncio.run(main())


def test_stop_kills_the_process_group():
    async def main():
        p = proc("import subprocess; c = subprocess.Popen(['sleep', '60']); print(c.pid, flush=True); c.wait()")
        await p.start(1)
        for _ in range(100):
            if p.next_index:
                break
            await asyncio.sleep(0.02)
        child = int(p.tail(1)[0])
        await p.stop()
        for _ in range(100):
            if not alive(child):
                return
            await asyncio.sleep(0.02)
        pytest.fail("grandchild still alive after stop()")
    asyncio.run(main())


def test_stop_kills_children_after_leader_exits():
    async def main():
        p = proc("import subprocess; c = subprocess.Popen(['sleep', '60']); print(c.pid, flush=True)")
        await p.start(1)
        t0 = time.monotonic()
        while p.returncode is None and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.02)
        assert p.returncode == 0
        child = int(p.tail(1)[0])
        assert alive(child)
        await p.stop()
        for _ in range(100):
            if not alive(child):
                return
            await asyncio.sleep(0.02)
        pytest.fail("child of an exited leader survived stop()")
    asyncio.run(main())


def test_long_line_is_truncated_and_does_not_hang_stop():
    async def main():
        p = proc("import sys; sys.stdout.write('x' * 200000 + '\\n'); print('after')")
        await p.start(1)
        t0 = time.monotonic()
        while p.returncode is None and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.02)
        t1 = time.monotonic()
        await p.stop()
        p.close()
        assert time.monotonic() - t1 < 3
        lines = [l.text for l in p.since(0)]
        assert len(lines) == 2
        assert lines[0].startswith("xxx")
        assert lines[0].endswith(" [line truncated]")
        assert len(lines[0]) <= 8192 + len(" [line truncated]")
        assert lines[1] == "after"
    asyncio.run(main())


def test_config_env_cannot_override_host_or_port():
    async def main():
        p = proc("import os; print(os.environ['HALSIMWS_PORT'])",
                 env={"PYTHONUNBUFFERED": "0", "HALSIMWS_PORT": "9"})
        await p.start(4321)
        t0 = time.monotonic()
        while p.returncode is None and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.02)
        await p.stop()
        assert [l.text for l in p.since(0)] == ["4321"]
    asyncio.run(main())


def test_double_start_raises():
    async def main():
        p = proc("import time; time.sleep(60)")
        await p.start(1)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                await p.start(1)
        finally:
            await p.stop()
    asyncio.run(main())


def test_bad_command_names_command_and_cwd():
    async def main():
        p = RobotProcess(["/nonexistent/robot"], "/tmp")
        with pytest.raises(RuntimeError, match=r"/nonexistent/robot.*in /tmp"):
            await p.start(1)
    asyncio.run(main())


def test_robot_dies_with_its_parent(tmp_path):
    """Even kill -9 of scrimmage (e.g. an MCP client killing its server) must not orphan the robot program."""
    import subprocess
    script = tmp_path / "parent.py"
    script.write_text(
        "import asyncio, sys\n"
        "from scrimmage.robot_process import RobotProcess\n"
        "async def main():\n"
        "    p = RobotProcess([sys.executable, '-c', 'import os, time; print(os.getpid(), flush=True); time.sleep(60)'], '.')\n"
        "    await p.start(1)\n"
        "    while not p.next_index:\n"
        "        await asyncio.sleep(0.02)\n"
        "    print(p.tail(1)[0], flush=True)\n"
        "    await asyncio.sleep(60)\n"
        "asyncio.run(main())\n")
    parent = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    parent.kill()
    parent.wait()
    for _ in range(100):
        if not alive(child):
            return
        time.sleep(0.02)
    pytest.fail("robot program outlived its parent")


def test_since_and_tail():
    async def main():
        p = proc("for i in range(5): print(i)")
        await p.start(1)
        t0 = time.monotonic()
        while p.next_index < 5 and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.02)
        await p.stop()
        assert [l.text for l in p.since(3)] == ["3", "4"]
        assert p.tail(2) == ["3", "4"]
    asyncio.run(main())


def test_second_stop_signals_nothing(monkeypatch):
    async def main():
        p = proc("import time; time.sleep(60)")
        await p.start(1)
        await p.stop()
        assert p.proc is None and p.returncode == -9 and not p.running  # the exit code outlives the handle
        calls = []
        monkeypatch.setattr(os, "killpg", lambda *a: calls.append(a))
        await p.stop()  # the old pid may belong to someone else by now
        assert calls == [] and p.returncode == -9
    asyncio.run(main())


def test_kill_by_token_kills_matching_processes_only():
    token = uuid.uuid4().hex
    mine = subprocess.Popen(["sleep", "60"], env={**os.environ, "SCRIMMAGE_RUN_TOKEN": token})
    other = subprocess.Popen(["sleep", "60"], env={**os.environ, "SCRIMMAGE_RUN_TOKEN": "x" + token})
    try:
        kill_by_token(token, timeout=5)
        assert mine.wait(timeout=5) != 0
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()
        mine.kill()


def test_stop_kills_a_grandchild_in_another_session(tmp_path):
    """Like a robot JVM started by a Gradle daemon: not in the robot program's process group."""
    pidfile = tmp_path / "grandchild.pid"
    code = ("import subprocess, time; "
            f"p = subprocess.Popen(['sleep', '60'], start_new_session=True); open({str(pidfile)!r}, 'w').write(str(p.pid)); "
            "time.sleep(60)")

    async def main():
        p = proc(code)
        await p.start(4321)
        for _ in range(200):
            if pidfile.exists() and pidfile.read_text():
                break
            await asyncio.sleep(0.05)
        pid = int(pidfile.read_text())
        assert alive(pid)
        await p.stop()
        for _ in range(100):
            if not alive(pid):
                break
            await asyncio.sleep(0.05)
        assert not alive(pid)
    asyncio.run(main())


def test_start_passes_scrimmage_paths_but_keeps_config_values(monkeypatch):
    from scrimmage import robot_process
    monkeypatch.setattr(robot_process, "_scrimmage_paths",
                        lambda: {"SCRIMMAGE_AGENT_JAR": "/default.jar", "SCRIMMAGE_LOCKSTEP_SO": "/default.so"})

    async def main():
        p = RobotProcess(["sh", "-c", "echo $SCRIMMAGE_AGENT_JAR $SCRIMMAGE_LOCKSTEP_SO"], ".",
                         {"SCRIMMAGE_AGENT_JAR": "/mine.jar"})
        await p.start(1)
        await p.wait_exited(5)
        assert p.tail() == ["/mine.jar /default.so"]
        await p.stop()
    asyncio.run(main())


def test_scrimmage_paths_skip_a_missing_so_and_replace_a_bad_java_home(monkeypatch, tmp_path):
    from scrimmage import robot_process, java_tools
    import scrimmage_lockstep
    monkeypatch.setattr(scrimmage_lockstep, "library_path", lambda: tmp_path / "missing.so")
    assert "SCRIMMAGE_LOCKSTEP_SO" not in robot_process._scrimmage_paths()
    jdk = tmp_path / "jdk"
    monkeypatch.setattr(java_tools, "find_jdk", lambda: jdk)
    monkeypatch.setenv("JAVA_HOME", "/not/a/jdk")

    async def main():
        out = []
        for cfg_env in ({}, {"JAVA_HOME": "/mine"}):
            p = RobotProcess(["sh", "-c", "echo $JAVA_HOME"], ".", cfg_env)
            await p.start(1)
            await p.wait_exited(5)
            out.append(p.tail()[0])
            await p.stop()
        assert out == [str(jdk), "/mine"]
    asyncio.run(main())


def test_agent_state_is_loaded_disabled_or_missing():
    from scrimmage.robot_process import RobotProcess
    r = RobotProcess(["true"], ".")
    assert r.agent_state() == "missing"
    r.agent_status = "loaded"
    assert r.agent_state() == "loaded"


def test_rev_state_comes_from_the_agents_rev_unavailable_line():
    from scrimmage.robot_process import RobotProcess
    r = RobotProcess(["true"], ".")
    assert r.rev_state is None
    r._add(b"scrimmage agent: loaded")
    r._add(b"scrimmage agent: REV sim unavailable: no com.revrobotics.spark.SparkSim.iterate(double, double, double)")
    assert r.rev_state == "unavailable: no com.revrobotics.spark.SparkSim.iterate(double, double, double)"
    assert r.agent_state() == "loaded"


def test_robotpy_rev_helper_lines_fill_the_same_fields_as_the_java_agent():
    from scrimmage.robot_process import RobotProcess
    r = RobotProcess(["true"], ".")
    r._add(b"scrimmage rev helper: loaded (python 0.2.0)")
    assert r.agent_state() == "loaded"
    r._add(b"scrimmage rev helper: ignored 4 robot-side sim writes (SparkSim.iterate from robot.MyRobot._simulationPeriodic)")
    assert r.agent_summary == "scrimmage rev helper: ignored 4 robot-side sim writes " \
        "(SparkSim.iterate from robot.MyRobot._simulationPeriodic)"
    assert r.rev_state is None
    r._add(b"scrimmage rev helper: REV sim unavailable: robotpy-rev has no SparkSim.iterate")
    assert r.rev_state == "unavailable: robotpy-rev has no SparkSim.iterate"
    assert r.agent_state() == "loaded"


ROBOTPY_LOCKSTEP = ["python", "-m", "robotpy", "sim", "--ws-client", "--scrimmage-lockstep"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the seed library is Linux-only")
def test_seed_library_is_preloaded_only_for_robotpy_lockstep_robots():
    scrimmage_lockstep = pytest.importorskip("scrimmage_lockstep")
    from scrimmage.robot_process import seed_preload
    seed = seed_preload(ROBOTPY_LOCKSTEP)
    assert seed == str(scrimmage_lockstep.seed_library_path()) and os.path.exists(seed)
    assert seed_preload(ROBOTPY_LOCKSTEP[:-1]) is None                          # RobotPy in real time
    assert seed_preload(["./gradlew", "simulateJava", "--scrimmage-lockstep"]) is None  # not RobotPy


def test_seed_library_goes_before_a_configured_ld_preload(monkeypatch):
    import scrimmage.robot_process as rp_mod
    monkeypatch.setattr(rp_mod, "seed_preload", lambda command: "/scrimmage/seed.so")

    async def main():
        p = proc("import os; print('LD=' + os.environ['LD_PRELOAD'])", env={"LD_PRELOAD": "/team/own.so"})
        await p.start(4321)
        t0 = time.monotonic()
        while p.returncode is None and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.02)
        await p.stop()
        return [l.text for l in p.since(0) if l.text.startswith("LD=")]
    assert asyncio.run(main()) == ["LD=/scrimmage/seed.so:/team/own.so"]


def test_a_java_robot_console_is_read_from_its_file_as_it_is_written(tmp_path):
    """Gradle passes a task's output on in batches; the init script writes the robot's to SCRIMMAGE_CONSOLE_FILE instead,
    which scrimmage reads as it grows. A stand-in gradlew writes it the way Gradle's output pump does."""
    gradlew = tmp_path / "gradlew"
    gradlew.write_text(f"#!{sys.executable}\n"
                       "import os, sys, time\n"
                       "print('> Task :simulateJavaRelease', flush=True)\n"
                       "with open(os.environ['SCRIMMAGE_CONSOLE_FILE'], 'a') as f:\n"
                       "    f.write('robot line 1\\n'); f.flush()\n"
                       "    time.sleep(0.3)\n"
                       "    f.write('robot line 2\\nno newline'); f.flush()\n"
                       "    sys.stdin.read()\n")
    gradlew.chmod(0o755)

    async def main():
        p = RobotProcess([str(gradlew), "simulateJava"], tmp_path)
        await p.start(4321)
        path = p._console_file
        assert path is not None and path.exists()
        t0 = time.monotonic()
        while "robot line 2" not in [l.text for l in p.lines]:
            assert time.monotonic() - t0 < 3, p.tail()
            await asyncio.sleep(0.01)
        assert time.monotonic() - t0 < 1.0          # as it comes, not batched
        await p.stop()
        p.close()
        assert sorted(l.text for l in p.since(0)) == sorted(
            ["> Task :simulateJavaRelease", "robot line 1", "robot line 2", "no newline"])
        assert not path.exists()
    asyncio.run(main())


def test_a_python_robot_gets_no_console_file(tmp_path):
    p = proc("import os; print(os.environ.get('SCRIMMAGE_CONSOLE_FILE'))")

    async def main():
        await p.start(4321)
        await p.wait_exited(5)
        await p.stop()
        p.close()
    asyncio.run(main())
    assert [l.text for l in p.since(0)] == ["None"]


def test_grapple_state_comes_from_the_lasercan_helpers_unavailable_line():
    from scrimmage.robot_process import RobotProcess
    r = RobotProcess(["true"], ".")
    r._add(b"scrimmage grapple helper: loaded")
    assert r.grapple_state is None and r.agent_state() == "missing", "the LaserCAN helper isn't the REV agent"
    r._add(b"scrimmage grapple helper: unavailable: no libgrapplefrc.LaserCAN")
    assert r.grapple_state == "unavailable: no libgrapplefrc.LaserCAN" and r.rev_state is None
