import hashlib
import io
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scrimmage import java_tools
from scrimmage.config import load_config


def _fake_jdk(path: Path, suffix: str) -> Path:
    (path / "bin").mkdir(parents=True)
    for tool in ("java", "javac", "jar"):
        (path / "bin" / (tool + suffix)).write_text("#!/bin/sh\n", encoding="utf-8")
    return path


def test_find_jdk_prefers_java_home(tmp_path, monkeypatch):
    jdk = _fake_jdk(tmp_path / "jdk", ".exe" if sys.platform == "win32" else "")
    monkeypatch.setenv("JAVA_HOME", str(jdk))
    assert java_tools.find_jdk() == jdk


def test_find_jdk_on_windows_looks_for_exe_files(tmp_path, monkeypatch):
    monkeypatch.setattr(java_tools.sys, "platform", "win32")
    monkeypatch.setattr(java_tools, "WPILIB_GLOB", str(tmp_path / "wpilib" / "*" / "jdk"))
    monkeypatch.setenv("JAVA_HOME", str(_fake_jdk(tmp_path / "unix", "")))
    with pytest.raises(java_tools.JavaToolError):
        java_tools.find_jdk()
    jdk = _fake_jdk(tmp_path / "windows", ".exe")
    monkeypatch.setenv("JAVA_HOME", str(jdk))
    assert java_tools.find_jdk() == jdk


def test_find_jdk_reports_where_it_looked(tmp_path, monkeypatch):
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "nope"))
    monkeypatch.setattr(java_tools, "WPILIB_GLOB", str(tmp_path / "wpilib" / "*" / "jdk"))
    with pytest.raises(java_tools.JavaToolError, match=r"JAVA_HOME.*wpilib"):
        java_tools.find_jdk()


def test_scrimmage_java_placeholder(tmp_path):
    p = tmp_path / "r.toml"
    p.write_text('[robot_program]\ncommand = ["./gradlew", "-I", "{scrimmage_java}/scrimmage.init.gradle", "simulateJava"]\n')
    cmd = load_config(p).robot_program.command
    assert cmd[2] == str(java_tools.JAVA_DIR / "scrimmage.init.gradle")


@pytest.mark.java
def test_build_agent_produces_jar_with_premain_and_asm(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))
    jar = java_tools.build_agent()
    assert jar == java_tools.agent_jar_path() and jar.exists()
    with zipfile.ZipFile(jar) as z:
        mf = z.read("META-INF/MANIFEST.MF").decode()
        assert "Premain-Class: scrimmage.agent.ScrimmageAgent" in mf
        assert "Class-Path: asm-9.10.1.jar" in mf
        assert f"Scrimmage-Agent-Version: {java_tools.AGENT_VERSION}" in mf
        assert "scrimmage/agent/ScrimmageRev.class" in z.namelist()
    assert java_tools.agent_missing_message("r.toml", ["./gradlew", "-I", "scrimmage.init.gradle"]) is None
    assert java_tools.asm_jar_path().exists()


@pytest.mark.java
def test_cli_java_build_and_path(tmp_path):
    env = {**os.environ, "SCRIMMAGE_CACHE": str(tmp_path)}
    r = subprocess.run([sys.executable, "-m", "scrimmage", "java", "build"], capture_output=True, text=True,
                       env=env, timeout=300)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[0].endswith("scrimmage-agent.jar")
    assert r.stdout.strip().splitlines()[1].endswith("fastmode-maven")
    r = subprocess.run([sys.executable, "-m", "scrimmage", "java", "path"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().endswith("scrimmage.init.gradle")


def test_fetch_asm_rejects_wrong_sha_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))
    monkeypatch.setattr(java_tools.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"not asm"))
    with pytest.raises(java_tools.JavaToolError, match="sha256"):
        java_tools._fetch_asm()
    assert list(tmp_path.iterdir()) == []


def test_fetch_asm_download_failure_is_actionable(tmp_path, monkeypatch):
    import urllib.error
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))

    def boom(*a, **k):
        raise urllib.error.URLError("offline")
    monkeypatch.setattr(java_tools.urllib.request, "urlopen", boom)
    with pytest.raises(java_tools.JavaToolError, match=r"could not download ASM.*offline.*network"):
        java_tools._fetch_asm()


def test_fetch_asm_accepts_good_bytes_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))
    monkeypatch.setattr(java_tools, "ASM_SHA256", hashlib.sha256(b"asm").hexdigest())
    monkeypatch.setattr(java_tools.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"asm"))
    assert java_tools._fetch_asm().read_bytes() == b"asm"
    assert [p.name for p in tmp_path.iterdir()] == [java_tools.asm_jar_path().name]


def test_path_getters_do_not_create_the_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(cache))
    java_tools.agent_jar_path(), java_tools.asm_jar_path()
    assert not cache.exists()


def test_unwritable_cache_is_a_tool_error(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(blocker / "sub"))
    with pytest.raises(java_tools.JavaToolError, match="SCRIMMAGE_CACHE"):
        java_tools._fetch_asm()


def test_cli_java_build_without_jdk_exits_2(tmp_path):
    env = {**os.environ, "JAVA_HOME": str(tmp_path / "none"), "HOME": str(tmp_path)}
    env.pop("SCRIMMAGE_CACHE", None)
    r = subprocess.run([sys.executable, "-m", "scrimmage", "java", "build"], capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "scrimmage: no JDK found" in r.stderr


def test_fetch_asm_truncated_download_is_a_tool_error(tmp_path, monkeypatch):
    import http.client
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))

    class Truncated:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"par")
    monkeypatch.setattr(java_tools.urllib.request, "urlopen", lambda *a, **k: Truncated())
    with pytest.raises(java_tools.JavaToolError, match="could not download ASM"):
        java_tools._fetch_asm()


def test_written_jars_have_normal_permissions(tmp_path):
    dest = tmp_path / "x.jar"
    old = os.umask(0o022)
    try:
        java_tools._write_atomic(dest, b"x")
    finally:
        os.umask(old)
    assert dest.stat().st_mode & 0o777 == 0o644


def test_agent_missing_message_only_for_java_commands_without_a_built_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))
    java = ["./gradlew", "-I", "/x/scrimmage.init.gradle", "simulateJava"]
    msg = java_tools.agent_missing_message("robot.toml", java)
    assert msg and "scrimmage java build" in msg and "robot.toml" in msg
    assert java_tools.agent_missing_message("robot.toml", ["python", "robot.py"]) is None
    _fake_agent_jar(tmp_path / "scrimmage-agent.jar", java_tools.AGENT_VERSION)
    assert java_tools.agent_missing_message("robot.toml", java) is None


def _fake_agent_jar(path, version) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nPremain-Class: scrimmage.agent.ScrimmageAgent\n"
                   + (f"Scrimmage-Agent-Version: {version}\n" if version is not None else ""))


@pytest.mark.parametrize("version", [None, 1, "garbage"])
def test_agent_built_by_an_older_scrimmage_must_be_rebuilt(tmp_path, monkeypatch, version):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))
    java = ["./gradlew", "-I", "/x/scrimmage.init.gradle", "simulateJava"]
    _fake_agent_jar(tmp_path / "scrimmage-agent.jar", version)
    msg = java_tools.agent_missing_message("robot.toml", java)
    assert msg and "out of date" in msg and "scrimmage java build" in msg and "robot.toml" in msg
    (tmp_path / "scrimmage-agent.jar").write_bytes(b"not a jar")
    assert "out of date" in java_tools.agent_missing_message("robot.toml", java)


def test_agent_missing_message_names_rev_devices_when_the_config_binds_any(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))
    java = ["./gradlew", "-I", "/x/scrimmage.init.gradle", "simulateJava"]
    assert "REV" not in java_tools.agent_missing_message("robot.toml", java)
    msg = java_tools.agent_missing_message("robot.toml", java, rev=True)
    assert "REV SPARK" in msg and "scrimmage java build" in msg


@pytest.mark.parametrize("text,binds", [
    ('[[motor]]\nrev = "SparkMax:3"\njoint = "j"\n', True),
    ('[[encoder]]\njoint = "j"\nrev = "SparkFlexAbs:4"\n', True),
    ('[[part]]\ntype = "arm"\nids = { pivot = "SparkFlex:7" }\n', True),
    ('[[part]]\ntype = "arm"\nids = { pivot = { rev = "SparkMax:7", neutral = "coast" } }\n', True),
    ('[[motor]]\nctre = "TalonFX:3"\njoint = "j"\n[[part]]\ntype = "arm"\nids = { pivot = "PWM:1" }\n', False),
])
def test_binds_rev_reads_bindings_and_part_ids(text, binds):
    import tomllib
    from scrimmage.bindings import binds_rev
    assert binds_rev(tomllib.loads(text)) is binds


def test_session_agent_missing_error_names_rev_devices(tmp_path, monkeypatch):
    from scrimmage.session import Session, SessionError
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    p = tmp_path / "r.toml"
    p.write_text('[robot_program]\ncommand = ["./gradlew", "-I", "{scrimmage_java}/scrimmage.init.gradle", '
                 '"simulateJava"]\n[[motor]]\nrev = "SparkMax:3"\njoint = "j"\n')
    with pytest.raises(SessionError, match="REV SPARK"):
        Session(p)


def _fake_fastmode_maven(cache):
    """A stand-in for what build_fastmode publishes into the cache."""
    d = (cache / "fastmode-maven" / "io" / "github" / "altainia" / "scrimmage" / "scrimmage-fastmode"
         / java_tools.FASTMODE_VERSION)
    d.mkdir(parents=True)
    (d / f"scrimmage-fastmode-{java_tools.FASTMODE_VERSION}.jar").write_bytes(b"jar")
    (d / f"scrimmage-fastmode-{java_tools.FASTMODE_VERSION}.pom").write_text("<project/>")


def _robot_project(path):
    """A stand-in GradleRIO robot project folder."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "build.gradle").write_text('plugins { id "edu.wpi.first.GradleRIO" }\n')
    return path


def test_vendordep_json_and_maven_folder(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    _fake_fastmode_maven(tmp_path / "cache")
    project = _robot_project(tmp_path / "robot")
    out = java_tools.write_vendordep(project)
    assert out == project / "vendordeps" / "Scrimmage.json"
    assert out.read_text().startswith('{\n  "fileName": "Scrimmage.json",\n')   # as WPILib's Spotless formats it
    assert json.loads(out.read_text()) == {
        "fileName": "Scrimmage.json",
        "name": "Scrimmage",
        "version": "0.4.0",
        "uuid": "312a1138-d262-41fe-9cca-bc05120dfa6b",
        "frcYear": "2026",
        "mavenUrls": ["scrimmage-maven"],
        "jsonUrl": "",
        "javaDependencies": [{"groupId": "io.github.altainia.scrimmage", "artifactId": "scrimmage-fastmode",
                              "version": "0.4.0"}],
        "jniDependencies": [],
        "cppDependencies": [],
    }
    jars = list(project.glob("scrimmage-maven/io/github/altainia/scrimmage/scrimmage-fastmode/0.4.0/*.jar"))
    assert [j.name for j in jars] == ["scrimmage-fastmode-0.4.0.jar"]


def test_vendordep_replaces_an_older_maven_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    _fake_fastmode_maven(tmp_path / "cache")
    stale = tmp_path / "robot" / "scrimmage-maven" / "io" / "old.jar"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    java_tools.write_vendordep(_robot_project(tmp_path / "robot"))
    assert not stale.exists()
    assert sorted(p.name for p in (tmp_path / "robot").iterdir()) == ["build.gradle", "scrimmage-maven", "vendordeps"]


def test_a_failed_vendordep_copy_leaves_the_older_maven_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    _fake_fastmode_maven(tmp_path / "cache")
    kept = _robot_project(tmp_path / "robot") / "scrimmage-maven" / "io" / "old.jar"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"old")

    def full_disk(src, dst, **kw):
        Path(dst).mkdir()
        (Path(dst) / "half").write_bytes(b"")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(java_tools.shutil, "copytree", full_disk)
    with pytest.raises(java_tools.JavaToolError, match="No space left"):
        java_tools.write_vendordep(tmp_path / "robot")
    assert kept.read_bytes() == b"old"
    assert sorted(p.name for p in (tmp_path / "robot").iterdir()) == ["build.gradle", "scrimmage-maven"]


def test_vendordep_refuses_a_folder_without_build_gradle(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    _fake_fastmode_maven(tmp_path / "cache")
    (tmp_path / "src").mkdir()
    with pytest.raises(java_tools.JavaToolError, match="no build.gradle"):
        java_tools.write_vendordep(tmp_path / "src")
    assert list((tmp_path / "src").iterdir()) == []


def test_vendordep_without_a_built_library_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    (tmp_path / "robot").mkdir()
    with pytest.raises(java_tools.JavaToolError, match="scrimmage java build"):
        java_tools.write_vendordep(tmp_path / "robot")
    assert not (tmp_path / "robot" / "vendordeps").exists()


def test_cli_java_vendordep(tmp_path):
    _fake_fastmode_maven(tmp_path / "cache")
    _robot_project(tmp_path / "robot")
    env = {**os.environ, "SCRIMMAGE_CACHE": str(tmp_path / "cache")}
    r = subprocess.run([sys.executable, "-m", "scrimmage", "java", "vendordep", str(tmp_path / "robot")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().endswith("Scrimmage.json")
    assert (tmp_path / "robot" / "scrimmage-maven").is_dir()


def test_cli_java_vendordep_without_a_project_exits_2(tmp_path):
    _fake_fastmode_maven(tmp_path / "cache")
    env = {**os.environ, "SCRIMMAGE_CACHE": str(tmp_path / "cache")}
    r = subprocess.run([sys.executable, "-m", "scrimmage", "java", "vendordep", str(tmp_path / "nope")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "scrimmage:" in r.stderr and "nope" in r.stderr


@pytest.mark.java
def test_build_fastmode_publishes_the_library(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path))
    repo = java_tools.build_fastmode()
    assert repo == tmp_path / "fastmode-maven"
    d = repo / "io/github/altainia/scrimmage/scrimmage-fastmode" / java_tools.FASTMODE_VERSION
    jar = d / f"scrimmage-fastmode-{java_tools.FASTMODE_VERSION}.jar"
    assert (d / f"scrimmage-fastmode-{java_tools.FASTMODE_VERSION}.pom").exists()
    with zipfile.ZipFile(jar) as z:
        names = z.namelist()
    assert "scrimmage/sim/ScrimmageKraken.class" in names
    assert not any("ctre" in n.lower() or "phoenix" in n.lower() for n in names)


def test_grapple_helper_constants_match_the_runtime():
    """The Java helper's status values are the ones scrimmage writes (scrimmage/sensors/runtime.py), and its reaches
    the checks file's LaserCAN reaches (the long one is a LaserCAN sensor's default max_range)."""
    from scrimmage.sensors import runtime
    from scrimmage.sensors.spec import DEVICE_MAX_RANGE
    src = (Path(java_tools.__file__).parent / "java" / "agent" / "src" / "scrimmage" / "agent"
           / "ScrimmageGrapple.java").read_text()
    consts = {k: int(v) for k, v in re.findall(r"static final int (\w+) = (\d+);", src)}
    assert consts["STATUS_VALID"] == runtime.LASERCAN_STATUS_VALID
    assert consts["STATUS_NO_TARGET"] == runtime.LASERCAN_STATUS_NO_TARGET
    assert consts["SHORT_REACH_MM"] == 1300
    assert consts["LONG_REACH_MM"] == round(DEVICE_MAX_RANGE["LaserCAN"] * 1000) == 4000
