"""The GitHub Actions recipe for a team's robot repository (examples/ci/scrimmage.yml): valid YAML a workflow can be,
and every command it runs is one Scrimmage has, with arguments it takes; the folders it installs from exist."""

from __future__ import annotations

import argparse
import shlex
import tomllib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
RECIPE = ROOT / "examples" / "ci" / "scrimmage.yml"
CHECKOUT = ".scrimmage"        # where the recipe checks Scrimmage out, beside the robot code


class _Parsed(Exception):
    pass


def _commands() -> list[list[str]]:
    flow = yaml.safe_load(RECIPE.read_text())
    steps = flow["jobs"]["scenarios"]["steps"]
    out = []
    for s in steps:
        for line in s.get("run", "").splitlines():
            line = line.split(" #")[0].strip()
            if line:
                out += [shlex.split(part) for part in line.split("&&")]
    return out


def test_the_recipe_is_a_workflow():
    flow = yaml.safe_load(RECIPE.read_text())
    assert flow["name"] == "Scrimmage"
    assert set(flow[True]) == {"push", "pull_request"}        # YAML 1.1 reads the key `on` as True
    job = flow["jobs"]["scenarios"]
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] > 0
    for s in job["steps"]:
        assert ("uses" in s) != ("run" in s), s
        assert set(s) <= {"name", "uses", "with", "run", "env", "if"}, s
    checkout = [s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@")]
    assert checkout[1]["with"] == {"repository": "Altainia/scrimmage", "path": CHECKOUT}
    run = next(s for s in job["steps"] if "scrimmage run" in s.get("run", ""))
    assert run["env"]["MUJOCO_GL"] == "osmesa"


def test_its_scrimmage_commands_exist_with_their_arguments(monkeypatch):
    from scrimmage.__main__ import main
    orig = argparse.ArgumentParser.parse_args

    def parsed(self, args=None, namespace=None):
        raise _Parsed(orig(self, args, namespace))
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parsed)
    ran = []
    for cmd in _commands():
        if cmd[0] != "scrimmage":
            continue
        with pytest.raises(_Parsed) as e:
            main(cmd[1:])
        ran.append(e.value.args[0].command)
    assert ran == ["java", "run"]


def test_its_installs_name_real_folders_and_extras():
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    installs = [c for c in _commands() if c[:2] == ["pip", "install"]]
    assert len(installs) == 2
    for cmd in installs:
        target = [a for a in cmd[2:] if not a.startswith("-")][0]
        folder, _, wanted = target.partition("[")
        here = ROOT / Path(folder).relative_to(CHECKOUT)
        assert (here / "pyproject.toml").is_file(), target
        for extra in filter(None, wanted.rstrip("]").split(",")):
            assert extra in extras, extra
