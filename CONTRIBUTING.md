# Contributing

Issues and pull requests are welcome at
<https://github.com/junii55/netwatch>.

## Licensing of contributions

NetWatch is dual-licensed: AGPL v3 for everyone, plus commercial licences sold
separately (see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md)). That second
option only works while a single party holds the copyright to the whole codebase
— a commercial licence cannot be granted over code the grantor does not own.

**By submitting a pull request you agree that your contribution is licensed under
the AGPL v3, and you grant junii55 a perpetual, irrevocable, worldwide right to
relicense it, including under commercial terms.** You retain copyright in your
own work.

If you would rather not grant that, open an issue describing the change instead
and it can be reimplemented independently. No offence taken — it is a reasonable
position, and it is better raised before you spend time on a patch.

## Layout

```
desktop/          NetWatch Desktop (Python)
  app/netwatch/   the application
  app/tests/      tests, runnable directly with python
  firmware/       Arduino sketches + prebuilt images
  packaging/      PyInstaller spec, Inno Setup, icon, CFG patcher
NetWatch-TAK/     the ATAK plugin (Java / Gradle)
docs/             setup, bridge API, honest status
tools/            make-plugin-zip.ps1
```

## Desktop

```powershell
pip install -r desktop\app\requirements.txt
cd desktop\app
python -m netwatch                          # native window
python -m netwatch --browser                # in your browser
$env:NETWATCH_DEMO=1; python -m netwatch     # synthetic data, no Apple calls
```

Demo mode is the fastest way to work on the UI. It invents a stable history, so
repeated syncs return the same points and you can see whether de-duplication
works.

### Tests

```powershell
cd desktop\app
python tests\test_keyinject.py    # image patching and integrity
python tests\test_prebuilt.py     # release gate for the shipped firmware
python tests\test_bridge.py       # the ATAK bridge, including key secrecy
python tests\test_session.py      # Apple session persistence (needs network)
```

They print their own results and exit non-zero on failure; no pytest needed.

> **Always set `NETWATCH_DATA_DIR` when a test touches the database.** Every test
> file does this already. A test that creates or deletes tags against the live
> store destroys the private key of any board already in the field, and that key
> is unrecoverable — the board continues to advertise and no position can ever be
> read from it again.

### Building a release

```powershell
.\desktop\packaging\build.ps1              # firmware, tests, bundle, installer
.\desktop\packaging\build_editions.ps1     # both editions
```

The build **fails** if `NetWatch.exe --selftest` doesn't pass, which is the gate
proving the Apple helper runs inside the frozen bundle. Don't weaken that check;
it exists because a packaged build can fail in ways source never does.

## The ATAK plugin

You cannot build this without the ATAK SDK from `artifacts.tak.gov`, and access
is restricted. In practice: edit the source, run
`tools\make-plugin-zip.ps1`, upload to <https://tak.gov/user_builds>, and the
pipeline builds and signs it.

There is therefore **no local compiler**, and a mistake costs a full round trip.
Before submitting, confirm that every `R.id`, `R.string` and `R.drawable`
reference resolves and that all XML parses.

### Three constraints specific to ATAK plugins

1. **A plugin context is not a UI context.** Building an `AlertDialog` or opening
   a `Spinner` popup on the plugin context throws. Use `mapView.getContext()` for
   anything that opens a window, and the plugin context only for inflating
   layouts and reading resources. Dropdowns must be
   `com.atakmap.android.gui.PluginSpinner`.
2. **A plugin context cannot write preferences.** It points at the plugin APK's
   own package and `apply()` fails *silently* — things work all session and are
   gone after a restart. Preferences come from `mapView.getContext()`.
3. **`resize()` is not orientation-aware.** `showDropDown` takes a landscape and
   a portrait size pair and chooses; `resize` takes one pair and applies it.
   Check `isPortrait()` yourself.

## Conventions

- **New dependencies need a justification.** The ATAK plugin has none beyond the
  ATAK SDK and the Android platform, which is why its dependency scan is empty
  and its static analysis returns no findings. The desktop list is deliberately
  short.
- **Comments explain *why*, not *what*.** Where a line looks unusual, the comment
  should record what goes wrong without it.
- **Do not describe something as verified unless it is.**
  [docs/STATUS.md](docs/STATUS.md) distinguishes what has run on hardware from
  what has only passed tests, and that distinction should be preserved.
- **Three PowerShell behaviours worth knowing**, each of which has caused a real
  defect in this project:
  - `Get-Content`/`Set-Content` round-trips read UTF-8 as ANSI and corrupt
    non-ASCII characters;
  - `Compress-Archive` writes backslash path separators, which Linux build
    servers cannot read — use `tools/make-plugin-zip.ps1`;
  - variable names are case-insensitive, so `$b` and `$B` are the same variable.
