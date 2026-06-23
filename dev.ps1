#!/usr/bin/env pwsh
# Dev runner: ensure deps exist, start Vite HMR, and launch the Python app watcher.
#
#   .\dev.ps1            run Vite HMR plus backend reload (auto-installs deps as needed)
#   .\dev.ps1 -Install   force a full (re)install of python + frontend deps
#   .\dev.ps1 -Backend   run the transcription engine directly, no GUI
#   .\dev.ps1 -NoDev     run without Vite or backend reload
#
# Deps auto-install on first run and whenever requirements.txt or src/ui/bun.lock
# change (tracked via hash stamps), so plain `.\dev.ps1` is always enough.
# Extra args pass through to app.py, e.g.  .\dev.ps1 -- --cleanup-overlay
param(
  [switch]$Install,
  [switch]$Backend,
  [switch]$NoDev,
  [Parameter(ValueFromRemainingArguments = $true)] $Rest
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = ".\.venv\Scripts\python.exe"

# Stamp helper: returns $true when the watched file's hash differs from the
# recorded stamp (i.e. deps drifted and we should (re)install).
function Test-Stale($source, $stamp) {
  if (-not (Test-Path $source)) { return $false }
  $hash = (Get-FileHash $source -Algorithm SHA256).Hash
  if ((Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq $hash)) { return $false }
  return $true
}
function Set-Stamp($source, $stamp) {
  (Get-FileHash $source -Algorithm SHA256).Hash | Set-Content $stamp -NoNewline
}

if (-not (Test-Path $py)) {
  Write-Host "[dev] no .venv found - creating one ..." -ForegroundColor Cyan
  python -m venv .venv
  $Install = $true
}

$reqStamp = ".\.venv\.requirements.sha256"
$bunStamp = ".\src\ui\node_modules\.bun.sha256"

# Auto-install: explicit -Install, missing node_modules, or drifted lockfiles.
$pyStale  = $Install -or (Test-Stale "requirements.txt" $reqStamp)
$uiStale  = $Install -or (-not (Test-Path "src\ui\node_modules")) -or (Test-Stale "src\ui\bun.lock" $bunStamp)

if ($pyStale) {
  Write-Host "[dev] installing python requirements ..." -ForegroundColor Cyan
  & $py -m pip install --upgrade pip
  & $py -m pip install -r requirements.txt
  Set-Stamp "requirements.txt" $reqStamp
}
if ($uiStale) {
  if (-not (Get-Command bun -ErrorAction SilentlyContinue)) {
    throw "bun is required for frontend dev. Install bun (https://bun.sh), then rerun .\dev.ps1."
  }
  Write-Host "[dev] installing frontend deps (bun install) ..." -ForegroundColor Cyan
  Push-Location src\ui
  bun install
  Pop-Location
  Set-Stamp "src\ui\bun.lock" $bunStamp
}

$env:PYTHONUTF8 = "1"
if (-not $NoDev) { $env:VT_DEV = "1" }

if ($Backend) {
  Write-Host "[dev] launching engine (--backend) ..." -ForegroundColor Green
  & $py src\app.py --backend @Rest
}
else {
  if ($NoDev) {
    Write-Host "[dev] launching GUI without dev reload ..." -ForegroundColor Green
    & $py src\app.py @Rest
    exit $LASTEXITCODE
  }

  if (-not (Get-Command bun -ErrorAction SilentlyContinue)) {
    throw "bun is required for frontend dev. Install bun, then rerun .\dev.ps1 -Install."
  }

  $uiDir = Join-Path $PSScriptRoot "src\ui"
  Write-Host "[dev] starting Vite on http://localhost:5173 ..." -ForegroundColor Cyan
  $vite = Start-Process -FilePath "bun" -ArgumentList @("run", "dev") -WorkingDirectory $uiDir -WindowStyle Hidden -PassThru
  try {
    $deadline = (Get-Date).AddSeconds(20)
    do {
      Start-Sleep -Milliseconds 250
      $open = Test-NetConnection -ComputerName localhost -Port 5173 -InformationLevel Quiet -WarningAction SilentlyContinue
    } while (-not $open -and (Get-Date) -lt $deadline)
    if (-not $open) { throw "Vite did not open port 5173." }

    Write-Host "[dev] launching GUI with backend reload ..." -ForegroundColor Green
    & $py src\app.py --dev-watch @Rest
  }
  finally {
    if ($vite -and -not $vite.HasExited) {
      Stop-Process -Id $vite.Id -Force
    }
  }
}
