"""Tests for firmware key injection, including ESP32 image checksum/hash repair."""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import secrets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from netwatch.keyinject import (  # noqa: E402
    KEY_LEN, KEY_MAGIC, KeyInjectError, inject_key, is_esp_image, read_key, verify_image,
)


def build_esp_image(payload: bytes, *, hash_appended: bool = True, segments: int = 2) -> bytes:
    """Construct a structurally valid ESP-IDF application image around `payload`."""
    header = bytearray(24)
    header[0] = 0xE9
    header[1] = segments
    header[2] = 0x02                      # spi_mode
    header[3] = 0x20                      # spi_speed/size
    struct.pack_into("<I", header, 4, 0x400080A0)   # entry_addr
    struct.pack_into("<H", header, 12, 0x0000)      # chip_id = ESP32
    header[23] = 1 if hash_appended else 0

    bodies = [payload]
    for i in range(segments - 1):
        bodies.append(bytes(range(256)) * (i + 1))

    out = bytearray(header)
    for i, body in enumerate(bodies):
        out += struct.pack("<II", 0x3F400020 + i * 0x1000, len(body))
        out += body

    # Pad so the checksum byte lands on a 16-byte boundary.
    pad_len = 15 - (len(out) % 16)
    out += b"\x00" * pad_len

    checksum = 0xEF
    off = 24
    for _ in range(segments):
        _addr, seg_len = struct.unpack_from("<II", out, off)
        off += 8
        for b in out[off:off + seg_len]:
            checksum ^= b
        off += seg_len
    out.append(checksum & 0xFF)

    if hash_appended:
        out += hashlib.sha256(bytes(out)).digest()
    return bytes(out)


def key_block(key: bytes = b"\x00" * KEY_LEN) -> bytes:
    return b"....filler...." + KEY_MAGIC + key + b"....trailing...."


def main() -> int:
    ok = True

    def check(label: str, cond: bool) -> None:
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS" if cond else "FAIL"), label)

    # --- plain blob (non-ESP) ----------------------------------------------
    blob = key_block()
    new_key = secrets.token_bytes(KEY_LEN)
    patched = inject_key(blob, new_key)
    check("plain blob: key injected", read_key(patched) == new_key)
    check("plain blob: length unchanged", len(patched) == len(blob))
    check("plain blob: not detected as ESP image", not is_esp_image(blob))

    # --- real-shaped ESP32 image -------------------------------------------
    img = build_esp_image(key_block())
    check("synthetic image is ESP image", is_esp_image(img))
    check("synthetic image verifies clean", verify_image(img))
    check("unpatched key is all zero", read_key(img) == b"\x00" * KEY_LEN)

    k = secrets.token_bytes(KEY_LEN)
    out = inject_key(img, k)
    check("ESP image: key injected", read_key(out) == k)
    check("ESP image: length unchanged", len(out) == len(img))
    check("ESP image: checksum+sha repaired", verify_image(out))

    # The hash must actually change (proves we recomputed, not copied).
    check("ESP image: sha256 tail differs", out[-32:] != img[-32:])
    check("ESP image: sha matches content", out[-32:] == hashlib.sha256(out[:-32]).digest())

    # --- tamper detection ---------------------------------------------------
    bad = bytearray(out)
    bad[30] ^= 0xFF
    check("tampered image fails verification", not verify_image(bytes(bad)))

    # --- no-hash variant ----------------------------------------------------
    img2 = build_esp_image(key_block(), hash_appended=False, segments=1)
    check("no-hash image verifies", verify_image(img2))
    out2 = inject_key(img2, k)
    check("no-hash image: key injected", read_key(out2) == k)
    check("no-hash image: checksum repaired", verify_image(out2))

    # --- re-injection is stable (reflash same board twice) ------------------
    k2 = secrets.token_bytes(KEY_LEN)
    out3 = inject_key(out, k2)
    check("re-injection replaces key", read_key(out3) == k2)
    check("re-injection still verifies", verify_image(out3))

    # --- error cases --------------------------------------------------------
    try:
        inject_key(b"no marker anywhere", secrets.token_bytes(KEY_LEN))
        check("missing marker raises", False)
    except KeyInjectError:
        check("missing marker raises", True)

    try:
        inject_key(key_block(), b"\x01" * 10)
        check("wrong key length raises", False)
    except KeyInjectError:
        check("wrong key length raises", True)

    try:
        inject_key(key_block() + key_block(), secrets.token_bytes(KEY_LEN))
        check("duplicate marker raises", False)
    except KeyInjectError:
        check("duplicate marker raises", True)

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
