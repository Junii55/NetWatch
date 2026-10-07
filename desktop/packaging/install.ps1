<#
    NetWatch per-user installer.

    Installs to %LOCALAPPDATA%\Programs\NetWatch, adds Start Menu and (optionally)
    Desktop shortcuts, and registers an Add/Remove Programs entry - all under
    HKCU, so it needs NO administrator rights. That matters: a customer who has
    to click through a UAC prompt for a hobby app is a customer who doesn't.

    Run from the folder holding NetWatch.exe (or pass -Source).

        powershell -ExecutionPolicy Bypass -File install.ps1
        powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
#>
[CmdletBinding()]
param(
    [string]$Source = "",
    [switch]$NoDesktopShortcut,
    [switch]$Uninstall,
    [switch]$Silent
)

$ErrorActionPreference = 'Stop'

$AppName  = 'NetWatch'
$ExeName  = 'NetWatch.exe'
$RegKey   = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\NetWatch'
$Target   = Join-Path $env:LOCALAPPDATA "Programs\$AppName"
$StartDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'

function Say($m, $c = 'Gray') { if (-not $Silent) { Write-Host $m -ForegroundColor $c } }

function New-Shortcut($Path, $TargetExe, $Desc) {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut($Path)
    $lnk.TargetPath       = $TargetExe
    $lnk.WorkingDirectory = Split-Path $TargetExe
    $lnk.Description      = $Desc
    $lnk.IconLocation     = "$TargetExe,0"
    $lnk.Save()
}

# ---------------------------------------------------------------- uninstall
if ($Uninstall) {
    Say "Removing $AppName..." Cyan
    foreach ($lnk in @((Join-Path $StartDir "$AppName.lnk"),
                       (Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk"))) {
        if (Test-Path $lnk) { Remove-Item $lnk -Force; Say "  removed shortcut" }
    }
    Get-Process -Name 'NetWatch','NetWatchPersonal' -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 700
    if (Test-Path $Target) { Remove-Item $Target -Recurse -Force; Say "  removed $Target" }
    if (Test-Path $RegKey) { Remove-Item $RegKey -Recurse -Force }

    # Deliberately NOT deleted: %LOCALAPPDATA%\NetWatch holds the tag private
    # keys. Losing them makes every flashed board permanently unlocatable, so
    # the data outlives an uninstall and a reinstall picks it straight back up.
    $data = Join-Path $env:LOCALAPPDATA $AppName
    if (Test-Path $data) {
        Say ""
        Say "Your tags and keys were kept at:" Yellow
        Say "  $data" Yellow
        Say "Delete that folder by hand only if you are certain - the private keys" Yellow
        Say "in it are the only way to locate the boards you have already flashed." Yellow
    }
    Say ""
    Say "$AppName removed." Green
    if (-not $Silent) { Read-Host "Press Enter to close" }
    return
}

# ------------------------------------------------------------------ install
if (-not $Source) { $Source = Split-Path -Parent $MyInvocation.MyCommand.Path }
$srcExe = Join-Path $Source $ExeName
if (-not (Test-Path $srcExe)) {
    # also allow running from a folder that CONTAINS the app folder
    $alt = Join-Path $Source "$AppName\$ExeName"
    if (Test-Path $alt) { $Source = Join-Path $Source $AppName; $srcExe = $alt }
    else { throw "$ExeName not found in $Source. Run this from the unzipped NetWatch folder." }
}

Say "Installing $AppName" Cyan
Say "  from : $Source"
Say "  to   : $Target"

Get-Process -Name 'NetWatch','NetWatchPersonal' -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 700

if (Test-Path $Target) {
    Say "  existing install found - replacing program files"
    Remove-Item $Target -Recurse -Force -ErrorAction SilentlyContinue
}
New-Item -ItemType Directory -Force $Target | Out-Null
Copy-Item (Join-Path $Source '*') $Target -Recurse -Force

$exe = Join-Path $Target $ExeName
if (-not (Test-Path $exe)) { throw "copy failed: $exe missing" }

New-Shortcut (Join-Path $StartDir "$AppName.lnk") $exe "Locate your own belongings"
Say "  Start Menu shortcut created"
if (-not $NoDesktopShortcut) {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) "$AppName.lnk") $exe "Locate your own belongings"
    Say "  Desktop shortcut created"
}

$size = [math]::Round(((Get-ChildItem $Target -Recurse -File | Measure-Object Length -Sum).Sum / 1KB))
New-Item -Path $RegKey -Force | Out-Null
$props = @{
    DisplayName     = $AppName
    DisplayVersion  = '2.3.0'
    Publisher       = $AppName
    InstallLocation = $Target
    DisplayIcon     = $exe
    UninstallString = "powershell -ExecutionPolicy Bypass -File `"$Target\install.ps1`" -Uninstall"
    EstimatedSize   = $size
    NoModify        = 1
    NoRepair        = 1
}
foreach ($k in $props.Keys) {
    $type = if ($props[$k] -is [int]) { 'DWord' } else { 'String' }
    New-ItemProperty -Path $RegKey -Name $k -Value $props[$k] -PropertyType $type -Force | Out-Null
}
Say "  registered in Add/Remove Programs"

Say ""
Say "$AppName installed." Green
Say "Launch it from the Start Menu, or:" Gray
Say "  $exe" Gray
Say ""
Say "Uninstall from Settings / Apps, or run install.ps1 -Uninstall" Gray

if (-not $Silent) {
    $go = Read-Host "Start $AppName now? [Y/n]"
    if ($go -eq '' -or $go -match '^[Yy]') { Start-Process $exe }
}
