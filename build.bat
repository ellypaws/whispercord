@echo off
REM Build the packaged app as a single self-contained .exe (always onefile).
cd /d "%~dp0"
where bun >nul 2>nul || (echo bun is required to build the frontend. Install bun and run build.bat again. & pause & exit /b 1)
pushd src\ui
bun install
bun run build
if errorlevel 1 (popd & pause & exit /b 1)
popd
".venv\Scripts\python.exe" -m PyInstaller discord-transcriber.spec --noconfirm
echo.
echo Built: dist\DiscordTranscriber.exe  (single file - share it as-is)
pause
