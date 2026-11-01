"""Report notes for a robot that tipped over or got stranded (scrimmage/terrain/monitor.py's first_tipped and
first_stranded), written by scenario_runner.terrain_notes."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path

import mujoco
import pytest

import scrimmage.session as session_mod
from range_fixtures import KITBOT, range_cfg
from scrimmage.protocol import HalState
from scrimmage.scenario import load_scenario
from scrimmage.scenario_runner import moved_joints, run_scenario, terrain_notes
from scrimmage.session import Session
from scrimmage.world import World
from test_range_checks import write_config, write_scenario

TWO_STEPS = ('[[step]]\nmode = "auto"\nduration = 0.5\n'
             '[[step]]\nmode = "auto"\nduration = 0.3\n')


@pytest.fixture(autouse=True)
def quick_nt_give_up(monkeypatch):
    monkeypatch.setattr(session_mod, "NT_CONNECT_TIMEOUT", 0.5)


def on_side(w) -> None:
    """Roll the robot 90 degrees onto its side."""
    q = w.free_qadr
    w.data.qpos[q + 3:q + 7] = [math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0, 0.0]
    w.data.qpos[q + 2] += 0.2
    mujoco.mj_forward(w.model, w.data)


def test_a_tipped_robot_is_noted_once_in_report_and_markdown(tmp_path):
    cfg = write_config(tmp_path, range_cfg(range={"file": "2026-bump"}))
    scn = load_scenario(write_scenario(tmp_path, cfg, TWO_STEPS))
    placed = []

    def watch(session, t):
        if not placed:
            placed.append(t)
            on_side(session.world)

    rep = asyncio.run(run_scenario(scn, tmp_path / "out", watch=watch))
    assert rep.error is None, rep.to_dict()
    tipped = [n for n in rep.notes if n.startswith("the robot tipped over at t=")]
    assert len(tipped) == 1, rep.notes
    assert " s (tilt " in tipped[0] and "°, at x=" in tipped[0]
    assert tipped[0] in (tmp_path / "out" / "report.md").read_text()


def test_no_terrain_signal_on_a_plain_range_means_no_notes(tmp_path):
    cfg = write_config(tmp_path, range_cfg())
    scn = load_scenario(write_scenario(tmp_path, cfg, '[[step]]\nmode = "auto"\nduration = 0.2\n'))
    rep = asyncio.run(run_scenario(scn, tmp_path / "out"))
    assert rep.error is None and not any("robot" in n and "t=" in n for n in rep.notes)


def test_notes_text_stranded_and_moved_joints_and_reset():
    w = World(range_cfg(range={"file": "2026-bump"}), None)
    mon = w.terrain_monitor()
    mon.first_stranded = (1.234, "ball.3")
    mon.first_tipped = (3.2, 71.4, (2.5, 4.0, 10.0))
    noted: dict = {}
    notes = terrain_notes(w, noted)
    assert notes == ["the robot tipped over at t=3.20 s (tilt 71°, at x=2.50, y=4.00)",
                     "the robot was stranded at t=1.23 s, resting on ball.3"]
    assert terrain_notes(w, noted) == []          # once per latch
    mon.reset()                                   # a world reset: a later tipping is a new first
    mon.first_tipped = (5.0, 80.0, (1.0, 2.0, 0.0))
    assert len(terrain_notes(w, noted)) == 1
    assert moved_joints({"a": (0.01, 0.0)}) == ""      # within JOINT_MOVED of rest


ELEVATOR = {"name": "elevator", "stock": "elevator", "attach": "drive.center", "pos": [0.1, 0.0, 0.12],
            "stages": 2, "rigging": "cascade", "travel": 1.0, "motor": "KrakenX60", "ids": {"lift": "PWM:2"}}


def elevator_world() -> World:
    w = World(range_cfg(range={"file": "2026-bump"}, part=[*KITBOT, ELEVATOR]), None)
    w.terrain_monitor()
    return w


def lift_joint(w) -> tuple[str, int]:
    name = next(n for n in w.joint_states() if "elevator" in n or "stage" in n or "carriage" in n)
    return name, int(w.model.joint(name).qposadr[0])


def test_note_gives_the_joints_at_the_tipping_not_later():
    w = elevator_world()
    name, adr = lift_joint(w)
    w.data.qpos[adr] = 0.3
    on_side(w)
    hal = HalState()
    mon = w.terrain
    for _ in range(50):
        w.step(hal)
        if mon.first_tipped:
            break
    assert mon.first_tipped is not None
    w.data.qpos[adr] = 0.9                      # it moves on after the tipping
    for _ in range(20):
        w.step(hal)
    note = terrain_notes(w, {})[0]
    assert note.startswith("the robot tipped over at t=")
    value = float(note.split(f", with joint.{name}.position = ")[1])      # a little drift from the set 0.3
    assert value == pytest.approx(0.3, abs=0.02), note


def test_stranded_note_carries_the_moved_joints():
    w = elevator_world()
    name, adr = lift_joint(w)
    mon = w.terrain
    mon.first_stranded = (1.5, "ball.2")
    mon.stranded_joints = {name: (0.4, 0.0), "other": (0.01, 0.0)}
    assert terrain_notes(w, {}) == [f"the robot was stranded at t=1.50 s, resting on ball.2, "
                                    f"with joint.{name}.position = 0.4"]


def test_a_step_that_ends_in_an_error_still_notes_the_tipping(tmp_path, monkeypatch):
    from scrimmage.session import SessionError
    cfg = write_config(tmp_path, range_cfg(range={"file": "2026-bump"}))
    scn = load_scenario(write_scenario(tmp_path, cfg, TWO_STEPS))
    placed = []

    def watch(session, t):
        if not placed:
            placed.append(t)
            on_side(session.world)
        elif t - placed[0] > 0.1:
            raise SessionError("the robot program died", t=t)

    rep = asyncio.run(run_scenario(scn, tmp_path / "out", watch=watch))
    assert any(not c.passed for c in rep.implicit) or rep.error, rep.to_dict()
    assert sum(n.startswith("the robot tipped over at t=") for n in rep.notes) == 1, rep.notes
