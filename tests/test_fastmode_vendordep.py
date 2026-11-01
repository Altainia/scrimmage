"""The fast-mode vendordep: a GradleRIO robot project resolves the library from the committed folder its
`mavenUrls` names by relative path."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from scrimmage import java_tools

ROOT = Path(__file__).resolve().parent.parent
JAVA_SWERVE = ROOT / "examples" / "java_swerve"
FASTMODE_SAMPLE = ROOT / "examples" / "java_fastmode"
LIBRARY_SOURCES = ROOT / "scrimmage" / "java" / "fastmode" / "src" / "main" / "java"


def test_sample_library_is_the_current_source():
    """The sample's committed library (scrimmage-maven) must be the library as it is now: after changing
    scrimmage/java/fastmode, run `scrimmage java build` and `scrimmage java vendordep examples/java_fastmode`."""
    v = java_tools.FASTMODE_VERSION
    folder = FASTMODE_SAMPLE.joinpath("scrimmage-maven", *java_tools.FASTMODE_GROUP.split("."),
                                      java_tools.FASTMODE_ARTIFACT, v)
    jar = folder / f"{java_tools.FASTMODE_ARTIFACT}-{v}-sources.jar"
    assert jar.is_file(), f"no {jar}: re-vendor the sample for version {v}"
    assert (folder / f"{java_tools.FASTMODE_ARTIFACT}-{v}.jar").is_file()
    with zipfile.ZipFile(jar) as z:
        committed = {n: z.read(n) for n in z.namelist() if not n.endswith("/") and not n.startswith("META-INF/")}
    current = {p.relative_to(LIBRARY_SOURCES).as_posix(): p.read_bytes()
               for p in LIBRARY_SOURCES.rglob("*") if p.is_file()}
    assert sorted(committed) == sorted(current)
    assert [n for n in current if committed[n] != current[n]] == []


@pytest.mark.java
def test_gradlerio_resolves_committed_vendordep(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    java_tools.build_fastmode()
    project = tmp_path / "robot"
    shutil.copytree(JAVA_SWERVE, project, ignore=shutil.ignore_patterns("build", ".gradle"))
    java_tools.write_vendordep(project)
    robot = project / "src" / "main" / "java" / "frc" / "robot" / "Robot.java"
    text = robot.read_text()
    anchor = "    m_robotContainer = new RobotContainer();\n"
    assert anchor in text
    robot.write_text(text.replace(anchor, anchor + "    System.out.println(scrimmage.sim.Scrimmage.fastMode());\n"))
    r = subprocess.run(["./gradlew", "build"], cwd=project, env=dict(os.environ, JAVA_HOME=str(java_tools.find_jdk())),
                       capture_output=True, text=True, timeout=900)  # a cold Gradle cache needs the network
    assert r.returncode == 0, "gradlew build failed:\n" + (r.stdout + r.stderr)[-3000:]
