@echo off
cd /d "%~dp0"

if not exist "app\web_server.py" (
  echo Marketplace Price Guard
  echo.
  echo ERROR: project files were not found near run.bat.
  echo.
  echo Most likely you started run.bat directly from a ZIP/RAR archive.
  echo Please extract/unzip the whole project folder first, then run run.bat again.
  echo.
  echo Steps:
  echo 1. Right click the archive
  echo 2. Choose "Extract all" / "Extract to folder"
  echo 3. Open the extracted folder
  echo 4. Double-click run.bat
  echo.
  pause
  exit /b 1
)

if not exist ".env" (
  copy ".env.example" ".env" >nul
)

set "PYTHON_CMD="
py --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py"

if not defined PYTHON_CMD (
  python --version >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo Marketplace Price Guard
  echo.
  echo ERROR: Python was not found on this computer.
  echo.
  echo Run install_python_and_run.bat to install Python automatically,
  echo or install Python 3.11+ manually from:
  echo https://www.python.org/downloads/windows/
  echo.
  pause
  exit /b 1
)

echo Marketplace Price Guard
echo.
echo Site will open in your browser:
echo http://127.0.0.1:8001
echo.
echo Keep this window open while the site is running.
echo To stop the service, close this window.
echo.

start "" "http://127.0.0.1:8001"
"%PYTHON_CMD%" app\web_server.py 8001
pause
