@echo off
rem FTE Studio - one-time setup. Double-click this file. No PowerShell commands to type.
cd /d "%~dp0"
echo.
echo  FTE Studio setup
echo  ----------------
where python >nul 2>nul
if errorlevel 1 (
  echo  Python nathi malyu. python.org thi Python 3.14 install karo ^(Add to PATH tick karo^), pachhi aa file pharithi chalavo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist .venv ( python -m venv .venv )
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet pillow playwright
echo.
.venv\Scripts\python.exe studio.py doctor
echo.
echo  Have Studio.bat double-click karo.
pause
