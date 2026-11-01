"""Parts (mechanisms) and the assembler that turns [[part]] entries into a MuJoCo model and bindings."""

from .model import PartError, load_part

__all__ = ["PartError", "load_part"]
