# NetWatch Desktop

A single installer that turns a bare ESP32 into a Find My tag for **your own
belongings** — sign in, plug in a board, click once, see it on a map.

No Docker. No terminal. No Arduino toolchain. No compiling.

---

> ### Responsible use
> A NetWatch tag is **not** an AirTag. It will not appear in Find My › Items and
> it does **not** trigger Apple's unwanted-tracking alerts, so someone carrying
> one is unlikely to be warned. Putting a tag on another person, their vehicle or
> their property is illegal in many jurisdictions. The app requires an explicit
> ownership attestation on first run, and that gate should stay in any build you
> ship.

---

## What the customer does

1. Run `NetWatch-Setup-2.0.0.exe` (per-user, no admin prompt).
2. Accept the ownership notice.
3. **Login** → Apple ID → two-factor code.
4. **Flash a board** → plug in an ESP32 → *Connect* → *Generate key & flash* (~10 s).
5. **Sync** → tags appear on the map.

That's it. Steps that used to require Docker Desktop, WSL2, arduino-cli, a 500 MB
toolchain download, a 15-minute compile and hand-editing `devices.json` are gone.

## Why there is no Docker

The usual OpenHaystack / Macless-Haystack stack runs Docker for exactly one
reason: to host an **anisette server**, which produces the device-attestation
headers Apple demands at login. Nothing in that container fetches locations.

The real pipeline is:

1. The ESP32 advertises the tag's **public** key over BLE.
2. Any passing iPhone encrypts its own GPS fix with that key and uploads it to
   Apple — Apple's phones do the work, not us.
3. NetWatch authenticates to Apple and requests reports matching the SHA-256 of
   its advertisement keys. ← *anisette headers needed only here*
4. NetWatch decrypts them locally with the tag's **private** key.

The `anisette` package does step 3's header generation in-process, provisioning a
virtual device in well under a second. So the container disappears and the
customer installs one file.

## Architecture

```
NetWatch.exe  (PyInstaller, ~60 MB)
├── native window            pywebview → WebView2 (Windows 11 ships it)
├── UI                       app/netwatch/web — sidebar + Leaflet map + modals
└── helper (in-process)      loopback HTTP API on 127.0.0.1, random port
    ├── appleclient.py       anisette + Apple auth + report fetch/decrypt
    ├── flasher.py           esptool chip detect + write
    ├── keyinject.py         patch the key into prebuilt firmware
    ├── store.py             SQLite, private keys sealed at rest
    └── exports.py           JSON / CSV / GPX / KML / GeoJSON / CoT
```

The API binds loopback only and requires a per-launch token, so other local
processes cannot drive the app or read tag keys.

### The 10-second flash

Firmware is compiled **once, by you**, per release. Each image embeds:

```c
magic[16] = "NETWATCH-KEYBLK\0"
key[28]   = 0x00 ...            // rewritten at flash time
```

At flash time the app overwrites those 28 bytes in the `.bin`, then **repairs the
ESP32 image's trailing XOR checksum and appended SHA-256** — skip that and the ROM
bootloader rejects the image and the board boot-loops. `app/tests/test_keyinject.py`
covers this, including tamper detection and re-flashing a board twice.

## Build

```powershell
.\packaging\build.ps1
```

1. `firmware/build_prebuilt.ps1` — compiles per chip, asserts the key marker
   survives optimisation exactly once. Builds must be **serial**: arduino-cli
   shares one sketch cache and concurrent compiles corrupt it.
2. tests, including the prebuilt release gate.
3. PyInstaller → `dist\NetWatch\NetWatch.exe`, then `disable_cfg.py`, then
   `NetWatch.exe --selftest` (the build **fails** if the Apple helper can't run).
4. Inno Setup → `dist\NetWatch-Setup-2.0.0.exe`

### Toolchain versions that actually matter

| Component | Pin | Why |
|---|---|---|
| esp32 core | 3.x | current |
| NimBLE-Arduino | **2.x** (2.5.1) | 1.4.x has no `setOwnAddr` and `addData()` takes `char*` — will not compile |
| flash mode | **`dio`** everywhere | `qio` boot-loops on real C3 boards |
| partitions | `huge_app` | the NimBLE build overflows the default 1.25 MB app partition |

### Why the build clears Control Flow Guard

`anisette` runs Apple's provisioning code inside the Unicorn CPU emulator — a
JIT. PyInstaller's bootloader enables CFG, which rejects indirect calls into
JIT'd code, so the packaged app `__fastfail`s (`0xC0000409`) at the first
emulated instruction **with no Python traceback**. `packaging/disable_cfg.py`
clears that one PE bit; DEP and ASLR stay on. `--selftest` proves it worked.

Run from source instead:

```powershell
pip install -r app\requirements.txt
cd app
python -m netwatch                 # native window
python -m netwatch --browser       # in your browser
$env:NETWATCH_DEMO=1; python -m netwatch   # synthetic data, no Apple calls
```

## Two editions

One codebase, two separate programs. The edition is **baked into the binary** at
build time, so it cannot be switched with an environment variable.

| | `retail` — **the product** | `personal` — yours only |
|---|---|---|
| Executable | `NetWatch.exe` | `NetWatchPersonal.exe` |
| Basemaps | 8 (community + keyed providers) | 12 (adds 4 Google layers) |
| Google tiles | **none** | `mt0-3.google.com/vt` |
| Safe to sell | yes, with a provider key | **no — breaches Google's terms** |

```powershell
.\packaging\build_editions.ps1                  # both
.\packaging\build_editions.ps1 -Only retail     # just the product
.\packaging\build_editions.ps1 -Only personal   # just your own build
```

Both share one data directory (`%LOCALAPPDATA%\NetWatch`), so tags and the Apple
session carry across. That is deliberate: forking storage would strand a tag's
private key and make the board it is flashed onto permanently unlocatable.

## Basemaps

Nothing needs an API key or an account. The map-layer button (bottom-left)
lists every source with its licensing status.

| Layer | Source | Status |
|---|---|---|
| Street, Dark, Light, Topographic | OSM / CARTO / OpenTopoMap | `open` — works out of the box |
| Custom | your own tile server | `self-hosted` — no third-party limit |
| Google Roads / Satellite / Hybrid / Terrain | `mt0-3.google.com/vt` | `unlicensed` — **personal edition only** |

**Map settings…** in that menu takes a self-hosted URL template if you want to
serve your own tiles.

One caveat for selling this: the open sources above are run by volunteers and
their usage policies are written for modest traffic, not a commercial product at
scale. If NetWatch ever gets busy, self-host (Protomaps or your own tile server)
or add a paid provider — `tileproviders.py` documents exactly where a `{key}`
URL would slot in.

## Supported boards

| Chip | Flashed via | Status |
|---|---|---|
| ESP32 (WROOM / DevKit) | esptool | supported |
| ESP32-C3 / C6 / S3 | esptool | supported |
| Seeed XIAO nRF52840 / Sense | serial DFU | supported |
| ESP32-S2, ESP8266 | — | **rejected at detection — no BLE radio** |

### nRF52840 is flashed over DFU, not UF2

Double-tap RESET and the XIAO appears as both a drive and a COM port. NetWatch
rewrites the 28-byte key inside a prebuilt `.hex`, wraps it in a DFU package and
sends it to the **COM port** — it resolves that automatically if you pick the
drive. Takes a few seconds, no toolchain, no compiling.

Two approaches were tried first and discarded, documented here so nobody repeats
them:

1. **Copying a `.uf2` to the bootloader drive.** Writes flash correctly — the
   bootloader's own `CURRENT.UF2` reads the exact key back — but the image never
   executes a single instruction. A diagnostic build blinking the red LED from
   `loop()` produced no blinking at all, proving the code never ran.
2. **Compiling per tag with arduino-cli.** Works, and it is what the original
   NetWatch-AnyBLE flasher did, but it drags ~400 MB of toolchain onto the
   customer's machine for a board a 314 KB prebuilt can serve.

The shipped path is prebuilt `.hex` + key injection + `adafruit-nrfutil` DFU,
driven through its Python API so it keeps working inside the frozen app.
`app/netwatch/dfu.py` carries the reasoning.

### How the UF2 code works (kept for reference)

It has no esptool protocol. Double-tap RESET and the board mounts as a USB
volume; copying a `.uf2` onto it writes flash. NetWatch handles this
automatically — the board list shows UF2 drives alongside serial ports, and
"Connect" reads `INFO_UF2.TXT` to identify it.

Three things that are easy to get wrong, all handled in `app/netwatch/uf2.py`:

1. **Family ID.** A UF2 bootloader silently discards every block whose family
   does not match its own. The copy appears to succeed, nothing is written, and
   the board never reboots. Seeed's XIAO uses `0x28860045` (their USB VID
   `0x2886` << 16 | PID `0x0045`), **not** the generic Adafruit `0xADA52840`.
   NetWatch reads the expected family from the drive's own `CURRENT.UF2` rather
   than trusting a constant, so it also works on other UF2 boards.
2. **Block boundaries.** UF2 carries 256 bytes of flash per 512-byte block, so
   the 44-byte key block can straddle two blocks. The injector maps flash
   addresses to file offsets instead of assuming contiguity.
3. **One image per session.** This bootloader programs a single UF2 and then
   waits for a reset; a second flash without resetting lands in the FAT
   unapplied. NetWatch reports "tap RESET" rather than failing.

Build its firmware with `firmware/build_nrf52840.ps1` (the Seeed core emits
Intel HEX, which the script converts to UF2 with the app's own converter so
build and flash can never disagree).

## ATAK / WinTAK

**ATAK bridge** in the sidebar puts these tags on a TAK map, through the
companion plugin **NetWatch-TAK**.

It is a second listener, separate from the loopback API, because that one binds
127.0.0.1 on a random port with a token that changes every launch — correct for a
UI talking to itself, useless to a tablet. The bridge instead takes a fixed port
(8787) and a long-lived pair code the operator reads off the screen once. It is
**off until switched on**: a tracker app has no business opening a port on
someone's network just in case.

| | |
|---|---|
| Serves | tag names, colours, positions, history, live CoT |
| Never serves | `advertisementKey`, `hashedAdvKey`, `privateKey` — **not any key** |
| Auth | pair code in `X-NetWatch-Pair`, rotatable |
| Write access | one endpoint, `POST /api/poll`, rate-limited to once a minute |

A tag's hashed advertisement key is the handle Apple's servers accept, so anyone
who copies it can query that tag's location themselves, forever, with no way to
revoke it short of reflashing the board. Tags go over the wire as a salted digest
instead: stable, so a marker moves rather than multiplying, and useless anywhere
else. `app/tests/test_bridge.py` walks every response looking for the real keys.

### Finding the PC

Two ways, because neither covers every network:

* **Discovery** — the plugin broadcasts a UDP probe on 8788 and NetWatch answers
  with its hostname and port, never the pair code. Works on a plain LAN.
* **Pairing link** — `netwatch://pair?h=…&p=…&c=…`, one string carrying host, port
  and code. Needed over a VPN, where broadcast does not exist.

Over Tailscale, WireGuard or IPsec the PC has several addresses and only one is
reachable from a given tablet, so the panel lists each with its own link and
labels which is which. The VPN address is found by asking the OS which source
address it would use to reach `100.64.0.0/10` — the routing table's own answer
rather than a guess.

Plain HTTP, guarded by the code. Fine on a LAN or a VPN; **do not port-forward
it**, because anyone who could read the traffic would have both the code and the
tag locations.

## Tests

```powershell
python app\tests\test_keyinject.py   # 20 checks — image patching + integrity
python app\tests\test_prebuilt.py    # release gate for shipped firmware
python app\tests\test_bridge.py      # 89 checks — ATAK bridge, incl. key secrecy
```

## Where data lives

`%LOCALAPPDATA%\NetWatch\` — SQLite database, log, cached anisette provisioning.

Tag private keys and the Apple session are encrypted with **Windows DPAPI** bound
to the user account (AES-GCM with a 0600 key file elsewhere).

**The saved Apple session includes the password.** Earlier versions of this file
claimed it did not; that was wrong. `findmy` puts the password in the session
blob it serialises, because Apple expires the iCloud token every few days and the
library silently re-runs the login to recover. Dropping it would not remove the
secret from anywhere — it would only break that renewal, so the customer retypes
their password and a 2FA code roughly twice a week.

What is true: the blob is sealed with DPAPI before it touches the disk, it is
never logged, and it goes nowhere except Apple. Signing out deletes it. Tell
customers to use a spare Apple ID if that trade is not one they want.

Uninstalling deliberately leaves this folder: deleting it destroys the private
keys for every tag, and any board still out there becomes permanently
unlocatable. Use **Export › JSON** for backups.

## Known limits

- First report after a flash typically takes **5–40 minutes** — that latency is
  Apple's crowd network, not the app.
- Tags do not rotate keys, so they are not AirTag-equivalent and are not covered
  by Apple's safety alerts.

## Licence

Dual-licensed: **GNU AGPL v3** ([LICENSE](../LICENSE)) for open use, or a
**commercial licence** ([details](../COMMERCIAL-LICENSE.md)) for selling it or
shipping it inside a product.

Copyright © 2026 junii55. See
[THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md) for the bundled dependencies
and their own terms.
