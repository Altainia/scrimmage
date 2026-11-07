"""What every part of the editor's server shares: ApiError (an answer other than 200), the poll and stream periods,
classify (what kind of file a path is), remember_opened, and the small helpers and words the App's parts use.
server.py re-exports ApiError, classify, remember_opened and _chassis_root_body; this module never imports server.py or the App's parts.
"""

from __future__ import annotations

import dataclasses
import re
import tomllib
from pathlib import Path

from .. import i18n, teamfolders
from ..measure.use import SUFFIX
from ..parts.stock import REGISTRY, _load_all
from . import edits, library, part_session, recent


POLL_SECONDS = 25.0
_FOLDER_PLACEHOLDER = re.compile(r"\{(cad|code)\}")
FRAME_PERIOD = 1 / 30     # at most one frame per 33 ms of wall time per source
STATE_PERIOD = 0.1


class ApiError(Exception):
    """An answer other than 200: `message` for people, `field` the code the page acts on (never the message's words,
    which are in the user's language): "status" unless a caller needs to tell this refusal from others."""

    def __init__(self, status: int, message: str, field: str = "status"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.field = field


def _plain(v):
    return v.unwrap() if hasattr(v, "unwrap") else v


def _nothing_to(which: str) -> str:
    """An undo or redo with nothing in its history (the button is disabled then, so a second page's race)."""
    return i18n.t("nothing to undo") if which == "undo" else i18n.t("nothing to redo")


def _cant(which: str, problem: str) -> str:
    """An undo or redo the file's check refuses, with the check's own words."""
    return (i18n.t("can't undo: {problem}", problem=problem) if which == "undo"
            else i18n.t("can't redo: {problem}", problem=problem))


def _sentence(why: str) -> str:
    """`why` (a reason, in the user's language) as a finished sentence: its first letter capitalized, and closed as
    the language closes a sentence, unless it is closed already."""
    why = why[:1].upper() + why[1:]
    return why if why.endswith((".", "!", "?")) else i18n.t("{sentence}.", sentence=why)


def _chassis_root_body() -> str:
    """The stock chassis's root body ("chassis"): an import part attached to it at zero offset sits where the CAD has it."""
    _load_all()
    return library.root_body("chassis")


def _stock_defaults(stock) -> dict:
    _load_all()
    s = REGISTRY.get(stock) if isinstance(stock, str) else None
    return {p.name: (list(p.default) if p.kind == "pair" else p.default) for p in s.params} if s else {}


def _fe(errs) -> list[dict]:
    return [{"field": e.field, "message": e.message} for e in errs]


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_STEP_HEADER = re.compile(r"(?m)^[ \t]*\[\[[ \t]*step[ \t]*\]\]")
_CONFIG_KEY = re.compile(r"(?m)^[ \t]*config[ \t]*=")
_TABLE = re.compile(r"(?m)^[ \t]*\[")
_BENCH_HEADER = re.compile(r"(?m)^[ \t]*\[[ \t]*bench[ \t]*\]")


def _strings(v):
    """Every string in a parsed TOML value."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


def unset_folder(values) -> tuple[str, str] | None:
    """(team folder name, the plain message) for the first `{cad}`/`{code}` in `values` (a parsed TOML value) that
    this computer hasn't set, else None."""
    known = teamfolders.folders()
    for text in _strings(values):
        for m in _FOLDER_PLACEHOLDER.finditer(text):
            if known[m.group(1)] is None:
                try:
                    teamfolders.expand(m.group(0))
                except teamfolders.TeamFolderError as e:
                    return m.group(1), str(e)
    return None


# The page's words for a few refusals and states, in the user's language (made when they are said, never at import)
def _hand_written() -> str:
    return i18n.t("this robot uses a hand-written model; the assembler edits {part_table} robots",
                  part_table="[[part]]")


def _no_program() -> str:
    return i18n.t("this robot config has no {robot_program_table} section: add one to drive it",
                  robot_program_table="[robot_program]")


def _unpicked() -> str:
    return i18n.t("Pick where the robot starts first")


def _not_built(why: str) -> str:
    """why: "never" (never built here), "failed" (the last Build now failed) or "running" (a build is running)."""
    if why == "running":
        return i18n.t("The robot code is still building. Wait for the build to finish, then start again.")
    if why == "failed":
        return i18n.t("The robot code didn't build the last time. Fix what the build said (under Ready to start? in "
                      "the Robot code panel), then build it again.")
    return i18n.t("The robot code hasn't been built on this computer yet. Build it first (Build now, under Ready to "
                  "start? in the Robot code panel): the first build downloads the libraries, so it takes a few minutes.")


def start_sentence(pose) -> str:
    """Plain words for a start pose (x, y, heading in degrees; the blue alliance wall at x = 0, red at the far end):
    which alliance's half of the field it is on, and which wall it faces."""
    x, _, h = (float(a) for a in pose)
    blue = x < edits.FIELD_LENGTH / 2
    h = (h + 180.0) % 360.0 - 180.0
    facing = "red" if abs(h) <= 45 else "blue" if abs(h) >= 135 else "side"
    if blue:
        return {"red": i18n.t("Starts on the blue side, facing the red wall"),
                "blue": i18n.t("Starts on the blue side, facing the blue wall"),
                "side": i18n.t("Starts on the blue side, facing a side wall")}[facing]
    return {"red": i18n.t("Starts on the red side, facing the red wall"),
            "blue": i18n.t("Starts on the red side, facing the blue wall"),
            "side": i18n.t("Starts on the red side, facing a side wall")}[facing]


_IMPORT_HEADER = re.compile(r"(?m)^[ \t]*\[[ \t]*import[ \t]*\]")
_TEST_HEADER = re.compile(r"(?m)^[ \t]*\[[ \t]*test[ \t]*\]")
_MEASURE_HEADER = re.compile(r"(?m)^[ \t]*\[[ \t]*measure[ \t]*\]")


def classify(path) -> str:
    """"import" for a CAD file (.step/.stp/.stl/.obj), a folder holding a part.toml, or a file with a top-level
    [import] table; "session" for another folder (of bench files); for a file, "measure" when its name ends in
    .measure.toml or it has a top-level [measure] table (a measurement file), "test" when it has a top-level [test]
    table (a block test), else "scenario" when it has a top-level `config` key or a [[step]] array, else "bench"
    when it has a top-level [bench] table, else "robot". A file that isn't valid TOML is judged by its lines (an
    [import] header; a [measure] header; a [test] header; a [[step]] header or `config =` before the first table;
    then a [bench] header)."""
    if part_session.is_part_target(path):
        return "import"
    if Path(path).is_dir():
        return "session"
    if Path(path).name.lower().endswith(SUFFIX):
        return "measure"
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "robot"
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        if _IMPORT_HEADER.search(text):
            return "import"
        if _MEASURE_HEADER.search(text):
            return "measure"
        if _TEST_HEADER.search(text):
            return "test"
        first_table = _TABLE.search(text)
        head = text[:first_table.start()] if first_table else text
        if _STEP_HEADER.search(text) or _CONFIG_KEY.search(head):
            return "scenario"
        return "bench" if _BENCH_HEADER.search(text) else "robot"
    if isinstance(raw.get("import"), dict):
        return "import"
    if isinstance(raw.get("measure"), dict):
        return "measure"
    if isinstance(raw.get("test"), dict):
        return "test"
    if "config" in raw or "step" in raw:
        return "scenario"
    return "bench" if isinstance(raw.get("bench"), dict) else "robot"


def _info_json(info) -> dict:
    """A robotcode.ProjectInfo as JSON (paths as text)."""
    d = dataclasses.asdict(info)
    d["folder"] = str(info.folder)
    d["python"] = str(info.python) if info.python is not None else None
    return d


def remember_opened(path: Path) -> None:
    """Put an opened file in the start page's recent list: a robot, a scenario, a test, a measurement file, a session
    (a bench file's folder) or an import (a CAD file's import file, an import-made folder's, or a plugin part
    folder)."""
    kind = classify(path)
    if kind == "import":
        return   # App.remember_part does, once the import file is known
    recent.remember(Path(path).parent if kind == "bench" else path, "session" if kind in ("bench", "session") else kind)
