@echo off
cd /d "%~dp0"
if exist .venv\Scripts\pythonw.exe (
  start "" .venv\Scripts\pythonw.exe studio.py gui
) else (
  start "" pythonw studio.py gui
)
