<#
    Build the prebuilt firmware images that ship inside NetWatch Desktop.

    YOU run this once per release. Your CUSTOMERS never do - that is the whole
    point: they get a 10-second flash instead of a 15-minute compile.

    Output:  firmware/prebuilt/<chip>/{bootloader.bin,partitions.bin,firmware.bin,manifest.json}

    Requires arduino-cli with the esp32 core and NimBLE-Arduino 2.x.
      arduino-cli core install esp32:esp32 --additional-urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
      arduino-cli lib install "NimBLE-Arduino@2.5.1"

    Version pairing matters: esp32 core 3.x needs NimBLE 2.x. NimBLE 1.4.x has
    no NimBLEDevice::setOwnAddr and its addData() takes char*, so this sketch
    will not compile against it.
#>
[CmdletBinding()]
param(
    [string[]]$Chips = @('esp32', 'esp32c3', 'esp32c6', 'esp32s3', 'nrf52840'),
    [switch]$LowPower = $true
)

$ErrorActionPreference = 'Stop'
$here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$sketch   = Join-Path $here 'NetWatch_Beacon'
$outRoot  = Join-Path $here 'prebuilt'

# fqbn, bootloader offset (differs: classic ESP32 is 0x1000, riscv/S3 are 0x0)
# NOTE: must NOT be called $CHIPS - PowerShell variables are case-insensitive, so
# that would collide with the [string[]]$Chips parameter and get coerced to strings.
# PartitionScheme=huge_app: the NimBLE build overflows the default 1.25 MB app
# partition on some targets. A beacon needs neither OTA nor a filesystem, so the
# 3 MB no-OTA layout is the right trade and keeps the app at 0x10000.
# flash_mode is 'dio' for EVERY target, deliberately.
# Verified on real hardware: an ESP32-C3 dev board flashed with qio boot-loops
# (ROM loads the 2nd-stage bootloader, then TG0WDT_SYS_RST forever), while the
# identical image in dio boots first time. Plenty of modules either wire only
# two data lines or use flash that will not do quad I/O. dio costs a little
# boot speed and works on everything -- the right trade for a shipped product.
$CHIP_SPECS = @{
    'esp32'   = @{ fqbn = 'esp32:esp32:esp32:PartitionScheme=huge_app';   boot = '0x1000'; mode = 'dio'; freq = '80m'; size = '4MB' }
    'esp32c3' = @{ fqbn = 'esp32:esp32:esp32c3:PartitionScheme=huge_app'; boot = '0x0';    mode = 'dio'; freq = '80m'; size = '4MB' }
    'esp32c6' = @{ fqbn = 'esp32:esp32:esp32c6:PartitionScheme=huge_app'; boot = '0x0';    mode = 'dio'; freq = '80m'; size = '4MB' }
    'esp32s3' = @{ fqbn = 'esp32:esp32:esp32s3:PartitionScheme=huge_app'; boot = '0x0';    mode = 'dio'; freq = '80m'; size = '4MB' }
}

$MAGIC = [Text.Encoding]::ASCII.GetBytes("NETWATCH-KEYBLK") + [byte]0

function Count-Magic([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    $n = 0
    for ($i = 0; $i -le $bytes.Length - $MAGIC.Length; $i++) {
        $hit = $true
        for ($j = 0; $j -lt $MAGIC.Length; $j++) {
            if ($bytes[$i + $j] -ne $MAGIC[$j]) { $hit = $false; break }
        }
        if ($hit) { $n++ }
    }
    return $n
}

if (-not (Get-Command arduino-cli -ErrorAction SilentlyContinue)) {
    throw "arduino-cli not found in PATH."
}

# arduino-cli shares one sketch cache across builds. Running two compiles at the
# same time corrupts it ("error reading <x>.c.o: file truncated"), which looks
# like a code failure but is not. Build serially, and refuse to start if another
# compile is already running.
if (Get-Process arduino-cli -ErrorAction SilentlyContinue) {
    throw "another arduino-cli build is already running; builds must be serial."
}

New-Item -ItemType Directory -Force $outRoot | Out-Null
$lp = if ($LowPower) { 1 } else { 0 }

foreach ($chip in $Chips) {
    # nRF52840 is a different world: different sketch, Intel HEX output, and a
    # UF2 bootloader instead of esptool. Handled by its own helper.
    if ($chip -eq 'nrf52840') {
        Write-Host "`n=== building nrf52840 ===" -ForegroundColor Cyan
        & (Join-Path $here 'build_nrf52840.ps1') -LowPower:$LowPower
        if ($LASTEXITCODE -ne 0) { throw "nrf52840 build failed" }
        continue
    }
    if (-not $CHIP_SPECS.ContainsKey($chip)) { Write-Warning "skipping unknown chip $chip"; continue }
    $spec = $CHIP_SPECS[$chip]
    Write-Host "`n=== building $chip ===" -ForegroundColor Cyan

    $build = Join-Path $env:TEMP "netwatch-build-$chip"
    Remove-Item -Recurse -Force $build -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $build | Out-Null

    # Some toolchains split on spaces in the sketch path (e.g. "C:\Users\Jane Doe"),
    # so always compile from a space-free staging directory.
    $stage = Join-Path ([IO.Path]::GetTempPath().TrimEnd('\')) "nwfw-$chip"
    if ($stage -match '\s') { $stage = "C:\nwfw-$chip" }
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $stage | Out-Null
    Copy-Item -Recurse $sketch (Join-Path $stage 'NetWatch_Beacon')

    $flags = "-DNETWATCH_LOW_POWER=$lp"
    arduino-cli compile --fqbn $spec.fqbn --output-dir $build `
        --build-property "compiler.cpp.extra_flags=$flags" (Join-Path $stage 'NetWatch_Beacon')
    if ($LASTEXITCODE -ne 0) { throw "compile failed for $chip" }
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue

    $app  = Get-ChildItem $build -Filter '*.ino.bin'            | Select-Object -First 1
    $boot = Get-ChildItem $build -Filter '*.ino.bootloader.bin' | Select-Object -First 1
    $part = Get-ChildItem $build -Filter '*.ino.partitions.bin' | Select-Object -First 1
    if (-not ($app -and $boot -and $part)) { throw "expected build artefacts missing for $chip" }

    # The injected key is useless if the compiler folded the block away.
    $count = Count-Magic $app.FullName
    if ($count -ne 1) {
        throw ("key marker appears $count time(s) in $($app.Name); expected exactly 1. " +
               "Check that NETWATCH_KEYBLOCK is still 'volatile' and '__attribute__((used))'.")
    }
    Write-Host "  key block present exactly once" -ForegroundColor Green

    $dst = Join-Path $outRoot $chip
    New-Item -ItemType Directory -Force $dst | Out-Null
    Copy-Item $boot.FullName (Join-Path $dst 'bootloader.bin') -Force
    Copy-Item $part.FullName (Join-Path $dst 'partitions.bin') -Force
    Copy-Item $app.FullName  (Join-Path $dst 'firmware.bin')   -Force

    @{
        chip       = $chip
        built_at   = (Get-Date).ToUniversalTime().ToString('o')
        low_power  = [bool]$LowPower
        baud       = 460800
        flash_mode = $spec.mode
        flash_freq = $spec.freq
        flash_size = $spec.size
        parts      = @(
            @{ path = 'bootloader.bin'; offset = $spec.boot }
            @{ path = 'partitions.bin'; offset = '0x8000'   }
            @{ path = 'firmware.bin';   offset = '0x10000'; inject_key = $true }
        )
    } | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $dst 'manifest.json') -Encoding utf8

    $kb = [math]::Round($app.Length / 1KB)
    Write-Host "  wrote $dst (${kb} KB app)" -ForegroundColor Green
}

Write-Host "`nDone. Verify before shipping:" -ForegroundColor Cyan
Write-Host "  python app/tests/test_prebuilt.py"
