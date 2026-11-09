"""A test run's words: the run made inside machine text keeps them in every Language and Units setting (`_Words`,
`_w`, `_v`, `_ws`; the run's end files them in RunResult.words with `_keep_words`), a block's place in words
(`_where`), describe()'s verb forms (`_VERBS`, `_recast`), and `_FailureWords`, the TestRun methods that say what
happened instead of a check, which block was under way, and each check's line. It never imports run.py; run.py
re-exports what other modules import from it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from .. import i18n, units
from .aim import aim_error, aim_target
from .clearance import NEVER_WITHIN_IN
from .conditions import IN, _amount, describe, scores_balls, turret_words
from .grip_watch import SET_DOWN, default_word
from .palette import ANY_GOAL, ANY_HOOK, ANY_PIECE, NEXT_BAR
from .placed import KNOCK_OFF_SHARE, seated_words

if TYPE_CHECKING:
    from .run import RunResult


# The verbs describe() uses, as "-ing" (after "without") and "to ..." (after "waiting for"): _recast's. A run says
# those forms with describe(form="without"/"waiting"), a whole phrase per form for a translator; _recast stays as
# the English rule they follow (tests/test_i18n_test_tab.py checks every condition's forms against it).
_VERBS = {"passes": ("passing", "to pass"), "goes": ("going", "to go"), "tilts": ("tilting", "to tilt"),
          "touches": ("touching", "to touch"), "reaches": ("reaching", "to reach"), "hangs": ("hanging", "to hang"),
          "are": ("being", "to be"), "is": ("being", "to be"), "made": ("having made", "to have made"),
          "is held": ("being held", "to be held"), "rests": ("resting", "to rest"),
          "clears": ("clearing", "to clear"), "moved": ("having moved", "to have moved"),
          # a color sensor's check: "without eye seeing red", "waiting for eye not to see red"
          "is seeing": ("seeing", "to see"), "isn't seeing": ("losing sight of", "not to see")}


class _Words(str):
    """Machine text with its words in every Language and Units setting (units.each_setting), for a run made inside
    units.machine_text() (a sweep's, a spread's or MCP test_run's): the str itself is the machine text, `each` the
    words by setting. The run's end files them in RunResult.words and leaves plain text (_keep_words)."""

    def __new__(cls, text: str, each: dict):
        s = super().__new__(cls, text)
        s.each = each
        return s

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def _w(fn) -> str:
    """fn()'s text; inside machine text, as _Words with fn() in every setting too, while the run's numbers are at
    hand (a kept run is shown in the viewer's setting)."""
    text = fn()
    return _Words(text, units.each_setting(fn)) if units.is_machine() else text


def _v(text):
    """Text made earlier (by _w) as it reads in the setting being worded now; itself in machine text."""
    if isinstance(text, _Words) and not units.is_machine():
        return text.each.get(units.setting_key(), str(text))
    return text


def _ws(fn) -> list:
    """_w() for a list of lines (each line worded in every setting)."""
    lines = fn()
    if not units.is_machine():
        return lines
    each = units.each_setting(fn)
    return [_Words(line, {k: v[i] if len(v) == len(lines) else line for k, v in each.items()})
            for i, line in enumerate(lines)]


WORDED_CHECK = ("text", "failure", "line", "until")     # a check's fields in words


def _keep_words(result: RunResult) -> None:
    """Inside machine text: file the run's words in every setting in result.words ({setting_key: {outcome, checks
    (each check's WORDED_CHECK fields), marks (each mark's text), seated, notes}}), and leave the machine text in
    the result as plain str (MCP results and result.json's other fields don't change)."""
    if not units.is_machine():
        return

    def pick(x, key):
        return x.each.get(key, str(x)) if isinstance(x, _Words) else x

    keys = list(units.each_setting(lambda: None))
    result.words = {key: {
        "outcome": pick(result.outcome, key),
        "checks": [{f: pick(c[f], key) for f in WORDED_CHECK if isinstance(c.get(f), str)} for c in result.checks],
        "marks": [pick(m.get("text"), key) for m in result.marks],
        "seated": {rod: pick(v, key) for rod, v in result.seated.items()},
        "notes": [pick(n, key) for n in result.notes]} for key in keys}
    result.outcome = str(result.outcome)
    for c in result.checks:
        for f in WORDED_CHECK:
            if isinstance(c.get(f), str):
                c[f] = str(c[f])
    for m in result.marks:
        if isinstance(m.get("text"), str):
            m["text"] = str(m["text"])
    result.seated = {rod: str(v) for rod, v in result.seated.items()}
    result.notes = [str(n) for n in result.notes]


def _where(place: tuple) -> str:
    """0-based place -> "block 3 inside block 2"."""
    words = i18n.t("block {n}", n=place[-1] + 1)
    for i in reversed(place[:-1]):
        words = i18n.t("{block} inside block {n}", block=words, n=i + 1)
    return words


def _recast(text: str, form: int) -> str:
    """describe()'s words with its verb (one word, or two: "is held") as "-ing" (form 0) or "to ..." (form 1)."""
    words = text.split(" ")
    for i, w in enumerate(words):
        two = " ".join(words[i:i + 2])
        if two in _VERBS:
            return " ".join([*words[:i], _VERBS[two][form], *words[i + 2:]])
        if w in _VERBS:
            words[i] = _VERBS[w][form]
            break
    return " ".join(words)


class _FailureWords:
    """TestRun's wording: what happened instead of a check (`_failure` and its helpers), the block under way, and each
    check's line. A mixin: it reads the run's state (`world`, `watch`, `feeder`, `frames`, ...) that TestRun keeps."""

    def _value(self, joint: str, unit: str) -> str:
        """A joint's position now in words (`38.2°`, `1.5 in` or `38 mm`): degrees for a `unit` of "deg", else
        a length in the Units setting."""
        q = float(self.world.data.qpos[self._qadr[joint]])
        if unit == "deg":
            return _amount(round(math.degrees(q), 1) + 0.0, unit)
        if units.is_machine():                  # the old words: the file's unit, to 0.1
            return _amount(round(q / IN if unit == "in" else q, 1), unit)
        return units.fmt(q, "length", fewer=2)

    def _failure(self, cond) -> str:
        """What happened instead of `cond`, in words."""
        a, k, watch = cond.args, cond.kind, self.watch
        t = i18n.t

        def bar(name: str) -> str:
            return (watch.next_bar() or t("the next bar")) if name == NEXT_BAR else name

        if k == "joint":
            p = dict(joint=a["name"], at=self._value(a["name"], a["unit"]))
            if "above" in a:
                return t("expected {joint} above {limit}, but it was {at}", limit=_amount(a["above"], a["unit"]), **p)
            return t("expected {joint} below {limit}, but it was {at}", limit=_amount(a["below"], a["unit"]), **p)
        if k == "tilt":
            tilted = _amount(round(watch.tilt, 1), "deg")
            if "above" in a:
                return t("expected the robot to tilt more than {limit}, but it tilted {tilted}",
                         limit=_amount(a["above"], "deg"), tilted=tilted)
            return t("expected the robot to tilt less than {limit}, but it tilted {tilted}",
                     limit=_amount(a["below"], "deg"), tilted=tilted)
        if k == "touch":
            if a["robot"] == ANY_HOOK:
                return t("no hook touched {target}", target=bar(a["field"]))
            return t("{part} didn't touch {target}", part=a["robot"], target=bar(a["field"]))
        if k == "piece_in":
            if a["piece"] == ANY_PIECE:
                return t("no {piece} was in {body}", piece=default_word(watch.kinds), body=a["body"])
            return t("{piece} wasn't in {body}", piece=watch.piece_names.get(a["piece"], a["piece"]), body=a["body"])
        if k == "in_hopper":
            n = watch.hopper_count()
            if "at_least" in a:
                return i18n.tn("expected {want} or more pieces in the hopper, but {count} piece was in it",
                               "expected {want} or more pieces in the hopper, but {count} pieces were in it",
                               n, want=a["at_least"])
            return i18n.tn("expected at most {want} pieces in the hopper, but {count} piece was in it",
                           "expected at most {want} pieces in the hopper, but {count} pieces were in it",
                           n, want=a["at_most"])
        if k == "scored":
            n = watch.scored_count(a["goal"])
            if not scores_balls(watch.kinds):
                if a["goal"] == ANY_GOAL:
                    return i18n.tn("expected {want} or more pieces scored in any goal, but {count} was",
                                   "expected {want} or more pieces scored in any goal, but {count} were", n,
                                   want=a["at_least"])
                return i18n.tn("expected {want} or more pieces scored in {goal}, but {count} was",
                               "expected {want} or more pieces scored in {goal}, but {count} were", n,
                               want=a["at_least"], goal=a["goal"])
            if a["goal"] == ANY_GOAL:
                return i18n.tn("expected {want} or more balls scored in any goal, but {count} was",
                               "expected {want} or more balls scored in any goal, but {count} were", n,
                               want=a["at_least"])
            return i18n.tn("expected {want} or more balls scored in {goal}, but {count} was",
                           "expected {want} or more balls scored in {goal}, but {count} were", n,
                           want=a["at_least"], goal=a["goal"])
        if k == "aimed":
            target, ok = aim_target(self.world, a["turret"], a["at"])
            if not ok:
                return t("{goal} is outside the turret's range (it stops at {stop}°)", goal=a["at"],
                         stop=f"{round(math.degrees(target), 1):g}")
            off = math.degrees(aim_error(self.world, a["turret"], a["at"]))
            return t("expected {turret} aimed at {goal} within {within}, but it was {off}° off",
                     turret=turret_words(a["turret"]), goal=a["at"], within=_amount(a["within_deg"], "deg"),
                     off=f"{abs(off):.1f}")
        if k == "cylinder":
            out = self.world.pneumatics.extension(a["cylinder"]) / IN
            stroke = next(c.stroke for c in self.world.parts.cylinders if c.name == a["cylinder"]) / IN
            if units.is_machine():
                p = dict(cylinder=a["cylinder"], out=f"{out:.1f} in", stroke=f"{round(stroke, 1):g} in")
            else:
                p = dict(cylinder=a["cylinder"], out=_amount(out, "in", fewer=2), stroke=_amount(stroke, "in", fewer=2))
            if a["is"] == "extended":
                return t("expected {cylinder} extended, but its rod was out {out} of {stroke}", **p)
            return t("expected {cylinder} retracted, but its rod was out {out} of {stroke}", **p)
        if k == "pressure":
            side, psi = ("above", a["above"]) if "above" in a else ("below", a["below"])
            s = self.world.pneumatics.system
            now = s.stored_psi if a["side"] == "stored" else s.working_psi
            # as the block reads: "stored air pressure above 60 psi"
            if units.is_machine():
                p = dict(limit=f"{psi:g} psi", now=f"{now:.1f} psi")
            else:
                p = dict(limit=_amount(psi, "psi"), now=_amount(now, "psi"))
            return {("stored", "above"): t("expected the stored air pressure above {limit}, but it was {now}", **p),
                    ("stored", "below"): t("expected the stored air pressure below {limit}, but it was {now}", **p),
                    ("working", "above"): t("expected the working air pressure above {limit}, but it was {now}", **p),
                    ("working", "below"): t("expected the working air pressure below {limit}, but it was {now}", **p),
                    }[a["side"], side]
        if k == "element":
            side = "above" if "above" in a else "below"
            unit = watch.units[a["element"]]
            now = watch.element_value(a["element"])
            if unit == "deg" or units.is_machine():                    # machine text: the old words, to 0.1
                now = round(now, 1) + 0.0                                 # + 0.0: a panel at rest never reads "-0"
            p = dict(element=a["element"], limit=_amount(a[side], unit), now=_amount(now, unit, fewer=2))
            if side == "above":
                return t("expected {element} above {limit}, but it was {now}", **p)
            return t("expected {element} below {limit}, but it was {now}", **p)
        if k == "clears":
            c = watch.closest.now[a["element"]]
            if units.is_machine():
                now = f"{c / IN:.2f} in" if math.isfinite(c) else f">{NEVER_WITHIN_IN} in"
            else:
                now = units.fmt(c, "length", fewer=1) if math.isfinite(c) else f">{_amount(NEVER_WITHIN_IN, 'in')}"
            return t("expected the robot to clear {element} by at least {limit}, but it was {now}",
                     element=a["element"], limit=_amount(a["at_least_in"], "in"), now=now)
        if k == "sensor":
            return self._sensor_failure(a)
        if k == "partner_lifted":
            p, level = self.world.partner, a.get("level_within_deg")
            want = (t("at least {lift} (level within {level})", lift=_amount(a["at_least_in"], "in"),
                      level=_amount(level, "deg")) if level is not None
                    else t("at least {lift}", lift=_amount(a["at_least_in"], "in")))
            if units.is_machine():
                up = round(p.height(), 1) + 0.0             # + 0.0: a partner at rest never reads "-0"
                up = f"{up:g} in"
            else:
                up = _amount(p.height(), "in", fewer=2)
            return t("expected the partner lifted {want}, but it was {up} up, {tilt}° from level", want=want, up=up,
                     tilt=f"{p.tilt():.1f}")
        if k == "in_zone":
            x, y = (float(c) for c in self.world.data.xpos[self.world.robot_body][:2])
            at = (f"({x:.2f} m, {y:.2f} m)" if units.is_machine()
                  else f"({units.fmt(x, 'field', fewer=1)}, {units.fmt(y, 'field', fewer=1)})")
            if a["is"] == "in":
                return i18n.t("expected the robot in {zone}, but its center was at {at}", zone=a["zone"], at=at)
            return i18n.t("expected the robot out of {zone}, but its center was at {at}", zone=a["zone"], at=at)
        if k == "partner_moved":
            moved = (f"{self.world.partner.moved():.1f} in" if units.is_machine()
                     else _amount(self.world.partner.moved(), 'in', fewer=2))
            return t("expected the partner to move at least {limit}, but it moved {moved}",
                     limit=_amount(a["at_least_in"], "in"), moved=moved)
        if k == "match":
            now = self.match.phase if self.match is not None else None
            return t("expected the match to be in {phase}, but it was in {now}", phase=a["phase"],
                     now=now or t("no phase"))
        if k == "points":
            got = f"{watch.points_so_far(a['phase']):g}"
            if a["phase"] == "total":
                return t("expected {want} or more points, but there were {got}", want=f"{a['at_least']:g}", got=got)
            return t("expected {want} or more points in {phase}, but there were {got}", want=f"{a['at_least']:g}",
                     phase=a["phase"], got=got)
        if k == "station_empty":
            left = self.feeder.left(a["station"]) if self.feeder is not None else None
            if left:
                return t("{station} wasn't empty ({left} left)", station=a["station"], left=left)
            return t("{station} wasn't empty", station=a["station"])
        if k == "made_it_in":
            c = self._final if self._final is not None else self.outcomes.so_far()
            return t("expected at least {share}% to make it in, but {made} of {fed} did", share=f"{a['at_least']:g}",
                     made=c["in"], fed=c["fed"])
        if k == "hanging_from":
            return t("the robot wasn't hanging from {bar}", bar=bar(a["bar"]))
        if k == "held":
            return self._not_held(a)
        if k in ("rests_on", "seated"):
            return self._not_placed(k, a)
        return t("the robot wasn't hanging at {level}", level=a["level"])           # level

    def _sensor_failure(self, a: dict) -> str:
        """What a sensor read instead: a beam broken or clear, a distance, the color seen."""
        t = i18n.t
        name, want = a["sensor"], a["is"]
        spec = next(s for s in self.world.sensors.specs if s.name == name)
        r = self.world.sensors.reading(name)
        if spec.kind in ("beam", "distance"):
            wanted = t("on", context="sensor") if want == "on" else t("off", context="sensor")
        if spec.kind == "beam":
            return t("expected {sensor} {want}, but it was {reading}", sensor=name, want=wanted,
                     reading=t("on (broken)") if r.on else t("off (clear)"))
        if spec.kind == "distance":
            if units.is_machine():
                p = dict(reading=f"{r.distance / IN:.1f} in", threshold=f"{spec.threshold / IN:.1f} in")
            else:
                p = dict(reading=units.fmt(r.distance, "length", fewer=2),
                         threshold=units.fmt(spec.threshold, "length", fewer=2))
            return t("expected {sensor} {want}, but it read {reading} (it is on closer than {threshold})", sensor=name,
                     want=wanted, **p)
        if not r.on:
            saw = t("no piece")
        elif r.color_name[0] in "aeiou":
            saw = t("an {color} piece", color=r.color_name)
        else:
            saw = t("a {color} piece", color=r.color_name)
        what = a["sees"] or t("a piece")
        if want == "on":
            return t("expected {sensor} to see {what}, but it saw {saw}", sensor=name, what=what, saw=saw)
        return t("expected {sensor} not to see {what}, but it saw {saw}", sensor=name, what=what, saw=saw)

    def _not_held(self, a: dict) -> str:
        """How a held check failed: when and how the piece was lost, and what was moving in the second before; or
        that it was never held. It carries its own time (the moment it was lost). How it was lost is grip_watch.py's
        words, in English."""
        watch = self.watch
        i, by = watch.piece_index(a["piece"]), a["by"]
        loss = watch.held.lost(i, by) if watch.held.ever(i, by) else None
        named = watch.piece_names.get(a["piece"], a["piece"]) if i is not None else None
        if loss is None:
            if named:
                return i18n.t("{piece} was never held by {by}", piece=named, by=by)
            return i18n.t("the {piece} was never held by {by}", piece=default_word(watch.kinds), by=by)
        t, how, kind = loss
        lost = f"{how} {by}" if how in ("slipped out of", "slid in") else how
        p = dict(lost=lost, t=f"{t:.1f}", moving=self._moving(t))
        if named:
            return i18n.t("{piece} {lost} at {t} s while {moving}", piece=named, **p)
        return i18n.t("the {piece} {lost} at {t} s while {moving}", piece=kind, **p)

    def _placed_hold(self, cond) -> str | None:
        """A held throughout check that stopped holding because the piece was set down on a rod or element a `seated`
        or `rests_on` condition of the test names (controller ruling P34): "until it was set down on branch_l2 at
        4.4 s" (set down, not placed: the touch may not stay). None for any other loss, and for any other condition."""
        if cond.kind != "held":
            return None
        held = self.watch.held
        i, by = self.watch.piece_index(cond.args["piece"]), cond.args["by"]
        loss = held.lost(i, by) if held.ever(i, by) else None
        if loss is None or not loss[1].startswith(SET_DOWN):
            return None
        on = loss[1].removeprefix(SET_DOWN)
        if on not in self._places:
            return None
        return _w(lambda: i18n.t("until it was set down on {place} at {t} s", place=on, t=f"{loss[0]:.1f}"))

    def _not_placed(self, k: str, a: dict) -> str:
        """How a rests_on or seated check failed: "the coral doesn't rest on shelf (a robot body still touches it)";
        "the coral isn't on branch_l2", "the coral is seated only 0.8 in on branch_l2 (could be knocked off)", or,
        on but short of what was asked, "the coral is seated 2 in of 4 in on branch_l2, less than 3 in"."""
        watch = self.watch
        i, on = watch.piece_index(a["piece"]), a["on"]
        what = (watch.piece_names.get(a["piece"], a["piece"]) if i is not None
                else i18n.t("the {piece}", piece=default_word(watch.kinds)))
        if k == "rests_on":
            why = watch.placed.why_not(i, on)
            if why:
                return i18n.t("{piece} doesn't rest on {place} ({why})", piece=what, place=on, why=why)
            return i18n.t("{piece} doesn't rest on {place}", piece=what, place=on)
        got = watch.placed.seated(on, i)
        if got is None:
            return i18n.t("{piece} isn't on {place}", piece=what, place=on)
        depth, could = got
        if depth < KNOCK_OFF_SHARE * could:
            shown = f"{depth / IN:.1f} in" if units.is_machine() else units.fmt(depth, "length", fewer=2)
            return i18n.t("{piece} is seated only {depth} on {place} (could be knocked off)", piece=what, depth=shown,
                          place=on)
        return i18n.t("{piece} is {seated} on {place}, less than {want}", piece=what, seated=seated_words(depth, could),
                      place=on, want=_amount(a["at_least_in"], "in"))

    def _describe(self, cond, form: str = "is") -> str:
        """describe() with this run's next bar, the field's piece words, its moving elements' units and the robot's
        sensors' kinds; `form` as describe()'s."""
        w, sensors = self.watch, getattr(self.world, "sensors", None)
        kinds = {s.name: s.kind for s in sensors.specs} if sensors is not None else None
        return describe(cond, w.next_bar(), w.kinds, w.piece_names, w.units, kinds, form=form)

    def _doing(self, b) -> str:
        """A waiting block under way, in words."""
        a = b.args
        if b.do == "wait":
            return i18n.t("waiting {seconds} s", seconds=f"{a['seconds']:g}")
        if b.do == "wait_until":
            return i18n.t("waiting for {what}", what=self._describe(a["until"], "waiting"))
        if b.do == "move":
            return i18n.t("moving {joint} to {to}", joint=a["joint"], to=_amount(a["to"], a["unit"]))
        if b.do == "feed_one":
            return i18n.t("feeding one from {station}", station=a["station"])
        if b.do == "turn":
            return i18n.t("turning {degrees}°", degrees=f"{a['degrees']:g}")
        if "distance" in a:
            far = f"{a['distance']:g} m" if units.is_machine() else units.fmt(a["distance"], "field")
            return (i18n.t("driving {direction} {distance}", direction=a["direction"], distance=far)
                    if "direction" in a else i18n.t("driving {distance}", distance=far))
        s = f"{a['seconds']:g}"
        return (i18n.t("driving {direction} for {seconds} s", direction=a["direction"], seconds=s)
                if "direction" in a else i18n.t("driving for {seconds} s", seconds=s))

    def _under_way(self) -> str:
        """The innermost repeat and the block under way, for the time-limit sentence ("" for neither)."""
        f = self.frames[-1]
        doing = None
        if self.active is not None and f.index < len(f.blocks):
            doing = self._doing(f.blocks[f.index]), f.trail + (f.index,)
        if len(self.frames) > 1:
            return (i18n.t(" during repeat ({where}), {doing}", where=_where(f.trail), doing=doing[0]) if doing
                    else i18n.t(" during repeat ({where})", where=_where(f.trail)))
        return i18n.t(", {doing} ({where})", doing=doing[0], where=_where(doing[1])) if doing else ""

    def _line(self, c: dict) -> str:
        p = dict(where=_where(tuple(c["place"])), t=f"{c['t']:.1f}")
        if c["passed"]:
            if c.get("until"):
                return i18n.t("passed: {check} from {t} s {until} ({where})", check=_v(c["text"]),
                              until=_v(c["until"]), **p)
            if c["throughout"]:
                return i18n.t("passed: {check} from {t} s on ({where})", check=_v(c["text"]), **p)
            return i18n.t("passed: {check} at {t} s ({where})", check=_v(c["text"]), **p)
        if c.get("timed"):
            return i18n.t("failed ({where}): {failure}", failure=_v(c["failure"]), where=p["where"])
        return i18n.t("failed ({where}): {failure} at {t} s", failure=_v(c["failure"]), **p)
