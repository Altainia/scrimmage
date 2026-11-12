"""Checking, sweeping and comparing bench files, and the numbers that go in their tables.

- `check_file`: what `bench check` prints.
- `sweep_bench`: every combination of a file's `[sweep]` values (the launcher model's `combinations`: the first key
  varies slowest), each parsed and checked before the first runs.
- `compare_benches`: two or more files run with one piece and feed: `[piece]` and `[[inlet]]` from `feed_from`, or
  from the first file. Everything else (shapes, shafts, outlets, `[run]`, `[flight]`) is each file's own.
- `metrics`: one row of numbers per run, the columns of the sweep and compare tables, the CSVs and the gallery.
  Exit numbers are for the first outlet in the file (put the main one first), over the pieces that left through it.
"""

import copy
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import i18n
from ..launcher import report as launcher_report
from ..launcher.inputs import LauncherConfigError
from ..launcher.report import check_charts, label
from ..launcher.sweep import combinations
from .build import build_bench, check_clear
from .problems import Problem, collect_problems
from .report import bench_uncalibrated, flex_line
from .run import BenchResult, run_bench
from .spec import Bench, BenchError, _absolute_paths, load_bench, parse_bench, with_overrides

# The table columns. out: pieces through the first outlet; out_other: through any other outlet. exit_*: the first
# outlet's pieces (NaN when none). speed_drop_pct, recovery_s: the shaft that dropped most. peak_load_n: the
# hardest-hit shape. time_inside_s: the median, inlet to outlet.
BENCH_METRICS = ("out", "out_other", "jammed", "escaped", "exit_speed", "exit_speed_sd", "elevation_deg", "spin_rps",
                 "speed_drop_pct", "recovery_s", "peak_load_n", "time_inside_s")
SHOWN = {"exit_speed": "speed", "exit_speed_sd": "speed", "peak_load_n": "force"}   # charted in the Units setting
NOISE = 1e-6  # a spread under this fraction of its mean, or a number under this, is rounding noise: shown as 0
CHART_KINDS = ("line",)  # a bench sweep charts a metric against a swept key; the launcher's shaft and landing charts
                         # need its own traces


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def check_file(path: Path) -> tuple[Bench, str]:
    """Load and check a bench file without running it, every [sweep] combination too; the bench and a one-line
    summary (`ok: name: 2 wheels, ...`). Raises BenchError, naming every error."""
    bench, summary, _ = check_problems(path)
    return bench, summary


def check_problems(path: Path) -> tuple[Bench, str, list[Problem]]:
    """`check_file`'s bench and summary, and the file's warnings (bench.warnings, overlapping parts) as problems."""
    bench, problems = collect_problems(path)
    if bench is None:
        text = [m if m.startswith(str(path)) else f"{path}: {m}" for m in (p.message for p in problems)]
        raise BenchError("\n".join(text))
    check_bench_charts(bench)
    if bench.sweep:
        sweep_benches(path)  # every combination parsed and built, as `bench sweep` does before running
    counts = [_plural(len(xs), w) for xs, w in ((bench.wheels, "wheel"), (bench.plates, "plate"),
                                                 (bench.bars, "bar"), (bench.curves, "curve"),
                                                 (bench.cads, "CAD part")) if xs]
    pieces = sum(i.count for i in bench.inlets)
    counts += [f"{_plural(len(bench.inlets), 'inlet')} ({_plural(pieces, 'piece')})",
               _plural(len(bench.outlets), "outlet")]
    if bench.sweep:
        counts.append(f"a [sweep] of {_plural(len(combinations(bench.sweep)), 'combination')}")
    return bench, f"ok: {bench.name}: {', '.join(counts)}", problems


def check_bench_charts(bench: Bench) -> None:
    """The file's [[chart]] entries against its swept keys and BENCH_METRICS; BenchError if one is bad."""
    try:
        check_charts(bench.charts, list(bench.sweep), BENCH_METRICS, CHART_KINDS)
    except LauncherConfigError as e:
        raise BenchError(str(e)) from None


def default_charts(keys: list[str]) -> list[dict]:
    """Exit speed against the first swept key, one line per value of the second."""
    if not keys:
        return []
    line = {"kind": "line", "x": keys[0], "y": "exit_speed"}
    if len(keys) > 1:
        line["series"] = keys[1]
    return [line]


def sweep_benches(path: Path, sweep: dict[str, list] | None = None) -> tuple[Bench, list[tuple[dict, Bench]]]:
    """The file's bench and every combination of its [sweep], parsed and checked (inlets clear) before anything runs.
    A bad combination raises BenchError prefixed with it (`wheel.top.diameter=-1: ...`). `sweep` ({dotted key:
    values}) replaces the file's own [sweep], and drops its [[chart]] (which names the file's swept keys)."""
    bench = load_bench(path)
    if sweep is not None:
        raw = {k: v for k, v in bench.raw.items() if k != "chart"}
        try:
            bench = parse_bench({**raw, "sweep": sweep}, bench.base_dir)
        except BenchError as e:
            raise BenchError(i18n.t("{path}: {error}", path=path, error=e), e.key) from None
    check_bench_charts(bench)
    out = []
    for combo in combinations(bench.sweep):
        try:
            b = parse_bench(with_overrides(bench.raw, combo), bench.base_dir)
            check_clear(build_bench(b))
        except BenchError as e:
            raise BenchError(i18n.t("{path}: {combination}: {error}", path=path, combination=label(combo), error=e)) \
                from None
        out.append((combo, b))
    return bench, out


def sweep_bench(path: Path, trace_hz: float = 60.0) -> list[tuple[dict, BenchResult]]:
    """Run every combination of the file's [sweep] values (a file with no [sweep] runs once, with an empty combo)."""
    _, benches = sweep_benches(path)
    return [(combo, run_bench(b, trace_hz=trace_hz)) for combo, b in benches]


def same_feed(paths: list[Path], feed_from: Path | None = None) -> list[Bench]:
    """Each file's bench with `[piece]` and `[[inlet]]` replaced by `feed_from`'s (default: the first file's), all
    checked before anything runs."""
    paths = [Path(p) for p in paths]
    if len(paths) < 2:
        raise BenchError(i18n.t("compare needs at least two bench files, got {n}", n=len(paths)))
    source = load_bench(feed_from if feed_from is not None else paths[0])
    piece = _absolute_paths({"piece": source.raw["piece"]}, source.base_dir)["piece"]  # a mesh path stays its file's
    inlets = copy.deepcopy(source.raw.get("inlet", []))
    out = []
    for p in paths:
        own = load_bench(p)
        raw = {**own.raw, "piece": copy.deepcopy(piece), "inlet": copy.deepcopy(inlets)}
        for key in ("sweep", "chart"):  # compare runs each file once; its sweep may name the inlets just replaced
            raw.pop(key, None)
        try:
            b = parse_bench(raw, own.base_dir)
            check_clear(build_bench(b))
        except BenchError as e:
            raise BenchError(i18n.t("{path}, with the piece and feed of {source}: {error}", path=p,
                                    source=feed_from or paths[0], error=e)) from None
        out.append(b)
    return out


def compare_benches(paths: list[Path], feed_from: Path | None = None,
                    trace_hz: float = 60.0) -> list[BenchResult]:
    """Run two or more bench files with one piece and feed (see same_feed), in order."""
    return [run_bench(b, trace_hz=trace_hz) for b in same_feed(paths, feed_from)]


# ----------------------------------------------------------------------------------------------------- metrics

def metrics(result: BenchResult) -> dict[str, float]:
    """One row of BENCH_METRICS for a run (NaN where there is nothing to measure)."""
    nan = float("nan")
    main = result.bench.outlets[0].name if result.bench.outlets else None
    out = [p for p in result.pieces if p.status == "out" and p.outlet == main]
    speeds = np.array([p.speed for p in out])
    spins = [float(np.linalg.norm(p.spin)) / (2 * math.pi) for p in out if p.spin is not None]
    worst = max(result.shafts.values(), key=lambda s: s.drop_pct, default=None)
    if worst is not None and worst.drop_pct <= 0.0:
        worst = None
    peak = max((ld.peak_force_n for ld in result.loads.values()), default=0.0)
    inside = [p.t_out - p.t_in for p in out]
    row = {
        "out": len(out),
        "out_other": sum(p.status == "out" and p.outlet != main for p in result.pieces),
        "jammed": sum(p.status == "jammed" for p in result.pieces),
        "escaped": sum(p.status == "escaped" for p in result.pieces),
        "exit_speed": float(speeds.mean()) if out else nan,
        "exit_speed_sd": float(speeds.std()) if len(out) > 1 else nan,
        "elevation_deg": float(np.mean([p.elevation_deg for p in out])) if out else nan,
        "spin_rps": float(np.mean(spins)) if spins else nan,
        "speed_drop_pct": worst.drop_pct if worst else 0.0,
        "recovery_s": worst.recovery_s if worst and worst.recovery_s is not None else nan,
        "peak_load_n": peak,
        "time_inside_s": float(np.median(inside)) if inside else nan,
    }
    return _quiet(row)


def _quiet(row: dict[str, float]) -> dict[str, float]:
    """Rounding noise to 0, so tables print `0` and not `2.308e-10`: a spread under NOISE of its mean (identical
    pieces), and any number under NOISE in size (a level `-1.2e-15` degrees)."""
    out = dict(row)
    sd, mean = out["exit_speed_sd"], out["exit_speed"]
    if not math.isnan(sd) and sd < NOISE * abs(mean):
        out["exit_speed_sd"] = 0.0
    for k, v in out.items():
        if isinstance(v, float) and abs(v) < NOISE:
            out[k] = 0.0
    return out


def format_table(rows: list[tuple[dict, "Row"]], bench: Bench) -> str:
    """The launcher model's table of BENCH_METRICS, headed by the bench's UNCALIBRATED line (not the launcher's): SHOWN
    in the Units setting, as the charts are."""
    table = launcher_report.format_table(rows, BENCH_METRICS, shown=SHOWN).split("\n", 1)[1]
    flex = flex_line(bench)     # a flex sweep is experimental: said first, as its runs' headlines do
    return (f"{flex}\n" if flex else "") + f"{bench_uncalibrated(bench)}\n{table}"


@dataclass
class Row:
    """A run's metrics in the shape the launcher model's table, CSV and chart writers read (`.metrics`)."""
    metrics: dict[str, float]


def rows(runs: list[tuple[dict, BenchResult]]) -> list[tuple[dict, Row]]:
    return [(combo, Row(metrics(r))) for combo, r in runs]


def idea_labels(results: list[BenchResult]) -> list[str]:
    """Each run's bench name, numbered when two share one."""
    names = [r.bench.name for r in results]
    return [n if names.count(n) == 1 else f"{n} ({names[:i + 1].count(n)})" for i, n in enumerate(names)]
