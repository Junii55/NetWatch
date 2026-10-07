<#
    Build both NetWatch editions as separate programs.

        .\packaging\build_editions.ps1                 # both
        .\packaging\build_editions.ps1 -Only retail    # just the product
        .\packaging\build_editions.ps1 -Only personal  # just your own build

    retail   -> dist\NetWatch\NetWatch.exe
                Licensed + community basemaps, optional provider key. THE PRODUCT.
    personal -> dist\NetWatchPersonal\NetWatchPersonal.exe
                Adds Google's undocumented tiles. Breaches Maps Platform terms:
                for your own use only, never for sale.

    Both share one data directory, so tags and the Apple session carry across.
#>
[CmdletBinding()]
param(
    [ValidateSet('both', 'retail', 'personal')][string]$Only = 'both'
)

# NOT 'Stop': PyInstaller and esptool write progress to stderr, and Windows
# PowerShell turns native stderr into terminating NativeCommandErrors. Every
# step below checks $LASTEXITCODE explicitly instead.
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $root

$editions = if ($Only -eq 'both') { @('retail', 'personal') } else { @($Only) }

try {
    foreach ($ed in $editions) {
        Write-Host "`n======================================================" -ForegroundColor Cyan
        Write-Host " building edition: $ed" -ForegroundColor Cyan
        Write-Host "======================================================" -ForegroundColor Cyan

        $env:NETWATCH_EDITION = $ed

        python -m PyInstaller packaging\netwatch.spec --noconfirm --clean
        if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $ed" }

        $name = if ($ed -eq 'retail') { 'NetWatch' } else { 'NetWatchPersonal' }
        $exe  = Join-Path $root "dist\$name\$name.exe"
        if (-not (Test-Path $exe)) { throw "expected $exe" }

        # Unicorn JITs; PyInstaller's bootloader ships with CFG on, which kills
        # the process at the first emulated instruction. See disable_cfg.py.
        python packaging\disable_cfg.py $exe
        if ($LASTEXITCODE -ne 0) { throw "could not clear CFG on $exe" }

        Write-Host "verifying the Apple helper runs inside the bundle..."
        # Prove the selftest actually RAN. Checking $LASTEXITCODE alone is not
        # enough: if Windows blocks the exe (Smart App Control refuses unsigned
        # binaries) the call never starts, the exit code is stale, and a build
        # that was never verified sails through this gate.
        $report = Join-Path $HOME 'netwatch-selftest.txt'
        Remove-Item $report -ErrorAction SilentlyContinue
        $blocked = $false
        try   { & $exe --selftest | Out-Null }
        catch { $blocked = $true; Write-Warning "could not launch: $($_.Exception.Message)" }

        if ($blocked -or -not (Test-Path $report)) {
            throw @"
$ed selftest did NOT run - the build is UNVERIFIED, do not ship it.
Most likely Windows blocked the unsigned executable. Check:
  Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy' VerifiedAndReputablePolicyState
  (1 = Smart App Control ENFORCED)
Code-sign the executable, or test on a machine without Smart App Control.
"@
        }
        if ((Get-Content $report -Raw) -notmatch 'SELFTEST PASSED') {
            throw "$ed selftest FAILED - Apple sign-in would crash. See $report"
        }
        Write-Host "  $ed OK -> $exe" -ForegroundColor Green
    }

    Remove-Item Env:\NETWATCH_EDITION -ErrorAction SilentlyContinue

    Write-Host "`nDone." -ForegroundColor Green
    Write-Host "Ship dist\NetWatch. Keep dist\NetWatchPersonal for yourself." -ForegroundColor Yellow
}
finally {
    Pop-Location
}
