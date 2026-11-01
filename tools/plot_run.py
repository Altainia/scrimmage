"""Plot a run log written by `python -m scrimmage ... --log run.csv`.

    python tools/plot_run.py run.csv -o run.png

Panels: field path (ground truth vs the robot's own odometry), speed, and battery voltage.
Odometry arrives over NetworkTables, so it can trail the ground truth by up to ~100 ms in time;
comparing paths rather than timelines keeps that latency out of the picture.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

TRUTH, ODO = "#2a78d6", "#eb6834"
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
MODE_FILL = {"auto": "#cde2fb", "teleop": "#d4f0e4", "test": "#fbe8c8"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.log)))
    f = lambda k: [float(r[k]) for r in rows]  # noqa: E731
    t, x, y, v, vb = f("t"), f("x"), f("y"), f("speed"), f("vbatt")
    ox, oy = f("odo_x"), f("odo_y")
    has_odo = any(not math.isnan(a) for a in ox)

    plt.rcParams.update({"font.size": 10, "text.color": INK, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "axes.edgecolor": GRID})
    fig = plt.figure(figsize=(11, 6.2), facecolor=SURFACE)
    gs = fig.add_gridspec(2, 2, width_ratios=[1.25, 1], hspace=0.38, wspace=0.22)
    ax_path = fig.add_subplot(gs[:, 0])
    ax_v = fig.add_subplot(gs[0, 1])
    ax_b = fig.add_subplot(gs[1, 1], sharex=ax_v)
    for ax in (ax_path, ax_v, ax_b):
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.8)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)

    # --- path
    ax_path.plot(x, y, color=TRUTH, linewidth=2, label="Ground truth (MuJoCo)")
    if has_odo:
        pts = [(a, b) for a, b in zip(ox, oy) if not math.isnan(a)]
        ax_path.plot(*zip(*pts), color=ODO, linewidth=2, linestyle=(0, (5, 2)), label="Robot odometry (encoders + gyro)")
        ax_path.plot([x[-1], pts[-1][0]], [y[-1], pts[-1][1]], color=INK2, linewidth=1)
        err = math.hypot(x[-1] - pts[-1][0], y[-1] - pts[-1][1])
        ax_path.text(0.015, 0.865, f"Final odometry error: {err * 100:.1f} cm (gray line)",
                     transform=ax_path.transAxes, color=INK2, fontsize=9)
    ax_path.plot(x[0], y[0], "o", color=TRUTH, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2)
    ax_path.annotate("start", (x[0], y[0]), xytext=(-10, -16), textcoords="offset points", color=INK2, fontsize=9)
    ax_path.set_aspect("equal", adjustable="datalim")
    ax_path.set_xlabel("field x (m)")
    ax_path.set_ylabel("field y (m)")
    ax_path.set_title("Path on the field", loc="left", fontsize=11, color=INK)
    ax_path.legend(loc="upper left", frameon=False, fontsize=9)

    # --- mode shading on the time panels
    def shade(ax):
        start = 0
        for i in range(1, len(rows) + 1):
            if i == len(rows) or rows[i]["mode"] != rows[start]["mode"]:
                c = MODE_FILL.get(rows[start]["mode"])
                if c:
                    ax.axvspan(t[start], t[i - 1], color=c, linewidth=0, zorder=0)
                start = i

    shade(ax_v)
    ax_v.plot(t, v, color=TRUTH, linewidth=2)
    ax_v.set_ylabel("speed (m/s)")
    ax_v.set_title("Robot speed", loc="left", fontsize=11, color=INK)
    ax_v.set_ylim(bottom=0)

    shade(ax_b)
    ax_b.plot(t, vb, color=TRUTH, linewidth=2)
    ax_b.axhline(6.8, color="#d03b3b", linewidth=1, linestyle=(0, (4, 3)))
    ax_b.text(t[-1], 6.8, "brownout 6.8 V ", color="#d03b3b", fontsize=8, ha="right", va="bottom")
    ax_b.set_ylabel("volts")
    ax_b.set_xlabel("sim time (s); shading = enabled mode")
    ax_b.set_title("Battery voltage", loc="left", fontsize=11, color=INK)
    ax_b.set_ylim(min(6.0, min(vb) - 0.5), 13)

    if args.title:
        fig.suptitle(args.title, x=0.06, ha="left", fontsize=13, fontweight="bold")
    out = args.out or args.log.with_suffix(".png")
    fig.savefig(out, dpi=130, bbox_inches="tight", facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
