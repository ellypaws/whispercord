@echo off
REM Build the single packaged app (onedir by default; set VT_ONEFILE=1 for one .exe).
cd /d "%~dp0"
where bun >nul 2>nul || (echo bun is required to build the frontend. Install bun and run build.bat again. & pause & exit /b 1)
pushd src\ui
bun install
bun run build
if errorlevel 1 (popd & pause & exit /b 1)
popd
".venv\Scripts\python.exe" -m PyInstaller discord-transcriber.spec --noconfirm
echo.
echo Built: dist\DiscordTranscriber\DiscordTranscriber.exe
echo Zip the dist\DiscordTranscriber folder to share it.
pause
