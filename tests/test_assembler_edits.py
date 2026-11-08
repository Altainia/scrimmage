import os
import shutil
import time
from pathlib import Path

import pytest
import tomlkit

from scrimmage.assembler import edits
from scrimmage.assembler.doc import ConfigDoc
from scrimmage.assembler.edits import EditError

ROOT = Path(__file__).resolve().parent.parent


def doc_for(tmp_path, name):
    """Copy a shipped config into tmp (config text unchanged)."""
    dest = tmp_path / f"{name}.toml"
    shutil.copy(ROOT / "configs" / f"{name}.toml", dest)
    return ConfigDoc(dest)


def lines_preserved(old: str, new: str):
    """Every original line is present unchanged, in order, and the original is a prefix of the new text
    up to trailing newlines."""
    assert new.startswith(old.rstrip("\n")), "original text is not a prefix of the new text"
    it = iter(new.splitlines())
    for line in old.splitlines():
        assert any(line == n for n in it), f"line lost or reordered: {line!r}"


def test_add_stock_part_key_order_and_prefix(tmp_path):
    cd = doc_for(tmp_path, "kitbot")
    new = cd.apply(lambda d: edits.add_part(
        d, name="arm", stock="arm", attach="drive.top", pos=[0, 0, 0.1], rot=[0, 0, 0],
        params={"length": 0.8, "mass": 5.0, "gearing": 100.0},
        ids={"motor": "TalonFX:5"}, defaults={"length": 0.8, "mass": 4.0}))
    lines_preserved(cd.text, new)
    appended = tomlkit.parse(new)["part"][-1]
    assert list(appended.keys()) == ["name", "stock", "attach", "pos", "mass", "gearing", "ids"]
    assert dict(appended["ids"]) == {"motor": "TalonFX:5"}
    assert "length" not in appended and "rot" not in appended
    assert len(tomlkit.parse(new)["part"]) == len(cd.doc["part"]) + 1
    assert cd.text == tomlkit.dumps(cd.doc)  # sanity: round-trip


def test_add_folder_part_with_ids(tmp_path):
    cd = doc_for(tmp_path, "kitbot")
    new = cd.apply(lambda d: edits.add_part(
        d, name="intake", path="parts/intake", attach="drive.top",
        ids={"roller": {"device": "X", "n": 1}}))
    lines_preserved(cd.text, new)
    p = tomlkit.parse(new)["part"][-1]
    assert list(p.keys()) == ["name", "path", "attach", "ids"]
    assert p["ids"]["roller"]["n"] == 1


def test_add_rejects_bad_and_duplicate_names(tmp_path):
    cd = doc_for(tmp_path, "kitbot")
    for bad in ("drive", "a b", ""):
        with pytest.raises(EditError) as e:
            cd.apply(lambda d, b=bad: edits.add_part(d, name=b, stock="arm"))
        assert e.value.field.endswith("/name")


def test_add_into_doc_without_parts():
    d = tomlkit.parse("# hi\n[model]\ntimestep = 0.001\n")
    edits.add_part(d, name="a", stock="chassis")
    out = tomlkit.dumps(d)
    assert tomlkit.parse(out)["part"][0]["name"] == "a"
    assert out.startswith("# hi\n[model]\ntimestep = 0.001\n")


def test_rename_rewrites_attach_and_keeps_comments(tmp_path):
    cd = doc_for(tmp_path, "swerve")
    n_attach = cd.text.count('attach = "drive.')
    assert n_attach >= 4
    new = cd.apply(lambda d: edits.rename_part(d, "drive", "base"))
    assert new.count('attach = "base.') == n_attach
    assert 'attach = "drive.' not in new
    assert 'name = "base"' in new
    # comments untouched
    assert [l for l in cd.text.splitlines() if "#" in l] == [l for l in new.splitlines() if "#" in l]
    # only attach/name lines differ
    diff = [(a, b) for a, b in zip(cd.text.splitlines(), new.splitlines()) if a != b]
    assert len(diff) == n_attach + 1
    assert len(cd.text.splitlines()) == len(new.splitlines())


def test_rename_rewrites_a_sensors_body():
    """B12: a [[sensor]]'s body names its part ("<part>.<body>"), so a rename updates it as it does an attach; a
    sensor on another part, and its comment, stay as they were."""
    d = tomlkit.parse('[[part]]\nname = "intake"\nstock = "arm"\n\n[[part]]\nname = "intake2"\nstock = "arm"\n\n'
                      '[[sensor]]\nname = "mouth"\nkind = "beam"\nbody = "intake.frame"   # the lip\n\n'
                      '[[sensor]]\nname = "other"\nkind = "beam"\nbody = "intake2.frame"\n')
    edits.rename_part(d, "intake", "grabber")
    text = tomlkit.dumps(d)
    assert 'body = "grabber.frame"   # the lip' in text and 'body = "intake2.frame"' in text
    assert [s["body"] for s in tomlkit.parse(text)["sensor"]] == ["grabber.frame", "intake2.frame"]


def test_rename_rejections(tmp_path):
    cd = doc_for(tmp_path, "swerve")
    for bad in ("fl", "has space", "x.y", ""):
        with pytest.raises(EditError) as e:
            cd.apply(lambda d, b=bad: edits.rename_part(d, "drive", b))
        assert e.value.field == "drive/name"


def test_remove_refuses_while_attached_and_leaf_works(tmp_path):
    cd = doc_for(tmp_path, "swerve")
    with pytest.raises(EditError) as e:
        cd.apply(lambda d: edits.remove_part(d, "drive"))
    assert e.value.field == "drive/part"
    for n in ("fl", "fr", "bl", "br"):
        assert n in e.value.message
    assert "move or delete those first" in e.value.message
    new = cd.apply(lambda d: edits.remove_part(d, "br"))
    names = [p["name"] for p in tomlkit.parse(new)["part"]]
    assert "br" not in names and "fl" in names


def test_set_params_merge_default_and_none():
    d = tomlkit.parse('[[part]]\nname = "a"\nstock = "arm"\nlength = 1.0  # keep me\nmass = 3.0\n')
    edits.set_params(d, "a", {"length": 2.0, "mass": None, "gearing": 50.0, "x": 4.0},
                     {"gearing": 50.0, "x": 1.0})
    p = d["part"][0]
    assert p["length"] == 2.0 and "mass" not in p and "gearing" not in p and p["x"] == 4.0
    assert "# keep me" in tomlkit.dumps(d)
    edits.set_params(d, "a", {"x": 1.0}, {"x": 1.0})
    assert "x" not in d["part"][0]


def test_set_ids_replace_and_remove():
    d = tomlkit.parse('[[part]]\nname = "a"\nstock = "arm"\nids = { m = "PWM:0" }\n')
    edits.set_ids(d, "a", {"m": "PWM:3", "e": {"channel_a": 1}})
    assert dict(d["part"][0]["ids"]) == {"m": "PWM:3", "e": {"channel_a": 1}}
    edits.set_ids(d, "a", {})
    assert "ids" not in d["part"][0]


def test_set_ids_keeps_a_part_ids_sub_table():
    """Cleanup D6: a [part.ids] sub-table is edited in place, not replaced with an inline table."""
    text = ('[[part]]\nname = "a"\nstock = "arm"\n\n[part.ids]\nm = "PWM:0"  # the arm\ne = "DIO:1"\n\n'
            '[[part]]\nname = "b"\nstock = "arm"\n')
    d = tomlkit.parse(text)
    edits.set_ids(d, "a", {"m": "PWM:3", "x": "PWM:4"})
    out = tomlkit.dumps(d)
    assert '[part.ids]\nm = "PWM:3"  # the arm\nx = "PWM:4"\n' in out and "ids = {" not in out, out
    assert dict(tomlkit.parse(out)["part"][0]["ids"]) == {"m": "PWM:3", "x": "PWM:4"}
    assert tomlkit.parse(out)["part"][1]["name"] == "b"
    # a new key lands before the next part's comment block, and the blank line above its header stays
    d = tomlkit.parse('[[part]]\nname = "a"\nstock = "arm"\n\n[part.ids]\nm = "PWM:0"\n\n# next part\n'
                      '[[part]]\nname = "b"\nstock = "arm"\n')
    edits.set_ids(d, "a", {"m": "PWM:0", "x": "PWM:4"})
    assert '[part.ids]\nm = "PWM:0"\nx = "PWM:4"\n\n# next part\n[[part]]\nname = "b"' in tomlkit.dumps(d), \
        tomlkit.dumps(d)


def test_set_attach_zero_pos_removed():
    d = tomlkit.parse('[[part]]\nname = "a"\nstock = "arm"\nattach = "x.y"\npos = [1, 2, 3]\n')
    edits.set_attach(d, "a", "z.w", [0, 0, 0], [0, 0, 90])
    p = d["part"][0]
    assert p["attach"] == "z.w" and "pos" not in p and list(p["rot"]) == [0, 0, 90]
    edits.set_attach(d, "a", None, None, None)
    assert "attach" not in p and "rot" not in p


def test_non_ascii_comment_and_bras_survive(tmp_path):
    src = ('# Le bras du robot, éléments mécaniques — ça roule\n'
           '[[part]]\nname = "drive"\nstock = "chassis"\n\n'
           '# bras: épaule\n[[part]]\nname = "bras"\nstock = "arm"\nattach = "drive.top"  # à vérifier\n')
    path = tmp_path / "c.toml"
    path.write_text(src, encoding="utf-8")
    cd = ConfigDoc(path)
    new = cd.apply(lambda d: (edits.add_part(d, name="b2", stock="arm", attach="bras.tip"),
                                         edits.set_params(d, "bras", {"mass": 2.0}, {})))
    cd.commit(new)
    on_disk = path.read_text(encoding="utf-8")
    assert "Le bras du robot, éléments mécaniques — ça roule" in on_disk
    assert "# à vérifier" in on_disk and 'name = "bras"' in on_disk
    lines_preserved(src, on_disk)


def test_configdoc_commit_undo_redo_atomic(tmp_path):
    cd = doc_for(tmp_path, "kitbot")
    original = cd.text
    v0 = cd.version
    t1 = cd.apply(lambda d: edits.add_part(d, name="a1", stock="arm"))
    assert cd.text == original and cd.version == v0  # apply does not commit
    cd.commit(t1)
    t2 = cd.apply(lambda d: edits.add_part(d, name="a2", stock="arm"))
    cd.commit(t2)
    assert cd.version == v0 + 2
    assert sorted(os.listdir(tmp_path)) == ["kitbot.toml"]  # no temp files left
    assert cd.path.read_text() == t2
    assert cd.undo() and cd.path.read_text() == t1 and cd.text == t1
    assert cd.undo() and cd.path.read_text() == original
    assert not cd.undo()
    assert cd.redo() and cd.path.read_text() == t1
    assert cd.redo() and cd.path.read_text() == t2
    assert not cd.redo()
    assert cd.version == v0 + 6
    assert [p["name"] for p in cd.doc["part"]][-1] == "a2"
    cd.undo()
    cd.commit(cd.apply(lambda d: edits.add_part(d, name="a3", stock="arm")))
    assert not cd.redo()  # commit clears redo
    assert sorted(os.listdir(tmp_path)) == ["kitbot.toml"]


def test_reload_if_changed(tmp_path):
    cd = doc_for(tmp_path, "kitbot")
    assert not cd.reload_if_changed()
    cd.commit(cd.apply(lambda d: edits.add_part(d, name="a1", stock="arm")))
    assert not cd.reload_if_changed()  # our own write
    v = cd.version
    # touching without changing content is not a change
    os.utime(cd.path, (time.time() + 5, time.time() + 5))
    assert not cd.reload_if_changed() and cd.version == v
    cd.path.write_text(cd.text + "\n# external\n")
    os.utime(cd.path, (time.time() + 10, time.time() + 10))
    assert cd.reload_if_changed()
    assert "# external" in cd.text and cd.version == v + 1
    assert not cd.undo() and not cd.redo()
    assert not cd.reload_if_changed()


def test_symlinked_config_stays_a_link(tmp_path):
    real = tmp_path / "real.toml"
    shutil.copy(ROOT / "configs" / "kitbot.toml", real)
    link = tmp_path / "link.toml"
    link.symlink_to(real)
    cd = ConfigDoc(link)
    t = cd.apply(lambda d: edits.add_part(d, name="a1", stock="arm"))
    cd.commit(t)
    assert link.is_symlink() and real.read_text() == t
    assert not real.is_symlink()


def test_open_time_read_only(tmp_path):
    path = tmp_path / "bad.toml"
    path.write_text("[model\nx = ")
    cd = ConfigDoc(path)
    assert cd.doc is None and cd.text == "[model\nx = "
    assert "bad.toml" in cd.read_only_reason
    assert cd.read_only_reason == cd.read_only_reason
    for call in (lambda: cd.apply(lambda d: None), lambda: cd.commit("a = 1\n"), cd.undo, cd.redo):
        with pytest.raises(EditError) as e:
            call()
        assert e.value.field == "status" and e.value.message == cd.read_only_reason
    assert path.read_text() == "[model\nx = "
    path.write_text("[model]\ntimestep = 0.001\n")
    assert cd.reload_if_changed()
    assert cd.read_only_reason is None and cd.doc is not None


def test_reload_time_read_only_and_recovery(tmp_path):
    cd = doc_for(tmp_path, "kitbot")
    v = cd.version
    cd.path.write_text("oops [")
    assert cd.reload_if_changed()
    assert cd.version == v + 1 and cd.doc is None and cd.text == "oops ["
    assert cd.read_only_reason and not cd.reload_if_changed()
    assert cd.read_only_reason  # still reported
    shutil.copy(ROOT / "configs" / "kitbot.toml", cd.path)
    assert cd.reload_if_changed() and cd.read_only_reason is None and cd.doc is not None


def test_commit_never_writes_invalid_toml(tmp_path):
    cd = doc_for(tmp_path, "kitbot")
    before = cd.path.read_text()
    with pytest.raises(EditError):
        cd.commit("[broken")
    assert cd.path.read_text() == before and cd.text == before and not cd._undo
    assert sorted(os.listdir(tmp_path)) == ["kitbot.toml"]


def test_failed_write_keeps_snapshots(tmp_path, monkeypatch):
    cd = doc_for(tmp_path, "kitbot")
    cd.commit(cd.apply(lambda d: edits.add_part(d, name="a1", stock="arm")))
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        cd.undo()
    monkeypatch.undo()
    assert len(cd._undo) == 1 and cd.undo()


def test_history_bounded(tmp_path):
    cd = doc_for(tmp_path, "kitbot")
    for i in range(205):
        cd.commit(cd.text + f"# {i}\n")
    assert len(cd._undo) == 200


def test_set_ids_keeps_inline_spacing():
    d = tomlkit.parse('[[part]]\nname = "a"\nstock = "arm"\nids = { m = "PWM:0", e = "PWM:1" }  # c\n')
    edits.set_ids(d, "a", {"m": "PWM:3", "e": "PWM:1"})
    out = tomlkit.dumps(d)
    assert 'ids = { m = "PWM:3", e = "PWM:1" }  # c' in out


# -- where new keys and tables land (the comment block above a [[part]] belongs to that part) --------------

def _around(text: str, anchor: str, before: int, after: int) -> list[str]:
    lines = text.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln.startswith(anchor))
    return lines[i - before:i + after + 1]


def test_new_param_lands_before_next_parts_comment_block(tmp_path):
    cd = doc_for(tmp_path, "elevator_arm")
    new = cd.apply(lambda d: edits.set_params(d, "dt", {"gearing": 8.0}, {}))
    assert _around(new, "gearing = 8.0", 2, 3) == [
        'attach = "drive.center"',
        'ids = { left = "PWM:0", right = "PWM:1", left_encoder = { channel_a = 0, counts_per_rev = 2048 }, '
        'right_encoder = { channel_a = 2, counts_per_rev = 2048 } }',
        "gearing = 8.0",
        "",
        "# A two-stage cascade elevator: stage1 rides the base and the carriage rides stage1, so the carriage",
        "# moves twice as far as stage1 (and as the rope). Two Kraken X60s through 20:1 onto a 22 mm spool",
    ]
    assert "# spool turns x 2 pi r x stages.\n[[part]]\nname = \"elevator\"" in new
    assert new.replace("gearing = 8.0\n", "") == cd.text


def test_new_param_in_swerve_keeps_section_comment(tmp_path):
    cd = doc_for(tmp_path, "swerve")
    new = cd.apply(lambda d: edits.set_params(d, "drive", {"mass": 50.0}, {}))
    assert _around(new, "mass = 50.0", 1, 2) == ['ids = { gyro = "Pigeon2:13" }', "mass = 50.0", "",
                                                  "# ---- Modules -------------------------------------------------------------------------"]
    assert "# matching the example's encoder offsets of 0.\n\n[[part]]\nname = \"fl\"" in new
    assert new.replace("mass = 50.0\n", "") == cd.text


def test_new_pos_and_rot_follow_attach_or_stock(tmp_path):
    cd = doc_for(tmp_path, "swerve")
    new = cd.apply(lambda d: edits.set_attach(d, "fl", "drive.front_left", [0, 0, 0.1], [0, 0, 90]))
    got = _around(new, "pos = ", 2, 2)
    assert got[:4] == ['ratio = "L2"', 'attach = "drive.front_left"', "pos = [0.0, 0.0, 0.1]", "rot = [0.0, 0.0, 90.0]"]
    assert got[4].startswith("ids = { drive = ")
    # the root part has no attach: rot goes right after stock
    new = cd.apply(lambda d: edits.set_attach(d, "drive", None, None, [0, 0, 45]))
    assert _around(new, "rot = ", 1, 1) == [
        'stock = "chassis"         # 0.7 m square frame, 46 kg, bumpers; modules mount at its corners',
        "rot = [0.0, 0.0, 45.0]",
        "module_inset = 0.0579     # m from each frame edge to a module center: modules at +-0.2921 m (11.5 in)"]
    # elevator_arm: dt gets pos right after its attach, above its ids (and the elevator's comment block)
    cd = doc_for(tmp_path, "elevator_arm")
    new = cd.apply(lambda d: edits.set_attach(d, "dt", "drive.center", [0.1, 0, 0], None))
    got = _around(new, "pos = [0.1, 0.0, 0.0]", 1, 1)
    assert got[:2] == ['attach = "drive.center"', "pos = [0.1, 0.0, 0.0]"] and got[2].startswith("ids = { left = ")
    assert new.replace("pos = [0.1, 0.0, 0.0]\n", "") == cd.text


def test_new_ids_on_part_without_ids_land_before_next_comment(tmp_path):
    src = ('[[part]]\nname = "drive"\nstock = "chassis"\n\n# the arm\n[[part]]\nname = "arm"\nstock = "arm"\n'
           'attach = "drive.top"\n')
    d = tomlkit.parse(src)
    edits.set_ids(d, "drive", {"gyro": "Pigeon2:1"})
    assert tomlkit.dumps(d) == ('[[part]]\nname = "drive"\nstock = "chassis"\nids = {gyro = "Pigeon2:1"}\n\n'
                                '# the arm\n[[part]]\nname = "arm"\nstock = "arm"\nattach = "drive.top"\n')


def test_appended_part_gets_a_blank_line(tmp_path):
    cd = doc_for(tmp_path, "elevator_arm")
    assert not cd.text.endswith("\n\n")
    new = cd.apply(lambda d: edits.add_part(d, name="x", stock="arm", attach="drive.top"))
    assert new.startswith(cd.text + "\n[[part]]\nname = \"x\"\n")
    # a file that already ends with a blank line gets no second one
    d = tomlkit.parse('[[part]]\nname = "a"\nstock = "chassis"\n\n')
    edits.add_part(d, name="b", stock="arm", attach="a.top")
    assert tomlkit.dumps(d) == '[[part]]\nname = "a"\nstock = "chassis"\n\n[[part]]\nname = "b"\nstock = "arm"\nattach = "a.top"\n'


def test_remove_last_part_drops_its_comment_block(tmp_path):
    cd = doc_for(tmp_path, "elevator_arm")
    new = cd.apply(lambda d: edits.remove_part(d, "arm"))
    assert "A 0.5 m, 2 kg arm" not in new and "0 degrees is horizontal" not in new
    assert new.endswith('ids = { lift = "PWM:2", encoder = { channel_a = 4, counts_per_rev = 2048 } }\n')
    assert new == cd.text[:cd.text.index("\n# A 0.5 m, 2 kg arm")]


def test_remove_middle_part_keeps_next_parts_comment(tmp_path):
    cd = doc_for(tmp_path, "elevator_arm")
    new = cd.apply(lambda d: edits.remove_part(d, "dt"))
    old = cd.text
    start = old.index('[[part]]\nname = "dt"')
    end = old.index("# A two-stage cascade elevator")
    assert new == old[:start] + old[end:]
    assert "# spool turns x 2 pi r x stages.\n[[part]]\nname = \"elevator\"" in new
    # swerve: removing fr leaves exactly one blank line between fl and bl
    cd = doc_for(tmp_path, "swerve")
    new = cd.apply(lambda d: edits.remove_part(d, "fr"))
    old = cd.text
    assert new == old[:old.index('[[part]]\nname = "fr"')] + old[old.index('[[part]]\nname = "bl"'):]


def test_remove_part_with_comment_and_a_following_commented_part():
    src = ('[[part]]\nname = "a"\nstock = "chassis"\n\n# about b\n[[part]]\nname = "b"\nstock = "arm"\n'
           'attach = "a.top"\n\n# about c\n[[part]]\nname = "c"\nstock = "arm"\nattach = "a.top"\n')
    d = tomlkit.parse(src)
    edits.remove_part(d, "b")
    assert tomlkit.dumps(d) == ('[[part]]\nname = "a"\nstock = "chassis"\n\n# about c\n[[part]]\nname = "c"\n'
                                'stock = "arm"\nattach = "a.top"\n')


def test_remove_first_part_keeps_next_parts_comment():
    src = ('[robot]\nstart_pose = [0.0, 0.0, 0.0]\n\n# Parts\n\n[[part]]\nname = "a"\nstock = "arm"\n'
           'attach = "b.top"\n\n# about b\n[[part]]\nname = "b"\nstock = "chassis"\n')
    d = tomlkit.parse(src)
    edits.remove_part(d, "a")
    assert tomlkit.dumps(d) == ('[robot]\nstart_pose = [0.0, 0.0, 0.0]\n\n# Parts\n\n# about b\n[[part]]\n'
                                'name = "b"\nstock = "chassis"\n')


def test_cad_offset():
    from scrimmage.assembler.edits import cad_offset
    mine = {"import_id": "a", "origins": {"hook": [0.0, 0.0, 0.0]}}
    q = {"import_id": "a", "origins": {"base": [0.0, 0.0, 0.0], "arm": [0.09, 0.0, 0.01]}}
    assert cad_offset(None, "chassis", None, True) is None                      # not from a CAD import
    assert cad_offset(mine, "chassis", None, True) == [0.0, 0.0, 0.0]          # the stock chassis's root body
    assert cad_offset(mine, "arm", q, False) == [-0.09, 0.0, -0.01]            # -origins[arm]
    assert str(cad_offset(mine, "base", q, False)) == "[0.0, 0.0, 0.0]"        # never -0.0
    assert cad_offset(mine, "arm", {**q, "import_id": "b"}, False) is None     # another import
    assert cad_offset(mine, "claw", q, False) is None                          # a body the other part doesn't have
    assert cad_offset(mine, "arm", None, False) is None                        # a part not from an import


def test_start_keep_pose_keeps_a_valid_start_pose():
    """The Test tab's field picker sends keep_pose: a robot placed by [robot] start_pose stays where it is when only
    the field changes."""
    doc = tomlkit.parse('[robot]\nstart_pose = [5.0, 2.0, 90.0]\n\n[range]\nfile = "2026"\n')
    edits.set_start(doc, {"range": "ladder", "named": "below", "keep_pose": True})
    assert doc["range"].unwrap() == {"file": "ladder"}
    assert doc["robot"]["start_pose"].unwrap() == [5.0, 2.0, 90.0]


@pytest.mark.parametrize("robot", ['[robot]\nstart_pose = [30.0, 2.0, 0.0]\n', '[robot]\nstart_pose = "here"\n',
                                   '[robot]\nmass = 50.0\n', ''])
def test_start_keep_pose_without_a_valid_pose_uses_the_named_start(robot):
    doc = tomlkit.parse(robot)
    edits.set_start(doc, {"range": "ladder", "named": "below", "keep_pose": True})
    assert doc["range"].unwrap() == {"file": "ladder", "start": "below"}
    assert "start_pose" not in doc.get("robot", {})


def test_start_keep_pose_must_be_true_or_false():
    with pytest.raises(EditError) as e:
        edits.set_start(tomlkit.parse(""), {"range": "ladder", "named": "below", "keep_pose": "yes"})
    assert e.value.field == "start/keep_pose"


def test_the_tomlkit_it_runs_on_has_what_tomlutil_leans_on():
    """Cleanup D6: assembler/tomlutil.py uses tomlkit's private Container API, and the editor relies on tomlkit refusing
    an unclosed array-of-tables header (tomlkit before 0.15.1 parses it). A tomlkit outside pyproject's tested range
    fails here, by name, rather than as odd edits."""
    import inspect
    from importlib.metadata import version

    from tomlkit.container import Container
    from tomlkit.exceptions import ParseError
    from packaging.version import Version
    assert Version("0.15.1") <= Version(version("tomlkit")) < Version("0.16"), version("tomlkit")
    for name, params in (("_insert_after", ["self", "key", "other_key", "item"]),
                         ("_insert_at", ["self", "idx", "key", "item"]), ("_raw_append", ["self", "key", "item"])):
        assert list(inspect.signature(getattr(Container, name)).parameters) == params, name
    assert isinstance(Container()._map, dict)
    with pytest.raises(ParseError):
        tomlkit.parse('config = "x"\n[[step]\n')


def test_a_start_on_a_ranges_own_field_size(tmp_path):
    # review M3: a range with field = { length, width } (2024: 8.211 m wide) takes starts out to its own walls
    (tmp_path / "r.toml").write_text('[range]\nname = "t"\nball = "2026-fuel"\nfield = { length = 16.541, width = 8.211 }\n')
    doc = tomlkit.parse('[range]\nfile = "r.toml"\n')
    edits.set_start(doc, {"pose": [1.0, 8.15, 0.0]}, base_dir=tmp_path)
    assert doc["robot"]["start_pose"].unwrap() == [1.0, 8.15, 0.0]
    with pytest.raises(EditError, match=r"off the field \(16.541 m by 8.211 m\)"):
        edits.set_start(doc, {"pose": [1.0, 8.3, 0.0]}, base_dir=tmp_path)
    with pytest.raises(EditError, match=r"off the field \(16.54 m by 8.07 m\)"):
        edits.set_start(tomlkit.parse('[range]\nfile = "2026"\n'), {"pose": [1.0, 8.15, 0.0]}, base_dir=tmp_path)


def test_the_start_sentence_halves_the_ranges_field():
    from scrimmage.assembler.server_common import start_sentence
    assert start_sentence([8.0, 4.0, 0.0]) == "Starts on the blue side, facing the red wall"
    assert start_sentence([8.0, 4.0, 0.0], length=10.0) == "Starts on the red side, facing the red wall"
