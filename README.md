# NetWatch
**Build your own Bluetooth trackers, locate them worldwide through Apple's Find
My network, and view them on Windows or on an ATAK tablet.**

No Mac. No iPhone. No Docker. No Arduino toolchain. No third-party service.
Everything runs on your own computer, and the keys never leave it.

```
  Your tag              Passing iPhones           NetWatch Desktop        NetWatch-TAK
  (ESP32 / XIAO)        (anyone's, not yours)     (your Windows PC)       (your tablet)
  --------------        ---------------------     -----------------       ------------
  broadcasts its        hear it, encrypt          requests the reports,   plots them on
  public key over  -->  their own GPS fix   -->   decrypts them with -->  the TAK map and
  Bluetooth LE          with that key, and        the tag's private       can share them
                        upload it to Apple        key                     with your team
```

A NetWatch tag is a beacon that broadcasts a public key. Any nearby iPhone
encrypts its own position with that key and uploads it to Apple on your behalf.
Only the holder of the matching private key can decrypt the result — and that key
stays on your PC. Apple's network of hundreds of millions of devices does the
searching; you keep the secrets.

---

> ### Responsible use
>
> A NetWatch tag is **not** an AirTag. It does not appear in Find My › Items and
> it does **not** trigger Apple's unwanted-tracking alerts, so a person carrying
> one is unlikely to be warned by their phone.
>
> **Use these on property you own.** Placing a tracker on another person, their
> vehicle, or their belongings is a criminal offence in many jurisdictions.
>
> NetWatch also authenticates to Apple using a non-Apple client, which is very
> likely contrary to the iCloud terms of service. Use a secondary Apple ID.
> Apple may lock the account or change the endpoints at any time.

---

## Contents

| Directory | Purpose |
|---|---|
| [`desktop/`](desktop/) | **NetWatch Desktop** — the Windows application: Apple sign-in, key management, board flashing, map, history, exports, and the ATAK bridge. |
| [`NetWatch-TAK/`](NetWatch-TAK/) | **ATAK plugin** — tags on the TAK map with breadcrumb trails and optional team sharing. |
| [`docs/SETUP.md`](docs/SETUP.md) | **Start here.** Complete step-by-step installation guide. |
| [`docs/BRIDGE-API.md`](docs/BRIDGE-API.md) | HTTP API reference, for building your own client. |
| [`docs/STATUS.md`](docs/STATUS.md) | Verification status and roadmap. |
| [`SECURITY.md`](SECURITY.md) | What is stored, where, and what is exposed. |

## Getting started

Full instructions, written for a first-time user, are in
**[docs/SETUP.md](docs/SETUP.md)**. In brief:

1. Install `NetWatch-Setup-<version>.exe` from
   [Releases](https://github.com/junii55/netwatch/releases). Per-user install;
   no administrator rights required.
2. Sign in with a secondary Apple ID.
3. Connect a supported board over USB and click **Flash a board** — about ten
   seconds, with no toolchain to install.
4. Allow 5–40 minutes for the first position to arrive, then click **Sync all**.
5. *Optional:* enable the **ATAK bridge** and pair a tablet running the plugin.

## Hardware

| Board | Flashing method | Notes |
|---|---|---|
| ESP32 (WROOM / DevKit) | esptool | Widely available, inexpensive |
| ESP32-C3 / C6 / S3 | esptool | |
| Seeed XIAO nRF52840 / Sense | Serial DFU | Very small; months of runtime on a coin cell |
| ESP32-S2, ESP8266 | — | **Rejected at detection — no Bluetooth LE radio** |

Firmware is compiled once per release rather than once per tag. Flashing rewrites
a 28-byte key inside a prebuilt image and repairs the image's trailing XOR
checksum and appended SHA-256 — without that repair the ROM bootloader rejects
the image and the board fails to start. This is why flashing completes in seconds
and requires no development environment.

## ATAK integration

The desktop application's own API binds to `127.0.0.1` on an OS-assigned port and
authenticates with a token regenerated on every launch. That is appropriate for a
user interface talking to its own backend and unusable from a tablet.

ATAK therefore connects through a separate, purpose-built interface: a
**read-only bridge** on a fixed port, authenticated with a long-lived pairing
code, and **disabled until explicitly enabled**.

Tag names, colours, positions and history cross that interface. **No key material
of any kind does.** Tags are identified on the wire by a salted digest, because a
tag's hashed advertisement key is the handle Apple's servers accept — anyone
obtaining it could query that tag's location indefinitely, with no way to revoke
access short of reflashing the board. `desktop/app/tests/test_bridge.py` inspects
every response for key material and fails if any appears.

The bridge operates over a LAN or a VPN. Automatic discovery works on a LAN only,
since VPNs carry no broadcast traffic; for VPN use the application provides a
pairing link for each address it can be reached on, labelled by interface.

## Portability

NetWatch contains no hardcoded addresses, hostnames or server configuration. Each
installation generates its own tag keys, pairing code and identifiers. The ATAK
plugin never opens a connection to a TAK server — it passes events to ATAK, which
routes them over whatever server or mesh is already configured. Both components
work unmodified on another operator's infrastructure.

## Known limitations

- **The first position typically takes 5–40 minutes**, and a tag in a quiet
  location may go hours between reports. This is a crowd-sourced network, not
  GPS, which is why every position is shown with its age.
- **A position records where an iPhone last heard the tag**, not where the tag is
  now.
- **Tags do not rotate keys.** They are not equivalent to an AirTag and are not
  covered by Apple's anti-stalking protections.
- **The stored Apple session includes the account password**, which the
  underlying library requires in order to renew the session Apple expires every
  few days. It is sealed with Windows DPAPI and transmitted only to Apple. See
  [SECURITY.md](SECURITY.md).
- **Release binaries are unsigned**, so SmartScreen will warn and Smart App
  Control may block them.
- **The desktop application targets Windows.** The storage layer includes a
  portable AES-GCM fallback, but packaging and flashing are not tested on other
  platforms.

## Acknowledgements

NetWatch implements the publicly documented Apple Find My advertisement format
described by the [OpenHaystack](https://github.com/seemoo-lab/openhaystack)
research project, and builds on the [`findmy`](https://pypi.org/project/findmy/)
and [`anisette`](https://pypi.org/project/anisette/) libraries for Apple
authentication and report retrieval. The ATAK plugin follows the structure of the
[ATAK-CIV plugin template](https://github.com/TAK-Product-Center/atak-civ).

## Licence

NetWatch is **dual-licensed**.

| | |
|---|---|
| **GNU AGPL v3** ([LICENSE](LICENSE)) | Free of charge. Use it, modify it, share it. If you distribute it or run a modified version as a service, you must publish your source under the same licence. |
| **Commercial licence** ([details](COMMERCIAL-LICENSE.md)) | For selling NetWatch, shipping it inside a product or device, or distributing a modified version without releasing your source. |

In short: **personal and open use is free and always will be. Building a
business on it requires a licence.** Enquiries via
[issues](https://github.com/junii55/netwatch/issues).

Copyright © 2026 junii55. Provided without warranty of any kind, including any
warranty that Apple will continue to permit this method of access.

NetWatch bundles third-party software, some of it under licences with their own
obligations. Read [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) before
distributing anything.

