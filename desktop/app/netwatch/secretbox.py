"""Encryption at rest for Apple session tokens.

Design rules (important — read before changing):

* The Apple ID **password is never persisted**. It is passed to Apple once to
  obtain a session, then dropped. Only the resulting session token blob is
  stored, and only encrypted.
* On Windows the blob is sealed with DPAPI bound to the current user account, so
  another user on the same machine cannot read it.
* Elsewhere we fall back to AES-GCM with a key file created 0600 in the app data
  directory.

Nothing here is a substitute for full-disk encryption; it stops casual reads and
other local users, not an attacker with your logged-in session.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from .config import DATA_DIR

_WIN = sys.platform.startswith("win")


# --------------------------------------------------------------------------- #
# Windows DPAPI
# --------------------------------------------------------------------------- #
if _WIN:
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def _blob(data: bytes) -> _BLOB:
        buf = ctypes.create_string_buffer(data, len(data))
        return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _unblob(blob: _BLOB) -> bytes:
        out = ctypes.string_at(blob.pbData, blob.cbData)
        _kernel32.LocalFree(blob.pbData)
        return out

    def _dpapi_protect(plaintext: bytes) -> bytes:
        inp, out = _blob(plaintext), _BLOB()
        if not _crypt32.CryptProtectData(
            ctypes.byref(inp), "NetWatch", None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)
        ):
            raise OSError("DPAPI CryptProtectData failed")
        return _unblob(out)

    def _dpapi_unprotect(ciphertext: bytes) -> bytes:
        inp, out = _blob(ciphertext), _BLOB()
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(inp), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)
        ):
            raise OSError("DPAPI CryptUnprotectData failed")
        return _unblob(out)


# --------------------------------------------------------------------------- #
# Portable AES-GCM fallback
# --------------------------------------------------------------------------- #
_KEY_FILE = DATA_DIR / "secret.key"


def _local_key() -> bytes:
    if _KEY_FILE.exists():
        return _KEY_FILE.read_bytes()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    # Create with restrictive permissions before writing anything sensitive.
    fd = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, key)
    finally:
        os.close(fd)
    return key


def _aesgcm_seal(plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(_local_key()).encrypt(nonce, plaintext, None)


def _aesgcm_open(ciphertext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(_local_key()).decrypt(ciphertext[:12], ciphertext[12:], None)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def seal(plaintext: bytes) -> bytes:
    """Encrypt bytes for storage on this machine, for this user."""
    if _WIN:
        return b"DPAPI1" + _dpapi_protect(plaintext)
    return b"AGCM1." + _aesgcm_seal(plaintext)


def open_sealed(blob: bytes) -> bytes:
    """Decrypt what seal() produced. Raises on tampering or wrong user."""
    if blob.startswith(b"DPAPI1"):
        if not _WIN:
            raise ValueError("DPAPI blob cannot be opened on this platform")
        return _dpapi_unprotect(blob[6:])
    if blob.startswith(b"AGCM1."):
        return _aesgcm_open(blob[6:])
    raise ValueError("unrecognised sealed blob")


def backend_name() -> str:
    return "Windows DPAPI (per-user)" if _WIN else "AES-GCM with local key file"
