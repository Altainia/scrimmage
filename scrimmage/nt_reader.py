"""The robot program's NetworkTables as scrimmage sees them: publish ground truth, read odometry, list and read topics.

The robot program is the NT server (port 5810 locally); scrimmage is a client. NT updates travel on the robot's
wall-clock flush timer (~100 ms), so at --speed max values lag the simulation; scenario checks hold for NT_SETTLE.
"""

from __future__ import annotations

from typing import Any

import numpy as np


class NTUnavailable(RuntimeError):
    pass


class NetworkTablesLink:
    def __init__(self, odometry_topic: str | None, server: str = "localhost", port: int = 5810):
        try:
            import ntcore
        except ImportError:
            raise NTUnavailable("NetworkTables needs pyntcore: pip install 'scrimmage[nt]'") from None
        self._ntcore = ntcore
        self.inst = ntcore.NetworkTableInstance.create()
        self._all = self._odo = self._truth = self._Pose2d = self._geometry = self._balls = self._last_balls = None
        try:
            self.inst.addConnectionListener(False, self._on_connection)   # a new server gets the balls again
            self.inst.startClient4("scrimmage")
            self.inst.setServer(server, port)
            self._all = ntcore.MultiSubscriber(self.inst, ["/"])  # every topic's values arrive, not just ones we ask for
            self._odo = self.inst.getDoubleArrayTopic(odometry_topic).subscribe([]) if odometry_topic else None
            try:
                from wpimath import geometry
            except ImportError:
                pass
            else:
                self._Pose2d = geometry.Pose2d
                self._geometry = geometry   # Pose3d for the test range's balls (publish_balls)
                self._truth = self.inst.getStructTopic("/MuJoCo/GroundTruthPose", geometry.Pose2d).publish()
        except BaseException:
            self.close()
            raise

    @property
    def connected(self) -> bool:
        return self.inst.isConnected()

    def publish_truth(self, x: float, y: float, yaw: float) -> None:
        if self._truth is not None:
            self._truth.set(self._Pose2d(x, y, yaw))

    def reset_balls(self) -> None:
        """Send the next ball poses even if they equal the last (a new or reconnected NT server)."""
        self._last_balls = None

    def _on_connection(self, event) -> None:
        if event.is_(self._ntcore.EventFlags.kConnected):
            self.reset_balls()

    def publish_balls(self, poses) -> None:
        """The test range's balls in play as a Pose3d array on /MuJoCo/Balls; `poses` is (k, 7): x, y, z and the
        quaternion w, x, y, z. The topic appears with the first call, so a robot without a range never has it. Poses
        identical to the last ones aren't sent again (balls at rest sleep and keep their exact poses; building 360
        Pose3d takes about 0.45 ms, about 6% of the 20 world steps between log rows)."""
        g = self._geometry
        if g is None:
            return
        if self._balls is None:
            self._balls = self.inst.getStructArrayTopic("/MuJoCo/Balls", g.Pose3d).publish()
        if self._last_balls is not None and np.array_equal(poses, self._last_balls):
            return
        self._last_balls = poses.copy()
        self._balls.set([g.Pose3d(g.Translation3d(p[0], p[1], p[2]), g.Rotation3d(g.Quaternion(p[3], p[4], p[5], p[6])))
                         for p in poses.tolist()])

    def odometry(self) -> tuple[float, float, float] | None:
        if self._odo is None:
            return None
        val = self._odo.get()
        return (float(val[0]), float(val[1]), float(val[2])) if len(val) >= 3 else None

    def topics(self, prefix: str = "/") -> list[dict]:
        return sorted(({"name": t.getName(), "type": t.getTypeString()} for t in self.inst.getTopics(prefix)),
                      key=lambda d: d["name"])

    def get(self, topic: str) -> Any:
        v = self.inst.getEntry(topic).getValue()
        if not v.isValid():
            raise KeyError(f"no NetworkTables topic {topic!r} (or it has no value yet)")
        val = v.value()
        if isinstance(val, (bytes, bytearray)):
            return val.hex()
        return list(val) if isinstance(val, (list, tuple)) else val

    def close(self) -> None:
        self.inst.stopClient()
        self._ntcore.NetworkTableInstance.destroy(self.inst)  # otherwise NT threads keep the process alive
