@echo off
cd /d "%~dp0"

if not exist "app\web_server.py" (
  echo Marketplace Price Guard
  echo.
  echo ERROR: project files were not found near this file.
  echo.
  echo Most likely you started this file directly from a ZIP/RAR archive.
  echo Please extract/unzip the whole project folder first.
  echo.
  pause
  exit /b 1
)

set "PYTHON_CMD="
py --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py"

if not defined PYTHON_CMD (
  python --version >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if defined PYTHON_CMD (
  echo Python is already installed.
  call run.bat
  exit /b %errorlevel%
)

echo Python was not found.
echo Trying to install Python 3.13 with winget...
echo.

winget --version >nul 2>&1
if errorlevel 1 (
  echo winget was not found on this computer.
  echo Opening the Python download page.
  start "" "https://www.python.org/downloads/windows/"
  echo.
  echo Install Python 3.11+ manually, then run run.bat again.
  pause
  exit /b 1
)

winget install -e --id Python.Python.3.13 --accept-package-agreements --accept-source-agreements

set "PYTHON_CMD="
py --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py"

if not defined PYTHON_CMD (
  python --version >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo.
  echo Python installation was started, but Python is still not available in this terminal.
  echo Close this window and run run.bat again.
  pause
  exit /b 1
)

call run.bat
