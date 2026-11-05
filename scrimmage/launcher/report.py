"""Terminal table, CSV and charts for launcher runs."""

import csv
import math
import textwrap
from pathlib import Path

from .. import i18n
from .. import units
from ..measure import labels
from .inputs import Goal, LauncherConfigError
from .run import METRIC_KEYS, RunResult

def uncalibrated() -> str:
    """The line for setups that use no measurement, in the language now (American English in the CLI)."""
    return labels.line({}, labels.LAUNCHER) + "."

FOOTER_WIDTH = 135   # characters: a longer chart footer wraps, below the chart, so it doesn't widen the PNG; every
#   no-measurement line (the launcher's 122, the disc's 127) fits on one, as before measurements
NO_MATPLOTLIB = "charts need matplotlib: pip install matplotlib"

Rows = list[tuple[dict, RunResult]]


def calibration(setups) -> str:
    """The line for runs of `setups` (each with its `provenance`): which of the ball's stiffness, its bounce and the
    wheel's friction were measured, and from which file, and which are still estimates. A number measured in some
    runs only (a swept one) reads as an estimate."""
    return labels.line(labels.common([s.provenance for s in setups]), labels.LAUNCHER) + "."


def _fmt(v) -> str:
    """4 significant figures for floats (`nan` for NaN), plain text for everything else."""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{float(v):.4g}"
    if isinstance(v, (list, tuple)):  # list-valued inputs: robot.velocity, feed.lanes, wheels.positions
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return str(v)


def _swept_keys(rows: Rows) -> list[str]:
    return list(rows[0][0]) if rows else []


def _cells(rows: Rows, metrics=METRIC_KEYS) -> tuple[list[str], list[list[str]]]:
    keys = _swept_keys(rows)
    header = [*keys, *metrics]
    body = [[*(_fmt(c[k]) for k in keys), *(_fmt(r.metrics[m]) for m in metrics)] for c, r in rows]
    return header, body


def format_table(rows: Rows, metrics=METRIC_KEYS, label: str | None = None) -> str:
    """`label` (default uncalibrated(), or calibration()'s line), then a header and one line per row: the swept values and
    `metrics` (the rows' `.metrics`): the ball launcher's by default, the disc launcher and the bench pass their own."""
    label = uncalibrated() if label is None else label
    header, body = _cells(rows, metrics)
    widths = [max(len(h), *(len(r[i]) for r in body)) if body else len(h) for i, h in enumerate(header)]
    line = lambda cells: "  ".join(c.rjust(w) for c, w in zip(cells, widths)).rstrip()
    return "\n".join([label, line(header), *(line(r) for r in body)])


def write_csv(rows: Rows, path: Path, metrics=METRIC_KEYS) -> None:
    header, body = _cells(rows, metrics)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(body)


def default_charts(keys: list[str]) -> list[dict]:
    charts = []
    if keys:
        line = {"kind": "line", "x": keys[0], "y": "exit_speed"}
        if len(keys) > 1:
            line["series"] = keys[1]
        charts.append(line)
    return [*charts, {"kind": "shaft"}, {"kind": "landing"}]


CHART_KINDS = ("line", "landing", "shaft")


def check_charts(charts: list[dict], keys: list[str], metrics=METRIC_KEYS, kinds=CHART_KINDS) -> None:
    """Raise LauncherConfigError for a bad [[chart]] spec: an unknown kind, or a line chart whose x or series is not
    one of the swept `keys` or whose y is not a metric. Called before anything runs, and again by write_charts."""
    for spec in charts:
        kind = spec.get("kind")
        if kind not in kinds:
            raise LauncherConfigError(i18n.t("[[chart]] kind must be one of: {kinds}; got {kind}",
                                             kinds=', '.join(kinds), kind=repr(kind)))
        if kind != "line":
            continue
        x, y, series = spec.get("x"), spec.get("y"), spec.get("series")
        for what, key in (("x", x), ("series", series)):
            if key is not None and key not in keys:
                raise LauncherConfigError(
                    i18n.t("[[chart]] {what} = {key} is not a swept key; swept keys: {keys}",
                           what=what, key=repr(key), keys=', '.join(keys) or i18n.t("none")))
        if x is None or y not in metrics:
            raise LauncherConfigError(
                i18n.t("[[chart]] line needs x (a swept key) and y (one of: {metrics}); got y = {y}",
                       metrics=', '.join(metrics), y=repr(y)))


def label(combo: dict) -> str:
    return ", ".join(f"{k}={_fmt(v)}" for k, v in combo.items()) or "run"


def write_charts(rows: Rows, charts: list[dict], out_dir: Path, goals: list[Goal] | None = None,
                 metrics=METRIC_KEYS, kinds=CHART_KINDS, *, footer: str,
                 shown: dict[str, str] | None = None) -> list[Path] | None:
    """Write one PNG per chart, `footer` (the result's own UNCALIBRATED line: calibration()'s for a ball launcher,
    the disc's or the bench's) under it, wrapped at FOOTER_WIDTH; None when matplotlib is missing. `goals` (aligned with rows) sizes the landing circle. The disc launcher passes its own `metrics` and
    `kinds` (line and shaft: its shots have no ball landing); the bench passes its own metrics and line charts only.
    `shown` ({metric: units kind}, the bench's) charts those metrics in the Units setting, the unit on the axis
    ("exit_speed (ft/s)"); the CSV beside them stays SI."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
        from matplotlib.patches import Circle
    except ImportError:
        return None
    keys = _swept_keys(rows)
    charts = charts or default_charts(keys)
    check_charts(charts, keys, metrics, kinds)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def save(fig, name: str) -> None:
        lines = textwrap.wrap(footer, FOOTER_WIDTH)
        if len(lines) == 1:
            fig.text(0.5, 0.005, footer, ha="center", va="bottom", fontsize=7, color="#a33")
        else:   # under the figure, where the tight bounding box makes room for it
            fig.text(0.5, 0.0, "\n".join(lines), ha="center", va="top", fontsize=7, color="#a33")
        path = out_dir / name
        n = 2
        while path in written:
            path = out_dir / f"{Path(name).stem}-{n}.png"
            n += 1
        fig.savefig(path, dpi=100, bbox_inches="tight")
        plt.close(fig)
        written.append(path)

    for spec in charts:
        kind = spec.get("kind")
        if kind == "line":
            x, y, series = spec.get("x"), spec.get("y"), spec.get("series")
            y_kind = (shown or {}).get(y)
            fig, ax = plt.subplots(figsize=(7, 4.5))
            # Swept values may be lists (robot.velocity, feed.lanes), which can't be hashed or ordered: group and
            # place them by repr, in first-seen order.
            groups: dict[str, tuple[object, list]] = {}
            for c, r in rows:
                s_val = c[series] if series else None
                v = r.metrics[y]
                groups.setdefault(repr(s_val), (s_val, []))[1].append((c[x], units.to_display(v, y_kind) if y_kind else v))
            numeric = all(isinstance(c[x], (int, float)) and not isinstance(c[x], bool) for c, _ in rows)
            order = list(dict.fromkeys(repr(c[x]) for c, _ in rows)) if not numeric else []
            ticks = {repr(c[x]): c[x] for c, _ in rows}
            for s_val, pts in groups.values():
                xs = [p[0] if numeric else order.index(repr(p[0])) for p in pts]
                ax.plot(xs, [p[1] for p in pts], marker="o", label=f"{series}={_fmt(s_val)}" if series else None)
            if not numeric:
                ax.set_xticks(range(len(order)), [_fmt(ticks[o]) for o in order])
            ax.set_xlabel(x)
            ax.set_ylabel(f"{y} ({units.unit_name(y_kind)})" if y_kind else y)
            if series:
                ax.legend()
            ax.grid(alpha=0.3)
            fig.subplots_adjust(bottom=0.15)
            save(fig, f"line-{y}-vs-{x}.png")
        elif kind == "shaft":
            fig, ax = plt.subplots(figsize=(7, 4.5))
            for c, r in rows:
                if r.trace:
                    ax.plot([t for t, _ in r.trace], [v for _, v in r.trace], label=label(c), linewidth=1)
            ax.set_xlabel("time (s)")
            ax.set_ylabel("shaft speed (rps)")
            ax.grid(alpha=0.3)
            if keys:
                ax.legend(fontsize=6)
            fig.subplots_adjust(bottom=0.15)
            save(fig, "shaft.png")
        elif kind == "landing":
            n = len(rows)
            cols = min(n, 3)
            nrows = math.ceil(n / cols)
            fig, axes = plt.subplots(nrows, cols, figsize=(4 * cols, 4 * nrows), squeeze=False)
            for i, (c, r) in enumerate(rows):
                ax = axes[i // cols][i % cols]
                if goals:
                    g = goals[i]
                    ax.add_patch(Circle((g.distance, 0.0), g.diameter / 2.0, fill=False, color="k"))
                    ax.plot([g.distance], [0.0], "k+")
                hits = [l.at_goal for _, l in r.shots if l is not None and l.at_goal is not None]
                misses = [l.floor for _, l in r.shots if l is not None and l.at_goal is None]
                if hits:
                    ax.scatter([p[0] for p in hits], [p[1] for p in hits], marker="o", label="at goal height")
                if misses:
                    ax.scatter([p[0] for p in misses], [p[1] for p in misses], marker="x", color="tab:red",
                               label="floor (never reached goal)")
                ax.set_title(label(c), fontsize=7)
                ax.set_aspect("equal", adjustable="datalim")
                ax.autoscale_view()
                ax.set_xlabel("downrange (m)")
                ax.set_ylabel("lateral (m)")
                ax.grid(alpha=0.3)
                if hits or misses:
                    ax.legend(fontsize=6)
            for j in range(n, nrows * cols):
                axes[j // cols][j % cols].axis("off")
            fig.tight_layout(rect=(0, 0.03, 1, 1))
            save(fig, "landing.png")
    return written
