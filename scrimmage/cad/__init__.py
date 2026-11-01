"""CAD to part: read a STEP/STL/OBJ file and build Scrimmage part folders from it, in the browser.

This package is import-light on purpose: it never imports trimesh or cascadio here (only `source.py` reads CAD
files, and only when called), so code that needs just `fit` or `mass` loads without the `cad` extra.
`CadError` is the one error type; its message is plain words meant for the student.
"""

MISSING_EXTRA = 'Reading CAD files needs the cad extra: pip install "scrimmage[cad]"'


class CadError(ValueError):
    """A problem the student can fix; the message is one plain sentence saying what's wrong and what to do."""
