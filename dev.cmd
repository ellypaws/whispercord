@echo off
rem One-line shim so `dev` runs from cmd, a double-click, or any shell, regardless of
rem PowerShell execution policy. All the logic lives in dev.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
