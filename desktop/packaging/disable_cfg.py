"""Clear the Control Flow Guard bit on the built NetWatch.exe.

Why this is necessary
---------------------
anisette runs Apple's provisioning libraries inside the Unicorn CPU emulator,
which is a JIT: it writes machine code at runtime and calls into it. PyInstaller
ships a bootloader built with Control Flow Guard enabled, and CFG rejects
indirect calls to targets it has no metadata for -- which is exactly what JIT'd
code is. The result is a `__fastfail` (exit code 0xC0000409) the moment emulation
starts, with no Python traceback, and Apple sign-in dies with it.

Verified: a frozen build crashes at the first `uc.emu_start()`; the identical
code passes unfrozen, and passes frozen once this bit is cleared.

Trade-off
---------
This removes one exploit-mitigation from the executable. DEP and ASLR stay on.
Runtimes that JIT (browsers, .NET, Java) routinely ship without CFG for the same
reason. If you would rather keep CFG, the alternative is to move the Apple
helper into a separate non-CFG process and talk to it over a pipe.

Usage:
    python packaging/disable_cfg.py dist/NetWatch/NetWatch.exe
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

IMAGE_DLLCHARACTERISTICS_GUARD_CF = 0x4000


def dll_characteristics_offset(data: bytes) -> int:
    if data[:2] != b"MZ":
        raise ValueError("not a PE file (missing MZ)")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\x00\x00":
        raise ValueError("not a PE file (missing PE signature)")
    magic = struct.unpack_from("<H", data, pe + 24)[0]
    if magic not in (0x10B, 0x20B):
        raise ValueError(f"unexpected optional header magic 0x{magic:x}")
    # DllCharacteristics sits 70 bytes into the optional header for PE32 and PE32+.
    return pe + 24 + 70


def clear_cfg(path: Path) -> bool:
    data = bytearray(path.read_bytes())
    off = dll_characteristics_offset(bytes(data))
    before = struct.unpack_from("<H", data, off)[0]
    if not before & IMAGE_DLLCHARACTERISTICS_GUARD_CF:
        print(f"{path.name}: CFG already off (0x{before:04X})")
        return False
    after = before & ~IMAGE_DLLCHARACTERISTICS_GUARD_CF
    struct.pack_into("<H", data, off, after)
    path.write_bytes(bytes(data))
    print(f"{path.name}: DllCharacteristics 0x{before:04X} -> 0x{after:04X} (CFG cleared)")
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    target = Path(argv[1])
    if not target.is_file():
        print(f"not found: {target}")
        return 1
    try:
        clear_cfg(target)
    except ValueError as exc:
        print(f"error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
