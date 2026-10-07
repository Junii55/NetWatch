# NetWatch setup guide

A complete walkthrough, from an empty desk to a tag on a map, and then onto an
ATAK tablet. No prior experience with electronics, Python or ATAK is assumed.

**Time required:** about 20 minutes of work, plus up to 40 minutes of waiting for
the first position to arrive.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Install NetWatch Desktop](#2-install-netwatch-desktop)
3. [Sign in to Apple](#3-sign-in-to-apple)
4. [Flash your first tag](#4-flash-your-first-tag)
5. [Wait for the first position](#5-wait-for-the-first-position)
6. [Day-to-day use](#6-day-to-day-use)
7. [ATAK: enable the bridge](#7-atak-enable-the-bridge)
8. [ATAK: obtain the plugin](#8-atak-obtain-the-plugin)
9. [ATAK: pair the tablet](#9-atak-pair-the-tablet)
10. [ATAK: using the plugin](#10-atak-using-the-plugin)
11. [Troubleshooting](#troubleshooting)
12. [Backups and uninstalling](#backups-and-uninstalling)

---

## 1. What you need

**Required**

- A **Windows 10 or 11 PC**. It does not need to be powerful, but it must be
  switched on when you want to check a tag's position.
- A **secondary Apple ID**. Create a free one at
  [appleid.apple.com](https://appleid.apple.com) if you do not already have a
  spare. Read the responsible-use notice in the [main README](../README.md)
  before proceeding.
- **One supported board** and a USB cable that carries data (not a charge-only
  cable — a surprisingly common cause of a board not being detected):

  | Board | Approximate cost | Comments |
  |---|---|---|
  | ESP32 DevKit / WROOM | low | Easiest to obtain; larger; needs USB power or a battery |
  | ESP32-C3 / C6 / S3 | low | Smaller, modern, lower power draw |
  | **Seeed XIAO nRF52840** | moderate | **Recommended.** Thumbnail-sized, months of runtime from a small cell |

**Optional, for the ATAK integration**

- An Android tablet or phone running **ATAK-CIV**.
- Both devices on the same network, or on the same VPN (Tailscale, WireGuard,
  IPsec — all work).

> **Note on power.** A tag only broadcasts while powered. For a bench test, USB
> is fine. For real use, attach a battery appropriate to the board.

---

## 2. Install NetWatch Desktop

1. Download `NetWatch-Setup-<version>.exe` from
   [Releases](https://github.com/junii55/netwatch/releases).
2. Run it. It installs to your user profile, so **no administrator prompt
   appears** and no system files are touched.
3. Launch **NetWatch** from the Start menu.

### If Windows blocks the installer

Release binaries are not code-signed, so Windows will object. This is expected.

- **"Windows protected your PC"** → click **More info** → **Run anyway**.
- **The installer closes instantly with no window.** This is Smart App Control,
  which blocks unsigned binaries outright and offers no override. Check with:

  ```powershell
  Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' VerifiedAndReputablePolicyState
  ```

  A value of `1` means it is enforcing. Your options are to disable it (note that
  it cannot be re-enabled without resetting Windows) or to build from source —
  see [`../desktop/README.md`](../desktop/README.md).

Windows 11 already includes the WebView2 runtime the application window requires.
On older systems the installer will tell you where to obtain it.

---

## 3. Sign in to Apple

Apple will only release location reports to an authenticated account.

1. Click **Login** in the left sidebar.
2. Enter your **secondary** Apple ID and password.
3. Apple sends a six-digit verification code. Choose a delivery method, enter the
   code.

The sidebar now shows your email address instead of "Login".

> **What is stored:** the resulting session is encrypted with your Windows
> account and never leaves the machine except to contact Apple. It does include
> your password, because Apple expires the session every few days and it has to
> be renewed automatically. [SECURITY.md](../SECURITY.md) explains this in full.
> This is why a secondary Apple ID is recommended.

**If sign-in fails** with a provisioning or "Apple helper" error, the usual cause
is no internet connection on first run — the first sign-in must register a
virtual device with Apple. Open **About › Apple Helper** to see its status.

---

## 4. Flash your first tag

"Flashing" writes the tag firmware, containing a freshly generated key, onto the
board. It takes about ten seconds.

1. Connect the board to the PC with a USB **data** cable.
2. Click **Flash a board**.
3. Click **Connect**. NetWatch identifies the chip automatically.
   - An unsupported board (ESP32-S2, ESP8266) is rejected at this point. Those
     chips have no Bluetooth LE radio and cannot work.
   - If nothing is listed, see [Troubleshooting](#troubleshooting).
4. Give the tag a name — for example *Bike* or *Toolbox*.
5. Click **Generate key & flash**.

NetWatch generates a key pair, writes the public half into the firmware image,
repairs the image checksums, and flashes the board. When it finishes, the tag is
already broadcasting.

### Seeed XIAO nRF52840 — one extra step

This board must be put into its bootloader first:

1. **Double-tap the RESET button** — two quick presses, firmly. The timing window
   is short; if nothing happens, try again slightly faster.
2. The board appears as **both** a removable drive and a COM port. Select either
   one; NetWatch resolves the other automatically.
3. Continue from step 4 above.

### Adding more tags

Repeat for each board. Every tag receives its own independent key pair. Tags are
listed in the sidebar, each with its own colour.

---

## 5. Wait for the first position

**This is the step that surprises people, and nothing is wrong.**

Your tag is now broadcasting a public key over Bluetooth. Nothing appears on the
map until **somebody else's iPhone passes nearby**, hears the broadcast, encrypts
its own GPS position with your key, and uploads that to Apple.

- In an area with foot traffic, expect **5 to 40 minutes**.
- In an empty garage or a rural building, it may take **hours**, or not report at
  all until the tag is moved.
- To speed up a first test, place the board near a window or carry it outside.

When you are ready, click **Sync all**. NetWatch asks Apple for reports matching
your tags and decrypts them locally.

Still nothing? Confirm the tag is powered, check **About › Apple Helper** is
healthy, and allow more time. There is no way to make iPhones walk past.

---

## 6. Day-to-day use

| Action | Result |
|---|---|
| **Click a tag** in the sidebar | Centres the map on it |
| **`···` button** on a tag | Opens details: rename, colour, breadcrumb trails, date range, export, delete |
| **Eye icon** on a tag | Hides or shows it on the map. Hidden tags keep collecting history |
| **Sync all** | Requests new reports from Apple |
| **Map style button** (bottom-left) | Switches basemap |

**Breadcrumb trails** show a tag's history as a line with a point per report.
Enable them per tag, and narrow them to a date range, in that tag's details
panel.

**Exports** are available in six formats, including GPX, KML and CoT.

> **Important:** the JSON export contains tag **private keys** so a tag can be
> moved to another machine. Treat that file exactly as you would a password.

---

## 7. ATAK: enable the bridge

Everything from here is optional and only needed for ATAK.

1. In NetWatch, click **ATAK bridge** in the sidebar.
2. Click **Switch on**.

The panel now shows:

- a **pairing code** — four groups of four characters, drawn from an alphabet
  with every confusable character removed (no `O`/`0`, `I`/`1`, `S`/`5`, `B`/`8`),
  so there is nothing to misread;
- every **network address** this PC can be reached on, each labelled by type and
  each with its own pairing link.

### Allow it through Windows Firewall

Windows will usually prompt the first time. **Tick both "Private" and "Public"
networks**, then allow.

> This matters more than it sounds. Windows classifies most VPN adapters as
> *public* networks. Allowing only private networks is the single most common
> reason a tablet on a VPN cannot reach a PC that otherwise works perfectly.

If you dismissed the prompt, add the rule manually: *Windows Security › Firewall
& network protection › Allow an app through firewall*.

---

## 8. ATAK: obtain the plugin

ATAK will only load plugins signed by the TAK Product Center, so the plugin must
be built by their pipeline. This is free and takes a few minutes.

1. **Create the submission archive.** From the repository root:

   ```powershell
   .\tools\make-plugin-zip.ps1
   ```

   This produces `NetWatch-TAK-<version>-source.zip` and validates it before you
   upload. (Do not simply right-click › *Send to* › *Compressed folder* — Windows
   writes path separators the build server cannot read.)

2. **Upload it** at [tak.gov/user_builds](https://tak.gov/user_builds) and accept
   the terms.

3. **Download the result**, named
   `ATAK-Plugin-NetWatch-TAK-<version>-<atak>-civ-release.apk`.

4. **Install it on the tablet** and load it from ATAK's plugin manager. ATAK will
   note that it was signed by the third-party service rather than built by the
   TAK Product Center — that is normal and expected.

> **Version matching.** The plugin targets a specific ATAK release, set by
> `ATAK_VERSION` in `NetWatch-TAK/app/build.gradle`, and ATAK enforces it. If
> your ATAK is a different version, change that one line and resubmit.

---

## 9. ATAK: pair the tablet

Open **NetWatch-TAK** from the ATAK toolbar. Choose the method that matches how
the tablet reaches the PC.

### Option A — same Wi-Fi or LAN

1. Tap **Find on my network**.
2. Select your computer from the list.
3. Enter the pairing code shown in NetWatch.

### Option B — over a VPN (Tailscale, WireGuard, IPsec)

1. In NetWatch, find the address labelled for your VPN and click **Copy this
   link**.
2. Get that link to the tablet — a message to yourself, a note, anything.
3. In the plugin, tap **Paste pairing link**, paste it, then **Use this link**.
4. Tap **Connect**.

> **"Find on my network" will not work over a VPN**, and that is expected
> behaviour rather than a fault. Discovery is a broadcast; VPNs are
> point-to-point links that carry no broadcast traffic, so the request has
> nowhere to travel. Use the pairing link.

### Option C — enter the details manually

Tap **Enter the address myself** and supply the host, port and pairing code from
the NetWatch panel.

### Choosing the right address

A PC connected to both a LAN and a VPN has several addresses, and only one of
them is reachable from any given tablet. Match the link to how the tablet
connects — a tablet on the VPN needs the VPN address; the LAN address will simply
time out for it, and vice versa.

NetWatch labels each one so the choice is obvious.

---

## 10. ATAK: using the plugin

Once paired, your tags appear in the list and on the map, in the same colours as
the desktop application.

| Control | Function |
|---|---|
| **Go to** | Centres the map on that tag |
| **Refresh** | Re-reads what NetWatch already holds (fast, no Apple traffic) |
| **Sync now** | Asks NetWatch to fetch new reports from Apple (limited to once per minute) |
| **Centre all** | Fits every located tag on screen |
| **Options** | Marker icon, breadcrumb trails and date range, auto-refresh interval, sharing |
| **Expand / Shrink** | Switches between a full-screen and a half-screen panel |

### Sharing with your team

Under **Options › Sharing**:

- **Do not share** — the default. Tags appear on this tablet only.
- **This device only** — tags are injected into this ATAK as CoT events, so other
  plugins and tools can see them. Nothing leaves the device.
- **Share with my TAK team** — tags are sent over whichever TAK Server or mesh
  **this ATAK is already connected to**. Everyone on that network will see them.

The plugin holds no server address and no certificate of its own. It hands events
to ATAK and ATAK routes them, which is why it works on any operator's existing
infrastructure without configuration.

---

## Troubleshooting

### Flashing

| Symptom | Cause and remedy |
|---|---|
| No board listed when you click Connect | Charge-only USB cable, or a missing USB-serial driver. Try another cable first; then install the CP210x or CH340 driver for your board |
| XIAO nRF52840 not detected | It is not in bootloader mode. Double-tap RESET quickly and firmly, then retry |
| "No bundled firmware for this chip" | That chip is not supported, or you are running a build without prebuilt firmware |

### Positions

| Symptom | Cause and remedy |
|---|---|
| Tag listed but never located | No iPhone has passed it yet. Check the age column, move the tag somewhere busier, confirm it still has power |
| "Sign in with your Apple ID first" | The session was lost. Click Login |
| Sidebar shows **Enter Apple code** | Apple expired the session and wants a new verification code — not a full sign-in. Click it and enter the code |

### ATAK bridge

| Symptom | Cause and remedy |
|---|---|
| "Nothing is listening on `<host>:<port>`" | The bridge is switched off in NetWatch, or the PC is asleep |
| "No answer from `<host>`" | Firewall. Allow NetWatch on **both** private and public networks |
| "NetWatch refused the pairing code" | The code was changed on the PC. Pair again with the new one |
| Find on my network returns nothing | Normal over a VPN. Use the pairing link instead |
| ATAK will not load the plugin | The plugin's `ATAK_VERSION` does not match your ATAK release |

### Logs

The desktop log is at `%LOCALAPPDATA%\NetWatch\netwatch.log`, also viewable from
**About › Logs**. It records activity but never credentials or key material.

---

## Backups and uninstalling

All data lives in `%LOCALAPPDATA%\NetWatch\`:

| File | Contents |
|---|---|
| `netwatch.db` | Tags, encrypted private keys, location history, settings |
| `netwatch.log` | Activity log |
| `anisette-*.bin` | Cached device registration, so sign-in stays fast |

**Uninstalling deliberately leaves this folder in place.**

> ### There is no key recovery
>
> Deleting that folder destroys the private key of every tag. Any board already
> deployed becomes permanently unlocatable — it will keep broadcasting, and no
> position will ever be readable again. This is a deliberate property of the
> design: the keys exist in exactly one place, under your control.
>
> **Use *Export › JSON* to back up any tag you care about**, and store that file
> as securely as you would a password, because it contains the private key.
