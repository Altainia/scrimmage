"""The intake example's robustness grids (K6-R10, K6-R15): the intake spike's three grids, one setting at a time (44
settings and the 0 V control), the 16 corners and 40 random draws (seed 7), run on examples/blocks/note-intake's own
files. Each geometry setting is an edit to a copy of the example (K6-P2; the lip as the spike's generator moved it);
the rest are the test's volts, the drive's speed and the NOTE's placement.

    python tests/intake_grid.py {oat,corners,random,all} [--out DIR]

prints each grid's table (one line per case, then the count) and, with --out, writes DIR/<grid>.jsonl.
"""

from __future__ import annotations

import argparse
import atexit
import hashlib
import itertools
import json
import math
import random
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import tomlkit

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "blocks" / "note-intake"
IN = 0.0254
SPEED_PER_V = 0.32  # source: intake spike, speed.py (the stand-in's speed per drive volt, m/s)
REACH, GAP_IN, TRAVEL = 0.6, 20.0, 1.4  # source: intake spike harness (run_case)
NOTE_HALF = 0.1778  # source: intake spike harness (run_case): the NOTE's outer radius, 7 in
SETTLE_S = 0.4      # source: intake spike harness (run_case): the drive runs this long past its travel
# source: the example's part.toml: the bin's floor, side walls and lid end here, 3 mm behind the bottom roller, and
# the ceiling over the rollers starts here
FLOOR_FRONT = 0.4716
NOMINAL_CLEAR_MM = 3.0  # source: the example's part.toml: the bottom roller 3 mm over the carpet
PART = Path("parts") / "intake" / "part.toml"
ROBOT = Path("note-intake-standin.toml")
TEST = Path("intake.test.toml")
MOUNT = "intake"
TARGET_CORNERS = 14  # source: K6-R10's target, at least 14 of the 16 corners
CONTROL = "control_0V"


@dataclass(frozen=True)
class Case:
    grid: str
    label: str
    x: object
    axle_in: float = 0.0      # the top rollers up, in
    lip_in: float = 0.0       # the bottom roller and the floor's front forward, in
    clear_mm: float = 3.0     # the bottom roller's clearance over the carpet, mm
    yaw_deg: float = 0.0      # the intake turned on its mount
    volts: float = 8.0        # the roller's
    speed: float = 1.0        # the drive's, m/s
    offset_in: float = 0.0    # the NOTE left of the robot's line, in

    def geometry(self) -> tuple:
        return (self.axle_in, self.lip_in, self.clear_mm, self.yaw_deg)


def oat_cases() -> list[Case]:
    """The spike's oat2.py list, in its order: nominal, the 0 V control, then each setting alone."""
    g = "oat"
    cases = [Case(g, "nominal", 0), Case(g, CONTROL, 0, volts=0.0)]
    cases += [Case(g, "axle_in", v, axle_in=v) for v in (-0.5, -0.375, -0.25, -0.125, 0.125, 0.25, 0.375, 0.5)]
    cases += [Case(g, "lip_in", v, lip_in=v) for v in (-1, -0.75, -0.5, -0.25, 0.25, 0.5, 0.75, 1)]
    cases += [Case(g, "volts", v, volts=v) for v in (5, 6, 7, 9, 10, 11)]
    cases += [Case(g, "speed", v, speed=v) for v in (0.5, 0.75, 1.25, 1.5, 1.75, 2.0)]
    cases += [Case(g, "yaw_deg", v, yaw_deg=v) for v in (-15, -10, -5, 5, 10, 15)]
    cases += [Case(g, "offset_in", v, offset_in=v) for v in (-6, -4, -2, 2, 4, 6)]
    cases += [Case(g, "clear_mm", v, clear_mm=v) for v in (1.5, 6.0, 10.0)]
    return cases


def corner_cases() -> list[Case]:
    """The spike's combo.py corners: axle x lip x volts x speed at their ends, the yaw at -15, 0, +15 in turn."""
    out = []
    for k, (a, l, v, s) in enumerate(itertools.product((-0.5, 0.5), (-1, 1), (5, 11), (0.5, 2.0))):
        yaw = (-15, 0, 15)[k % 3]
        out.append(Case("corners", "corner", [a, l, v, s, yaw], axle_in=a, lip_in=l, volts=v, speed=s, yaw_deg=yaw))
    return out


def random_cases(n: int = 40, seed: int = 7) -> list[Case]:
    """The spike's combo.py random draws, in its order (axle, lip, volts, speed, yaw, offset), volts and speed
    rounded as it ran them."""
    r = random.Random(seed)
    out = []
    for _ in range(n):
        a, l = r.uniform(-0.5, 0.5), r.uniform(-1, 1)
        v, s, yaw, off = r.uniform(5, 11), r.uniform(0.5, 2.0), r.uniform(-15, 15), r.uniform(-4, 4)
        x = [round(a, 3), round(l, 3), round(v, 2), round(s, 2), round(yaw, 1), round(off, 1)]
        out.append(Case("random", "random", x, axle_in=a, lip_in=l, volts=round(v, 2), speed=round(s, 2),
                        yaw_deg=yaw, offset_in=off))
    return out


GRIDS = {"oat": oat_cases, "corners": corner_cases, "random": random_cases}


# ------------------------------------------------------------------------------------------------ the variant

def _m(v: float) -> float:
    return round(v, 7)    # choice: positions written to 0.1 micron


def _body(doc, name: str):
    return next(b for b in doc["body"] if b["name"] == name)


def _placed(part, name: str) -> list[float]:
    """A body's position in the part's frame, through its parents (none of this part's bodies is turned)."""
    body = _body(part, name)
    pos = [float(v) for v in body.get("pos", [0.0, 0.0, 0.0])]
    parent = body.get("parent")
    if parent and any(b["name"] == parent for b in part["body"]):
        pos = [a + b for a, b in zip(pos, _placed(part, parent))]
    return pos


def _move_lip(part, d: float) -> None:
    """The bottom roller d forward, and every bin and guide box with an end at the floor's front (the floor slab, the
    side walls and the lid end there; the ceiling starts there) keeping that end at the moved front, as the spike's
    designs.nip did. A separate lip body moved whole (K6-P2) would leave the floor's two blocks overlapping, which
    doubles the NOTE's contacts on the overlap, or a hole between them: the spike's corners then come out
    differently."""
    pos = _body(part, "bottom")["pos"]
    pos[0] = _m(pos[0] + d)
    for name in ("hopper", "guide"):
        for geom in _body(part, name)["geom"]:
            if "box" not in geom:
                continue
            x, size = geom["pos"][0], geom["box"][0]
            if abs(x + size / 2 - FLOOR_FRONT) < 1e-5:
                geom["box"][0], geom["pos"][0] = _m(size + d), _m(x + d / 2)
            elif abs(x - size / 2 - FLOOR_FRONT) < 1e-5:
                geom["box"][0], geom["pos"][0] = _m(size - d), _m(x + d / 2)


def variant(case: Case, into: Path, example: Path = EXAMPLE) -> Path:
    """A copy of the example in `into` with the case's geometry (K6-P2, but the lip as the spike's generator moved
    it): axle moves `roller` and `top0` up; lip moves `bottom` forward, and the floor's, walls' and lid's front ends
    and the ceiling's back end with it (_move_lip); clearance raises the mount and keeps the front roller where it
    was, 3.4 in from the bottom one; and yaw turns the mount. Comments are kept."""
    into = Path(into)
    shutil.copytree(example, into, ignore=shutil.ignore_patterns("*-runs"), dirs_exist_ok=True)
    part = tomlkit.parse((into / PART).read_text(encoding="utf-8"))
    robot = tomlkit.parse((into / ROBOT).read_text(encoding="utf-8"))
    mount = next(p for p in robot["part"] if p["name"] == MOUNT)
    # the front roller's center from the bottom one's, as saved: 3.4 in apart, a 1.4 in nip (the spike's designs.nip),
    # and 0.03764 m over it since the K6 retune (the spike's place was 0.02748 m over)
    front, back = _placed(part, "roller"), _placed(part, "bottom")
    h0 = front[2] - back[2]
    cd = math.hypot(front[0] - back[0], h0)
    if case.axle_in:
        for name in ("roller", "top0"):
            pos = _body(part, name)["pos"]
            pos[2] = _m(pos[2] + case.axle_in * IN)
    if case.lip_in:
        _move_lip(part, case.lip_in * IN)
    if case.clear_mm != NOMINAL_CLEAR_MM:
        d = (case.clear_mm - NOMINAL_CLEAR_MM) / 1000
        mount["pos"][2] = _m(mount["pos"][2] + d)
        pos = _body(part, "roller")["pos"]
        pos[2] = _m(pos[2] - d)
        pos[0] = _m(pos[0] + math.sqrt(cd ** 2 - (h0 - d) ** 2) - math.sqrt(cd ** 2 - h0 ** 2))
    if case.yaw_deg:
        if "rot" in mount:
            mount["rot"][2] = float(case.yaw_deg)
        else:
            mount["rot"] = [0.0, 0.0, float(case.yaw_deg)]
    (into / PART).write_text(tomlkit.dumps(part), encoding="utf-8")
    (into / ROBOT).write_text(tomlkit.dumps(robot), encoding="utf-8")
    return into


# ------------------------------------------------------------------------------------------------ one case

_TMP: list[Path] = []
_WORLD: dict = {}


def _tmp() -> Path:
    if not _TMP:
        _TMP.append(Path(tempfile.mkdtemp(prefix="intake-grid-")))
        atexit.register(shutil.rmtree, _TMP[0], True)
    return _TMP[0]


def _folder(case: Case, example: Path) -> Path:
    """The case's variant folder, made once per geometry and example contents."""
    h = hashlib.sha1(repr(case.geometry()).encode())
    for p in sorted(example.rglob("*.toml")):
        if not any(part.endswith("-runs") for part in p.relative_to(example).parts):
            h.update(str(p.relative_to(example)).encode() + p.read_bytes())
    folder = _tmp() / h.hexdigest()[:12]
    if not folder.exists():
        variant(case, folder, example)
    return folder


def _test_text(folder: Path, case: Case) -> str:
    """The example's test with the case's roller volts, and its drive at the case's speed for long enough to travel
    the gap and TRAVEL past it."""
    doc = tomlkit.parse((folder / TEST).read_text(encoding="utf-8"))
    for block in doc["block"]:
        if block["do"] == "spin":
            block["volts"] = float(case.volts)
        elif block["do"] == "drive":
            block["volts"] = round(case.speed / SPEED_PER_V, 2)
            block["seconds"] = round((GAP_IN * IN + TRAVEL) / case.speed + SETTLE_S, 2)
    return tomlkit.dumps(doc)


def _ring_speed(world) -> float:
    """The fastest segment of any in-use soft ring now (m/s, Ring.speeds)."""
    pool = world.test_range.soft
    if pool is None:
        return 0.0
    best = 0.0
    for k, ring in enumerate(pool.parked.rings):
        if not pool.parked.free[k]:
            best = max(best, float(ring.speeds(world.data).max()))
    return best


def run_case(case: Case, example: Path = EXAMPLE) -> dict:
    """Run one case on its variant (the world reused while the variant is the same) with the NOTE placed as the
    spike's harness did: the field's lie, REACH + GAP_IN past the bumper, offset_in to the left."""
    from scrimmage.blocks.program import loads
    from scrimmage.blocks.run import TestRun
    from scrimmage.blocks.run_setup import world_for
    from scrimmage.blocks.spread import spread_ball

    folder = _folder(case, example)
    prog = loads(_test_text(folder, case), folder / TEST)
    if _WORLD.get("folder") != folder:
        _WORLD.clear()
        _WORLD.update(folder=folder, world=world_for(prog))
    w = _WORLD["world"]
    lie = spread_ball(w)[1]
    dist = 0.43 + REACH + GAP_IN * IN + NOTE_HALF     # source: the spike's harness: the robot's center to the NOTE's
    place = {"offset_in": case.offset_in, "distance_in": dist / IN, "angle_deg": 0.0,
             "rotation_deg": lie.get("rotation_deg", 0.0), "tip_deg": lie.get("tip_deg", 0.0)}
    vmax = [0.0]
    orig = w.step

    def step(*a, **k):
        res = orig(*a, **k)
        vmax[0] = max(vmax[0], _ring_speed(w))
        return res

    w.step = step
    t0 = time.perf_counter()
    try:
        r = TestRun(w, prog, placement=place).run()
    finally:
        del w.step
    return {"grid": case.grid, "label": case.label, "x": case.x, "passed": bool(r.passed), "status": r.status,
            "outcome": r.outcome, "vmax": round(vmax[0], 2), "wall": round(time.perf_counter() - t0, 1)}


# ------------------------------------------------------------------------------------------------ the grids

def run_grid(name: str, example: Path = EXAMPLE, out: Path | None = None) -> list[dict]:
    """Every case of grid `name` (oat, corners or random); with `out`, one JSON line per case in out/<name>.jsonl."""
    rows = []
    sink = None
    if out is not None:
        Path(out).mkdir(parents=True, exist_ok=True)
        sink = open(Path(out) / f"{name}.jsonl", "w", encoding="utf-8")
    try:
        for case in GRIDS[name]():
            row = run_case(case, example)
            rows.append(row)
            if sink:
                sink.write(json.dumps(row) + "\n")
                sink.flush()
    finally:
        if sink:
            sink.close()
    return rows


def table(rows: list[dict]) -> list[str]:
    lines = [f"{r['label']:<11} {json.dumps(r['x']):<40} {'pass' if r['passed'] else 'FAIL'}  "
             f"{r['vmax']:5.1f} m/s  {r['outcome']}" for r in rows]
    lines.append(f"{sum(r['passed'] for r in rows)} of {len(rows)} passed; "
                 f"fastest segment {max((r['vmax'] for r in rows), default=0.0):.1f} m/s")
    return lines


def meets_target(oat: list[dict], corners: list[dict]) -> bool:
    """K6-R10's target: every one-setting case but the 0 V control passes, the control fails, and at least 14 of
    the 16 corners pass."""
    control = [r for r in oat if r["label"] == CONTROL]
    rest = [r for r in oat if r["label"] != CONTROL]
    return (len(control) == 1 and not control[0]["passed"] and all(r["passed"] for r in rest)
            and sum(r["passed"] for r in corners) >= TARGET_CORNERS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("grid", choices=[*GRIDS, "all"])
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    names = list(GRIDS) if args.grid == "all" else [args.grid]
    got = {}
    for name in names:
        got[name] = run_grid(name, out=args.out)
        print(f"== {name}", flush=True)
        print("\n".join(table(got[name])), flush=True)
    if "oat" in got and "corners" in got:
        print("meets the target:", meets_target(got["oat"], got["corners"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
