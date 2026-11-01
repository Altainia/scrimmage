"""machine_too_slow(): only a slow machine's timing failures are skipped."""

from __future__ import annotations

from phoenix_runs import machine_too_slow


def fired(check: str) -> bool:
    return check.startswith(("by_time fired ", "at_end fired "))


def report(failed: list[str], notes: list[str], implicit_failed: list[str] = (), interrupted=None, error=None):
    checks = [{"check": c, "passed": False} for c in failed] + [{"check": "at_end score.hub_blue >= 0",
                                                                  "passed": True}]
    return {"passed": not failed and not implicit_failed and not interrupted and not error, "notes": notes,
            "steps": [{"checks": checks, "interrupted": interrupted}], "error": error,
            "implicit": [{"check": c, "passed": False} for c in implicit_failed]
            + [{"check": "robot program running", "passed": True}]}


BEHIND = ["the robot program fell 0.66 s behind real time (silent, ...)"]
SKIPPED = ["physics on this computer kept up with only 0.9x real time: physics fell behind real time and skipped "
           "ahead 1.04 s (3 times), ..."]
LATE, SHORT = ["by_time fired >= 8 by t=8"], ["at_end fired >= 8"]


def test_only_a_short_fired_count_on_a_slow_machine_is_skipped():
    assert BEHIND[0] in machine_too_slow(report(LATE, BEHIND), fired)
    assert SKIPPED[0] in machine_too_slow(report(LATE, SKIPPED), fired)
    assert machine_too_slow(report(LATE + SHORT, SKIPPED), fired)
    assert machine_too_slow(report(SHORT, BEHIND), fired)
    assert machine_too_slow(report(LATE, []), fired) is None                       # not slow: a real failure
    assert machine_too_slow(report(LATE + SHORT, []), fired) is None
    assert machine_too_slow(report([], BEHIND), fired) is None                      # passed
    assert machine_too_slow(report(LATE + ["at_end score.hub_blue >= 2"], BEHIND), fired) is None
    assert machine_too_slow(report(["by_time score.hub_blue >= 2 by t=8"], BEHIND), fired) is None
    assert machine_too_slow(report(LATE, BEHIND, ["no foreign sim writes"]), fired) is None
    assert machine_too_slow(report(LATE, BEHIND, ["no crash in console"]), fired) is None
    assert machine_too_slow(report(LATE, BEHIND, ["all configured devices present"]), fired) is None
    assert machine_too_slow(report([], BEHIND, ["no foreign sim writes"]), fired) is None
    assert machine_too_slow(report(LATE, BEHIND, interrupted="step interrupted at t=3.0"), fired) is None
    assert machine_too_slow(report(LATE, BEHIND, error="the robot program exited"), fired) is None


def test_the_skip_reason_names_the_failed_checks():
    reason = machine_too_slow(report(LATE + SHORT, SKIPPED), fired)
    assert reason.endswith(f"(failed: {LATE[0]}; {SHORT[0]})")
