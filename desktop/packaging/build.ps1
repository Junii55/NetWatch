<#
    One-shot release build for NetWatch Desktop (Windows).

        .\packaging\build.ps1              # full build
        .\packaging\build.ps1 -SkipFirmware

    Steps
      1. firmware  : compile prebuilt images (needs arduino-cli)
      2. verify    : run the test suite, including the firmware release gate
      3. bundle    : PyInstaller  -> dist\NetWatch\NetWatch.exe
      4. installer : Inno Setup   -> dist\NetWatch-Setup-<ver>.exe
#>
[CmdletBinding()]
param(
    [switch]$SkipFirmware,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $root

function Step($n) { Write-Host "`n=== $n ===" -ForegroundColor Cyan }

try {
    Step "1/4 prebuilt firmware"
    if ($SkipFirmware) {
        Write-Host "skipped"
    } else {
        & (Join-Path $root 'firmware\build_prebuilt.ps1')
    }

    Step "2/4 tests"
    python app\tests\test_keyinject.py
    if ($LASTEXITCODE -ne 0) { throw "key injection tests failed" }
    python app\tests\test_prebuilt.py
    if ($LASTEXITCODE -ne 0) { throw "prebuilt firmware gate failed - do NOT ship" }
    # The bridge is the only part that listens on a network interface, so its
    # key-secrecy checks gate the release too.
    python app\tests\test_bridge.py
    if ($LASTEXITCODE -ne 0) { throw "ATAK bridge tests failed - do NOT ship" }

    Step "3/4 PyInstaller bundle"
    python -m pip install --quiet --upgrade pyinstaller
    python -m PyInstaller packaging\netwatch.spec --noconfirm --clean
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
    $exe = Join-Path $root 'dist\NetWatch\NetWatch.exe'
    if (-not (Test-Path $exe)) { throw "expected $exe" }
    Write-Host "built $exe" -ForegroundColor Green

    # Unicorn (which anisette uses to run Apple's provisioning code) is a JIT,
    # and PyInstaller's bootloader ships with Control Flow Guard on. CFG kills
    # the process at the first emulated instruction. See packaging\disable_cfg.py.
    python packaging\disable_cfg.py $exe
    if ($LASTEXITCODE -ne 0) { throw "could not clear CFG on $exe" }

    Write-Host "verifying the Apple helper works inside the bundle..."
    & $exe --selftest | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "bundle selftest FAILED (exit $LASTEXITCODE) - Apple sign-in would crash. See ~\netwatch-selftest.txt"
    }
    Write-Host "bundle selftest passed" -ForegroundColor Green

    Step "4/4 installer"
    if ($SkipInstaller) {
        Write-Host "skipped"
    } else {
        $iscc = Get-Command iscc -ErrorAction SilentlyContinue
        if (-not $iscc) {
            Write-Warning "Inno Setup (iscc) not found - skipping. Install from https://jrsoftware.org/isdl.php"
        } else {
            & $iscc.Source (Join-Path $root 'packaging\installer.iss')
            if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
            Get-ChildItem (Join-Path $root 'dist') -Filter 'NetWatch-Setup-*.exe' |
                ForEach-Object { Write-Host "installer: $($_.FullName)" -ForegroundColor Green }
        }
    }

    Write-Host "`nBuild complete." -ForegroundColor Green
    Write-Host "Before shipping: code-sign both the .exe and the installer, or " -NoNewline
    Write-Host "SmartScreen will warn every customer." -ForegroundColor Yellow
}
finally {
    Pop-Location
}
