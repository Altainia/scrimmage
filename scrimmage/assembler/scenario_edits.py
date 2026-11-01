"""Pure edit operations on a scenario's tomlkit document: top-level keys, [[step]] tables and what is nested in
them. Every function mutates the document it is given and raises `EditError(field, message)` for a rejected
edit; field ids are `scenario/<key>`, `step<i>/<key>`, `step<i>/check<j>/<key>`, `step<i>/shot<j>/<key>` and
`step<i>/sheet/<key>` (0-based). Whether the result is a valid scenario is `load_scenario_text`'s call."""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path, PurePosixPath

import tomlkit
from tomlkit.items import AoT, InlineTable, Table

from ..checks import CHECK_KINDS
from ..tracks import Sample, TrackError, load_track, write_track
from .edits import EditError
from .tomlutil import (
    ensure_newline_end, inline, insert_aot_item, insert_first, insert_table, leaf, remove_aot_item,
    remove_table, set_value, tail_region, tail_start, body, region_before, split_tail,
)

TOP_ORDER = ("config", "description", "speed", "require_lockstep")
STEP_KEYS = ("name", "mode", "duration")
INPUT_KINDS = ("axes", "buttons", "povs")
SHOT_KEYS = ("view", "at", "width", "height", "camera")
SHEET_KEYS = ("frames", "view", "width", "height")


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ---- top-level keys ---------------------------------------------------------------------------------------

def set_top(doc, key: str, value) -> None:
    """Set config / description / speed / require_lockstep; `None` removes the key."""
    if key not in TOP_ORDER:
        raise EditError(f"scenario/{key}", f"unknown scenario key {key!r}; use {', '.join(TOP_ORDER)}")
    if value is None:
        if key == "config":
            raise EditError("scenario/config", "config is required")
        doc.pop(key, None)
        return
    ok = {"config": isinstance(value, str), "description": isinstance(value, str),
          "speed": isinstance(value, str) or _is_num(value), "require_lockstep": isinstance(value, bool)}[key]
    if not ok:
        want = {"config": "text", "description": "text", "speed": "a number or text such as \"max\"",
                "require_lockstep": "true or false"}[key]
        raise EditError(f"scenario/{key}", f"{key} must be {want}")
    if key in doc:
        set_value(doc, key, value)
        return
    earlier = tuple(reversed(TOP_ORDER[:TOP_ORDER.index(key)]))
    anchor = next((a for a in earlier if a in doc), None)
    if anchor is None:   # nothing to sit after: first key of the file, ahead of any other
        item = tomlkit.item(value)
        doc._insert_at(0, key, item)
        dict.__setitem__(doc, key, item)
    else:
        set_value(doc, key, value, earlier)


# ---- steps ------------------------------------------------------------------------------------------------

def _steps(doc) -> list:
    return list(doc["step"]) if "step" in doc else []


def _step(doc, i: int):
    steps = _steps(doc)
    if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(steps):
        raise EditError(f"step{i}/step", f"there is no step {i}")
    return steps[i]


def _aot(step, name: str, i: int):
    """The step's `[[step.<name>]]` array, None when it has none."""
    if name not in step:
        return None
    arr = step[name]
    if not isinstance(arr, AoT):
        nm = step.get("name")
        who = f"step {i + 1} {nm!r}" if nm else f"step {i + 1}"
        raise EditError(f"step{i}/{name}", f"{who} writes {name} as an inline list; edit it by hand")
    return arr


def _step_fields(i: int, fields: dict) -> None:
    for k, v in fields.items():
        if k not in STEP_KEYS:
            raise EditError(f"step{i}/{k}", f"unknown step field {k!r}; use {', '.join(STEP_KEYS)}")
        if v is None:
            if k != "name":
                raise EditError(f"step{i}/{k}", f"{k} is required")
        elif k == "duration":
            if not _is_num(v):
                raise EditError(f"step{i}/duration", "duration must be a number of seconds")
        elif not isinstance(v, str):
            raise EditError(f"step{i}/{k}", f"{k} must be text")


def add_step(doc, after: int | None, name, mode, duration) -> None:
    """A new step after step `after` (at the end when `after` is None). An empty name leaves it out."""
    n = len(_steps(doc))
    if after is not None:
        _step(doc, after)
    index = n if after is None else after + 1
    fields = {"name": name or None, "mode": mode, "duration": duration}
    _step_fields(index, fields)
    t = tomlkit.table()
    for k in STEP_KEYS:
        if fields[k] is not None:
            t[k] = fields[k]
    insert_aot_item(doc, "step", t, index)


def duplicate_step(doc, i: int) -> None:
    """A copy of step `i` (nested checks, screenshots and sheet included) right after it, without its comments."""
    src = _step(doc, i)
    t = copy.deepcopy(src)
    lf = leaf(t)
    del body(lf)[tail_start(lf):]
    insert_aot_item(doc, "step", t, i + 1)


def delete_step(doc, i: int) -> None:
    """Remove step `i` with the comments directly above it; the next step's comments stay with that step."""
    _step(doc, i)
    remove_aot_item(doc, "step", i, own_block_first=True)


def move_step(doc, i: int, j: int) -> None:
    """Move step `i` so it becomes step `j`; each step keeps the comments directly above it."""
    _step(doc, i)
    _step(doc, j)
    if i == j:
        return
    arr = doc["step"]
    tables = list(arr)
    n = len(tables)
    pre = region_before(doc, "step")
    tails = [tail_region(t) for t in tables]
    # seps[p]: what sits after position p's table apart from the next table's block (seps[-1] before the first);
    # blocks[t]: the comments directly above table t
    pre_rest, first_block = split_tail(pre.get())
    seps = [pre_rest] + [None] * n
    blocks = {0: first_block}
    for k in range(n):
        if k < n - 1:
            rest, blk = split_tail(tails[k].get())
            blocks[k + 1] = blk
            seps[k + 1] = rest
        else:
            seps[k + 1] = tails[k].get()
    order = list(range(n))
    order.insert(j, order.pop(i))
    for k in range(n):
        tails[k].set([])
    for _ in range(n):
        del arr[0]
    for p, k in enumerate(order):
        arr.insert(p, tables[k])
    for p, k in enumerate(order):
        nxt = blocks[order[p + 1]] if p + 1 < n else []
        tails[k].set(seps[p + 1] + nxt)
    pre.set(seps[0] + blocks[order[0]])
    for t in tables:
        ensure_newline_end(t)


def set_step(doc, i: int, fields: dict) -> None:
    """Change a step's name / mode / duration; a `None` name removes it."""
    t = _step(doc, i)
    _step_fields(i, fields)
    after = {"name": (), "mode": ("name",), "duration": ("mode", "name")}
    for k, v in fields.items():
        if v is None:
            t.pop(k, None)
        elif k == "name" and k not in t:
            insert_first(t, k, v)
        else:
            set_value(t, k, v, after[k])


# ---- joystick inputs --------------------------------------------------------------------------------------

def _port(i: int, kind: str, port) -> str:
    if isinstance(port, str) and port.isdigit():
        port = int(port)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 5:
        raise EditError(f"step{i}/{kind}", "the joystick port must be 0-5")
    return str(port)


def _values(i: int, kind: str, port: str, values: list) -> None:
    for n, x in enumerate(values):
        at = f"step{i}/{kind}"
        if kind == "axes" and not (_is_num(x) and -1 <= x <= 1):
            raise EditError(at, f"port {port}, index {n}: an axis is a number between -1 and 1")
        if kind == "buttons" and not isinstance(x, bool):
            raise EditError(at, f"port {port}, index {n}: a button is true or false")
        if kind == "povs" and not (isinstance(x, int) and not isinstance(x, bool) and (x == -1 or 0 <= x <= 359)):
            raise EditError(at, f"port {port}, index {n}: a POV is a whole number, -1 or 0-359 degrees")


def set_inputs(doc, i: int, kind: str, port: int, values) -> None:
    """Set one joystick port's list of axes / buttons / povs, written as `axes = { 0 = [...] }`; `None` or an
    empty list removes the port (and the whole key when no port is left)."""
    t = _step(doc, i)
    if kind not in INPUT_KINDS:
        raise EditError(f"step{i}/{kind}", f"inputs are {', '.join(INPUT_KINDS)}")
    p = _port(i, kind, port)
    if values is not None and not isinstance(values, (list, tuple)):
        raise EditError(f"step{i}/{kind}", "give a list of values")
    cur = t.get(kind)
    if kind in t and not isinstance(cur, (InlineTable, Table)):
        raise EditError(f"step{i}/{kind}", f"{kind} isn't written as a table; edit the file by hand")
    if values:
        values = list(values)
        _values(i, kind, p, values)
    if isinstance(cur, Table):   # a hand-written [step.axes] table: edit in place
        if values:
            cur[p] = inline(values)
        else:
            cur.pop(p, None)
        if not len(cur):
            del t[kind]
        return
    ports = {k: v.unwrap() if hasattr(v, "unwrap") else v for k, v in (cur or {}).items()}
    if values:
        ports[p] = values
    else:
        ports.pop(p, None)
    if not ports:
        t.pop(kind, None)
        return
    new = inline(dict(sorted(ports.items(), key=lambda kv: int(kv[0]))))
    prior = INPUT_KINDS[:INPUT_KINDS.index(kind)]
    set_value(t, kind, new, tuple(reversed(prior)) + ("duration", "mode", "name"))


# ---- input tracks -----------------------------------------------------------------------------------------

def set_inputs_file(doc, i: int, path: str | None) -> None:
    """Point step `i` at a track CSV (`inputs = "<path>"`, after `duration`) and drop its axes / buttons / povs,
    which can't be combined with it; `None` removes `inputs`."""
    t = _step(doc, i)
    if path is None:
        t.pop("inputs", None)
        return
    if not isinstance(path, str) or not path.strip():
        raise EditError(f"step{i}/inputs", "inputs must be the path of a .csv file")
    for k in INPUT_KINDS:
        t.pop(k, None)
    set_value(t, "inputs", path, ("duration", "mode", "name"))


def _write_new_csv(dest_dir: Path, stem: str, samples: list[Sample]) -> Path:
    """Write `<stem>.trim<N>.csv` (the first unused N >= 1) atomically (temp file, then rename) into `dest_dir`."""
    n = 1
    while (dest_dir / f"{stem}.trim{n}.csv").exists():
        n += 1
    dest = dest_dir / f"{stem}.trim{n}.csv"
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=dest.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        write_track(Path(tmp), samples)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def trim_track(doc, i: int, start: float, end: float, scenario_path: Path) -> Path:
    """Cut step `i`'s track to start..end (seconds into the step): a t = 0 row with the state in effect at
    `start`, then the samples inside the range shifted by -start, written to a NEW file `<stem>.trim<N>.csv` next
    to the old one (never overwriting, so undoing the TOML brings the old track back). `inputs` then names the
    new file and `duration` is end - start. Returns the new file's path (the caller removes it when the edit is
    rejected later)."""
    t = _step(doc, i)
    at = f"step{i}/inputs"
    old = t.get("inputs")
    if not isinstance(old, str):
        raise EditError(at, f"step {i} has no input track to trim")
    dur = t.get("duration")
    if not (_is_num(start) and _is_num(end) and _is_num(dur) and 0 <= start < end <= dur + 1e-9):
        raise EditError(at, f"trim needs 0 <= start < end <= the step's duration ({dur}); got {start} to {end}")
    old_path = Path(scenario_path).parent / old
    try:
        track = load_track(old_path)
    except TrackError as e:
        raise EditError(at, str(e)) from None
    k = track.at(start)
    first = track.samples[k].ports if k >= 0 else {}
    samples = [Sample(0.0, copy.deepcopy(first))]
    for s in track.samples:
        if s.t > start + 1e-9 and s.t <= end + 1e-9:
            samples.append(Sample(round(s.t - start, 6), s.ports))
    try:
        dest = _write_new_csv(old_path.parent, old_path.stem, samples)
    except OSError as e:
        raise EditError(at, f"can't write the trimmed track: {e.strerror or e}") from None
    try:
        t["inputs"] = str(PurePosixPath(old).with_name(dest.name))
        set_value(t, "duration", round(end - start, 6))
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return dest


# ---- nested [[step.check]] / [[step.screenshot]] / [step.contact_sheet] ------------------------------------

def _indents(doc) -> tuple[str, str]:
    """(header indent, key indent) the author uses for nested tables: from the first one in the file, else two
    spaces."""
    for s in _steps(doc):
        for name in ("check", "screenshot", "contact_sheet"):
            if name not in s:
                continue
            v = s[name]
            t = v[0] if isinstance(v, AoT) and len(v) else v
            if not isinstance(t, (Table, InlineTable)):   # e.g. `check = [ {...} ]` written inline
                continue
            keyed = [it for k, it in body(t) if k is not None]
            return t.trivia.indent, (keyed[0].trivia.indent if keyed else t.trivia.indent)
    return "  ", "  "


def _nested(doc, key_indent_pairs: dict) -> Table:
    head, keys = _indents(doc)
    t = tomlkit.table()
    for k, v in key_indent_pairs.items():
        t[k] = v
        t[k].trivia.indent = keys
    t.trivia.indent = head
    return t


def _check_spec(at: str, kind, spec) -> dict:
    if kind not in CHECK_KINDS:
        raise EditError(at, f"a check is one of {', '.join(CHECK_KINDS)}, not {kind!r}")
    if spec is None:
        raise EditError(at, f"{kind} needs a value")
    return {kind: inline(spec)}


def add_check(doc, i: int, kind: str, spec) -> None:
    """Append `[[step.check]]` with `kind = spec` (a table, or text for never_touch / must_touch) to step `i`."""
    step = _step(doc, i)
    n = len(_aot(step, "check", i) or [])
    t = _nested(doc, _check_spec(f"step{i}/check{n}", kind, spec))
    insert_aot_item(step, "check", t, before=("screenshot", "contact_sheet"))


def _item(step, name: str, i: int, j: int, what: str):
    arr = _aot(step, name, i)
    if arr is None or isinstance(j, bool) or not isinstance(j, int) or not 0 <= j < len(arr):
        raise EditError(f"step{i}/{what}{j}", f"step {i} has no {what} {j}")
    return arr[j]


def set_check(doc, i: int, j: int, spec: dict) -> None:
    """Replace check `j` of step `i` with `spec`, a one-key table `{kind: value}`."""
    step = _step(doc, i)
    c = _item(step, "check", i, j, "check")
    at = f"step{i}/check{j}"
    if not isinstance(spec, dict) or len(spec) != 1:
        raise EditError(at, f"a check is one key: {', '.join(CHECK_KINDS)}")
    (kind, val), = spec.items()
    new = _check_spec(f"{at}/{kind}", kind, val)[kind]
    old = [k for k in c if k != kind]
    for k in old:
        del c[k]
    if kind in c:
        set_value(c, kind, new)
    else:
        item = inline(new) if not hasattr(new, "trivia") else new
        item.trivia.indent = _indents(doc)[1]
        insert_first(c, kind, item)


def delete_check(doc, i: int, j: int) -> None:
    step = _step(doc, i)
    _item(step, "check", i, j, "check")
    remove_aot_item(step, "check", j, own_block_first=True)


def _fields(at: str, spec, allowed: tuple[str, ...]) -> dict:
    if not isinstance(spec, dict):
        raise EditError(at, "give the fields as a table")
    for k in spec:
        if k not in allowed:
            raise EditError(f"{at}/{k}", f"unknown field {k!r}; use {', '.join(allowed)}")
    return spec


def _apply_fields(t, spec: dict, order: tuple[str, ...]) -> None:
    for k, v in spec.items():
        if v is None:
            t.pop(k, None)
            continue
        new = k not in t
        set_value(t, k, inline(v), tuple(reversed(order[:order.index(k)])))
        if new:
            t[k].trivia.indent = _key_indent(t)


def _key_indent(t) -> str:
    keyed = [it for k, it in body(t) if k is not None]
    return keyed[0].trivia.indent if keyed else t.trivia.indent


def add_shot(doc, i: int, spec: dict) -> None:
    """Append `[[step.screenshot]]` to step `i`; `spec` has view / at / width / height / camera (None skips one)."""
    step = _step(doc, i)
    n = len(_aot(step, "screenshot", i) or [])
    spec = _fields(f"step{i}/shot{n}", spec, SHOT_KEYS)
    t = _nested(doc, {k: inline(spec[k]) for k in SHOT_KEYS if spec.get(k) is not None})
    insert_aot_item(step, "screenshot", t, before=("contact_sheet",))


def set_shot(doc, i: int, j: int, spec: dict) -> None:
    """Change fields of screenshot `j` of step `i`; a `None` value removes that field."""
    step = _step(doc, i)
    s = _item(step, "screenshot", i, j, "shot")
    _apply_fields(s, _fields(f"step{i}/shot{j}", spec, SHOT_KEYS), SHOT_KEYS)


def delete_shot(doc, i: int, j: int) -> None:
    step = _step(doc, i)
    _item(step, "screenshot", i, j, "shot")
    remove_aot_item(step, "screenshot", j, own_block_first=True)


def set_sheet(doc, i: int, spec) -> None:
    """Add or change step `i`'s `[step.contact_sheet]` (frames / view / width / height; None values remove a
    field); `spec` of None removes the sheet."""
    step = _step(doc, i)
    cur = step.get("contact_sheet")
    if spec is None:
        if cur is not None:
            remove_table(step, "contact_sheet")
        return
    spec = _fields(f"step{i}/sheet", spec, SHEET_KEYS)
    if cur is None:
        t = _nested(doc, {k: inline(spec[k]) for k in SHEET_KEYS if spec.get(k) is not None})
        insert_table(step, "contact_sheet", t)
    elif isinstance(cur, Table):
        _apply_fields(cur, spec, SHEET_KEYS)
    else:
        raise EditError(f"step{i}/sheet", "contact_sheet isn't written as a table; edit the file by hand")
