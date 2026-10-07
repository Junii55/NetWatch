"""Paths, constants and runtime configuration for NetWatch Desktop."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_VERSION = "2.4.1"

# --------------------------------------------------------------------------- #
# Editions
# --------------------------------------------------------------------------- #
# Two shippable programs, one codebase (a forked tree would drift):
#
#   retail   - the product. Licensed/attributable basemaps only, with first-class
#              support for a keyed provider. NO Google scraping.
#   personal - adds Google's undocumented mt0-3.google.com/vt layers. Convenient,
#              but a Maps Platform terms violation: do not sell this build.
#
# The packaging step writes _build_edition.py, so a shipped binary's edition is
# fixed: a customer cannot turn the unlicensed Google layers on with an
# environment variable. Unfrozen, NETWATCH_EDITION wins so both can be tested.
try:
    from ._build_edition import EDITION as _BAKED_EDITION
except Exception:
    _BAKED_EDITION = ""

if getattr(sys, "frozen", False) and _BAKED_EDITION:
    EDITION = _BAKED_EDITION.strip().lower()
else:
    EDITION = (os.environ.get("NETWATCH_EDITION") or _BAKED_EDITION or "retail").strip().lower()
if EDITION not in ("retail", "personal"):
    EDITION = "retail"

IS_RETAIL = EDITION == "retail"
APP_NAME = "NetWatch" if IS_RETAIL else "NetWatch Personal"

# No tile API keys: every basemap works without an account, and anyone wanting
# their own source points at a self-hosted tile server instead.

# Where the user's data lives. Everything is local; nothing is uploaded.
#
# Deliberately NOT per-edition: both builds share one database so that moving
# between them keeps your tags. Losing a tag's private key makes the board it is
# flashed onto permanently unlocatable, so the editions must never fork storage.
_DATA_NAME = "NetWatch"

# NETWATCH_DATA_DIR redirects all storage. Two uses:
#   * tests MUST set it - anything that creates or deletes tags against the real
#     database can destroy the private key of a board already in the field, and
#     that key is unrecoverable;
#   * portable installs (run from a USB stick, keys travelling with it).
_override = os.environ.get("NETWATCH_DATA_DIR", "").strip()
if _override:
    DATA_DIR = Path(_override).expanduser()
elif sys.platform.startswith("win"):
    _base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    DATA_DIR = _base / _DATA_NAME
elif sys.platform == "darwin":
    DATA_DIR = Path.home() / "Library" / "Application Support" / _DATA_NAME
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / _DATA_NAME.lower()

DB_PATH = DATA_DIR / "netwatch.db"
LOG_PATH = DATA_DIR / "netwatch.log"
ANISETTE_LIBS = DATA_DIR / "anisette-libs.bin"
ANISETTE_PROV = DATA_DIR / "anisette-prov.bin"

# Bundled resources (firmware blobs, web UI). PyInstaller unpacks to _MEIPASS.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
WEB_DIR = Path(__file__).resolve().parent / "web"
FIRMWARE_DIR = BUNDLE_DIR / "firmware" / "prebuilt"

# Loopback API. Port 0 = let the OS pick a free one.
HOST = "127.0.0.1"
PORT = int(os.environ.get("NETWATCH_PORT", "0"))

# Set NETWATCH_DEMO=1 to run the whole UI with synthetic data and no Apple calls.
DEMO_MODE = os.environ.get("NETWATCH_DEMO", "") == "1"

# First-run ownership gate. Off by default - it is a consent/liability control,
# never a security one (anyone intending misuse just ticks the box), and it gets
# in the way on every fresh install.
#
# Turn it back on with NETWATCH_ATTESTATION=1 at build time if you want the
# recorded click for liability cover on a build you sell. The substance - that
# these tags do not trigger Apple's unwanted-tracking alerts - stays in the
# About dialog either way, where it informs without blocking.
REQUIRE_ATTESTATION = os.environ.get("NETWATCH_ATTESTATION", "") == "1"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
