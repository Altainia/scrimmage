"""Browser end-to-end tests of `scrimmage edit` (headless Chromium through Playwright).

Marked `browser`. They skip, with a reason, when `playwright` isn't importable, when no usable Chromium is found, or
when SCRIMMAGE_SKIP_BROWSER=1. With SCRIMMAGE_REQUIRE_BROWSER=1 (set on CI) a missing playwright or Chromium fails them
instead, so CI can't pass with the browser tests silently skipped.

Chromium: CI runs `python -m playwright install --with-deps chromium` for the pinned playwright version (see the
`dev` extra in pyproject.toml), and the tests then use that build. Locally the order is:
  1. SCRIMMAGE_CHROMIUM=<path to a chrome / chrome-headless-shell executable> (always wins);
  2. the build the installed playwright expects, when it is in the cache (~/.cache/ms-playwright, or
     PLAYWRIGHT_BROWSERS_PATH);
  3. the newest cached chromium_headless_shell-* / chromium-* build, whatever its revision (an older or newer
     Chromium is fine for these tests).
WebGL goes through SwiftShader (software), so no GPU is needed.

Set SCRIMMAGE_README_SHOT=<file.png> to also run the test that writes the README screenshot (docs/img/assembler.png).
"""
import glob
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.browser

REPO = Path(__file__).resolve().parent.parent
GL_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]


def _find_chromium(sync_playwright) -> str | None:
    env = os.environ.get("SCRIMMAGE_CHROMIUM")
    if env:
        return env if Path(env).is_file() else None
    try:
        with sync_playwright() as p:
            default = p.chromium.executable_path
        if default and Path(default).is_file():
            return default
    except Exception:
        pass
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache" / "ms-playwright")
    for pattern in ("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
                    "chromium-*/chrome-linux*/chrome"):
        found = sorted(glob.glob(str(root / pattern)), key=lambda s: int(re.search(r"-(\d+)/", s).group(1)))
        if found:
            return found[-1]
    return None


def _unavailable(reason: str):
    """Skip for a missing browser, or fail when SCRIMMAGE_REQUIRE_BROWSER=1."""
    if os.environ.get("SCRIMMAGE_REQUIRE_BROWSER") == "1":
        pytest.fail(f"SCRIMMAGE_REQUIRE_BROWSER=1 but the browser tests can't run: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("SCRIMMAGE_SKIP_BROWSER") == "1":
        pytest.skip("SCRIMMAGE_SKIP_BROWSER=1")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright is not installed (pip install -e '.[dev]')")
    exe = _find_chromium(sync_playwright)
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, args=GL_ARGS, **({"executable_path": exe} if exe else {}))
        except Exception as e:  # no browser installed, or one that cannot start
            _unavailable(f"no usable Chromium (run `python -m playwright install chromium` or set SCRIMMAGE_CHROMIUM): "
                        f"{str(e).splitlines()[0]}")
        yield b
        b.close()


class Server:
    def __init__(self, cfg: Path | None, *extra: str, cwd: Path | None = None):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "scrimmage", "edit", *([str(cfg)] if cfg else []), "--no-browser", *extra],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd or cfg.parent)
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=lambda: [lines.put(ln) for ln in self.proc.stdout], daemon=True).start()
        self.url = None
        self.output = ""
        try:
            while self.url is None:
                ln = lines.get(timeout=60)
                self.output += ln
                m = re.search(r"http://127\.0\.0\.1:\d+/#token=\S+", ln)
                if m:
                    self.url = m.group(0)
        except queue.Empty:
            self.stop()
            raise AssertionError(f"edit printed no URL:\n{self.output}")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@pytest.fixture
def make_page(browser, tmp_path):
    """make_page(config_name_or_None, *extra_args, subdir=None) -> (page, config path); everything is torn down
    afterwards. With `subdir` the config goes in tmp_path/subdir and the server runs in tmp_path."""
    servers, contexts, logs = [], [], []

    def make(src: str | None, *extra: str, viewport=(1400, 900), subdir: str | None = None):
        folder = tmp_path / subdir if subdir else tmp_path
        folder.mkdir(exist_ok=True)
        cfg = folder / (src or "robot.toml")
        if src:
            shutil.copy(REPO / "configs" / src, cfg)
        srv = Server(cfg, *extra, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(20000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        # switching between a robot and a part (Import CAD..., "Back to the robot") makes the old one's polls answer
        # 404 ("no robot is open", "no part is open") just before the page reloads
        page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text} ({m.location.get('url') or '?'})")
                if m.type == "error" and not ("404" in m.text and re.search(
                    r"/api/(part/state|robot/(state|view))", m.location.get("url") or "")) else None)
        page.goto(srv.url)
        page.wait_for_function("window.__scrimmage && __scrimmage.state && __scrimmage.scene && __scrimmage.scene.meshes > 0")
        return page, cfg

    yield make
    for c in contexts:
        c.close()
    for s in servers:
        s.stop()
    assert not logs, f"browser errors: {logs}"


def _add_arm(page, attach: str = "drive.top"):
    v0 = page.evaluate("__scrimmage.state.version")
    page.click('button[aria-label="Add arm"]')
    page.wait_for_selector("#add-dialog[open]")
    page.select_option("#add-attach", attach)
    page.click("#add-ok")
    page.wait_for_function(f"__scrimmage.state.version > {v0} && __scrimmage.state.parts.some(p => p.name === 'arm')")
    page.wait_for_function("__scrimmage.scene.instances.includes('arm')")


def _problems(page) -> str:
    return page.inner_text("#problems")


def _undo(page):
    v = page.evaluate("__scrimmage.state.version")
    page.click("#undo")
    page.wait_for_function(f"__scrimmage.state.version > {v}")


def test_add_arm_set_id_invalid_id_undo_and_pick(make_page, tmp_path):
    page, cfg = make_page("kitbot.toml")
    shots = tmp_path / "shots"
    shots.mkdir()
    base_instances = set(page.evaluate("__scrimmage.scene.instances"))
    assert "arm" not in base_instances
    problems0 = _problems(page)

    # add an arm at drive.top through the dialog
    _add_arm(page)
    assert "arm" in page.evaluate("__scrimmage.scene.instances")
    assert 'name = "arm"' in cfg.read_text()
    problems1 = _problems(page)
    assert problems1 != problems0, "an arm without a motor id should change the problem count"

    # its pivot id
    page.fill("#insp-id-pivot", "PWM:3")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.state.parts.find(p => p.name === 'arm').ids.pivot === 'PWM:3'")
    text = cfg.read_text()
    assert "PWM:3" in text
    assert _problems(page) != problems1, "setting the id should change the warning count"
    assert "arm" in page.evaluate("__scrimmage.scene.instances")
    page.screenshot(path=str(shots / "arm-added.png"))

    # an invalid id: a field error, file byte-identical
    before = cfg.read_bytes()
    page.fill("#insp-id-pivot", "TalonFX")
    page.keyboard.press("Enter")
    err = page.locator('[data-field="arm/ids:pivot"] .error')
    err.wait_for()
    assert err.inner_text().strip()
    assert cfg.read_bytes() == before
    assert page.evaluate("__scrimmage.state.parts.find(p => p.name === 'arm').ids.pivot") == "PWM:3"

    # Ctrl+Z (outside a text field) undoes the id; the file loses it
    page.click(".col-title >> nth=0")
    v = page.evaluate("__scrimmage.state.version")
    page.keyboard.press("Control+z")
    page.wait_for_function(f"__scrimmage.state.version > {v}")
    assert "PWM:3" not in cfg.read_text()
    assert "arm" in page.evaluate("__scrimmage.scene.instances")

    # a second part by clicking a mount triad in the 3D view
    v = page.evaluate("__scrimmage.state.version")
    page.click('button[aria-label="Add elevator"]')
    page.wait_for_selector("#add-dialog[open]")
    page.click("#add-pick")
    page.wait_for_selector("#pick-banner:not([hidden])")
    page.wait_for_timeout(300)
    target = page.evaluate("__scrimmage.screenPos('drive.top')")
    page.mouse.click(target["x"], target["y"])
    page.wait_for_selector("#add-dialog[open]")
    assert page.input_value("#add-attach") == "drive.top"
    page.click("#add-ok")
    page.wait_for_function(f"__scrimmage.state.version > {v} && __scrimmage.scene.instances.length > {len(base_instances) + 1}")
    names = [p["name"] for p in page.evaluate("__scrimmage.state.parts")]
    assert len(names) == len(base_instances) + 2
    page.screenshot(path=str(shots / "two-parts.png"))
    assert (shots / "two-parts.png").stat().st_size > 5000

    # undo removes the elevator again (the view follows the state)
    _undo(page)
    page.wait_for_function(f"__scrimmage.scene.instances.length === {len(base_instances) + 1}")
    assert "elevator" not in page.evaluate("__scrimmage.scene.instances")


def test_new_swerve_page_has_five_instances(make_page):
    page, cfg = make_page(None, "--new", "swerve")
    assert len(page.evaluate("__scrimmage.scene.instances")) == 5
    assert page.evaluate("__scrimmage.scene.meshes") > 0
    assert "swerve" in cfg.read_text()
    assert page.inner_text("#file-path").startswith("robot.toml")


# WCAG AA contrast of every piece of visible text under a root: {lum(cssColor), check(root) -> [failures]}. The
# background is the nearest ancestor's opaque one (a translucent one is blended over the one under it). Disabled
# controls are exempt (as WCAG says); large text (24 px, or 18.66 px bold) needs 3:1, the rest 4.5:1.
CONTRAST_JS = r"""{
  parse(c) { const m = String(c).match(/[\d.]+/g) || [0, 0, 0, 0]; return [+m[0], +m[1], +m[2], m.length > 3 ? +m[3] : 1]; },
  lum(c) {
    const [r, g, b] = this.parse(c).slice(0, 3).map((x) => x / 255).map((x) => x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  },
  bgOf(el) {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const c = this.parse(getComputedStyle(e).backgroundColor);
      if (c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let out = [255, 255, 255];
    for (const [r, g, b, a] of layers.reverse()) out = [r * a + out[0] * (1 - a), g * a + out[1] * (1 - a), b * a + out[2] * (1 - a)];
    return `rgb(${out.join(",")})`;
  },
  ratio(a, b) { const x = this.lum(a), y = this.lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); },
  check(root) {
    const bad = [];
    for (const el of [root, ...root.querySelectorAll("*")]) {
      if (!el.checkVisibility || !el.checkVisibility()) continue;
      if (el.closest(":disabled, [aria-disabled='true']") || el.matches("canvas, svg, option")) continue;
      const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("").trim()
        || (el.matches("input:not([type=checkbox]):not([type=radio]), select") ? (el.value || el.placeholder || "x") : "");
      if (!text) continue;
      const cs = getComputedStyle(el);
      const fg = el.matches("input") && !el.value ? getComputedStyle(el, "::placeholder").color : cs.color;
      const size = parseFloat(cs.fontSize), bold = Number(cs.fontWeight) >= 700;
      const need = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
      const r = this.ratio(fg, this.bgOf(el));
      if (r < need) bad.push(`${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}.${el.className} "${text.slice(0, 30)}" ${fg} on ${this.bgOf(el)}: ${r.toFixed(2)}`);
    }
    return bad;
  },
}"""


def test_placeholder_contrast_meets_wcag_aa(make_page):
    page, _ = make_page("kitbot.toml")
    page.evaluate("document.querySelector('#view').insertAdjacentHTML('beforeend', '<p class=\"view-placeholder\" id=\"probe\">x</p>')")
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        assert page.evaluate(f"({CONTRAST_JS}).check(document.querySelector('#probe'))") == [], scheme


def test_cg_text_and_marker(make_page):
    """The status bar says where the robot's CG is; the 3D view's CG button draws an orange marker there (and the text
    meets WCAG AA in both themes)."""
    page, _ = make_page("kitbot.toml")
    page.wait_for_function("document.querySelector('#cg').textContent.startsWith('CG: ')")
    com = page.evaluate("__scrimmage.state.com")
    assert re.fullmatch(r"CG: \d+\.\d\d m up, \d+\.\d\d m (forward|back)", page.inner_text("#cg"))
    assert page.inner_text("#cg").startswith(f"CG: {com['height']:.2f} m up, ")
    # left/right shows from 0.01 m; each side comes from the rounded value (never "0.00 m back")
    texts = page.evaluate("""async () => { const { cgText } = await import('./app.js');
      return [[0.31, 0.02, 0.03], [0.31, -0.004, -0.004], [0.2, -0.005, -0.012], [0.2, 0.004, 0.006]]
        .map(([height, forward, left]) => cgText({ height, forward, left })); }""")
    assert texts == ["CG: 0.31 m up, 0.02 m forward, 0.03 m left", "CG: 0.31 m up, 0.00 m forward",
                     "CG: 0.20 m up, 0.01 m back, 0.01 m right", "CG: 0.20 m up, 0.00 m forward, 0.01 m left"]
    assert page.evaluate("__scrimmage.scene.cg") is False and page.get_attribute("#view-cg", "aria-pressed") == "false"
    page.click("#view-cg")
    page.wait_for_function("__scrimmage.scene.cg === true")
    assert page.get_attribute("#view-cg", "aria-pressed") == "true"
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        assert page.evaluate(f"({CONTRAST_JS}).check(document.querySelector('#statusbar'))") == [], scheme
    page.click("#view-cg")
    page.wait_for_function("__scrimmage.scene.cg === false")


def test_import_cad_from_the_library_places_it_by_cad(make_page, tmp_path, monkeypatch):
    """Import CAD... opens "New part from CAD" for this robot; Back to the robot opens it again, and the new part folder
    added from the library goes on the chassis where the CAD has it (Reset to CAD puts it back after a nudge)."""
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    shutil.copy(REPO / "tests" / "data" / "cad" / "climber.step", tmp_path / "climber.step")
    page, cfg = make_page("kitbot.toml")
    page.click("#lib-import-cad")
    page.wait_for_selector("#start-cad-for")
    page.click("#start-cad-browse-btn")
    page.click('#start-cad-browse [data-entry="climber.step"]')
    page.wait_for_function("window.__scrimmagePart && __scrimmagePart.store.state"
                           " && __scrimmagePart.store.state.build.state === 'idle' && __scrimmagePart.store.state.parts.length")
    assert (tmp_path / "parts" / "climber" / "import-origin.toml").is_file()
    page.click("#back-robot")
    page.wait_for_function("window.__scrimmage && __scrimmage.mode === 'robot' && __scrimmage.state && __scrimmage.scene"
                           " && __scrimmage.scene.meshes > 0")
    page.click('button[aria-label="Add climber (folder)"]')
    page.wait_for_selector("#add-dialog[open]")
    assert page.input_value("#add-attach") == "drive.chassis"
    assert page.is_visible("#add-cad-hint")
    v = page.evaluate("__scrimmage.state.version")
    page.click("#add-ok")
    page.wait_for_function(f"__scrimmage.state.version > {v} && __scrimmage.state.parts.some(p => p.name === 'climber')")
    page.wait_for_selector("#insp-cad")
    assert page.inner_text("#insp-cad") == "Placed where the CAD has it"
    assert page.is_disabled("#insp-cad-reset")
    assert 'attach = "drive.chassis"' in cfg.read_text()
    page.click('#inspector [aria-label="Increase z by 10 mm"]')     # one click, one edit: no typing to lose
    page.wait_for_function("document.querySelector('#insp-cad').textContent === 'Moved from where the CAD has it'")
    page.click("#insp-cad-reset")
    page.wait_for_function("document.querySelector('#insp-cad').textContent === 'Placed where the CAD has it'")
    assert page.evaluate("__scrimmage.state.parts.find(p => p.name === 'climber').pos") == [0, 0, 0]


def _split_climber(folder: Path) -> None:
    """parts/ in `folder`: the climber split as a student would in the Part tab: part `base` (the base plate, arm_B on
    a hinge; its folder stays parts/climber) and part `right_arm` (the hook welded to arm_B, then made its own part)."""
    from scrimmage.cad.build import build_parts, write_parts
    from scrimmage.cad.edits import apply_edit
    from scrimmage.cad.settings import new_import, write_import
    from scrimmage.cad.source import load_source
    step = folder / "climber.step"
    shutil.copy(REPO / "tests" / "data" / "cad" / "climber.step", step)
    src = load_source(step)
    imp = new_import(src, "climber", step, folder / "parts")
    for op, args in (("set_part", {"body": "base_plate", "part": "base"}),
                     ("set_joint", {"body": "arm_B", "parent": "base_plate",
                                    "joint": {"kind": "hinge", "anchor": [0.24, 0.1, 0.01], "axis": [0, 1, 0]}}),
                     ("set_joint", {"body": "hook_assy_2", "parent": "arm_B", "joint": None}),
                     ("set_part", {"body": "hook_assy_2", "part": "right_arm"})):
        imp = apply_edit(imp, op, args)[0]
    path = folder / "parts" / "climber.import.toml"
    write_parts(build_parts(imp, src), imp, path)
    write_import(imp, path)


def test_second_part_from_an_import_hangs_where_the_cad_has_it(make_page, tmp_path):
    """Adding base, then right_arm: the dialog preselects drive.chassis, then base.arm_B (what the CAD hangs the hook
    on), and every body lands at its CAD position. Fixed to the chassis instead, the panel says so plainly."""
    import tomllib
    import mujoco
    import numpy as np
    from scrimmage.parts.assemble import assemble
    _split_climber(tmp_path)
    page, cfg = make_page("kitbot.toml")
    for name, target in (("base", "drive.chassis"), ("right_arm", "base.arm_B")):
        page.click(f'button[aria-label="Add {name} (folder)"]')
        page.wait_for_selector("#add-dialog[open]")
        assert page.input_value("#add-attach") == target
        v = page.evaluate("__scrimmage.state.version")
        page.click("#add-ok")
        page.wait_for_function(f"__scrimmage.state.version > {v} && __scrimmage.state.parts.some(p => p.name === {name!r})")
        page.wait_for_selector("#insp-cad")
        assert page.inner_text("#insp-cad") == "Placed where the CAD has it"
        assert page.locator("#insp-cad-hangs").count() == 0

    # each body, in the world, is at its CAD origin (the robot frame on the chassis)
    w = assemble(tomllib.loads(cfg.read_text()), cfg.parent)
    m, d = w.model, mujoco.MjData(w.model)
    mujoco.mj_kinematics(m, d)
    bid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, n)
    origin, rot = d.xpos[bid("drive.chassis")], d.xmat[bid("drive.chassis")].reshape(3, 3)
    for inst, folder in (("base", "climber"), ("right_arm", "right_arm")):
        origins = tomllib.loads((tmp_path / "parts" / folder / "import-origin.toml").read_text())["origins"]
        for body, o in origins.items():
            assert np.allclose(d.xpos[bid(f"{inst}.{body}")], origin + rot @ np.array(o), atol=1e-6), (inst, body)
    assert set(origins) == {"hook_assy_2"} and "arm_B" in tomllib.loads(
        (tmp_path / "parts" / "climber" / "import-origin.toml").read_text())["origins"]

    # fixed to the chassis, the hook is in place but won't ride the arm: the panel says where the CAD hangs it
    v = page.evaluate("__scrimmage.state.version")
    page.select_option("#insp-attach", "drive.chassis")
    page.wait_for_function(f"__scrimmage.state.version > {v}")
    page.click("#insp-cad-reset")
    page.wait_for_function("document.querySelector('#insp-cad').textContent === 'Placed where the CAD has it'")
    assert page.inner_text("#insp-cad-hangs") == "In the CAD this hangs on base.arm_B; it's fixed to the chassis here."


def test_pick_mode_cancels_when_config_goes_read_only(make_page):
    page, cfg = make_page("kitbot.toml")
    page.click('button[aria-label="Add arm"]')
    page.wait_for_selector("#add-dialog[open]")
    page.click("#add-pick")
    page.wait_for_selector("#pick-banner:not([hidden])")
    assert page.evaluate("document.body.classList.contains('picking')")
    # break the file behind the page's back (an unparseable file makes the config read-only)
    cfg.write_text("this is = not [valid toml\n")
    page.wait_for_function("__scrimmage.state.read_only", timeout=30000)
    page.wait_for_selector("#pick-banner", state="hidden")
    assert not page.evaluate("document.body.classList.contains('picking')")


def test_context_loss_message(make_page):
    page, _ = make_page("kitbot.toml")
    page.evaluate("document.querySelector('#view canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()")
    page.wait_for_function("document.querySelector('#view').textContent.includes('3D view lost its graphics context')")
    assert "reload the page" in page.inner_text("#view")


def test_bad_text_in_number_boxes_is_an_error_and_not_sent(make_page):
    page, cfg = make_page("elevator_arm.toml")
    page.evaluate("__scrimmage.select('elevator')")
    page.wait_for_selector("#insp-pos-0")
    before = cfg.read_bytes()

    # "0.1-" is no number: the box reports badInput and an empty value (which used to be sent as 0)
    page.fill("#insp-pos-0", "")
    page.type("#insp-pos-0", "0.1-")
    page.keyboard.press("Enter")
    err = page.locator('[data-field="elevator/pos"] .error')
    err.wait_for()
    assert err.inner_text().strip() == "Enter a number."
    page.wait_for_timeout(500)
    assert cfg.read_bytes() == before

    # the +/- buttons don't step from bad text either
    page.fill("#insp-pos-1", "")
    page.type("#insp-pos-1", "1e")
    page.click('button[aria-label="Increase y by 10 mm"]')
    page.wait_for_timeout(500)
    assert cfg.read_bytes() == before
    assert page.locator('[data-field="elevator/pos"] .error').inner_text().strip() == "Enter a number."

    # a float parameter and a pair
    for sel, fid in (("#insp-p-travel", "elevator/param:travel"),):
        page.fill(sel, "")
        page.type(sel, "2-")
        page.keyboard.press("Enter")
        e = page.locator(f'[data-field="{fid}"] .error')
        e.wait_for()
        assert e.inner_text().strip() == "Enter a number."
    page.evaluate("__scrimmage.select('arm')")
    page.wait_for_selector("#insp-p-range-1")
    page.fill("#insp-p-range-1", "")
    page.type("#insp-p-range-1", "1-")
    page.keyboard.press("Enter")
    e = page.locator('[data-field="arm/param:range"] .error')
    e.wait_for()
    assert e.inner_text().strip() == "Enter a number."
    page.wait_for_timeout(500)
    assert cfg.read_bytes() == before


def test_header_path_is_relative_to_the_working_directory(make_page, tmp_path):
    page, cfg = make_page("kitbot.toml", subdir="robot")
    text = page.inner_text("#file-path")
    assert text.split() == ["kitbot.toml", "in", "robot/"], text
    assert str(tmp_path) not in text and str(tmp_path) not in page.inner_text("#other-path")
    assert page.get_attribute("#file-path", "title") == str(cfg)


def test_require_browser_turns_skips_into_failures(monkeypatch):
    monkeypatch.delenv("SCRIMMAGE_REQUIRE_BROWSER", raising=False)
    with pytest.raises(pytest.skip.Exception):
        _unavailable("no browser")
    monkeypatch.setenv("SCRIMMAGE_REQUIRE_BROWSER", "1")
    with pytest.raises(pytest.fail.Exception, match="SCRIMMAGE_REQUIRE_BROWSER=1.*no browser"):
        _unavailable("no browser")


@pytest.mark.skipif(not os.environ.get("SCRIMMAGE_README_SHOT"), reason="set SCRIMMAGE_README_SHOT=<png> to write the README screenshot")
def test_readme_screenshot(make_page, tmp_path):
    # in <tmp>/robot with the server run from <tmp>, so the header shows "swerve.toml in robot/" (no temp path)
    page, cfg = make_page("swerve.toml", viewport=(1400, 900), subdir="robot")
    assert str(tmp_path) not in page.inner_text("header")
    _add_arm(page, "drive.top")
    page.fill("#insp-id-pivot", "PWM:3")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.state.parts.find(p => p.name === 'arm').ids.pivot === 'PWM:3'")
    page.click("#view-frame")
    page.evaluate("document.querySelector('#inspector').scrollTop = 0")
    page.wait_for_timeout(800)
    page.screenshot(path=os.environ["SCRIMMAGE_README_SHOT"])


# -- the Scenario tab: field picker, signal picker, run panel -----------------------------------------------------

@pytest.fixture
def scenario_page(browser, tmp_path):
    """scenario_page(name) -> (page, scenario path): a copy of tests/scenarios/<name> (config made absolute) opened
    with `scrimmage edit`, the server running in tmp_path (run folders go to tmp_path/runs)."""
    servers, contexts, logs = [], [], []

    def make(name: str):
        scn = tmp_path / name
        scn.write_text((REPO / "tests" / "scenarios" / name).read_text()
                       .replace('"../fake_robot.toml"', f'"{REPO / "tests" / "fake_robot.toml"}"'))
        srv = Server(scn, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text} ({m.location.get('url') or '?'})") if m.type == "error" else None)
        page.goto(srv.url)
        page.wait_for_selector(".step-row")
        page.wait_for_function("__scrimmage.scenario && __scrimmage.scenario.signals && __scrimmage.scenario.runs")
        return page, scn

    yield make
    for c in contexts:
        c.close()
    for s in servers:
        s.stop()
    assert not logs, f"browser errors: {logs}"


def _run(page, state: str):
    """Click Run and wait for the newest run to end in `state`."""
    n = page.evaluate("__scrimmage.scenario.runs.length")
    page.click("#run-btn")
    page.wait_for_function(f"__scrimmage.scenario.runs.length === {n + 1}")
    page.wait_for_function("__scrimmage.scenario.runs[0].state !== 'running'", timeout=60000)
    assert page.evaluate("__scrimmage.scenario.runs[0].state") == state


def test_scenario_run_fail_fix_with_field_picker_then_pass(scenario_page):
    page, scn = scenario_page("fake_fail.toml")
    page.click("#step-row-1")

    # run: the pose check fails, with the runner's own message, and step 2's timeline dot turns red
    _run(page, "failed")
    fail = page.locator("#run-report .rp-check.rp-fail")
    fail.first.wait_for()
    assert "from target" in fail.first.inner_text()
    assert page.locator("#run-report .rp-check.rp-pass").count() >= 1
    page.wait_for_selector("#step-row-1 .run-dot.run-fail")
    assert page.locator("#run-list li").count() == 1

    # pick the pose on the field: click the robot's start point, drag toward +y for a 90 degree heading
    page.click("[data-key='pick-1-1']")
    page.wait_for_selector("#field-dialog[open]")
    p = page.evaluate("__scrimmage.fieldPick.toScreen(2.0, 4.035)")
    q = page.evaluate("__scrimmage.fieldPick.toScreen(2.0, 5.0)")
    page.mouse.move(p["x"], p["y"])
    page.mouse.down()
    page.mouse.move(q["x"], q["y"], steps=5)
    page.mouse.up()
    assert abs(float(page.input_value("#fp-heading")) - 90) < 1
    page.fill("#fp-heading", "")          # no heading: the fake robot doesn't turn, so check only x and y
    page.fill("#fp-tol-xy", "0.3")
    page.fill("#fp-x", "2")
    page.fill("#fp-y", "4.035")
    v = page.evaluate("__scrimmage.scenario.state.version")
    page.click("#fp-ok")
    page.wait_for_function(f"__scrimmage.scenario.state.version === {v + 1}")
    spec = page.evaluate("__scrimmage.scenario.state.steps[1].checks[1].spec")
    assert spec == {"x": 2, "y": 4.035, "tol_xy": 0.3}, spec
    assert "x = 2" in scn.read_text() and "50.0" not in scn.read_text()

    # pick a signal from the signal picker
    page.select_option("#ck-1-2-signal-pick", "motor.left_drive.current")
    page.wait_for_function("__scrimmage.scenario.state.steps[1].checks[2].spec.signal === 'motor.left_drive.current'")
    page.fill("#ck-1-2-max", "100")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.scenario.state.steps[1].checks[2].spec.max === 100")
    page.fill("#ck-1-2-min", "")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.scenario.state.steps[1].checks[2].spec.min === undefined")

    # re-run: everything passes
    _run(page, "passed")
    page.wait_for_selector("#run-report .rp-verdict.rp-passed")
    assert page.locator("#run-report .rp-check.rp-fail").count() == 0
    page.wait_for_selector("#step-row-1 .run-dot.run-pass")
    assert page.locator("#run-list li").count() == 2

    # dots follow the steps' names: after a reorder no step is where the run saw it, so no dots
    v = page.evaluate("__scrimmage.scenario.state.version")
    page.evaluate("__scrimmage.scenario.edit('move_step', {step: 1, to: 0})")
    page.wait_for_function(f"__scrimmage.scenario.state.version > {v}")
    page.wait_for_function("document.querySelectorAll('#timeline .run-dot').length === 0")
    # moving it back restores both dots
    page.evaluate("__scrimmage.scenario.edit('move_step', {step: 1, to: 0})")
    page.wait_for_selector("#step-row-0 .run-dot.run-pass")
    page.wait_for_selector("#step-row-1 .run-dot.run-pass")


def test_scenario_run_can_be_stopped(scenario_page):
    page, _ = scenario_page("fake_pass.toml")
    page.select_option("#run-speed", "0.25")
    page.click("#run-btn")
    page.wait_for_function("__scrimmage.scenario.runs.length === 1 && __scrimmage.scenario.runs[0].t > 0")
    assert page.is_enabled("#run-stop")
    assert page.is_disabled("#run-btn")
    page.click("#run-stop")
    page.wait_for_function("__scrimmage.scenario.runs[0].state !== 'running'", timeout=60000)
    assert page.evaluate("__scrimmage.scenario.runs[0].state") == "stopped"
    page.wait_for_selector("#run-report .rp-verdict.rp-stopped")
    assert page.is_enabled("#run-btn")


def test_scenario_rejected_value_reverts_when_another_error_replaces_it(scenario_page):
    page, _ = scenario_page("fake_fail.toml")
    page.click("#step-row-1")
    page.fill("#ed-duration", "-1")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-fields~='step1/duration'] .error")
    assert page.input_value("#ed-duration") == "-1"
    page.fill("#ck-1-1-tol_xy", "-3")
    page.keyboard.press("Enter")
    page.wait_for_selector("[data-fields~='step1/check1/pose.tol_xy'] .error")
    # the duration's error is gone, so its box shows the saved value again; tol_xy keeps the rejected text
    page.wait_for_function("document.querySelector('#ed-duration').value === '1'")
    assert page.locator("[data-fields~='step1/duration'] .error").count() == 0
    assert page.input_value("#ck-1-1-tol_xy") == "-3"


def test_scenario_run_is_refused_while_the_file_has_errors(scenario_page):
    page, scn = scenario_page("fake_pass.toml")
    assert page.is_enabled("#run-btn")
    scn.write_text(scn.read_text().replace('signal = "battery"', 'signal = "nope"'))
    page.wait_for_function("__scrimmage.scenario.state.errors.length > 0")
    page.wait_for_selector("#run-btn:disabled")
    assert "Fix the error" in page.inner_text("#run-why")


def test_rev_short_form_is_kept_as_a_string_in_the_inspector(make_page):
    page, cfg = make_page(None, "--new", "swerve-rev")
    page.evaluate("__scrimmage.select('fr')")   # its encoder is a plain short form (no offset)
    page.wait_for_selector("#insp-id-drive")

    # a REV short form typed into a slot is sent as it is
    page.fill("#insp-id-drive", "SparkFlex:21")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.state.parts.find(p => p.name === 'fr').ids.drive === 'SparkFlex:21'")
    assert 'drive = "SparkFlex:21"' in cfg.read_text()
    page.fill("#insp-id-encoder", "SparkFlexAbs:22")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.state.parts.find(p => p.name === 'fr').ids.encoder === 'SparkFlexAbs:22'")
    assert not page.locator('[data-field="fr/ids:encoder"] .error').count()

    # converting to the table form keeps the whole "<Kind>:<id>" string under `rev` (not Number("21"))
    page.click('[aria-label="drive entry form"] button:has-text("Table")')
    inputs = page.locator('[data-field="fr/ids:drive"] .kv-row input')
    inputs.nth(0).wait_for()
    assert inputs.nth(0).input_value() == "rev" and inputs.nth(1).input_value() == "SparkFlex:21"
    inputs.nth(1).fill("SparkMax:23")
    v = page.evaluate("__scrimmage.state.version")
    page.keyboard.press("Enter")
    page.wait_for_function(f"__scrimmage.state.version > {v}")
    assert page.evaluate("__scrimmage.state.parts.find(p => p.name === 'fr').ids.drive") == {"rev": "SparkMax:23"}
    assert 'rev = "SparkMax:23"' in cfg.read_text()


# -- every screen, in the light and the dark theme ------------------------------------------------------------------
def _screen_start(page, tmp_path):
    page.wait_for_selector("#start-page:not([hidden])")


def _screen_robot(page, tmp_path):
    page.wait_for_function("__scrimmage.state && __scrimmage.scene && __scrimmage.scene.meshes > 0 && __scrimmage.store.library")
    page.evaluate("__scrimmage.select(__scrimmage.state.parts[1].name)")


def _screen_scenario(page, tmp_path):
    page.wait_for_function("__scrimmage.tab === 'scenario' && __scrimmage.scenario && __scrimmage.scenario.runs")
    page.click("#run-btn")
    page.wait_for_function("__scrimmage.scenario.runs.length === 1 && __scrimmage.scenario.runs[0].state !== 'running'", timeout=90000)
    page.wait_for_selector(".rp-verdict")


def _screen_drive(page, tmp_path):
    page.wait_for_function("__scrimmage.state && __scrimmage.code")
    page.evaluate("""async () => {
      const t = new URLSearchParams(location.hash.slice(1)).get('token');
      await fetch('/api/robot/check', {method: 'POST', headers: {'X-Scrimmage-Token': t, 'Content-Type': 'application/json'}, body: '{}'});
    }""")
    page.wait_for_function("__scrimmage.code.devices && __scrimmage.code.devices.settled", timeout=60000)
    page.evaluate("__scrimmage.setTab('drive')")
    page.wait_for_selector("#drive-devices:not([hidden])")
    page.wait_for_function("__scrimmage.live && __scrimmage.live.state && __scrimmage.live.state.state === 'running' && __scrimmage.live.frames > 5")


def _screen_bench_stage(page, tmp_path):
    page.wait_for_function("window.__scrimmageBench && __scrimmageBench.state && __scrimmageBench.shown")
    page.wait_for_function("document.querySelectorAll('#bench-headlines li').length === 2")


def _screen_bench_editor(page, tmp_path):
    _screen_bench_stage(page, tmp_path)
    page.click("#bench-edit")
    page.wait_for_function("__scrimmageBench.editor && __scrimmageBench.editor.instances.length > 0")


def _screen_part(page, tmp_path):
    page.wait_for_function("window.__scrimmagePart && __scrimmagePart.store.state && __scrimmagePart.scene"
                           " && __scrimmagePart.scene.drawn.length > 0")
    page.click('#part-tree [data-hardware="base_plate"]')
    page.click('#part-tree [data-piece="Climber/arm_A"]')
    page.wait_for_selector("#part-material")
    # a hinge on hook_assy: the frame step, the pick hint, the joint and slot controls and the collision suggestion
    page.wait_for_selector("#part-frame-step")
    page.click('#part-tree [data-body="hook_assy"]')
    page.select_option("#part-joint-kind", "hinge")
    page.wait_for_selector("#part-pick-hint")
    page.wait_for_selector("#part-joint-draft")
    page.click("#part-snap-x")
    page.wait_for_selector("#part-try-hook_assy")
    page.click("#part-pick-axis")
    page.wait_for_selector("#part-pick-hint")
    page.click("#part-joint-more summary")
    page.wait_for_selector("#part-collision-suggest")


def _robot_with_program(folder: Path, mismatch: bool = False) -> Path:
    import json as _json
    from scrimmage.assembler import templates
    cmd = _json.dumps(["{python}", "fake_robot_main.py", "--lockstep", "--phoenix"])
    text = (templates.new_config("swerve").replace("[robot]\n", "[robot]\nstart_pose = [2.0, 2.0, 0.0]\n", 1)
            + f"\n[robot_program]\ncommand = {cmd}\ncwd = {_json.dumps(str(REPO / 'tests'))}\n")
    if mismatch:
        text = text.replace('drive = "TalonFX:1", steer = "TalonFX:2"', 'drive = "TalonFX:21", steer = "TalonFX:2"')
    p = folder / "bot.toml"
    p.write_text(text)
    return p


def _open_for(screen: str, tmp_path: Path) -> Path | None:
    if screen == "start":
        return None
    if screen == "robot":
        shutil.copy(REPO / "configs" / "kitbot.toml", tmp_path / "kitbot.toml")
        return tmp_path / "kitbot.toml"
    if screen == "scenario":
        _robot_with_program(tmp_path)
        scn = tmp_path / "sit.toml"
        scn.write_text('config = "bot.toml"\n\n[[step]]\nname = "sit"\nmode = "disabled"\nduration = 0.3\n'
                       '  [[step.check]]\n  at_end = { signal = "battery", min = 20 }\n'
                       '\n[[step]]\nname = "still sit"\nmode = "disabled"\nduration = 0.2\n'
                       '\n[[step]]\nname = "drive"\nmode = "teleop"\nduration = 0.2\n'
                       '\n[[step]]\nname = "auto"\nmode = "auto"\nduration = 0.2\n'
                       '  [[step.check]]\n  at_end = { signal = "battery", min = 5 }\n')
        return scn
    if screen == "drive":
        return _robot_with_program(tmp_path, mismatch=True)
    if screen == "part":
        shutil.copy(REPO / "tests" / "data" / "cad" / "climber.step", tmp_path / "climber.step")
        return tmp_path / "climber.step"
    # the bench: a session folder whose idea has a run on the stage
    import time as _time
    from scrimmage.bench import session
    from test_bench_browser import _fake_results
    folder = tmp_path / "kickoff"
    folder.mkdir()
    now = _time.time()
    for i, name in enumerate(["pinch_car.toml", "bounce_bar.toml"]):
        shutil.copy(REPO / "examples" / "bench" / name, folder / name)
        os.utime(folder / name, (now - 100 + 50 * i, now - 100 + 50 * i))
    out = _fake_results(folder, "bounce_bar", "bounce bar (Priya)", ["3 of 3 got out through `goal`", "the bar gives 1 cm"])
    session.log_event(folder, "run", [folder / "bounce_bar.toml"], out, "agent", True)
    return folder


SCREENS = {"start": _screen_start, "robot": _screen_robot, "scenario": _screen_scenario, "drive": _screen_drive,
           "bench_stage": _screen_bench_stage, "bench_editor": _screen_bench_editor, "part": _screen_part}


@pytest.mark.parametrize("scheme", ["light", "dark"])
@pytest.mark.parametrize("screen", list(SCREENS))
def test_every_screen_meets_wcag_aa(browser, tmp_path, monkeypatch, screen, scheme):
    """Every piece of text on each screen (the start page, the Robot tab's 3D view, the Scenario tab with a run's
    report, the Drive tab running with its device line, the Bench stage with a result, the Bench editor and the Part tab
    with a piece selected) has WCAG AA
    contrast, in the light and the dark theme."""
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    monkeypatch.setenv("SCRIMMAGE_NT_CONNECT_TIMEOUT", "0.5")
    target = _open_for(screen, tmp_path)
    srv = Server(target, cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000}, color_scheme=scheme)
    try:
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(srv.url)
        SCREENS[screen](page, tmp_path)
        page.wait_for_timeout(300)
        dark = page.evaluate(f"({CONTRAST_JS}).lum(getComputedStyle(document.body).backgroundColor) < 0.2")
        assert dark == (scheme == "dark")
        bad = page.evaluate(f"({CONTRAST_JS}).check(document.body)")
        assert not bad, bad
        if screen == "drive":
            page.evaluate("fetch('/api/live/stop', {method: 'POST', headers: {'X-Scrimmage-Token': new URLSearchParams(location.hash.slice(1)).get('token'), 'Content-Type': 'application/json'}, body: '{}'})")
    finally:
        ctx.close()
        srv.stop()


def test_a_redraw_while_typing_neither_commits_early_nor_loses_the_text(make_page):
    """Half-typed text in an inspector box survives a redraw (a state arriving) without being committed, and the
    student's Enter then commits the whole value exactly once."""
    page, _ = make_page("kitbot.toml")
    posts = []
    page.on("request", lambda r: posts.append(r.post_data_json) if r.url.endswith("/api/robot/edit") and r.method == "POST" else None)
    page.evaluate("__scrimmage.select('dt')")
    page.wait_for_selector("#insp-pos-0")
    page.click("#insp-pos-0", click_count=3)
    page.keyboard.type("0.1")
    page.evaluate("__scrimmage.render()")
    page.wait_for_timeout(300)
    assert posts == [] and page.input_value("#insp-pos-0") == "0.1"
    assert page.evaluate("document.activeElement.id") == "insp-pos-0"
    page.keyboard.type("5")
    page.evaluate("__scrimmage.render()")          # again between the last key and Enter
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.state.parts.find(p => p.name === 'dt').pos[0] === 0.15")
    page.wait_for_timeout(300)
    assert [(p["op"], p["pos"][0]) for p in posts] == [("attach", 0.15)]


def test_a_redraw_after_a_trailing_point_keeps_the_decimal(make_page):
    """"1." then a redraw then "5" commits 1.5, not 15: the boxes are text boxes, so the redraw keeps "1." as typed."""
    page, _ = make_page("kitbot.toml")
    posts = []
    page.on("request", lambda r: posts.append(r.post_data_json) if r.url.endswith("/api/robot/edit") and r.method == "POST" else None)
    page.evaluate("__scrimmage.select('dt')")
    page.wait_for_selector("#insp-pos-0")
    page.click("#insp-pos-0", click_count=3)
    page.keyboard.type("1.")
    page.evaluate("__scrimmage.render()")
    assert page.input_value("#insp-pos-0") == "1."
    page.keyboard.type("5")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.state.parts.find(p => p.name === 'dt').pos[0] === 1.5")
    page.wait_for_timeout(300)
    assert [(p["op"], p["pos"][0]) for p in posts] == [("attach", 1.5)]
