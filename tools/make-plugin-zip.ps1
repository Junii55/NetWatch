<#
    Build a source archive of NetWatch-TAK for the TAK third-party pipeline.

        .\tools\make-plugin-zip.ps1
        .\tools\make-plugin-zip.ps1 -OutDir C:\somewhere

    Upload the result at https://tak.gov/user_builds and you get back a signed
    APK.

    WHY THIS SCRIPT EXISTS, instead of "just zip the folder"
    -------------------------------------------------------
    Windows PowerShell 5.1's Compress-Archive writes entry paths with BACKSLASH
    separators. The ZIP specification says forward slashes. Windows tools paper
    over it; the pipeline's Linux builder does not, and treats
    "NetWatch-TAK\app\build.gradle" as one long filename in the root rather than
    a path. The archive looks fine locally and the build fails after upload.

    So this writes the entries by hand with forward slashes, and checks the
    result before handing it over.

    It also enforces the two structural rules the pipeline cares about: exactly
    one root folder, and that folder's name becomes the APK name.
#>
[CmdletBinding()]
param(
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$src = Join-Path $repo 'NetWatch-TAK'
if (-not (Test-Path $src)) { throw "cannot find $src" }
if (-not $OutDir) { $OutDir = $repo }

# Read the version out of the gradle file so the archive name always matches
# what the pipeline will stamp on the APK.
$gradle = Get-Content (Join-Path $src 'app\build.gradle') -Raw
if ($gradle -match 'PLUGIN_VERSION\s*=\s*"([^"]+)"') { $version = $Matches[1] }
else { throw 'could not read PLUGIN_VERSION from app/build.gradle' }

$zipPath = Join-Path $OutDir "NetWatch-TAK-$version-source.zip"
if (Test-Path $zipPath) { [System.IO.File]::Delete($zipPath) }

Add-Type -AssemblyName System.IO.Compression.FileSystem
Add-Type -AssemblyName System.IO.Compression

$FWD = [char]47
$BACK = [char]92
# Build output and IDE state must not travel: the pipeline builds from scratch,
# and a stale build/ directory has produced confusing failures before.
$SKIP = @('build', '.gradle', '.takdev', '.idea', 'local.properties')

$archive = [System.IO.Compression.ZipFile]::Open(
    $zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
$added = 0
try {
    Get-ChildItem $src -Recurse -File | Sort-Object FullName | ForEach-Object {
        $rel = $_.FullName.Substring($src.Length + 1)
        $skip = $false
        foreach ($seg in $rel.Split($BACK)) { if ($SKIP -contains $seg) { $skip = $true } }
        if (-not $skip) {
            $entry = 'NetWatch-TAK' + $FWD + $rel.Replace($BACK, $FWD)
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive, $_.FullName, $entry,
                [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
            $added++
        }
    }
} finally {
    $archive.Dispose()
}

# Verify rather than assume. A bad archive is only discovered after upload.
$check = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    # @(...) matters: Sort-Object -Unique returns a bare string when there is
    # only one distinct value, and indexing a string gives its first character,
    # so this reported a root folder of 'N' instead of 'NetWatch-TAK'.
    $roots = @($check.Entries |
        ForEach-Object { $_.FullName.Split([char[]]@($FWD))[0] } |
        Sort-Object -Unique)
    $backslashes = @($check.Entries | Where-Object { $_.FullName.Contains($BACK) }).Count
    $required = @(
        'NetWatch-TAK/settings.gradle',
        'NetWatch-TAK/build.gradle',
        'NetWatch-TAK/gradlew',
        'NetWatch-TAK/gradle/wrapper/gradle-wrapper.jar',
        'NetWatch-TAK/app/build.gradle',
        'NetWatch-TAK/app/proguard-gradle.txt',
        'NetWatch-TAK/app/src/main/AndroidManifest.xml',
        'NetWatch-TAK/app/src/main/assets/plugin.xml'
    )
    $missing = @()
    foreach ($r in $required) {
        if (-not ($check.Entries | Where-Object { $_.FullName -eq $r })) { $missing += $r }
    }

    if ($roots.Count -ne 1) { throw "archive must have exactly one root folder, found: $($roots -join ', ')" }
    if ($backslashes -gt 0) { throw "$backslashes entries use backslash separators - the Linux builder will not read these" }
    if ($missing.Count -gt 0) { throw "missing required files:`n  $($missing -join "`n  ")" }

    Write-Host ''
    Write-Host "  $zipPath" -ForegroundColor Green
    Write-Host ("  {0} files, {1:N0} KB, root folder '{2}'" -f $added, ((Get-Item $zipPath).Length / 1KB), $roots[0])
    Write-Host ''
    Write-Host '  Upload at https://tak.gov/user_builds' -ForegroundColor Cyan
    Write-Host "  You will get back ATAK-Plugin-NetWatch-TAK-$version-<atak>-civ-release.apk"
    Write-Host ''
} finally {
    $check.Dispose()
}
