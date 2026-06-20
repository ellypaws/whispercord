@echo off
REM Launch the Discord Live Transcriber GUI from source.
cd /d "%~dp0"
set PYTHONUTF8=1
".venv\Scripts\python.exe" -u src\app.py %*
