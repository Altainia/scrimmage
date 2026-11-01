"""The bench's MCP tools: run in a worker thread, return headlines and pictures, and leave any robot session alone."""

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from scrimmage import mcp_server  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PINCH = (ROOT / "examples" / "bench" / "pinch_car.toml").read_text(encoding="utf-8").replace("count = 3", "count = 1")
BENCH_TOOLS = {"bench_check", "bench_run", "bench_sweep", "bench_compare", "bench_gallery", "bench_focus"}


def _car(folder: Path, name: str = "car.toml", text: str = PINCH) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(text, encoding="utf-8")
    return p


def _png(img) -> bytes:
    return img.data


def test_bench_tools_are_registered():
    async def go():
        async with Client(mcp_server.server) as c:
            return {t.name for t in (await c.list_tools()).tools}
    assert BENCH_TOOLS <= asyncio.run(go())


def test_mcp_bench_run_returns_images(tmp_path):
    f = _car(tmp_path)
    out = tmp_path / "res"
    result = asyncio.run(mcp_server.bench_run(str(f), str(out)))
    info = json.loads(result[0])
    assert info["headlines"][0].startswith("1 of 1 left through `shot`")
    assert info["headlines"][-1].startswith("UNCALIBRATED")
    assert info["author"] == "Maya" and info["out_dir"] == str(out.resolve())
    images = result[1:]
    assert len(images) == 2  # the side path picture and the pass contact sheet (nothing moves across y)
    assert all(_png(i)[:4] == b"\x89PNG" for i in images)
    assert (out / "path_side.png").read_bytes() == _png(images[0])
    assert (out / "pass.png").read_bytes() == _png(images[1])

    text = json.loads(asyncio.run(mcp_server.bench_check(str(f))))
    assert text["ok"] is True and text["summary"].startswith("ok: pinch car")
    assert text["problems"] == []
    bad = _car(tmp_path, "bad.toml", PINCH.replace("[[outlet]]", "[[outlet]]\ncolor = 'red'"))
    for call in (mcp_server.bench_check(str(bad)), mcp_server.bench_run(str(bad))):
        with pytest.raises(ToolError, match="color"):
            asyncio.run(call)


def test_mcp_sweep_compare_gallery(tmp_path):
    f = _car(tmp_path, "sweep.toml", PINCH + '\n[sweep]\n"shaft.top.target_rps" = [30, 40]\n')
    result = asyncio.run(mcp_server.bench_sweep(str(f), str(tmp_path / "sw")))
    info = json.loads(result[0])
    assert [row["shaft.top.target_rps"] for row in info["rows"]] == [30, 40]
    assert info["rows"][1]["exit_speed"] > info["rows"][0]["exit_speed"]
    assert len(result) >= 2 and _png(result[1])[:4] == b"\x89PNG"  # the chart
    with pytest.raises(ToolError, match="bench_run"):
        asyncio.run(mcp_server.bench_sweep(str(_car(tmp_path, "plain.toml"))))

    slow = _car(tmp_path / "s", "slow.toml", PINCH.replace("target_rps = 40", "target_rps = 30"))
    fast = _car(tmp_path / "s", "fast.toml")
    result = asyncio.run(mcp_server.bench_compare([str(fast), str(slow)], out_dir=str(tmp_path / "c")))
    info = json.loads(result[0])
    assert [i["file"] for i in info["ideas"]] == [str(fast.resolve()), str(slow.resolve())]
    assert info["feed_from"] == str(fast.resolve())
    assert _png(result[1])[:4] == b"\x89PNG"

    info = json.loads(asyncio.run(mcp_server.bench_gallery(str(tmp_path / "s"))))
    assert info["ideas"] == 2 and info["problems"] == []
    assert Path(info["gallery"]).read_text(encoding="utf-8").count("<img") == 2


def test_mcp_bench_does_not_touch_a_session(tmp_path, monkeypatch):
    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError(f"a bench tool touched the session ({name})")

    session = Untouchable()
    monkeypatch.setattr(mcp_server, "_session", session)
    threaded = []
    real = asyncio.to_thread

    async def spy(fn, *args, **kwargs):
        threaded.append(fn)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(mcp_server.asyncio, "to_thread", spy)
    f = _car(tmp_path)
    asyncio.run(mcp_server.bench_check(str(f)))
    asyncio.run(mcp_server.bench_run(str(f), str(tmp_path / "r")))
    assert mcp_server._session is session
    assert len(threaded) == 2  # each tool's work ran off the event loop


def test_mcp_returns_warnings_and_energy(tmp_path):
    hooded = ROOT / "examples" / "bench" / "hooded.toml"
    text = hooded.read_text().replace('preset = "2020-power-cell"',
                                      'kind = "squishy"\nshape = "sphere"\nsize = [0.1778]\nmass = 0.14')
    f = _car(tmp_path, "ball.toml", text.replace("count = 10", "count = 1"))
    info = json.loads(asyncio.run(mcp_server.bench_check(str(f))))
    assert len(info["warnings"]) == 1 and "nearest ball preset" in info["warnings"][0]
    info = json.loads(asyncio.run(mcp_server.bench_run(str(f), str(tmp_path / "r")))[0])
    assert info["warnings"] and info["energy_failures"] == 0
    info = json.loads(asyncio.run(mcp_server.bench_check(str(_car(tmp_path)))))
    assert info["warnings"] == []


def test_mcp_bench_run_logs_and_focus_pins(tmp_path):
    from scrimmage.bench import session
    f = _car(tmp_path)
    asyncio.run(mcp_server.bench_run(str(f)))
    events = session.read_events(tmp_path)
    assert [(e["kind"], e["source"], e["ok"]) for e in events] == [("run", "agent", True)]
    assert Path(events[0]["out"]).is_dir() and (Path(events[0]["out"]) / "summary.txt").exists()
    bad = _car(tmp_path, "bad.toml", PINCH.replace("[[outlet]]", "[[outlet]]\ncolor = 'red'"))
    with pytest.raises(ToolError):
        asyncio.run(mcp_server.bench_run(str(bad)))
    assert session.read_events(tmp_path)[-1]["ok"] is False

    out = json.loads(asyncio.run(mcp_server.bench_focus(str(f), ["run.duration", "wheel.top.diameter"])))
    assert out == {"ok": True, "focus": ["run.duration", "wheel.top.diameter"]}
    assert session.focus(tmp_path, f)[0] == "run.duration"
    with pytest.raises(ToolError, match="nope"):
        asyncio.run(mcp_server.bench_focus(str(f), ["wheel.nope.diameter"]))


def test_instructions_give_the_bench_docs_full_path():
    # an agent started outside the scrimmage folder can still find the bench format
    from pathlib import Path
    from scrimmage import mcp_server
    docs = Path(mcp_server.__file__).resolve().parent.parent / "docs" / "bench.md"
    assert docs.is_file()
    assert str(docs) in mcp_server.INSTRUCTIONS
    assert str(docs.parent.parent / "examples" / "bench") in mcp_server.INSTRUCTIONS
