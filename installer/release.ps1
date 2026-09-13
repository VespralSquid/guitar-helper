# Produce a complete release: frozen tree -> installer -> manifest.json.
#
#   .\installer\release.ps1
#   .\installer\release.ps1 -SkipBuild        : reuse dist\GuitarHelper
#   .\installer\release.ps1 -Notes "Fixes X"
#
# The manifest digest is computed from the installer this run produced. It is
# never written by hand: a manifest whose sha256 does not match its own artifact
# makes every client refuse the update, and one that matches an artifact nobody
# checked defeats the point of having a digest at all.
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [string]$Notes = "",
    [string]$MinUpgradableFrom = "",
    [string]$SourceUrl = "https://github.com/VespralSquid/guitar-helper",
    [string]$BuildRoot = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $BuildRoot) { $BuildRoot = $env:GUITAR_HELPER_BUILD_ROOT }
if (-not $BuildRoot) { $BuildRoot = Join-Path $env:LOCALAPPDATA "GuitarHelperBuild" }
$distPath = Join-Path $BuildRoot "dist"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "No venv at $python" }

$version = (& $python -c "import guitar_helper; print(guitar_helper.__version__)").Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "guitar_helper.__version__ is '$version'; the updater requires MAJOR.MINOR.PATCH."
}
Write-Host "Releasing Guitar Helper $version" -ForegroundColor Cyan

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot "build.ps1") -Clean -BuildRoot $BuildRoot
    if ($LASTEXITCODE -ne 0) { throw "build.ps1 failed" }
}

$iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
if (-not (Test-Path $iscc)) { throw "ISCC.exe not found. winget install JRSoftware.InnoSetup" }

Write-Host "Compiling installer..." -ForegroundColor Cyan
& $iscc "/DAppVersion=$version" "/DDistDir=$distPath" (Join-Path $PSScriptRoot "GuitarHelper.iss") | Out-Null
if ($LASTEXITCODE -ne 0) { throw "ISCC failed with exit code $LASTEXITCODE" }

$setup = Join-Path $distPath "GuitarHelper-Setup-$version.exe"
if (-not (Test-Path $setup)) { throw "Expected installer at $setup" }

$hash = (Get-FileHash -Algorithm SHA256 -Path $setup).Hash.ToLower()
$size = (Get-Item $setup).Length

# source_url is not optional in practice: GPLv3 section 6 obliges us to tell
# every recipient of a binary where its corresponding source is, and an update
# conveys a binary just as the first install does.
$manifest = [ordered]@{
    version    = $version
    url        = "https://github.com/VespralSquid/guitar-helper/releases/download/v$version/GuitarHelper-Setup-$version.exe"
    sha256     = $hash
    notes      = $Notes
    source_url = $SourceUrl
}
if ($MinUpgradableFrom) { $manifest.min_upgradable_from = $MinUpgradableFrom }

$manifestPath = Join-Path $distPath "manifest.json"
$manifest | ConvertTo-Json -Depth 3 | Out-File -FilePath $manifestPath -Encoding utf8

# Fail here rather than shipping a manifest the client will reject.
& $python -c @"
import json, sys
from guitar_helper.update import parse_manifest
with open(r'$manifestPath', encoding='utf-8-sig') as fh:
    parse_manifest(json.load(fh))
print('manifest validates')
"@
if ($LASTEXITCODE -ne 0) { throw "Generated manifest failed validation" }

Write-Host ""
Write-Host "Release artifacts ready:" -ForegroundColor Green
Write-Host "  $setup"
Write-Host "  $([math]::Round($size / 1MB, 1)) MB  sha256=$hash"
Write-Host "  $manifestPath"
Write-Host ""
Write-Host "Publish with:" -ForegroundColor Yellow
Write-Host "  gh release create v$version `"$setup`" `"$manifestPath`" --title `"v$version`" --notes `"$Notes`""
