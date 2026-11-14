"""Input tracks: CSV files of timed joystick inputs that a scenario step replays.

Header: ``t`` then ``p<port>.axis<i>`` (i from 0), ``p<port>.button<i>`` (i from 1, WPILib numbering) and
``p<port>.pov<i>`` (i from 0); ports 0-5. Every row has every column; ``t`` is >= 0 and strictly increasing.
"""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

from . import i18n

__all__ = ["PortInputs", "Sample", "Track", "TrackError", "parse_track", "load_track", "write_track"]

COLUMN = re.compile(r"p(\d+)\.(axis|button|pov)(\d+)$")
KIND_START = {"axis": 0, "button": 1, "pov": 0}


class TrackError(ValueError):
    pass


@dataclass
class PortInputs:
    axes: list[float] = field(default_factory=list)
    buttons: list[bool] = field(default_factory=list)
    povs: list[int] = field(default_factory=list)


@dataclass
class Sample:
    t: float
    ports: dict[int, PortInputs] = field(default_factory=dict)


@dataclass
class Track:
    path: Path
    samples: list[Sample] = field(default_factory=list)
    _times: list[float] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._times = [s.t for s in self.samples]   # at() runs every robot step: the samples are fixed once loaded

    def at(self, elapsed: float) -> int:
        """Index of the latest sample with t <= elapsed (within 1e-9), or -1 before the first."""
        return bisect_right(self._times, elapsed + 1e-9) - 1


def _header(cells: list[str], err) -> tuple[list[tuple[int, str, int]], dict[int, dict[str, int]]]:
    """The columns after ``t`` as (port, kind, index), and the width of each kind per port."""
    if cells[0] != "t":
        raise TrackError(err(1, i18n.t("the first column must be 't', not {value}", value=repr(cells[0]))))
    cols, seen = [], set()
    for name in cells[1:]:
        m = COLUMN.match(name)
        if not m:
            raise TrackError(err(1, i18n.t("unknown column {name}; use t, p<port>.axis<i>, p<port>.button<i>, "
                                           "p<port>.pov<i>", name=repr(name))))
        port, kind, idx = int(m.group(1)), m.group(2), int(m.group(3))
        if port > 5:
            raise TrackError(err(1, i18n.t("column {name}: joystick ports are 0-5", name=repr(name))))
        if idx < KIND_START[kind]:
            raise TrackError(err(1, i18n.t("column {name}: {kind}s are numbered from {first}",
                                           name=repr(name), kind=kind, first=KIND_START[kind])))
        if (port, kind, idx) in seen:
            raise TrackError(err(1, i18n.t("column {name} appears twice", name=repr(name))))
        seen.add((port, kind, idx))
        cols.append((port, kind, idx))
    widths: dict[int, dict[str, int]] = {}
    for port, kind, idx in cols:
        w = widths.setdefault(port, {"axis": 0, "button": 0, "pov": 0})
        w[kind] = max(w[kind], idx - KIND_START[kind] + 1)
    for port, kind, idx in cols:
        for j in range(KIND_START[kind], KIND_START[kind] + widths[port][kind]):
            if (port, kind, j) not in seen:
                raise TrackError(err(1, i18n.t("column p{port}.{kind}{missing} is missing (p{port}.{kind}{index} "
                                               "needs it)", port=port, kind=kind, missing=j, index=idx)))
    return cols, widths


def parse_track(text: str, path: Path) -> Track:
    path = Path(path)

    def err(n: int, problem: str) -> str:
        return i18n.t("{file}: line {line}: {problem}", file=path.name, line=n, problem=problem)

    lines = text.splitlines()
    if not any(l.strip() for l in lines):
        raise TrackError(err(1, i18n.t("the file is empty; the first line must be the header (t, p0.axis0, ...)")))
    cols, widths = _header([c.strip() for c in lines[0].split(",")], err)
    samples: list[Sample] = []
    last_t = None
    for n, line in enumerate(lines[1:], 2):
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split(",")]
        if len(cells) != len(cols) + 1:
            raise TrackError(err(n, i18n.t("expected {expected} values (one per header column), got {got}",
                                           expected=len(cols) + 1, got=len(cells))))
        try:
            t = float(cells[0])
        except ValueError:
            raise TrackError(err(n, i18n.t("t must be a number, not {value}", value=repr(cells[0])))) from None
        if not math.isfinite(t) or t < 0:
            raise TrackError(err(n, i18n.t("t must be 0 or more, not {value}", value=repr(cells[0]))))
        if last_t is not None and t <= last_t:
            raise TrackError(err(n, i18n.t("{t_setting} must be greater than the previous row's {last_t}",
                                           last_t=f"{last_t:g}", t_setting="t = {}".format(f"{t:g}"))))
        last_t = t
        ports = {p: PortInputs([0.0] * w["axis"], [False] * w["button"], [-1] * w["pov"])
                 for p, w in widths.items()}
        for (port, kind, idx), cell in zip(cols, cells[1:]):
            name = f"p{port}.{kind}{idx}"
            pi = ports[port]
            if kind == "axis":
                try:
                    v = float(cell)
                except ValueError:
                    v = math.nan
                if not (math.isfinite(v) and -1 <= v <= 1):
                    raise TrackError(err(n, i18n.t("{name} must be a number between -1 and 1, not {value}",
                                                   name=name, value=repr(cell))))
                pi.axes[idx] = v
            elif kind == "button":
                if cell not in ("0", "1"):
                    raise TrackError(err(n, i18n.t("{name} must be 0 or 1, not {value}", name=name, value=repr(cell))))
                pi.buttons[idx - 1] = cell == "1"
            else:
                try:
                    v = int(cell)
                except ValueError:
                    v = None
                if v is None or not (v == -1 or 0 <= v <= 359):
                    raise TrackError(err(n, i18n.t("{name} must be a whole number: -1 or 0-359 degrees, not {value}",
                                                   name=name, value=repr(cell))))
                pi.povs[idx] = v
        samples.append(Sample(t, ports))
    return Track(path, samples)


def load_track(path: Path) -> Track:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise TrackError(i18n.t("file not found: {path}", path=path)) from None
    except IsADirectoryError:
        raise TrackError(i18n.t("{path} is a folder, not a .csv file", path=path)) from None
    except OSError as e:
        raise TrackError(i18n.t("can't read {path}: {error}", path=path, error=e.strerror or e)) from None
    except UnicodeDecodeError as e:
        raise TrackError(i18n.t("{path} isn't a text file: {error}", path=path, error=e)) from None
    return parse_track(text, path)


def _num(v: float) -> str:
    return repr(round(float(v), 4))


def write_track(path: Path, samples: list[Sample]) -> None:
    """Writes the CSV; the header covers every port seen and the widest list of each kind. A sample that lacks a
    port or a value gets the neutral one (axis 0, button 0, POV -1)."""
    widths: dict[int, dict[str, int]] = {}
    for s in samples:
        for p, pi in s.ports.items():
            w = widths.setdefault(p, {"axis": 0, "button": 0, "pov": 0})
            w["axis"], w["button"], w["pov"] = (max(w["axis"], len(pi.axes)), max(w["button"], len(pi.buttons)),
                                                max(w["pov"], len(pi.povs)))
    ports = sorted(widths)
    head = ["t"]
    for p in ports:
        head += [f"p{p}.axis{i}" for i in range(widths[p]["axis"])]
        head += [f"p{p}.button{i}" for i in range(1, widths[p]["button"] + 1)]
        head += [f"p{p}.pov{i}" for i in range(widths[p]["pov"])]
    rows = [",".join(head)]
    for s in samples:
        row = [_num(s.t)]
        for p in ports:
            pi, w = s.ports.get(p, PortInputs()), widths[p]
            row += [_num(pi.axes[i]) if i < len(pi.axes) else "0.0" for i in range(w["axis"])]
            row += [("1" if pi.buttons[i] else "0") if i < len(pi.buttons) else "0" for i in range(w["button"])]
            row += [str(int(pi.povs[i])) if i < len(pi.povs) else "-1" for i in range(w["pov"])]
        rows.append(",".join(row))
    Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")
