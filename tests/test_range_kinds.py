"""Several piece kinds on one field: `[piece.<name>]` tables, `kind` on floor pieces, stations and goals."""

import tomllib
from pathlib import Path

import pytest

from scrimmage.testrange.spec import BUILTIN_RANGES, RangeError, load_range, parse_range

ROOT = Path(__file__).resolve().parents[1]

MIXED = '''
[range]
name = "mixed"
ball = "2025-algae"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[3.0, 2.0]]

[[floor_balls]]
at = [[5.0, 2.0]]
kind = "coral"
tip_deg = 90
'''
CORAL_ONLY = '''
[range]
name = "reef"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[3.0, 2.0]]
'''
GOAL = '''
[[goal]]
name = "reef"
pos = [8.0, 4.0]
rim_height = 2.0
opening = { diameter = 1.0 }
'''
FEEDER = '''
[[station]]
name = "feeder"
release = [1.0, 4.0, 0.5]
heading_deg = 0.0
kind = "coral"
'''
TWO_KINDS = '''
[range]
name = "two"

[piece.coral]
preset = "2025-coral"

[piece.2025-algae]
ball = "2025-algae"
'''


def parse(tmp_path, text):
    return parse_range(tomllib.loads(text), tmp_path / "r.toml")


def test_ball_shorthand_is_one_ball_kind():
    rng = load_range("intake", ROOT)
    assert list(rng.kinds) == ["2026-fuel"] and rng.kinds["2026-fuel"].form == "ball"
    assert rng.floor_kinds == ("2026-fuel",) * len(rng.floor_balls) and rng.ball is not None
    assert rng.default_kind == "2026-fuel" and rng.piece is rng.ball and rng.piece_preset == "2026-fuel"


def test_a_mixed_field(tmp_path):
    rng = parse(tmp_path, MIXED)
    assert list(rng.kinds) == ["2025-algae", "coral"] and rng.floor_kinds == ("2025-algae", "coral")
    assert rng.floor_turns[1] == pytest.approx([90.0, 0.0])
    assert rng.floor_turns[0] == pytest.approx([0.0, 0.0])
    assert rng.default_kind == "2025-algae" and rng.ball_preset == "2025-algae" and rng.disc is None


def test_a_field_of_only_coral(tmp_path):
    rng = parse(tmp_path, CORAL_ONLY)
    assert rng.ball is None and rng.disc is None and rng.ball_preset is None
    assert rng.piece.name == "coral" and rng.default_kind == "coral" and rng.piece_preset == "2025-coral"
    assert rng.floor_kinds == ("coral",)


def test_range_piece_names_a_piece_or_a_ball_preset(tmp_path):
    rng = parse(tmp_path, '[range]\nname = "p"\npiece = "2025-coral"\n')
    assert list(rng.kinds) == ["2025-coral"] and rng.kinds["2025-coral"].form == "rigid"
    rng = parse(tmp_path, '[range]\nname = "p"\npiece = "2019-cargo"\n')
    assert rng.ball_preset == "2019-cargo" and rng.ball.diameter == pytest.approx(0.3302)
    with pytest.raises(RangeError, match=r"\[range\] piece: unknown preset 'rock'; known ball presets: .*2026-fuel"
                                         r".*; known piece presets: .*2025-coral"):
        parse(tmp_path, '[range]\nname = "p"\npiece = "rock"\n')


def test_a_goal_counts_a_named_ball_and_refuses_coral(tmp_path):
    rng = parse(tmp_path, MIXED + GOAL + 'kind = "2025-algae"\n')
    assert rng.goals[0].kind == "2025-algae"
    assert parse(tmp_path, MIXED + GOAL).goals[0].kind is None
    with pytest.raises(RangeError, match=r"r\.toml: \[\[goal\]\] 'reef': goals count balls and discs; 'coral' is a"
                                         " rigid piece"):
        parse(tmp_path, MIXED + GOAL + 'kind = "coral"\n')
    with pytest.raises(RangeError, match=r"\[\[goal\]\] 'reef': goals count balls and discs; 'coral' is a rigid"):
        parse(tmp_path, CORAL_ONLY + GOAL)


def test_floor_pieces_are_spaced_by_their_reach(tmp_path):
    def two(gap):
        return CORAL_ONLY.replace("at = [[3.0, 2.0]]", f"at = [[3.0, 2.0], [3.0, {2.0 + gap}]]\nrotation_deg = 90")
    with pytest.raises(RangeError, match=r"floor_balls: coral 1 and coral 2 overlap"):
        parse(tmp_path, two(0.2))
    rng = parse(tmp_path, two(0.35))
    assert rng.floor_turns.tolist() == [[0.0, 90.0], [0.0, 90.0]]


def test_a_coral_off_the_field_by_its_reach(tmp_path):
    # lying in line along x, a coral reaches 0.161 m from its center: 0.1 m from the wall is off the field
    with pytest.raises(RangeError, match=r"floor_balls: coral 1 at \(0\.1, 2\) lies off the field"):
        parse(tmp_path, CORAL_ONLY.replace("[3.0, 2.0]", "[0.1, 2.0]"))


def test_piece_and_piece_preset_name_the_same_kind(tmp_path):
    # a rigid default with a ball kind after it: piece is the ball, and so is piece_preset (ruling P12)
    rng = parse(tmp_path, CORAL_ONLY + '[piece.algae]\nball = "2025-algae"\n')
    assert rng.default_kind == "coral" and rng.piece is rng.ball and rng.piece_preset == "2025-algae"
    disc = parse(tmp_path, CORAL_ONLY + '[piece.frisbee]\ndisc = "2013-frisbee"\n')
    assert disc.piece is disc.disc and disc.piece_preset == "2013-frisbee"


def test_a_station_feeds_its_kind(tmp_path):
    rng = parse(tmp_path, MIXED + FEEDER)
    st = rng.stations[0]
    assert st.kind == "coral" and st.kind_of(rng).name == "coral"
    assert (st.tip_deg, st.turn_deg, st.spread_tip_deg, st.spread_turn_deg) == (0.0, 0.0, 0.0, 0.0)
    rng = parse(tmp_path, MIXED + FEEDER.replace('kind = "coral"', ""))
    assert rng.stations[0].kind is None and rng.stations[0].kind_of(rng).name == "2025-algae"
    rng = parse(tmp_path, MIXED + FEEDER + "tip_deg = 45\nturn_deg = 90\nspread_tip_deg = 10\nspread_turn_deg = 30\n")
    st = rng.stations[0]
    assert (st.tip_deg, st.turn_deg, st.spread_tip_deg, st.spread_turn_deg) == (45.0, 90.0, 10.0, 30.0)
    with pytest.raises(RangeError, match="spread_tip_deg must be 0 or more"):
        parse(tmp_path, MIXED + FEEDER + "spread_tip_deg = -1\n")
    with pytest.raises(RangeError, match="tip_deg must be from -90 to 90"):
        parse(tmp_path, MIXED + FEEDER + "tip_deg = 120\n")


def test_a_station_on_a_disc_kind_is_refused(tmp_path):
    text = MIXED + '[piece.frisbee]\ndisc = "2013-frisbee"\n' + FEEDER.replace("coral", "frisbee")
    with pytest.raises(RangeError, match="'feeder': stations feed balls; this range's piece is a disc"):
        parse(tmp_path, text)


@pytest.mark.parametrize("extra, where", [
    ('[[floor_balls]]\nat = [[3.0, 2.0]]\nkind = "coal"\n', r"\[\[floor_balls\]\] #1"),
    (GOAL + 'kind = "coal"\n', r"\[\[goal\]\] 'reef'"),
    (FEEDER.replace('"coral"', '"coal"'), r"\[\[station\]\] 'feeder'"),
])
def test_unknown_kinds_name_the_fields_pieces(tmp_path, extra, where):
    with pytest.raises(RangeError, match=rf"r\.toml: {where}: kind 'coal' isn't one of this field's pieces: coral,"
                                         " 2025-algae$"):
        parse(tmp_path, TWO_KINDS + extra)


def test_a_range_without_a_piece_or_with_two_is_refused(tmp_path):
    with pytest.raises(RangeError, match=r"\[range\] is missing its game piece: give ball = \"<ball preset>\", piece ="
                                         r" \"<piece preset>\" or a \[piece.<name>\] table"):
        parse(tmp_path, '[range]\nname = "none"\n')
    with pytest.raises(RangeError, match=r"\[range\] has both ball and piece; give ball or piece, not both"):
        parse(tmp_path, '[range]\nname = "two"\nball = "2026-fuel"\npiece = "2025-coral"\n')
    with pytest.raises(RangeError, match=r"\[piece.2025-algae\] has the same name as \[range\] ball"):
        parse(tmp_path, MIXED + '[piece.2025-algae]\nball = "2025-algae"\n')


def test_a_piece_override():
    rng = load_range("station", ROOT, piece="2025-coral")
    assert rng.piece.name == "2025-coral" and list(rng.kinds) == ["2025-coral"] and rng.ball is None
    assert load_range("station", ROOT, piece="2019-cargo").ball_preset == "2019-cargo"
    with pytest.raises(RangeError, match="give ball or piece, not both"):
        load_range("station", ROOT, ball="2019-cargo", piece="2025-coral")


def test_a_piece_override_replaces_a_default_from_a_piece_table(tmp_path):
    (tmp_path / "reef.toml").write_text(CORAL_ONLY.replace("[3.0, 2.0]]", '[3.0, 2.0]]\nkind = "coral"'))
    rng = load_range("reef.toml", tmp_path, piece="2019-cargo")
    assert list(rng.kinds) == ["coral"] and rng.kinds["coral"].form == "ball"
    assert rng.ball_preset == "2019-cargo" and rng.floor_kinds == ("coral",)
    rng = load_range("reef.toml", tmp_path, piece="2023-cube")
    assert list(rng.kinds) == ["coral"] and rng.piece_preset == "2023-cube" and rng.kinds["coral"].label == "cube"


def test_every_builtin_range_is_unchanged():
    for name in BUILTIN_RANGES:
        if name == "reef":      # new with pieces beyond balls: a coral range from the start
            continue
        r = load_range(name, ROOT)
        assert len(r.kinds) == 1 and all(g.kind is None for g in r.goals)
        n = len(r.floor_balls)
        assert r.floor_kinds == (r.default_kind,) * n and r.floor_turns.shape == (n, 2)
        assert r.piece_preset == (r.disc_preset if r.disc is not None else r.ball_preset)
