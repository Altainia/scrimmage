"""The prebuilt Windows lockstep extension (lockstep/scrimmage_lockstep/lib/win_amd64) and its tools: the stamp says
which source the committed DLL was built from, and changing the C++ without refreshing the DLL fails here, on every
platform. The update script takes the DLL only from CI's run on the commit checked out (gh faked here)."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "lockstep" / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(f"lockstep_tools_{name}", TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


stamp = load("stamp")
REGENERATE = ("to refresh it, push this commit, wait for CI's windows job, then run "
              "`python lockstep/tools/update_windows_dll.py` and commit lockstep/scrimmage_lockstep/lib/win_amd64")


def test_the_committed_windows_dll_was_built_from_this_source():
    dll = stamp.DLL_DIR / stamp.DLL_NAME
    assert dll.is_file(), f"the prebuilt Windows lockstep extension is missing: {REGENERATE}"
    assert stamp.read_stamp() == stamp.source_hash(), (
        f"the prebuilt Windows lockstep extension was built from other source than lockstep/'s (src/"
        f"scrimmage_lockstep.cpp or CMakeLists.txt changed since): {REGENERATE}")


def copy_sources(dest: Path, crlf: bool = False) -> Path:
    for rel in stamp.SOURCES:
        data = (stamp.LOCKSTEP / rel).read_bytes()
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes(data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n") if crlf else data)
    return dest


def test_the_stamp_reads_crlf_as_lf_and_sees_any_change(tmp_path):
    lf, crlf = copy_sources(tmp_path / "lf"), copy_sources(tmp_path / "crlf", crlf=True)
    assert (crlf / "CMakeLists.txt").read_bytes() != (lf / "CMakeLists.txt").read_bytes()
    assert stamp.source_hash(crlf) == stamp.source_hash(lf) == stamp.source_hash()
    cpp = lf / "src" / "scrimmage_lockstep.cpp"
    cpp.write_bytes(cpp.read_bytes() + b"// a comment\n")
    assert stamp.source_hash(lf) != stamp.source_hash()


def test_the_stamp_cli_prints_and_writes(tmp_path):
    r = subprocess.run([sys.executable, str(TOOLS / "stamp.py")], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.strip() == stamp.source_hash()
    r = subprocess.run([sys.executable, str(TOOLS / "stamp.py"), "--write", str(tmp_path)], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0
    assert (tmp_path / "SOURCE.sha256").read_bytes() == f"{stamp.source_hash()}\n".encode()
    assert stamp.read_stamp(tmp_path) == stamp.source_hash()
    assert stamp.read_stamp(tmp_path / "none") is None


# ---------------------------------------------------------------------------------------------------- the update


SHA = "a" * 40


class FakeGh:
    """git and gh as subprocess.run sees them: HEAD is SHA; `runs` are the CI runs; download writes `artifact`."""

    def __init__(self, runs, artifact):
        self.runs, self.artifact, self.calls = runs, artifact, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = ""
        if cmd[:2] == ["git", "rev-parse"]:
            out = SHA + "\n"
        elif cmd[:3] == ["gh", "run", "list"]:
            out = json.dumps([r for r in self.runs if r["headSha"] == cmd[cmd.index("--commit") + 1]])
        elif cmd[:3] == ["gh", "run", "view"]:
            out = json.dumps({"headSha": next(r["headSha"] for r in self.runs if str(r["databaseId"]) == cmd[3])})
        elif cmd[:3] == ["gh", "run", "download"]:
            for name, data in self.artifact.items():
                (Path(cmd[cmd.index("--dir") + 1]) / name).write_bytes(data)
        return subprocess.CompletedProcess(cmd, 0, out, "")


def good_artifact():
    return {stamp.DLL_NAME: b"MZ fake dll", stamp.STAMP_NAME: f"{stamp.source_hash()}\n".encode()}


@pytest.fixture
def update():
    return load("update_windows_dll")


def test_update_copies_the_dll_and_stamp_from_this_commits_run(tmp_path, update):
    gh = FakeGh([{"databaseId": 7, "headSha": SHA}], good_artifact())
    path = update.update(dest=tmp_path, run=gh)
    assert path.read_bytes() == b"MZ fake dll" and stamp.read_stamp(tmp_path) == stamp.source_hash()
    assert ["gh", "run", "download", "7", "--repo", "Altainia/scrimmage", "--name", "lockstep-win-dll"] == \
        gh.calls[-1][:8]


def test_update_refuses_another_commits_run(tmp_path, update):
    gh = FakeGh([{"databaseId": 7, "headSha": "b" * 40}], good_artifact())
    with pytest.raises(update.Refused, match="no ci.yml run on commit aaaaaaaaaaaa"):
        update.update(dest=tmp_path, run=gh)
    with pytest.raises(update.Refused, match="run 7 is on commit bbbbbbbbbbbb"):
        update.update("7", dest=tmp_path, run=gh)
    assert list(tmp_path.iterdir()) == []


def test_update_refuses_a_dll_built_from_other_source(tmp_path, update):
    gh = FakeGh([{"databaseId": 7, "headSha": SHA}], {**good_artifact(), stamp.STAMP_NAME: b"0" * 64 + b"\n"})
    with pytest.raises(update.Refused, match="built from other source"):
        update.update(dest=tmp_path, run=gh)
    gh = FakeGh([{"databaseId": 7, "headSha": SHA}], {stamp.STAMP_NAME: good_artifact()[stamp.STAMP_NAME]})
    with pytest.raises(update.Refused, match="has no halsim_scrimmage_lockstep.dll"):
        update.update(dest=tmp_path, run=gh)
    assert list(tmp_path.iterdir()) == []


def test_update_without_gh_says_how_to_get_it(tmp_path, update):
    def run(cmd, **kw):
        if cmd[0] == "gh":
            raise FileNotFoundError(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, SHA, "")
    with pytest.raises(update.Refused, match="GitHub CLI"):
        update.update(dest=tmp_path, run=run)
