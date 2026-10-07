"""Tag key material: generation, import, export.

A tag is a P-224 key pair. The public (advertisement) key goes on the ESP32; the
private key stays here and is the only thing that can decrypt that tag's
location reports. Lose it and the tag is permanently unlocatable — which is why
`store` seals it and why we never silently overwrite one.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from findmy import KeyPair


def _mac_from_adv(adv_key: bytes) -> str:
    return ":".join(f"{b:02X}" for b in bytes(
        [adv_key[0] | 0xC0, adv_key[1], adv_key[2], adv_key[3], adv_key[4], adv_key[5]]
    ))


def _pack(kp: KeyPair, name: str) -> dict:
    adv = kp.adv_key_bytes
    return {
        "name": name,
        "privateKey": kp.private_key_b64,
        "advertisementKey": kp.adv_key_b64,
        "hashedAdvKey": kp.hashed_adv_key_b64,
        "printedMac": _mac_from_adv(adv),
        "_adv_bytes": adv,
    }


def generate(name: str) -> dict:
    """Create a brand new tag key pair."""
    return _pack(KeyPair.new(), name)


def from_private_b64(private_key_b64: str, name: str = "Imported tag") -> dict:
    """Rebuild a tag from its base64 private key."""
    try:
        raw = base64.b64decode(private_key_b64, validate=True)
    except Exception as exc:
        raise ValueError(f"private key is not valid base64: {exc}") from exc
    if len(raw) != 28:
        raise ValueError(f"private key must decode to 28 bytes, got {len(raw)}")
    return _pack(KeyPair(raw), name)


def keypair_for(private_key_b64: str) -> KeyPair:
    """A findmy KeyPair for report fetching/decryption."""
    return KeyPair(base64.b64decode(private_key_b64))


# --------------------------------------------------------------------------- #
# Import / export of key files
# --------------------------------------------------------------------------- #
def parse_keyfile(raw: str | bytes) -> list[dict]:
    """Accept the common DIY Find My key-file shapes and normalise them.

    Handles a bare object, an array of objects, and a ``{"tags": [...]}`` wrapper,
    tolerating a UTF-8 BOM and a stray trailing ``[]``.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig", errors="replace")
    raw = raw.lstrip("﻿").strip()
    if not raw:
        raise ValueError("key file is empty")
    try:
        data, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"key file is not valid JSON: {exc}") from exc

    if isinstance(data, dict):
        data = data.get("tags") or data.get("devices") or [data]
    if not isinstance(data, list):
        raise ValueError("key file must contain an object or an array of tags")

    out: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        priv = item.get("privateKey") or item.get("private_key")
        if not priv:
            raise ValueError(f"tag #{i + 1} has no privateKey — cannot locate it without one")
        tag = from_private_b64(priv, item.get("name") or f"Tag {i + 1}")
        # Cross-check any supplied public key against the one we derived.
        supplied = item.get("advertisementKey") or item.get("adv_key")
        if supplied and supplied != tag["advertisementKey"]:
            raise ValueError(
                f"tag '{tag['name']}': the advertisementKey in the file does not match "
                f"its privateKey. The file is inconsistent — do not use it."
            )
        tag["icon"] = item.get("icon", "tag")
        out.append(tag)
    if not out:
        raise ValueError("no tags found in key file")
    return out


def export_keyfile(tags: list[dict]) -> str:
    """Serialise tags to the portable key-file format (includes private keys)."""
    payload = [
        {
            "name": t["name"],
            "id": t["hashedAdvKey"],
            "privateKey": t["privateKey"],
            "advertisementKey": t["advertisementKey"],
            "hashedAdvKey": t["hashedAdvKey"],
            "hashedPublicKey": t["hashedAdvKey"],
            "printedMac": t["printedMac"],
            "icon": t.get("icon", "tag"),
        }
        for t in tags
    ]
    return json.dumps(payload, indent=2) + "\n"
