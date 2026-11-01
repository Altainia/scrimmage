"""Comment-aware helpers on tomlkit documents, shared by the robot-config edits and the scenario edits.

tomlkit keeps the blank lines and comments after a table's last key at the end of that table's body, and the
text between two tables of an array belongs to the first of them: it is what sits above the second ("its comment
block"). With nested tables (`[[step.check]]` inside `[[step]]`) it ends up in the deepest last table. These
helpers keep new keys before that trailing text, and keep a table's comment block with the table when it is
inserted or removed. They lean on tomlkit's private `Container._insert_after`/`_insert_at`.
"""

from __future__ import annotations

import copy

import tomlkit
from tomlkit.container import Container
from tomlkit.items import AoT, Comment, SingleKey, Table, Whitespace


def container(x) -> Container:
    """The Container behind a document or table."""
    return x if isinstance(x, Container) else x.value


def body(x) -> list:
    return container(x).body


def plain(v):
    """A tomlkit value for a plain Python value (lists and dicts become arrays/inline tables)."""
    if isinstance(v, dict):
        t = tomlkit.inline_table()
        for k, x in v.items():
            t[str(k)] = plain(x)
        return t
    if isinstance(v, (list, tuple)):
        a = tomlkit.array()
        a.extend([plain(x) for x in v])
        return a
    return v


def _inline_text(v) -> str:
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{tomlkit.key(str(k)).as_string()} = {_inline_text(x)}" for k, x in v.items()) + " }"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_inline_text(x) for x in v) + "]"
    if isinstance(v, float):
        return repr(v)
    return tomlkit.item(v).as_string()


def inline(v):
    """A tomlkit value for a plain Python value, written the way scenarios are by hand: `{ a = 1, b = [1, 2] }`."""
    if not isinstance(v, (dict, list, tuple)):
        return tomlkit.item(v)
    out = tomlkit.parse("v = " + _inline_text(v))["v"]
    out.trivia.trail = "\n"
    return out


def reindex(c: Container) -> None:
    """Rebuild a container's key positions after its body list was changed by hand."""
    c._map.clear()
    for i, (k, _) in enumerate(c.body):
        if k is None:
            continue
        cur = c._map.get(k)
        c._map[k] = i if cur is None else (*((cur,) if isinstance(cur, int) else cur), i)


def tail_start(table) -> int:
    """Index in the table's body just past its last key/value: what follows (blank lines and comments) is the
    comment block above the next table, so new keys go before it."""
    b = body(table)
    for i in range(len(b) - 1, -1, -1):
        if b[i][0] is not None:
            return i + 1
    return 0


def leaf(item):
    """The deepest last table of a table or array of tables: where the text after it is kept."""
    while True:
        if isinstance(item, AoT):
            if not len(item):
                return item
            item = item[-1]
            continue
        b = body(item)
        i = tail_start(item)
        if i and isinstance(b[i - 1][1], (Table, AoT)):
            item = b[i - 1][1]
            continue
        return item


def insert(table, key: str, value, after: tuple[str, ...] = ()) -> None:
    """Add a new key after the first of `after` present in the table, else after its last key/value (before
    the trailing comment block); a plain table assignment when there's nothing to anchor to."""
    anchor = next((a for a in after if a in table), None)
    if anchor is None:
        b = body(table)
        i = tail_start(table)
        if i == 0 or isinstance(b[i - 1][1], (Table, AoT)):
            table[key] = value
            return
        anchor = b[i - 1][0].key
    item = tomlkit.item(value) if not hasattr(value, "trivia") else value
    container(table)._insert_after(anchor, key, item)
    dict.__setitem__(table, key, item)


def insert_first(table, key: str, value) -> None:
    """Add a new key as the first key/value of the table."""
    item = tomlkit.item(value) if not hasattr(value, "trivia") else value
    container(table)._insert_at(0, key, item)
    dict.__setitem__(table, key, item)


def set_value(table, key: str, value, after: tuple[str, ...] = ()) -> None:
    """Set a key keeping its position and trailing comment when it already exists."""
    if key in table:
        old = table.item(key) if hasattr(table, "item") else table[key]
        new = tomlkit.item(value) if not hasattr(value, "trivia") else value
        try:
            new.trivia.comment_ws = old.trivia.comment_ws
            new.trivia.comment = old.trivia.comment
        except AttributeError:
            pass
        table[key] = new
    else:
        insert(table, key, value, after)


# ---- comment blocks of array-of-tables members -----------------------------------------------------------

def split_tail(tail: list) -> tuple[list, list]:
    """(rest, comment block): the comments directly above the next table (touching its header) and what is
    before them. A comment separated from the header by a blank line is not part of its block."""
    n = len(tail)
    while n and isinstance(tail[n - 1][1], Comment):
        n -= 1
    return tail[:n], tail[n:]


def drop_own_comment_block(prev, moved: list, last: bool) -> None:
    """Removing the table after `prev`: drop the comments directly above that table's header (they describe it;
    tomlkit keeps them at the end of `prev`), then give `prev` the removed table's own tail, which is the
    comment block of what follows it. Only comments touching the header go: one separated from it by a
    blank line (a section heading) stays."""
    b = body(prev)
    start = tail_start(prev)
    tail = b[start:]
    while tail and isinstance(tail[-1][1], Comment):
        tail.pop()
    if moved or last:   # the removed table's tail (or the end of the file) replaces prev's blank lines
        while tail and isinstance(tail[-1][1], Whitespace):
            tail.pop()
    del b[start:]
    b.extend(tail + list(moved))


class _Region:
    """A slice of a body list that holds the text before some table: [start, end) of `items`. `owner` is the
    container whose key positions change if the slice's length does (None when it is a nested table's tail)."""

    def __init__(self, items: list, start: int, end: int, owner: Container | None):
        self.items, self.start, self.end, self.owner = items, start, end, owner

    def get(self) -> list:
        return self.items[self.start:self.end]

    def set(self, new: list) -> None:
        self.items[self.start:self.end] = new
        self.end = self.start + len(new)
        if self.owner is not None:
            reindex(self.owner)


def tail_region(item) -> _Region:
    lf = leaf(item)
    b = body(lf)
    return _Region(b, tail_start(lf), len(b), None)


def region_before(parent, name: str, tables_only: bool = False) -> _Region | None:
    """The text just before the `name` array of tables in `parent`: the tail of the table before it, or the
    comments and blank lines after the last key before it. None (with `tables_only`) unless a table precedes."""
    c = container(parent)
    b = c.body
    k = next(j for j, (key, _) in enumerate(b) if key is not None and key.key == name)
    if k > 0 and isinstance(b[k - 1][1], (Table, AoT)):
        return tail_region(b[k - 1][1])
    if tables_only:
        return None
    j = next((n + 1 for n in range(k - 1, -1, -1) if b[n][0] is not None), 0)
    return _Region(b, j, k, c)


def hand_tail_to_previous_section(parent, moved: list, name: str = "part") -> None:
    """Removing the first table of the `name` array: the next table's comment block moves to the end of the
    section before the array (whose own text is kept as it is). Without a table there, the comments go with
    the removed table."""
    region = region_before(parent, name, tables_only=True)
    if region is None:
        return
    prev = region.get()
    moved = list(moved)
    if prev and isinstance(prev[-1][1], Whitespace):
        while moved and isinstance(moved[0][1], Whitespace):
            moved.pop(0)
    region.set(prev + moved)


def _hand_to_region(region: _Region, moved: list, drop_block: bool) -> None:
    tail = region.get()
    if drop_block:   # the comments directly above the removed table go with it
        while tail and isinstance(tail[-1][1], Comment):
            tail.pop()
    moved = list(moved)
    if tail and isinstance(tail[-1][1], Whitespace):
        while moved and isinstance(moved[0][1], Whitespace):
            moved.pop(0)
    region.set(tail + moved)


def remove_table(parent, name: str) -> None:
    """Delete the single sub-table `name` of `parent` (e.g. `[step.contact_sheet]`) with the comments
    directly above it; the comments above what follows it stay."""
    tl = leaf(parent[name])
    _hand_to_region(region_before(parent, name), body(tl)[tail_start(tl):], drop_block=True)
    del parent[name]


def ensure_newline_end(table) -> None:
    """Make sure a table whose body ends in a key/value (nothing after it) ends its last line."""
    lf = leaf(table)
    b = body(lf)
    i = tail_start(lf)
    if i and i == len(b) and not isinstance(b[i - 1][1], (Table, AoT)) and not b[i - 1][1].trivia.trail.endswith("\n"):
        b[i - 1][1].trivia.trail = "\n"


def remove_aot_item(parent, name: str, i: int, *, own_block_first: bool = False) -> None:
    """Delete table `i` of the `name` array in `parent`. The comments directly above it go with it; the
    comments above the table after it stay with that table. With `own_block_first`, the first table's own
    comments go too and the next table's block is handed to whatever precedes the array, even a bare key
    (otherwise the first table's own comments stay and a section without a table before it loses the block)."""
    arr = parent[name]
    target = arr[i]
    tl = leaf(target)
    moved = body(tl)[tail_start(tl):]   # the comment block above what follows (or the end of the file)
    if i > 0:
        drop_own_comment_block(leaf(arr[i - 1]), moved, last=i == len(arr) - 1)
    elif own_block_first:
        _hand_to_region(region_before(parent, name), moved, drop_block=True)
    elif moved and len(arr) > 1:
        hand_tail_to_previous_section(parent, moved, name)
    del arr[i]
    if len(arr) == 0:
        del parent[name]


def _gap(arr) -> list:
    """The blank lines the author puts between members of this array (copied), or none."""
    for t in list(arr)[:-1]:
        rest, _ = split_tail(tail_region(t).get())
        ws = [x for x in rest if isinstance(x[1], Whitespace)]
        if ws:
            return copy.deepcopy(ws)
    return []


def insert_aot_item(parent, name: str, table, index: int | None = None, before: tuple[str, ...] = ()) -> None:
    """Add `table` to the `name` array in `parent` at `index` (the end by default), creating the array when
    there is none (then before the first of the `before` sibling keys present, else at the end). The comment
    block above the member that follows stays above that member; the new table gets no comments of its own."""
    c = container(parent)
    b = c.body
    if name in parent:
        arr = parent[name]
        n = len(arr)
        index = n if index is None else index
        gap = _gap(arr) if n > 1 else []
        region = tail_region(arr[index - 1]) if index > 0 else region_before(parent, name)
        tail = region.get()
        tl = leaf(table)
        if index == n:
            # the text after the last member (blank lines, the next thing's comments) now follows the new one
            region.set(gap)
            body(tl)[tail_start(tl):] = tail
        else:
            rest, block = split_tail(tail)
            region.set(rest)
            body(tl)[tail_start(tl):] = gap + block
        arr.insert(index, table)
        if index > 0:
            ensure_newline_end(arr[index - 1])
        return
    aot = tomlkit.aot()
    aot.append(table)
    _place_sub(parent, name, aot, table, before)


def insert_table(parent, name: str, table, before: tuple[str, ...] = ()) -> None:
    """Add the sub-table `[parent.name]` after the parent's keys and nested tables (or before the first of
    the `before` sibling keys), moving the text that ended the parent after the new table."""
    _place_sub(parent, name, table, table, before)


def _place_sub(parent, name: str, value, table, before: tuple[str, ...]) -> None:
    c = container(parent)
    b = c.body
    tl = leaf(table)
    k = next((j for j, (key, _) in enumerate(b) if key is not None and key.key in before), None)
    if k is not None:
        region = region_before(parent, b[k][0].key)
        rest, block = split_tail(region.get())
        region.set(rest)
        body(tl)[tail_start(tl):] = block
        k = next(j for j, (key, _) in enumerate(b) if key is not None and key.key in before)
        c._insert_at(k, name, value)
    else:
        lf = leaf(parent)
        lb = body(lf)
        ts = tail_start(lf)
        tail = lb[ts:]
        del lb[ts:]
        body(tl)[tail_start(tl):] = tail
        ensure_newline_end(lf)
        c._raw_append(SingleKey(name), value)
    dict.__setitem__(parent, name, value)
