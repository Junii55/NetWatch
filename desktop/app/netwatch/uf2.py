"""UF2 and Intel HEX handling for nRF52840 boards.

The XIAO nRF52840 does not flash like an ESP32. It runs a UF2 bootloader:
double-tap RESET and it mounts as a USB mass-storage volume, and you copy a
.uf2 file onto it. No esptool, no serial protocol — a file copy.

Two jobs here:

  build time  - the Seeed core emits Intel HEX, not UF2, so convert.
  flash time  - inject the tag's key into the prebuilt .uf2.

A UF2 file is a sequence of 512-byte blocks, each carrying a little slice of
flash (256 bytes for Adafruit/Nordic bootloaders) plus the address it belongs
at. There is no per-block checksum, so patching is simply rewriting payload
bytes in place — but a 44-byte key block can straddle a block boundary, so the
patcher maps flash addresses to file offsets rather than assuming contiguity.

Block layout (512 bytes)::

    0   u32 magicStart0 = 0x0A324655  ("UF2\\n")
    4   u32 magicStart1 = 0x9E5D5157
    8   u32 flags
    12  u32 targetAddr        where this payload belongs in flash
    16  u32 payloadSize
    20  u32 blockNo
    24  u32 numBlocks
    28  u32 fileSize / familyID
    32  u8  data[476]
    508 u32 magicEnd   = 0x0AB16F30
"""

from __future__ import annotations

import struct

UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY_ID = 0x00002000
UF2_BLOCK_SIZE = 512
UF2_PAYLOAD = 256                      # what nRF52 bootloaders expect

# Family IDs. A UF2 bootloader silently ignores every block whose family does
# not match its own -- the file lands on the drive, nothing is written, and the
# board never reboots, which looks exactly like a successful copy.
FAMILY_NRF52840 = 0xADA52840        # generic Adafruit nRF52840
FAMILY_XIAO_NRF52840 = 0x28860045   # Seeed XIAO: USB VID 0x2886 << 16 | PID 0x0045

DEFAULT_FAMILY = FAMILY_XIAO_NRF52840


def family_of(data: bytes) -> int | None:
    """Family ID declared by a UF2 file's first block, if it declares one."""
    if len(data) < UF2_BLOCK_SIZE:
        return None
    m0, m1, flags = struct.unpack_from("<III", data, 0)
    if m0 != UF2_MAGIC_START0 or m1 != UF2_MAGIC_START1:
        return None
    if not flags & UF2_FLAG_FAMILY_ID:
        return None
    return struct.unpack_from("<I", data, 28)[0]


def detect_family(drive) -> int | None:
    """Read the family a bootloader expects from the CURRENT.UF2 it exposes.

    More reliable than hardcoding: Seeed, Adafruit and Nordic bootloaders all
    differ, and reading the drive's own dump matches whatever is actually there.
    """
    from pathlib import Path

    cur = Path(drive) / "CURRENT.UF2"
    try:
        with open(cur, "rb") as fh:
            return family_of(fh.read(UF2_BLOCK_SIZE))
    except OSError:
        return None


def set_family(data: bytes, family_id: int) -> bytes:
    """Rewrite every block's family ID (payloads untouched)."""
    out = bytearray(data)
    for off in range(0, len(out), UF2_BLOCK_SIZE):
        struct.pack_into("<I", out, off + 28, family_id)
    return bytes(out)


class UF2Error(Exception):
    pass


# --------------------------------------------------------------------------- #
# Intel HEX -> flat segments
# --------------------------------------------------------------------------- #
def parse_hex(text: str) -> list[tuple[int, bytes]]:
    """Parse Intel HEX into merged, address-sorted (addr, data) segments."""
    chunks: list[tuple[int, bytes]] = []
    upper = 0

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or not line.startswith(":"):
            continue
        try:
            rec = bytes.fromhex(line[1:])
        except ValueError as exc:
            raise UF2Error(f"line {lineno}: not valid hex ({exc})") from exc
        if len(rec) < 5:
            raise UF2Error(f"line {lineno}: record too short")

        count, addr, rtype = rec[0], (rec[1] << 8) | rec[2], rec[3]
        data = rec[4:4 + count]
        if len(data) != count:
            raise UF2Error(f"line {lineno}: truncated record")
        if (sum(rec) & 0xFF) != 0:
            raise UF2Error(f"line {lineno}: bad checksum")

        if rtype == 0x00:                      # data
            chunks.append((upper + addr, data))
        elif rtype == 0x01:                    # EOF
            break
        elif rtype == 0x02:                    # extended segment address
            upper = ((data[0] << 8) | data[1]) << 4
        elif rtype == 0x04:                    # extended linear address
            upper = ((data[0] << 8) | data[1]) << 16
        elif rtype in (0x03, 0x05):            # start address — nothing to place
            continue
        else:
            raise UF2Error(f"line {lineno}: unsupported record type 0x{rtype:02x}")

    if not chunks:
        raise UF2Error("no data records in HEX file")

    # Merge anything contiguous so we emit as few UF2 blocks as possible.
    chunks.sort(key=lambda c: c[0])
    merged: list[tuple[int, bytearray]] = []
    for addr, data in chunks:
        if merged and addr == merged[-1][0] + len(merged[-1][1]):
            merged[-1][1].extend(data)
        else:
            merged.append((addr, bytearray(data)))
    return [(a, bytes(d)) for a, d in merged]


# --------------------------------------------------------------------------- #
# flat segments -> UF2
# --------------------------------------------------------------------------- #
def segments_to_uf2(segments: list[tuple[int, bytes]], *,
                    family_id: int = FAMILY_NRF52840,
                    payload_size: int = UF2_PAYLOAD) -> bytes:
    pieces: list[tuple[int, bytes]] = []
    for addr, data in segments:
        for off in range(0, len(data), payload_size):
            pieces.append((addr + off, data[off:off + payload_size]))

    total = len(pieces)
    if total == 0:
        raise UF2Error("nothing to convert")

    out = bytearray()
    for i, (addr, chunk) in enumerate(pieces):
        block = bytearray(UF2_BLOCK_SIZE)
        struct.pack_into(
            "<IIIIIIII", block, 0,
            UF2_MAGIC_START0, UF2_MAGIC_START1, UF2_FLAG_FAMILY_ID,
            addr, len(chunk), i, total, family_id,
        )
        block[32:32 + len(chunk)] = chunk
        struct.pack_into("<I", block, 508, UF2_MAGIC_END)
        out += block
    return bytes(out)


def hex_to_uf2(text: str, *, family_id: int = FAMILY_NRF52840) -> bytes:
    return segments_to_uf2(parse_hex(text), family_id=family_id)


# --------------------------------------------------------------------------- #
# flat segments -> Intel HEX
# --------------------------------------------------------------------------- #
def _hex_record(rtype: int, addr: int, data: bytes) -> str:
    rec = bytes([len(data), (addr >> 8) & 0xFF, addr & 0xFF, rtype]) + data
    checksum = (-sum(rec)) & 0xFF
    return ":" + rec.hex().upper() + f"{checksum:02X}"


def segments_to_hex(segments: list[tuple[int, bytes]], *, width: int = 16) -> str:
    """Serialise segments back to Intel HEX.

    Needed for the DFU path: adafruit-nrfutil packages a .hex, so a key injected
    into the image has to be written back out in that format.
    """
    out: list[str] = []
    upper = None
    for addr, data in sorted(segments, key=lambda s: s[0]):
        for off in range(0, len(data), width):
            chunk = data[off:off + width]
            a = addr + off
            hi = (a >> 16) & 0xFFFF
            if hi != upper:
                out.append(_hex_record(0x04, 0, bytes([(hi >> 8) & 0xFF, hi & 0xFF])))
                upper = hi
            out.append(_hex_record(0x00, a & 0xFFFF, chunk))
    out.append(_hex_record(0x01, 0, b""))
    return "\n".join(out) + "\n"


def patch_hex(text: str, marker: bytes, payload: bytes) -> str:
    """Replace the bytes following `marker` inside an Intel HEX image."""
    segments = parse_hex(text)
    patched: list[tuple[int, bytes]] = []
    hits = 0
    for addr, data in segments:
        pos = data.find(marker)
        if pos >= 0:
            if data.find(marker, pos + 1) >= 0:
                raise UF2Error("marker appears more than once in a HEX segment")
            buf = bytearray(data)
            start = pos + len(marker)
            if start + len(payload) > len(buf):
                raise UF2Error("payload runs past the end of the HEX segment")
            buf[start:start + len(payload)] = payload
            data = bytes(buf)
            hits += 1
        patched.append((addr, data))
    if hits != 1:
        raise UF2Error(f"expected the marker exactly once, found {hits}")
    return segments_to_hex(patched)


# --------------------------------------------------------------------------- #
# UF2 -> blocks
# --------------------------------------------------------------------------- #
class Uf2Block:
    __slots__ = ("file_offset", "addr", "size")

    def __init__(self, file_offset: int, addr: int, size: int):
        self.file_offset = file_offset      # offset of this block's payload
        self.addr = addr                    # flash address of payload[0]
        self.size = size


def parse_uf2(data: bytes) -> list[Uf2Block]:
    if len(data) % UF2_BLOCK_SIZE:
        raise UF2Error(f"UF2 length {len(data)} is not a multiple of {UF2_BLOCK_SIZE}")
    blocks: list[Uf2Block] = []
    for off in range(0, len(data), UF2_BLOCK_SIZE):
        m0, m1, _flags, addr, size = struct.unpack_from("<IIIII", data, off)
        (end,) = struct.unpack_from("<I", data, off + 508)
        if m0 != UF2_MAGIC_START0 or m1 != UF2_MAGIC_START1 or end != UF2_MAGIC_END:
            raise UF2Error(f"bad UF2 magic in block at offset {off}")
        if size > 476:
            raise UF2Error(f"block at {off} claims payload {size} > 476")
        blocks.append(Uf2Block(off + 32, addr, size))
    if not blocks:
        raise UF2Error("UF2 file has no blocks")
    return blocks


def flatten(data: bytes, blocks: list[Uf2Block]) -> tuple[int, bytearray]:
    """Reconstruct a contiguous image and the flash address it starts at.

    Gaps between segments are filled with 0xFF (erased flash) so that searching
    the result cannot produce a false match spanning a hole.
    """
    base = min(b.addr for b in blocks)
    end = max(b.addr + b.size for b in blocks)
    flat = bytearray(b"\xff" * (end - base))
    for b in blocks:
        flat[b.addr - base:b.addr - base + b.size] = data[b.file_offset:b.file_offset + b.size]
    return base, flat


def write_at(data: bytearray, blocks: list[Uf2Block], addr: int, payload: bytes) -> None:
    """Write `payload` at flash address `addr`, across block boundaries."""
    remaining = len(payload)
    written = 0
    while remaining:
        target = addr + written
        for b in blocks:
            if b.addr <= target < b.addr + b.size:
                within = target - b.addr
                n = min(remaining, b.size - within)
                start = b.file_offset + within
                data[start:start + n] = payload[written:written + n]
                written += n
                remaining -= n
                break
        else:
            raise UF2Error(f"flash address 0x{target:08x} is not present in the UF2")
