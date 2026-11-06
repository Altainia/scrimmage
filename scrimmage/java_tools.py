"""Java-side tooling: find the WPILib JDK, build the sim-write guard agent, and locate the Gradle init script."""

from __future__ import annotations

import glob
import hashlib
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import i18n

JAVA_DIR = Path(__file__).resolve().parent / "java"
WPILIB_GLOB = str(Path.home() / "wpilib" / "*" / "jdk")
ASM_VERSION = "9.10.1"
ASM_URL = f"https://repo1.maven.org/maven2/org/ow2/asm/asm/{ASM_VERSION}/asm-{ASM_VERSION}.jar"
ASM_SHA256 = "ed825d10ab1399c8c0cb669e688cf0c8c82629b4c8399b58352b68e92ca10fcb"
# Bumped whenever the agent's behavior changes, so a jar built by an older scrimmage is rebuilt.
# 2: the REV SPARK helper (ScrimmageRev).
# 3: Phoenix classes are never modified (CTRE license); robot code's calls to their sim setters are rewritten instead.
AGENT_VERSION = 3
# The fast-mode library (scrimmage/java/fastmode), published to a file Maven repository a vendordep points at.
FASTMODE_VERSION = "0.3.1"
FASTMODE_GROUP = "io.github.altainia.scrimmage"
FASTMODE_ARTIFACT = "scrimmage-fastmode"
VENDORDEP_UUID = "312a1138-d262-41fe-9cca-bc05120dfa6b"
VENDORDEP_MAVEN_DIR = "scrimmage-maven"


class JavaToolError(RuntimeError):
    pass


def _cache_dir() -> Path:
    base = os.environ.get("SCRIMMAGE_CACHE")
    return Path(base) if base else Path.home() / ".cache" / "scrimmage" / "java"


def _ensure_cache_dir() -> Path:
    d = _cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise JavaToolError(f"can't create the cache directory {d}: {e.strerror or e}. "
                            "Set SCRIMMAGE_CACHE to a writable directory.") from None
    return d


def _write_atomic(dest: Path, data: bytes) -> None:
    """Write via a temp file in the same directory, then rename, so no partial file ever sits at dest."""
    try:
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o644 & ~umask)  # mkstemp makes 0600
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError as e:
        raise JavaToolError(f"can't write {dest}: {e.strerror or e}") from None


def agent_jar_path() -> Path:
    return _cache_dir() / "scrimmage-agent.jar"


def agent_jar_version(jar: Path) -> int | None:
    """The Scrimmage-Agent-Version in a built agent jar's manifest (None if unreadable or absent: built before 2)."""
    try:
        with zipfile.ZipFile(jar) as z:
            mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    for line in mf.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "Scrimmage-Agent-Version":
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def agent_missing_message(config_path, command: list[str], rev: bool = False) -> str | None:
    """Why a robot program command can't run: it goes through the scrimmage Gradle init script (a Java robot
    program) but the Java agent isn't built, or was built by an older scrimmage. None when the command isn't a
    Java one or the agent is current. `rev`: the config binds REV SPARKs, which only simulate through the agent."""
    if not any("scrimmage.init.gradle" in c for c in command):
        return None
    jar = agent_jar_path()
    if not jar.exists():
        if rev:
            return i18n.t("{config} runs a Java robot program with REV SPARKs (they only simulate through the "
                          "agent), but the scrimmage Java agent isn't built (missing {jar}): run "
                          "`{scrimmage_java_build}` (needs the WPILib JDK)", config=config_path, jar=jar,
                          scrimmage_java_build="scrimmage java build")
        return i18n.t("{config} runs a Java robot program, but the scrimmage Java agent isn't built (missing {jar}): "
                      "run `{scrimmage_java_build}` (needs the WPILib JDK)", config=config_path, jar=jar,
                      scrimmage_java_build="scrimmage java build")
    if agent_jar_version(jar) != AGENT_VERSION:
        if rev:
            return i18n.t("{config} runs a Java robot program with REV SPARKs (they only simulate through the "
                          "agent), but the scrimmage Java agent at {jar} is out of date (built by an older "
                          "scrimmage): run `{scrimmage_java_build}` (needs the WPILib JDK)", config=config_path,
                          jar=jar, scrimmage_java_build="scrimmage java build")
        return i18n.t("{config} runs a Java robot program, but the scrimmage Java agent at {jar} is out of date "
                      "(built by an older scrimmage): run `{scrimmage_java_build}` (needs the WPILib JDK)",
                      config=config_path, jar=jar, scrimmage_java_build="scrimmage java build")
    return None


def asm_jar_path() -> Path:
    return _cache_dir() / f"asm-{ASM_VERSION}.jar"


def init_script_path() -> Path:
    return JAVA_DIR / "scrimmage.init.gradle"


def _is_jdk(p: Path) -> bool:
    return all((p / "bin" / t).exists() for t in ("java", "javac", "jar"))


def find_jdk() -> Path:
    tried = []
    home = os.environ.get("JAVA_HOME")
    if home:
        tried.append(f"JAVA_HOME={home}")
        if _is_jdk(Path(home)):
            return Path(home)
    for cand in sorted(glob.glob(WPILIB_GLOB), reverse=True):  # newest WPILib year first
        tried.append(cand)
        if _is_jdk(Path(cand)):
            return Path(cand)
    tried.append(WPILIB_GLOB)
    raise JavaToolError("no JDK found (looked at: " + ", ".join(tried) + "). Install WPILib "
                        "(https://docs.wpilib.org) or set JAVA_HOME to a JDK 17.")


def _fetch_asm() -> Path:
    _ensure_cache_dir()
    dest = asm_jar_path()
    try:
        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() == ASM_SHA256:
            return dest
    except OSError:
        pass  # unreadable cached copy: fetch a fresh one
    try:
        with urllib.request.urlopen(ASM_URL, timeout=60) as r:
            data = r.read()
    except (OSError, ValueError, http.client.HTTPException) as e:  # URLError/HTTPError/TimeoutError are OSErrors
        raise JavaToolError(f"could not download ASM from {ASM_URL}: {getattr(e, 'reason', None) or e}. "
                            f"Check your network, or place {dest.name} at {dest}") from None
    digest = hashlib.sha256(data).hexdigest()
    if digest != ASM_SHA256:
        raise JavaToolError(f"downloaded ASM has sha256 {digest}, expected {ASM_SHA256}")
    _write_atomic(dest, data)
    return dest


def build_agent(jdk: Path | None = None) -> Path:
    """Compile the agent against ASM and write the jar (with a Class-Path to ASM beside it) to the cache."""
    jdk = jdk or find_jdk()
    asm = _fetch_asm()
    src = sorted((JAVA_DIR / "agent" / "src").rglob("*.java"))
    with tempfile.TemporaryDirectory() as tmp:
        classes = Path(tmp) / "classes"
        classes.mkdir()
        _run([str(jdk / "bin" / "javac"), "--release", "17", "-cp", str(asm), "-d", str(classes), *map(str, src)])
        mf = Path(tmp) / "MANIFEST.MF"
        mf.write_text(f"Premain-Class: scrimmage.agent.ScrimmageAgent\nClass-Path: {asm.name}\n"
                      f"Scrimmage-Agent-Version: {AGENT_VERSION}\n")
        built = Path(tmp) / "agent.jar"
        _run([str(jdk / "bin" / "jar"), "cfm", str(built), str(mf), "-C", str(classes), "."])
        out = agent_jar_path()
        _write_atomic(out, built.read_bytes())
    return out


def fastmode_maven_path() -> Path:
    return _cache_dir() / "fastmode-maven"


def _fastmode_published(repo: Path) -> Path:
    return repo.joinpath(*FASTMODE_GROUP.split("."), FASTMODE_ARTIFACT, FASTMODE_VERSION)


def build_fastmode(jdk: Path | None = None) -> Path:
    """Build the fast-mode library and publish it as a file Maven repository in the cache; returns that folder.
    Gradle runs on a temporary copy of the project, so an installed scrimmage's own files are never written."""
    jdk = jdk or find_jdk()
    _ensure_cache_dir()
    out = fastmode_maven_path()
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "fastmode"
        shutil.copytree(JAVA_DIR / "fastmode", proj, ignore=shutil.ignore_patterns("build", ".gradle"))
        repo = Path(tmp) / "repo"
        _run(["sh", str(proj / "gradlew"), "--no-daemon", "-q", "-p", str(proj), "publish",
              f"-PlibVersion={FASTMODE_VERSION}", f"-PpublishDir={repo}"],
             env={**os.environ, "JAVA_HOME": str(jdk)})
        if not _fastmode_published(repo).is_dir():
            raise JavaToolError(f"gradlew publish wrote nothing to {_fastmode_published(repo)}")
        try:
            _replace_dir(repo, out)
        except OSError as e:
            raise JavaToolError(f"can't write {out}: {e.strerror or e}") from None
    return out


def _replace_dir(src: Path, dest: Path) -> None:
    """Make `dest` a copy of `src`. The copy is made beside it first, so a failed copy leaves `dest` as it was."""
    new, old = dest.with_name(f".{dest.name}.new"), dest.with_name(f".{dest.name}.old")
    for d in (new, old):
        shutil.rmtree(d, ignore_errors=True)
    try:
        shutil.copytree(src, new)
    except OSError:
        shutil.rmtree(new, ignore_errors=True)
        raise
    if dest.exists():
        dest.rename(old)
    new.rename(dest)
    shutil.rmtree(old, ignore_errors=True)


def vendordep_json() -> dict:
    """The WPILib vendordep for the fast-mode library. Its Maven repository is a folder committed in the robot
    project (`mavenUrls` is relative: GradleRIO registers it as a Gradle Maven repository, which Gradle resolves
    against the project folder), so no computer needs scrimmage installed to build the robot."""
    return {
        "fileName": "Scrimmage.json",
        "name": "Scrimmage",
        "version": FASTMODE_VERSION,
        "uuid": VENDORDEP_UUID,
        "frcYear": "2026",
        "mavenUrls": [VENDORDEP_MAVEN_DIR],
        "jsonUrl": "",
        "javaDependencies": [{"groupId": FASTMODE_GROUP, "artifactId": FASTMODE_ARTIFACT,
                              "version": FASTMODE_VERSION}],
        "jniDependencies": [],
        "cppDependencies": [],
    }


def write_vendordep(project: Path) -> Path:
    """Copy the built library's Maven repository into `<project>/scrimmage-maven/` (replacing any older copy) and
    write `<project>/vendordeps/Scrimmage.json`; returns the JSON's path."""
    project = Path(project)
    src = fastmode_maven_path()
    if not _fastmode_published(src).is_dir():
        raise JavaToolError(f"the fast-mode library {FASTMODE_VERSION} isn't built (missing "
                            f"{_fastmode_published(src)}): run `scrimmage java build` (needs the WPILib JDK)")
    if not project.is_dir():
        raise JavaToolError(f"{project} isn't a robot project folder")
    if not any((project / f).is_file() for f in ("build.gradle", "build.gradle.kts")):
        raise JavaToolError(f"{project} isn't a GradleRIO robot project (no build.gradle): give the folder that "
                            "holds your robot's build.gradle")
    dest = project / VENDORDEP_MAVEN_DIR
    out = project / "vendordeps" / "Scrimmage.json"
    try:
        _replace_dir(src, dest)
        out.parent.mkdir(exist_ok=True)
        # Two-space indent: what WPILib's Spotless setup (gson, 2 spaces) formats it to, so a build leaves it alone.
        out.write_text(json.dumps(vendordep_json(), indent=2) + "\n")
    except OSError as e:
        raise JavaToolError(f"can't write the vendordep into {project}: {e.strerror or e}") from None
    return out


def _run(cmd: list[str], env: dict | None = None) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    except OSError as e:
        raise JavaToolError(f"can't run {cmd[0]}: {e.strerror or e}") from None
    if r.returncode != 0:
        raise JavaToolError(f"{Path(cmd[0]).name} failed:\n{r.stdout}{r.stderr}")
