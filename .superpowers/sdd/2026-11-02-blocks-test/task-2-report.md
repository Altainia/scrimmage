# Task 2 report
Status: DONE. Commit: climb: hooks on any body, square rungs, a ladder range.
- ClimbMonitor(world, hook_bodies=None): named robot bodies join hooks/_hook_l/_assembly/signal keys, keyed `<part>.<body>`; unknown body -> ValueError listing robot bodies. World.climb_monitor(hook_bodies=None) rebuilds on a different list; no argument keeps the cached monitor.
- Bar `section` = round|square (square is a box, side `diameter`, same geom frame so the top rule size[0] + z is unchanged).
- Ranges ladder and ladder-square (registered in BUILTIN_RANGES, start `below`), docs in terrain.md, climbing.md, test-range.md.
- Tests: tests/test_climb_hooks_any_body.py (6), section test in tests/test_climb_elements.py.
- Run: climb_*, terrain*, range* tests 388 passed after fixing citation comments; scenario tests pass.
Concerns: test_range_file requires every number-bearing line cited, so rung name lines carry a choice comment. No test hangs a robot on a square rung (only geometry/top checked).
