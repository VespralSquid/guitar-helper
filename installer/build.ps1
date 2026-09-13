# Build the frozen onedir tree.
#
#   .\installer\build.ps1            : build
#   .\installer\build.ps1 -Clean     : discard the build and dist trees first
#   .\installer\build.ps1 -BuildRoot D:\gh-build
#
# Output goes OUTSIDE the repository by default, to
# %LOCALAPPDATA%\GuitarHelperBuild. The repo lives in OneDrive, and an 847 MB
# tree that is deleted and recreated on every build is actively harmful there:
# OneDrive re-uploads all of it, prompts the user about mass deletions, and
# holds handles on directories PyInstaller is trying to remove - which fails the
# build outright with "Access is denied".
#
# Override with -BuildRoot or $env:GUITAR_HELPER_BUILD_ROOT.
#
# Run from the repo root with .venv activated.
[CmdletBinding()]
param(
    [switch]$Clean,
    [string]$BuildRoot = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $BuildRoot) { $BuildRoot = $env:GUITAR_HELPER_BUILD_ROOT }
if (-not $BuildRoot) { $BuildRoot = Join-Path $env:LOCALAPPDATA "GuitarHelperBuild" }

$distPath = Join-Path $BuildRoot "dist"
$workPath = Join-Path $BuildRoot "build"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "No venv at $python - create it and pip install -r requirements.txt first."
}

if ($Clean) {
    foreach ($dir in @($workPath, $distPath)) {
        if (Test-Path $dir) {
            Write-Host "Removing $dir"
            Remove-Item -Recurse -Force $dir
        }
    }
}

New-Item -ItemType Directory -Force $BuildRoot | Out-Null

Write-Host "Build root: $BuildRoot" -ForegroundColor DarkGray
Write-Host "Freezing..." -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm --distpath $distPath --workpath $workPath `
    (Join-Path $root "installer\GuitarHelper.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$out = Join-Path $distPath "GuitarHelper"
$exe = Join-Path $out "GuitarHelper.exe"
if (-not (Test-Path $exe)) { throw "Build reported success but $exe is missing." }

$bytes = (Get-ChildItem -Recurse $out | Measure-Object -Property Length -Sum).Sum
$mb = [math]::Round($bytes / 1MB, 1)
$version = (& $python -c "import guitar_helper; print(guitar_helper.__version__)").Trim()

Write-Host ""
Write-Host "Built GuitarHelper $version" -ForegroundColor Green
Write-Host "  $out"
Write-Host "  $mb MB"
