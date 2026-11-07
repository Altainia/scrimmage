"""Drive recorder: turns a stream of (time, mode, joystick inputs, game data) into a scenario whose steps hold input
tracks, with the match settings as its [match]."""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import tomlkit

from scrimmage import i18n
from scrimmage.driverstation import MATCH_KEYS, MatchSettings
from scrimmage.tracks import PortInputs, Sample, write_track

__all__ = ["DriveRecorder", "RecordingError"]


class RecordingError(ValueError):
    pass


class RecordingExists(RecordingError):
    """The scenario file or its tracks folder is already there: the page asks before overwriting it."""


class _RestoreFailed(Exception):
    """An install failed and the old recording couldn't all be put back: what wasn't (`left`: (where it was, where
    it is now) pairs) stays in the staging folder; `whole` when none of it went back."""

    def __init__(self, left: list[tuple[Path, Path]], whole: bool):
        super().__init__()
        self.left, self.whole = left, whole


MARKER = "# Recorded in the Drive tab"   # the first line of every recording; Overwrite replaces only those


def _is_recording(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return text.startswith(MARKER)


_STEP_CSV = re.compile(r"step\d+(\.trim\d+)*\.csv")


def _recorder_made(folder: Path) -> bool:
    """True when `folder` holds only step<k>.csv files (what a recording writes) and their trimmed copies
    (step<k>.trim<n>.csv, from the Scenario tab's Trim): the only kind of folder an overwrite may replace."""
    return folder.is_dir() and not folder.is_symlink() and all(
        p.is_file() and not p.is_symlink() and _STEP_CSV.fullmatch(p.name) for p in folder.iterdir())


def _remove(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


@dataclass
class _Segment:
    mode: str
    start: float
    game_data: str = ""   # what the robot had at the segment's start
    end: float | None = None
    events: list[tuple[float, dict[int, PortInputs]]] = field(default_factory=list)


class DriveRecorder:
    def __init__(self, step: float, config_match: dict | None = None):
        self.step = step
        self.recording = False
        self.saved = False   # save() has written this recording (until the next start())
        self.pokes = 0
        self._segments: list[_Segment] = []
        self._config_match = config_match if config_match is not None else asdict(MatchSettings())
        self._match: dict = {}

    def start(self, t: float, mode: str, ports: dict[int, PortInputs], game_data: str = "",
              match: dict | None = None) -> None:
        """`match` is the match settings (MATCH_KEYS) in effect: the recording's [match]."""
        self._segments = []
        self.pokes = 0
        self.recording = True
        self.saved = False
        self._match = dict(match) if match is not None else dict(self._config_match)
        self._open(t, mode, ports, game_data)

    def sample(self, t: float, mode: str, ports: dict[int, PortInputs], game_data: str = "") -> None:
        """From sim time ``t`` on, the effective mode, inputs and game data are these; unchanged samples are dropped.
        A new mode or game data starts a step."""
        if not self.recording:
            return
        cur = self._segments[-1]
        if mode != cur.mode or game_data != cur.game_data:
            cur.end = t
            self._open(t, mode, ports, game_data)
        elif ports != cur.events[-1][1]:
            cur.events.append((t, copy.deepcopy(ports)))

    def poke(self) -> None:
        if self.recording:
            self.pokes += 1

    def stop(self, t: float) -> None:
        if self.recording:
            self._segments[-1].end = t
            self.recording = False

    @property
    def empty(self) -> bool:
        return not self._timed()

    def _open(self, t: float, mode: str, ports: dict[int, PortInputs], game_data: str) -> None:
        self._segments.append(_Segment(mode, t, game_data, None, [(t, copy.deepcopy(ports))]))

    def _match_table(self) -> dict:
        """The keys that differ from the defaults, and those that differ from the config's (else a replay would take
        the config's)."""
        default = asdict(MatchSettings())
        return {k: v for k in MATCH_KEYS if (v := self._match.get(k)) is not None
                and (v != default[k] or v != self._config_match.get(k))}

    def _duration(self, seg: _Segment) -> float:
        end = seg.end if seg.end is not None else seg.events[-1][0]
        return round(round((end - seg.start) / self.step) * self.step, 6)

    def _timed(self) -> list[tuple[_Segment, float]]:
        return [(s, d) for s in self._segments if (d := self._duration(s)) > 0]

    def _samples(self, seg: _Segment, duration: float) -> list[Sample]:
        out: list[Sample] = []
        for t, ports in seg.events:
            rel = round(t - seg.start, 6)
            if rel >= duration:
                continue
            if out and rel <= out[-1].t:
                out[-1] = Sample(out[-1].t, ports)
            else:
                out.append(Sample(rel, ports))
        return out

    def save(self, path: Path, config: Path, start_pose: tuple[float, float, float],
             overwrite: bool = False) -> dict:
        path, config = Path(path), Path(config)
        segments = self._timed()
        if not segments:
            raise RecordingError(i18n.t("nothing was recorded"))
        if path.suffix != ".toml":
            raise RecordingError(i18n.t("{name} must end in .toml", name=path.name))
        folder = path.parent / path.stem
        # an overwrite replaces only an earlier recording: NAME.toml starting with the marker line, and its
        # NAME/ folder of step CSVs (none of these refusals ends in "already exists": there's nothing to confirm)
        is_recording = path.is_file() and not path.is_symlink() and _is_recording(path)
        if (path.exists() or path.is_symlink()) and not is_recording:
            raise RecordingError(i18n.t("{name} is not a recording made in the Drive tab, so it can't be replaced; choose another name", name=path.name))
        if folder.exists() or folder.is_symlink():
            if not is_recording:
                raise RecordingError(i18n.t("the folder {name} exists without a recording {recording}, so it can't be replaced; choose another name", name=folder.name, recording=path.name))
            if not _recorder_made(folder):
                raise RecordingError(i18n.t("the folder {name} holds more than a recording's step CSVs, so it can't be replaced; choose another name", name=folder.name))
        if path.exists() and not overwrite:
            raise RecordingExists(i18n.t("{name} already exists", name=path.name))
        if folder.exists() and not overwrite:
            raise RecordingExists(i18n.t("{name} already exists", name=folder.name))
        try:
            rel_config = Path(os.path.relpath(config.resolve(), path.parent.resolve())).as_posix()
        except ValueError:   # different drive
            rel_config = config.resolve().as_posix()
        x, y, h = start_pose
        doc = tomlkit.document()
        doc.add(tomlkit.comment(f"Recorded in the Drive tab on {datetime.now():%Y-%m-%d %H:%M} from start pose "
                                f"x={x:.3f}, y={y:.3f}, heading={h:.1f}\N{DEGREE SIGN}"))
        doc.add("description", "Recorded drive")
        doc.add("config", rel_config)
        match = self._match_table()
        if match:
            table = tomlkit.table()
            for k, v in match.items():
                table.add(k, v)
            doc.add("match", table)
        steps = tomlkit.aot()
        path.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp"))
        keep_stage = False
        try:
            tracks = stage / "tracks"
            tracks.mkdir()
            had = ""   # the game data at the previous step's start
            for k, (seg, duration) in enumerate(segments, 1):
                write_track(tracks / f"step{k}.csv", self._samples(seg, duration))
                t = tomlkit.table()
                t.add("name", f"{seg.mode} {k}")
                t.add("mode", seg.mode)
                t.add("duration", duration)
                if seg.game_data != had:
                    t.add("game_data", seg.game_data)
                    had = seg.game_data
                t.add("inputs", f"{path.stem}/step{k}.csv")
                steps.append(t)
            doc.add("step", steps)
            toml_tmp = stage / "scenario.toml"
            toml_tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
            try:
                self._install(stage, tracks, folder, toml_tmp, path)
            except _RestoreFailed as e:
                keep_stage = True
                if e.whole:
                    raise RecordingError(i18n.t("can't write {name} ({cause}), and the old recording couldn't be put back: it is kept in {stage}", name=path.name, cause=e.__cause__, stage=stage)) from e.__cause__
                [(old, kept)] = e.left   # the folder or the TOML: the other one went back
                raise RecordingError(i18n.t("can't write {name} ({cause}), and only part of the old recording could be put back: the old {old} is kept as {kept}", name=path.name, cause=e.__cause__, old=old.name, kept=kept)) from e.__cause__
        finally:
            if not keep_stage:
                shutil.rmtree(stage, ignore_errors=True)
        self.saved = True
        return {"path": str(path), "steps": len(segments), "pokes": self.pokes}

    @staticmethod
    def _install(stage: Path, tracks: Path, folder: Path, toml_tmp: Path, path: Path) -> None:
        """Move the old recording aside (into `stage`, deleted with it), then put the new one in place; when
        anything fails the old recording is put back (_RestoreFailed, caused by the install's error, when it can't
        all be: what's left of it is still in `stage`)."""
        aside: list[tuple[Path, Path]] = []    # (where it went, where it was)
        placed: list[Path] = []
        try:
            for old, spot in ((folder, stage / "old-folder"), (path, stage / "old.toml")):
                if old.exists():
                    os.replace(old, spot)
                    aside.append((spot, old))
            os.replace(tracks, folder)
            placed.append(folder)
            os.replace(toml_tmp, path)
        except BaseException as err:
            for p in placed:
                try:
                    _remove(p)
                except OSError:
                    pass   # putting the old piece back over it then fails too
            left = []
            for spot, old in reversed(aside):
                try:
                    os.replace(spot, old)
                except OSError:
                    left.append((old, spot))
            if left:
                raise _RestoreFailed(left, whole=len(left) == len(aside)) from err
            raise
