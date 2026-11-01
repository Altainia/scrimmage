"""Every problem in a bench file, each with the dotted key of its field; overlap warnings for moving parts."""

from pathlib import Path

import pytest

from scrimmage.__main__ import main
from scrimmage.bench.compare import check_file
from scrimmage.bench.problems import collect_problems
from scrimmage.bench.spec import BenchError, load_bench

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "bench"
PINCH = (EXAMPLES / "pinch_car.toml").read_text()


def write(tmp_path, text):
    p = tmp_path / "b.toml"
    p.write_text(text)
    return p


def bounce_bar_with_kicker_at(a=(0.0, -0.15, -0.08), b=(0.0, 0.15, -0.08)):
    """examples/bench/bounce_bar.toml with its kicker bar moved: the default runs through the bottom wheel."""
    text = (EXAMPLES / "bounce_bar.toml").read_text()
    text = text.replace("a = [0.25, -0.15, -0.058]", f"a = {list(a)}").replace("b = [0.25, 0.15, -0.058]", f"b = {list(b)}")
    assert str(list(a)) in text
    return text


def test_collects_errors_from_several_entries(tmp_path):
    p = write(tmp_path, PINCH.replace("diameter = 0.06", "diameter = -1", 1).replace("width = 0.08", 'width = "x"', 2))
    bench, probs = collect_problems(p)
    errors = [q for q in probs if q.level == "error"]
    assert bench is None and len(errors) >= 2
    keys = {q.key for q in probs}
    assert "wheel.top.width" in keys and keys & {"wheel.bottom.diameter", "wheel.bottom.width"}


def test_unknown_key_has_entry_key(tmp_path):
    _, probs = collect_problems(write(tmp_path, PINCH.replace("tread = ", "treed = ", 1)))
    assert probs[0].key.startswith("wheel.") and "valid keys" in probs[0].message


def test_first_error_still_raises_without_a_list(tmp_path):
    with pytest.raises(BenchError, match="unknown key 'treed'") as e:
        load_bench(write(tmp_path, PINCH.replace("tread = ", "treed = ", 1)))
    assert e.value.key.startswith("wheel.") and e.value.key.endswith(".treed")


def test_bar_into_wheel_warns(tmp_path):
    bench, probs = collect_problems(write(tmp_path, bounce_bar_with_kicker_at()))
    assert bench is not None
    hits = [q for q in probs if q.level == "warning" and "kicker" in q.message and "overlap" in q.message]
    assert hits and hits[0].key in ("wheel.bottom", "bar.kicker")


@pytest.mark.parametrize("path", sorted(EXAMPLES.glob("**/*.toml")))
def test_examples_have_no_problems(path):
    bench, probs = collect_problems(path)
    assert bench is not None and not [q for q in probs if q.level == "error"]
    assert not [q for q in probs if "overlap" in q.message]


def test_check_file_names_every_error(tmp_path):
    p = write(tmp_path, PINCH.replace("diameter = 0.06", "diameter = -1", 1).replace("width = 0.08", 'width = "x"', 2))
    with pytest.raises(BenchError) as e:
        check_file(p)
    assert len(str(e.value).splitlines()) >= 2


def test_bench_check_prints_every_error(tmp_path, capsys):
    p = write(tmp_path, PINCH.replace("diameter = 0.06", "diameter = -1", 1).replace("width = 0.08", 'width = "x"', 2))
    with pytest.raises(SystemExit) as e:
        main(["bench", "check", str(p)])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "wheel" in err and len([ln for ln in err.splitlines() if ln.strip()]) >= 2
