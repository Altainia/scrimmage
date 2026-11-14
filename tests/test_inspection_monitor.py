"""Watching a robot's height, reach and diameter during a run (scrimmage/inspection/monitor.py), its notes in a
scenario's report, the assembler's inspection warnings and state, and MCP's inspect_robot."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import math

import mujoco
import pytest

import scrimmage.session as session_mod
from range_fixtures import range_cfg
from scrimmage import units
from scrimmage.assembler import validate
from scrimmage.checks import shown
from scrimmage.inspection.monitor import InspectionMonitor
from scrimmage.inspection.spec import SEASONS, Limit
from scrimmage.protocol import HalState
from scrimmage.scenario import load_scenario
from scrimmage.scenario_runner import run_scenario
from scrimmage.world import World
from test_range_checks import write_config, write_scenario

IN = 0.0254

# A 0.6 m square chassis box (top at 0.15) with a 0.4 m arm on a hinge about y at x 0.25: at 0 it points up (inside
# the frame, its top at 0.55 m, 21.7 in); at 90 deg it points forward, reaching 0.25 + 0.4 - 0.3 = 0.35 m past the
# frame (13.8 in), over 2026's 12 in by 0.35 - 0.3048 = 0.0452 m.
ARM_XML = """<mujoco>
  <option timestep="0.001"/>
  <worldbody>
    <geom name="floor" type="plane" size="5 5 0.1"/>
    <body name="chassis" pos="0 0 0.1"><freejoint/>
      <geom type="box" size="0.3 0.3 0.05" mass="40"/>
      <body name="arm" pos="0.25 0 0.05">
        <joint name="pivot" type="hinge" axis="0 1 0"/>
        <geom name="arm" type="box" size="0.02 0.02 0.2" pos="0 0 0.2" mass="1"/>
      </body>
    </body>
  </worldbody>
</mujoco>"""
OUT = 0.35 - 12 * IN


def arm_world(tmp_path, inspection: dict | None = None) -> World:
    (tmp_path / "robot.xml").write_text(ARM_XML, encoding="utf-8")
    cfg = {"model": {"mjcf": "robot.xml"}}
    if inspection is not None:
        cfg["inspection"] = inspection
    return World(cfg, tmp_path)


def hold(w: World, angle: float, seconds: float) -> None:
    """Step `w` for `seconds` with the arm held at `angle` (rad)."""
    m, d = w.model, w.data
    q, v = m.jnt_qposadr[m.joint("pivot").id], m.jnt_dofadr[m.joint("pivot").id]
    hal = HalState()
    for _ in range(round(seconds / m.opt.timestep)):
        d.qpos[q], d.qvel[v] = angle, 0.0
        w.step(hal)


def test_an_arm_out_past_the_limit_for_a_second_is_one_note(tmp_path):
    w = arm_world(tmp_path, {"season": 2026})
    assert w.inspection.start_notes() == []          # tucked at the start: nothing over
    hold(w, 0.0, 0.5)
    hold(w, math.pi / 2, 1.0)
    hold(w, 0.0, 0.5)
    [b] = w.inspection.breaches()
    assert b.key == "reach" and b.rule == "R105" and b.peak_part == "arm"
    assert b.peak == pytest.approx(OUT, abs=2e-3)
    assert b.over_s == pytest.approx(1.0, abs=0.021) and b.first == pytest.approx(0.5, abs=0.025)
    [note] = w.inspection.notes()
    assert str(note).startswith("2026 R105: reach beyond the frame over the 304.8 mm limit from t=0.5")
    assert "by up to 45 mm (arm) at " in str(note) and "(1.0" in str(note) and str(note).endswith(" s over in all)")
    with units.fixed("imperial"):
        assert "over the 12 in limit" in shown(note) and "by up to 1.78 in (arm) at" in shown(note)


def test_the_monitor_samples_every_20_ms(tmp_path):
    w = arm_world(tmp_path, {"season": 2026})
    assert w.inspection.samples == 1                 # the start pose, at the reset
    hold(w, 0.0, 1.0)
    assert w.inspection.samples == 51


def test_the_monitor_skips_a_measure_the_season_has_no_limit_for(tmp_path):
    assert set(arm_world(tmp_path, {"season": 2025}).inspection.watched) == {"reach"}
    assert set(arm_world(tmp_path, {"season": 2013}).inspection.watched) == {"height", "diameter"}
    w = arm_world(tmp_path, {"season": 2023})        # 48 in reach: the arm's 13.8 in is within
    hold(w, math.pi / 2, 0.2)
    assert w.inspection.notes() == []


def test_a_height_exception_is_quoted(tmp_path):
    """Lifted 1.2 m, the robot is over 2013's 60 in height, whose pyramid and auto zone exception is quoted."""
    w = arm_world(tmp_path, {"season": 2013})
    w.data.qpos[w.free_qadr + 2] += 1.2
    mujoco.mj_forward(w.model, w.data)
    hold(w, 0.0, 0.02)                               # the next sample
    [b] = w.inspection.breaches()
    assert b.key == "height" and b.rule == "G22"
    assert str(b.note()).endswith("; on the pyramid or in the auto zone (84 in.): not checked")


def test_the_diameter_is_the_smallest_circle(tmp_path):
    """The tucked robot's smallest circle is the chassis's diagonal, 0.6 sqrt 2 = 0.849 m (the arm is inside it):
    against a 0.5 m diameter limit (2013's, lowered) it is over by 0.349 m."""
    w = arm_world(tmp_path)
    season = dataclasses.replace(SEASONS[2013], diameter=Limit(0.5, "R03-B, G23"))
    mon = InspectionMonitor(w, season)
    [b] = [x for x in mon.breaches() if x.key == "diameter"]
    assert b.peak == pytest.approx(0.6 * math.sqrt(2) - 0.5, abs=1e-6) and b.rule == "R03-B, G23"
    assert str(b.note()).startswith("2013 R03-B, G23: diameter of the smallest circle holding the robot over the "
                                    "500 mm limit from t=0.00 s")
    assert InspectionMonitor(w, SEASONS[2013]).breaches() == []    # 54 in: within


def test_a_reset_starts_the_monitor_over(tmp_path):
    w = arm_world(tmp_path, {"season": 2026})
    hold(w, math.pi / 2, 0.2)
    assert len(w.inspection.breaches()) == 1
    w.reset()
    assert w.inspection.episode == 1 and w.inspection.samples == 1
    assert w.inspection.breaches(since=1) == []
    hold(w, math.pi / 2, 0.1)
    [again] = w.inspection.breaches(since=1)
    assert again.first == pytest.approx(0.0, abs=0.021) and again.over_s == pytest.approx(0.1, abs=0.021)
    assert len(w.inspection.notes()) == 2            # the earlier episode's breach is kept
    w.reset_robot()
    assert w.inspection.episode == 2


def test_no_inspection_section_no_monitor_and_no_notes(tmp_path):
    w = arm_world(tmp_path)
    assert w.inspection is None
    hold(w, math.pi / 2, 0.1)                        # steps as before


def test_the_start_notes_leave_height_and_reach_to_the_monitor(tmp_path):
    w = World(range_cfg(part=[{"name": "drive", "stock": "chassis"}], range=None, inspection={"season": 2026}),
              None)
    notes = w.inspection.start_notes()
    assert [n.split(":")[0] for n in notes] == ["2026 R104"] and "frame perimeter" in notes[0]


@pytest.fixture
def quick_nt(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)


def test_a_scenario_notes_the_start_and_a_breach_once(tmp_path, quick_nt):
    """The default chassis (110.24 in perimeter, over 2026's 110 in) is a start note; lifted 1 m at the first watch,
    it is over 2026's 30 in height until it falls back: one height note."""
    parts = [{"name": "drive", "stock": "chassis"},
             {"name": "dt", "stock": "differential", "attach": "drive.center", "ids": {"left": "PWM:0",
                                                                                         "right": "PWM:1"}}]
    body = '[[step]]\nmode = "auto"\nduration = 0.5\n[[step]]\nmode = "auto"\nduration = 0.5\n'
    cfg = write_config(tmp_path, range_cfg(part=parts, inspection={"season": 2026}))
    scn = load_scenario(write_scenario(tmp_path, cfg, body))
    lifted = []

    def watch(session, t):
        if not lifted:
            lifted.append(t)
            w = session.world
            w.data.qpos[w.free_qadr + 2] += 1.0
            mujoco.mj_forward(w.model, w.data)

    rep = asyncio.run(run_scenario(scn, tmp_path / "out", watch=watch))
    assert rep.error is None, rep.to_dict()
    start = [n for n in rep.notes if n.startswith("2026 R104: frame perimeter")]
    height = [n for n in rep.notes if n.startswith("2026 R107: height over")]
    assert len(start) == 1 and len(height) == 1, rep.notes
    assert rep.notes.index(start[0]) < rep.notes.index(height[0])
    assert "height over the" in (tmp_path / "out" / "report.md").read_text(encoding="utf-8")

    plain = write_config(tmp_path, range_cfg(part=parts), name="plain.toml")
    scn = load_scenario(write_scenario(tmp_path, plain, body, name="plain_s.toml"))
    lifted.clear()
    rep = asyncio.run(run_scenario(scn, tmp_path / "out2", watch=watch))
    assert rep.error is None and not any(n.startswith("2026 R") for n in rep.notes), rep.notes


CHASSIS = '[[part]]\nname = "drive"\nstock = "chassis"\nids = { gyro = "Pigeon2:1" }\n'


def test_the_assembler_warns_with_an_inspection_section(tmp_path):
    res = validate.check(CHASSIS + '[inspection]\nseason = 2026\n', tmp_path / "robot.toml")
    assert res.ok
    perimeter = [w for w in res.warnings if "frame perimeter" in w.message]
    assert len(perimeter) == 1 and perimeter[0].field == "status" and perimeter[0].message.startswith("2026 R104")
    assert res.world.inspection is not None
    plain = validate.check(CHASSIS, tmp_path / "robot.toml")
    assert plain.ok and not any("frame perimeter" in w.message for w in plain.warnings)
    assert plain.world.inspection is None


def test_the_assembler_state_carries_the_inspection(tmp_path):
    from scrimmage.assembler.server import App
    cfg = tmp_path / "robot.toml"
    cfg.write_text(CHASSIS + '[inspection]\nseason = 2026\n', encoding="utf-8")
    app = App(cfg, open_browser=False)
    s = app.state()
    assert s["inspection"]["season"] == 2026 and s["inspection"]["reason"] == "[inspection] season"
    per = next(x for x in s["inspection"]["lines"] if x["key"] == "perimeter")
    assert per["measured"] == pytest.approx(2.8) and per["limit"] == pytest.approx(110 * IN)
    assert per["over"] > 0 and per["estimate"] is False and "frame perimeter" in per["text"]
    assert any("2026 R104" in w["message"] for w in s["warnings"])
    cfg.write_text(CHASSIS, encoding="utf-8")
    assert App(cfg, open_browser=False).state()["inspection"] is None


def test_inspect_robot_reads_the_session_s_config(tmp_path):
    pytest.importorskip("mcp")
    from mcp import Client
    from parts_fake import parts_config
    from scrimmage import mcp_server
    cfg = parts_config(tmp_path)

    async def main():
        async with Client(mcp_server.server) as c:
            r = await c.call_tool("inspect_robot", {})
            assert r.is_error and "start_session" in r.content[0].text
            try:
                assert not (await c.call_tool("start_session", {"config": str(cfg)})).is_error
                got = json.loads((await c.call_tool("inspect_robot", {"season": 2026})).content[0].text)
                assert got["season"] == 2026 and got["reason"] == "--season"
                per = next(x for x in got["lines"] if x["key"] == "perimeter")
                assert per["over"] > 0 and per["rule"] == "R104" and per["text"].startswith("frame perimeter: 2800 mm")
                assert got["monitor"] is None and got["not_checked"]      # no [inspection]: no monitor
                r = await c.call_tool("inspect_robot", {"season": 1999})
                assert r.is_error and "season" in r.content[0].text
            finally:
                await c.call_tool("stop_session", {})
            cfg.write_text(cfg.read_text(encoding="utf-8") + "\n[inspection]\nseason = 2024\n", encoding="utf-8")
            try:
                assert not (await c.call_tool("start_session", {"config": str(cfg)})).is_error
                got = json.loads((await c.call_tool("inspect_robot", {})).content[0].text)
                assert got["season"] == 2024 and got["monitor"] == []
            finally:
                await c.call_tool("stop_session", {})

    asyncio.run(main())
