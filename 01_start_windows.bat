@echo off
setlocal

cd /d %~dp0

echo ==========================================
echo  Catcity Music Asset Manager - Start
echo ==========================================
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" start_app.py
) else (
  echo [WARN] .venv not found. Trying system python.
  echo        If this fails, run 00_install_windows.bat first.
  python start_app.py
)

pause
