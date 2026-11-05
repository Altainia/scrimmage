"""CAD to part: read a STEP/STL/OBJ file and build Scrimmage part folders from it, in the browser.

This package is import-light on purpose: it never imports trimesh or cascadio here (only `source.py` reads CAD
files, and only when called), so code that needs just `fit` or `mass` loads without the `cad` extra.
`CadError` is the one error type; its message is plain words meant for the student.
"""

from ..i18n import N_, translated

MISSING_EXTRA = N_('Reading CAD files needs the cad extra: pip install "{package}"')   # said with missing_extra()


def missing_extra() -> str:
    """MISSING_EXTRA in the Language setting, the package (what the student types) filled in."""
    return translated(MISSING_EXTRA, package="scrimmage[cad]")


class CadError(ValueError):
    """A problem the student can fix; the message is one plain sentence saying what's wrong and what to do.
    `field` is the setting at fault ("custom_density", "infill", "material"), for code that mustn't read the
    message's words (spec L3); None when unknown."""

    def __init__(self, message: str = "", field: str | None = None):
        super().__init__(message)
        self.field = field
