"""A stand-in for the FRC Driver Station app, for tests on Linux: it talks to a robot program's DS socket extension
(`robotpy sim --ds-socket`, GradleRIO's `wpi.sim.addDriverstation()`) as the real DS does.

It follows allwpilib simulation/halsim_ds_socket DSCommPacket.cpp (2026 branch):

- **UDP to the robot (port 1110), every 20 ms:** a 6-byte header, then tags.
  - Header: the sequence number (2 bytes, big-endian), the version 0x01, the control byte, the request byte and the
    station (0-5: red1, red2, red3, blue1, blue2, blue3; the extension adds 1 for the HAL's station IDs).
  - Control byte: the mode in the low bits (0 teleop, 0x01 test, 0x02 auto), 0x04 enabled, 0x08 FMS attached,
    0x80 E-stop.
  - Request byte: 0x80 ("normal") once a reply from the robot has come, 0 before, as the DS app does (LibDS:
    cRequestNormal, cRequestUnconnected). The extension sets DsAttached from it (request & 0xF0) and the HAL sets
    DsAttached true again as it takes the packet, so for a 0 the robot reports >ds false, then >ds true: a fake DS
    that always sent 0 made the robot flip >ds every packet, and scrimmage briefly saw no DS (Bridge._ds_reported).
  - Tags: [size][id][data], where size counts the id and the data (not itself).
  - The joystick tag (id 0x0C): [axis count][one signed byte per axis: v * 127 when positive, v * 128 when
    negative][button count][the buttons as a big-endian bitmask, (count + 7) // 8 bytes, button 1 in bit 0]
    [POV count][2 bytes per POV]. The extension gives the n-th joystick tag to joystick n, so every port up to the
    highest one set gets a tag; a tag with no data (size 1) is a joystick with nothing.
- **UDP from the robot (port 1150):** one reply to each packet, sent to the packet's address on port 1150.
- **TCP to the robot (port 1740):** tags as [size, 2 bytes big-endian][id][data]; game data is id 0x0E.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time

UDP_ROBOT, UDP_DS, TCP_ROBOT = 1110, 1150, 1740
REQUEST_UNCONNECTED, REQUEST_NORMAL = 0x00, 0x80
PERIOD = 0.02
MODE_BITS = {"teleop": 0x00, "test": 0x01, "auto": 0x02}
ENABLED, FMS, ESTOP = 0x04, 0x08, 0x80
STATIONS = ("red1", "red2", "red3", "blue1", "blue2", "blue3")
JOYSTICK_TAG, GAME_DATA_TAG = 0x0C, 0x0E


def skip_if_ds_ports_taken() -> None:
    """Skip the test when this computer's Driver Station ports are already taken. A real-DS robot program binds UDP
    1110 and TCP 1740 for the whole machine, and the fake DS binds UDP 1150, so another test run (or a robot program)
    holding one isn't this test failing."""
    import pytest
    from scrimmage.session import ds_ports_problem
    problem = ds_ports_problem()
    if problem is None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                sock.bind(("0.0.0.0", UDP_DS))
            except OSError:
                problem = f"UDP port {UDP_DS} is already in use"
    if problem:
        msg = f"the Driver Station ports are taken on this computer (another test run?): {problem}"
        if os.environ.get("CI"):   # on CI a held port would skip every real-DS test and still show green
            pytest.fail(msg)
        pytest.skip(msg)


def _axis(v: float) -> int:
    v = max(-1.0, min(1.0, float(v)))
    return round(v * 127) if v >= 0 else round(v * 128)


def joystick_tag(axes: list[float], buttons: list[bool]) -> bytes:
    data = bytes([len(axes)]) + struct.pack(f">{len(axes)}b", *[_axis(a) for a in axes])
    mask = sum(1 << i for i, b in enumerate(buttons) if b)
    n = (len(buttons) + 7) // 8
    data += bytes([len(buttons)]) + mask.to_bytes(n, "big") + bytes([0])   # no POVs
    return bytes([len(data) + 1, JOYSTICK_TAG]) + data


class FakeDS:
    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self._control, self._station = 0, 0
        self._sticks: dict[int, tuple[list[float], list[bool]]] = {}
        self._game_data: bytes | None = None    # waiting to go out on TCP
        self._replies = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._udp: socket.socket | None = None
        self._tcp: socket.socket | None = None

    def set(self, enabled: bool, mode: str, station: str, fms: bool = False, estop: bool = False) -> None:
        control = MODE_BITS[mode] | (ENABLED if enabled else 0) | (FMS if fms else 0) | (ESTOP if estop else 0)
        with self._lock:
            self._control, self._station = control, STATIONS.index(station)

    def set_joystick(self, port: int, axes: list[float], buttons: list[bool]) -> None:
        with self._lock:
            self._sticks[int(port)] = (list(axes), list(buttons))

    def send_game_data(self, s: str) -> None:
        """Sent on the TCP link (connected when the robot program accepts it)."""
        with self._lock:
            self._game_data = s.encode()

    def replies(self) -> int:
        with self._lock:
            return self._replies

    def start(self) -> FakeDS:
        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._udp.bind((self.host, UDP_DS))
        self._udp.setblocking(False)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fake-ds", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None
        for sock in (self._udp, self._tcp):
            if sock is not None:
                sock.close()
        self._udp = self._tcp = None

    def _packet(self, seq: int) -> bytes:
        with self._lock:
            request = REQUEST_NORMAL if self._replies else REQUEST_UNCONNECTED
            out = struct.pack(">HBBBB", seq, 0x01, self._control, request, self._station)
            top = max(self._sticks, default=-1)
            for port in range(top + 1):
                stick = self._sticks.get(port)
                out += joystick_tag(*stick) if stick else bytes([1, JOYSTICK_TAG])
        return out

    def _send_tcp(self) -> None:
        with self._lock:
            data = self._game_data
        if data is None:
            return
        if self._tcp is None:
            try:
                self._tcp = socket.create_connection((self.host, TCP_ROBOT), timeout=0.2)
            except OSError:
                return
        self._tcp.sendall(struct.pack(">HB", len(data) + 1, GAME_DATA_TAG) + data)
        with self._lock:
            if self._game_data is data:
                self._game_data = None

    def _run(self) -> None:
        seq = 0
        while not self._stop.is_set():
            seq = (seq + 1) & 0xFFFF
            try:
                self._udp.sendto(self._packet(seq), (self.host, UDP_ROBOT))
            except OSError:
                pass
            while True:
                try:
                    self._udp.recv(1024)
                except (BlockingIOError, OSError):
                    break
                with self._lock:
                    self._replies += 1
            try:
                self._send_tcp()
            except OSError:
                self._tcp = None
            time.sleep(PERIOD)
