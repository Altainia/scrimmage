"""Drive recorder: turns a stream of (time, mode, joystick inputs) into a scenario whose steps hold input tracks."""

from __future__ import annotations

import copy
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import tomlkit

from scrimmage.tracks import PortInputs, Sample, write_track

__all__ = ["DriveRecorder", "RecordingError"]


class RecordingError(ValueError):
    pass


class _RestoreFailed(Exception):
    """An install failed and the old recording couldn't all be put back: it stays in the staging folder."""


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
    end: float | None = None
    events: list[tuple[float, dict[int, PortInputs]]] = field(default_factory=list)


class DriveRecorder:
    def __init__(self, step: float):
        self.step = step
        self.recording = False
        self.saved = False   # save() has written this recording (until the next start())
        self.pokes = 0
        self._segments: list[_Segment] = []

    def start(self, t: float, mode: str, ports: dict[int, PortInputs]) -> None:
        self._segments = []
        self.pokes = 0
        self.recording = True
        self.saved = False
        self._open(t, mode, ports)

    def sample(self, t: float, mode: str, ports: dict[int, PortInputs]) -> None:
        """From sim time ``t`` on, the effective mode and inputs are these; unchanged samples are dropped."""
        if not self.recording:
            return
        cur = self._segments[-1]
        if mode != cur.mode:
            cur.end = t
            self._open(t, mode, ports)
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

    def _open(self, t: float, mode: str, ports: dict[int, PortInputs]) -> None:
        self._segments.append(_Segment(mode, t, None, [(t, copy.deepcopy(ports))]))

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
            raise RecordingError("nothing was recorded")
        if path.suffix != ".toml":
            raise RecordingError(f"{path.name} must end in .toml")
        folder = path.parent / path.stem
        # an overwrite replaces only an earlier recording: NAME.toml starting with the marker line, and its
        # NAME/ folder of step CSVs (none of these refusals ends in "already exists": there's nothing to confirm)
        is_recording = path.is_file() and not path.is_symlink() and _is_recording(path)
        if (path.exists() or path.is_symlink()) and not is_recording:
            raise RecordingError(f"{path.name} is not a recording made in the Drive tab, so it can't be replaced; "
                                 "choose another name")
        if folder.exists() or folder.is_symlink():
            if not is_recording:
                raise RecordingError(f"the folder {folder.name} exists without a recording {path.name}, so it "
                                     "can't be replaced; choose another name")
            if not _recorder_made(folder):
                raise RecordingError(f"the folder {folder.name} holds more than a recording's step CSVs, so it "
                                     "can't be replaced; choose another name")
        if path.exists() and not overwrite:
            raise RecordingError(f"{path.name} already exists")
        if folder.exists() and not overwrite:
            raise RecordingError(f"{folder.name} already exists")
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
        steps = tomlkit.aot()
        path.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp"))
        keep_stage = False
        try:
            tracks = stage / "tracks"
            tracks.mkdir()
            for k, (seg, duration) in enumerate(segments, 1):
                write_track(tracks / f"step{k}.csv", self._samples(seg, duration))
                t = tomlkit.table()
                t.add("name", f"{seg.mode} {k}")
                t.add("mode", seg.mode)
                t.add("duration", duration)
                t.add("inputs", f"{path.stem}/step{k}.csv")
                steps.append(t)
            doc.add("step", steps)
            toml_tmp = stage / "scenario.toml"
            toml_tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
            try:
                self._install(stage, tracks, folder, toml_tmp, path)
            except _RestoreFailed as e:
                keep_stage = True
                raise RecordingError(f"can't write {path.name} ({e.__cause__}), and the old recording couldn't be "
                                     f"put back: it is kept in {stage}") from e.__cause__
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
            restored = True
            for p in placed:
                try:
                    _remove(p)
                except OSError:
                    restored = False
            for spot, old in reversed(aside):
                try:
                    os.replace(spot, old)
                except OSError:
                    restored = False
            if not restored:
                raise _RestoreFailed() from err
            raise
