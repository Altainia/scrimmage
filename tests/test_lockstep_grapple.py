"""The RobotPy Grapple LaserCAN helper (lockstep/scrimmage_lockstep/grapple.py) against fake `libgrapplefrc` and `hal`
modules, and (where installed) the real libgrapplefrc-py.

The helper is a port of the Java agent's ScrimmageGrapple: these tests check the same rules (None until scrimmage
has written both inputs and a robot loop has run, short ranging from the start, no target past the mode's reach,
the setters recorded and mirrored, each LaserCAN its own reading). Unlike Java it replaces libgrapplefrc's LaserCAN
class (the real one can't be built in a desktop simulation), so these also check that the stand-in reaches robot
code however it imported the class.
"""

import importlib
import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "lockstep" / "scrimmage_lockstep"
P = "scrimmage grapple helper: "
UNWRITTEN = -(2 ** 31)


# ---------------------------------------------------------------------------------------------------- fakes


class Value:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


class FakeEnum:
    """A pyo3 enum member: compares equal to itself (and its int), converts with int()."""

    def __init__(self, cls_name, name, value):
        self.cls_name, self.name, self.value = cls_name, name, value

    def __int__(self):
        return self.value

    def __eq__(self, other):
        return other is self or (isinstance(other, int) and other == self.value)

    __hash__ = None

    def __repr__(self):
        return f"{self.cls_name}.{self.name}"


def make_libgrapplefrc(missing=()):
    g = types.ModuleType("libgrapplefrc")

    class LaserCAN:  # libgrapplefrc's own class: building one crashes a desktop simulation
        def __init__(self, can_id):
            raise AssertionError("the real LaserCAN was built")

        def get_measurement(self):
            return None

        def set_range(self, mode):
            pass

        def set_roi(self, roi):
            pass

        def set_timing_budget(self, budget):
            pass

    class LaserCanRangingMode(FakeEnum):
        pass

    LaserCanRangingMode.Long = LaserCanRangingMode("LaserCanRangingMode", "Long", 1)
    LaserCanRangingMode.Short = LaserCanRangingMode("LaserCanRangingMode", "Short", 0)

    class LaserCanTimingBudget(FakeEnum):
        pass

    for ms in (20, 33, 50, 100):
        setattr(LaserCanTimingBudget, f"TB{ms}ms", LaserCanTimingBudget("LaserCanTimingBudget", f"TB{ms}ms", ms))

    class LaserCanRoi:
        def __init__(self, x, y, w, h):
            self.x, self.y, self.w, self.h = x, y, w, h

    for name, cls in [("LaserCAN", LaserCAN), ("LaserCanRangingMode", LaserCanRangingMode),
                      ("LaserCanTimingBudget", LaserCanTimingBudget), ("LaserCanRoi", LaserCanRoi)]:
        if name not in missing:
            setattr(g, name, cls)
    return g


class Fakes:
    def __init__(self, missing=()):
        self.g = make_libgrapplefrc(missing)
        self.devices = {}
        self.callbacks = []
        fakes = self

        class SimDevice:
            class Direction:
                kInput, kOutput, kBidir = 0, 1, 2

            def __init__(self, name):
                self.name = name
                self.ok = name not in fakes.devices
                self.values = {}
                if self.ok:
                    fakes.devices[name] = self

            def __bool__(self):
                return self.ok

            def createInt(self, name, direction, initial):
                assert direction == SimDevice.Direction.kInput
                v = self.values[name] = Value(initial)
                return v

        self.hal = types.ModuleType("hal")
        self.hal.SimDevice = SimDevice
        self.hal_sim = types.ModuleType("hal.simulation")
        self.hal_sim.registerSimPeriodicBeforeCallback = lambda cb: self.callbacks.append(cb) or ("SimCB", cb)
        self.hal.simulation = self.hal_sim

    def modules(self):
        return {"libgrapplefrc": self.g, "hal": self.hal, "hal.simulation": self.hal_sim}

    def write(self, can_id, mm=None, status=None):
        values = self.devices[f"ScrimmageGrapple:LaserCAN[{can_id}]"].values
        if mm is not None:
            values["distance_mm"].set(mm)
        if status is not None:
            values["status"].set(status)

    def loop(self):
        for cb in self.callbacks:
            cb()


def load_package(monkeypatch):
    """Import scrimmage_lockstep (and its helpers) from the source tree, fresh, as `scrimmage_lockstep`."""
    for name in ("scrimmage_lockstep", "scrimmage_lockstep.rev", "scrimmage_lockstep.grapple"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    spec = importlib.util.spec_from_file_location("scrimmage_lockstep", PKG_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PKG_DIR)])
    pkg = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "scrimmage_lockstep", pkg)
    spec.loader.exec_module(pkg)
    return pkg


def load_helper(monkeypatch, fakes=None):
    if fakes is not None:
        for name, mod in fakes.modules().items():
            monkeypatch.setitem(sys.modules, name, mod)
    load_package(monkeypatch)
    helper = importlib.import_module("scrimmage_lockstep.grapple")
    monkeypatch.setattr(helper, "_version", lambda: "9.8.7")
    return helper


@pytest.fixture
def env(monkeypatch, capsys):
    """Fake modules installed; the helper loaded from source and installed."""
    fakes = Fakes()
    fakes.helper = load_helper(monkeypatch, fakes)
    fakes.helper.install()
    assert capsys.readouterr().out == ""  # nothing said before the first LaserCAN
    return fakes


def robot_module(monkeypatch, name, source):
    """A robot module (as robot.py is, under `robotpy sim`) imported now, from `source`."""
    mod = types.ModuleType(name)
    monkeypatch.setitem(sys.modules, name, mod)
    exec(source, vars(mod))
    return mod


# ---------------------------------------------------------------------------------------------------- install


def test_install_is_a_no_op_without_libgrapplefrc(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "libgrapplefrc", None)  # `import libgrapplefrc` raises ImportError
    helper = load_helper(monkeypatch)
    helper.install()
    assert capsys.readouterr().out == ""
    assert helper.HELPER_PREFIX == P


def test_install_replaces_the_module_class(env):
    original = make_libgrapplefrc().LaserCAN
    assert env.g.LaserCAN is env.helper.LaserCAN and env.g.LaserCAN is not original
    env.helper.install()  # a second call changes nothing
    assert env.g.LaserCAN is env.helper.LaserCAN


def test_a_name_bound_before_install_is_replaced_too(monkeypatch, capsys):
    """`robotpy sim` imports robot.py before it loads the extension: a `from libgrapplefrc import LaserCAN` (under any
    name) made then is pointed at the stand-in; `import libgrapplefrc` finds it on the module."""
    fakes = Fakes()
    helper = load_helper(monkeypatch, fakes)
    original = fakes.g.LaserCAN
    early = robot_module(monkeypatch, "robot_early", "from libgrapplefrc import LaserCAN\n"
                                                     "from libgrapplefrc import LaserCAN as LC\n"
                                                     "import libgrapplefrc\n")
    assert early.LaserCAN is original
    helper.install()
    assert early.LaserCAN is helper.LaserCAN and early.LC is helper.LaserCAN
    assert early.libgrapplefrc.LaserCAN is helper.LaserCAN
    late = robot_module(monkeypatch, "robot_late", "from libgrapplefrc import LaserCAN\n")
    assert late.LaserCAN is helper.LaserCAN
    for mod in (early, late):
        exec("x = LaserCAN(7)\n", vars(mod))  # doesn't build the real class
    capsys.readouterr()


def test_the_first_lasercan_says_loaded_once(env, capsys):
    env.g.LaserCAN(7)
    env.g.LaserCAN(9)
    assert capsys.readouterr().out == P + "loaded (python 9.8.7)\n"
    assert len(env.callbacks) == 1


def test_load_extension_installs_the_grapple_helper(monkeypatch, capsys, tmp_path):
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "rev", None)  # the REV helper: a no-op
    fakes.hal.loadOneExtension = lambda path: 0
    pkg = load_package(monkeypatch)
    lib = tmp_path / "lib.so"
    lib.write_bytes(b"")
    monkeypatch.setattr(pkg, "library_path", lambda: lib)
    pkg.loadExtension()
    helper = sys.modules["scrimmage_lockstep.grapple"]
    assert fakes.g.LaserCAN is helper.LaserCAN
    fakes.g.LaserCAN(7)
    assert capsys.readouterr().out.startswith(P + "loaded (python ")


@pytest.mark.parametrize("platform,machine", [("darwin", "arm64"), ("win32", "ARM64")])
def test_load_extension_without_lockstep_installs_the_helpers_without_the_library(monkeypatch, capsys, caplog, tmp_path,
                                                                                 platform, machine):
    """Lockstep is built only for Linux and Windows x64: elsewhere the package has no library, so loading it says once
    that the robot runs in real time and still installs the helpers."""
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "rev", None)
    fakes.hal.loadOneExtension = lambda path: pytest.fail(f"loaded {path}")
    pkg = load_package(monkeypatch)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(pkg.platform, "machine", lambda: machine)
    assert not pkg.library_path().exists()
    with caplog.at_level("INFO", logger="scrimmage_lockstep"):
        pkg.loadExtension()
    assert [r.getMessage() for r in caplog.records] == [
        "lockstep runs on Linux and Windows x64 only: on this computer the robot runs in real time"]
    helper = sys.modules["scrimmage_lockstep.grapple"]
    assert fakes.g.LaserCAN is helper.LaserCAN
    fakes.g.LaserCAN(7)
    assert capsys.readouterr().out.startswith(P + "loaded (python ")


@pytest.mark.parametrize("platform,machine,want", [
    ("linux", "x86_64", "lib/libhalsim_scrimmage_lockstep.so"),
    ("linux", "aarch64", "lib/libhalsim_scrimmage_lockstep.so"),
    ("win32", "AMD64", "lib/win_amd64/halsim_scrimmage_lockstep.dll"),
    ("win32", "x86_64", "lib/win_amd64/halsim_scrimmage_lockstep.dll"),
    ("win32", "ARM64", "lib/win_arm64/halsim_scrimmage_lockstep.dll"),
    ("darwin", "arm64", "lib/libhalsim_scrimmage_lockstep.dylib"),
])
def test_library_path_is_this_computers_extension(monkeypatch, platform, machine, want):
    pkg = load_package(monkeypatch)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(pkg.platform, "machine", lambda: machine)
    assert pkg.library_path() == PKG_DIR.resolve() / want
    assert pkg.windows_x64() is (want == "lib/win_amd64/halsim_scrimmage_lockstep.dll")


def windows_x64_package(monkeypatch, tmp_path, load, lib_exists=True):
    """The package loaded as on Windows x64 with fake hal (loadOneExtension = `load`) and a DLL that exists or not."""
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "rev", None)
    fakes.hal.loadOneExtension = load
    pkg = load_package(monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(pkg.platform, "machine", lambda: "AMD64")
    lib = tmp_path / "halsim_scrimmage_lockstep.dll"
    if lib_exists:
        lib.write_bytes(b"")
    monkeypatch.setattr(pkg, "library_path", lambda: lib)
    return pkg, fakes, lib


def test_load_extension_on_windows_x64_loads_the_dll(monkeypatch, caplog, tmp_path):
    loaded = []
    pkg, fakes, lib = windows_x64_package(monkeypatch, tmp_path, lambda path: loaded.append(path) or 0)
    with caplog.at_level("INFO", logger="scrimmage_lockstep"):
        pkg.loadExtension()
    assert loaded == [str(lib)] and caplog.records == []
    assert fakes.g.LaserCAN is sys.modules["scrimmage_lockstep.grapple"].LaserCAN


def raises(path):
    raise RuntimeError("no such procedure")


@pytest.mark.parametrize("load,lib_exists,said", [
    (lambda path: pytest.fail(f"loaded {path}"), False, "is missing (reinstall scrimmage-lockstep), so the robot runs "
                                                        "in real time"),
    (lambda path: -1, True, "(error -1; robotpy-hal "),
    (raises, True, "(error RuntimeError: no such procedure; robotpy-hal "),
])
def test_load_extension_on_windows_x64_falls_back_to_real_time_in_one_line(monkeypatch, caplog, tmp_path, load,
                                                                          lib_exists, said):
    """A missing DLL, or one that doesn't load (a RobotPy whose HAL it can't bind): one warning saying the robot runs in
    real time, nothing raised into the robot program, and the helpers still installed."""
    pkg, fakes, lib = windows_x64_package(monkeypatch, tmp_path, load, lib_exists)
    with caplog.at_level("INFO", logger="scrimmage_lockstep"):
        pkg.loadExtension()
    lines = [r.getMessage() for r in caplog.records]
    assert len(lines) == 1 and lines[0].startswith("lockstep couldn't load") and said in lines[0], lines
    assert "real time" in lines[0] and caplog.records[0].levelname == "WARNING"
    assert fakes.g.LaserCAN is sys.modules["scrimmage_lockstep.grapple"].LaserCAN


def test_load_extension_on_linux_needs_the_library(monkeypatch, tmp_path):
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    pkg = load_package(monkeypatch)
    monkeypatch.setattr(pkg, "library_path", lambda: tmp_path / "missing.so")
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(ImportError, match="reinstall scrimmage-lockstep"):
        pkg.loadExtension()
    assert "scrimmage_lockstep.grapple" not in sys.modules   # nothing installed


def test_load_extension_survives_a_failing_grapple_helper(monkeypatch, tmp_path):
    fakes = Fakes()
    for name, mod in fakes.modules().items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "rev", None)
    fakes.hal.loadOneExtension = lambda path: 0
    pkg = load_package(monkeypatch)
    lib = tmp_path / "lib.so"
    lib.write_bytes(b"")
    monkeypatch.setattr(pkg, "library_path", lambda: lib)
    helper = importlib.import_module("scrimmage_lockstep.grapple")

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(helper, "install", boom)
    pkg.loadExtension()  # doesn't raise


# ---------------------------------------------------------------------------------------------------- readings


def test_none_until_both_written_and_a_loop_ran(env):
    lc = env.g.LaserCAN(7)
    assert lc.get_measurement() is None  # the real class's answer with no CAN frames
    env.loop()
    assert lc.get_measurement() is None
    env.write(7, mm=400)
    env.loop()
    assert lc.get_measurement() is None  # status not written yet
    env.write(7, status=0)
    assert lc.get_measurement() is None  # written, but no robot loop since
    env.loop()
    m = lc.get_measurement()
    assert (m.status, m.distance_mm, m.ambient) == (0, 400, 0)
    assert m.mode is env.g.LaserCanRangingMode.Short and m.budget is env.g.LaserCanTimingBudget.TB33ms
    assert (m.roi.x, m.roi.y, m.roi.w, m.roi.h) == (8, 8, 16, 16)
    assert re.fullmatch(r"Measurement\(status=0, distance_mm=400, .*\)", repr(m))


def test_short_ranging_from_the_start_caps_at_its_reach(env):
    lc = env.g.LaserCAN(7)
    env.loop()
    env.write(7, mm=2000, status=0)
    env.loop()
    m = lc.get_measurement()
    assert (m.status, m.distance_mm) == (2, 1300) and m.mode is env.g.LaserCanRangingMode.Short
    env.write(7, mm=1300)
    env.loop()
    assert lc.get_measurement().status == 0  # at the reach: still seen


def test_long_ranging_reaches_4_m(env):
    lc = env.g.LaserCAN(7)
    r = lc.set_range(env.g.LaserCanRangingMode.Long)
    assert r.error is None and r.ok == ()
    env.loop()
    env.write(7, mm=2000, status=0)
    env.loop()
    m = lc.get_measurement()
    assert (m.status, m.distance_mm) == (0, 2000) and m.mode is env.g.LaserCanRangingMode.Long
    env.write(7, mm=4500)
    env.loop()
    assert (lc.get_measurement().status, lc.get_measurement().distance_mm) == (2, 4000)
    env.write(7, mm=4000, status=2)  # scrimmage's nothing: no target at the reach
    env.loop()
    assert (lc.get_measurement().status, lc.get_measurement().distance_mm) == (2, 4000)
    lc.set_range(env.g.LaserCanRangingMode.Short)  # set after: short again
    m = lc.get_measurement()
    assert (m.status, m.distance_mm) == (2, 1300) and m.mode is env.g.LaserCanRangingMode.Short


def test_budget_and_roi_are_recorded_and_mirrored(env):
    lc = env.g.LaserCAN(7)
    assert lc.set_timing_budget(env.g.LaserCanTimingBudget.TB100ms).error is None
    roi = env.g.LaserCanRoi(4, 6, 8, 10)
    assert lc.set_roi(roi).error is None
    roi.x = 1  # a copy was recorded
    env.loop()
    env.write(7, mm=500, status=0)
    env.loop()
    m = lc.get_measurement()
    assert m.budget is env.g.LaserCanTimingBudget.TB100ms
    assert (m.roi.x, m.roi.y, m.roi.w, m.roi.h) == (4, 6, 8, 10)


@pytest.mark.parametrize("call", ["set_range", "set_timing_budget", "set_roi"])
def test_a_setter_rejects_a_wrong_type_like_the_real_one(env, call):
    lc = env.g.LaserCAN(7)
    with pytest.raises(TypeError, match="object cannot be converted to"):
        getattr(lc, call)(1)


@pytest.mark.parametrize("bad, error", [(-1, OverflowError), (256, OverflowError), ("7", TypeError), (7.0, TypeError)])
def test_the_constructor_takes_a_u8_like_the_real_one(env, bad, error):
    with pytest.raises(error):
        env.g.LaserCAN(bad)


def test_two_lasercans_read_their_own(env):
    a, b = env.g.LaserCAN(7), env.g.LaserCAN(9)
    env.loop()
    env.write(7, mm=400, status=0)
    env.write(9, mm=900, status=0)
    env.loop()
    assert a.get_measurement().distance_mm == 400 and b.get_measurement().distance_mm == 900


def test_the_same_id_twice_is_said_and_reads_none(env, capsys):
    a, b = env.g.LaserCAN(7), env.g.LaserCAN(7)
    env.loop()
    out = capsys.readouterr().out
    assert P + "LaserCAN 7 is not simulated: can't create its scrimmage device ScrimmageGrapple:LaserCAN[7]" in out
    env.write(7, mm=400, status=0)
    env.loop()
    assert a.get_measurement().distance_mm == 400 and b.get_measurement() is None


# ---------------------------------------------------------------------------------------------------- problems


def test_a_missing_hal_api_is_said_once_and_reads_none(monkeypatch, capsys):
    fakes = Fakes()
    del fakes.hal_sim.registerSimPeriodicBeforeCallback
    helper = load_helper(monkeypatch, fakes)
    helper.install()
    a, b = fakes.g.LaserCAN(7), fakes.g.LaserCAN(9)  # doesn't raise
    assert capsys.readouterr().out == P + "unavailable: no hal.simulation.registerSimPeriodicBeforeCallback\n"
    assert a.get_measurement() is None and b.get_measurement() is None
    assert a.set_range(fakes.g.LaserCanRangingMode.Long).error is None


def test_a_failing_robot_loop_is_said_once_and_reads_none(env, capsys):
    lc = env.g.LaserCAN(7)
    env.loop()
    env.write(7, mm=400, status=0)
    env.loop()
    assert lc.get_measurement().distance_mm == 400
    env.devices["ScrimmageGrapple:LaserCAN[7]"].values["status"].get = lambda: 1 / 0
    env.loop()
    env.loop()
    out = capsys.readouterr().out
    assert out.endswith(P + "unavailable: the robot-loop step: ZeroDivisionError: division by zero\n")
    assert out.count("unavailable") == 1
    assert lc.get_measurement() is None


def test_a_missing_libgrapplefrc_type_leaves_the_class_alone(monkeypatch, capsys):
    fakes = Fakes(missing=("LaserCanRoi",))
    original = fakes.g.LaserCAN
    helper = load_helper(monkeypatch, fakes)
    helper.install()
    assert capsys.readouterr().out == P + "unavailable: no libgrapplefrc.LaserCanRoi\n"
    assert fakes.g.LaserCAN is original


@pytest.mark.parametrize("body, reason", [
    ("raise RuntimeError('broken build')", "RuntimeError: broken build"),
    ("import zzz_no_such_module", "ModuleNotFoundError: No module named 'zzz_no_such_module'"),
])
def test_a_broken_libgrapplefrc_is_unavailable(monkeypatch, capsys, tmp_path, body, reason):
    (tmp_path / "libgrapplefrc").mkdir()
    (tmp_path / "libgrapplefrc" / "__init__.py").write_text(body + "\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "libgrapplefrc", raising=False)
    helper = load_helper(monkeypatch)
    helper.install()  # doesn't raise
    assert capsys.readouterr().out == P + "unavailable: " + reason + "\n"
    monkeypatch.delitem(sys.modules, "libgrapplefrc", raising=False)


def test_the_helper_constants_match_the_runtime_and_the_java_helper():
    """The status values are the ones scrimmage writes, and the reaches and defaults the Java helper's."""
    from scrimmage.sensors import runtime
    spec = importlib.util.spec_from_file_location("_grapple_helper_standalone", PKG_DIR / "grapple.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    java = (ROOT / "scrimmage" / "java" / "agent" / "src" / "scrimmage" / "agent" / "ScrimmageGrapple.java").read_text()
    consts = {k: int(v) for k, v in re.findall(r"static final int (\w+) = (\d+);", java)}
    assert mod.STATUS_VALID == runtime.LASERCAN_STATUS_VALID == consts["STATUS_VALID"]
    assert mod.STATUS_NO_TARGET == runtime.LASERCAN_STATUS_NO_TARGET == consts["STATUS_NO_TARGET"]
    for name in ("LONG_REACH_MM", "SHORT_REACH_MM", "DEFAULT_BUDGET_MS"):
        assert getattr(mod, name) == consts[name], name
    assert mod.DEFAULT_ROI == tuple(int(v) for v in re.search(r"DEFAULT_ROI = \{([\d, ]+)\}", java).group(1).split(","))


# ---------------------------------------------------------------------------------------------------- the real package


def test_the_real_package_gets_the_stand_in_and_its_own_types(monkeypatch, capsys):
    """With libgrapplefrc-py itself: the module's class (and its native submodule's) is the stand-in, and a
    measurement carries libgrapplefrc's own mode, budget and ROI types."""
    g = pytest.importorskip("libgrapplefrc")
    native = sys.modules.get("libgrapplefrc.libgrapplefrc")
    monkeypatch.setattr(g, "LaserCAN", g.LaserCAN)  # restored after the test
    if native is not None:
        monkeypatch.setattr(native, "LaserCAN", native.LaserCAN)
    fakes = Fakes()
    monkeypatch.setitem(sys.modules, "hal", fakes.hal)
    monkeypatch.setitem(sys.modules, "hal.simulation", fakes.hal_sim)
    helper = load_helper(monkeypatch)
    helper.install()
    assert g.LaserCAN is helper.LaserCAN and (native is None or native.LaserCAN is helper.LaserCAN)
    lc = g.LaserCAN(7)
    fakes.loop()
    fakes.write(7, mm=400, status=0)
    lc.set_range(g.LaserCanRangingMode.Long)
    lc.set_timing_budget(g.LaserCanTimingBudget.TB50ms)
    lc.set_roi(g.LaserCanRoi(1, 2, 3, 4))
    fakes.loop()
    m = lc.get_measurement()
    assert (m.status, m.distance_mm) == (0, 400)
    assert isinstance(m.mode, g.LaserCanRangingMode) and m.mode == g.LaserCanRangingMode.Long
    assert isinstance(m.budget, g.LaserCanTimingBudget) and int(m.budget) == 50
    assert isinstance(m.roi, g.LaserCanRoi) and (m.roi.x, m.roi.y, m.roi.w, m.roi.h) == (1, 2, 3, 4)
    with pytest.raises(TypeError):
        lc.set_range(1)
    capsys.readouterr()
