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
    assert choose_season({"inspection": {"season": 2022}, "range": {"file": "2024"}})[0].year == 2022
    assert choose_season({"inspection": {"season": 2022}}, override=2016) == (SEASONS[2016], "--season")
def test_bad_inspection_sections_are_config_errors():
    for bad in ({"season": 2019}, {"season": "2026"}, {"battery_mass": -1}, {"weight": 3}, {"bumper_mass": True}):
        with pytest.raises(ConfigError):
            settings({"inspection": bad})
    assert settings({}).present is False and settings({"inspection": {}}).present is True
