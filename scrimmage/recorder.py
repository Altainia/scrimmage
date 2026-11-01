"""Where the robot has been (ground truth) and where it thought it was (odometry), for path overlays."""

from __future__ import annotations


class Recorder:
    def __init__(self, max_points: int = 20000):
        self.max_points = max_points
        self.truth: list[tuple[float, float, float]] = []      # (t, x, y)
        self.odometry: list[tuple[float, float, float]] = []

    def reset(self) -> None:
        self.truth.clear()
        self.odometry.clear()

    def sample(self, t: float, x: float, y: float, odo: tuple[float, float, float] | None) -> None:
        self.truth.append((t, x, y))
        if odo is not None:
            self.odometry.append((t, odo[0], odo[1]))
        # keep long runs bounded: halve the resolution instead of dropping the start of the path
        if len(self.truth) > self.max_points:
            self.truth = self.truth[::2]
        if len(self.odometry) > self.max_points:
            self.odometry = self.odometry[::2]

    def paths(self) -> dict[str, list[tuple[float, float]]]:
        return {"truth": [(x, y) for _, x, y in self.truth], "odometry": [(x, y) for _, x, y in self.odometry]}
