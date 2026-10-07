# NetWatch-TAK 1.0.2

### 1.0.2 — Shrink, and a mangled character

1. **Shrink put the pane in a narrow left-hand column on a phone held upright.**
   `showDropDown` is handed a landscape width/height pair *and* a portrait pair
   and chooses between them itself; `resize` is not — it takes one pair and
   applies it as given. Shrink passed the landscape shape (half width, full
   height) unconditionally, which is right in landscape and wrong in portrait.
   Expand was unaffected because full width by full height is the same either
   way. It now asks `isPortrait()` and picks the matching shape.

2. **The status dot rendered as `a-` in the title bar.** The glyph was a
   non-ASCII character in a layout attribute, and it got double-encoded by a
   tool that read the UTF-8 file as ANSI. It is now set from Java, where the
   compiler resolves the escape and no file read can mangle it. Both layouts
   were rewritten without a byte-order mark.

---

# NetWatch-TAK 1.0.1

### 1.0.1 — two plugin-context bugs found in the field

Both come from the same trap: an ATAK plugin runs inside the ATAK process but
carries its *own* package context, and the two are not interchangeable.

1. **Opening any dropdown crashed ATAK.** A plain `Spinner` inflated with the
   plugin context opens its popup against that context, which is not a UI
   context, and Android refuses. The SDK ships
   `com.atakmap.android.gui.PluginSpinner` for exactly this — it swaps the
   Activity context in for the duration of the click and back out after. All
   four dropdowns now use it, and layouts are inflated with
   `PluginLayoutInflater`, the SDK's "preferred mechanism", which also avoids a
   view cache keyed by class name across two class loaders.

2. **The paired PC was forgotten on every ATAK restart.** `ServerBook` took its
   `SharedPreferences` from the plugin context, which points at the plugin APK's
   own package — the ATAK process cannot write there, so `apply()` failed
   silently. Saved servers lived in memory for the session and vanished with it.
   Preferences now come from `mapView.getContext()`, which is what every SDK
   sample does.

---

# NetWatch-TAK 1.0.0

An ATAK plugin that puts your **NetWatch** Find My tags on the TAK map — current
position, breadcrumb trails, and optional sharing to your TAK team.

Targets ATAK **5.6.0** CIV. Built from the
[plugintemplate](https://github.com/TAK-Product-Center/atak-civ/tree/master/plugin-examples/plugintemplate).

---

## What it talks to

NetWatch Desktop on a Windows PC, over that app's **ATAK bridge**.

This is a different design from the previous HAYWATCH plugin, which spoke to a
Raspberry Pi running macless-haystack at a fixed address. NetWatch is a desktop
application: it holds the tag keys, authenticates to Apple, decrypts the location
reports, and keeps the history. The plugin is a viewer.

```
  iPhones nearby                NetWatch Desktop (Windows)        this plugin
  ───────────────               ──────────────────────────        ───────────
  hear the tag's BLE                Apple sign-in                  ATAK map
  advert, encrypt their    ──▶      report decryption      ──▶     markers
  own GPS, upload to                SQLite history                 trails
  Apple                             ATAK bridge ──────────────────▶ CoT
```

Nothing in this plugin contacts Apple, and it never sees a tag key.

## Nothing is hardcoded

Every address, port and credential is something the operator enters. There are no
baked-in IPs, no assumed VPN, and no TAK server configuration anywhere in this
plugin — so it works unchanged for anyone running their own NetWatch, their own
network and their own TAK infrastructure.

Three ways to connect, in order of convenience:

| | How | When to use it |
|---|---|---|
| **Find on my network** | One UDP broadcast on port 8788; every NetWatch with its bridge on answers | Tablet and PC on the same LAN or wifi |
| **Paste pairing link** | `netwatch://pair?h=…&p=…&c=…` — one string carries host, port and code | **Over a VPN**, or any routed network |
| **Enter the address** | Type host, port and pair code | Fixed address, or anything unusual |

> **Discovery cannot work over a VPN**, and that is not a bug to be fixed later.
> Tailscale, WireGuard and IPsec are point-to-point: they carry no broadcast
> domain, so a broadcast probe has nowhere to go. Over a VPN, paste the pairing
> link.

### Over a VPN

This is the normal case for a tablet in the field, and it works: the bridge
listens on every interface, including the VPN adapter, so a tablet on the tailnet
reaches the PC on its VPN address.

The catch is that the PC has more than one address and only one of them is any
use to a given tablet. So NetWatch lists them all, says which is which, and gives
each its own pairing link:

```
  [VPN (Tailscale)]  netwatch://pair?h=100.64.0.5&p=8787&c=…&n=WORKSHOP-PC
  [local network]    netwatch://pair?h=192.168.1.42&p=8787&c=…&n=WORKSHOP-PC
```

Copy the one that matches how the tablet connects. A tablet on the tailnet needs
the `100.x` link; the `10.x` link will simply time out for it, and vice versa.
NetWatch finds the VPN address by asking the OS which source address it would use
to reach `100.64.0.0/10`, so it is the address the routing table actually picks —
not a guess.

WireGuard and IPsec work the same way, but their addresses are chosen by whoever
configured them and usually sit in ordinary private space, so they are listed
without a VPN label. Pick the one on the same subnet as the tablet.

One firewall note: Windows often treats a VPN adapter as a **public** network, so
allowing NetWatch only on private networks is a common reason a VPN-connected
tablet cannot reach a PC that answers fine on the LAN.

Saved computers are kept in a list, so one tablet can switch between a desktop at
home and a laptop in a vehicle.

## What it does

**Tags** — a list with each tag's colour, age, coordinates, accuracy and report
count. Tags with no fix yet are listed too, saying so, rather than silently
missing.

**Map** — a marker per tag in NetWatch's own colour, inside a child map group
named *NetWatch*, so Overlay Manager gets one checkbox that hides them all. The
CoT type (and therefore the icon) is selectable. Markers are keyed by a stable
uid, so a tag moves rather than accumulating duplicates, and they are not
archived into ATAK's state saver — a stale marker surviving a restart would claim
a tag is somewhere it was hours ago.

**Breadcrumb trails** — drawn as polylines in the tag's colour, limited to a date
range you pick. By default each tag's own trail setting from the NetWatch desktop
app is honoured, so the two agree; you can override that.

**Sharing** — off, local, or out to your TAK team:

* *This device only* — the event enters this ATAK through the internal
  dispatcher, so other plugins and tools see the tag. Nothing leaves the tablet.
* *Share with my TAK team* — the external dispatcher puts it on whatever TAK
  Server or mesh **this ATAK is already connected to**. The plugin configures no
  server and holds no certificate; ATAK routes it over the connection you already
  set up, which is why this works on anyone's infrastructure.

**Refresh vs Sync now** — *Refresh* reads what NetWatch already has and is cheap.
*Sync now* makes NetWatch ask Apple for new reports; the bridge rate-limits it to
once a minute because Apple rate-limits it too.

## CoT dispatch is bound at runtime

`CotPush` reaches ATAK's dispatcher by reflection rather than linking against it.

That is deliberate. HAYWATCH 1.1.2 had CoT removed outright after a compile
failure, recorded at the time as `CommsMapComponent.getInternalDispatcher()` not
existing on the 5.6.0 CIV plugin API — and it does not: that method is on
`com.atakmap.android.cot.CotMapComponent`, a different class. Late binding means
one APK keeps working when something moves again, and the operator gets a
disabled switch with an explanation instead of a crash. The cost is no
compile-time check, so availability is probed once at startup and the UI reflects
it.

Markers never depend on CoT. If dispatch is unavailable, tags still appear.

## Security

| | |
|---|---|
| Transport | Plain HTTP on a LAN or VPN, guarded by the pair code |
| What crosses it | Tag names, colours, positions, history |
| What never crosses it | Tag keys of any kind — see below |
| Pair code storage | This app's private `SharedPreferences`, in clear |

A NetWatch tag's *hashed advertisement key* is the handle Apple's servers accept.
Anyone holding it can query that tag's location themselves, forever, with no way
to revoke it short of reflashing the board. So the bridge never sends it: tags are
identified by a salted digest that is stable for markers and useless anywhere
else. The desktop side has a test that walks every response looking for the real
keys.

A pair code is a weaker thing — read access to positions, revocable instantly by
rotating it in NetWatch. It is stored the way ATAK stores its own server
credentials. Treat it as a password anyway.

**Do not port-forward the bridge.** On a LAN or a VPN the pair code is a
reasonable gate. Exposed to the internet, anyone who could read the traffic would
have both the code and your tag locations. Use a VPN if you need it from away.

## Responsible use

A NetWatch tag is not an AirTag: it does not appear in Find My › Items and does
**not** trigger Apple's unwanted-tracking alerts, so someone carrying one is
unlikely to be warned. Use these on property you own, and check the law where you
live.

Positions are where a passing iPhone last heard the tag — not live GPS. First fix
after flashing commonly takes 5–40 minutes, and a tag somewhere quiet may go
hours between reports. The age column is there because it matters.

## Build

The signed APK comes from the TAK third-party pipeline; see `TPC-UPLOAD.txt`.

To build locally you need the ATAK SDK. With `artifacts.tak.gov` credentials in
`local.properties`:

```
./gradlew -Ptakrepo.force=true \
          -Ptakrepo.url=https://artifacts.tak.gov/artifactory/maven \
          -Ptakrepo.user=<user> -Ptakrepo.password=<pass> \
          assembleCivRelease
```

Without credentials, put `atak-gradle-takdev.jar` two directories above the
project root, or set `takdev.plugin` in `local.properties`.

### Configuration, and why

| | Value | Why |
|---|---|---|
| Gradle | 8.14.3 | What the pipeline runs and what the current plugintemplate ships |
| AGP | 8.13.0 | Same. Note AGP 8.x cannot run on the Gradle 6.9.1 named in some older pipeline docs |
| `atak-gradle-takdev` | `3.+` | What the 5.6.0 template uses. The published requirement text says `2.+`, which predates the 3.x line; if a submission is ever rejected over it, change the one line in `app/build.gradle` |
| NDK | **not declared** | There is no native code here. The pipeline refuses to install an NDK at build time, so declaring one is a build failure waiting to happen for no benefit |
| `compileSdk` / `target` | 34 | Template default |
| Java | 17 | Template default |

### Layout

```
app/src/main/java/com/atakmap/android/netwatch/
├── plugin/        NetWatchLifecycle, NetWatchTool   entry points
├── NetWatchMapComponent.java                        registers the pane
├── NetWatchDropDownReceiver.java                    the pane and all dialogs
├── net/
│   ├── Server.java        one computer: host, port, pair code, link parsing
│   ├── ServerBook.java    saved computers and view options
│   ├── Discovery.java     UDP broadcast probe
│   └── BridgeClient.java  the five GETs and one POST
├── map/
│   ├── TagOverlay.java    markers, trails, the NetWatch map group
│   └── CotPush.java       runtime-bound CoT dispatch
└── model/Tag.java         one tag as the bridge describes it
```

## Bridge API

Read-only apart from `/api/poll`. Every call carries `X-NetWatch-Pair`, except
`/api/hello`, which exists so a client can say "found NetWatch, now enter the
code" instead of showing a bare 401.

| | |
|---|---|
| `GET /api/hello` | identity only — no counts, no names, no positions |
| `GET /api/status` | version, tag count, how many are located, last sync |
| `GET /api/fixes` | current position of every tag |
| `GET /api/history?id=&from=&to=&limit=` | one tag's breadcrumbs, oldest first |
| `GET /api/tracks?from=&to=&limit=` | every tag's breadcrumbs in one request |
| `GET /api/cot?stale=` | live CoT events, one per located tag |
| `POST /api/poll` | ask Apple for new reports; rate-limited to once a minute |

Requires NetWatch Desktop **2.4.0** or later.
