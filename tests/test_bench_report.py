"""Bench results: headlines, the tables and CSVs, landings with [flight], path pictures and a pass."""

import csv
import dataclasses
import math
import re
import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.bench.pictures import (SHAPE_RGBA, UNCALIBRATED_INK, pass_frames, pass_sheet, path_picture,
                                       write_gif)
from scrimmage.bench.report import STATUS_COLORS, energy_failures, headlines, landings, write_results
from scrimmage.bench.run import run_bench
from scrimmage.bench.spec import load_bench, parse_bench, with_overrides
from scrimmage.launcher.flight import fly, fly_state
from scrimmage.launcher.inputs import load_setup
from scrimmage.launcher.run import run_setup

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "bench"


@pytest.fixture(scope="module")
def pinch():
    return run_bench(load_bench(EXAMPLES / "pinch_car.toml"))


@pytest.fixture(scope="module")
def bounce():
    return run_bench(load_bench(EXAMPLES / "bounce_bar.toml"))


FLIGHT = {"outlet": "shot", "height": 0.5, "target_distance": 3.0, "target_height": 2.0, "ring_diameter": 1.2}


@pytest.fixture(scope="module")
def hooded_flight():
    path = EXAMPLES / "hooded.toml"
    raw = with_overrides(tomllib.loads(path.read_text()), {"inlet.feed.count": 2})
    raw["flight"] = dict(FLIGHT)
    return run_bench(parse_bench(raw, path.parent))


def test_fly_state_matches_fly():
    setup = load_setup(ROOT / "examples" / "launcher" / "hooded.toml")[0]
    shot = next(s for s, _ in run_setup(setup).shots if s.status == "launched")
    phi = math.radians(setup.hood.exit_angle_deg + 90.0)
    rc = setup.wheels.diameter / 2 + setup.hood.gap / 2
    pos = [rc * math.cos(phi), shot.lane, setup.launcher.height + rc * math.sin(phi)]
    e, y = math.radians(shot.launch_deg), math.radians(shot.yaw_deg)
    vel = shot.speed * np.array([math.cos(e) * math.cos(y), math.cos(e) * math.sin(y), math.sin(e)])
    vel[:2] += setup.robot.velocity
    axis = [math.sin(y), -math.cos(y), 0.0]
    assert fly_state(pos, vel, axis, shot.spin, setup.ball, setup.air, setup.goal) == fly(shot, setup, setup.ball)


def test_headlines_wording(pinch):
    lines = headlines(pinch)
    assert 3 <= len(lines) <= 6
    assert re.fullmatch(r"3 of 3 left through `shot` at \d+\.\d m/s ± \d+\.\d, \d+° down", lines[0]), lines[0]
    assert any(re.fullmatch(r"the `(top|bottom)` shaft drops \d+% and recovers in \d+\.\d\d s"
                            r"( \(the worse of 2 shafts\))?", line) for line in lines), lines
    assert not any("hardest" in line for line in lines)  # no part stands out
    assert lines[-1].startswith("UNCALIBRATED: ") and "tread" in lines[-1] and "friction" in lines[-1]


def test_squishy_uncalibrated_names_the_ball_tests(hooded_flight):
    last = headlines(hooded_flight)[-1]
    assert last.startswith("UNCALIBRATED: ")
    assert "stiffness" in last and "squeeze" in last and "bounce" in last and "drop" in last


def test_loads_headline_when_a_bar_takes_the_hit(bounce):
    lines = headlines(bounce)
    hit = [line for line in lines if "`kicker`" in line]
    assert len(hit) == 1 and re.search(r"up to \d+ N", hit[0]), lines
    assert 3 <= len(lines) <= 6


def test_loads_headline_with_two_touched_shapes(bounce):
    """A wheel plus a bounce bar: the bar stands out against the one other part; one touched part has no line."""
    from scrimmage.bench.run import LoadStats

    def with_loads(**peaks):
        return dataclasses.replace(bounce, loads={s: LoadStats(shape=s, peak_force_n=f, peak_impulse_ns=0.0, at=None)
                                                  for s, f in peaks.items()})
    lines = headlines(with_loads(bottom=50.0, kicker=200.0, feeder=0.0))
    hit = [line for line in lines if "hardest" in line]
    assert len(hit) == 1 and "`kicker`" in hit[0] and "up to 200 N, 4.0 times" in hit[0], lines
    assert not any("hardest" in line for line in headlines(with_loads(bottom=50.0, kicker=140.0)))
    assert not any("hardest" in line for line in headlines(with_loads(kicker=200.0, bottom=0.0)))


def test_jammed_and_escaped_are_counted(pinch):
    p = pinch.pieces
    odd = dataclasses.replace(pinch, pieces=[
        p[0], dataclasses.replace(p[1], status="jammed", outlet=None, speed=None,
                                  path=p[1].path[:1] + [(0.3, 0.0, 0.0, 0.0)]),  # stopped between the wheels
        dataclasses.replace(p[2], status="escaped", outlet=None, speed=None)])
    lines = headlines(odd)
    assert re.fullmatch(r"1 of 3 left through `shot` at .*", lines[0]), lines
    assert lines[1].startswith("2 of 3 didn't get out: 1 jammed at `"), lines
    assert "1 escaped" in lines[1]


def test_write_results_tables(pinch, tmp_path):
    out = write_results(pinch, tmp_path / "r")
    names = sorted(p.name for p in out.iterdir())
    assert names == ["loads.csv", "pieces.csv", "shafts.csv", "summary.txt"]
    summary = (out / "summary.txt").read_text()
    for line in headlines(pinch):
        assert line in summary
    rows = list(csv.DictReader((out / "pieces.csv").open()))
    assert [r["status"] for r in rows] == ["out"] * 3 and float(rows[0]["speed_m_s"]) > 5
    shafts = list(csv.DictReader((out / "shafts.csv").open()))
    assert {r["shaft"] for r in shafts} == {"top", "bottom"}
    loads = list(csv.DictReader((out / "loads.csv").open()))
    assert {r["shape"] for r in loads} == {"top", "bottom", "track"}


def test_flight_landing_written(hooded_flight, tmp_path):
    out = write_results(hooded_flight, tmp_path)
    rows = list(csv.DictReader((out / "landings.csv").open()))
    assert len(rows) == 2
    piece = hooded_flight.pieces[0]
    (_, x, y, z) = piece.path[-1]
    e, yaw = math.radians(piece.elevation_deg), math.radians(piece.yaw_deg)
    v = piece.speed * np.array([math.cos(e) * math.cos(yaw), math.cos(e) * math.sin(yaw), math.sin(e)])
    land = landings(hooded_flight)[0][1]
    spin = np.array(piece.spin)
    ball = hooded_flight.bench.piece.ball
    from scrimmage.launcher.inputs import PRESETS, Air, Goal
    want = fly_state([x, y, z + 0.5], v, spin / np.linalg.norm(spin), float(np.linalg.norm(spin)), ball,
                     Air(**PRESETS["air"]["default"]), Goal(distance=3.0, height=2.0, diameter=1.2))
    assert land == want
    assert float(rows[0]["floor_x_m"]) == pytest.approx(want.floor[0], abs=1e-3)
    assert rows[0]["in_ring"] == ("yes" if want.scored else "no")
    lines = headlines(hooded_flight)
    assert any("ring" in line and "3.0 m away" in line for line in lines), lines
    assert "landing" in (out / "summary.txt").read_text().lower()


STATUS_RGB = {k: np.array(v) for k, v in STATUS_COLORS.items()}


def _count(img, rgb, tol=40):
    return int((np.abs(img.astype(int) - rgb).sum(axis=2) < tol).sum())


def test_path_picture_shapes(pinch):
    img = path_picture(pinch, "side", width=640, height=360)
    assert img.shape == (360, 640, 3) and img.dtype == np.uint8
    assert img.std() > 5
    assert _count(img, STATUS_RGB["out"]) > 200        # three thick green paths
    assert _count(img, STATUS_RGB["jammed"]) == 0
    p = pinch.pieces
    odd = dataclasses.replace(pinch, pieces=[p[0], dataclasses.replace(p[1], status="jammed"), p[2]])
    assert _count(path_picture(odd, "side", width=640, height=360), STATUS_RGB["jammed"]) > 50
    top = path_picture(pinch, "top", width=640, height=360)
    assert top.shape == (360, 640, 3) and top.std() > 5
    with pytest.raises(ValueError, match="side"):
        path_picture(pinch, "front")


def test_pass_frames_and_gif(pinch, tmp_path):
    frames = pass_frames(pinch, frames=4, width=320, height=180)
    assert len(frames) == 4
    times = [t for t, _ in frames]
    first = next(p for p in pinch.pieces if p.status == "out")
    assert times == sorted(times) and first.t_in <= times[0] and times[-1] <= first.t_out + 1e-9
    assert all(img.shape == (180, 320, 3) for _, img in frames)
    assert not np.array_equal(frames[0][1], frames[-1][1])  # the car moves
    gif = write_gif(frames, tmp_path / "pass.gif", fps=10)
    from PIL import Image
    with Image.open(gif) as im:
        assert im.n_frames == 4


UNCAL_RGB = np.array(UNCALIBRATED_INK)


def test_failed_energy_check_is_the_first_headline(pinch, tmp_path):
    bad = dataclasses.replace(pinch, energy=[dataclasses.replace(e, ok=(k != 1)) for k, e in enumerate(pinch.energy)])
    lines = headlines(bad)
    assert lines[0] == ("the energy check failed for 1 of 3 pieces: the simulation is wrong here, not the design; "
                        "try a smaller [run] timestep"), lines
    assert lines[-1].startswith("UNCALIBRATED") and len(lines) <= 6
    assert energy_failures(bad) == 1 and energy_failures(pinch) == 0
    assert not any("energy" in line for line in headlines(pinch))


def test_tilt_is_blank_for_a_ball(hooded_flight, pinch, tmp_path):
    out = write_results(hooded_flight, tmp_path / "ball")
    rows = list(csv.DictReader((out / "pieces.csv").open()))
    assert rows and all(r["tilt_deg"] == "" for r in rows)
    assert hooded_flight.pieces[0].tilt_deg is not None  # measured, just not shown for a ball
    out = write_results(pinch, tmp_path / "car")
    assert all(r["tilt_deg"] != "" for r in csv.DictReader((out / "pieces.csv").open()))


def test_borrowed_preset_warning_in_summary(tmp_path):
    raw = tomllib.loads((EXAMPLES / "hooded.toml").read_text())
    raw["piece"] = {"kind": "squishy", "shape": "sphere", "size": [0.1778], "mass": 0.14}
    raw["inlet"][0]["count"] = 1
    raw["run"] = {"duration": 1.2}
    r = run_bench(parse_bench(raw, EXAMPLES))
    text = (write_results(r, tmp_path) / "summary.txt").read_text()
    assert "warning: [piece] stiffness_n and restitution not given; using the nearest ball preset's" in text


def test_pictures_say_uncalibrated(pinch, tmp_path):
    img = path_picture(pinch, "side", width=640, height=360)
    assert _count(img[-30:], UNCAL_RGB) > 30  # the red line in the bottom band
    assert _count(img[:-60], UNCAL_RGB) == 0
    frames = pass_frames(pinch, frames=2, width=320, height=180)
    sheet = pass_sheet(frames)
    assert sheet.shape[0] > 180 and _count(sheet[180:], UNCAL_RGB) > 20
    from PIL import Image
    with Image.open(write_gif(frames, tmp_path / "p.gif")) as im:
        assert _count(np.asarray(im.convert("RGB"))[-20:], UNCAL_RGB, tol=60) > 10


def test_path_picture_draws_outlines_where_pieces_touch(bounce):
    """The kicker takes the hardest hits: the ball's outline must be drawn touching it, not just its center line."""
    assert all(len(p.poses) >= 2 for p in bounce.pieces)  # passes against parts, then the end
    assert any(t == p.t_out for p in bounce.pieces for t, _ in p.poses[-1:])
    bare = dataclasses.replace(bounce, pieces=[dataclasses.replace(p, poses=[]) for p in bounce.pieces])
    green = STATUS_RGB["out"]
    assert _count(path_picture(bounce, "side"), green) > _count(path_picture(bare, "side"), green) + 1000
    from scrimmage.bench.pictures import _Frame, _piece_outline
    frame = _Frame(bounce, "side", 1280, 720, 100, 104)
    kicker = next(b for b in bounce.bench.bars if b.name == "kicker")
    kx, ky = frame.px((np.array(kicker.a) + np.array(kicker.b)) / 2)
    touch = [q for p in bounce.pieces for t, q in p.poses]
    near = []
    for q in touch:
        kind, (x, y, r) = _piece_outline(bounce, frame, q)
        assert kind == "circle" and r == pytest.approx(0.075 * frame.scale, rel=0.01)  # true size: 0.15 m ball
        near.append(math.hypot(x - kx, y - ky) - r)
    assert min(near) < kicker.diameter * frame.scale  # an outline reaches the kicker


def test_pass_frames_thicken_thin_shapes(hooded_flight):
    """The hood is a few mm thick: the pass frames draw it at a projector's size, under the ball."""
    (_, img), = pass_frames(hooded_flight, frames=1, width=640, height=360)
    gray = np.array([int(255 * c) for c in SHAPE_RGBA["other"][:3]])
    assert _count(img, gray) > 600
