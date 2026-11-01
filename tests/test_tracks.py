import asyncio
import json
from pathlib import Path

import pytest

import scrimmage.session as session_mod
from scrimmage.scenario import ScenarioError, load_scenario, load_scenario_text
from scrimmage.scenario_runner import run_scenario
from scrimmage.tracks import PortInputs, Sample, Track, TrackError, load_track, parse_track, write_track

TESTS = Path(__file__).resolve().parent
P = Path("drive.csv")
GOOD = ("t,p0.axis0,p0.axis1,p0.button1,p0.button2,p0.pov0,p3.axis0\n"
        "0,0.0,-1.0,0,1,-1,0.25\n"
        "0.02,0.5,-1.0,1,0,90,-0.25\n")


def test_parse_good_track():
    tr = parse_track(GOOD, P)
    assert [s.t for s in tr.samples] == [0.0, 0.02]
    assert tr.samples[1].ports == {0: PortInputs([0.5, -1.0], [True, False], [90]), 3: PortInputs([-0.25], [], [])}
    assert tr.samples[0].ports[0].buttons == [False, True]


@pytest.mark.parametrize("text,line,fragment", [
    ("time,p0.axis0\n0,0\n", 1, "first column must be 't'"),
    ("t,x0\n0,0\n", 1, "unknown column"),
    ("t,p6.axis0\n0,0\n", 1, "ports are 0-5"),
    ("t,p0.button0\n0,0\n", 1, "numbered from 1"),
    ("t,p0.axis1\n0,0\n", 1, "p0.axis0 is missing"),
    ("t,p0.axis0\n0,1.5\n", 2, "between -1 and 1"),
    ("t,p0.button1\n0,0\n0.1,2\n", 3, "must be 0 or 1"),
    ("t,p0.pov0\n0,400\n", 2, "-1 or 0-359"),
    ("t,p0.pov0\n0,12.5\n", 2, "whole number"),
    ("t,p0.axis0\n0,0\n0,0\n", 3, "greater than the previous"),
    ("t,p0.axis0\n0.5,0\n0.2,0\n", 3, "greater than the previous"),
    ("t,p0.axis0\n-1,0\n", 2, "0 or more"),
    ("t,p0.axis0\n0,0,0\n", 2, "expected 2 values"),
    ("t,p0.axis0\n0,abc\n", 2, "between -1 and 1"),
    ("t,p0.axis0\nx,0\n", 2, "t must be a number"),
    ("", 1, "empty"),
])
def test_parse_errors_have_line_numbers(text, line, fragment):
    with pytest.raises(TrackError) as e:
        parse_track(text, P)
    assert str(e.value).startswith(f"drive.csv: line {line}: ") and fragment in str(e.value)


def test_write_round_trip(tmp_path):
    samples = parse_track(GOOD, P).samples
    samples.append(Sample(0.1234567, {0: PortInputs([0.12345678, 0.0], [False, False], [-1])}))  # port 3 absent
    f = tmp_path / "out.csv"
    write_track(f, samples)
    back = load_track(f).samples
    assert [s.t for s in back] == [0.0, 0.02, 0.1235]
    assert back[0] == samples[0] and back[1] == samples[1]
    assert back[2].ports[0].axes == [0.1235, 0.0]
    assert back[2].ports[3] == PortInputs([0.0], [], [])  # neutral for a port the sample lacked
    assert f.read_text().splitlines()[0] == "t,p0.axis0,p0.axis1,p0.button1,p0.button2,p0.pov0,p3.axis0"


def test_at_boundaries():
    tr = parse_track("t,p0.axis0\n0.1,0\n0.2,0\n0.5,0\n", P)
    assert [tr.at(x) for x in (0.0, 0.0999, 0.1, 0.15, 0.2, 0.4, 0.5, 9.0)] == [-1, -1, 0, 0, 1, 1, 2, 2]
    assert tr.at(0.2 - 1e-12) == 1  # float noise at a boundary


def test_at_exact_sample_times_and_between():
    tr = Track(P, [Sample(t) for t in (0.0, 0.005, 0.01, 0.3)])
    assert [tr.at(t) for t in (0.0, 0.005, 0.01, 0.3)] == [0, 1, 2, 3]            # exactly on a sample
    assert [tr.at(t) for t in (0.0025, 0.0075, 0.2999, 1e9)] == [0, 1, 2, 3]      # between, and after the last
    assert [tr.at(t) for t in (0.005 - 1e-10, 0.005 - 1e-8)] == [1, 0]           # within 1e-9 counts as reached
    assert tr.at(-1e-12) == 0 and tr.at(-1e-6) == -1                              # before the first
    assert Track(P, []).at(0.0) == -1


def test_at_uses_sample_times_computed_once():
    tr = Track(P, [Sample(t) for t in (0.1, 0.2, 0.5)])

    class NoWalk(list):
        def __iter__(self):
            raise AssertionError("at() walked the samples")
    tr.samples = NoWalk(tr.samples)
    assert [tr.at(x) for x in (0.0, 0.1, 0.15, 0.2, 0.5, 9.0)] == [-1, 0, 0, 1, 2, 2]


def scenario_in(tmp_path, step_body: str, csv: str | None = "t,p0.axis0\n0,0.5\n"):
    if csv is not None:
        (tmp_path / "in.csv").write_text(csv)
    text = 'config = "r.toml"\n[[step]]\nname = "s"\nmode = "teleop"\nduration = 1.0\n' + step_body
    return load_scenario_text(text, tmp_path / "s.toml")


def test_inputs_resolve_relative_to_the_scenario(tmp_path):
    sc = scenario_in(tmp_path, 'inputs = "in.csv"\n')
    assert sc.steps[0].inputs.path == tmp_path / "in.csv"
    assert sc.steps[0].inputs.samples[0].ports[0].axes == [0.5]


@pytest.mark.parametrize("body,fragment", [
    ("inputs = 3\n", "s.toml, step 1 (s): inputs must be the path of a .csv file"),
    ('inputs = "in.csv"\naxes = { 0 = [1.0] }\n', "inputs can't be combined with axes, buttons or povs in the same step"),
    ('inputs = "nope.csv"\n', "inputs: file not found: "),
])
def test_inputs_errors(tmp_path, body, fragment):
    with pytest.raises(ScenarioError) as e:
        scenario_in(tmp_path, body)
    assert fragment in str(e.value)


def test_inputs_bad_csv_and_late_sample(tmp_path):
    with pytest.raises(ScenarioError, match=r"step 1 \(s\): inputs: in.csv: line 2: "):
        scenario_in(tmp_path, 'inputs = "in.csv"\n', csv="t,p0.axis0\n0,9\n")
    with pytest.raises(ScenarioError) as e:
        scenario_in(tmp_path, 'inputs = "in.csv"\n', csv="t,p0.axis0\n0,0\n1.5,0\n")
    assert str(e.value).endswith("inputs: in.csv: the last sample (t = 1.5) is after the step's 1 s")


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)  # the fake robot serves no NetworkTables


def test_runner_replays_a_track_and_the_fake_robot_drives(tmp_path, monkeypatch):
    log = tmp_path / "inputs.jsonl"
    monkeypatch.setenv("SCRIMMAGE_FAKE_INPUT_LOG", str(log))
    sc = load_scenario(TESTS / "scenarios" / "fake_track.toml")
    out = tmp_path / "out"
    rep = asyncio.run(run_scenario(sc, out))
    assert rep.exit_code == 0, rep.to_dict()
    samples = sc.steps[0].inputs.samples
    step = 0.005
    entries = [json.loads(l) for l in log.read_text().splitlines()]
    teleop = [e for e in entries if e["enabled"]]
    assert len(teleop) >= 20
    # Alignment: the step request numbered seq (1-based) integrates the interval ending at seq*step, and it
    # carries the inputs in force at the start of that interval, (seq-1)*step - the sample that
    # track.at(that time) picks.  Teleop begins at robot step first_seq, i.e. elapsed 0 at (first_seq-1)*step.
    first = teleop[0]["seq"]
    for e in teleop:
        elapsed = (e["seq"] - first) * step
        k = sc.steps[0].inputs.at(elapsed)
        want = samples[k].ports[0]
        got = e["joysticks"]["0"]
        assert got["axes"] == want.axes and got["buttons"] == want.buttons and got["povs"] == want.povs, (e, elapsed)
    # the moves happened: forward (axis1 = -1) for 0.05 s, then released
    rows = (out / "run.csv").read_text().splitlines()
    head = rows[0].split(",")
    xi = head.index("x") if "x" in head else next(i for i, h in enumerate(head) if h.endswith("x"))
    xs = [float(r.split(",")[xi]) for r in rows[1:]]
    assert abs(xs[-1] - xs[0]) > 1e-4, (head, xs[0], xs[-1])
