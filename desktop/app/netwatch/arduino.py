"""Compile-and-upload path for boards that cannot use a prebuilt image.

The ESP32 targets ship as prebuilt binaries with the key injected at flash time
(see keyinject.py) — seconds, no toolchain. The XIAO nRF52840 does not work that
way: the only combination proven on real hardware is the original NetWatch-AnyBLE
flow, which rewrites PUBLIC_KEY in the sketch source, compiles with arduino-cli,
and uploads over DFU.

That costs a one-off toolchain install (~300 MB) and a first build of several
minutes on the customer's machine. It is used ONLY for nRF52840; ESP32 keeps the
fast path.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from .config import DATA_DIR

log = logging.getLogger("netwatch.arduino")

Progress = Callable[[str], None]

TOOLS_DIR = DATA_DIR / "tools"
ARDUINO_ZIP = "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip"
ARDUINO_SH = "https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh"
SEEED_URL = "https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json"

# The patcher locates this declaration and replaces the bytes inside the braces.
KEY_DECL = "static const uint8_t PUBLIC_KEY[28]"


class ArduinoError(Exception):
    """A user-presentable toolchain or build failure."""


# --------------------------------------------------------------------------- #
# arduino-cli
# --------------------------------------------------------------------------- #
def _which() -> str | None:
    for name in ("arduino-cli", "arduino-cli.exe"):
        found = shutil.which(name)
        if found:
            return found
    for c in (TOOLS_DIR / "arduino-cli.exe", TOOLS_DIR / "arduino-cli"):
        if c.exists():
            return str(c)
    return None


def _run(cmd: list[str], say: Progress) -> int:
    say("$ " + " ".join(cmd))
    env = os.environ.copy()
    env["PATH"] = str(TOOLS_DIR) + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, env=env)
    except FileNotFoundError:
        say(f"command not found: {cmd[0]}")
        return 127
    assert proc.stdout
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            say(line)
    return proc.wait()


def _download(url: str, dest: Path, say: Progress) -> None:
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    say(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "NetWatch"})
    with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as out:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)


def ensure_toolchain(say: Progress) -> str:
    """Install arduino-cli and the Seeed nRF52 core if they are not present."""
    exe = _which()
    if not exe:
        say("Installing arduino-cli (one time, into your NetWatch folder)...")
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            zp = TOOLS_DIR / "arduino-cli.zip"
            _download(ARDUINO_ZIP, zp, say)
            import zipfile

            with zipfile.ZipFile(zp) as zf:
                zf.extractall(TOOLS_DIR)
            exe = str(TOOLS_DIR / "arduino-cli.exe")
        else:
            sh = TOOLS_DIR / "install.sh"
            _download(ARDUINO_SH, sh, say)
            sh.chmod(0o755)
            if _run(["sh", str(sh), "-b", str(TOOLS_DIR)], say) != 0:
                raise ArduinoError("arduino-cli install script failed")
            exe = str(TOOLS_DIR / "arduino-cli")
        if not Path(exe).exists():
            raise ArduinoError(f"arduino-cli did not unpack to {exe}")

    _run([exe, "config", "init", "--overwrite"], say)
    if _run([exe, "core", "update-index", "--additional-urls", SEEED_URL], say) != 0:
        raise ArduinoError("could not update the board index (needs internet)")
    say("Installing the Seeed nRF52 core if missing (this can take a few minutes)...")
    if _run([exe, "core", "install", "Seeeduino:nrf52", "--additional-urls", SEEED_URL], say) != 0:
        raise ArduinoError("could not install the Seeed nRF52 core")
    return exe


# --------------------------------------------------------------------------- #
# source patch + build
# --------------------------------------------------------------------------- #
def patch_source(text: str, adv_key: bytes) -> str:
    """Replace the PUBLIC_KEY initialiser with this tag's 28 bytes."""
    if len(adv_key) != 28:
        raise ArduinoError(f"advertisement key must be 28 bytes, got {len(adv_key)}")
    start = text.find(KEY_DECL)
    if start < 0:
        raise ArduinoError(f"could not find '{KEY_DECL}' in the sketch")
    brace = text.find("{", start)
    end = text.find("};", brace)
    if brace < 0 or end < 0:
        raise ArduinoError("malformed PUBLIC_KEY block in the sketch")
    body = "\n  " + ", ".join(f"0x{b:02x}" for b in adv_key) + "\n"
    out = text[: brace + 1] + body + text[end:]
    if out.count(KEY_DECL) != 1:
        raise ArduinoError("PUBLIC_KEY appears more than once after patching")
    return out


def _stage(sketch_src: Path, adv_key: bytes) -> Path:
    """Copy the sketch to a space-free dir with the key patched in.

    The ARM toolchain splits paths on spaces, so a user called "Jane Doe" breaks
    the build unless it happens somewhere else entirely.
    """
    if sys.platform.startswith("win"):
        base = Path(r"C:\NetWatch\build")
    else:
        base = Path(tempfile.gettempdir()) / "netwatch-build"
    work = base / sketch_src.parent.name
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    (work / sketch_src.name).write_text(patch_source(sketch_src.read_text(), adv_key))
    return work


def compile_and_upload(sketch: Path, fqbns: list[str], port: str,
                       adv_key: bytes, say: Progress) -> dict:
    """Patch, build and DFU-upload the sketch. Returns {'ok': True, 'fqbn': ...}."""
    exe = ensure_toolchain(say)
    work = _stage(sketch, adv_key)
    say(f"sketch staged at {work}")
    say("Compiling. The first build for this board can take several minutes.")

    used, rc = None, 1
    for fqbn in dict.fromkeys(fqbns):
        say(f"try fqbn {fqbn}")
        rc = _run([exe, "compile", "--fqbn", fqbn, str(work)], say)
        if rc == 0:
            used = fqbn
            break
    if rc != 0 or not used:
        raise ArduinoError("compile failed - see the log above")

    say("Compile OK. Uploading over DFU...")
    if _run([exe, "upload", "-p", port, "--fqbn", used, str(work)], say) != 0:
        raise ArduinoError(
            "upload failed. Double-tap RESET so the board is in bootloader mode, "
            "then pick its port and try again."
        )
    say("Upload complete. The board restarts and begins advertising.")
    return {"ok": True, "fqbn": used}
