@echo off
REM First-time setup: create the venv and install dependencies.
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
where bun >nul 2>nul || (echo bun is required for the frontend build. Install bun, then run setup.bat again. & pause & exit /b 1)
pushd src\ui
bun install
bun run build
if errorlevel 1 (popd & pause & exit /b 1)
popd
echo.
echo Setup complete. Double-click run.bat to start.
pause
