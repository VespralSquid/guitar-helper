# Build the frozen onedir tree into dist/GuitarHelper.
#
#   .\installer\build.ps1            : build
#   .\installer\build.ps1 -Clean     : discard build/ and dist/ first
#
# Run from the repo root with .venv activated.
[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "No venv at $python - create it and pip install -r requirements.txt first."
}

if ($Clean) {
    foreach ($dir in @("build", "dist")) {
        if (Test-Path $dir) {
            Write-Host "Removing $dir"
            Remove-Item -Recurse -Force $dir
        }
    }
}

Write-Host "Freezing..." -ForegroundColor Cyan
& $python -m PyInstaller --noconfirm --distpath dist --workpath build `
    (Join-Path $root "installer\GuitarHelper.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$out = Join-Path $root "dist\GuitarHelper"
$exe = Join-Path $out "GuitarHelper.exe"
if (-not (Test-Path $exe)) { throw "Build reported success but $exe is missing." }

$bytes = (Get-ChildItem -Recurse $out | Measure-Object -Property Length -Sum).Sum
$mb = [math]::Round($bytes / 1MB, 1)
$version = (& $python -c "import guitar_helper; print(guitar_helper.__version__)").Trim()

Write-Host ""
Write-Host "Built GuitarHelper $version" -ForegroundColor Green
Write-Host "  $out"
Write-Host "  $mb MB"
