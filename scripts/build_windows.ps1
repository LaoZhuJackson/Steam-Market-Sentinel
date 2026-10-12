# Build the Windows executable. Must run on Windows - a Windows EXE cannot be
# cross-built.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 -OneFile
#   powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1 -Python "C:\...\python.exe"
#
# Default (--onedir) produces dist\SteamMarketSentinel\SteamMarketSentinel.exe and you
# distribute the WHOLE folder zipped.
#
# -OneFile produces a single dist\SteamMarketSentinel.exe that runs on its own. It is
# easier to hand over, but it unpacks ~200 MB to %TEMP% on every launch (so the first
# window takes noticeably longer to appear) and unsigned single-file bundles trip
# antivirus heuristics more often.
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 files as ANSI
# unless they carry a UTF-8 BOM, so any non-ASCII text here can break parsing.
param(
    [string]$Python = "python",  # override to use a specific interpreter (e.g. conda)
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param([string]$Label, [scriptblock]$Command)
    Write-Host ""
    Write-Host "== $Label" -ForegroundColor Cyan
    & $Command
    # $ErrorActionPreference only covers cmdlet errors - NOT the exit code of an
    # external program. Without this check a failing pytest would quietly continue
    # into packaging and still print "Build ready".
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE - build aborted."
    }
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# pyproject.toml declares 3.10 as the floor; PyInstaller would otherwise bundle an
# older interpreter and fail in a much more confusing way.
Invoke-Step "Check the interpreter ($Python)" {
    & $Python -c "import sys; assert sys.version_info >= (3, 10), 'Python 3.10+ required, got ' + sys.version"
}

Invoke-Step "Install dependencies" {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install ".[dev]"
}

Invoke-Step "Run the test suite" { & $Python -m pytest -q }

$Bundle = if ($OneFile) { "--onefile" } else { "--onedir" }

Invoke-Step "Build with PyInstaller ($Bundle)" {
    # --onedir keeps startup reliable and debuggable; --onefile is a single portable
    # exe at the cost of unpacking at every launch. The keyring backends are collected
    # explicitly because without them the app cannot store notification secrets at all.
    & $Python -m PyInstaller --noconfirm --clean --windowed $Bundle `
        --name "SteamMarketSentinel" `
        --collect-submodules "keyring.backends" `
        main.py
}

if ($OneFile) {
    $Exe = Join-Path $Root "dist\SteamMarketSentinel.exe"
    $Folder = Join-Path $Root "dist"
} else {
    $Exe = Join-Path $Root "dist\SteamMarketSentinel\SteamMarketSentinel.exe"
    $Folder = Split-Path -Parent $Exe
}
if (-not (Test-Path $Exe)) {
    throw "PyInstaller did not produce $Exe - check the build output above."
}
$Megabytes = [math]::Round(
    ((Get-ChildItem $Folder -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
if ($OneFile) {
    $Megabytes = [math]::Round(((Get-Item $Exe).Length / 1MB), 1)
}

if (Select-String -Path (Join-Path $Root "CHANGELOG.md") -Pattern '^## Unreleased' -Quiet) {
    Write-Warning "CHANGELOG.md still has an '## Unreleased' section - date it before publishing."
}

Write-Host ""
Write-Host "Build ready: $Exe" -ForegroundColor Green
Write-Host "Size: $Megabytes MB ($Bundle)"
Write-Host ""
Write-Host "Smoke-test it first (this does NOT touch your real watchlist data):"
Write-Host ('  $env:SMS_DATA_DIR = "{0}\.build-test"; & "{1}"' -f $Root, $Exe)
Write-Host ""
if ($OneFile) {
    Write-Host "To distribute, hand over that single exe."
    Write-Host "It unpacks on every launch, so the first window takes a few seconds longer."
} else {
    Write-Host ('To distribute, ZIP this whole folder: {0}' -f $Folder)
}
Write-Host "Builds are unsigned, so the first run may show a SmartScreen warning."
# Tip: pass -Python <path> to build with a specific interpreter (e.g. a conda env).
