"""Loads the scrimmage_lockstep HALSim extension: `python -m robotpy sim --ws-client --scrimmage-lockstep`.

Lockstep is Linux only for now: elsewhere the package has no compiled extension, and loading it installs only the REV
and LaserCAN helpers (the robot runs in real time)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("scrimmage_lockstep")


def library_path() -> Path:
    name = "halsim_scrimmage_lockstep.dll" if sys.platform == "win32" else "libhalsim_scrimmage_lockstep.so"   # SPIKE
    return Path(__file__).resolve().parent / "lib" / name


def seed_library_path() -> Path:
    """The library scrimmage preloads (LD_PRELOAD) so std::random_device, and with it REV's sensor noise, repeats."""
    return Path(__file__).resolve().parent / "lib" / "libscrimmage_seed.so"


def loadExtension() -> None:
    import hal

    path = library_path()
    if path.exists():
        retval = hal.loadOneExtension(str(path))
        if retval != 0:
            logger.warning("loading %s may have failed (error=%d)", path, retval)
    elif sys.platform.startswith("linux"):
        raise ImportError(f"{path} is missing; reinstall scrimmage-lockstep")
    else:  # built only on Linux (see CMakeLists.txt): scrimmage notices no lockstep device and runs in real time
        logger.info("lockstep is Linux only for now: on this computer the robot runs in real time")
    try:  # drive REV SPARKs' simulation (a no-op without robotpy-rev); never stops the robot program
        from . import rev as rev_helper

        rev_helper.install()
    except Exception:
        logger.exception("the scrimmage REV helper couldn't be installed")
    try:  # feed Grapple LaserCANs (a no-op without libgrapplefrc-py); never stops the robot program
        from . import grapple as grapple_helper

        grapple_helper.install()
    except Exception:
        logger.exception("the scrimmage grapple helper couldn't be installed")
