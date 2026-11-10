"""When a failed Phoenix-mode scenario run is the machine's fault, not scrimmage's."""

from __future__ import annotations

import os
from collections.abc import Callable


def machine_too_slow(data: dict, may_fail: Callable[[str], bool]) -> str | None:
    """Why a failed Phoenix-mode run proves nothing about scrimmage, or None. Phoenix 6 needs real time: a run where
    physics or the robot program fell behind real time (either can, on a starved 2-vCPU runner) may miss checks that
    depend on timing. Only those: every failed check must be one `may_fail` accepts. A foreign write, a crash,
    missing devices, an interrupted step, an error or any other failed check still fail. The skip reason names the
    failed checks, so a skipped run's failures stay visible.

    On CI the timing checks are skipped even when the run didn't report falling behind: Phoenix 6 runs on the wall
    clock, and a shared runner's scheduling can throw a path off without tripping either note (the weekly run of
    2026-11-10 ended 0.29 m off, at full speed). The same robot's fast-mode run checks the numbers exactly, in
    lockstep; on CI a Phoenix run gates only what doesn't depend on the runner's pace."""
    if data["passed"]:
        return None
    slow = [n for n in data.get("notes", []) if "kept up with only" in n or "behind real time" in n]
    steps = data.get("steps", [])
    failed = [c for s in steps for c in s["checks"] if not c["passed"]]
    implicit = [c for c in data.get("implicit", []) if not c["passed"]]
    timing_only = (failed and not implicit and all(may_fail(c["check"]) for c in failed)
                   and not any(s.get("interrupted") for s in steps) and not data.get("error"))
    if not timing_only:
        return None
    names = "; ".join(c["check"] for c in failed)
    if slow:
        return f"this machine couldn't run Phoenix 6 at real time: {slow[0]} (failed: {names})"
    if os.environ.get("CI"):
        return f"on CI a Phoenix 6 run's timing checks don't gate: it runs on the wall clock (failed: {names})"
    return None
