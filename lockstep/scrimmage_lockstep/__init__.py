"""Loads the scrimmage_lockstep HALSim extension: `python -m robotpy sim --ws-client --scrimmage-lockstep`."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("scrimmage_lockstep")


def library_path() -> Path:
    return Path(__file__).resolve().parent / "lib" / "libhalsim_scrimmage_lockstep.so"


def seed_library_path() -> Path:
    """The library scrimmage preloads (LD_PRELOAD) so std::random_device, and with it REV's sensor noise, repeats."""
    return Path(__file__).resolve().parent / "lib" / "libscrimmage_seed.so"


def loadExtension() -> None:
    import hal

    path = library_path()
    if not path.exists():
        raise ImportError(f"{path} is missing; reinstall scrimmage-lockstep")
    retval = hal.loadOneExtension(str(path))
    if retval != 0:
        logger.warning("loading %s may have failed (error=%d)", path, retval)
    try:  # drive REV SPARKs' simulation (a no-op without robotpy-rev); never stops the robot program
        from . import rev as rev_helper

        rev_helper.install()
    except Exception:
        logger.exception("the scrimmage REV helper couldn't be installed")
