"""The block test's MCP tools: test_run, test_sweep and test_spread run in a worker thread and keep their runs."""

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
    assert {"test_run", "test_sweep", "test_spread"} <= asyncio.run(go())
    assert "test_run" in mcp_server.INSTRUCTIONS


def test_test_run_reports_and_keeps_the_run(tmp_path):
    path = write_test(tmp_path, MOVE_45)
    info = asyncio.run(mcp_server.test_run(str(path)))
    assert info["status"] == "finished" and info["passed"] is True
    assert info["outcome"] and info["t_end"] > 0
    assert len(info["checks"]) == 1 and info["checks"][0]["passed"] is True
    assert info["counts"] is None and info["feeds"] == [] and info["seed"] is None
    assert info["marks"] == [] and info["notes"] == []
    assert info["run_folder"] == str((tmp_path / "t-runs" / "001").resolve())
    assert (tmp_path / "t-runs" / "001" / "result.json").is_file()
    sig = info["signals"]
    assert sig and "t" not in sig
    for s in sig.values():
        assert s["min"] <= s["final"] <= s["max"]
    json.dumps(info)


def test_test_run_returns_the_counts_and_feeds(tmp_path):
    from test_blocks_feeds import FEED_ONE, write
    info = asyncio.run(mcp_server.test_run(str(write(tmp_path, FEED_ONE))))
    assert info["counts"] == {"fed": 1, "in": 1, "stuck": 0, "missed": 0}, info["outcome"]
    assert info["outcome"] == "1 of 1 made it in (100%)" and info["seed"] == 1
    assert [f["outcome"] for f in info["feeds"]] == ["in"] and info["feeds"][0]["source"] == "feeder"
    json.dumps(info)


def test_test_run_returns_the_grip_notes(tmp_path):
    """A test of held on the reef's grabber says its pads' friction is a default estimate (run_notes)."""
    import shutil
    from pathlib import Path

    from scrimmage.blocks.run import DEFAULT_GRIP_NOTE
    reef = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "reef"
    shutil.copytree(reef, tmp_path / "reef")
    path = tmp_path / "reef" / "g.test.toml"
    path.write_text('[test]\nrobot = "grabber-standin.toml"\n\n[[block]]\ndo = "wait_until"\n'
                    'until = { held = { piece = "any", by = "grabber" } }\ntimeout = 0.2\n', encoding="utf-8")
    info = asyncio.run(mcp_server.test_run(str(path)))
    assert info["notes"] == [DEFAULT_GRIP_NOTE] and info["marks"] == []
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


def test_test_spread_returns_the_sentence_and_rows(tmp_path):
    from test_blocks_spread import spread_test
    path = spread_test(tmp_path, "offset_in = [0, 30]")
    info = asyncio.run(mcp_server.test_spread(str(path)))
    assert info["sentence"] == "picked up 1 of 2; missed at 30 in left", info
    assert [r["picked up"] for r in info["rows"]] == ["yes", "no"]
    assert info["spread_folder"] == str((path.parent / "intake-runs" / "spread-001").resolve())
    json.dumps(info)
    with pytest.raises(ToolError, match="test file not found"):
        asyncio.run(mcp_server.test_spread(str(tmp_path / "nope.test.toml")))
    path.write_text(path.read_text(encoding="utf-8").split("[setup.floor_spread]")[0], encoding="utf-8")
    with pytest.raises(ToolError, match="no floor spread"):
        asyncio.run(mcp_server.test_spread(str(path)))
