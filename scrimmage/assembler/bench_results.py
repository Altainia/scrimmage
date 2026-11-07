"""What the Bench tab reads back from a results folder: summary.json and summary.txt's headlines (by position,
never by their words), the pictures and CSV a sweep or compare wrote, that CSV as the stage's table, and whether the
energy check failed. The folder is written by bench/report.py (or the page's jobs, bench_jobs.py, which write the
same files); bench_session re-exports the names it and its tests use.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..bench.compare import BENCH_METRICS
from ..bench.report import SUMMARY_DATA


RESULT_SUFFIXES = (".txt", ".csv", ".png", ".gif")   # what a bench writes in its results folder
TABLE_METRICS = ("out", "jammed", "exit_speed", "elevation_deg", "spin_rps", "speed_drop_pct", "peak_load_n")
TABLE_ROWS = 40   # a sweep's table on the stage shows its first rows; results.csv has them all


def _sig(p: Path):
    try:
        st = p.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size



def _summary_data(out: Path | None) -> dict:
    """A results folder's summary.json (bench/report.py's SUMMARY_DATA, written beside summary.txt): summary.txt's
    layout and the energy check as data, no words. {} without a readable one (a folder from before it)."""
    try:
        data = json.loads((out / SUMMARY_DATA).read_text(encoding="utf-8")) if out is not None else {}
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _summary(out: Path | None) -> tuple[list[str], str | None]:
    """(headlines, UNCALIBRATED line) from a results folder's summary.txt, by position, never by their words (spec
    L3): the `headlines` lines summary.json counts after the title block (its `title_lines` lines, notes with blank
    lines included) and one blank line, the last of them the UNCALIBRATED line. A summary.json without title_lines
    (written before it) counts from the file's first blank line. Without summary.json (an older folder), the lines
    from the first blank line up to the next, the last taken as the UNCALIBRATED line. ([], None) when there is
    none."""
    try:
        lines = (out / "summary.txt").read_text(encoding="utf-8").splitlines() if out is not None else []
    except (OSError, UnicodeDecodeError):
        return [], None
    data = _summary_data(out)
    title = data.get("title_lines")
    if isinstance(title, int) and not isinstance(title, bool) and 0 < title < len(lines) and lines[title] == "":
        block = lines[title + 1:]
    elif "" in lines:
        block = lines[lines.index("") + 1:]
    else:
        return [], None
    count = data.get("headlines")
    if isinstance(count, int) and not isinstance(count, bool):
        heads = block[:count] if 0 < count <= len(block) and "" not in block[:count] else []
    else:
        heads = block[:block.index("")] if "" in block else block
    if not heads:
        return [], None
    return heads, (None if data.get("uncalibrated") is False else heads[-1])


def _result_files(kind: str, out: Path | None) -> tuple[list[str], Path | None]:
    """(picture paths, CSV) a sweep or compare wrote in `out`: a compare's compare.png and compare.csv; a sweep's
    results.csv and the charts written with it (line-*.png no older than results.csv: a folder shared with runs or
    older sweeps keeps their pictures, which aren't this sweep's)."""
    if out is None:
        return [], None
    if kind == "compare":
        pic, table = out / "compare.png", out / "compare.csv"
        return ([str(pic)] if pic.is_file() else []), (table if table.is_file() else None)
    table = out / "results.csv"
    since = _sig(table)
    if since is None:
        return [], None
    try:
        charts = sorted((p for p in out.glob("line-*.png") if p.is_file() and (_sig(p) or (0,))[0] >= since[0]),
                        key=lambda p: (not p.name.startswith("line-exit_speed-"), p.name))   # exit speed first: on stage
    except OSError:
        charts = []
    return [str(p) for p in charts], table


def _table(path: Path | None) -> dict | None:
    """{columns, rows} from a sweep's or compare's CSV: the swept keys (or `idea`), then TABLE_METRICS; at most
    TABLE_ROWS rows. None without a readable one."""
    if path is None:
        return None
    try:
        with open(path, newline="", encoding="utf-8") as f:
            lines = list(csv.reader(f))
    except (OSError, UnicodeDecodeError, csv.Error):
        return None
    if not lines:
        return None
    keep = [i for i, c in enumerate(lines[0]) if c not in BENCH_METRICS or c in TABLE_METRICS]
    pick = lambda row: [row[i] if i < len(row) else "" for i in keep]   # noqa: E731
    return {"columns": pick(lines[0]), "rows": [pick(r) for r in lines[1:TABLE_ROWS + 1]]}


def _energy_failed(out: Path | None) -> bool:
    """Whether the results folder's summary.json says the energy check failed (written by report.write_results for
    every run, whoever ran it). False without one."""
    return _summary_data(out).get("energy_failed") is True
