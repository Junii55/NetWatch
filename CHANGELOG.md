# Changelog

Two things ship from this repo on their own cadence: **NetWatch Desktop** and the
**NetWatch-TAK** ATAK plugin. The plugin needs Desktop 2.4.0 or later.

---

## NetWatch Desktop

### 2.4.1

**Fixed: the Apple sign-in did not survive a restart.**

The session was being handed back to Apple by a different device every launch.
`restore()` rebuilt the account with `AppleAccount(provider, state_info=saved)`,
which restores the login tokens but pairs them with the *fresh* anisette provider
it is given, discarding the provisioning saved alongside them. Apple binds a
session to the anisette device that created it, so old tokens presented by a new
device get a 401 — which the library answers by re-running the login, which Apple
answers with `REQUIRE_2FA`. Nothing was expired or corrupt.

`AppleAccount.from_json(state_info)` rebuilds the provider from the saved mapping
and the device survives. `app/tests/test_session.py` asserts the difference and
keeps the old call in the test so it can't be "simplified" back.

Also: **a failed restore no longer deletes the saved session.** It used to, so a
single bad launch wiped the only copy with nothing left to retry from. Only an
explicit logout clears it now.

### 2.4.0

- **ATAK bridge.** Read-only LAN/VPN listener so the ATAK plugin can show your
  tags. Off by default, fixed port, long-lived pair code, UDP discovery, and a
  pairing link per address with VPN addresses detected and labelled. No key
  material crosses it, enforced by test.
- **Show/hide tags.** Per-tag visibility; hidden tags stay listed and keep
  collecting history but leave the map, and are excluded from CoT so hiding also
  stops sharing them.
- **Clicking a tag goes to it on the map** instead of opening the settings
  dialog. Details moved behind a `...` button on each row.
- Honest restore/2FA states: the sidebar offers **Enter Apple code** rather than
  a full sign-in when that's all Apple wants.
- Corrected the false "the password is never written to disk" claim in the
  README, the sign-in dialog and the code comments. See [SECURITY.md](SECURITY.md).
- Fixed a PyInstaller spec `for...else` that printed "firmware is missing" on
  every build while bundling it correctly.

### 2.3.1 and earlier

- Seeed XIAO nRF52840 support — prebuilt `.hex` plus key injection over serial
  DFU, no toolchain. Two earlier approaches were tried and discarded; see
  [docs/STATUS.md](docs/STATUS.md).
- Removed basemaps that had started demanding an API key.
- Breadcrumb trails with date filtering and export; per-tag colours.
- Retail and Personal editions, baked in at build time so the edition can't be
  flipped with an environment variable.
- Per-user installer, no admin prompt.
- Docker removed entirely — anisette runs in-process in well under a second.

---

## NetWatch-TAK (ATAK plugin)

### 1.0.2

- **Shrink put the pane in a narrow left-hand column in portrait.**
  `showDropDown` takes a landscape *and* a portrait size pair and picks between
  them; `resize` takes one pair and applies it literally. Shrink passed the
  landscape shape unconditionally — right sideways, wrong upright. Expand was
  unaffected because full-by-full is identical either way.
- **The status dot rendered as mojibake in the title bar.** A non-ASCII glyph in
  a layout attribute, double-encoded by a tool that read the UTF-8 file as ANSI.
  Now set from Java, where the compiler resolves the escape.

### 1.0.1

- **Any dropdown crashed ATAK.** A plain `Spinner` inflated with the plugin
  context opens its popup against a non-UI context and Android refuses. Now uses
  `com.atakmap.android.gui.PluginSpinner`, which swaps the Activity context in
  for the duration of the click. Layouts also moved to `PluginLayoutInflater`.
- **The paired PC was forgotten on every ATAK restart.** Preferences were read
  from the plugin context, which points at the plugin APK's own package — the
  ATAK process can't write there, so `apply()` failed *silently*. Now uses
  `mapView.getContext()`.

### 1.0.0

First release. Tag list with colours and ages, markers and breadcrumb trails in
their own map group, CoT sharing bound at runtime so it degrades instead of
crashing, discovery plus pairing links, and multiple saved servers.
