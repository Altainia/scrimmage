"""`bench view`: a run played back in the MuJoCo viewer, slowed down, over and over until the window is closed.

The run is simulated first (as `bench run` does), then its trace is replayed: each sample's qpos is set and
`mj_forward` puts the bodies there. Parked pool pieces are made invisible so only the pieces in the bench show.
"""

import copy
import time

import mujoco
import numpy as np

from .run import BenchResult

PAUSE = 1.0  # s of wall time between replays


def _launch(model: mujoco.MjModel, data: mujoco.MjData):
    import mujoco.viewer
    return mujoco.viewer.launch_passive(model, data)


def play(result: BenchResult, slow: float = 0.25) -> None:
    """Replay `result.trace` at `slow` times real time (0.25: four times slower) until the viewer is closed."""
    if not slow > 0.0:
        raise ValueError(f"--slow must be greater than 0, not {slow!r}")
    m = copy.deepcopy(result.built.model)
    d = mujoco.MjData(m)
    lay = result.built.layout
    floor = lay.bounds[0][2]
    alpha = [m.geom_rgba[list(gs), 3].copy() for gs in lay.piece_geom_sets]
    trace = result.trace
    if not trace:
        return
    lo, hi = lay.bounds
    with _launch(m, d) as viewer:
        viewer.cam.lookat[:] = (lo + hi) / 2
        viewer.cam.azimuth, viewer.cam.elevation = 90.0, -10.0  # from the side, a little above
        viewer.cam.distance = float(np.linalg.norm(hi - lo))
        while viewer.is_running():
            prev = trace[0][0]
            for t, qpos in trace:
                if not viewer.is_running():
                    return
                with viewer.lock():  # the viewer draws from another thread
                    d.qpos[:] = qpos
                    for i, (a, gs) in enumerate(zip(lay.piece_qpos, lay.piece_geom_sets)):
                        m.geom_rgba[list(gs), 3] = alpha[i] if qpos[a + 2] > floor else 0.0
                    mujoco.mj_forward(m, d)
                viewer.sync()
                time.sleep(max(0.0, (t - prev) / slow))
                prev = t
            time.sleep(PAUSE)
