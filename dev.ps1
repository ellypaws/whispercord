#!/usr/bin/env pwsh
# Dev runner: ensure the venv + deps exist, enable hot reload, and launch.
#
#   .\dev.ps1            run the dev GUI with hot reload (edit ui/* -> webview reloads)
#   .\dev.ps1 -Install   (re)install requirements into the venv first
#   .\dev.ps1 -Backend   run the transcription engine directly, no GUI
#   .\dev.ps1 -NoDev     run without hot reload
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
if (-not (Test-Path $py)) {
  Write-Host "[dev] no .venv found - creating one ..." -ForegroundColor Cyan
  python -m venv .venv
  $Install = $true
}
if ($Install) {
  Write-Host "[dev] installing requirements ..." -ForegroundColor Cyan
  & $py -m pip install --upgrade pip
  & $py -m pip install -r requirements.txt
}

$env:PYTHONUTF8 = "1"
if (-not $NoDev) { $env:VT_DEV = "1" }

if ($Backend) {
  Write-Host "[dev] launching engine (--backend) ..." -ForegroundColor Green
  & $py src\app.py --backend @Rest
}
else {
  Write-Host "[dev] launching GUI (hot reload: $(if ($NoDev) {'off'} else {'on'})) ..." -ForegroundColor Green
  & $py src\app.py @Rest
}
