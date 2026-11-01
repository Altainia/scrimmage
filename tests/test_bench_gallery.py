"""`bench sweep`, `bench compare` and `bench gallery`: every combination, one piece and feed, one HTML file."""

import re
from pathlib import Path

import numpy as np
import pytest

from scrimmage.__main__ import main
from scrimmage.bench.compare import BENCH_METRICS, compare_benches, metrics, sweep_bench
from scrimmage.bench.gallery import compare_picture, write_gallery
from scrimmage.bench.spec import BenchError

ROOT = Path(__file__).resolve().parent.parent
PINCH = (ROOT / "examples" / "bench" / "pinch_car.toml").read_text(encoding="utf-8")


def _one_car(text: str = PINCH) -> str:
    return text.replace("count = 3", "count = 1")


def _write(folder: Path, name: str, text: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(text, encoding="utf-8")
    return p


def test_sweep_combinations(tmp_path):
    f = _write(tmp_path, "car.toml", _one_car() + """
[sweep]
"shaft.top.target_rps" = [30, 45]
"shaft.bottom.target_rps" = [30, 45]
""")
    rows = sweep_bench(f)
    assert [c for c, _ in rows] == [
        {"shaft.top.target_rps": 30, "shaft.bottom.target_rps": 30},
        {"shaft.top.target_rps": 30, "shaft.bottom.target_rps": 45},
        {"shaft.top.target_rps": 45, "shaft.bottom.target_rps": 30},
        {"shaft.top.target_rps": 45, "shaft.bottom.target_rps": 45},
    ]
    for combo, r in rows:  # each run is the file with that combination set
        assert r.bench.shafts["top"].target_rps == combo["shaft.top.target_rps"]
        assert r.bench.shafts["bottom"].target_rps == combo["shaft.bottom.target_rps"]
    speeds = [metrics(r)["exit_speed"] for _, r in rows]
    assert speeds[3] > speeds[0] * 1.2  # both wheels faster: a faster car
    assert set(metrics(rows[0][1])) == set(BENCH_METRICS)

    bad = _write(tmp_path, "bad.toml", _one_car() + '\n[sweep]\n"wheel.top.diameter" = [0.06, -1.0]\n')
    with pytest.raises(BenchError, match=r"wheel\.top\.diameter=-1.*positive"):
        sweep_bench(bad)  # every combination is checked before the first runs


def test_sweep_cli_writes_table_csv_and_charts(tmp_path, capsys):
    f = _write(tmp_path, "car.toml", _one_car() + '\n[sweep]\n"shaft.top.target_rps" = [30, 45]\n')
    main(["bench", "sweep", str(f)])
    out = capsys.readouterr().out
    assert "UNCALIBRATED" in out and "shaft.top.target_rps" in out and "exit_speed" in out
    res = tmp_path / "car-results"
    assert (res / "results.csv").read_text().splitlines()[0].startswith("shaft.top.target_rps,")
    assert (res / "line-exit_speed-vs-shaft.top.target_rps.png").is_file()

    with pytest.raises(SystemExit) as e:  # no [sweep]: point at bench run
        main(["bench", "sweep", str(_write(tmp_path, "plain.toml", _one_car()))])
    assert e.value.code == 2 and "bench run" in capsys.readouterr().err
    chart = _write(tmp_path, "chart.toml", _one_car() + '\n[sweep]\n"shaft.top.target_rps" = [30]\n'
                   '[[chart]]\nkind = "line"\nx = "shaft.top.target_rps"\ny = "nope"\n')
    with pytest.raises(SystemExit) as e:
        main(["bench", "sweep", str(chart)])
    assert e.value.code == 2 and "nope" in capsys.readouterr().err


def test_compare_uses_one_feed(tmp_path):
    a = _write(tmp_path, "a.toml", _one_car())
    b_text = (PINCH.replace("count = 3", "count = 2").replace("mass = 0.2", "mass = 0.3")
              .replace("velocity = [3.0, 0.0, 0.0]", "velocity = [2.0, 0.0, 0.0]")
              .replace('name = "pinch car"', 'name = "heavy car"').replace("target_rps = 40", "target_rps = 30"))
    b = _write(tmp_path, "b.toml", b_text)

    ra, rb = compare_benches([a, b], None)  # the first file's piece and feed
    for r in (ra, rb):
        assert r.bench.piece.mass == pytest.approx(0.2)
        assert r.bench.inlets[0].count == 1 and r.bench.inlets[0].velocity == (3.0, 0.0, 0.0)
        assert len(r.pieces) == 1
    assert rb.bench.name == "heavy car" and rb.bench.shafts["top"].target_rps == 30  # the rest is b's own

    ra, rb = compare_benches([a, b], b)  # a named file's piece and feed
    for r in (ra, rb):
        assert r.bench.piece.mass == pytest.approx(0.3) and len(r.pieces) == 2
    assert ra.bench.shafts["top"].target_rps == 40

    pic = compare_picture([ra, rb])
    assert pic.ndim == 3 and pic.shape[2] == 3 and pic.shape[1] > pic.shape[0]  # side by side
    with pytest.raises(BenchError, match="at least two"):
        compare_benches([a], None)


def test_gallery_is_self_contained(tmp_path):
    folder = tmp_path / "session"
    _write(folder, "maya.toml", _one_car())
    _write(folder, "jo.toml", _one_car().replace('author = "Maya"', 'author = "Jo"')
           .replace('idea = "two wheels pinch the car down the track"',
                    'idea = "slower wheels < faster wheels & a softer squeeze"')
           .replace("target_rps = 40", "target_rps = 30"))
    out = write_gallery(folder, tmp_path / "g.html")
    html = out.read_text(encoding="utf-8")
    assert not re.search(r"(src|href)\s*=\s*[\"']?(https?:)?//", html)  # nothing fetched from anywhere
    assert "http://" not in html and "https://" not in html
    assert len(re.findall(r"<img\b", html)) == 2  # one picture per idea
    assert len(re.findall(r'<img[^>]+src="data:image/png;base64,', html)) == 2
    for text in ("Maya", "Jo", "two wheels pinch the car down the track",
                 "slower wheels &lt; faster wheels &amp; a softer squeeze", "UNCALIBRATED", "left through"):
        assert text in html
    table = html[html.index("<table"):]
    assert table.count("<tr") == 3  # a header and one row per idea

    (folder / "broken.toml").write_text("[bench]\nname = 'x'\n", encoding="utf-8")
    html = write_gallery(folder, tmp_path / "g2.html").read_text(encoding="utf-8")
    assert "broken.toml" in html and "[piece] is missing" in html  # listed with its problem, not fatal
    assert len(re.findall(r"<img\b", html)) == 2


def test_compare_and_gallery_cli(tmp_path, capsys):
    folder = tmp_path / "s"
    a = _write(folder, "a.toml", _one_car())
    b = _write(folder, "b.toml", _one_car().replace("target_rps = 40", "target_rps = 30")
               .replace('name = "pinch car"', 'name = "slow car"'))
    main(["bench", "compare", str(a), str(b), "--out", str(tmp_path / "c")])
    out = capsys.readouterr().out
    assert "pinch car" in out and "slow car" in out and "UNCALIBRATED" in out
    assert {"compare.csv", "compare.png"} <= {p.name for p in (tmp_path / "c").iterdir()}
    main(["bench", "gallery", str(folder)])
    assert (folder / "gallery.html").is_file()
    assert "gallery.html" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        main(["bench", "gallery", str(tmp_path / "nowhere")])
    assert e.value.code == 2


def test_metrics_of_a_run():
    from scrimmage.bench.run import run_bench
    from scrimmage.bench.spec import parse_bench
    import tomllib
    r = run_bench(parse_bench(tomllib.loads(_one_car()), ROOT / "examples" / "bench"))
    m = metrics(r)
    assert m["out"] == 1 and m["jammed"] == 0 and m["escaped"] == 0
    assert m["exit_speed"] == pytest.approx(r.pieces[0].speed)
    assert np.isnan(m["exit_speed_sd"])  # one piece: no spread


def test_check_validates_every_sweep_combination(tmp_path):
    from scrimmage.bench.compare import check_file
    ok = _write(tmp_path, "ok.toml", _one_car() + '\n[sweep]\n"wheel.top.diameter" = [0.08, 0.1]\n')
    assert "a [sweep] of 2 combinations" in check_file(ok)[1]
    bad = _write(tmp_path, "bad.toml", _one_car() + '\n[sweep]\n"wheel.top.diameter" = [0.06, -1.0]\n')
    with pytest.raises(BenchError, match=r"wheel\.top\.diameter=-1.*positive"):
        check_file(bad)  # without running anything


def test_metrics_round_noise_to_zero():
    from scrimmage.bench.compare import _quiet
    row = dict.fromkeys(BENCH_METRICS, float("nan")) | {"exit_speed": 7.4, "exit_speed_sd": 2.308e-10,
                                                        "elevation_deg": -1.2e-15, "spin_rps": 3.0}
    q = _quiet(row)
    assert q["exit_speed_sd"] == 0.0 and q["elevation_deg"] == 0.0
    assert q["exit_speed"] == 7.4 and q["spin_rps"] == 3.0 and np.isnan(q["recovery_s"])
    assert _quiet(row | {"exit_speed_sd": 0.01})["exit_speed_sd"] == 0.01


def test_compare_cli_prints_the_bench_uncalibrated_once(tmp_path, capsys):
    from scrimmage.launcher.report import UNCALIBRATED as LAUNCHER_UNCALIBRATED
    a = _write(tmp_path, "a.toml", _one_car())
    b = _write(tmp_path, "b.toml", _one_car().replace('name = "pinch car"', 'name = "twin car"'))
    main(["bench", "compare", str(a), str(b), "--out", str(tmp_path / "c")])
    out = capsys.readouterr().out
    assert out.count("UNCALIBRATED") == 1 and LAUNCHER_UNCALIBRATED not in out
    assert "measure the tread's friction" in out
    assert not re.search(r"\de-\d\d", out)  # no rounding noise like 2.3e-10 (test_metrics_round_noise_to_zero)
