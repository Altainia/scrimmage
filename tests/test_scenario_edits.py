import tomllib
from pathlib import Path

import pytest
import tomlkit

from scrimmage.assembler import scenario_edits as se
from scrimmage.assembler.edits import EditError
from scrimmage.scenario import load_scenario_text

ROOT = Path(__file__).resolve().parent.parent
FAKE_PATH = ROOT / "scenarios" / "x.toml"

COMMENTED = '''# header
config = "../configs/kitbot.toml"
description = "d"

# --- section: setup ---

# A: idle
[[step]]
name = "A"
mode = "disabled"
duration = 1.0

# B: drive
# second line of B
[[step]]
name = "B"
mode = "auto"
duration = 2.0
  # check one
  [[step.check]]
  console = { contains = "x" }
  [[step.screenshot]]
  view = "top"

# C: end
[[step]]
name = "C"
mode = "teleop"
duration = 3.0
# trailing at EOF
'''


def run(text, *fns, load=True):
    """Apply edits to a parsed copy, return the new text (which must still load as a scenario)."""
    d = tomlkit.parse(text)
    for fn in fns:
        fn(d)
    out = tomlkit.dumps(d)
    tomlkit.parse(out)
    if load:
        load_scenario_text(out, FAKE_PATH)
    return out


def names(text):
    return [s.get("name") for s in tomllib.loads(text)["step"]]


def block_above(text, name):
    """The comment lines directly above the [[step]] whose name is `name`."""
    lines = text.splitlines()
    i = lines.index(f'name = "{name}"')
    while not lines[i].startswith("[[step]]"):
        i -= 1
    out = []
    i -= 1
    while i >= 0 and lines[i].startswith("#"):
        out.insert(0, lines[i])
        i -= 1
    return out


BLOCKS = {"A": ["# A: idle"], "B": ["# B: drive", "# second line of B"], "C": ["# C: end"]}


def assert_blocks(text):
    for n in names(text):
        if n in BLOCKS:
            assert block_above(text, n) == BLOCKS[n], f"comment block of {n}\n{text}"
    assert "# --- section: setup ---" in text and "# header" in text and "# trailing at EOF" in text


def scenario_text(name):
    if name == "fake_pass":
        return (ROOT / "tests" / "scenarios" / "fake_pass.toml").read_text()
    return (ROOT / "scenarios" / f"{name}.toml").read_text()


SHIPPED = ["kitbot_auto", "swerve_auto", "elevator_arm", "fake_pass"]


# ---- top-level keys ------------------------------------------------------------------------------------------

def test_set_top_adds_in_order_and_keeps_header_comments():
    out = run(COMMENTED,
              lambda d: se.set_top(d, "require_lockstep", False),
              lambda d: se.set_top(d, "speed", 2))
    lines = out.splitlines()
    assert lines[:5] == ['# header', 'config = "../configs/kitbot.toml"', 'description = "d"',
                         "speed = 2", 'require_lockstep = false']
    assert_blocks(out)
    assert tomllib.loads(out)["speed"] == 2


def test_set_top_replaces_in_place_and_removes():
    t = COMMENTED.replace('description = "d"', 'description = "d"  # keep me')
    out = run(t, lambda d: se.set_top(d, "description", "new"))
    assert 'description = "new"  # keep me' in out
    out = run(out, lambda d: se.set_top(d, "description", None), lambda d: se.set_top(d, "config", "../x.toml"))
    assert "description" not in out and 'config = "../x.toml"' in out
    assert_blocks(out)


def test_set_top_rejects_bad_input():
    d = tomlkit.parse(COMMENTED)
    for key, val in (("bogus", 1), ("config", None), ("config", 3), ("speed", True), ("require_lockstep", "yes")):
        with pytest.raises(EditError) as e:
            se.set_top(d, key, val)
        assert e.value.field == f"scenario/{key}"


# ---- steps ----------------------------------------------------------------------------------------------------

def test_add_step_positions_and_comment_blocks():
    out = run(COMMENTED, lambda d: se.add_step(d, 0, "N", "auto", 1.5))
    assert names(out) == ["A", "N", "B", "C"]
    assert_blocks(out)
    assert block_above(out, "N") == []
    out = run(COMMENTED, lambda d: se.add_step(d, None, "N", "auto", 1.5))
    assert names(out) == ["A", "B", "C", "N"]
    assert_blocks(out)
    assert out.rstrip().endswith("# trailing at EOF")   # the end-of-file comment stays at the end
    out = run(COMMENTED, lambda d: se.add_step(d, 1, "", "disabled", 2))
    assert names(out) == ["A", "B", None, "C"]
    assert_blocks(out)


def test_add_step_into_file_without_trailing_newline():
    t = COMMENTED.replace("# trailing at EOF\n", "").rstrip("\n")
    out = run(t, lambda d: se.add_step(d, None, "N", "auto", 1.0))
    assert names(out) == ["A", "B", "C", "N"]
    out = run(t, lambda d: se.move_step(d, 2, 0))
    assert names(out) == ["C", "A", "B"]


@pytest.mark.parametrize("i", [0, 1, 2])
def test_duplicate_step(i):
    out = run(COMMENTED, lambda d: se.duplicate_step(d, i))
    want = ["A", "B", "C"]
    want.insert(i + 1, want[i])
    assert names(out) == want
    if i == 1:   # the copy keeps its checks but not the step's comments; the original keeps its block
        steps = tomllib.loads(out)["step"]
        assert steps[1] == steps[2]
        assert block_above(out, "B") == BLOCKS["B"]
        assert out.count("# B: drive") == 1 and out.count("# check one") == 2
    else:
        assert_blocks(out)


def test_delete_step_takes_its_comments_and_keeps_the_next_ones():
    out = run(COMMENTED, lambda d: se.delete_step(d, 1))
    assert names(out) == ["A", "C"]
    assert "B: drive" not in out and "second line of B" not in out and "check one" not in out
    assert block_above(out, "C") == BLOCKS["C"] and block_above(out, "A") == BLOCKS["A"]
    assert "# trailing at EOF" in out and "# --- section: setup ---" in out
    out = run(COMMENTED, lambda d: se.delete_step(d, 0))
    assert names(out) == ["B", "C"]
    assert "A: idle" not in out
    assert block_above(out, "B") == BLOCKS["B"] and block_above(out, "C") == BLOCKS["C"]
    assert "# --- section: setup ---" in out and "# header" in out
    out = run(COMMENTED, lambda d: se.delete_step(d, 2))
    assert names(out) == ["A", "B"]
    assert "C: end" not in out
    assert block_above(out, "B") == BLOCKS["B"]
    assert "# trailing at EOF" in out


def test_delete_all_but_one_then_the_last():
    out = run(COMMENTED, lambda d: se.delete_step(d, 0), lambda d: se.delete_step(d, 0))
    assert names(out) == ["C"] and block_above(out, "C") == BLOCKS["C"]
    out = run(out, lambda d: se.delete_step(d, 0), load=False)
    assert "step" not in tomllib.loads(out)
    assert "# header" in out and "# --- section: setup ---" in out


@pytest.mark.parametrize("i,j", [(0, 1), (0, 2), (1, 0), (1, 2), (2, 0), (2, 1), (1, 1)])
def test_move_step_keeps_each_comment_block(i, j):
    out = run(COMMENTED, lambda d: se.move_step(d, i, j))
    want = ["A", "B", "C"]
    want.insert(j, want.pop(i))
    assert names(out) == want
    assert_blocks(out)
    assert out.count("check one") == 1 and out.rstrip().endswith("# trailing at EOF")
    assert tomllib.loads(out)["step"][want.index("B")]["check"] == [{"console": {"contains": "x"}}]


def test_move_step_in_flat_file_without_comments():
    t = scenario_text("kitbot_auto")
    out = run(t, lambda d: se.move_step(d, 1, 0))
    assert [s["mode"] for s in tomllib.loads(out)["step"]] == ["auto", "disabled", "disabled"]
    assert out.startswith(t.split("[[step]]")[0])   # the file header is untouched


def test_set_step():
    out = run(COMMENTED,
              lambda d: se.set_step(d, 0, {"name": None, "duration": 4}),
              lambda d: se.set_step(d, 2, {"name": "Z"}),
              lambda d: se.set_step(d, 1, {"mode": "disabled"}))
    steps = tomllib.loads(out)["step"]
    assert [s.get("name") for s in steps] == [None, "B", "Z"]
    assert steps[0]["duration"] == 4 and steps[1]["mode"] == "disabled"
    assert block_above(out, "B") == BLOCKS["B"]
    out = run(out, lambda d: se.set_step(d, 0, {"name": "first"}))
    assert out.index('name = "first"') < out.index('mode = "disabled"')   # name stays first


def test_set_step_rejects_bad_input():
    d = tomlkit.parse(COMMENTED)
    for fields, field in (({"mode": None}, "step0/mode"), ({"duration": "x"}, "step0/duration"),
                          ({"bogus": 1}, "step0/bogus"), ({"mode": 3}, "step0/mode")):
        with pytest.raises(EditError) as e:
            se.set_step(d, 0, fields)
        assert e.value.field == field
    for fn in (lambda: se.set_step(d, 9, {}), lambda: se.delete_step(d, -1), lambda: se.move_step(d, 0, 3),
               lambda: se.duplicate_step(d, 3), lambda: se.add_step(d, 5, "n", "auto", 1)):
        with pytest.raises(EditError):
            fn()


# ---- inputs ---------------------------------------------------------------------------------------------------

def test_inputs_written_inline_and_load():
    out = run(COMMENTED,
              lambda d: se.set_inputs(d, 1, "axes", 1, [1.0]),
              lambda d: se.set_inputs(d, 1, "axes", 0, [0.5, -0.5]),
              lambda d: se.set_inputs(d, 1, "buttons", 0, [True, False]),
              lambda d: se.set_inputs(d, 1, "povs", 2, [90, -1]),
              lambda d: se.set_inputs(d, 0, "axes", 0, [0]))
    assert "axes = { 0 = [0.5, -0.5], 1 = [1.0] }" in out
    assert "buttons = { 0 = [true, false] }" in out and "povs = { 2 = [90, -1] }" in out
    s = load_scenario_text(out, FAKE_PATH).steps
    assert s[1].axes == {0: [0.5, -0.5], 1: [1.0]} and s[1].buttons == {0: [True, False]} and s[1].povs == {2: [90, -1]}
    assert out.index("axes = { 0 = [0.5") < out.index("# check one")   # keys stay above the nested tables
    assert_blocks(out)


def test_inputs_remove_port_and_key():
    out = run(COMMENTED, lambda d: se.set_inputs(d, 1, "axes", 0, [0.5]), lambda d: se.set_inputs(d, 1, "axes", 3, [0.2]))
    out = run(out, lambda d: se.set_inputs(d, 1, "axes", 0, None))
    assert "axes = { 3 = [0.2] }" in out
    out = run(out, lambda d: se.set_inputs(d, 1, "axes", 3, []), lambda d: se.set_inputs(d, 1, "buttons", 1, None))
    assert "axes" not in out
    assert_blocks(out)


def test_inputs_edit_a_hand_written_table_in_place():
    t = COMMENTED.replace('duration = 2.0\n', 'duration = 2.0\naxes = { 0 = [1.0] }\n', 1)
    t = t.replace('axes = { 0 = [1.0] }\n', '', 1).replace('  # check one', '  [step.axes]\n  0 = [1.0]\n  # check one')
    out = run(t, lambda d: se.set_inputs(d, 1, "axes", 1, [0.5]), lambda d: se.set_inputs(d, 1, "axes", 0, None))
    assert tomllib.loads(out)["step"][1]["axes"] == {"1": [0.5]}
    out = run(out, lambda d: se.set_inputs(d, 1, "axes", 1, None))
    assert "axes" not in tomllib.loads(out)["step"][1]


def test_inputs_reject_bad_input():
    d = tomlkit.parse(COMMENTED)
    for args in (("sliders", 0, [1]), ("axes", 6, [0]), ("axes", 0, [2.0]), ("buttons", 0, [1]),
                 ("povs", 0, [400]), ("axes", 0, "x")):
        with pytest.raises(EditError) as e:
            se.set_inputs(d, 0, *args)
        assert e.value.field.startswith("step0/")


# ---- checks, screenshots, contact sheet ------------------------------------------------------------------------

def test_add_check_in_each_position():
    out = run(COMMENTED,
              lambda d: se.add_check(d, 1, "pose", {"x": 1.0, "y": 2, "tol_xy": 0.3}),
              lambda d: se.add_check(d, 0, "never_touch", "wall"),
              lambda d: se.add_check(d, 2, "console", {"contains": "hi"}))
    steps = tomllib.loads(out)["step"]
    assert steps[1]["check"] == [{"console": {"contains": "x"}}, {"pose": {"x": 1.0, "y": 2, "tol_xy": 0.3}}]
    assert steps[0]["check"] == [{"never_touch": "wall"}] and steps[2]["check"] == [{"console": {"contains": "hi"}}]
    assert "pose = { x = 1.0, y = 2, tol_xy = 0.3 }" in out
    assert "  [[step.check]]\n  pose" in out   # indented like the file's other nested tables
    assert out.index("pose = ") < out.index("[[step.screenshot]]")   # before the screenshots
    assert_blocks(out)


def test_check_and_screenshot_comments_stay_in_place():
    t = COMMENTED.replace("  [[step.screenshot]]", "  # shot comment\n  [[step.screenshot]]")
    out = run(t, lambda d: se.add_check(d, 1, "always", {"signal": "battery", "min": 7}))
    assert out.index("# shot comment") > out.index("always =")
    assert out.index("# shot comment") < out.index("[[step.screenshot]]")
    out = run(t, lambda d: se.delete_check(d, 1, 0))
    assert "check one" not in out and "# shot comment" in out
    out = run(t, lambda d: se.delete_shot(d, 1, 0))
    assert "shot comment" not in out and "# check one" in out
    assert_blocks(out)


def test_set_check_replaces_kind_and_keeps_position():
    out = run(COMMENTED, lambda d: se.set_check(d, 1, 0, {"always": {"signal": "battery", "min": 7}}))
    assert tomllib.loads(out)["step"][1]["check"] == [{"always": {"signal": "battery", "min": 7}}]
    assert "# check one" in out and out.index("always") < out.index("[[step.screenshot]]")
    out = run(out, lambda d: se.set_check(d, 1, 0, {"always": {"signal": "battery", "min": 8}}))
    assert "min = 8" in out
    assert_blocks(out)


def test_delete_check_removes_array_when_empty():
    out = run(COMMENTED, lambda d: se.delete_check(d, 1, 0))
    assert "check" not in tomllib.loads(out)["step"][1] and "[[step.check]]" not in out
    assert_blocks(out)
    out = run(COMMENTED, lambda d: se.add_check(d, 2, "console", {"contains": "hi"}),
              lambda d: se.delete_check(d, 2, 0))
    assert out.rstrip().endswith("# trailing at EOF")
    assert_blocks(out)


def test_check_errors():
    d = tomlkit.parse(COMMENTED)
    for fn in (lambda: se.add_check(d, 0, "bogus", {}), lambda: se.add_check(d, 0, "pose", None),
               lambda: se.set_check(d, 1, 0, {"pose": {}, "always": {}}), lambda: se.set_check(d, 1, 5, {"pose": {}}),
               lambda: se.delete_check(d, 0, 0), lambda: se.add_check(d, 7, "pose", {})):
        with pytest.raises(EditError):
            fn()


def test_shots():
    out = run(COMMENTED,
              lambda d: se.add_shot(d, 1, {"view": "follow", "at": 1.0, "camera": {"lookat": [1, 2, 3]}}),
              lambda d: se.add_shot(d, 0, {"view": "top"}),
              lambda d: se.add_shot(d, 2, {"width": 640, "height": 480}))
    steps = tomllib.loads(out)["step"]
    assert steps[1]["screenshot"][1] == {"view": "follow", "at": 1.0, "camera": {"lookat": [1, 2, 3]}}
    assert steps[0]["screenshot"] == [{"view": "top"}] and steps[2]["screenshot"] == [{"width": 640, "height": 480}]
    assert "camera = { lookat = [1, 2, 3] }" in out
    assert_blocks(out)
    out = run(out, lambda d: se.set_shot(d, 1, 1, {"at": None, "view": "top", "width": 800}))
    assert tomllib.loads(out)["step"][1]["screenshot"][1] == {"view": "top", "camera": {"lookat": [1, 2, 3]}, "width": 800}
    out = run(out, lambda d: se.delete_shot(d, 1, 0), lambda d: se.delete_shot(d, 0, 0))
    steps = tomllib.loads(out)["step"]
    assert len(steps[1]["screenshot"]) == 1 and "screenshot" not in steps[0]
    assert_blocks(out)
    d = tomlkit.parse(out)
    for fn in (lambda: se.add_shot(d, 1, {"bogus": 1}), lambda: se.set_shot(d, 1, 3, {}), lambda: se.delete_shot(d, 0, 0)):
        with pytest.raises(EditError):
            fn()


def test_contact_sheet():
    out = run(COMMENTED, lambda d: se.set_sheet(d, 1, {"frames": 4, "view": "top"}),
              lambda d: se.set_sheet(d, 2, {"frames": 2}), lambda d: se.set_sheet(d, 0, {"width": 400}))
    steps = tomllib.loads(out)["step"]
    assert steps[1]["contact_sheet"] == {"frames": 4, "view": "top"} and steps[2]["contact_sheet"] == {"frames": 2}
    assert steps[0]["contact_sheet"] == {"width": 400}
    assert_blocks(out)
    out = run(out, lambda d: se.set_sheet(d, 1, {"frames": 6, "view": None}))
    assert tomllib.loads(out)["step"][1]["contact_sheet"] == {"frames": 6}
    out = run(out, lambda d: se.set_sheet(d, 1, None), lambda d: se.set_sheet(d, 2, None), lambda d: se.set_sheet(d, 0, None),
              lambda d: se.set_sheet(d, 0, None))
    assert "contact_sheet" not in out
    assert out == COMMENTED
    with pytest.raises(EditError):
        se.set_sheet(tomlkit.parse(COMMENTED), 0, {"bogus": 1})


def test_add_then_delete_roundtrips_to_the_original_text():
    for fn_add, fn_del in (
            (lambda d: se.add_check(d, 1, "console", {"contains": "y"}), lambda d: se.delete_check(d, 1, 1)),
            (lambda d: se.add_shot(d, 1, {"view": "top"}), lambda d: se.delete_shot(d, 1, 1)),
            (lambda d: se.add_check(d, 0, "never_touch", "w"), lambda d: se.delete_check(d, 0, 0)),
            (lambda d: se.add_shot(d, 2, {"view": "top"}), lambda d: se.delete_shot(d, 2, 0)),
            (lambda d: se.add_step(d, 0, "N", "auto", 1), lambda d: se.delete_step(d, 1)),
            (lambda d: se.add_step(d, None, "N", "auto", 1), lambda d: se.delete_step(d, 3)),
            (lambda d: se.duplicate_step(d, 1), lambda d: se.delete_step(d, 2))):
        assert run(COMMENTED, fn_add, fn_del) == COMMENTED


# ---- the shipped scenarios -------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", SHIPPED)
def test_every_op_on_shipped_scenarios_still_loads_and_keeps_comments(name):
    text = scenario_text(name)
    n = len(tomllib.loads(text)["step"])
    head = [ln for ln in text.splitlines() if ln.startswith("#")]
    out = run(text,
              lambda d: se.set_top(d, "speed", "max"),
              lambda d: se.set_top(d, "require_lockstep", False),
              lambda d: se.add_step(d, 0, "extra", "teleop", 2.5),
              lambda d: se.set_step(d, 1, {"name": "renamed", "duration": 3}),
              lambda d: se.set_inputs(d, 1, "axes", 0, [0.25, -0.5]),
              lambda d: se.set_inputs(d, 1, "buttons", 1, [True]),
              lambda d: se.set_inputs(d, 1, "povs", 0, [180]),
              lambda d: se.add_check(d, 1, "console", {"contains": "hello"}),
              lambda d: se.add_check(d, 2, "never_touch", "obstacle"),
              lambda d: se.add_shot(d, 1, {"view": "follow", "at": 1.0}),
              lambda d: se.set_sheet(d, 1, {"frames": 4, "view": "top"}),
              lambda d: se.duplicate_step(d, 2),
              lambda d: se.move_step(d, 0, 2))
    sc = load_scenario_text(out, FAKE_PATH)
    assert len(sc.steps) == n + 2 and sc.require_lockstep is False
    assert [ln for ln in out.splitlines() if ln.startswith("#")][:len(head)] == head   # file header kept
    # and back down again
    out2 = run(out, lambda d: se.delete_step(d, 0), lambda d: se.delete_step(d, 0))
    assert len(load_scenario_text(out2, FAKE_PATH).steps) == n
    for k in range(len(tomllib.loads(out2)["step"])):
        out2 = run(out2, lambda d, k=k: [se.set_sheet(d, k, None)])
    assert "contact_sheet" not in out2
    for k in range(len(tomllib.loads(out2)["step"])):
        raw = tomllib.loads(out2)["step"][k]
        for j in reversed(range(len(raw.get("check", [])))):
            out2 = run(out2, lambda d, k=k, j=j: se.delete_check(d, k, j))
        for j in reversed(range(len(raw.get("screenshot", [])))):
            out2 = run(out2, lambda d, k=k, j=j: se.delete_shot(d, k, j))
    assert "[[step.check]]" not in out2 and "[[step.screenshot]]" not in out2


@pytest.mark.parametrize("name", SHIPPED)
def test_shipped_scenario_edit_changes_only_what_it_touches(name):
    text = scenario_text(name)
    out = run(text, lambda d: se.set_step(d, 0, {"duration": 9.5}))
    old, new = text.splitlines(), out.splitlines()
    assert len(old) == len(new)
    assert [a for a, b in zip(old, new) if a != b] == ["duration = " + old[[i for i, (a, b) in enumerate(zip(old, new)) if a != b][0]].split("= ")[1]]


def test_kitbot_delete_middle_and_last_with_nested_tables():
    text = scenario_text("kitbot_auto")
    out = run(text, lambda d: se.delete_step(d, 1))
    assert len(tomllib.loads(out)["step"]) == 2 and "[[step.check]]" not in out
    out = run(text, lambda d: se.delete_step(d, 2))
    assert len(tomllib.loads(out)["step"]) == 2 and "[step.contact_sheet]" in out


INLINE_FORMS = '''config = "../configs/kitbot.toml"

[[step]]
name = "inl"
mode = "auto"
duration = 1.0
check = [ { console = { contains = "a" } } ]
screenshot = [ { view = "top" } ]
contact_sheet = { frames = 2 }

[[step]]
name = "plain"
mode = "auto"
duration = 1.0
'''


def test_ops_on_a_step_next_to_inline_array_forms():
    out = run(INLINE_FORMS, lambda d: se.add_check(d, 1, "console", {"contains": "b"}),
              lambda d: se.add_shot(d, 1, {"view": "top"}), lambda d: se.set_sheet(d, 1, {"frames": 3}))
    s = tomllib.loads(out)["step"][1]
    assert s["check"] == [{"console": {"contains": "b"}}] and s["screenshot"] == [{"view": "top"}]
    assert s["contact_sheet"] == {"frames": 3}
    d = tomlkit.parse(INLINE_FORMS)
    for fn, field in ((lambda: se.add_check(d, 0, "console", {}), "step0/check"),
                      (lambda: se.add_shot(d, 0, {}), "step0/screenshot"),
                      (lambda: se.delete_check(d, 0, 0), "step0/check")):
        with pytest.raises(EditError) as e:
            fn()
        assert e.value.field == field and "step 1 'inl'" in e.value.message
    with pytest.raises(EditError) as e:
        se.set_sheet(d, 0, {"frames": 3})
    assert e.value.field == "step0/sheet"


# ---- input tracks -----------------------------------------------------------------------------------------

INPUT_STEP = '''config = "../configs/kitbot.toml"

[[step]]
name = "drive"  # the driving step
mode = "teleop"
duration = 4.0   # seconds
axes = { 0 = [0.5, 0.0] }
buttons = { 0 = [true] }
povs = { 0 = [90] }
  # check one
  [[step.check]]
  console = { contains = "x" }
'''


def _track_samples():
    from scrimmage.tracks import PortInputs, Sample
    return [Sample(float(t), {0: PortInputs([t / 10, -t / 10], [t % 2 == 1], [t * 10 if t < 4 else -1])})
            for t in range(5)]


def _tracked(tmp_path, name="a.csv", duration=4.0):
    from scrimmage.tracks import write_track
    write_track(tmp_path / name, _track_samples())
    p = tmp_path / "s.toml"
    text = (f'config = "c.toml"\n\n[[step]]\nname = "d"  # c\nmode = "teleop"\nduration = {duration}\n'
            f'inputs = "{name}"  # t\n')
    p.write_text(text)
    return p, tomlkit.parse(text)


def test_set_inputs_file_replaces_joystick_keys_and_keeps_comments():
    d = tomlkit.parse(INPUT_STEP)
    se.set_inputs_file(d, 0, "drive.csv")
    out = tomlkit.dumps(d)
    raw = tomllib.loads(out)["step"][0]
    assert raw["inputs"] == "drive.csv" and not {"axes", "buttons", "povs"} & set(raw)
    assert "# the driving step" in out and "# seconds" in out and "# check one" in out
    assert out.index("duration") < out.index("inputs") < out.index("[[step.check]]")
    se.set_inputs_file(d, 0, "other.csv")
    assert tomllib.loads(tomlkit.dumps(d))["step"][0]["inputs"] == "other.csv"
    se.set_inputs_file(d, 0, None)
    out = tomlkit.dumps(d)
    assert "inputs" not in out and "# the driving step" in out and "# seconds" in out
    se.set_inputs_file(d, 0, None)   # nothing there: fine
    with pytest.raises(EditError):
        se.set_inputs_file(d, 0, 5)
    with pytest.raises(EditError):
        se.set_inputs_file(d, 3, "x.csv")


def test_trim_track(tmp_path):
    from scrimmage.tracks import load_track
    p, d = _tracked(tmp_path)
    se.trim_track(d, 0, 1.5, 3.0, p)
    step = tomllib.loads(tomlkit.dumps(d))["step"][0]
    assert step["inputs"] == "a.trim1.csv" and step["duration"] == 1.5
    t = load_track(tmp_path / "a.trim1.csv")
    assert [s.t for s in t.samples] == [0.0, 0.5, 1.5]   # 0 = state in effect at 1.5 (sample t=1), then 2, 3
    assert [s.ports[0].axes[0] for s in t.samples] == [0.1, 0.2, 0.3]
    assert (tmp_path / "a.csv").exists()
    # the second trim gets the next free name, and trims relative to its own track
    se.trim_track(d, 0, 0.0, 1.0, p)
    assert tomllib.loads(tomlkit.dumps(d))["step"][0]["inputs"] == "a.trim1.trim1.csv"
    p2, d2 = _tracked(tmp_path)
    se.trim_track(d2, 0, 0.0, 2.0, p2)
    assert tomllib.loads(tomlkit.dumps(d2))["step"][0]["inputs"] == "a.trim2.csv"
    assert "# c" in tomlkit.dumps(d2) and "# t" in tomlkit.dumps(d2)
    assert not list(tmp_path.glob("*.tmp"))


def test_trim_track_keeps_a_row_at_start_exactly(tmp_path):
    from scrimmage.tracks import load_track
    p, d = _tracked(tmp_path)
    se.trim_track(d, 0, 2.0, 4.0, p)
    t = load_track(tmp_path / "a.trim1.csv")
    assert [s.t for s in t.samples] == [0.0, 1.0, 2.0] and t.samples[0].ports[0].axes == [0.2, -0.2]


def test_trim_track_in_a_subfolder(tmp_path):
    from scrimmage.tracks import write_track
    (tmp_path / "tr").mkdir()
    write_track(tmp_path / "tr" / "a.csv", _track_samples())
    p = tmp_path / "s.toml"
    d = tomlkit.parse('config = "c.toml"\n[[step]]\nmode = "teleop"\nduration = 4.0\ninputs = "tr/a.csv"\n')
    se.trim_track(d, 0, 0.0, 2.0, p)
    assert tomllib.loads(tomlkit.dumps(d))["step"][0]["inputs"] == "tr/a.trim1.csv"
    assert (tmp_path / "tr" / "a.trim1.csv").exists()


@pytest.mark.parametrize("start,end", [(-1, 2), (2, 2), (3, 2), (0, 4.5), (0, 0)])
def test_trim_track_range_errors(tmp_path, start, end):
    p, d = _tracked(tmp_path)
    with pytest.raises(EditError) as e:
        se.trim_track(d, 0, start, end, p)
    assert e.value.field == "step0/inputs"
    assert not list(tmp_path.glob("*.trim*"))


def test_trim_track_needs_inputs_and_a_loadable_track(tmp_path):
    p = tmp_path / "s.toml"
    d = tomlkit.parse('config = "c.toml"\n[[step]]\nmode = "auto"\nduration = 4.0\n')
    with pytest.raises(EditError) as e:
        se.trim_track(d, 0, 0, 1, p)
    assert e.value.field == "step0/inputs"
    d = tomlkit.parse('config = "c.toml"\n[[step]]\nmode = "auto"\nduration = 4.0\ninputs = "nope.csv"\n')
    with pytest.raises(EditError) as e:
        se.trim_track(d, 0, 0, 1, p)
    assert e.value.field == "step0/inputs"
    with pytest.raises(EditError):
        se.trim_track(d, 2, 0, 1, p)
