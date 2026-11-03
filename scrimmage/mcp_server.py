"""MCP server: lets an agent run a robot program in scrimmage and "step, look, poke".

Run as `scrimmage-mcp` (stdio). Register it with an MCP client, e.g. Claude Code:
    claude mcp add scrimmage -- /path/to/.venv/bin/scrimmage-mcp

stdout carries the MCP protocol, so nothing here may print to it; logging goes to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import signal
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .config import ConfigError
from .lockstep import parse_speed
from .scenario import ScenarioError, load_scenario
from .scenario_runner import run_scenario as _run_scenario
from .session import Session, SessionError

MAX_ADVANCE = 60.0
MAX_IMAGES = 6

INSTRUCTIONS = """\
scrimmage simulates a WPILib (FRC) robot program in a physics world. Typical loop:
start_session(config=<robot config .toml>) -> set_mode("auto") -> advance(seconds) -> screenshot("top") / get_state()
-> edit robot code -> restart_robot() -> repeat. The simulation is held (paused, robot code included) between calls,
so take your time (except robots with Phoenix 6 devices: they run in real time and keep running between calls). Every result includes t, the simulated time in seconds. Pokes (set_pose, push, move_body) change
physics only; the robot's own odometry isn't told. run_scenario runs a scenario file's checks and returns its report.

The component bench (bench_check, bench_run, bench_sweep, bench_compare, bench_gallery, bench_focus) tries a mechanism idea with
game pieces, no robot or session needed: write a bench file (TOML) with your own file tools, following {bench_docs}
(examples, each written as a student's idea: {bench_examples}), then bench_check it and bench_run it. Pass absolute
paths. A Bench tab (`scrimmage edit <folder>`) may be showing the session: bench_focus pins the numbers you are
talking about there.

Block tests (test_run, test_sweep, test_spread) try a mechanism on a robot with no robot program: write a test file
(<name>.test.toml) with your own file tools, then test_run it. test_sweep tries one number in the test or the robot at
several values; test_spread tries the floor ball at each placement of the test's floor spread. Pass absolute paths.
Each run is kept next to the test file, where the app's Test tab can replay it.
"""


def _instructions() -> str:
    """INSTRUCTIONS with the bench docs' and examples' full paths when scrimmage runs from its checkout (pip install
    -e), so an agent started in another folder can find them; else where they are in the repository."""
    root = Path(__file__).resolve().parent.parent
    docs, examples = root / "docs" / "bench.md", root / "examples" / "bench"
    if docs.is_file() and examples.is_dir():
        return INSTRUCTIONS.format(bench_docs=str(docs), bench_examples=str(examples))
    return INSTRUCTIONS.format(bench_docs="docs/bench.md in the scrimmage repository",
                               bench_examples="examples/bench/ there")


INSTRUCTIONS = _instructions()

server = MCPServer(name="scrimmage", instructions=INSTRUCTIONS)
_session: Session | None = None
_session_lock: asyncio.Lock | None = None  # start_session / stop_session run one at a time
_lock_loop: asyncio.AbstractEventLoop | None = None


def _lock() -> asyncio.Lock:
    """The session lock, created on first use in the running loop (a lock is bound to one loop)."""
    global _session_lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _session_lock is None or _lock_loop is not loop:
        _session_lock, _lock_loop = asyncio.Lock(), loop
    return _session_lock


def _json(d: Any) -> str:
    return json.dumps(d, separators=(",", ":"), default=str)


def _fail(e: Exception) -> ToolError:
    if isinstance(e, SessionError):
        return ToolError(_json(e.to_dict()))
    return ToolError(str(e))


def _need() -> Session:
    if _session is None:
        raise ToolError("no session: call start_session(config=...) first")
    return _session


def _check_seconds(seconds: float) -> None:
    if isinstance(seconds, bool) or not 0 < seconds <= MAX_ADVANCE:  # NaN fails the comparison too
        raise ToolError(f"seconds must be > 0 and at most {MAX_ADVANCE:g} per call")


@server.tool()
async def start_session(config: str, attach: bool = False, viewer: bool = False, replace: bool = False,
                        speed: str | float = "max", sim_mode: str | None = None) -> str:
    """Start simulating a robot program. config: path to a robot config TOML (model, bindings, [robot_program]);
    a relative path is resolved against the MCP server's working directory, so prefer an absolute path.
    attach: wait for a robot program you start yourself instead of launching one. viewer: open a 3D window.
    speed: "max" (default) or a real-time factor (a number), used while advancing. replace: stop a running session
    first. sim_mode: "fast" or "phoenix", overriding the config's [robot_program] sim_mode (fast mode needs robot
    code that checks Scrimmage.fastMode()). The session starts held at t=0, disabled."""
    global _session
    async with _lock():
        if _session is not None:
            if not replace:
                raise ToolError("a session is already running: call stop_session first, or pass replace=true")
            old, _session = _session, None
            await old.stop()
        try:
            s = Session(Path(config).expanduser(), attach=attach, viewer=viewer, speed=parse_speed(str(speed)),
                        sim_mode=sim_mode)
            result = await s.start()
        except (SessionError, ConfigError, ValueError) as e:
            raise _fail(e) from None
        _session = s
        return _json(result)


@server.tool()
async def stop_session() -> str:
    """Stop the session and the robot program."""
    global _session
    async with _lock():
        if _session is not None:
            old, _session = _session, None
            await old.stop()
    return _json({"stopped": True})


@server.tool()
async def restart_robot() -> str:
    """Restart the robot program (e.g. after editing its code) and reset the world to t=0, disabled."""
    try:
        return _json(await _need().restart_robot())
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def session_status() -> str:
    """Whether a session is running, lockstep is active, and which HAL devices the robot program created."""
    return _json(_session.status() if _session is not None else {"session": None})


@server.tool()
async def robot_parts() -> str:
    """The robot's part instances (name, source, attach, slots with kind and device ID, mounts, joints) when it is
    built from [[part]] entries; an error for a hand-written [model] mjcf robot."""
    tree = _need().world.part_tree()
    if tree is None:
        raise ToolError("this robot uses a hand-written model, not parts")
    return _json(tree)


@server.tool()
async def advance(seconds: float) -> str:
    """Run the simulation forward by `seconds` (at most 60), then hold it again. Returns the state and any new
    robot console lines."""
    _check_seconds(seconds)
    try:
        return _json(await _need().advance(seconds))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def set_mode(mode: str) -> str:
    """Set the Driver Station mode: disabled, auto, teleop or test. Takes effect on the next advance."""
    try:
        return _json(_need().set_mode(mode))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def set_joystick(port: int, axes: list[float] | None = None, buttons: list[bool] | None = None,
                       povs: list[int] | None = None) -> str:
    """Set a joystick (port 0-5): axes in [-1, 1], buttons (index 0 = button 1), POV angles (-1 = centered)."""
    try:
        return _json(_need().set_joystick(port, axes, buttons, povs))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def get_state() -> str:
    """Ground-truth pose (m, degrees CCW), speed, battery voltage, motor commands/currents, joint positions; with
    pneumatic cylinders, the air (stored and working psi, compressor on, each rod's extension_m in meters)."""
    try:
        return _json(_need().state())
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def get_hal(device_filter: str = "") -> str:
    """Raw HAL values the robot program reported, by "type/device"; device_filter matches a substring."""
    try:
        return _json(_need().hal(device_filter))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def get_console(since: int = 0) -> str:
    """Robot program console lines (with simulated timestamps) from line index `since`; returns `next`."""
    try:
        return _json(_need().console(since))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def nt_list(prefix: str = "/") -> str:
    """List the robot's NetworkTables topics under a prefix."""
    try:
        return _json(_need().nt_list(prefix))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def nt_get(topics: list[str]) -> str:
    """Read NetworkTables values (e.g. "/SmartDashboard/Gyro deg"). Values lag the simulation by up to ~0.1 s."""
    try:
        return _json(_need().nt_get(topics))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def set_pose(x: float, y: float, heading_deg: float) -> str:
    """Teleport the robot (physics only; the robot's odometry isn't reset). For a parts robot the pose is where the
    start-pose frame goes; get_state reports the root body, which may be offset from it."""
    try:
        return _json(_need().set_pose(x, y, heading_deg))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def push(force_n: float, direction_deg: float, duration_s: float) -> str:
    """Push the robot with a horizontal force (N) toward direction_deg (field frame, CCW from +x) for duration_s
    of simulated time (at most 60). This advances time."""
    _check_seconds(duration_s)
    try:
        return _json(await _need().push(force_n, direction_deg, duration_s))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def move_body(name: str, x: float, y: float, z: float | None = None, yaw_deg: float = 0.0) -> str:
    """Move an object in the world (a free body or a named static geom such as "obstacle")."""
    try:
        return _json(_need().move_body(name, x, y, z, yaw_deg))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def screenshot(view: str = "follow", width: int = 960, height: int = 600, paths: bool = True,
                     camera: dict | None = None) -> list:
    """A picture of the world (width/height 16-4096). view: "follow" (camera behind the robot), "top" (whole field;
    ground-truth path in green, odometry in orange), "camera:<name>" (a camera in the model), or "free"
    (camera={lookat:[x,y,z], distance, azimuth, elevation})."""
    s = _need()
    try:
        png = await s.screenshot_png(view, width=width, height=height, paths=paths, camera=camera)
    except SessionError as e:
        raise _fail(e) from None
    return [_json({"t": round(s.t, 4), "view": view}), Image(data=png, format="png")]


@server.tool()
async def contact_sheet(seconds: float, frames: int = 8, view: str = "follow", width: int = 480, height: int = 300,
                        paths: bool = True, camera: dict | None = None) -> list:
    """Advance `seconds` (at most 60), capturing `frames` (1-16) pictures at equal steps, as one grid image.
    view: "follow", "top", "camera:<name>" or "free" (with camera={...}), as for screenshot. width/height are the
    size of each frame, 16-4096."""
    _check_seconds(seconds)
    s = _need()
    try:
        png = await s.contact_sheet(seconds, frames, view, width=width, height=height, paths=paths, camera=camera)
        state = s.state()
    except SessionError as e:
        raise _fail(e) from None
    return [_json(state), Image(data=png, format="png")]


@server.tool()
async def run_scenario(path: str, out_dir: str | None = None) -> list:
    """Run a scenario file (steps, checks, screenshots). Uses the current session if its config matches (the robot
    is restarted); with no session open, runs standalone in its own session (an open session with another config is
    an error: stop_session first). Returns the report (with mode "session" or
    "standalone") and up to 6 of its screenshots. out_dir defaults to runs/<scenario>-mcp, relative to the server's
    working directory, and is overwritten each run."""
    try:
        sc = load_scenario(Path(path).expanduser())
    except (ScenarioError, ConfigError) as e:
        raise _fail(e) from None
    out = Path(out_dir).expanduser() if out_dir else Path("runs") / f"{sc.path.stem}-mcp"
    if _session is None:
        use, note = None, "no session is open; ran in a standalone session"
    elif _session.config.path != sc.config_path:
        # Two robot programs at once would share NetworkTables port 5810 and read each other's values.
        raise ToolError(f"a session is open with a different config ({_session.config.path}); stop_session first, "
                        f"or start a session with {sc.config_path}")
    else:
        speed = "max" if _session.speed is None else _session.speed
        use, note = _session, (f"ran in the open session at its speed ({speed}); the scenario's speed "
                               f"({'max' if sc.speed is None else f'{sc.speed:g}'}) is ignored here")
    try:
        report = await _run_scenario(sc, out, session=use)
        names = [n for step in report.steps for n in step.screenshots]
        shown = names[:MAX_IMAGES]
        images = [Image(data=(out / n).read_bytes(), format="png") for n in shown]
        info = {**report.to_dict(), "out_dir": str(out.resolve()), "mode": "session" if use else "standalone",
                "images_returned": len(images), "images_total": len(names)}
        if use:
            info["speed"] = "max" if use.speed is None else use.speed
        if note:
            info["note"] = note
    except Exception as e:
        raise ToolError(f"run_scenario failed: {e!r}; any partial report is in {out}") from None
    return [_json(info), *images]


# ----------------------------------------------------------------------------------------------------- the bench
# Bench tools never touch the robot session: each builds its own MuJoCo model, and the work (MuJoCo, rendering, file
# writing) runs in a worker thread so the event loop, and any open session, carries on.

def _bench_errors():
    from .bench.run import BenchRunError
    from .bench.spec import BenchError
    from .render import RenderUnavailable
    return (BenchError, BenchRunError, RenderUnavailable, OSError, ValueError)


def _bench_path(path: str) -> Path:
    return Path(path).expanduser().resolve()


async def _bench_call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except _bench_errors() as e:
        raise ToolError(str(e)) from None


def _bench_recorded(kind: str, files: list[Path], out: Path, folder: Path | None = None):
    """Inside the worker thread: the folder's run lock and run log (source "agent") around one bench job."""
    from .bench import session
    return session.recorded(folder or files[0].parent, kind, files, out, "agent")


def _bench_ideas(results, files) -> list[dict]:
    from .bench.report import energy_failures, headlines
    return [{"file": str(f), "name": r.bench.name, "author": r.bench.author, "idea": r.bench.idea,
             "headlines": headlines(r), "energy_failures": energy_failures(r)} for f, r in zip(files, results)]


def _warnings(benches) -> list[str]:
    return list(dict.fromkeys(w for b in benches for w in b.warnings))


def _metric_rows(rows) -> list[dict]:
    import math
    return [{**combo, **{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.metrics.items()}}
            for combo, row in rows]


@server.tool()
async def bench_check(path: str) -> str:
    """Check a bench file (TOML) without running it: unknown keys, bad values, an inlet that overlaps a shape. Fast;
    run it after every edit. path: give an absolute path (a relative one resolves against the MCP server's working
    directory). Returns {"ok": true, "summary": "ok: <name>: 2 wheels, ...", "warnings": [...], "problems":
    [{level, key, message}]} (warnings don't stop a run, e.g. a custom ball borrowing a preset's numbers or a moving
    part overlapping another shape: tell the room); errors are all reported at once, each naming the bad key and
    listing the valid ones."""
    from .bench.compare import check_problems
    bench, summary, problems = await _bench_call(check_problems, _bench_path(path))
    return _json({"ok": True, "summary": summary, "warnings": [p.message for p in problems if p.level == "warning"],
                  "problems": [{"level": p.level, "key": p.key, "message": p.message} for p in problems]})


@server.tool()
async def bench_focus(path: str, keys: list[str]) -> str:
    """Pin the numbers you are talking about on the Bench tab (`scrimmage edit <folder>`), if one is open: the fields
    for these keys float to the top of that bench file's page. keys: dotted keys such as "wheel.top.diameter",
    "run.duration" or "bar.kicker.spring"; each is checked against the file (an unknown key or name is an error
    listing the valid ones). path: an absolute path. Returns {"ok": true, "focus": [...]}, newest first."""
    file = _bench_path(path)

    def work():
        from .bench import session
        from .bench.spec import _check_path, _read_toml
        raw = _read_toml(file)
        for k in keys:
            _check_path(raw, k)
        session.bump(file.parent, file, keys)
        return {"ok": True, "focus": session.focus(file.parent, file)}

    return _json(await _bench_call(work))


@server.tool()
async def bench_run(path: str, out_dir: str | None = None) -> list:
    """Run a bench file (ignoring its [sweep]): returns the headlines (plain words for the room, UNCALIBRATED last),
    then the side path picture (the mechanism with every piece's path, green out, red jammed, orange escaped), a top
    view when pieces move across, and the pass contact sheet (frames of one piece going through). Show the room the
    pictures and read them the headlines. path: an absolute path. out_dir (absolute) defaults to
    <file's folder>/<file stem>-results/, where the tables (summary.txt, pieces/shafts/loads CSVs), the pictures and
    pass.gif are written; it is overwritten each run. "warnings" are things to tell the room that didn't stop the
    run; "energy_failures" over 0 means the simulation went wrong (a headline says so): try a smaller [run]
    timestep."""
    file = _bench_path(path)

    def work():
        want = Path(out_dir).expanduser() if out_dir else file.parent / f"{file.stem}-results"
        with _bench_recorded("run", [file], want):
            return _run_locked(file, want)

    def _run_locked(file, want):
        from .bench.pictures import PICTURE_TRACE_HZ, write_pictures
        from .bench.report import energy_failures, headlines, write_results
        from .bench.run import run_bench
        from .bench.spec import load_bench
        from .render import RenderUnavailable
        bench = load_bench(file)
        result = run_bench(bench, trace_hz=PICTURE_TRACE_HZ)
        out = write_results(result, want)
        info = {"name": bench.name, "author": bench.author, "idea": bench.idea, "headlines": headlines(result),
                "warnings": _warnings([bench]), "energy_failures": energy_failures(result),
                "out_dir": str(out.resolve())}
        try:
            pictures = write_pictures(result, out)
        except RenderUnavailable as e:
            info["pictures"] = f"no pictures: {e}"
            pictures = []
        order = {"path_side.png": 0, "path_top.png": 1, "pass.png": 2}
        shown = sorted((p for p in pictures if p.name in order), key=lambda p: order[p.name])
        info["files"] = sorted(p.name for p in out.iterdir())
        return info, [p.read_bytes() for p in shown]

    info, pngs = await _bench_call(work)
    return [_json(info), *(Image(data=png, format="png") for png in pngs)]


@server.tool()
async def bench_sweep(path: str, out_dir: str | None = None) -> list:
    """Run every combination of a bench file's [sweep] table (dotted keys, list entries by name, e.g.
    "wheel.top.diameter" = [0.06, 0.08]): returns one row per combination (the swept values, then out, jammed,
    exit_speed, elevation_deg, speed_drop_pct, peak_load_n and more; null where there is nothing to measure) and the
    charts as images. Every combination is checked before the first runs. path: an absolute path. out_dir (absolute)
    defaults to <file's folder>/<file stem>-results/ (results.csv and the chart PNGs)."""
    file = _bench_path(path)

    def work():
        out = Path(out_dir).expanduser() if out_dir else file.parent / f"{file.stem}-results"
        with _bench_recorded("sweep", [file], out):
            return _sweep_locked(file, out)

    def _sweep_locked(file, out):
        from .bench import compare
        from .bench.report import bench_uncalibrated
        from .bench.run import run_bench
        from .bench.spec import BenchError
        from .launcher import report
        bench, benches = compare.sweep_benches(file)
        if not bench.sweep:
            raise BenchError("no [sweep] section: use bench_run for one run")
        rows = compare.rows([(c, run_bench(b)) for c, b in benches])
        out.mkdir(parents=True, exist_ok=True)
        report.write_csv(rows, out / "results.csv", compare.BENCH_METRICS)
        charts = report.write_charts(rows, bench.charts or compare.default_charts(list(bench.sweep)), out, None,
                                     compare.BENCH_METRICS, compare.CHART_KINDS)
        info = {"rows": _metric_rows(rows), "out_dir": str(out.resolve()), "warnings": _warnings([bench]),
                "uncalibrated": bench_uncalibrated(bench)}
        if charts is None:
            info["charts"] = report.NO_MATPLOTLIB
        return info, [p.read_bytes() for p in charts or []][:MAX_IMAGES]

    info, pngs = await _bench_call(work)
    return [_json(info), *(Image(data=png, format="png") for png in pngs)]


@server.tool()
async def bench_compare(paths: list[str], feed_from: str | None = None, out_dir: str | None = None) -> list:
    """Run two or more bench files with the same piece and feed ([piece] and [[inlet]] from feed_from, default the
    first file; each keeps its own shapes, shafts and outlets): returns each idea's headlines, a table row per idea,
    and one picture with their side views side by side (each with its own scale bar). paths, feed_from: absolute
    paths. out_dir (absolute) defaults to <first file's folder>/compare-results/ (compare.csv, compare.png)."""
    files = [_bench_path(p) for p in paths]
    feed = _bench_path(feed_from) if feed_from else None

    def work():
        out = Path(out_dir).expanduser() if out_dir else files[0].parent / "compare-results"
        with _bench_recorded("compare", files, out):
            return _compare_locked(out)

    def _compare_locked(out):
        from PIL import Image as PILImage

        from .bench import compare, gallery
        from .bench.pictures import PICTURE_TRACE_HZ
        from .launcher import report
        from .render import to_png
        results = compare.compare_benches(files, feed, trace_hz=PICTURE_TRACE_HZ)
        rows = compare.rows([({"idea": n}, r) for n, r in zip(compare.idea_labels(results), results)])
        out.mkdir(parents=True, exist_ok=True)
        report.write_csv(rows, out / "compare.csv", compare.BENCH_METRICS)
        pic = gallery.compare_picture(results)
        PILImage.fromarray(pic).save(out / "compare.png")
        info = {"ideas": _bench_ideas(results, files), "rows": _metric_rows(rows),
                "warnings": _warnings([r.bench for r in results]),
                "feed_from": str(feed or files[0]), "out_dir": str(out.resolve())}
        return info, to_png(pic)

    info, png = await _bench_call(work)
    return [_json(info), Image(data=png, format="png")]


@server.tool()
async def bench_gallery(folder: str, out: str | None = None) -> str:
    """Run every bench file in a folder (*.toml, not subfolders; each once, ignoring [sweep]) and write one
    self-contained HTML page: each idea's author, idea line, side path picture and headlines, then a table comparing
    them all. It opens in a browser with no server, for the projector or to share. A file that doesn't run is listed
    with its problem (returned in "problems") rather than stopping the gallery. folder: an absolute path. out
    (absolute) defaults to <folder>/gallery.html."""
    where = _bench_path(folder)

    def work():
        target = Path(out).expanduser() if out else where / "gallery.html"
        with _bench_recorded("gallery", [where], target, where):
            return _gallery_locked(target)

    def _gallery_locked(target):
        from .bench import gallery
        entries = gallery.run_folder(where)
        page = gallery.write_gallery(where, target, entries)
        return {"gallery": str(page.resolve()), "ideas": sum(e.result is not None for e in entries),
                "problems": [{"file": str(e.file), "problem": e.problem} for e in entries if e.problem]}

    return _json(await _bench_call(work))


# ------------------------------------------------------------------------------------------------- block tests
# Like the bench, a block test never touches the robot session: it builds its own world, in a worker thread.

def _test_errors():
    return (ValueError, OSError)    # TestFileError, ConfigError and PartError are ValueErrors


def _test_file(path: str) -> Path:
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise ToolError(f"test file not found: {file}")
    return file


def _signal_summary(signals: dict) -> dict:
    """Each signal's min, max and final value; an empty sample (NaN: a seated depth while no piece is on its rod) is
    left out of min and max, and a final one is None (JSON has no NaN)."""
    out = {}
    for name, values in signals.items():
        if name == "t" or not values:
            continue
        have = [v for v in values if not math.isnan(v)]
        final = values[-1]
        out[name] = {"min": min(have, default=None), "max": max(have, default=None),
                     "final": None if math.isnan(final) else final}
    return out


@server.tool()
async def test_run(path: str) -> dict:
    """Run a block test (a <name>.test.toml file) on its robot, with no robot program: the blocks drive the motors.
    Returns "status" (finished, check failed, timed out, tipped over, walked off, time limit, repeat limit),
    "outcome" (one plain sentence to read to the room), "passed" (finished with every check passed), "t_end"
    (seconds), "checks" (one plain line each, with whether it passed), "hung" (the bars the robot hung from),
    "signals" (each measured signal's min, max and final value), "counts" (on a field with a human player
    station: fed, in, stuck, missed, and placed when a piece was placed; else null), "feeds" (one row per fed piece:
    ball, source, t, outcome "in", "placed" (on a rod or element a seated or rests on check names: why is its name),
    "stuck" or "missed", why, t_outcome, body for a stuck piece), "seed" (the stations' seed, or null), "marks" (the
    moments the replay marks: {t, kind "slip", text, ball}, such as when a held piece slipped out), "notes" (what the
    numbers rest on: each gripped body's material and friction, and on a robot with cylinders that the pneumatic
    numbers are estimates, all UNCALIBRATED; say them with the outcome),
    "goals" (on a field with a shooter: the balls scored in each goal, else null), "air" (on a robot with cylinders:
    start_psi, end_psi, low_psi, low_t, compressor_s, below_working_t (when the tanks fell below working_psi, the
    cylinders weaker from then on; null if never), and working_psi; else null), "air_lines" (the air in words) and
    "run_folder", where the run is kept (the app's Test tab replays it). path: an absolute path. A bad test file
    or robot is a plain error naming the block."""
    file = _test_file(path)

    def work():
        from .blocks import program as blocks_program
        from .blocks import run as blocks_run
        from .blocks import store as blocks_store
        program = blocks_program.load(file)
        result = blocks_run.TestRun(blocks_run.world_for(program), program).run()
        folder = blocks_store.save_run(result, program, blocks_store.runs_dir(file))
        return {"status": result.status, "outcome": result.outcome, "passed": result.passed,
                "t_end": result.t_end,
                "checks": [{"line": c["line"], "passed": c["passed"]} for c in result.checks],
                "hung": list(result.hung), "signals": _signal_summary(result.signals),
                "counts": result.counts, "feeds": result.feeds, "seed": result.seed,
                "marks": list(result.marks), "notes": list(result.notes), "goals": result.goals,
                "air": result.air, "air_lines": blocks_run.air_lines(result),
                "run_folder": str(folder.resolve())}

    try:
        return await asyncio.to_thread(work)
    except _test_errors() as e:
        raise ToolError(str(e)) from None


@server.tool()
async def test_sweep(path: str, target: dict, values: list[float | str]) -> dict:
    """Run a block test several times, changing one number each time, to find which value works. target says what
    changes: {"block": [2], "key": "to"} is the "to" of block 3 (a list of positions, counting from 0, goes inside
    repeats, and key is volts, seconds, to or distance); {"robot": "mass"} is the robot's mass (also com_forward,
    com_left, com_up for the center of mass); {"mount": "arm", "axis": "x"} is where a part is mounted (axis x, y,
    z or yaw); {"grip": "claw"} is the grip material of every body of a path part that touches anything, and
    {"grip": "claw.pad"} of one body (values are material names such as "hdpe", "nitrile" or "grip-tape", or a
    measured friction number: "would grip tape help?"; sweeping one pad of several may show no difference, so
    sweep the part); {"setup": "seed"} is the human player's shuffle (a whole number), and {"station": "feeder", "key":
    "interval"} is a station's interval (also stock, spread_yaw_deg, spread_pitch_deg, spread_speed, spread_position,
    spread_rotation_deg; the last two set all three axes). values: the numbers
    (for a grip, material names or numbers) to try, in order (at most 12).
    Returns "rows" (each value's status, outcome, t_end and a passed or failed per check), "sweep_folder" (table.csv
    and sweep.json, each run kept inside; the app's Test tab shows them) and any "problems" to tell the room. A value
    that can't run gets a row saying why; the rest still run. path: an absolute path."""
    file = _test_file(path)

    def work():
        import csv
        from .blocks import program as blocks_program
        from .blocks import store as blocks_store
        from .blocks.sweep import run_sweep
        program = blocks_program.load(file)
        folder = run_sweep(program, target, values, blocks_store.runs_dir(file))
        with open(folder / "table.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        meta = json.loads((folder / "sweep.json").read_text(encoding="utf-8"))
        info = {"rows": rows, "sweep_folder": str(folder.resolve())}
        if meta.get("problems"):
            info["problems"] = meta["problems"]
        return info

    try:
        return await asyncio.to_thread(work)
    except _test_errors() as e:
        raise ToolError(str(e)) from None


@server.tool()
async def test_spread(path: str) -> dict:
    """Run a block test once per placement of its floor spread (the test file's [setup] floor_spread, e.g.
    floor_spread = { offset_in = [-6, 0, 6], angle_deg = [0, 30] }: every combination, at most 25), to see which
    placements of the floor ball in front of the robot got it picked up (into a hopper body). The axes are offset_in
    (left of the robot's center line +, right -), distance_in (ahead), angle_deg (the robot's approach angle),
    rotation_deg (the piece turned, from the robot's heading for a piece other than a ball) and tip_deg (0 to 90: a
    coral's axis pitched up from lying; 90 stands it on end). Returns "sentence" ("picked up 7 of 9; missed at 6 in
    left at 30° and 6 in right at 30°", or for a coral "picked up 2 of 3; missed standing on end", to read to the
    room), "rows" (each placement's words, its five axes, status, "picked up" yes or no, and outcome) and
    "spread_folder" (table.csv and spread.json, each run kept inside; the app's Test tab shows them). path: an
    absolute path."""
    file = _test_file(path)

    def work():
        import csv
        from .blocks import program as blocks_program
        from .blocks import store as blocks_store
        from .blocks.spread import run_spread
        folder = run_spread(blocks_program.load(file), blocks_store.runs_dir(file))
        with open(folder / "table.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        meta = json.loads((folder / "spread.json").read_text(encoding="utf-8"))
        return {"sentence": meta["sentence"], "rows": rows, "spread_folder": str(folder.resolve())}

    try:
        return await asyncio.to_thread(work)
    except _test_errors() as e:
        raise ToolError(str(e)) from None


def _kill_leftover_robot() -> None:
    """Synchronous best-effort cleanup at exit: the event loop is gone, so just kill the robot's process group."""
    s = _session
    proc = s.robot.proc if s is not None and s.robot is not None else None
    if proc is not None and proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so the finally below runs
    try:
        server.run()
    finally:
        _kill_leftover_robot()


if __name__ == "__main__":
    main()
