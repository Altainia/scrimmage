"""Loads the scrimmage_lockstep HALSim extension: `python -m robotpy sim --ws-client --scrimmage-lockstep`.

Lockstep runs on Linux (the extension is built by `pip install`) and on Windows x64 (a prebuilt one ships in
lib/win_amd64). Elsewhere the package has no extension, and loading it installs only the REV and LaserCAN helpers
(the robot runs in real time)."""

from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

logger = logging.getLogger("scrimmage_lockstep")

WINDOWS_X64 = ("AMD64", "x86_64")   # platform.machine() on Windows x64 (x86_64 under some Python builds)


def windows_x64() -> bool:
    """Whether this is Windows on x64, the one Windows the prebuilt extension is for."""
    return sys.platform == "win32" and platform.machine() in WINDOWS_X64


def library_path() -> Path:
    """The extension for this computer: the built .so on Linux, the prebuilt DLL on Windows x64. Anywhere else, a
    path that doesn't exist (nothing is built there; the robot runs in real time)."""
    lib = Path(__file__).resolve().parent / "lib"
    if sys.platform == "win32":
        folder = "win_amd64" if windows_x64() else f"win_{platform.machine().lower() or 'unknown'}"
        return lib / folder / "halsim_scrimmage_lockstep.dll"
    if sys.platform == "darwin":
        return lib / "libhalsim_scrimmage_lockstep.dylib"
    return lib / "libhalsim_scrimmage_lockstep.so"


def seed_library_path() -> Path:
    """The library scrimmage preloads (LD_PRELOAD) so std::random_device, and with it REV's sensor noise, repeats."""
    return Path(__file__).resolve().parent / "lib" / "libscrimmage_seed.so"


def _hal_version() -> str:
    try:
        from importlib.metadata import version
        return version("robotpy-hal")
    except Exception:
        return "unknown"


def _load_windows(hal, path: Path) -> None:
    """Load the prebuilt DLL. Whatever goes wrong is one log line, never an exception: the robot then runs in real
    time (scrimmage sees no lockstep device), and the REV and LaserCAN helpers are installed all the same."""
    if not path.exists():
        logger.warning("lockstep couldn't load: %s is missing (reinstall scrimmage-lockstep), so the robot runs in "
                       "real time", path)
        return
    try:
        retval = hal.loadOneExtension(str(path))
    except Exception as e:
        retval = f"{type(e).__name__}: {e}"
    if retval != 0:
        # The usual cause: a RobotPy whose HAL the DLL can't bind to (it was built against RobotPy 2026's).
        logger.warning("lockstep couldn't load %s (error %s; robotpy-hal %s), so the robot runs in real time: "
                       "updating scrimmage and reinstalling scrimmage-lockstep may help", path, retval, _hal_version())


def loadExtension() -> None:
    import hal

    path = library_path()
    if windows_x64():
        _load_windows(hal, path)
    elif path.exists():
        retval = hal.loadOneExtension(str(path))
        if retval != 0:
            logger.warning("loading %s may have failed (error=%d)", path, retval)
    elif sys.platform.startswith("linux"):
        raise ImportError(f"{path} is missing; reinstall scrimmage-lockstep")
    else:  # nothing built here (see CMakeLists.txt): scrimmage notices no lockstep device and runs in real time
        logger.info("lockstep runs on Linux and Windows x64 only: on this computer the robot runs in real time")
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
