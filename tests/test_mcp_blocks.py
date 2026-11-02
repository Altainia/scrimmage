"""The block test's MCP tools: test_run and test_sweep run in a worker thread and keep their runs."""

import asyncio
import json

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from scrimmage import mcp_server  # noqa: E402
from test_blocks_run import MOVE_45, write_test  # noqa: E402


def test_block_tools_are_registered():
    async def go():
        async with Client(mcp_server.server) as c:
            return {t.name for t in (await c.list_tools()).tools}
    assert {"test_run", "test_sweep"} <= asyncio.run(go())
    assert "test_run" in mcp_server.INSTRUCTIONS


def test_test_run_reports_and_keeps_the_run(tmp_path):
    path = write_test(tmp_path, MOVE_45)
    info = asyncio.run(mcp_server.test_run(str(path)))
    assert info["status"] == "finished" and info["passed"] is True
    assert info["outcome"] and info["t_end"] > 0
    assert len(info["checks"]) == 1 and info["checks"][0]["passed"] is True
    assert info["run_folder"] == str((tmp_path / "t-runs" / "001").resolve())
    assert (tmp_path / "t-runs" / "001" / "result.json").is_file()
    sig = info["signals"]
    assert sig and "t" not in sig
    for s in sig.values():
        assert s["min"] <= s["final"] <= s["max"]
    json.dumps(info)


def test_test_run_plain_errors(tmp_path):
    with pytest.raises(ToolError, match="test file not found"):
        asyncio.run(mcp_server.test_run(str(tmp_path / "nope.test.toml")))
    path = write_test(tmp_path, MOVE_45.replace('"arm.pivot"', '"arm.gone"'))
    with pytest.raises(ToolError, match="arm.gone"):
        asyncio.run(mcp_server.test_run(str(path)))


def test_test_sweep_returns_the_table(tmp_path):
    path = write_test(tmp_path, MOVE_45)
    info = asyncio.run(mcp_server.test_sweep(str(path), {"robot": "mass"}, [30, 45]))
    assert info["sweep_folder"] == str((tmp_path / "t-runs" / "sweep-001").resolve())
    assert [float(r["value"]) for r in info["rows"]] == [30, 45]
    assert all(r["status"] == "finished" for r in info["rows"])
    assert (tmp_path / "t-runs" / "sweep-001" / "table.csv").is_file()
    with pytest.raises(ToolError, match="can't sweep"):
        asyncio.run(mcp_server.test_sweep(str(path), {"nope": 1}, [1]))
    with pytest.raises(ToolError, match="at least one value"):
        asyncio.run(mcp_server.test_sweep(str(path), {"robot": "mass"}, []))
