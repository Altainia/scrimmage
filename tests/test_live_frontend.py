"""Browser tests of the live front-end foundation: scene3d/live3d/livestream/gamepad modules (headless Chromium).

The page is served by a real `scrimmage edit` on a copy of tests/fake_robot.toml; no drive session is started, the
modules are imported in the page and exercised directly.
"""
import pytest

from test_assembler_browser import Server, browser  # noqa: F401  (fixtures)
from test_live import config_copy
from browser_harness import close_context

pytestmark = pytest.mark.browser


@pytest.fixture
def live_page(browser, tmp_path):
    srv = Server(config_copy(tmp_path))
    ctx = browser.new_context(viewport={"width": 1000, "height": 700})
    page = ctx.new_page()
    page.set_default_timeout(20000)
    logs = []
    page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
    page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text} ({m.location.get('url') or '?'})") if m.type == "error" else None)
    page.goto(srv.url)
    page.wait_for_function("window.__scrimmage && __scrimmage.state && __scrimmage.store.view3d")
    yield page
    close_context(ctx)
    srv.stop()
    assert not logs, f"browser errors: {logs}"


def test_decode_frame_follows_the_wire_layout(live_page):
    r = live_page.evaluate("""async () => {
      const { decodeFrame } = await import('./livestream.js');
      const buf = new ArrayBuffer(4 + 1 + 8 + 2 + 2 * 28);
      const dv = new DataView(buf);
      dv.setUint32(0, 7, true); dv.setUint8(4, 1); dv.setFloat64(5, 1.25, true); dv.setUint16(13, 2, true);
      for (let i = 0; i < 14; i++) dv.setFloat32(15 + 4 * i, i + 0.5, true);
      const f = decodeFrame(buf);
      return { seq: f.seq, source: f.source, t: f.t, n: f.n, poses: Array.from(f.poses), isF32: f.poses instanceof Float32Array };
    }""")
    assert (r["seq"], r["source"], r["t"], r["n"]) == (7, 1, 1.25, 2)
    assert r["poses"] == [i + 0.5 for i in range(14)] and r["isF32"]


def test_keyboard_inputs(live_page):
    r = live_page.evaluate("""async () => {
      const { keyboardInputs, KEYBOARD } = await import('./gamepad.js');
      const a = keyboardInputs(new Set(['KeyW', 'KeyD', 'Digit3']));
      const b = keyboardInputs(new Set(['KeyS', 'KeyA', 'KeyI', 'KeyL', 'Digit8']));
      return { KEYBOARD, a, b, none: keyboardInputs(new Set()) };
    }""")
    assert r["KEYBOARD"] == "keyboard"
    a = r["a"]
    assert len(a["axes"]) == 6 and len(a["buttons"]) == 10 and a["povs"] == [-1]
    assert a["axes"][1] == -1 and a["axes"][0] == 1
    assert [i for i, b in enumerate(a["buttons"]) if b] == [2]
    b = r["b"]
    assert b["axes"][1] == 1 and b["axes"][0] == -1 and b["axes"][5] == -1 and b["axes"][4] == 1
    assert [i for i, x in enumerate(b["buttons"]) if x] == [7]
    assert r["none"]["axes"] == [0] * 6 and not any(r["none"]["buttons"])


def test_gamepad_inputs_standard_and_other_mappings(live_page):
    r = live_page.evaluate("""async () => {
      const { gamepadInputs } = await import('./gamepad.js');
      const btn = (n, down = {}) => Array.from({ length: n }, (_, i) => ({ pressed: i in down, value: down[i] || 0 }));
      const std = { mapping: 'standard', axes: [0.1, -0.2, 0.3, 0.4],
                    buttons: btn(17, { 6: 0.5, 7: 0.75, 12: 1, 15: 1, 1: 1, 11: 1 }) };
      const down = { mapping: 'standard', axes: [0, 0, 0, 0], buttons: btn(17, { 13: 1, 14: 1 }) };
      const odd = { mapping: '', axes: [0.5, -0.5], buttons: [{ pressed: true, value: 1 }, { pressed: false, value: 0 }] };
      return { std: gamepadInputs(std), down: gamepadInputs(down), odd: gamepadInputs(odd) };
    }""")
    s = r["std"]
    assert s["axes"] == pytest.approx([0.1, -0.2, 0.5, 0.75, 0.3, 0.4])
    assert s["povs"] == [45]
    assert len(s["buttons"]) == 10 and s["buttons"][1] and s["buttons"][9] and not s["buttons"][0]
    assert r["down"]["povs"] == [225]
    assert r["odd"]["axes"] == [0.5, -0.5] and r["odd"]["buttons"] == [True, False] and r["odd"]["povs"] == [-1]


def test_input_router_keyboard_and_blur(live_page):
    r = live_page.evaluate("""async () => {
      const { InputRouter, KEYBOARD } = await import('./gamepad.js');
      const router = new InputRouter();
      router.assign(0, KEYBOARD);
      window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyW' }));
      const down = router.poll();
      window.dispatchEvent(new Event('blur'));
      const after = router.poll();
      router.dispose();
      return { assigned: router.assignments(), w: down['0'].axes[1], blurred: after['0'].axes[1] };
    }""")
    assert r["w"] == -1 and r["blurred"] == 0


def test_live_view_builds_one_group_per_mover(live_page):
    r = live_page.evaluate("""async () => {
      const { LiveView } = await import('./live3d.js');
      const token = new URLSearchParams(location.hash.slice(1)).get('token');
      const view = await (await fetch('/api/live/view', { headers: { 'X-Scrimmage-Token': token } })).json();
      const host = document.createElement('div');
      host.style.cssText = 'width:400px;height:300px';
      document.body.append(host);
      const lv = new LiveView(host);
      lv.setModel(view);
      const groups = __scrimmage.live3d.groups;
      const n = view.movers.length;
      const poses = new Float32Array(n * 7);
      for (let i = 0; i < n; i++) poses.set([1 + i, 2, 3, 1, 0, 0, 0], i * 7);
      lv.applyFrame({ seq: 9, poses, n });
      lv.setCamera('follow');
      const root = __scrimmage.live3d.root, follow = __scrimmage.live3d.follow, rootMover = view.root_mover;
      const applied = __scrimmage.live3d.lastSeq;
      lv.applyFrame({ seq: 10, poses: new Float32Array(7), n: 1 });   // wrong mover count: ignored
      const out = { n, groups, root, follow, rootMover, applied, ignored: __scrimmage.live3d.lastSeq };
      for (const m of ['follow', 'top', 'driver', 'free']) { lv.setCamera(m); out[m] = __scrimmage.live3d.camera; }
      lv.resize();
      lv.dispose();
      return out;
    }""")
    assert r["n"] >= 1 and r["groups"] == r["n"]
    assert r["applied"] == 9 and r["ignored"] == 9
    assert (r["follow"], r["top"], r["driver"], r["free"]) == ("follow", "top", "driver", "free")


def test_robot_tab_still_renders(live_page):
    # a hand-written config has no parts: the Robot tab's 3D view is up (read-only notice), nothing else is needed
    assert live_page.evaluate("__scrimmage.store.view3d") is True
    assert live_page.evaluate("document.querySelector('#view canvas') !== null")


def test_follow_camera_tracks_the_root_mover(live_page):
    # every mover is given a distinct x (1 + index): the follow target's x says which mover the camera follows
    r = live_page.evaluate("""async () => {
      const { LiveView } = await import('./live3d.js');
      const token = new URLSearchParams(location.hash.slice(1)).get('token');
      const view = await (await fetch('/api/live/view', { headers: { 'X-Scrimmage-Token': token } })).json();
      const host = document.createElement('div');
      host.style.cssText = 'width:400px;height:300px';
      document.body.append(host);
      const lv = new LiveView(host);
      lv.setModel(view);
      const n = view.movers.length;
      const poses = new Float32Array(n * 7);
      for (let i = 0; i < n; i++) poses.set([10 + i, 2, 0.1, 1, 0, 0, 0], i * 7);
      lv.applyFrame({ seq: 1, poses, n });
      lv.setCamera('follow');
      const out = { root: view.root_mover, name: view.movers[view.root_mover], follow: __scrimmage.live3d.follow };
      lv.dispose();
      return out;
    }""")
    assert r["name"] == {"kind": "body", "name": "chassis"}
    assert r["follow"][0] == pytest.approx(10 + r["root"])


def test_parked_movers_hidden(live_page):
    r = live_page.evaluate("""async () => {
      const { LiveView } = await import('./live3d.js');
      const host = document.createElement('div');
      host.style.cssText = 'width:400px;height:300px';
      document.body.append(host);
      const lv = new LiveView(host);
      lv.setModel({ movers: [{ kind: 'body', name: 'ball.0' }], geoms: [], world_geoms: [
        { name: 'b', type: 'sphere', size: [0.075, 0, 0], pos: [1, 1, 0.075], quat: [1, 0, 0, 0], rgba: [1, 1, 0, 1],
          mover: 0, lpos: [0, 0, 0], lquat: [1, 0, 0, 0] }], root_mover: -1 });
      const g = lv.groups[0], out = {};
      lv.applyFrame({ seq: 1, poses: new Float32Array([1, 1, -20, 1, 0, 0, 0]), n: 1 });
      out.parked = g.visible;
      lv.applyFrame({ seq: 2, poses: new Float32Array([1, 1, 0.075, 1, 0, 0, 0]), n: 1 });
      out.shown = g.visible;
      lv.dispose();
      return out;
    }""")
    assert r == {"parked": False, "shown": True}


def test_score_overlay_ticks(live_page):
    r = live_page.evaluate("""async () => {
      const { LiveView } = await import('./live3d.js');
      const host = document.createElement('div');
      host.style.cssText = 'width:400px;height:300px';
      document.body.append(host);
      const lv = new LiveView(host);
      const el = host.querySelector('.view-score'), out = { start: el.hidden };
      lv.setScore({ goals: { hub_blue: 1 }, fired: 2, held: 3 });
      out.first = el.textContent; out.shown = !el.hidden;
      lv.setScore({ goals: { hub_blue: 2 }, fired: 2, held: 3 });
      out.second = el.textContent;
      lv.setScore(null);
      out.hidden = el.hidden;
      lv.dispose();
      return out;
    }""")
    assert r["start"] is True and r["shown"] is True and r["hidden"] is True
    assert r["first"] == "hub_blue 1\nfired 2 · held 3" and r["second"] == "hub_blue 2\nfired 2 · held 3"


def test_range_balls_are_coarse_spheres(live_page):
    """Hundreds of range balls get few segments; a robot's sphere keeps the full count."""
    r = live_page.evaluate("""async () => {
      const { LiveView } = await import('./live3d.js');
      const host = document.createElement('div');
      host.style.cssText = 'width:400px;height:300px';
      document.body.append(host);
      const lv = new LiveView(host);
      const sphere = (name, mover) => ({ name, type: 'sphere', size: [0.075, 0, 0], pos: [1, 1, 0.075],
        quat: [1, 0, 0, 0], rgba: [1, 1, 0, 1], mover, lpos: [0, 0, 0], lquat: [1, 0, 0, 0] });
      lv.setModel({ movers: [{ kind: 'body', name: 'ball.0' }, { kind: 'body', name: 'arm.wrist' }], geoms: [],
                    world_geoms: [sphere('b', 0), sphere('w', 1)], root_mover: 1 });
      const segs = (g) => g.children[0].geometry.parameters.widthSegments;
      const out = { ball: segs(lv.groups[0]), robot: segs(lv.groups[1]) };
      lv.dispose();
      return out;
    }""")
    assert r == {"ball": 12, "robot": 24}


def test_ball_result_compares_a_misses_apex_with_the_rim(live_page):
    """The run panel's ball table reads like report.md's: a ball fired that missed shows its apex against the rim."""
    r = live_page.evaluate("""async () => {
      const { ballResult } = await import('./report.js');
      const b = { outcome: 'on_field', fired_t: 1.5, last_pos: [6.1, 4.2, 0.08], at_rest: true,
        first_hit: 'hub_blue.funnel_0', apex_z: 1.7, rim_height: 1.829, nearest_goal: 'hub_blue' };
      return [ballResult(b), ballResult({ ...b, fired_t: null, apex_z: null, nearest_goal: null, first_hit: null }),
              ballResult({ ...b, outcome: 'scored', goal: 'hub_blue', scored_t: 2 })];
    }""")
    # in the chosen units: feet (the default, Imperial) here
    assert r[0] == "rest at (20.01, 13.78) ft; first hit hub_blue.funnel_0; apex 5.58 ft vs rim 6.00 ft (hub_blue)"
    assert r[1] == "rest at (20.01, 13.78) ft"
    assert "apex" not in r[2]


def test_the_live_view_labels_each_apriltag(live_page):
    # No expect(): the labels are sprites in a WebGL scene, not DOM, and setModel is synchronous, so the hook's
    # count is final when evaluate returns.
    try:
        live_page.evaluate("""async () => {
          const { LiveView } = await import('./live3d.js');
          const host = document.createElement('div');
          host.id = 'tag-host';
          host.style.cssText = 'width:400px;height:300px';
          document.body.append(host);
          const lv = new LiveView(host);
          window.__tagView = lv;
          lv.setModel({ movers: [], geoms: [], apriltags: [
            { id: 7, face: [0.001, 5.5, 1.45], quat: [1, 0, 0, 0], size: 0.1651 },
            { id: 8, face: [0.001, 4.9, 1.45], quat: [1, 0, 0, 0], size: 0.1651 }] });
        }""")
        assert live_page.evaluate("__scrimmage.live3d.tagLabels") == 2
        live_page.evaluate("__tagView.setModel({ movers: [], geoms: [] })")
        assert live_page.evaluate("__scrimmage.live3d.tagLabels") == 0
    finally:
        live_page.evaluate("() => { if (window.__tagView) __tagView.dispose(); document.getElementById('tag-host')?.remove(); }")
