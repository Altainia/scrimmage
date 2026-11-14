"""The inspection season table and choosing a season (design Calls I1 to I3)."""
import pytest

from scrimmage.config import ConfigError
from scrimmage.inspection.spec import SEASONS, choose_season, settings

LB, IN = 0.45359237, 0.0254


def test_every_season_matches_the_manuals():
    want = {  # year: (weight lb, perimeter in, start height in, match height in or None, reach in or None)
        2013: (120, 112, 84, 60, None), 2016: (120, 120, 54, 54, 15), 2020: (125, 120, 45, 45, 12),
        2022: (125, 120, 52, 52, 16), 2023: (125, 120, 54, 78, 48), 2024: (125.5, 120, 48, 48, 12),
        2025: (115, 120, 42, None, 18), 2026: (115, 110, 30, 30, 12)}
    assert sorted(SEASONS) == sorted(want)
    for year, (w, p, sh, h, r) in want.items():
        s = SEASONS[year]
        assert s.weight.value == pytest.approx(w * LB) and s.perimeter.value == pytest.approx(p * IN)
        assert s.start_height.value == pytest.approx(sh * IN)
        assert (s.height.value is None) == (h is None) and (h is None or s.height.value == pytest.approx(h * IN))
        assert (s.reach.value is None) == (r is None) and (r is None or s.reach.value == pytest.approx(r * IN))
        assert s.weight.rule and s.perimeter.rule and s.url.startswith("https://firstfrc.blob.core.windows.net/")


RULES = {  # year: (weight, weight with bumpers, bumper set, perimeter, start reach, start height, height, reach,
           # diameter); "" where the season has no such limit. From the manuals (sources file), read 2026-11-14.
    2013: ("R05", "", "R23", "R03-A", "R04", "R03-C", "G22", "", "R03-B, G23"),
    2016: ("R5", "", "R20", "R3-A", "R4", "R3-C", "G17", "G18", ""),
    2020: ("R5", "", "R23", "R3", "R2", "R3", "G17", "G18", ""),
    2022: ("R103", "", "R407", "R104", "R102", "R104", "G106", "G107", ""),
    2023: ("R103", "", "R407", "R104", "R102", "R104", "G106", "G107", ""),
    2024: ("R103", "", "R407", "R104", "R102", "R104", "G413-A", "G413-B", ""),
    2025: ("R103", "R408", "", "R104", "R102", "R104", "", "G415", ""),
    2026: ("R103", "R408", "", "R104", "R102", "R104", "R107", "R105", ""),
}
KEYS = ("weight", "weight_with_bumpers", "bumper_set", "perimeter", "start_reach", "start_height", "height", "reach",
        "diameter")


def test_every_season_s_rule_numbers():
    assert sorted(RULES) == sorted(SEASONS)
    for year, rules in RULES.items():
        got = tuple(getattr(SEASONS[year], key).rule for key in KEYS)
        assert got == rules, year
        for key, rule in zip(KEYS, rules):
            assert (getattr(SEASONS[year], key).value is None) == (rule == ""), (year, key)
        assert SEASONS[year].start_reach.value == 0.0                 # no overhang at the start, in every season


def test_bumpers_and_the_cylinder():
    assert SEASONS[2013].diameter.value == pytest.approx(54 * IN) and SEASONS[2016].diameter.value is None
    assert SEASONS[2013].bumper_set.value == pytest.approx(20 * LB) and SEASONS[2024].bumper_set.value == pytest.approx(15 * LB)
    assert SEASONS[2026].weight_with_bumpers.value == pytest.approx(135 * LB) and SEASONS[2024].weight_with_bumpers.value is None
    assert "hangar" in SEASONS[2022].height.unless and SEASONS[2023].not_checked
def test_choose_season():
    assert choose_season({})[0].year == 2026 and choose_season({})[1] == "the newest season"
    assert choose_season({"range": {"file": "2024"}})[0].year == 2024
    assert choose_season({"range": {"file": "2013-pyramid"}})[0].year == 2013
    assert choose_season({"range": {"file": "reef"}})[0].year == 2026          # no year in its name: newest
    assert choose_season({"range": {"file": "20245-x"}})[0].year == 2026       # five digits: not 2024
    assert choose_season({"range": {"file": "2024x"}})[0].year == 2024
    assert choose_season({"inspection": {"season": 2022}, "range": {"file": "2024"}})[0].year == 2022
    assert choose_season({"inspection": {"season": 2022}}, override=2016) == (SEASONS[2016], "--season")
def test_bad_inspection_sections_are_config_errors():
    for bad in ({"season": 2019}, {"season": "2026"}, {"battery_mass": -1}, {"weight": 3}, {"bumper_mass": True}):
        with pytest.raises(ConfigError):
            settings({"inspection": bad})
    assert settings({}).present is False and settings({"inspection": {}}).present is True


def test_a_malformed_season_table_names_the_season_and_the_limit():
    from scrimmage.inspection.spec import _parse
    broken = '[2030]\nmanual = "m"\nurl = "u"\n[2030.weight]\nrule = "R1"\n'
    with pytest.raises(ValueError, match=r"season 2030, limit weight"):
        _parse(broken)
    no_rule = '[2031]\nmanual = "m"\nurl = "u"\n[2031.perimeter]\nmax_in = 100\n'
    with pytest.raises(ValueError, match=r"season 2031, limit perimeter"):
        _parse(no_rule)
    good = '[2032]\nmanual = "m"\nurl = "u"\n[2032.perimeter]\nmax_in = 100\nrule = "R1"\n'
    assert _parse(good)[2032].perimeter.rule == "R1"


def test_an_unknown_season_override_lists_the_known_ones():
    from scrimmage.inspection.spec import check_override
    with pytest.raises(ConfigError, match=r"^season 2019: scrimmage knows the seasons 2013, 2016"):
        check_override(2019, "season")
    check_override(2024, "season")
