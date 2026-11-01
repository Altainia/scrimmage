import asyncio
import time

import pytest

from scrimmage.recorder import Recorder

ntcore = pytest.importorskip("ntcore")
from scrimmage.nt_reader import NetworkTablesLink  # noqa: E402

PORT = 5891


def test_recorder_paths_and_decimation():
    r = Recorder(max_points=10)
    for i in range(25):
        r.sample(i * 0.02, float(i), 0.0, (float(i), 1.0, 0.0) if i % 2 else None)
    p = r.paths()
    assert len(p["truth"]) <= 10 and p["truth"][0] == (0.0, 0.0) and p["truth"][-1][0] >= 20
    assert all(y == 1.0 for _, y in p["odometry"])
    r.reset()
    assert r.paths() == {"truth": [], "odometry": []}


def wait_for(pred, timeout=5.0):
    t0 = time.monotonic()
    while not pred():
        assert time.monotonic() - t0 < timeout, "timed out"
        time.sleep(0.05)


def test_link_reads_topics_and_odometry(tmp_path):
    srv = ntcore.NetworkTableInstance.create()
    srv.startServer(str(tmp_path / "nt.json"), "", 0, PORT)
    link = None
    try:
        pubs = [srv.getDoubleTopic("/SmartDashboard/Gyro deg").publish(),
                srv.getDoubleArrayTopic("/SmartDashboard/Field/Robot").publish()]  # a dropped publisher unpublishes
        pubs[0].set(12.5)
        pubs[1].set([1.0, 2.0, 30.0])
        link = NetworkTablesLink("/SmartDashboard/Field/Robot", port=PORT)
        wait_for(lambda: link.odometry() is not None)
        assert link.odometry() == (1.0, 2.0, 30.0)
        assert {"name": "/SmartDashboard/Gyro deg", "type": "double"} in link.topics("/SmartDashboard")
        assert link.get("/SmartDashboard/Gyro deg") == 12.5
        with pytest.raises(KeyError, match="no NetworkTables topic"):
            link.get("/nope")
    finally:
        if link is not None:
            link.close()
        srv.stopServer()
        ntcore.NetworkTableInstance.destroy(srv)


def test_nt_publishes_ball_poses(tmp_path):
    """/MuJoCo/Balls: one Pose3d per ball on the field (the range's ball_poses()), published beside the ground truth."""
    geometry = pytest.importorskip("wpimath.geometry")
    import numpy as np
    from range_fixtures import range_cfg, spares_world
    from scrimmage.testrange.balls import BallState
    from scrimmage.testrange.build import BALL_BITS

    w = spares_world(range_cfg())
    balls = w.test_range.balls
    for k, i in enumerate(np.flatnonzero(balls.state == BallState.SPARE)[:3]):
        balls.place(int(i), np.array([2.0 + k, 1.0, 0.5 + k]), np.zeros(3), np.zeros(3), BALL_BITS)
    poses = w.test_range.ball_poses()
    assert poses.shape == (3, 7)
    srv = ntcore.NetworkTableInstance.create()
    srv.startServer(str(tmp_path / "nt.json"), "", 0, PORT + 1)
    link = None
    try:
        sub = srv.getStructArrayTopic("/MuJoCo/Balls", geometry.Pose3d).subscribe([])
        link = NetworkTablesLink(None, port=PORT + 1)
        wait_for(lambda: link.connected)
        assert not any(t.getName() == "/MuJoCo/Balls" for t in srv.getTopics())   # no range, no topic
        link.publish_balls(poses)
        wait_for(lambda: len(sub.get()) == 3)
        got = sub.get()
        for p, want in zip(got, poses):
            assert (p.X(), p.Y(), p.Z()) == pytest.approx(tuple(want[:3]))
            q = p.rotation().getQuaternion()
            assert (q.W(), q.X(), q.Y(), q.Z()) == pytest.approx(tuple(want[3:]))
        link.publish_balls(poses[:0])
        wait_for(lambda: len(sub.get()) == 0)
        # the same poses again (balls at rest) aren't rebuilt and sent
        sent = []
        real, link._balls = link._balls, type("P", (), {"set": lambda self, v: sent.append(v)})()
        link.publish_balls(poses[:0])
        assert sent == []
        link.publish_balls(poses)
        assert len(sent) == 1 and len(sent[0]) == 3
        link._balls = real
    finally:
        if link is not None:
            link.close()
        srv.stopServer()
        ntcore.NetworkTableInstance.destroy(srv)


NT_SERVER = """
import sys, time, ntcore
from wpimath import geometry
srv = ntcore.NetworkTableInstance.create()
srv.startServer(sys.argv[2], "", 0, int(sys.argv[1]))
sub = srv.getStructArrayTopic("/MuJoCo/Balls", geometry.Pose3d).subscribe([])
t = time.monotonic()
while time.monotonic() - t < 8 and not sub.get():
    time.sleep(0.05)
print(len(sub.get()), flush=True)
"""


def test_ball_poses_reach_a_new_server(tmp_path):
    """Unchanged poses aren't re-sent, except to a server that (re)connected, as a restarted robot program's: it
    always gets the balls. (Each server is its own process, as a robot program is: a second server in this process
    on the same port never sees the client's topics.)"""
    pytest.importorskip("wpimath.geometry")
    import subprocess
    import sys
    import numpy as np
    poses = np.array([[1.0, 2.0, 0.1, 1.0, 0.0, 0.0, 0.0], [3.0, 4.0, 0.1, 1.0, 0.0, 0.0, 0.0]])
    link = NetworkTablesLink(None, port=PORT + 2)
    try:
        for k in range(2):
            srv = subprocess.Popen([sys.executable, "-c", NT_SERVER, str(PORT + 2), str(tmp_path / f"nt{k}.json")],
                                   stdout=subprocess.PIPE, text=True)
            try:
                wait_for(lambda: link.connected, timeout=10)
                wait_for(lambda: link._last_balls is None)     # the connection listener forgot the last poses
                link.publish_balls(poses)       # the same poses both times
                assert srv.stdout.readline().strip() == "2", f"server {k} didn't get the balls"
            finally:
                srv.kill()
                srv.wait()
            wait_for(lambda: not link.connected, timeout=10)
    finally:
        link.close()
