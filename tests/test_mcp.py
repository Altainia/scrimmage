import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from scrimmage import mcp_server  # noqa: E402
from scrimmage.robot_process import RobotProcess  # noqa: E402

TESTS = Path(__file__).resolve().parent
FAKE = str(TESTS / "fake_robot.toml")
TOOLS = {"start_session", "stop_session", "restart_robot", "session_status", "advance", "set_mode", "set_joystick",
         "get_state", "get_hal", "get_console", "nt_list", "nt_get", "set_pose", "push", "move_body", "screenshot",
         "contact_sheet", "run_scenario", "robot_parts"}

SCENARIO = f"""config = "{FAKE}"
[[step]]
mode = "auto"
duration = 0.5
  [[step.check]]
  always = {{ signal = "battery", min = 12.0 }}
  [[step.screenshot]]
  view = "top"
  at = 0.25
  width = 320
  height = 200
"""


@pytest.fixture(autouse=True)
def quick_nt(monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_NT_CONNECT_TIMEOUT", "0.3")  # the fake robot serves no NetworkTables


def text(result) -> dict:
    return json.loads(result.content[0].text)


def test_tools_drive_a_session():
    async def main():
        async with Client(mcp_server.server) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert TOOLS <= names
            try:
                r = await c.call_tool("start_session", {"config": FAKE})
                assert not r.is_error and text(r)["lockstep"] is True
                r = await c.call_tool("start_session", {"config": FAKE})
                assert r.is_error and "replace" in r.content[0].text
                assert text(await c.call_tool("set_mode", {"mode": "teleop"}))["mode"] == "teleop"
                assert text(await c.call_tool("advance", {"seconds": 0.1}))["t"] == pytest.approx(0.1)
                r = await c.call_tool("advance", {"seconds": 61})
                assert r.is_error and "60" in r.content[0].text
                shot = await c.call_tool("screenshot", {"view": "top", "width": 320, "height": 200})
                img = [b for b in shot.content if b.type == "image"][0]
                assert img.mime_type == "image/png" and base64.b64decode(img.data)[:4] == b"\x89PNG"
                r = await c.call_tool("screenshot", {"width": 8})
                assert r.is_error and "width" in r.content[0].text
                r = await c.call_tool("set_mode", {"mode": "autonomous"})
                assert r.is_error and "teleop" in r.content[0].text
                assert text(await c.call_tool("set_pose", {"x": 3.0, "y": 3.0, "heading_deg": 0}))["pose"]["x"] == 3.0
                r = await c.call_tool("set_pose", {"x": float("nan"), "y": 0, "heading_deg": 0})
                assert r.is_error
                sheet = await c.call_tool("contact_sheet", {"seconds": 0.2, "frames": 2, "width": 160, "height": 100})
                assert any(b.type == "image" for b in sheet.content)
            finally:
                await c.call_tool("stop_session", {})
            r = await c.call_tool("advance", {"seconds": 0.1})
            assert r.is_error and "start_session" in r.content[0].text
    asyncio.run(main())


def test_run_scenario_tool_returns_report_and_screenshot(tmp_path):
    sc = tmp_path / "s.toml"
    sc.write_text(SCENARIO)

    async def main():
        async with Client(mcp_server.server) as c:
            try:
                r = await c.call_tool("run_scenario", {"path": str(sc), "out_dir": str(tmp_path / "out")})
                assert not r.is_error
                report = text(r)
                assert report["exit_code"] == 0, report
                assert report["mode"] == "standalone" and "note" in report
                assert report["images_returned"] == report["images_total"] == 1
                assert any(b.type == "image" for b in r.content)
                r = await c.call_tool("run_scenario", {"path": str(tmp_path / "missing.toml")})
                assert r.is_error
            finally:
                await c.call_tool("stop_session", {})
    asyncio.run(main())


def test_parallel_starts_leave_one_session_and_no_orphans(monkeypatch):
    spawned = []
    orig = RobotProcess.start

    async def recording_start(self, *args, **kwargs):
        await orig(self, *args, **kwargs)
        spawned.append(self.proc)

    monkeypatch.setattr(RobotProcess, "start", recording_start)

    async def main():
        async with Client(mcp_server.server) as c:
            try:
                rs = await asyncio.gather(c.call_tool("start_session", {"config": FAKE}),
                                          c.call_tool("start_session", {"config": FAKE}))
                assert sorted(r.is_error for r in rs) == [False, True]
                bad = [r for r in rs if r.is_error][0]
                assert "already running" in bad.content[0].text
            finally:
                await c.call_tool("stop_session", {})
        deadline = time.monotonic() + 3
        while any(p.returncode is None for p in spawned) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
    asyncio.run(main())
    assert len(spawned) == 1 and all(p.returncode is not None for p in spawned)  # the loser never spawned a robot


def test_stdio_server_lists_tools():
    async def main():
        params = StdioServerParameters(command=sys.executable, args=["-m", "scrimmage.mcp_server"],
                                       env={**os.environ, "SCRIMMAGE_NT_CONNECT_TIMEOUT": "0.3"})
        async with Client(params) as c:
            assert TOOLS <= {t.name for t in (await c.list_tools()).tools}
            try:
                r = await c.call_tool("start_session", {"config": FAKE})
                assert not r.is_error and text(r)["lockstep"] is True  # stdout stayed clean: the reply parsed
                assert text(await c.call_tool("advance", {"seconds": 0.1}))["t"] == pytest.approx(0.1)
            finally:
                await c.call_tool("stop_session", {})
    asyncio.run(main())


def test_run_scenario_refuses_standalone_beside_an_open_session(tmp_path):
    other = tmp_path / "other.toml"  # the same robot under another path: a different config
    other.write_text(Path(FAKE).read_text().replace('"models/', f'"{TESTS / "models"}/')
                     .replace('cwd = "."', f'cwd = "{TESTS}"'))
    sc = tmp_path / "s.toml"
    sc.write_text(SCENARIO.replace(FAKE, str(other)))

    async def main():
        async with Client(mcp_server.server) as c:
            try:
                assert not (await c.call_tool("start_session", {"config": FAKE})).is_error
                r = await c.call_tool("run_scenario", {"path": str(sc), "out_dir": str(tmp_path / "out")})
                assert r.is_error and "stop_session" in r.content[0].text and str(other) in r.content[0].text
            finally:
                await c.call_tool("stop_session", {})
    asyncio.run(main())


def test_run_scenario_in_a_session_reports_the_speed_it_used(tmp_path):
    sc = tmp_path / "s.toml"
    sc.write_text(SCENARIO.replace("[[step]]", 'speed = "1"\n[[step]]', 1))

    async def main():
        async with Client(mcp_server.server) as c:
            try:
                assert not (await c.call_tool("start_session", {"config": FAKE})).is_error
                r = await c.call_tool("run_scenario", {"path": str(sc), "out_dir": str(tmp_path / "out")})
                report = text(r)
                assert report["mode"] == "session" and report["speed"] == "max", report
                assert "scenario's speed (1) is ignored" in report["note"]
            finally:
                await c.call_tool("stop_session", {})
    asyncio.run(main())


def test_robot_parts_tool(tmp_path):
    from parts_fake import WARNING, parts_config
    cfg = str(parts_config(tmp_path))

    async def main():
        async with Client(mcp_server.server) as c:
            try:
                r = await c.call_tool("robot_parts", {})
                assert r.is_error and "start_session" in r.content[0].text
                assert not (await c.call_tool("start_session", {"config": cfg})).is_error
                tree = json.loads((await c.call_tool("robot_parts", {})).content[0].text)
                assert [t["name"] for t in tree] == ["drive", "dt", "intake"]
                assert tree[2]["slots"]["roller"] == {"kind": "motor", "id": None}
                assert "intake.roller" in tree[2]["joints"]
                assert WARNING in text(await c.call_tool("session_status", {}))["warnings"]
                await c.call_tool("stop_session", {})
                assert not (await c.call_tool("start_session", {"config": FAKE})).is_error
                r = await c.call_tool("robot_parts", {})
                assert r.is_error and "hand-written" in r.content[0].text
            finally:
                await c.call_tool("stop_session", {})

    asyncio.run(main())


def test_start_session_rejects_an_unknown_sim_mode():
    async def main():
        async with Client(mcp_server.server) as c:
            r = await c.call_tool("start_session", {"config": FAKE, "sim_mode": "x"})
            assert r.is_error and "sim_mode" in r.content[0].text and "fast" in r.content[0].text
    asyncio.run(main())
