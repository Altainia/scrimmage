"""scrimmage serve CONFIG [...]   |   scrimmage run SCENARIO [...]"""

from __future__ import annotations

import argparse
import asyncio
import errno
import logging
import math
import re
import signal
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .bindings import binds_rev
from .bridge import Bridge, robot_language
from .config import SIM_MODES, ConfigError, build_world, load_config, set_sim_mode, sim_mode_unused
from .lockstep import DEFAULT_STEP, parse_speed
from .robot_process import RobotProcess
from .scenario import ScenarioError, load_scenario, validate
from .scenario_runner import UNSET, run_scenario


SIM_MODE_HELP = ("fast: set SCRIMMAGE_MODE=fast for the robot program (overrides [robot_program] sim_mode); "
                 "bindings are unchanged, and the robot code must check Scrimmage.fastMode()")


def _speed(text: str) -> float | None:
    try:
        return parse_speed(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _port(text: str) -> int:
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a port number: {text!r}") from None
    if not 0 <= n <= 65535:
        raise argparse.ArgumentTypeError(f"port must be 0-65535, not {n}")
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="scrimmage", description="Simulate a WPILib robot program in a MuJoCo world.")
    sub = ap.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("serve", help="run the world for a robot program you drive yourself (WPILib sim GUI)")
    sp.add_argument("config", type=Path, help="robot config TOML (model, device bindings, [robot_program])")
    sp.add_argument("--launch", action="store_true", help="start the robot program from [robot_program]")
    sp.add_argument("--viewer", action="store_true", help="open the MuJoCo 3D viewer")
    sp.add_argument("--speed", type=_speed, default=1.0,
                    help='real-time factor, or "max" (needs the lockstep extension unless 1; default 1)')
    sp.add_argument("--step", type=float, help=f"lockstep step in seconds (default: [lockstep] step, else {DEFAULT_STEP})")
    sp.add_argument("--sim-mode", choices=SIM_MODES, help=SIM_MODE_HELP)
    sp.add_argument("--log", type=Path, help="write a CSV of ground truth, odometry, motor commands and currents")
    sp.add_argument("--host", default="localhost")
    sp.add_argument("--port", type=int, default=3300)
    sp.add_argument("--no-nt", action="store_true", help="don't use NetworkTables")
    sp.add_argument("-v", "--verbose", action="store_true")

    rp = sub.add_parser("run", help="run a scenario file and write a report (exit 0 pass, 1 fail, 2 couldn't run)")
    rp.add_argument("scenario", type=Path)
    rp.add_argument("--out", type=Path, help="report directory (default runs/<scenario>-<timestamp>)")
    rp.add_argument("--sim-mode", choices=SIM_MODES, help=SIM_MODE_HELP)
    rp.add_argument("--speed", type=_speed, default=UNSET, help='override the scenario\'s speed ("max" or a factor)')
    rp.add_argument("-v", "--verbose", action="store_true")

    jp = sub.add_parser("java", help="Java support: build the agent and the fast-mode library, print the init "
                                     "script path, or add the fast-mode vendordep to a robot project")
    jp.add_argument("-v", "--verbose", action="store_true")
    jsub = jp.add_subparsers(dest="java_command", required=True)
    jsub.add_parser("build", help="compile the agent (downloads ASM once) and the fast-mode library (Gradle) with "
                                  "the WPILib JDK")
    jsub.add_parser("path", help="print the Gradle init script to pass as ./gradlew -I <path> simulateJava")
    jv = jsub.add_parser("vendordep", help="copy the built fast-mode library into <project>/scrimmage-maven/ and "
                                           "write <project>/vendordeps/Scrimmage.json (commit both)")
    jv.add_argument("project", type=Path, help="the robot project folder (holding build.gradle)")

    lp = sub.add_parser("launcher", help="model a hooded ball launcher: run one setup, or sweep several")
    lp.add_argument("-v", "--verbose", action="store_true")
    lsub = lp.add_subparsers(dest="launcher_command", required=True)
    for name, text in (("run", "run one setup (ignoring [sweep]) and report it"),
                       ("sweep", "run every combination in the file's [sweep] table and compare them")):
        lc = lsub.add_parser(name, help=text)
        lc.add_argument("file", type=Path, help="launcher setup TOML")
        lc.add_argument("--out", type=Path, help="results folder (default <FILE's folder>/<FILE stem>-results)")

    la = lsub.add_parser("aim", help="an aiming table (wheel speed, hood angle per distance) from a parts robot's "
                                     "launcher model")
    la.add_argument("config", type=Path, help="robot config TOML with a [range] and a [shooter]")
    la.add_argument("--goal", required=True, help="the range's goal to aim at (a name in the range file)")
    la.add_argument("--distances", required=True, help="robot center to goal center, FROM:TO:STEP in meters")
    la.add_argument("--hood-step", type=float, default=2.0, metavar="DEG", help="hood angles tried every DEG (default 2)")
    la.add_argument("--csv", type=Path, help="write the table as CSV")
    la.add_argument("--java", type=Path, help="write the table as a Java class (needs --java-class)")
    la.add_argument("--java-class", help="the Java class's name, with its package: pkg.Name")

    dp = sub.add_parser("disc", help="model a disc (Frisbee) launcher and the disc's flight: run, sweep, or tabulate")
    dp.add_argument("-v", "--verbose", action="store_true")
    dsub = dp.add_subparsers(dest="disc_command", required=True)
    for name, text in (("run", "run one setup (ignoring [sweep]) and report it"),
                       ("sweep", "run every combination in the file's [sweep] table and compare them")):
        dc = dsub.add_parser(name, help=text)
        dc.add_argument("file", type=Path, help="disc launcher setup TOML")
        dc.add_argument("--out", type=Path, help="results folder (default <FILE's folder>/<FILE stem>-results)")
    dt = dsub.add_parser("table", help="exit speed and spin against wheel rpm for one setup")
    dt.add_argument("file", type=Path, help="disc launcher setup TOML")
    dt.add_argument("--rpm", required=True, help="wheel rpm, FROM:TO:STEP (the first wheel group; others keep "
                                                  "their ratio to it)")

    bp = sub.add_parser("bench", help="the component bench: try a mechanism idea with game pieces (docs/bench.md)")
    bp.add_argument("-v", "--verbose", action="store_true")
    bsub = bp.add_subparsers(dest="bench_command", required=True)
    bc = bsub.add_parser("check", help="check a bench file without running it (fast)")
    bc.add_argument("file", type=Path, help="bench file TOML")
    br = bsub.add_parser("run", help="run a bench file: print the headlines, write tables and pictures")
    br.add_argument("file", type=Path, help="bench file TOML")
    br.add_argument("--out", type=Path, help="results folder (default <FILE's folder>/<FILE stem>-results)")
    bv = bsub.add_parser("view", help="run a bench file and play it in the MuJoCo 3D viewer, slowed down")
    bv.add_argument("file", type=Path, help="bench file TOML")
    bv.add_argument("--slow", type=float, default=0.25,
                    help="playback speed as a fraction of real time (default 0.25: four times slower)")
    bw = bsub.add_parser("sweep", help="run every combination in the file's [sweep] table: a table, a CSV and charts")
    bw.add_argument("file", type=Path, help="bench file TOML with a [sweep] table")
    bw.add_argument("--out", type=Path, help="results folder (default <FILE's folder>/<FILE stem>-results)")
    bm = bsub.add_parser("compare", help="run two or more bench files with one piece and feed: a table and one "
                                         "picture, side by side")
    bm.add_argument("files", type=Path, nargs="+", help="bench files TOML (two or more)")
    bm.add_argument("--feed-from", type=Path, help="take [piece] and [[inlet]] from this file (default: the first)")
    bm.add_argument("--out", type=Path, help="results folder (default <first FILE's folder>/compare-results)")
    bg = bsub.add_parser("gallery", help="run every bench file in a folder and write one HTML page of the ideas")
    bg.add_argument("folder", type=Path, help="a folder of bench files (*.toml; subfolders are not read)")
    bg.add_argument("--out", type=Path, help="the HTML file (default <FOLDER>/gallery.html)")

    ap_air = sub.add_parser("air", help="an air budget: how many cylinder cycles the tanks and compressor pay for")
    ap_air.add_argument("config", type=Path, help="robot config TOML with cylinders (and a [pneumatics] table)")
    ap_air.add_argument("--match-seconds", type=float, default=150.0, metavar="S",
                        help="match length for the compressor's air (default 150)")
    ap_air.add_argument("-v", "--verbose", action="store_true")

    tp = sub.add_parser("terrain", help="field elements: find how fast a robot can take one")
    tp.add_argument("-v", "--verbose", action="store_true")
    tsub = tp.add_subparsers(dest="terrain_command", required=True)
    ts = tsub.add_parser("sweep", help="drive the robot straight over a field element at each speed (no robot "
                                       "program) and find the fastest safe one (docs/terrain.md)")
    ts.add_argument("config", type=Path, help="robot config TOML with a [range] whose range file has the element")
    ts.add_argument("--element", required=True, help="the element to drive over (a name in the range file)")
    ts.add_argument("--speeds", required=True, help="FROM:TO:STEP in m/s: FROM, FROM+STEP, ... up to TO")
    ts.add_argument("--set", action="append", default=[], metavar="JOINT=VALUE",
                    help="hold a joint at a position (m or rad) in every run, e.g. elevator.carriage=0.9; "
                         "repeatable")
    ts.add_argument("--gap", type=float, default=0.05, metavar="M", help="peak ground gap allowed (default 0.05)")
    ts.add_argument("--tilt", type=float, default=30.0, metavar="DEG",
                    help="peak tilt allowed: the chassis's up from vertical, pitch and roll together (default 30)")
    ts.add_argument("--out", type=Path,
                    help="results folder (default <CONFIG's folder>/<CONFIG stem>-<element>-sweep)")

    cp = sub.add_parser("climb", help="climbing: play a scripted climb with no robot program")
    cp.add_argument("-v", "--verbose", action="store_true")
    csub = cp.add_subparsers(dest="climb_command", required=True)
    cr = csub.add_parser("run", help="play a climb script on the robot's climbing range and report the timeline, "
                                     "hook loads, swing and outcome (docs/climbing.md)")
    cr.add_argument("config", type=Path, help="robot config TOML with climbers, on a range with a [climb] table")
    cr.add_argument("--script", type=Path, required=True, help="the climb script TOML ([[step]]s)")
    cr.add_argument("--out", type=Path, help="results folder (default <SCRIPT's folder>/<SCRIPT stem>-climb)")

    xp = sub.add_parser("test", help="test a mechanism with blocks: play a test file's blocks with no robot program")
    xp.add_argument("-v", "--verbose", action="store_true")
    xsub = xp.add_subparsers(dest="test_command", required=True)
    xr = xsub.add_parser("run", help="run a test file (<name>.test.toml) and print how it ended and each check "
                                     "(exit 0 finished with every check passed, 1 not, 2 couldn't run)")
    xr.add_argument("file", type=Path, help="the test file")
    xr.add_argument("--out", type=Path, help="folder to keep the run in (default <FILE's folder>/<FILE stem>-runs)")
    xf = xsub.add_parser("spread", help="run a test file once per placement of its [setup] floor_spread and say which "
                                        "placements got the floor ball picked up (exit 0 every one picked up, 1 not, "
                                        "2 couldn't run)")
    xf.add_argument("file", type=Path, help="the test file")
    xf.add_argument("--out", type=Path, help="folder to keep the spread in, as spread-<NNN> (default <FILE's "
                                             "folder>/<FILE stem>-runs)")

    mp = sub.add_parser("model", help="print (or write) the MJCF a parts robot assembles to")
    mp.add_argument("config", type=Path)
    mp.add_argument("-o", "--output", type=Path, help="write the MJCF to this file instead of printing it")
    mp.add_argument("-v", "--verbose", action="store_true")

    pp = sub.add_parser("parts", help="list the stock parts, or describe one")
    pp.add_argument("name", nargs="?", help="a stock part name")
    pp.add_argument("-v", "--verbose", action="store_true")

    fp = sub.add_parser("freecad", help="FreeCAD add-on: install it into FreeCAD, zip it, or export a part")
    fp.add_argument("-v", "--verbose", action="store_true")
    fsub = fp.add_subparsers(dest="freecad_command", required=True)
    fi = fsub.add_parser("install", help="copy the add-on into FreeCAD's user Mod folder")
    fi.add_argument("--to", type=Path, help="Mod folder to install into (default: FreeCAD's user Mod folder)")
    fz = fsub.add_parser("zip", help="write the add-on as a zip (Scrimmage/ top folder)")
    fz.add_argument("-o", "--output", type=Path, default=Path("Scrimmage.zip"))
    fe = fsub.add_parser("export", help="export a FreeCAD Assembly to a part folder (part.toml + meshes)")
    fe.add_argument("file", type=Path, help="the .FCStd file holding the Assembly")
    fe.add_argument("out_dir", type=Path, help="the part folder to write (replaced if it already holds a part.toml)")

    ep = sub.add_parser("edit", help="edit a robot config, a scenario, an idea session or a part from CAD in a local "
                                     "web app (3D view, validation, runs); with no FILE, a start page")
    ep.add_argument("file", type=Path, nargs="?",
                    help="robot config or scenario TOML to edit (a scenario has a top-level config key or [[step]]s), "
                         "a bench file or a folder of them (an idea session, on the Bench tab), or a .step/.stp/.stl/"
                         ".obj file, an import file or a part folder (the Part tab)")
    ep.add_argument("--new", choices=("swerve", "swerve-rev", "differential"), help="write a starting robot config to FILE first")
    ep.add_argument("--new-scenario", action="store_true", help="write a starting scenario to FILE first (with --robot)")
    ep.add_argument("--robot", type=Path, metavar="CONFIG",
                    help="the robot config a --new-scenario uses; with a CAD file, the robot whose parts/ folder "
                         "gets its import file")
    ep.add_argument("--port", type=_port, default=0, help="port on 127.0.0.1 (default: any free port)")
    ep.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    ep.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    if args.command == "serve":
        _serve(args, sp)
    elif args.command == "java":
        _java(args)
    elif args.command == "freecad":
        _freecad(args)
    elif args.command == "launcher":
        _launcher(args)
    elif args.command == "disc":
        _disc(args)
    elif args.command == "bench":
        _bench(args)
    elif args.command == "air":
        _air(args)
    elif args.command == "terrain":
        _terrain(args)
    elif args.command == "climb":
        _climb(args)
    elif args.command == "test":
        _test(args)
    elif args.command == "model":
        _model(args)
    elif args.command == "parts":
        _parts(args)
    elif args.command == "edit":
        _edit(args)
    else:
        sys.exit(_run(args, rp))


def _java(args) -> None:
    from . import java_tools
    try:
        if args.java_command == "build":
            print(java_tools.build_agent())
            print(java_tools.build_fastmode())
        elif args.java_command == "vendordep":
            print(java_tools.write_vendordep(args.project))
        else:
            print(java_tools.init_script_path())
    except (java_tools.JavaToolError, OSError) as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)


def _freecad(args) -> None:
    from . import freecad_tools
    try:
        if args.freecad_command == "install":
            print(freecad_tools.install(args.to))
        elif args.freecad_command == "export":
            _print_export(freecad_tools.export_part(args.file, args.out_dir))
        else:
            print(freecad_tools.make_zip(args.output))
    except freecad_tools.ExportProblems as e:
        print(f"scrimmage: can't export {args.file}:", file=sys.stderr)
        for p in e.problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(2)
    except (freecad_tools.FreeCADError, OSError) as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)


def _distances(text: str) -> list[float]:
    """FROM:TO:STEP, both ends included."""
    try:
        lo, hi, step = (float(x) for x in text.split(":"))
    except ValueError:
        raise ValueError(f"--distances {text!r}: want FROM:TO:STEP, three numbers in meters") from None
    if not (0.0 < lo <= hi and step > 0.0 and all(map(math.isfinite, (lo, hi, step)))):
        raise ValueError(f"--distances {text!r}: want 0 < FROM <= TO and STEP > 0")
    n = int((hi - lo) / step + 1e-9) + 1
    if n > 500:
        raise ValueError(f"--distances {text!r}: {n} distances is too many (500 at most)")
    return [round(lo + k * step, 9) for k in range(n)]


def _aim(args) -> None:
    from .testrange import aim
    try:
        if (args.java is None) != (args.java_class is None):
            raise ValueError("--java and --java-class go together")
        if args.java_class is not None and not re.fullmatch(r"([A-Za-z_]\w*\.)*[A-Za-z_]\w*", args.java_class):
            raise ValueError(f"--java-class {args.java_class!r}: want a class name with its package, pkg.Name")
        distances = _distances(args.distances)
        model = aim.load_model(args.config, args.goal, args.hood_step)
        rows = aim.aim_table(args.config, args.goal, distances, hood_step_deg=args.hood_step,
                              on_unreachable=lambda msg: print(f"scrimmage: warning: {msg}", file=sys.stderr),
                              model=model)
        label = aim.calibration(model)
        print(label)
        print(aim.to_csv(rows).replace(",", "  "), end="")
        if args.csv is not None:
            args.csv.write_text(aim.to_csv(rows), encoding="utf-8")
            print(f"wrote {args.csv}")
        if args.java is not None:
            # fixed text: the config and output are placeholders, so a regenerated file is the same bytes anywhere
            command = (f"scrimmage launcher aim CONFIG --goal {args.goal} --distances {args.distances}"
                       f"{'' if args.hood_step == 2.0 else f' --hood-step {args.hood_step:g}'} "
                       f"--java FILE --java-class {args.java_class}")
            args.java.write_text(aim.to_java(rows, args.java_class, command, label), encoding="utf-8")
            print(f"wrote {args.java}")
    except (ValueError, OSError) as e:    # AimError, ConfigError and PartError are ValueErrors
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)


def _launcher(args) -> None:
    if args.launcher_command == "aim":
        return _aim(args)
    from .launcher import report, sweep
    from .launcher.inputs import LauncherConfigError, load_setup
    try:
        _, swept, charts = load_setup(args.file)
        report.check_charts(charts, list(swept))  # before anything runs
        if args.launcher_command == "sweep":
            if not swept:
                print("scrimmage: no [sweep] section: use 'scrimmage launcher run'", file=sys.stderr)
                sys.exit(2)
            rows, setups = sweep.run_all(args.file)  # parses every combination before the first runs
        else:
            rows, setups = sweep.run_one(args.file)
            # One setup, nothing swept: a line chart (over swept keys) has nothing to draw.
            charts = [c for c in charts if c.get("kind") != "line"]
        out = args.out or args.file.parent / f"{args.file.stem}-results"
        out.mkdir(parents=True, exist_ok=True)
        report.write_csv(rows, out / "results.csv")
        label = report.calibration(setups)
        print(report.format_table(rows, label=label))
        print(f"wrote {out / 'results.csv'}")
        paths = report.write_charts(rows, charts, out, [s.goal for s in setups], footer=label)
    except (LauncherConfigError, OSError) as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    if paths is None:
        print(report.NO_MATPLOTLIB)
    else:
        for p in paths:
            print(f"wrote {p}")


def _rpm_range(text: str) -> list[float]:
    from .disc.inputs import DiscConfigError
    try:
        lo, hi, step = (float(x) for x in text.split(":"))
    except ValueError:
        raise DiscConfigError(f"--rpm must be FROM:TO:STEP in wheel rpm, e.g. 3000:6000:500; got {text!r}") from None
    if not (step > 0 and hi >= lo >= 0) or (hi - lo) / step > 200:
        raise DiscConfigError(f"--rpm {text}: needs 0 <= FROM <= TO, STEP above 0, and at most 200 steps")
    return [lo + i * step for i in range(int((hi - lo) / step + 1e-9) + 1)]


def _disc(args) -> None:
    from .disc import report
    from .disc.inputs import DiscConfigError, load_setup
    try:
        if args.disc_command == "table":
            setup, _, _ = load_setup(args.file)
            print(report.exit_table_text(setup, _rpm_range(args.rpm)))
            return
        _, swept, _ = load_setup(args.file)
        if args.disc_command == "sweep" and not swept:
            print("scrimmage: no [sweep] section: use 'scrimmage disc run'", file=sys.stderr)
            sys.exit(2)
        out = args.out or args.file.parent / f"{args.file.stem}-results"
        paths = report.run_and_write(args.file, out, sweep=args.disc_command == "sweep")
    except (DiscConfigError, OSError) as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    if paths is None:
        from .launcher.report import NO_MATPLOTLIB
        print(NO_MATPLOTLIB)
    else:
        for p in paths:
            print(f"wrote {p}")


def _bench(args) -> None:
    from .bench.compare import check_problems
    from .bench.run import BenchRunError, run_bench
    from .bench.spec import BenchError, load_bench
    if args.bench_command in ("sweep", "compare", "gallery"):
        return _bench_many(args)
    try:
        if args.bench_command == "view" and not args.slow > 0:
            raise BenchError(f"--slow must be greater than 0, not {args.slow:g}")
        if args.bench_command == "check":
            bench, summary, problems = check_problems(args.file)
            for w in dict.fromkeys(p.message for p in problems if p.level == "warning"):
                print(f"scrimmage: warning: {w}", file=sys.stderr)
            print(summary)
            return
        from .bench.pictures import PICTURE_TRACE_HZ, write_pictures
        from .bench.report import headlines, write_results
        from .render import RenderUnavailable
        if args.bench_command == "view":
            bench = load_bench(args.file)
            _bench_warnings([bench])
            result = run_bench(bench, trace_hz=PICTURE_TRACE_HZ)
            print("\n".join(headlines(result)))
            from .bench.view import play
            play(result, args.slow)
            return
        out = args.out or args.file.parent / f"{args.file.stem}-results"
        with _bench_recorded("run", [args.file], out):
            bench = load_bench(args.file)
            _bench_warnings([bench])
            result = run_bench(bench, trace_hz=PICTURE_TRACE_HZ)
            print("\n".join(headlines(result)))
            out = write_results(result, out)
            try:
                pictures = write_pictures(result, out)
            except RenderUnavailable as e:
                print(f"scrimmage: no pictures: {e}", file=sys.stderr)
                pictures = []
    except (BenchError, BenchRunError, OSError) as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    print(f"wrote {out}/ (summary.txt, the CSVs" + "".join(f", {p.name}" for p in pictures) + ")")


def _bench_recorded(kind: str, files: list[Path], out: Path, folder: Path | None = None):
    """The run lock and the session log (source "cli") around one bench job; says once if it has to wait."""
    from .bench import session

    def waiting(other: dict) -> None:
        print(f"scrimmage: waiting: {other.get('source', 'another process')} is running {other.get('file', '?')}",
              file=sys.stderr)
    return session.recorded(folder or Path(files[0]).resolve().parent, kind, files, out, "cli", waiting)


def _bench_warnings(benches) -> None:
    """Each bench's warnings (a borrowed ball preset, ...) once, as `scrimmage: warning: ...` on stderr."""
    for w in dict.fromkeys(w for b in benches for w in b.warnings):
        print(f"scrimmage: warning: {w}", file=sys.stderr)


def _bench_many(args) -> None:
    """bench sweep, compare and gallery."""
    from PIL import Image

    from .bench import compare, gallery
    from .bench.pictures import PICTURE_TRACE_HZ
    from .bench.report import bench_estimates, headlines
    from .bench.run import BenchRunError
    from .bench.spec import BenchError
    from .launcher import report
    from .render import RenderUnavailable
    if args.bench_command == "sweep":
        kind, files, out = "sweep", [args.file], args.out or args.file.parent / f"{args.file.stem}-results"
    elif args.bench_command == "compare":
        kind, files, out = "compare", args.files, args.out or args.files[0].parent / "compare-results"
    else:
        kind, files, out = "gallery", [args.folder], args.out or args.folder / "gallery.html"
    folder = args.folder if kind == "gallery" else files[0].resolve().parent
    try:
        with _bench_recorded(kind, files, out, folder):
            if args.bench_command == "sweep":
                bench, benches = compare.sweep_benches(args.file)  # every combination checked before the first runs
                if not bench.sweep:
                    raise BenchError("no [sweep] section: use 'scrimmage bench run'")
                _bench_warnings([bench])
                from .bench.run import run_bench
                rows = compare.rows([(c, run_bench(b)) for c, b in benches])
                out.mkdir(parents=True, exist_ok=True)
                report.write_csv(rows, out / "results.csv", compare.BENCH_METRICS)
                print(compare.format_table(rows, bench))
                print(f"wrote {out / 'results.csv'}")
                charts = bench.charts or compare.default_charts(list(bench.sweep))
                paths = report.write_charts(rows, charts, out, None, compare.BENCH_METRICS, compare.CHART_KINDS,
                                            footer=bench_estimates(bench))
                print(report.NO_MATPLOTLIB if paths is None else "\n".join(f"wrote {p}" for p in paths))
            elif args.bench_command == "compare":
                from .bench.run import run_bench
                benches = compare.same_feed(args.files, args.feed_from)  # all checked before the first runs
                _bench_warnings(benches)
                results = [run_bench(b, trace_hz=PICTURE_TRACE_HZ) for b in benches]
                labels = compare.idea_labels(results)
                for label, r in zip(labels, results):
                    print(f"== {label}" + (f" ({r.bench.author})" if r.bench.author else ""))
                    print("\n".join(headlines(r)[:-1]))
                rows = compare.rows([({"idea": label}, r) for label, r in zip(labels, results)])
                print(compare.format_table(rows, results[0].bench))  # one piece for all: one UNCALIBRATED line
                out.mkdir(parents=True, exist_ok=True)
                report.write_csv(rows, out / "compare.csv", compare.BENCH_METRICS)
                print(f"wrote {out / 'compare.csv'}")
                Image.fromarray(gallery.compare_picture(results)).save(out / "compare.png")
                print(f"wrote {out / 'compare.png'}")
            else:
                entries = gallery.run_folder(args.folder)
                out = gallery.write_gallery(args.folder, out, entries)
                for e in entries:
                    if e.problem:
                        print(f"scrimmage: {e.file.name} did not run (listed in the gallery): {e.problem}",
                              file=sys.stderr)
                print(f"wrote {out} ({sum(e.result is not None for e in entries)} of {len(entries)} ideas ran)")
    except (BenchError, BenchRunError, OSError, RenderUnavailable) as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)


def _speeds(text: str) -> list[float]:
    """FROM:TO:STEP in m/s: FROM, FROM+STEP, ... up to TO."""
    try:
        lo, hi, step = (float(x) for x in text.split(":"))
    except ValueError:
        raise ValueError(f"--speeds {text!r}: want FROM:TO:STEP, three numbers in m/s") from None
    if not (0.0 < lo <= hi and step > 0.0 and all(map(math.isfinite, (lo, hi, step)))):
        raise ValueError(f"--speeds {text!r}: want 0 < FROM <= TO and STEP > 0")
    n = int((hi - lo) / step + 1e-9) + 1
    if n > 100:
        raise ValueError(f"--speeds {text!r}: {n} speeds is too many (100 at most)")
    return [round(lo + k * step, 9) for k in range(n)]


def _holds(items: list[str]) -> dict[str, float]:
    out = {}
    for item in items:
        name, eq, value = item.partition("=")
        try:
            if not eq or not name.strip():
                raise ValueError
            out[name.strip()] = float(value)
        except ValueError:
            raise ValueError(f"--set {item!r}: want JOINT=VALUE, e.g. elevator.carriage=0.9") from None
        if not math.isfinite(out[name.strip()]):
            raise ValueError(f"--set {item!r}: the value must be a finite number")
    return out


def _air(args) -> None:
    """Exit 0 with the budget, 1 when the robot has no cylinders, 2 on bad input."""
    import tomllib
    from .parts.assemble import assemble
    from .pneumatics.budget import budget, format_budget
    from .pneumatics.runtime import parse_section
    try:
        if not (args.match_seconds > 0.0 and math.isfinite(args.match_seconds)):
            raise ValueError("--match-seconds wants a time above 0 s")
        path = args.config.resolve()
        try:
            cfg = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{path}: {e}") from None
        built = assemble(cfg, path.parent)
        config = parse_section(cfg.get("pneumatics"))
    except (ValueError, OSError) as e:    # PartError and ConfigError are ValueErrors
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    if not built.cylinders:
        print(f"scrimmage: {args.config} has no pneumatic cylinders", file=sys.stderr)
        sys.exit(1)
    print(format_budget(budget(config, built.cylinders, args.match_seconds)))


def _terrain(args) -> None:
    from .terrain import sweep
    try:
        speeds = _speeds(args.speeds)
        holds = _holds(args.set)
        if not (args.gap > 0.0 and math.isfinite(args.gap)) or not (0.0 < args.tilt <= 180.0):
            raise ValueError("--gap wants a distance above 0 m, --tilt an angle above 0 and at most 180 deg")
        result = sweep.sweep(args.config, args.element, speeds, holds, sweep.Limits(gap=args.gap, tilt=args.tilt))
        out = args.out or args.config.parent / f"{args.config.stem}-{args.element}-sweep"
        out.mkdir(parents=True, exist_ok=True)
        sweep.write_csv(result, out / "sweep.csv")
        md = sweep.format_markdown(result, str(args.config))
        (out / "sweep.md").write_text(md, encoding="utf-8")
    except (ValueError, OSError) as e:    # SweepError, ConfigError and PartError are ValueErrors
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    print(md)
    print(f"wrote {out / 'sweep.csv'}")
    print(f"wrote {out / 'sweep.md'}")


def _climb(args) -> None:
    """Exit 0 when the climb reached the script's goal (or, with no goal, ended at a level), 1 when not, 2 on bad
    input."""
    from .climb import run as climb_run
    from .climb.script import load_script
    try:
        script = load_script(args.script)
        result = climb_run.run_climb(args.config, script)
        out = args.out or args.script.parent / f"{args.script.stem}-climb"
        out.mkdir(parents=True, exist_ok=True)
        climb_run.write_csv(result, out / "timeline.csv")
        md = climb_run.format_markdown(result, str(args.config), str(args.script))
        (out / "summary.md").write_text(md, encoding="utf-8")
    except (ValueError, OSError) as e:    # ScriptError, ConfigError and PartError are ValueErrors
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    print(md)
    print(f"wrote {out / 'timeline.csv'}")
    print(f"wrote {out / 'summary.md'}")
    sys.exit(0 if result.reached_goal else 1)


def _test(args) -> None:
    """Exit 0 when the test finished with every check passed, 1 when not, 2 when the file or its robot is wrong."""
    if args.test_command == "spread":
        _test_spread(args)
        return
    from .blocks import program as blocks_program
    from .blocks import run as blocks_run
    from .blocks import store as blocks_store
    try:
        if not args.file.is_file():
            raise FileNotFoundError(f"test file not found: {args.file}")
        program = blocks_program.load(args.file)
        result = blocks_run.TestRun(blocks_run.world_for(program), program).run()
    except (ValueError, OSError) as e:    # TestFileError, ConfigError and PartError are ValueErrors
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    out = args.out if args.out is not None else blocks_store.runs_dir(args.file)
    try:
        folder = blocks_store.save_run(result, program, out)     # names a new shuffle in the outcome
    except OSError as e:
        folder, error = None, e
    print(result.outcome)
    for check in result.checks:
        print(f"  {check['line']}")
    for line in blocks_run.goal_lines(result) + blocks_run.air_lines(result) + blocks_run.clearance_lines(result):
        print(f"  {line}")
    for f in result.feeds:
        if f["outcome"] == "stuck":
            what = "the floor piece" if f["source"] == "floor" else f"a piece from {f['source']}"
            print(f"  stuck in the robot: {what} (fed at {f['t']:.1f} s) resting on {f['body']}")
    for note in result.notes:
        print(f"  note: {note}")
    if folder is None:
        print(f"scrimmage: couldn't keep the run in {out}: {error}", file=sys.stderr)
        sys.exit(2)
    print(f"kept in {folder}")
    sys.exit(0 if result.passed else 1)


def _test_spread(args) -> None:
    """Exit 0 when every placement got the floor ball picked up, 1 when not, 2 when the file or its robot is wrong."""
    import csv
    import json
    from .blocks import program as blocks_program
    from .blocks import store as blocks_store
    from .blocks.spread import run_spread
    out = args.out if args.out is not None else blocks_store.runs_dir(args.file)
    try:
        if not args.file.is_file():
            raise FileNotFoundError(f"test file not found: {args.file}")
        folder = run_spread(blocks_program.load(args.file), out)
    except (ValueError, OSError) as e:    # TestFileError, ConfigError and PartError are ValueErrors
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    meta = json.loads((folder / "spread.json").read_text(encoding="utf-8"))
    print(meta["sentence"])
    with open(folder / "table.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            what = "picked up" if row["picked up"] == "yes" else "missed"
            print(f"  {row['placement']}: {what} ({row['outcome']})")
    print(f"kept in {folder}")
    sys.exit(0 if meta["picked"] and all(meta["picked"]) else 1)


def _print_export(s: dict) -> None:
    def names(xs):
        return ", ".join(xs) if xs else "none"
    print(f"exported part {s['part']!r} to {s['out_dir']}")
    print(f"  bodies ({len(s['bodies'])}): {names(s['bodies'])}")
    print(f"  joints ({len(s['joints'])}): {names(s['joints'])}")
    print(f"  loops: {s['loops']}, couplings: {s['couplings']}")
    print(f"  slots: {names(s['slots'])}")
    print(f"  mounts: {names(s['mounts'])}")
    print(f"  mass: {s['mass_kg']:.3f} kg")
    for w in s["warnings"]:
        print(f"warning: {w}")


def _model(args) -> None:
    try:
        config = load_config(args.config)
        world = build_world(config)
        if world.parts is None:
            mjcf = config.dir / config.raw.get("model", {}).get("mjcf", "model.xml")
            raise ConfigError(f"this config uses a hand-written model: {mjcf.resolve()}")
        xml = world.parts.spec.to_xml()
        if args.output is not None:
            args.output.write_text(xml)
        else:
            print(xml)
    except (ConfigError, ValueError, OSError) as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)


def _parts(args) -> None:
    from .parts.model import PartError
    from .parts.stock import describe
    try:
        print(describe(args.name))
    except PartError as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)


def _edit(args) -> None:
    import os
    import threading
    from .assembler import scenario_validate
    from .assembler.server import App, remember_opened
    from .blocks.program import TestFileError
    from .cad import CadError
    from .assembler.templates import new_config, new_scenario

    def fail(msg: str) -> None:
        print(f"scrimmage: {msg}", file=sys.stderr)
        sys.exit(2)

    how = "create one with --new swerve, --new swerve-rev, --new differential or --new-scenario --robot CONFIG"
    if args.file is None and (args.new or args.new_scenario):
        fail(f"{'--new' if args.new else '--new-scenario'} needs FILE, the file to write")
    if args.new and args.new_scenario:
        fail("use --new (a robot config) or --new-scenario (a scenario), not both")
    cad = args.file is not None and args.file.suffix.lower() in (".step", ".stp", ".stl", ".obj")
    if args.robot is not None and not args.new_scenario and not cad:
        fail("--robot goes with --new-scenario or a CAD file")
    if cad and args.robot is not None and not args.robot.is_file():
        fail(f"robot config {args.robot} not found")
    if args.new_scenario and args.robot is None:
        fail("--new-scenario needs --robot CONFIG (the robot config the scenario runs)")
    if args.new or args.new_scenario:
        if args.new_scenario:
            if not args.robot.is_file():
                fail(f"robot config {args.robot} not found")
            rel = Path(os.path.relpath(args.robot.resolve(), args.file.resolve().parent)).as_posix()
            text = new_scenario(rel)
        else:
            text = new_config(args.new)
        try:
            with open(args.file, "x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:
            fail(f"{args.file} already exists; {'--new' if args.new else '--new-scenario'} won't overwrite it")
        except OSError as e:
            fail(str(e))
        print(f"wrote {args.file}")
        if args.new_scenario:
            res = scenario_validate.check(text, args.file)
            for e in res.errors:   # e.g. the robot config has errors: the editor opens it read-only and says why
                print(f"scrimmage: note: {e.message}", file=sys.stderr)
    elif args.file is not None and not args.file.exists():
        fail(f"{args.file} not found; {how}")
    try:
        app = App(args.file, open_browser=not args.no_browser, port=args.port,
                  parts_dir=args.robot.resolve().parent / "parts" if cad and args.robot is not None else None)
        url = app.start()
        if args.file is not None:
            remember_opened(args.file)
            app.remember_part()
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            fail(f"port {args.port} is in use; choose another with --port or leave it out")
        fail(str(e))
    except CadError as e:   # an import-made part folder whose import file is gone
        fail(str(e))
    except TestFileError as e:   # a block test that can't be read
        fail(str(e))
    try:
        # Ctrl+C stops the server even when the parent started us with SIGINT ignored (Python then keeps it ignored).
        # Set before the URL is printed, inside the try: a Ctrl+C right after the URL appears must stop us cleanly.
        signal.signal(signal.SIGINT, signal.default_int_handler)
        print(url, flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


def _log_warnings(world) -> None:
    for w in world.warnings:
        logging.getLogger("scrimmage").warning(w)


def _serve(args, ap) -> None:
    try:
        config = load_config(args.config)
        set_sim_mode(config, args.sim_mode)
        if (unused := sim_mode_unused(config, args.sim_mode, launching=args.launch)) is not None:
            logging.getLogger("scrimmage").warning(unused)
        world = build_world(config)
        bridge = Bridge(world, args.host, args.port, log_path=args.log, viewer=args.viewer, nt=not args.no_nt and config.nt,
                        speed=args.speed, step=args.step if args.step is not None else config.step,
                        odometry_topic=config.odometry_topic, nt_port=config.nt_port,
                        sim_mode=config.robot_program.sim_mode if args.launch and config.robot_program else None)
    except (ConfigError, ValueError) as e:
        ap.error(str(e))
    _log_warnings(world)
    if args.launch and config.robot_program is None:
        ap.error("--launch needs a [robot_program] section in the config")
    if args.launch:
        from . import java_tools
        missing = java_tools.agent_missing_message(config.path, config.robot_program.command,
                                                    rev=binds_rev(config.raw))
        if missing:
            ap.error(missing)
        from .session import lockstep_version_problem
        if problem := lockstep_version_problem(config):
            ap.error(problem)

    robot = None

    async def go() -> None:
        nonlocal robot
        task = asyncio.create_task(bridge.run())
        bridge.agent_state = lambda: robot.agent_state() if robot is not None else None
        bridge.rev_state = lambda: robot.rev_state if robot is not None else None
        if config.robot_program is not None:
            bridge.robot_language = robot_language(config.robot_program.command)
        try:
            if args.launch:
                # don't wait forever if the server fails to start (port in use): `await task` raises below
                listening = asyncio.ensure_future(bridge.listening.wait())
                await asyncio.wait({task, listening}, return_when=asyncio.FIRST_COMPLETED)
                listening.cancel()
            if args.launch and bridge.listening.is_set():
                p = config.robot_program
                robot = RobotProcess(p.command, p.cwd, p.env, clock=lambda: world.time, echo=True)
                await robot.start(bridge.bound_port)
            await task
        finally:
            if robot is not None:
                await robot.stop()

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    except (RuntimeError, OSError) as e:  # couldn't launch the robot program, or bind the port
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    sys.exit(bridge.exit_code)


def _run(args, ap) -> int:
    try:
        scenario = load_scenario(args.scenario)
        config = load_config(scenario.config_path)
        world = build_world(config)
        validate(scenario, world)
    except (ScenarioError, ConfigError, ValueError) as e:
        ap.error(str(e))
    _log_warnings(world)
    out = args.out or Path("runs") / f"{scenario.path.stem}-{datetime.now():%Y%m%d-%H%M%S}"
    try:
        report = asyncio.run(run_scenario(scenario, out, speed=args.speed, sim_mode=args.sim_mode))
    except Exception as e:  # KeyboardInterrupt and SystemExit go through
        if args.verbose:
            traceback.print_exc()
        print(f"scrimmage: {e}", file=sys.stderr)
        return 2
    print(report.summary())
    print(f"report: {out / 'report.md'}")
    return report.exit_code


if __name__ == "__main__":
    main()
