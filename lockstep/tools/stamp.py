"""The stamp of the prebuilt Windows extension: the SHA-256 of the source it was built from (src/scrimmage_lockstep.cpp
and CMakeLists.txt, CRLF read as LF so a Windows checkout hashes the same). It says which source the committed DLL came
from; it doesn't claim a reproducible binary (the compiler and the HAL headers can differ).

    python lockstep/tools/stamp.py                print the source's hash
    python lockstep/tools/stamp.py --write DIR    write it to DIR/SOURCE.sha256 (CI, beside the DLL it just built)
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

LOCKSTEP = Path(__file__).resolve().parents[1]
SOURCES = ("src/scrimmage_lockstep.cpp", "CMakeLists.txt")   # relative to lockstep/, hashed in this order
DLL_NAME = "halsim_scrimmage_lockstep.dll"
STAMP_NAME = "SOURCE.sha256"
DLL_DIR = LOCKSTEP / "scrimmage_lockstep" / "lib" / "win_amd64"   # where the committed DLL and its stamp live


def source_hash(root: Path = LOCKSTEP) -> str:
    """The hash of the extension's source under `root` (a lockstep/ folder). Each file's name and length go in
    before its bytes, so moving text from one file to the other changes it."""
    h = hashlib.sha256()
    for rel in SOURCES:
        data = (Path(root) / rel).read_bytes().replace(b"\r\n", b"\n")
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()


def read_stamp(folder: Path = DLL_DIR) -> str | None:
    """The hash recorded in folder/SOURCE.sha256, None when there is no stamp."""
    try:
        text = (Path(folder) / STAMP_NAME).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    return text.split()[0] if text.split() else None


def write_stamp(folder: Path, root: Path = LOCKSTEP) -> str:
    """Write the source's hash to folder/SOURCE.sha256; returns it."""
    digest = source_hash(root)
    (Path(folder) / STAMP_NAME).write_text(f"{digest}\n", encoding="ascii", newline="\n")
    return digest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="The SHA-256 of the lockstep extension's source (the Windows DLL's stamp)")
    ap.add_argument("--write", metavar="DIR", type=Path, help=f"write it to DIR/{STAMP_NAME}")
    args = ap.parse_args(argv)
    print(write_stamp(args.write) if args.write is not None else source_hash())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
