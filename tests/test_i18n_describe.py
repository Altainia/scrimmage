"""describe()'s forms: "without" and "waiting" are whole phrases a translator can translate, and in American English
they read exactly as the old rule (run._recast, the verb recast in the plain sentence) made them."""

import json

import pytest

from scrimmage import i18n
from scrimmage.blocks.conditions import describe
from scrimmage.blocks.palette import ANY_BAR, ANY_GOAL, ANY_HOOK, ANY_PIECE, NEXT_BAR
from scrimmage.blocks.program import Cond
from scrimmage.blocks.run import _recast

KINDS = None          # any piece reads "a piece"
CONDITIONS = [
    Cond("touch", {"robot": ANY_HOOK, "field": ANY_BAR}),
    Cond("touch", {"robot": "arm", "field": NEXT_BAR}),
    Cond("touch", {"robot": "arm", "field": "low_bar"}),
    Cond("joint", {"name": "arm.pivot", "above": 40.0, "unit": "deg"}),
    Cond("joint", {"name": "lift.slide", "below": 2.0, "unit": "in"}),
    Cond("tilt", {"above": 10.0}),
    Cond("tilt", {"below": 5.0}),
    Cond("piece_in", {"piece": ANY_PIECE, "body": "hopper"}),
    Cond("piece_in", {"piece": "ball.0", "body": "hopper"}),
    Cond("in_hopper", {"at_least": 2}),
    Cond("in_hopper", {"at_most": 1}),
    Cond("scored", {"goal": ANY_GOAL, "at_least": 3}),
    Cond("scored", {"goal": "high", "at_least": 1}),
    Cond("aimed", {"turret": "turret", "at": "high", "within_deg": 2.0}),
    Cond("aimed", {"turret": "top", "at": "high", "within_deg": 2.0}),
    Cond("cylinder", {"cylinder": "cyl", "is": "extended"}),
    Cond("cylinder", {"cylinder": "cyl", "is": "retracted"}),
    *(Cond("pressure", {"side": side, how: 60.0}) for side in ("stored", "working") for how in ("above", "below")),
    Cond("element", {"element": "ramp", "above": 3.0}),
    Cond("element", {"element": "ramp", "below": 3.0}),
    Cond("clears", {"element": "trench", "at_least_in": 1.0}),
    Cond("sensor", {"sensor": "eye", "is": "on", "sees": "red"}),
    Cond("sensor", {"sensor": "eye", "is": "off", "sees": "red"}),
    Cond("sensor", {"sensor": "beam", "is": "on"}),
    Cond("sensor", {"sensor": "beam", "is": "off"}),
    Cond("sees_tag", {"camera": "front_cam", "tag": 7}),
    Cond("sees_tag", {"camera": "front_cam", "tag": None}),
    Cond("partner_lifted", {"at_least_in": 4.0, "level_within_deg": 5.0}),
    Cond("partner_lifted", {"at_least_in": 4.0}),
    Cond("partner_moved", {"at_least_in": 2.0}),
    Cond("station_empty", {"station": "feeder"}),
    Cond("made_it_in", {"at_least": 80.0}),
    Cond("hanging_from", {"bar": NEXT_BAR}),
    Cond("level", {"level": "high"}),
    Cond("held", {"piece": ANY_PIECE, "by": "claw"}),
    Cond("rests_on", {"piece": ANY_PIECE, "on": "shelf"}),
    Cond("seated", {"piece": ANY_PIECE, "on": "branch", "at_least_in": 3.0}),
    Cond("seated", {"piece": ANY_PIECE, "on": "branch", "at_least_in": 0}),
]


@pytest.mark.parametrize("cond", CONDITIONS, ids=lambda c: c.kind)
def test_each_form_reads_as_the_old_rule_in_american_english(cond):
    plain = describe(cond, kinds=KINDS, names={"ball.0": "ball 1"})
    assert describe(cond, kinds=KINDS, names={"ball.0": "ball 1"}, form="without") == _recast(plain, 0)
    assert describe(cond, kinds=KINDS, names={"ball.0": "ball 1"}, form="waiting") == _recast(plain, 1)


def test_a_form_is_its_own_phrase_for_a_translator(tmp_path, monkeypatch, language):
    folder = tmp_path / "locales"
    folder.mkdir()
    (folder / "en-GB.json").write_text(json.dumps({"_language": "English (UK)",
                                                   "{joint} to pass {limit}": "ZZ {limit} {joint}"}),
                                       encoding="utf-8")
    shipped = i18n.LOCALES
    monkeypatch.setattr(i18n, "LOCALES", folder)
    i18n.reload()
    language("en-GB")
    try:
        cond = Cond("joint", {"name": "arm.pivot", "above": 40.0, "unit": "deg"})
        assert describe(cond, form="waiting").startswith("ZZ ")
        assert describe(cond, form="waiting").endswith(" arm.pivot")
    finally:
        monkeypatch.setattr(i18n, "LOCALES", shipped)
        i18n.reload()
