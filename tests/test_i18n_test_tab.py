"""The Test tab's server words in another language (sub-project 20b, Task 4): a run's outcome and check lines,
describe(), the test file's refusals (a refused save sends the block's place as a code, spec L3) and the state's
problems follow the Language setting; machine text (MCP test_run, test_sweep and test_spread, and their table.csv)
stays byte for byte main's American English whatever it is (spec L2, Review Focus 5).

Most use a catalog of their own (the `locales` fixture), so they can change sentences en-GB spells the same; one uses
the shipped en-GB catalog."""
import json

import pytest

from scrimmage import i18n
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.program import Cond, TestFileError, from_json, loads
from scrimmage.blocks.run import TestRun, world_for
from test_blocks_run import write_test

# en-GB, made up for these tests: each source's words changed (and moved), its placeholders kept
GB = {
    "_language": "English (UK)",
    "block {n}": "block no. {n}",
    "a check failed ({where}): {failure} at {t} s": "a check didn't pass ({where}): {failure}, at {t} s",
    "expected the robot to tilt more than {limit}, but it tilted {tilted}":
        "the robot should have tilted more than {limit}; it tilted {tilted}",
    "passed: {check} at {t} s ({where})": "passed at {t} s ({where}): {check}",
    "the robot tilts less than {limit}": "the robot leans under {limit}",
    "{cylinder} is extended": "{cylinder} is out",
    "{what} can't be negative": "{what} mustn't be below nought",
    "the stored air pressure is above {limit}": "the stored air is over {limit}",
    "expected the stored air pressure above {limit}, but it was {now}": "wanted stored air over {limit}; it was {now}",
    "drive ({where}) didn't reach {far} in {timeout} s (went {went})":
        "the drive ({where}) fell short of {far} in {timeout} s (it went {went})",
    "the test can't run on this robot yet:": "this test can't run on this robot yet:",
    "the robot isn't loaded": "the robot hasn't loaded",
    "{made} of {fed} made it in ({percent}%)": "{made} of {fed} got in ({percent} per cent)",
}

EXPECT_TILT = """
[[block]]
do = "expect"
that = { tilt = { below = 45.0 } }

[[block]]
do = "expect"
that = { tilt = { above = 45.0 } }
"""


@pytest.fixture
def gb(tmp_path, monkeypatch, language):
    """The Language setting en-GB, read from a scratch locales folder holding GB (test_i18n.py's `locales`)."""
    folder = tmp_path / "locales"
    folder.mkdir()
    (folder / "en-GB.json").write_text(json.dumps(GB, ensure_ascii=False), encoding="utf-8")
    shipped = i18n.LOCALES
    monkeypatch.setattr(i18n, "LOCALES", folder)
    i18n.reload()
    language("en-GB")
    yield
    monkeypatch.setattr(i18n, "LOCALES", shipped)
    i18n.reload()


def _run(tmp_path, blocks):
    path = write_test(tmp_path, blocks)
    program = loads(path.read_text(encoding="utf-8"), path)
    return TestRun(world_for(program), program).run()


def test_a_runs_outcome_and_checks_are_in_the_language(tmp_path, gb):
    result = _run(tmp_path, EXPECT_TILT)
    assert result.status == "check failed"
    # the failure's whole sentence, its parts moved where the translation puts them
    assert result.outcome.startswith("a check didn't pass (block no. 2): the robot should have tilted more than 45°; "
                                     "it tilted "), result.outcome
    assert result.checks[0]["line"].startswith("passed at "), result.checks[0]["line"]
    assert result.checks[0]["line"].endswith(" (block no. 1): the robot leans under 45°")


def test_the_same_run_in_american_english(tmp_path):
    result = _run(tmp_path, EXPECT_TILT)
    assert result.outcome.startswith("a check failed (block 2): expected the robot to tilt more than 45°, but it "
                                     "tilted "), result.outcome
    assert result.checks[0]["line"].startswith("passed: the robot tilts less than 45° at ")


def test_describe_follows_the_language(gb):
    assert describe(Cond("cylinder", {"cylinder": "lift", "is": "extended"})) == "lift is out"
    assert describe(Cond("pressure", {"side": "stored", "above": 60})) == "the stored air is over 60 psi"
    with i18n.fixed():
        assert describe(Cond("cylinder", {"cylinder": "lift", "is": "extended"})) == "lift is extended"


def test_a_bad_block_names_its_place_as_a_code(tmp_path, gb):
    """L3: the Test tab reads a refused block's place from the error, not from its words."""
    test = {"robot": "robot.toml", "blocks": [{"do": "wait", "seconds": 1},
                                             {"do": "repeat", "times": 2, "body": [{"do": "wait", "seconds": -1}]}]}
    with pytest.raises(TestFileError) as e:
        from_json(test, tmp_path / "t.test.toml")
    assert str(e.value) == "block no. 1 inside block 2 (repeat): seconds mustn't be below nought"
    assert e.value.place == [1, 0] and e.value.problem == "seconds mustn't be below nought"
    with pytest.raises(TestFileError) as e:          # not a block's: no place
        from_json({"robot": "robot.toml", "blocks": [], "time_limit": 0.1}, tmp_path / "t.test.toml")
    assert e.value.place is None and e.value.problem is None


def test_a_refused_save_sends_the_place_and_the_state_follows_the_language(tmp_path, gb, language):
    from scrimmage.assembler.server import App
    from test_assembler_server import Client
    from test_test_server import write_robot
    write_robot(tmp_path, arm=False)          # the test's spin names a joint this robot doesn't have
    path = tmp_path / "t.test.toml"
    path.write_text('[test]\nrobot = "robot.toml"\n\n[[block]]\ndo = "spin"\nmotor = "arm.pivot"\nvolts = 6.0\n',
                    encoding="utf-8")
    app = App(path, open_browser=False)
    try:
        c = Client(app, app.start())
        s, st = c.get("/api/test/state")
        assert s == 200
        bad = {**st["test"], "blocks": [{"do": "wait", "seconds": -1}]}
        s, b = c.call("POST", "/api/test/save", {"test": bad, "version": st["version"]})
        assert s == 400 and b["errors"][0]["place"] == [0], b
        assert b["errors"][0]["problem"] == "seconds mustn't be below nought"
        assert b["error"] == "block no. 1: seconds mustn't be below nought"
        language("en-US")                     # the state's problems are made again in the new words
        _, st = c.get("/api/test/state")
        assert st["problems"][0]["message"] == 'no joint "arm.pivot" on this robot any more', st["problems"]
    finally:
        app.stop()


def test_the_shipped_en_gb_catalog_spells_a_problem_the_british_way(language):
    from scrimmage.blocks.palette import _unit_problem
    assert _unit_problem("lift", "slide", "deg") == '"lift" slides, so give it in inches or meters, not degrees'
    language("en-GB")
    assert _unit_problem("lift", "slide", "deg") == '"lift" slides, so give it in inches or metres, not degrees'


# ---- machine text: byte for byte main's, in en-GB too ---------------------------------------------------------------
def test_mcp_test_run_sweep_and_spread_are_mains_in_en_gb(tmp_path, gb):
    """Review Focus 5: test_units_machine_text's cases (MCP test_run's outcome, check, air and clearance lines and
    refusals; a sweep's and a spread's table.csv and sentence) with the room in en-GB, whose catalog changes the words
    of many of them: each is what main wrote."""
    pytest.importorskip("mcp")
    from test_units_machine_text import MAIN, machine_text
    assert i18n.t("block {n}", n=1) == "block no. 1"      # the setting is en-GB here
    got = machine_text(tmp_path)
    for name in MAIN:
        assert got[name] == MAIN[name], name


def test_an_mcp_refusal_from_the_file_is_american_english_in_en_gb(tmp_path, gb):
    """A bad test file through MCP test_run and test_sweep (its load is inside the guard too)."""
    import asyncio
    pytest.importorskip("mcp")
    from scrimmage import mcp_server
    path = write_test(tmp_path, '[[block]]\ndo = "wait"\nseconds = -1.0\n')
    for call in (mcp_server.test_run(str(path)),
                 mcp_server.test_sweep(str(path), {"block": [0], "key": "seconds"}, [1.0])):
        with pytest.raises(mcp_server.ToolError) as e:
            asyncio.run(call)
        assert str(e.value) == "block 1: seconds can't be negative"


def test_the_cli_prints_american_english_in_en_gb(tmp_path, gb, capsys, monkeypatch):
    """The ruling: `scrimmage test run` prints the CLI's words, run.py's outcome and check lines among them, in
    American English whatever the Language setting (units still follow it)."""
    from test_blocks_run import _cli
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    assert i18n.t("block {n}", n=1) == "block no. 1"
    assert _cli(["test", "run", str(write_test(tmp_path, EXPECT_TILT))]) == 1
    out = capsys.readouterr().out
    assert "a check failed (block 2): expected the robot to tilt more than 45°" in out, out
    assert "block no." not in out



def test_the_robot_isnt_loaded_problem_follows_the_language(tmp_path, gb, language):
    """With no robot world (it didn't load), the state's fallback problem is made again after a switch too."""
    from scrimmage.assembler.test_session import TestSession
    session = TestSession(write_test(tmp_path, EXPECT_TILT), simlock=None)
    assert session.state()["problems"][0]["message"] == "the robot hasn't loaded"
    language("en-US")
    assert session.state()["problems"][0]["message"] == "the robot isn't loaded"
