"""Every problem a bench file has, each with the dotted key of the field at fault.

`collect_problems` is what the Bench tab and `bench check` show: all the parse errors at once (one per bad entry),
and only when there are none, the build checks and the warnings that do not stop a run.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class Problem:
    level: Literal["error", "warning"]
    key: str | None  # the most specific dotted key known ("bar.kicker.spring", else "bar.kicker"), or None
    message: str


def collect_problems(path: Path, text: str | None = None):
    """(the bench or None, every problem). The bench is None when there is any error. With `text`, that text is
    checked as if it were the file at `path` (includes and mesh paths read from its folder)."""
    from .build import build_bench, check_clear, overlap_warnings
    from .spec import BenchError, _read_toml, _resolve_includes, parse_bench

    path = Path(path)
    problems: list[Problem] = []
    try:
        if text is None:
            raw = _read_toml(path)
        else:
            try:
                raw = tomllib.loads(text)
            except tomllib.TOMLDecodeError as e:
                raise BenchError(f"{path}: {e}") from None
        base = path.resolve().parent
        raw = _resolve_includes(raw, base, (path.resolve(),))
    except BenchError as e:
        return None, [Problem("error", e.key, str(e))]
    bench = parse_bench(raw, base, problems)
    if bench is None:
        return None, problems
    try:
        built = build_bench(bench)
        check_clear(built)
    except BenchError as e:
        return None, [Problem("error", e.key, str(e))]
    problems += [Problem("warning", "piece", w) for w in bench.warnings]
    problems += overlap_warnings(built, bench)
    return bench, problems
