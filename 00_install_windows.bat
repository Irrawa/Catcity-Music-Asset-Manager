@echo off
setlocal

cd /d %~dp0

echo ==========================================
echo  Catcity Music Asset Manager - Install
echo ==========================================
echo.

if exist ".venv\Scripts\python.exe" (
  echo Found existing .venv, skipping venv creation.
) else (
  echo Creating venv: .venv
  python -m venv .venv
  if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create venv. Ensure Python is installed.
    pause
    exit /b 1
  )
)

echo.
echo Installing requirements...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Done. Next: double-click 01_start_windows.bat
pause
