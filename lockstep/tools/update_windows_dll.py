"""Refresh the committed Windows extension (scrimmage_lockstep/lib/win_amd64) from CI, after a change to its source.

Windows CI's `windows` job builds the extension from source on every run and uploads it, with its stamp, as the
artifact `lockstep-win-dll`. This downloads that artifact (with the GitHub CLI, `gh`) from an Altainia/scrimmage run on
the commit checked out here, checks its stamp matches the source here, and copies the DLL and the stamp into place:

    git push                                   # the commit with the C++ change; wait for CI's windows job
    python lockstep/tools/update_windows_dll.py [--run RUN_ID]
    git add lockstep/scrimmage_lockstep/lib/win_amd64 && git commit

It refuses a run on any other commit, and an artifact whose stamp isn't this source's.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stamp  # noqa: E402

REPO = "Altainia/scrimmage"
WORKFLOW = "ci.yml"
ARTIFACT = "lockstep-win-dll"


class Refused(Exception):
    pass


def _gh(args: list[str], run=subprocess.run) -> str:
    try:
        r = run(["gh", *args], capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        raise Refused("the GitHub CLI (gh) isn't installed: https://cli.github.com") from None
    if r.returncode != 0:
        raise Refused(f"gh {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def head_commit(run=subprocess.run) -> str:
    r = run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=stamp.LOCKSTEP)
    if r.returncode != 0:
        raise Refused(f"git rev-parse HEAD failed: {r.stderr.strip()}")
    return r.stdout.strip()


def find_run(sha: str, run_id: str | None, run=subprocess.run) -> str:
    """The CI run to take the DLL from: `run_id` (checked to be on `sha`), else the newest CI run on `sha`."""
    if run_id is not None:
        got = json.loads(_gh(["run", "view", run_id, "--repo", REPO, "--json", "headSha"], run))["headSha"]
        if got != sha:
            raise Refused(f"run {run_id} is on commit {got[:12]}, not {sha[:12]} (checked out here)")
        return run_id
    runs = json.loads(_gh(["run", "list", "--repo", REPO, "--workflow", WORKFLOW, "--commit", sha,
                           "--json", "databaseId,headSha", "--limit", "20"], run))
    runs = [r for r in runs if r.get("headSha") == sha]
    if not runs:
        raise Refused(f"no {WORKFLOW} run on commit {sha[:12]} in {REPO}: push it and wait for the windows job")
    return str(runs[0]["databaseId"])


def update(run_id: str | None = None, dest: Path = stamp.DLL_DIR, run=subprocess.run) -> Path:
    """Download, check and copy; returns the DLL's new path. Raises Refused, changing nothing, when anything is off."""
    sha = head_commit(run)
    rid = find_run(sha, run_id, run)
    with tempfile.TemporaryDirectory() as tmp:
        _gh(["run", "download", rid, "--repo", REPO, "--name", ARTIFACT, "--dir", tmp], run)
        dll = Path(tmp) / stamp.DLL_NAME
        if not dll.is_file():
            raise Refused(f"run {rid}'s {ARTIFACT} artifact has no {stamp.DLL_NAME}")
        got, want = stamp.read_stamp(Path(tmp)), stamp.source_hash()
        if got != want:
            raise Refused(f"run {rid}'s DLL was built from other source (stamp {got}, this source {want}): commit "
                          f"and push the source change first, then take the DLL from that commit's run")
        Path(dest).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dll, Path(dest) / stamp.DLL_NAME)
        shutil.copyfile(Path(tmp) / stamp.STAMP_NAME, Path(dest) / stamp.STAMP_NAME)
    return Path(dest) / stamp.DLL_NAME


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy CI's Windows build of the lockstep extension into the package")
    ap.add_argument("--run", metavar="RUN_ID", help=f"the {WORKFLOW} run (default: the newest on this commit)")
    args = ap.parse_args(argv)
    try:
        path = update(args.run)
    except Refused as e:
        print(f"update_windows_dll: {e}", file=sys.stderr)
        return 1
    print(f"updated {path} and its stamp; commit both")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
