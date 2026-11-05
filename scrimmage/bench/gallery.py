"""A session's ideas side by side: the compare picture, and the gallery, one HTML file a browser opens with no server.

The gallery runs every bench file in a folder (not its subfolders) once, ignoring `[sweep]`, and lists each idea:
its author, its one-line idea, its side path picture (embedded as base64 PNG, so the file can be put on a projector
or emailed on its own) and its headlines, then a table comparing every idea. A file that doesn't load or run is
listed with its problem rather than stopping the gallery: one typo shouldn't cost the room the rest of the session.

The gallery is for people: its words are in the Units and Language settings when it is written (a translation's text
is put in as it is, with what goes into it escaped). The UNCALIBRATED line is report.py's, untranslated.
"""

import base64
import html
import io
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image

from .. import i18n, units
from ..i18n import t
from .compare import idea_labels, metrics
from .pictures import PICTURE_TRACE_HZ, path_picture
from .report import headlines, uncalibrated
from .run import BenchResult, BenchRunError, run_bench
from .spec import BenchError, load_bench

COMPARE_COLUMNS = 3          # pictures per row in compare_picture
COMPARE_SIZE = (960, 540)    # each picture's width and height (px)
GUTTER = 12                  # px between pictures
GALLERY_SIZE = (1280, 720)   # each idea's picture in the gallery


@i18n.for_people()
def compare_picture(results: list[BenchResult], width: int = COMPARE_SIZE[0],
                    height: int = COMPARE_SIZE[1]) -> np.ndarray:
    """The side path pictures in a grid, side by side, up to COMPARE_COLUMNS to a row (RGB, uint8). Each keeps its own
    scale bar: the pictures are framed one by one, so equal sizes on screen are not equal sizes in meters."""
    if not results:
        raise ValueError("compare_picture needs at least one result")
    cols = min(len(results), COMPARE_COLUMNS)
    nrows = math.ceil(len(results) / cols)
    sheet = Image.new("RGB", (cols * width + (cols - 1) * GUTTER, nrows * height + (nrows - 1) * GUTTER),
                      (200, 204, 212))
    for i, r in enumerate(results):
        x, y = (i % cols) * (width + GUTTER), (i // cols) * (height + GUTTER)
        sheet.paste(Image.fromarray(path_picture(r, "side", width, height)), (x, y))
    return np.asarray(sheet)


# ----------------------------------------------------------------------------------------------------- gallery

@dataclass
class Entry:
    file: Path
    result: BenchResult | None = None
    problem: str | None = None


def gallery_files(folder: Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise BenchError(f"{folder} is not a folder of bench files")
    files = sorted(folder.glob("*.toml"))
    if not files:
        raise BenchError(f"{folder} has no bench files (*.toml)")
    return files


def run_folder(folder: Path) -> list[Entry]:
    """Every bench file in the folder, run once at the picture trace rate; problems are kept, not raised."""
    out = []
    for f in gallery_files(folder):
        try:
            out.append(Entry(f, result=run_bench(load_bench(f), trace_hz=PICTURE_TRACE_HZ)))
        except (BenchError, BenchRunError, OSError) as e:
            out.append(Entry(f, problem=str(e)))
    return out


def _png64(img: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _words(line: str) -> str:
    """A headline as HTML: escaped, with the file's `names` in code."""
    return re.sub(r"`([^`]*)`", r"<code>\1</code>", html.escape(line))


def _num(v: float, digits: int = 1, unit: str = "") -> str:
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{digits}f}{unit}"


CSS = """
:root { --ink: #191923; --muted: #55586a; --line: #d5d8e0; --band: #f0f2f6; --accent: #b3261e; }
* { box-sizing: border-box; }
body { margin: 0; background: #ffffff; color: var(--ink); font: 18px/1.45 system-ui, -apple-system, "Segoe UI",
       Roboto, sans-serif; }
main { max-width: 1320px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 2.1rem; margin: 0 0 4px; }
.sub { color: var(--muted); margin: 0 0 20px; }
.warn { border-left: 6px solid var(--accent); background: #fbeeee; padding: 10px 14px; margin: 16px 0; }
nav ol { columns: 2; padding-left: 1.4em; }
section { border-top: 2px solid var(--line); padding: 24px 0 8px; }
h2 { font-size: 1.7rem; margin: 0; }
.author { color: var(--muted); font-size: 1.15rem; margin: 2px 0 8px; }
blockquote { margin: 0 0 14px; font-size: 1.35rem; font-style: italic; }
img { display: block; width: 100%; height: auto; border: 1px solid var(--line); }
ul.head { font-size: 1.2rem; padding-left: 1.2em; }
.file { color: var(--muted); font-size: 0.9rem; word-break: break-all; }
.problem { white-space: pre-wrap; background: var(--band); padding: 10px 14px; }
.wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 1.05rem; }
th, td { border-bottom: 1px solid var(--line); padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: var(--band); }
td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
code { font-size: 0.95em; background: var(--band); padding: 0 4px; border-radius: 2px; }   /* app.css --radius-sm */
"""


def _idea_html(e: Entry, label: str, anchor: str) -> str:
    if e.result is None:
        return (f'<section id="{anchor}"><h2>{html.escape(e.file.name)}</h2>'
                f'<p class="problem">{t("This file did not run: {problem}", problem=html.escape(e.problem or ""))}</p>'
                '</section>')
    b, lines = e.result.bench, headlines(e.result)
    parts = [f'<section id="{anchor}"><h2>{html.escape(label)}</h2>']
    if b.author:
        parts.append(f'<p class="author">{html.escape(b.author)}</p>')
    if b.idea:
        parts.append(f"<blockquote>“{html.escape(b.idea)}”</blockquote>")
    alt = t("{name} from the side, with every piece's path", name=b.name)
    parts.append(f'<img alt="{html.escape(alt)}" src="data:image/png;base64,'
                 f'{_png64(path_picture(e.result, "side", *GALLERY_SIZE))}">')
    parts.append('<ul class="head">' + "".join(f"<li>{_words(x)}</li>" for x in lines[:-1]) + "</ul>")
    if b.notes:
        parts.append(f"<p>{html.escape(b.notes)}</p>")
    parts.append(f'<p class="file">{html.escape(e.file.name)}</p></section>')  # no home folders in a shared page
    return "".join(parts)


def _table_html(entries: list[tuple[Entry, str, str]]) -> str:
    head = (t("idea"), t("author"), t("got out"), t("exit speed ({unit})", unit=units.unit_name("speed")),
            t("direction"), t("didn't get out"), t("worst shaft drop"),
            t("hardest hit ({unit})", unit=units.unit_name("force")))
    speed_of = lambda v: units.to_display(v, "speed")   # noqa: E731 - the Units setting, as the headlines
    trs = []
    for e, label, anchor in entries:
        if e.result is None:
            trs.append(f'<tr><td><a href="#{anchor}">{html.escape(e.file.name)}</a></td>'
                       f'<td colspan="{len(head) - 1}">{t("did not run")}</td></tr>')
            continue
        r, m = e.result, metrics(e.result)
        n = len(r.pieces)
        main = r.bench.outlets[0].name if r.bench.outlets else "-"
        speed = _num(speed_of(m["exit_speed"])) + ("" if math.isnan(m["exit_speed_sd"])
                                                   else f" ± {speed_of(m['exit_speed_sd']):.1f}")
        e_deg = m["elevation_deg"]
        way = "–" if math.isnan(e_deg) else t("{deg}° up", deg=f"{e_deg:.0f}") if e_deg >= 0.5 \
            else t("{deg}° down", deg=f"{-e_deg:.0f}") if e_deg <= -0.5 else t("level")
        stuck = n - m["out"] - m["out_other"]
        cells = [f'<a href="#{anchor}">{html.escape(label)}</a>', html.escape(r.bench.author),
                 (t("{out} of {n} through {outlet}, {other} elsewhere", out=m["out"], n=n, other=m["out_other"],
                    outlet=f"<code>{html.escape(main)}</code>") if m["out_other"] else
                  t("{out} of {n} through {outlet}", out=m["out"], n=n, outlet=f"<code>{html.escape(main)}</code>")),
                 speed, way, str(stuck), _num(m["speed_drop_pct"], 0, "%"),
                 _num(units.to_display(m["peak_load_n"], "force"), 0)]
        numeric = {3, 5, 6, 7}
        trs.append("<tr>" + "".join(f'<td class="n">{c}</td>' if i in numeric else f"<td>{c}</td>"
                                    for i, c in enumerate(cells)) + "</tr>")
    return ('<div class="wrap"><table><tr>' + "".join(f"<th>{html.escape(h, quote=False)}</th>" for h in head) + "</tr>"
            + "".join(trs) + "</table></div>")


@i18n.for_people()       # the page is for people (an MCP gallery's too)
def gallery_html(folder: Path, entries: list[Entry]) -> str:
    ran = [e.result for e in entries if e.result is not None]
    labels = iter(idea_labels(ran))
    named = [(e, next(labels) if e.result is not None else e.file.name, f"idea-{i + 1}") for i, e in enumerate(entries)]
    title = t("Bench gallery: {folder}", folder=Path(folder).resolve().name)
    warn = (uncalibrated(ran[0]) if ran else "UNCALIBRATED: estimates from preset numbers, not measurements.")
    kinds = {r.bench.piece.kind for r in ran}
    if len(kinds) > 1:
        warn = "UNCALIBRATED: estimates from preset numbers, not measurements; see docs/bench.md."
    body = [f"<h1>{html.escape(title)}</h1>",
            '<p class="sub">' + html.escape(t(
                "{count} ideas, made {date} with scrimmage bench gallery. Paths: green left through an outlet, red "
                "jammed, orange escaped, gray still inside or never got in.", count=len(entries),
                date=date.today().isoformat()), quote=False) + "</p>",
            f'<p class="warn">{_words(warn)} '
            + html.escape(t("Compare ideas with each other; trust the differences before the numbers."), quote=False)
            + "</p>",
            "<nav><ol>" + "".join(f'<li><a href="#{a}">{html.escape(lab)}</a>'
                                  + (f" ({html.escape(e.result.bench.author)})"
                                     if e.result is not None and e.result.bench.author else "")
                                  + "</li>" for e, lab, a in named) + "</ol></nav>"]
    body += [_idea_html(e, lab, a) for e, lab, a in named]
    body += [f'<section id="compare"><h2>{html.escape(t("Every idea"), quote=False)}</h2>', _table_html(named),
             '<p class="sub">' + html.escape(t(
                 "Exit numbers are for each file's first outlet. \"Worst shaft drop\" is the largest dip in a "
                 "shaft's speed as a piece goes through; \"hardest hit\" the largest contact force on any one "
                 "part."), quote=False) + "</p>"
             "</section>"]
    return (f"<!doctype html>\n<html lang=\"{i18n.current_language()}\"><head><meta charset=\"utf-8\">"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
            f"<body><main>{''.join(body)}</main></body></html>\n")


def write_gallery(folder: Path, out: Path, entries: list[Entry] | None = None) -> Path:
    """Run every bench file in `folder` (or take `entries`, from run_folder) and write the gallery to `out`: one
    self-contained HTML file."""
    entries = run_folder(folder) if entries is None else entries
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(gallery_html(folder, entries), encoding="utf-8")
    return out
