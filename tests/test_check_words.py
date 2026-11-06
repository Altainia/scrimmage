"""A signal check's words (checks.py, cleanup A10): each is a whole sentence for a translator, never a translated
piece inside another, and its en-US words are its machine text, word for word."""
from types import SimpleNamespace

import pytest

from scrimmage import i18n, units
from scrimmage.checks import SignalCheck


def _check(kind="always", hal=False, period=None, **limits):
    c = object.__new__(SignalCheck)
    c.kind, c.label = kind, "check"
    c.min, c.max, c.equals, c.approx, c.tol = (limits.get(k) for k in ("min", "max", "equals", "approx", "tol"))
    c.signal = SimpleNamespace(name="hal:PWM/3" if hal else "arm.angle", read=lambda s: None)
    c.uses_nt, c.nt_period = period is not None, period
    c._reset()
    return c


SESSION = SimpleNamespace(t=1.0, bridge=SimpleNamespace(hal=SimpleNamespace(devices={("DIO", 0)})))


def _missing(c):
    c.n_missing, c.first_missing, c.first_fail, c.seen = 3, 0.25, 0.25, True
    return c.end(SESSION)


@pytest.fixture
def pseudo(monkeypatch):
    """en-GB as a pseudo catalog: every sentence reads "XX <source>"."""
    real = i18n._entry
    monkeypatch.setattr(i18n, "_entry", lambda key, lang, plural: ("XX " + key.split("\u0004")[-1])
                        if lang == "en-GB" and not plural else real(key, lang, plural))


@pytest.mark.parametrize("hal", [False, True])
@pytest.mark.parametrize("period", [None, 0.02])
def test_a_signal_with_no_value_is_one_sentence(hal, period, pseudo):
    r = _missing(_check(hal=hal, period=period, min=1.0, max=2.0))
    with units.shown_in("imperial", "en-US"):
        assert r.words() == r.message      # en-US: the machine text, word for word
    with units.shown_in("imperial", "en-GB"):
        gb = r.words()
    # one sentence, translated once: the "has no value" part is not a translated piece inside it
    assert gb.startswith("XX ") and gb.count("XX ") == 2, gb   # the sentence, and the "and" of the expected range
    assert "XX >= 1 and <= 2" in gb


def test_the_expected_range_takes_the_translators_and(pseudo):
    c = _check(kind="at_end", min=1.0, max=2.0)
    r = c.end(SimpleNamespace(t=1.0))
    with units.shown_in("imperial", "en-US"):
        assert r.words() == r.message == "arm.angle has no value at the end of the step"
    c.signal.read = lambda s: 3.0
    r = c.end(SimpleNamespace(t=1.0))
    with units.shown_in("imperial", "en-US"):
        assert r.words() == r.message == "arm.angle = 3 at the end; expected >= 1 and <= 2"
    with units.shown_in("imperial", "en-GB"):
        assert r.words() == "XX arm.angle = 3 at the end; expected XX >= 1 and <= 2"
