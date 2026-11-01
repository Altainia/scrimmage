"""The live view's WebSocket hub: frames, state and notices out to the editor page, inputs and commands in.

A `websockets` server on 127.0.0.1 (a random port) on its own thread and asyncio loop. A connection counts only once
it has authenticated: its Host must be 127.0.0.1 or localhost with the hub's port, its Origin the editor page's own
(http://127.0.0.1:<http port> or http://localhost:<http port>), and its first message ``{"token": "<session
token>"}`` within AUTH_TIMEOUT seconds; any other connection is closed (code 1008) with nothing sent to it.

Out: binary frames (the bytes given to publish("frame", ...)) and JSON text ``{"type": "state" | "notice", ...}``.
Per client, frames and states are latest-wins slots (a slow client skips intermediate ones) and notices are queued.
In: JSON objects from authenticated clients go to `on_message`; anything else (including JSON nested too deeply to
parse) is ignored.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import secrets
import threading
import weakref
from typing import Callable

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

log = logging.getLogger("scrimmage.assembler")

AUTH_TIMEOUT = 5.0
MAX_MESSAGE = 64 * 1024
MAX_NOTICES = 100
POLICY_VIOLATION = 1008


class _Client:
    def __init__(self, ws: ServerConnection):
        self.ws = ws
        self.frame: bytes | None = None
        self.state: str | None = None
        self.notices: collections.deque[str] = collections.deque(maxlen=MAX_NOTICES)
        self.wake = asyncio.Event()


class StreamHub:
    def __init__(self, token: str, http_port: Callable[[], int], on_message: Callable[[dict], None],
                 on_last_client_gone: Callable[[], None]):
        self._token = token
        self._http_port = http_port
        self._on_message = on_message
        self._on_last_client_gone = on_last_client_gone
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._clients: set[_Client] = set()   # authenticated; touched only on the loop
        self._refused: weakref.WeakSet[ServerConnection] = weakref.WeakSet()
        self.port = 0

    # -- lifecycle --------------------------------------------------------------------------------------------

    def start(self) -> int:
        """Starts the server on its own thread; returns its port."""
        ready = threading.Event()
        failure: list[BaseException] = []
        self._thread = threading.Thread(target=self._work, args=(ready, failure), name="live-stream", daemon=True)
        self._thread.start()
        ready.wait()
        if failure:
            raise failure[0]
        return self.port

    def close(self) -> None:
        """Closes every connection and the server; safe to call more than once."""
        loop, closing = self._loop, self._closing
        if loop is not None and closing is not None:
            try:
                loop.call_soon_threadsafe(closing.set)
            except RuntimeError:
                pass   # the loop has already closed
        if self._thread is not None:
            self._thread.join(10)
            self._thread = None

    def _work(self, ready: threading.Event, failure: list) -> None:
        try:
            asyncio.run(self._main(ready))
        except BaseException as e:  # noqa: BLE001 - reported to start() when it hasn't returned yet
            if not ready.is_set():
                failure.append(e)
            else:
                log.exception("live stream: the hub stopped")
        finally:
            self._loop = None
            ready.set()

    async def _main(self, ready: threading.Event) -> None:
        self._loop = asyncio.get_running_loop()
        self._closing = asyncio.Event()
        async with serve(self._handle, "127.0.0.1", 0, process_request=self._check_request,
                         max_size=MAX_MESSAGE, compression=None) as server:
            self.port = server.sockets[0].getsockname()[1]
            ready.set()
            await self._closing.wait()

    # -- publishing (any thread) --------------------------------------------------------------------------------

    def publish(self, kind: str, payload) -> None:
        """Send to every authenticated client: "frame" bytes, or a "state" / "notice" dict (sent as JSON with
        "type": kind). Thread-safe; a no-op while the hub isn't running."""
        if kind == "frame":
            msg = bytes(payload)
        elif kind in ("state", "notice"):
            msg = json.dumps({"type": kind, **payload}, default=str)
        else:
            raise ValueError(f"unknown message kind {kind!r}")
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._offer, kind, msg)
        except RuntimeError:
            pass   # the loop is closing

    def _offer(self, kind: str, msg) -> None:
        for c in self._clients:
            if kind == "frame":
                c.frame = msg
            elif kind == "state":
                c.state = msg
            else:
                c.notices.append(msg)
            c.wake.set()

    # -- connections (on the loop) ------------------------------------------------------------------------------

    def _check_request(self, connection: ServerConnection, request) -> None:
        """Handshake hook: remember a connection whose Host or Origin is wrong; it is closed before anything is
        sent to it."""
        port, http = self.port, self._http_port()
        host = request.headers.get("Host", "")
        origin = request.headers.get("Origin", "")
        ok = (host in (f"127.0.0.1:{port}", f"localhost:{port}")
              and origin in (f"http://127.0.0.1:{http}", f"http://localhost:{http}"))
        if not ok:
            self._refused.add(connection)
        return None

    async def _authenticate(self, ws: ServerConnection) -> bool:
        if ws in self._refused:
            self._refused.discard(ws)
            return False
        try:
            first = await asyncio.wait_for(ws.recv(), AUTH_TIMEOUT)
            token = json.loads(first).get("token") if isinstance(first, str) else None
        except (TimeoutError, ConnectionClosed, ValueError, AttributeError, RecursionError):
            return False   # RecursionError: JSON nested too deeply is malformed too
        return isinstance(token, str) and secrets.compare_digest(token.encode(), self._token.encode())

    async def _handle(self, ws: ServerConnection) -> None:
        if not await self._authenticate(ws):
            await ws.close(POLICY_VIOLATION, "not authorized")
            return
        client = _Client(ws)
        self._clients.add(client)
        sender = asyncio.ensure_future(self._send_loop(client))
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw) if isinstance(raw, str) else None
                except (ValueError, RecursionError):   # malformed, or nested too deeply to parse
                    continue
                if not isinstance(msg, dict):
                    continue
                try:
                    self._on_message(msg)
                except Exception:  # noqa: BLE001 - a bad message must not drop the connection
                    log.exception("live stream: handling a message failed")
        except ConnectionClosed:
            pass
        finally:
            sender.cancel()
            self._clients.discard(client)
            if not self._clients:
                try:
                    self._on_last_client_gone()
                except Exception:  # noqa: BLE001
                    log.exception("live stream: on_last_client_gone failed")

    async def _send_loop(self, c: _Client) -> None:
        try:
            while True:
                await c.wake.wait()
                c.wake.clear()
                while c.notices:
                    await c.ws.send(c.notices.popleft())
                if c.state is not None:
                    msg, c.state = c.state, None
                    await c.ws.send(msg)
                if c.frame is not None:
                    msg, c.frame = c.frame, None
                    await c.ws.send(msg)
        except ConnectionClosed:
            pass
