"""`climb run`: a scripted climb with no robot program (scrimmage/climb). The example climbs to HIGH on the sample
climber robot; scripts that wait for what never happens, swing a tube into a bar or reach too far report it; the CLI
writes the timeline and summary and exits by the goal."""

from __future__ import annotations

import csv
import math
import tomllib
from pathlib import Path

import pytest

from scrimmage.__main__ import main
from scrimmage.climb.run import ClimbRun, run_climb
from scrimmage.climb.script import G107_EXTENSION, ScriptError, load_script, parse_script
from scrimmage.config import build_world, load_config

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "configs" / "climber_robot.toml"
HIGH = ROOT / "examples" / "climb" / "high.toml"
WHEEL_R = 0.0762          # m: the kitbot's 6 in wheels (a wheel joint's radians times this is the robot's travel)


def high_steps(n: int) -> list[dict]:
    """The example's first n steps (5: hanging from MID, settled)."""
    return tomllib.loads(HIGH.read_text())["step"][:n]


def play(raw: dict):
    return ClimbRun(build_world(load_config(CONFIG)), parse_script(raw, "test")).run()


def test_high_example_reaches_high():
    r = run_climb(CONFIG, load_script(HIGH))
    assert r.status == "reached HIGH"
    assert r.reached_goal
    assert list(r.level_times) == ["MID", "HIGH"]
    assert r.level_times["MID"] < r.level_times["HIGH"] < 15.0     # research notes 2.3: mid-tier, 9-15 s to HIGH or more
    assert r.peak_hook_load < 4000.0                                # research notes 4.4: a sane re-hook peak
    assert 5.0 < r.level_swings["HIGH"] < 60.0                      # released from MID, it swings under HIGH
    assert r.notes == []                                            # within G106 and G107 throughout
    assert r.peak_extension < G107_EXTENSION


def test_timeout_reports_clearly():
    r = play({"step": [{"name": "wait for a rung no hook reaches", "wait": {"hook_contact": "traversal"},
                        "timeout": 0.3}]})
    assert r.status == "timed out at step 1"
    assert not r.reached_goal
    assert r.duration == pytest.approx(0.3, abs=0.002)
    assert any("timed out after 0.3 s waiting for hook contact traversal" in row.event for row in r.rows)
    assert r.steps[-1].how == "timed out"


def test_tube_into_bar_reported():
    """The robot backs up until the high climber stands right under MID and extends at full voltage: its tube's top,
    not a hook (they hang off to the side), runs into the rung."""
    back = [{"joint": j, "to": -0.25 / WHEEL_R, "max_voltage": 3.0} for j in ("dt.left_2", "dt.right_2")]
    r = play({"step": [{"name": "back up", "drive": back, "wait": {"time": 1.5}},
                       {"name": "extend into MID", "drive": {"joint": "high.stage1", "to": 0.45},
                        "wait": {"time": 2.0}}]})
    assert r.status == "hit a bar"
    assert r.steps[-1].name == "extend into MID"
    hit = next(row.event for row in r.rows if " hit " in row.event)
    assert hit.startswith("high.tip") and "hook" not in hit and hit.split(" hit ")[1].startswith("mid (")
    assert float(hit.split("(")[-1].split(" N")[0]) > 200.0


def test_bracket_pressing_on_a_bar_is_not_a_hit():
    """The hook bracket carries the hooks and is meant to touch bars: pressing on one harder than hit_force isn't
    "hit a bar"; a telescope tube doing the same is."""
    w = build_world(load_config(CONFIG))
    m = w.model
    run = ClimbRun(w, parse_script({"step": [{"wait": {"time": 0.01}}]}, "test"))
    mon = run.mon
    bracket = m.geom("mid.bracket").id
    tube = next(g for g in range(m.ngeom) if m.geom(g).name == "" and m.body(int(m.geom_bodyid[g])).name == "mid.tip")
    hit_force = parse_script({"step": [{}]}).limits.hit_force
    for geom, hit in ((bracket, False), (tube, True)):
        mon.contact_state = lambda g=geom: (False, {"mid"}, {}, [(g, "mid", 0.6 * hit_force), (g, "mid", 0.6 * hit_force)])
        assert mon.is_hook_assembly(geom) is not hit
        r = run.run()
        assert (r.status == "hit a bar") is hit
        if hit:
            assert [row.event for row in r.rows if " hit " in row.event and not row.event.startswith("end")] == [f"mid.tip (geom {tube}) hit mid "
                                                                              f"({1.2 * hit_force:.0f} N)"]


def test_g107_breach_noted():
    """On the carpet, the high climber leans 40 deg forward and extends: its hooks reach about 21 in beyond the frame,
    over G107's 16 in. A note, not a failure."""
    r = play({"step": [{"drive": {"joint": "high.pivot", "to_deg": 40.0}, "wait": {"time": 0.8}},
                       {"drive": {"joint": "high.stage1", "to": 0.15}, "wait": {"time": 1.0}}]})
    assert r.status == "not hanging"
    g107 = [n for n in r.notes if n.rule == "G107"]
    assert len(g107) == 1
    assert 0.0 < g107[0].first < 1.8
    assert 2.0 < g107[0].peak / 0.0254 < 10.0          # inches over the 16 in limit
    assert not [n for n in r.notes if n.rule == "G106"]
    assert any(row.event == "G107: over 16 in" for row in r.rows)


def test_g106_breach_noted():
    """Forward past MID (so its hooks rise in front of the rung), the mid climber extends all the way: about 69 in
    tall, over G106's 66 in. A note, not a failure."""
    r = play({"step": high_steps(1) + [{"drive": {"joint": "mid.stage1", "to": 0.45}, "wait": {"time": 1.5}}]})
    assert r.status == "not hanging"
    g106 = [n for n in r.notes if n.rule == "G106"]
    assert len(g106) == 1
    assert 1.0 < g106[0].peak / 0.0254 < 5.0           # inches over the 66 in limit
    assert not [n for n in r.notes if n.rule == "G107"]
    assert any(row.event == "G106: over 66 in" for row in r.rows)


def lowered_from_mid(volts: float, seconds: float):
    """Hung from MID, the mid climber extends at up to `volts`: the robot is let down toward the carpet. Returns the
    result and how many times the robot lost every bar for a moment (climb.hanging 1 to 0) while being let down."""
    w = build_world(load_config(CONFIG))
    run = ClimbRun(w, parse_script({"step": high_steps(5) + [
        {"name": "let down", "drive": {"joint": "mid.stage1", "to": 0.45, "max_voltage": volts},
         "wait": {"time": seconds}}]}, "test"))
    gaps, prev = [], [False]

    def watch(t, v):
        hanging = bool(v["climb.hanging"])
        if prev[0] and not hanging:
            gaps.append(t)
        prev[0] = hanging
    run.on_step = watch
    r = run.run()
    start = r.steps[-1].start
    return r, len([t for t in gaps if t > start])


def test_hooks_bouncing_while_let_down_is_no_failure():
    """Let down slowly (3 V), the hooks rest on MID in bursts, losing it for a few ms at a time with tens of N between:
    no real drop, so neither a walk-off nor a fall."""
    r, gaps = lowered_from_mid(3.0, 3.0)
    assert gaps >= 5
    assert r.status == "reached MID"
    assert r.rows[-1].event == "end: reached MID"
    assert not any("dropped" in row.event or "ground" in row.event for row in r.rows)


def test_let_down_onto_the_carpet_fell():
    """Let down on the hooks until the wheels touch the carpet: a descent no hook let go of. Reported "fell", as a
    deliberate descent is (docs/climbing.md)."""
    r, _ = lowered_from_mid(3.0, 4.5)
    assert r.status == "fell"
    assert r.steps[-1].name == "let down"
    assert [row.event for row in r.rows if row.event][-2:] == ["touched the ground", "end: fell"]


def test_hooks_lifted_off_by_a_fast_extension_walk_off():
    """At full voltage the telescope extends faster than the robot falls: the hooks rise off MID while carrying it
    and the robot drops: "hook walked off", reported once the CoM is 5 cm down."""
    r, _ = lowered_from_mid(12.0, 2.0)
    assert r.status == "hook walked off"
    walk = [row.event for row in r.rows if row.event][-2:]
    assert walk[0].startswith("let go of every bar and dropped ") and walk[1] == "end: hook walked off"
    assert r.duration - r.steps[-1].start < 0.3        # within a fall of a few cm


def test_cli_writes_outputs_and_exit_codes(tmp_path, capsys):
    mid = tmp_path / "mid.toml"
    lines = ['goal = "MID"']
    for st in high_steps(5):
        lines.append("[[step]]")
        for k, v in st.items():
            lines.append(f"{k} = {_toml(v)}")
    mid.write_text("\n".join(lines) + "\n")
    with pytest.raises(SystemExit) as e:
        main(["climb", "run", str(CONFIG), "--script", str(mid), "--out", str(tmp_path / "out")])
    assert e.value.code == 0
    with open(tmp_path / "out" / "timeline.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == ["t", "event", "level", "swing", "hook_load"]
    assert any(r["event"] == "level MID" for r in rows)
    assert rows[-1]["event"] == "end: reached MID"
    summary = (tmp_path / "out" / "summary.md").read_text()
    assert "**Status: reached MID**" in summary and "Goal: MID (reached)" in summary
    assert "**Status: reached MID**" in capsys.readouterr().out

    higher = tmp_path / "higher.toml"                   # the same climb short of its goal: exit 1
    higher.write_text(mid.read_text().replace('goal = "MID"', 'goal = "HIGH"'))
    with pytest.raises(SystemExit) as e:
        main(["climb", "run", str(CONFIG), "--script", str(higher), "--out", str(tmp_path / "out2")])
    assert e.value.code == 1
    assert "Goal: HIGH (not reached)" in (tmp_path / "out2" / "summary.md").read_text()

    bad = tmp_path / "bad.toml"                         # bad input: exit 2, saying what's wrong
    bad.write_text('[[step]]\ndrive = { joint = "high.nope", to = 0.1 }\n')
    with pytest.raises(SystemExit) as e:
        main(["climb", "run", str(CONFIG), "--script", str(bad), "--out", str(tmp_path / "out3")])
    assert e.value.code == 2
    assert "no joint 'high.nope'" in capsys.readouterr().err


def _toml(v) -> str:
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml(x)}" for k, x in v.items()) + " }"
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    if isinstance(v, str):
        return f'"{v}"'
    return repr(v)


# ------------------------------------------------------------------ script checks


@pytest.mark.parametrize("raw, message", [
    ({}, r"needs at least one \[\[step\]\]"),
    ({"step": [{"wait": {"time": 1.0}, "speed": 2}]}, r"unknown key 'speed'"),
    ({"step": [{"drive": {"joint": "a.b"}}]}, r"one of to \(a position\), to_deg or voltage"),
    ({"step": [{"drive": {"joint": "a.b", "to": 1, "voltage": 3}}]}, r"one of to"),
    ({"step": [{"drive": {"joint": "a.b", "voltage": 3, "max_voltage": 4}}]}, r"max_voltage caps a position loop"),
    ({"step": [{"wait": {"swing_phase": "up"}}]}, r"swing_phase 'up' isn't one of forward_zero"),
    ({"step": [{"wait": {"time": 1, "level": "MID"}}]}, r"wait is one of"),
    ({"step": [{"wait": {"time": 5.0}, "timeout": 2.0}]}, r"waits 5 s but times out after 2 s"),
    ({"limits": {"extension_in": 0}, "step": [{"wait": {"time": 1.0}}]}, r"\[limits\]: extension_in must be above 0"),
    ({"limits": {"hit_force": -5}, "step": [{"wait": {"time": 1.0}}]}, r"\[limits\]: hit_force must be above 0"),
    ({"step": [{"release": 1.5}]}, r"release output must be -1 to 1"),
    ({"step": [{"drive": [{"joint": "a.b", "to": 1}, {"joint": "a.b", "to": 2}]}]}, r"drives the same joint twice"),
])
def test_script_errors(raw, message):
    with pytest.raises(ScriptError, match=message):
        parse_script(raw, "s")


def test_angles_in_degrees():
    s = parse_script({"step": [{"drive": {"joint": "high.pivot", "to_deg": 15.0},
                                "wait": {"joint_at": {"joint": "high.pivot", "value_deg": 15.0, "tol_deg": 0.5}}}]})
    assert s.steps[0].drives[0].to == pytest.approx(math.radians(15.0))
    assert s.steps[0].wait.tol == pytest.approx(math.radians(0.5))


@pytest.mark.parametrize("step, message", [
    ({"drive": {"joint": "high.tip", "to": 0.1}}, r"joint 'high.tip' has no motor .* joints with a motor: .*high.stage1"),
    ({"wait": {"hook_contact": "nowhere"}}, r"no bar 'nowhere' in the range; its bars and switches: .*high"),
    ({"wait": {"level": "TOP"}}, r"no level 'TOP'; the range's levels: LOW, MID, HIGH, TRAVERSAL"),
    ({"release": 0.5}, r"release needs a latch release motor"),
])
def test_script_checked_against_the_robot(step, message):
    with pytest.raises(ScriptError, match=message):
        ClimbRun(build_world(load_config(CONFIG)), parse_script({"step": [step]}, "s"))


def test_swing_phase_wait_fires_on_the_swing_under_high():
    """Hung on HIGH, swinging: a forward_zero wait ends as the CoM passes under the bar heading forward, and a
    backward_peak wait at the back of the swing."""
    raw = tomllib.loads(HIGH.read_text())
    raw["step"] = raw["step"][:11] + [{"name": "under, forward", "wait": {"swing_phase": "forward_zero"}, "timeout": 3},
                                      {"name": "back", "wait": {"swing_phase": "backward_peak"}, "timeout": 3}]
    w = build_world(load_config(CONFIG))
    run = ClimbRun(w, parse_script(raw, "s"))
    trace = []
    run.on_step = lambda t, v: trace.append((t, v["climb.swing"], run._forward_swing()))
    r = run.run()
    assert r.status == "reached HIGH"
    zero, back = r.steps[-2], r.steps[-1]
    fwd = {round(t, 3): f for t, _, f in trace}
    assert abs(fwd[round(zero.end, 3)]) < 1.0                       # under the bar
    assert fwd[round(zero.end - 0.01, 3)] < fwd[round(zero.end, 3)]  # heading forward
    assert fwd[round(back.end, 3)] < -5.0                            # out at the back
    assert fwd[round(back.end - 0.05, 3)] < fwd[round(back.end, 3)] + 0.5   # just past the turn


def test_every_motor_on_a_joint_is_driven(tmp_path, monkeypatch):
    """A slot bound to a list of IDs (a lead and its follower) puts two motors on one joint: the runner writes both,
    as the Test tab's runs and robot code do, not just the last one bound."""
    import scrimmage.hal_drive as hal_drive
    text = CONFIG.read_text().replace('extend = "PWM:2", extend_encoder', 'extend = ["PWM:2", "PWM:5"], extend_encoder')
    assert '"PWM:5"' in text
    cfg = tmp_path / "two_motors.toml"
    cfg.write_text(text)
    written = set()
    orig = hal_drive.MotorDrive.write

    def write(self, hal, volts, bus):
        if volts:
            written.add(self.b.device_key)
        return orig(self, hal, volts, bus)
    monkeypatch.setattr(hal_drive.MotorDrive, "write", write)
    run = ClimbRun(build_world(load_config(cfg)),
                   parse_script({"step": [{"name": "lift", "drive": [{"joint": "mid.stage1", "voltage": 4.0}],
                                           "wait": {"time": 0.05}}]}, "test"))
    assert len(run._drive["mid.stage1"]) == 2
    run.run()
    assert {("PWM", "2"), ("PWM", "5")} <= written
