"""The robot frame, the import file, and reconciling an import with a changed CAD file."""

from pathlib import Path

import numpy as np
import pytest

from scrimmage import teamfolders
from scrimmage.cad import CadError
from scrimmage.cad.frame import RobotFrame, default_frame, to_robot
from scrimmage.cad.settings import (BodySettings, JointSettings, MotorSettings, MountSettings, dumps_import,
                                    new_import, read_import, reimport, remove_empty_bodies, write_import)
from scrimmage.cad.source import Source, load_source

DATA = Path(__file__).parent / "data" / "cad"


def test_default_frame_z_up():
    f = default_frame(load_source(DATA / "climber.step"))
    assert (f.up, f.front, f.skip, f.confirmed) == ("+z", "+x", False, False)
    assert f.floor == pytest.approx(0.0)
    assert f.center == pytest.approx((0.15, 0.10))


def test_y_up_frame_puts_floor_on_y():
    src = load_source(DATA / "climber_yup.step")
    f = RobotFrame(up="+y", front="+x", floor=default_frame(src).floor)
    # the default (z up) guess has the floor on z; for y up, take the lowest y
    lo = min(p.v[:, 1].min() for p in src.pieces)
    f.floor = float(lo)
    R, t = to_robot(f)
    plate = next(p for p in src.pieces if p.key.endswith("base_plate"))
    z = (plate.v @ R.T + t)[:, 2]
    assert z.min() == pytest.approx(0.0, abs=1e-9) and z.max() == pytest.approx(0.01, abs=1e-6)
    assert np.linalg.det(R) == pytest.approx(1.0)


def test_skip_is_identity_and_bad_axes_rejected():
    R, t = to_robot(RobotFrame(skip=True, floor=3.0, center=(1, 2)))
    assert np.allclose(R, np.eye(3)) and np.allclose(t, 0)
    with pytest.raises(CadError):
        RobotFrame(up="+z", front="-z")
    with pytest.raises(CadError):
        RobotFrame(up="z")


def make(tmp_path, name="climber", step="climber.step"):
    src = load_source(DATA / step)
    return src, new_import(src, name, DATA / step, tmp_path)


def test_round_trip_import_file(tmp_path):
    src, imp = make(tmp_path)
    imp.frame.confirmed = True
    imp.bodies["arm_A"].parent = "base_plate"
    imp.bodies["arm_A"].joint = JointSettings("hinge", [0.1, 0.2, 0.3], [0, 1, 0], range=(-1.0, 1.0),
                                              spring=(2.0, 0.5), damping=0.1)
    imp.bodies["arm_A"].motor = MotorSettings("neo", count=2, ratio=5.0)
    imp.bodies["arm_A"].mounts = [MountSettings("tip", [0, 0, 1], [0, 0, 1])]
    imp.bodies["arm_A"].gyro = True
    imp.pieces["Climber/bolt#2"].left_out = True
    imp.couplings = [{"a": "arm_A", "b": "arm_B", "ratio": 1.0}]
    path = tmp_path / "climber.import.toml"
    write_import(imp, path)
    assert read_import(path) == imp
    assert "[import]" in path.read_text()


def test_awkward_piece_keys_round_trip(tmp_path):
    src, imp = make(tmp_path)
    ps = imp.pieces.pop("Climber/bolt")
    for key in ('A/b#2 "x"', "über part/ü", "a.b\\c"):
        imp.pieces[key] = ps
    path = tmp_path / "x.import.toml"
    write_import(imp, path)
    assert read_import(path).pieces.keys() == imp.pieces.keys()


def test_unknown_key_is_an_error(tmp_path):
    src, imp = make(tmp_path)
    path = tmp_path / "a.import.toml"
    write_import(imp, path)
    text = path.read_text().replace("[bodies.arm_A]\n", '[bodies.arm_A]\ncolour = "red"\n')
    path.write_text(text)
    with pytest.raises(CadError, match=r"bodies\.arm_A\.colour"):
        read_import(path)


def test_invalid_values_are_errors(tmp_path):
    src, imp = make(tmp_path)
    d = imp.to_dict()
    d["bodies"]["arm_A"]["collision"] = "bouncy"
    with pytest.raises(CadError, match=r"bodies\.arm_A\.collision"):
        type(imp).from_dict(d)


def test_source_path_portable(tmp_path, monkeypatch):
    cad = tmp_path / "team"
    (cad / "sub").mkdir(parents=True)
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    monkeypatch.setenv("SCRIMMAGE_CAD", str(cad))
    src = load_source(DATA / "climber.step")
    assert new_import(src, "c", cad / "sub" / "climber.step", tmp_path).source == "{cad}/sub/climber.step"
    assert new_import(src, "c", Path("sub\\climber.step"), tmp_path).source == "sub/climber.step"
    assert Path(teamfolders.expand("{cad}/sub/climber.step")) == cad / "sub" / "climber.step"


def test_new_import_defaults(tmp_path):
    src, imp = make(tmp_path)
    first = next(iter(imp.bodies))
    assert imp.bodies[first].parent is None and imp.bodies[first].part == "climber"
    assert all(b.parent == first and b.joint is None for n, b in imp.bodies.items() if n != first)
    assert set(imp.pieces) == {p.key for p in src.pieces}
    assert imp.import_id and imp.parts["climber"].total_mass is None


def test_reimport_v2_messages(tmp_path):
    src, imp = make(tmp_path)
    new, msgs = reimport(imp, load_source(DATA / "climber_v2.step"))
    assert sum("bolt#6" in m and "no longer in the CAD" in m for m in msgs) == 1
    # the second hook sub-assembly was renamed and moved 100 mm, so it is matched by shape: check it
    assert any("hook_assy#2/hook" in m and "check it" in m for m in msgs)
    assert "Climber/hook_assy_right/hook" in new.pieces
    assert new.pieces["Climber/hook_assy_right/hook"].body == imp.pieces["Climber/hook_assy#2/hook"].body
    assert "Climber/bolt#6" not in new.pieces
    assert len(msgs) == 2
    assert imp.pieces["Climber/bolt#6"]  # the input is not changed


def test_reimport_new_piece_goes_to_default_body(tmp_path):
    src, imp = make(tmp_path)
    smaller = Source(src.kind, src.path, src.units, [p for p in src.pieces if p.key != "Climber/bolt#6"], src.groups)
    imp2, _ = reimport(imp, smaller)
    again, msgs = reimport(imp2, src)
    assert again.pieces["Climber/bolt#6"].body == "base_plate" and msgs == []


def test_reimport_drops_joint_of_emptied_body(tmp_path):
    src, imp = make(tmp_path)
    imp.bodies["hook_assy_2"].joint = JointSettings("hinge", [0, 0, 0], [0, 1, 0])
    imp.bodies["hook_assy_2"].motor = MotorSettings("neo")
    imp.bodies["hook_assy_2"].mounts = [MountSettings("m", [0, 0, 0], [0, 0, 1])]
    imp.bodies["arm_A"].joint = JointSettings("slide", [0, 0, 0], [0, 0, 1])
    imp.bodies["hook_assy"].parent = "hook_assy_2"
    smaller = Source(src.kind, src.path, src.units,
                     [p for p in src.pieces if p.key != "Climber/hook_assy#2/hook"], src.groups)
    new, msgs = reimport(imp, smaller)
    assert "hook_assy_2" not in new.bodies
    assert new.bodies["hook_assy"].parent == imp.bodies["hook_assy_2"].parent == "base_plate"
    assert any("hook_assy_2 is no longer in the CAD" in m and "joint" in m and "motor" in m for m in msgs)
    for n in ("base_plate", "arm_A", "arm_B"):
        assert new.bodies[n] == imp.bodies[n]


def test_remove_empty_bodies_root(tmp_path):
    src, imp = make(tmp_path)
    for k in [k for k, p in imp.pieces.items() if p.body == "base_plate"]:
        del imp.pieces[k]
    new, msgs = remove_empty_bodies(imp)
    root = next(n for n, b in new.bodies.items() if b.parent is None)
    assert new.bodies[root].part == "climber" and len(msgs) == 1
    assert all(b.parent in (None, root) for b in new.bodies.values())


def test_dumps_has_no_none(tmp_path):
    src, imp = make(tmp_path)
    assert "None" not in dumps_import(imp)


def test_written_folders_round_trip_and_are_checked(tmp_path):
    from scrimmage.cad.settings import ImportFile
    src, imp = make(tmp_path)
    imp.written = {"climber": "climber", "right arm": "{cad}/arms/right arm"}
    path = tmp_path / "climber.import.toml"
    write_import(imp, path)
    assert read_import(path).written == imp.written
    d = imp.to_dict()
    d["written"] = {"climber": 3}
    with pytest.raises(CadError, match="written.climber must be text"):
        ImportFile.from_dict(d)
    d["written"] = ["climber"]
    with pytest.raises(CadError, match="written must be a table"):
        ImportFile.from_dict(d)


def test_part_attach_round_trips_and_is_checked(tmp_path):
    from scrimmage.cad.settings import ImportFile
    _, imp = make(tmp_path)
    imp.parts["climber"].attach = "arm_B"
    path = tmp_path / "climber.import.toml"
    write_import(imp, path)
    assert read_import(path).parts["climber"].attach == "arm_B"
    for bad in (3, ["arm_B"], ""):
        d = imp.to_dict()
        d["parts"]["climber"]["attach"] = bad
        with pytest.raises(CadError, match=r"parts\.climber: attach must name a body"):
            ImportFile.from_dict(d)


def test_replace_file_waits_out_a_brief_hold_and_gives_up_on_a_long_one(tmp_path, monkeypatch):
    """A file a virus scanner holds for a moment (PermissionError from the rename) is replaced once it lets go; one
    still held after about a second raises, and nothing else is retried."""
    from scrimmage.cad import settings
    src, dst = tmp_path / "new.tmp", tmp_path / "x.import.toml"
    dst.write_text("old", encoding="utf-8")
    real, tries, slept = settings.os.replace, [], []
    monkeypatch.setattr(settings.time, "sleep", slept.append)

    def held(n, err=PermissionError(13, "Access is denied")):
        def replace(a, b):
            tries.append(1)
            if len(tries) <= n:
                raise err
            real(a, b)
        return replace

    src.write_text("new", encoding="utf-8")
    monkeypatch.setattr(settings.os, "replace", held(2))
    settings.replace_file(src, dst)
    assert dst.read_text(encoding="utf-8") == "new" and len(tries) == 3 and len(slept) == 2

    tries.clear()
    slept.clear()
    src.write_text("newer", encoding="utf-8")
    monkeypatch.setattr(settings.os, "replace", held(99))
    with pytest.raises(PermissionError):
        settings.replace_file(src, dst)
    assert dst.read_text(encoding="utf-8") == "new" and len(tries) == len(settings.REPLACE_WAITS) + 1
    assert sum(slept) <= 1.0

    tries.clear()
    slept.clear()
    monkeypatch.setattr(settings.os, "replace", held(99, FileNotFoundError(2, "gone")))
    with pytest.raises(FileNotFoundError):
        settings.replace_file(src, dst)
    assert len(tries) == 1 and not slept


def test_write_import_retries_a_held_file(tmp_path, monkeypatch):
    from scrimmage.cad import settings
    src = load_source(DATA / "climber.step")
    path = tmp_path / "climber.import.toml"
    imp = new_import(src, "climber", Path("../climber.step"), tmp_path)
    real, tries = settings.os.replace, []
    monkeypatch.setattr(settings.time, "sleep", lambda s: None)

    def replace(a, b):
        tries.append(1)
        if len(tries) == 1:
            raise PermissionError(13, "Access is denied")
        real(a, b)
    monkeypatch.setattr(settings.os, "replace", replace)
    write_import(imp, path)
    assert read_import(path).name == "climber" and len(tries) == 2
    assert [p.name for p in tmp_path.iterdir()] == ["climber.import.toml"]
