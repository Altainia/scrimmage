"""The App's Test and Measure tabs (TestMeasureMixin): their long polls, saving a block test, its grip and runs,
making a new test, and the Part tab's "Test it" (a stand-in robot and a test for a CAD import's parts).
A mixin of server.py's App: it has no __init__ (App.__init__ sets every attribute it uses) and never imports server.py.
"""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

from .. import i18n
from ..blocks.program import Program as TestProgram, dumps as dumps_test
from ..cad import CadError
from ..cad.build import part_folder
from ..cad.names import sanitize
from ..parts.model import PartError, load_part
from . import library, part_session, test_session
from .server_common import POLL_SECONDS, ApiError, _chassis_root_body, classify


class TestMeasureMixin:
    """The Test and Measure tabs: waiting for their state, saving, starting runs and making tests."""

    # -- test -------------------------------------------------------------------------------
    def wait_test_state(self, since: int | None) -> dict:
        """The test's state once its version moves from `since` (or after POLL_SECONDS); each second the files are
        read again, so an outside edit of the test or its robot shows."""
        end = time.monotonic() + POLL_SECONDS
        while True:
            with self._lock:
                self._sync()
                test = self.need_test()
            if test.wait(since, end):
                return self.need_test().state()   # another file opened meanwhile: its state (or 404)

    # -- measure ----------------------------------------------------------------------------
    def wait_measure_state(self, since: int | None) -> dict:
        """The measurement file's state once its version moves from `since` (or after POLL_SECONDS); each second the
        file is read again, so an outside edit shows."""
        end = time.monotonic() + POLL_SECONDS
        while True:
            measure = self.need_measure()
            measure.reload_if_changed()
            if measure.wait(since, end):
                return self.need_measure().state()   # another file opened meanwhile: its state (or 404)

    def test_save(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._sync()
            test = self.need_test()
            status, out = test.save(body)
            if status == 200 and self._point_test_robot():   # the test now names another robot
                self._revalidate()
        return status, out

    def test_grip(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._sync()
            test = self.need_test()
            status, out = test.grip(body)
            self._sync()    # the part folder changed: the robot is built again, and the test's palette with it
            return status, {**out, "version": test.version}

    def test_start(self, kind: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._sync()   # the problems as the files are now
            return self.need_test().start(kind, body)

    def new_test(self, body: dict) -> dict:
        """Write `<robot stem>.test.toml` next to the robot config `robot` (default: the open robot), or
        `<stem>-2.test.toml` and so on when that is taken, with starter blocks and hooks, and open it. With
        `from_part: true` it is the Part tab's "Test it": see test_from_part."""
        if body.get("from_part") is True:
            return self.test_from_part()
        if body.get("robot") is not None:
            robot = self._user_path(body.get("robot"), "the robot config the test is for")
            if not robot.is_file() or classify(robot) != "robot":
                raise ApiError(400, i18n.t("{path} is not a robot config", path=self._display(robot)))
        else:
            with self._lock:
                robot = self.robot.path if self.mode == "robot" and self.robot is not None else None
            if robot is None:
                raise ApiError(400, i18n.t("say which robot config the test is for (robot)"))
        with self._lock:
            self._check_can_open()
        text = self._starter_test(robot)
        for k in range(1, 1000):
            p = robot.parent / (f"{robot.stem}.test.toml" if k == 1 else f"{robot.stem}-{k}.test.toml")
            try:
                if test_session.write_new(p, text):
                    break
            except OSError as e:
                raise ApiError(400, i18n.t("can't write {path}: {why}", path=self._display(p), why=e.strerror or e)) from None
        else:
            raise ApiError(400, i18n.t("there are too many tests of {name} already; delete some", name=robot.name))
        return self.open_path(str(p))

    def _starter_test(self, robot: Path, folder: Path | None = None) -> str:
        """A new test's text for the robot config `robot` (the test in `folder`, default the robot's): starter blocks,
        hooks and hopper from the robot's palette (a robot that doesn't load gets one wait block, and its problem
        shows once the test is open)."""
        from ..blocks.palette import palette
        from ..config import build_world, load_config
        folder = folder or robot.parent
        try:
            world = build_world(load_config(robot))
            pal, roots = palette(world, []), test_session.part_roots(world)
        except Exception:     # a robot that doesn't build: the open test says why
            pal, roots = None, {}
        return dumps_test(TestProgram(folder / "x.test.toml", Path(test_session.config_path(robot, folder)),
                                      hooks=test_session.starter_hooks(pal), blocks=test_session.starter_blocks(pal),
                                      hopper=test_session.starter_hopper(pal, roots) or None))

    def test_from_part(self) -> dict:
        """The Part tab's "Test it": make (once) `<import name>-standin.toml` next to the import's parts folder, the
        built parts on it where the CAD has them (test_session.standin_config), and `<import name>.test.toml` beside
        it with starter blocks, then open the test. Pressed again it opens the same test: nothing is made twice.
        `plain_field` is true when it made the stand-in on the plain field (no body like a hook or a hopper picked a
        field for it), so the page can say where it starts."""
        with self._lock:
            part = self.part
            if self.mode != "part" or not isinstance(part, part_session.PartSession):
                raise ApiError(400, i18n.t("Test it works on a part made from a CAD import: open one in the Part tab"))
            st, imp = part.state_and_settings()
        if st["read_only_reason"]:
            raise ApiError(409, i18n.t("the part can't be tested yet: {why}", why=st["read_only_reason"]))
        if st["build"]["state"] == "building":
            raise ApiError(409, i18n.t("the parts are still updating; try again when they're up to date"))
        if st["build"]["state"] == "failed" or imp is None:
            raise ApiError(409, i18n.t("the parts didn't build: {why}",
                                       why=st["build"]["message"] or i18n.t("see the Part tab")))
        imp_path = Path(st["path"])
        # next to the parts folder the import file is in (or beside an import file kept elsewhere)
        home = imp_path.parent.parent if imp_path.parent.name == "parts" else imp_path.parent
        parts, slots, roots = [], {}, {}
        for p in st["parts"]:
            try:
                folder = part_folder(imp, p["name"], imp_path)
            except CadError as e:
                raise ApiError(409, str(e)) from None
            marker = library.cad_marker(folder)
            if marker is None or not (folder / "part.toml").is_file():
                raise ApiError(409, i18n.t("the part {name} isn't built yet; try again when the parts are up to date",
                                           name=p["name"]))
            try:
                built = load_part(folder)
                slots[marker["part"]] = [m.slot for m in built.motors]
                roots[marker["part"]] = next((b.name for b in built.bodies if b.parent is None), None)
            except (PartError, OSError) as e:
                raise ApiError(409, i18n.t("the part {name} can't be read: {why}", name=p["name"], why=e)) from None
            parts.append((test_session.config_path(folder, home), marker))
        if not parts:
            raise ApiError(409, i18n.t("this import makes no parts yet: put its pieces in a body first"))
        with self._lock:
            self._check_can_open()
        name = sanitize(st["name"]) or "part"
        standin, test = home / f"{name}-standin.toml", home / f"{name}.test.toml"
        added: list[str] = []
        plain = False     # a new stand-in on the plain field: no body is like a hook or a hopper (the page says so)
        try:
            new = test_session.standin_config(parts, _chassis_root_body(), slots, roots)
            if test_session.write_new(standin, new):
                plain = "range" not in tomllib.loads(new)
            else:
                text, added = test_session.add_to_standin(standin.read_text(encoding="utf-8"), parts,
                                                          _chassis_root_body(), slots)
                if added:
                    test_session.replace_text(standin, text)
            if not test.exists():
                test_session.write_new(test, self._starter_test(standin, home))
        except OSError as e:
            raise ApiError(400, i18n.t("can't write next to {path}: {why}", path=self._display(home),
                                       why=e.strerror or e)) from None
        except (ValueError, KeyError, TypeError) as e:   # a hand-edited stand-in that can't be read as one
            raise ApiError(409, i18n.t("can't add the new parts to {name}: {why}", name=standin.name, why=e)) from None
        return {**self.open_path(str(test)), "added": added, "plain_field": plain}
