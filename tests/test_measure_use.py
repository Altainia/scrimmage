"""Using a measurement file wherever its number is used, with provenance (spec goal 4, C11, C12): a launcher setup's
[ball] and [wheels], a range's ball and a test's Setup ball, a piece's friction, a grip, and the stock cylinder."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from scrimmage.launcher.inputs import PRESETS, LauncherConfigError, Setup, load_setup, parse_setup
from scrimmage.measure.file import MeasureError
from scrimmage.measure.fit_cylinder import ROD_MASS, Stroke, stroke_time
from scrimmage.measure.use import apply_ball, friction_value
from scrimmage.pneumatics.model import DEFAULT_CONDUCTANCE
from test_measure_launcher_fit import _ball_file, _synthetic

INCH = 0.0254
LB = 0.45359237
HEAD = '[measure]\nkind = "{kind}"\nby = "J. Smith"\ndate = 2026-11-10\n'
FUEL = PRESETS["ball"]["2026-fuel"]

SHOOTER = """\
[ball]
preset = "2026-fuel"
{ball}
[wheels]
preset = "35A"
diameter = 0.1016
width = 0.05
{wheels}
[shaft]
wheel_inertia = 0.0006

[control]
target_rps = 60.0
kP = 0.2

[hood]
gap = 0.11
exit_angle_deg = 50.0
friction = 0.5

[launcher]
height = 0.6
width = 0.2

[goal]
distance = 4.0
height = 2.5
diameter = 0.76
"""


def ball_file(folder: Path, squeezes=(118.0, 120.0, 122.0), drops=(9.0, 9.2, 8.8), name="fuel.measure.toml",
              base='base = "2026-fuel"\n') -> str:
    rows = "".join(f"\n[[squeeze]]\nforce_n = {f}\n" for f in squeezes)
    rows += "\n[drop_setup]\ndrop_in = 18\nfloor = \"concrete\"\n" if drops else ""
    rows += "".join(f"\n[[drop]]\nbounce_in = {b}\n" for b in drops)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(HEAD.format(kind="ball") + base + rows, encoding="utf-8")
    return name


def friction_file(folder: Path, pulls=(4.0, 4.1, 3.9), name="tread.measure.toml") -> str:
    rows = "".join(f"\n[[sled]]\npull_lb = {p}\n" for p in pulls)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(HEAD.format(kind="friction") + '\n[sled_setup]\nsliding = "40A tread"\non = "FUEL"\n'
                               "sled_lb = 5\n" + rows, encoding="utf-8")
    return name


def shooter(folder: Path, ball: str = "", wheels: str = "", name="shooter.toml") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(SHOOTER.format(ball=ball, wheels=wheels), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------------------------- a launcher setup

def test_a_setup_with_no_measurement_parses_as_before(tmp_path):
    p = shooter(tmp_path)
    setup = load_setup(p)[0]
    assert type(setup) is Setup and setup.provenance == {}
    import tomllib
    assert setup == parse_setup(tomllib.loads(p.read_text()))
    assert "provenance" not in repr(setup)
    assert [f.name for f in dataclasses.fields(setup)][-1] == "provenance"


def test_a_ball_file_replaces_the_measured_fields_only(tmp_path):
    name = ball_file(tmp_path)
    setup = load_setup(shooter(tmp_path, ball=f'measured = "{name}"\n'))[0]
    assert setup.ball.stiffness_n == pytest.approx(120.0)
    assert setup.ball.restitution == pytest.approx((9.0 / 18) ** 0.5, rel=0.01)
    assert setup.ball.diameter == FUEL["diameter"] and setup.ball.mass == FUEL["mass"]
    assert set(setup.provenance) == {"ball.stiffness_n", "ball.restitution"}
    prov = setup.provenance["ball.stiffness_n"]
    assert (prov.file, prov.by, prov.date, prov.trials, prov.good, prov.why) == (
        tmp_path / name, "J. Smith", "2026-11-10", 3, True, "")


def test_a_file_that_is_not_good_is_used_and_marked_not_good(tmp_path):
    name = ball_file(tmp_path, squeezes=(22.0, 67.0), drops=())
    setup = load_setup(shooter(tmp_path, ball=f'measured = "{name}"\n'))[0]
    assert setup.ball.stiffness_n == pytest.approx(44.5)
    prov = setup.provenance["ball.stiffness_n"]
    assert not prov.good and "2 trials: take at least 3" in prov.why


def test_the_ball_file_base_must_be_the_preset(tmp_path):
    name = ball_file(tmp_path, base='base = "2022-cargo"\n')
    with pytest.raises(LauncherConfigError, match=r'\[ball\]: measured = "fuel.measure.toml" measured a 2022-cargo '
                                                  r"ball, but the preset is 2026-fuel"):
        load_setup(shooter(tmp_path, ball=f'measured = "{name}"\n'))


def test_with_no_preset_the_file_base_is_the_preset(tmp_path):
    name = ball_file(tmp_path)
    p = shooter(tmp_path, ball=f'measured = "{name}"\n')
    p.write_text(p.read_text().replace('preset = "2026-fuel"\n', "", 1))
    setup = load_setup(p)[0]
    assert setup.ball.diameter == FUEL["diameter"] and setup.ball.stiffness_n == pytest.approx(120.0)


def test_a_number_given_and_measured_is_an_error(tmp_path):
    name = ball_file(tmp_path)
    with pytest.raises(LauncherConfigError, match=r"stiffness_n is given and also measured by measured = "
                                                  r'"fuel.measure.toml": give one'):
        load_setup(shooter(tmp_path, ball=f'stiffness_n = 90.0\nmeasured = "{name}"\n'))


def test_a_ball_setting_pointed_at_a_friction_file(tmp_path):
    """Review Focus 4: the error names the setting and the file's kind."""
    name = friction_file(tmp_path)
    p = shooter(tmp_path, ball=f'measured = "{name}"\n')
    with pytest.raises(LauncherConfigError) as e:
        load_setup(p)
    assert str(e.value) == (f'{p}: [ball]: measured = "tread.measure.toml" is a friction measurement; a ball needs '
                            "a ball or launcher measurement")


def test_a_measurement_file_moved_away(tmp_path):
    """Review Focus 5: the error names the setup, the key and the path it looked for."""
    name = ball_file(tmp_path / "measurements")
    p = shooter(tmp_path / "setups", ball='measured = "../measurements/fuel.measure.toml"\n')
    assert load_setup(p)[0].ball.stiffness_n == pytest.approx(120.0)  # relative to the setup file (C12)
    (tmp_path / "measurements" / name).rename(tmp_path / name)
    with pytest.raises(LauncherConfigError) as e:
        load_setup(p)
    looked = (tmp_path / "setups" / "../measurements/fuel.measure.toml").resolve()
    assert str(e.value) == (f'{p}: [ball]: measured = "../measurements/fuel.measure.toml": no such file '
                            f"(looked in {looked})")


def test_a_wheels_friction_file(tmp_path):
    name = friction_file(tmp_path)
    setup = load_setup(shooter(tmp_path, wheels=f'measured = "{name}"\n'))[0]
    assert setup.wheels.friction == pytest.approx(0.8)
    assert setup.wheels.durometer == PRESETS["wheels"]["35A"]["durometer"]
    assert list(setup.provenance) == ["wheels.friction"]
    with pytest.raises(LauncherConfigError, match="is a ball measurement; a wheel needs a friction or launcher"):
        load_setup(shooter(tmp_path, wheels=f'measured = "{ball_file(tmp_path)}"\n'))


def test_measured_must_name_a_file(tmp_path):
    with pytest.raises(LauncherConfigError, match=r"\[wheels\] measured must be a measurement file's path"):
        load_setup(shooter(tmp_path, wheels="measured = 0.9\n"))


def test_a_sweep_keeps_the_measurements_and_drops_a_swept_ones(tmp_path):
    from scrimmage.launcher.sweep import setups_for
    name = ball_file(tmp_path)
    p = shooter(tmp_path, ball=f'measured = "{name}"\n')
    p.write_text(p.read_text() + '\n[sweep]\n"ball.stiffness_n" = [100.0]\n"hood.gap" = [0.10, 0.11]\n')
    rows = setups_for(p)
    assert [s.ball.stiffness_n for _, s in rows] == [100.0, 100.0]
    assert all(set(s.provenance) == {"ball.restitution"} for _, s in rows)
    p.write_text(p.read_text().replace('"ball.stiffness_n" = [100.0]\n', ""))
    rows = setups_for(p)
    assert all(s.ball.stiffness_n == pytest.approx(120.0) and "ball.stiffness_n" in s.provenance for _, s in rows)


def test_a_launcher_file_named_by_the_ball_and_the_wheels(tmp_path):
    """The shots' setup names this very launcher file (a loop the fit passes over). Named by both sections, the
    ball takes the fitted stiffness and the wheels keep their own friction: shots alone fit one number."""
    mf = _synthetic(tmp_path, 120.0, 0.95, [50.0, 50.0, 50.0, 80.0, 80.0, 80.0])
    p = tmp_path / "shooter.toml"
    p.write_text(p.read_text().replace('preset = "2026-fuel"\n', f'preset = "2026-fuel"\nmeasured = "{mf.path.name}"\n')
                 .replace('preset = "35A"\n', f'preset = "35A"\nmeasured = "{mf.path.name}"\n'))
    setup = load_setup(p)[0]
    assert setup.ball.stiffness_n == pytest.approx(120.0, rel=0.03)
    assert setup.wheels.friction == PRESETS["wheels"]["35A"]["friction"]
    assert list(setup.provenance) == ["ball.stiffness_n"]
    assert setup.provenance["ball.stiffness_n"].trials == 6
    p.write_text(p.read_text().replace(f'preset = "2026-fuel"\nmeasured = "{mf.path.name}"\n',
                                       'preset = "2026-fuel"\n'))
    with pytest.raises(LauncherConfigError, match="fits the ball's stiffness, not the wheel's friction"):
        load_setup(p)


def test_a_launcher_file_with_the_squeeze_measured_gives_the_wheels_friction(tmp_path):
    _ball_file(tmp_path, [118.0, 120.0, 122.0])
    from test_measure_launcher_fit import WITH_BALL
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 50.0, 50.0, 80.0, 80.0, 80.0], header=WITH_BALL)
    fields, prov = apply_ball({"preset": "2026-fuel"}, mf.path.name, "[ball]", base=tmp_path, shared=True)
    assert fields == {"preset": "2026-fuel"} and prov == {}
    with pytest.raises(MeasureError, match="fits the wheel's friction, not the ball's stiffness"):
        apply_ball({"preset": "2026-fuel"}, mf.path.name, "[ball]", base=tmp_path)
    p = tmp_path / "shooter.toml"
    p.write_text(p.read_text().replace('preset = "35A"\n', f'preset = "35A"\nmeasured = "{mf.path.name}"\n'))
    setup = load_setup(p)[0]
    assert setup.wheels.friction == pytest.approx(1.1, rel=0.03) and list(setup.provenance) == ["wheels.friction"]


# --------------------------------------------------------------------------------------- a range's ball and pieces

RANGE = '[range]\nname = "mine"\n{range}\n[[floor_balls]]\nat = [[3.5, 4.0]]\n{pieces}'


def range_file(folder: Path, range_: str = "", pieces: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / "mine.toml"
    p.write_text(RANGE.format(range=range_, pieces=pieces), encoding="utf-8")
    return p


def test_a_range_ball_file(tmp_path):
    from scrimmage.testrange.spec import load_range
    name = ball_file(tmp_path / "balls")
    p = range_file(tmp_path, f'ball = "balls/{name}"\n')
    rng = load_range("mine.toml", tmp_path)
    assert rng.ball_preset == "2026-fuel" and rng.default_kind == "2026-fuel"
    assert rng.ball.stiffness_n == pytest.approx(120.0) and rng.ball.diameter == FUEL["diameter"]
    assert set(rng.kinds["2026-fuel"].provenance) == {"stiffness_n", "restitution"}
    # a config's (or Setup's) ball replaces the range's own, found from the folder that names it
    plain = range_file(tmp_path / "other", 'ball = "2026-fuel"\n')
    rng = load_range(plain.as_posix(), tmp_path / "balls", ball=name)
    assert rng.ball.stiffness_n == pytest.approx(120.0)
    assert rng.kinds["2026-fuel"].provenance["stiffness_n"].file == tmp_path / "balls" / name
    assert load_range(p.as_posix(), tmp_path).kinds["2026-fuel"].provenance  # a path given whole


def test_a_range_ball_pointed_at_a_friction_file_or_moved(tmp_path):
    from scrimmage.testrange.spec import RangeError, load_range
    name = friction_file(tmp_path)
    p = range_file(tmp_path, f'ball = "{name}"\n')
    with pytest.raises(RangeError) as e:
        load_range("mine.toml", tmp_path)
    assert str(e.value) == (f'{p}: [range]: ball = "tread.measure.toml" is a friction measurement; a ball needs a '
                            "ball measurement")
    range_file(tmp_path, 'ball = "gone.measure.toml"\n')
    with pytest.raises(RangeError) as e:
        load_range("mine.toml", tmp_path)
    assert str(e.value) == (f'{p}: [range]: ball = "gone.measure.toml": no such file (looked in '
                            f"{(tmp_path / 'gone.measure.toml').resolve()})")


def test_a_range_ball_file_needs_a_base(tmp_path):
    from scrimmage.testrange.spec import RangeError, load_range
    name = ball_file(tmp_path, base="")
    range_file(tmp_path, f'ball = "{name}"\n')
    with pytest.raises(RangeError, match=r"needs its \[measure\] base, the ball preset it measured"):
        load_range("mine.toml", tmp_path)


def test_a_piece_friction_file(tmp_path):
    from scrimmage.testrange.spec import RangeError, load_range
    name = friction_file(tmp_path / "m")
    box = '\n[piece.crate]\nshape = "box"\nsize = [0.2, 0.2, 0.2]\nmass = 1.0\nfriction = "m/{}"\n'
    range_file(tmp_path, pieces=box.format(name))
    kind = load_range("mine.toml", tmp_path).kinds["crate"]
    assert kind.friction == pytest.approx(0.8)
    assert kind.provenance["friction"].file == tmp_path / "m" / name
    assert kind.provenance["friction"].good
    ball_file(tmp_path / "m")
    p = range_file(tmp_path, pieces=box.format("fuel.measure.toml"))
    with pytest.raises(RangeError, match=r'\[piece.crate\]: friction = "m/fuel.measure.toml" is a ball measurement; '
                                         "a piece's surface needs a friction measurement"):
        load_range("mine.toml", tmp_path)
    assert p.is_file()


def test_a_piece_ball_file(tmp_path):
    from scrimmage.testrange.spec import RangeError, load_range
    name = ball_file(tmp_path)
    range_file(tmp_path, 'ball = "2026-fuel"\n', pieces=f'\n[piece.mine]\nball = "{name}"\n')
    kind = load_range("mine.toml", tmp_path).kinds["mine"]
    assert kind.ball.stiffness_n == pytest.approx(120.0) and kind.preset == "2026-fuel"
    assert "stiffness_n" in kind.provenance
    p = range_file(tmp_path, 'ball = "2026-fuel"\n', pieces=f'\n[piece.mine]\nball = "{friction_file(tmp_path)}"\n')
    with pytest.raises(RangeError) as e:
        load_range("mine.toml", tmp_path)
    assert str(e.value) == (f'{p}: [piece.mine]: ball = "tread.measure.toml" is a friction measurement; a ball '
                            "needs a ball measurement")


def test_no_measurement_leaves_kinds_without_provenance():
    from scrimmage.testrange.spec import load_range
    rng = load_range("intake", Path("."))
    assert all(k.provenance == {} for k in rng.kinds.values())


def test_a_test_setup_ball_file_is_found_from_the_test_file(tmp_path):
    from scrimmage.assembler.templates import new_config
    from scrimmage.blocks.program import TestFileError, loads
    from scrimmage.blocks.run import world_for
    (tmp_path / "robots").mkdir()
    (tmp_path / "robots" / "robot.toml").write_text(new_config("standin") + '\n[range]\nfile = "intake"\n',
                                                    encoding="utf-8")
    name = ball_file(tmp_path / "tests")
    path = tmp_path / "tests" / "t.test.toml"
    path.write_text(f'[test]\nrobot = "../robots/robot.toml"\n\n[setup]\nball = "{name}"\n', encoding="utf-8")
    w = world_for(loads(path.read_text(encoding="utf-8"), path))
    assert w.test_range.range.ball.stiffness_n == pytest.approx(120.0)
    path.write_text('[test]\nrobot = "../robots/robot.toml"\n\n[setup]\nball = "gone.measure.toml"\n',
                    encoding="utf-8")
    with pytest.raises(TestFileError) as e:
        world_for(loads(path.read_text(encoding="utf-8"), path))
    assert str(e.value) == (f'{path}: [setup]: ball = "gone.measure.toml": no such file (looked in '
                            f"{(tmp_path / 'tests' / 'gone.measure.toml').resolve()})")


# ------------------------------------------------------------------------------------------------------- a grip

PART = """
[part]
name = "claw"
format = 1

[[body]]
name = "base"
mass = 1.0
geom = [{ box = [0.1, 0.1, 0.1] }]

[[body]]
name = "pad"
parent = "base"
mass = 0.2
geom = [{ box = [0.02, 0.1, 0.05] }]
"""


def claw(folder: Path, overlay: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "part.toml").write_text(PART, encoding="utf-8")
    if overlay:
        (folder / "scrimmage-edits.toml").write_text(overlay, encoding="utf-8")
    return folder


def test_an_overlay_grip_file_is_found_from_the_part_folder(tmp_path):
    from scrimmage.parts.grip import grip_notes
    from scrimmage.parts.model import load_part
    folder = claw(tmp_path / "claw", '[body.pad]\ngrip = "pad.measure.toml"\n')
    friction_file(folder, name="pad.measure.toml")
    part = load_part(folder)
    (g,) = [g for b in part.bodies if b.name == "pad" for g in b.geoms]
    assert g.friction == pytest.approx(0.8)
    assert part.grips == {"pad": "pad.measure.toml"}
    assert part.grip_provenance["pad"].file == folder / "pad.measure.toml"

    class World:
        parts = type("P", (), {"instances": [type("I", (), {"name": "claw", "part": part})()]})()
    (note,) = grip_notes(World())
    assert note.startswith("claw.pad grip: measured, friction 0.8 (pad.measure.toml, J. Smith, 2026-11-10, 3 trials)")


def test_a_config_grip_file_is_found_from_the_config_and_replaces_the_overlay(tmp_path):
    from scrimmage.parts.assemble import _instances
    from scrimmage.parts.model import PartError
    claw(tmp_path / "parts" / "claw", '[body.pad]\ngrip = "pad.measure.toml"\n')
    friction_file(tmp_path / "parts" / "claw", pulls=(2.0, 2.0, 2.0), name="pad.measure.toml")
    friction_file(tmp_path, name="mine.measure.toml")
    cfg = {"part": [{"name": "claw", "path": "parts/claw", "grip": {"pad": "mine.measure.toml"}}]}
    (inst,) = _instances(cfg, tmp_path)
    assert inst.part.grip_provenance["pad"].file == tmp_path / "mine.measure.toml"
    cfg["part"][0]["grip"] = {"pad": "nitrile"}
    (inst,) = _instances(cfg, tmp_path)
    assert inst.part.grip_provenance == {}
    ball_file(tmp_path)
    cfg["part"][0]["grip"] = {"pad": "fuel.measure.toml"}
    with pytest.raises(PartError) as e:
        _instances(cfg, tmp_path)
    assert str(e.value) == ("parts[0] 'claw': body pad: grip = \"fuel.measure.toml\" is a ball measurement; a grip "
                            "needs a friction measurement")
    cfg["part"][0]["grip"] = {"pad": "gone.measure.toml"}
    with pytest.raises(PartError, match=r"parts\[0\] 'claw': body pad: grip = \"gone.measure.toml\": no such file"):
        _instances(cfg, tmp_path)


def test_a_grip_with_no_file_has_no_provenance(tmp_path):
    from scrimmage.parts.model import load_part
    part = load_part(claw(tmp_path / "claw", '[body.pad]\ngrip = "nitrile"\n'))
    assert part.grip_provenance == {}


# ----------------------------------------------------------------------------------------------- a stock cylinder

def cylinder_file(folder: Path, times, name="deploy.measure.toml") -> str:
    rows = "".join(f"\n[[stroke]]\ntime_s = {t!r}\n" for t in times)
    (folder / name).write_text(HEAD.format(kind="cylinder") + "\n[stroke_setup]\nbore_in = 1.0625\nrod_in = 0.3125\n"
                               "stroke_in = 6\nload_mass_lb = 2\n" + rows, encoding="utf-8")
    return name


def robot(**params):
    return {"part": [{"name": "drive", "stock": "chassis"},
                     {"name": "cyl", "stock": "cylinder", "attach": "drive.top", **params}]}


def test_a_stock_cylinder_takes_its_conductance_from_a_file(tmp_path):
    from scrimmage.parts.assemble import assemble
    s = Stroke(bore=1.0625 * INCH, rod=0.3125 * INCH, stroke=6 * INCH, mass=ROD_MASS + 2 * LB, vertical=False,
               working_psi=60.0, friction_n=8.0)
    name = cylinder_file(tmp_path, [stroke_time(s, 2 * DEFAULT_CONDUCTANCE)] * 3)
    (spec,) = assemble(robot(measured=name), tmp_path).cylinders
    assert spec.conductance == pytest.approx(2 * DEFAULT_CONDUCTANCE, rel=0.03)
    assert spec.provenance.file == tmp_path / name and spec.provenance.trials == 3
    # timed by stopwatch, the fit isn't good: still used, its provenance saying why
    assert not spec.provenance.good and spec.provenance.why == "the strokes aren't timed closely enough"
    (plain,) = assemble(robot(), tmp_path).cylinders
    assert plain.conductance == DEFAULT_CONDUCTANCE and plain.provenance is None


def test_a_stock_cylinder_measured_errors(tmp_path):
    from scrimmage.parts.assemble import assemble
    from scrimmage.parts.model import PartError
    name = cylinder_file(tmp_path, [0.2] * 3)
    with pytest.raises(PartError, match="conductance is given and also measured by measured = "):
        assemble(robot(measured=name, conductance=1e-8), tmp_path)
    with pytest.raises(PartError, match=r"is a ball measurement; a cylinder needs a cylinder measurement"):
        assemble(robot(measured=ball_file(tmp_path)), tmp_path)
    with pytest.raises(PartError) as e:
        assemble(robot(measured="gone.measure.toml"), tmp_path)
    assert str(e.value) == (f"parts[1] 'cyl': measured = \"gone.measure.toml\": no such file (looked in "
                            f"{(tmp_path / 'gone.measure.toml').resolve()})")


def test_friction_value_reads_a_sled_file(tmp_path):
    value, prov = friction_value(friction_file(tmp_path), "here", base=tmp_path)
    assert value == pytest.approx(0.8) and prov.trials == 3


# ------------------------------------------------------------------------------------------------ review round 1

def test_a_test_setup_ball_pointed_at_a_friction_file(tmp_path):
    from scrimmage.assembler.templates import new_config
    from scrimmage.blocks.program import TestFileError, loads
    from scrimmage.blocks.run import world_for
    (tmp_path / "robot.toml").write_text(new_config("standin") + '\n[range]\nfile = "intake"\n', encoding="utf-8")
    name = friction_file(tmp_path / "m")
    path = tmp_path / "t.test.toml"
    path.write_text(f'[test]\nrobot = "robot.toml"\n\n[setup]\nball = "m/{name}"\n', encoding="utf-8")
    with pytest.raises(TestFileError) as e:
        world_for(loads(path.read_text(encoding="utf-8"), path))
    assert str(e.value) == (f'{path}: [setup]: ball = "m/tread.measure.toml" is a friction measurement; a ball needs '
                            "a ball measurement")


def test_a_config_range_ball_pointed_at_a_friction_file(tmp_path):
    from scrimmage.assembler.templates import new_config
    from scrimmage.config import ConfigError, build_world, load_config
    name = friction_file(tmp_path / "m")
    robot = tmp_path / "robot.toml"
    robot.write_text(new_config("standin") + f'\n[range]\nfile = "intake"\nball = "m/{name}"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        build_world(load_config(robot))
    assert str(e.value) == (f'{robot}: [range]: ball = "m/tread.measure.toml" is a friction measurement; a ball '
                            "needs a ball measurement")
    ball_file(tmp_path / "m")
    robot.write_text(new_config("standin") + '\n[range]\nfile = "intake"\nball = "m/fuel.measure.toml"\n',
                     encoding="utf-8")
    w = build_world(load_config(robot))
    assert w.test_range.range.ball.stiffness_n == pytest.approx(120.0)


def test_the_setup_ball_message_names_measurement_files(tmp_path):
    from scrimmage.blocks.program import TestFileError, loads
    path = tmp_path / "t.test.toml"
    with pytest.raises(TestFileError, match=r'or a ball measurement file \("fuel.measure.toml"\)'):
        loads('[test]\nrobot = "robot.toml"\n\n[setup]\nball = "beachball"\n', path)


def test_a_shared_launcher_file_is_fit_once_even_in_a_sweep(tmp_path, monkeypatch):
    from scrimmage.launcher.sweep import setups_for
    from scrimmage.measure import fit_launcher
    calls = []
    real = fit_launcher.fit
    monkeypatch.setattr(fit_launcher, "fit", lambda mf: calls.append(mf.path) or real(mf))
    mf = _synthetic(tmp_path, 120.0, 0.95, [50.0, 50.0, 50.0, 80.0, 80.0, 80.0])
    p = tmp_path / "shooter.toml"
    p.write_text(p.read_text().replace('preset = "2026-fuel"\n', f'preset = "2026-fuel"\nmeasured = "{mf.path.name}"\n')
                 .replace('preset = "35A"\n', f'preset = "35A"\nmeasured = "{mf.path.name}"\n')
                 + '\n[sweep]\n"hood.gap" = [0.10, 0.11, 0.12]\n')
    load_setup(p)
    assert len(calls) == 1
    rows = setups_for(p)
    assert len(calls) == 2  # setups_for's own load; its three rows reuse it
    assert all(s.ball.stiffness_n == pytest.approx(120.0, rel=0.03) and list(s.provenance) == ["ball.stiffness_n"]
               for _, s in rows)


def test_sweeping_the_ball_preset_of_a_measured_ball(tmp_path):
    from scrimmage.launcher.sweep import setups_for
    name = ball_file(tmp_path)
    p = shooter(tmp_path, ball=f'measured = "{name}"\n')
    p.write_text(p.read_text() + '\n[sweep]\n"ball.preset" = ["2026-fuel"]\n')
    assert setups_for(p)[0][1].ball.stiffness_n == pytest.approx(120.0)
    p.write_text(p.read_text().replace('["2026-fuel"]', '["2026-fuel", "2022-cargo"]'))
    with pytest.raises(LauncherConfigError, match=r'ball.preset=2022-cargo: \[ball\] preset 2022-cargo isn\'t the '
                                                  r'2026-fuel ball that measured = "fuel.measure.toml" measured'):
        setups_for(p)


def test_load_range_finds_a_ball_file_from_a_relative_folder(tmp_path, monkeypatch):
    from scrimmage.testrange.spec import load_range
    name = ball_file(tmp_path / "balls")
    plain = range_file(tmp_path / "other", 'ball = "2026-fuel"\n')
    monkeypatch.chdir(tmp_path)
    rng = load_range(plain.as_posix(), Path("balls"), ball=name)
    assert rng.kinds["2026-fuel"].provenance["stiffness_n"].file == tmp_path / "balls" / name


def test_the_in_world_shooter_takes_a_shared_launcher_file(tmp_path):
    from range_fixtures import shooter_cfg
    from scrimmage.world import World
    from test_measure_launcher_fit import WITH_BALL
    _ball_file(tmp_path, [118.0, 120.0, 122.0])
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 50.0, 50.0, 80.0, 80.0, 80.0], header=WITH_BALL)
    (tmp_path / "mine.toml").write_text(f'[ball]\npreset = "2026-fuel"\nmeasured = "{mf.path.name}"\n'
                                        f'[wheels]\npreset = "35A"\nmeasured = "{mf.path.name}"\n', encoding="utf-8")
    wheels = {"durometer": 35.0, "positions": [-0.08, 0.08], "width": 0.05}
    w = World(shooter_cfg(shooter={"setup": "mine.toml", "wheels": wheels}), tmp_path)
    s = w.test_range.shooter.config.setup
    assert s.wheels.friction == pytest.approx(1.1, rel=0.03) and list(s.provenance) == ["wheels.friction"]
    (tmp_path / "mine.toml").write_text('[ball]\npreset = "2026-fuel"\nmeasured = "gone.measure.toml"\n',
                                        encoding="utf-8")
    with pytest.raises(ValueError) as e:
        World(shooter_cfg(shooter={"setup": "mine.toml", "wheels": wheels}), tmp_path)
    assert f"{tmp_path / 'mine.toml'}: [ball]: measured = \"gone.measure.toml\": no such file" in str(e.value)
    assert str(e.value).count(str(tmp_path / "mine.toml")) == 1
