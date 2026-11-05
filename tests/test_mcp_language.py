"""Machine text stays American English by construction (L2): every MCP tool runs inside i18n.machine(), and every
CLI command but `edit` and `serve` inside i18n.fixed(). Checked whole: the fake robot through MCP run_scenario, a
bench_run and a test_run, and `scrimmage run`, under a pseudo catalog that translates every source ("ZZ" in front),
give the same bytes as in en-US, while the files an MCP call writes for people (report.md, summary.txt) carry it."""

import asyncio
import json
import re
import shutil
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from scrimmage import i18n, i18n_tool, mcp_server  # noqa: E402

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
FAKE = TESTS / "fake_robot.toml"

# A scenario whose checks pass and fail in each way the report words (pose, always, at_end, console, by_time), with a
# screenshot, then a second step.
SCENARIO = f'''config = "{FAKE}"
[[step]]
mode = "auto"
duration = 0.5
  [[step.check]]
  pose = {{ x = 2.0, y = 4.035, tol_xy = 0.05 }}
  [[step.check]]
  pose = {{ x = 3.0, y = 4.035, tol_xy = 0.05 }}
  [[step.check]]
  always = {{ signal = "battery", min = 13.0 }}
  [[step.check]]
  always = {{ signal = "battery", min = 1.0 }}
  [[step.check]]
  at_end = {{ signal = "hal:PWM/9/<speed", min = 1.0 }}
  [[step.check]]
  at_end = {{ signal = "battery", min = 1.0 }}
  [[step.check]]
  console = {{ contains = "never-printed-xyz" }}
  [[step.check]]
  by_time = {{ signal = "battery", min = 20.0, t = 0.2 }}
  [[step.screenshot]]
  view = "top"
  at = 0.25
  width = 160
  height = 100
[[step]]
mode = "teleop"
duration = 0.3
'''


@pytest.fixture(autouse=True)
def quick_nt(monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_NT_CONNECT_TIMEOUT", "0.3")  # the fake robot serves no NetworkTables


@pytest.fixture(scope="module")
def pseudo_folder(tmp_path_factory):
    """A locales folder whose en-GB.json translates every source `scrimmage i18n template` finds: "ZZ" in front."""
    sources, problems = i18n_tool.scan()
    assert not problems, problems
    catalog = {"_language": "Pseudo"}
    for key, entry in i18n_tool.starter(sources).items():
        if key == "_language":
            continue
        text = key.split(i18n.CONTEXT, 1)[-1]
        if isinstance(entry, dict):
            one, other = text.split(i18n.PLURAL, 1)
            catalog[key] = {"one": "ZZ" + one, "other": "ZZ" + other}
        else:
            catalog[key] = "ZZ" + text
    folder = tmp_path_factory.mktemp("pseudo")
    (folder / "en-GB.json").write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def pseudo(pseudo_folder, monkeypatch, language):
    """pseudo("en-GB") reads the pseudo catalog; pseudo("en-US") the source."""
    shipped = i18n.LOCALES
    monkeypatch.setattr(i18n, "LOCALES", pseudo_folder)
    i18n.reload()

    def use(tag):
        language(tag)
        assert i18n.current_language() == tag
    yield use
    monkeypatch.setattr(i18n, "LOCALES", shipped)
    i18n.reload()


def _steady(text: str) -> str:
    """`text` without what changes from run to run: the wall-clock speed and time."""
    text = re.sub(r'"effective_speed": ?[-0-9.eE]+', '"effective_speed": 0', text)
    return re.sub(r"\d{4}-\d\d-\d\dT[0-9:.+-]+", "TIME", text)


def _files(folder: Path, names) -> dict[str, bytes]:
    return {n: (folder / n).read_bytes() for n in names if (folder / n).is_file()}


def test_every_mcp_tool_runs_inside_the_guard():
    tools = mcp_server.server._tool_manager.list_tools()
    assert len(tools) >= 29
    assert [t.name for t in tools if not getattr(t.fn, "machine_guard", False)] == []


def test_a_tool_writes_american_english_while_its_files_follow_the_setting(pseudo):
    pseudo("en-GB")
    assert i18n.t("Pieces") == "ZZPieces"
    seen = {}

    async def look():
        seen["call"] = i18n.t("Pieces")
        with i18n.for_people():
            seen["file"] = i18n.t("Pieces")
        seen["thread"] = await mcp_server._in_thread(lambda: i18n.t("Pieces"))

    asyncio.run(mcp_server._tool()(look)())
    assert seen == {"call": "Pieces", "file": "ZZPieces", "thread": "Pieces"}
    mcp_server.server.remove_tool("look")
    with i18n.fixed():                      # the CLI's guard: its files are American English too
        with i18n.for_people():
            assert i18n.t("Pieces") == "Pieces"


def _mcp_runs(work: Path) -> dict:
    """run_scenario, bench_run and test_run through MCP, each written to its own folder in `work`."""
    from test_blocks_run import MOVE_45, write_test
    work.mkdir()
    out = {}

    async def go():
        async with Client(mcp_server.server) as c:
            # refusals worded with t() and no guard of their own: only the tool's guard keeps them American English
            refused = []
            r = await c.call_tool("start_session", {"config": "/nonexistent/robot.toml"})
            refused.append(r.content[0].text)
            r = await c.call_tool("start_session", {"config": str(FAKE)})
            assert not r.is_error, r.content[0].text
            r = await c.call_tool("set_mode", {"mode": "bogus"})
            refused.append(r.content[0].text)
            r = await c.call_tool("set_match", {"station": "red9"})
            refused.append(r.content[0].text)
            await c.call_tool("stop_session", {})
            out["refused"] = "\n".join(json.loads(x)["error"] if x.startswith("{") else x for x in refused)
            scenario = work / "a.toml"
            scenario.write_text(SCENARIO, encoding="utf-8")
            r = await c.call_tool("run_scenario", {"path": str(scenario), "out_dir": str(work / "run")})
            assert not r.is_error, r.content[0].text
            out["run_scenario"] = _steady(r.content[0].text)
            out["run"] = {**_files(work / "run", ["report.json", "run.csv"])}
            out["run"]["report.json"] = _steady(out["run"]["report.json"].decode()).encode()
            out["report.md"] = (work / "run" / "report.md").read_text(encoding="utf-8")
            bench = REPO / "examples" / "bench" / "pinch_car.toml"
            r = await c.call_tool("bench_run", {"path": str(bench), "out_dir": str(work / "bench")})
            assert not r.is_error, r.content[0].text
            out["bench_run"] = r.content[0].text.replace(str(work), "WORK")
            out["bench"] = {p.name: p.read_bytes() for p in sorted((work / "bench").glob("*.csv"))}
            out["summary.txt"] = (work / "bench" / "summary.txt").read_text(encoding="utf-8")
            (work / "t").mkdir()
            r = await c.call_tool("test_run", {"path": str(write_test(work / "t", MOVE_45))})
            assert not r.is_error, r.content[0].text
            info = json.loads(r.content[0].text)
            folder = Path(info.pop("run_folder"))
            out["test_run"] = json.dumps(info, sort_keys=True)
            out["test"] = {p.name: _steady(p.read_text(encoding="utf-8")).encode()
                           for p in sorted(folder.iterdir()) if p.suffix in (".json", ".csv")}
    asyncio.run(go())
    return out


def test_mcp_machine_text_is_the_same_bytes_in_any_language(pseudo, tmp_path):
    pseudo("en-US")
    us = _mcp_runs(tmp_path / "us")
    pseudo("en-GB")
    zz = _mcp_runs(tmp_path / "zz")
    assert "/nonexistent/robot.toml" in us["refused"] and "'bogus'" in us["refused"] and "'red9'" in us["refused"]
    for key in ("refused", "run_scenario", "bench_run", "test_run"):
        assert zz[key].replace("/zz/", "/us/") == us[key], key
        assert "ZZ" not in zz[key], key
    for key in ("run", "bench", "test"):
        assert zz[key].keys() == us[key].keys() and zz[key], key
        for name, data in zz[key].items():
            assert data.replace(b"/zz/", b"/us/") == us[key][name], (key, name)
            assert b"ZZ" not in data, (key, name)
    assert "ZZ" not in us["report.md"] and "ZZ" not in us["summary.txt"]
    assert "ZZ" in zz["report.md"] and "ZZ" in zz["summary.txt"]       # the files for people follow the setting


def _cli_run(work: Path, capsys, lang: str) -> dict:
    from scrimmage import __main__ as cli
    from scrimmage import settings
    scenario = work / "a.toml"
    work.mkdir()
    scenario.write_text(SCENARIO, encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["--language", lang, "run", str(scenario), "--out", str(work / "run")])
    settings.set_language_override(None)
    assert e.value.code == 1        # some checks fail
    printed = capsys.readouterr()
    files = _files(work / "run", ["report.json", "report.md", "run.csv"])
    files["report.json"] = _steady(files["report.json"].decode()).encode()
    return {"out": printed.out.replace(str(work), "WORK"), "err": printed.err.replace(str(work), "WORK"), **files}


def test_the_cli_is_american_english_in_any_language(pseudo, tmp_path, capsys):
    pseudo("en-US")
    us = _cli_run(tmp_path / "us", capsys, "en-US")     # the baseline: American English throughout
    pseudo("en-GB")
    zz = _cli_run(tmp_path / "zz", capsys, "en-GB")
    assert us.keys() == zz.keys() and {"report.json", "report.md", "run.csv"} <= us.keys()
    assert us["out"] and "FAIL" in us["out"]
    for key, value in zz.items():
        if key == "err":
            continue                # log lines carry the clock
        if isinstance(value, bytes):
            value, want = value.replace(b"/zz/", b"/us/"), us[key]
            assert b"ZZ" not in value, key
        else:
            want = us[key]
            assert "ZZ" not in value, key
        assert value == want, key
    assert "ZZ" not in zz["err"]
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_worker_threads_go_through_the_guard():
    """asyncio.to_thread in mcp_server.py only inside _in_thread, which enters i18n.machine() in the worker."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(mcp_server))
    inside = {id(n) for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and f.name == "_in_thread"
              for n in ast.walk(f)}
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in ("to_thread", "run_in_executor")]
    assert calls and all(id(n) in inside for n in calls), [n.lineno for n in calls if id(n) not in inside]


def _page_run(work: Path) -> dict:
    """Runs as the web app's Scenario tab starts them (assembler/runs.py), each on its own worker thread: the fake
    robot's scenario, and one whose robot file binds PWM 0 twice (a ConfigError worded with t(), with no
    Said of its own: only the run's guard keeps report.json's error American English)."""
    from scrimmage.assembler.runs import RunManager
    work.mkdir()
    bad = work / "bad_robot.toml"
    bad.write_text(FAKE.read_text(encoding="utf-8").replace("models/kitbot.xml", str(TESTS / "models" / "kitbot.xml"))
                   .replace("pwm = 1\n", "pwm = 0\n"), encoding="utf-8")
    out = {}
    for name, text, state in (("a", SCENARIO, "failed"), ("b", SCENARIO.replace(str(FAKE), str(bad)), "error")):
        scenario = work / f"{name}.toml"
        scenario.write_text(text, encoding="utf-8")
        runs = RunManager(lambda: None, runs_dir=work / "runs")
        runs.start(scenario, text)
        assert runs.join(60)
        (r,) = runs.runs()["runs"]
        assert r["state"] == state, r
        folder = Path(r["out_dir"])
        files = _files(folder, ["report.json", "run.csv"])
        out |= {f"{name}/{k}": re.sub(rb"/(us|zz)/runs/[ab]-[0-9-]+", b"RUN",
                                      _steady(v.decode()).encode()).replace(b"/zz/", b"/us/")
                for k, v in files.items()}
        out[f"{name}/report.md"] = (folder / "report.md").read_text(encoding="utf-8")
        out[f"{name}/shown"] = json.dumps(r["report"])
    return out


def test_a_web_app_run_writes_report_json_in_american_english(pseudo, tmp_path):
    """The web app's run is MCP's: inside i18n.machine(), so report.json is the same bytes in any language, while
    report.md and the page's words (the run's to_dict(shown=True)) follow the setting."""
    pseudo("en-US")
    us = _page_run(tmp_path / "us")
    pseudo("en-GB")
    zz = _page_run(tmp_path / "zz")
    assert b"PWM 0 is bound by motor left_drive and motor right_drive" in us["b/report.json"]
    for key in ("a/report.json", "a/run.csv", "b/report.json"):
        assert zz[key] == us[key], key
        assert b"ZZ" not in zz[key], key
    assert not any("ZZ" in v for k, v in us.items() if isinstance(v, str))
    assert "ZZ" in zz["a/report.md"] and "ZZ" in zz["a/shown"]
