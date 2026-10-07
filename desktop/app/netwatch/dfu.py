"""nRF52840 flashing: prebuilt image + key injection + DFU upload.

Why this and not the obvious alternatives
-----------------------------------------
* **Not UF2.** Copying a .uf2 to the bootloader drive writes flash correctly
  (verified by reading the bootloader's own CURRENT.UF2 back) but the resulting
  image never executes a single instruction. Hours went into that; it is a dead
  end on this board.
* **Not compile-on-demand.** arduino-cli plus the Seeed core is ~400 MB on the
  customer's machine for a board that can be served by a 320 KB prebuilt.

So: ship one prebuilt .hex, rewrite the 28-byte key inside it, wrap it in a DFU
package and send it over the bootloader's serial port. That is the transport the
original NetWatch-AnyBLE flasher used (arduino-cli delegates to the same
adafruit-nrfutil), and it is verified end to end on real hardware - flashed,
booted, and heard advertising at -38 dBm with the right key.

adafruit-nrfutil is driven through its Python API rather than its console script
so it keeps working inside the frozen app, where sys.executable is NetWatch.exe.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Callable

from . import uf2
from .keyinject import KEY_LEN, KEY_MAGIC

log = logging.getLogger("netwatch.dfu")

Progress = Callable[[str], None]

# Nordic device type for the nRF52832/52840 Adafruit-style bootloader.
DEV_TYPE = 0x0052
BAUD = 115200
# Opening the port at 1200 baud makes a running application reboot into its
# bootloader, so a board that is already advertising can be reflashed without
# the user touching it. Harmless when it is already in bootloader mode.
TOUCH_BAUD = 1200


class DfuError(Exception):
    """A user-presentable DFU failure."""


def inject_into_hex(hex_text: str, adv_key: bytes) -> str:
    """Rewrite the advertisement key inside a prebuilt Intel HEX image."""
    if len(adv_key) != KEY_LEN:
        raise DfuError(f"advertisement key must be {KEY_LEN} bytes, got {len(adv_key)}")
    try:
        return uf2.patch_hex(hex_text, KEY_MAGIC, adv_key)
    except uf2.UF2Error as exc:
        raise DfuError(
            f"could not place the key in the firmware image ({exc}). The .hex was "
            f"probably not built from NetWatch firmware carrying the key block."
        ) from exc


def read_key_from_hex(hex_text: str) -> bytes:
    blob = b"".join(d for _, d in uf2.parse_hex(hex_text))
    pos = blob.find(KEY_MAGIC)
    if pos < 0:
        raise DfuError("key marker not found in the firmware image")
    return blob[pos + len(KEY_MAGIC):pos + len(KEY_MAGIC) + KEY_LEN]


def upload(hex_text: str, port: str, say: Progress) -> None:
    """Package `hex_text` and send it to `port` over serial DFU."""
    from nordicsemi.dfu.dfu import Dfu
    from nordicsemi.dfu.dfu_transport_serial import DfuTransportSerial
    from nordicsemi.dfu.package import Package

    with tempfile.TemporaryDirectory(prefix="netwatch-dfu-") as tmp:
        work = Path(tmp)
        hex_path = work / "firmware.hex"
        hex_path.write_text(hex_text)
        pkg_path = work / "firmware.zip"

        say("Building the DFU package...")
        try:
            Package(dev_type=DEV_TYPE, app_fw=str(hex_path)).generate_package(str(pkg_path))
        except Exception as exc:
            raise DfuError(f"could not build the DFU package: {exc}") from exc

        say(f"Uploading to {port} over DFU...")
        try:
            transport = DfuTransportSerial(
                com_port=port, baud_rate=BAUD, single_bank=True, touch=TOUCH_BAUD)
            Dfu(zip_file_path=str(pkg_path), dfu_transport=transport).dfu_send_images()
        except Exception as exc:
            raise DfuError(
                f"DFU upload failed: {exc}. Double-tap RESET so the board is in "
                f"bootloader mode, pick its COM port, and try again."
            ) from exc
    say("Device programmed. The board restarts and begins advertising.")


def flash(hex_path: Path, port: str, adv_key: bytes, say: Progress) -> dict:
    """Inject the key into the prebuilt image and DFU it to the board."""
    if not hex_path.exists():
        raise DfuError(f"firmware image missing: {hex_path}")
    text = hex_path.read_text()

    if read_key_from_hex(text) != b"\x00" * KEY_LEN:
        log.warning("prebuilt %s does not ship with a blank key", hex_path.name)

    say("Placing this tag's key into the firmware image...")
    patched = inject_into_hex(text, adv_key)
    if read_key_from_hex(patched) != adv_key:
        raise DfuError("internal error: the key did not survive injection")
    say("Key placed and verified.")

    upload(patched, port, say)
    return {"ok": True, "port": port}
