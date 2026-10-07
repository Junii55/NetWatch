<#
    Build the prebuilt nRF52840 image that ships inside NetWatch.

    YOU run this once per release; customers never compile. The output is a
    single Intel HEX with a blank key block. At flash time the app rewrites the
    28-byte key inside it and sends it over serial DFU - see app/netwatch/dfu.py
    for why that transport and not UF2.

    Requires the Seeed nRF52 core:
      arduino-cli core install Seeeduino:nrf52 --additional-urls `
        https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json
#>
[CmdletBinding()]
param(
    [string]$Fqbn = 'Seeeduino:nrf52:xiaonRF52840Sense'
)

$ErrorActionPreference = 'Continue'
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$root   = Split-Path -Parent $here
$sketch = Join-Path $here 'NetWatch_Beacon_nRF52840'
$outDir = Join-Path $here 'prebuilt\nrf52840'

if (-not (Get-Command arduino-cli -ErrorAction SilentlyContinue)) { throw "arduino-cli not found in PATH." }
if (Get-Process arduino-cli -ErrorAction SilentlyContinue) {
    throw "another arduino-cli build is running; builds must be serial."
}

# The ARM toolchain splits paths on spaces, so stage somewhere without any.
$stage = "C:\nwfw-nrf-build"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $stage | Out-Null
Copy-Item -Recurse $sketch (Join-Path $stage 'NetWatch_Beacon_nRF52840')

$build = Join-Path $stage 'out'
arduino-cli compile --fqbn $Fqbn --output-dir $build (Join-Path $stage 'NetWatch_Beacon_nRF52840')
if ($LASTEXITCODE -ne 0) { throw "compile failed for nrf52840" }

$hex = Get-ChildItem $build -Filter '*.ino.hex' | Select-Object -First 1
if (-not $hex) { throw "no .hex produced" }

New-Item -ItemType Directory -Force $outDir | Out-Null
$dest = Join-Path $outDir 'firmware.hex'
Copy-Item $hex.FullName $dest -Force

# Gate: the key block must survive optimisation exactly once and ship blank.
$py = @"
import sys
sys.path.insert(0, r'$root\app')
from netwatch import uf2
from netwatch.keyinject import KEY_MAGIC, KEY_LEN
text = open(r'$dest').read()
blob = b''.join(d for _, d in uf2.parse_hex(text))
n = blob.count(KEY_MAGIC)
if n != 1:
    raise SystemExit(f'key marker appears {n} time(s); expected exactly 1. '
                     'Check NETWATCH_KEYBLOCK is still volatile and __attribute__((used)).')
pos = blob.find(KEY_MAGIC)
if blob[pos+len(KEY_MAGIC):pos+len(KEY_MAGIC)+KEY_LEN] != b'\x00' * KEY_LEN:
    raise SystemExit('prebuilt does not ship with a blank key')
print('  key block present exactly once, ships blank')
print('  image %d bytes across %d segment(s)' % (len(blob), len(uf2.parse_hex(text))))
"@
python -c $py
if ($LASTEXITCODE -ne 0) { throw "prebuilt gate failed" }

@{
    chip      = 'nrf52840'
    built_at  = (Get-Date).ToUniversalTime().ToString('o')
    fqbn      = $Fqbn
    method    = 'dfu'        # serial DFU from a prebuilt .hex; no esptool, no UF2
    dev_type  = '0x0052'
    baud      = 115200
    parts     = @(
        @{ path = 'firmware.hex'; offset = 'dfu'; inject_key = $true }
    )
} | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $outDir 'manifest.json') -Encoding utf8

Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
$kb = [math]::Round((Get-Item $dest).Length / 1KB)
Write-Host "  wrote $outDir (${kb} KB hex)" -ForegroundColor Green
exit 0
