"""Board detection and flashing.

The customer never installs a toolchain. We ship prebuilt images per chip,
inject the tag's advertisement key into the application image (repairing its
checksum and SHA-256 — see `keyinject`), and write it with esptool. A flash is
seconds, not the 5-15 minute arduino-cli compile it replaces.

Firmware layout on disk::

    firmware/prebuilt/<chip>/manifest.json
    firmware/prebuilt/<chip>/bootloader.bin
    firmware/prebuilt/<chip>/partitions.bin
    firmware/prebuilt/<chip>/firmware.bin      <- key injected here
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Callable

from . import keyinject
from .config import BUNDLE_DIR, FIRMWARE_DIR

log = logging.getLogger("netwatch.flash")

Progress = Callable[[str], None]

# Chips with a BLE radio. ESP32-S2 has none, so a tag can never work on it.
SUPPORTED = {
    "esp32": "ESP32 (WROOM / DevKit)",
    "esp32c3": "ESP32-C3",
    "esp32c6": "ESP32-C6",
    "esp32s3": "ESP32-S3",
    "nrf52840": "Seeed XIAO nRF52840",
}
NO_BLE = {"esp32s2": "ESP32-S2", "esp8266": "ESP8266"}

# nRF52840 boards do not speak esptool. They expose a UF2 bootloader: double-tap
# RESET, a USB volume appears, and copying a .uf2 onto it writes flash. Detection
# is therefore "look for a removable drive with INFO_UF2.TXT", not a serial probe.
UF2_INFO = "INFO_UF2.TXT"
UF2_CURRENT = "CURRENT.UF2"

# Board-ID strings seen in INFO_UF2.TXT -> our chip id.
UF2_BOARD_IDS = {
    "seeed_xiao_nrf52840": "nrf52840",
    "seeed_xiao_nrf52840_sense": "nrf52840",
    "seeed_xiao_nrf52840_plus": "nrf52840",
    "seeed_xiao_nrf52840_sense_plus": "nrf52840",
}

USB_HINTS = {
    (0x10C4, 0xEA60): "CP2102 USB-UART (classic ESP32)",
    (0x1A86, 0x7523): "CH340 USB-UART (classic ESP32)",
    (0x1A86, 0x55D4): "CH9102 USB-UART",
    (0x303A, 0x1001): "Espressif native USB",
    (0x303A, 0x0002): "Espressif native USB",
}


class FlashError(Exception):
    """A user-presentable flashing failure."""


# --------------------------------------------------------------------------- #
# Ports
# --------------------------------------------------------------------------- #
def _uf2_info(root: Path) -> dict | None:
    """Read INFO_UF2.TXT from a mounted UF2 bootloader volume."""
    info = root / UF2_INFO
    try:
        text = info.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip().lower()] = v.strip()
    board_id = fields.get("board-id", "")
    return {
        "model": fields.get("model", board_id or "UF2 board"),
        "board_id": board_id,
        "chip": UF2_BOARD_IDS.get(board_id.lower(), ""),
        "softdevice": fields.get("softdevice", ""),
    }


def list_uf2_drives() -> list[dict]:
    """Mounted UF2 bootloader volumes (a XIAO after a double-tap RESET)."""
    import string

    out = []
    roots: list[Path] = []
    if os.name == "nt":
        roots = [Path(f"{d}:/") for d in string.ascii_uppercase]
    else:
        for base in ("/media", "/run/media", "/Volumes"):
            b = Path(base)
            if b.is_dir():
                for entry in b.iterdir():
                    roots.append(entry)
                    if entry.is_dir():
                        roots.extend(x for x in entry.iterdir() if x.is_dir())

    for root in roots:
        try:
            if not (root / UF2_INFO).is_file():
                continue
        except OSError:
            continue
        info = _uf2_info(root)
        if not info:
            continue
        out.append({
            "port": str(root),
            "kind": "uf2",
            "description": info["model"],
            "hint": f"UF2 bootloader — {info['model']}",
            "likely_board": True,
            "chip": info["chip"],
            "board_id": info["board_id"],
        })
    return out


def list_ports() -> list[dict]:
    """Everything flashable: ESP32 serial ports and UF2 bootloader drives."""
    out: list[dict] = []
    try:
        from serial.tools import list_ports as lp
        for p in lp.comports():
            hint = USB_HINTS.get((p.vid, p.pid), "")
            out.append({
                "port": p.device,
                "kind": "serial",
                "description": p.description or "",
                "hint": hint,
                "likely_board": bool(hint),
            })
    except ImportError:
        pass

    out.extend(list_uf2_drives())
    # Surface probable boards first.
    out.sort(key=lambda d: (not d["likely_board"], d["port"]))
    return out


def _is_uf2_target(port: str) -> bool:
    try:
        return (Path(port) / UF2_INFO).is_file()
    except OSError:
        return False


def _detect_uf2(port: str) -> dict:
    root = Path(port)
    info = _uf2_info(root)
    if not info:
        raise FlashError(f"{port} is not a UF2 bootloader volume")
    chip = info["chip"]
    if not chip:
        raise FlashError(
            f"Unrecognised UF2 board '{info['board_id'] or info['model']}'. "
            f"NetWatch supports the Seeed XIAO nRF52840 family."
        )
    return {
        "chip": chip,
        "label": info["model"],
        "mac": "",                       # not exposed by the bootloader
        "supported": True,
        "firmware_available": chip in available_firmware(),
        "kind": "uf2",
        "board_id": info["board_id"],
        "softdevice": info["softdevice"],
        # Flags the UI that this board is flashed over serial DFU, not esptool.
        "dfu": chip in DFU_CHIPS,
    }


def _detect_nrf_serial(port: str) -> dict | None:
    """A XIAO in bootloader mode also exposes a COM port; DFU uploads go there."""
    try:
        from serial.tools import list_ports as lp
        for p in lp.comports():
            if p.device == port and p.vid == 0x2886:
                return {
                    "chip": "nrf52840",
                    "label": "Seeed XIAO nRF52840 (bootloader)",
                    "mac": "",
                    "supported": True,
                    "firmware_available": "nrf52840" in available_firmware(),
                    "kind": "serial",
                    "dfu": True,
                }
    except ImportError:
        pass
    return None


def detect(port: str) -> dict:
    """Identify the board behind `port`. The app's 'Connect device' step.

    `port` is a serial device for ESP32, or a mounted UF2 volume for nRF52840.
    """
    if _is_uf2_target(port):
        return _detect_uf2(port)

    nrf = _detect_nrf_serial(port)
    if nrf:
        return nrf

    import esptool

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            esp = esptool.detect_chip(port=port, baud=115200, connect_attempts=3)
            try:
                chip = (esp.CHIP_NAME or "").lower().replace("-", "").replace(" ", "")
                mac = ":".join(f"{b:02X}" for b in esp.read_mac())
                desc = esp.CHIP_NAME
            finally:
                with contextlib.suppress(Exception):
                    esp.hard_reset()
                with contextlib.suppress(Exception):
                    esp._port.close()
    except Exception as exc:
        raise FlashError(
            f"Could not talk to a board on {port}. Use a DATA USB cable, then hold "
            f"BOOT, tap RST, release BOOT and try again. ({exc})"
        ) from exc

    if chip in NO_BLE:
        raise FlashError(
            f"{NO_BLE[chip]} has no Bluetooth radio, so it cannot be a tag. "
            f"Use an ESP32, C3, C6 or S3."
        )
    supported = chip in SUPPORTED
    return {
        "chip": chip,
        "label": SUPPORTED.get(chip, desc),
        "mac": mac,
        "supported": supported,
        "firmware_available": supported and manifest_path(chip).exists(),
    }


# --------------------------------------------------------------------------- #
# Firmware images
# --------------------------------------------------------------------------- #
def manifest_path(chip: str) -> Path:
    return FIRMWARE_DIR / chip / "manifest.json"


def sketch_dir(name: str) -> Path:
    """Locate a firmware sketch folder in dev and frozen layouts."""
    for base in (FIRMWARE_DIR.parent, BUNDLE_DIR / "firmware"):
        p = base / name
        if (p / f"{name}.ino").exists():
            return p
    return FIRMWARE_DIR.parent / name


# Chips flashed over serial DFU from a prebuilt .hex rather than by esptool.
DFU_CHIPS = {"nrf52840"}


def available_firmware() -> list[str]:
    """Chips we can flash.

    A leading underscore marks a prebuilt that exists but is deliberately not
    shipped, so it stays hidden.
    """
    if not FIRMWARE_DIR.exists():
        return []
    return sorted(d.name for d in FIRMWARE_DIR.iterdir()
                  if (d / "manifest.json").exists() and not d.name.startswith("_"))


def load_manifest(chip: str) -> dict:
    mp = manifest_path(chip)
    if not mp.exists():
        raise FlashError(
            f"No prebuilt firmware bundled for {chip}. "
            f"Available: {', '.join(available_firmware()) or 'none'}."
        )
    try:
        return json.loads(mp.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise FlashError(f"firmware manifest for {chip} is corrupt: {exc}") from exc


def prepare_images(chip: str, adv_key: bytes, workdir: Path) -> list[tuple[str, Path]]:
    """Materialise the flash set, with the key injected into the app image."""
    man = load_manifest(chip)
    base = FIRMWARE_DIR / chip
    out: list[tuple[str, Path]] = []
    injected = False

    for part in man.get("parts", []):
        src = base / part["path"]
        if not src.exists():
            raise FlashError(f"firmware file missing: {src.name} for {chip}")
        data = src.read_bytes()
        if part.get("inject_key"):
            data = keyinject.inject_key(data, adv_key)
            if not keyinject.verify_image(data):
                raise FlashError(
                    "internal error: patched firmware failed its own integrity check; "
                    "refusing to flash a board that would not boot"
                )
            injected = True
        dst = workdir / src.name
        dst.write_bytes(data)
        out.append((str(part["offset"]), dst))

    if not injected:
        raise FlashError(f"manifest for {chip} marks no part with inject_key")
    return out


# --------------------------------------------------------------------------- #
# Flashing
# --------------------------------------------------------------------------- #
def _run_esptool(argv: list[str], progress: Progress) -> None:
    import esptool

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            esptool.main(argv)
    except SystemExit as exc:          # esptool exits non-zero on failure
        if exc.code not in (0, None):
            progress(buf.getvalue())
            raise FlashError(f"esptool failed (exit {exc.code})")
    except Exception as exc:
        progress(buf.getvalue())
        raise FlashError(f"esptool error: {exc}") from exc
    finally:
        text = buf.getvalue()
        if text:
            for line in text.splitlines():
                if line.strip():
                    progress(line)


def _flash_uf2(port: str, chip: str, adv_key: bytes, say: Progress) -> dict:
    """Flash an nRF52840 by copying a key-injected UF2 onto its bootloader drive."""
    from . import uf2 as uf2mod

    root = Path(port)
    if not (root / UF2_INFO).is_file():
        raise FlashError(
            f"{port} is not in bootloader mode. Double-tap RESET on the board "
            f"until its drive appears, then try again."
        )

    man = load_manifest(chip)
    parts = [p for p in man.get("parts", []) if p.get("inject_key")]
    if len(parts) != 1:
        raise FlashError(f"manifest for {chip} must mark exactly one part inject_key")
    src = FIRMWARE_DIR / chip / parts[0]["path"]
    if not src.exists():
        raise FlashError(f"firmware file missing: {src.name} for {chip}")

    say(f"Preparing firmware for {SUPPORTED.get(chip, chip)}...")
    image = src.read_bytes()
    image = keyinject.inject_key(image, adv_key)
    if not keyinject.verify_image(image):
        raise FlashError("internal error: patched UF2 failed its own integrity check")

    # A UF2 bootloader silently discards blocks whose family id is not its own:
    # the copy appears to succeed and nothing is written. Match the family the
    # drive itself advertises rather than trusting a hardcoded constant.
    want = uf2mod.detect_family(root)
    have = uf2mod.family_of(image)
    if want and want != have:
        say(f"Matching bootloader family 0x{want:08X} (image had 0x{have or 0:08X})")
        image = uf2mod.set_family(image, want)
    say("Key injected and image verified.")

    target = root / "NETWATCH.UF2"
    say(f"Copying {len(image):,} bytes to {target}...")
    try:
        with open(target, "wb") as fh:
            fh.write(image)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass            # the volume often vanishes mid-flush; that is success
    except OSError as exc:
        # The bootloader reboots the instant it has the whole file, so the
        # handle can die underneath us. Verify rather than assume failure.
        say(f"Write ended with {exc.__class__.__name__} (normal — the board reboots).")

    say("Waiting for the board to restart...")
    for _ in range(20):
        time.sleep(0.5)
        if not (root / UF2_INFO).is_file():
            say("Bootloader drive gone — the board restarted.")
            return {"ok": True, "chip": chip, "port": port}

    # Still mounted. Try to confirm via the bootloader's own dump of flash, but
    # treat a mismatch as "needs a reset", NOT as a failure:
    #
    #   * this bootloader programs one image per session and then waits for a
    #     reset, so a second flash without resetting lands in the FAT unapplied;
    #   * Windows caches the synthetic volume, so CURRENT.UF2 can be stale.
    #
    # Reporting failure here would throw away a write that is actually fine.
    say("Drive still mounted; checking what the bootloader reports...")
    verified = False
    try:
        current = (root / UF2_CURRENT).read_bytes()
        blocks = uf2mod.parse_uf2(current)
        _base, flat = uf2mod.flatten(current, blocks)
        pos = flat.find(keyinject.KEY_MAGIC)
        if pos >= 0:
            in_flash = bytes(flat[pos + len(keyinject.KEY_MAGIC):
                                  pos + len(keyinject.KEY_MAGIC) + keyinject.KEY_LEN])
            verified = in_flash == adv_key
    except Exception as exc:
        say(f"(could not read back {UF2_CURRENT}: {exc})")

    if verified:
        say("Verified: the tag's key is in flash. Tap RESET once to start it.")
    else:
        say("Firmware written. This bootloader applies one image per session, "
            "so tap RESET on the board to start it.")
    return {"ok": True, "chip": chip, "port": port,
            "needs_reset": True, "verified": verified}


def _nrf_port(port: str) -> str:
    """Resolve the COM port to DFU to, given whatever the user picked.

    A XIAO in bootloader mode appears as BOTH a drive and a COM port; DFU needs
    the COM port. If the drive was selected, find the matching serial device
    rather than making the user work it out.
    """
    if port and not _is_uf2_target(port):
        return port
    try:
        from serial.tools import list_ports as lp
        for p in lp.comports():
            if p.vid == 0x2886:
                return p.device
    except ImportError:
        pass
    raise FlashError(
        "Could not find the board's COM port. Double-tap RESET so its drive "
        "appears, then Rescan and pick the COM port entry."
    )


def _flash_nrf(port: str, chip: str, adv_key: bytes, say: Progress) -> dict:
    """Prebuilt image + key injection + DFU. See netwatch/dfu.py for why."""
    from . import dfu as dfumod

    man = load_manifest(chip)
    parts = [p for p in man.get("parts", []) if p.get("inject_key")]
    if len(parts) != 1:
        raise FlashError(f"manifest for {chip} must mark exactly one part inject_key")
    image = FIRMWARE_DIR / chip / parts[0]["path"]

    target = _nrf_port(port)
    if target != port:
        say(f"Using {target} for DFU (you picked {port}).")
    say(f"Flashing {SUPPORTED.get(chip, chip)}...")
    try:
        dfumod.flash(image, target, adv_key, say)
    except dfumod.DfuError as exc:
        raise FlashError(str(exc)) from exc
    return {"ok": True, "chip": chip, "port": target}


def flash_tag(port: str, chip: str, adv_key: bytes, progress: Progress | None = None) -> dict:
    """Inject `adv_key` into the prebuilt image for `chip` and flash it to `port`."""
    say: Progress = progress or (lambda m: None)
    if len(adv_key) != keyinject.KEY_LEN:
        raise FlashError(f"advertisement key must be {keyinject.KEY_LEN} bytes")

    if chip in DFU_CHIPS:
        return _flash_nrf(port, chip, adv_key, say)

    man = load_manifest(chip)
    with tempfile.TemporaryDirectory(prefix="netwatch-flash-") as tmp:
        workdir = Path(tmp)
        say(f"Preparing firmware for {SUPPORTED.get(chip, chip)}...")
        images = prepare_images(chip, adv_key, workdir)
        say("Key injected and image integrity verified.")

        argv = [
            "--chip", chip,
            "--port", port,
            "--baud", str(man.get("baud", 460800)),
            "write-flash",
            "--flash-mode", man.get("flash_mode", "dio"),
            "--flash-freq", man.get("flash_freq", "80m"),
            "--flash-size", man.get("flash_size", "4MB"),
        ]
        for offset, path in images:
            argv += [offset, str(path)]

        say(f"Writing {len(images)} image(s) to {port}...")
        try:
            _run_esptool(argv, say)
        except FlashError:
            # esptool 5 renamed commands; retry the legacy spelling once.
            legacy = [a.replace("write-flash", "write_flash")
                       .replace("--flash-mode", "--flash_mode")
                       .replace("--flash-freq", "--flash_freq")
                       .replace("--flash-size", "--flash_size") for a in argv]
            if legacy == argv:
                raise
            say("Retrying with legacy esptool command names...")
            _run_esptool(legacy, say)

    say("Flash complete. Tap RST — the tag starts advertising immediately.")
    return {"ok": True, "chip": chip, "port": port}
