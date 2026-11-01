"""The getting-started guides are followed literally by people new to scrimmage, so every link, anchor, command
and example file they name has to exist."""

import re
from pathlib import Path

import pytest

from scrimmage.__main__ import main

ROOT = Path(__file__).resolve().parent.parent
GUIDES = sorted((ROOT / "docs" / "getting-started").glob("*.md")) + [ROOT / "docs" / "usability-future-work.md"]
LINK = re.compile(r"\]\(([^)\s]+)\)")
CODE = re.compile(r"```[a-z]*\n(.*?)\n```", re.S)
COMMAND = re.compile(r"^\s*scrimmage (\w+)(?: (\w+))?", re.M)
EXAMPLE = re.compile(r"(?<![\w/])((?:examples|scenarios|configs)/[\w./-]+\.toml)\b")  # a repo path, not sim/scenarios/...
# commands whose next word is a subcommand (`scrimmage bench run`); for the rest it is a file
GROUPS = {"java", "launcher", "bench", "terrain", "climb", "freecad"}


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to hyphens."""
    text = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(md: Path) -> set[str]:
    text = CODE.sub("", md.read_text(encoding="utf-8"))
    return {_slug(h) for h in re.findall(r"^#+ (.+)$", text, re.M)}


def test_guides_exist():
    names = {p.name for p in GUIDES}
    assert {"README.md", "install.md", "try-a-mechanism.md", "run-your-robot-code.md",
            "test-your-robot-code.md", "usability-future-work.md"} <= names


@pytest.mark.parametrize("md", GUIDES, ids=lambda p: p.name)
def test_links_resolve(md):
    for target in LINK.findall(md.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://")):
            continue
        path, _, anchor = target.partition("#")
        dest = (md.parent / path).resolve() if path else md
        assert dest.exists(), f"{md.name}: {target} doesn't exist"
        if anchor:
            assert anchor in _anchors(dest), f"{md.name}: {target}: no heading with that anchor"


@pytest.mark.parametrize("md", GUIDES, ids=lambda p: p.name)
def test_commands_exist(md, capsys):
    for block in CODE.findall(md.read_text(encoding="utf-8")):
        for cmd, sub in COMMAND.findall(block):
            # `--help` exits 0 for a real command; argparse exits 2 for an unknown one (a word after `bench`, say)
            words = [cmd, sub] if sub and cmd in GROUPS else [cmd]
            with pytest.raises(SystemExit) as stop:
                main([*words, "--help"])
            capsys.readouterr()
            assert stop.value.code == 0, f"{md.name}: scrimmage {' '.join(words)} isn't a command"


@pytest.mark.parametrize("md", GUIDES, ids=lambda p: p.name)
def test_example_files_exist(md):
    for rel in EXAMPLE.findall(md.read_text(encoding="utf-8")):
        assert (ROOT / rel).exists(), f"{md.name}: {rel} doesn't exist"
