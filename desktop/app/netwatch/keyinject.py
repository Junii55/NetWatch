"""Inject a tag's 28-byte advertisement key into a prebuilt ESP32 firmware image.

This is what removes the 5-15 minute arduino-cli compile from the product. We
ship one prebuilt .bin per chip; at flash time we overwrite the key in place.

The firmware reserves the key inside a marked struct::

    magic[16] = "NETWATCH-KEYBLK\\0"
    key[28]   = 0x00 * 28

Patching raw bytes in an ESP-IDF application image is not simply a memcpy: the
image carries a trailing XOR checksum over all segment data, and usually an
appended SHA-256 over the whole image. Both must be recomputed or the ROM
bootloader rejects the image and the board boot-loops.

Image layout (esp_image_header_t + segments):

    offset 0   : 24-byte header, magic 0xE9, segment_count, ..., hash_appended
    then       : segment_count x (load_addr u32, data_len u32, data[data_len])
    then       : padding so that (len + 1) % 16 == 0
    then       : 1 checksum byte = 0xEF XOR (all segment data bytes)
    then       : 32-byte SHA-256 of everything above, iff hash_appended
"""

from __future__ import annotations

import hashlib
import struct

KEY_MAGIC = b"NETWATCH-KEYBLK\x00"
KEY_LEN = 28
HEADER_LEN = 24
ESP_IMAGE_MAGIC = 0xE9
CHECKSUM_SEED = 0xEF


class KeyInjectError(Exception):
    pass


def _find_single(data: bytes, needle: bytes) -> int:
    first = data.find(needle)
    if first < 0:
        raise KeyInjectError(
            "key marker not found in firmware image — this .bin was not built "
            "from NetWatch firmware with the NETWATCH_KEYBLOCK struct"
        )
    if data.find(needle, first + 1) >= 0:
        raise KeyInjectError(
            "key marker appears more than once in the image; refusing to guess "
            "which one is the real key block"
        )
    return first


def is_esp_image(data: bytes) -> bool:
    return len(data) > HEADER_LEN and data[0] == ESP_IMAGE_MAGIC


def _segment_spans(data: bytes) -> tuple[list[tuple[int, int]], int, bool]:
    """Return ([(data_start, data_len)], end_of_last_segment, hash_appended)."""
    if not is_esp_image(data):
        raise KeyInjectError("not an ESP32 application image (missing 0xE9 magic)")
    segment_count = data[1]
    hash_appended = bool(data[23])
    spans: list[tuple[int, int]] = []
    off = HEADER_LEN
    for i in range(segment_count):
        if off + 8 > len(data):
            raise KeyInjectError(f"truncated image: segment {i} header runs past EOF")
        _load_addr, seg_len = struct.unpack_from("<II", data, off)
        off += 8
        if off + seg_len > len(data):
            raise KeyInjectError(f"truncated image: segment {i} data runs past EOF")
        spans.append((off, seg_len))
        off += seg_len
    return spans, off, hash_appended


def _recompute(data: bytearray) -> bytearray:
    """Fix the trailing XOR checksum and appended SHA-256 after an in-place edit."""
    spans, end_of_segments, hash_appended = _segment_spans(bytes(data))

    checksum = CHECKSUM_SEED
    for start, length in spans:
        for b in data[start:start + length]:
            checksum ^= b

    # The checksum byte sits at the end of 16-byte-aligned padding.
    pad_len = 15 - (end_of_segments % 16)
    checksum_pos = end_of_segments + pad_len
    if checksum_pos >= len(data):
        raise KeyInjectError("image too short to contain its checksum byte")
    data[checksum_pos] = checksum & 0xFF

    if hash_appended:
        hash_pos = checksum_pos + 1
        if hash_pos + 32 > len(data):
            raise KeyInjectError("image claims an appended SHA-256 but is too short")
        data[hash_pos:hash_pos + 32] = hashlib.sha256(bytes(data[:hash_pos])).digest()

    return data


def is_uf2(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == b"UF2\n"


def _inject_uf2(image: bytes, adv_key: bytes) -> bytes:
    """Patch a UF2 (nRF52840). The key block may straddle two UF2 blocks."""
    from . import uf2 as uf2mod

    blocks = uf2mod.parse_uf2(image)
    base, flat = uf2mod.flatten(image, blocks)

    first = flat.find(KEY_MAGIC)
    if first < 0:
        raise KeyInjectError(
            "key marker not found in UF2 — this image was not built from "
            "NetWatch firmware with the NETWATCH_KEYBLOCK struct"
        )
    if flat.find(KEY_MAGIC, first + 1) >= 0:
        raise KeyInjectError("key marker appears more than once in the UF2")

    out = bytearray(image)
    uf2mod.write_at(out, blocks, base + first + len(KEY_MAGIC), adv_key)
    return bytes(out)


def _read_key_uf2(image: bytes) -> bytes:
    from . import uf2 as uf2mod

    blocks = uf2mod.parse_uf2(image)
    _base, flat = uf2mod.flatten(image, blocks)
    pos = flat.find(KEY_MAGIC)
    if pos < 0:
        raise KeyInjectError("key marker not found in UF2")
    return bytes(flat[pos + len(KEY_MAGIC): pos + len(KEY_MAGIC) + KEY_LEN])


def inject_key(image: bytes, adv_key: bytes) -> bytes:
    """Return a copy of ``image`` with ``adv_key`` written into the key block.

    Handles an ESP-IDF application image (checksum + SHA-256 are repaired), a
    UF2 for nRF52840 (payload bytes rewritten across block boundaries), or a
    plain binary blob.
    """
    if len(adv_key) != KEY_LEN:
        raise KeyInjectError(f"advertisement key must be {KEY_LEN} bytes, got {len(adv_key)}")

    if is_uf2(image):
        return _inject_uf2(image, adv_key)

    pos = _find_single(image, KEY_MAGIC)
    key_at = pos + len(KEY_MAGIC)
    if key_at + KEY_LEN > len(image):
        raise KeyInjectError("key block runs past the end of the image")

    out = bytearray(image)
    out[key_at:key_at + KEY_LEN] = adv_key

    if is_esp_image(image):
        out = _recompute(out)
    return bytes(out)


def read_key(image: bytes) -> bytes:
    """Read the key currently embedded in an image (0s for an unpatched build)."""
    if is_uf2(image):
        return _read_key_uf2(image)
    pos = _find_single(image, KEY_MAGIC)
    return image[pos + len(KEY_MAGIC): pos + len(KEY_MAGIC) + KEY_LEN]


def verify_image(data: bytes) -> bool:
    """True if an image is internally consistent.

    ESP32: trailing checksum byte and appended SHA-256 both match.
    UF2:   every block carries valid magic and a sane payload size.
    """
    if is_uf2(data):
        try:
            from . import uf2 as uf2mod
            uf2mod.parse_uf2(data)
            return True
        except Exception:
            return False
    if not is_esp_image(data):
        return True  # nothing to verify
    try:
        spans, end_of_segments, hash_appended = _segment_spans(data)
    except KeyInjectError:
        return False

    checksum = CHECKSUM_SEED
    for start, length in spans:
        for b in data[start:start + length]:
            checksum ^= b

    pad_len = 15 - (end_of_segments % 16)
    checksum_pos = end_of_segments + pad_len
    if checksum_pos >= len(data) or data[checksum_pos] != (checksum & 0xFF):
        return False

    if hash_appended:
        hash_pos = checksum_pos + 1
        if hash_pos + 32 > len(data):
            return False
        if data[hash_pos:hash_pos + 32] != hashlib.sha256(data[:hash_pos]).digest():
            return False
    return True
