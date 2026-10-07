"""Release gate: validate the prebuilt firmware images before shipping.

Run after firmware/build_prebuilt.ps1. Catches the failure modes that would
brick a customer's board:
  * the key marker optimised away, or duplicated
  * a manifest that points at a missing file or the wrong part
  * an image whose checksum/SHA-256 we cannot repair
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from netwatch.keyinject import (  # noqa: E402
    KEY_LEN, KEY_MAGIC, inject_key, is_esp_image, is_uf2, read_key, verify_image,
)
from netwatch import uf2 as uf2mod  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PREBUILT = ROOT / "firmware" / "prebuilt"

ok = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    print(("PASS" if cond else "FAIL"), label, extra)


def main() -> int:
    # A leading underscore marks a build that is deliberately not shipped
    # (e.g. _nrf52840.unverified). Skip those rather than failing the gate.
    chips = sorted(d for d in PREBUILT.iterdir()
                   if (d / "manifest.json").exists() and not d.name.startswith("_")) \
        if PREBUILT.exists() else []
    if not chips:
        # This is a release gate: a build with no flashable firmware must not ship.
        print(f"No prebuilt firmware found under {PREBUILT}")
        print("Run firmware/build_prebuilt.ps1 first, then re-run this gate.")
        return 1

    for chip_dir in chips:
        chip = chip_dir.name
        print(f"\n--- {chip} ---")
        man = json.loads((chip_dir / "manifest.json").read_text(encoding="utf-8-sig"))
        check(f"{chip}: manifest names the chip", man.get("chip") == chip, man.get("chip", ""))

        parts = man.get("parts", [])
        check(f"{chip}: has parts", bool(parts))
        injectable = [p for p in parts if p.get("inject_key")]
        check(f"{chip}: exactly one injectable part", len(injectable) == 1)

        method = man.get("method", "esptool")
        offsets = set()
        for p in parts:
            f = chip_dir / p["path"]
            check(f"{chip}: {p['path']} exists", f.exists())
            if method in ("uf2", "dfu"):
                # These carry their own target addresses; no flash offset applies.
                check(f"{chip}: {p['path']} offset marked {method}",
                      str(p["offset"]) == method)
            else:
                check(f"{chip}: {p['path']} offset is hex", str(p["offset"]).startswith("0x"))
            offsets.add(str(p["offset"]))
        check(f"{chip}: offsets are distinct", len(offsets) == len(parts))

        if not injectable:
            continue
        app = chip_dir / injectable[0]["path"]
        if not app.exists():
            continue
        data = app.read_bytes()

        if method == "dfu":
            text = app.read_text()
            blob = b"".join(d for _, d in uf2mod.parse_hex(text))
            n = blob.count(KEY_MAGIC)
            check(f"{chip}: marker appears exactly once", n == 1, f"count={n}")
            pos = blob.find(KEY_MAGIC)
            check(f"{chip}: ships with a blank key",
                  blob[pos + len(KEY_MAGIC):pos + len(KEY_MAGIC) + KEY_LEN] == b"\x00" * KEY_LEN)
            import secrets as _s
            k = _s.token_bytes(KEY_LEN)
            patched = uf2mod.patch_hex(text, KEY_MAGIC, k)
            pblob = b"".join(d for _, d in uf2mod.parse_hex(patched))
            ppos = pblob.find(KEY_MAGIC)
            check(f"{chip}: injection round-trips",
                  pblob[ppos + len(KEY_MAGIC):ppos + len(KEY_MAGIC) + KEY_LEN] == k)
            check(f"{chip}: size unchanged", len(pblob) == len(blob))
            continue
        if method == "uf2":
            check(f"{chip}: image is a UF2", is_uf2(data))
            blocks = uf2mod.parse_uf2(data)
            _base, flat = uf2mod.flatten(data, blocks)
            n = flat.count(KEY_MAGIC)
            check(f"{chip}: marker appears exactly once", n == 1, f"count={n}")
            fam = uf2mod.family_of(data)
            check(f"{chip}: declares a family id", fam is not None,
                  f"0x{fam:08X}" if fam else "none")
        else:
            check(f"{chip}: app is an ESP image", is_esp_image(data))
            check(f"{chip}: marker appears exactly once", data.count(KEY_MAGIC) == 1,
                  f"count={data.count(KEY_MAGIC)}")
        check(f"{chip}: shipped image verifies", verify_image(data))
        check(f"{chip}: ships with a blank key", read_key(data) == b"\x00" * KEY_LEN)

        key = secrets.token_bytes(KEY_LEN)
        patched = inject_key(data, key)
        check(f"{chip}: injection round-trips", read_key(patched) == key)
        check(f"{chip}: patched image verifies", verify_image(patched))
        check(f"{chip}: size unchanged", len(patched) == len(data))

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
