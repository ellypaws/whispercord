@echo off
REM Build the single packaged app (onedir by default; set VT_ONEFILE=1 for one .exe).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m PyInstaller discord-transcriber.spec --noconfirm
echo.
echo Built: dist\DiscordTranscriber\DiscordTranscriber.exe
echo Zip the dist\DiscordTranscriber folder to share it.
pause
