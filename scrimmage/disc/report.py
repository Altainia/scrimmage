"""What `scrimmage disc` prints and writes: the table, results.csv, summary.md and charts, through the ball
launcher's report functions (given the disc's result columns)."""

import math
from pathlib import Path

from ..launcher import report
from ..measure import labels
from .inputs import DiscSetup, load_setup
from .run import METRIC_KEYS, exit_table
from .sweep import run_all, run_one

CHART_KINDS = ("line", "shaft")
DISC_UNCALIBRATED = labels.line({}, labels.DISC) + "."   # a disc setup takes no measurement file yet


def run_and_write(path: Path, out: Path, sweep: bool) -> list[Path] | None:
    """Run the file (every `[sweep]` combination, or just its setup), print the table and write results.csv,
    summary.md and charts into `out`. Returns the charts written (None without matplotlib)."""
    _, swept, charts = load_setup(path)
    report.check_charts(charts, list(swept), METRIC_KEYS, CHART_KINDS)  # before anything runs
    keys = list(swept) if sweep else []
    rows, _ = run_all(path) if sweep else run_one(path)
    # One setup, nothing swept: a line chart (over swept keys) has nothing to draw, as in the ball launcher's `run`.
    charts = charts if sweep else [c for c in charts if c.get("kind") != "line"]
    out.mkdir(parents=True, exist_ok=True)
    report.write_csv(rows, out / "results.csv", METRIC_KEYS)
    table = report.format_table(rows, METRIC_KEYS, label=DISC_UNCALIBRATED)
    print(table)
    print(f"wrote {out / 'results.csv'}")
    (out / "summary.md").write_text(
        f"# Disc launcher: {Path(path).name}\n\n{DISC_UNCALIBRATED}\n\n```\n{table.split(chr(10), 1)[1]}\n```\n\n"
        "Columns are explained in docs/disc.md, \"Reading results.csv\".\n", encoding="utf-8")
    print(f"wrote {out / 'summary.md'}")
    return report.write_charts(rows, charts or [c for c in report.default_charts(keys) if c["kind"] in CHART_KINDS], out, None, METRIC_KEYS, CHART_KINDS, footer=DISC_UNCALIBRATED)


def exit_table_text(setup: DiscSetup, rpms: list[float]) -> str:
    rows = exit_table(setup, rpms)
    keys = ["rpm", "surface", "exit_speed", "ratio", "spin_rpm", "advance_ratio"]
    header = ["rpm", "surface_m_s", *keys[2:]]
    cells = [[f"{r['rpm']:g}", *(f"{r[k]:.4g}" for k in keys[1:])] for r in rows]
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, h in enumerate(header)]
    line = lambda cs: "  ".join(c.rjust(w) for c, w in zip(cs, widths))
    good = [r for r in rows if r["surface"] > 0 and not math.isnan(r["exit_speed"])]
    if good:
        slope = sum(r["surface"] * r["exit_speed"] for r in good) / sum(r["surface"] ** 2 for r in good)
        fit = f"exit speed = {slope:.3g} x wheel surface speed (least squares through the origin)"
    else:
        fit = "no disc launched: no fit (exit speed = nan x wheel surface speed)"
    return "\n".join([DISC_UNCALIBRATED, line(header), *(line(c) for c in cells), fit])
