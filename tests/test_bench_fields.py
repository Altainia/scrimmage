"""The bench's field table: plain-word help, units and choices for every key."""

from pathlib import Path

from scrimmage.bench.fields import FIELDS, choices, fields_payload

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_every_parser_key_has_help():
    from scrimmage.bench.spec import _SECTIONS
    for section, (keys, _) in _SECTIONS.items():
        for k in keys:
            f = FIELDS[f"{section}.{k}"]
            assert f.help and len(f.help) <= 120


def test_lengths_match_parser():
    from scrimmage.bench.spec import _SECTIONS
    for section, (_, lengths) in _SECTIONS.items():
        assert {k for k in _SECTIONS[section][0] if FIELDS[f"{section}.{k}"].length} == set(lengths)


def test_no_field_the_parser_lacks():
    from scrimmage.bench.spec import _SECTIONS
    assert set(FIELDS) == {f"{s}.{k}" for s, (keys, _) in _SECTIONS.items() for k in keys}


def test_every_field_is_in_the_docs():
    text = (DOCS / "bench.md").read_text()
    for dotted, f in FIELDS.items():
        assert f"`{f.key}`" in text, dotted


def test_choices():
    assert "2026-fuel" in choices("pieces", {})
    assert "40A" in choices("treads", {})
    assert "KrakenX60" in choices("motors", {})
    assert choices("shafts", {"shaft": {"top": {}, "bottom": {}}}) == ["top", "bottom"]
    assert choices("outlets", {"outlet": [{"name": "shot"}, {"name": "drop"}]}) == ["shot", "drop"]
    assert choices("controls", {}) == ["speed", "voltage", "free"]
    assert choices("units", {}) == ["m", "mm", "in"]
    assert "polycarbonate" in choices("materials", {})
    assert FIELDS["wheel.tread"].choices == "treads" and FIELDS["wheel.shaft"].choices == "shafts"


def test_payload_leaves_out_raw_dependent_choices():
    p = fields_payload()
    assert p["fields"]["wheel.diameter"]["unit"] == "m" and p["fields"]["wheel.diameter"]["length"] is True
    assert set(p["choices"]) == {"pieces", "materials", "treads", "motors", "controls", "units"}
