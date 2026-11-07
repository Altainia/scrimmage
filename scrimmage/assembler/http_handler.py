"""The editor's HTTP side: _Handler routes each request (the API in server.py's docstring, the static page files
and the blank forms) to the App, and answers with JSON, a file or an error. App.start serves it; this module never
imports server.py at run time.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlparse

from .. import i18n, settings
from ..measure import forms
from . import part_session
from .bench_session import BenchSession, SessionError
from .server_common import ApiError

if TYPE_CHECKING:
    from .server import App


STATIC = Path(__file__).resolve().parent / "static"
MAX_BODY = 1_000_000
_FORM = re.compile(r"/forms/([a-z]+)\.html")
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
          ".png": "image/png", ".txt": "text/plain; charset=utf-8", ".map": "application/json",
          ".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8",
          ".log": "text/plain; charset=utf-8", ".toml": "text/plain; charset=utf-8", ".gif": "image/gif",
          ".cur": "image/x-icon"}


class _Bounded:
    """At most `length` bytes of a request body; drain() reads and drops what is left, so an upload refused before
    its body was read (a bad name, a part that can't take it) still gets its answer: a server that closes with the
    body unread resets the connection, and the browser (on Windows above all) shows a network error instead."""

    def __init__(self, stream, length: int):
        self._stream, self.left = stream, length

    def read(self, size: int) -> bytes:
        data = self._stream.read(min(size, self.left)) if self.left > 0 else b""
        self.left -= len(data)
        return data

    def drain(self) -> None:
        try:
            while self.left > 0 and self.read(1 << 20):
                pass
        except OSError:   # the client went away: nothing to answer
            pass


class _Handler(BaseHTTPRequestHandler):
    app: App
    protocol_version = "HTTP/1.1"
    server_version = "scrimmage-assembler"

    def log_message(self, *a) -> None:
        pass

    def _send(self, status: int, payload, ctype: str = "application/json") -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(status)
        if status >= 400:   # an unread request body must not be parsed as the next request
            self.close_connection = True
            self.send_header("Connection", "close")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _authorized(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        tok = self.headers.get("X-Scrimmage-Token") or ""
        return secrets.compare_digest(tok.encode(), self.app.token.encode())

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, i18n.t("bad Content-Length")) from None
        if n > MAX_BODY:
            raise ApiError(413, i18n.t("request too large"))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, i18n.t("body is not JSON")) from None
        if not isinstance(body, dict):
            raise ApiError(400, i18n.t("body must be a JSON object"))
        return body

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        if method == "HEAD" and url.path.startswith("/api/"):
            method = "GET"
        try:
            if url.path.startswith("/api/"):
                if not self._authorized():
                    raise ApiError(403, i18n.t("missing or wrong token"))
                self._send(*self._api(method, url))
            elif method in ("GET", "HEAD"):
                self._static(url.path)
            else:
                raise ApiError(405, i18n.t("method not allowed"))
        except (ApiError, SessionError) as e:
            self._send(e.status, {"ok": False, "error": e.message,
                                  "errors": [{"field": getattr(e, "field", "status"), "message": e.message,
                                              **getattr(e, "detail", {})}]})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:   # never take the server down on a request
            logging.getLogger("scrimmage.assembler").exception("unexpected error handling %s", self.path)
            try:
                msg = f"{type(e).__name__}: {e}"
                self._send(500, {"ok": False, "error": msg, "errors": [{"field": "status", "message": msg}]})
            except OSError:
                pass

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    @staticmethod
    def _since(url) -> int | None:
        since = parse_qs(url.query).get("since", [None])[0]
        try:
            return int(since) if since is not None else None
        except ValueError:
            raise ApiError(400, i18n.t("since must be an integer")) from None

    def _api(self, method: str, url) -> tuple:
        app, path = self.app, url.path
        if path.startswith("/api/app/"):
            return self._app_api(method, url)
        if path == "/api/settings":
            if method == "POST":
                return 200, app.set_settings(self._body())
            return 200, app.settings_state(parse_qs(url.query).get("catalog", [None])[0])
        if path.startswith("/api/bench/"):
            return self._bench_api(method, url, app.need_bench())
        if path == "/api/part/upload" and method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ApiError(400, i18n.t("bad Content-Length")) from None
            if n > part_session.MAX_UPLOAD:   # (not read: a body this large may be cut off)
                raise ApiError(413, i18n.t("that file is too large to upload (over 512 MB)"))
            body = _Bounded(self.rfile, n)
            try:
                files = None
                if self.headers.get("X-Files"):
                    try:
                        files = [(f["name"], f["size"]) for f in json.loads(unquote(self.headers["X-Files"]))]
                    except (ValueError, TypeError, KeyError):
                        raise ApiError(400, i18n.t("X-Files must be a list of {name_size_form}",
                                                   name_size_form="{name, size}")) from None
                    if not files:
                        raise ApiError(400, i18n.t("no files were sent"))
                return app.upload(body, n, self.headers.get("X-Filename"), files,
                                  again=self.headers.get("X-Import-Again") == "1")
            finally:
                body.drain()   # a refusal answers after the body arrived, never by resetting the connection
        if path.startswith("/api/part/"):
            return self._part_api(method, url, app.need_part())
        if path.startswith("/api/test/"):
            return self._test_api(method, url)
        if path.startswith("/api/measure/"):
            return self._measure_api(method, url)
        if path.startswith(("/api/robot/", "/api/scenario/", "/api/live/")):
            app.need_robot()
        if method == "GET":
            if path == "/api/robot/state":
                return 200, app.wait_state(self._since(url))
            if path == "/api/robot/view":
                return 200, app.view()
            if path == "/api/robot/library":
                return 200, app.library()
            if path == "/api/robot/code":
                return 200, app.code_state()
            if path == "/api/robot/code/fix":
                return 200, app.wait_fix(self._since(url))
            if path == "/api/robot/devices":
                return 200, app.live.devices(self._since(url))
            if path == "/api/scenario/state":
                return 200, app.wait_scenario_state(self._since(url))
            if path == "/api/scenario/track":
                return 200, app.scenario_track(parse_qs(url.query).get("step", [None])[0])
            if path == "/api/scenario/signals":
                return 200, app.scenario_signals()
            if path == "/api/scenario/view":
                return 200, app.scenario_view()
            if path == "/api/scenario/configs":
                return 200, app.scenario_configs()
            if path == "/api/scenario/runs":
                return 200, app.wait_runs(self._since(url))
            if path == "/api/live/info":
                return 200, app.live_info()
            if path == "/api/live/view":
                return 200, app.world_view()
            if path == "/api/live/signals":
                return 200, app.robot_signals()
            if path.startswith("/api/scenario/run/"):
                f = app.run_file(unquote(path[len("/api/scenario/run/"):]))
                return 200, f.read_bytes(), _TYPES.get(f.suffix.lower(), "application/octet-stream")
        elif method == "POST":
            body = self._body()
            if path == "/api/robot/edit":
                return app.edit(body)
            if path in ("/api/robot/undo", "/api/robot/redo"):
                return app.history(path.rsplit("/", 1)[1], body)
            if path == "/api/robot/folder":
                return 200, app.add_folder(body.get("path"))
            if path == "/api/robot/code":
                return app.code_set(body)
            if path == "/api/robot/code/fix":
                return 200, app.code_fix(body)
            if path == "/api/robot/check":
                return app.robot_check(body)
            if path == "/api/scenario/edit":
                return app.scenario_edit(body)
            if path in ("/api/scenario/undo", "/api/scenario/redo"):
                return app.scenario_history(path.rsplit("/", 1)[1], body)
            if path == "/api/scenario/run":
                return app.run(body)
            if path == "/api/scenario/stop":
                return 200, app.stop_run()
            if path == "/api/scenario/open":
                return 200, app.open_scenario(body.get("path"))
            if path == "/api/live/start":
                return app.live_start(body)
            if path == "/api/live/stop":
                return 200, app.live_stop()
            if path == "/api/live/save_recording":
                return app.save_recording(body)
        raise ApiError(404, i18n.t("no such endpoint"))

    def _app_api(self, method: str, url) -> tuple:
        app, path = self.app, url.path
        if method == "GET" and path == "/api/app/state":
            return 200, app.app_state()
        if method == "GET" and path == "/api/app/browse":
            return 200, app.browse(parse_qs(url.query).get("dir", [None])[0])
        if method == "POST" and path == "/api/app/open":
            return 200, app.open_path(self._body().get("path"))
        if method == "POST" and path == "/api/app/new":
            return 200, app.new(self._body())
        if method == "GET" and path == "/api/app/folders":
            return 200, app.get_folders()
        if method == "POST" and path == "/api/app/folders":
            return 200, app.set_folder(self._body())
        raise ApiError(404, i18n.t("no such endpoint"))

    def _bench_api(self, method: str, url, bench: BenchSession) -> tuple:
        path, q = url.path, parse_qs(url.query)
        if method == "GET":
            if path == "/api/bench/state":
                return 200, bench.wait_state(self._since(url))
            if path == "/api/bench/fields":
                return 200, bench.fields()
            if path == "/api/bench/view":
                return 200, bench.view(q.get("file", [None])[0])
            if path == "/api/bench/idea":
                return 200, bench.idea(q.get("file", [None])[0])
            if path == "/api/bench/file":
                f = bench.file(q.get("path", [None])[0])
                return 200, f.read_bytes(), _TYPES.get(f.suffix.lower(), "application/octet-stream")
            if path == "/api/bench/jobs":
                return 200, self.app.bench_jobs.wait_jobs(self._since(url))
        elif method == "POST":
            body = self._body()
            if path == "/api/bench/edit":
                return bench.edit(body)
            if path in ("/api/bench/undo", "/api/bench/redo"):
                return bench.history(path.rsplit("/", 1)[1], body)
            if path == "/api/bench/new_idea":
                return bench.new_idea(body)
            if path in ("/api/bench/run", "/api/bench/sweep", "/api/bench/compare", "/api/bench/gallery"):
                return self.app.bench_jobs.submit(bench, path.rsplit("/", 1)[1], body)
        raise ApiError(404, i18n.t("no such endpoint"))

    def _test_api(self, method: str, url) -> tuple:
        app, path = self.app, url.path
        if method == "POST" and path == "/api/test/new":
            return 200, app.new_test(self._body())
        if method == "GET" and path == "/api/test/state":
            return 200, app.wait_test_state(self._since(url))
        test = app.need_test()
        if method == "GET" and path.startswith("/api/test/run/"):
            rest = unquote(path[len("/api/test/run/"):])
            for what, ctype in (("view", "application/json"), ("motion", "application/octet-stream")):
                if rest.endswith("/" + what):
                    try:
                        data = test.run_file(rest[:-len(what) - 1], what).read_bytes()
                    except FileNotFoundError:   # pruned meanwhile
                        raise ApiError(404, i18n.t("that run is no longer kept")) from None
                    return 200, data, ctype
            return 200, test.run_result(rest)
        if method == "POST":
            body = self._body()
            if path == "/api/test/save":
                return app.test_save(body)
            if path in ("/api/test/run", "/api/test/sweep", "/api/test/spread"):
                return app.test_start(path.rsplit("/", 1)[1], body)
            if path == "/api/test/cancel":
                return 200, test.cancel()
            if path == "/api/test/grip":
                return app.test_grip(body)
        raise ApiError(404, i18n.t("no such endpoint"))

    def _measure_api(self, method: str, url) -> tuple:
        app, path = self.app, url.path
        if method == "GET" and path == "/api/measure/state":
            return 200, app.wait_measure_state(self._since(url))
        measure = app.need_measure()
        if method == "POST":
            body = self._body()
            if path == "/api/measure/summary":
                return 200, measure.check(body)
            if path == "/api/measure/fit":
                return 200, measure.fit(body)
            if path == "/api/measure/save":
                return measure.save(body)
        raise ApiError(404, i18n.t("no such endpoint"))

    def _part_api(self, method: str, url, part) -> tuple:
        path = url.path
        if method == "GET":
            if path == "/api/part/state":
                return 200, part.wait_state(self._since(url))
            if path == "/api/part/view":
                return 200, part.view()
            if path == "/api/part/materials":
                return 200, part_session.materials()
        elif method == "POST":
            body = self._body()
            if path == "/api/part/edit":
                return part.edit(body)
            if path in ("/api/part/undo", "/api/part/redo"):
                return part.history(path.rsplit("/", 1)[1], body)
            if path == "/api/part/axis":
                return 200, part.axis(body)
            if path == "/api/part/reimport":
                return part.reimport(body)
        raise ApiError(404, i18n.t("no such endpoint"))

    def _static(self, urlpath: str) -> None:
        form = _FORM.fullmatch(urlpath)
        if form is not None:   # a blank form to print: a plain link opens it, so it needs no token (it holds no data)
            if form.group(1) not in forms.TEST:
                raise ApiError(404, i18n.t("not found"))
            self._send(200, forms.form_html(form.group(1)).encode("utf-8"), _TYPES[".html"])
            return
        rel = "index.html" if urlpath in ("", "/") else urlpath.lstrip("/")
        root = STATIC.resolve()
        try:
            target = (root / rel).resolve()
            target.relative_to(root)
        except (ValueError, OSError):
            raise ApiError(404, i18n.t("not found")) from None
        if not target.is_file():
            raise ApiError(404, i18n.t("not found"))
        body = target.read_bytes()
        if rel == "index.html":    # the page starts in the Theme setting: no light flash before theme.js runs
            body = body.replace(b'data-theme="system"', f'data-theme="{settings.load().theme}"'.encode(), 1)
        self._send(200, body, _TYPES.get(target.suffix.lower(), "application/octet-stream"))
