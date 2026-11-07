"""server.py's App is split into mixins (one per responsibility): no method may be defined in two of them, or in a
mixin and App itself, because then which one runs would depend on the order of App's bases."""
import itertools

from scrimmage.assembler.server import App


def _own(cls) -> set[str]:
    return {k for k in vars(cls) if not (k.startswith("__") and k.endswith("__"))}


def test_no_method_is_defined_in_two_mixins():
    mixins = App.__bases__
    assert len(mixins) == 7 and all(m.__name__.endswith("Mixin") for m in mixins)
    for m in mixins:
        assert "__init__" not in vars(m), f"{m.__name__} has an __init__: App.__init__ sets the attributes"
    for a, b in itertools.combinations(mixins, 2):
        assert not _own(a) & _own(b), f"{a.__name__} and {b.__name__} both define {sorted(_own(a) & _own(b))}"
    for m in mixins:
        assert not _own(m) & _own(App), f"{m.__name__} and App both define {sorted(_own(m) & _own(App))}"
