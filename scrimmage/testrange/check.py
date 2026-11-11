"""`scrimmage range check` and `range view`: a range file read on its own, with no robot, said in a few lines or
pictured from above.

`is_range_file` tells a range file (a season's field: its `[range]` table names the field, `name = "..."`, and it has
no robot) from a robot config (whose `[range]` table points at a range, `file = "..."`), so `scrimmage edit` can say
what a range file is instead of reading it as a robot config.

`summary` is what `range check` prints for a file that reads: its name, field size and what it holds, then its
warnings (spec.Range.warnings). `field_model` builds the field alone (the generated field at the range's size and the
range's goals, obstacles, elements, stations and floor pieces, no robot) for `picture`, a top-down view, and for the
MuJoCo viewer.
"""

from __future__ import annotations

from pathlib import Path

from .. import i18n
from .spec import BUILTIN_RANGES, Range, load_range

ROBOT_KEYS = ("part", "robot", "model", "robot_program", "motor", "encoder", "gyro", "shooter", "partner")
SCENARIO_KEYS = ("config", "step", "check", "match")      # a scenario's: it may have a [range] of overrides too
OTHER_KEYS = ("test", "bench", "measure", "import")      # a test's, a bench file's, a measurement's, an import file's


def is_range_file(raw: dict) -> bool:
    """Whether a TOML file's tables are a range file's: a `[range]` table naming the field (`name`, which a range file
    must have) that isn't a robot config's pointer to one (`file = "..."`), and none of the tables of a robot config, a
    scenario (its `[range]` holds overrides: preload, start), a test, a bench file, a measurement or an import file."""
    rng = raw.get("range")
    return (isinstance(rng, dict) and "name" in rng and "file" not in rng
            and not any(k in raw for k in ROBOT_KEYS + SCENARIO_KEYS + OTHER_KEYS))


def what_it_is(path: Path) -> str | None:
    """"range" for a readable TOML file that is a range file, else None (a robot config, a scenario, unreadable)."""
    import tomllib
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    return "range" if is_range_file(raw) else None


def load(ref: str) -> Range:
    """A built-in range by its name, or a range file's path (from the current folder). A file that is a robot config
    is refused in words, not read as a broken range."""
    from .spec import RangeError
    if ref not in BUILTIN_RANGES:
        import tomllib
        try:
            raw = tomllib.loads(Path(ref).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            raw = None      # load_range says why
        if isinstance(raw, dict) and not is_range_file(raw) and any(k in raw for k in ROBOT_KEYS):
            raise RangeError(i18n.t("{path} is a robot config, not a range file: a range file has a {range_table} "
                                    "table with {name_setting} and the field's goals, pieces and starts. To check "
                                    "the robot, run {command}", path=Path(ref).name, range_table="[range]",
                                    name_setting="name = \"...\"", command=f"scrimmage model {ref}"))
    return load_range(ref, Path.cwd())


def _names(names) -> str:
    return ", ".join(names)


def summary(ref: str, rng: Range) -> list[str]:
    """`range check`'s lines for a range that reads: "<file>: ok, ..." and a line per warning."""
    parts = [i18n.t("field {length} x {width} m", length=f"{rng.field_length:g}", width=f"{rng.field_width:g}")]
    goals = [g.name for g in rng.goals]
    if goals:
        parts.append(i18n.tn("{count} goal ({names})", "{count} goals ({names})", len(goals), count=len(goals),
                             names=_names(goals)))
    if len(rng.floor_balls):
        parts.append(i18n.tn("{count} floor piece", "{count} floor pieces", len(rng.floor_balls),
                             count=len(rng.floor_balls)))
    if rng.stations:
        n = len(rng.stations)
        parts.append(i18n.tn("{count} station ({names})", "{count} stations ({names})", n, count=n,
                             names=_names(x.name for x in rng.stations)))
    if rng.elements:
        n = len(rng.elements)
        parts.append(i18n.tn("{count} element ({names})", "{count} elements ({names})", n, count=n,
                             names=_names(x.name for x in rng.elements)))
    if rng.obstacles:
        n = len(rng.obstacles)
        parts.append(i18n.tn("{count} obstacle ({names})", "{count} obstacles ({names})", n, count=n,
                             names=_names(x.name for x in rng.obstacles)))
    if rng.game is not None:
        g = rng.game
        scoring = sum(1 for p in g.points.values() if p.auto or p.teleop)
        parts.append(i18n.tn(
            "game {name} ({auto} s auto, {pause} s pause, {teleop} s teleop, {count} goal scores points)",
            "game {name} ({auto} s auto, {pause} s pause, {teleop} s teleop, {count} goals score points)",
            scoring, name=repr(g.name), auto=f"{g.auto_s:g}", pause=f"{g.pause_s:g}", teleop=f"{g.teleop_s:g}",
            count=scoring))
    if rng.apriltags is not None:
        n = len(rng.apriltags.tags)
        parts.append(i18n.tn("{count} AprilTag ({layout})", "{count} AprilTags ({layout})", n, count=n,
                             layout=rng.apriltags.name))
    if rng.starts:
        parts.append(i18n.tn("{count} start ({names})", "{count} starts ({names})", len(rng.starts),
                             count=len(rng.starts), names=_names(rng.starts)))
    kinds = _names(rng.kinds)
    lines = [i18n.t("{file}: ok, range {name}: {parts}; pieces: {kinds}", file=ref, name=repr(rng.name),
                    parts=i18n.t("; ", context="list separator").join(parts), kinds=kinds)]
    from ..checks import shown
    lines += ["  " + i18n.t("warning: {warning}", warning=shown(w)) for w in rng.warnings]
    return lines


def field_model(rng: Range):
    """The range's field alone, compiled: no robot, the floor pieces on the carpet, the stations' pools parked."""
    import mujoco

    from ..parts.field import add_field
    from .build import add_range

    spec = mujoco.MjSpec()
    spec.compiler.degree = False
    spec.option.timestep = 0.001
    add_field(spec, {}, obstacle_default=False, size=(rng.field_length, rng.field_width))
    add_range(spec, rng, 0, floor_balls=True)
    return spec.compile()


class _Field:
    """What render.Renderer reads from a world: the model, its data and a robot body (none here: the world body)."""

    def __init__(self, model):
        import mujoco
        self.model, self.data, self.robot_body = model, mujoco.MjData(model), 0
        mujoco.mj_forward(self.model, self.data)


def picture(rng: Range, out: Path, *, width: int = 1600, height: int = 800) -> Path:
    """A top-down picture of the range's field, written to `out` (a PNG). RenderUnavailable without headless GL."""
    from ..render import Renderer, to_png

    world = _Field(field_model(rng))
    r = Renderer(world)
    try:
        img = r.render("top", width=width, height=height)
    finally:
        r.close()
    out = Path(out)
    out.write_bytes(to_png(img))
    return out


def view(rng: Range) -> None:
    """The range's field in the MuJoCo viewer, from above, until its window is closed."""
    import time

    import mujoco.viewer

    world = _Field(field_model(rng))
    with mujoco.viewer.launch_passive(world.model, world.data) as v:
        v.cam.lookat[:] = [rng.field_length / 2.0, rng.field_width / 2.0, 0.0]
        v.cam.azimuth, v.cam.elevation, v.cam.distance = 90.0, -60.0, 1.2 * rng.field_length
        while v.is_running():
            v.sync()
            time.sleep(0.05)
