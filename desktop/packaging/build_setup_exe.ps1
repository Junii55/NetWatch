<#
    Build a single-file NetWatch-Setup.exe.

    Uses IExpress, which ships with Windows, so no third-party packager is
    needed (Inno Setup produces a nicer wizard - use installer.iss if you have
    it). The package carries the built app plus install.ps1 and runs the
    installer on extraction.

        .\packaging\build_setup_exe.ps1                  # retail
        .\packaging\build_setup_exe.ps1 -Edition personal

    IExpress cannot recurse directories, so the app folder is zipped first and
    the payload is that single .zip plus a bootstrap script that unpacks it.
#>
[CmdletBinding()]
param(
    [ValidateSet('retail', 'personal')][string]$Edition = 'retail',
    [string]$Version = '2.3.0'
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$appName = if ($Edition -eq 'retail') { 'NetWatch' } else { 'NetWatchPersonal' }
$distDir = Join-Path $root "dist\$appName"
if (-not (Test-Path (Join-Path $distDir "$appName.exe"))) {
    throw "build the app first: packaging\build_editions.ps1 -Only $Edition"
}

# Staging must also be space-free for the same reason.
$work = "C:\nwsetup-build-$Edition"
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $work | Out-Null

Write-Host "packing $appName ..." -ForegroundColor Cyan
$payload = Join-Path $work 'app.zip'
Compress-Archive -Path (Join-Path $distDir '*') -DestinationPath $payload
Copy-Item (Join-Path $root 'packaging\install.ps1') $work -Force

# Bootstrap: IExpress drops the payload in a temp dir and runs this.
$boot = @"
@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Expand-Archive -LiteralPath 'app.zip' -DestinationPath 'app' -Force; ^
   & '.\install.ps1' -Source (Join-Path (Get-Location) 'app')"
"@
Set-Content (Join-Path $work 'bootstrap.cmd') $boot -Encoding ASCII

$finalExe = Join-Path $root "dist\$appName-Setup-$Version.exe"
Remove-Item $finalExe -ErrorAction SilentlyContinue

# IExpress silently does nothing when any path in the SED contains a space
# (e.g. C:\Users\Jane Doe\...). Build to a space-free staging path, then move.
$stageOut = "C:\nwsetup"
New-Item -ItemType Directory -Force $stageOut | Out-Null
$outExe = Join-Path $stageOut "$appName-Setup-$Version.exe"
Remove-Item $outExe -ErrorAction SilentlyContinue

# IExpress needs an .SED describing the package.
$sed = @"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=%InstallPrompt%
DisplayLicense=%DisplayLicense%
FinishMessage=%FinishMessage%
TargetName=%TargetName%
FriendlyName=%FriendlyName%
AppLaunched=%AppLaunched%
PostInstallCmd=%PostInstallCmd%
AdminQuietInstCmd=%AdminQuietInstCmd%
UserQuietInstCmd=%UserQuietInstCmd%
SourceFiles=SourceFiles
[Strings]
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$outExe
FriendlyName=NetWatch $Version Setup
AppLaunched=cmd /c bootstrap.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=
UserQuietInstCmd=
FILE0="app.zip"
FILE1="install.ps1"
FILE2="bootstrap.cmd"
[SourceFiles]
SourceFiles0=$work\
[SourceFiles0]
%FILE0%=
%FILE1%=
%FILE2%=
"@
$sedPath = Join-Path $work 'netwatch.sed'
Set-Content $sedPath $sed -Encoding ASCII

Write-Host "running IExpress ..." -ForegroundColor Cyan
& "$env:WINDIR\System32\iexpress.exe" /N /Q $sedPath
Start-Sleep -Seconds 2

if (Test-Path $outExe) {
    Move-Item $outExe $finalExe -Force
    Remove-Item -Recurse -Force $work, $stageOut -ErrorAction SilentlyContinue
    $mb = [math]::Round((Get-Item $finalExe).Length / 1MB, 1)
    Write-Host "built $finalExe ($mb MB)" -ForegroundColor Green
    Write-Host "NOTE: unsigned. Smart App Control / SmartScreen will block it on" -ForegroundColor Yellow
    Write-Host "      many Windows 11 machines until you code-sign it." -ForegroundColor Yellow
} else {
    Write-Host "SED used:" -ForegroundColor Yellow
    Get-Content $sedPath | ForEach-Object { "   $_" }
    throw "IExpress did not produce $outExe"
}
