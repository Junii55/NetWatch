# Project status

A record of what has been verified on real hardware, what is known to be
incomplete, and the engineering decisions behind the less obvious parts of the
implementation.

The distinction maintained throughout this document is between *the tests pass*
and *this ran on a board*. Both are recorded, and they are not treated as
equivalent.

---

## Verified on hardware

| Area | Evidence |
|---|---|
| Find My payload | The firmware's derived MAC address and 31-byte advertisement match the `findmy` reference implementation byte for byte |
| Apple authentication without Docker | `Anisette.init()` and `provision()` complete in-process in approximately 0.7 s, returning 10 Apple headers |
| Key injection | 20/20 synthetic cases, plus verification against real 1.15 MB ESP-IDF images |
| Prebuilt firmware | 5/5 chips (ESP32, C3, C6, S3, nRF52840) pass the release gate |
| ESP32-C3 end to end | Flashed and advertising. Serial output confirms `set_rnd rc=0` and `Advertising Find My payload`, with the MAC matching the tag record |
| Seeed XIAO nRF52840 | Flashed from the packaged executable and confirmed advertising at −54 dBm carrying its own key, using the prebuilt `.hex`, key injection and serial DFU |
| Packaged application | Builds and runs. The full API suite passes 34/34 against the packaged executable, which then flashed a physical board |
| Apple helper inside the bundle | `--selftest` passes in the frozen application: the CPU emulator runs, provisioning succeeds, 10 headers returned |
| Backend API | 34/34 covering authentication, path traversal, tag CRUD, import validation, sync de-duplication and six export formats |
| ATAK bridge | 93/93, including an inspection of every response for advertisement, hashed and private keys. Exercised live over both a VPN address and a LAN address; incorrect pairing codes rejected |
| Apple session persistence | 11/11 (`test_session.py`): a saved session survives a restart, and a failed restore no longer discards it |
| ATAK plugin | Built and signed by the TAK third-party pipeline across three successive submissions, each returning **0 Fortify findings** and an empty dependency scan. Confirmed in the field: tags located on the TAK map with the tablet connected over a VPN |

## Known gaps

| Item | Status |
|---|---|
| Chips other than C3 and nRF52840 | The ESP32, C6 and S3 images pass the release gate but have not each been flashed to a physical board |
| Code signing | Not done. Installers are unsigned, so SmartScreen warns and Smart App Control may block them |
| Automatic updates | Not implemented. See the note under *Planned work* |
| Platforms other than Windows | The storage layer has a portable AES-GCM fallback, but packaging and flashing are untested elsewhere |
| Leaflet delivery | Loaded from a CDN. A CDN outage currently affects the map; vendoring it locally is outstanding |

---

## Engineering notes

The following were found during hardware bring-up. They are recorded because
each one is silent, and none would have been caught by code review alone.

### Flashing and firmware

1. **UF2 family ID.** The XIAO bootloader expects `0x28860045` (Seeed's USB
   VID:PID), not the generic Adafruit `0xADA52840`. With the wrong family the
   copy *appears to succeed*: the file lands on the drive, nothing is written,
   and the board never reboots. The expected family is now read from the
   bootloader's own `CURRENT.UF2` rather than assumed.

2. **One UF2 per bootloader session.** A second flash without an intervening
   reset lands in the FAT unapplied. Treating that as corruption would discard a
   perfectly good flash, so it is reported as "tap RESET" instead.

3. **NimBLE version.** Version 1.4.3 has no `NimBLEDevice::setOwnAddr` and its
   `addData()` takes `char*`. ESP32 core 3.x requires NimBLE 2.x; the build pins
   2.5.1.

4. **`esp_bt_sleep_enable()` does not exist on the C6.** It is a classic
   Bluetooth API that BLE-only parts do not declare. Now guarded by target.

5. **`qio` flash mode boot-loops.** A C3 board loaded the second-stage bootloader
   and then watchdog-reset indefinitely. The identical image in `dio` mode boots
   first time, so all targets ship `dio`.

### Packaging

6. **Control Flow Guard terminated the packaged application.** Apple
   authentication runs provisioning code inside a CPU emulator, which is a JIT.
   PyInstaller's bootloader enables CFG, which rejects indirect calls into
   generated code, so the process raised `0xC0000409` at the first emulated
   instruction with no Python traceback. `packaging/disable_cfg.py` clears that
   one PE bit; DEP and ASLR remain enabled, and the build refuses to ship if
   `--selftest` subsequently fails.

Also addressed: PyInstaller executed `netwatch/__main__.py` as top level, which
broke relative imports (resolved with `launch.py`); the emulator's native library
must be placed at `unicorn/lib/`; and the bundled Visual C++ runtime shadowed the
newer system copy and is now excluded.

### Flashing approaches evaluated and rejected

Two approaches to the nRF52840 were tried before settling on serial DFU:

- **Copying a `.uf2` to the bootloader drive** writes flash correctly — the
  bootloader's own `CURRENT.UF2` reads the key back exactly — but the resulting
  image never executes a single instruction. A diagnostic build blinking an LED
  from `loop()` produced no output at all.
- **Compiling per tag with arduino-cli** works, but requires roughly 400 MB of
  toolchain on the user's machine for a board a 314 KB prebuilt image serves.

The shipped approach is a prebuilt `.hex`, key injection, and `adafruit-nrfutil`
driven through its Python API so it continues to work inside the frozen
application.

---

## Apple session handling

Three defects in session persistence were identified and corrected. They are
documented because the symptom — being signed out on every restart — had two
plausible but incorrect explanations before the real cause was isolated.

### The root cause

**A different device identity was presented to Apple on every launch.**

`restore()` rebuilt the account with `AppleAccount(provider, state_info=saved)`.
That restores the login tokens but pairs them with the *fresh* provider supplied,
discarding the device provisioning saved alongside them. Apple binds a session to
the device identity that created it, so the original tokens presented by a new
device receive a 401. The library answers that by re-running the login, and Apple
answers *that* by requiring two-factor authentication again. The session was
never expired or corrupt.

`AppleAccount.from_json(state_info)` rebuilds the provider from the saved mapping
and the device identity survives the round trip. `app/tests/test_session.py`
asserts the difference directly and retains the previous call in the test so the
distinction cannot be lost in a later refactor.

### Contributing defects

1. **`restore()` did not check the restored account's state**, reporting a
   successful sign-in unconditionally. A session saved mid-verification, or one
   Apple had since invalidated, appeared healthy and failed later as an
   unexplained error during the first sync. `login()` and `submit_2fa()` shared
   the flaw: both accepted an `AUTHENTICATED` state, which indicates the password
   was accepted but the iCloud login did not complete and cannot retrieve
   anything.

2. **Refreshed tokens were never written back.** Apple expires the iCloud token
   every few days and the library renews it in memory during a fetch. Nothing
   persisted that renewal, so the stored session only aged and eventually
   required a fresh verification code.

3. **A failed restore deleted the stored session**, turning any transient failure
   into a permanent sign-out with nothing left to retry from. Only an explicit
   sign-out clears it now.

What this does not change: a session left unused long enough will still require a
new verification code, which is Apple's policy. The difference is that the
application now requests a code — the sidebar reads **Enter Apple code** — rather
than reporting an error with no path forward.

### Password storage

The `findmy` library's `AppleAccount.to_json()` includes the account password,
because that is what it re-authenticates with; persisting the session therefore
persists the password. Removing it before saving would not eliminate the secret,
only break silent renewal, requiring the password and a verification code roughly
twice a week.

The session blob is sealed with Windows DPAPI, never logged, and transmitted only
to Apple. Earlier revisions of the documentation stated that the password was
never written to disk; that was inaccurate and has been corrected throughout.
Using a secondary Apple ID is recommended for this reason.

---

## Planned work

1. **Code signing.** The most valuable next step. Unsigned installers trigger
   SmartScreen for every user, and because the executable has the CFG bit cleared
   some anti-malware engines examine it more closely — which makes signing more
   important here, not less.
2. **Signed automatic updates.** Any update channel must verify an Ed25519-signed
   manifest before applying. An unsigned one would amount to remote code
   execution on every installation, which is why none ships today.
3. **Vendor Leaflet locally**, removing the runtime CDN dependency.
4. **Hardware verification of the remaining chips** (ESP32, C6, S3).
5. Opt-in crash reporting; macOS and Linux packaging.

## Operational considerations

**Apple's terms of service.** Authenticating to Find My with a non-Apple client
is very likely contrary to the iCloud terms. The realistic outcomes are an
account lock, or Apple changing the endpoints so that every installation stops
working simultaneously. Plan for both.

**Map tiles.** The included sources are operated by volunteers whose usage
policies are written for modest traffic. For anything beyond personal use,
self-host (Protomaps or an equivalent) or add a commercial provider;
`tileproviders.py` documents where a keyed URL fits.

**Anti-stalking exposure.** These tags fall outside Apple's unwanted-tracking
alerts. The ownership warning should remain, concealment should never be
promoted, and abuse reports should be acted on.

**User expectations.** A first position taking 5–40 minutes is normal and should
be communicated clearly in advance; users expecting GPS behaviour will otherwise
report it as a fault.
