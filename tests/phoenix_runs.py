"""When a failed Phoenix-mode scenario run is the machine's fault, not scrimmage's."""

from __future__ import annotations

from collections.abc import Callable


def machine_too_slow(data: dict, may_fail: Callable[[str], bool]) -> str | None:
    """Why a failed Phoenix-mode run proves nothing about scrimmage, or None. Phoenix 6 needs real time: a run where
    physics or the robot program fell behind real time (either can, on a starved 2-vCPU runner) may miss checks that
    depend on timing. Only those: every failed check must be one `may_fail` accepts. A foreign write, a crash,
    missing devices, an interrupted step, an error or any other failed check still fail. The skip reason names the
    failed checks, so a skipped run's failures stay visible."""
    if data["passed"]:
        return None
    slow = [n for n in data.get("notes", []) if "kept up with only" in n or "behind real time" in n]
    steps = data.get("steps", [])
    failed = [c for s in steps for c in s["checks"] if not c["passed"]]
    implicit = [c for c in data.get("implicit", []) if not c["passed"]]
    timing_only = (failed and not implicit and all(may_fail(c["check"]) for c in failed)
                   and not any(s.get("interrupted") for s in steps) and not data.get("error"))
    if slow and timing_only:
        names = "; ".join(c["check"] for c in failed)
        return f"this machine couldn't run Phoenix 6 at real time: {slow[0]} (failed: {names})"
    return None
