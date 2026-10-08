# Third-party notices

NetWatch depends on software written by others. The desktop installer **bundles**
several of these, which means distributing the installer distributes them, and
their licence terms apply to you as the distributor.

This matters most if you are shipping NetWatch commercially. A commercial licence
for NetWatch's own code does not and cannot override the terms below.

**This list is a starting point for your own review, not a legal opinion.**
Verify current licences before distributing — they change between versions.

---

## Bundled in the desktop installer

| Component | Licence | What it does |
|---|---|---|
| **esptool** | **GPLv2 or later** | Flashes ESP32 boards |
| **adafruit-nrfutil** | **Nordic Semiconductor licence** | Serial DFU for the nRF52840 |
| findmy | see package metadata | Apple authentication and report retrieval |
| anisette | see package metadata | Device attestation headers |
| unicorn | BSD (Python bindings); the engine itself is GPLv2 — verify for your version | CPU emulation used during Apple provisioning |
| cryptography | Apache 2.0 / BSD dual | AES-GCM for the portable secret store |
| pyserial | BSD | Serial port enumeration |
| pywebview | BSD 3-Clause | Native application window |
| intelhex | BSD | Intel HEX parsing for nRF images |

### Two that need attention before commercial distribution

**esptool is GPLv2+.** Distributing a binary that includes GPLv2 code carries
source-availability obligations for the distributed work. This is the single most
likely obstacle to shipping NetWatch as a closed-source commercial product, and
it exists independently of how NetWatch itself is licensed. NetWatch's own choice
of AGPL is compatible with this; a proprietary redistribution may not be.

**adafruit-nrfutil** derives from Nordic Semiconductor's `nrfutil`, whose licence
has historically restricted use to Nordic hardware. NetWatch only ever uses it to
flash Nordic nRF52840 boards, which is its intended purpose, but confirm the
current terms if you intend to sell.

---

## Firmware

The tag firmware is built against:

| Component | Licence |
|---|---|
| Arduino ESP32 core | LGPL 2.1 |
| NimBLE-Arduino | Apache 2.0 |
| Adafruit nRF52 Arduino core | MIT / LGPL, varies by file |

Prebuilt images in `desktop/firmware/prebuilt/` are compiled from the sketches in
`desktop/firmware/`, and therefore contain code under those licences.

---

## Protocol and prior art

The Apple Find My advertisement format implemented here was publicly documented
by the [OpenHaystack](https://github.com/seemoo-lab/openhaystack) research
project (AGPL-3.0) at TU Darmstadt. NetWatch is an independent implementation and
contains no OpenHaystack code, but the protocol understanding originates there
and that work deserves the credit.

## ATAK plugin

The plugin is built against the ATAK-CIV SDK, supplied by the TAK Product Center
under its own terms, and follows the structure of the published plugin template.
It carries **no third-party dependencies of its own** — only the ATAK SDK and the
Android platform — which is why its dependency scans return nothing.

The signed APK is produced by the TAK third-party pipeline. Software submitted
there remains the submitter's property; the pipeline's terms are presented at
submission time.

---

## Checking for yourself

```powershell
cd desktop\app
pip install pip-licenses
pip-licenses --from=mixed --with-urls --format=markdown
```
